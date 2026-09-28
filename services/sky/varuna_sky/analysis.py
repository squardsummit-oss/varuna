"""The observed-rain analysis series: what the radar and gauges said, step by step.

A forecast cycle hands the Twin three hours of *nowcast*. A hot-started Twin also needs the rain
that has already fallen - the spin-up from a bundle's ``t0`` and the catch-up from one cycle's
checkpoint to the next - and that rain is not a forecast. This module produces it from exactly
the three analysis stages :func:`varuna_sky.pipeline.run_sky` runs before it starts
forecasting, applied to every elapsed radar frame in turn:

1. :func:`~varuna_sky.qc.run_qc` - coverage, clutter and the attenuation flag;
2. :func:`~varuna_sky.zr.run_zr` - the adaptive ``Z = a R^b``, or Marshall-Palmer;
3. :func:`~varuna_sky.motion.rain_from_dbz` then :func:`~varuna_sky.merge.merge_gauges` -
   mean-field bias and inverse-distance residuals.

No stage is reimplemented here. The only new decisions are *which* frames and gauge readings
each step may see, and how 10-minute radar frames become a 5-minute forcing.

**It never reads the truth field.** ``truth/rain.zarr`` is the answer the verification scores
against, and live mode does not have one. The lead's decision (storm-realism plan, decision 1)
is that the spin-up runs on Sky's own analyses, whatever mass they carry, and that the measured
analysis-to-truth ratio is published beside the result rather than corrected away. Nothing in
this module opens the ``truth`` member of a bundle, and a test holds it to that.

**The time convention is the Twin's.** ``varuna_twin.runner`` applies ``rain_mm_h[k]`` over
``[t0 + k * step, t0 + (k + 1) * step)``, and a forecast step is labelled with the instant it
ends at (:class:`~varuna_sky.types.RainEnsemble`). An analysis series over ``[t_from, t_to]``
therefore has ``(t_to - t_from) / step`` steps, step ``k`` labelled
``t_from + (k + 1) * step``, so a catch-up ending at a cycle is followed by that cycle's
forecast with no gap and no overlap.

**No look-ahead.** Step ``k`` is built from the newest radar frame at or before its labelled
instant, the frames of the hour before that frame, and the gauge readings of the hour ending at
that frame. A frame or reading after the step's instant is never read: the frame source is
asked only for the indices a step needs.

**Each 10-minute frame is held for two 5-minute steps** (plan chunk R3). With frames at 06:30
and 06:40, the steps labelled 06:40 and 06:45 both carry the 06:40 analysis. A frame is held for
at most :data:`MAX_FRAME_AGE_MIN`; a longer gap in the radar raises rather than smearing one
frame's rain across it.

**The analysis for an instant does not depend on where a series starts** (the critique's bake
ladder requirement, rule 8). Every step is a pure function of its own frame window and gauge
window - no state is carried from ``t_from`` - so the 06:40 step is byte-identical whether a
catch-up began at 06:10 or at 06:35. That is what lets ``make bake`` at every 5 minutes and at
``--every 30`` agree.

**Clutter needs six frames.** SPEC.md 11.1 takes clutter as near-zero variance over the last
six frames. Early in a bundle fewer exist, and the variance over two or three frames would call
persistent convective rain clutter and delete it. Steps with fewer than
:data:`~varuna_sky.qc.CLUTTER_FRAMES` frames behind them therefore skip the clutter test, keep
the coverage and attenuation stages, and say so in their provenance and in the series notes.

**Why an hour of frames, not three.** ``run_sky`` sees the three frames optical flow needs.
The analysis sees every frame of the :data:`~varuna_sky.zr.PAIR_WINDOW_MIN` hour ending at its
own frame, because the Z-R fit pairs each gauge reading with its nearest frame and SPEC.md
11.1 step 2 fits over the last 60 minutes; with three frames, readings older than about 25
minutes find no frame and drop out of the fit. The clutter test slices its own last six. The
analysis at a cycle instant can therefore differ from ``run_sky``'s own analysis at that
instant; the difference is measured, not assumed, and reported with the chunk.

Determinism (rule 8): nothing here is random, every stage it calls is order-independent, and
each distinct frame is computed once per call from the same inputs.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np
import structlog
from varuna_schemas.constants import IST, STEP_MIN
from varuna_schemas.paths import bundle_dir

from varuna_sky.merge import merge_gauges
from varuna_sky.motion import rain_from_dbz
from varuna_sky.products import AoiGrid, aoi_mean_weights, load_aoi_grid, resample_to_aoi
from varuna_sky.qc import (
    CLUTTER_FRAMES,
    attenuation_flag,
    attenuation_shadow,
    coverage_mask,
    run_qc,
)
from varuna_sky.types import QCResult, RadarFrames, RadarGrid
from varuna_sky.zr import PAIR_WINDOW_MIN, REQUIRED_COLUMNS, run_zr

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    import pandas as pd
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.sky.analysis")

__all__ = [
    "ANALYSIS_VERSION",
    "FRAME_WINDOW_MIN",
    "MAX_FRAME_AGE_MIN",
    "RADAR_MEMBER",
    "AnalysisRain",
    "AnalysisSeries",
    "AnalysisStep",
    "ArrayFrameSource",
    "FrameSource",
    "ZarrFrameSource",
    "analysis_rain",
    "analysis_series",
    "frame_analysis",
    "read_gauges",
]

ANALYSIS_VERSION = "1"
"""Version of the analysis rules: which frames and readings a step sees, and the hold.

A checkpoint spun up on this series should record it (critique of the storm-realism plan:
the Twin state fingerprint names the analysis version), so a change to any rule here bumps it."""

FRAME_WINDOW_MIN = PAIR_WINDOW_MIN
"""Minutes of radar history behind each analysed frame, inclusive of the frame itself.

The same hour the Z-R fit draws gauge readings from (SPEC.md 11.1 step 2), so every reading in
that hour has a frame within half an interval of it. With the bundles' 10-minute frames that is
seven frames, which also covers the six the clutter test needs."""

MAX_FRAME_AGE_MIN = 10.0
"""The oldest a held frame may be at the instant a step is labelled with, in minutes.

A design choice. With frames every 10 minutes and 5-minute steps a frame is 0 or 5 minutes old
when used; one radar interval is the most a missing frame may be bridged by. Beyond that the
series raises, because holding one frame's rain across a longer outage would invent a storm
the radar never showed (rule 6)."""

RADAR_MEMBER = ("radar", "frames.zarr")
"""Where a bundle keeps its reflectivity cube (SPEC.md 10.2). Restated rather than imported:
Sky depends on ``varuna_schemas`` and nothing else in the workspace (``types.py``)."""

GAUGES_MEMBER = "gauges.csv"
MANIFEST_MEMBER = "manifest.json"


# ============================================================================ frame sources
class FrameSource(Protocol):
    """Radar frames addressed by index, so a caller can prove which ones were read."""

    @property
    def times(self) -> tuple[datetime, ...]:
        """Valid time of every frame, oldest first."""
        ...

    @property
    def grid(self) -> RadarGrid:
        """Georeference of every frame."""
        ...

    def read(self, indices: Sequence[int]) -> NDArray[np.floating]:
        """``(len(indices), n_px, n_px)`` dBZ for exactly these frames, in the order given."""
        ...


@dataclass(frozen=True, slots=True)
class ArrayFrameSource:
    """Frames already in memory: ``dbz`` is ``(n_frames, n_px, n_px)``, oldest first."""

    dbz: NDArray[np.floating]
    times: tuple[datetime, ...]
    grid: RadarGrid

    def __post_init__(self) -> None:
        shape = np.shape(self.dbz)
        if len(shape) != 3 or shape[0] != len(self.times) or tuple(shape[1:]) != self.grid.shape:
            msg = (
                f"frames {shape} do not match {len(self.times)} times on the {self.grid.shape} grid"
            )
            raise ValueError(msg)

    def read(self, indices: Sequence[int]) -> NDArray[np.floating]:
        return np.asarray(self.dbz, dtype=np.float64)[list(indices)]


@dataclass(frozen=True, slots=True)
class ZarrFrameSource:
    """A bundle's ``radar/frames.zarr``, read one frame at a time and only when asked.

    The cube is chunked one frame per chunk (``varuna_replay.bundle.write_cube``), so reading
    index ``i`` touches that frame's chunk and no other: a frame after a step's instant is not
    merely unused, it is never read off the disk.
    """

    path: Path
    variable: str
    times: tuple[datetime, ...]
    grid: RadarGrid

    @classmethod
    def open(cls, path: Path | str) -> ZarrFrameSource:
        """Read the time axis and georeference from the group's attributes, no frames."""
        import zarr

        store = Path(path)
        if not store.exists():
            msg = f"No radar cube at {store}. Run `make bundle` to build the bundle's frames."
            raise FileNotFoundError(msg)
        group: Any = zarr.open_group(str(store), mode="r")
        attrs: dict[str, Any] = dict(group.attrs)
        variable = str(attrs.get("variable", "dbz"))
        units = str(attrs.get("units", ""))
        if units and units != "dBZ":
            msg = f"{store} holds {variable!r} in {units!r}; an analysis needs reflectivity in dBZ."
            raise ValueError(msg)
        transform = tuple(float(v) for v in attrs.get("transform", ()))
        if len(transform) != 6:
            msg = f"{store} carries no 6-coefficient transform; the frames cannot be placed."
            raise ValueError(msg)
        t0 = datetime.fromisoformat(str(attrs["t0"])).astimezone(IST)
        minutes = np.asarray(group["time_min"][:], dtype=np.float64).tolist()
        grid = RadarGrid(
            crs=str(attrs["crs"]),
            res_m=float(attrs["res_m"]),
            n_px=int(attrs["n_px"]),
            transform=transform,  # type: ignore[arg-type]
        )
        return cls(
            path=store,
            variable=variable,
            times=tuple(t0 + timedelta(minutes=float(m)) for m in minutes),
            grid=grid,
        )

    def read(self, indices: Sequence[int]) -> NDArray[np.floating]:
        import zarr

        group: Any = zarr.open_group(str(self.path), mode="r")
        cube = group[self.variable]
        frames = [np.asarray(cube[int(index)], dtype=np.float64) for index in indices]
        if not frames:
            return np.zeros((0, *self.grid.shape), dtype=np.float64)
        return np.stack(frames)


# ============================================================================ gauges
def read_gauges(path: Path | str) -> pd.DataFrame:
    """A bundle's ``gauges.csv`` with timezone-aware IST timestamps.

    A bundle without gauges is valid - a design storm has none - and gives an empty frame, the
    same rule ``varuna_cycle.sky_cycle.gauge_window`` follows; Z-R then falls back to
    Marshall-Palmer and the provenance says so.
    """
    import pandas as pd

    file = Path(path)
    if not file.is_file():
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))
    frame = pd.read_csv(file)
    frame["ts"] = pd.to_datetime(frame["ts"], format="ISO8601", utc=True).dt.tz_convert(IST)
    return frame


def _gauges_before(gauges: pd.DataFrame, frame_ts: datetime) -> pd.DataFrame:
    """Readings in the hour ending at ``frame_ts``: ``(frame_ts - 60 min, frame_ts]``."""
    if len(gauges.index) == 0:
        return gauges
    start = frame_ts - timedelta(minutes=PAIR_WINDOW_MIN)
    ts = gauges["ts"]
    return gauges[(ts > start) & (ts <= frame_ts)].reset_index(drop=True)


# ============================================================================ one frame
@dataclass(frozen=True, slots=True)
class AnalysisStep:
    """What one analysis step was built from - the provenance a run can publish."""

    valid_ts: datetime
    """The instant the step ends at; the Twin applies the rate over the 5 minutes before it."""

    frame_ts: datetime
    """The radar frame the step holds; never later than :attr:`valid_ts`."""

    n_frames: int
    """Frames of history the QC and pairing saw, including :attr:`frame_ts`."""

    clutter_applied: bool
    """False when fewer than six frames existed and the clutter test was skipped."""

    coverage_fraction: float
    clutter_fraction: float
    attenuation_flag: str
    zr_source: str
    zr_a: float
    zr_b: float
    zr_pairs: int
    zr_reason: str | None
    merge_method: str
    mfb: float
    merge_n_gauges: int
    max_gauge_error_pct: float | None
    gauge_rows: int
    """Gauge readings in the hour ending at :attr:`frame_ts` that were offered to the fit."""

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly, with times as ISO 8601 in IST."""
        return {
            "valid_ts": self.valid_ts.astimezone(IST).isoformat(),
            "frame_ts": self.frame_ts.astimezone(IST).isoformat(),
            "n_frames": self.n_frames,
            "clutter_applied": self.clutter_applied,
            "coverage_fraction": self.coverage_fraction,
            "clutter_fraction": self.clutter_fraction,
            "attenuation_flag": self.attenuation_flag,
            "zr_source": self.zr_source,
            "zr_a": self.zr_a,
            "zr_b": self.zr_b,
            "zr_pairs": self.zr_pairs,
            "zr_reason": self.zr_reason,
            "merge_method": self.merge_method,
            "mfb": self.mfb,
            "merge_n_gauges": self.merge_n_gauges,
            "max_gauge_error_pct": self.max_gauge_error_pct,
            "gauge_rows": self.gauge_rows,
        }


def _qc_without_clutter(frames: RadarFrames) -> QCResult:
    """:func:`~varuna_sky.qc.run_qc` with the clutter test skipped, stage for stage.

    Coverage and the attenuation shadow are the qc module's own functions on the same frames;
    only the clutter mask is empty, because a variance over fewer than six frames is not the
    test SPEC.md 11.1 specifies.
    """
    dbz = np.asarray(frames.dbz, dtype=np.float64)
    coverage = coverage_mask(dbz, frames.grid)
    latest = np.where(coverage, dbz[-1], np.nan)
    shadow = attenuation_shadow(latest, coverage, frames.grid)
    return QCResult(
        coverage=coverage,
        clutter=np.zeros(frames.grid.shape, dtype=bool),
        attenuation=shadow,
        attenuation_flag=attenuation_flag(shadow, coverage),
        dbz=latest,
    )


def frame_analysis(
    frames: RadarFrames, gauges: pd.DataFrame
) -> tuple[NDArray[np.floating], dict[str, Any]]:
    """QC, Z-R and gauge merge of the newest frame in ``frames`` - ``run_sky`` steps 1 to 3.

    Args:
        frames: the analysed frame last, with the history QC and pairing may use before it.
        gauges: readings no later than the analysed frame (the caller enforces the window).

    Returns:
        The merged rain field on the Sky grid in mm/h (``nan``-free, non-negative) and the
        stage facts :class:`AnalysisStep` records.
    """
    clutter_applied = frames.n_frames >= CLUTTER_FRAMES
    qc = run_qc(frames) if clutter_applied else _qc_without_clutter(frames)
    zr, pairs = run_zr(frames, gauges, qc)
    merge = merge_gauges(rain_from_dbz(qc.dbz, zr), pairs, frames.grid, zr)
    facts: dict[str, Any] = {
        "frame_ts": frames.latest_ts,
        "n_frames": frames.n_frames,
        "clutter_applied": clutter_applied,
        "coverage_fraction": round(qc.coverage_fraction, 6),
        "clutter_fraction": round(qc.clutter_fraction, 6),
        "attenuation_flag": qc.attenuation_flag,
        "zr_source": zr.source,
        "zr_a": float(zr.a),
        "zr_b": float(zr.b),
        "zr_pairs": int(zr.n_pairs),
        "zr_reason": zr.reason,
        "merge_method": merge.method,
        "mfb": float(merge.mfb),
        "merge_n_gauges": int(merge.n_gauges),
        "max_gauge_error_pct": merge.max_gauge_error_pct,
        "gauge_rows": len(gauges.index),
    }
    return np.asarray(merge.rain_mm_h, dtype=np.float64), facts


# ============================================================================ the series
@dataclass(frozen=True, slots=True)
class AnalysisSeries:
    """Analysis rain on the Sky grid, one field per 5-minute step.

    ``rain_mm_h`` is ``(n_steps, n_px, n_px)``; ``times[k]`` is the instant step ``k`` ends at.
    """

    rain_mm_h: NDArray[np.floating]
    times: tuple[datetime, ...]
    grid: RadarGrid
    steps: tuple[AnalysisStep, ...]
    step_min: float
    notes: tuple[str, ...]
    elapsed_ms: int
    """Wall time of the whole series, for the cost-per-step figure the chunk reports."""

    @property
    def n_steps(self) -> int:
        return int(self.rain_mm_h.shape[0])

    def on_aoi(self, aoi: AoiGrid) -> NDArray[np.floating]:
        """``(n_steps, height, width)`` on the city grid, through the forecast's own resample."""
        return resample_to_aoi(self.rain_mm_h, self.grid, aoi)

    def aoi_mean_mm_h(self, aoi: AoiGrid) -> NDArray[np.floating]:
        """AOI-mean rain rate per step, identical to the mean of :meth:`on_aoi` per step."""
        weights = aoi_mean_weights(self.grid, aoi)
        return np.tensordot(self.rain_mm_h, weights, axes=((1, 2), (0, 1)))

    def aoi_accumulation_mm(self, aoi: AoiGrid) -> float:
        """AOI-mean depth the series delivers, as the Twin integrates it (rate x step)."""
        return float(self.aoi_mean_mm_h(aoi).sum() * self.step_min / 60.0)


def _valid_instants(t_from: datetime, t_to: datetime, step_min: float) -> tuple[datetime, ...]:
    if t_from.tzinfo is None or t_to.tzinfo is None:
        msg = "t_from and t_to must carry a time zone (+05:30); a naive instant cannot be placed."
        raise ValueError(msg)
    if step_min <= 0:
        msg = f"step_min must be positive, got {step_min}"
        raise ValueError(msg)
    span_min = (t_to - t_from).total_seconds() / 60.0
    n_steps = round(span_min / step_min)
    if n_steps < 1 or abs(n_steps * step_min - span_min) > 1e-6:
        msg = (
            f"The span {t_from.astimezone(IST):%H:%M}-{t_to.astimezone(IST):%H:%M} IST is not a "
            f"positive whole number of {step_min:g}-minute steps."
        )
        raise ValueError(msg)
    step = timedelta(minutes=step_min)
    return tuple(t_from + (k + 1) * step for k in range(n_steps))


def _held_frame(times: tuple[datetime, ...], instant: datetime) -> int:
    """Index of the newest frame at or before ``instant``, within :data:`MAX_FRAME_AGE_MIN`."""
    candidates = [index for index, ts in enumerate(times) if ts <= instant]
    if not candidates:
        first = times[0].astimezone(IST) if times else None
        msg = (
            f"No radar frame at or before {instant.astimezone(IST):%H:%M} IST"
            + (f"; the first is {first:%H:%M} IST" if first else "; the cube is empty")
            + ". Start the analysis at or after the first frame."
        )
        raise ValueError(msg)
    index = candidates[-1]
    age_min = (instant - times[index]).total_seconds() / 60.0
    if age_min > MAX_FRAME_AGE_MIN:
        msg = (
            f"The newest radar frame before {instant.astimezone(IST):%H:%M} IST is "
            f"{age_min:g} minutes old, more than the {MAX_FRAME_AGE_MIN:g} a frame may be held. "
            "The radar has a gap here; the analysis refuses to stretch one frame across it."
        )
        raise ValueError(msg)
    return index


def _series_notes(steps: Sequence[AnalysisStep], step_min: float) -> tuple[str, ...]:
    """The honesty labels a series has earned, each a statement about these steps (rule 6)."""
    frames = {step.frame_ts: step for step in steps}
    n_frames = len(frames)
    notes = [
        f"Observed rain from Sky's analysis of {n_frames} radar frames (QC, Z-R, gauge merge); "
        "no nowcast and no truth field.",
        f"Each radar frame is held for the {step_min:g}-minute steps up to the next frame.",
    ]
    skipped = sum(1 for step in frames.values() if not step.clutter_applied)
    if skipped:
        notes.append(
            f"Clutter QC skipped on {skipped} of {n_frames} frames: fewer than "
            f"{CLUTTER_FRAMES} frames of history existed."
        )
    sources = Counter(step.zr_source for step in frames.values())
    if sources.get("marshall_palmer"):
        notes.append(
            f"Z-R is Marshall-Palmer on {sources['marshall_palmer']} of {n_frames} frames and "
            f"fitted on {sources.get('adaptive', 0)}."
        )
    methods = Counter(step.merge_method for step in frames.values())
    if methods.get("none"):
        notes.append(f"No gauge merge on {methods['none']} of {n_frames} frames: no usable gauges.")
    return tuple(notes)


def analysis_series(
    source: FrameSource,
    gauges: pd.DataFrame,
    t_from: datetime,
    t_to: datetime,
    *,
    step_min: float = float(STEP_MIN),
) -> AnalysisSeries:
    """Analysis rain on the Sky grid for the steps ending in ``(t_from, t_to]``.

    Args:
        source: the radar frames, read by index and only for the frames a step needs.
        gauges: the gauge table (SPEC.md 10.2 columns, timezone-aware ``ts``). Rows after a
            step's frame are filtered out before the stages see them.
        t_from: where the series starts; the first step ends ``step_min`` later.
        t_to: the instant the last step ends at - typically the cycle a catch-up reaches.
        step_min: the Twin's step, 5 minutes.

    Raises:
        ValueError: the span is not a whole number of steps, an instant has no radar frame at or
            before it, or the radar has a gap longer than :data:`MAX_FRAME_AGE_MIN`.
    """
    mark = perf_counter()
    instants = _valid_instants(t_from, t_to, step_min)
    times = tuple(source.times)
    held = [_held_frame(times, instant) for instant in instants]

    fields: dict[int, NDArray[np.floating]] = {}
    facts: dict[int, dict[str, Any]] = {}
    window = timedelta(minutes=FRAME_WINDOW_MIN)
    for index in sorted(set(held)):
        frame_ts = times[index]
        history = [j for j in range(index + 1) if times[j] >= frame_ts - window]
        frames = RadarFrames(
            dbz=source.read(history),
            times=tuple(times[j] for j in history),
            grid=source.grid,
        )
        fields[index], facts[index] = frame_analysis(frames, _gauges_before(gauges, frame_ts))

    rain = np.stack([fields[index] for index in held]).astype(np.float64, copy=False)
    steps = tuple(
        AnalysisStep(valid_ts=instant, **facts[index])
        for instant, index in zip(instants, held, strict=True)
    )
    elapsed_ms = round((perf_counter() - mark) * 1000.0)
    notes = _series_notes(steps, step_min)
    log.info(
        "sky.analysis",
        t_from=t_from.astimezone(IST).isoformat(),
        t_to=t_to.astimezone(IST).isoformat(),
        steps=len(steps),
        frames=len(fields),
        elapsed_ms=elapsed_ms,
        max_mm_h=round(float(rain.max()), 3) if rain.size else 0.0,
    )
    return AnalysisSeries(
        rain_mm_h=rain,
        times=instants,
        grid=source.grid,
        steps=steps,
        step_min=float(step_min),
        notes=notes,
        elapsed_ms=int(elapsed_ms),
    )


# ============================================================================ from a bundle
@dataclass(frozen=True, slots=True)
class AnalysisRain:
    """Analysis rain on the city's 30 m grid, the forcing a hot-started Twin runs on."""

    rain_mm_h: NDArray[np.floating]
    """``(n_steps, height, width)`` in mm/h; step ``k`` ends at ``times[k]``."""

    times: tuple[datetime, ...]
    aoi: AoiGrid
    series: AnalysisSeries
    bundle: str
    version: str = ANALYSIS_VERSION

    @property
    def notes(self) -> tuple[str, ...]:
        return self.series.notes

    def aoi_mean_mm_h(self) -> NDArray[np.floating]:
        return np.asarray(self.rain_mm_h.mean(axis=(1, 2)), dtype=np.float64)

    def aoi_accumulation_mm(self) -> float:
        return float(self.aoi_mean_mm_h().sum() * self.series.step_min / 60.0)


def _bundle_root(bundle: str | Path) -> Path:
    path = Path(bundle)
    if path.is_dir():
        return path
    return bundle_dir(str(bundle))


def analysis_rain(
    bundle: str | Path,
    t_from: datetime,
    t_to: datetime,
    aoi: AoiGrid | str | None = None,
    *,
    step_min: float = float(STEP_MIN),
) -> AnalysisRain:
    """A bundle's analysis rain on the city grid for the steps ending in ``(t_from, t_to]``.

    Reads ``radar/frames.zarr`` frame by frame and ``gauges.csv``; never ``truth/rain.zarr``.

    Args:
        bundle: a bundle id under ``bundles/`` (or ``VARUNA_BUNDLES_DIR``), or its folder.
        t_from: where the spin-up or catch-up starts.
        t_to: the instant it reaches.
        aoi: the city grid, a city slug, or ``None`` to use the city the manifest names.
        step_min: the Twin's step.

    Raises:
        FileNotFoundError: the bundle's radar cube or the city grid is not built, with the
            command that builds it.
        ValueError: see :func:`analysis_series`.
    """
    root = _bundle_root(bundle)
    source = ZarrFrameSource.open(root.joinpath(*RADAR_MEMBER))
    if aoi is None:
        manifest = root / MANIFEST_MEMBER
        if not manifest.is_file():
            msg = f"{root} has no {MANIFEST_MEMBER}; pass the city grid explicitly."
            raise FileNotFoundError(msg)
        aoi = str(json.loads(manifest.read_text(encoding="utf-8"))["city"])
    grid = aoi if isinstance(aoi, AoiGrid) else load_aoi_grid(aoi)
    series = analysis_series(
        source, read_gauges(root / GAUGES_MEMBER), t_from, t_to, step_min=step_min
    )
    return AnalysisRain(
        rain_mm_h=series.on_aoi(grid),
        times=series.times,
        aoi=grid,
        series=series,
        bundle=root.name,
    )

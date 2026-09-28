"""Rain skill by lead time over one event's baked cycles (SPEC.md 7.10, 11.12; task P3.6).

:mod:`varuna_verify.rain_skill` scores one Sky forecast against the truth field. ``/verify``
needs the event: every baked cycle of the bundle, pooled by lead time, with the baseline that
says whether the nowcast earned its keep. This module builds that, from artifacts only (rule 6):

* **the forecasts** are each run's own published rain products - ``rain/quantiles.zarr`` for the
  ensemble mean and median, ``rain/cube.zarr`` for the members' exceedance probabilities;
* **the truth** is the bundle's ``truth/rain.zarr``, the storm designer's field, labelled as a
  reconstruction wherever it is quoted (rule 7);
* **the persistence baseline** is the analysis each nowcast started from - the bundle's radar
  frames and gauge readings put through Sky's own QC, Z-R and gauge merge exactly as
  :func:`varuna_sky.pipeline.run_sky` runs them at the cycle - held unchanged for three hours.
  It is recomputed, not stored, so every cycle records whether the recomputed Z-R matches the
  one its run published (``persistence.matches_cycle``). A baseline that does not match is still
  scored, and says so.

**What is pooled.** For each lead time, the contingency tables of every cycle that reaches that
lead inside the truth window are summed, and the scores come from the sums. The truth window of
the 2 July reconstruction ends at 09:40 IST, so a cycle at 08:10 contributes leads to +90 min and
no further: the number of cycles falls with lead, and every row carries ``n_cycles`` and its
event-pixel count so a reader can see how thin the long leads are.

**Spread across cycles.** Beside each pooled score, ``spread`` gives the 10th and 90th
percentiles of the same score computed cycle by cycle, for the ensemble mean and median, once at
least :data:`SPREAD_MIN_CYCLES` cycles have a defined score at that lead. It says how much the
pooled number depends on which cycle you look at; it is not a confidence interval.

**Scopes.** ``aoi`` is the Sky pixels (500 m) whose centres fall inside the city's 30 m grid, the
streets VARUNA forecasts for. ``domain`` is every pixel the radar covers on the 60 km Sky domain,
a larger sample of the same storm. Pixels outside the radar's coverage are never scored.

**The useful skill horizon** is computed, never typed. At 20 mm/h, in the AOI, for the ensemble
mean: walk the leads outward; the first lead at which the forecast's CSI is below persistence's
CSI or below :data:`CSI_FLOOR` is the *first failure*, and the horizon is the lead before it. A
lead with no event forecast or observed has no CSI and stops the walk as *undetermined*. A
forecast that never fails inside the scored leads reports the last scored lead and says the
horizon lies beyond it.

Definitions (strictly greater than, matching the ``P(> 20 mm/h)`` products of SPEC.md 11.1):
``CSI = H / (H + M + F)``, ``POD = H / (H + M)``, ``FAR = F / (H + F)``, each ``None`` when its
denominator is empty; ``Brier = mean((p - o)^2)`` with ``p`` the fraction of members above the
threshold; persistence's Brier treats its yes/no as ``p`` of 1 or 0; the climatological Brier is
``o_bar * (1 - o_bar)`` with ``o_bar`` the lead's own observed base rate, so its skill score says
whether the probabilities beat the sample's own frequency.

Determinism (rule 8): nothing is random, runs are read in name order, and every sum is taken in
that order, so the same artifacts give the same bytes.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog
from varuna_schemas.constants import IST
from varuna_schemas.paths import bundles_dir, runs_dir
from varuna_sky.types import RadarGrid

from varuna_verify.rain_skill import TRUTH_NOTE, TRUTH_SOURCE, TruthCube, load_truth_cube

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.verify.rain_event")

__all__ = [
    "CSI_FLOOR",
    "HEADLINE_THRESHOLD_MM_H",
    "RAIN_EVENT_VERSION",
    "THRESHOLDS_MM_H",
    "CycleForecast",
    "cycle_analysis",
    "event_rain_skill",
    "load_cycle",
    "rain_event_inputs",
    "score_cycles",
    "skill_horizon",
]

RAIN_EVENT_VERSION = "2"
"""Version of the scoring rules here; a change to any definition bumps it."""

THRESHOLDS_MM_H: tuple[float, ...] = (10.0, 20.0, 40.0)
"""Rain rates scored. 20 and 40 are SPEC.md 11.12's; 10 mm/h is added because the member cube
makes it free and it is where a monsoon street starts to pond."""

HEADLINE_THRESHOLD_MM_H = 20.0
"""The threshold the useful skill horizon is computed at."""

CSI_FLOOR = 0.5
"""The CSI below which a rain forecast is not called useful, whatever persistence manages. A
design choice stated with the horizon, not a spec number."""

FORECASTS: tuple[str, ...] = ("mean", "p50", "persistence")
"""The deterministic forecasts scored: ensemble mean, ensemble median, and the held analysis."""

SPREAD_FORECASTS: tuple[str, ...] = ("mean", "p50")
"""The forecasts whose cycle-to-cycle spread is reported beside the pooled score."""

SPREAD_MIN_CYCLES = 3
"""Fewest cycles with a defined score at a lead before a p10-p90 spread is quoted. Two cycles
have no tenth percentile worth the name; the count is served either way."""

SPREAD_QUANTILES: tuple[float, float] = (0.1, 0.9)
"""The spread band: the 10th and 90th percentiles of the per-cycle scores (linear
interpolation), never a smoothing or a fit."""

PROBABILITY_BINS = 10
"""Reliability bins of width 0.1 over the members' exceedance fraction."""

LEAD_BANDS: tuple[tuple[int, int], ...] = ((5, 60), (65, 120), (125, 180))
"""Lead-time bands the reliability summary is split into, in minutes."""

SCOPES: tuple[str, ...] = ("aoi", "domain")

SCOPE_LABELS: dict[str, str] = {
    "aoi": "Sky pixels (500 m) whose centres fall inside the city grid",
    "domain": "Every pixel the radar covers on the 60 km Sky domain",
}

RAIN_CUBE = ("rain", "cube.zarr")
RAIN_QUANTILES = ("rain", "quantiles.zarr")
RADAR_MEMBER = ("radar", "frames.zarr")
GAUGES_MEMBER = "gauges.csv"
TRUTH_MEMBER = ("truth", "rain.zarr")

HISTORY_FRAMES = 3
"""Radar frames a cycle sees - ``varuna_cycle.sky_cycle.HISTORY_FRAMES``, restated because
``varuna_verify`` does not depend on the cycle package."""

GAUGE_WINDOW_MIN = 60.0
"""Gauge readings a cycle consumes - ``varuna_cycle.sky_cycle.GAUGE_WINDOW_MIN``."""

ZR_TOLERANCE = 1e-6
"""How close the recomputed Z-R must be to the one the run published to count as the same."""

TIME_TOLERANCE_S = 1.0

DECIMALS = 4
"""Rounding of every published score; counts stay integers."""

UNITS: dict[str, str] = {
    "rain_rate": "mm/h",
    "lead": "minutes after the cycle",
    "csi": "0 to 1, higher is better",
    "pod": "0 to 1, higher is better",
    "far": "0 to 1, lower is better",
    "brier": "0 to 1, lower is better",
    "brier_skill": "1 is perfect, 0 is no better than the reference, negative is worse",
    "mae": "mm/h",
    "counts": "Sky pixels (500 m x 500 m), pooled over cycles",
    "probability": "fraction of ensemble members above the threshold",
}

DEFINITIONS: dict[str, str] = {
    "event_pixel": "A pixel whose truth rain rate is strictly above the threshold.",
    "csi": "Critical success index, hits / (hits + misses + false alarms).",
    "pod": "Probability of detection, hits / (hits + misses).",
    "far": "False-alarm ratio, false alarms / (hits + false alarms).",
    "mean": "The ensemble mean rain rate, as the run published it in rain/quantiles.zarr.",
    "p50": "The ensemble median, the field the console draws.",
    "persistence": (
        "The analysis the nowcast started from - the cycle's radar frames and gauge readings "
        "through Sky's own QC, Z-R and gauge merge - held unchanged for every lead."
    ),
    "brier": "Mean squared difference between the members' exceedance fraction and 0 or 1.",
    "brier_skill_vs_persistence": "1 - Brier / Brier of persistence's yes-or-no forecast.",
    "brier_skill_vs_climatology": (
        "1 - Brier / (o * (1 - o)), with o the lead's observed base rate in the sample."
    ),
    "horizon": (
        "At 20 mm/h in the AOI for the ensemble mean: the last lead before CSI first falls below "
        "persistence's CSI or below 0.5. A lead with nothing forecast or observed stops the walk "
        "as undetermined."
    ),
    "beats_persistence_leads_min": (
        "Every lead at which the forecast's CSI is strictly above persistence's, whether or not "
        "it lies inside the horizon."
    ),
}


# ============================================================================ one cycle
@dataclass(frozen=True, slots=True)
class CycleForecast:
    """What scoring needs from one baked cycle, all on the Sky grid."""

    run_id: str
    cycle_ts: datetime
    times: tuple[datetime, ...]
    grid: RadarGrid
    mean: NDArray[np.floating]
    """``(n_steps, n_px, n_px)`` ensemble mean, mm/h."""

    p50: NDArray[np.floating]
    probs: dict[float, NDArray[np.floating] | None]
    """Members' exceedance fraction per threshold; ``None`` where the run keeps no way to compute it."""

    persistence: NDArray[np.floating]
    """``(n_px, n_px)`` analysis at the cycle, mm/h."""

    coverage: NDArray[np.bool_]
    """Pixels the radar sees at the cycle."""

    aoi_bounds: tuple[float, float, float, float] | None
    n_members: int
    member_cube: bool
    products_source: str
    persistence_facts: dict[str, Any] = field(default_factory=dict)


def _stamp(value: Any) -> datetime:
    ts = datetime.fromisoformat(str(value))
    if ts.tzinfo is None:
        msg = f"cycle time {value!r} carries no offset; a run's cycle_ts is always IST-aware"
        raise ValueError(msg)
    return ts.astimezone(IST)


def _grid_from_attrs(attrs: dict[str, Any]) -> RadarGrid:
    transform = tuple(float(v) for v in attrs["transform"])
    if len(transform) != 6:
        msg = f"a rain product carries a {len(transform)}-coefficient transform, not six"
        raise ValueError(msg)
    return RadarGrid(
        crs=str(attrs["crs"]),
        res_m=float(attrs["res_m"]),
        n_px=int(attrs["n_px"]),
        transform=transform,  # type: ignore[arg-type]
    )


def _times(attrs: dict[str, Any], minutes: Iterable[float]) -> tuple[datetime, ...]:
    t0 = _stamp(attrs["t0"])
    return tuple(t0 + timedelta(minutes=float(m)) for m in minutes)


def _aoi_bounds(attrs: dict[str, Any]) -> tuple[float, float, float, float] | None:
    aoi = attrs.get("aoi")
    if not isinstance(aoi, dict) or len(aoi.get("bounds") or ()) != 4:
        return None
    left, bottom, right, top = (float(v) for v in aoi["bounds"])
    return (left, bottom, right, top)


def cycle_analysis(
    bundle_root: Path, cycle_ts: datetime, *, gauges: pd.DataFrame | None = None
) -> tuple[NDArray[np.floating], NDArray[np.bool_], dict[str, Any]]:
    """The analysis a cycle's nowcast started from: Sky steps 1-3 on the cycle's own inputs.

    The last :data:`HISTORY_FRAMES` radar frames at or before ``cycle_ts`` and the gauge readings
    of the :data:`GAUGE_WINDOW_MIN` minutes ending at it - the inputs
    ``varuna_cycle.sky_cycle`` hands :func:`~varuna_sky.pipeline.run_sky` - through
    :func:`~varuna_sky.qc.run_qc`, :func:`~varuna_sky.zr.run_zr`,
    :func:`~varuna_sky.motion.rain_from_dbz` and :func:`~varuna_sky.merge.merge_gauges`.
    Nothing is reimplemented, and ``truth/rain.zarr`` is never read.

    Returns:
        The analysis in mm/h, the radar coverage mask, and the Z-R and frame facts.

    Raises:
        FileNotFoundError: the bundle has no radar cube.
        ValueError: fewer than three frames lie at or before ``cycle_ts``.
    """
    from varuna_sky.analysis import ZarrFrameSource, read_gauges
    from varuna_sky.merge import merge_gauges
    from varuna_sky.motion import rain_from_dbz
    from varuna_sky.qc import run_qc
    from varuna_sky.types import RadarFrames
    from varuna_sky.zr import run_zr

    source = ZarrFrameSource.open(bundle_root.joinpath(*RADAR_MEMBER))
    available = [i for i, ts in enumerate(source.times) if ts <= cycle_ts]
    if len(available) < HISTORY_FRAMES:
        msg = (
            f"{cycle_ts:%H:%M} IST has {len(available)} radar frames before it and a cycle needs "
            f"{HISTORY_FRAMES}."
        )
        raise ValueError(msg)
    taken = available[-HISTORY_FRAMES:]
    frames = RadarFrames(
        dbz=source.read(taken), times=tuple(source.times[i] for i in taken), grid=source.grid
    )
    readings = gauges if gauges is not None else read_gauges(bundle_root / GAUGES_MEMBER)
    if len(readings.index):
        start = cycle_ts - timedelta(minutes=GAUGE_WINDOW_MIN)
        readings = readings[(readings["ts"] > start) & (readings["ts"] <= cycle_ts)]
        readings = readings.reset_index(drop=True)
    qc = run_qc(frames)
    zr, pairs = run_zr(frames, readings, qc)
    merge = merge_gauges(rain_from_dbz(qc.dbz, zr), pairs, frames.grid, zr)
    facts = {
        "frame_ts": frames.latest_ts.astimezone(IST).isoformat(),
        "zr_a": float(zr.a),
        "zr_b": float(zr.b),
        "zr_source": str(zr.source),
        "merge_method": str(merge.method),
    }
    rain = np.asarray(merge.rain_mm_h, dtype=np.float64)
    return rain, np.asarray(qc.coverage, dtype=bool), facts


def load_cycle(
    run_dir: Path, bundle_root: Path, *, gauges: pd.DataFrame | None = None
) -> CycleForecast:
    """One baked run's rain products and the analysis its nowcast started from.

    Raises:
        FileNotFoundError: the run keeps neither ``rain/quantiles.zarr`` nor ``rain/cube.zarr``.
    """
    import zarr

    quantiles_path = run_dir.joinpath(*RAIN_QUANTILES)
    cube_path = run_dir.joinpath(*RAIN_CUBE)
    has_quantiles = quantiles_path.is_dir()
    has_cube = cube_path.is_dir()
    if not has_quantiles and not has_cube:
        msg = f"{run_dir.name} keeps no rain products (rain/quantiles.zarr or rain/cube.zarr)"
        raise FileNotFoundError(msg)

    members: NDArray[np.floating] | None = None
    cube_attrs: dict[str, Any] = {}
    if has_cube:
        cube: Any = zarr.open_group(str(cube_path), mode="r")
        cube_attrs = dict(cube.attrs)
        members = np.asarray(cube["rain"][:], dtype=np.float64)
        minutes = np.asarray(cube["time_min"][:], dtype=np.float64).tolist()

    if has_quantiles:
        q: Any = zarr.open_group(str(quantiles_path), mode="r")
        attrs: dict[str, Any] = dict(q.attrs)
        mean = np.asarray(q["mean"][:], dtype=np.float64)
        p50 = np.asarray(q["p50"][:], dtype=np.float64)
        minutes = np.asarray(q["time_min"][:], dtype=np.float64).tolist()
        stored = {
            float(t): np.asarray(q[f"p_gt_{int(t)}"][:], dtype=np.float64)
            for t in THRESHOLDS_MM_H
            if f"p_gt_{int(t)}" in set(q.array_keys())
        }
        source = "rain/quantiles.zarr"
    else:
        assert members is not None
        attrs = cube_attrs
        mean = members.mean(axis=0)
        p50 = np.median(members, axis=0)
        stored = {}
        source = "rain/cube.zarr"

    probs: dict[float, NDArray[np.floating] | None] = {}
    for threshold in THRESHOLDS_MM_H:
        if members is not None:
            probs[threshold] = (members > threshold).mean(axis=0)
        else:
            probs[threshold] = stored.get(threshold)

    cycle_ts = _stamp(attrs.get("cycle_ts") or cube_attrs["cycle_ts"])
    grid = _grid_from_attrs(attrs)
    persistence, coverage, facts = cycle_analysis(bundle_root, cycle_ts, gauges=gauges)
    published = cube_attrs or attrs
    if "zr_a" in published and "zr_b" in published:
        facts["matches_cycle"] = bool(
            abs(float(published["zr_a"]) - facts["zr_a"]) <= ZR_TOLERANCE
            and abs(float(published["zr_b"]) - facts["zr_b"]) <= ZR_TOLERANCE
        )
    else:
        facts["matches_cycle"] = None
    n_members = int(attrs.get("n_members") or (members.shape[0] if members is not None else 0))
    return CycleForecast(
        run_id=run_dir.name,
        cycle_ts=cycle_ts,
        times=_times(attrs, minutes),
        grid=grid,
        mean=mean,
        p50=p50,
        probs=probs,
        persistence=persistence,
        coverage=coverage,
        aoi_bounds=_aoi_bounds(attrs),
        n_members=n_members,
        member_cube=members is not None,
        products_source=source,
        persistence_facts=facts,
    )


# ============================================================================ accumulation
def _blank_counts() -> dict[str, int]:
    return {"hits": 0, "misses": 0, "false_alarms": 0, "correct_negatives": 0}


@dataclass
class _ThresholdSums:
    counts: dict[str, dict[str, int]] = field(
        default_factory=lambda: {name: _blank_counts() for name in FORECASTS}
    )
    event_pixels: int = 0
    brier_n: int = 0
    brier_sum: float = 0.0
    brier_persistence_sum: float = 0.0
    per_cycle: dict[str, list[tuple[int, int, int]]] = field(
        default_factory=lambda: {name: [] for name in SPREAD_FORECASTS}
    )


@dataclass
class _LeadSums:
    cycles: list[str] = field(default_factory=list)
    n: int = 0
    abs_err: dict[str, float] = field(default_factory=lambda: dict.fromkeys(FORECASTS, 0.0))
    by_threshold: dict[float, _ThresholdSums] = field(
        default_factory=lambda: {t: _ThresholdSums() for t in THRESHOLDS_MM_H}
    )


@dataclass
class _ReliabilitySums:
    n: NDArray[np.int64] = field(default_factory=lambda: np.zeros(PROBABILITY_BINS, np.int64))
    sum_p: NDArray[np.float64] = field(
        default_factory=lambda: np.zeros(PROBABILITY_BINS, np.float64)
    )
    sum_o: NDArray[np.int64] = field(default_factory=lambda: np.zeros(PROBABILITY_BINS, np.int64))


def _contingency(forecast: NDArray[np.floating], yes: NDArray[np.bool_], t: float) -> list[int]:
    said = forecast > t
    return [
        int(np.count_nonzero(said & yes)),
        int(np.count_nonzero(~said & yes)),
        int(np.count_nonzero(said & ~yes)),
        int(np.count_nonzero(~said & ~yes)),
    ]


def _band(lead: int) -> int | None:
    for index, (low, high) in enumerate(LEAD_BANDS):
        if low <= lead <= high:
            return index
    return None


def _match(times: Sequence[datetime], truth: TruthCube) -> list[tuple[int, int]]:
    instants = {round(ts.timestamp()): i for i, ts in enumerate(truth.times)}
    pairs: list[tuple[int, int]] = []
    for step, ts in enumerate(times):
        key = round(ts.timestamp())
        for probe in (key, key - 1, key + 1):
            if probe in instants and abs(probe - ts.timestamp()) <= TIME_TOLERANCE_S:
                pairs.append((step, instants[probe]))
                break
    return pairs


def _scope_masks(cycle: CycleForecast) -> dict[str, NDArray[np.bool_]]:
    grid = cycle.grid
    a, _, c, _, e, f = grid.transform
    centres = np.arange(grid.n_px, dtype=np.float64) + 0.5
    xs = c + a * centres
    ys = f + e * centres
    masks: dict[str, NDArray[np.bool_]] = {"domain": cycle.coverage.copy()}
    if cycle.aoi_bounds is not None:
        left, bottom, right, top = cycle.aoi_bounds
        inside = ((ys >= bottom) & (ys <= top))[:, None] & ((xs >= left) & (xs <= right))[None, :]
        masks["aoi"] = inside & cycle.coverage
    return masks


def _check_grid(cycle: CycleForecast, truth: TruthCube) -> None:
    same = (
        cycle.grid.shape == truth.grid.shape
        and cycle.grid.crs == truth.grid.crs
        and abs(cycle.grid.res_m - truth.grid.res_m) < 1e-6
        and np.allclose(cycle.grid.transform, truth.grid.transform, atol=1e-6)
    )
    if not same:
        msg = f"{cycle.run_id} is not on the truth cube's grid"
        raise ValueError(msg)


def _r(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, DECIMALS)


def _scores(counts: dict[str, int]) -> dict[str, Any]:
    h, m, fa = counts["hits"], counts["misses"], counts["false_alarms"]
    return {
        **counts,
        "csi": _r(h / (h + m + fa)) if h + m + fa else None,
        "pod": _r(h / (h + m)) if h + m else None,
        "far": _r(fa / (h + fa)) if h + fa else None,
    }


def _spread(tables: Sequence[tuple[int, int, int]]) -> dict[str, Any]:
    """The p10-p90 of CSI, POD and FAR across cycles, each cycle scored on its own table.

    A cycle whose score has no denominator at this lead (nothing forecast and nothing seen) is
    left out and not counted, so ``n`` is the number of cycles the band is taken over.
    """
    per_metric: dict[str, list[float]] = {"csi": [], "pod": [], "far": []}
    for h, m, fa in tables:
        if h + m + fa:
            per_metric["csi"].append(h / (h + m + fa))
        if h + m:
            per_metric["pod"].append(h / (h + m))
        if h + fa:
            per_metric["far"].append(fa / (h + fa))
    out: dict[str, Any] = {}
    for metric, values in per_metric.items():
        if len(values) < SPREAD_MIN_CYCLES:
            out[metric] = {"n": len(values), "p10": None, "p90": None}
            continue
        low, high = np.quantile(np.asarray(values, dtype=np.float64), SPREAD_QUANTILES)
        out[metric] = {"n": len(values), "p10": _r(float(low)), "p90": _r(float(high))}
    return out


def _skill(score: float, reference: float) -> float | None:
    return None if reference <= 0 else _r(1.0 - score / reference)


# ============================================================================ scoring
def score_cycles(cycles: Sequence[CycleForecast], truth: TruthCube) -> dict[str, Any]:
    """Pool every cycle's contingency tables, Brier sums and reliability bins by lead time.

    Pure: artifacts in, a JSON-ready dict out. See the module docstring for every definition.
    """
    leads: dict[str, dict[int, _LeadSums]] = {scope: {} for scope in SCOPES}
    reliability: dict[str, dict[float, list[_ReliabilitySums]]] = {
        scope: {t: [_ReliabilitySums() for _ in LEAD_BANDS] for t in THRESHOLDS_MM_H}
        for scope in SCOPES
    }
    scope_pixels: dict[str, int] = dict.fromkeys(SCOPES, 0)
    per_cycle: list[dict[str, Any]] = []
    cycle_rows: list[dict[str, Any]] = []
    edges = np.linspace(0.0, 1.0, PROBABILITY_BINS + 1)

    for cycle in cycles:
        _check_grid(cycle, truth)
        masks = _scope_masks(cycle)
        for scope, mask in masks.items():
            scope_pixels[scope] = max(scope_pixels[scope], int(mask.sum()))
        pairs = _match(cycle.times, truth)
        curve: dict[str, dict[int, float | None]] = {"mean": {}, "persistence": {}}
        for step, frame in pairs:
            lead = round((cycle.times[step] - cycle.cycle_ts).total_seconds() / 60.0)
            observed = np.asarray(truth.rain_mm_h[frame], dtype=np.float64)
            fields = {
                "mean": cycle.mean[step],
                "p50": cycle.p50[step],
                "persistence": cycle.persistence,
            }
            band = _band(lead)
            for scope, mask in masks.items():
                valid = mask & np.isfinite(observed)
                for name in ("mean", "p50"):
                    valid &= np.isfinite(fields[name])
                n = int(valid.sum())
                if n == 0:
                    continue
                sums = leads[scope].setdefault(lead, _LeadSums())
                sums.cycles.append(cycle.run_id)
                sums.n += n
                o = observed[valid]
                values = {name: np.asarray(fields[name])[valid] for name in FORECASTS}
                for name in FORECASTS:
                    sums.abs_err[name] += float(np.abs(values[name] - o).sum())
                for threshold in THRESHOLDS_MM_H:
                    acc = sums.by_threshold[threshold]
                    yes = o > threshold
                    acc.event_pixels += int(yes.sum())
                    for name in FORECASTS:
                        cells = _contingency(values[name], yes, threshold)
                        for key, value in zip(
                            ("hits", "misses", "false_alarms", "correct_negatives"),
                            cells,
                            strict=True,
                        ):
                            acc.counts[name][key] += value
                        if name in acc.per_cycle:
                            acc.per_cycle[name].append((cells[0], cells[1], cells[2]))
                    if scope == "aoi" and threshold == HEADLINE_THRESHOLD_MM_H:
                        h, m, fa, _ = _contingency(values["mean"], yes, threshold)
                        curve["mean"][lead] = _r(h / (h + m + fa)) if h + m + fa else None
                        h, m, fa, _ = _contingency(values["persistence"], yes, threshold)
                        curve["persistence"][lead] = _r(h / (h + m + fa)) if h + m + fa else None
                    prob = cycle.probs.get(threshold)
                    if prob is None:
                        continue
                    p = np.clip(np.asarray(prob[step], dtype=np.float64)[valid], 0.0, 1.0)
                    outcome = yes.astype(np.float64)
                    acc.brier_n += n
                    acc.brier_sum += float(np.square(p - outcome).sum())
                    held = (values["persistence"] > threshold).astype(np.float64)
                    acc.brier_persistence_sum += float(np.square(held - outcome).sum())
                    if band is not None:
                        bins = np.clip(
                            np.digitize(p, edges[1:-1], right=False), 0, PROBABILITY_BINS - 1
                        )
                        rel = reliability[scope][threshold][band]
                        rel.n += np.bincount(bins, minlength=PROBABILITY_BINS).astype(np.int64)
                        rel.sum_p += np.bincount(bins, weights=p, minlength=PROBABILITY_BINS)
                        rel.sum_o += np.bincount(
                            bins, weights=outcome, minlength=PROBABILITY_BINS
                        ).astype(np.int64)
        lead_axis = sorted(curve["mean"])
        per_cycle.append(
            {
                "run_id": cycle.run_id,
                "cycle_ts": cycle.cycle_ts.isoformat(),
                "leads_min": lead_axis,
                "csi_mean_20": [curve["mean"][lead] for lead in lead_axis],
                "csi_persistence_20": [curve["persistence"][lead] for lead in lead_axis],
            }
        )
        scored = sorted(
            {round((cycle.times[s] - cycle.cycle_ts).total_seconds() / 60.0) for s, _ in pairs}
        )
        cycle_rows.append(
            {
                "run_id": cycle.run_id,
                "cycle_ts": cycle.cycle_ts.isoformat(),
                "n_members": cycle.n_members,
                "member_cube": cycle.member_cube,
                "products_source": cycle.products_source,
                "n_leads": len(cycle.times),
                "n_leads_scored": len(scored),
                "max_lead_scored_min": scored[-1] if scored else None,
                "persistence": cycle.persistence_facts,
            }
        )

    by_scope: dict[str, Any] = {}
    for scope in SCOPES:
        rows = [_lead_row(lead, sums) for lead, sums in sorted(leads[scope].items())]
        by_scope[scope] = {
            "label": SCOPE_LABELS[scope],
            "n_pixels": scope_pixels[scope],
            "by_lead": rows,
            "reliability": {
                _key(t): [
                    _reliability_band(LEAD_BANDS[i], sums)
                    for i, sums in enumerate(reliability[scope][t])
                ]
                for t in THRESHOLDS_MM_H
                if any(cycle.probs.get(t) is not None for cycle in cycles)
            },
            "horizons": [
                skill_horizon(rows, threshold=t, forecast=name)
                for name in ("mean", "p50")
                for t in THRESHOLDS_MM_H
            ],
        }
    by_scope["aoi"]["per_cycle"] = per_cycle
    headline = next(
        (
            h
            for h in by_scope["aoi"]["horizons"]
            if h["forecast"] == "mean" and h["threshold_mm_h"] == HEADLINE_THRESHOLD_MM_H
        ),
        None,
    )
    return {
        "cycles": cycle_rows,
        "n_cycles": len(cycle_rows),
        "by_scope": by_scope,
        "horizon": headline,
    }


def _key(threshold: float) -> str:
    return f"{int(threshold)}" if float(threshold).is_integer() else f"{threshold:g}"


def _lead_row(lead: int, sums: _LeadSums) -> dict[str, Any]:
    thresholds: dict[str, Any] = {}
    for threshold, acc in sums.by_threshold.items():
        base_rate = acc.event_pixels / sums.n if sums.n else None
        brier = acc.brier_sum / acc.brier_n if acc.brier_n else None
        brier_p = acc.brier_persistence_sum / acc.brier_n if acc.brier_n else None
        climatology = base_rate * (1.0 - base_rate) if base_rate is not None else None
        thresholds[_key(threshold)] = {
            "event_pixels": acc.event_pixels,
            "base_rate": _r(base_rate),
            **{name: _scores(acc.counts[name]) for name in FORECASTS},
            "brier": _r(brier),
            "brier_persistence": _r(brier_p),
            "brier_climatology": _r(climatology),
            "brier_skill_vs_persistence": (
                None if brier is None or brier_p is None else _skill(brier, brier_p)
            ),
            "brier_skill_vs_climatology": (
                None if brier is None or climatology is None else _skill(brier, climatology)
            ),
            "spread": {name: _spread(acc.per_cycle[name]) for name in SPREAD_FORECASTS},
        }
    return {
        "lead_min": lead,
        "n_cycles": len(sums.cycles),
        "n_pixels": sums.n,
        "mae_mm_h": {name: _r(sums.abs_err[name] / sums.n) for name in FORECASTS},
        "thresholds": thresholds,
    }


def _reliability_band(band: tuple[int, int], sums: _ReliabilitySums) -> dict[str, Any]:
    n_total = int(sums.n.sum())
    bins: list[dict[str, Any]] = []
    rel = res = 0.0
    o_bar = float(sums.sum_o.sum()) / n_total if n_total else None
    for k in range(PROBABILITY_BINS):
        n = int(sums.n[k])
        mean_p = float(sums.sum_p[k]) / n if n else None
        freq = float(sums.sum_o[k]) / n if n else None
        if n and mean_p is not None and freq is not None and o_bar is not None:
            rel += n * (mean_p - freq) ** 2
            res += n * (freq - o_bar) ** 2
        bins.append(
            {
                "p_from": _r(k / PROBABILITY_BINS),
                "p_to": _r((k + 1) / PROBABILITY_BINS),
                "n": n,
                "mean_p": _r(mean_p),
                "observed_frequency": _r(freq),
            }
        )
    return {
        "lead_from_min": band[0],
        "lead_to_min": band[1],
        "n": n_total,
        "base_rate": _r(o_bar),
        "reliability": _r(rel / n_total) if n_total else None,
        "resolution": _r(res / n_total) if n_total else None,
        "uncertainty": _r(o_bar * (1.0 - o_bar)) if o_bar is not None else None,
        "bins": bins,
    }


def skill_horizon(
    rows: Sequence[dict[str, Any]],
    *,
    threshold: float = HEADLINE_THRESHOLD_MM_H,
    forecast: str = "mean",
    floor: float = CSI_FLOOR,
) -> dict[str, Any]:
    """The useful skill horizon of one forecast at one threshold, from pooled lead rows.

    Walks the leads outward. The first lead whose CSI is below persistence's or below ``floor``
    is the first failure; the horizon is the last lead before it (0 when the first lead fails).
    A lead with no CSI stops the walk as undetermined. See the module docstring.
    """
    key = _key(threshold)
    result: dict[str, Any] = {
        "threshold_mm_h": threshold,
        "forecast": forecast,
        "csi_floor": floor,
        "lead_min": None,
        "status": "no_leads",
        "first_failure": None,
        "beats_persistence_leads_min": [],
    }
    last_ok = 0
    for row in rows:
        cell = row["thresholds"].get(key)
        if cell is None:
            continue
        csi = cell[forecast]["csi"]
        held = cell["persistence"]["csi"]
        if csi is not None and held is not None and csi > held:
            result["beats_persistence_leads_min"].append(row["lead_min"])
    for row in rows:
        cell = row["thresholds"].get(key)
        if cell is None:
            continue
        csi = cell[forecast]["csi"]
        held = cell["persistence"]["csi"]
        if csi is None:
            result.update(
                lead_min=last_ok,
                status="undetermined",
                first_failure={
                    "lead_min": row["lead_min"],
                    "reasons": ["no_event"],
                    "csi": None,
                    "persistence_csi": held,
                    "n_cycles": row["n_cycles"],
                },
            )
            return result
        reasons: list[str] = []
        if held is not None and csi < held:
            reasons.append("below_persistence")
        if csi < floor:
            reasons.append("below_floor")
        if reasons:
            result.update(
                lead_min=last_ok,
                status="found",
                first_failure={
                    "lead_min": row["lead_min"],
                    "reasons": reasons,
                    "csi": csi,
                    "persistence_csi": held,
                    "n_cycles": row["n_cycles"],
                },
            )
            return result
        last_ok = row["lead_min"]
    if rows:
        result.update(lead_min=last_ok, status="beyond_scored_range")
    return result


# ============================================================================ the event
def _event_runs(event: str, root: Path) -> list[Path]:
    """Baked or live runs of ``event`` that keep rain products, in name order."""
    if not root.is_dir():
        return []
    found: list[Path] = []
    for run in sorted(root.iterdir()):
        meta = run / "run.json"
        if not run.is_dir() or not meta.is_file():
            continue
        if not (run.joinpath(*RAIN_QUANTILES).is_dir() or run.joinpath(*RAIN_CUBE).is_dir()):
            continue
        try:
            bundle = json.loads(meta.read_text(encoding="utf-8")).get("bundle")
        except (OSError, ValueError):
            continue
        if bundle == event:
            found.append(run)
    return found


def _stat(path: Path) -> tuple[str, int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return (path.as_posix(), info.st_size, info.st_mtime_ns)


def rain_event_inputs(
    event: str, *, runs_root: Path | None = None, bundles_root: Path | None = None
) -> tuple[Any, ...]:
    """What :func:`event_rain_skill` reads, as file stats: the cache key a server keeps it by."""
    bundle = (bundles_root or Path(bundles_dir())) / event
    parts: list[Any] = [
        RAIN_EVENT_VERSION,
        _stat(bundle.joinpath(*TRUTH_MEMBER) / "zarr.json"),
        _stat(bundle.joinpath(*TRUTH_MEMBER) / "rain" / "zarr.json"),
        _stat(bundle.joinpath(*RADAR_MEMBER) / "zarr.json"),
        _stat(bundle / GAUGES_MEMBER),
    ]
    for run in _event_runs(event, runs_root or runs_dir()):
        parts.append(_stat(run / "run.json"))
        for member in (RAIN_QUANTILES, RAIN_CUBE):
            parts.append(_stat(run.joinpath(*member) / "zarr.json"))
    return tuple(parts)


def _unavailable(event: str, reason: str, *, missing: str, command: str | None) -> dict[str, Any]:
    """An event the scorer cannot score: what happened, what is missing, and the one fix.

    ``reason`` says what happened and never names a command; ``missing`` is ``"truth"`` (the
    bundle has no truth field), ``"runs"`` (no run keeps rain products) or ``"loadable_runs"``
    (every run failed to load); ``command`` is the single make target that supplies it, so a
    screen can print the fix once instead of guessing it from the prose.
    """
    return {
        "event": event,
        "available": False,
        "reason": reason,
        "missing": missing,
        "command": command,
        "version": RAIN_EVENT_VERSION,
    }


def event_rain_skill(
    event: str = "MUM-2019-07-02",
    *,
    runs_root: Path | None = None,
    bundles_root: Path | None = None,
) -> dict[str, Any]:
    """Rain skill by lead time for every baked cycle of ``event``, pooled, with its provenance.

    Returns ``{"available": False, "reason": ..., "missing": ..., "command": ...}`` rather than
    raising when the event has no truth field or no run keeps rain products, so a caller can serve
    the pin scores beside it.
    """
    started = perf_counter()
    bundle = (bundles_root or Path(bundles_dir())) / event
    truth_path = bundle.joinpath(*TRUTH_MEMBER)
    if not truth_path.exists():
        return _unavailable(
            event,
            f"{event} has no truth/rain.zarr, so there is no rain field to score against. Only a "
            "reconstructed or design-storm bundle carries one.",
            missing="truth",
            command=f"make bundle BUNDLE={event}",
        )
    runs = _event_runs(event, runs_root or runs_dir())
    if not runs:
        return _unavailable(
            event,
            f"No run of {event} keeps rain products (rain/quantiles.zarr or rain/cube.zarr).",
            missing="runs",
            command=f"make bake BUNDLE={event}",
        )
    truth = load_truth_cube(truth_path)

    from varuna_sky.analysis import read_gauges

    gauges = read_gauges(bundle / GAUGES_MEMBER)
    cycles: list[CycleForecast] = []
    skipped: list[dict[str, str]] = []
    for run in runs:
        try:
            cycles.append(load_cycle(run, bundle, gauges=gauges))
        except (FileNotFoundError, KeyError, ValueError) as error:
            skipped.append({"run_id": run.name, "reason": str(error)})
    if not cycles:
        return _unavailable(
            event,
            "Every run of the event failed to load: " + "; ".join(s["reason"] for s in skipped),
            missing="loadable_runs",
            command=f"make bake BUNDLE={event}",
        )

    scored = score_cycles(cycles, truth)
    no_members = [c.run_id for c in cycles if not c.member_cube]
    unavailable: dict[str, str] = {}
    if no_members:
        unavailable["probability_10_mm_h"] = (
            f"{len(no_members)} of {len(cycles)} runs keep no member cube (rain/cube.zarr), so "
            "their exceedance probability at 10 mm/h cannot be computed; their 20 and 40 mm/h "
            "probabilities are the run's own published p_gt_20 and p_gt_40."
        )
    mismatched = [c.run_id for c in cycles if c.persistence_facts.get("matches_cycle") is False]
    notes = [
        TRUTH_NOTE,
        f"Pooled over {len(cycles)} baked cycles of {event}: each lead sums the contingency "
        "tables of the cycles that reach it inside the truth window, so the long leads rest on "
        "fewer cycles, and every row says how many.",
        "Persistence is the analysis each nowcast started from, recomputed from the bundle's "
        "radar frames and gauges through Sky's own QC, Z-R and gauge merge, and held for three "
        "hours.",
        "The radar, the gauges and the truth are all the storm designer's reconstruction of "
        "2 July 2019; these scores show how the nowcast behaves on that storm, not how it "
        "compares with IMD's radar.",
    ]
    if mismatched:
        notes.append(
            f"The recomputed Z-R differs from the published one on {len(mismatched)} runs "
            f"({', '.join(mismatched)}); their persistence baseline is scored anyway."
        )
    elapsed_ms = round((perf_counter() - started) * 1000.0)
    log.info(
        "verify.rain_event",
        bundle=event,
        cycles=len(cycles),
        skipped=len(skipped),
        horizon=(scored["horizon"] or {}).get("lead_min"),
        ms=elapsed_ms,
    )
    first = cycles[0].grid
    return {
        "event": event,
        "available": True,
        "version": RAIN_EVENT_VERSION,
        "label": "Reconstructed replay",
        "truth": {
            "source": TRUTH_SOURCE,
            "note": TRUTH_NOTE,
            "t0": truth.times[0].isoformat(),
            "t1": truth.times[-1].isoformat(),
            "n_frames": truth.n_frames,
        },
        "grid": {"crs": first.crs, "res_m": first.res_m, "n_px": first.n_px},
        "units": UNITS,
        "definitions": DEFINITIONS,
        "thresholds_mm_h": list(THRESHOLDS_MM_H),
        "headline_threshold_mm_h": HEADLINE_THRESHOLD_MM_H,
        "csi_floor": CSI_FLOOR,
        "lead_bands_min": [list(band) for band in LEAD_BANDS],
        **scored,
        "runs_without_member_cube": no_members,
        "skipped_runs": skipped,
        "unavailable": unavailable,
        "provenance": {
            "generator": "varuna_verify.rain_event",
            "truth": f"bundles/{event}/{TRUTH_SOURCE}",
            "radar": f"bundles/{event}/radar/frames.zarr",
            "gauges": f"bundles/{event}/{GAUGES_MEMBER}",
            "forecasts": [
                f"data/runs/{c.run_id}/{c.products_source}"
                + (
                    ""
                    if not c.member_cube or c.products_source.endswith("cube.zarr")
                    else " + rain/cube.zarr"
                )
                for c in cycles
            ],
        },
        "notes": notes,
    }

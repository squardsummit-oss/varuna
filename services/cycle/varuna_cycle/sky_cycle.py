"""The rain half of a cycle: computed from a bundle, or read back from a baked run.

``make bake`` and the full orchestrator land in Phase 5. Until they do, this module is both
ways the console can get rain out of VARUNA-Sky, and it hands the API one shape either way
(:class:`RainCycle`), so a caller never has to know which one it got except by reading
:attr:`RainCycle.mode` - which is exactly what the run stamp shows (SPEC.md 7.2).

**Computing from a bundle** does the ingest half of a cycle - read the recent radar frames,
read the last hour of gauge readings, hand both to :func:`varuna_sky.pipeline.run_sky` - and
stops there. Nothing is written to ``data/runs``: a cycle that only ran Sky is not a run (a run
carries Twin, Flash and Pulse products too), and giving it a run id would put an identifier on
screen that resolves to nothing (rule 6). ``RainCycle.run_id`` is ``None`` and says so.

**The cycle time is snapped, and the response says so.** A replay triggers a cycle every five
simulated minutes from the bundle's ``t0`` (SPEC.md 11.11), so an arbitrary instant is
floored onto that ladder. The earliest cycle a bundle can serve is the one with three radar
frames behind it, because optical flow needs three (SPEC.md 11.1 step 4); for the demo
bundle, whose frames are ten minutes apart from 05:40 IST, that is 06:00.

**Caching.** The console asks for the spread band and the fan chart separately, and both are
the same cycle. :func:`run_bundle_cycle` therefore keeps the last few results keyed on the
bundle folder, the radar cube's timestamp and the cycle instant, so the second request is free
and a rebuilt bundle is never served from the first one's cache.

Determinism (rule 8): the seed comes from the bundle manifest, so two computes of one cycle
produce the same ensemble.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog
from varuna_replay.bundle import (
    RADAR_VARIABLE,
    RADAR_ZARR,
    BundleLayout,
    BundleNotFoundError,
    load_manifest,
    read_cube,
    read_cube_info,
)
from varuna_schemas.constants import CYCLE_PERIOD_MIN, IST, STEP_MIN
from varuna_schemas.models.bundle import BundleManifest
from varuna_schemas.models.run import RunMeta, RunMode
from varuna_sky.pipeline import run_sky
from varuna_sky.products import (
    EXCEEDANCE_MM_H,
    RAIN_CUBE,
    RAIN_QUANTILES,
    quantiles,
    read_attrs,
    read_sky_products,
)
from varuna_sky.types import RadarFrames, RadarGrid, SkyInputs, SkyProducts, SkyResult

if TYPE_CHECKING:  # pragma: no cover - pandas is imported lazily, as everywhere in the workspace
    import pandas as pd
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.cycle.sky")

__all__ = [
    "CACHE_SIZE",
    "GAUGE_WINDOW_MIN",
    "HISTORY_FRAMES",
    "RainCycle",
    "clear_cycle_cache",
    "cycle_window",
    "gauge_window",
    "has_rain_products",
    "radar_history",
    "read_run_rain",
    "run_bundle_cycle",
    "snap_to_cycle",
]

HISTORY_FRAMES = 3
"""Radar frames one cycle sees. Three is the minimum Lucas-Kanade needs (SPEC.md 11.1)."""

GAUGE_WINDOW_MIN = 60.0
"""Minutes of gauge readings a cycle consumes (SPEC.md 11.1: 'gauges (last 60 min)')."""

CACHE_SIZE = 3
"""Computed cycles kept in memory. One cycle's ensemble is about 40 MB on the Mumbai domain,
and the two rain endpoints plus a re-scrub to the same instant are the access pattern."""


@dataclass(frozen=True, slots=True)
class RainCycle:
    """One cycle's rain products and the provenance the run stamp shows.

    The same shape whether the products were baked by ``make bake`` or computed for this
    request; :attr:`mode` is the only difference a caller has to care about. Fields that a
    baked run does not record stay ``None`` rather than being filled with a plausible value.
    """

    products: SkyProducts
    cycle_ts: datetime
    city: str
    mode: RunMode
    run_id: str | None = None
    bundle: str | None = None
    nowcaster: str | None = None
    seed: int | None = None
    zr_a: float | None = None
    zr_b: float | None = None
    zr_source: str | None = None
    zr_pairs: int | None = None
    exceedance_mm_h: tuple[float, float] = EXCEEDANCE_MM_H
    stage_ms: dict[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def n_members(self) -> int:
        return int(np.asarray(self.products.aoi_hyetographs).shape[0])

    @property
    def n_steps(self) -> int:
        return len(self.products.times)

    @property
    def step_min(self) -> float:
        """Minutes between forecast steps, measured from the products' own time axis."""
        times = self.products.times
        if len(times) < 2:
            return float(STEP_MIN)
        return (times[1] - times[0]).total_seconds() / 60.0

    def aoi_band(self) -> NDArray[np.floating]:
        """``(3, n_steps)``: the p10/p50/p90 spread of the AOI-mean rain across the members.

        This is the band under the console's time bar (SPEC.md 7.2). It is taken with
        :func:`varuna_sky.products.quantiles`, so the band and the per-pixel quantiles on the
        map are the same three quantiles by the same interpolation - a band that meant
        something subtly different from the map would be worse than no band.
        """
        return quantiles(np.asarray(self.products.aoi_hyetographs, dtype=np.float64))


# ============================================================================ the cycle ladder
def _cycle_period(manifest: BundleManifest) -> timedelta:
    """The bundle's cycle cadence, defaulting to the five minutes of SPEC.md 11.11."""
    return timedelta(minutes=manifest.cadences.get("cycle", CYCLE_PERIOD_MIN))


def snap_to_cycle(manifest: BundleManifest, when: datetime) -> datetime:
    """Floor an instant onto the bundle's cycle ladder (``t0`` + k x cadence).

    Cycles happen on a ladder, not whenever a request arrives, so a scrub to 06:43 asks about
    the 06:40 cycle. The result is clamped into ``[t0, t1]``, the same way the replay clock
    clamps a seek rather than refusing it.
    """
    period = _cycle_period(manifest)
    elapsed = (when - manifest.t0).total_seconds()
    step = max(math.floor(elapsed / period.total_seconds()), 0)
    last = math.floor((manifest.t1 - manifest.t0).total_seconds() / period.total_seconds())
    return manifest.t0 + min(step, last) * period


def cycle_window(manifest: BundleManifest, layout: BundleLayout) -> tuple[datetime, datetime]:
    """First and last cycle instant this bundle can actually be forecast from.

    The first is the earliest ladder instant with :data:`HISTORY_FRAMES` radar frames at or
    before it; the last is the ladder instant at or before ``t1``.

    Raises:
        BundleNotFoundError: the radar cube is not built.
        ValueError: the cube holds fewer than :data:`HISTORY_FRAMES` frames, or no ladder
            instant has that many behind it.
    """
    info = read_cube_info(layout.radar, RADAR_VARIABLE)
    times = info.timestamps()
    if len(times) < HISTORY_FRAMES:
        msg = (
            f"{layout.bundle_id} has {len(times)} radar frames and a cycle needs "
            f"{HISTORY_FRAMES} to track storm motion. Run make bundle BUNDLE={layout.bundle_id}."
        )
        raise ValueError(msg)
    period = _cycle_period(manifest)
    elapsed = (times[HISTORY_FRAMES - 1] - manifest.t0).total_seconds()
    first = manifest.t0 + math.ceil(elapsed / period.total_seconds()) * period
    last = snap_to_cycle(manifest, manifest.t1)
    if first > last:
        msg = (
            f"{layout.bundle_id} has no cycle with {HISTORY_FRAMES} radar frames behind it "
            f"inside its own window. Run make bundle BUNDLE={layout.bundle_id}."
        )
        raise ValueError(msg)
    return (first, last)


# ============================================================================ cycle inputs
def radar_history(
    layout: BundleLayout, cycle_ts: datetime, n_frames: int = HISTORY_FRAMES
) -> RadarFrames:
    """The last ``n_frames`` radar frames at or before ``cycle_ts``, newest last.

    The georeference comes off the cube's own attributes, never from the city config: the
    frames and the grid have to be the pair that was written together (SPEC.md 11.1).

    Raises:
        BundleNotFoundError: the radar cube is not built.
        ValueError: fewer than ``n_frames`` frames lie at or before ``cycle_ts``.
    """
    info = read_cube_info(layout.radar, RADAR_VARIABLE)
    if len(info.transform) != 6:
        msg = f"{layout.relative(layout.radar)} carries no 6-coefficient transform."
        raise ValueError(msg)
    times = info.timestamps()
    available = [index for index, ts in enumerate(times) if ts <= cycle_ts]
    if len(available) < n_frames:
        msg = (
            f"{cycle_ts.astimezone(IST):%H:%M} IST has {len(available)} radar frames before it "
            f"and a cycle needs {n_frames}. The earliest cycle {layout.bundle_id} can forecast "
            f"is {times[n_frames - 1].astimezone(IST):%H:%M} IST or later."
        )
        raise ValueError(msg)
    taken = available[-n_frames:]
    cube = read_cube(layout.radar, RADAR_VARIABLE)
    grid = RadarGrid(
        crs=str(info.crs),
        res_m=float(info.res_m),
        n_px=int(info.n_px),
        transform=tuple(float(v) for v in info.transform),  # type: ignore[arg-type]
    )
    return RadarFrames(
        dbz=np.asarray(cube[taken], dtype=np.float64),
        times=tuple(times[index] for index in taken),
        grid=grid,
    )


def gauge_window(
    layout: BundleLayout, cycle_ts: datetime, minutes: float = GAUGE_WINDOW_MIN
) -> pd.DataFrame:
    """The bundle's gauge readings over the ``minutes`` ending at ``cycle_ts``.

    Timestamps are parsed as timezone-aware IST: ``varuna_sky.zr`` refuses naive ones, because
    a reading without an offset cannot be matched to a radar frame.

    **A bundle with no gauges is a valid bundle**, and returns an empty frame rather than raising.
    A design storm - `CHN-IDF-25yr`, the one the onboarding wizard runs on stage - is a synthetic
    hyetograph over a city that has no gauge network in the bundle, and there is nothing wrong with
    that: Sky's Z-R already falls back to Marshall-Palmer below eight co-located pairs
    (SPEC.md 11.1) and labels the run accordingly. Raising here made the first forecast for a
    newly onboarded city impossible, which is the whole point of the wizard.
    """
    import pandas as pd

    if not layout.gauges.is_file():
        log.info("sky.no_gauges", bundle=layout.bundle_id, note="Z-R falls back to Marshall-Palmer")
        return pd.DataFrame(columns=["ts", "station_id", "lat", "lon", "mm_5min"])
    frame = pd.read_csv(layout.gauges)
    frame["ts"] = pd.to_datetime(frame["ts"], format="ISO8601", utc=True).dt.tz_convert(IST)
    start = cycle_ts - timedelta(minutes=minutes)
    window = frame[(frame["ts"] > start) & (frame["ts"] <= cycle_ts)]
    return window.reset_index(drop=True)


# ============================================================================ computing one
def _radar_stamp(layout: BundleLayout) -> float:
    """Modification time of the radar cube, so a rebuilt bundle misses the cache.

    A Zarr store is a folder, so the stamp comes from the group's ``zarr.json``, which
    ``write_cube`` rewrites on every build - the same handle the replay router's ETag uses.
    """
    meta = layout.radar / "zarr.json"
    target = meta if meta.is_file() else layout.radar
    if not target.exists():
        msg = f"No {RADAR_ZARR} in {layout.bundle_id}"
        raise BundleNotFoundError(msg)
    return target.stat().st_mtime


@lru_cache(maxsize=CACHE_SIZE)
def _compute(root: str, stamp: float, cycle_ts: datetime, city: str, seed: int) -> SkyResult:
    """Run one cycle. Keyed by bundle folder and radar timestamp, never by bundle id alone."""
    layout = BundleLayout(root=Path(root))
    frames = radar_history(layout, cycle_ts)
    gauges = gauge_window(layout, cycle_ts)
    result = run_sky(SkyInputs(frames=frames, gauges=gauges, cycle_ts=cycle_ts, seed=seed), city)
    log.info(
        "cycle.sky_computed",
        bundle=layout.bundle_id,
        city=city,
        cycle_ts=cycle_ts.isoformat(),
        radar_frames=frames.n_frames,
        gauge_rows=len(gauges.index),
        total_ms=result.total_ms,
        nowcaster=result.ensemble.source,
    )
    return result


def clear_cycle_cache() -> None:
    """Forget every cached cycle. Tests call this; the demo never needs to."""
    _compute.cache_clear()


def run_bundle_cycle(bundle_id: str, when: datetime | None = None) -> RainCycle:
    """Compute one Sky cycle of ``bundle_id``, at ``when`` snapped onto the cycle ladder.

    Args:
        bundle_id: a folder under ``bundles/``.
        when: the instant the operator is looking at. Clamped into the bundle's forecastable
            window and floored onto the cycle ladder; the default is the earliest cycle the
            bundle can forecast.

    Returns:
        The cycle's rain, from cache when the same one was computed recently.

    Raises:
        BundleNotFoundError: the bundle, its radar cube or its gauges are not on disk.
        ValueError: the manifest is unreadable, or the bundle cannot forecast any cycle.
        FileNotFoundError: the city has not been built, with the ``make city`` command in the
            message (raised by :func:`varuna_sky.products.load_aoi_grid`).
    """
    layout = BundleLayout.for_bundle(bundle_id)
    manifest = load_manifest(layout.root)
    first, last = cycle_window(manifest, layout)
    target = min(max(snap_to_cycle(manifest, when), first), last) if when else first
    seed = int(manifest.seed)
    result = _compute(str(layout.root), _radar_stamp(layout), target, manifest.city, seed)
    return RainCycle(
        products=result.products,
        cycle_ts=target,
        city=manifest.city,
        mode="live",
        run_id=None,
        bundle=manifest.id,
        nowcaster=result.ensemble.source,
        seed=seed,
        zr_a=float(result.ensemble.zr.a),
        zr_b=float(result.ensemble.zr.b),
        zr_source=result.ensemble.zr.source,
        zr_pairs=int(result.ensemble.zr.n_pairs),
        stage_ms=dict(result.stage_ms),
        notes=tuple(result.notes),
    )


# ============================================================================ reading a run
def _float_pair(values: Any, fallback: tuple[float, float]) -> tuple[float, float]:
    """Two thresholds from a store attribute, or the compiled-in pair when it is absent."""
    try:
        first, second = (float(v) for v in list(values)[:2])
    except (TypeError, ValueError):
        return fallback
    return (first, second)


def has_rain_products(run_dir: Path) -> bool:
    """True when a run directory carries the rain products a rain endpoint can serve."""
    return (Path(run_dir) / RAIN_QUANTILES).exists()


def read_run_rain(run_dir: Path, meta: RunMeta) -> RainCycle:
    """The rain products of a baked run, with the provenance its two stores carry.

    ``rain/quantiles.zarr`` holds the products; ``rain/cube.zarr`` is the only artifact that
    records which nowcaster ran, with which seed and through which Z-R relation, so it is read
    for those and its absence leaves them ``None`` rather than guessed.

    Raises:
        FileNotFoundError: the run has no ``rain/quantiles.zarr``, with the command that
            writes one.
    """
    quantiles = Path(run_dir) / RAIN_QUANTILES
    if not quantiles.exists():
        msg = (
            f"Run {meta.run_id} has no {RAIN_QUANTILES}. Run "
            f"`make bake BUNDLE={meta.bundle or 'MUM-2019-07-02'}` to write the rain products "
            "for every cycle of the bundle."
        )
        raise FileNotFoundError(msg)
    products = read_sky_products(quantiles)
    store = read_attrs(quantiles)
    cube = Path(run_dir) / RAIN_CUBE
    cube_attrs = read_attrs(cube) if cube.exists() else {}
    zr_a = cube_attrs.get("zr_a")
    zr_b = cube_attrs.get("zr_b")
    return RainCycle(
        products=products,
        cycle_ts=meta.cycle_ts,
        city=meta.city,
        mode=meta.mode,
        run_id=meta.run_id,
        bundle=meta.bundle,
        nowcaster=cube_attrs.get("nowcast_source"),
        seed=int(cube_attrs["seed"]) if "seed" in cube_attrs else None,
        zr_a=float(zr_a) if zr_a is not None else None,
        zr_b=float(zr_b) if zr_b is not None else None,
        zr_source=cube_attrs.get("zr_source"),
        exceedance_mm_h=_float_pair(store.get("exceedance_mm_h"), EXCEEDANCE_MM_H),
        stage_ms=dict(meta.stage_ms),
        notes=tuple(meta.notes),
    )

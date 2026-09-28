"""One full cycle: radar in, a run directory out (SPEC.md 11.11, 10.3).

This is the stage that turns everything the engines can do into something the console can draw.
It runs Sky, feeds its rain onto the city grid, runs the coupled Twin, reduces the depth field
to products, and writes the whole lot into ``data/runs/<run_id>/`` atomically.

**The order**: decode/QC -> Sky -> Twin -> Pulse -> Flash -> products -> publish. SPEC.md
11.11 lists Pulse after Flash, but 11.7 draws the ensemble's blockage from the Pulse posterior and
cleans pipes at it for attribution, so Pulse has to have run first; it reads only the network,
the bundle's feeds and the clock, so nothing it needs comes later. The Twin is the depth of
record and Flash spreads it: 50 members over the twenty Sky members, so ``ensemble_n`` is the
number that actually ran rather than a literal. When the emulator cannot run - no fit, a design
storm, another city's segments - the stage says so in the run's notes and ``ensemble_n`` falls
back to 1, so no screen implies a spread nothing computed (rule 6).

**Atomicity.** The registry writes into a temporary folder and renames it into place, so a
half-written run can never be served: a reader either sees a complete run directory or none at
all. That matters during a bake, where the console may be polling while cycles are landing.

**The rain hand-off.** Sky produces its ensemble on the 500 m radar grid; the Twin wants mm/h on
the 30 m city grid. ``varuna_sky.products.resample_to_aoi`` owns that resample and is called
here rather than inside the Twin, which keeps the Twin ignorant of Sky (its ``types.py`` says as
much). The **ensemble mean** is what the Twin runs on, because one deterministic Twin run is what
Phase 4 provides; the members themselves go to Flash, which is cheap enough to run fifty.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog
from varuna_schemas.constants import IST, N_STEPS, STEP_MIN
from varuna_schemas.models.run import (
    CityFingerprint,
    EngineVersions,
    GridSpec,
    RunMeta,
    build_run_id,
)
from varuna_schemas.paths import bundles_dir, city_dir, repo_root

if TYPE_CHECKING:  # pragma: no cover - typing only
    from numpy.typing import NDArray
    from varuna_twin.types import TideSeries, TwinResult

log = structlog.get_logger("varuna.cycle.twin")

__all__ = [
    "TWIN_REVISION",
    "CycleResult",
    "aoi_hyetograph",
    "land_peak_depth_cm",
    "mass_balance_ledger",
    "pump_benefit_notes",
    "run_cycle",
    "tide_notes",
]

SKY_VERSION = "1.0"
TWIN_VERSION = "1.0"
TWIN_REVISION = "1.0+coast-2026-09-28"
"""Which Twin computed a run, finer than the run id says; ``run.json`` keeps it.

The coastline changed what the Twin computes - the city's own sea held at the tide, the coast
wall behind the intertidal zone, tidal outfalls from the sea raster, no exchange or rain on sea
cells, the CFL read off land and shoreline faces, products that never read the sea - without
changing ``TWIN_VERSION``. The version is in the run id (``...-twin1.0-...``), and the id is what
the console's deep links, the demo runs the API seeds, the fixtures and the end-to-end tests key
on - 281 tracked files carry a ``twin1.0`` id on 2026-09-28, 208 of them the shipped runs under
``demo/runs`` - so bumping it would orphan every shipped run and link while saying less than
this string does. A re-bake overwrites each run in place under the same id; this
revision, with :func:`varuna_cycle.provenance.city_fingerprint` beside it, is how a reader tells
a run baked before the coastline from one baked after (:func:`varuna_cycle.provenance.stale_runs`).
Change it whenever the Twin's physics changes and its version does not."""
FLASH_VERSION = "0.1"
"""The fitted emulator's version, used only on a cycle where it actually ran."""

FLASH_ABSENT_VERSION = "0.0"
"""What the run id carries when Flash did not run - no fit on disk, or a design storm.

``0.0`` says so rather than claiming a version of an engine that did not run: the run stamp is
on screen throughout the demo, and a reader has to be able to tell a 50-member cycle from a
single-member one by its id alone."""

PULSE_VERSION = "1.0"
PRODUCTS_VERSION = "1.0"

INFERRED_NOTE = (
    "Drain graph inferred from roads and terrain, not a municipal SWD model "
    "(SPEC.md 10.1 step 7); every pipe carries a learned blockage."
)
DETERMINISTIC_NOTE = (
    "One deterministic Twin run and no street ensemble this cycle, so p10 = p50 = p90 and "
    "every exceedance is 0 or 1."
)
TWIN_LEVEL_NOTE = (
    "The depth on screen is the Twin's, not the emulator's: Flash supplies the spread around it "
    "and never the level (ADR-0025)."
)
RAIN_STORES_ABSENT_NOTE = (
    "No rain/cube.zarr this cycle: a design storm is forced from the bundle's own truth field, "
    "so the Sky ensemble is not the rain this run was computed on (SPEC.md 10.3)."
)
NO_MEMBER_AXIS_NOTE = (
    "The segment quantiles in this run are still the Twin's single run - p10 = p50 = p90 and "
    "every exceedance is 0 or 1 - because the products writer does not take a member axis yet: "
    "the ensemble ran and nothing downstream read it."
)

FLASH_MODEL_PATHS = ("data/train/flash_lite.npz", "demo/flash_lite.npz")
"""Where the fitted emulator is looked for, in order - the same two places `/v1/whatif` looks."""

PUMP_BENEFIT_NOTES = {
    "emulator": (
        "Pump benefit is the emulator re-run with the pump's outflow (SPEC.md 11.10), so it "
        "saturates: a pump removes only the water the storm actually ponded at that junction."
    ),
    "reduced_model": (
        "Pump benefit is the bathtub estimate, not a physics run: the fitted emulator was not on "
        "disk for this cycle, so the fallback lowers the junction at the pump's rated rate and "
        "ignores the inflow still arriving. It overstates a pump at a spot that is still filling."
    ),
    "mixed": (
        "Pump benefit is the emulator for most assignments and the bathtub estimate for the rest, "
        "because some candidate street has no segment the fitted emulator knows."
    ),
}
"""``pump_plan.benefit_model`` -> the run note that says which model produced the number.

The board prints `benefit_label` beside the figure, and this puts the same fact in the run's own
provenance, where `/v1/runs` and the run stamp read it - the two cannot drift, because both are
keyed on the plan's own `benefit_model` (rule 6)."""


@dataclass(frozen=True, slots=True)
class CycleResult:
    """What one cycle produced, for the caller that has to report it."""

    run_id: str
    run_dir: Path
    stage_ms: dict[str, int]
    ensemble_n: int
    mass_balance_err: float
    peak_depth_cm: float
    """Deepest water on land over the run, in cm (:func:`land_peak_depth_cm`): never the sea's."""
    wet_segments: int
    surcharging_nodes: int
    backflow_edges: int
    notes: tuple[str, ...]


def _tidal_cells(
    terrain: Any, intertidal: NDArray[np.bool_] | None = None
) -> NDArray[np.bool_] | None:
    """The cells whose water is the tide's: the city's sea, plus its intertidal zone.

    ``terrain.sea`` and ``terrain.intertidal`` are read with ``getattr`` so a plain
    ``TerrainGrid`` - every test grid, a nest, a city built before the sea step - has neither and
    gets ``None``. ``intertidal`` is the wet land the tide may cover (mangrove, wetland, open
    water behind the sea, in front of the coast wall). The Twin's terrain does not carry it yet,
    so the cycle also passes the city's ``intertidal_mask.tif`` as read by the products
    (:func:`varuna_products.depth.city_intertidal_mask`); the union is taken, so a terrain that
    starts carrying it later changes nothing.
    """
    shape = tuple(np.shape(terrain.z))
    mask: NDArray[np.bool_] | None = None
    sources = [(f"terrain.{name}", getattr(terrain, name, None)) for name in ("sea", "intertidal")]
    sources.append(("intertidal", intertidal))
    for label, cells in sources:
        if cells is None:
            continue
        cells = np.asarray(cells, dtype=np.bool_)
        if cells.shape != shape:
            raise ValueError(f"{label} has shape {cells.shape}, expected {shape}")
        mask = cells if mask is None else (mask | cells)
    return mask


def land_peak_depth_cm(
    depth_m: NDArray[np.floating],
    terrain: Any,
    *,
    intertidal: NDArray[np.bool_] | None = None,
) -> float:
    """Deepest water off the sea and the intertidal zone over the whole run, in cm to 0.1.

    The Twin holds the city's sea at the tide, so its whole-grid maximum is the deepest sea cell:
    Chennai's first onboarded forecast published "peak 529.1 cm", which was its -5.29 m sea cell
    at mean sea level, and on Mumbai's coastline it is up to 2.58 m of bay at the crest. Neither
    is a street, and nor is a mangrove the tide walks onto, which the segment table and the
    hotspot rank already leave out. Without a sea or intertidal mask every cell counts, exactly
    as before - the same ``nanmax`` over the same array - and it is 0 when the tide covers the
    whole grid.
    """
    tidal = _tidal_cells(terrain, intertidal)
    if tidal is None:
        return round(float(np.nanmax(depth_m)) * 100.0, 1)
    land = np.asarray(depth_m)[:, ~tidal]
    return round(float(np.nanmax(land)) * 100.0, 1) if land.size else 0.0


def _is_design_storm(bundle: str) -> bool:
    """Whether this bundle is a design storm rather than a reconstructed event."""
    from varuna_replay.bundle import load_manifest

    try:
        return bool(getattr(load_manifest(bundle), "design_storm", None))
    except Exception:
        return False


def tide_notes(tide: TideSeries | None) -> list[str]:
    """The run notes the tide contributes: its rule-7 label, then its datum conversion.

    Kept apart from :func:`run_cycle` so the notes a run publishes about its sea boundary can be
    checked on a bundle's ``tide.csv`` and manifest without a Twin run.
    """
    if tide is None:
        return []
    notes: list[str] = []
    if "illustrative" in tide.source.lower():
        notes.append(f"Tide series is {tide.source}, not a published tide table (rule 7).")
    if tide.datum_note:
        notes.append(tide.datum_note)
    return notes


def aoi_hyetograph(rain_cube: NDArray[np.floating]) -> list[float]:
    """The storm this cycle is running on, as AOI-mean mm/h per step.

    One list, computed once, used twice: it is what `run.json` keeps as ``rain_aoi_mm_h``
    (SPEC.md 10.3) *and* what the pump plan is priced against. They were the same number
    computed in one place and not passed to the other, which is how every shipped pump benefit
    came from the bathtub fallback while the run beside it carried the storm that would have
    driven the emulator. Rounding here rather than only on the way into `run.json` means the plan
    is priced on exactly the series a later `rain_for_run` reads back, so re-optimising a run on
    disk cannot quietly disagree with the plan the cycle wrote.
    """
    return [round(float(v), 3) for v in rain_cube.mean(axis=(1, 2))]


LEDGER_FIELDS = (
    "volume_in_m3",
    "volume_out_m3",
    "volume_stored_m3",
    "volume_stored_start_m3",
    "residual_m3",
    "surface_residual_m3",
    "drain_residual_m3",
    "inlet_gap_m3",
    "surcharge_gap_m3",
)
"""The `MassBalance` fields `run.json` keeps, so a run says where its error sits (ADR-0071)."""


def mass_balance_ledger(balance: Any) -> dict[str, float]:
    """The Twin's residual decomposed, rounded to the litre, for `run.json`.

    One number over budget cannot tell a leak from an honest denominator; the decomposition can,
    and it is what located both exchange leaks on 2026-09-24. A field an older Twin does not carry
    is left out rather than written as zero.
    """
    return {
        name: round(float(getattr(balance, name)), 3)
        for name in LEDGER_FIELDS
        if getattr(balance, name, None) is not None
    }


def pump_benefit_notes(plan: dict[str, Any]) -> list[str]:
    """The run note naming the model behind the pump plan's "minutes above 45 cm avoided"."""
    if not plan.get("assignments"):
        return []  # no pump was worth sending this cycle; there is no benefit to attribute
    note = PUMP_BENEFIT_NOTES.get(str(plan.get("benefit_model")))
    return [note] if note else []


def _design_storm_rain(bundle: str, cycle_ts: datetime | None, city: str, n_steps: int):
    """A design storm's own truth field on the city grid, or None if this is not one.

    Returns the same ``(cube, cycle)`` pair the Sky path does, with ``cycle`` carrying the notes
    that say the nowcast was bypassed and why.
    """
    import zarr
    from varuna_replay.bundle import bundle_dir, load_manifest
    from varuna_sky.products import load_aoi_grid, resample_to_aoi

    from varuna_cycle.sky_cycle import run_bundle_cycle

    try:
        manifest = load_manifest(bundle)
    except Exception:  # not a loadable bundle; let the Sky path raise its own error
        return None
    if not getattr(manifest, "design_storm", None):
        return None

    truth = bundle_dir(bundle) / "truth" / "rain.zarr"
    if not truth.is_dir():
        return None

    store = zarr.open(str(truth), mode="r")
    names = list(store.array_keys())
    key = "rain" if "rain" in names else names[0]
    field = np.asarray(store[key], dtype=np.float64)

    # The truth cube starts at the bundle's t0; a cycle at t reads forward from there.
    offset = 0
    if cycle_ts is not None:
        offset = max(int((cycle_ts - manifest.t0).total_seconds() // (STEP_MIN * 60)), 0)
    window = field[offset : offset + int(n_steps)]
    if window.shape[0] == 0:
        return None

    # Sky still runs, for its grid, its products and its honesty labels; only the *forcing* is
    # taken from the truth field. Running it also keeps the stage timing and the run's provenance
    # identical between a design storm and a reconstruction.
    cycle = run_bundle_cycle(bundle, cycle_ts)
    aoi = load_aoi_grid(city)
    cube = np.stack(
        [resample_to_aoi(window[k], cycle.products.grid, aoi) for k in range(window.shape[0])]
    )
    cube = np.where(np.isfinite(cube), cube, 0.0)
    log.info(
        "cycle.design_storm_forcing",
        bundle=bundle,
        city=city,
        steps=int(cube.shape[0]),
        total_mm=round(float(cube.mean(axis=(1, 2)).sum()) * STEP_MIN / 60.0, 1),
    )
    return cube, cycle


def _sky_rain_on_city(bundle: str, cycle_ts: datetime | None, city: str, n_steps: int):
    """Run Sky for this cycle and return its **ensemble-mean** rain on the 30 m city grid, mm/h.

    The mean, as SPEC.md 11.11 specifies, and the reason is a water balance. A pixelwise
    quantile is not a rainfall field: at a given pixel and lead time the 20 STEPS members
    disagree about *where* the convective cell is, so the median there can be near zero while
    every member is carrying a downpour a kilometre away. Measured on the 2 July storm, the p50
    field delivers 13 mm over three hours against the truth field's 100 mm at the chronic spots;
    the Twin ran on it and gave Hindmata 4 cm.

    The mean is the only reduction here that conserves volume - the expected total is the total
    of the expectations - so the city receives the water the ensemble actually forecasts. It
    smooths the peak, which is a real cost and is why the ensemble goes to Flash-lite whole in
    Phase 7; the run's notes say the spread was discarded.

    **A design storm is not nowcast.** `MUM-IDF-25yr` and `CHN-IDF-25yr` are a stated depth over a
    stated duration, spatially uniform by construction, and STEPS is the wrong instrument for
    them: its whole method is advecting and perturbing *spatial structure*, and a featureless field
    has none, so the twenty members decorrelate into noise and their mean collapses. Measured on
    `CHN-IDF-25yr`: the members carry the 11.5 mm/h the radar shows, the ensemble mean peaks at
    3.3 mm/h, and the AOI received 0.1 mm of a 150 mm storm. Issuing at the storm's peak instead
    made it worse in the other direction - persistence held the 447 mm/h spike for three hours and
    delivered 609 mm.

    So a design storm forces the Twin from its own truth field, which is exactly what a design
    storm is *for* (`varuna_replay.design`: the drainage-norm intensity sizing the pipes, held over
    three hours). The run's notes say so. A reconstructed event still goes through Sky, because
    there the nowcast is the thing being demonstrated.
    """
    from varuna_sky.products import load_aoi_grid, resample_to_aoi

    from varuna_cycle.sky_cycle import run_bundle_cycle

    design = _design_storm_rain(bundle, cycle_ts, city, n_steps)
    if design is not None:
        return design

    cycle = run_bundle_cycle(bundle, cycle_ts)
    aoi = load_aoi_grid(city)
    field = np.asarray(cycle.products.mean, dtype=np.float64)  # (steps, y, x) on the Sky grid
    steps = min(int(field.shape[0]), int(n_steps))
    cube = np.stack([resample_to_aoi(field[k], cycle.products.grid, aoi) for k in range(steps)])
    # resample_to_aoi fills outside the radar domain with nan; the Twin reads that as no rain
    # (hydrology logs and zeroes non-finite rain), but zeroing here keeps the mass-balance
    # accounting reading a real number rather than nan.
    cube = np.where(np.isfinite(cube), cube, 0.0)
    return cube, cycle


@dataclass(frozen=True, slots=True)
class _FlashPlan:
    """Everything the Flash stage needs that can be settled before Pulse has run.

    Split from the members themselves because the run id records whether Flash ran, Pulse needs
    the run id, and the members need Pulse's posterior. Settling the guards first breaks that
    circle without guessing: every reason Flash could refuse is known here.
    """

    model: Any
    model_name: str
    hyetographs: NDArray[np.floating]
    n_steps: int


def _flash_plan(
    sky,
    segment_ids: tuple[str, ...],
    n_steps: int,
    *,
    design_storm: bool,
) -> tuple[_FlashPlan | None, tuple[str, ...]]:
    """The fitted emulator and this cycle's per-member rain, or ``(None, notes)`` saying why not.

    The members carry the **AOI-mean hyetograph per member** (`varuna_sky.products`), which is the
    only per-member rain a cycle keeps; the full member cube is 20 x 36 x 120 x 120 and is not
    written. So the members differ from one another in amplitude and not in where the cell sits,
    and the spread this produces is amplitude-driven. That is a real limitation of the
    construction rather than a defect of the fit, and `varuna_flash.ensemble` puts it into the
    run's notes so nobody reads the band as the ensemble's disagreement about the *map*.

    Returns ``(None, notes)`` whenever the stage cannot honestly run - a design storm, no fitted
    emulator, a fit belonging to another city - so the caller keeps ``ensemble_n`` at 1 and the
    run says why rather than failing the cycle for a product that is a bonus over the Twin's.
    """
    if design_storm:
        return None, (
            "Flash did not run: a design storm is forced from its own truth field, so this cycle "
            "has no Sky ensemble to spread over and ensemble_n stays 1.",
        )

    from varuna_flash.model import load

    model_path = None
    for candidate in FLASH_MODEL_PATHS:
        path = repo_root() / candidate
        if path.is_file():
            model_path = path
            break
    if model_path is None:
        return None, (
            "Flash did not run: no fitted emulator at "
            + " or ".join(FLASH_MODEL_PATHS)
            + ". Run make train to fit one from Twin runs; ensemble_n stays 1.",
        )

    model = load(model_path)
    if tuple(model.segment_ids) != tuple(segment_ids):
        # Same-length-different-order is the dangerous case, which is why this compares the ids
        # rather than the count: the emulator's arrays are positional, so a mismatched order
        # would silently give every street somebody else's storage coefficient.
        return None, (
            f"Flash did not run: the fitted emulator carries {model.n_segments} segments and "
            f"this city has {len(segment_ids)} in a different order, so it was fitted on another "
            "city or an older build. Run make train for this city; ensemble_n stays 1.",
        )

    hyetographs = np.asarray(sky.products.aoi_hyetographs, dtype=np.float64)
    if hyetographs.ndim != 2 or hyetographs.size == 0:
        return None, (
            "Flash did not run: this cycle's Sky products carry no per-member hyetographs, so "
            "there is nothing to spread over; ensemble_n stays 1.",
        )

    steps = min(int(hyetographs.shape[1]), int(n_steps))
    return _FlashPlan(model, str(model_path.name), hyetographs[:, :steps], steps), ()


def _flash_members(
    plan: _FlashPlan,
    city: str,
    network,
    pulse,
    *,
    seed: int,
) -> tuple[NDArray[np.floating], tuple[str, ...]]:
    """The 50-member street ensemble (SPEC.md 11.7, P7.6): ``(n_members, n_steps, n_segments)``.

    Twenty Sky members, covered exhaustively rather than sampled, crossed with blockage draws from
    this cycle's Pulse posterior and Monte Carlo noise on the emulator's storage coefficient
    (`varuna_flash.ensemble`). Pulse publishes blockage per *drain edge* and the emulator is keyed
    on road segments, so the join is made here - `varuna_flash` does not depend on `varuna_pulse`.

    When Pulse did not run, or its posterior cannot be joined, the members carry the city's
    **prior** and the note says so, rather than borrowing another cycle's posterior silently.
    """
    import pandas as pd
    from varuna_flash.ensemble import build_members
    from varuna_pulse.join import SegmentBetaError, segment_beta

    segment_ids = tuple(plan.model.segment_ids)
    source = "this cycle's Pulse posterior"
    beta_mean = beta_sd = None
    if pulse is not None:
        posterior = pd.DataFrame(
            {
                "edge_id": list(network.edge_ids),
                "beta_mean": np.asarray(pulse.beta_mean, dtype=np.float64),
                "beta_sd": np.asarray(pulse.beta_sd, dtype=np.float64),
            }
        )
        try:
            beta_mean, beta_sd = segment_beta(city, posterior, segment_ids)
        except SegmentBetaError as error:
            source = f"the city's prior, because the posterior could not be joined ({error})"
    else:
        source = "the city's prior, because Pulse did not run this cycle"
    if beta_mean is None or beta_sd is None:
        beta_mean, beta_sd = segment_beta(city, None, segment_ids)

    built = build_members(
        plan.model, plan.hyetographs, beta_mean, beta_sd, n_steps=plan.n_steps, seed=seed
    )
    spread = built.spread
    log.info(
        "cycle.flash_members",
        members=built.n_members,
        steps=built.n_steps,
        segments=int(built.depth_cm.shape[2]),
        model=plan.model_name,
        peak_cm=round(float(built.depth_cm.max()), 1),
        band_cm=round(spread.band_cm, 2) if spread is not None else None,
        band_without_blockage_cm=(
            round(spread.band_without_blockage_cm, 2) if spread is not None else None
        ),
        ms=spread.ms if spread is not None else None,
    )
    # The builder's own notes open with the ensemble and its skill; this adds only which
    # posterior its blockage came from, which is the cycle's to know and not the builder's.
    return built.depth_cm, (
        *built.notes,
        f"The blockage draws came from {source}.",
        TWIN_LEVEL_NOTE,
    )


def _bundle_seed(bundle: str) -> int:
    """The bundle's own seed, which every draw in a bake must come from (rule 8)."""
    from varuna_replay.bundle import BundleLayout, load_manifest

    try:
        return int(load_manifest(BundleLayout.for_bundle(bundle).root).seed)
    except Exception as error:  # a bundle without a manifest cannot have been replayed
        log.warning("cycle.bundle_seed_default", bundle=bundle, error=str(error))
        return 2019


def _sky_ensemble(
    bundle: str, cycle_ts: datetime, *, design_storm: bool
) -> tuple[Any | None, tuple[str, ...]]:
    """This cycle's member cube for ``rain/cube.zarr``, or ``(None, notes)`` when it cannot be kept.

    SPEC.md 10.3 lists ``rain/cube.zarr`` and ``rain/quantiles.zarr`` among a run's artifacts,
    and they are the only place the twenty Sky members survive the cycle: :class:`RainCycle`
    carries the reduced products because the console never needs a 20 x 36 x 120 x 120 cube, so
    without the stores a baked run cannot be re-ensembled offline and rebuilding the members
    costs a 6-8 s Sky run per cycle. The read side has been waiting for them all along -
    ``sky_cycle.read_run_rain`` reads both and ``has_rain_products`` gates the rain endpoints on
    the quantiles store.

    The ensemble comes out of the memo behind :func:`run_bundle_cycle`, which this cycle filled a
    moment ago with exactly this key (same bundle folder, same radar stamp, same snapped instant),
    so recovering it is a dictionary lookup and not a second nowcast. ``sky_cycle`` is this
    package's own module, which is why reaching for its cache is a package-private call rather
    than a reimplementation; an evicted entry recomputes from the bundle's own seed and returns
    the same ensemble (rule 8).

    **A design storm keeps no cube.** Its Twin is forced from the bundle's truth field rather
    than from the nowcast (:func:`_design_storm_rain`), so a cube written there would be a
    forecast nobody ran the city on - and ``has_rain_products`` would then serve it as this run's
    rain (rule 6).
    """
    if design_storm:
        return None, (RAIN_STORES_ABSENT_NOTE,)

    from varuna_replay.bundle import BundleLayout, load_manifest
    from varuna_sky.products import RAIN_CUBE, RAIN_QUANTILES

    from varuna_cycle.sky_cycle import _compute, _radar_stamp

    try:
        layout = BundleLayout.for_bundle(bundle)
        manifest = load_manifest(layout.root)
        ensemble = _compute(
            str(layout.root), _radar_stamp(layout), cycle_ts, manifest.city, int(manifest.seed)
        ).ensemble
    except Exception as error:
        # Degraded, not broken: the rain stores are provenance the rest of the run does not
        # depend on, so a run without them is still a complete forecast that says what is missing.
        log.warning("cycle.rain_stores_skipped", bundle=bundle, error=str(error))
        return None, (
            f"No {RAIN_CUBE} this cycle: the Sky ensemble could not be recovered ({error}).",
        )
    return ensemble, (
        f"Rain ensemble kept as {RAIN_CUBE} ({ensemble.n_members} members x {ensemble.n_steps} "
        f"steps at {ensemble.grid.res_m:.0f} m) and {RAIN_QUANTILES}, so this cycle can be "
        "re-ensembled offline without re-running Sky (SPEC.md 10.3).",
    )


def run_cycle(
    bundle: str = "MUM-2019-07-02",
    cycle_ts: datetime | None = None,
    *,
    city: str = "mumbai",
    n_steps: int = N_STEPS,
    mode: str = "baked",
    overwrite: bool = False,
) -> CycleResult:
    """Run one cycle end to end and write its run directory.

    Args:
        bundle: replay bundle the radar and gauges come from.
        cycle_ts: the instant to forecast from; defaults to the bundle's first computable cycle.
        city: which built city to run on.
        n_steps: forecast steps of ``STEP_MIN`` minutes each (36 = 3 hours).
        mode: ``baked`` when pre-computing, ``live`` when the operator pressed Compute live.
        overwrite: replace an existing run directory of the same id.
    """
    from varuna_products.alerts import (
        STREET_POINTS,
        build_alerts,
        street_member_series,
        street_series,
        write_alerts,
    )
    from varuna_products.depth import (
        aoi_depth_band,
        city_intertidal_mask,
        depth_bounds,
        segment_cell_index,
        segment_forecast,
        segment_names,
        segment_points,
        write_depth_rasters,
        write_wet_segments,
    )
    from varuna_products.hotspots import attach_ensemble_band, rank_hotspots
    from varuna_products.pumps import build_pump_plan, write_pump_plan
    from varuna_products.surcharge import surcharge_product, write_surcharge
    from varuna_pulse.cycle import run_pulse
    from varuna_pulse.health import write_drain_health
    from varuna_sky.products import load_aoi_grid, write_rain_products
    from varuna_twin.city import load_network, load_terrain, load_tide
    from varuna_twin.runner import run_twin
    from varuna_twin.types import TwinInputs

    from varuna_cycle.provenance import city_fingerprint
    from varuna_cycle.registry import RunRegistry

    stage_ms: dict[str, int] = {}
    started = perf_counter()

    # ---- Sky ----------------------------------------------------------------------------
    mark = perf_counter()
    rain_cube, sky = _sky_rain_on_city(bundle, cycle_ts, city, n_steps)
    cycle_ts = sky.cycle_ts
    n_steps = int(rain_cube.shape[0])
    stage_ms["sky"] = round((perf_counter() - mark) * 1000.0)

    # Which city files this run reads, hashed before the Twin loads them and outside its mark:
    # the run id cannot say the coastline changed (TWIN_REVISION), so run.json does. Decoded
    # once per bake and cached on the files' bytes: on Mumbai about 30 ms a cycle warm, 0.2-0.4 s
    # on a process's first call. Billed to its own key so `total` is never unexplained.
    mark = perf_counter()
    fingerprint = city_fingerprint(city_dir(city))
    stage_ms["provenance"] = round((perf_counter() - mark) * 1000.0)

    # ---- Twin ---------------------------------------------------------------------------
    mark = perf_counter()
    terrain = load_terrain(city)
    network = load_network(city)
    tide = load_tide(bundle)
    twin: TwinResult = run_twin(
        TwinInputs(
            terrain=terrain,
            network=network,
            rain_mm_h=rain_cube,
            t0=cycle_ts,
            step_min=STEP_MIN,
            tide=tide,
        )
    )
    stage_ms["twin"] = round((perf_counter() - mark) * 1000.0)
    stage_ms.update({f"twin_{k}": v for k, v in twin.stage_ms.items()})

    # The segment index is pure geometry and cached on disk, so both stages read it and neither
    # pays for it; it is taken before the marks so the stage timings are the engines alone.
    index = segment_cell_index(city_dir(city), terrain.transform, terrain.shape, terrain.crs)
    design_storm = bool(getattr(sky, "design_storm_forced", False) or _is_design_storm(bundle))
    # The member cube the run keeps (SPEC.md 10.3), taken before the Flash mark for the same
    # reason the segment index is: it is a cache lookup, not work this stage should be billed for.
    rain_ensemble, rain_notes = _sky_ensemble(bundle, cycle_ts, design_storm=design_storm)
    flash_plan, flash_notes = _flash_plan(sky, index[0], n_steps, design_storm=design_storm)
    run_id = build_run_id(
        city,
        cycle_ts,
        SKY_VERSION,
        TWIN_VERSION,
        FLASH_VERSION if flash_plan is not None else FLASH_ABSENT_VERSION,
        mode,
    )

    # ---- Pulse ---------------------------------------------------------------------------
    # After the Twin, because assimilation is about what the city showed while this cycle's
    # water was on the ground; before Flash and the products, because the street ensemble draws
    # its blockage from this posterior (11.7) and attribution cleans pipes at it (11.7). Pulse
    # reads only the network, the bundle's feeds and the clock, so nothing it needs moved.
    mark = perf_counter()
    try:
        pulse = run_pulse(
            network,
            city_dir(city),
            bundles_dir() / bundle,
            cycle_ts,
            transform=terrain.transform,
            crs=terrain.crs,
            run_id=run_id,
        )
    except Exception as error:
        # Degraded, not broken (SPEC.md 11.11): the depth forecast is complete and useful
        # without an assimilation, and the run says which feed was missing rather than
        # publishing a drain map nothing computed.
        log.warning("cycle.pulse_failed", run_id=run_id, error=str(error))
        pulse = None
    stage_ms["pulse"] = round((perf_counter() - mark) * 1000.0)

    # ---- Flash --------------------------------------------------------------------------
    mark = perf_counter()
    members: NDArray[np.floating] | None = None
    if flash_plan is not None:
        members, flash_notes = _flash_members(
            flash_plan, city, network, pulse, seed=_bundle_seed(bundle)
        )
    stage_ms["flash"] = round((perf_counter() - mark) * 1000.0)
    ensemble_n = int(members.shape[0]) if members is not None else 1

    # ---- Products -----------------------------------------------------------------------
    mark = perf_counter()
    # The member axis reaches the products writer only once that writer takes one (P7.6 splits
    # the two). Until it does, the ensemble runs and the parquet still carries the Twin's single
    # run - so the run's notes say which of the two happened rather than letting ensemble_n
    # imply a spread the file does not have (rule 6).
    takes_members = "member_depth_cm" in inspect.signature(segment_forecast).parameters
    ensemble: dict[str, Any] = (
        {"member_depth_cm": members} if members is not None and takes_members else {}
    )
    frame, depth_cm = segment_forecast(
        twin.depth_m, twin.times, index, run_id="pending", **ensemble
    )
    # Attribution cleans pipes against the blockage this cycle learned, not the city's prior,
    # and says which one it was. Its drain1d runs are the bulk of this stage (ADR-0071), so its
    # share is recorded as a sub-timing: `products` still counts it once (ADR-0046).
    attribution_network = (
        replace(network, beta=np.asarray(pulse.beta_mean, dtype=np.float64))
        if pulse is not None
        else network
    )
    product_timings: dict[str, int] = {}
    hotspots = rank_hotspots(
        twin.depth_m,
        twin.times,
        city_dir(city),
        terrain.transform,
        terrain.crs,
        run_id,
        index,
        network=attribution_network,
        blockage_source="posterior" if pulse is not None else "prior",
        timings=product_timings,
    )
    if "attribution" in product_timings:
        stage_ms["products_attribution"] = product_timings["attribution"]
    # The junction's fan chart takes its band from the streets around it (ADR-0076): the
    # hotspot's own Twin level plus its segments' member spread.
    attach_ensemble_band(hotspots, frame)
    # Alerts are about named places, so the per-segment series are collapsed onto street names
    # first (`street_series`); a segment id in an alert headline is no use to a ward officer.
    names = segment_names(city_dir(city))
    points = segment_points(city_dir(city))
    street_depths = street_series(
        {sid: list(depth_cm[:, k]) for k, sid in enumerate(index[0])}, names, points
    )
    # How many of the members also cross each level, counted on the same street collapse, so an
    # alert can say "N of 50 members above" beside the deterministic raise it comes from.
    street_members = (
        street_member_series(depth_cm, members, index[0], names) if members is not None else None
    )
    alerts = build_alerts(
        hotspots,
        run_id,
        cycle_ts,
        twin.times,
        mode,
        streets=street_depths,
        street_members=street_members,
    )
    # The storm the Twin was driven by, handed to the plan so the benefit is the emulator
    # re-run with the pump's outflow rather than the bathtub fallback (SPEC.md 11.10). The
    # plan never reads it back off disk - a first bake would find nothing and a re-bake would
    # find what it had just written, and rule 8 asks for byte-identical bakes.
    rain_aoi_mm_h = aoi_hyetograph(rain_cube)
    pump_plan = build_pump_plan(
        hotspots,
        city_dir(city),
        run_id,
        STEP_MIN,
        street_depths,
        dict(STREET_POINTS),
        rain_mm_h=rain_aoi_mm_h,
    )
    surcharge = surcharge_product(
        twin.q_surcharge, twin.edge_flow, network, terrain.transform, terrain.crs, run_id
    )
    stage_ms["products"] = round((perf_counter() - mark) * 1000.0)
    frame["run_id"] = run_id

    notes = [
        INFERRED_NOTE,
        *flash_notes,
        *((NO_MEMBER_AXIS_NOTE,) if members is not None and not takes_members else ()),
        *((DETERMINISTIC_NOTE,) if members is None else ()),
        *rain_notes,
        *(pulse.notes if pulse is not None else ("Pulse did not run this cycle.",)),
        *twin.notes,
        *(getattr(sky, "notes", None) or ()),
    ]
    if design_storm:
        notes.append(
            "Design storm: the Twin was forced from the bundle's own hyetograph, not from a "
            "nowcast. A design storm is spatially uniform by construction and STEPS extrapolates "
            "spatial structure, so nowcasting one says more about the noise model than the storm."
        )

    notes.extend(tide_notes(tide))
    notes.extend(pump_benefit_notes(pump_plan))

    bounds = depth_bounds(terrain.transform, terrain.shape, terrain.crs)
    meta = RunMeta(
        run_id=run_id,
        city=city,
        cycle_ts=cycle_ts,
        radar_frame_ts=sky.products.times[0] if sky.products.times else None,
        versions=EngineVersions(
            sky=SKY_VERSION,
            twin=TWIN_VERSION,
            flash=FLASH_VERSION if members is not None else FLASH_ABSENT_VERSION,
            pulse=PULSE_VERSION,
            products=PRODUCTS_VERSION,
        ),
        mode=mode,
        ensemble_n=ensemble_n,
        stage_ms=stage_ms,
        mass_balance_err=float(twin.mass_balance.error_fraction),
        mass_balance_ledger=mass_balance_ledger(twin.mass_balance),
        aoi_depth_band=aoi_depth_band(depth_cm, members),
        bundle=bundle,
        created_at=datetime.now(tz=IST),
        grid=GridSpec(
            dx_m=terrain.res_m,
            nx=terrain.n_cols,
            ny=terrain.n_rows,
            crs=terrain.crs,
            bounds=bounds["wgs84"],
            transform=terrain.transform,
        ),
        step_min=STEP_MIN,
        n_steps=n_steps,
        notes=notes,
        # The storm, one number per step. What-if scales this to answer "what if it rains 30 %
        # harder", and without it stored the endpoint can only answer questions about pipes.
        # The same list the pump plan was priced on, so the two cannot disagree.
        rain_aoi_mm_h=rain_aoi_mm_h,
        # Provenance the run id cannot carry: which Twin revision ran, and a hash of each city
        # file it read. `varuna_cycle.provenance.stale_runs` compares both against the city now.
        twin_revision=TWIN_REVISION,
        city_fingerprint=CityFingerprint(**fingerprint),
    )

    def _write(tmp: Path) -> None:
        if rain_ensemble is not None:
            # Both stores or neither: the quantiles are what the rain endpoints serve and the
            # cube is the only artifact carrying the nowcaster, its seed and the Z-R relation,
            # so a run with one and not the other is a run whose rain cannot be traced.
            write_rain_products(tmp, rain_ensemble, sky.products, load_aoi_grid(city))
        # The city's sea and its intertidal land are drawn transparent: the Twin holds the sea at
        # the tide's level and lets the tide walk onto the mangroves, and through the depth ramp
        # either would read "rescue vehicles only" where no street is. The segment table and the
        # hotspot rank leave out the same cells.
        write_depth_rasters(
            tmp,
            twin.depth_m,
            terrain.transform,
            terrain.crs,
            stat="p50",
            sea_mask=_tidal_cells(
                terrain, city_intertidal_mask(city_dir(city), tuple(np.shape(terrain.z)))
            ),
        )
        frame.to_parquet(tmp / "segment_forecast.parquet", index=False)
        write_wet_segments(tmp, depth_cm, index[0], twin.times, run_id)
        (tmp / "hotspots.json").write_text(json.dumps(hotspots, indent=2) + "\n", encoding="utf-8")
        write_surcharge(tmp, surcharge)
        write_alerts(tmp, alerts)
        if pulse is not None:
            write_drain_health(tmp, pulse.health)
            (tmp / "observations.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "n_traffic": pulse.n_traffic,
                        "n_reports": pulse.n_reports,
                        "n_assimilated": pulse.n_assimilated,
                        "n_edges_updated": pulse.n_edges_updated,
                        "observations": pulse.observations,
                        "notes": list(pulse.notes),
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        write_pump_plan(tmp, pump_plan)
        q_node = twin.q_surcharge
        (tmp / "node_summary.json").write_text(
            json.dumps(
                {
                    "n_nodes": int(network.n_nodes),
                    "surcharging_by_step": [int((s > 0).sum()) for s in q_node],
                    "backflow_by_step": [int((f < 0).sum()) for f in twin.edge_flow],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    registry = RunRegistry()
    run_dir = registry.write_run_dir(run_id, _write, meta=meta, overwrite=overwrite)
    stage_ms["total"] = round((perf_counter() - started) * 1000.0)

    result = CycleResult(
        run_id=run_id,
        run_dir=run_dir,
        stage_ms=stage_ms,
        ensemble_n=ensemble_n,
        mass_balance_err=float(twin.mass_balance.error_fraction),
        peak_depth_cm=land_peak_depth_cm(
            twin.depth_m,
            terrain,
            intertidal=city_intertidal_mask(city_dir(city), tuple(np.shape(terrain.z))),
        ),
        wet_segments=int((depth_cm.max(axis=0) > 5.0).sum()) if depth_cm.size else 0,
        surcharging_nodes=int((twin.q_surcharge[-1] > 0).sum()),
        backflow_edges=int((twin.edge_flow[-1] < 0).sum()),
        notes=tuple(notes),
    )
    log.info(
        "cycle.published",
        run_id=run_id,
        total_ms=stage_ms["total"],
        flash_ms=stage_ms["flash"],
        ensemble_n=ensemble_n,
        peak_cm=result.peak_depth_cm,
        wet_segments=result.wet_segments,
        mass_balance=round(result.mass_balance_err, 6),
    )
    return result

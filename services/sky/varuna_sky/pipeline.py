"""One VARUNA-Sky cycle, end to end (SPEC.md 11.1).

:func:`run_sky` is the only function the cycle orchestrator calls. It runs the six stages of
SPEC.md 11.1 in order, times each one into :attr:`~varuna_sky.types.SkyResult.stage_ms` for
the console's ``CycleBudgetBar`` (SPEC.md 7.2), and collects the honesty labels the run has
earned into :attr:`~varuna_sky.types.SkyResult.notes`.

**The order is forced, not stylistic.** Each stage needs something the one before it produces:

* quality control comes first because a clutter pixel is not a usable gauge pair, so the Z-R
  fit must see the masks (SPEC.md 11.1 step 1 before step 2);
* the Z-R relation comes before optical flow because Lucas-Kanade tracks *rain*, not
  reflectivity, and the conversion is exactly what Z-R is (step 2 before step 4);
* the gauge merge adjusts the **analysis only**, never the history. ``rain_history`` takes the
  merged field as ``latest_rain_mm_h`` and leaves the older frames on radar alone, so a bias
  factor measured for one instant is not retro-fitted onto earlier ones;
* the pairs are built once, by :func:`~varuna_sky.zr.run_zr`, and handed to the merge. Pairing
  twice would let step 2 and step 3 disagree about which gauges counted.

**Notes are not decoration.** SPEC.md rule 6 says every number on screen comes from a run and
every simplification the user can see is labelled. The console prints these strings, so each one
has to be true of *this* run: whether Z-R was fitted or fell back to Marshall-Palmer, which
nowcaster produced the ensemble, what the merge actually did, and that the NWP blend of
Appendix A is disabled in P0 because there is no NWP field to blend.
"""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

import numpy as np
import structlog

from varuna_sky.merge import merge_gauges
from varuna_sky.motion import optical_flow, rain_from_dbz
from varuna_sky.products import AoiGrid, load_aoi_grid, sky_products
from varuna_sky.qc import run_qc
from varuna_sky.steps import nowcast
from varuna_sky.types import SkyInputs, SkyResult
from varuna_sky.zr import MP_A, MP_B, run_zr

if TYPE_CHECKING:  # pragma: no cover - typing only
    from varuna_sky.types import MergeResult, QCResult, RainEnsemble, ZRParams

log = structlog.get_logger("varuna.sky.pipeline")

__all__ = ["STAGES", "run_sky", "sky_notes"]

STAGES: tuple[str, ...] = ("qc", "zr", "merge", "motion", "nowcast", "products")
"""Stage keys of :attr:`~varuna_sky.types.SkyResult.stage_ms`, in execution order.

The console's ``CycleBudgetBar`` draws one segment per key (SPEC.md 7.2), so the order here
is the order the bar fills in. ``motion`` and ``nowcast`` are separate segments even though
SPEC.md 11.1 numbers them steps 4 and 5 of one ensemble stage: optical flow is a fixed cost
and the nowcast scales with the member count, and an operator watching the bar should be able
to tell which of the two is eating the budget.
"""

NWP_NOTE = "NWP blend disabled: no NWP field in P0 (SPEC.md 11.1, Appendix A)."
"""The blend of Appendix A needs a forecast field VARUNA does not have in P0. Saying so is
cheaper than the alternative, which is a judge assuming the ensemble is blended when it is not.
"""


def sky_notes(
    zr: ZRParams,
    merge: MergeResult,
    ensemble: RainEnsemble,
    qc: QCResult,
) -> tuple[str, ...]:
    """The honesty labels this run has earned (SPEC.md rule 6).

    Every string is a statement about *this* cycle, built from what the stages actually
    reported, so none of them can drift out of date the way a hard-coded caption would.
    """
    notes = [NWP_NOTE]

    if zr.source == "marshall_palmer":
        # The reason comes from the fit, never from the pair count. varuna_sky.zr abandons the
        # fit for three different reasons and only one of them is "too few pairs": a cycle whose
        # gauges all read within a factor of two falls back holding twenty pairs. Inferring
        # "fewer than eight" from `source` alone printed a false sentence on the console until
        # ZRParams.reason existed (rule 6).
        why = zr.reason or f"{zr.n_pairs} usable gauge-radar pairs did not support a fit"
        notes.append(f"Z-R is Marshall-Palmer (a={MP_A:.0f}, b={MP_B}): {why}.")
    else:
        fitted = f"Z-R fitted this cycle: a={zr.a:.0f}, b={zr.b:.2f} from {zr.n_pairs} pairs"
        notes.append(f"{fitted}, clamped to the allowed range." if zr.clamped else f"{fitted}.")

    if merge.method == "none":
        notes.append("No gauge merge: no usable gauge readings this cycle.")
    elif merge.method == "mfb":
        notes.append(
            f"Gauge merge is mean-field bias only ({merge.mfb:.2f}) from {merge.n_gauges} "
            "gauge: one anchor cannot interpolate a residual field."
        )
    else:
        honoured = (
            f", gauges honoured to {merge.max_gauge_error_pct:.1f} %"
            if merge.max_gauge_error_pct is not None
            else ""
        )
        notes.append(
            f"Gauge merge: bias {merge.mfb:.2f} over {merge.n_gauges} gauges, "
            f"inverse-distance residuals{honoured}."
        )

    if ensemble.source == "fallback_steps":
        notes.append(
            "Nowcast from the fallback scheme, not pySTEPS (SPEC.md 17): own advection, "
            "AR(2) per cascade level, no probability matching."
        )
    if ensemble.motion is not None and ensemble.motion.method == "zero":
        notes.append("No storm motion could be tracked; the nowcast is persistence, not advection.")
    if qc.attenuation_flag != "none":
        notes.append(
            f"Attenuation shadow behind cores above 50 dBZ is {qc.attenuation_flag}; "
            "the shadow is flagged, not corrected."
        )
    return tuple(notes)


def run_sky(inputs: SkyInputs, aoi: AoiGrid | str = "mumbai") -> SkyResult:
    """Run one Sky cycle: radar and gauges in, a 20-member 3-hour rain ensemble out.

    Args:
        inputs: the cycle's radar history, gauge readings and ensemble configuration.
        aoi: the city grid the hyetographs are averaged over, or a city slug to load it from
            the built city. It defaults to Mumbai because that is the demo city, but it is
            never *guessed*: :func:`~varuna_sky.products.load_aoi_grid` reads the grid the
            city pipeline wrote and raises with the ``make city`` command if it is missing.

    Returns:
        A :class:`~varuna_sky.types.SkyResult` carrying the ensemble, the products, the two
        stage results the rest of the cycle needs (QC and the merge), the per-stage timings
        and the run's honesty labels.

    Every stage is timed with :func:`time.perf_counter` and rounded to whole milliseconds,
    which is the resolution the budget bar shows; the total is the sum, so a reader can check
    the arithmetic against the ``Compute live`` display.
    """
    aoi_grid = aoi if isinstance(aoi, AoiGrid) else load_aoi_grid(aoi)
    stage_ms: dict[str, int] = {}

    def timed(name: str, start: float) -> None:
        stage_ms[name] = round((perf_counter() - start) * 1000.0)

    # -- step 1: quality control -------------------------------------------------------
    mark = perf_counter()
    qc = run_qc(inputs.frames)
    timed("qc", mark)

    # -- step 2: the Z-R relation, and the pairs step 3 will reuse ----------------------
    mark = perf_counter()
    zr, pairs = run_zr(inputs.frames, inputs.gauges, qc)
    timed("zr", mark)

    # -- step 3: the gauge merge, on the analysis alone ---------------------------------
    mark = perf_counter()
    analysis = rain_from_dbz(qc.dbz, zr)
    merge = merge_gauges(analysis, pairs, inputs.frames.grid, zr)
    timed("merge", mark)

    # -- step 4: optical flow, measured on the field the nowcast starts from ------------
    # Outside QC's coverage the frames are missing, not dry: floored to dry, the range edge is
    # a static feature Lucas-Kanade tracks to zero, and the storm is forecast at a fifth to a
    # third of its speed (varuna_sky.motion module docstring).
    mark = perf_counter()
    motion = optical_flow(inputs.frames, zr, merge.rain_mm_h, coverage=qc.coverage)
    timed("motion", mark)

    # -- step 5: the STEPS ensemble (pySTEPS, or the section 17 fallback) ---------------
    mark = perf_counter()
    ensemble = nowcast(merge.rain_mm_h, motion, inputs, zr)
    timed("nowcast", mark)

    # -- step 6: the products the rest of VARUNA reads ----------------------------------
    mark = perf_counter()
    products = sky_products(ensemble, aoi_grid)
    timed("products", mark)

    notes = sky_notes(zr, merge, ensemble, qc)
    total = int(sum(stage_ms.values()))
    log.info(
        "sky.cycle",
        cycle_ts=inputs.cycle_ts.isoformat(),
        total_ms=total,
        members=ensemble.n_members,
        steps=ensemble.n_steps,
        nowcaster=ensemble.source,
        zr=zr.source,
        merge=merge.method,
        max_mm_h=round(float(np.max(ensemble.rain_mm_h)), 2),
        **{f"ms_{name}": stage_ms[name] for name in STAGES},
    )
    return SkyResult(
        ensemble=ensemble,
        products=products,
        qc=qc,
        merge=merge,
        stage_ms=stage_ms,
        notes=notes,
    )

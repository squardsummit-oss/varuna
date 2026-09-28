"""Effective rainfall: the share of a rain rate that reaches the street
(SPEC.md 11.2, Appendix A).

VARUNA-Sky delivers a rain **rate**; the local-inertial solver of SPEC.md 11.3 wants the
rate that actually arrives on the surface, after the cell has kept what it keeps. Appendix A
fixes the split exactly:

    R_eff = I * max(0, R - d_s) + (1 - I) * max(0, R - f)

with ``d_s = 1.5 mm`` of depression storage on the **impervious** fraction, and ``f`` the
infiltration loss on the pervious fraction, given by SCS-CN with ``S = 25400/CN - 254`` and
``Q = (P - 0.2 S)^2 / (P + 0.8 S)`` at CN 90-98 for saturated monsoon soils.

Both loss terms are **memory**, not a per-step subtraction, and that is the whole difficulty
of this module. Three choices, each made here on purpose:

**1. ``d_s`` is a bucket, not a rate.** SPEC.md 11.2 says the 1.5 mm is "applied once per
event". So it is a per-cell *remaining capacity*: the first 1.5 mm of rain over a cell fills
the puddles, the kerb line and the roof parapet and never runs off; everything after it runs
off in full. :class:`HydrologyState` carries that remaining capacity and the caller carries
the state across steps. Subtracting 1.5 mm from every step instead would remove 1.5 mm per
5-minute step - 18 mm/h of pure invention, more than the design storm of a legacy Mumbai
drain. Nothing refills the bucket inside a run: over a three-hour cloudburst there is no
evaporation worth the name and the puddle does not drain to somewhere the model can see.

**2. SCS-CN is a cumulative relation, applied incrementally.** ``Q`` is the runoff produced by
the *total* rainfall ``P`` since the event began, not by one step's rain. The incremental form
SPEC.md 11.2 asks for is therefore the **difference of the cumulative curve between two
times**: this module keeps cumulative ``P`` per cell in :class:`HydrologyState` and returns
``Q(P_after) - Q(P_before)`` for the step.

Applying the cumulative formula to each step's rain on its own is the classic blunder, and it
errs in a specific direction worth naming: ``Q`` is convex above the initial abstraction and
``Q(0) = 0``, so it is *superadditive* - the sum of per-step runoffs can never exceed the
runoff of their sum, and it **under**-predicts, the more so the shorter the step. At 5-minute
steps it is catastrophic rather than merely wrong: each step's few millimetres fall below the
``0.2 S`` initial abstraction, which the blunder re-pays every step, so a storm that produces
156 mm of runoff at CN 92 produces 0.5 mm of it - 0.3 % (measured in
``tests/test_hydrology.py::test_incremental_sum_equals_the_cumulative_curve``, not asserted
from memory). A pleasant consequence of doing it properly: the answer telescopes, so a run
stepped every 5 minutes and a run stepped every second produce the same cumulative runoff.

**3. The pervious fraction never sees ``d_s``.** The ``0.2 S`` initial abstraction *is* the
depression storage of the pervious surface, already inside the SCS curve; ``d_s`` is the
impervious term's own abstraction. Read the equation above - ``d_s`` sits under ``I`` and
nothing else. Subtracting both would abstract 1.5 mm twice on soil that has already had
5.6 mm withheld at CN 90.

**Units, converted once, here.** Rain arrives in mm/h because that is what Sky produces
(``varuna_sky.products``); effective rain leaves in **m/s** because that is what the
continuity equation of SPEC.md 11.3 adds to ``h``. The conversion happens at this boundary
and nowhere else: mm/h -> depth over the step in mm -> runoff depth in mm -> m/s. Inside, the
arithmetic is entirely in millimetres, which is the unit both loss terms are quoted in.

**Buildings get no rain.** :attr:`~varuna_twin.types.TerrainGrid.blocked` cells hold no water
and pass no flux, so a rate placed on one could never leave and would appear in the mass
balance as inflow that vanished. Their effective rain is set to zero. The honest reading of
that: roof runoff is dropped rather than routed to the street it really drains to, so a dense
ward is forced with slightly less water than fell on it. Routing roofs to their neighbours is
a modelling addition, not a bug fix, and is not in SPEC.md 11.2.

Determinism (rule 8): pure numpy arithmetic in a fixed order, no random draws, float64
throughout, so the same rain over the same terrain gives byte-identical effective rain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import structlog
from varuna_schemas.constants import STEP_MIN

if TYPE_CHECKING:  # pragma: no cover - keeps numpy off the runtime type surface
    from numpy.typing import NDArray

    from varuna_twin.types import TerrainGrid

log = structlog.get_logger("varuna.twin.hydrology")

__all__ = [
    "DEPRESSION_STORAGE_MM",
    "IA_RATIO",
    "MM_PER_M",
    "MONSOON_CN_RANGE",
    "SECONDS_PER_HOUR",
    "S_OFFSET_MM",
    "S_SCALE_MM",
    "HydrologyState",
    "effective_rain",
    "effective_rain_series",
    "retention_mm",
    "scs_cumulative_runoff_mm",
]

# ============================================================================== constants
DEPRESSION_STORAGE_MM = 1.5
"""``d_s``: depression storage on the impervious fraction, in mm (SPEC.md 11.2).

Spec value, not a choice. Applied once per event as a bucket - see the module docstring."""

S_SCALE_MM = 25400.0
"""Numerator of ``S = 25400/CN - 254`` (Appendix A). The imperial 1000 inches in mm."""

S_OFFSET_MM = 254.0
"""Offset of ``S = 25400/CN - 254`` (Appendix A). Ten inches in mm."""

IA_RATIO = 0.2
"""Initial abstraction as a fraction of ``S`` (Appendix A: ``Q = (P - 0.2 S)^2/(P + 0.8 S)``).

Spec value. The standard SCS 0.2; the 0.05 of the later NRCS literature would need its own
re-fitted curve numbers, which the city pipeline does not produce."""

MONSOON_CN_RANGE = (90.0, 98.0)
"""The curve-number band SPEC.md 11.2 states for saturated monsoon soils.

Outside this band is **warned about, not rejected**: a curve number is only wrong if it is
outside ``(0, 100]``, where the formula itself breaks down, and a second city's land cover may
legitimately sit a little below 90. The warning names the range so a bad ``cn.tif`` shows up
in the run log rather than silently changing the answer."""

MM_PER_M = 1000.0
"""Millimetres per metre. The depth half of the mm/h -> m/s conversion."""

SECONDS_PER_HOUR = 3600.0
"""Seconds per hour. The time half of the mm/h -> m/s conversion."""


# ============================================================================== the curve
def retention_mm(cn: NDArray[np.floating]) -> NDArray[np.floating]:
    """Potential maximum retention ``S = 25400/CN - 254`` in mm, per cell (Appendix A).

    Rejects any curve number outside ``(0, 100]``: at ``CN <= 0`` the formula divides by zero
    or returns a negative retention, and above 100 it returns a negative one, so either is a
    broken ``cn.tif`` (a nodata fill of 0 or -9999 is the usual cause) rather than a cell that
    simply infiltrates a lot. Raising here is deliberate - this runs once, before a run
    starts, and a silent clamp would put a made-up number on screen (rule 6).

    ``CN = 100`` is legal and gives ``S = 0``: a surface that abstracts nothing and runs off
    everything, which is what a curve number of 100 means.
    """
    cn64 = np.asarray(cn, dtype=np.float64)
    if not np.all(np.isfinite(cn64)):
        raise ValueError(
            f"curve number raster has {int(np.count_nonzero(~np.isfinite(cn64)))} non-finite "
            "cells; S = 25400/CN - 254 is undefined there"
        )
    bad = (cn64 <= 0.0) | (cn64 > 100.0)
    if np.any(bad):
        raise ValueError(
            f"curve number out of (0, 100] in {int(np.count_nonzero(bad))} cells "
            f"(min {float(cn64.min()):.3f}, max {float(cn64.max()):.3f}); "
            "check cn.tif nodata handling in the city pipeline"
        )
    low, high = MONSOON_CN_RANGE
    outside = int(np.count_nonzero((cn64 < low) | (cn64 > high)))
    if outside:
        log.warning(
            "curve numbers outside the monsoon band",
            cells=outside,
            band=MONSOON_CN_RANGE,
            cn_min=float(cn64.min()),
            cn_max=float(cn64.max()),
        )
    return S_SCALE_MM / cn64 - S_OFFSET_MM


def scs_cumulative_runoff_mm(
    p_mm: NDArray[np.floating], s_mm: NDArray[np.floating]
) -> NDArray[np.floating]:
    """Cumulative SCS-CN runoff ``Q`` in mm from cumulative rainfall ``P`` in mm (Appendix A).

    ``Q = (P - 0.2 S)^2 / (P + 0.8 S)`` once ``P`` exceeds the initial abstraction ``0.2 S``,
    and zero before it. **Cumulative in, cumulative out** - the name says so because the whole
    correctness of this module rests on the caller never handing this function one step's rain
    (see the module docstring). :func:`effective_rain` differences it instead.

    The branch is evaluated by masked assignment rather than :func:`numpy.where` so the
    division is never performed where ``P + 0.8 S`` is zero, which happens at the perfectly
    reasonable point ``CN = 100, P = 0``.
    """
    p = np.asarray(p_mm, dtype=np.float64)
    s = np.asarray(s_mm, dtype=np.float64)
    excess = p - IA_RATIO * s
    denominator = p + (1.0 - IA_RATIO) * s
    runoff = np.zeros(np.broadcast_shapes(p.shape, s.shape), dtype=np.float64)
    active = (excess > 0.0) & (denominator > 0.0)
    runoff[active] = np.square(excess[active]) / denominator[active]
    return runoff


# ============================================================================== event state
@dataclass(slots=True)
class HydrologyState:
    """What a cell remembers about the event so far: two accumulators and one parameter.

    The caller carries this across steps - :func:`effective_rain` advances it in place, once
    per step. It is per-cell and shaped like the terrain grid.

    ``retention_s_mm`` is not state; it is ``S`` derived once from ``cn.tif`` because
    ``25400/CN - 254`` on 168 000 cells, 36 times a cycle, is pure waste, and because deriving
    it once means the curve-number raster is validated once, before the run, rather than being
    re-checked in a hot loop.
    """

    depression_remaining_mm: NDArray[np.floating]
    """Impervious depression storage still to be filled, in mm, per unit **impervious** area.

    Starts at :data:`DEPRESSION_STORAGE_MM` everywhere and only ever decreases. It is quoted
    per unit impervious area because that is where Appendix A puts it: the ``d_s`` term sits
    inside the ``I *`` factor, so a cell that is 40 % impervious retains ``0.4 * 1.5 mm`` of
    the rain that falls on it. On a fully pervious cell the counter still runs down and is
    multiplied by ``I = 0``, contributing nothing."""

    cumulative_rain_mm: NDArray[np.floating]
    """Total rainfall on the cell since the event began, in mm - the ``P`` of the SCS curve.

    Raw rainfall: ``d_s`` is **not** taken off it, because the SCS curve carries its own
    initial abstraction ``0.2 S`` and ``d_s`` belongs to the impervious term."""

    retention_s_mm: NDArray[np.floating]
    """``S = 25400/CN - 254`` in mm per cell, from :func:`retention_mm`."""

    @classmethod
    def for_terrain(cls, terrain: TerrainGrid) -> HydrologyState:
        """A fresh state for a terrain grid: empty depression stores, no rain yet.

        Validates the two rasters this module depends on, since a run that starts with a
        nodata-filled ``imperviousness.tif`` should fail here and say so, not quietly route
        the wrong volume of water down Dr Ambedkar Road.
        """
        imperviousness = _validated_imperviousness(terrain.imperviousness)
        retention = retention_mm(terrain.cn)
        if retention.shape != imperviousness.shape:
            raise ValueError(
                f"cn {retention.shape} and imperviousness {imperviousness.shape} "
                "are not on the same grid"
            )
        blocked = np.asarray(terrain.blocked, dtype=bool)
        log.info(
            "hydrology state initialised",
            shape=tuple(int(n) for n in imperviousness.shape),
            depression_storage_mm=DEPRESSION_STORAGE_MM,
            mean_imperviousness=float(imperviousness.mean()),
            mean_retention_mm=float(retention.mean()),
            blocked_fraction=float(blocked.mean()) if blocked.size else 0.0,
        )
        return cls(
            depression_remaining_mm=np.full(
                imperviousness.shape, DEPRESSION_STORAGE_MM, dtype=np.float64
            ),
            cumulative_rain_mm=np.zeros(imperviousness.shape, dtype=np.float64),
            retention_s_mm=retention,
        )

    @property
    def shape(self) -> tuple[int, int]:
        return (
            int(self.cumulative_rain_mm.shape[0]),
            int(self.cumulative_rain_mm.shape[1]),
        )

    def copy(self) -> HydrologyState:
        """An independent copy, so a what-if can branch a run without disturbing it."""
        return HydrologyState(
            depression_remaining_mm=self.depression_remaining_mm.copy(),
            cumulative_rain_mm=self.cumulative_rain_mm.copy(),
            retention_s_mm=self.retention_s_mm.copy(),
        )


def _validated_imperviousness(imperviousness: NDArray[np.floating]) -> NDArray[np.floating]:
    """Impervious fraction as float64, rejecting anything outside ``[0, 1]``.

    Same reasoning as :func:`retention_mm`: a fraction outside the unit interval is a broken
    raster, and clamping it would invent the number the map then shows.
    """
    values = np.asarray(imperviousness, dtype=np.float64)
    if not np.all(np.isfinite(values)):
        raise ValueError(
            f"imperviousness raster has {int(np.count_nonzero(~np.isfinite(values)))} "
            "non-finite cells"
        )
    bad = (values < 0.0) | (values > 1.0)
    if np.any(bad):
        raise ValueError(
            f"imperviousness out of [0, 1] in {int(np.count_nonzero(bad))} cells "
            f"(min {float(values.min()):.3f}, max {float(values.max()):.3f})"
        )
    return values


# ============================================================================== the step
def effective_rain(
    rain_mm_h: NDArray[np.floating],
    terrain: TerrainGrid,
    state: HydrologyState,
    dt_s: float,
) -> NDArray[np.floating]:
    """Effective rain in **m/s** over one step, advancing ``state`` in place.

    ``rain_mm_h`` is the rain rate on the city grid for this step, in mm/h, held constant
    across the step - which is what a 5-minute Sky product is. ``dt_s`` is the step length in
    seconds; the two together are the only place a rate becomes a depth.

    Call this **exactly once per step**: it fills depression storage and accumulates ``P``, so
    calling it twice for the same step charges the cell twice. Use :meth:`HydrologyState.copy`
    to branch instead.

    Rain that is negative or non-finite is treated as no rain. Sky's products are neither, but
    a masked member or a resample at the domain edge can be ``nan``, and the honest reading of
    "no rain information here" is "no forcing here" rather than a ``nan`` that would poison the
    depth field and every product downstream. The count is logged when it happens.
    """
    if not np.isfinite(dt_s) or dt_s <= 0.0:
        raise ValueError(f"dt_s must be a positive number of seconds, got {dt_s!r}")

    rain = np.asarray(rain_mm_h, dtype=np.float64)
    if rain.shape != state.shape:
        raise ValueError(
            f"rain field {rain.shape} does not match the hydrology state {state.shape}"
        )

    invalid = ~np.isfinite(rain)
    if np.any(invalid):
        log.warning("non-finite rain treated as zero", cells=int(np.count_nonzero(invalid)))
        rain = np.where(invalid, 0.0, rain)
    rain = np.maximum(rain, 0.0)

    imperviousness = np.asarray(terrain.imperviousness, dtype=np.float64)
    if imperviousness.shape != state.shape:
        raise ValueError(
            f"terrain {imperviousness.shape} does not match the hydrology state {state.shape}"
        )

    # --- rate -> depth over the step, the one conversion in this module ------------------
    step_rain_mm = rain * (dt_s / SECONDS_PER_HOUR)

    # --- impervious: fill the bucket first, the rest runs off ---------------------------
    # d_s is a store the event fills ONCE (SPEC.md 11.2: "applied once per event as a
    # bucket"), not a threshold each step has to clear. Subtracting DEPRESSION_STORAGE_MM from
    # every step charges the cell 1.5 mm per step instead of 1.5 mm per event, which loses more
    # runoff the finer the step: at 5-minute steps a 36-step event lost 52.5 mm instead of 1.5,
    # and the same event scored 15.2 mm at 1-minute steps against 28.6 mm at 5-minute ones. The
    # remaining capacity is carried on the state precisely so this stays step-independent.
    absorbed_mm = np.minimum(state.depression_remaining_mm, step_rain_mm)
    state.depression_remaining_mm -= absorbed_mm
    impervious_runoff_mm = step_rain_mm - absorbed_mm

    # --- pervious: the difference of the cumulative SCS curve across the step ------------
    before_mm = scs_cumulative_runoff_mm(state.cumulative_rain_mm, state.retention_s_mm)
    state.cumulative_rain_mm += step_rain_mm
    after_mm = scs_cumulative_runoff_mm(state.cumulative_rain_mm, state.retention_s_mm)
    pervious_runoff_mm = after_mm - before_mm

    runoff_mm = imperviousness * impervious_runoff_mm + (1.0 - imperviousness) * pervious_runoff_mm

    # --- depth over the step -> rate the continuity equation can add to h ----------------
    rate_m_s = runoff_mm / (MM_PER_M * dt_s)
    blocked = np.asarray(terrain.blocked, dtype=bool)
    return np.where(blocked, 0.0, rate_m_s)


def effective_rain_series(
    rain_mm_h: NDArray[np.floating],
    terrain: TerrainGrid,
    step_min: int = STEP_MIN,
    state: HydrologyState | None = None,
) -> NDArray[np.floating]:
    """Effective rain in m/s for a whole ``(n_steps, n_rows, n_cols)`` rain cube.

    A convenience for callers that want the forcing up front - verification, the Flash
    training runs of SPEC.md 11.7 - and the readable reference the stepped solver is checked
    against. The 2D solver itself is better off calling :func:`effective_rain` per step: it
    only needs one field at a time and this allocates the whole series.

    ``state`` continues an event when given, and is advanced; omitted, the event starts here.
    """
    cube = np.asarray(rain_mm_h, dtype=np.float64)
    if cube.ndim != 3:
        raise ValueError(f"rain cube must be (n_steps, n_rows, n_cols), got shape {cube.shape}")
    if state is None:
        state = HydrologyState.for_terrain(terrain)
    dt_s = float(step_min) * 60.0
    return np.stack([effective_rain(step, terrain, state, dt_s) for step in cube])

"""Effective rainfall (SPEC.md 11.2, Appendix A, ``varuna_twin.hydrology``).

Every field here is built in the test, so each property has an answer that is known by
construction rather than by running the code and blessing what came out: a fully impervious
cell whose bucket can be counted in millimetres, a fully pervious cell checked against SCS-CN
arithmetic written out in full, and a seeded mixed grid for the two properties that must hold
everywhere at once - non-negativity and runoff never exceeding rainfall.

The load-bearing test is :func:`test_incremental_sum_equals_the_cumulative_curve`. It is the
one that separates the incremental form SPEC.md 11.2 asks for from the per-step application
of the cumulative formula, and it measures how far apart the two are on the demo's own storm.
"""

from __future__ import annotations

import numpy as np
import pytest
from varuna_twin.hydrology import (
    DEPRESSION_STORAGE_MM,
    IA_RATIO,
    HydrologyState,
    effective_rain,
    effective_rain_series,
    retention_mm,
    scs_cumulative_runoff_mm,
)
from varuna_twin.types import TerrainGrid

RES_M = 30.0
"""The city grid of SPEC.md 3.3; only the value of ``cell_area_m2`` depends on it."""

STEP_S = 300.0
"""One 5-minute forecast step in seconds (SPEC.md 10.3)."""


def _terrain(
    *,
    imperviousness: float | np.ndarray,
    cn: float | np.ndarray = 95.0,
    blocked: bool | np.ndarray = False,
    shape: tuple[int, int] = (2, 3),
) -> TerrainGrid:
    """A terrain grid whose hydrology rasters are exactly what the test asks for.

    ``z``, ``manning_n`` and the georeference are present because the contract requires them;
    this module reads none of them.
    """
    return TerrainGrid(
        z=np.zeros(shape, dtype=np.float64),
        manning_n=np.full(shape, 0.016, dtype=np.float64),
        blocked=np.broadcast_to(np.asarray(blocked, dtype=bool), shape).copy(),
        imperviousness=np.broadcast_to(np.asarray(imperviousness, dtype=np.float64), shape).copy(),
        cn=np.broadcast_to(np.asarray(cn, dtype=np.float64), shape).copy(),
        res_m=RES_M,
        crs="EPSG:32643",
        transform=(RES_M, 0.0, 0.0, 0.0, -RES_M, 0.0),
    )


def _runoff_mm(rate_m_s: np.ndarray, dt_s: float) -> np.ndarray:
    """Back out the runoff depth in mm from the rate the module returns, for readability."""
    return rate_m_s * 1000.0 * dt_s


# ====================================================================== the impervious bucket
def test_impervious_cell_passes_all_rain_after_the_first_1_5_mm() -> None:
    """I = 1: the first 1.5 mm fills the depression store, everything after runs off whole.

    12 mm/h for 5 minutes is exactly 1.0 mm a step, so the 1.5 mm bucket empties over the
    first one and a half steps and the arithmetic can be read off:

    * step 1 - 1.0 mm in, 1.0 mm stored, nothing runs off;
    * step 2 - 1.0 mm in, the last 0.5 mm stored, 0.5 mm runs off;
    * step 3 - 1.0 mm in, bucket empty, 1.0 mm runs off.

    CN is 90, whose initial abstraction is 5.64 mm - four steps of rain. An impervious cell
    that paid it would still be dry at step 3, so this also checks that the pervious loss
    never reaches the impervious fraction.
    """
    terrain = _terrain(imperviousness=1.0, cn=90.0)
    state = HydrologyState.for_terrain(terrain)
    rain = np.full(terrain.shape, 12.0)
    assert 12.0 * STEP_S / 3600.0 == pytest.approx(1.0)

    first = effective_rain(rain, terrain, state, STEP_S)
    second = effective_rain(rain, terrain, state, STEP_S)
    third = effective_rain(rain, terrain, state, STEP_S)

    assert np.all(first == 0.0)
    assert _runoff_mm(second, STEP_S) == pytest.approx(0.5)
    assert _runoff_mm(third, STEP_S) == pytest.approx(1.0)

    # Once the bucket is empty the cell is a pass-through: 12 mm/h in, 12 mm/h out.
    assert third == pytest.approx(12.0 / 1000.0 / 3600.0)


def test_impervious_event_total_is_the_rain_minus_the_bucket() -> None:
    """Over a whole event the only water an impervious cell keeps is its 1.5 mm."""
    terrain = _terrain(imperviousness=1.0, cn=98.0)
    state = HydrologyState.for_terrain(terrain)
    rates = np.array([2.0, 45.0, 90.0, 120.0, 30.0, 6.0, 0.0, 18.0])

    total_mm = 0.0
    for rate in rates:
        step = effective_rain(np.full(terrain.shape, rate), terrain, state, STEP_S)
        total_mm = total_mm + _runoff_mm(step, STEP_S)

    rain_mm = float(rates.sum()) * STEP_S / 3600.0
    assert rain_mm > DEPRESSION_STORAGE_MM
    assert total_mm == pytest.approx(rain_mm - DEPRESSION_STORAGE_MM, rel=1e-12)


# ============================================================================== the SCS curve
def test_pervious_cell_follows_the_scs_curve() -> None:
    """I = 0: runoff is the SCS curve, checked against arithmetic written out here.

    CN 90, the dry end of the band SPEC.md 11.2 states for saturated monsoon soils::

        S  = 25400/90 - 254 = 282.2222222 - 254 =  28.2222222 mm
        Ia = 0.2 * S                            =   5.6444444 mm
        P  = 50 mm  (50 mm/h for one hour)
        Q  = (50 - 5.6444444)^2 / (50 + 22.5777778)
           =        44.3555556^2 / 72.5777778
           =        1967.4153086 / 72.5777778
           =          27.1076818 mm

    So 50 mm of rain on saturated Mumbai soil yields 27.1 mm of runoff and the ground keeps
    22.9 mm. The tolerance on the module's own answer is 1e-12 relative, which is tight enough
    that charging the 1.5 mm depression store to this fraction as well - the mistake SPEC.md
    11.2 warns against, since 0.2 S is already the pervious surface's abstraction - would fail
    it by three orders of magnitude.
    """
    s_mm = 25400.0 / 90.0 - 254.0
    assert s_mm == pytest.approx(28.2222222, abs=1e-6)
    assert IA_RATIO * s_mm == pytest.approx(5.6444444, abs=1e-6)

    expected_mm = (50.0 - IA_RATIO * s_mm) ** 2 / (50.0 + (1.0 - IA_RATIO) * s_mm)
    assert expected_mm == pytest.approx(27.1076818, abs=1e-6)

    terrain = _terrain(imperviousness=0.0, cn=90.0)
    state = HydrologyState.for_terrain(terrain)
    rate = effective_rain(np.full(terrain.shape, 50.0), terrain, state, 3600.0)

    assert _runoff_mm(rate, 3600.0) == pytest.approx(expected_mm, rel=1e-12)


def test_pervious_cell_is_dry_below_the_initial_abstraction() -> None:
    """Rain that never exceeds ``0.2 S`` produces no runoff at all - but is remembered.

    The second half matters more than the first: the water is not discarded, it is banked in
    the cumulative rain that the next step's curve is evaluated at. A state that forgot it
    would make a storm's runoff depend on how the forecast happened to be chopped into steps.
    """
    terrain = _terrain(imperviousness=0.0, cn=90.0)
    state = HydrologyState.for_terrain(terrain)
    initial_abstraction_mm = IA_RATIO * (25400.0 / 90.0 - 254.0)

    for _ in range(6):  # 6 steps of 6 mm/h = 3.0 mm, below the 5.64 mm abstraction
        assert np.all(effective_rain(np.full(terrain.shape, 6.0), terrain, state, STEP_S) == 0.0)

    assert np.all(state.cumulative_rain_mm == pytest.approx(3.0))
    assert 3.0 < initial_abstraction_mm


def test_curve_number_100_runs_off_everything_and_survives_zero_rain() -> None:
    """CN = 100 gives S = 0, so Q = P: the degenerate end of the curve, without a divide by 0."""
    assert float(retention_mm(np.array([100.0]))[0]) == 0.0
    assert float(scs_cumulative_runoff_mm(np.array([0.0]), np.array([0.0]))[0]) == 0.0

    terrain = _terrain(imperviousness=0.0, cn=100.0)
    state = HydrologyState.for_terrain(terrain)
    rate = effective_rain(np.full(terrain.shape, 60.0), terrain, state, STEP_S)
    assert _runoff_mm(rate, STEP_S) == pytest.approx(5.0, rel=1e-12)


# ================================================================= the incremental form itself
def test_incremental_sum_equals_the_cumulative_curve() -> None:
    """The property SPEC.md 11.2's "incremental form" means, and the blunder it excludes.

    Part one: 60 one-minute steps of varying rain produce the same event runoff as one
    60-minute step delivering the same total. That holds only if the module differences a
    cumulative curve; any per-step scheme depends on the step length.

    Part two: the same storm run through the cumulative formula applied to each step's rain on
    its own. ``Q`` is convex above ``0.2 S`` with ``Q(0) = 0``, hence superadditive, so that
    blunder can only **under**-predict - and at 5-minute steps it re-pays the initial
    abstraction 36 times over rain that barely exceeds it. On 180 mm of rain at CN 92 the
    correct answer is 155.96 mm of runoff and the blunder gives 0.54 mm: 0.35 % of it. This is
    the number quoted in the module docstring, measured here rather than remembered.
    """
    shape = (1, 1)
    terrain = _terrain(imperviousness=0.4, cn=92.0, shape=shape)
    rates = np.array([2.0, 8.0, 35.0, 120.0, 60.0, 15.0] * 10)  # 60 minutes, one a minute
    assert rates.size == 60

    fine = HydrologyState.for_terrain(terrain)
    fine_mm = 0.0
    for rate in rates:
        step = effective_rain(np.full(shape, rate), terrain, fine, 60.0)
        fine_mm += float(_runoff_mm(step, 60.0)[0, 0])

    coarse = HydrologyState.for_terrain(terrain)
    one_step = effective_rain(np.full(shape, float(rates.mean())), terrain, coarse, 3600.0)
    coarse_mm = float(_runoff_mm(one_step, 3600.0)[0, 0])

    assert fine_mm == pytest.approx(coarse_mm, rel=1e-9)
    assert float(fine.cumulative_rain_mm[0, 0]) == pytest.approx(
        float(coarse.cumulative_rain_mm[0, 0]), rel=1e-12
    )

    # --- and the blunder, on a purely pervious cell so only the SCS half is measured -------
    pervious = _terrain(imperviousness=0.0, cn=92.0, shape=shape)
    state = HydrologyState.for_terrain(pervious)
    correct_mm = 0.0
    for _ in range(36):  # 36 steps of 60 mm/h for 5 min = 5 mm each, 180 mm in all
        step = effective_rain(np.full(shape, 60.0), pervious, state, STEP_S)
        correct_mm += float(_runoff_mm(step, STEP_S)[0, 0])

    s_mm = retention_mm(np.array([92.0]))
    blunder_mm = 36.0 * float(scs_cumulative_runoff_mm(np.array([5.0]), s_mm)[0])

    assert correct_mm == pytest.approx(155.9635771, abs=1e-5)
    assert blunder_mm == pytest.approx(0.5390304, abs=1e-6)
    assert blunder_mm < 0.01 * float(correct_mm)


def test_the_two_terms_do_not_contaminate_each_other() -> None:
    """A half-impervious cell is exactly the average of the two pure cells.

    Appendix A weights the two losses by ``I`` and ``1 - I`` and lets neither touch the other's
    fraction, so this linearity is the equation restated. It fails the moment the SCS curve is
    evaluated on rain that has already had ``d_s`` removed, or the bucket is charged against
    the pervious share.
    """
    shape = (1, 1)
    rates = [4.0, 30.0, 75.0, 12.0, 95.0]

    totals = []
    for share in (1.0, 0.0, 0.5):
        terrain = _terrain(imperviousness=share, cn=93.0, shape=shape)
        state = HydrologyState.for_terrain(terrain)
        total = 0.0
        for rate in rates:
            step = effective_rain(np.full(shape, rate), terrain, state, STEP_S)
            total += float(_runoff_mm(step, STEP_S)[0, 0])
        totals.append(total)

    impervious_mm, pervious_mm, mixed_mm = totals
    assert impervious_mm != pytest.approx(pervious_mm)  # the test would be vacuous otherwise
    assert mixed_mm == pytest.approx(0.5 * impervious_mm + 0.5 * pervious_mm, rel=1e-12)


# ================================================================ zero rain, and no state drift
def test_zero_rain_gives_exactly_zero_and_no_state_drift() -> None:
    """A dry cycle forces nothing and remembers nothing - exactly zero, not nearly zero."""
    terrain = _terrain(imperviousness=0.55, cn=95.0)
    state = HydrologyState.for_terrain(terrain)
    before = state.copy()

    for _ in range(12):
        rate = effective_rain(np.zeros(terrain.shape), terrain, state, STEP_S)
        assert np.array_equal(rate, np.zeros(terrain.shape))

    assert np.array_equal(state.depression_remaining_mm, before.depression_remaining_mm)
    assert np.array_equal(state.cumulative_rain_mm, before.cumulative_rain_mm)
    assert np.all(state.depression_remaining_mm == DEPRESSION_STORAGE_MM)


def test_negative_and_non_finite_rain_are_treated_as_no_rain() -> None:
    """Neither can charge the depression store nor bank cumulative rain."""
    terrain = _terrain(imperviousness=0.5, cn=94.0, shape=(1, 3))
    state = HydrologyState.for_terrain(terrain)

    rate = effective_rain(np.array([[np.nan, -12.0, 0.0]]), terrain, state, STEP_S)

    assert np.all(rate == 0.0)
    assert np.all(state.cumulative_rain_mm == 0.0)
    assert np.all(state.depression_remaining_mm == DEPRESSION_STORAGE_MM)


# ==================================================================== the properties, everywhere
def test_runoff_never_exceeds_rainfall_in_any_cell_at_any_step() -> None:
    """Water cannot be created. Seeded (rule 8), mixed land cover, three hours of storm."""
    rng = np.random.default_rng(2019)
    shape = (16, 24)
    terrain = _terrain(
        imperviousness=rng.uniform(0.0, 1.0, shape),
        cn=rng.uniform(90.0, 98.0, shape),
        shape=shape,
    )
    state = HydrologyState.for_terrain(terrain)

    for _ in range(36):
        rain = rng.uniform(0.0, 120.0, shape)
        rate = effective_rain(rain, terrain, state, STEP_S)
        assert np.all(np.isfinite(rate))
        assert np.all(rate >= 0.0)
        assert np.all(_runoff_mm(rate, STEP_S) <= rain * STEP_S / 3600.0 + 1e-12)

    # Over the whole event, too: the losses only ever accumulate.
    assert np.all(state.depression_remaining_mm >= 0.0)
    assert np.all(state.depression_remaining_mm <= DEPRESSION_STORAGE_MM)


def test_rain_in_mm_per_hour_becomes_a_rate_in_m_per_second() -> None:
    """The one unit conversion in the module, on a cell that keeps nothing.

    36 mm/h = 0.036 m/h = 0.036 / 3600 m/s = 1e-5 m/s.
    """
    terrain = _terrain(imperviousness=1.0, cn=98.0, shape=(1, 1))
    state = HydrologyState.for_terrain(terrain)
    effective_rain(np.full((1, 1), 36.0), terrain, state, 3600.0)  # fills the 1.5 mm bucket

    rate = effective_rain(np.full((1, 1), 36.0), terrain, state, 3600.0)
    assert float(rate[0, 0]) == pytest.approx(1e-5, rel=1e-12)


def test_buildings_receive_no_effective_rain() -> None:
    """Blocked cells hold no water, so they are forced with none (module docstring)."""
    blocked = np.array([[True, False], [False, True]])
    terrain = _terrain(imperviousness=1.0, cn=98.0, blocked=blocked, shape=(2, 2))
    state = HydrologyState.for_terrain(terrain)
    effective_rain(np.full((2, 2), 60.0), terrain, state, STEP_S)

    rate = effective_rain(np.full((2, 2), 60.0), terrain, state, STEP_S)
    assert np.all(rate[blocked] == 0.0)
    assert np.all(rate[~blocked] > 0.0)


def test_two_identical_runs_are_byte_identical() -> None:
    """Rule 8: same inputs, same bytes."""
    rng = np.random.default_rng(2019)
    shape = (8, 8)
    terrain = _terrain(
        imperviousness=rng.uniform(0.0, 1.0, shape),
        cn=rng.uniform(90.0, 98.0, shape),
        shape=shape,
    )
    cube = rng.uniform(0.0, 90.0, (12, *shape))

    first = effective_rain_series(cube, terrain)
    second = effective_rain_series(cube, terrain)
    assert first.tobytes() == second.tobytes()


def test_series_is_the_stepped_solver_run_end_to_end() -> None:
    """``effective_rain_series`` is a loop over ``effective_rain``, and must stay one."""
    rng = np.random.default_rng(702)
    shape = (4, 5)
    terrain = _terrain(
        imperviousness=rng.uniform(0.0, 1.0, shape),
        cn=rng.uniform(90.0, 98.0, shape),
        shape=shape,
    )
    cube = rng.uniform(0.0, 100.0, (9, *shape))

    series = effective_rain_series(cube, terrain, step_min=5)

    state = HydrologyState.for_terrain(terrain)
    stepped = np.stack([effective_rain(step, terrain, state, STEP_S) for step in cube])
    assert np.array_equal(series, stepped)


# ============================================================================== broken rasters
def test_a_nodata_curve_number_raster_is_rejected() -> None:
    """A ``cn.tif`` full of zeros or -9999 must fail loudly, not be clamped into a number."""
    with pytest.raises(ValueError, match="out of"):
        retention_mm(np.array([[95.0, 0.0]]))
    with pytest.raises(ValueError, match="out of"):
        retention_mm(np.array([[95.0, -9999.0]]))
    with pytest.raises(ValueError, match="out of"):
        retention_mm(np.array([[95.0, 101.0]]))
    with pytest.raises(ValueError, match="non-finite"):
        retention_mm(np.array([[95.0, np.nan]]))


def test_a_broken_imperviousness_raster_is_rejected() -> None:
    with pytest.raises(ValueError, match="imperviousness out of"):
        HydrologyState.for_terrain(_terrain(imperviousness=1.4))
    with pytest.raises(ValueError, match="imperviousness out of"):
        HydrologyState.for_terrain(_terrain(imperviousness=-0.1))
    with pytest.raises(ValueError, match="non-finite"):
        HydrologyState.for_terrain(_terrain(imperviousness=np.nan))


def test_a_mismatched_rain_field_or_step_is_rejected() -> None:
    """The state is bound to one grid; silently broadcasting onto another would be a wrong map."""
    terrain = _terrain(imperviousness=0.5, shape=(2, 3))
    state = HydrologyState.for_terrain(terrain)

    with pytest.raises(ValueError, match="does not match"):
        effective_rain(np.zeros((3, 2)), terrain, state, STEP_S)
    with pytest.raises(ValueError, match="positive number of seconds"):
        effective_rain(np.zeros((2, 3)), terrain, state, 0.0)
    with pytest.raises(ValueError, match="positive number of seconds"):
        effective_rain(np.zeros((2, 3)), terrain, state, -300.0)
    with pytest.raises(ValueError, match="n_steps"):
        effective_rain_series(np.zeros((2, 3)), terrain)

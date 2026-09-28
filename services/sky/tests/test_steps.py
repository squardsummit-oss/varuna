"""The STEPS ensemble and its fallback (SPEC.md 11.1 step 5, 17; the P3.7 properties).

The four properties tested here are the ones the spec names: spread grows with lead, a dry
input gives a dry cube, a translating storm is carried along the motion field, and the
fallback is interchangeable with pySTEPS - same shape, same dtype, and deterministic.

Everything runs on a 64 x 64 domain with a handful of members and steps, so the file finishes
in seconds. The production 20 x 36 cube on the 120 x 120 Sky grid is the integrator's problem.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import numpy as np
import pytest
from varuna_sky import fallback_steps, steps
from varuna_sky.motion import MAX_RAIN_MM_H, optical_flow
from varuna_sky.types import MotionField, RainEnsemble, SkyInputs, ZRParams

from .test_motion import MP, N_PX, frames_from_rain, inputs_for, translating_rain

Nowcaster = Callable[[np.ndarray, MotionField, SkyInputs, ZRParams], RainEnsemble]

NOWCASTERS: dict[str, Nowcaster] = {
    "pysteps": steps.nowcast,
    "fallback": fallback_steps.nowcast,
}
"""Both must satisfy every property below: SPEC.md 17 puts them behind one function."""

SPREAD_MEMBERS = 20
"""Enough members that the across-member standard deviation is not swamped by sampling error.
The standard error of an n-member standard deviation is about sd/sqrt(2(n-1)) - 16 % at 20
members, 32 % at 6 - which is why the monotonicity check below carries a tolerance."""


def storm_setup(
    du: float = 3.0,
    dv: float = 1.0,
    n_members: int = 6,
    n_steps: int = 6,
) -> tuple[np.ndarray, MotionField, SkyInputs]:
    """A translating cell, its measured motion field, and one cycle's inputs."""
    rain = translating_rain(du=du, dv=dv)
    frames = frames_from_rain(rain)
    motion = optical_flow(frames, MP)
    return rain, motion, inputs_for(frames, n_members=n_members, n_steps=n_steps)


def domain_mean_spread(ensemble: RainEnsemble) -> np.ndarray:
    """Across-member standard deviation of the domain-mean rain rate, one value per step."""
    return ensemble.rain_mm_h.mean(axis=(2, 3)).std(axis=0, ddof=1)


def blob_centre(field: np.ndarray) -> tuple[float, float]:
    """Centroid (row, col) of the pixels above half the field's maximum.

    Half-maximum ignores the stratiform background, which otherwise drags every centroid
    towards the middle of the domain and hides the displacement being tested.
    """
    mask = field >= 0.5 * float(field.max())
    rows, cols = np.nonzero(mask)
    weights = field[mask]
    return (
        float(np.average(rows, weights=weights)),
        float(np.average(cols, weights=weights)),
    )


# ============================================================================ shape, labels
@pytest.mark.parametrize("name", list(NOWCASTERS))
def test_the_cube_has_the_shape_and_times_the_contract_promises(name: str) -> None:
    rain, motion, inputs = storm_setup()
    ensemble = NOWCASTERS[name](rain[-1], motion, inputs, MP)

    assert ensemble.rain_mm_h.shape == (inputs.n_members, inputs.n_steps, N_PX, N_PX)
    assert ensemble.rain_mm_h.dtype == np.float64
    assert np.isfinite(ensemble.rain_mm_h).all(), "no nan reaches the products stage"
    assert (ensemble.rain_mm_h >= 0.0).all(), "rain rate is never negative"
    assert ensemble.n_members == inputs.n_members
    assert ensemble.n_steps == inputs.n_steps
    assert ensemble.seed == inputs.seed
    assert ensemble.motion is motion
    assert ensemble.zr is MP
    # Step k is cycle_ts + (k + 1) * step_min: the first step is the first forecast instant.
    assert ensemble.times[0] == inputs.cycle_ts + timedelta(minutes=inputs.step_min)
    assert ensemble.times[-1] == inputs.cycle_ts + timedelta(
        minutes=inputs.step_min * inputs.n_steps
    )


def test_the_source_field_says_which_nowcaster_ran() -> None:
    rain, motion, inputs = storm_setup()
    assert steps.nowcast(rain[-1], motion, inputs, MP).source == "pysteps_steps"
    assert fallback_steps.nowcast(rain[-1], motion, inputs, MP).source == "fallback_steps"


def test_a_pysteps_failure_falls_back_and_is_labelled(monkeypatch: pytest.MonkeyPatch) -> None:
    """SPEC.md 17: the caller never chooses, and the run is never quietly downgraded."""
    rain, motion, inputs = storm_setup()

    def explode(*_args: object, **_kwargs: object) -> RainEnsemble:
        raise RuntimeError("pysteps is not installed")

    monkeypatch.setattr(steps, "steps_nowcast", explode)
    ensemble = steps.nowcast(rain[-1], motion, inputs, MP)
    assert ensemble.source == "fallback_steps"
    assert ensemble.rain_mm_h.shape == (inputs.n_members, inputs.n_steps, N_PX, N_PX)


def test_the_analysis_may_arrive_as_a_history_or_as_a_single_field() -> None:
    """The merge hands over one field; a caller with its own history may pass the stack."""
    rain, motion, inputs = storm_setup()
    from_field = steps.nowcast(rain[-1], motion, inputs, MP)
    from_stack = steps.nowcast(rain, motion, inputs, MP)
    assert from_field.rain_mm_h.shape == from_stack.rain_mm_h.shape
    assert np.allclose(from_field.rain_mm_h, from_stack.rain_mm_h)


# ============================================================================ spread
@pytest.mark.parametrize("name", list(NOWCASTERS))
def test_ensemble_spread_grows_with_lead_time(name: str) -> None:
    """Members must agree at +5 min and disagree at +40: that is what a nowcast ensemble is for."""
    rain, motion, inputs = storm_setup(n_members=SPREAD_MEMBERS, n_steps=8)
    spread = domain_mean_spread(NOWCASTERS[name](rain[-1], motion, inputs, MP))

    assert spread[-1] > 1.5 * spread[0], f"spread barely grew: {spread}"
    # A finite ensemble estimates its own spread with sampling error, so a single step may dip
    # while the underlying spread rises. The tolerance is a quarter of the largest spread,
    # which is comfortably above the ~16 % standard error at SPREAD_MEMBERS members.
    tolerance = 0.25 * float(spread.max())
    assert np.all(np.diff(spread) >= -tolerance), f"spread is not rising: {spread}"


# ============================================================================ dry
@pytest.mark.parametrize("name", list(NOWCASTERS))
def test_a_dry_input_yields_a_dry_cube(name: str) -> None:
    dry = np.zeros((3, N_PX, N_PX))
    frames = frames_from_rain(dry)
    motion = optical_flow(frames, MP)
    inputs = inputs_for(frames, n_members=4, n_steps=4)

    ensemble = NOWCASTERS[name](dry[-1], motion, inputs, MP)
    assert ensemble.rain_mm_h.shape == (4, 4, N_PX, N_PX)
    assert float(ensemble.rain_mm_h.max()) == 0.0, "no rain may be invented from a dry sky"
    assert motion.method == "zero"


# ============================================================================ advection
@pytest.mark.parametrize("name", list(NOWCASTERS))
def test_a_translating_storm_moves_along_the_motion_field(name: str) -> None:
    """The cell moves 3 px east and 1 px south per 10-minute frame, so over eight 5-minute
    steps its centre must travel about 12 px east and 4 px south.

    Measured as the *median of the members' own centroids*, not the centroid of the ensemble
    mean, and on :data:`SPREAD_MEMBERS` members rather than a handful. Transport and dispersion
    are different things and the mean field confounds them: pySTEPS perturbs each member's
    advection velocity (its ``bps`` perturbator), so by step eight the members' centroids here
    are genuinely 10-18 px apart, and superposing them puts the half-maximum contour wherever
    the brightest member happens to be. On six members that measured a row displacement of
    +8.4 px against a true +4.0; on twenty members the median reads +2.9. The fallback, whose
    members share one velocity field, sits at +3.9 either way - which is what told us the
    disagreement was in the measurement and not in pySTEPS' advection.
    """
    rain, motion, inputs = storm_setup(du=3.0, dv=1.0, n_members=SPREAD_MEMBERS, n_steps=8)
    ensemble = NOWCASTERS[name](rain[-1], motion, inputs, MP)

    start_row, start_col = blob_centre(rain[-1])
    centres = np.array([blob_centre(member) for member in ensemble.rain_mm_h[:, -1]])
    end_row, end_col = float(np.median(centres[:, 0])), float(np.median(centres[:, 1]))
    steps_per_interval = inputs.step_min / (inputs.frames.interval.total_seconds() / 60.0)
    expected_col = 3.0 * steps_per_interval * inputs.n_steps
    expected_row = 1.0 * steps_per_interval * inputs.n_steps

    assert end_col - start_col == pytest.approx(expected_col, abs=4.0)
    assert end_row - start_row == pytest.approx(expected_row, abs=4.0)
    assert end_col > start_col, "the storm must travel east, the way the wind blew it"


def test_a_zero_motion_field_leaves_the_storm_where_it_is() -> None:
    """With no measured flow the fallback must not invent a direction to drift in.

    The storm is *still* as well as the field being zero. A translating history read through a
    zero motion field is a contradiction - it says "nothing moved" about frames in which
    something plainly did - and it is tested one case below, where what matters is that the
    nowcast stays finite rather than where it lands.
    """
    rain, _, inputs = storm_setup(du=0.0, dv=0.0, n_members=4, n_steps=6)
    still = MotionField(
        u=np.zeros((N_PX, N_PX)),
        v=np.zeros((N_PX, N_PX)),
        interval=timedelta(minutes=10),
        res_m=500.0,
        method="zero",
    )
    ensemble = fallback_steps.nowcast(rain[-1], still, inputs, MP)
    start = blob_centre(rain[-1])
    end = blob_centre(ensemble.rain_mm_h[:, -1].mean(axis=0))
    assert end[0] == pytest.approx(start[0], abs=2.0)
    assert end[1] == pytest.approx(start[1], abs=2.0)


# ============================================================================ ill-conditioned
@pytest.mark.parametrize("name", list(NOWCASTERS))
def test_frames_the_motion_field_does_not_explain_still_give_physical_rain(name: str) -> None:
    """A history the motion field cannot align must not be nowcast into impossible rain.

    Telling a nowcaster the storm is stationary while handing it frames in which the storm
    moved 3 px per interval is the worst case optical flow can hand downstream: the lag-2
    correlation of every cascade level collapses, and an AR(2) fitted to that lands on complex
    roots of modulus ~0.99 - a barely damped ringing. Before
    :func:`~varuna_sky.fallback_steps._yule_walker` guarded against it, six steps of that
    reached 10,589 mm/h, roughly 340 times the 31 mm/h that went in.

    The cycle cannot afford that: SPEC.md 11.11 expects Sky to degrade rather than fail, and
    rule 6 says every number VARUNA publishes has to be one it can defend.
    """
    rain, _, inputs = storm_setup(du=3.0, dv=1.0, n_members=4, n_steps=6)
    still = MotionField(
        u=np.zeros((N_PX, N_PX)),
        v=np.zeros((N_PX, N_PX)),
        interval=timedelta(minutes=10),
        res_m=500.0,
        method="zero",
    )
    ensemble = NOWCASTERS[name](rain[-1], still, inputs, MP)

    assert np.isfinite(ensemble.rain_mm_h).all()
    assert ensemble.rain_mm_h.max() <= MAX_RAIN_MM_H
    # Well inside the ceiling, too: the guard should keep the fit sane, not lean on the clip.
    assert ensemble.rain_mm_h.max() < 10.0 * float(rain[-1].max())


# ============================================================================ determinism
def test_the_fallback_is_deterministic_for_a_fixed_seed() -> None:
    rain, motion, inputs = storm_setup()
    first = fallback_steps.nowcast(rain[-1], motion, inputs, MP)
    second = fallback_steps.nowcast(rain[-1], motion, inputs, MP)
    assert np.array_equal(first.rain_mm_h, second.rain_mm_h), "rule 8: same inputs, same cube"


def test_the_fallback_seed_actually_changes_the_members() -> None:
    rain, motion, inputs = storm_setup()
    other = inputs_for(inputs.frames, n_members=inputs.n_members, n_steps=inputs.n_steps, seed=7)
    first = fallback_steps.nowcast(rain[-1], motion, inputs, MP)
    second = fallback_steps.nowcast(rain[-1], motion, other, MP)
    assert not np.array_equal(first.rain_mm_h, second.rain_mm_h)


def test_pysteps_is_deterministic_for_a_fixed_seed() -> None:
    rain, motion, inputs = storm_setup(n_members=4, n_steps=4)
    first = steps.nowcast(rain[-1], motion, inputs, MP)
    second = steps.nowcast(rain[-1], motion, inputs, MP)
    assert np.array_equal(first.rain_mm_h, second.rain_mm_h)


# ============================================================ the split ensemble (P3.7)
# SPEC.md 11.1 budgets Sky at 5 s and it missed at 6.32 s warm, almost all of it the pySTEPS
# nowcast, which measures as pure per-member cost (see varuna_sky.ensemble_pool). So the members
# are split across worker processes. The split is only legitimate if it reproduces the cube the
# single call produces - the seven demo cycles are already baked, and rule 8 says a re-bake is
# byte-identical - and that is what these tests pin. They also pin VARUNA to a pySTEPS internal:
# the per-member seed chain. If pySTEPS changes it, the first test here goes red, which is where
# that should be found rather than in a silently different bake.
def test_splitting_the_ensemble_reproduces_the_single_call_element_for_element() -> None:
    """Eight members as 4 + 4, with each group given the seed pySTEPS would have held there."""
    from dataclasses import replace

    from varuna_sky.ensemble_pool import member_seed

    rain, motion, inputs = storm_setup(n_members=8, n_steps=4)
    whole = steps.steps_nowcast(rain[-1], motion, inputs, MP).rain_mm_h

    parts = []
    for offset in (0, 4):
        group = replace(inputs, n_members=4, seed=member_seed(inputs.seed, offset))
        parts.append(steps.steps_nowcast(rain[-1], motion, group, MP).rain_mm_h)
    split = np.concatenate(parts, axis=0)

    assert np.array_equal(whole, split), (
        "a group starting at member m must be pySTEPS' member m; if this fails, pySTEPS has "
        "changed how it chains per-member random states and ensemble_pool.member_seed is stale"
    )


def test_a_worker_group_is_not_the_first_group_by_accident() -> None:
    """The guard on the test above: members 4-7 must differ from members 0-3, or "identical"
    would be satisfied by a chain that ignored the offset."""
    from varuna_sky.ensemble_pool import member_seed

    rain, motion, inputs = storm_setup(n_members=8, n_steps=4)
    assert member_seed(inputs.seed, 0) == inputs.seed
    assert member_seed(inputs.seed, 4) != inputs.seed
    cube = steps.steps_nowcast(rain[-1], motion, inputs, MP).rain_mm_h
    assert not np.array_equal(cube[:4], cube[4:])


def test_member_groups_spread_the_remainder_over_the_leading_groups() -> None:
    """The stage ends when its slowest group ends, so 20 over 3 is 7 + 7 + 6, not 6 + 6 + 8."""
    from varuna_sky.ensemble_pool import member_groups

    assert member_groups(20, 4) == ((0, 5), (5, 5), (10, 5), (15, 5))
    assert member_groups(20, 3) == ((0, 7), (7, 7), (14, 6))
    assert member_groups(3, 8) == ((0, 1), (1, 1), (2, 1))
    assert member_groups(1, 4) == ((0, 1),)


def test_the_worker_count_can_be_pinned_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``VARUNA_SKY_WORKERS=1`` is the escape hatch for a machine where a pool cannot start."""
    from varuna_sky.ensemble_pool import WORKERS_ENV, worker_count

    monkeypatch.setenv(WORKERS_ENV, "1")
    assert worker_count(20) == 1
    monkeypatch.setenv(WORKERS_ENV, "3")
    assert worker_count(20) == 3
    monkeypatch.setenv(WORKERS_ENV, "not a number")
    assert worker_count(20) >= 1, "a bad value is ignored, never raised into a cycle"
    monkeypatch.delenv(WORKERS_ENV)
    assert worker_count(2) == 1, "two members are not worth a worker's pickle round trip"


# ============================================================ interpolated-lead mass (11.1)
# VARUNA asks for 5-minute leads from 10-minute radar, so half the lead times land between two
# of pySTEPS' integer steps, and pySTEPS reaches those by blending the bracketing states in dBR
# - a geometric mean in rain, which is below the arithmetic one wherever the two fields differ.
# varuna_sky.steps.correct_interpolated_mass carries the measured saw-tooth this produced and
# what it is corrected to. These tests pin the two properties that make the correction honest
# rather than a fudge: it hits the target the neighbours imply, and it does not push the tail
# above what the same lead's neighbours already carry (which is what ADR-0040 rejected
# probmatching="mean" for).
def _bracketed_totals(cube: np.ndarray, analysis: np.ndarray) -> tuple[float, float, float]:
    """``(target, actual, analysis)`` domain totals for the first interpolated lead.

    With 10-minute frames and 5-minute steps, output 0 is lead 0.5 and output 1 is lead 1.0,
    so the two whole steps bracketing output 0 are the analysis itself and output 1.
    """
    member = np.nan_to_num(cube[0])
    analysis_total = float(np.nan_to_num(analysis).sum())
    next_whole = float(member[1].sum())
    return 0.5 * (analysis_total + next_whole), float(member[0].sum()), analysis_total


def test_an_interpolated_lead_carries_the_mass_its_whole_step_neighbours_imply() -> None:
    rain, motion, inputs = storm_setup(n_members=4, n_steps=4)
    cube = steps.steps_nowcast(rain[-1], motion, inputs, MP).rain_mm_h
    target, actual, _ = _bracketed_totals(cube, rain[-1])
    assert actual == pytest.approx(target, rel=0.02), (
        f"the interpolated lead holds {actual:.0f} mm/h against the {target:.0f} mm/h its "
        "whole-step neighbours imply; the dBR blend has been left uncorrected"
    )


TAIL_ORDER_TOL = 0.05
"""Slack on the tail ordering below, because this file's fixture cannot resolve it.

``storm_setup`` is one Gaussian translating a few pixels over a uniform background, so the
analysis, the interpolated lead and the following whole step have almost the same 99.9th
percentile - measured 30.55, 30.20 and 30.28 mm/h, a spread of 1 %. A strict ordering there
would be a coin toss on rounding. The ordering is *measured* on the storm designer's own field
in ``test_pipeline.py``, where the three are 73.06, 29.51 and 26.64 mm/h and the ordering is
the whole point; this test is the cheap guard that the correction has not run away.
"""


def test_the_mass_correction_restores_a_tail_rather_than_inventing_one() -> None:
    """The corrected lead's 99.9th percentile must sit between the two fields it lies between
    in time - below the analysis it decays from, above the whole step it decays towards.

    Uncorrected it sits *below both*, which is the signature of the log blend rather than of a
    forecast, and is why the ordering is the assertion. ADR-0040 rejected
    ``probmatching_method="mean"`` for the same mass deficit because it raised the 99.9th
    percentile 52 % above the analysis; an upper bound of the analysis' own tail is the
    property that rejection was really about.
    """
    rain, motion, inputs = storm_setup(n_members=4, n_steps=4)
    cube = np.nan_to_num(steps.steps_nowcast(rain[-1], motion, inputs, MP).rain_mm_h[0])
    analysis_p999 = float(np.percentile(np.nan_to_num(rain[-1]), 99.9))
    lead_p999 = float(np.percentile(cube[0], 99.9))
    whole_p999 = float(np.percentile(cube[1], 99.9))
    message = (
        f"p99.9 analysis={analysis_p999:.2f} interpolated lead={lead_p999:.2f} "
        f"whole step={whole_p999:.2f}: the corrected lead must lie between them in time"
    )
    assert lead_p999 <= analysis_p999 * (1.0 + TAIL_ORDER_TOL), message
    assert lead_p999 >= whole_p999 * (1.0 - TAIL_ORDER_TOL), message


def test_the_correction_leaves_a_whole_step_cadence_alone() -> None:
    """Asked for lead times that are whole input steps, nothing is interpolated and nothing is
    corrected - the function has to be a no-op there, or it would be a tuning knob."""
    from varuna_sky.steps import correct_interpolated_mass

    cube = np.ones((2, 3, 4, 4))
    out, corrected = correct_interpolated_mass(cube, [1.0, 2.0, 3.0], np.full((4, 4), 5.0))
    assert corrected == 0
    assert out is cube


def test_the_correction_leaves_a_dry_cube_dry() -> None:
    from varuna_sky.steps import correct_interpolated_mass

    cube = np.zeros((2, 3, 4, 4))
    out, corrected = correct_interpolated_mass(cube, [0.5, 1.0, 1.5], np.zeros((4, 4)))
    assert corrected == 0
    assert not np.any(out), "a dry forecast stays dry; there is no mass to restore"

"""The properties every what-if number rests on (SPEC.md 11.7, task P7.5).

`varuna_flash.model.simulate` is the arithmetic under the what-if drawer, the delta table and
the 50-member products of P7.6. Until now the only test in this package that touched the model
was `test_whatif`, which exercises it through `run_scenario`; the recursion itself - its member
axis, its broadcast of an AOI hyetograph across segments, its blockage term - had nothing
guarding it, so a change to any of those shapes would have surfaced as a wrong depth on a map
rather than a red test.

These cases are deliberately arithmetic: a two-segment model built by hand, no fitted npz,
no file I/O, so they run in milliseconds and can be read as a statement about the model rather
than about a particular Mumbai fit. They pin, in order:

* zero rain returns the base state untouched, which is what makes the model a perturbation
  around a reference rather than a depth field in its own right;
* the peak is monotone non-decreasing in the rain scale, so "rain +30 %" can never come back
  drier than the run it is compared against;
* cleaning a pipe never raises its own street's peak, and moves no other street *at all* -
  the element-wise property `varuna_flash.whatif.attribute` refuses on (ADR-0042);
* the returned array is ``(n_steps, n_segments)`` for both the per-segment and the AOI-mean
  rain shapes.

The `draw_*` cases below cover `draw_members`, which is where the ensemble stops being one
member wide (P7.6). They pin what rule 8 and 11.7 each demand of it: the same seed gives
byte-identical draws and a different seed does not; the blockage draw reproduces the spread the
EnKF published rather than a spread of its own invention; every storage coefficient stays inside
`K_BOUNDS`, including when the fit put a segment on the boundary; the Sky index is a draw with
replacement over the rain members; and a segment Pulse could not resolve comes back NaN rather
than quietly carrying the prior.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

import numpy as np
import pytest
from varuna_flash.model import K_BOUNDS, K_LOG_SD, FlashModel, draw_members, simulate

SEGMENTS = ("A-Hindmata", "B-Sion-Circle")
N_STEPS = 12
RAIN_MM_H = 10.0
"""Steady rain: the peak is then the last step and no timing subtlety enters a comparison."""

BASELINE_CM = np.array([[1.0, 4.0]]) * np.linspace(0.5, 2.0, N_STEPS)[:, None]
"""A base state that differs per segment and per step, so "reproduces the baseline" is a real
claim and not one a zero array would pass by accident."""


def _model() -> FlashModel:
    """Two segments, each with a pipe that can be desilted.

    ``k = 1`` step makes the cascade deliver within two steps, so a twelve-step run ponds
    visibly; the drainage term is smaller than the inflow, so the street fills at every blockage
    and lowering beta changes a peak rather than the shape of a dry series. The skill numbers
    are the measured ones from `docs/verification/flash_lite.json`, so nothing here implies a
    better emulator than the one that ships.
    """
    return FlashModel(
        segment_ids=SEGMENTS,
        k_steps=np.ones(2),
        gain=np.ones(2),
        drain_cm_per_step=np.array([4.0, 6.0]),
        beta_ref=np.full(2, 0.5),
        baseline_cm=BASELINE_CM.copy(),
        n_training_runs=6,
        rmse_cm=5.7,
        csi_30cm=0.085,
        fitted_segments=2,
    )


def _rain(scale: float = 1.0) -> np.ndarray:
    return np.full(N_STEPS, RAIN_MM_H * scale)


def test_zero_rain_reproduces_the_base_state_exactly() -> None:
    """Not "close to" the baseline - equal to it, because the cascade contributes nothing."""
    model = _model()

    depth = simulate(model, np.zeros(N_STEPS))

    np.testing.assert_array_equal(depth, BASELINE_CM)


def test_peak_is_monotone_non_decreasing_in_the_rain_scale() -> None:
    """More rain is never less water. The what-if rain slider depends on this holding."""
    model = _model()

    peaks = [simulate(model, _rain(scale)).max(axis=0) for scale in (0.0, 0.5, 1.0, 1.5, 2.0)]

    for quieter, wetter in pairwise(peaks):
        assert np.all(wetter >= quieter)
    # And it actually moves, so a model that ignored the rain would not pass this.
    assert np.all(peaks[-1] > peaks[0])


def test_cleaning_a_pipe_never_raises_its_own_street() -> None:
    """Capacity rises as ``(1 - beta)^(5/3)``, so a lower beta drains more, never less."""
    model = _model()
    cleaned = model.beta_ref.copy()
    cleaned[0] = 0.05

    before = simulate(model, _rain(), beta=model.beta_ref).max(axis=0)
    after = simulate(model, _rain(), beta=cleaned).max(axis=0)

    assert after[0] <= before[0]
    # Desilting a pipe that is carrying water has to show, or the lever is inert.
    assert after[0] < before[0]


def test_cleaning_a_pipe_moves_no_other_street_at_all() -> None:
    """Zero to the bit, on the whole series - every term in the recursion is indexed by
    segment and nothing crosses between them. This is the property that makes attribution over
    a neighbour's pipe unanswerable on Flash-lite rather than merely weak (ADR-0042)."""
    model = _model()
    cleaned = model.beta_ref.copy()
    cleaned[0] = 0.05

    before = simulate(model, _rain(), beta=model.beta_ref)
    after = simulate(model, _rain(), beta=cleaned)

    np.testing.assert_array_equal(after[:, 1], before[:, 1])


def test_depth_is_returned_as_steps_by_segments_for_both_rain_shapes() -> None:
    """An AOI hyetograph broadcasts across segments; a per-segment field is used as given."""
    model = _model()

    aoi_mean = simulate(model, _rain())
    per_segment = simulate(model, np.tile(_rain()[:, None], (1, model.n_segments)))

    assert aoi_mean.shape == (N_STEPS, model.n_segments)
    assert per_segment.shape == (N_STEPS, model.n_segments)
    # A uniform per-segment field is the same storm as the AOI mean, so the two agree.
    np.testing.assert_allclose(per_segment, aoi_mean)


# --- draw_members: the ensemble's parameters (SPEC.md 11.7, task P7.6) -------------------

POSTERIOR_MEAN = np.array([0.30, 0.55])
POSTERIOR_SD = np.array([0.10, 0.04])
"""A Pulse posterior for the two segments, well away from 0 and 1 so the clip in `draw_members`
does not bite: a spread claim measured against a clipped draw would be measuring the clip."""

SPREAD_MEMBERS = 4000
"""Members to draw when the assertion is about a *distribution*.

The sample standard deviation of ``n`` draws carries a relative error of about
``1 / sqrt(2 (n - 1))`` on its own, which at the fifty members the cycle actually runs is 10 % -
exactly the tolerance being tested, so a fifty-member check would be measuring luck. Four
thousand takes that down to 1 % and makes the 10 % a statement about the sampler."""


def test_draw_members_is_byte_identical_from_the_same_seed() -> None:
    """Rule 8: two bakes of the same cycle must produce the same files, so the member draws
    that feed the products have to be reproducible to the bit, not merely to a tolerance."""
    model = _model()

    first = draw_members(model, POSTERIOR_MEAN, POSTERIOR_SD, seed=2019)
    second = draw_members(model, POSTERIOR_MEAN, POSTERIOR_SD, seed=2019)

    for one, other in zip(first, second, strict=True):
        np.testing.assert_array_equal(one, other)


def test_draw_members_differs_under_a_different_seed() -> None:
    """And the seed has to be doing something - a generator that ignored it would pass the
    reproducibility test above perfectly."""
    model = _model()

    _, beta, k = draw_members(model, POSTERIOR_MEAN, POSTERIOR_SD, seed=2019)
    _, other_beta, other_k = draw_members(model, POSTERIOR_MEAN, POSTERIOR_SD, seed=7)

    assert not np.array_equal(beta, other_beta)
    assert not np.array_equal(k, other_k)


def test_drawn_blockage_reproduces_the_posterior_spread() -> None:
    """The ensemble's disagreement about a pipe is the EnKF's disagreement about it.

    This is the rule 6 property of the whole tranche: a band on screen is only honest if it was
    measured across members whose spread came from somewhere real. Widening the draw here would
    widen every probability the console shows, for free and for nothing.
    """
    model = _model()

    _, beta, _ = draw_members(
        model, POSTERIOR_MEAN, POSTERIOR_SD, n_members=SPREAD_MEMBERS, seed=2019
    )

    np.testing.assert_allclose(beta.std(axis=0, ddof=1), POSTERIOR_SD, rtol=0.10)
    np.testing.assert_allclose(beta.mean(axis=0), POSTERIOR_MEAN, atol=0.01)


def test_every_drawn_k_stays_inside_the_bounds() -> None:
    """Including when the fit put a segment on the boundary, which is where the clip has to
    work: ``k`` is used as ``1 / k`` in the cascade, so a draw at or below zero is not a bad
    forecast but a divide, and the upper bound is what stops a member from never responding."""
    model = replace(_model(), k_steps=np.array([K_BOUNDS[0], K_BOUNDS[1]]))

    _, _, k = draw_members(model, POSTERIOR_MEAN, POSTERIOR_SD, n_members=SPREAD_MEMBERS, seed=2019)

    assert np.all(k >= K_BOUNDS[0])
    assert np.all(k <= K_BOUNDS[1])
    # And the clip is load-bearing rather than decorative: from a boundary value, half the
    # lognormal draws fall outside and are pulled back.
    assert np.any(k == K_BOUNDS[0])
    assert np.any(k == K_BOUNDS[1])


def test_drawn_k_carries_the_documented_structural_spread() -> None:
    """Lognormal about the fitted value: the median member keeps the fit, and the log-space
    spread is the `K_LOG_SD` the module derives from its own fitting grid."""
    model = _model()

    _, _, k = draw_members(model, POSTERIOR_MEAN, POSTERIOR_SD, n_members=SPREAD_MEMBERS, seed=2019)

    log_ratio = np.log(k / model.k_steps[None, :])
    np.testing.assert_allclose(log_ratio.std(axis=0, ddof=1), K_LOG_SD, rtol=0.10)
    np.testing.assert_allclose(log_ratio.mean(axis=0), 0.0, atol=0.02)


def test_draw_assigns_sky_members_with_replacement() -> None:
    """Fifty members over twenty rain members cannot be a permutation (11.7), so the index is a
    draw: in range, one per member, and repeating."""
    model = _model()

    sky_index, beta, k = draw_members(model, POSTERIOR_MEAN, POSTERIOR_SD, n_members=50, n_sky=20)

    assert sky_index.shape == (50,)
    assert beta.shape == (50, model.n_segments)
    assert k.shape == (50, model.n_segments)
    assert sky_index.min() >= 0
    assert sky_index.max() < 20
    assert len(set(sky_index.tolist())) < 50


def test_draw_leaves_an_unresolved_segment_unresolved() -> None:
    """`varuna_pulse.join.segment_beta` returns NaN for a segment with no inlet link, and that
    NaN has to survive: substituting the city prior here would turn "we do not know this street's
    drain" into a number the map would draw as a measurement (rule 6)."""
    model = _model()
    mean = POSTERIOR_MEAN.copy()
    mean[0] = np.nan

    _, beta, _ = draw_members(model, mean, POSTERIOR_SD)

    assert np.all(np.isnan(beta[:, 0]))
    assert np.all(np.isfinite(beta[:, 1]))


def test_draw_members_refuses_edge_keyed_blockage() -> None:
    """Pulse publishes blockage per drain edge and this function wants it per road segment; the
    two id spaces share no values, so the only way a caller finds out is the shape."""
    model = _model()

    with pytest.raises(ValueError, match="segment_beta"):
        draw_members(model, np.full(7, 0.3), np.full(7, 0.1))


def test_simulate_runs_a_drawn_k() -> None:
    """The third draw has to reach the recursion, or the structural spread is inert: two members
    differing only in ``k`` must produce different depths."""
    model = _model()
    _, _, k = draw_members(model, POSTERIOR_MEAN, POSTERIOR_SD, n_members=2, seed=2019)

    fitted = simulate(model, _rain())
    member = simulate(model, _rain(), k_steps=k[0])

    assert not np.array_equal(member, fitted)
    np.testing.assert_array_equal(simulate(model, _rain(), k_steps=model.k_steps), fitted)

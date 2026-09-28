"""The 50-member street ensemble (task P7.6, SPEC.md 11.7).

These tests pin the two decisions the module's docstring is about - the exhaustive Sky
allocation and the counted blockage substitution - and the one property rule 8 requires of
anything a bake writes. The *size* of each axis's contribution is measured per cycle into
:class:`~varuna_flash.ensemble.SpreadReport` rather than asserted here: on a synthetic emulator
the numbers would say nothing about Mumbai, and the module docstring carries the Mumbai ones.
"""

from __future__ import annotations

import numpy as np
import pytest
from varuna_flash.ensemble import (
    DEFAULT_MEMBERS,
    PRIOR_BETA,
    build_members,
    spread_note,
)
from varuna_flash.model import FlashModel

N_SEGMENTS = 24
N_SKY = 20
N_STEPS = 12


def _model(seed: int = 3) -> FlashModel:
    """A small fitted-looking emulator with segments that actually pond.

    ``gain`` is generous and ``drain_cm_per_step`` small so most segments pass the 5 cm wet
    threshold the band is averaged over; a model whose streets stay dry would give every band a
    zero and the tests would pass without measuring anything.
    """
    rng = np.random.default_rng(seed)
    return FlashModel(
        segment_ids=tuple(f"S{i:04d}-000" for i in range(N_SEGMENTS)),
        k_steps=rng.uniform(1.5, 6.0, N_SEGMENTS),
        gain=rng.uniform(0.8, 2.0, N_SEGMENTS),
        drain_cm_per_step=rng.uniform(0.2, 1.2, N_SEGMENTS),
        beta_ref=np.zeros(N_SEGMENTS),
        baseline_cm=np.zeros((N_STEPS, N_SEGMENTS)),
        n_training_runs=8,
        rmse_cm=5.7,
        csi_30cm=0.085,
        fitted_segments=N_SEGMENTS,
    )


def _hyetographs(seed: int = 11) -> np.ndarray:
    """Twenty member hyetographs that differ in amplitude, as a real cycle's do."""
    rng = np.random.default_rng(seed)
    shape = np.exp(-(((np.arange(N_STEPS) - 4.0) / 3.0) ** 2))
    return shape[None, :] * rng.uniform(20.0, 80.0, (N_SKY, 1))


def _beta(unresolved: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(5)
    mean = rng.uniform(0.15, 0.5, N_SEGMENTS)
    sd = rng.uniform(0.05, 0.2, N_SEGMENTS)
    if unresolved:
        mean[:unresolved] = np.nan
        sd[:unresolved] = np.nan
    return mean, sd


def test_every_sky_member_is_used_and_none_is_used_twice_more_than_another() -> None:
    """The allocation is exhaustive, not sampled.

    Drawing the weather with replacement is what SPEC.md 11.7 words, and it cost 29.4 % of
    the reported band between seeds on the 2 July 08:40 cycle (module docstring). This is the
    property that replaced it: every Sky member appears, and the counts differ by at most one,
    so no weather is over-represented by more than the arithmetic of 50 over 20 forces.
    """
    result = build_members(
        _model(), _hyetographs(), *_beta(), n_members=DEFAULT_MEMBERS, decompose=False
    )
    counts = np.bincount(result.sky_index, minlength=N_SKY)
    assert counts.min() >= 1, "a Sky member no ensemble member ran on"
    assert counts.max() - counts.min() <= 1
    assert counts.sum() == DEFAULT_MEMBERS


def test_a_member_count_that_divides_the_sky_members_says_nothing_about_allocation() -> None:
    """The allocation note appears only when it is true, and 60 members removes it."""
    unbalanced = build_members(
        _model(), _hyetographs(), *_beta(), n_members=50, decompose=False
    ).notes
    balanced = build_members(
        _model(), _hyetographs(), *_beta(), n_members=60, decompose=False
    ).notes
    assert any("not a multiple" in note for note in unbalanced)
    assert not any("not a multiple" in note for note in balanced)


def test_unresolved_blockage_is_substituted_and_counted_not_absorbed() -> None:
    """A NaN blockage becomes :data:`PRIOR_BETA` and the count reaches the notes (rule 6).

    ``varuna_flash.model.draw_members`` deliberately propagates the NaN and says the caller that
    substitutes must count what it substituted. This is that caller, so the count is a field and
    a sentence, and the depths are finite rather than silently NaN in the quantiles the map
    colours streets by.
    """
    result = build_members(_model(), _hyetographs(), *_beta(unresolved=7), decompose=False)
    assert result.n_flat_beta == 7
    assert result.n_resolved_beta == N_SEGMENTS - 7
    assert np.isfinite(result.depth_cm).all()
    assert any(f"flat {PRIOR_BETA}" in note for note in result.notes)


def test_the_same_seed_builds_the_same_members(_: None = None) -> None:
    """Rule 8: two bakes of one cycle produce byte-identical products."""
    args = (_model(), _hyetographs(), *_beta())
    first = build_members(*args, decompose=False)
    second = build_members(*args, decompose=False)
    assert np.array_equal(first.depth_cm, second.depth_cm)
    assert np.array_equal(first.sky_index, second.sky_index)
    # And a different seed does move it, so the equality above is a property of the seed and
    # not of an ensemble that forgot to vary.
    other = build_members(*args, seed=7, decompose=False)
    assert not np.array_equal(first.depth_cm, other.depth_cm)


def test_the_decomposition_prices_each_axis_on_this_run() -> None:
    """The report is measured, and its three axes are ordered the way the cycle's are.

    The sizes are not asserted - this is a synthetic emulator - but two things must hold of any
    honest decomposition: the weather-only band cannot exceed the full ensemble's by much
    (turning axes off cannot generally widen it), and the parameters-only band must be positive,
    because ``k`` is drawn and ``k`` moves depth.
    """
    result = build_members(_model(), _hyetographs(), *_beta(), decompose=True)
    report = result.spread
    assert report is not None
    assert report.n_members == DEFAULT_MEMBERS
    assert report.n_sky == N_SKY
    assert report.wet_segments > 0, "no wet segment to average a band over"
    assert report.band_cm > 0.0
    assert report.band_params_only_cm > 0.0
    assert report.band_sky_only_cm > 0.0
    assert report.ms >= 0
    note = spread_note(report)
    assert "Ensemble band" in note
    assert f"{report.band_cm:.2f} cm" in note
    assert note in result.notes


def test_no_decomposition_means_no_number_to_quote() -> None:
    """``decompose=False`` returns no report rather than a default one.

    A caller that skipped the measurement must be unable to print a band, instead of printing
    one that came from somewhere else.
    """
    result = build_members(_model(), _hyetographs(), *_beta(), decompose=False)
    assert result.spread is None
    assert not any("Ensemble band" in note for note in result.notes)


@pytest.mark.parametrize(
    ("hyetographs", "message"),
    [
        (np.zeros((0, N_STEPS)), "non-empty"),
        (np.zeros(N_STEPS), "non-empty"),
    ],
)
def test_a_cycle_with_no_member_rain_is_refused_by_name(hyetographs, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_members(_model(), hyetographs, *_beta())


def test_edge_keyed_blockage_is_refused_with_the_join_named() -> None:
    """The mistake this catches is passing Pulse's per-edge arrays straight in."""
    with pytest.raises(ValueError, match="segment_beta"):
        build_members(_model(), _hyetographs(), np.full(999, 0.2), np.full(999, 0.1))


def test_the_stack_is_shaped_for_the_products_and_truncates_to_the_twin_horizon() -> None:
    """``varuna_products.depth.segment_forecast`` wants (members, steps, segments) in cm."""
    result = build_members(_model(), _hyetographs(), *_beta(), n_steps=5, decompose=False)
    assert result.depth_cm.shape == (DEFAULT_MEMBERS, 5, N_SEGMENTS)
    assert result.depth_cm.dtype == np.float32
    assert result.n_members == DEFAULT_MEMBERS
    assert result.n_steps == 5

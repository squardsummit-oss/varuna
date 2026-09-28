"""Unit tests for the drain-sizing physics (task P1.8, SPEC.md 10.1 step 7, Appendix A)."""

from __future__ import annotations

import math

import pytest
from varuna_city.sizing import (
    BETA_PRIOR_MEAN,
    MANNING_N_CONCRETE,
    STANDARD_DIAMETERS_M,
    beta_prior,
    beta_sd,
    box_drain_dims,
    box_q_full,
    circular_q_full,
    kappa_prior,
    land_use_class,
    manning_diameter,
    rational_method_q,
    runoff_coefficient,
    size_conduit,
    snap_diameter,
)


def test_runoff_coefficient_spans_pervious_to_sealed() -> None:
    assert runoff_coefficient(0.0) == pytest.approx(0.15)
    assert runoff_coefficient(1.0) == pytest.approx(0.90)
    assert runoff_coefficient(0.5) == pytest.approx(0.525)
    # out-of-range input is clamped, not trusted
    assert runoff_coefficient(-3.0) == pytest.approx(0.15)
    assert runoff_coefficient(9.0) == pytest.approx(0.90)


def test_rational_method_is_c_times_i_times_a_in_si() -> None:
    # 1 ha fully sealed under 50 mm/h: Q = 0.9 * (50/3.6e6) m/s * 10000 m2
    q = rational_method_q(area_m2=10_000.0, c=0.9, intensity_mm_h=50.0)
    assert q == pytest.approx(0.9 * 50.0 / 3.6e6 * 10_000.0)
    assert q == pytest.approx(0.125, rel=1e-3)
    assert rational_method_q(0.0, 0.9, 50.0) == 0.0


def test_manning_diameter_inverts_the_full_flow_capacity() -> None:
    """The diameter we solve for must carry exactly the flow we asked for."""
    for q in (0.05, 0.4, 2.0, 12.0):
        d = manning_diameter(q, slope=0.004, n=MANNING_N_CONCRETE)
        assert circular_q_full(d, 0.004, MANNING_N_CONCRETE) == pytest.approx(q, rel=1e-9)


def test_steeper_pipes_are_smaller() -> None:
    gentle = manning_diameter(1.0, slope=0.003)
    steep = manning_diameter(1.0, slope=0.03)
    assert steep < gentle


def test_snap_diameter_rounds_up_to_a_standard_size() -> None:
    assert snap_diameter(0.01) == 0.45
    assert snap_diameter(0.45) == 0.45
    assert snap_diameter(0.451) == 0.60
    assert snap_diameter(1.21) == 1.50
    assert snap_diameter(1.9) is None  # too big for a pipe: it becomes a box drain


def test_a_larger_contributing_area_needs_a_larger_conduit() -> None:
    """The jury's first question about sizing: does more catchment mean a bigger pipe?

    The honest property is monotonicity, not strict growth. The ladder of standard sizes
    has a floor at 450 mm (SPEC.md 10.1 step 7), and a 450 mm pipe on this slope carries
    about 0.18 m3/s, so every catchment whose design flow fits under that lands on the same
    rung. What must hold is that the design flow is strictly ordered, that the snapped
    diameter never goes *down* as the catchment grows, and that a catchment big enough to
    exceed a rung's capacity does move up a rung.
    """
    common = {"imperviousness": 0.9, "intensity_mm_h": 25.0, "slope": 0.004, "is_trunk": False}
    small = size_conduit(area_m2=2_000.0, **common)
    medium = size_conduit(area_m2=30_000.0, **common)
    large = size_conduit(area_m2=200_000.0, **common)

    assert small.q_design_m3s < medium.q_design_m3s < large.q_design_m3s
    assert small.diameter_m is not None and medium.diameter_m is not None
    # 2,000 m2 and 30,000 m2 both fit inside the smallest standard pipe, so they share it.
    assert small.diameter_m == medium.diameter_m == STANDARD_DIAMETERS_M[0]
    assert small.q_full_m3s >= medium.q_design_m3s
    # 200,000 m2 does not fit: it must climb the ladder.
    assert large.diameter_m is not None
    assert large.diameter_m > STANDARD_DIAMETERS_M[0]
    assert large.area_m2 > medium.area_m2
    # and monotone non-decreasing across a whole sweep, once snapping is taken into account
    sweep = [size_conduit(area_m2=a, **common) for a in (1e3, 1e4, 1e5, 1e6, 5e6)]
    assert [s.area_m2 for s in sweep] == sorted(s.area_m2 for s in sweep)
    diameters = [s.diameter_m for s in sweep if s.diameter_m is not None]
    assert diameters == sorted(diameters)


def test_every_sized_pipe_actually_carries_its_design_flow() -> None:
    common = {"imperviousness": 0.8, "intensity_mm_h": 25.0, "slope": 0.005, "is_trunk": False}
    for area in (5e3, 5e4, 5e5, 5e6):
        size = size_conduit(area_m2=area, **common)
        assert size.q_full_m3s >= size.q_design_m3s - 1e-9


def test_every_rung_of_the_ladder_carries_its_design_flow() -> None:
    """The property that actually matters hydraulically, checked on every standard size.

    A dense sweep of catchments exercises all five rungs plus the box-drain overflow; on
    each one the conduit we chose must carry at least the flow the rational method asked
    for, otherwise the inferred network is undersized by construction.
    """
    common = {"imperviousness": 0.85, "intensity_mm_h": 50.0, "slope": 0.004, "is_trunk": False}
    seen: set[float | None] = set()
    for area in (1e3 * 1.5**k for k in range(30)):
        size = size_conduit(area_m2=area, **common)
        assert size.q_full_m3s >= size.q_design_m3s - 1e-9, area
        assert size.area_m2 > 0.0
        seen.add(size.diameter_m)
    # every standard diameter was reached, and beyond 1500 mm the conduit becomes a box
    assert set(STANDARD_DIAMETERS_M) <= seen
    assert None in seen


def test_trunks_are_box_drains_and_carry_their_flow() -> None:
    size = size_conduit(
        area_m2=1e6, imperviousness=0.85, intensity_mm_h=50.0, slope=0.003, is_trunk=True
    )
    assert size.shape == "box"
    assert size.diameter_m is None
    assert size.width_m is not None and size.height_m is not None
    assert size.width_m > size.height_m  # wider than deep, like a nullah box section
    assert size.q_full_m3s >= size.q_design_m3s


def test_box_drain_dims_snap_to_quarter_metre_steps() -> None:
    width, height = box_drain_dims(6.0, slope=0.003)
    assert math.isclose(width % 0.25, 0.0, abs_tol=1e-9)
    assert math.isclose(height % 0.25, 0.0, abs_tol=1e-9)
    assert height >= 0.60
    assert box_q_full(width, height, 0.003) >= 6.0


def test_land_use_class_follows_the_documented_rule() -> None:
    assert land_use_class("primary") == "arterial"
    assert land_use_class("trunk_link") == "arterial"
    assert land_use_class("residential") == "residential"
    assert land_use_class(None) == "residential"
    assert land_use_class("service", imperviousness=0.9) == "market_informal"
    # open ground is not a bazaar
    assert land_use_class("service", imperviousness=0.05) == "residential"


def test_beta_priors_are_valid_and_carry_the_documented_means() -> None:
    for land_use, mean in BETA_PRIOR_MEAN.items():
        a, b = beta_prior(land_use)
        assert a > 0 and b > 0
        assert 0.0 < a / (a + b) < 1.0
        assert a / (a + b) == pytest.approx(mean)
    assert beta_prior("market_informal")[0] / sum(beta_prior("market_informal")) == pytest.approx(
        0.35
    )
    assert beta_prior("residential")[0] / sum(beta_prior("residential")) == pytest.approx(0.20)
    assert beta_prior("arterial")[0] / sum(beta_prior("arterial")) == pytest.approx(0.15)


def test_the_blockage_prior_is_honestly_wide() -> None:
    """A prior nobody measured must not pretend to be precise: sd of about 0.15."""
    a, b = beta_prior("residential")
    assert 0.12 <= beta_sd(a, b) <= 0.20


def test_kappa_prior_mean_is_a_quarter() -> None:
    a, b = kappa_prior()
    assert a / (a + b) == pytest.approx(0.25)
    assert 0.0 < beta_sd(a, b) < 0.25


def test_standard_diameters_are_the_indian_norm_set() -> None:
    assert STANDARD_DIAMETERS_M == (0.45, 0.60, 0.90, 1.20, 1.50)

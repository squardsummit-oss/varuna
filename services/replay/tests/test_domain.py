"""The storm domain: the grid the radar and truth cubes share (SPEC.md 3.3, 10.2)."""

from __future__ import annotations

import numpy as np
import pytest
from varuna_replay.domain import StormDomain, step_times_min
from varuna_schemas.models.city import CityConfig, RadarDomain
from varuna_schemas.models.common import BBox
from varuna_schemas.paths import city_config_path

MUMBAI_DOMAIN = RadarDomain(center_lon=72.86, center_lat=19.065, size_km=60.0, res_m=500.0)


def test_the_mumbai_domain_is_sixty_kilometres_at_five_hundred_metres() -> None:
    domain = StormDomain.from_radar_domain(MUMBAI_DOMAIN, 32643)
    assert domain.n_px == 120, "120 x 120 px is what pySTEPS needs for its scale cascade"
    assert domain.shape == (120, 120)
    assert domain.size_km == 60.0
    assert domain.crs_string == "EPSG:32643"
    left, bottom, right, top = domain.bounds
    assert right - left == pytest.approx(60_000.0)
    assert top - bottom == pytest.approx(60_000.0)


def test_the_grid_is_north_up_and_snapped_to_whole_pixels() -> None:
    domain = StormDomain.from_radar_domain(MUMBAI_DOMAIN, 32643)
    a, b, c, d, e, f = domain.transform
    assert (a, b, d, e) == (500.0, 0.0, 0.0, -500.0), "row 0 is the northern row"
    assert c % 500.0 == 0.0 and f % 500.0 == 0.0, "snapping keeps two runs identical"
    ys = domain.y_coords()
    assert ys[0] > ys[-1], "y decreases down the array"
    assert domain.x_coords()[0] == pytest.approx(domain.left + 250.0)


def test_the_domain_of_each_city_config_matches_its_computation_crs() -> None:
    for city, crs in (("mumbai", 32643), ("chennai", 32644)):
        config = CityConfig.from_yaml(city_config_path(city))
        domain = StormDomain.from_city_config(config)
        assert domain.crs == crs
        assert domain.n_px == config.radar_domain.n_px
        assert domain.res_m == config.radar_domain.res_m


def test_the_area_of_interest_mask_covers_the_bbox_and_nothing_else() -> None:
    domain = StormDomain.from_radar_domain(MUMBAI_DOMAIN, 32643)
    aoi = BBox(min_lon=72.815, min_lat=18.995, max_lon=72.905, max_lat=19.135)
    mask = domain.aoi_mask(aoi)
    assert mask.shape == domain.shape
    assert mask.any() and not mask.all()

    left, bottom, right, top = domain.project_bbox(aoi)
    x, y = domain.cell_centres()
    assert x[mask].min() >= left and x[mask].max() <= right
    assert y[mask].min() >= bottom and y[mask].max() <= top
    # the area of interest is roughly 9.5 km x 15.5 km, so about 590 pixels of 500 m
    assert 400 < int(mask.sum()) < 900


def test_the_coverage_circle_is_inscribed_in_the_domain() -> None:
    domain = StormDomain.from_radar_domain(MUMBAI_DOMAIN, 32643)
    mask = domain.coverage_mask(30.0)
    assert mask[60, 60], "the centre is always covered"
    assert not mask[0, 0], "the corners fall outside a 30 km circle"
    # a circle of radius 30 km inside a 60 km square covers about pi/4 of it
    assert float(mask.mean()) == pytest.approx(np.pi / 4, rel=0.02)


def test_step_times_are_inclusive_and_refuse_a_ragged_window() -> None:
    times = step_times_min(0.0, 60.0, 5.0)
    assert times[0] == 0.0 and times[-1] == 60.0
    assert times.size == 13
    assert np.array_equal(np.diff(times), np.full(12, 5.0))
    with pytest.raises(ValueError, match="whole number"):
        step_times_min(0.0, 47.0, 5.0)
    with pytest.raises(ValueError, match="positive"):
        step_times_min(0.0, 60.0, 0.0)
    with pytest.raises(ValueError, match="must not precede"):
        step_times_min(60.0, 0.0, 5.0)

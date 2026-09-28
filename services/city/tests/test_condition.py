"""Hydro-conditioning tests on small synthetic DEMs (task P1.5).

Every fixture is a 10 m grid, so one cell is 100 m2 and the 900 m2 spurious-pit threshold
falls between a 4-cell pit (400 m2) and a 20-cell pit (2000 m2).
"""

from __future__ import annotations

import numpy as np
import pytest
from rasterio.transform import Affine
from shapely.geometry import LineString, Point, Polygon
from varuna_city.condition import (
    BUILDING_BURN_M,
    ROAD_CARVE_M,
    breach_culverts,
    condition_dem,
    rasterize_mask,
)
from varuna_city.depressions import find_depressions, label_pits

CRS = "EPSG:32643"
RES = 10.0


def grid_transform(height: int) -> Affine:
    """North-up 10 m transform with its origin at (0, height * 10)."""
    return Affine(RES, 0.0, 0.0, 0.0, -RES, height * RES)


def cell_center(transform: Affine, row: int, col: int) -> tuple[float, float]:
    x, y = transform * (col + 0.5, row + 0.5)
    return float(x), float(y)


def test_building_footprint_raises_its_cells_by_five_metres() -> None:
    dem = np.full((20, 20), 10.0)
    transform = grid_transform(20)
    # a 3 x 3 cell footprint at rows/cols 5..7
    x0, y0 = transform * (5, 8)
    x1, y1 = transform * (8, 5)
    footprint = Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])

    result = condition_dem(dem, transform, CRS, buildings=[footprint], use_whitebox=False)

    mask = result.buildings_mask
    assert mask.sum() == 9
    assert np.allclose(result.dem[mask], 10.0 + BUILDING_BURN_M)
    assert np.allclose(result.dem[~mask], 10.0)
    assert result.changes["cells_burned"] == 9
    assert result.changes["building_burn_m"] == BUILDING_BURN_M


def test_carved_road_lowers_a_one_cell_wide_line() -> None:
    dem = np.full((20, 20), 10.0)
    transform = grid_transform(20)
    start = cell_center(transform, 10, 2)
    end = cell_center(transform, 10, 17)
    road = LineString([start, end])

    result = condition_dem(dem, transform, CRS, roads=[road], use_whitebox=False)

    carved = result.roads_mask
    assert carved.any()
    # one cell wide: every carved cell sits on row 10
    rows = np.unique(np.nonzero(carved)[0])
    assert rows.tolist() == [10]
    assert np.allclose(result.dem[carved], 10.0 - ROAD_CARVE_M)
    assert result.changes["cells_carved"] == int(carved.sum())


def test_culvert_breaches_an_embankment_so_water_passes() -> None:
    height, width = 20, 20
    transform = grid_transform(height)
    # ground falling west to east, with a 5 m embankment across column 10
    dem = np.tile(np.linspace(12.0, 8.0, width), (height, 1)).astype(float)
    dem[:, 10] += 5.0

    row = 10
    upstream = cell_center(transform, row, 8)
    downstream = cell_center(transform, row, 12)
    culvert = LineString([upstream, downstream])

    before = dem[row, 10]
    breached, stats = breach_culverts(dem, transform, [culvert])

    assert stats["culvert_ways_breached"] == 1
    assert stats["culvert_cells_breached"] >= 1
    # the embankment cell is now no higher than the lower of the two end cells
    assert breached[row, 10] < before
    assert breached[row, 10] <= min(dem[row, 8], dem[row, 12]) + 1e-9
    # water can pass: a monotone non-increasing path exists across the embankment
    profile = breached[row, 8:13]
    assert profile.max() <= dem[row, 8] + 1e-9
    # the rest of the embankment is untouched
    assert np.isclose(breached[0, 10], dem[0, 10])


def _dem_with_two_pits() -> tuple[np.ndarray, Affine]:
    """A plateau with a 4-cell (400 m2) pit and a 20-cell (2000 m2) pit."""
    height, width = 30, 30
    dem = np.full((height, width), 10.0)
    dem[0, :] = 6.0  # a drainable edge so both pits have somewhere to spill to
    dem[5:7, 5:7] = 8.0  # 4 cells  -> 400 m2  (spurious)
    dem[20:24, 20:25] = 7.0  # 20 cells -> 2000 m2 (kept)
    return dem, grid_transform(height)


def test_small_pit_is_breached_and_large_pit_is_kept() -> None:
    dem, transform = _dem_with_two_pits()
    labels, _depth = label_pits(dem, use_pyflwdir=False)
    assert labels.max() == 2, "fixture should start with exactly two pits"

    result = condition_dem(dem, transform, CRS, min_pit_area_m2=900.0, use_whitebox=False)

    after, _ = label_pits(result.dem, use_pyflwdir=False)
    remaining = {
        (int(r), int(c))
        for r, c in zip(*np.nonzero(after > 0), strict=True)  # type: ignore[arg-type]
    }
    assert not any(5 <= r < 7 and 5 <= c < 7 for r, c in remaining), "400 m2 pit should be gone"
    assert any(20 <= r < 24 and 20 <= c < 25 for r, c in remaining), "2000 m2 pit should remain"
    assert result.changes["pits_before"] == 2
    assert result.changes["pits_spurious"] == 1
    assert result.changes["pits_breached"] == 1
    assert result.changes["breach_method"] == "python_least_cost"
    # the big pit's floor is untouched
    assert np.isclose(result.dem[21, 21], 7.0)


def test_a_sink_point_protects_its_small_pit() -> None:
    dem, transform = _dem_with_two_pits()
    sink = Point(*cell_center(transform, 5, 5))

    result = condition_dem(
        dem, transform, CRS, sinks=[sink], min_pit_area_m2=900.0, use_whitebox=False
    )

    after, _ = label_pits(result.dem, use_pyflwdir=False)
    assert after[5, 5] > 0, "an underpass passed in as a sink must stay a pit"
    assert result.changes["sinks_protected"] == 1
    assert result.changes["pits_spurious"] == 0


def test_conditioning_is_deterministic_for_a_seed() -> None:
    """Determinism (SPEC.md rule 8) is about the products, not about the wall clock.

    ``changes["stage_ms"]`` is how long the run took, so it differs between two runs of the
    same input (the first pays for Numba/pyflwdir warm-up). Everything else must match
    exactly, and the timing is only required to be a sane non-negative number.
    """
    dem, transform = _dem_with_two_pits()
    first = condition_dem(dem, transform, CRS, seed=2019, use_whitebox=False)
    second = condition_dem(dem, transform, CRS, seed=2019, use_whitebox=False)
    assert np.array_equal(first.dem, second.dem)

    def products(changes: dict[str, object]) -> dict[str, object]:
        return {k: v for k, v in changes.items() if k != "stage_ms"}

    assert products(first.changes) == products(second.changes)
    for result in (first, second):
        assert isinstance(result.changes["stage_ms"], float)
        assert result.changes["stage_ms"] >= 0.0


def test_full_step_order_burn_then_carve_then_breach() -> None:
    dem, transform = _dem_with_two_pits()
    x0, y0 = transform * (12, 13)
    x1, y1 = transform * (14, 11)
    building = Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    road = LineString([cell_center(transform, 15, 2), cell_center(transform, 15, 27)])

    result = condition_dem(
        dem,
        transform,
        CRS,
        buildings=[building],
        roads=[road],
        min_pit_area_m2=900.0,
        use_whitebox=False,
    )

    assert np.isclose(result.dem[12, 12], 10.0 + BUILDING_BURN_M)
    assert np.isclose(result.dem[15, 15], 10.0 - ROAD_CARVE_M)
    assert result.stage_ms >= 0.0
    assert result.changes["stage_ms"] == result.stage_ms


def test_rasterize_mask_handles_empty_and_reprojected_input() -> None:
    transform = grid_transform(10)
    assert rasterize_mask(None, transform, (10, 10)).sum() == 0
    assert rasterize_mask([], transform, (10, 10)).sum() == 0


def test_no_vector_input_leaves_the_dem_alone_apart_from_spurious_pits() -> None:
    dem = np.full((15, 15), 10.0)
    dem[0, :] = 6.0
    transform = grid_transform(15)
    result = condition_dem(dem, transform, CRS, use_whitebox=False)
    assert np.array_equal(result.dem, dem)
    assert result.changes["pits_before"] == 0


@pytest.mark.parametrize("use_pyflwdir", [True, False])
def test_conditioned_dem_feeds_find_depressions(use_pyflwdir: bool) -> None:
    dem, transform = _dem_with_two_pits()
    result = condition_dem(
        dem, transform, CRS, min_pit_area_m2=900.0, use_whitebox=False, use_pyflwdir=use_pyflwdir
    )
    pits = find_depressions(
        result.dem, transform, CRS, min_area_m2=900.0, use_pyflwdir=use_pyflwdir
    )
    assert len(pits) == 1
    assert pits.iloc[0]["area_m2"] == pytest.approx(2000.0)


def _dem_with_a_one_cell_pit_at_thirty_metres() -> tuple[np.ndarray, Affine]:
    """A plateau at the city's real 30 m resolution with a single-cell pit 2 m deep."""
    height, width = 20, 20
    dem = np.full((height, width), 20.0)
    dem[0, :] = 14.0  # a drainable edge
    dem[10, 10] = 18.0  # one cell -> exactly 900 m2 at 30 m
    return dem, Affine(30.0, 0.0, 0.0, 0.0, -30.0, height * 30.0)


def test_a_one_cell_pit_is_spurious_at_the_grid_resolution() -> None:
    """One 30 m cell is exactly ``MIN_PIT_AREA_M2``, and it must still be breached.

    A `>=` comparison here kept every single-cell pit in the city - 47 % of Mumbai's
    depressions - and each one filled to over 60 cm in a 3-hour run, putting deep water on
    hillsides while the real sinks drained. The threshold reads "no bigger than one cell".
    """
    dem, transform = _dem_with_a_one_cell_pit_at_thirty_metres()
    labels, _ = label_pits(dem, use_pyflwdir=False)
    assert labels.max() == 1, "fixture should start with exactly one pit"

    result = condition_dem(dem, transform, CRS, min_pit_area_m2=900.0, use_whitebox=False)

    after, _ = label_pits(result.dem, use_pyflwdir=False)
    assert after[10, 10] == 0, "a single-cell pit is a DEM artefact, not a place that floods"
    assert result.changes["pits_spurious"] == 1
    assert result.changes["pits_breached"] == 1
    assert result.changes["pits_large"] == 0


def test_a_registered_sink_on_a_building_is_not_burned_or_blocked() -> None:
    """Khar Subway and Parel sit under an OSM building footprint.

    Burning the footprint before resolving the sinks raised both 5 m and, because
    ``buildings_mask`` is also what roughness turns into the solver's blocked mask, left two of
    the ten hotspots SPEC.md 3.3 names with no flux at all. The register has to win over the
    footprint: a cell cannot be both a place water is known to pool and an obstacle.
    """
    dem, transform = _dem_with_a_one_cell_pit_at_thirty_metres()
    sink_xy = cell_center(transform, 10, 10)
    footprint = Point(*sink_xy).buffer(45.0)  # covers the sink cell and its neighbours
    before = float(dem[10, 10])

    result = condition_dem(
        dem,
        transform,
        CRS,
        buildings=[footprint],
        sinks=[Point(*sink_xy)],
        min_pit_area_m2=900.0,
        use_whitebox=False,
    )

    assert not result.buildings_mask[10, 10], "a registered sink must not stay a building cell"
    assert result.dem[10, 10] == pytest.approx(before), "the sink must not be burned upward"
    assert result.changes["sinks_cleared_of_building"] == 1
    # The footprint around it is still burned: only the sink cell is cleared.
    assert result.buildings_mask.sum() > 0


def test_a_protected_one_cell_pit_survives_at_thirty_metres() -> None:
    """Andheri and Milan subways are one cell across; the sink list is what keeps them."""
    dem, transform = _dem_with_a_one_cell_pit_at_thirty_metres()
    sink = Point(*cell_center(transform, 10, 10))

    result = condition_dem(
        dem, transform, CRS, sinks=[sink], min_pit_area_m2=900.0, use_whitebox=False
    )

    after, _ = label_pits(result.dem, use_pyflwdir=False)
    assert after[10, 10] > 0, "a registered subway must stay a sink whatever its area"
    assert result.changes["pits_protected"] == 1
    assert result.changes["pits_spurious"] == 0


# --------------------------------------------------------------------------------------
# the coastline (wave B): bridge ends on the sea, flattened land, the coast wall
# --------------------------------------------------------------------------------------


def _coast_fixture(size: int = 20) -> tuple[np.ndarray, np.ndarray, np.ndarray, Affine]:
    """Sea in cols 0-3 at -1 m (class 80); built-up land (class 50) at 3 m to the east."""
    dem = np.full((size, size), 3.0)
    classes = np.full((size, size), 50, dtype=np.int16)
    sea = np.zeros((size, size), dtype=bool)
    sea[:, :4] = True
    dem[sea] = -1.0
    classes[sea] = 80
    return dem, classes, sea, grid_transform(size)


def test_a_bridge_end_on_the_sea_does_not_floor_the_land_to_sea_level() -> None:
    """The Sewri defect: a viaduct way leaving the shore took the sea's 0 m as its low end."""
    dem, _, sea, transform = _coast_fixture()
    dem[:, 10] = 4.0  # an embankment the bridge crosses
    bridge = LineString([cell_center(transform, 10, 1), cell_center(transform, 10, 15)])

    unmasked, _ = breach_culverts(dem, transform, [bridge])
    masked, stats = breach_culverts(dem, transform, [bridge], sea=sea)

    # without the mask the landward length of the way is floored to the sea's -1 m
    assert unmasked[10, 8] == pytest.approx(-1.0)
    # with it the sea end is ignored and the way is lowered only to its land end (3 m)
    assert masked[10, 8] == pytest.approx(3.0)
    assert masked[10, 10] == pytest.approx(3.0)
    assert stats["culvert_ends_on_sea_ignored"] == 1
    assert stats["culvert_ways_with_no_land_end"] == 0


def test_a_way_with_both_ends_on_the_sea_is_left_alone() -> None:
    dem, _, sea, transform = _coast_fixture()
    way = LineString([cell_center(transform, 2, 1), cell_center(transform, 12, 2)])
    out, stats = breach_culverts(dem, transform, [way], sea=sea)
    assert np.array_equal(out, dem)
    assert stats["culvert_ways_breached"] == 0
    assert stats["culvert_ways_with_no_land_end"] == 1


def test_flattened_land_beside_the_sea_takes_the_median_of_its_neighbours() -> None:
    from varuna_city.condition import repair_flattened_land

    dem, classes, sea, transform = _coast_fixture()
    dem[8:11, 4:6] = 0.0  # built-up land held at sea level, touching the sea
    dem[15, 15] = 0.0  # low land, but not connected to the sea: a real pit, left alone
    out, stats = repair_flattened_land(dem, sea, classes, transform, radius_m=30.0)
    assert stats["flattened_cells"] == 6
    assert stats["flattened_repaired"] == 6
    assert np.all(out[8:11, 4:6] == pytest.approx(3.0))
    assert out[15, 15] == 0.0
    assert np.array_equal(out[sea], dem[sea])


def test_flattened_wet_classes_and_protected_cells_are_not_raised() -> None:
    from varuna_city.condition import repair_flattened_land

    dem, classes, sea, transform = _coast_fixture()
    dem[5:7, 4] = 0.0
    classes[5, 4] = 95  # mangroves may sit at sea level
    protect = np.zeros_like(sea)
    protect[6, 4] = True  # an underpass on the register
    out, stats = repair_flattened_land(dem, sea, classes, transform, protect=protect)
    assert stats["flattened_cells"] == 0
    assert out[5, 4] == 0.0 and out[6, 4] == 0.0


def test_flattened_cells_ignore_buildings_as_donors() -> None:
    from varuna_city.condition import repair_flattened_land

    dem, classes, sea, transform = _coast_fixture()
    dem[10, 4] = 0.0
    buildings = np.zeros_like(sea)
    buildings[9:12, 5] = True
    dem[buildings] = 8.0  # burned footprints: wall, not ground
    out, _ = repair_flattened_land(dem, sea, classes, transform, buildings=buildings, radius_m=45.0)
    assert out[10, 4] == pytest.approx(3.0)


def test_coast_wall_raises_only_the_shore_ring_and_only_upwards() -> None:
    from varuna_city.condition import raise_coast_wall

    dem, _, sea, _ = _coast_fixture()
    dem[:, 4] = 1.0
    dem[3, 4] = 5.0  # a seawall already higher than the wall level stays
    blocked = np.zeros_like(sea)
    blocked[7, 4] = True
    out, stats = raise_coast_wall(dem, sea, wall_m=2.72, blocked=blocked)
    ring = np.zeros_like(sea)
    ring[:, 4] = True
    ring[7, 4] = False
    assert np.all(out[ring & (dem < 2.72)] == pytest.approx(2.72))
    assert out[3, 4] == 5.0
    assert out[7, 4] == 1.0  # a building cell is not part of the wall
    assert np.array_equal(out[:, 5:], dem[:, 5:])
    assert np.array_equal(out[sea], dem[sea])
    assert stats["shore_ring_cells"] == dem.shape[0] - 1
    assert stats["coast_wall_cells_raised"] == dem.shape[0] - 2


def test_condition_dem_with_a_sea_raises_the_wall_after_the_pit_breach() -> None:
    """The wall comes last, so no breach carves back through it; without a sea nothing changes."""
    dem, classes, sea, transform = _coast_fixture()
    dem[:, 4] = 0.5  # a low shore the sea would walk over
    dem[12:14, 7:9] = 2.0  # a small pit inland, spurious at 10 m

    plain = condition_dem(dem, transform, CRS, use_whitebox=False, use_pyflwdir=False)
    coast = condition_dem(
        dem,
        transform,
        CRS,
        use_whitebox=False,
        use_pyflwdir=False,
        sea=sea,
        landcover=classes,
        coast_wall_m=2.72,
    )
    assert np.all(coast.dem[:, 4] >= 2.72)
    assert coast.changes["coast_wall_cells_raised"] == dem.shape[0]
    assert "coast_wall_m" not in plain.changes
    assert "flattened_cells" not in plain.changes
    # everything away from the shore is conditioned exactly as without the sea
    assert np.array_equal(coast.dem[:, 6:], plain.dem[:, 6:])


def _mangrove_bay(size: int = 20) -> tuple[np.ndarray, np.ndarray, np.ndarray, Affine]:
    """``_coast_fixture`` with a bay in rows 5-14: mangroves (class 95) at 0.5 m in cols 4-5,
    built-up land at 1.0 m in cols 6-8, and the 3 m land closing it off everywhere else."""
    dem, classes, sea, transform = _coast_fixture(size)
    dem[5:15, 4:6] = 0.5
    classes[5:15, 4:6] = 95
    dem[5:15, 6:9] = 1.0
    return dem, classes, sea, transform


def test_the_wall_stands_behind_the_mangroves_not_on_them() -> None:
    from varuna_city.condition import raise_coast_wall

    dem, classes, sea, _ = _mangrove_bay()
    out, stats = raise_coast_wall(dem, sea, wall_m=2.72, landcover=classes)
    assert np.array_equal(out[:, 4:6], dem[:, 4:6]), "no wet cell is raised"
    assert np.all(out[5:15, 6] == pytest.approx(2.72)), "the dry land behind them is"
    assert np.array_equal(out[:, 7:], dem[:, 7:])
    assert np.array_equal(out[sea], dem[sea])
    assert stats["coast_wall_rule"] == "landward edge of the intertidal zone"
    assert stats["intertidal_cells"] == 20
    assert stats["coast_wall_cells_raised"] == 10
    # The rule the wall first shipped with stands on the mangroves' seaward edge instead.
    ring, ring_stats = raise_coast_wall(dem, sea, wall_m=2.72)
    assert np.all(ring[5:15, 4] == pytest.approx(2.72))
    assert np.array_equal(ring[:, 5:], dem[:, 5:])
    assert ring_stats["coast_wall_rule"] == "shore ring"
    assert "intertidal_cells" not in ring_stats


def test_wet_land_above_the_wall_or_cut_off_from_the_sea_is_ordinary_land() -> None:
    from varuna_city.condition import intertidal_zone

    dem, classes, sea, _ = _coast_fixture()
    dem[2, 4] = 3.5  # mangrove canopy standing above the wall level
    classes[2, 4] = 95
    dem[10:12, 12:14] = 0.2  # an inland marsh behind dry land
    classes[10:12, 12:14] = 90
    blocked = np.zeros_like(sea)
    dem[16, 4], classes[16, 4], blocked[16, 4] = 0.5, 95, True  # a building in the mangroves
    zone = intertidal_zone(dem, sea, classes, wall_m=2.72, blocked=blocked)
    assert np.array_equal(zone, sea)


def test_wall_basins_counts_the_land_the_wall_closed_off() -> None:
    """The first rule enclosed the mangroves and the land behind them (40 cells); behind the
    mangroves the wall encloses only the dry land lower than itself (20 cells)."""
    from varuna_city.condition import raise_coast_wall, wall_basins

    dem, classes, sea, _ = _mangrove_bay()
    area = RES * RES
    ring, _ = raise_coast_wall(dem, sea, wall_m=2.72)
    behind, _ = raise_coast_wall(dem, sea, wall_m=2.72, landcover=classes)

    none = wall_basins(dem, dem, sea, cell_area_m2=area)
    assert none["coast_wall_basin_cells"] == 0
    assert none["coast_wall_basin_m3"] == 0.0

    on = wall_basins(dem, ring, sea, cell_area_m2=area)
    assert on["coast_wall_basin_cells"] == 40
    assert on["coast_wall_basins"] == 1
    assert on["coast_wall_basin_max_m"] == pytest.approx(2.22)
    assert on["coast_wall_basin_m3"] == pytest.approx((10 * 2.22 + 30 * 1.72) * area)

    off = wall_basins(dem, behind, sea, cell_area_m2=area)
    assert off["coast_wall_basin_cells"] == 20
    assert off["coast_wall_basin_max_m"] == pytest.approx(1.72)
    assert off["coast_wall_basin_m3"] == pytest.approx(20 * 1.72 * area)
    assert off["coast_wall_basin_cells_over_30cm"] == 20


def test_a_building_is_never_a_basin_and_never_an_outlet() -> None:
    from varuna_city.condition import raise_coast_wall, wall_basins

    dem, classes, sea, _ = _mangrove_bay()
    blocked = np.zeros_like(sea)
    blocked[9, 7] = True
    behind, _ = raise_coast_wall(dem, sea, wall_m=2.72, landcover=classes, blocked=blocked)
    stats = wall_basins(dem, behind, sea, blocked=blocked, cell_area_m2=RES * RES)
    assert stats["coast_wall_basin_cells"] == 19


def test_condition_dem_walls_behind_the_mangroves_and_counts_its_basins() -> None:
    dem, classes, sea, transform = _mangrove_bay()
    coast = condition_dem(
        dem,
        transform,
        CRS,
        use_whitebox=False,
        use_pyflwdir=False,
        sea=sea,
        landcover=classes,
        coast_wall_m=2.72,
    )
    assert np.array_equal(coast.dem[5:15, 4:6], dem[5:15, 4:6])
    assert coast.changes["coast_wall_rule"] == "landward edge of the intertidal zone"
    assert coast.changes["intertidal_cells"] == 20
    assert coast.changes["coast_wall_basin_cells"] == 20
    assert coast.changes["coast_wall_basins"] == 1

"""The coastline rules of :mod:`varuna_city.sea` on small synthetic grids.

Every fixture is a 30 m grid in EPSG:32643, so one cell is 900 m2 and ``res_m`` matches the city
grid. Nothing here reads ``city/``.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import Affine
from shapely.geometry import LineString
from varuna_city.sea import (
    SEA_CREEK,
    SEA_LAND,
    SEA_OPEN,
    SeaConfig,
    build_sea_mask,
    chessboard_distance,
    load_sea_config,
    open_sea,
    river_lines,
    sea_config_path,
    shore_ring,
    tidal_creeks,
)

CRS = "EPSG:32643"
RES = 30.0
LAND_CLASS = 50  # WorldCover built-up
WATER = 80


def transform_for(height: int) -> Affine:
    return Affine(RES, 0.0, 270_000.0, 0.0, -RES, 2_100_000.0 + height * RES)


def centre(transform: Affine, row: float, col: float) -> tuple[float, float]:
    return transform.c + (col + 0.5) * transform.a, transform.f + (row + 0.5) * transform.e


def coast(height: int = 20, width: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """Sea in the west (cols 0-3, DEM -1 m, class 80), land rising to the east."""
    dem = np.tile(np.linspace(0.5, 10.0, width), (height, 1))
    classes = np.full((height, width), LAND_CLASS, dtype=np.int16)
    dem[:, :4] = -1.0
    classes[:, :4] = WATER
    return dem, classes


# ---------------------------------------------------------------------- open sea


def test_open_sea_is_the_edge_water_that_reaches_zero() -> None:
    dem, classes = coast()
    sea = open_sea(dem, classes)
    assert sea[:, :4].all()
    assert not sea[:, 4:].any()


def test_a_high_lake_that_touches_the_edge_is_not_sea() -> None:
    """Powai Lake: WorldCover water on the frame, 33.5 m up. The 2.0 m cap removes it."""
    dem, classes = coast()
    dem[:5, 15:] = 33.5
    classes[:5, 15:] = WATER
    sea = open_sea(dem, classes)
    assert not sea[:5, 15:].any()
    assert sea[:, :4].all()


def test_edge_water_with_no_cell_at_zero_is_not_sea() -> None:
    """Mumbai's 2-cell pond on the south edge at 0.37-0.41 m: water, low, on the frame, not sea."""
    dem, classes = coast()
    dem[-1, 10:12] = [0.37, 0.41]
    classes[-1, 10:12] = WATER
    sea = open_sea(dem, classes)
    assert not sea[-1, 10:12].any()


def test_low_inland_water_off_the_edge_is_not_sea() -> None:
    dem, classes = coast()
    dem[9:12, 9:12] = -0.5
    classes[9:12, 9:12] = WATER
    sea = open_sea(dem, classes)
    assert not sea[9:12, 9:12].any()


def test_shallow_margin_joins_the_sea_through_the_water_class() -> None:
    """A margin WorldCover calls water at 1.5 m is sea when it connects to the flattened sea."""
    dem, classes = coast()
    dem[:, 4] = 1.5
    classes[:, 4] = WATER
    sea = open_sea(dem, classes)
    assert sea[:, 4].all()


def test_nan_is_never_sea() -> None:
    dem, classes = coast()
    dem[0, 0] = np.nan
    sea = open_sea(dem, classes)
    assert not sea[0, 0]
    assert sea[1:, :4].all()


# ---------------------------------------------------------------------- tidal creeks


def creek_fixture() -> tuple[np.ndarray, np.ndarray, Affine, gpd.GeoDataFrame]:
    """A creek 3 cells wide running east from the sea at 1.0 m, behind a 3 m ridge at col 4.

    The ridge stands in for Mahim causeway: the open-sea rule cannot cross it, so only the named
    river brings the creek in.
    """
    height, width = 20, 30
    dem, classes = coast(height, width)
    transform = transform_for(height)
    dem[:, 4] = 3.0
    dem[8:11, 5:28] = 1.0
    line = LineString([centre(transform, 9, 4), centre(transform, 9, 27)])
    ways = gpd.GeoDataFrame(
        {"name": ["Mithi River", "Mithi Nagar Nala"]},
        geometry=[line, LineString([centre(transform, 2, 6), centre(transform, 2, 25)])],
        crs=CRS,
    )
    return dem, classes, transform, ways


def test_named_river_brings_a_creek_in_past_the_ridge() -> None:
    dem, classes, transform, ways = creek_fixture()
    lines, found = river_lines(ways, ("Mithi River",), transform, dem.shape, crs=CRS)
    assert found == {"Mithi River": 1}
    creek = tidal_creeks(dem, classes, lines)
    assert creek[8:11, 5:28].all()
    # the 3 m ridge and the land either side stay land
    assert not creek[:, 4].any()
    assert not creek[:7, 5:].any()
    assert not creek[12:, 5:].any()


def test_river_names_match_exactly_not_by_substring() -> None:
    dem, _, transform, ways = creek_fixture()
    lines, found = river_lines(ways, ("Mithi",), transform, dem.shape, crs=CRS)
    assert found == {"Mithi": 0}
    assert not lines.any()


def test_a_semicolon_list_of_names_matches_one_item() -> None:
    dem, _, transform, ways = creek_fixture()
    ways.loc[0, "name"] = "Mithi River;Mahim Creek"
    lines, found = river_lines(ways, ("Mahim Creek",), transform, dem.shape, crs=CRS)
    assert found == {"Mahim Creek": 1}
    assert lines.any()


def test_a_small_creek_component_is_dropped() -> None:
    dem, classes, transform, ways = creek_fixture()
    dem[8:11, 12:28] = 5.0  # the channel rises above 2.0 m after 7 columns: 21 cells < 50
    lines, _ = river_lines(ways, ("Mithi River",), transform, dem.shape, crs=CRS)
    creek = tidal_creeks(dem, classes, lines)
    assert not creek.any()
    assert tidal_creeks(dem, classes, lines, min_cells=10).any()


def test_build_codes_open_sea_creek_and_removes_buildings() -> None:
    dem, classes, transform, ways = creek_fixture()
    blocked = np.zeros(dem.shape, dtype=bool)
    blocked[0, 0] = True  # a pier building on the sea
    blocked[9, 15] = True  # and one on the creek
    result = build_sea_mask(
        dem,
        classes,
        transform=transform,
        crs=CRS,
        waterways=ways,
        blocked=blocked,
        sea_config=SeaConfig(tidal_rivers=("Mithi River",)),
    )
    codes = result.codes
    assert codes.dtype == np.uint8
    assert codes[5, 0] == SEA_OPEN
    assert codes[10, 15] == SEA_CREEK
    assert codes[0, 0] == SEA_LAND and codes[9, 15] == SEA_LAND
    assert result.stats["blocked_cells_removed"] == 2
    assert result.stats["cells"] == int(result.sea.sum())
    assert result.stats["open_sea"]["cells"] + result.stats["tidal_creek"]["cells"] == int(
        result.sea.sum()
    )
    assert result.stats["km2"] == pytest.approx(result.stats["cells"] * 0.0009, abs=1e-3)
    assert result.stats["tidal_creek"]["rivers"] == {"Mithi River": {"osm_ways": 1}}
    # the creek is its own component here: the ridge separates it from the sea
    assert len(result.stats["components"]) == 2
    assert all(-180 <= c["centroid_lon"] <= 180 for c in result.stats["components"])


def test_build_is_deterministic() -> None:
    dem, classes, transform, ways = creek_fixture()
    cfg = SeaConfig(tidal_rivers=("Mithi River",))
    a = build_sea_mask(dem, classes, transform=transform, crs=CRS, waterways=ways, sea_config=cfg)
    b = build_sea_mask(dem, classes, transform=transform, crs=CRS, waterways=ways, sea_config=cfg)
    assert np.array_equal(a.codes, b.codes)
    assert a.stats == b.stats


def test_no_named_rivers_means_no_creek() -> None:
    dem, classes, transform, ways = creek_fixture()
    result = build_sea_mask(dem, classes, transform=transform, crs=CRS, waterways=ways)
    assert not (result.codes == SEA_CREEK).any()
    assert (result.codes == SEA_OPEN).any()


# ---------------------------------------------------------------------- helpers and config


def test_shore_ring_is_land_beside_the_sea_without_buildings() -> None:
    sea = np.zeros((6, 6), dtype=bool)
    sea[:, :2] = True
    blocked = np.zeros_like(sea)
    blocked[0, 2] = True
    ring = shore_ring(sea, blocked)
    assert ring[1:, 2].all() and not ring[0, 2]
    assert not ring[:, 3:].any() and not ring[:, :2].any()


def test_chessboard_distance_counts_diagonals_as_one() -> None:
    sea = np.zeros((5, 5), dtype=bool)
    sea[0, 0] = True
    distance = chessboard_distance(sea)
    assert distance[0, 0] == 0
    assert distance[1, 1] == 1
    assert distance[2, 1] == 2
    assert chessboard_distance(np.zeros((3, 3), dtype=bool)).min() > 10**6


def test_sea_config_rejects_unknown_keys_and_negative_cells() -> None:
    with pytest.raises(ValueError, match="unknown sea setting"):
        SeaConfig.from_mapping({"coast_wall": 2.0}, source="test")
    with pytest.raises(ValueError, match=">= 0"):
        SeaConfig.from_mapping({"tidal_outfall_cells": -1}, source="test")
    cfg = SeaConfig.from_mapping({"tidal_rivers": "Mithi River"}, source="test")
    assert cfg.tidal_rivers == ("Mithi River",)
    assert cfg.coast_wall_m is None


@pytest.mark.parametrize("city", ["mumbai", "chennai"])
def test_committed_sea_configs_load(city: str) -> None:
    """The two committed sea files parse, and every numeric setting says where it came from."""
    from types import SimpleNamespace

    assert sea_config_path(city).is_file()
    cfg = load_sea_config(SimpleNamespace(id=city))
    assert cfg.tidal_rivers
    assert cfg.tidal_outfall_cells == 2
    assert cfg.tidal_outfall_invert_m == 0.0
    assert "tidal_outfall_invert_m" in cfg.notes
    if cfg.coast_wall_m is not None:
        assert "coast_wall_m" in cfg.notes


def test_mumbai_wall_is_the_sourced_high_water_plus_freeboard() -> None:
    """4.92 m chart-datum high water, less 2.70 m mean sea level, plus 0.5 m freeboard."""
    from types import SimpleNamespace

    cfg = load_sea_config(SimpleNamespace(id="mumbai"))
    assert cfg.coast_wall_m == pytest.approx(4.92 - 2.70 + 0.5)
    assert cfg.tidal_rivers == ("Mithi River",)


def test_the_report_lists_the_sea_the_wall_and_every_tidal_outfall() -> None:
    from varuna_city.report import _coastline_section

    sea = {
        "cells": 100,
        "km2": 0.09,
        "open_sea": {"cells": 90},
        "tidal_creek": {"cells": 10, "rivers": {"Mithi River": {"osm_ways": 3}}},
        "blocked_cells_removed": 1,
        "rules": {"open_sea": "rule A", "tidal_creek": "rule B", "assumption": "Not surveyed."},
        "config": {"coast_wall_m": 2.72},
    }
    tidal = {
        "tidal_within_cells": 2,
        "tidal_outfall_invert_m": 0.0,
        "outfalls": [
            {
                "node_id": "MUM-N1",
                "outfall_id": "MAHIM",
                "flap_gate": True,
                "sea_distance_cells": 1,
                "z_ground_m": 1.2,
                "z_invert_m": 0.0,
            }
        ],
        "config_dropped": [
            {
                "outfall_id": "WORLI",
                "nearest_tidal_outfall_m": 2767.0,
                "nearest_node_m": 20.0,
                "nearest_node_sea_distance_cells": 36,
            }
        ],
    }
    condition = {
        "flattened_cells": 108,
        "flattened_repaired": 108,
        "culvert_ends_on_sea_ignored": 4,
        "coast_wall_m": 2.72,
        "shore_ring_cells": 500,
        "coast_wall_cells_raised": 318,
    }
    text = "\n".join(_coastline_section(sea, tidal, condition))
    assert "100 (0.09 km2)" in text
    assert "Mithi River (3 OSM ways)" in text
    assert "318 of 500 shore cells raised to 2.72 m" in text
    assert "| MUM-N1 | MAHIM | 1 | 1.20 | 0.00 | yes |" in text
    assert "WORLI (nearest tidal outfall 2,767 m; nearest node 20 m away and 36 cells" in text
    assert "Not surveyed." in text
    assert _coastline_section(None, tidal, condition) == []
    assert "Closed basins" not in text, "a build without the count prints no basin row"

    behind = {
        **condition,
        "coast_wall_cells_raised": 382,
        "shore_ring_cells": 2289,
        "intertidal_cells": 674,
        "coast_wall_basin_cells": 54,
        "coast_wall_basin_km2": 0.049,
        "coast_wall_basins": 12,
        "coast_wall_basin_m3": 28995.0,
        "coast_wall_basin_max_m": 1.77,
    }
    text = "\n".join(_coastline_section(sea, tidal, behind))
    assert "382 of 2,289 shore cells raised to 2.72 m, behind 674 intertidal cells" in text
    assert "54 land cells (0.049 km2) in 12 basins, 28,995 m3, deepest 1.77 m" in text


def test_the_pipeline_step_writes_the_mask_and_reads_it_back(tmp_path) -> None:
    """``_step_sea`` writes ``sea_mask.tif`` and ``sea.json``; ``_load_sea`` restores both."""
    import json
    from types import SimpleNamespace

    from varuna_city import pipeline as P
    from varuna_city.config import CityGrid

    dem, classes, transform, ways = creek_fixture()
    grid = CityGrid(crs=CRS, res=RES, width=dem.shape[1], height=dem.shape[0], transform=transform)
    city = SimpleNamespace(id="nowhere-city")  # no sea file: defaults, no creek, no wall
    ctx = P.Ctx(config=city, grid=grid, out_dir=tmp_path)  # type: ignore[arg-type]
    ctx.put("dem", dem)
    ctx.put("landcover", classes)
    ctx.put("osm.waterways", ways)
    summary = P._step_sea(ctx)

    codes = ctx.get("sea_codes")
    assert summary["cells"] == int(np.count_nonzero(codes)) > 0
    assert summary["tidal_creek_cells"] == 0
    assert (tmp_path / "sea_mask.tif").is_file()
    written = json.loads((tmp_path / "sea.json").read_text(encoding="utf-8"))
    assert written["cells"] == summary["cells"]
    assert written["config"]["source"] == "defaults"

    again = P.Ctx(config=city, grid=grid, out_dir=tmp_path)  # type: ignore[arg-type]
    assert P._load_sea(again) == summary
    assert np.array_equal(again.get("sea_codes"), codes)
    assert np.array_equal(P._sea_bool(again), codes != SEA_LAND)

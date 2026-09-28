"""P1.7 — surface units: D8 watersheds inside the 0.5-2 ha cap, and the hexagon fallback."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import Affine
from shapely.geometry import Point
from shapely.ops import unary_union
from varuna_city.units import (
    MAX_UNIT_AREA_M2,
    MIN_UNIT_AREA_M2,
    UNIT_COLUMNS,
    build_surface_units,
    d8_receivers,
    hex_units,
)

CRS = "EPSG:32643"
RES = 30.0
ORIGIN_X, ORIGIN_Y = 270_000.0, 2_105_000.0
SHAPE = (60, 60)
TRANSFORM = Affine(RES, 0.0, ORIGIN_X, 0.0, -RES, ORIGIN_Y)
AOI_AREA_M2 = SHAPE[0] * SHAPE[1] * RES * RES


def _dem() -> np.ndarray:
    """A tilted plane with shallow dimples, so flow converges but never ponds forever."""
    rows, cols = np.indices(SHAPE)
    plane = 30.0 - 0.25 * rows - 0.12 * cols
    dimples = 0.4 * np.sin(rows / 2.5) * np.cos(cols / 2.5)
    return (plane + dimples).astype(np.float64)


def _inlets(spacing_cells: int = 5) -> gpd.GeoDataFrame:
    """Inlets on a regular street-like lattice, one every 150 m (SPEC.md 10.1 step 7)."""
    points = []
    ids = []
    for row in range(2, SHAPE[0], spacing_cells):
        for col in range(2, SHAPE[1], spacing_cells):
            x = ORIGIN_X + (col + 0.5) * RES
            y = ORIGIN_Y - (row + 0.5) * RES
            points.append(Point(x, y))
            ids.append(f"IN-{row:03d}-{col:03d}")
    return gpd.GeoDataFrame({"node_id": ids}, geometry=points, crs=CRS)


def test_d8_receivers_point_downhill() -> None:
    dem = np.array([[3.0, 2.0], [2.0, 1.0]])
    receiver = d8_receivers(dem, RES)
    assert receiver[0] == 3  # steepest descent from the corner is the diagonal
    assert receiver[3] == 3  # the low corner is its own receiver


def test_units_tile_the_aoi_without_overlaps() -> None:
    units = build_surface_units(_dem(), TRANSFORM, _inlets(), crs=CRS)
    assert not units.empty
    assert set(units["method"]) == {"d8_watershed"}

    areas = units.geometry.area.to_numpy()
    covered = unary_union(list(units.geometry))
    # A partition: the parts sum to the union (no overlaps) and cover the whole grid.
    # Union and sum walk the same vertices in a different order, so compare with a
    # relative tolerance rather than bit-for-bit.
    assert covered.area == pytest.approx(float(np.sum(areas)), rel=1e-9)
    assert abs(covered.area - AOI_AREA_M2) < 1e-6
    assert units["unit_id"].is_unique


def test_every_unit_area_is_inside_the_cap() -> None:
    units = build_surface_units(_dem(), TRANSFORM, _inlets(), crs=CRS)
    areas = units.geometry.area.to_numpy()
    assert areas.min() >= MIN_UNIT_AREA_M2 - 1e-6
    assert areas.max() <= MAX_UNIT_AREA_M2 + 1e-6
    # The cell-count bookkeeping matches the geometry.
    assert np.allclose(units["area_m2"].to_numpy(), areas)
    assert int(units["n_cells"].sum()) == SHAPE[0] * SHAPE[1]


def test_units_carry_raster_attributes_and_cells() -> None:
    impervious = np.full(SHAPE, 0.7)
    cn = np.full(SHAPE, 94.0)
    manning = np.full(SHAPE, 0.016)
    depth = np.zeros(SHAPE)
    depth[10, 10] = 0.45
    units = build_surface_units(
        _dem(),
        TRANSFORM,
        _inlets(),
        crs=CRS,
        imperviousness=impervious,
        cn=cn,
        manning_n=manning,
        depression_depth=depth,
    )
    assert np.allclose(units["imperviousness"].to_numpy(), 0.7)
    assert np.allclose(units["cn"].to_numpy(), 94.0)
    assert np.allclose(units["manning_n"].to_numpy(), 0.016)
    # Depression depth reduces with max: the unit holding the 0.45 m pit keeps it, and a
    # unit with no pit at all is flat (0.0), never nan.
    assert float(units["depression_depth_m"].max()) == 0.45
    assert units["depression_depth_m"].notna().all()
    assert float(units["depression_depth_m"].min()) == 0.0
    cells = np.concatenate([np.asarray(c) for c in units["cells"]])
    assert cells.size == SHAPE[0] * SHAPE[1]
    assert np.array_equal(np.unique(cells), np.arange(SHAPE[0] * SHAPE[1]))
    assert units["inlet_node_id"].notna().any()


def test_units_are_deterministic() -> None:
    first = build_surface_units(_dem(), TRANSFORM, _inlets(), crs=CRS)
    second = build_surface_units(_dem(), TRANSFORM, _inlets(), crs=CRS)
    assert list(first["unit_id"]) == list(second["unit_id"])
    assert np.allclose(first["area_m2"].to_numpy(), second["area_m2"].to_numpy())


def test_units_attach_to_the_nearest_segment() -> None:
    from shapely.geometry import LineString

    segments = gpd.GeoDataFrame(
        {"segment_id": ["S1-000"]},
        geometry=[
            LineString([(ORIGIN_X, ORIGIN_Y - 300.0), (ORIGIN_X + 1800.0, ORIGIN_Y - 300.0)])
        ],
        crs=CRS,
    )
    units = build_surface_units(_dem(), TRANSFORM, _inlets(), segments, crs=CRS)
    assert (units["segment_id"] == "S1-000").any()


def test_hex_fallback_triggers_without_inlets() -> None:
    units = build_surface_units(_dem(), TRANSFORM, [], crs=CRS)
    assert set(units["method"]) == {"hex_fallback"}
    assert not units.empty
    assert units.geometry.is_valid.all()
    areas = units.geometry.area.to_numpy()
    assert areas.min() >= MIN_UNIT_AREA_M2
    assert areas.max() <= MAX_UNIT_AREA_M2 + 1e-6
    covered = unary_union(list(units.geometry))
    # hexagons do not overlap (again, a relative tolerance: this is floating-point area)
    assert covered.area == pytest.approx(float(np.sum(areas)), rel=1e-9)
    assert covered.area > 0.8 * AOI_AREA_M2


def test_hex_fallback_triggers_on_a_missing_dem() -> None:
    units = build_surface_units(None, TRANSFORM, _inlets(), crs=CRS)
    assert set(units["method"]) == {"hex_fallback"}
    assert not units.empty
    assert list(units.columns) == list(UNIT_COLUMNS)
    assert units.geometry.name == "geometry"
    assert units.geometry.is_valid.all()


def test_hex_fallback_treats_no_inlets_the_same_however_they_are_spelled() -> None:
    """``None``, an empty list and an empty GeoDataFrame all mean 'no inlets'."""
    empty_frame = gpd.GeoDataFrame({"node_id": []}, geometry=[], crs=CRS)
    for inlets in (None, [], empty_frame):
        units = build_surface_units(_dem(), TRANSFORM, inlets, crs=CRS)
        assert set(units["method"]) == {"hex_fallback"}


def test_hex_fallback_still_carries_the_city_rasters() -> None:
    """A fallback city must still feed Flash real CN and roughness, not a column of nans."""
    depth = np.zeros(SHAPE)
    depth[30, 30] = 0.6
    units = build_surface_units(
        _dem(),
        TRANSFORM,
        [],
        crs=CRS,
        imperviousness=np.full(SHAPE, 0.65),
        cn=np.full(SHAPE, 92.0),
        manning_n=np.full(SHAPE, 0.02),
        depression_depth=depth,
    )
    assert set(units["method"]) == {"hex_fallback"}
    assert np.allclose(units["imperviousness"].to_numpy(), 0.65)
    assert np.allclose(units["cn"].to_numpy(), 92.0)
    assert np.allclose(units["manning_n"].to_numpy(), 0.02)
    assert float(units["depression_depth_m"].max()) == 0.6
    assert int(units["n_cells"].sum()) > 0
    cells = np.concatenate([np.asarray(c) for c in units["cells"]])
    assert np.unique(cells).size == cells.size  # a cell belongs to at most one hexagon


def test_hex_units_shrink_to_respect_the_cap() -> None:
    units = hex_units(TRANSFORM, SHAPE, crs=CRS, max_area_m2=10_000.0)
    assert units.geometry.area.max() <= 10_000.0 + 1e-6


def test_hex_units_return_an_empty_frame_not_a_broken_one() -> None:
    """A grid too small to hold one hexagon still returns a usable, empty units frame."""
    units = hex_units(TRANSFORM, (1, 1), crs=CRS)
    assert units.empty
    assert list(units.columns) == list(UNIT_COLUMNS)
    assert units.geometry.name == "geometry"
    assert str(units.crs) == CRS

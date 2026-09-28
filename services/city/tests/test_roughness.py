"""Manning roughness raster tests (task P1.5, SPEC.md 10.1 step 4)."""

from __future__ import annotations

import numpy as np
import pytest
from varuna_city.roughness import (
    N_ASPHALT,
    N_BUILDING,
    N_OPEN_GROUND,
    N_VEGETATION,
    N_WATER,
    manning_n,
)


def test_road_cell_is_asphalt_and_building_cell_is_blocked() -> None:
    shape = (6, 6)
    landcover = np.full(shape, 30, dtype=np.int32)  # grassland
    roads = np.zeros(shape, dtype=bool)
    roads[3, :] = True
    buildings = np.zeros(shape, dtype=bool)
    buildings[1, 1] = True

    result = manning_n(landcover, buildings, roads)

    assert result.n[3, 2] == pytest.approx(N_ASPHALT)
    assert result.n[1, 1] == pytest.approx(N_BUILDING)
    assert result.blocked[1, 1]
    assert not result.blocked[3, 2]
    assert result.n.dtype == np.float32


def test_worldcover_classes_map_to_the_spec_values() -> None:
    codes = np.array([[10, 30, 50, 80]], dtype=np.int32)
    result = manning_n(codes)
    assert result.n[0, 0] == pytest.approx(N_VEGETATION)  # tree cover
    assert result.n[0, 1] == pytest.approx(N_OPEN_GROUND)  # grassland
    assert result.n[0, 2] == pytest.approx(N_ASPHALT)  # built-up
    assert result.n[0, 3] == pytest.approx(N_WATER)  # permanent water


def test_buildings_win_over_roads_and_land_cover() -> None:
    shape = (3, 3)
    landcover = np.full(shape, 80, dtype=np.int32)  # water
    roads = np.ones(shape, dtype=bool)
    buildings = np.zeros(shape, dtype=bool)
    buildings[0, 0] = True

    result = manning_n(landcover, buildings, roads)

    assert result.n[0, 0] == pytest.approx(N_BUILDING)
    assert result.n[2, 2] == pytest.approx(N_ASPHALT)


def test_unknown_codes_and_missing_land_cover_fall_back_to_the_default() -> None:
    codes = np.array([[255, 0]], dtype=np.int32)
    assert np.allclose(manning_n(codes).n, N_OPEN_GROUND)

    roads = np.zeros((4, 4), dtype=bool)
    roads[0, 0] = True
    fallback = manning_n(None, None, roads)
    assert fallback.n[0, 0] == pytest.approx(N_ASPHALT)
    assert fallback.n[3, 3] == pytest.approx(N_OPEN_GROUND)


def test_counts_cover_every_cell_and_array_protocol_returns_n() -> None:
    landcover = np.array([[10, 30], [50, 80]], dtype=np.int32)
    buildings = np.array([[False, False], [False, True]])
    result = manning_n(landcover, buildings)

    assert sum(result.counts.values()) == landcover.size
    assert result.counts["building"] == 1
    assert np.array_equal(np.asarray(result), result.n)
    assert result.stage_ms >= 0.0


def test_float_land_cover_with_nan_is_treated_as_no_data() -> None:
    codes = np.array([[10.0, np.nan]])
    result = manning_n(codes)
    assert result.n[0, 0] == pytest.approx(N_VEGETATION)
    assert result.n[0, 1] == pytest.approx(N_OPEN_GROUND)


def test_shape_mismatch_and_unknown_default_are_rejected() -> None:
    with pytest.raises(ValueError, match="does not match"):
        manning_n(np.zeros((2, 2), dtype=np.int32), np.zeros((3, 3), dtype=bool))
    with pytest.raises(ValueError, match="Unknown surface class"):
        manning_n(np.zeros((2, 2), dtype=np.int32), default="lava")
    with pytest.raises(ValueError, match="needs land cover"):
        manning_n(None)

"""Unit tests for the 2D local-inertial solver (SPEC.md 11.3, Phase 4 P4.1).

Tests:
- Still water stays still (no spurious motion on a flat surface)
- Conservation on a closed basin under uniform rain (rain in = stored)
- Radial symmetry (centred depth spreads symmetrically on a flat square)
- Tide boundary (rising tide floods low cells; falling tide drains them)
- Buildings block flow (water does not enter blocked cells)
- CFL step formula matches the spec
"""

from __future__ import annotations

import numpy as np
import pytest
from varuna_twin.swe2d import (
    CFL_ALPHA,
    DT_MAX_S,
    DT_MIN_S,
    cfl_dt,
    dry_state,
    prepare_terrain,
    run_surface,
    step_surface,
)
from varuna_twin.types import GRAVITY, SurfaceState, TerrainGrid

RES_M = 10.0
"""A small cell size so the tests run fast."""


def _flat_terrain(
    shape: tuple[int, int] = (20, 20),
    z: float = 0.0,
    manning: float = 0.03,
    blocked: np.ndarray | None = None,
) -> TerrainGrid:
    """A flat terrain grid for testing."""
    _n_rows, _n_cols = shape
    return TerrainGrid(
        z=np.full(shape, z, dtype=np.float64),
        manning_n=np.full(shape, manning, dtype=np.float64),
        blocked=np.zeros(shape, dtype=bool) if blocked is None else blocked,
        imperviousness=np.full(shape, 0.5, dtype=np.float64),
        cn=np.full(shape, 95.0, dtype=np.float64),
        res_m=RES_M,
        crs="EPSG:32643",
        transform=(RES_M, 0.0, 0.0, 0.0, -RES_M, 0.0),
    )


def _tilted_terrain(
    shape: tuple[int, int] = (20, 20),
    slope: float = 0.01,
) -> TerrainGrid:
    """A terrain that tilts southward (increasing row = decreasing z)."""
    n_rows, _n_cols = shape
    z = np.zeros(shape, dtype=np.float64)
    for i in range(n_rows):
        z[i, :] = (n_rows - 1 - i) * slope * RES_M
    return TerrainGrid(
        z=z,
        manning_n=np.full(shape, 0.03, dtype=np.float64),
        blocked=np.zeros(shape, dtype=bool),
        imperviousness=np.full(shape, 0.5, dtype=np.float64),
        cn=np.full(shape, 95.0, dtype=np.float64),
        res_m=RES_M,
        crs="EPSG:32643",
        transform=(RES_M, 0.0, 0.0, 0.0, -RES_M, 0.0),
    )


# ============================================================================ P4.1 tests


class TestStillWater:
    """Still water stays still: a flat surface with uniform depth should not move."""

    def test_uniform_depth_on_flat_terrain_stays_still(self) -> None:
        terrain = _flat_terrain()
        kernel = prepare_terrain(terrain)
        state = SurfaceState(
            h=np.full(terrain.shape, 0.1, dtype=np.float64),
            qx=np.zeros(terrain.shape, dtype=np.float64),
            qy=np.zeros(terrain.shape, dtype=np.float64),
        )
        initial_h = state.h.copy()

        # Run for 60 seconds with no forcing
        run = run_surface(state, kernel, 60.0)

        # Depth should not change (no gradient to drive flow)
        assert np.allclose(state.h, initial_h, atol=1e-12)
        assert np.allclose(state.qx, 0.0, atol=1e-12)
        assert np.allclose(state.qy, 0.0, atol=1e-12)
        assert run.error_fraction < 1e-12

    def test_dry_grid_stays_dry(self) -> None:
        terrain = _flat_terrain()
        state = dry_state(terrain)
        run = run_surface(state, terrain, 60.0)
        assert np.all(state.h == 0.0)
        assert run.volume_stored_m3 == 0.0


class TestConservation:
    """Conservation on a closed basin under uniform rain: rain in = stored."""

    def test_rain_on_closed_basin_conserves_volume(self) -> None:
        terrain = _flat_terrain(shape=(15, 15))
        kernel = prepare_terrain(terrain)
        state = dry_state(kernel)

        # Apply uniform rain for 60 seconds
        rain_rate = 1e-4  # m/s ~ 360 mm/h
        run = run_surface(state, kernel, 60.0, r_eff_ms=rain_rate)

        # Expected volume: rate x area x time
        expected_m3 = rain_rate * kernel.cell_area_m2 * 15 * 15 * 60.0
        actual_m3 = run.volume_stored_m3

        assert actual_m3 == pytest.approx(expected_m3, rel=1e-6)
        assert run.error_fraction < 1e-6

    def test_multiple_steps_conserve_volume(self) -> None:
        """Run for multiple steps and check cumulative conservation."""
        terrain = _flat_terrain(shape=(10, 10))
        kernel = prepare_terrain(terrain)
        state = dry_state(kernel)

        rain_rate = 5e-5  # m/s
        total_stored = 0.0
        for _ in range(10):
            run_surface(state, kernel, 30.0, r_eff_ms=rain_rate)
        total_stored = state.volume_m3(kernel.cell_area_m2)

        # After 300 s of rain on 10x10 grid
        expected = rain_rate * kernel.cell_area_m2 * 100 * 300.0
        assert total_stored == pytest.approx(expected, rel=1e-4)


class TestRadialSymmetry:
    """Depth placed at the centre of a flat square spreads symmetrically."""

    def test_centre_blob_spreads_symmetrically(self) -> None:
        shape = (21, 21)
        terrain = _flat_terrain(shape=shape)
        kernel = prepare_terrain(terrain)
        state = dry_state(kernel)

        # Place a blob of water at the centre
        centre_r, centre_c = 10, 10
        state.h[centre_r, centre_c] = 0.5  # 50 cm deep

        # Run for enough time for water to spread
        run_surface(state, kernel, 30.0)

        # Check 4-fold symmetry: the four orthogonal neighbours should be equal
        h = state.h
        assert h[centre_r - 1, centre_c] == pytest.approx(h[centre_r + 1, centre_c], rel=1e-6)
        assert h[centre_r, centre_c - 1] == pytest.approx(h[centre_r, centre_c + 1], rel=1e-6)
        assert h[centre_r - 1, centre_c] == pytest.approx(h[centre_r, centre_c - 1], rel=1e-6)

        # Also check diagonal symmetry
        assert h[centre_r - 1, centre_c - 1] == pytest.approx(
            h[centre_r + 1, centre_c + 1], rel=1e-6
        )
        assert h[centre_r - 1, centre_c + 1] == pytest.approx(
            h[centre_r + 1, centre_c - 1], rel=1e-6
        )


class TestTideBoundary:
    """Rising tide floods low coastal cells; falling tide drains them."""

    def test_rising_tide_floods_low_cells(self) -> None:
        terrain = _flat_terrain(shape=(5, 5), z=0.0)
        kernel = prepare_terrain(terrain)
        state = dry_state(kernel)

        # Bottom row is sea boundary
        sea_mask = np.zeros((5, 5), dtype=bool)
        sea_mask[4, :] = True

        # Tide rises to 0.3 m (all cells are at z=0, so h = 0.3)
        run = run_surface(state, kernel, 10.0, sea_mask=sea_mask, tide_stage_m=0.3)

        # The boundary cells should have depth ~ 0.3
        assert np.all(state.h[4, :] == pytest.approx(0.3, abs=1e-6))
        # Some water should have flowed inland
        assert np.any(state.h[3, :] > 0.0)
        assert run.volume_tide_in_m3 > 0.0

    def test_falling_tide_drains_cells(self) -> None:
        terrain = _flat_terrain(shape=(5, 5), z=0.0)
        kernel = prepare_terrain(terrain)
        # Start with water everywhere
        state = SurfaceState(
            h=np.full((5, 5), 0.5, dtype=np.float64),
            qx=np.zeros((5, 5), dtype=np.float64),
            qy=np.zeros((5, 5), dtype=np.float64),
        )

        sea_mask = np.zeros((5, 5), dtype=bool)
        sea_mask[4, :] = True

        # Tide at 0.0  -  below the water level
        run = run_surface(state, kernel, 10.0, sea_mask=sea_mask, tide_stage_m=0.0)

        # Boundary cells should be drained to stage
        assert np.all(state.h[4, :] == pytest.approx(0.0, abs=1e-6))
        assert run.volume_tide_out_m3 > 0.0


class TestBuildingsBlockFlow:
    """Water does not enter blocked cells."""

    def test_water_does_not_enter_buildings(self) -> None:
        shape = (11, 11)
        blocked = np.zeros(shape, dtype=bool)
        # Block the centre 3x3
        blocked[4:7, 4:7] = True
        terrain = _flat_terrain(shape=shape, blocked=blocked)
        kernel = prepare_terrain(terrain)
        state = dry_state(kernel)

        # Rain everywhere
        rain = np.full(shape, 1e-4, dtype=np.float64)
        rain[blocked] = 0.0  # buildings get no rain (per hydrology.py)

        run_surface(state, kernel, 60.0, r_eff_ms=rain)

        # Buildings must have zero depth
        assert np.all(state.h[blocked] == 0.0)
        # Non-buildings should have water
        assert np.any(state.h[~blocked] > 0.0)

    def test_rain_on_buildings_is_not_applied(self) -> None:
        """Even if we pass rain on blocked cells, they should stay dry."""
        shape = (5, 5)
        blocked = np.zeros(shape, dtype=bool)
        blocked[2, 2] = True
        terrain = _flat_terrain(shape=shape, blocked=blocked)
        state = dry_state(terrain)

        # Deliberately pass rain on the blocked cell too
        rain = np.full(shape, 1e-4, dtype=np.float64)
        run_surface(state, terrain, 30.0, r_eff_ms=rain)

        assert state.h[2, 2] == 0.0


class TestCFL:
    """CFL step formula matches the spec."""

    def test_cfl_formula(self) -> None:
        h = np.array([[0.0, 0.5, 0.0]])
        dt = cfl_dt(h, 30.0)
        expected = CFL_ALPHA * 30.0 / np.sqrt(GRAVITY * 0.5)
        assert dt == pytest.approx(expected, rel=1e-10)

    def test_dry_grid_gives_max_dt(self) -> None:
        h = np.zeros((5, 5), dtype=np.float64)
        assert cfl_dt(h, 30.0) == DT_MAX_S

    def test_cfl_clamp_min(self) -> None:
        # Very deep water -> very small CFL step, clamped to DT_MIN_S
        h = np.array([[1000.0]])
        dt = cfl_dt(h, 30.0)
        assert dt >= DT_MIN_S


class TestMassBalanceError:
    """The mass balance error raises when it should."""

    def test_step_with_zero_duration_raises(self) -> None:
        terrain = _flat_terrain(shape=(3, 3))
        state = dry_state(terrain)
        with pytest.raises(ValueError, match="positive"):
            step_surface(state, terrain, -1.0)

    def test_mismatched_shape_raises(self) -> None:
        terrain = _flat_terrain(shape=(3, 3))
        state = dry_state(_flat_terrain(shape=(4, 4)))
        with pytest.raises(ValueError, match="shape"):
            step_surface(state, terrain, 1.0)


class TestDeterminism:
    """Two identical runs produce identical results (rule 8)."""

    def test_two_runs_are_byte_identical(self) -> None:
        terrain = _flat_terrain(shape=(8, 8))

        state1 = dry_state(terrain)
        state1.h[4, 4] = 0.3
        run_surface(state1, terrain, 30.0, r_eff_ms=5e-5)

        state2 = dry_state(terrain)
        state2.h[4, 4] = 0.3
        run_surface(state2, terrain, 30.0, r_eff_ms=5e-5)

        assert state1.h.tobytes() == state2.h.tobytes()
        assert state1.qx.tobytes() == state2.qx.tobytes()
        assert state1.qy.tobytes() == state2.qy.tobytes()

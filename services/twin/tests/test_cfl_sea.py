"""The CFL step reads the land and the shoreline faces, never the sea's own water.

A sea cell is clamped to the stage before the fluxes and again after continuity, so two sea cells
side by side exchange nothing that survives the clamp, and what a sea cell does exchange with
land crosses one face at that face's flow depth. One deep hole in the sea (Chennai has one at
-5.29 m, with land on three of its faces) therefore no longer halves every sync of a run, while a
shoreline face carrying real water - the tide over a low quay - still shortens the step as it
must. Without a sea every number is the one the full-grid maximum always gave.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np
import pytest
from test_runner import T0, _make_network, _make_rain_cube
from varuna_twin import coupling, swe2d
from varuna_twin.city import CityTerrain
from varuna_twin.runner import run_twin
from varuna_twin.types import GRAVITY, DrainNetwork, SurfaceState, TwinInputs, TwinResult

RES = 30.0
SHAPE = (20, 20)
SEA_COLS = 6
PIT = (10, 2)  # every face of it is sea
SHORE = (10, SEA_COLS - 1)  # its eastern face is land
PIT_Z = -5.29  # Chennai's deepest sea cell


def _grid(*, deep: tuple[int, int] | None = None, blocked: np.ndarray | None = None) -> CityTerrain:
    """Sea in the first six columns at -1 m, land rising gently eastward from 2 m."""
    z = np.full(SHAPE, 2.0) + np.linspace(0.0, 0.4, SHAPE[1])[None, :]
    z[:, :SEA_COLS] = -1.0
    if deep is not None:
        z[deep] = PIT_Z
    sea = np.zeros(SHAPE, dtype=bool)
    sea[:, :SEA_COLS] = True
    return CityTerrain(
        z=z,
        manning_n=np.full(SHAPE, 0.03),
        blocked=np.zeros(SHAPE, dtype=bool) if blocked is None else blocked,
        imperviousness=np.full(SHAPE, 0.7),
        cn=np.full(SHAPE, 95.0),
        res_m=RES,
        crs="EPSG:32643",
        transform=(RES, 0.0, 0.0, 0.0, -RES, 0.0),
        sea=sea,
    )


def _scope(terrain: CityTerrain) -> swe2d.CflScope:
    kernel = swe2d.prepare_terrain(terrain)
    scope = swe2d.cfl_scope(swe2d._sea_index(terrain.sea, kernel), kernel)
    assert scope is not None
    return scope


def _sea_filled(terrain: CityTerrain, stage: float) -> SurfaceState:
    assert terrain.sea is not None
    state = swe2d.dry_state(terrain)
    state.h[terrain.sea] = np.maximum(stage - terrain.z[terrain.sea], 0.0)
    return state


def _dt(h_m: float, ceiling: float) -> float:
    return float(min(swe2d.CFL_ALPHA * RES / np.sqrt(GRAVITY * h_m), ceiling))


# ============================================================================ the scope
def test_no_sea_has_no_scope() -> None:
    kernel = swe2d.prepare_terrain(_grid())
    assert swe2d.cfl_scope(None, kernel) is None


def test_the_scope_is_the_land_and_the_shoreline_faces() -> None:
    terrain = _grid()
    scope = _scope(terrain)
    assert terrain.sea is not None
    np.testing.assert_array_equal(scope.land, ~terrain.sea)
    assert scope.face_sea.size == SHAPE[0], "one face per row, between columns 5 and 6"
    rows, cols = np.unravel_index(scope.face_sea, SHAPE)
    assert set(cols.tolist()) == {SEA_COLS - 1}
    _, land_cols = np.unravel_index(scope.face_land, SHAPE)
    assert set(land_cols.tolist()) == {SEA_COLS}
    np.testing.assert_array_equal(np.sort(rows), np.arange(SHAPE[0]))


def test_a_building_on_the_shore_has_no_face_with_the_sea() -> None:
    blocked = np.zeros(SHAPE, dtype=bool)
    blocked[:, SEA_COLS] = True
    assert _scope(_grid(blocked=blocked)).face_sea.size == 0


# ============================================================================ the step itself
@pytest.mark.parametrize("ceiling", [5.0, 10.0])
@pytest.mark.parametrize("where", [PIT, SHORE], ids=["interior", "shore"])
def test_a_deep_hole_in_the_sea_does_not_shorten_the_step(
    where: tuple[int, int], ceiling: float
) -> None:
    """The whole grid's maximum is the hole's 5.29 m (2.91 s); the land beside it is dry and
    the shoreline faces carry nothing, so the step is the one the land alone sets."""
    terrain = _grid(deep=where)
    state = _sea_filled(terrain, 0.0)
    assert swe2d.cfl_dt(state.h, RES, ceiling) == pytest.approx(_dt(-PIT_Z, ceiling))
    assert swe2d.cfl_dt(state.h, RES, ceiling, _scope(terrain)) == ceiling


def test_a_shoreline_face_carrying_the_tide_still_sets_the_step() -> None:
    """A quay at 0 m under a 2.2 m stage: the land cell is still dry, but its face with the
    sea carries 2.2 m, and that is what the step must resolve."""
    terrain = _grid(deep=SHORE)
    terrain.z[SHORE[0], SEA_COLS] = 0.0
    state = _sea_filled(terrain, 2.2)
    dt = swe2d.cfl_dt(state.h, RES, 5.0, _scope(terrain))
    assert dt == pytest.approx(_dt(2.2, 5.0))
    assert dt < 5.0
    # The land's own water counts as it always did.
    state = _sea_filled(_grid(), 0.0)
    state.h[3, 15] = 2.5
    assert swe2d.cfl_dt(state.h, RES, 5.0, _scope(_grid())) == pytest.approx(_dt(2.5, 5.0))


def test_a_nan_on_land_still_comes_back_as_nan() -> None:
    terrain = _grid(deep=PIT)
    state = _sea_filled(terrain, 0.0)
    state.h[3, 15] = np.nan
    assert np.isnan(swe2d.cfl_dt(state.h, RES, 5.0, _scope(terrain)))


def test_the_split_point_reads_the_same_scope_as_the_solver() -> None:
    """``coupling._first_substep_s`` must agree with ``cfl_dt`` to the bit, scope and all."""
    for where in (PIT, SHORE):
        terrain = _grid(deep=where)
        scope = _scope(terrain)
        for stage in (-2.0, 0.0, 0.7, 2.2, 4.5):
            h = _sea_filled(terrain, stage).h
            h[4, 12] = 0.37
            for ceiling in (5.0, 10.0):
                expected = swe2d.cfl_dt(h, RES, ceiling, scope)
                assert coupling._first_substep_s(h, RES, ceiling, scope) == expected


def test_the_stepper_and_run_surface_take_the_same_steps_over_a_deep_hole() -> None:
    """The stepper stays :func:`run_surface` bit for bit, with the hole's water left out of
    both, and each 5 s sync is one sub-step again."""
    terrain = _grid(deep=SHORE)
    assert terrain.sea is not None
    rain = np.where(terrain.sea, 0.0, 60.0 / 3.6e6)
    a = _sea_filled(terrain, 0.3)
    b = _sea_filled(terrain, 0.3)
    stepper = swe2d.SurfaceStepper(a, terrain, sea_mask=terrain.sea)
    assert stepper.cfl_scope is not None
    stepper.set_rain(rain)
    runs = []
    for _ in range(6):
        stepper.advance(5.0, tide_stage_m=0.3, max_dt_s=5.0)
        runs.append(
            swe2d.run_surface(
                b,
                terrain,
                5.0,
                r_eff_ms=rain,
                sea_mask=terrain.sea,
                tide_stage_m=0.3,
                max_dt_s=5.0,
            ).n_steps
        )
    assert stepper.n_steps == 6
    assert runs == [1] * 6
    np.testing.assert_array_equal(a.h, b.h)
    np.testing.assert_array_equal(a.qx, b.qx)
    np.testing.assert_array_equal(a.qy, b.qy)


def test_without_a_sea_the_step_is_the_full_grid_expression() -> None:
    h = np.zeros(SHAPE)
    h[4, 7] = 3.3
    assert swe2d.cfl_dt(h, RES, 5.0) == swe2d.cfl_dt(h, RES, 5.0, None) == _dt(3.3, 5.0)
    assert coupling._first_substep_s(h, RES, 5.0) == swe2d.cfl_dt(h, RES, 5.0)


# ============================================================================ the coupled run
def _network(terrain: CityTerrain) -> DrainNetwork:
    cells = [(10, c) for c in range(SEA_COLS + 1, SHAPE[1] - 1)]
    base = _make_network(terrain, n_nodes=len(cells))
    rows = np.array([r for r, _ in cells], dtype=np.int32)
    cols = np.array([c for _, c in cells], dtype=np.int32)
    ground = terrain.z[rows, cols]
    return dataclasses.replace(
        base, cell_row=rows, cell_col=cols, z_ground=ground, z_invert=ground - 1.5
    )


def _coupled(terrain: CityTerrain, monkeypatch: pytest.MonkeyPatch) -> tuple[TwinResult, int]:
    calls = {"n": 0}
    original = swe2d.cfl_dt

    def counted(*args: Any, **kwargs: Any) -> float:
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(swe2d, "cfl_dt", counted)
    rain = _make_rain_cube(n_steps=4, shape=SHAPE, peak_mm_h=60.0)
    result = run_twin(TwinInputs(terrain=terrain, network=_network(terrain), rain_mm_h=rain, t0=T0))
    monkeypatch.setattr(swe2d, "cfl_dt", original)
    return result, calls["n"]


def test_a_deep_hole_in_the_sea_changes_nothing_on_land_in_a_coupled_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sea held at mean sea level, as Chennai's is: with the hole, in the sea's interior or
    on its shore, the land takes the same sub-steps and ends with the same water, to the bit."""
    flat, flat_calls = _coupled(_grid(), monkeypatch)
    land = np.zeros(SHAPE, dtype=bool)
    land[:, SEA_COLS:] = True
    for where in (PIT, SHORE):
        hole, calls = _coupled(_grid(deep=where), monkeypatch)
        assert calls == flat_calls, where
        np.testing.assert_array_equal(hole.depth_m[:, land], flat.depth_m[:, land])
        assert hole.depth_m[:, where[0], where[1]].max() == pytest.approx(-PIT_Z)
        assert hole.mass_balance.error_fraction < 1e-3

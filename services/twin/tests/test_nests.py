"""Unit tests for the 5 m nests (SPEC.md 11.3, 10.1 step 1; task P4.8).

SPEC.md 11.3 states four assertions for the 2D kernel, and a nest is that kernel on a
different grid, so all four are re-asserted here rather than assumed to carry over from
``test_swe2d``: still water stays still, a closed basin conserves under uniform rain, a radial
spread on a flat plane stays symmetric, and the mass balance closes inside 0.1 % - the last one
read off the decomposed :class:`~varuna_twin.types.MassBalance` that task P4.5 added, because a
nest has no drain and no exchange and must therefore put its entire residual in
``surface_residual_m3``. A non-zero ``drain_residual_m3``, ``inlet_gap_m3`` or
``surcharge_gap_m3`` on a nest run is a bug in ``nests.py``, not a finding about the city.

The four kernel assertions are made with ``boundary="closed"``. That is the point of the mode:
with the ring driven from a parent, "still water stays still" would be a statement about the
parent's stillness and "conservation" would be a statement about the ring bookkeeping. Closed,
each one is a statement about the solver on a 5 m grid.

The rest of the file tests what is new at 5 m and nowhere else: the refinement has to be a whole
number, rain is block-replicated so the window receives exactly the volume the parent gave it,
the Dirichlet ring follows the parent in space and in time, and the wet/dry gate refuses to
invent water under a dry parent.

The grids here are deliberately small - a 10 x 10 parent window refined 6x is 60 x 60 - because
the CFL clamp binds six times harder at 5 m than at 30 m and a full 1 km Mumbai nest is a
measurement rather than a unit test. That measurement is reported separately; it is not run
here, because it needs a built city and a baked parent run and would put both inside the unit
suite.
"""

from __future__ import annotations

import json
from math import cos, radians

import numpy as np
import pytest
from rasterio.warp import transform as warp_transform
from varuna_schemas.paths import city_dir
from varuna_twin.city import load_nests
from varuna_twin.nests import (
    Nest,
    NestSpec,
    build_nest,
    compare_with_parent,
    run_nest,
)
from varuna_twin.swe2d import DRY_DEPTH_M, MassBalanceError
from varuna_twin.types import TerrainGrid

PARENT_RES_M = 30.0
"""The city grid's cell size (SPEC.md 3.3), so the refinement to 5 m is the real 6."""

NEST_RES_M = 5.0
LEFT = 300_000.0
TOP = 2_100_000.0
CRS = "EPSG:32643"
"""UTM 43N, the Mumbai computation CRS."""

RAIN_MM_H = 50.0
"""The upgraded-corridor design intensity from services/city/configs/mumbai.yaml."""


# ------------------------------------------------------------------ fixtures
def _parent(
    n: int = 12,
    z: np.ndarray | float = 10.0,
    manning: float = 0.03,
    blocked: np.ndarray | None = None,
) -> TerrainGrid:
    """A square parent grid at 30 m, north-up, in the Mumbai CRS."""
    shape = (n, n)
    return TerrainGrid(
        z=np.full(shape, float(z)) if np.isscalar(z) else np.asarray(z, dtype=np.float64),
        manning_n=np.full(shape, manning, dtype=np.float64),
        blocked=np.zeros(shape, dtype=bool) if blocked is None else blocked,
        imperviousness=np.full(shape, 0.8, dtype=np.float64),
        cn=np.full(shape, 95.0, dtype=np.float64),
        res_m=PARENT_RES_M,
        crs=CRS,
        transform=(PARENT_RES_M, 0.0, LEFT, 0.0, -PARENT_RES_M, TOP),
    )


def _centre_lonlat(parent: TerrainGrid) -> tuple[float, float]:
    """The parent grid's centre as lon/lat, so a spec can address it the way a register does."""
    res, _b, left, _d, _e, top = parent.transform
    x = left + parent.n_cols / 2.0 * res
    y = top - parent.n_rows / 2.0 * res
    lons, lats = warp_transform(parent.crs, "EPSG:4326", [x], [y])
    return float(lons[0]), float(lats[0])


def _spec(parent: TerrainGrid, size_m: float = 300.0, res_m: float = NEST_RES_M) -> NestSpec:
    lon, lat = _centre_lonlat(parent)
    return NestSpec(id="TEST-NEST", name="Test nest", lon=lon, lat=lat, size_m=size_m, res_m=res_m)


def _nest(**kwargs: object) -> Nest:
    parent = kwargs.pop("parent", None) or _parent()
    return build_nest(parent, _spec(parent, **kwargs))  # type: ignore[arg-type]


def _rain_ms(mm_h: float = RAIN_MM_H) -> float:
    return mm_h / 1000.0 / 3600.0


# ------------------------------------------------------------------ SPEC.md 11.3 assertions
def test_still_water_stays_still() -> None:
    """A flat sheet on a flat 5 m grid must not move, and must not lose a drop.

    The 30 m version of this test lives in ``test_swe2d``; it is repeated here because the CFL
    step and the friction term both scale with the cell size, so "the kernel is still on 30 m
    cells" is not the same statement as "the kernel is still on 5 m cells".
    """
    nest = _nest()
    depth0 = np.full(nest.shape, 0.2, dtype=np.float64)
    run = run_nest(
        nest,
        r_eff_ms=0.0,
        boundary="closed",
        n_steps=2,
        step_min=5,
        initial_h=depth0,
    )
    final = run.depth_m[-1]
    assert np.allclose(final, 0.2, atol=1e-9), f"max drift {np.max(np.abs(final - 0.2)):.2e} m"
    assert run.volume_created_m3 == pytest.approx(0.0, abs=1e-9)


def test_closed_basin_conserves_under_uniform_rain() -> None:
    """Rain in equals water stored, to the ledger's own precision, with no boundary at all."""
    nest = _nest()
    run = run_nest(nest, r_eff_ms=_rain_ms(), boundary="closed", n_steps=3, step_min=5)
    stored = float(np.sum(run.depth_m[-1])) * nest.terrain.cell_area_m2
    expected = _rain_ms() * 3 * 300.0 * nest.shape[0] * nest.shape[1] * nest.terrain.cell_area_m2

    assert run.volume_rain_m3 == pytest.approx(expected, rel=1e-12)
    assert stored == pytest.approx(run.volume_rain_m3, rel=1e-9)
    assert run.volume_boundary_in_m3 == 0.0
    assert run.volume_boundary_out_m3 == 0.0


def test_radial_spread_is_symmetric() -> None:
    """A column of water at the centre of a flat 5 m plane spreads the same way in four ways.

    Asserted by comparing the field with its own flips rather than by measuring a radius: on an
    even-sided grid there is no centre cell, so the symmetry that exists is about the grid's
    centre lines and both diagonals, and that is exactly what a flip and a transpose test.
    """
    nest = _nest()
    n = nest.shape[0]
    depth0 = np.zeros(nest.shape, dtype=np.float64)
    half = n // 2
    depth0[half - 3 : half + 3, half - 3 : half + 3] = 0.5

    run = run_nest(
        nest,
        r_eff_ms=0.0,
        boundary="closed",
        n_steps=1,
        step_min=1,
        initial_h=depth0,
    )
    final = run.depth_m[-1]
    assert np.allclose(final, np.flipud(final), atol=1e-12)
    assert np.allclose(final, np.fliplr(final), atol=1e-12)
    assert np.allclose(final, final.T, atol=1e-12)
    assert float(np.max(final)) < 0.5, "the column did not spread at all"


def test_mass_balance_is_inside_the_budget_and_all_of_it_is_the_surface() -> None:
    """SPEC.md 11.3's 0.1 %, read off the decomposed ledger task P4.5 added.

    The decomposition is the assertion that matters here. A nest has no pipes and no exchange,
    so the three coupling fields must be identically zero: if any of them were not, the nest
    would be reporting a residual it has no mechanism to produce.
    """
    nest = _nest()
    run = run_nest(nest, r_eff_ms=_rain_ms(), boundary="closed", n_steps=3, step_min=5)
    mb = run.mass_balance

    assert mb.error_fraction < 1e-3
    assert mb.drain_residual_m3 == 0.0
    assert mb.inlet_gap_m3 == 0.0
    assert mb.surcharge_gap_m3 == 0.0
    assert mb.surface_residual_m3 == pytest.approx(mb.residual_m3, abs=1e-12)
    assert mb.volume_stored_start_m3 == pytest.approx(0.0, abs=1e-12)


def test_a_non_finite_starting_depth_is_refused() -> None:
    """A NaN depth does not crash this loop - it stops it, silently, with a clean ledger.

    Found while writing this file, and it is the reason both guards exist.
    :func:`~varuna_twin.swe2d.cfl_dt` divides by ``sqrt(g * h_max)``, so one NaN cell makes the
    step NaN; ``elapsed_s += nan`` makes the sub-stepping condition false, every remaining
    output step repeats the same non-step, and the final audit passes because
    ``SurfaceState.volume_m3`` sums with ``nansum`` and never sees the cell that broke. The run
    returned a field of NaN reporting ``error_fraction`` 0.0 over zero sub-steps.
    """
    nest = _nest()
    depth0 = np.zeros(nest.shape, dtype=np.float64)
    depth0[0, 0] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        run_nest(nest, r_eff_ms=_rain_ms(), boundary="closed", n_steps=1, initial_h=depth0)


def test_a_negative_starting_depth_is_refused() -> None:
    """Not a shallower puddle: a hole the kernel's positivity clamp would create water to fill."""
    nest = _nest()
    depth0 = np.zeros(nest.shape, dtype=np.float64)
    depth0[0, 0] = -0.01
    with pytest.raises(ValueError, match="negative depth"):
        run_nest(nest, r_eff_ms=_rain_ms(), boundary="closed", n_steps=1, initial_h=depth0)


def test_a_non_finite_cfl_step_stops_the_run_rather_than_ending_it() -> None:
    """The backstop for a depth that goes non-finite after the run has started.

    Driven here through a NaN ``max_dt_s``, which is the one input that reaches the same code
    path without having to corrupt the state mid-loop: ``cfl_dt`` returns the clamp unchanged on
    a dry grid. What is being asserted is the guard, not the input - that a step which is not a
    positive number raises instead of letting the loop fall out and the ledger close at 0.0 %.
    """
    nest = _nest()
    with pytest.raises(MassBalanceError, match="no longer finite"):
        run_nest(
            nest,
            r_eff_ms=_rain_ms(),
            boundary="closed",
            n_steps=1,
            max_dt_s=float("nan"),
        )


# ------------------------------------------------------------------ building the crop
def test_refinement_must_be_a_whole_number() -> None:
    """7 m into 30 m is 4.286 cells, and a nest cell would straddle two parents."""
    parent = _parent()
    with pytest.raises(ValueError, match="whole number"):
        build_nest(parent, _spec(parent, res_m=7.0))


def test_a_window_holding_the_sea_is_refused_and_a_sea_outside_it_is_not() -> None:
    """A nest has no sea boundary, so the city's sea inside its window would be street water."""
    from varuna_twin.city import CityTerrain

    plain = _parent()

    def with_sea(col: int) -> CityTerrain:
        sea = np.zeros(plain.shape, dtype=bool)
        sea[:, col] = True
        return CityTerrain(
            z=plain.z,
            manning_n=plain.manning_n,
            blocked=plain.blocked,
            imperviousness=plain.imperviousness,
            cn=plain.cn,
            res_m=plain.res_m,
            crs=plain.crs,
            transform=plain.transform,
            sea=sea,
        )

    inside = with_sea(6)
    with pytest.raises(ValueError, match="sea cells"):
        build_nest(inside, _spec(inside))
    # The 300 m window is ten parent cells centred on a twelve-cell grid; column 0 is outside it.
    outside = with_sea(0)
    nest = build_nest(outside, _spec(outside))
    assert nest.col0 > 0
    np.testing.assert_array_equal(nest.terrain.z, build_nest(plain, _spec(plain)).terrain.z)


def test_window_snaps_to_whole_parent_cells() -> None:
    """SPEC.md 3.3 asks for 1 km2 and 30 m does not divide 1000 m, so the window rounds up."""
    parent = _parent(n=40)
    nest = build_nest(parent, _spec(parent, size_m=1000.0))
    assert nest.n_parent == 34
    assert nest.size_m == pytest.approx(1020.0)
    assert nest.shape == (204, 204)
    assert nest.refine == 6


def test_the_window_is_pushed_inside_the_grid_and_says_so() -> None:
    """A nest near the edge is moved rather than clipped, and the move is a note, not silence."""
    parent = _parent(n=12)
    res, _b, left, _d, _e, top = parent.transform
    lons, lats = warp_transform(parent.crs, "EPSG:4326", [left + res], [top - res])
    spec = NestSpec(
        id="EDGE", name="Edge", lon=float(lons[0]), lat=float(lats[0]), size_m=180.0, res_m=5.0
    )
    nest = build_nest(parent, spec)
    assert nest.row0 == 0
    assert nest.col0 == 0
    assert any("pushed" in note for note in nest.notes)


def test_buildings_are_replicated_not_interpolated() -> None:
    """A mask stays a mask: a bicubic building would be a building with a soft edge."""
    blocked = np.zeros((12, 12), dtype=bool)
    blocked[6, 6] = True
    parent = _parent(n=12, blocked=blocked)
    nest = build_nest(parent, _spec(parent, size_m=360.0))
    assert set(np.unique(nest.terrain.blocked)) <= {False, True}
    # Exactly one parent cell of buildings, so exactly refine^2 nest cells of buildings.
    assert int(nest.terrain.blocked.sum()) == nest.refine**2


def test_bicubic_overshoot_is_counted_at_a_burned_building_edge() -> None:
    """Interpolation is not monotone across the +5 m burn, and the nest reports the trough.

    SPEC.md 10.1 step 4 burns buildings +5 m into the conditioned DEM, so the parent carries
    a cliff at every footprint edge. A bicubic interpolant rings across a cliff: some nest cells
    come out above the highest parent in their neighbourhood and some below the lowest, and the
    low ones are spurious depressions that hold water no street holds.
    """
    z = np.full((12, 12), 10.0)
    z[5:8, 5:8] = 15.0
    parent = _parent(n=12, z=z)
    nest = build_nest(parent, _spec(parent, size_m=360.0))
    assert nest.z_overshoot_cells > 0
    assert any("Bicubic" in note for note in nest.notes)


def test_every_nest_carries_its_two_honesty_labels() -> None:
    """Rule 6: the 5 m DEM is interpolated, and the nest has no drains. Both, on every nest."""
    notes = " ".join(_nest().notes)
    assert "bicubic interpolation of the 30 m conditioned DEM" in notes
    assert "surface-only" in notes


def test_coarsen_is_the_block_mean_and_conserves_volume() -> None:
    """The only like-for-like comparison with a parent cell is the mean of its 36 children."""
    nest = _nest()
    field = np.arange(nest.shape[0] * nest.shape[1], dtype=np.float64).reshape(nest.shape)
    coarse = nest.coarsen(field)
    assert coarse.shape == (nest.n_parent, nest.n_parent)
    assert float(coarse.sum()) * nest.refine**2 == pytest.approx(float(field.sum()), rel=1e-12)


# ------------------------------------------------------------------ rain
def test_parent_rain_is_block_replicated_exactly() -> None:
    """The window must receive precisely the volume the parent gave it - not more, not less."""
    nest = _nest()
    rng = np.random.default_rng(2019)
    rain = rng.uniform(0.0, _rain_ms(), size=nest.parent_shape)
    run = run_nest(nest, r_eff_ms=rain, boundary="closed", n_steps=1, step_min=5)

    crop = rain[nest.parent_slice()]
    expected = float(crop.sum()) * 300.0 * nest.parent_res_m**2
    assert run.volume_rain_m3 == pytest.approx(expected, rel=1e-9)


def test_a_rain_frame_on_the_wrong_grid_is_refused() -> None:
    """Neither the nest's shape nor the parent's, so there is no defensible way to read it."""
    nest = _nest()
    with pytest.raises(ValueError, match="expected the nest's"):
        run_nest(nest, r_eff_ms=np.zeros((7, 7)), boundary="closed", n_steps=1)


# ------------------------------------------------------------------ the Dirichlet ring
def _parent_pair(depth_m: float, n: int = 12, n_steps: int = 2) -> tuple[TerrainGrid, np.ndarray]:
    """A flat parent grid and a depth series that is uniform in space and constant in time."""
    parent = _parent(n=n)
    series = np.full((n_steps, n, n), depth_m, dtype=np.float64)
    return parent, series


def test_the_ring_takes_the_parents_level_and_the_volume_is_counted() -> None:
    """Water crossing the window edge is boundary inflow, in the ledger, in both directions."""
    parent, series = _parent_pair(0.30)
    nest = build_nest(parent, _spec(parent, size_m=300.0))
    run = run_nest(
        nest,
        r_eff_ms=0.0,
        parent_terrain=parent,
        parent_depth_m=series,
        step_min=5,
        n_steps=2,
    )
    ring = run.depth_m[-1]
    assert run.n_boundary_cells == 4 * nest.shape[0] - 4
    assert ring[0, 0] == pytest.approx(0.30, abs=1e-9)
    assert ring[-1, -1] == pytest.approx(0.30, abs=1e-9)
    assert run.volume_boundary_in_m3 > 0.0
    assert run.mass_balance.error_fraction < 1e-3
    # The interior filled from the ring, which is the whole point of driving it.
    assert float(np.max(ring[1:-1, 1:-1])) > 0.0


def test_the_ring_follows_the_parent_between_its_five_minute_outputs() -> None:
    """The parent keeps no sub-step history, so the ring is a ramp between its snapshots.

    This is the accuracy cost the module's docstring names, made visible: a wave that arrives
    and drains inside one 5-minute interval reaches the nest as a slower, shallower rise. Here
    the parent is dry at t0 and at +5 min and 0.40 m at +10 min, so the ring must still be dry
    at the first output and exactly at the parent's level at the second.
    """
    parent = _parent(n=12)
    series = np.stack([np.zeros(parent.shape), np.full(parent.shape, 0.40)])
    nest = build_nest(parent, _spec(parent, size_m=300.0))

    first = run_nest(
        nest,
        r_eff_ms=0.0,
        parent_terrain=parent,
        parent_depth_m=series,
        step_min=5,
        n_steps=1,
    )
    assert first.depth_m[0][0, 0] == pytest.approx(0.0, abs=1e-9)

    full = run_nest(
        nest,
        r_eff_ms=0.0,
        parent_terrain=parent,
        parent_depth_m=series,
        step_min=5,
        n_steps=2,
    )
    assert full.depth_m[-1][0, 0] == pytest.approx(0.40, abs=1e-9)


def test_a_dry_parent_leaves_the_ring_dry_rather_than_flooding_the_interpolant() -> None:
    """The wet/dry gate. Without it a dry parent would impose its coarse ground on a finer one.

    The parent here is a plane tilted across the window, so bicubic resampling puts plenty of
    nest ring cells below the parent ground they sit in. If the gate imposed the parent's level
    minus the nest's ground with the parent dry, every one of those would fill with water
    nothing put there, and the audit would report it - correctly - as boundary inflow.
    """
    n = 12
    z = np.tile(np.linspace(10.0, 16.0, n), (n, 1))
    parent = _parent(n=n, z=z)
    series = np.zeros((2, n, n), dtype=np.float64)
    nest = build_nest(parent, _spec(parent, size_m=300.0))
    run = run_nest(
        nest,
        r_eff_ms=0.0,
        parent_terrain=parent,
        parent_depth_m=series,
        step_min=5,
        n_steps=2,
    )
    assert run.volume_boundary_in_m3 == pytest.approx(0.0, abs=1e-12)
    assert float(np.max(run.depth_m[-1])) <= DRY_DEPTH_M


def test_boundary_parent_without_a_parent_is_refused() -> None:
    """There is nothing to fall back on, so the error says that rather than running closed."""
    nest = _nest()
    with pytest.raises(ValueError, match="needs parent_terrain and parent_depth_m"):
        run_nest(nest, r_eff_ms=0.0, n_steps=1)


def test_a_parent_series_on_the_wrong_grid_is_refused() -> None:
    """A series that is not the city grid cannot be interpolated onto the window's ring."""
    parent = _parent(n=12)
    nest = build_nest(parent, _spec(parent, size_m=300.0))
    with pytest.raises(ValueError, match="expected"):
        run_nest(
            nest,
            r_eff_ms=0.0,
            parent_terrain=parent,
            parent_depth_m=np.zeros((2, 5, 5)),
            n_steps=1,
        )


# ------------------------------------------------------------------ comparison with the parent
def test_compare_with_parent_is_like_for_like() -> None:
    """A 5 m cell and a 30 m cell do not describe the same street, so the nest is coarsened."""
    parent = _parent(n=12)
    nest = build_nest(parent, _spec(parent, size_m=300.0))
    nest_depth = np.full(nest.shape, 0.25, dtype=np.float64)
    parent_depth = np.full(parent.shape, 0.20, dtype=np.float64)

    result = compare_with_parent(nest, nest_depth, parent_depth)
    assert result.n_cells == nest.n_parent**2
    assert result.bias_m == pytest.approx(0.05, abs=1e-12)
    assert result.mean_abs_diff_m == pytest.approx(0.05, abs=1e-12)
    assert result.nest_at_centre_m == pytest.approx(0.25, abs=1e-12)
    assert result.parent_at_centre_m == pytest.approx(0.20, abs=1e-12)


def test_the_deepest_nest_cell_is_reported_beside_the_block_mean() -> None:
    """The block mean is the fair comparison; the deepest cell is what a street actually sees."""
    parent = _parent(n=12)
    nest = build_nest(parent, _spec(parent, size_m=300.0))
    nest_depth = np.zeros(nest.shape, dtype=np.float64)
    row_c = int(nest.centre_rc[0]) - nest.row0
    col_c = int(nest.centre_rc[1]) - nest.col0
    r = nest.refine
    nest_depth[row_c * r, col_c * r] = 0.60

    result = compare_with_parent(nest, nest_depth, np.zeros(parent.shape))
    assert result.nest_cell_max_at_centre_m == pytest.approx(0.60)
    assert result.nest_at_centre_m == pytest.approx(0.60 / r**2)


# ------------------------------------------------------------------ determinism
def test_two_runs_on_the_same_inputs_agree_bit_for_bit() -> None:
    """SPEC.md rule 8. Everything here is float64 arithmetic in a fixed order."""
    parent, series = _parent_pair(0.25)
    nest = build_nest(parent, _spec(parent, size_m=300.0))
    kwargs = {
        "r_eff_ms": _rain_ms(),
        "parent_terrain": parent,
        "parent_depth_m": series,
        "step_min": 5,
        "n_steps": 2,
    }
    first = run_nest(nest, **kwargs)  # type: ignore[arg-type]
    second = run_nest(nest, **kwargs)  # type: ignore[arg-type]
    assert np.array_equal(first.depth_m, second.depth_m)
    assert first.volume_rain_m3 == second.volume_rain_m3
    assert first.volume_boundary_in_m3 == second.volume_boundary_in_m3
    assert first.n_steps == second.n_steps


# ------------------------------------------------------------ centres come from the register
_REGISTER = city_dir("mumbai") / "hotspots.geojson"
_needs_city = pytest.mark.skipif(
    not _REGISTER.is_file(),
    reason="needs a built city: run `make city CITY=mumbai`. The register is what a nest is "
    "centred on, and there is nothing honest to substitute for it (SPEC.md 3.3, rule 7).",
)


@_needs_city
def test_nest_centres_come_from_the_register_and_not_from_the_config() -> None:
    """SPEC.md 3.3 calls the config's coordinates approximate and says to verify each one.

    The distances asserted here are the measured drift between the two, and they are the reason
    the rule exists rather than a formality: 242 m at Hindmata and 127 m at King's Circle, on a
    1 km window centred on a junction. A nest built from the config literal would be off-centre
    by a quarter of its own width.
    """
    specs = {spec.id: spec for spec in load_nests("mumbai")}
    assert set(specs) == {"MUM-NEST-HINDMATA", "MUM-NEST-KINGSCIRCLE"}

    register = {
        f["properties"]["hotspot_id"]: f["properties"]
        for f in json.loads(_REGISTER.read_text(encoding="utf-8"))["features"]
    }
    assert specs["MUM-NEST-HINDMATA"].hotspot_id == "MUM-HS-01"
    assert specs["MUM-NEST-KINGSCIRCLE"].hotspot_id == "MUM-HS-03"

    for spec in specs.values():
        point = register[str(spec.hotspot_id)]
        assert spec.lon == pytest.approx(float(point["lon"]))
        assert spec.lat == pytest.approx(float(point["lat"]))
        assert spec.source_url, f"{spec.id} must carry the register's source_url (rule 7)"
        assert point["coord_verified"] is True
        assert point["confidence"] == "sourced"


@_needs_city
def test_the_drift_from_the_config_literal_is_the_documented_quarter_window() -> None:
    """Measured, not asserted loosely: the numbers this module's docstrings quote."""
    from varuna_schemas.models.city import CityConfig
    from varuna_schemas.paths import city_config_path

    config = {entry.id: entry for entry in CityConfig.from_yaml(city_config_path("mumbai")).nests}
    measured = {}
    for spec in load_nests("mumbai"):
        entry = config[spec.id]
        dx = (spec.lon - entry.lon) * 111_320.0 * cos(radians(spec.lat))
        dy = (spec.lat - entry.lat) * 110_540.0
        measured[spec.id] = (dx * dx + dy * dy) ** 0.5

    assert measured["MUM-NEST-HINDMATA"] == pytest.approx(242.0, abs=1.0)
    assert measured["MUM-NEST-KINGSCIRCLE"] == pytest.approx(127.0, abs=1.0)


@_needs_city
def test_a_resolved_nest_builds_on_the_real_city_grid() -> None:
    """End to end on Mumbai: 1 km2 at 5 m is 204 x 204 cells, six per parent cell."""
    from varuna_twin.city import load_terrain

    parent = load_terrain("mumbai")
    nest = build_nest(parent, load_nests("mumbai")[0])
    assert nest.refine == 6
    assert nest.shape == (204, 204)
    assert nest.size_m == pytest.approx(1020.0)
    assert nest.terrain.crs == parent.crs
    assert nest.terrain.res_m == 5.0


def test_the_ring_imposes_depth_not_level_across_a_burned_building_edge() -> None:
    """The regression for the defect the Hindmata measurement found.

    The nest's ground is a bicubic resampling of the parent's, and bicubic is not monotone, so
    across the +5 m building burn of SPEC.md 10.1 step 4 the nest's ground and the parent's
    disagree - by up to 4.09 m on the real Hindmata window. The ring used to impose the parent's
    water *level*, which is the principled choice when two grids genuinely disagree about the
    ground; here it meant ``level - z_nest`` wrote metres of water onto cells the parent had
    centimetres on. Measured on the 06:00 IST cycle of 2 July 2019: 17,994,252 m3 of boundary
    inflow into a 1.04 km2 window against 1,123 m3 of rain, with the nest peaking at 6.58 m
    where the parent peaked at 2.4 cm - and the audit passing, correctly, because the water did
    cross the boundary.

    Every other ring test in this file uses a flat parent, where level and depth are the same
    number, so none of them could have caught it. This one needs the cliff.
    """
    n = 12
    z = np.full((n, n), 10.0)
    # A burned terrace across the window's northern edge - the +5 m of the conditioning step -
    # so the cliff crosses the ring rather than sitting harmlessly in the interior.
    z[1:3, :] = 15.0
    parent = _parent(n=n, z=z)
    series = np.full((2, n, n), 0.02, dtype=np.float64)  # 2 cm everywhere, as in the measurement
    nest = build_nest(parent, _spec(parent, size_m=300.0))

    run = run_nest(
        nest,
        r_eff_ms=0.0,
        parent_terrain=parent,
        parent_depth_m=series,
        step_min=5,
        n_steps=2,
    )
    # The cliff is in the window, so the two grounds really do disagree - that is the setup.
    # 0.977 m on this fixture: enough that level-matching would put 49x the parent's depth on
    # the worst ring cell, which is the assertion below.
    assert run.ring_ground_gap_m > 0.5, "the fixture did not reproduce the ground disagreement"
    # Every ring cell holds the parent's 2 cm, not the parent's level minus the nest's ground.
    rows, cols = np.nonzero(
        np.pad(np.zeros((nest.shape[0] - 2, nest.shape[1] - 2), bool), 1, constant_values=True)
    )
    ring = run.depth_m[-1][rows, cols]
    assert float(ring.max()) == pytest.approx(0.02, abs=1e-9)
    # A 300 m window held at 2 cm cannot take more water than it can hold several times over.
    window_volume_m3 = 0.02 * nest.size_m**2
    assert run.volume_boundary_in_m3 < 5.0 * window_volume_m3, (
        f"{run.volume_boundary_in_m3:.0f} m3 crossed a {nest.size_m:.0f} m window holding "
        f"{window_volume_m3:.0f} m3"
    )
    assert float(run.depth_m[-1].max()) < 0.5

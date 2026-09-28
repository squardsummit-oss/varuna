"""The drains accept exactly the water the street gives up, and the sea is the city's boundary.

Two defects in the coupled ledger, both found by ``inlet_gap_m3`` - the volume the 1D network
accepted at its inlets less the volume the 2D surface released through them.

**The inlet gap.** The drains step first in every 5 s sync (ADR-0072) and accept the whole capture
:func:`~varuna_twin.coupling.compute_exchange` asked for; the surface then gives up
``min(wanted, available)`` per CFL sub-step. Two things let ``available`` fall short, and each
invented water on its own:

* several nodes on one cell, each limited against the street separately - up to eight share a
  30 m cell on the Mumbai graph - so together they asked for more than the cell held;
* a sync split into CFL sub-steps (any cell deeper than about 1.8 m at 30 m, which a tidal sea
  always is), in which the neighbours drained the cell during the first sub-step and left
  nothing for the capture's share of the second.

On the full city at tide +1 m on the 08:40 cycle it was -66,269 m3, 0.140 % of the inflow and
over SPEC.md 11.3's budget; with the sea cells kept out of the exchange it was still -74,678 m3,
so the sea was not the cause. The fixture below, a deep channel flooding a flat street with the
drains around it, measured -650.9 m3 with six nodes on one cell, -620.9 m3 with one node per cell
(the sub-step mechanism alone) and -279,059.3 m3 with large manholes, under the code before this
change, over six 5-minute steps. All three are 0.0 now.

**The sea ledger.** The sea's own cells used to start dry and fill in the first sub-step, and the
whole-domain ledger booked that fill, and every rise of the tide, as inflow the city received:
23.2 Mm3 against 3.18 Mm3 of rain on the city run, which dilutes any error eight times. The sea
now starts at its ``t0`` stage and the audit is over the city - land cells and pipes - with the
net exchange across the sea boundary as one of its terms.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from test_runner import T0, _make_network, _make_rain_cube, _make_terrain
from varuna_twin import coupling, runner, swe2d
from varuna_twin.coupling import (
    SURCHARGE_CD,
    advance_surface_with_capture,
    compute_exchange,
)
from varuna_twin.drain1d import BOUNDARY_FREE, BOUNDARY_TIDAL, prepare
from varuna_twin.runner import run_twin
from varuna_twin.types import (
    GRAVITY,
    DrainNetwork,
    SurfaceState,
    TerrainGrid,
    TideSeries,
    TwinInputs,
)

RES_M = 30.0
CELL_M2 = RES_M * RES_M
SYNC_S = 5.0


# ============================================================================ fixtures
def _network(
    rows: list[int],
    cols: list[int],
    z_ground: list[float],
    *,
    storage: float,
    from_node: list[int],
    to_node: list[int],
    boundary: dict[int, int],
) -> DrainNetwork:
    n = len(rows)
    ne = len(from_node)
    zg = np.asarray(z_ground, dtype=np.float64)
    diameter = 0.6
    area = np.pi * diameter * diameter / 4.0
    r_h = diameter / 4.0
    n_manning = 0.013
    q_full = (1.0 / n_manning) * area * r_h ** (2.0 / 3.0) * np.sqrt(0.003)
    codes = np.zeros(n, dtype=np.int8)
    for node, code in boundary.items():
        codes[node] = code
    return DrainNetwork(
        node_ids=tuple(f"N{i}" for i in range(n)),
        z_ground=zg,
        z_invert=zg - 1.5,
        storage_area=np.full(n, storage),
        inlet_length=np.full(n, 0.6),
        inlet_area=np.full(n, 0.04),
        kappa=np.zeros(n),
        boundary=codes,
        flap_gate=np.zeros(n, dtype=bool),
        cell_row=np.asarray(rows, dtype=np.int32),
        cell_col=np.asarray(cols, dtype=np.int32),
        edge_ids=tuple(f"E{i}" for i in range(ne)),
        from_node=np.asarray(from_node, dtype=np.int32),
        to_node=np.asarray(to_node, dtype=np.int32),
        length=np.full(ne, 60.0),
        area=np.full(ne, area),
        hydraulic_radius=np.full(ne, r_h),
        diameter=np.full(ne, diameter),
        edge_manning_n=np.full(ne, n_manning),
        q_full=np.full(ne, q_full),
        beta=np.zeros(ne),
    )


def _flat_terrain(shape: tuple[int, int], z: np.ndarray) -> TerrainGrid:
    return TerrainGrid(
        z=z,
        manning_n=np.full(shape, 0.03),
        blocked=np.zeros(shape, dtype=bool),
        imperviousness=np.full(shape, 0.9),
        cn=np.full(shape, 98.0),
        res_m=RES_M,
        crs="EPSG:32643",
        transform=(RES_M, 0.0, 0.0, 0.0, -RES_M, 0.0),
    )


def _flooding_street(*, n_shared: int, storage: float, n_steps: int = 6) -> TwinInputs:
    """A channel 4.5 m deep under a 3 m sea, flooding a flat street with drains on it.

    ``n_shared`` nodes sit on one street cell, 1 m below it, and drain through a collector to a
    tidal outfall in the channel; the sea backs up the pipes and the manholes surcharge and pull
    the street down in turn, which is when several of them ask the one cell for water at once.
    The channel is deeper than 1.8 m, so every sync is split into CFL sub-steps.
    """
    shape = (12, 12)
    z = np.full(shape, 0.5)
    z[:, 0] = -2.0
    mid = shape[0] // 2
    rows = [mid] * n_shared + [mid, mid]
    cols = [6] * n_shared + [3, 0]
    z_ground = [-0.5] * (n_shared + 1) + [-2.0]
    network = _network(
        rows,
        cols,
        z_ground,
        storage=storage,
        from_node=[*range(n_shared), n_shared],
        to_node=[n_shared] * n_shared + [n_shared + 1],
        boundary={n_shared + 1: BOUNDARY_TIDAL},
    )
    times = tuple(T0 + timedelta(minutes=5 * k) for k in range(n_steps + 2))
    tide = TideSeries(times=times, stage_m=np.full(len(times), 3.0), source="illustrative")
    return TwinInputs(
        terrain=_flat_terrain(shape, z),
        network=network,
        rain_mm_h=np.full((n_steps, *shape), 50.0),
        t0=T0,
        step_min=5,
        tide=tide,
    )


# ============================================================================ the cell's cap
def _shared_cell() -> tuple[DrainNetwork, np.ndarray, np.ndarray, np.ndarray]:
    """Six surcharged manholes on one street cell, one ordinary inlet on another, one outfall."""
    shape = (5, 5)
    rows = [2] * 6 + [1, 2]
    cols = [2] * 6 + [1, 2]
    z_ground = [4.0] * 6 + [5.0, 4.0]
    network = _network(
        rows,
        cols,
        z_ground,
        storage=50.0,
        from_node=[0, 1, 2, 3, 4, 5, 6],
        to_node=[7] * 7,
        boundary={7: BOUNDARY_FREE},
    )
    surface_z = np.full(shape, 5.0)
    surface_h = np.zeros(shape)
    surface_h[2, 2] = 0.3  # 270 m3 on the shared cell
    surface_h[1, 1] = 0.05
    head = np.asarray(network.z_invert, dtype=np.float64).copy()
    # Above their own ground, below the street's water surface: the reversed branch, each manhole
    # pulling the street down at up to `h * A / dt` - the whole cell - on its own.
    head[:6] = 4.5
    return network, surface_h, surface_z, head


@pytest.mark.parametrize("compiled", [False, True])
def test_nodes_sharing_a_cell_take_no_more_than_the_cell_holds(compiled: bool) -> None:
    network, surface_h, surface_z, head = _shared_cell()
    exchange = compute_exchange(
        surface_h,
        surface_z,
        head,
        network,
        prepare(network),
        CELL_M2,
        SYNC_S,
        compiled=compiled,
    )
    shared = exchange.q_inlet_node[:6]
    held_m3 = 0.3 * CELL_M2
    # Unlimited, each asks for the orifice or the whole cell, whichever is less: here the cell.
    orifice = SURCHARGE_CD * 50.0 * np.sqrt(2.0 * GRAVITY * 0.8)
    assert orifice > held_m3 / SYNC_S, "fixture: each manhole alone must ask for the whole cell"
    # Together they take the cell's water and not a drop more, split evenly as the formulae were.
    assert float(np.sum(shared)) * SYNC_S == pytest.approx(held_m3, rel=1e-12)
    assert float(exchange.q_inlet_cell[2, 2]) * CELL_M2 * SYNC_S == pytest.approx(
        held_m3, rel=1e-12
    )
    np.testing.assert_allclose(shared, shared[0], rtol=1e-15, atol=0.0)
    # The ordinary inlet on its own cell is not over-asked and keeps Appendix A's rate exactly.
    weir = 1.66 * 0.6 * 0.05**1.5
    orifice_inlet = 0.6 * 0.04 * np.sqrt(2.0 * GRAVITY * 0.05)
    assert exchange.q_inlet_node[6] == pytest.approx(min(weir, orifice_inlet), rel=1e-12)
    assert exchange.q_inlet_node[7] == 0.0, "an outfall captures nothing"


def test_the_kernel_and_numpy_paths_agree_when_a_cell_is_capped() -> None:
    network, surface_h, surface_z, head = _shared_cell()
    solver = prepare(network)
    kwargs = dict(
        surface_h=surface_h,
        surface_z=surface_z,
        drain_head=head,
        network=network,
        solver=solver,
        cell_area_m2=CELL_M2,
        sync_s=SYNC_S,
    )
    numpy_path = compute_exchange(compiled=False, **kwargs)
    kernel_path = compute_exchange(compiled=True, **kwargs)
    # The capped cell agrees to the bit. The uncapped inlet differs in the last place, because the
    # kernel computes the weir's `h ** 1.5` as `h * sqrt(h)` under fastmath - Appendix A's rate,
    # not the cap, and 1.6e-16 relative.
    for name in ("q_inlet_node", "q_surcharge_node", "q_inlet_cell", "q_surcharge_cell"):
        np.testing.assert_allclose(
            getattr(numpy_path, name),
            getattr(kernel_path, name),
            rtol=1e-15,
            atol=0.0,
            err_msg=name,
        )
    np.testing.assert_array_equal(numpy_path.q_inlet_node[:6], kernel_path.q_inlet_node[:6])
    assert numpy_path.q_inlet_cell[2, 2] == kernel_path.q_inlet_cell[2, 2]


# ============================================================================ front-loaded capture
def _stepper(z: np.ndarray, h: np.ndarray) -> swe2d.SurfaceStepper:
    shape = z.shape
    state = SurfaceState(h=h.copy(), qx=np.zeros(shape), qy=np.zeros(shape))
    stepper = swe2d.SurfaceStepper(state, _flat_terrain(shape, z), audit_every=0)
    stepper.set_rain(np.zeros(shape))
    return stepper


def _advance_both(z: np.ndarray, h: np.ndarray, q_inlet: np.ndarray):
    shape = z.shape
    plain = _stepper(z, h)
    plain_run = plain.advance(
        SYNC_S, q_inlet_ms=q_inlet, q_surcharge_ms=np.zeros(shape), max_dt_s=SYNC_S
    )
    front = _stepper(z, h)
    front_run = advance_surface_with_capture(
        front,
        SYNC_S,
        q_inlet_ms=q_inlet,
        q_surcharge_ms=np.zeros(shape),
        tide_stage_m=None,
        capture_buffer=np.zeros(shape),
        no_capture=np.zeros(shape),
    )
    return plain, plain_run, front, front_run


def test_a_sync_of_one_sub_step_is_one_plain_call_bit_for_bit() -> None:
    """Most of a monsoon morning: nothing deeper than about 1.8 m, so nothing changes at all."""
    shape = (6, 6)
    z = np.tile(np.linspace(1.0, 0.0, shape[1]), (shape[0], 1))
    h = np.full(shape, 0.2)
    q_inlet = np.zeros(shape)
    q_inlet[2, 2] = 0.2 / SYNC_S
    plain, plain_run, front, front_run = _advance_both(z, h, q_inlet)
    assert plain_run.n_steps == 1, "fixture: the sync must be a single sub-step"
    for name in ("h", "qx", "qy"):
        np.testing.assert_array_equal(getattr(plain.state, name), getattr(front.state, name))
    assert front_run == (
        plain_run.n_steps,
        plain_run.volume_rain_m3,
        plain_run.volume_surcharge_m3,
        plain_run.volume_inlet_m3,
        plain_run.volume_tide_in_m3,
        plain_run.volume_tide_out_m3,
        plain_run.volume_created_m3,
    )


def test_a_split_sync_hands_over_everything_the_cell_promised() -> None:
    """A street cell beside a deep pool drains into it in the first sub-step.

    Plain, the capture is spread over the sub-steps at one rate, so the second finds the cell
    already emptied by its neighbour and the drains keep water the street never gave: measured
    here as the difference between the two calls. Front-loaded, the first sub-step - the one the
    solver takes anyway - serves the whole sync's capture before any face flux, from water that
    is there by construction.
    """
    shape = (5, 5)
    z = np.full(shape, 2.0)
    z[:, 3:] = -2.0
    h = np.zeros(shape)
    h[:, 3:] = 3.0  # the pool: 3 m deep, so the CFL step is about 3.9 s
    h[2, 2] = 0.5  # the street cell on the pool's edge, its ground 4 m above the pool floor
    q_inlet = np.zeros(shape)
    q_inlet[2, 2] = 0.5 / SYNC_S  # everything on the cell, as the cap allows at most
    promised_m3 = 0.5 * CELL_M2
    _, plain_run, _, front_run = _advance_both(z, h, q_inlet)
    assert plain_run.n_steps >= 2, "fixture: the sync must be split"
    assert plain_run.volume_inlet_m3 < promised_m3 - 1.0, (
        "fixture: the plain call must under-deliver, or this asserts nothing"
    )
    assert front_run[3] == pytest.approx(promised_m3, rel=1e-12)
    assert front_run[0] == plain_run.n_steps
    assert front_run[6] == 0.0, "the positivity clamp created water"


# ============================================================================ coupled
@pytest.mark.parametrize(
    ("n_shared", "storage"),
    [(6, 4.0), (1, 4.0), (6, 20.0)],
    ids=["six-on-one-cell", "one-per-cell", "large-manholes"],
)
def test_a_flooding_street_closes_the_inlet_exchange(n_shared: int, storage: float) -> None:
    result = run_twin(_flooding_street(n_shared=n_shared, storage=storage))
    mb = result.mass_balance
    assert float(np.max(result.q_surcharge)) > 0.0, "fixture: nothing surcharged"
    assert mb.inlet_gap_m3 == pytest.approx(0.0, abs=1e-6), (
        f"the drains accepted {-mb.inlet_gap_m3:.3f} m3 the street never gave"
    )
    assert mb.surcharge_gap_m3 == pytest.approx(0.0, abs=1e-6)
    assert mb.error_fraction < 1e-9


# ============================================================================ the sea ledger
def test_a_sea_that_never_floods_leaves_the_ledger_as_it_was() -> None:
    """Today's Mumbai graph: three tidal outfalls whose cells sit above every stage.

    Both sea terms are then exactly zero, and the city ledger has to give the bits the
    whole-domain ledger gave - the baked runs' mass balance is quoted to ten figures.
    """
    terrain = _make_terrain(shape=(15, 15))
    network = _make_network(terrain, n_nodes=10, has_tidal_outfall=True)
    tide = TideSeries(
        times=(T0, T0 + timedelta(minutes=15), T0 + timedelta(minutes=30)),
        stage_m=np.array([0.5, 1.5, 2.0]),
        source="illustrative",
    )
    outfall = (int(network.cell_row[-1]), int(network.cell_col[-1]))
    assert float(terrain.z[outfall]) > 2.0, "fixture: the sea cell must never flood"
    rain = _make_rain_cube(n_steps=4, shape=(15, 15), peak_mm_h=80.0)
    sea: dict[str, float] = {}
    result = run_twin(
        TwinInputs(terrain=terrain, network=network, rain_mm_h=rain, t0=T0, tide=tide),
        sea_ledger=sea,
    )
    mb = result.mass_balance
    assert sea["sea_stored_start_m3"] == 0.0
    assert sea["sea_stored_end_m3"] == 0.0
    assert sea["tide_in_m3"] == 0.0
    assert sea["tide_out_m3"] > 0.0, "fixture: the sea cell must drain the street"
    assert sea["sea_to_land_m3"] == -sea["tide_out_m3"]
    whole_in = sea["rain_in_m3"] + sea["tide_in_m3"] + max(-sea["outfall_m3"], 0.0)
    whole_out = sea["tide_out_m3"] + max(sea["outfall_m3"], 0.0)
    assert mb.volume_in_m3 == whole_in
    assert mb.volume_out_m3 == whole_out


def _sea_below_the_stage(stage: float = 1.0) -> TwinInputs:
    """The same street, with the tidal outfall's cell 2 m below datum: a sea cell that is wet."""
    terrain = _make_terrain(shape=(15, 15))
    network = _make_network(terrain, n_nodes=10, has_tidal_outfall=True)
    z = np.asarray(terrain.z, dtype=np.float64).copy()
    outfall = (int(network.cell_row[-1]), int(network.cell_col[-1]))
    z[outfall] = -2.0
    terrain = TerrainGrid(
        z=z,
        manning_n=terrain.manning_n,
        blocked=terrain.blocked,
        imperviousness=terrain.imperviousness,
        cn=terrain.cn,
        res_m=terrain.res_m,
        crs=terrain.crs,
        transform=terrain.transform,
    )
    times = tuple(T0 + timedelta(minutes=5 * k) for k in range(8))
    tide = TideSeries(
        times=times,
        stage_m=np.linspace(stage, stage + 0.6, len(times)),
        source="illustrative",
    )
    rain = _make_rain_cube(n_steps=4, shape=(15, 15), peak_mm_h=80.0)
    return TwinInputs(terrain=terrain, network=network, rain_mm_h=rain, t0=T0, tide=tide)


def test_the_sea_starts_at_its_stage_and_its_fill_is_not_inflow() -> None:
    inputs = _sea_below_the_stage()
    sea: dict[str, float] = {}
    result = run_twin(inputs, sea_ledger=sea)
    mb = result.mass_balance
    stage_t0 = float(inputs.tide.at(inputs.t0))
    assert sea["sea_stored_start_m3"] == pytest.approx((stage_t0 + 2.0) * CELL_M2, rel=1e-12)
    # The city's inflow is its rain, the sea it received and any backflow - not the sea itself.
    assert mb.volume_in_m3 == (
        sea["rain_in_m3"] + max(sea["sea_to_land_m3"], 0.0) + max(-sea["outfall_m3"], 0.0)
    )
    assert mb.volume_in_m3 < sea["rain_in_m3"] + sea["tide_in_m3"] + sea["sea_stored_start_m3"]
    # The residual is the whole-domain one, rearranged: only the denominator moved.
    whole = (mb.volume_stored_m3 + sea["sea_stored_end_m3"]) - (
        mb.volume_stored_start_m3 + sea["sea_stored_start_m3"]
    )
    whole -= (
        sea["rain_in_m3"]
        + sea["tide_in_m3"]
        + max(-sea["outfall_m3"], 0.0)
        - sea["tide_out_m3"]
        - max(sea["outfall_m3"], 0.0)
    )
    assert mb.residual_m3 == pytest.approx(whole, abs=1e-6)
    assert mb.error_fraction < 1e-3


def test_filling_the_sea_moves_no_depth_only_the_booking(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fill is the clamp the first sub-step makes anyway, taken before storage is measured."""
    inputs = _sea_below_the_stage()
    filled_sea: dict[str, float] = {}
    filled = run_twin(inputs, sea_ledger=filled_sea)
    with monkeypatch.context() as patch:
        patch.setattr(runner, "_fill_sea", lambda *args, **kwargs: None)
        empty_sea: dict[str, float] = {}
        empty = run_twin(inputs, sea_ledger=empty_sea)

    for name in ("depth_m", "head_m", "q_surcharge", "edge_flow"):
        np.testing.assert_array_equal(getattr(filled, name), getattr(empty, name), err_msg=name)
    fill_m3 = filled_sea["sea_stored_start_m3"]
    assert fill_m3 > 0.0
    assert empty_sea["sea_stored_start_m3"] == 0.0
    # Without the fill the same water arrives as tide during the run.
    assert empty_sea["tide_in_m3"] - filled_sea["tide_in_m3"] == pytest.approx(fill_m3, rel=1e-9)
    assert empty_sea["sea_to_land_m3"] == pytest.approx(filled_sea["sea_to_land_m3"], abs=1e-6)


def test_the_sea_mask_is_the_terrain_sea_alone_when_there_is_one() -> None:
    """A tidal outfall's cell joins the 2D sea only when the raster already says it is sea.

    The mask used to be the raster united with every tidal outfall's cell, and the city build
    makes an outfall tidal within two cells of the sea, so its cell can be land: on the Mumbai
    build 7 of 21 were, 4 on the walled shore ring and 3 two cells inland. The pinned drain head
    is the outfall's whole coupling to the tide. Without a raster the outfalls' cells are still
    the sea, as they were before a coastline existed."""
    terrain = _make_terrain(shape=(10, 10))
    network = _make_network(terrain, n_nodes=5, has_tidal_outfall=True)
    outfall = (int(network.cell_row[-1]), int(network.cell_col[-1]))

    only_outfalls = runner._build_sea_mask(terrain, network)
    assert only_outfalls is not None
    assert int(only_outfalls.sum()) == 1 and bool(only_outfalls[outfall])

    sea = np.zeros(terrain.shape, dtype=bool)
    sea[:, 0] = True
    assert not sea[outfall]
    with_sea = runner._build_sea_mask(SimpleNamespace(shape=terrain.shape, sea=sea), network)
    assert with_sea is not None
    assert np.array_equal(with_sea, sea)

    free = _make_network(terrain, n_nodes=5, has_tidal_outfall=False)
    assert runner._build_sea_mask(terrain, free) is None
    with pytest.raises(ValueError, match=r"terrain\.sea"):
        runner._build_sea_mask(SimpleNamespace(shape=(10, 10), sea=np.zeros((9, 10))), free)


@pytest.mark.parametrize("h_max", [0.0, 1e-6, 0.3, 1.797, 1.8, 3.0, 4.77, 250.0])
def test_the_split_point_is_the_solvers_own_first_sub_step(h_max: float) -> None:
    """``coupling._first_substep_s`` repeats ``swe2d.cfl_dt`` rather than calling it.

    It has to agree to the bit: the capture is scaled by ``duration / dt0`` and served in a
    sub-step of ``dt0``, so a split point one ulp off the solver's would either leave part of
    the capture unserved or serve it over a step the solver never takes. 250 m reaches the
    0.5 s floor; 0 m and 1 micron are the dry-grid and near-dry branches.
    """
    h = np.zeros((4, 4))
    h[1, 2] = h_max
    for res_m in (5.0, 10.0, RES_M):
        for ceiling in (SYNC_S, 10.0):
            assert coupling._first_substep_s(h, res_m, ceiling) == swe2d.cfl_dt(
                h, res_m, ceiling
            ), (h_max, res_m, ceiling)


def test_the_module_exports_what_the_runner_calls() -> None:
    assert "advance_surface_with_capture" in coupling.__all__
    assert "cap_inlet_to_cell_water" in coupling.__all__

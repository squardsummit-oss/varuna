"""The city's sea in the Twin: ``sea_mask.tif`` in, the tide imposed on it, its digest recorded.

``city.load_terrain`` reads the raster into :class:`varuna_twin.city.CityTerrain`; the runner
imposes the tide on it - on the raster alone, never on a tidal outfall's land cell - keeps its
nodes out of the exchange and its rain out of the ledger, and a checkpoint written under one
coastline does not resume under another. A city without the raster runs as it always did, with
the tidal outfalls' cells as its sea. Every city here is a few rasters in ``tmp_path``.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from test_coupling import _coupled_network
from test_runner import T0, _make_network, _make_rain_cube, _make_terrain
from varuna_twin import city as twin_city
from varuna_twin import runner
from varuna_twin.city import SEA_RASTER, CityTerrain
from varuna_twin.coupling import compute_exchange
from varuna_twin.drain1d import prepare
from varuna_twin.runner import SEA_PROVENANCE_KEY, run_twin
from varuna_twin.types import TerrainGrid, TideSeries, TwinInputs

SHAPE = (12, 10)
RES = 30.0


def _write(path: Path, band: np.ndarray, dtype: str, *, shape: tuple[int, int] = SHAPE) -> None:
    import rasterio
    from rasterio.transform import Affine

    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=shape[0],
        width=shape[1],
        count=1,
        dtype=dtype,
        crs="EPSG:32643",
        transform=Affine(RES, 0.0, 270_000.0, 0.0, -RES, 2_100_360.0),
    ) as dst:
        dst.write(band.astype(dtype), 1)


@pytest.fixture
def city_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A city folder holding the five rasters ``load_terrain`` needs, and no sea."""
    root = tmp_path / "testcity"
    root.mkdir()
    ground = np.linspace(-1.0, 5.0, SHAPE[1])[None].repeat(SHAPE[0], 0)
    _write(root / "dem_conditioned.tif", ground, "float32")
    _write(root / "roughness.tif", np.full(SHAPE, 0.03), "float32")
    _write(root / "blocked.tif", np.zeros(SHAPE), "uint8")
    _write(root / "imperviousness.tif", np.full(SHAPE, 0.9), "float32")
    _write(root / "cn.tif", np.full(SHAPE, 98.0), "float32")
    monkeypatch.setattr(twin_city, "city_dir", lambda city: tmp_path / city)
    return root


# ============================================================================ load_terrain
def test_a_city_without_a_sea_raster_loads_with_no_sea(city_root: Path) -> None:
    terrain = twin_city.load_terrain("testcity")
    assert isinstance(terrain, TerrainGrid)
    assert getattr(terrain, "sea", "missing") is None


def test_the_sea_raster_is_read_as_open_sea_and_creek_alike(city_root: Path) -> None:
    codes = np.zeros(SHAPE, dtype=np.uint8)
    codes[:, 0] = 1  # open sea
    codes[3:5, 1] = 2  # tidal creek
    codes[0, 5] = 255  # the writer's no-data is never sea
    _write(city_root / SEA_RASTER, codes, "uint8")
    terrain = twin_city.load_terrain("testcity")
    assert isinstance(terrain, CityTerrain)
    assert terrain.sea is not None and terrain.sea.dtype == np.bool_
    assert terrain.sea[:, 0].all()
    assert terrain.sea[3:5, 1].all()
    assert int(terrain.sea.sum()) == SHAPE[0] + 2


def test_a_sea_raster_on_another_grid_is_refused(city_root: Path) -> None:
    _write(city_root / SEA_RASTER, np.zeros((12, 11)), "uint8", shape=(12, 11))
    with pytest.raises(ValueError, match="share one grid"):
        twin_city.load_terrain("testcity")


# ============================================================================ the runner's sea
def _with_sea(terrain: TerrainGrid, sea: np.ndarray | None) -> CityTerrain:
    return CityTerrain(
        z=terrain.z,
        manning_n=terrain.manning_n,
        blocked=terrain.blocked,
        imperviousness=terrain.imperviousness,
        cn=terrain.cn,
        res_m=terrain.res_m,
        crs=terrain.crs,
        transform=terrain.transform,
        sea=sea,
    )


def test_the_sea_mask_is_the_raster_alone_and_a_land_outfall_is_not_added() -> None:
    """A tidal outfall's head is pinned to the stage already; its cell joins the 2D sea only
    when the raster says it is sea. Without a raster the outfall cells are the sea, as before."""
    terrain = _make_terrain(shape=(15, 15))
    network = _make_network(terrain, n_nodes=10, has_tidal_outfall=True)
    outfall = (int(network.cell_row[-1]), int(network.cell_col[-1]))

    legacy = runner._build_sea_mask(terrain, network)
    assert legacy is not None and int(legacy.sum()) == 1 and legacy[outfall]

    strip = np.zeros(terrain.shape, dtype=bool)
    strip[:, 0] = True
    mask = runner._build_sea_mask(_with_sea(terrain, strip), network)
    assert mask is not None
    np.testing.assert_array_equal(mask, strip)
    assert not mask[outfall]

    # An outfall standing on a sea cell is sea because the raster says so.
    on_sea = strip.copy()
    on_sea[outfall] = True
    np.testing.assert_array_equal(
        runner._build_sea_mask(_with_sea(terrain, on_sea), network), on_sea
    )

    no_tide = _make_network(terrain, n_nodes=10, has_tidal_outfall=False)
    assert runner._build_sea_mask(terrain, no_tide) is None
    assert runner._build_sea_mask(_with_sea(terrain, np.zeros_like(strip)), no_tide) is None


def test_a_sea_on_another_grid_is_refused_by_the_runner() -> None:
    terrain = _make_terrain(shape=(15, 15))
    network = _make_network(terrain, n_nodes=10)
    with pytest.raises(ValueError, match=r"terrain.sea"):
        runner._build_sea_mask(_with_sea(terrain, np.zeros((15, 14), dtype=bool)), network)


def _coast_inputs(sea: np.ndarray | None, *, stage: float = 0.5) -> TwinInputs:
    """A strip of sea two cells wide on the west edge, 1 m below datum, under a steady tide."""
    base = _make_terrain(shape=(15, 15))
    z = np.asarray(base.z, dtype=np.float64).copy()
    z[:, :2] = -1.0
    blocked = np.asarray(base.blocked, dtype=bool).copy()
    blocked[:, :2] = False
    terrain = CityTerrain(
        z=z,
        manning_n=base.manning_n,
        blocked=blocked,
        imperviousness=base.imperviousness,
        cn=base.cn,
        res_m=base.res_m,
        crs=base.crs,
        transform=base.transform,
        sea=sea,
    )
    network = _make_network(terrain, n_nodes=10)
    times = tuple(T0 + timedelta(minutes=5 * k) for k in range(6))
    tide = TideSeries(times=times, stage_m=np.full(len(times), stage), source="illustrative")
    rain = _make_rain_cube(n_steps=3, shape=(15, 15), peak_mm_h=20.0)
    return TwinInputs(terrain=terrain, network=network, rain_mm_h=rain, t0=T0, tide=tide)


def test_the_tide_is_imposed_on_every_sea_cell_and_the_ledger_balances() -> None:
    strip = np.zeros((15, 15), dtype=bool)
    strip[:, :2] = True
    inputs = _coast_inputs(strip, stage=0.5)
    ledger: dict[str, float] = {}
    result = run_twin(inputs, sea_ledger=ledger)
    # every sea cell stands at the stage at every output step: 0.5 m over a -1 m bed
    np.testing.assert_allclose(result.depth_m[:, strip], 1.5, atol=1e-9)
    # the sea's volume at t0 is storage, not inflow
    assert ledger["sea_stored_start_m3"] == pytest.approx(1.5 * RES * RES * strip.sum())
    assert result.mass_balance.error_fraction < 1e-3


def test_with_no_tide_series_the_sea_stands_at_mean_sea_level_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A design storm (``CHN-IDF-25yr``) carries no ``tide.csv``, and a coastal city still has a
    sea. The run holds it at 0.0 m in the DEM's frame - over a -1 m bed that is 1 m of water on
    every sea cell at every step - and its notes say the level was assumed, not measured.

    The drain's outfalls are pinned to the same level from the first instant: every pin the run
    makes, the one before the loop included, is given 0.0 m and never ``None``, which would stand
    a tidal outfall whose invert is below 0 m at that invert until the first inner step."""
    strip = np.zeros((15, 15), dtype=bool)
    strip[:, :2] = True
    coast = _coast_inputs(strip)
    inputs = TwinInputs(
        terrain=coast.terrain,
        network=_make_network(coast.terrain, n_nodes=10, has_tidal_outfall=True),
        rain_mm_h=coast.rain_mm_h,
        t0=T0,
        tide=None,
    )

    stages: list[object] = []
    real_pin = runner.drain1d.pin_boundaries

    def spy(solver: object, head: np.ndarray, tide_stage_m: object) -> None:
        stages.append(tide_stage_m)
        real_pin(solver, head, tide_stage_m)

    monkeypatch.setattr(runner.drain1d, "pin_boundaries", spy)
    ledger: dict[str, float] = {}
    result = run_twin(inputs, sea_ledger=ledger)

    np.testing.assert_allclose(result.depth_m[:, strip], 1.0, atol=1e-9)
    assert ledger["sea_stored_start_m3"] == pytest.approx(1.0 * RES * RES * strip.sum())
    assert result.mass_balance.error_fraction < 1e-3
    assert any("held at mean sea level (0.0 m)" in note for note in result.notes)
    assert stages, "the run pins its outfalls at least once"
    assert all(stage == runner.MEAN_SEA_LEVEL_M for stage in stages), stages


def test_with_no_tide_series_and_no_sea_nothing_is_imposed() -> None:
    """No sea and no series: no level is invented, and no note claims one was."""
    inputs = _coast_inputs(None)
    result = run_twin(
        TwinInputs(
            terrain=inputs.terrain,
            network=inputs.network,
            rain_mm_h=inputs.rain_mm_h,
            t0=T0,
            tide=None,
        )
    )
    assert not any("mean sea level" in note for note in result.notes)


def test_without_the_raster_the_same_cells_are_land() -> None:
    """A city built before the sea step: nothing imposes a level on its low west edge."""
    result = run_twin(_coast_inputs(None, stage=0.5))
    strip = np.zeros((15, 15), dtype=bool)
    strip[:, :2] = True
    assert float(result.depth_m[:, strip].max()) < 1.5


def test_the_sea_is_part_of_the_fingerprint() -> None:
    strip = np.zeros((15, 15), dtype=bool)
    strip[:, :2] = True
    with_sea = run_twin(_coast_inputs(strip))
    keys = dict(with_sea.final_state.fingerprint.provenance)
    assert SEA_PROVENANCE_KEY in keys and len(keys[SEA_PROVENANCE_KEY]) == 64

    other = strip.copy()
    other[0, 2] = True
    differs = runner._sea_provenance(_coast_inputs(other).terrain, None)
    assert differs is not None and differs[SEA_PROVENANCE_KEY] != keys[SEA_PROVENANCE_KEY]

    without = run_twin(_coast_inputs(None))
    assert SEA_PROVENANCE_KEY not in dict(without.final_state.fingerprint.provenance)


def test_a_checkpoint_does_not_resume_under_another_coastline() -> None:
    strip = np.zeros((15, 15), dtype=bool)
    strip[:, :2] = True
    first = run_twin(_coast_inputs(strip))
    moved = strip.copy()
    moved[:, 2] = True
    resumed = _coast_inputs(moved)
    later = TwinInputs(
        terrain=resumed.terrain,
        network=resumed.network,
        rain_mm_h=resumed.rain_mm_h,
        t0=T0 + timedelta(minutes=15),
        tide=resumed.tide,
        initial_state=first.final_state,
    )
    with pytest.raises(ValueError, match=SEA_PROVENANCE_KEY):
        run_twin(later)


# ============================================================================ review repairs
def _walled_coast(
    *, sea_cols: int, wall: float | None, land_m: float, nodes: list[tuple[int, int]]
) -> tuple[CityTerrain, np.ndarray]:
    """A 15 x 15 coast: ``sea_cols`` of sea at -1 m on the west, an optional wall one cell wide,
    flat land behind it, no buildings; and a drain line through ``nodes``, last one the outfall."""
    shape = (15, 15)
    z = np.full(shape, land_m, dtype=np.float64)
    z[:, :sea_cols] = -1.0
    if wall is not None:
        z[:, sea_cols] = wall
    sea = np.zeros(shape, dtype=bool)
    sea[:, :sea_cols] = True
    terrain = CityTerrain(
        z=z,
        manning_n=np.full(shape, 0.03),
        blocked=np.zeros(shape, dtype=bool),
        imperviousness=np.full(shape, 0.7),
        cn=np.full(shape, 95.0),
        res_m=RES,
        crs="EPSG:32643",
        transform=(RES, 0.0, 0.0, 0.0, -RES, 0.0),
        sea=sea,
    )
    return terrain, sea


def _line_network(terrain: TerrainGrid, cells: list[tuple[int, int]], **replace):
    import dataclasses

    base = _make_network(terrain, n_nodes=len(cells))
    rows = np.array([r for r, _ in cells], dtype=np.int32)
    cols = np.array([c for _, c in cells], dtype=np.int32)
    ground = np.asarray(terrain.z, dtype=np.float64)[rows, cols]
    return dataclasses.replace(
        base, cell_row=rows, cell_col=cols, z_ground=ground, z_invert=ground - 1.5, **replace
    )


def _steady(stage: float) -> TideSeries:
    times = tuple(T0 + timedelta(minutes=5 * k) for k in range(8))
    return TideSeries(times=times, stage_m=np.full(len(times), stage), source="illustrative")


def test_a_tidal_outfall_on_land_behind_the_wall_is_never_flooded_by_the_stage() -> None:
    """MUM-N049187's case: a tidal outfall two cells from the sea, on land at 1.5 m behind a
    wall, under a stage above its ground. Its head is the stage; its cell stays land."""
    cells = [(7, c) for c in range(12, 2, -1)]  # east to west, the outfall at column 3
    terrain, sea = _walled_coast(sea_cols=2, wall=3.0, land_m=1.5, nodes=cells)
    boundary = np.zeros(len(cells), dtype=np.int8)
    boundary[-1] = twin_city.BOUNDARY_TIDAL
    flap = np.zeros(len(cells), dtype=bool)
    flap[-1] = True
    network = _line_network(terrain, cells, boundary=boundary, flap_gate=flap)
    outfall = cells[-1]

    # the old union would have made this land cell a Dirichlet sea cell
    legacy = runner._build_sea_mask(_with_sea(terrain, None), network)
    assert legacy is not None and legacy[outfall]

    inputs = TwinInputs(
        terrain=terrain,
        network=network,
        rain_mm_h=np.zeros((3, 15, 15)),
        t0=T0,
        tide=_steady(2.2),
    )
    result = run_twin(inputs)
    land = ~sea
    assert float(result.depth_m[:, land].max()) == 0.0
    np.testing.assert_allclose(result.depth_m[:, sea], 3.2, atol=1e-9)


def test_an_inlet_on_a_sea_cell_takes_no_seawater_under_a_rising_tide() -> None:
    """148 of Mumbai's interior nodes sit on sea cells. The clamp refills those cells every
    sub-step, so an inlet there was an ungated pipe from the sea; it exchanges nothing now."""
    cells = [(7, c) for c in range(3, 13)]  # two nodes on the sea, eight on land, free outfall
    terrain, sea = _walled_coast(sea_cols=5, wall=None, land_m=2.0, nodes=cells)
    network = _line_network(terrain, cells)
    assert sea[cells[0]] and sea[cells[1]]
    rising = TideSeries(
        times=(T0, T0 + timedelta(minutes=30)),
        stage_m=np.array([0.3, 1.5]),
        source="illustrative",
    )
    inputs = TwinInputs(
        terrain=terrain, network=network, rain_mm_h=np.zeros((6, 15, 15)), t0=T0, tide=rising
    )
    ledger: dict[str, float] = {}
    result = run_twin(inputs, sea_ledger=ledger)

    # the node's cell is under at least 1.3 m of sea at every step, so an inlet would capture
    assert float(result.depth_m[:, 7, 3].min()) >= 1.3 - 1e-9
    # and nothing entered the pipes: no flow, no head above an invert, no water on land
    assert float(np.abs(result.edge_flow).max()) == 0.0
    np.testing.assert_array_equal(
        result.head_m, np.broadcast_to(network.z_invert, result.head_m.shape)
    )
    assert float(result.depth_m[:, ~sea].max()) == 0.0
    assert ledger["outfall_m3"] == 0.0


def test_rain_on_the_sea_is_not_booked_as_the_city_s_rain() -> None:
    """The clamp returns rain on a sea cell to the sea at once, so booking it made `sea_to_land`
    mostly the sea's own rain. With a sea raster, only the land's rain enters the ledger."""
    strip = np.zeros((15, 15), dtype=bool)
    strip[:, :2] = True
    coast = _coast_inputs(strip, stage=0.5)
    coast_ledger: dict[str, float] = {}
    run_twin(coast, sea_ledger=coast_ledger)

    # the same terrain with no raster: every cell's rain is booked
    plain = _coast_inputs(None, stage=0.5)
    plain_ledger: dict[str, float] = {}
    run_twin(plain, sea_ledger=plain_ledger)

    # effective rain is uniform on this terrain's open cells and zero under its buildings, so the
    # sea's share is its share of the open cells
    open_cells = ~np.asarray(coast.terrain.blocked, dtype=bool)
    land_share = float((open_cells & ~strip).sum() / open_cells.sum())
    assert coast_ledger["rain_in_m3"] == pytest.approx(
        plain_ledger["rain_in_m3"] * land_share, rel=1e-12
    )
    # what the sea passed on is land runoff reaching it, never its own rain handed back
    assert coast_ledger["sea_to_land_m3"] <= 0.0
    assert coast_ledger["tide_in_m3"] == pytest.approx(0.0, abs=1e-9)


def test_a_coastal_run_resumed_under_a_moving_tide_is_the_whole_run() -> None:
    """Hot start plus a sea: the resume fills the sea to its new ``t0`` stage, which is the clamp
    the continuing run makes at the same instant, so no depth anywhere differs."""
    strip = np.zeros((15, 15), dtype=bool)
    strip[:, :2] = True
    coast = _coast_inputs(strip)
    tide = TideSeries(
        times=(T0, T0 + timedelta(minutes=30)),
        stage_m=np.array([-0.8, 1.6]),
        source="illustrative",
    )
    rain = _make_rain_cube(n_steps=6, shape=(15, 15), peak_mm_h=40.0)

    def inputs(cube: np.ndarray, t0, initial=None) -> TwinInputs:
        return TwinInputs(
            terrain=coast.terrain,
            network=coast.network,
            rain_mm_h=cube,
            t0=t0,
            tide=tide,
            initial_state=initial,
        )

    whole = run_twin(inputs(rain, T0))
    first = run_twin(inputs(rain[:3], T0))
    second = run_twin(inputs(rain[3:], T0 + timedelta(minutes=15), first.final_state))

    # the stage moved between the checkpoint's last clamp and the resume
    assert tide.at(T0 + timedelta(minutes=15)) != tide.at(T0 + timedelta(minutes=10))
    for name in ("depth_m", "head_m", "q_surcharge", "edge_flow"):
        joined = np.concatenate([getattr(first, name), getattr(second, name)])
        np.testing.assert_array_equal(getattr(whole, name), joined, err_msg=name)
    assert second.mass_balance.ok, second.notes


# ============================================================================ the exchange on the sea
def _exchange_case() -> dict[str, object]:
    """Three nodes on a 5 x 5 grid: node 0 half a metre over its street, so it surcharges, and
    node 1 under 0.3 m of water with room in its pipe, so it captures. Node 2 is a free outfall."""
    network, surface_h, surface_z = _coupled_network()
    head = np.asarray(network.z_invert, dtype=np.float64).copy()
    head[0] = network.z_ground[0] + 0.5
    surface_h[2, 2] = 0.3
    return dict(
        surface_h=surface_h,
        surface_z=surface_z,
        drain_head=head,
        network=network,
        solver=prepare(network),
        cell_area_m2=900.0,
        sync_s=5.0,
    )


@pytest.mark.parametrize("compiled", [False, True])
def test_a_node_on_a_sea_cell_neither_captures_nor_surcharges(compiled: bool) -> None:
    """The review's 148 Mumbai nodes: a sea cell the clamp keeps at the stage is not a street."""
    case = _exchange_case()
    land = compute_exchange(compiled=compiled, **case)
    assert land.q_surcharge_node[0] > 0.0, "the case must surcharge to be worth testing"
    assert land.q_inlet_node[1] > 0.0, "the case must capture to be worth testing"

    sea = np.zeros((5, 5), dtype=bool)
    sea[1, 1] = True  # node 0's cell
    sea[2, 2] = True  # node 1's cell
    shore = compute_exchange(compiled=compiled, sea=sea, **case)
    assert shore.q_surcharge_node[0] == 0.0
    assert shore.q_inlet_node[1] == 0.0
    assert not shore.q_inlet_cell.any()
    assert not shore.q_surcharge_cell.any()


def test_the_kernel_and_numpy_paths_agree_with_a_sea_and_buildings() -> None:
    """The parity `coupling_kernel` claims, with both masks set and a node left on each side."""
    case = _exchange_case()
    sea = np.zeros((5, 5), dtype=bool)
    sea[2, 2] = True  # node 1 on the sea; node 0 still surcharges onto land
    sea[0, :] = True  # sea with no node on it
    blocked = np.zeros((5, 5), dtype=bool)
    blocked[4, 0] = True
    numpy_path = compute_exchange(compiled=False, sea=sea, blocked=blocked, **case)
    kernel_path = compute_exchange(compiled=True, sea=sea, blocked=blocked, **case)
    assert numpy_path.q_surcharge_node[0] > 0.0, "agreement on all zeros would prove nothing"
    for name in ("q_inlet_node", "q_surcharge_node", "q_inlet_cell", "q_surcharge_cell"):
        np.testing.assert_array_equal(
            getattr(numpy_path, name), getattr(kernel_path, name), err_msg=name
        )


@pytest.mark.parametrize("compiled", [False, True])
def test_no_sea_is_an_empty_sea_and_a_sea_without_nodes_changes_nothing(compiled: bool) -> None:
    """`sea=None` is not a third behaviour, and a mask only matters where a node stands on it."""
    case = _exchange_case()
    omitted = compute_exchange(compiled=compiled, **case)
    empty = np.zeros((5, 5), dtype=bool)
    nodeless = empty.copy()
    nodeless[0, :] = True
    nodeless[:, 4] = True
    for sea in (None, empty, nodeless):
        other = compute_exchange(compiled=compiled, sea=sea, **case)
        for name in ("q_inlet_node", "q_surcharge_node", "q_inlet_cell", "q_surcharge_cell"):
            np.testing.assert_array_equal(
                getattr(omitted, name), getattr(other, name), err_msg=name
            )


# ============================================================================ the sea's ledger
def test_sea_to_land_is_the_face_exchange_alone() -> None:
    """A sea walled off from the land by a column of buildings shares no face with it. Rain falls
    on the sea, two drain nodes stand in it and the tide rises 1.2 m, and still nothing crosses:
    `sea_to_land` is zero, because rain on the sea is never booked and its nodes never exchange.
    Before the repair the sea's own rain came back out of the clamp as `tide_out` and the nodes'
    capture as a draw on the sea, and the term read as the negative of both."""
    shape = (15, 15)
    z = np.full(shape, 2.0, dtype=np.float64)
    z += np.linspace(0.3, 0.0, shape[1])[None, :]  # land falls gently east, away from the sea
    z[:, :3] = -1.0
    blocked = np.zeros(shape, dtype=bool)
    blocked[:, 3] = True
    sea = np.zeros(shape, dtype=bool)
    sea[:, :3] = True
    terrain = CityTerrain(
        z=z,
        manning_n=np.full(shape, 0.03),
        blocked=blocked,
        imperviousness=np.full(shape, 0.7),
        cn=np.full(shape, 95.0),
        res_m=RES,
        crs="EPSG:32643",
        transform=(RES, 0.0, 0.0, 0.0, -RES, 0.0),
        sea=sea,
    )
    cells = [(7, 1), (7, 2)] + [(7, c) for c in range(4, 13)]
    network = _line_network(terrain, cells)
    rising = TideSeries(
        times=(T0, T0 + timedelta(minutes=30)),
        stage_m=np.array([0.3, 1.5]),
        source="illustrative",
    )
    inputs = TwinInputs(
        terrain=terrain,
        network=network,
        rain_mm_h=_make_rain_cube(n_steps=6, shape=shape, peak_mm_h=40.0),
        t0=T0,
        tide=rising,
    )
    ledger: dict[str, float] = {}
    result = run_twin(inputs, sea_ledger=ledger)

    assert ledger["rain_in_m3"] > 0.0 and ledger["tide_in_m3"] > 0.0
    assert ledger["outfall_m3"] > 0.0, "the land's runoff leaves by the pipes, not the sea"
    assert abs(ledger["sea_to_land_m3"]) <= 1e-9 * ledger["sea_stored_end_m3"]
    assert result.mass_balance.ok, result.notes


def test_without_the_raster_the_ledger_books_rain_as_it_always_did() -> None:
    """The no-sea path is untouched: a city built before the sea step books the rain on every
    open cell, the tidal outfall's own cell included, and its sea is that cell under the stage."""
    terrain = _make_terrain(shape=(15, 15))
    tidal = _make_network(terrain, n_nodes=10, has_tidal_outfall=True)
    free = _make_network(terrain, n_nodes=10, has_tidal_outfall=False)
    outfall = (int(tidal.cell_row[-1]), int(tidal.cell_col[-1]))
    stage = float(terrain.z[outfall]) + 0.4  # the outfall's cell is under 0.4 m of sea
    rain = _make_rain_cube(n_steps=3, shape=(15, 15), peak_mm_h=40.0)

    def run(network, sea_attr: bool) -> tuple[object, dict[str, float]]:
        grid = _with_sea(terrain, None) if sea_attr else terrain
        ledger: dict[str, float] = {}
        inputs = TwinInputs(
            terrain=grid, network=network, rain_mm_h=rain, t0=T0, tide=_steady(stage)
        )
        return run_twin(inputs, sea_ledger=ledger), ledger

    coastal, coastal_ledger = run(tidal, sea_attr=False)
    _inland, inland_ledger = run(free, sea_attr=False)
    assert coastal_ledger["rain_in_m3"] == inland_ledger["rain_in_m3"]
    assert coastal.depth_m[:, outfall[0], outfall[1]].min() == pytest.approx(0.4, abs=1e-12)

    # `sea=None` on a CityTerrain is the plain grid, bit for bit
    same, same_ledger = run(tidal, sea_attr=True)
    for name in ("depth_m", "head_m", "q_surcharge", "edge_flow"):
        np.testing.assert_array_equal(getattr(coastal, name), getattr(same, name), err_msg=name)
    assert same_ledger == coastal_ledger


def test_the_run_log_quotes_the_lands_peak_not_the_seas() -> None:
    """The sea is held at the tide, so the whole-grid maximum is the deepest sea cell - on
    Mumbai 2.58 m at the crest - and never a street."""
    depth = np.full((2, 4, 5), 0.1)
    sea = np.zeros((4, 5), dtype=bool)
    sea[:, 0] = True
    depth[:, sea] = 2.58
    depth[1, 2, 3] = 0.45
    assert runner._land_peak_m(depth, None) == pytest.approx(2.58)
    assert runner._land_peak_m(depth, sea) == pytest.approx(0.45)
    assert runner._land_peak_m(depth, np.ones((4, 5), dtype=bool)) == 0.0
    assert runner._land_peak_m(np.zeros((0, 4, 5)), sea) == 0.0

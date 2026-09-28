"""The physics check's crop carries the city's sea (``POST /v1/whatif/physics-check``, P7.8).

The Twin reads the sea off the terrain, and the rule it applies depends on whether a raster is
there at all: with one, the tide is imposed on the sea cells alone, rain on them is not booked,
and a node standing on one exchanges nothing; without one, the tidal outfalls' cells are the sea.
The crop used to be a plain ``TerrainGrid``, so a window cut from a coastal city ran the second
rule - its sea ponded rain as land, and a tidal outfall on the land behind the wall became a
Dirichlet sea cell.

These run on a synthetic coast small enough to reason about by hand and need no city build.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest
from varuna_api.routers.whatif import _crop, _crop_sea_notes, _run_crop
from varuna_schemas.constants import IST
from varuna_twin.city import BOUNDARY_TIDAL, CityTerrain
from varuna_twin.drain1d import BOUNDARY_FREE
from varuna_twin.types import DrainNetwork, TerrainGrid, TideSeries

RES = 30.0
SHAPE = (24, 24)
SEA_COLS = 5
"""Columns 0-4 are open sea at -1 m; the land rises gently eastward from 2 m."""

T0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)


def _coast(*, sea: bool = True) -> CityTerrain:
    z = np.empty(SHAPE, dtype=np.float64)
    z[:, :SEA_COLS] = -1.0
    z[:, SEA_COLS:] = 2.0 + 0.02 * np.arange(SHAPE[1] - SEA_COLS)[None, :]
    mask = np.zeros(SHAPE, dtype=bool)
    mask[:, :SEA_COLS] = True
    return CityTerrain(
        z=z,
        manning_n=np.full(SHAPE, 0.03),
        blocked=np.zeros(SHAPE, dtype=bool),
        imperviousness=np.full(SHAPE, 0.7),
        cn=np.full(SHAPE, 95.0),
        res_m=RES,
        crs="EPSG:32643",
        transform=(RES, 0.0, 300000.0, 0.0, -RES, 2100000.0),
        sea=mask if sea else None,
    )


def _chain(terrain: TerrainGrid, cols: list[int], *, row: int = 12, last: int) -> DrainNetwork:
    """A drain line along one row through ``cols``, draining toward the last node."""
    n = len(cols)
    rows = np.full(n, row, dtype=np.int32)
    cells = np.asarray(cols, dtype=np.int32)
    ground = np.asarray(terrain.z, dtype=np.float64)[rows, cells]
    diameter = np.full(n - 1, 0.6)
    area = np.pi * (diameter / 2.0) ** 2
    r_h = diameter / 4.0
    manning = np.full(n - 1, 0.013)
    boundary = np.zeros(n, dtype=np.int8)
    boundary[-1] = last
    # A tidal outfall is flap-gated, as the city build gates them, so the stage cannot push up
    # the pipe and surcharge onto the land around it; that is the Twin's own test, not this one.
    flap = np.zeros(n, dtype=bool)
    flap[-1] = last == BOUNDARY_TIDAL
    return DrainNetwork(
        node_ids=tuple(f"N{i:02d}" for i in range(n)),
        z_ground=ground,
        z_invert=ground - 1.5,
        storage_area=np.full(n, 1.0),
        inlet_length=np.full(n, 0.6),
        inlet_area=np.full(n, 0.04),
        kappa=np.zeros(n),
        boundary=boundary,
        flap_gate=flap,
        cell_row=rows,
        cell_col=cells,
        edge_ids=tuple(f"E{i:02d}" for i in range(n - 1)),
        from_node=np.arange(n - 1, dtype=np.int32),
        to_node=np.arange(1, n, dtype=np.int32),
        length=np.full(n - 1, RES),
        area=area,
        hydraulic_radius=r_h,
        diameter=diameter,
        edge_manning_n=manning,
        q_full=(1.0 / manning) * area * np.cbrt(np.square(r_h)) * np.sqrt(0.005),
        beta=np.full(n - 1, 0.15),
    )


def _steady(stage: float, steps: int) -> TideSeries:
    times = tuple(T0 + timedelta(minutes=5 * k) for k in range(steps + 1))
    return TideSeries(times=times, stage_m=np.full(len(times), stage), source="illustrative")


def _legacy(crop: TerrainGrid) -> TerrainGrid:
    """The crop exactly as `_crop` built it before it carried the sea: a plain grid, no raster."""
    return TerrainGrid(
        z=crop.z,
        manning_n=crop.manning_n,
        blocked=crop.blocked,
        imperviousness=crop.imperviousness,
        cn=crop.cn,
        res_m=crop.res_m,
        crs=crop.crs,
        transform=crop.transform,
    )


def _assert_same_run(a, b) -> None:
    for name in ("depth_m", "head_m", "q_surcharge", "edge_flow"):
        np.testing.assert_array_equal(getattr(a, name), getattr(b, name), err_msg=name)
    assert a.mass_balance == b.mass_balance


def test_a_crop_that_holds_sea_clamps_it_and_its_sea_inlets_take_nothing() -> None:
    """Two nodes of the line stand on the sea under 1.5 m of it. Their inlets are what the Twin
    keeps out of the exchange, so no water enters the pipes and the land stays dry."""
    terrain = _coast()
    network = _chain(terrain, list(range(3, 16)), last=BOUNDARY_FREE)
    crop, crop_net, (r0, r1, c0, c1) = _crop(terrain, network, 12, 8, 7)

    assert isinstance(crop, CityTerrain)
    assert crop.sea is not None
    np.testing.assert_array_equal(crop.sea, terrain.sea[r0:r1, c0:c1])
    sea = crop.sea
    assert sea.any() and (~sea).any(), "the window must straddle the shore to test anything"
    assert (crop_net.cell_row >= 0).all(), "every node of the line is inside the window"
    assert int(sea[crop_net.cell_row, crop_net.cell_col].sum()) == 2
    # and the response says so, in the counts the window actually holds
    (note,) = _crop_sea_notes(crop, crop_net)
    assert note.startswith(f"{int(sea.sum()):,} cells of the window are the city's sea")
    assert "the 2 manholes standing on them exchange no water" in note

    steps = 3
    run = _run_crop(crop, crop_net, crop_net.beta, np.zeros(steps), T0, _steady(0.5, steps))
    # the tide is imposed on every sea cell of the window: 0.5 m over a -1 m bed
    np.testing.assert_allclose(run.depth_m[:, sea], 1.5, atol=1e-9)
    assert float(run.depth_m[:, ~sea].max()) == 0.0
    # and the two inlets standing in it took nothing: no flow anywhere, every head at its invert
    assert float(np.abs(run.edge_flow).max()) == 0.0
    np.testing.assert_array_equal(run.head_m, np.broadcast_to(crop_net.z_invert, run.head_m.shape))
    assert run.mass_balance.error_fraction < 1e-3

    # The crop this replaced had no sea at all: the same window ran it as dry land.
    old = _run_crop(
        _legacy(crop), crop_net, crop_net.beta, np.zeros(steps), T0, _steady(0.5, steps)
    )
    assert float(old.depth_m[:, sea].max()) == 0.0


def test_rain_on_the_crop_s_sea_is_the_sea_s_not_ponded_as_land() -> None:
    """With rain and the tide below the sea bed, the old crop ponded the rain on its sea cells;
    the crop that carries the raster holds them at the stage, which here is dry."""
    terrain = _coast()
    network = _chain(terrain, list(range(6, 16)), last=BOUNDARY_FREE)
    crop, crop_net, _ = _crop(terrain, network, 12, 8, 7)
    sea = crop.sea
    assert sea is not None and sea.any()

    steps = 3
    rain = np.full(steps, 60.0)
    tide = _steady(-2.0, steps)
    run = _run_crop(crop, crop_net, crop_net.beta, rain, T0, tide)
    old = _run_crop(_legacy(crop), crop_net, crop_net.beta, rain, T0, tide)
    assert float(old.depth_m[:, sea].min()) > 0.0, "the old crop should pond rain on its sea"
    assert float(run.depth_m[:, sea].max()) == 0.0


def test_a_window_without_sea_does_not_turn_a_tidal_outfall_into_sea() -> None:
    """MUM-N049187's case in a crop: a tidal outfall on land at 2 m, one cell in from the sea,
    under a 2.2 m stage. The window holds no sea cell. The city has a coastline, so the crop
    carries an empty mask rather than none, and the outfall's cell stays land: its head is the
    stage and nothing else. With no mask the old rule made that cell a sea cell and flooded it."""
    terrain = _coast()
    cols = list(range(16, SEA_COLS, -1))  # east to west, the tidal outfall at column 6
    network = _chain(terrain, cols, last=BOUNDARY_TIDAL)
    crop, crop_net, (_, _, c0, _) = _crop(terrain, network, 12, 12, 6)
    assert c0 == SEA_COLS + 1
    assert crop.sea is not None and not crop.sea.any()

    steps = 3
    tide = _steady(2.2, steps)
    run = _run_crop(crop, crop_net, crop_net.beta, np.zeros(steps), T0, tide)
    assert float(run.depth_m.max()) == 0.0

    old = _run_crop(_legacy(crop), crop_net, crop_net.beta, np.zeros(steps), T0, tide)
    assert float(old.depth_m.max()) > 0.0, "the old crop should flood the outfall's cell"


@pytest.mark.parametrize("city_has_sea", [True, False])
def test_a_crop_without_sea_runs_exactly_as_the_old_crop(city_has_sea: bool) -> None:
    """Away from the coast, and in a city built before the sea step, nothing changes: the same
    depths, heads, flows and ledger, bit for bit."""
    terrain = _coast(sea=city_has_sea)
    network = _chain(terrain, list(range(10, 22)), last=BOUNDARY_FREE)
    crop, crop_net, _ = _crop(terrain, network, 12, 17, 6)
    if city_has_sea:
        assert crop.sea is not None and not crop.sea.any()
    else:
        assert crop.sea is None
    assert _crop_sea_notes(crop, crop_net) == [], "a window with no sea adds nothing to the notes"

    steps = 4
    rain = np.array([20.0, 80.0, 40.0, 10.0])
    tide = _steady(0.5, steps)
    run = _run_crop(crop, crop_net, crop_net.beta, rain, T0, tide)
    old = _run_crop(_legacy(crop), crop_net, crop_net.beta, rain, T0, tide)
    assert float(run.depth_m.max()) > 0.0, "a dry run would make the equality trivial"
    assert float(np.abs(run.edge_flow).max()) > 0.0
    _assert_same_run(run, old)

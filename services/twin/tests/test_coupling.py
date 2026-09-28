"""Unit tests for the coupling module (SPEC.md 11.5, Phase 4 P4.4 and P4.5).

Tests:
- Inlet capture matches Appendix A formulae
- Surcharge pushes water onto the street
- κ (kappa) reduces capture proportionally
- Exchange conserves water (no creation or destruction at the interface)
"""

from __future__ import annotations

import numpy as np
import pytest
from varuna_twin.coupling import (
    ORIFICE_CD,
    SURCHARGE_CD,
    WEIR_CD,
    compute_exchange,
)
from varuna_twin.drain1d import BOUNDARY_FREE, prepare
from varuna_twin.types import GRAVITY, DrainNetwork


def _coupled_network(
    *,
    n_nodes: int = 3,
    kappa: float = 0.0,
    beta: float = 0.0,
    grid_shape: tuple[int, int] = (5, 5),
) -> tuple[DrainNetwork, np.ndarray, np.ndarray]:
    """A small drain network sitting on a 2D grid.

    Returns the network, surface_h (depth), and surface_z (elevation).
    Node 0 is at cell (1,1), node 1 at (2,2), node 2 (outfall) at (3,3).
    """
    n_edges = n_nodes - 1
    z_ground = np.array([5.0, 4.9, 4.8][:n_nodes], dtype=np.float64)
    z_invert = z_ground - 1.5

    diameter = 0.6
    area = np.pi * (diameter / 2) ** 2
    r_h = diameter / 4
    manning_n = 0.013
    s = 0.005
    q_full = (1.0 / manning_n) * area * r_h ** (2.0 / 3.0) * np.sqrt(s)
    length = 40.0

    cell_rows = np.array([1, 2, 3][:n_nodes], dtype=np.int32)
    cell_cols = np.array([1, 2, 3][:n_nodes], dtype=np.int32)

    boundary = np.zeros(n_nodes, dtype=np.int8)
    boundary[-1] = BOUNDARY_FREE

    network = DrainNetwork(
        node_ids=tuple(f"N{i:04d}" for i in range(n_nodes)),
        z_ground=z_ground,
        z_invert=z_invert,
        storage_area=np.full(n_nodes, 1.0, dtype=np.float64),
        inlet_length=np.full(n_nodes, 0.6, dtype=np.float64),
        inlet_area=np.full(n_nodes, 0.04, dtype=np.float64),
        kappa=np.full(n_nodes, kappa, dtype=np.float64),
        boundary=boundary,
        flap_gate=np.zeros(n_nodes, dtype=bool),
        cell_row=cell_rows,
        cell_col=cell_cols,
        edge_ids=tuple(f"E{i:04d}" for i in range(n_edges)),
        from_node=np.arange(n_edges, dtype=np.int32),
        to_node=np.arange(1, n_nodes, dtype=np.int32),
        length=np.full(n_edges, length, dtype=np.float64),
        area=np.full(n_edges, area, dtype=np.float64),
        hydraulic_radius=np.full(n_edges, r_h, dtype=np.float64),
        diameter=np.full(n_edges, diameter, dtype=np.float64),
        edge_manning_n=np.full(n_edges, manning_n, dtype=np.float64),
        q_full=np.full(n_edges, q_full, dtype=np.float64),
        beta=np.full(n_edges, beta, dtype=np.float64),
    )

    # 2D grid: flat at z=5.0
    surface_z = np.full(grid_shape, 5.0, dtype=np.float64)
    # Set the z at node cells to match the network
    for i in range(n_nodes):
        surface_z[cell_rows[i], cell_cols[i]] = z_ground[i]

    surface_h = np.zeros(grid_shape, dtype=np.float64)

    return network, surface_h, surface_z


class TestInletCapture:
    """Inlet capture matches Appendix A."""

    def test_dry_street_produces_no_capture(self) -> None:
        network, surface_h, surface_z = _coupled_network()
        solver = prepare(network)
        head = np.asarray(network.z_invert, dtype=np.float64).copy()

        exchange = compute_exchange(
            surface_h=surface_h,
            surface_z=surface_z,
            drain_head=head,
            network=network,
            solver=solver,
            cell_area_m2=100.0,
        )

        assert np.all(exchange.q_inlet_node == 0.0)
        assert np.all(exchange.q_inlet_cell == 0.0)

    def test_wet_street_captures_water(self) -> None:
        network, surface_h, surface_z = _coupled_network()
        solver = prepare(network)
        head = np.asarray(network.z_invert, dtype=np.float64).copy()

        # Put 10 cm of water on the cells where nodes sit
        surface_h[1, 1] = 0.1
        surface_h[2, 2] = 0.15

        exchange = compute_exchange(
            surface_h=surface_h,
            surface_z=surface_z,
            drain_head=head,
            network=network,
            solver=solver,
            cell_area_m2=100.0,
        )

        # Interior nodes should capture water
        assert exchange.q_inlet_node[0] > 0.0  # node 0 at (1,1)
        assert exchange.q_inlet_node[1] > 0.0  # node 1 at (2,2)

        # Outfall (fixed head) should not capture
        assert exchange.q_inlet_node[2] == 0.0

    def test_capture_formula_matches_appendix_a(self) -> None:
        """Check the weir and orifice formulae at a known depth."""
        network, surface_h, surface_z = _coupled_network(kappa=0.0)
        solver = prepare(network)
        head = np.asarray(network.z_invert, dtype=np.float64).copy()

        h = 0.2  # 20 cm of water
        surface_h[1, 1] = h

        exchange = compute_exchange(
            surface_h=surface_h,
            surface_z=surface_z,
            drain_head=head,
            network=network,
            solver=solver,
            cell_area_m2=900.0,
        )

        # Expected from Appendix A
        L = 0.6  # inlet_length
        A_o = 0.04  # inlet_area
        q_weir = WEIR_CD * L * h**1.5
        q_orifice = ORIFICE_CD * A_o * np.sqrt(2.0 * GRAVITY * h)
        q_expected = min(q_weir, q_orifice)

        # The actual capture may be limited by Q_avail, so it should be <= q_expected
        assert exchange.q_inlet_node[0] <= q_expected + 1e-10
        assert exchange.q_inlet_node[0] > 0.0


class TestKappaReducesCapture:
    """κ (clogging) reduces inlet capture proportionally."""

    def test_clogged_inlet_captures_less(self) -> None:
        # Unclogged
        net0, h0, z0 = _coupled_network(kappa=0.0)
        solver0 = prepare(net0)
        head0 = np.asarray(net0.z_invert, dtype=np.float64).copy()
        h0[1, 1] = 0.2

        ex0 = compute_exchange(h0, z0, head0, net0, solver0, 900.0)

        # 50% clogged
        net50, h50, z50 = _coupled_network(kappa=0.5)
        solver50 = prepare(net50)
        head50 = np.asarray(net50.z_invert, dtype=np.float64).copy()
        h50[1, 1] = 0.2

        ex50 = compute_exchange(h50, z50, head50, net50, solver50, 900.0)

        # The clogged inlet should capture about half as much
        if ex0.q_inlet_node[0] > 0:
            ratio = ex50.q_inlet_node[0] / ex0.q_inlet_node[0]
            assert ratio == pytest.approx(0.5, rel=0.1)


class TestSurcharge:
    """Surcharge pushes water onto the street."""

    def test_surcharged_node_pushes_water_to_cell(self) -> None:
        network, surface_h, surface_z = _coupled_network()
        solver = prepare(network)

        # Force the head above ground level at node 0
        head = np.asarray(network.z_invert, dtype=np.float64).copy()
        head[0] = network.z_ground[0] + 0.5  # 50 cm above ground

        exchange = compute_exchange(
            surface_h=surface_h,
            surface_z=surface_z,
            drain_head=head,
            network=network,
            solver=solver,
            cell_area_m2=900.0,
        )

        # Surcharge should be positive at node 0
        assert exchange.q_surcharge_node[0] > 0.0

        # The cell should receive the surcharge
        assert exchange.q_surcharge_cell[1, 1] > 0.0

    def test_no_surcharge_when_head_below_ground(self) -> None:
        network, surface_h, surface_z = _coupled_network()
        solver = prepare(network)
        head = np.asarray(network.z_invert, dtype=np.float64).copy()

        exchange = compute_exchange(
            surface_h=surface_h,
            surface_z=surface_z,
            drain_head=head,
            network=network,
            solver=solver,
            cell_area_m2=900.0,
        )

        # No surcharge when heads are well below ground
        assert np.all(exchange.q_surcharge_node == 0.0)

    def test_surcharge_formula_matches_appendix_a(self) -> None:
        """Q_surch = 0.6 * A_m * sqrt(2g(H - z_g - h)) - at a sync short enough to use it.

        The orifice equation gives the rate water *could* leave a manhole; the flux limiter
        SPEC.md 11.5 requires ("fluxes frozen and limited so no cell goes negative") caps it at
        the volume that is actually there to leave. Which of the two binds is a race between
        ``0.6*A*sqrt(2 g e)`` and ``e*A/dt``, and the manhole area cancels: the orifice only wins
        when ``dt <= sqrt(e) / 2.657``. At half a metre of excess head that is 0.19 s, so at the
        5 s sync of a real run the limiter binds essentially always and this equation sets an
        upper bound rather than the answer.

        That is worth stating plainly rather than hiding behind a passing test, so this one uses
        a 0.15 s interval - short enough for the formula itself to be what is checked - and
        ``test_surcharge_cannot_emit_more_than_the_node_holds`` checks the limiter at the
        interval a run actually uses.
        """
        network, surface_h, surface_z = _coupled_network()
        solver = prepare(network)

        excess = 0.5  # 50 cm above the street surface
        head = np.asarray(network.z_invert, dtype=np.float64).copy()
        head[0] = surface_z[1, 1] + surface_h[1, 1] + excess  # z_g + h + excess

        exchange = compute_exchange(
            surface_h=surface_h,
            surface_z=surface_z,
            drain_head=head,
            network=network,
            solver=solver,
            cell_area_m2=900.0,
            sync_s=0.15,
        )

        a_m = float(network.storage_area[0])
        q_expected = SURCHARGE_CD * a_m * np.sqrt(2.0 * GRAVITY * excess)
        assert exchange.q_surcharge_node[0] == pytest.approx(q_expected, rel=1e-4)
        # And the limiter really was slack here, which is what makes this a test of the formula.
        assert q_expected < excess * a_m / 0.15

    def test_surcharge_cannot_emit_more_than_the_node_holds(self) -> None:
        """The cap that keeps the coupled run mass-balanced.

        Without it the city run manufactured water on a spectacular scale: 39,801 nodes each
        emitting an unbacked orifice flow every 5 s turned 861,096 m3 of rain into 196,951,114 m3
        of standing water, a 228x gain, with 28 m deep streets. The cap is the volume that would
        bring the node's head down to the street surface, which is where the exchange stops
        anyway - below it there is no excess head left to push with.
        """
        network, surface_h, surface_z = _coupled_network()
        solver = prepare(network)

        excess = 0.5
        network.storage_area[:] = 1.0  # a 1 m2 manhole holds 0.5 m3 above the street
        head = np.asarray(network.z_invert, dtype=np.float64).copy()
        head[0] = surface_z[1, 1] + surface_h[1, 1] + excess

        sync_s = 5.0
        exchange = compute_exchange(
            surface_h=surface_h,
            surface_z=surface_z,
            drain_head=head,
            network=network,
            solver=solver,
            cell_area_m2=900.0,
            sync_s=sync_s,
        )

        orifice = SURCHARGE_CD * 1.0 * np.sqrt(2.0 * GRAVITY * excess)
        held_m3 = excess * 1.0
        assert exchange.q_surcharge_node[0] == pytest.approx(held_m3 / sync_s, rel=1e-9)
        assert exchange.q_surcharge_node[0] < orifice, "the cap must bind here"
        # Over the interval it emits exactly what it had, and not a drop more.
        assert exchange.q_surcharge_node[0] * sync_s == pytest.approx(held_m3, rel=1e-9)


class TestExchangeConservation:
    """No water is created or destroyed at the coupling interface."""

    def test_cell_rates_sum_to_node_rates(self) -> None:
        """The per-cell rates scattered from the nodes must sum to the same total."""
        network, surface_h, surface_z = _coupled_network()
        solver = prepare(network)
        head = np.asarray(network.z_invert, dtype=np.float64).copy()
        surface_h[1, 1] = 0.15
        surface_h[2, 2] = 0.10

        cell_area = 900.0
        exchange = compute_exchange(
            surface_h=surface_h,
            surface_z=surface_z,
            drain_head=head,
            network=network,
            solver=solver,
            cell_area_m2=cell_area,
        )

        # Total inlet from node view vs cell view
        node_total = float(np.sum(exchange.q_inlet_node))
        cell_total = float(np.sum(exchange.q_inlet_cell)) * cell_area
        assert cell_total == pytest.approx(node_total, rel=1e-8)

    def test_coupling_fluxes_contract_type(self) -> None:
        """as_coupling_fluxes returns the correct contract type."""
        network, surface_h, surface_z = _coupled_network()
        solver = prepare(network)
        head = np.asarray(network.z_invert, dtype=np.float64).copy()

        exchange = compute_exchange(surface_h, surface_z, head, network, solver, 900.0)

        cf = exchange.as_coupling_fluxes()
        assert np.array_equal(cf.q_inlet, exchange.q_inlet_node)
        assert np.array_equal(cf.q_surcharge, exchange.q_surcharge_node)


class TestBlockedCells:
    """A node under a building exchanges nothing (task P4.5).

    `swe2d._update_depth` sets a blocked cell's depth to zero and `continue`s *before* it tallies
    rain, surcharge or capture, so a surcharge scattered onto a building is deleted without
    appearing in any ledger - the 2D solver's own audit still closes, because the water was never
    counted as having arrived. Measured on the 08:40 cycle of 2 July 2019 on the Mumbai graph,
    that was 5,185.4 m3 destroyed over three hours, 0.46 % of all surcharge; the building mask is
    densest over exactly the wards where the inlets are (ADR-0039). So the exchange refuses the
    node rather than the surface swallowing it.
    """

    def _surcharged(self) -> tuple[DrainNetwork, np.ndarray, np.ndarray, np.ndarray]:
        network, surface_h, surface_z = _coupled_network()
        head = np.asarray(network.z_invert, dtype=np.float64).copy()
        head[0] = network.z_ground[0] + 0.5  # node 0 half a metre over its street
        return network, surface_h, surface_z, head

    @pytest.mark.parametrize("compiled", [False, True])
    def test_a_node_under_a_building_neither_captures_nor_surcharges(self, compiled: bool) -> None:
        network, surface_h, surface_z, head = self._surcharged()
        solver = prepare(network)
        surface_h[2, 2] = 0.3  # node 1's street is wet, so it would otherwise capture

        free = compute_exchange(
            surface_h=surface_h,
            surface_z=surface_z,
            drain_head=head,
            network=network,
            solver=solver,
            cell_area_m2=900.0,
            sync_s=5.0,
            compiled=compiled,
        )
        assert free.q_surcharge_node[0] > 0.0, "the fixture must surcharge to be worth testing"
        assert free.q_inlet_node[1] > 0.0, "the fixture must capture to be worth testing"

        blocked = np.zeros(surface_h.shape, dtype=bool)
        blocked[1, 1] = True  # node 0's cell
        blocked[2, 2] = True  # node 1's cell
        walled = compute_exchange(
            surface_h=surface_h,
            surface_z=surface_z,
            drain_head=head,
            network=network,
            solver=solver,
            cell_area_m2=900.0,
            sync_s=5.0,
            compiled=compiled,
            blocked=blocked,
        )
        assert walled.q_surcharge_node[0] == 0.0
        assert walled.q_inlet_node[1] == 0.0
        assert not walled.q_surcharge_cell.any()
        assert not walled.q_inlet_cell.any()

    def test_the_kernel_and_numpy_paths_agree_on_a_blocked_grid(self) -> None:
        """The parity `coupling_kernel` claims, exercised with the mask rather than without it."""
        network, surface_h, surface_z, head = self._surcharged()
        solver = prepare(network)
        surface_h[2, 2] = 0.3
        blocked = np.zeros(surface_h.shape, dtype=bool)
        blocked[1, 1] = True

        kwargs = dict(
            surface_h=surface_h,
            surface_z=surface_z,
            drain_head=head,
            network=network,
            solver=solver,
            cell_area_m2=900.0,
            sync_s=5.0,
            blocked=blocked,
        )
        numpy_path = compute_exchange(compiled=False, **kwargs)
        kernel_path = compute_exchange(compiled=True, **kwargs)
        for name in ("q_inlet_node", "q_surcharge_node", "q_inlet_cell", "q_surcharge_cell"):
            np.testing.assert_allclose(
                getattr(numpy_path, name),
                getattr(kernel_path, name),
                rtol=0.0,
                atol=0.0,
                err_msg=name,
            )

    @pytest.mark.parametrize("compiled", [False, True])
    def test_passing_no_mask_is_the_same_as_an_empty_one(self, compiled: bool) -> None:
        """`blocked=None` must not be a third behaviour; it is the all-False mask."""
        network, surface_h, surface_z, head = self._surcharged()
        solver = prepare(network)
        surface_h[2, 2] = 0.3
        kwargs = dict(
            surface_h=surface_h,
            surface_z=surface_z,
            drain_head=head,
            network=network,
            solver=solver,
            cell_area_m2=900.0,
            sync_s=5.0,
            compiled=compiled,
        )
        without = compute_exchange(blocked=None, **kwargs)
        empty = compute_exchange(blocked=np.zeros(surface_h.shape, dtype=bool), **kwargs)
        np.testing.assert_array_equal(without.q_surcharge_node, empty.q_surcharge_node)
        np.testing.assert_array_equal(without.q_inlet_cell, empty.q_inlet_cell)

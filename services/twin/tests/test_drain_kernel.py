"""The compiled drain kernel must be the NumPy step, exactly (task P4.6).

`drain1d.step` is the readable statement of SPEC.md 11.4 and Appendix A; `drain_kernel` is the
same arithmetic fused into three passes so a three-hour city run fits its budget. A fused kernel is
only worth having if it is the *same* solver, so these tests run both paths over the same forcing
and assert the heads, the flows and every reported volume agree - on a network that exercises the
three reductions the step applies in order: flap gates, the stability limiter and the supply check.
"""

from __future__ import annotations

from dataclasses import replace

import numba
import numpy as np
import pytest

# The same linear network the rest of the drain tests use, with the knobs this file needs.
#
# Imported bare rather than as `tests.test_drain1d`: there is no `__init__.py` here, so pytest
# prepends this directory to `sys.path` and the module is top-level. The qualified form only
# worked when pytest was pointed at `services/twin`; from the repository root `tests` resolves to
# the repo's own `tests/` package instead, and `uv run pytest` - the SPEC.md 14 gate - died on
# a collection error before running a single test.
from test_drain1d import _init_state, _simple_network  # type: ignore[import-not-found]
from varuna_twin.drain1d import (
    BOUNDARY_FREE,
    DrainNetwork,
    DrainState,
    edge_colouring,
    prepare,
    simulate,
)

TOLERANCE_M = 1e-9
"""Heads agree to a nanometre. The two paths do the same float operations in the same order, so
the only difference should be whether an intermediate stayed in a register."""


def _both_paths(
    network: DrainNetwork, **kwargs: object
) -> tuple[DrainState, DrainState, object, object]:
    """Run `simulate` twice over the same inputs: NumPy, then the kernel."""
    numpy_state = _init_state(network)
    kernel_state = _init_state(network)
    numpy_solver = prepare(network)
    kernel_solver = prepare(network)

    numpy_report = simulate(numpy_solver, numpy_state, compiled=False, **kwargs)  # type: ignore[arg-type]
    kernel_report = simulate(kernel_solver, kernel_state, compiled=True, **kwargs)  # type: ignore[arg-type]
    return numpy_state, kernel_state, numpy_report, kernel_report


def _assert_same(
    numpy_state: DrainState, kernel_state: DrainState, numpy_report, kernel_report
) -> None:
    np.testing.assert_allclose(kernel_state.head, numpy_state.head, atol=TOLERANCE_M, rtol=0)
    np.testing.assert_allclose(kernel_state.flow, numpy_state.flow, atol=TOLERANCE_M, rtol=0)
    for field in ("inlet_m3", "surcharge_m3", "sink_m3", "boundary_m3", "stored_end_m3"):
        assert getattr(kernel_report, field) == pytest.approx(
            getattr(numpy_report, field), abs=1e-9
        ), field
    assert kernel_report.limited_edges == numpy_report.limited_edges
    assert kernel_report.n_steps == numpy_report.n_steps


def test_kernel_matches_numpy_on_a_free_draining_network() -> None:
    network = _simple_network(n_nodes=6)
    inlet = np.zeros(network.n_nodes)
    inlet[0] = 0.05  # 50 l/s into the head of the line
    _assert_same(*_both_paths(network, duration_s=60.0, dt_s=1.0, q_inlet=inlet))


def test_kernel_matches_numpy_with_a_tide_locked_outfall() -> None:
    """The demo's reversed-flow case: the sea above the trunk invert pushes water back inland."""
    network = _simple_network(n_nodes=6, has_tidal_outfall=True)
    inlet = np.zeros(network.n_nodes)
    inlet[0] = 0.02
    # Well above the outfall invert, so the last edge runs backwards.
    stage = float(network.z_invert[-1]) + 1.2
    _assert_same(
        *_both_paths(network, duration_s=90.0, dt_s=1.0, q_inlet=inlet, tide_stage_m=stage)
    )


def test_kernel_matches_numpy_with_a_flap_gate() -> None:
    network = _simple_network(n_nodes=5, has_tidal_outfall=True, flap_gate=True)
    inlet = np.zeros(network.n_nodes)
    inlet[0] = 0.02
    stage = float(network.z_invert[-1]) + 1.2
    _assert_same(
        *_both_paths(network, duration_s=60.0, dt_s=1.0, q_inlet=inlet, tide_stage_m=stage)
    )


def test_kernel_matches_numpy_when_a_node_runs_out_of_water() -> None:
    """The supply check: a node asked to surcharge more than it holds scales every outflow."""
    network = _simple_network(n_nodes=5)
    surcharge = np.zeros(network.n_nodes)
    # Far more than a 1 m2 manhole with a few centimetres in it can give.
    surcharge[1] = 5.0
    inlet = np.zeros(network.n_nodes)
    inlet[0] = 0.01
    _assert_same(
        *_both_paths(network, duration_s=30.0, dt_s=1.0, q_inlet=inlet, q_surcharge=surcharge)
    )


def test_kernel_matches_numpy_when_nodes_pressurise() -> None:
    """Above the crown the Preissmann slot takes over, on both the storage curve and dH/dt."""
    network = _simple_network(n_nodes=5, beta=0.6)  # blocked, so it backs up
    inlet = np.zeros(network.n_nodes)
    inlet[0] = 0.4  # enough to fill and pressurise
    _assert_same(*_both_paths(network, duration_s=120.0, dt_s=1.0, q_inlet=inlet))


def test_kernel_conserves_what_the_numpy_path_conserves() -> None:
    """A closed network gains exactly what is put into it (SPEC.md 11.4's conservation test)."""
    network = _simple_network(n_nodes=5)
    # No outfall: make the last node an ordinary manhole so nothing can leave.
    boundary = np.zeros(network.n_nodes, dtype=np.int8)
    closed = replace(network, boundary=boundary)

    inlet = np.zeros(closed.n_nodes)
    inlet[0] = 0.01
    state = _init_state(closed)
    solver = prepare(closed)
    report = simulate(solver, state, duration_s=120.0, dt_s=1.0, q_inlet=inlet, compiled=True)

    gained = report.stored_end_m3 - report.stored_start_m3
    assert gained == pytest.approx(report.inlet_m3, rel=1e-6)
    assert report.boundary_m3 == pytest.approx(0.0, abs=1e-12)


def test_the_compiled_path_is_the_default() -> None:
    """`simulate` compiles unless asked not to: the budget is the reason the kernel exists."""
    network = _simple_network(n_nodes=4)
    state = _init_state(network)
    solver = prepare(network)
    # Would raise if the default dispatched to a path that did not exist.
    report = simulate(solver, state, duration_s=5.0, dt_s=1.0)
    assert report.n_steps == 5


# ============================================================================ P4.6: colouring


class TestEdgeColouring:
    """The colouring the parallel kernel rests on (task P4.6).

    Correctness of the parallel scatter reduces entirely to one property: inside a colour, no
    node is written twice. Everything else - how many colours there are, how big they are, which
    edge got which - is a performance question. So that property is asserted directly, on a graph
    with branches rather than a chain, because a chain would satisfy it by accident.
    """

    def _branching(self, n_leaves: int = 500) -> DrainNetwork:
        """A star of chains: one trunk node with `n_leaves` edges on it, plus a chain each."""
        network = _simple_network(n_nodes=3)
        n_nodes = 1 + 2 * n_leaves
        from_node = np.empty(2 * n_leaves, dtype=np.int32)
        to_node = np.empty(2 * n_leaves, dtype=np.int32)
        for i in range(n_leaves):
            from_node[2 * i] = 1 + 2 * i
            to_node[2 * i] = 2 + 2 * i
            from_node[2 * i + 1] = 2 + 2 * i
            to_node[2 * i + 1] = 0  # every chain ends at the shared outfall
        z_ground = np.linspace(6.0, 4.0, n_nodes)
        return replace(
            network,
            node_ids=tuple(f"N{i:05d}" for i in range(n_nodes)),
            z_ground=z_ground,
            z_invert=z_ground - 1.5,
            storage_area=np.full(n_nodes, 1.0),
            inlet_length=np.full(n_nodes, 0.6),
            inlet_area=np.full(n_nodes, 0.04),
            kappa=np.zeros(n_nodes),
            boundary=np.concatenate(([BOUNDARY_FREE], np.zeros(n_nodes - 1, dtype=np.int8))).astype(
                np.int8
            ),
            flap_gate=np.zeros(n_nodes, dtype=bool),
            cell_row=np.full(n_nodes, -1, dtype=np.int32),
            cell_col=np.full(n_nodes, -1, dtype=np.int32),
            edge_ids=tuple(f"E{i:05d}" for i in range(2 * n_leaves)),
            from_node=from_node,
            to_node=to_node,
            length=np.full(2 * n_leaves, 40.0),
            area=np.full(2 * n_leaves, float(network.area[0])),
            hydraulic_radius=np.full(2 * n_leaves, float(network.hydraulic_radius[0])),
            diameter=np.full(2 * n_leaves, float(network.diameter[0])),
            edge_manning_n=np.full(2 * n_leaves, float(network.edge_manning_n[0])),
            q_full=np.full(2 * n_leaves, float(network.q_full[0])),
            beta=np.zeros(2 * n_leaves),
        )

    def test_no_two_edges_in_a_colour_touch_the_same_node(self) -> None:
        network = self._branching()
        colour_edges, colour_start = edge_colouring(network)
        from_node = np.asarray(network.from_node)
        to_node = np.asarray(network.to_node)

        assert len(colour_start) > 2, "fixture: a single colour would prove nothing"
        for c in range(len(colour_start) - 1):
            edges = colour_edges[colour_start[c] : colour_start[c + 1]]
            touched = np.concatenate([from_node[edges], to_node[edges]])
            assert len(np.unique(touched)) == len(touched), f"colour {c} writes a node twice"

    def test_every_edge_appears_exactly_once(self) -> None:
        """A permutation, not a selection: a dropped edge would silently stop carrying water."""
        network = self._branching()
        colour_edges, colour_start = edge_colouring(network)
        np.testing.assert_array_equal(np.sort(colour_edges), np.arange(network.n_edges))
        assert int(colour_start[-1]) == network.n_edges


class TestParallelKernel:
    """`step_kernel_parallel` must be `step_kernel`, and must not depend on the thread count.

    The serial kernel is already held to the NumPy step above, so pinning the parallel one to the
    serial one pins all three. Two separate claims, and they are not the same strength:

    * **Thread-count invariance is byte equality**, and it is the one rule 8 rests on: `make bake`
      must produce identical files on a laptop and on CI. Colouring buys exactly this - inside a
      colour no two edges write the same node, so no float addition order depends on the schedule.
      A per-thread reduction would have failed here, which is why it was not used.
    * **Agreement with the serial kernel is to floating-point tolerance.** Measured on the fixture
      below: bit-identical when every colour is parallel, and 3.4e-16 relative on `flow` (1-2 ULP,
      heads identical) when one colour falls to the serial tail. The colour order is the same in
      both; the difference is codegen, because the same tail loop compiled inside a
      `parallel=True` function is not the same machine code as in a serial one. Claiming byte
      equality here would be claiming something about LLVM rather than about the solver.
    """

    def _pressurised_chain(self) -> DrainNetwork:
        """Long enough to colour, with a tide-locked flap-gated outfall and small pipes."""
        return _simple_network(n_nodes=9000, diameter=0.9, has_tidal_outfall=True, flap_gate=True)

    def _run(self, network: DrainNetwork, n_parallel_colours: int) -> tuple[DrainState, object]:
        solver = prepare(network, colour=True)
        assert solver.colour_edges is not None, "fixture: too small to be coloured"
        solver = replace(solver, n_parallel_colours=n_parallel_colours)
        state = _init_state(network)
        # Enough inlet to pressurise, and a surcharge demand the shallow nodes cannot supply, so
        # all three reductions bite: flap gate, stability limiter and supply check.
        q_inlet = np.zeros(network.n_nodes)
        q_inlet[::2] = 2.0
        q_surcharge = np.zeros(network.n_nodes)
        q_surcharge[::11] = 0.5
        report = simulate(
            solver,
            state,
            duration_s=20.0,
            dt_s=1.0,
            q_inlet=q_inlet,
            q_surcharge=q_surcharge,
            tide_stage_m=float(network.z_ground[-1]) + 1.0,
        )
        return state, report

    def test_the_result_does_not_depend_on_how_many_threads_ran(self) -> None:
        """Rule 8's requirement, and the whole reason the scatter is coloured rather than reduced."""
        network = self._pressurised_chain()
        available = numba.config.NUMBA_NUM_THREADS
        if available < 2:
            pytest.skip(f"one thread available; nothing to compare ({available})")

        try:
            numba.set_num_threads(1)
            one, one_report = self._run(network, n_parallel_colours=2)
            numba.set_num_threads(available)
            many, many_report = self._run(network, n_parallel_colours=2)
        finally:
            numba.set_num_threads(available)

        assert one.head.tobytes() == many.head.tobytes()
        assert one.flow.tobytes() == many.flow.tobytes()
        for field in ("inlet_m3", "surcharge_m3", "sink_m3", "boundary_m3", "stored_end_m3"):
            assert getattr(one_report, field) == getattr(many_report, field), field

    def test_the_parallel_kernel_matches_the_serial_one(self) -> None:
        network = self._pressurised_chain()
        serial_state, serial_report = self._run(network, n_parallel_colours=0)
        assert serial_report.limited_edges > 0, (
            "fixture: the stability limiter never bit, so the hardest branch is untested"
        )
        assert serial_report.surcharge_m3 > 0.0, "fixture: nothing surcharged"

        for n_colours in (1, 2):
            state, report = self._run(network, n_parallel_colours=n_colours)
            np.testing.assert_allclose(
                state.head, serial_state.head, atol=TOLERANCE_M, rtol=0, err_msg=str(n_colours)
            )
            np.testing.assert_allclose(
                state.flow, serial_state.flow, atol=0, rtol=1e-12, err_msg=str(n_colours)
            )
            for field in ("inlet_m3", "surcharge_m3", "sink_m3", "boundary_m3", "stored_end_m3"):
                assert getattr(report, field) == pytest.approx(
                    getattr(serial_report, field), rel=1e-12
                ), (field, n_colours)
            assert report.limited_edges == serial_report.limited_edges

    def test_every_colour_parallel_is_bit_identical_to_the_serial_kernel(self) -> None:
        """With no serial tail, the two kernels are the same instructions in the same order.

        This is the case the Mumbai graph does *not* have - 3 of its 22 colours are big enough to
        be worth a launch - so it is here to separate the two sources of difference: reordering
        (none, by construction) from tail codegen (the 1-2 ULP the test above allows).
        """
        network = self._pressurised_chain()
        serial_state, _ = self._run(network, n_parallel_colours=0)
        parallel_state, _ = self._run(network, n_parallel_colours=2)
        assert parallel_state.head.tobytes() == serial_state.head.tobytes()
        assert parallel_state.flow.tobytes() == serial_state.flow.tobytes()

    def test_a_partly_parallel_colouring_still_covers_every_edge(self) -> None:
        """One colour parallel and the rest serial is the Mumbai case: 3 of 22 colours.

        The tail is not an optimisation detail - if the serial loop over the remaining colours
        were mis-bounded, the edges in it would simply stop carrying water and the run would look
        plausible rather than wrong. A dropped half of the chain moves the stored volume by far
        more than the ULP the codegen difference is worth.
        """
        network = self._pressurised_chain()
        whole, whole_report = self._run(network, n_parallel_colours=0)
        split, split_report = self._run(network, n_parallel_colours=1)
        np.testing.assert_allclose(split.flow, whole.flow, atol=0, rtol=1e-12)
        assert split_report.stored_end_m3 == pytest.approx(whole_report.stored_end_m3, rel=1e-12)

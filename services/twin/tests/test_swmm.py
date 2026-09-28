"""The SWMM export and adapter, and the measured gap to ``drain1d`` (SPEC.md task P4.9).

SPEC.md 11.4 calls the prototype's 1D drain model "diffusive-wave-lite" and names PySWMM
dynamic wave as the P1 upgrade behind the same interface. The task's own wording says the test
that matters is the one that runs the same small network through both engines and reports where
they agree and where they do not, because that is the honest answer to "how reduced is your
reduced model?".

So :class:`TestEngineGap` is the deliverable. It does not assert that the engines agree - they
are different equations and they do not - it asserts the *shape* of the disagreement and prints
every number, so a reader gets the measurement rather than a green tick. The numbers it printed
on this machine are quoted in ``varuna_twin.swmm``'s module docstring; if a future SWMM build
moves them, this file is where that shows up first.

The network is the one ``test_drain1d`` already uses: four nodes 40 m apart, a 600 mm circular
pipe at 0.5 %, 1 m2 manholes, a free or tidal outfall at the foot. Small enough to reason about
by hand, and the hand calculation is in :meth:`TestEngineGap.test_normal_depth_hand_check`.

Every test here needs the real engine. ``pyswmm`` is in the dev group and installs on this
machine (2.1.0, SWMM 5.2.4); where it does not, the module is skipped rather than xfailed,
because an absent engine is a missing tool and not a failing claim - ``drain1d`` is the P0
solver and needs nothing (SPEC.md 11.4, section 17).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

# Sibling import: `conftest.py` beside this file puts the directory on `sys.path`, because
# `--import-mode=importlib` puts nothing there and the qualified name `tests.test_drain1d`
# resolves to the repository's own `tests/` package from the root (ADR-0038).
from test_drain1d import _init_state, _simple_network  # type: ignore[import-not-found]
from varuna_twin import drain1d, swmm
from varuna_twin.types import DrainNetwork, DrainState

pytestmark = pytest.mark.skipif(
    not swmm.available(),
    reason="pyswmm is not importable; drain1d is the P0 solver and needs nothing (SPEC.md 11.4)",
)

GRAVITY_FREE_SECONDS = 1800.0
"""Long enough for the 4-node line to reach steady state in both engines.

Measured rather than guessed: at 1800 s both engines' heads are unchanged to five decimals
from their 7200 s values, and ``drain1d``'s flows are within 1e-9 of the 10 L/s forcing."""


def _both(
    tmp_path: Path,
    network: DrainNetwork,
    *,
    seconds: float,
    q_inlet: np.ndarray,
    tide_stage_m: float | None = None,
    initial_head: np.ndarray | None = None,
) -> tuple[DrainState, drain1d.DrainRunReport, DrainState, drain1d.DrainRunReport, float | None]:
    """Run the same forcing through both engines and hand back both states and both audits.

    The two runs share nothing but the network and the forcing arrays, which is the point: any
    difference in the results is a difference between the engines and not between two
    differently-set-up problems.
    """
    state_a = _init_state(network)
    if initial_head is not None:
        state_a.head[:] = initial_head
    report_a = drain1d.simulate(
        drain1d.prepare(network),
        state_a,
        duration_s=seconds,
        dt_s=1.0,
        q_inlet=q_inlet,
        tide_stage_m=tide_stage_m,
    )

    state_b = _init_state(network)
    if initial_head is not None:
        state_b.head[:] = initial_head
    with swmm.prepare(network, state_b, workdir=tmp_path, horizon_s=seconds * 3) as solver:
        report_b = swmm.simulate(
            solver,
            state_b,
            duration_s=seconds,
            dt_s=1.0,
            q_inlet=q_inlet,
            tide_stage_m=tide_stage_m,
        )
    continuity = solver.continuity_error_pct()
    return state_a, report_a, state_b, report_b, continuity


def _inflow_at_head(network: DrainNetwork, rate: float) -> np.ndarray:
    q = np.zeros(network.n_nodes, dtype=np.float64)
    q[0] = rate
    return q


# ============================================================================ the export
class TestExport:
    """:func:`swmm.write_inp` - the "when a SWMM model arrives we import it" claim, reversed."""

    def test_writes_every_element_once(self, tmp_path: Path) -> None:
        network = _simple_network(n_nodes=4)
        report = swmm.write_inp(network, tmp_path / "n.inp")

        assert report.n_junctions == 3
        assert report.n_outfalls == 1
        assert report.n_conduits == 3
        assert report.n_junctions + report.n_outfalls == network.n_nodes
        assert report.lossless, "the plain test network should need no clamping"

        text = report.path.read_text()
        for name in network.node_ids:
            assert f"\n{name:<16}" in text, f"{name} is missing from the .inp"
        for name in network.edge_ids:
            # Once as a conduit and once as a cross-section.
            assert text.count(f"\n{name:<16}") == 2, f"{name} is not written exactly twice"

    def test_the_engine_accepts_what_we_wrote(self, tmp_path: Path) -> None:
        """The only export check that means anything: SWMM parses it and runs.

        A ``.inp`` that looks right and that SWMM rejects is worth nothing to the ministry
        officer SPEC.md 16 imagines opening it, so this is a round trip and not a string
        comparison.
        """
        network = _simple_network(n_nodes=4)
        state = _init_state(network)
        with swmm.prepare(network, state, workdir=tmp_path, horizon_s=600.0) as solver:
            report = swmm.simulate(
                solver, state, duration_s=60.0, dt_s=1.0, q_inlet=_inflow_at_head(network, 0.01)
            )
        assert report.n_steps == 60
        assert np.all(np.isfinite(state.head))
        assert np.all(state.head >= network.z_invert - 1e-9)

    def test_rebuilding_is_byte_identical(self, tmp_path: Path) -> None:
        """Rule 8. Two exports of one network differ in no byte."""
        network = _simple_network(n_nodes=6)
        first = swmm.write_inp(network, tmp_path / "a.inp")
        second = swmm.write_inp(network, tmp_path / "b.inp")
        assert first.path.read_bytes() == second.path.read_bytes()
        assert first.bytes_written == second.bytes_written

    def test_blockage_becomes_roughness_and_preserves_capacity(self, tmp_path: Path) -> None:
        """Mapping 1 in the module docstring, checked as algebra rather than trusted.

        ``drain1d`` writes ``Q_full = (1/n)(1-b) A R_h^(2/3) S^(1/2)``. SWMM has no ``beta``, so
        the export divides the roughness. The check is that the SWMM conduit's Manning capacity
        at the written roughness equals ``drain1d``'s ``q_cap`` after blockage - if it did not,
        a desilting result from one engine would not mean the same thing in the other.
        """
        beta = 0.4
        network = _simple_network(n_nodes=4, beta=beta)
        swmm.write_inp(network, tmp_path / "n.inp")
        written_n = float(_conduit_field(tmp_path / "n.inp", network.edge_ids[0], column=4))

        assert written_n == pytest.approx(network.edge_manning_n[0] / (1.0 - beta), rel=1e-6)
        solver = drain1d.prepare(network)
        swmm_capacity = (
            (1.0 / written_n)
            * network.area[0]
            * network.hydraulic_radius[0] ** (2.0 / 3.0)
            * math.sqrt(0.005)
        )
        assert swmm_capacity == pytest.approx(float(solver.q_cap[0]), rel=1e-6)

    def test_a_fully_blocked_pipe_is_clamped_and_counted(self, tmp_path: Path) -> None:
        """``n_eff = n/(1-beta)`` has no value at ``beta = 1``; the report says so out loud."""
        network = _simple_network(n_nodes=4, beta=1.0)
        report = swmm.write_inp(network, tmp_path / "n.inp")
        assert report.clamped_blockage == network.n_edges
        assert not report.lossless

    def test_an_invert_above_its_street_is_clamped_and_counted(self, tmp_path: Path) -> None:
        """ADR-0048 measured 27,974 Mumbai nodes reaching an outfall over a raised invert.

        SWMM reads ``MaxDepth = 0`` as "work it out from the conduits", which would replace the
        value rather than refuse it, so the export clamps and counts. A silent replacement here
        is how a node with an impossible geometry becomes a plausible-looking depth.
        """
        network = _simple_network(n_nodes=4)
        z_invert = np.asarray(network.z_invert).copy()
        z_invert[1] = float(network.z_ground[1]) + 0.5  # invert half a metre above the street
        broken = _replace(network, z_invert=z_invert)
        report = swmm.write_inp(broken, tmp_path / "n.inp")
        assert report.clamped_depth == 1

    def test_a_box_drain_keeps_its_area_and_its_crown(self, tmp_path: Path) -> None:
        """Mapping 2: the graph stores an area and a crown depth but not a shape.

        A trunk whose area does not match ``pi D^2 / 4`` is written as a closed rectangle of the
        same height and the area's own width, so both quantities survive. Writing it as a circle
        of that diameter instead would have silently changed the conveyance.
        """
        network = _simple_network(n_nodes=3)
        area = np.full(network.n_edges, 1.2, dtype=np.float64)  # 1.2 m2 at a 0.9 m crown: a box
        depth = np.full(network.n_edges, 0.9, dtype=np.float64)
        boxy = _replace(network, area=area, diameter=depth)
        report = swmm.write_inp(boxy, tmp_path / "n.inp")

        assert report.n_box == network.n_edges
        assert report.n_circular == 0
        shape, geom1, geom2 = _xsection_fields(report.path, network.edge_ids[0])
        assert shape == "RECT_CLOSED"
        assert float(geom1) == pytest.approx(0.9)
        assert float(geom1) * float(geom2) == pytest.approx(1.2, rel=1e-4)

    def test_a_name_swmm_would_split_is_refused(self, tmp_path: Path) -> None:
        """A node id with a space would become two tokens and a different network.

        It raises rather than sanitising because a renamed node breaks the join back to
        ``city/<city>/graph/nodes.parquet``, and a graph that no longer joins is worse than one
        that would not export.
        """
        network = _simple_network(n_nodes=3)
        bad = _replace(network, node_ids=("N 0000", *network.node_ids[1:]))
        with pytest.raises(ValueError, match="SWMM"):
            swmm.write_inp(bad, tmp_path / "n.inp")


# ============================================================================ the adapter
class TestAdapter:
    """:func:`swmm.simulate` presents ``drain1d.simulate``'s call shape over the real engine."""

    def test_a_zero_length_call_moves_nothing(self, tmp_path: Path) -> None:
        network = _simple_network(n_nodes=4)
        state = _init_state(network)
        before = state.head.copy()
        with swmm.prepare(network, state, workdir=tmp_path, horizon_s=600.0) as solver:
            report = swmm.simulate(solver, state, duration_s=0.0, dt_s=1.0)
        assert report.n_steps == 0
        assert report.stored_start_m3 == report.stored_end_m3
        np.testing.assert_array_equal(state.head, before)

    def test_a_sub_second_stride_is_refused_not_rounded(self, tmp_path: Path) -> None:
        """SWMM's stride API takes whole seconds. A 0.5 s request silently becoming 1 s would
        double every volume the caller then integrates."""
        network = _simple_network(n_nodes=3)
        state = _init_state(network)
        with swmm.prepare(network, state, workdir=tmp_path, horizon_s=600.0) as solver:
            with pytest.raises(ValueError, match="whole seconds"):
                swmm.simulate(solver, state, duration_s=10.0, dt_s=0.5)

    def test_a_closed_solver_refuses_to_run(self, tmp_path: Path) -> None:
        """SWMM owns its state behind a C library and cannot be restarted in place."""
        network = _simple_network(n_nodes=3)
        state = _init_state(network)
        solver = swmm.prepare(network, state, workdir=tmp_path, horizon_s=600.0)
        solver.close()
        solver.close()  # idempotent
        with pytest.raises(RuntimeError, match="closed"):
            swmm.simulate(solver, state, duration_s=10.0, dt_s=1.0)

    def test_a_per_node_stage_array_is_read_at_its_tidal_nodes(self, tmp_path: Path) -> None:
        """``drain1d`` takes a per-node stage array; this adapter pushes one sea level.

        The array form is accepted for signature parity and read only at the tidal outfalls -
        the interior zeros in it are not a sea. Taking the array's first entry instead would be
        a silent flattening of a boundary condition, which is the class of defect ADR-0055 had
        to fix on the tide datum, and a genuinely varying sea raises (checked below).
        """
        network = _simple_network(n_nodes=4, has_tidal_outfall=True)
        state = _init_state(network)
        stage = np.zeros(network.n_nodes)
        stage[-1] = 3.0
        with swmm.prepare(network, state, workdir=tmp_path, horizon_s=600.0) as solver:
            # One tidal node, so a uniform array is fine and is read at that node.
            swmm.simulate(solver, state, duration_s=5.0, dt_s=1.0, tide_stage_m=stage)
        assert float(state.head[-1]) == pytest.approx(3.0, abs=1e-6)

    def test_a_genuinely_varying_sea_is_refused(self, tmp_path: Path) -> None:
        """Two tidal outfalls given different stages: the adapter will not pick one."""
        network = _simple_network(n_nodes=4, has_tidal_outfall=True)
        boundary = np.asarray(network.boundary).copy()
        boundary[0] = 1  # a second tidal outfall, at the head of the line
        two_seas = _replace(network, boundary=boundary)
        state = _init_state(two_seas)
        stage = np.zeros(two_seas.n_nodes)
        stage[0], stage[-1] = 2.0, 3.0
        with swmm.prepare(two_seas, state, workdir=tmp_path, horizon_s=600.0) as solver:
            with pytest.raises(ValueError, match="spatially varying sea"):
                swmm.simulate(solver, state, duration_s=5.0, dt_s=1.0, tide_stage_m=stage)

    def test_the_window_running_out_is_reported_not_hidden(self, tmp_path: Path) -> None:
        """A short run that claims its full duration makes every volume after it meaningless."""
        network = _simple_network(n_nodes=3)
        state = _init_state(network)
        with swmm.prepare(network, state, workdir=tmp_path, horizon_s=30.0) as solver:
            report = swmm.simulate(solver, state, duration_s=120.0, dt_s=1.0)
        assert report.n_steps < 120
        assert report.n_steps >= 25

    def test_pumps_draw_on_the_engines_own_heads(self, tmp_path: Path) -> None:
        """A controlled sink (SPEC.md 11.4) reaches SWMM as a negative lateral inflow.

        The check is that the pump actually removes water: the same run with and without it
        ends with less stored, and the sink's filled volume matches what was withdrawn.
        """
        network = _simple_network(n_nodes=4)
        q = _inflow_at_head(network, 0.02)
        sinks = drain1d.ControlledSink(
            ids=("P-12",),
            node=np.array([1], dtype=np.int32),
            curve_depth_m=np.array([[0.0, 1.0]], dtype=np.float64),
            curve_rate_m3_s=np.array([[0.005, 0.005]], dtype=np.float64),
            capacity_m3=np.array([np.inf], dtype=np.float64),
        )
        sink_state = drain1d.SinkState(filled_m3=np.zeros(1, dtype=np.float64))

        state = _init_state(network)
        with swmm.prepare(network, state, workdir=tmp_path / "with", horizon_s=2000.0) as solver:
            pumped = swmm.simulate(
                solver,
                state,
                duration_s=600.0,
                dt_s=1.0,
                q_inlet=q,
                sinks=sinks,
                sink_state=sink_state,
            )
        bare_state = _init_state(network)
        with swmm.prepare(network, bare_state, workdir=tmp_path / "bare", horizon_s=2000.0) as s2:
            swmm.simulate(s2, bare_state, duration_s=600.0, dt_s=1.0, q_inlet=q)

        assert pumped.sink_m3 == pytest.approx(0.005 * 600.0, rel=1e-9)
        assert float(sink_state.filled_m3[0]) == pytest.approx(0.005 * 600.0, rel=1e-9)
        # The pumped node and everything downstream of it sit lower: 5 of the 20 L/s never
        # reaches them. Node 0 is *upstream* of the pump and is not part of the claim - measured,
        # it ends 0.97 mm higher, which is the engine's iteration and not the pump.
        for i in (1, 2, 3):
            assert float(state.head[i]) < float(bare_state.head[i]), (
                f"node {i} is at or below the pump and should sit lower than the unpumped run"
            )
        assert float(state.head[0]) == pytest.approx(float(bare_state.head[0]), abs=5e-3)
        np.testing.assert_allclose(state.flow[1:], 0.015, rtol=1e-3)

    def test_a_surcharging_node_spills_at_its_cover_and_says_so(self, tmp_path: Path) -> None:
        """``drain1d`` cannot flood; SWMM can, and with the physical default it does.

        A 150 mm pipe cannot take 0.3 m3/s, so node 0 must surcharge. SWMM holds it at the
        manhole cover and spills the rest, where ``drain1d``'s Preissmann slot would have let
        the head keep rising. The adapter measures the spill rather than hiding it, because a
        coupled caller that also imposes a ``q_surcharge`` is counting the same water twice -
        the one thing standing between this module and ``varuna_twin.runner``.
        """
        network = _simple_network(n_nodes=4, diameter=0.15)
        state = _init_state(network)
        q = _inflow_at_head(network, 0.3)
        with swmm.prepare(network, state, workdir=tmp_path, horizon_s=2000.0) as solver:
            swmm.simulate(solver, state, duration_s=600.0, dt_s=1.0, q_inlet=q)
            flooded = solver.flooded_m3

        print(
            f"\n[surcharge] head {np.round(state.head, 3).tolist()}"
            f"  cover {np.round(network.z_ground, 2).tolist()}"
            f"  spilled {flooded:.1f} m3 of {0.3 * 600.0:.0f} put in"
        )
        assert flooded > 0.0, "a node forced far past its capacity must spill somewhere"
        # Held at the cover, not driven metres above it: that is what SurDepth 0 buys, and the
        # alternative reached 105 m (see DEFAULT_SURCHARGE_DEPTH_M).
        assert float(state.head[0]) == pytest.approx(float(network.z_ground[0]), abs=1e-2)

    def test_headroom_lets_the_head_run_away(self, tmp_path: Path) -> None:
        """Why :data:`swmm.DEFAULT_SURCHARGE_DEPTH_M` is 0 and not a large number.

        Pinned as a test because it is a design decision that looks wrong until you see the
        number: giving the junctions 100 m of surcharge depth so SWMM cannot spill sends the
        head 100 m over a Mumbai street, and under the default EXTRAN method it floods anyway.
        """
        network = _simple_network(n_nodes=4, diameter=0.15)
        state = _init_state(network)
        q = _inflow_at_head(network, 0.3)
        with swmm.prepare(
            network, state, workdir=tmp_path, horizon_s=2000.0, surcharge_depth_m=100.0
        ) as solver:
            swmm.simulate(solver, state, duration_s=600.0, dt_s=1.0, q_inlet=q)
            flooded = solver.flooded_m3

        print(
            f"\n[headroom 100 m] head {np.round(state.head, 2).tolist()} spilled {flooded:.1f} m3"
        )
        assert float(state.head[0]) > float(network.z_ground[0]) + 50.0, (
            "if the head no longer runs away, DEFAULT_SURCHARGE_DEPTH_M can be revisited"
        )
        assert flooded > 0.0, "and it floods anyway, so the headroom bought nothing"


# ============================================================================ the deliverable
class TestEngineGap:
    """How reduced is the reduced model? Measured on one network, printed in full.

    This is the P4.9 deliverable. Every assertion here is on the *shape* of the gap - which way
    it points, and that it stays inside a bound wide enough to be a finding rather than a
    tuning - and the exact figures go to stdout so ``-s`` gives a reader the table.
    """

    def test_steady_state_flows_agree_and_depths_do_not(self, tmp_path: Path) -> None:
        """The constitutive law, isolated: once nothing is changing, storage cannot matter.

        Both engines must carry the forcing exactly - that is conservation, and a disagreement
        would be a bug in one of them. The depths then say how much the reduced model's
        full-bore hydraulic radius costs, with nothing else mixed in.
        """
        network = _simple_network(n_nodes=4)
        q = _inflow_at_head(network, 0.01)
        a, rep_a, b, rep_b, continuity = _both(
            tmp_path, network, seconds=GRAVITY_FREE_SECONDS, q_inlet=q
        )

        depth_a = np.asarray(a.head) - np.asarray(network.z_invert)
        depth_b = np.asarray(b.head) - np.asarray(network.z_invert)
        interior = slice(0, network.n_nodes - 1)
        print(
            f"\n[steady state, 600 mm pipe at 0.5 %, 10 L/s]"
            f"\n  drain1d head {np.round(a.head, 5).tolist()}"
            f"\n  swmm    head {np.round(b.head, 5).tolist()}"
            f"\n  depth cm     drain1d {np.round(depth_a[interior] * 100, 3).tolist()}"
            f"  swmm {np.round(depth_b[interior] * 100, 3).tolist()}"
            f"\n  max head gap {float(np.max(np.abs(a.head - b.head))):.5f} m"
            f"\n  flows        drain1d {np.round(a.flow, 8).tolist()}"
            f"  swmm {np.round(b.flow, 8).tolist()}"
            f"\n  max flow gap {float(np.max(np.abs(a.flow - b.flow))):.3e} m3/s"
            f"\n  continuity   drain1d {rep_a.mass_balance.error_fraction * 100:.4f} %"
            f"  adapter-on-swmm {rep_b.mass_balance.error_fraction * 100:.4f} %"
            f"  swmm's own report {continuity} %"
        )

        # Both engines carry the forcing. This is the part that must agree.
        np.testing.assert_allclose(a.flow, 0.01, rtol=1e-4)
        np.testing.assert_allclose(b.flow, 0.01, rtol=1e-3)

        # And the depths do not. SWMM's part-full hydraulic radius is smaller than drain1d's
        # full-bore one, so SWMM needs more water to carry the same flow - always this way
        # round, at this fill.
        assert np.all(depth_b[interior] > depth_a[interior]), (
            "drain1d's full-bore R_h should over-convey a part-full pipe"
        )
        ratio = float(np.mean(depth_b[interior] / depth_a[interior]))
        assert 3.0 < ratio < 7.0, f"depth ratio {ratio:.2f} is outside the measured 4.5x"

    def test_normal_depth_hand_check(self) -> None:
        """Which engine is right? Manning's normal depth for a part-full circle, by hand.

        Without this the previous test only says the two disagree. This says the disagreement is
        ``drain1d``'s: SWMM's 6.28 cm reproduces the textbook normal depth for 10 L/s in a
        600 mm pipe at 0.5 % to better than 1 %, so the 4.5x is the reduced model's error and
        not a difference of opinion. ``drain1d``'s own docstring names this over-conveyance;
        this is the number it does not carry.
        """
        d, n, slope, q_target = 0.6, 0.013, 0.005, 0.01
        depth = 0.0628  # what SWMM settled at, measured
        theta = 2.0 * math.acos(1.0 - 2.0 * depth / d)
        area = (d * d / 8.0) * (theta - math.sin(theta))
        perimeter = theta * d / 2.0
        r_h = area / perimeter
        q_manning = (1.0 / n) * area * r_h ** (2.0 / 3.0) * math.sqrt(slope)
        print(
            f"\n[hand check] at {depth * 100:.2f} cm: A={area:.5f} m2 R_h={r_h:.5f} m "
            f"-> Q={q_manning:.6f} m3/s against the 0.010000 asked for "
            f"({abs(q_manning - q_target) / q_target * 100:.2f} % off)"
        )
        assert q_manning == pytest.approx(q_target, rel=0.02)

    def test_the_transient_difference_is_storage(self, tmp_path: Path) -> None:
        """Where the water sits, measured, because it dominates every transient comparison.

        ``drain1d`` puts it all in the manholes and none in the pipes; SWMM does the opposite
        and reports a junction volume of exactly zero. Quoting a transient RMSE between the two
        without saying this would attribute a storage assumption to Saint-Venant.
        """
        network = _simple_network(n_nodes=4)
        q = _inflow_at_head(network, 0.01)
        _, rep_a, _, rep_b, _ = _both(tmp_path, network, seconds=GRAVITY_FREE_SECONDS, q_inlet=q)

        with swmm.prepare(
            network, _init_state(network), workdir=tmp_path / "probe", horizon_s=600.0
        ) as solver:
            state = _init_state(network)
            swmm.simulate(solver, state, duration_s=300.0, dt_s=1.0, q_inlet=q)
            node_volume = sum(float(node.volume) for node in solver._nodes)
            link_volume = sum(float(link.volume) for link in solver._links)

        print(
            f"\n[storage at steady state]"
            f"\n  drain1d stored {rep_a.stored_end_m3:.4f} m3 (all of it in 1 m2 manholes)"
            f"\n  swmm    stored {rep_b.stored_end_m3:.4f} m3"
            f" = {node_volume:.4f} in nodes + {link_volume:.4f} in conduits"
            f"\n  ratio   {rep_b.stored_end_m3 / max(rep_a.stored_end_m3, 1e-12):.1f}x"
        )
        assert node_volume == 0.0, (
            "a SWMM junction is a point with no plan area; if this ever stops being true the "
            "storage half of the engine gap has changed and the docstring's 46x is stale"
        )
        assert link_volume > 0.0
        assert rep_b.stored_end_m3 > 10.0 * rep_a.stored_end_m3

    def test_swmm_loses_volume_on_a_short_run_and_drain1d_does_not(self, tmp_path: Path) -> None:
        """SPEC.md 11.3 budgets 0.1 %. The P1 "upgrade" misses it on a cold five-minute cycle.

        The loss is a one-off taken during the initial filling transient rather than a leak:
        measured, the same 0.338 m3 goes missing whether the run is 30 minutes or two hours, so
        the *percentage* falls as the run lengthens while the volume does not change. It also
        did not move when the routing step went from 1.0 to 0.05 s or ``MAX_TRIALS`` from 8 to
        20, which is what rules out a convergence explanation.
        """
        network = _simple_network(n_nodes=4)
        q = _inflow_at_head(network, 0.01)
        _, short_a, _, short_b, short_rpt = _both(
            tmp_path / "short", network, seconds=1800.0, q_inlet=q
        )
        _, long_a, _, long_b, long_rpt = _both(
            tmp_path / "long", network, seconds=7200.0, q_inlet=q
        )

        short_missing = short_b.mass_balance.error_fraction * short_b.mass_balance.volume_in_m3
        long_missing = long_b.mass_balance.error_fraction * long_b.mass_balance.volume_in_m3
        print(
            f"\n[continuity]"
            f"\n  30 min  drain1d {short_a.mass_balance.error_fraction * 100:.4f} %"
            f"  swmm(adapter) {short_b.mass_balance.error_fraction * 100:.3f} %"
            f"  swmm(own report) {short_rpt} %"
            f"  -> {short_missing:.3f} m3"
            f"\n  2 h     drain1d {long_a.mass_balance.error_fraction * 100:.4f} %"
            f"  swmm(adapter) {long_b.mass_balance.error_fraction * 100:.3f} %"
            f"  swmm(own report) {long_rpt} %"
            f"  -> {long_missing:.3f} m3"
        )

        assert short_a.mass_balance.error_fraction < 1e-3, "drain1d must meet SPEC.md 11.3"
        assert long_a.mass_balance.error_fraction < 1e-3
        assert short_b.mass_balance.error_fraction > 1e-3, (
            "SWMM missing the 0.1 % budget on a cold short run is the finding; if it now meets "
            "it, the docstring's -1.901 % is stale and should be re-measured"
        )
        # The absolute loss is the same water in both runs - that is what makes it a startup
        # artefact rather than a leak. 15 % is loose because it is a claim about a mechanism.
        assert short_missing == pytest.approx(long_missing, rel=0.15)
        # And the adapter's own audit tracks the engine's, which is what makes either quotable.
        if short_rpt is not None:
            assert short_b.mass_balance.error_fraction * 100 == pytest.approx(
                abs(short_rpt), abs=0.1
            )

    def test_both_engines_lock_at_a_tide(self, tmp_path: Path) -> None:
        """SPEC.md P4.7 and the 1:40 beat of the demo (SPEC.md 15), in both engines.

        The sea is raised to 5.0 m, 2.1 m above the outfall invert and above the cover of every
        manhole on the line but the first. Both engines drown the network; **what they do with
        the water that will not fit is where they part company, and it is not a subtlety.**

        ``drain1d`` has no concept of flooding - its Preissmann slot holds the water at the
        manhole and the head rises past the cover (5.025 m at node 0, whose cover is 5.0 m),
        leaving it to ``coupling`` to put that water on the street (SPEC.md 11.5). SWMM spills
        at the cover itself, so the lowest manhole on the line pins at its own ground level and
        holds the rest of the line down with it. Measured: the sea reaches every node, but SWMM
        settles at 4.600 m - node 2's cover - and floods, where ``drain1d`` settles at the sea.

        Both are tide-locked, which is SPEC.md P4.7's acceptance test and the 1:40 beat of the
        demo (SPEC.md 15). Only one of them has told you the street is under water.
        """
        network = _simple_network(n_nodes=4, has_tidal_outfall=True)
        q = _inflow_at_head(network, 0.01)
        state_a = _init_state(network)
        drain1d.simulate(
            drain1d.prepare(network),
            state_a,
            duration_s=7200.0,
            dt_s=1.0,
            q_inlet=q,
            tide_stage_m=5.0,
        )
        state_b = _init_state(network)
        with swmm.prepare(network, state_b, workdir=tmp_path, horizon_s=21600.0) as solver:
            swmm.simulate(solver, state_b, duration_s=7200.0, dt_s=1.0, q_inlet=q, tide_stage_m=5.0)
            flooding = np.array([float(n.flooding) for n in solver._nodes])
            spilled = solver.flooded_m3

        print(
            f"\n[tide lock at 5.0 m, outfall invert {float(network.z_invert[-1]):.2f} m]"
            f"\n  covers       {np.round(network.z_ground, 2).tolist()}"
            f"\n  drain1d head {np.round(state_a.head, 5).tolist()}  (no flooding concept)"
            f"\n  swmm    head {np.round(state_b.head, 5).tolist()}"
            f"  flooding {np.round(flooding, 5).tolist()}"
            f"\n  swmm spilled {spilled:.1f} m3 over the covers"
        )
        # The sea is imposed at the outfall in both.
        assert float(state_a.head[-1]) == pytest.approx(5.0, abs=1e-6)
        assert float(state_b.head[-1]) == pytest.approx(5.0, abs=1e-3)
        # drain1d drowns every manhole and keeps the water.
        assert np.all(np.asarray(state_a.head)[:-1] >= 5.0 - 1e-6)
        # SWMM drowns them too - every interior head is above its invert by more than a metre -
        # but the lowest cover caps the line and the excess leaves as flooding.
        lowest_cover = float(np.min(np.asarray(network.z_ground)[:-1]))
        assert np.all(np.asarray(state_b.head)[:-1] >= lowest_cover - 1e-3)
        assert float(np.max(state_b.head[:-1])) == pytest.approx(lowest_cover, abs=1e-2)
        assert spilled > 0.0, "a sea above the manhole covers must reach the street"

    def test_a_flap_gate_stops_the_sea_in_both_engines(self, tmp_path: Path) -> None:
        """The counter-measure, and the one place the engines differ in kind rather than degree.

        With a gate at the outfall neither engine lets the sea in. ``drain1d`` then fills its
        1 m2 manholes until the head rises above the sea and discharges over it; SWMM has to
        fill 33.9 m3 of conduit first and is still filling at the same wall time. Both
        behaviours are right for the storage each model was given.
        """
        network = _simple_network(n_nodes=4, has_tidal_outfall=True, flap_gate=True)
        q = _inflow_at_head(network, 0.01)
        a, _, b, rep_b, _ = _both(tmp_path, network, seconds=1800.0, q_inlet=q, tide_stage_m=5.0)

        print(
            f"\n[flap gate, sea at 5.0 m, 30 min]"
            f"\n  drain1d head {np.round(a.head, 5).tolist()} flow {np.round(a.flow, 6).tolist()}"
            f"\n  swmm    head {np.round(b.head, 5).tolist()} flow {np.round(b.flow, 6).tolist()}"
            f"\n  swmm boundary volume {rep_b.boundary_m3:+.4f} m3 (negative would be sea entering)"
        )
        # The gate's whole job: no sea comes in.
        assert rep_b.boundary_m3 >= -1e-6, "a gated outfall let the sea into the SWMM network"
        # And SWMM's manholes stay below the sea, because nothing filled them from the seaward
        # side. drain1d's rise above it is its own inflow finding its way out over the gate.
        assert np.all(np.asarray(b.head)[:-1] < 5.0)


# ============================================================================ helpers
def _replace(network: DrainNetwork, **changes: object) -> DrainNetwork:
    """A frozen-dataclass copy with fields replaced. ``dataclasses.replace`` does not work on a
    ``slots=True`` frozen dataclass holding numpy arrays without copying them, so this is
    explicit."""
    import dataclasses

    fields = {f.name: getattr(network, f.name) for f in dataclasses.fields(network)}
    fields.update(changes)
    return DrainNetwork(**fields)  # type: ignore[arg-type]


def _conduit_field(path: Path, edge_id: str, *, column: int) -> str:
    """One whitespace-separated field of an edge's ``[CONDUITS]`` row."""
    return _section_row(path, "[CONDUITS]", edge_id)[column]


def _xsection_fields(path: Path, edge_id: str) -> tuple[str, str, str]:
    row = _section_row(path, "[XSECTIONS]", edge_id)
    return row[1], row[2], row[3]


def _section_row(path: Path, section: str, name: str) -> list[str]:
    inside = False
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            inside = stripped == section
            continue
        if not inside or not stripped or stripped.startswith(";"):
            continue
        fields = stripped.split()
        if fields[0] == name:
            return fields
    msg = f"{name} is not in {section} of {path}"
    raise AssertionError(msg)


# ============================================================================ at city scale
@pytest.mark.slow
@pytest.mark.skipif(
    not (Path("city") / "mumbai" / "graph" / "edges.parquet").is_file(),
    reason="no city graph; run `make city CITY=mumbai` first",
)
def test_the_whole_mumbai_graph_exports(tmp_path: Path) -> None:
    """The 4-node network proves the mapping; this proves it survives the real graph.

    Measured on this machine at 12 python processes: ``load_network`` 1.80 s, the export 0.62 s
    and 0.46 s on a second call, 12.4 MB of ``.inp``. Nothing had to be clamped - no blockage
    above 0.99, no invert above its own street among the 49,770 junctions, no conduit under
    10 cm - so on this graph the export is lossless apart from the three documented mappings,
    and the hydraulic-radius mapping costs at most 48 micrometres.

    The number worth reading is ``adverse_edges``: **18,994** conduits run uphill, which is
    exactly what ADR-0048's independent audit measured. ``drain1d`` does not care, because it
    drives every flow from the head difference and caps at the city pipeline's design ``q_full``
    rather than at the invert slope. SWMM does care, and would say so on every one of them. That
    is the strongest argument in the repository for ADR-0048's regrade, and it is here rather
    than in prose because the exporter can count it.
    """
    from varuna_twin import city

    network = city.load_network("mumbai")
    # Into `tmp_path`, not into the tree: `data/swmm/` is not in `.gitignore` and 12.4 MB of
    # generated `.inp` is not something a test should leave where `git add -A` can reach it.
    # To keep the file - to open it in EPA SWMM's own GUI, which is the whole claim SPEC.md
    # 16 makes - call `swmm.write_inp(city.load_network("mumbai"), "mumbai.inp")` directly.
    report = swmm.write_inp(
        network, tmp_path / "mumbai.inp", title="VARUNA Mumbai inferred drain graph"
    )

    assert report.n_junctions + report.n_outfalls == network.n_nodes
    assert report.n_conduits == network.n_edges
    assert report.lossless, (
        f"the Mumbai graph needed clamping: depth {report.clamped_depth}, "
        f"blockage {report.clamped_blockage}, length {report.clamped_length}"
    )
    assert report.max_r_h_mismatch_m < 1e-3, (
        "the recovered cross-sections no longer reproduce the graph's own hydraulic radius"
    )
    print(
        f"\n[mumbai] {report.n_junctions} junctions, {report.n_outfalls} outfalls "
        f"({report.n_tidal} tidal, {report.n_flap_gates} gated), {report.n_conduits} conduits "
        f"({report.n_circular} circular, {report.n_box} box)"
        f"\n  adverse edges {report.adverse_edges} (ADR-0048 measured 18,994)"
        f"\n  max R_h mismatch {report.max_r_h_mismatch_m:.6f} m"
        f"\n  {report.bytes_written / 1e6:.1f} MB at {report.path}"
    )

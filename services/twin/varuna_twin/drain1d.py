"""The 1D drain solver: "diffusive-wave-lite" on the inferred pipe graph
(SPEC.md 11.4, Appendix A).

This is the sewer half of VARUNA-Twin. The street half (``swe2d``) routes water across the
30 m grid; this routes it *through* the pipes, and ``coupling`` is the only thing that moves
water between the two. Nothing here reaches for a surface cell: the inlet capture and the
surcharge discharge arrive as per-node arrays computed by SPEC.md 11.5, and this module
tells the caller how much of each it was actually able to apply.

**What it solves.** A head per node and a flow per edge, stepped explicitly at
``TwinInputs.inner_dt_s`` (1 s). The constitutive law is Appendix A exactly::

    A_eff  = (1 - beta) * A * fill,   fill = clip(min(H_up - z_inv_up, D) / D, 0, 1)
    Q_full = (1/n) * (1 - beta) * A * R_h^(2/3) * sqrt(S)
    Q      = sign(dH) * min(Q_full, (1/n) * A_eff * R_h^(2/3) * sqrt(|dH| / L))
    dH/dt  = (sum Q_in + Q_inlet - sum Q_out - Q_surch) / A_s

One formula covers both regimes, which is why there is no separate pressurised branch. For a
part-full pipe running normally, ``H = z_inv + depth`` at each end, so equal depths give
``dH/L = S`` and the friction term collapses to Manning's normal flow; when both ends rise
above the crown the pipe is full, ``fill`` saturates at 1, and the same ``|dH|/L`` is the
pressure gradient of a closed conduit. :func:`pressurised` reports which edges are in the
second regime for the console, but the solver does not need to know.

**Backflow is not an edge case.** ``Q`` carries the sign of ``H_from - H_to``, so a rising
sea at a tide-locked outfall drives ``Q < 0`` on the trunk and pushes the first upstream
manhole into surcharge. That negative number is what the console draws as a reversed-flow
edge (SPEC.md 6.2, 7.2) and what the demo shows at the 1:40 beat (SPEC.md 15). A flap
gate (``network.flap_gate``) is the counter-measure: it lets water leave the network at an
outfall and never lets it back in.

**Simplifications, stated once.** ``R_h`` is the full-bore hydraulic radius at every fill,
because that is what Appendix A writes; a part-full circular pipe's true ``R_h`` peaks near
0.8 D and falls to zero at both ends, so this model over-conveys a nearly empty pipe and
under-conveys one at 0.8 D. ``Q_full`` is likewise capped at the design capacity the city
pipeline sized the pipe for rather than recomputed from the inverts, because the city
pipeline enforced a 0.3 % minimum slope that the inverts alone would not reproduce
(SPEC.md 10.1 step 7). Both are the "-lite" in diffusive-wave-lite; PySWMM dynamic wave is
the P1 upgrade behind the same interface (SPEC.md P4.9).

**Conservation.** Volume, not head, is the integrated quantity. Storage per node is a
piecewise-linear curve - the manhole area below the crown, the manhole plus a Preissmann slot
above it (:data:`SLOT_CELERITY_M_S`) - so the slot cannot leak volume the way a changing
``A_s`` in ``dH/dt`` would. Every flow this module reduces (flap gate, stability limiter,
node-supply scaling) is reduced on *both* ends of its edge, so the reduction moves no water.
:func:`stored_volume_m3` is the audit quantity, and it is not the same as
``DrainState.volume_m3``: the latter is the slot-free approximation on the contract type,
which under-reports by the slot volume once nodes pressurise. Use this one for a balance.

Determinism (rule 8): no randomness, no parallel reductions. ``np.bincount`` sums edges into
nodes in edge order, so two runs of the same inputs produce byte-identical arrays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import structlog

from varuna_twin.types import GRAVITY, DrainNetwork, DrainState, MassBalance

if TYPE_CHECKING:  # pragma: no cover - keeps numpy off the runtime type surface
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.twin.drain1d")

__all__ = [
    "BOUNDARY_FREE",
    "BOUNDARY_INTERIOR",
    "BOUNDARY_TIDAL",
    "MIN_FLOW_DEPTH_M",
    "MIN_HEAD_GRADIENT_M",
    "MIN_STORAGE_AREA_M2",
    "SLOT_CELERITY_M_S",
    "ControlledSink",
    "DrainRunReport",
    "DrainSolver",
    "DrainStepReport",
    "SinkState",
    "backflow",
    "edge_flow",
    "head_from_volume",
    "no_sinks",
    "pin_boundaries",
    "prepare",
    "pressurised",
    "simulate",
    "sink_draw",
    "step",
    "storage_area",
    "stored_volume_m3",
    "surcharged",
    "volume_from_head",
]

# ------------------------------------------------------------------------ boundaries
BOUNDARY_INTERIOR = 0
"""``DrainNetwork.boundary`` code for a node that only exchanges with the graph."""

BOUNDARY_TIDAL = 1
"""``DrainNetwork.boundary`` code for an outfall held at the sea stage."""

BOUNDARY_FREE = 2
"""``DrainNetwork.boundary`` code for an outfall discharging freely at its invert."""

# ---------------------------------------------------------------------- numerical floors
MIN_FLOW_DEPTH_M = 1.0e-3
"""Upstream water depth below which an edge carries nothing, in metres.

A design choice, taken to match the 2D solver's ``h_f < 1 mm`` cut-off (SPEC.md 11.3) so
the two halves of Twin agree on when a millimetre of water stops being water. Without it the
fill fraction chatters between zero and a few microns on a drying pipe and the step count
climbs for no physical reason."""

MIN_HEAD_GRADIENT_M = 1.0e-6
"""Head difference below which an edge is quiescent, in metres.

A design choice. ``sqrt(|dH|/L)`` has an infinite derivative at ``dH = 0``, so two nodes that
have equalised to within a micron would otherwise trade denormal flows forever. A micron of
head is four orders of magnitude below the millimetre the rest of the model resolves."""

MIN_STORAGE_AREA_M2 = 0.25
"""Smallest manhole plan area the solver will integrate with, in m2.

A design choice: a 0.5 m square chamber. ``dH/dt = Q/A_s`` divides by this, so a node that
arrived with zero or negative storage area would produce an infinite head rather than a
diagnosable one. :func:`prepare` clamps and logs the count instead of failing, because the
graph is inferred (SPEC.md 10.1 step 7) and one bad node must not stop a cycle."""

SLOT_CELERITY_M_S = 10.0
"""Pressure-wave celerity the Preissmann slot is sized for, in m/s.

A numerical choice, not spec. SPEC.md 11.4 asks for "a Preissmann slot width when
surcharged so heads stay bounded" and leaves the width open. The classical slot width is
``T = g A / c^2``, the width whose free surface reproduces a wave celerity ``c`` in a pipe of
area ``A``; the real water-hammer celerity in a buried concrete sewer is around 1000 m/s,
which an explicit 1 s step could never resolve (it would need ``dt <= L/c``, tens of
milliseconds on a 40 m pipe).

10 m/s is chosen so that the Courant condition ``dt <= L/c`` holds at ``dt = 1 s`` for the
shortest pipes the city pipeline produces - inlets are placed every 40 m (SPEC.md 10.1
step 7), giving ``dt <= 4 s`` - while the slot stays small enough to be honest about storage:
on a 600 mm pipe the slot is 28 mm wide, so a metre of surcharge head adds about 10 % of the
pipe's own volume. The consequence is stated rather than hidden: pressure waves in VARUNA's
sewer travel two orders of magnitude slower than in a real one, which is invisible at the
5-minute cadence the products are published on and would matter to a water-hammer study."""


# ============================================================================ pumps, tanks
@dataclass(frozen=True, slots=True)
class ControlledSink:
    """Pumps and holding tanks as controlled sinks on the graph (SPEC.md 11.4).

    A unit draws water out of one node at a rate read off its capacity curve, and stops when
    it has taken :attr:`capacity_m3`. That single shape covers both cases the prototype
    needs: a mobile pump discharging to a nallah is a unit with infinite capacity, and the
    Hindmata holding tanks are units with a finite one (SPEC.md 3.3).

    Curves are per unit and interpolated on the water depth above the node invert, clamped to
    the end points - a pump that has not reached its start level appears as a leading
    ``(depth, 0.0)`` point, which is how a wet-well start/stop control is expressed here.
    """

    ids: tuple[str, ...]
    node: NDArray[np.int32]
    """Index of the node each unit draws from."""

    curve_depth_m: NDArray[np.floating]
    """``(n_units, n_points)`` depths above the node invert, increasing along axis 1."""

    curve_rate_m3_s: NDArray[np.floating]
    """``(n_units, n_points)`` withdrawal rate in m3/s at each depth."""

    capacity_m3: NDArray[np.floating]
    """Total volume each unit may take; ``np.inf`` for a pump that discharges away."""

    @property
    def n_units(self) -> int:
        return len(self.ids)


@dataclass(slots=True)
class SinkState:
    """How much each unit has taken so far, in m3. Persists across steps."""

    filled_m3: NDArray[np.floating]


def no_sinks() -> tuple[ControlledSink, SinkState]:
    """An empty pump/tank set, for a network that has none."""
    empty_i = np.zeros(0, dtype=np.int32)
    empty_f = np.zeros(0, dtype=np.float64)
    sinks = ControlledSink(
        ids=(),
        node=empty_i,
        curve_depth_m=np.zeros((0, 2), dtype=np.float64),
        curve_rate_m3_s=np.zeros((0, 2), dtype=np.float64),
        capacity_m3=empty_f,
    )
    return sinks, SinkState(filled_m3=empty_f.copy())


def sink_draw(
    sinks: ControlledSink,
    sink_state: SinkState,
    solver: DrainSolver,
    head: NDArray[np.floating],
    dt_s: float,
    out: NDArray[np.floating] | None = None,
) -> NDArray[np.floating]:
    """Withdrawal rate per **node** in m3/s, before the supply check.

    The loop is over units, not nodes: the prototype's inventory is twelve mobile pumps and a
    pair of holding tanks (SPEC.md 3.3), so a Python loop over units costs nothing next to
    the per-edge work, and each unit gets its own ``np.interp`` on its own curve.

    ``out`` is written in place and returned. Without it this allocated a fresh 50,110-element
    array on every one of a cycle's 10,800 inner steps - half a gigabyte, to carry fourteen
    non-zero numbers - and the caller then copied it again.
    """
    draw = np.zeros(solver.n_nodes, dtype=np.float64) if out is None else out
    if out is not None:
        # Only the unit nodes can be non-zero, so clearing those is enough and costs fourteen
        # writes rather than fifty thousand.
        draw[sinks.node] = 0.0
    if sinks.n_units == 0:
        return draw
    depth = head[sinks.node] - solver.network.z_invert[sinks.node]
    remaining = sinks.capacity_m3 - sink_state.filled_m3
    for u in range(sinks.n_units):
        if remaining[u] <= 0.0:
            continue
        rate = float(np.interp(depth[u], sinks.curve_depth_m[u], sinks.curve_rate_m3_s[u]))
        if rate <= 0.0:
            continue
        # A tank in its last second takes only what is left of its capacity.
        rate = min(rate, float(remaining[u]) / dt_s)
        draw[sinks.node[u]] += rate
    return draw


# ============================================================================ preparation
@dataclass(frozen=True, slots=True)
class DrainSolver:
    """A network with everything that does not change between steps computed once.

    Rebuild it whenever ``beta`` changes - VARUNA-Pulse rewrites the posterior once per cycle
    (SPEC.md 11.6), and :attr:`conveyance` and :attr:`q_cap` both carry ``(1 - beta)``.
    """

    network: DrainNetwork

    conveyance: NDArray[np.floating]
    """``(1/n) * (1 - beta) * A * R_h^(2/3)`` per edge: the friction term at full fill."""

    q_cap: NDArray[np.floating]
    """``(1 - beta) * q_full`` per edge: the Manning design capacity after blockage."""

    inv_diameter: NDArray[np.floating]
    """``1/D`` per edge, for the fill fraction."""

    storage_base: NDArray[np.floating]
    """Manhole plan area per node in m2, clamped to :data:`MIN_STORAGE_AREA_M2`."""

    slot_area: NDArray[np.floating]
    """Preissmann slot plan area per node in m2, active above the crown."""

    crown_depth: NDArray[np.floating]
    """Depth above the node invert at which its shallowest pipe fills; ``inf`` if it has none."""

    fixed_head: NDArray[np.bool_]
    """True at outfalls, whose head is imposed rather than integrated."""

    colour_edges: NDArray[np.int64] | None = None
    """Edge indices grouped so that no two edges in a group touch the same node (task P4.6).

    The two edge passes in the kernel are scatter-adds onto nodes - several edges write the same
    ``q_out[j]`` and ``net_edge[j]`` - so a plain ``prange`` over edges races. Colouring removes
    the race rather than papering over it with atomics or per-thread buffers: inside one colour
    every node is written by at most one edge, so the threads never meet, and across colours the
    order is the colour order. That is what keeps ``make bake`` byte-identical whatever the thread
    count (rule 8), which a per-thread reduction would not, because float addition is not
    associative and the partial sums would depend on how many threads ran.

    ``None`` on a network too small to be worth a parallel launch; the serial kernel then runs."""

    colour_start: NDArray[np.int64] | None = None
    """``n_colours + 1`` offsets into :attr:`colour_edges`; colour ``c`` is ``[start[c], start[c+1])``."""

    n_parallel_colours: int = 0
    """How many leading colours are large enough to hand to threads.

    Greedy colouring of the Mumbai graph gives 22 colours of 21,225, 18,333, 7,990, 1,386, 263,
    178 ... 1 edges: a long tail that is 4.5 % of the edges and would be 19 of the 22 `prange`
    launches. The kernel runs 10,800 times a cycle with one launch per colour per pass, so the
    tail would cost some 400,000 launches to parallelise 2,222 edges. The leading colours run in
    parallel and the tail runs serially in colour order, which is conflict-free either way."""

    @property
    def n_nodes(self) -> int:
        return self.network.n_nodes

    @property
    def n_edges(self) -> int:
        return self.network.n_edges


MIN_EDGES_FOR_COLOURING = 4096
"""Below this many edges the parallel kernel is not worth its launch overhead.

Measured, not guessed: a `prange` launch costs a few microseconds and the kernel runs 10,800
times per cycle with one launch per colour per pass, so a network of a few hundred edges pays
more in launches than the loops themselves cost. The Mumbai graph has 49,770 edges; the test
fixtures have four to twenty, and they run the serial kernel, which is also what keeps the
parity tests comparing two paths rather than one path with itself."""


MIN_COLOUR_FOR_PRANGE = 4096
"""The smallest colour worth handing to threads, in edges. See `n_parallel_colours`."""


def edge_colouring(network: DrainNetwork) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Group edges so that no two in a group share a node (task P4.6).

    Greedy, in edge order: each edge takes the lowest colour neither of its endpoints has used.
    Measured on Mumbai's 49,770 edges: **22 colours** of 21,225, 18,333, 7,990, 1,386, 263, 178,
    123, 86, 64, 44, 25, 20, 11, 6, 5, 3, 3 and then six of one or two - 0.12 s to compute, once.
    Three of the 22 are large enough to be worth a thread launch and carry 95.5 % of the edges;
    see :attr:`DrainSolver.n_parallel_colours` for what happens to the tail.

    Greedy is the right algorithm here and not a compromise: the optimum (Vizing) is at most one
    colour better, the colouring is computed once per network and cached on the solver, and what
    matters for correctness is only that the classes are *conflict-free*, which greedy guarantees
    by construction. :func:`~varuna_twin.tests.test_drain_kernel` asserts that property directly
    rather than trusting this docstring.

    Returns:
        ``(colour_edges, colour_start)``: a permutation of ``0..n_edges`` grouped by colour, and
        the ``n_colours + 1`` offsets that bound each colour.
    """
    from_node = np.asarray(network.from_node, dtype=np.int64)
    to_node = np.asarray(network.to_node, dtype=np.int64)
    n_edges = int(from_node.shape[0])
    n_nodes = int(network.n_nodes)

    # `last_colour[j]` is the highest colour already used at node j, as a bitmask would be if the
    # degree were bounded; a small per-node set is cheaper than a mask when the degree is 2 or 3.
    used_at: list[set[int]] = [set() for _ in range(n_nodes)]
    colour_of = np.empty(n_edges, dtype=np.int64)
    n_colours = 0
    for e in range(n_edges):
        a = int(from_node[e])
        b = int(to_node[e])
        taken = used_at[a] | used_at[b]
        c = 0
        while c in taken:
            c += 1
        colour_of[e] = c
        used_at[a].add(c)
        used_at[b].add(c)
        if c + 1 > n_colours:
            n_colours = c + 1

    counts = np.bincount(colour_of, minlength=n_colours)
    colour_start = np.zeros(n_colours + 1, dtype=np.int64)
    np.cumsum(counts, out=colour_start[1:])
    # Stable sort, so the edges inside a colour stay in index order and the permutation - and
    # therefore every float addition order in the kernel - is a function of the network alone.
    colour_edges = np.argsort(colour_of, kind="stable").astype(np.int64)

    log.info(
        "drain1d.coloured",
        n_edges=n_edges,
        n_colours=n_colours,
        largest_colour=int(counts.max()) if n_colours else 0,
    )
    return colour_edges, colour_start


def prepare(network: DrainNetwork, *, colour: bool = False) -> DrainSolver:
    """Precompute the per-edge conveyance and per-node storage curve of ``network``.

    ``colour`` asks for the edge colouring the parallel kernel needs (task P4.6). It is off by
    default and :mod:`varuna_twin.runner` is the only caller that turns it on, because the runner
    is the only caller whose shape was measured: 10,800 inner steps over 49,770 edges, where the
    coloured kernel is 3.49x the serial one. `varuna_flash.whatif` prepares a tiled network for a
    handful of steps at a time, where a `prange` launch per colour per pass may well cost more
    than it saves and where Pulse's 3 s budget is already met only warm - so it keeps the serial
    kernel until someone measures it there rather than assuming the gain transfers.

    The colouring itself costs 10 ms at 10,000 edges and 55 ms at 50,000, once per solver.
    """
    n_nodes = network.n_nodes
    from_node = np.asarray(network.from_node, dtype=np.intp)
    to_node = np.asarray(network.to_node, dtype=np.intp)
    area = np.asarray(network.area, dtype=np.float64)
    length = np.maximum(np.asarray(network.length, dtype=np.float64), MIN_HEAD_GRADIENT_M)
    diameter = np.asarray(network.diameter, dtype=np.float64)
    manning_n = np.asarray(network.edge_manning_n, dtype=np.float64)
    beta = np.clip(np.asarray(network.beta, dtype=np.float64), 0.0, 1.0)
    r_h = np.asarray(network.hydraulic_radius, dtype=np.float64)

    conveyance = (1.0 - beta) * area * np.cbrt(np.square(r_h)) / manning_n
    q_cap = (1.0 - beta) * np.maximum(np.asarray(network.q_full, dtype=np.float64), 0.0)

    storage_base = np.asarray(network.storage_area, dtype=np.float64).copy()
    thin = storage_base < MIN_STORAGE_AREA_M2
    if bool(thin.any()):
        log.warning(
            "drain1d.storage_area_clamped",
            n_nodes=int(thin.sum()),
            floor_m2=MIN_STORAGE_AREA_M2,
        )
        storage_base[thin] = MIN_STORAGE_AREA_M2

    # Preissmann slot: half of each pipe's slot volume is stored at each of its ends.
    slot_width = GRAVITY * area / (SLOT_CELERITY_M_S * SLOT_CELERITY_M_S)
    half = 0.5 * length * slot_width
    slot_area = np.bincount(from_node, weights=half, minlength=n_nodes) + np.bincount(
        to_node, weights=half, minlength=n_nodes
    )

    # The slot turns on when the *shallowest* pipe at a node fills, which is the first moment
    # the node loses part of its free surface and the stiffest point of the step.
    crown_depth = np.full(n_nodes, np.inf, dtype=np.float64)
    np.minimum.at(crown_depth, from_node, diameter)
    np.minimum.at(crown_depth, to_node, diameter)
    slot_area[np.isinf(crown_depth)] = 0.0

    boundary = np.asarray(network.boundary)
    fixed_head = boundary != BOUNDARY_INTERIOR

    colour_edges = colour_start = None
    n_parallel_colours = 0
    if colour and int(network.n_edges) >= MIN_EDGES_FOR_COLOURING:
        colour_edges, colour_start = edge_colouring(network)
        sizes = np.diff(colour_start)
        # The leading run of colours big enough to be worth a launch; `argmin` on the boolean
        # gives the first that is not, and the colours are in descending size by construction.
        big = sizes >= MIN_COLOUR_FOR_PRANGE
        n_parallel_colours = int(np.argmin(big)) if not big.all() else int(big.size)

    log.info(
        "drain1d.prepared",
        n_nodes=n_nodes,
        n_edges=network.n_edges,
        n_tidal=int((boundary == BOUNDARY_TIDAL).sum()),
        n_free=int((boundary == BOUNDARY_FREE).sum()),
        n_flap=int(np.asarray(network.flap_gate).sum()),
    )
    return DrainSolver(
        network=network,
        conveyance=conveyance,
        q_cap=q_cap,
        inv_diameter=1.0 / np.maximum(diameter, MIN_FLOW_DEPTH_M),
        storage_base=storage_base,
        slot_area=slot_area,
        crown_depth=crown_depth,
        fixed_head=fixed_head,
        colour_edges=colour_edges,
        colour_start=colour_start,
        n_parallel_colours=n_parallel_colours,
    )


# ============================================================================ storage curve
def storage_area(solver: DrainSolver, head: NDArray[np.floating]) -> NDArray[np.floating]:
    """Plan area governing ``dH/dt`` at each node, in m2.

    The manhole area below the crown; the manhole plus the Preissmann slot above it.
    """
    depth = head - solver.network.z_invert
    return np.where(
        depth > solver.crown_depth, solver.storage_base + solver.slot_area, solver.storage_base
    )


def volume_from_head(solver: DrainSolver, head: NDArray[np.floating]) -> NDArray[np.floating]:
    """Water stored at each node in m3, on the piecewise-linear storage curve."""
    depth = np.maximum(head - solver.network.z_invert, 0.0)
    below = np.minimum(depth, solver.crown_depth)
    above = np.maximum(depth - solver.crown_depth, 0.0)
    return solver.storage_base * below + (solver.storage_base + solver.slot_area) * above


def head_from_volume(solver: DrainSolver, volume: NDArray[np.floating]) -> NDArray[np.floating]:
    """Invert :func:`volume_from_head`. Negative volumes clamp to the invert."""
    v = np.maximum(volume, 0.0)
    v_crown = solver.storage_base * solver.crown_depth  # inf where the node has no pipes
    depth = np.where(
        v <= v_crown,
        v / solver.storage_base,
        solver.crown_depth + (v - v_crown) / (solver.storage_base + solver.slot_area),
    )
    return solver.network.z_invert + depth


def stored_volume_m3(solver: DrainSolver, head: NDArray[np.floating]) -> float:
    """Total water in the network's manholes and slots, in m3.

    The audit quantity for the mass balance. It exceeds ``DrainState.volume_m3`` by the
    Preissmann slot volume once nodes pressurise; the contract type carries the slot-free
    approximation because it does not know the solver's storage curve.
    """
    interior = ~solver.fixed_head
    return float(np.sum(volume_from_head(solver, head)[interior]))


# ============================================================================ boundaries
def pin_boundaries(
    solver: DrainSolver,
    head: NDArray[np.floating],
    tide_stage_m: float | NDArray[np.floating] | None,
) -> None:
    """Impose the outfall heads in place (SPEC.md 11.4).

    A free outfall sits at its invert. A tidal outfall sits at the sea stage, or at its invert
    when the sea is below it - the sea cannot pull the water table below the pipe. Pass
    ``tide_stage_m`` as a scalar for one sea level, or as a per-node array when outfalls sit
    on different creeks.
    """
    boundary = np.asarray(solver.network.boundary)
    z_invert = solver.network.z_invert

    free = boundary == BOUNDARY_FREE
    head[free] = z_invert[free]

    tidal = boundary == BOUNDARY_TIDAL
    if not bool(tidal.any()):
        return
    if tide_stage_m is None:
        head[tidal] = z_invert[tidal]
        return
    stage = np.asarray(tide_stage_m, dtype=np.float64)
    stage_at = stage[tidal] if stage.ndim else np.full(int(tidal.sum()), float(stage))
    head[tidal] = np.maximum(stage_at, z_invert[tidal])


# ============================================================================ constitutive law
def edge_flow(solver: DrainSolver, head: NDArray[np.floating]) -> NDArray[np.floating]:
    """Appendix A's pipe flow in m3/s, positive ``from_node -> to_node``.

    The pure constitutive law: no flap gates, no stability limiter, no supply check. Those
    belong to :func:`step`, which has the time step they need. Negative entries are backflow.
    """
    net = solver.network
    a = np.asarray(net.from_node, dtype=np.intp)
    b = np.asarray(net.to_node, dtype=np.intp)
    z_inv = net.z_invert

    dh = head[a] - head[b]
    forward = dh >= 0.0

    # The fill fraction is read at the upstream end, whichever end that currently is.
    up_head = np.where(forward, head[a], head[b])
    up_invert = np.where(forward, z_inv[a], z_inv[b])
    fill_depth = up_head - up_invert
    fill = np.clip(fill_depth * solver.inv_diameter, 0.0, 1.0)

    slope = np.abs(dh) / np.maximum(np.asarray(net.length, dtype=np.float64), MIN_HEAD_GRADIENT_M)
    friction = solver.conveyance * fill * np.sqrt(slope)

    q = np.where(forward, 1.0, -1.0) * np.minimum(solver.q_cap, friction)
    q[np.abs(dh) < MIN_HEAD_GRADIENT_M] = 0.0
    q[fill_depth < MIN_FLOW_DEPTH_M] = 0.0
    return q


def pressurised(solver: DrainSolver, head: NDArray[np.floating]) -> NDArray[np.bool_]:
    """Edges whose **both** ends stand above the pipe crown (SPEC.md 11.4)."""
    net = solver.network
    a = np.asarray(net.from_node, dtype=np.intp)
    b = np.asarray(net.to_node, dtype=np.intp)
    d = np.asarray(net.diameter, dtype=np.float64)
    return (head[a] >= net.z_invert[a] + d) & (head[b] >= net.z_invert[b] + d)


def surcharged(solver: DrainSolver, head: NDArray[np.floating]) -> NDArray[np.bool_]:
    """Nodes whose head stands above ground level - the surcharge condition of SPEC.md 11.5.

    Whether water actually reaches the street is the coupling's call, because it also knows
    the depth already lying on the cell. This is the pipe-side half of the test, and it is
    what the console's surcharge markers key off.
    """
    return np.asarray(head > solver.network.z_ground, dtype=np.bool_)


def backflow(state: DrainState) -> NDArray[np.bool_]:
    """Edges currently running ``to_node -> from_node``: the console's reversed-flow edges."""
    return np.asarray(state.flow < 0.0, dtype=np.bool_)


# ============================================================================ one step
@dataclass(frozen=True, slots=True)
class DrainStepReport:
    """What one inner step actually moved, in m3.

    The *applied* arrays exist because a step can be supply-limited: a node cannot give away
    more water than it holds, so the surcharge and sink withdrawals the caller asked for may
    be scaled down. SPEC.md 11.5 makes the coupling own the surcharge formula; it also has
    to know what got through, or the two halves of Twin would disagree about the volume.
    """

    inlet_m3: float
    """Street to pipe through the inlets."""

    surcharge_m3: float
    """Pipe to street through the manholes, as applied."""

    sink_m3: float
    """Taken out by pumps and tanks, as applied."""

    boundary_m3: float
    """Net volume leaving through the outfalls. Negative means the sea pushed water in."""

    stored_m3: float
    """Water in the interior nodes after the step."""

    limited_edges: int
    """Edges the stability limiter held back this step."""

    applied_surcharge: NDArray[np.floating]
    applied_sink: NDArray[np.floating]


def step(
    solver: DrainSolver,
    state: DrainState,
    *,
    dt_s: float = 1.0,
    q_inlet: NDArray[np.floating] | None = None,
    q_surcharge: NDArray[np.floating] | None = None,
    tide_stage_m: float | NDArray[np.floating] | None = None,
    sinks: ControlledSink | None = None,
    sink_state: SinkState | None = None,
) -> DrainStepReport:
    """Advance the drain state by ``dt_s`` seconds, in place.

    ``q_inlet`` and ``q_surcharge`` are per-node m3/s and non-negative, exactly as
    :class:`~varuna_twin.types.CouplingFluxes` carries them; SPEC.md 11.5 computes them, not
    this module. They are held constant over the call, which is what the coupling's frozen
    sync interval means.

    Three reductions can bite, in this order, and each is applied to *both* ends of its edge
    so that no water is created or destroyed:

    1. **flap gates** at outfalls, which forbid flow out of the gated node into the network;
    2. the **stability limiter**, which caps an exchange at the flow that would just level the
       two heads in one step, so an explicit step can never overshoot into oscillation;
    3. the **supply check**, which scales every outflow from a node - pipes, surcharge and
       sinks alike - when they would together take more than the node holds.
    """
    net = solver.network
    head = state.head
    a = np.asarray(net.from_node, dtype=np.intp)
    b = np.asarray(net.to_node, dtype=np.intp)
    n_nodes = solver.n_nodes

    inlet = np.zeros(n_nodes) if q_inlet is None else np.asarray(q_inlet, dtype=np.float64)
    surch = np.zeros(n_nodes) if q_surcharge is None else np.asarray(q_surcharge, dtype=np.float64)

    pin_boundaries(solver, head, tide_stage_m)
    q = edge_flow(solver, head)

    # 1. Flap gates. Honoured at outfalls only: the field documents itself as an outfall
    #    device, and applying it to an interior node would trap water there permanently.
    gated = np.asarray(net.flap_gate, dtype=bool) & solver.fixed_head
    if bool(gated.any()):
        q = np.where(gated[b], np.maximum(q, 0.0), q)
        q = np.where(gated[a], np.minimum(q, 0.0), q)

    # 2. Stability limiter. A fixed-head node is an infinite reservoir, so it contributes
    #    nothing to the head that has to be shared.
    area_now = storage_area(solver, head)
    inv_area = np.where(solver.fixed_head, 0.0, 1.0 / area_now)
    share = inv_area[a] + inv_area[b]
    dh = np.abs(head[a] - head[b])
    with np.errstate(divide="ignore"):
        q_limit = np.where(share > 0.0, dh / (dt_s * np.where(share > 0.0, share, 1.0)), np.inf)
    limited = np.abs(q) > q_limit
    q = np.clip(q, -q_limit, q_limit)

    # 3. Supply check. Everything a node gives away this step, against what it holds.
    draw = (
        np.zeros(n_nodes)
        if sinks is None or sink_state is None
        else sink_draw(sinks, sink_state, solver, head, dt_s)
    )
    q_out = np.bincount(a, weights=np.maximum(q, 0.0), minlength=n_nodes) + np.bincount(
        b, weights=np.maximum(-q, 0.0), minlength=n_nodes
    )
    demand = dt_s * (q_out + surch + draw)
    supply = volume_from_head(solver, head)
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(demand > supply, np.where(demand > 0.0, supply / demand, 1.0), 1.0)
    scale[solver.fixed_head] = 1.0  # an outfall is never short of water

    source = np.where(q >= 0.0, scale[a], scale[b])
    q = q * source
    applied_surch = surch * scale
    applied_draw = draw * scale

    # Node balance on volume, so the storage curve cannot leak.
    net_edge = np.bincount(b, weights=q, minlength=n_nodes) - np.bincount(
        a, weights=q, minlength=n_nodes
    )
    volume = volume_from_head(solver, head) + dt_s * (
        net_edge + inlet - applied_surch - applied_draw
    )
    new_head = head_from_volume(solver, volume)
    head[:] = np.where(solver.fixed_head, head, new_head)
    pin_boundaries(solver, head, tide_stage_m)
    state.flow[:] = q

    if sinks is not None and sink_state is not None and sinks.n_units > 0:
        sink_state.filled_m3 += dt_s * applied_draw[sinks.node]

    return DrainStepReport(
        inlet_m3=float(dt_s * inlet[~solver.fixed_head].sum()),
        surcharge_m3=float(dt_s * applied_surch[~solver.fixed_head].sum()),
        sink_m3=float(dt_s * applied_draw.sum()),
        boundary_m3=float(dt_s * net_edge[solver.fixed_head].sum()),
        stored_m3=stored_volume_m3(solver, head),
        limited_edges=int(limited.sum()),
        applied_surcharge=applied_surch,
        applied_sink=applied_draw,
    )


# ============================================================================ a run
@dataclass(frozen=True, slots=True)
class DrainRunReport:
    """The volume audit over a whole :func:`simulate` call, in m3."""

    n_steps: int
    inlet_m3: float
    surcharge_m3: float
    sink_m3: float
    boundary_m3: float
    stored_start_m3: float
    stored_end_m3: float
    limited_edges: int
    applied_surcharge_m3: NDArray[np.floating] | None = None
    """Per node, the volume the network *actually* pushed onto the street over this call.

    Not ``dt * q_surcharge``: the supply check scales every outflow from a node - pipes,
    surcharge and sinks together - when they would take more than the node holds, so a node
    draining hard through its pipes surcharges less than the coupling asked for. Filled only
    when the caller passes ``applied_surcharge_out``, because the coupled runner is the only
    caller that needs it and a 50,110-element array per sync is not free.

    **Why it exists** (task P4.5). The runner used to hand the surface the *requested* surcharge
    while the drain applied the scaled one, so every scaled node put water on the street that no
    node ever gave up. Measured on the 08:40 cycle of 2 July 2019: 6,052.7 m3 invented against
    2,970,218 m3 of rain - 0.204 % - which is the whole of that run's mass-balance failure."""

    @property
    def mass_balance(self) -> MassBalance:
        """The SPEC.md 11.3 audit: stored change against net inflow, budget 0.1 %.

        Water entering the network is the inlets plus whatever the sea pushed back through
        the outfalls; water leaving is the surcharge, the sinks and the outfall discharge.
        """
        volume_in = self.inlet_m3 + max(-self.boundary_m3, 0.0)
        volume_out = self.surcharge_m3 + self.sink_m3 + max(self.boundary_m3, 0.0)
        stored = self.stored_end_m3 - self.stored_start_m3
        residual = abs(stored - (volume_in - volume_out))
        scale = max(volume_in, abs(stored), 1e-12)
        return MassBalance(
            volume_in_m3=volume_in,
            volume_out_m3=volume_out,
            volume_stored_m3=stored,
            error_fraction=residual / scale,
        )


@dataclass(slots=True)
class _Scratch:
    """Buffers the compiled kernel reuses, so a step allocates nothing.

    Cached on the solver: rebuilding it - which VARUNA-Pulse does once per cycle when the
    posterior moves - gets fresh buffers with it.
    """

    q: NDArray[np.floating]
    q_out: NDArray[np.floating]
    scale: NDArray[np.floating]
    net_edge: NDArray[np.floating]
    applied_surch: NDArray[np.floating]
    applied_draw: NDArray[np.floating]
    draw: NDArray[np.floating]
    tide: NDArray[np.floating]
    from_node: NDArray[np.integer]
    to_node: NDArray[np.integer]
    length: NDArray[np.floating]
    flap_gate: NDArray[np.bool_]
    boundary: NDArray[np.integer]


_SCRATCH: dict[int, tuple[DrainSolver, _Scratch]] = {}
"""The kernel buffers for one solver, and a **strong reference to that solver**.

The reference is the correctness, not an accident. This was keyed on ``id(solver)`` and validated
by node and edge count, and that is not enough: CPython reuses an address as soon as the object at
it is collected, and two solvers of the same size are exactly what this repository's test fixtures
are. `_Scratch` carries copies of ``boundary`` and ``flap_gate``, so a stale hit steps the new
network with the old one's outfalls - a tide-locked outfall as a free one. It was found on
2026-09-24 when `test_hot_start`'s `tidal` fixture started reporting the `plain` fixture's drain
numbers exactly (outfall +2.0 m3 where the tide should push -5.2 m3 back up the trunk), and it
fires or not depending on what the allocator did earlier in the process, which is why it had never
shown. Keeping the solver alive makes the address unrecyclable while the entry is cached, so the
``is`` check below cannot be fooled."""

_STORED: dict[int, tuple[NDArray[np.floating], float]] = {}
"""The stored volume each drain state ended its last compiled run at.

Keyed on the state's id, and validated against the *identity of its head array*: `id()` is reused
after a collection, and a stale hit here would report a mass balance against another run's water.
`simulate` mutates `head` in place, so the array object is the same one from call to call and a
different state means a different array."""


def _stored_start(solver: DrainSolver, state: DrainState) -> float:
    """The water in the network now, reusing the last compiled run's end value when it applies."""
    found = _STORED.get(id(state))
    if found is not None and found[0] is state.head:
        return found[1]
    return stored_volume_m3(solver, state.head)


def _scratch(solver: DrainSolver) -> _Scratch:
    """The kernel's reusable buffers for one solver, built once. See :data:`_SCRATCH`."""
    key = id(solver)
    found = _SCRATCH.get(key)
    if found is not None and found[0] is solver:
        return found[1]
    net = solver.network
    n_nodes = solver.n_nodes
    made = _Scratch(
        q=np.zeros(solver.n_edges),
        q_out=np.zeros(n_nodes),
        scale=np.ones(n_nodes),
        net_edge=np.zeros(n_nodes),
        applied_surch=np.zeros(n_nodes),
        applied_draw=np.zeros(n_nodes),
        draw=np.zeros(n_nodes),
        tide=np.zeros(n_nodes),
        from_node=np.ascontiguousarray(net.from_node, dtype=np.int64),
        to_node=np.ascontiguousarray(net.to_node, dtype=np.int64),
        length=np.ascontiguousarray(net.length, dtype=np.float64),
        flap_gate=np.ascontiguousarray(net.flap_gate, dtype=np.bool_),
        boundary=np.ascontiguousarray(net.boundary, dtype=np.int64),
    )
    # One solver is live at a time, so clearing keeps a long bake from holding the buffers of
    # every solver it has ever built.
    _SCRATCH.clear()
    _SCRATCH[key] = (solver, made)
    return made


def simulate(
    solver: DrainSolver,
    state: DrainState,
    *,
    duration_s: float,
    dt_s: float = 1.0,
    q_inlet: NDArray[np.floating] | None = None,
    q_surcharge: NDArray[np.floating] | None = None,
    tide_stage_m: float | NDArray[np.floating] | None = None,
    sinks: ControlledSink | None = None,
    sink_state: SinkState | None = None,
    compiled: bool = True,
    applied_surcharge_out: NDArray[np.floating] | None = None,
) -> DrainRunReport:
    """Run the drain solver over one sync interval with the forcing held constant.

    SPEC.md 11.5 freezes the exchange fluxes over ``sync_s`` (5 s) and SPEC.md 11.4 fixes
    the inner step at ``inner_dt_s`` (1 s), so a call here is normally five steps. The tide is
    frozen with them; it moves on a scale of hours.

    ``compiled`` runs the Numba kernel in :mod:`varuna_twin.drain_kernel` - the same arithmetic
    with no temporaries (task P4.6). ``compiled=False`` runs the NumPy path, which is the readable
    specification and what the kernel is tested against.

    ``applied_surcharge_out`` is an ``n_nodes`` buffer the **caller owns**, zeroed and filled here
    with the volume each node actually surcharged (see
    :attr:`DrainRunReport.applied_surcharge_m3`). It is a parameter rather than a fresh array
    because the coupled runner makes 2,160 of these calls per cycle, and it is the caller's
    buffer rather than the solver's scratch because two reports sharing one scratch array is the
    aliasing defect ADR-0035 already had to fix once.
    """
    n_steps = max(round(duration_s / dt_s), 0)
    # **Carried between calls, not recomputed.** `stored_volume_m3` is a full NumPy pass over
    # 50,110 nodes with several temporaries, and the runner calls `simulate` 2,160 times per
    # cycle - so this was several seconds of work to learn a number the previous call already
    # ended holding. The cached value is keyed on the state object's identity *and* its head
    # array, so anything that touched the heads in between falls back to the real computation.
    started = _stored_start(solver, state)
    inlet = surch = sink = boundary = 0.0
    limited = 0
    if applied_surcharge_out is not None:
        applied_surcharge_out[:] = 0.0

    if compiled and n_steps > 0:
        return _simulate_compiled(
            solver,
            state,
            n_steps=n_steps,
            dt_s=dt_s,
            q_inlet=q_inlet,
            q_surcharge=q_surcharge,
            tide_stage_m=tide_stage_m,
            sinks=sinks,
            sink_state=sink_state,
            started=started,
            applied_surcharge_out=applied_surcharge_out,
        )

    for _ in range(n_steps):
        report = step(
            solver,
            state,
            dt_s=dt_s,
            q_inlet=q_inlet,
            q_surcharge=q_surcharge,
            tide_stage_m=tide_stage_m,
            sinks=sinks,
            sink_state=sink_state,
        )
        inlet += report.inlet_m3
        surch += report.surcharge_m3
        sink += report.sink_m3
        boundary += report.boundary_m3
        limited += report.limited_edges
        if applied_surcharge_out is not None:
            applied_surcharge_out += dt_s * report.applied_surcharge
    return DrainRunReport(
        n_steps=n_steps,
        inlet_m3=inlet,
        surcharge_m3=surch,
        sink_m3=sink,
        boundary_m3=boundary,
        stored_start_m3=started,
        stored_end_m3=stored_volume_m3(solver, state.head),
        limited_edges=limited,
        applied_surcharge_m3=applied_surcharge_out,
    )


def _simulate_compiled(
    solver: DrainSolver,
    state: DrainState,
    *,
    n_steps: int,
    dt_s: float,
    q_inlet: NDArray[np.floating] | None,
    q_surcharge: NDArray[np.floating] | None,
    tide_stage_m: float | NDArray[np.floating] | None,
    sinks: ControlledSink | None,
    sink_state: SinkState | None,
    started: float,
    applied_surcharge_out: NDArray[np.floating] | None = None,
) -> DrainRunReport:
    """:func:`simulate` through the compiled kernel. Same arithmetic, no temporaries."""
    from varuna_twin.drain_kernel import step_kernel, step_kernel_parallel

    # The coloured kernel when `prepare` found the network worth colouring (task P4.6); the
    # serial one otherwise, and on every test fixture, which is what keeps the parity test
    # comparing two paths rather than one path with itself.
    coloured = solver.colour_edges is not None and solver.n_parallel_colours > 0

    scratch = _scratch(solver)
    n_nodes = solver.n_nodes
    head = state.head

    inlet = (
        np.zeros(n_nodes) if q_inlet is None else np.ascontiguousarray(q_inlet, dtype=np.float64)
    )
    surch = (
        np.zeros(n_nodes)
        if q_surcharge is None
        else np.ascontiguousarray(q_surcharge, dtype=np.float64)
    )

    have_tide = tide_stage_m is not None
    if have_tide:
        stage = np.asarray(tide_stage_m, dtype=np.float64)
        scratch.tide[:] = stage if stage.ndim else float(stage)

    totals = np.zeros(5)
    limited = 0
    # **Allocated once, not per step.** With no sinks this used to build a fresh 50,110-element
    # array of zeros on every one of the 10,800 inner steps - half a gigabyte of allocation for a
    # value that never changes, and enough to show up beside the kernel it was feeding.
    has_sinks = sinks is not None and sink_state is not None
    draw = scratch.draw
    if not has_sinks:
        draw[:] = 0.0

    # Every element is the same object on every inner step - `sink_draw` writes into `draw`
    # in place and the state arrays are updated in place by the kernel - so the tuple is built
    # once rather than 10,800 times.
    args = (
        scratch.from_node,
        scratch.to_node,
        scratch.length,
        solver.conveyance,
        solver.q_cap,
        solver.inv_diameter,
        solver.network.z_invert,
        solver.storage_base,
        solver.slot_area,
        solver.crown_depth,
        solver.fixed_head,
        scratch.flap_gate,
        scratch.boundary,
        head,
        state.flow,
        inlet,
        surch,
        draw,
        float(dt_s),
        scratch.tide,
        have_tide,
        MIN_HEAD_GRADIENT_M,
        MIN_FLOW_DEPTH_M,
        BOUNDARY_FREE,
        BOUNDARY_TIDAL,
        scratch.q,
        scratch.q_out,
        scratch.scale,
        scratch.net_edge,
        scratch.applied_surch,
        scratch.applied_draw,
    )

    for _ in range(n_steps):
        # Sinks draw on the *current* head, so they are recomputed per step exactly as the NumPy
        # path does. `n_units` is a handful of tanks and pumps; NumPy is the right tool for it.
        if has_sinks:
            sink_draw(sinks, sink_state, solver, head, dt_s, out=draw)
        report = (
            step_kernel_parallel(
                *args, solver.colour_edges, solver.colour_start, solver.n_parallel_colours
            )
            if coloured
            else step_kernel(*args)
        )
        # Element-wise rather than a slice add, which would allocate a temporary per step.
        totals[0] += report[0]
        totals[1] += report[1]
        totals[2] += report[2]
        totals[3] += report[3]
        totals[4] = report[4]
        limited += int(report[5])

        if sinks is not None and sink_state is not None and sinks.n_units > 0:
            sink_state.filled_m3 += dt_s * scratch.applied_draw[sinks.node]
        if applied_surcharge_out is not None:
            # The kernel leaves the step's applied rate in the scratch buffer; the volume is the
            # sum over inner steps, which is what the surface has to be given.
            applied_surcharge_out += dt_s * scratch.applied_surch

    stored_end = float(totals[4])
    _STORED[id(state)] = (state.head, stored_end)
    return DrainRunReport(
        n_steps=n_steps,
        inlet_m3=float(totals[0]),
        surcharge_m3=float(totals[1]),
        sink_m3=float(totals[2]),
        boundary_m3=float(totals[3]),
        stored_start_m3=started,
        stored_end_m3=stored_end,
        limited_edges=limited,
        applied_surcharge_m3=applied_surcharge_out,
    )

"""The coupling: water moves between the street and the pipe
(SPEC.md 11.5, Appendix A).

This module is the only place water crosses from the 2D surface (``swe2d``) into the 1D
drain graph (``drain1d``) and back. Neither solver reaches for the other: the surface
receives per-cell rates of capture and surcharge in m/s (added to depth by ``_update_depth``
in ``swe2d``), and the drains receive per-node volumes of inlet flow and surcharge withdrawal
in m3/s (added to heads by ``step`` in ``drain1d``). This module computes both, and runs the
two solvers in alternating sync intervals.

**The three exchange formulae** (Appendix A, verbatim):

* Inlet capture:
  ``Q_inlet = (1 - kappa) * min(Q_weir, Q_orifice, Q_avail)``
  ``Q_weir = 1.66 * L * h^{3/2}``
  ``Q_orifice = 0.6 * A_o * sqrt(2 g h)``
  with ``Q_avail`` = remaining node capacity given ``H_j``.

* Surcharge when ``H_j > z_g + h``:
  ``Q_surch = 0.6 * A_m * sqrt(2 g (H_j - z_g - h))``

* Reversed surcharge when ``h + z_g > H_j`` and the node is surcharged: the manhole
  acts as a drain, pulling the street water down. In the prototype, this second direction
  is handled by the same formula with the sign reversed.

**Sync interval** (SPEC.md 11.5): the exchange fluxes are computed once and frozen for
``sync_s`` (5 s default), then both solvers sub-step independently within that interval.
The 2D solver sub-steps under the CFL rule; the 1D solver runs at ``inner_dt_s`` (1 s).
This is explicit, cheap, and honest about its coupling error - which is small at 5 s
because the capture rates change slowly compared to the wave.

**Flux limiter** (SPEC.md 11.5): no cell or node goes negative from the exchange.
Capture is limited to the water on the cell and the remaining capacity of the node.
Surcharge is limited to the volume above ground in the node.

Determinism (rule 8): no randomness, fixed traversal order, float64 throughout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import numpy as np
import structlog

from varuna_twin import swe2d
from varuna_twin.types import GRAVITY, CouplingFluxes, DrainNetwork

if TYPE_CHECKING:  # pragma: no cover
    from numpy.typing import NDArray

    from varuna_twin.drain1d import DrainSolver

log = structlog.get_logger("varuna.twin.coupling")

__all__ = [
    "ORIFICE_CD",
    "SURCHARGE_CD",
    "WEIR_CD",
    "advance_surface_with_capture",
    "cap_inlet_to_cell_water",
    "compute_exchange",
    "coupling_to_drain_rates",
    "coupling_to_surface_rates",
    "scatter_node_volumes_to_cells",
]

# ------------------------------------------------------------------ coefficients from Appendix A
WEIR_CD = 1.66
"""Weir coefficient for the inlet grate, in m^{0.5}/s (Appendix A)."""

ORIFICE_CD = 0.6
"""Orifice discharge coefficient for the inlet opening (Appendix A)."""

SURCHARGE_CD = 0.6
"""Discharge coefficient for the surcharge manhole (Appendix A)."""


# ============================================================================ exchange computation
@dataclass(frozen=True, slots=True)
class ExchangeResult:
    """What crosses between the street and the pipe this sync interval.

    Both arrays are per node in m3/s and non-negative. The sign convention follows
    :class:`~varuna_twin.types.CouplingFluxes`: capture is street->pipe, surcharge is
    pipe->street. They are separate rather than signed so the product layer can report
    them independently - which is what the drain X-ray and the surcharge markers show.

    ``q_inlet_cell`` and ``q_surcharge_cell`` are the same fluxes converted to per-cell
    m/s rates for the 2D solver: the surface sees capture as a *sink* and surcharge as a
    *source*. The conversion scatters each node's flux onto the single 2D cell it sits on,
    divided by the cell area to get a rate. Multiple nodes on the same cell are summed.
    """

    # per-node, m3/s
    q_inlet_node: NDArray[np.floating]
    """Street -> pipe through the inlets, m3/s per node."""

    q_surcharge_node: NDArray[np.floating]
    """Pipe -> street through the manholes, m3/s per node."""

    # per-cell, m/s (for the 2D solver)
    q_inlet_cell: NDArray[np.floating]
    """Street -> pipe as a rate on the 2D grid, m/s (a surface sink)."""

    q_surcharge_cell: NDArray[np.floating]
    """Pipe -> street as a rate on the 2D grid, m/s (a surface source)."""

    def as_coupling_fluxes(self) -> CouplingFluxes:
        """The contract-type view."""
        return CouplingFluxes(
            q_inlet=self.q_inlet_node,
            q_surcharge=self.q_surcharge_node,
        )


DRY_STREET_M = 1.0e-4
"""Depth below which a street is dry enough that no inlet captures from it.

Was an inline `1e-4` in the NumPy path; named here so the compiled kernel can be handed the same
number rather than a copy of it."""


@dataclass(slots=True)
class ExchangeBuffers:
    """Reusable output arrays for :func:`compute_exchange`.

    **Owned by the caller, deliberately.** A module-level cache here was the obvious way to stop
    2,160 calls allocating four arrays each - two of them the full 323 x 522 grid - and it is
    wrong: :class:`ExchangeResult` holds *references*, so two results computed from the same
    buffers are the same arrays, and the second call silently overwrites the first. A test that
    compared a clean inlet with a clogged one caught it; in the run loop it would never have
    shown, because each result is consumed before the next sync.

    So the caller passes these in when it knows the result is consumed immediately - which the
    runner does, once per sync - and omits them anywhere the result has to outlive the next call.
    """

    q_inlet: NDArray[np.floating]
    q_surcharge: NDArray[np.floating]
    q_inlet_cell: NDArray[np.floating]
    q_surcharge_cell: NDArray[np.floating]

    @classmethod
    def allocate(cls, n_nodes: int, grid_shape: tuple[int, int]) -> ExchangeBuffers:
        return cls(
            q_inlet=np.zeros(n_nodes, dtype=np.float64),
            q_surcharge=np.zeros(n_nodes, dtype=np.float64),
            q_inlet_cell=np.zeros(grid_shape, dtype=np.float64),
            q_surcharge_cell=np.zeros(grid_shape, dtype=np.float64),
        )


_NODE_CACHE: dict[int, tuple[DrainNetwork, dict[str, object]]] = {}
"""Per-node kernel constants for one network, and a **strong reference to that network**.

Keyed on ``id(network)`` and previously validated by node count, which is not enough and was not
a theoretical worry: ``drain1d._SCRATCH`` had the same shape and, on 2026-09-24, handed one
10-node test fixture another 10-node fixture's outfall types. Holding the key object alive makes
its address unrecyclable while the entry is cached, so ``is`` below cannot get a false hit."""


def _node_arrays(network: DrainNetwork) -> dict[str, object]:
    """The per-node constants the kernel needs, in its dtypes, built once per network.

    **The network itself is checked, not its id or its length.** CPython reuses `id()` once an
    object is collected, so a freed network's arrays were being handed back for a different
    network that happened to land at the same address - and since the new one had more nodes, the
    kernel read past the end of `row` and `col` and scattered into whatever integer it found.
    Numba does not bounds-check, so that surfaced as a Windows access violation in an unrelated
    test rather than an IndexError at the line that caused it. A length check was the first fix
    and is not enough: two networks of the same size are what the test fixtures are, and on
    2026-09-24 the identically-shaped cache in `drain1d` handed one 10-node fixture another's
    outfall types, silently. See :data:`_NODE_CACHE`.
    """
    key = id(network)
    found = _NODE_CACHE.get(key)
    if found is not None and found[0] is network:
        return found[1]
    made: dict[str, object] = {
        "row": np.ascontiguousarray(network.cell_row, dtype=np.int64),
        "col": np.ascontiguousarray(network.cell_col, dtype=np.int64),
        "z_ground": np.ascontiguousarray(network.z_ground, dtype=np.float64),
        "kappa": np.clip(np.asarray(network.kappa, dtype=np.float64), 0.0, 1.0),
        "inlet_length": np.maximum(np.asarray(network.inlet_length, dtype=np.float64), 0.0),
        "inlet_area": np.maximum(np.asarray(network.inlet_area, dtype=np.float64), 0.0),
        "storage_area": np.maximum(np.asarray(network.storage_area, dtype=np.float64), 0.01),
    }
    _NODE_CACHE.clear()
    _NODE_CACHE[key] = (network, made)
    return made


def compute_exchange(
    surface_h: NDArray[np.floating],
    surface_z: NDArray[np.floating],
    drain_head: NDArray[np.floating],
    network: DrainNetwork,
    solver: DrainSolver,
    cell_area_m2: float,
    sync_s: float = 5.0,
    compiled: bool = True,
    out: ExchangeBuffers | None = None,
    blocked: NDArray[np.bool_] | None = None,
    sea: NDArray[np.bool_] | None = None,
) -> ExchangeResult:
    """Compute the inlet capture and surcharge fluxes for one sync interval.

    Args:
        surface_h: depth on the 2D grid in metres ``(n_rows, n_cols)``.
        surface_z: ground elevation on the 2D grid in metres ``(n_rows, n_cols)``.
        drain_head: hydraulic head at each drain node in metres ``(n_nodes,)``.
        network: the drain graph with geometry and clogging parameters.
        solver: the prepared solver (for ``fixed_head``).
        cell_area_m2: area of one 2D cell in m2.
        sync_s: the interval these rates will be held over, in seconds. Every limiter below
            divides a *volume* the cell or the node actually has by this, so a rate can never
            move water that is not there. Passing a value that does not match the interval the
            caller then integrates over would break that guarantee, which is why it is an
            argument rather than a constant.
        blocked: the 2D solver's building mask, ``True`` where a cell never holds water. A node
            whose cell is blocked exchanges nothing at all (task P4.5). It is not a refinement:
            ``swe2d._update_depth`` sets a blocked cell's depth to zero and skips it *before*
            tallying, so surcharge scattered there is discarded without appearing in any ledger.
            Measured on the 08:40 cycle of 2 July 2019 that was 5,185.4 m3 destroyed, 0.46 % of
            the surcharge and 0.17 % of the inflow - and the building mask is dense over exactly
            the wards where the drain graph is densest (ADR-0039). Passing ``None`` keeps the old
            behaviour, which is only safe when the grid has no blocked cells.
        sea: the city's sea, ``True`` on every cell the tide is imposed on (``terrain.sea``). A
            node whose cell is sea exchanges nothing, exactly as under a building: no inlet
            capture and no surcharge. The clamp refills a sea cell to the stage at every
            sub-step, so an inlet there is an ungated pipe from the sea at ground minus 1.5 m -
            past the coast wall, the tidal-outfall invert and any flap gate - and what it took
            would be booked as ``sea_to_land`` where the audit cannot see it. The Mumbai build
            puts 148 interior nodes on sea cells (106 on the Mithi creek buffer, 42 on open sea),
            all 148 under water at the +2.218 m crest. The pipes meet the sea only at tidal
            outfalls, whose head is pinned to the stage. ``None`` is a grid with no sea raster
            and changes nothing.

    Returns:
        An :class:`ExchangeResult` with per-node and per-cell rates.
    """
    n_nodes = network.n_nodes
    grid_shape = surface_h.shape

    if compiled:
        return _compute_exchange_compiled(
            surface_h,
            surface_z,
            drain_head,
            network,
            solver,
            cell_area_m2,
            sync_s,
            out,
            blocked,
            sea,
        )

    # Gather the 2D depth and elevation at each node's cell
    row = np.asarray(network.cell_row, dtype=np.intp)
    col = np.asarray(network.cell_col, dtype=np.intp)

    # Nodes that have no 2D cell (row == -1) cannot exchange; nor can a node under a building,
    # because the cell it would exchange with is one the 2D solver holds at zero depth; nor a
    # node on the sea, whose cell the tide clamp holds at the stage.
    has_cell = (row >= 0) & (col >= 0)
    for mask in (blocked, sea):
        if mask is not None:
            excluded = np.zeros(n_nodes, dtype=bool)
            excluded[has_cell] = np.asarray(mask, dtype=bool)[row[has_cell], col[has_cell]]
            has_cell &= ~excluded

    h_at_node = np.zeros(n_nodes, dtype=np.float64)
    z_at_node = np.zeros(n_nodes, dtype=np.float64)
    h_at_node[has_cell] = np.asarray(surface_h, dtype=np.float64)[row[has_cell], col[has_cell]]
    z_at_node[has_cell] = np.asarray(surface_z, dtype=np.float64)[row[has_cell], col[has_cell]]

    head = np.asarray(drain_head, dtype=np.float64)
    z_ground = np.asarray(network.z_ground, dtype=np.float64)
    kappa = np.clip(np.asarray(network.kappa, dtype=np.float64), 0.0, 1.0)
    inlet_length = np.maximum(np.asarray(network.inlet_length, dtype=np.float64), 0.0)
    inlet_area = np.maximum(np.asarray(network.inlet_area, dtype=np.float64), 0.0)
    storage_area = np.maximum(np.asarray(network.storage_area, dtype=np.float64), 0.01)

    # ---- Inlet capture (Appendix A) ------------------------------------------
    # Q_weir = 1.66 * L * h^{3/2}
    # Q_orifice = 0.6 * A_o * sqrt(2 g h)
    # Q_inlet = (1 - kappa) * min(Q_weir, Q_orifice, Q_avail)

    h_positive = np.maximum(h_at_node, 0.0)
    q_weir = WEIR_CD * inlet_length * np.power(h_positive, 1.5)
    q_orifice = ORIFICE_CD * inlet_area * np.sqrt(2.0 * GRAVITY * h_positive)
    q_hydraulic = np.minimum(q_weir, q_orifice)

    # Q_avail: remaining capacity in the node  -  water can enter only if the head
    # is below ground level. Above ground, the node is full and surcharges instead.
    # This prevents the coupling from overfilling a node in one sync interval.
    depth_available = np.maximum(z_ground - head, 0.0)
    # Approximate available volume per second: how fast the node can accept water
    # without exceeding ground level. This is a linear estimate over the sync interval.
    # A more precise value would integrate the 1D solver, but that is what the sync
    # interval is for: the error is small when sync_s is small.
    dt = max(float(sync_s), 1e-9)
    q_avail = np.where(depth_available > 0.0, depth_available * storage_area / dt, 0.0)

    q_inlet = (1.0 - kappa) * np.minimum(q_hydraulic, q_avail)

    # No capture at outfalls (their head is imposed, not integrated)
    q_inlet[solver.fixed_head] = 0.0
    # No capture where the node has no 2D cell
    q_inlet[~has_cell] = 0.0
    # No capture when the street is dry
    q_inlet[h_positive < DRY_STREET_M] = 0.0

    # ---- Surcharge (Appendix A) -----------------------------------------------
    # Q_surch = 0.6 * A_m * sqrt(2 g (H - z_g - h)) when H > z_g + h
    # Reversed when h + z_g > H and node is surcharged (manhole drains the street)

    surface_level = z_at_node + h_at_node  # water surface on the street
    excess_head = head - surface_level  # positive when the pipe pushes up

    q_surcharge = np.zeros(n_nodes, dtype=np.float64)

    # Pipe pushes water onto the street
    pushing_up = (excess_head > 0.0) & has_cell
    if np.any(pushing_up):
        manhole_area = storage_area[pushing_up]  # A_m approximated by the manhole area
        orifice = SURCHARGE_CD * manhole_area * np.sqrt(2.0 * GRAVITY * excess_head[pushing_up])
        # The orifice equation says how fast water COULD leave the manhole, not how much is
        # there to leave. Unlimited, it invents water: a node a centimetre over the street emits
        # at that rate for the whole interval whether or not it holds the volume, and on the
        # Mumbai graph 39,801 nodes doing that once every 5 s turned 861,096 m3 of rain into
        # 196,951,114 m3 of standing water - a 228x mass gain, and peak depths of 28 m.
        #
        # The cap is the volume that would bring the node's head down to the street's water
        # surface, which is where the exchange stops by definition: below that there is no
        # excess head left to push with. The inlet side has always had its mirror of this in
        # q_avail; this is the half that was missing.
        emitted = excess_head[pushing_up] * manhole_area / dt
        q_surcharge[pushing_up] = np.minimum(orifice, emitted)

    # Street drains into the pipe (reversed surcharge)
    # This happens when the street level is above the pipe head AND the node head is
    # above the ground (surcharged state). If the node is below ground, normal inlet
    # capture handles it.
    pulling_down = (excess_head < 0.0) & (head > z_ground) & has_cell
    if np.any(pulling_down):
        # The drainage flow uses the same orifice formula with the reversed head
        reversed_excess = -excess_head[pulling_down]
        manhole_area = storage_area[pulling_down]
        # This flow enters the drain, so it's treated as additional inlet. Limited the same way
        # and for the same reason, but against the STREET: the cell cannot give the manhole more
        # water than is standing on it, or the surface goes negative and the deficit reappears
        # downstream as invented water.
        orifice = SURCHARGE_CD * manhole_area * np.sqrt(2.0 * GRAVITY * reversed_excess)
        on_street = h_at_node[pulling_down] * cell_area_m2 / dt
        q_inlet[pulling_down] += np.minimum(orifice, on_street)

    # No surcharge at outfalls, and no capture either: the reversed branch above can add to an
    # outfall's inlet after the first zeroing, which the kernel never did. An outfall's head is
    # imposed, so what it would "capture" is booked nowhere on the drain's side.
    q_surcharge[solver.fixed_head] = 0.0
    q_surcharge[~has_cell] = 0.0
    q_inlet[solver.fixed_head] = 0.0

    # ---- Scatter onto the 2D grid --------------------------------------------
    q_inlet_cell = np.zeros(grid_shape, dtype=np.float64)
    q_surcharge_cell = np.zeros(grid_shape, dtype=np.float64)

    # Scatter node fluxes onto cells: m3/s / cell_area = m/s.
    #
    # `np.add.at` rather than `cell[rows, cols] += values`, because several nodes share a cell -
    # inlets sit every 40 m along a road and the grid is 30 m, so a cell routinely carries two or
    # three - and fancy-index assignment applies each repeated index only ONCE, silently dropping
    # every duplicate's contribution. `np.add.at` is the unbuffered form that accumulates them.
    #
    # It replaces a Python loop over all 50,110 nodes. That loop ran once per sync, 60 times per
    # 5-minute step, which is three million interpreted iterations per step and was the single
    # largest cost in the coupled run at 4.5 s of the 7.9 s each step took.
    rate_factor = 1.0 / cell_area_m2
    active = has_cell & ~solver.fixed_head
    rows_active = row[active]
    cols_active = col[active]
    np.add.at(q_inlet_cell, (rows_active, cols_active), q_inlet[active] * rate_factor)
    np.add.at(q_surcharge_cell, (rows_active, cols_active), q_surcharge[active] * rate_factor)
    cap_inlet_to_cell_water(
        q_inlet, q_inlet_cell, np.asarray(surface_h, dtype=np.float64), row, col, rate_factor, dt
    )

    return ExchangeResult(
        q_inlet_node=q_inlet,
        q_surcharge_node=q_surcharge,
        q_inlet_cell=q_inlet_cell,
        q_surcharge_cell=q_surcharge_cell,
    )


def cap_inlet_to_cell_water(
    q_inlet: NDArray[np.floating],
    q_inlet_cell: NDArray[np.floating],
    surface_h: NDArray[np.floating],
    row: NDArray[np.integer],
    col: NDArray[np.integer],
    rate_factor: float,
    dt: float,
) -> bool:
    """Scale every node on an over-asked cell so together they take no more than the cell holds.

    In place on both arrays; returns whether any cell was capped. ``q_inlet`` is per node in m3/s,
    ``q_inlet_cell`` the same scattered onto the grid in m/s, and the cell's limit is its depth
    over the interval, ``h / dt`` in m/s - the volume standing on it, handed over across the sync.

    **Why it exists.** Each node is limited against the street on its own: the regular inlet by
    its weir, orifice and remaining capacity, the reversed branch by ``h * A / dt``. Up to eight
    nodes share one 30 m cell on the Mumbai graph (7,880 of the 35,841 unblocked cells that carry
    an interior node carry more than one), so together they could ask for several times the water
    there. The surface gives up ``min(wanted, available)`` and the network, which has already
    stepped, keeps what it asked for; the difference is water neither solver holds - the
    ``inlet_gap_m3`` of the ledger. On a 12 x 12 test street with six manholes on one cell under
    a 3 m sea it was -650.9 m3 over half an hour, and -279,059 m3 with larger manholes
    (``tests/test_inlet_exchange.py``). On the full city at tide +1 m on the 08:40 cycle the gap
    was -66,269 m3, 0.140 % of the inflow and over SPEC.md 11.3's budget.

    Every node on a capped cell is scaled by the same factor, so the split between them is the
    one the formulae gave; the cell's rate is then rebuilt as the sum of the scaled nodes, in
    node order, which is how :func:`compute_exchange` scattered it in the first place. Nodes are
    only read where ``q_inlet > 0``, so outfalls and nodes without a cell, already zero, are never
    indexed. The compiled kernel does the same arithmetic in the same order.
    """
    taking = np.flatnonzero(np.asarray(q_inlet) > 0.0)
    if taking.size == 0:
        return False
    r = row[taking]
    c = col[taking]
    asked = q_inlet_cell[r, c]
    limit = surface_h[r, c] / dt
    over = asked > limit
    if not np.any(over):
        return False
    capped = taking[over]
    q_inlet[capped] = q_inlet[capped] * (limit[over] / asked[over])
    q_inlet_cell[r[over], c[over]] = 0.0
    np.add.at(q_inlet_cell, (r[over], c[over]), q_inlet[capped] * rate_factor)
    return True


def advance_surface_with_capture(
    stepper: swe2d.SurfaceStepper,
    duration_s: float,
    *,
    q_inlet_ms: NDArray[np.floating],
    q_surcharge_ms: NDArray[np.floating],
    tide_stage_m: float | None,
    capture_buffer: NDArray[np.floating],
    no_capture: NDArray[np.floating],
) -> tuple[int, float, float, float, float, float, float]:
    """Advance the surface one sync with the whole capture taken in its first CFL sub-step.

    Returns ``(n_steps, rain, surcharge, inlet, tide_in, tide_out, created)``, the fields of
    :class:`~varuna_twin.swe2d.SurfaceAdvance` summed over the calls made.

    **Why the capture is front-loaded.** :func:`compute_exchange` limits what a cell's nodes ask
    for to the water on the cell at the start of the sync, and the network then accepts all of
    it over the sync. The surface used to hand it over at the same rate across every CFL
    sub-step, which it can only do if the water is still there: a cell its neighbours drain in
    the first sub-step has nothing left for the second, and the network keeps what it was
    promised. Measured on a 30 m street cell holding 450 m3 beside a 3 m pool, all of it
    promised to the drains: 348.4 m3 given in two sub-steps, 450.0 m3 front-loaded. The same
    mechanism alone, with one node per cell, invented 620.9 m3 on the 12 x 12 test street.

    So the sync is split at the first CFL sub-step. That sub-step is the one the solver would
    have taken anyway - its length is :func:`swe2d.cfl_dt` of the same depth with the same
    ceiling - and it removes the whole sync's capture, which is available by construction because
    ``wanted <= h <= h + gained`` and the kernel serves the inlet before any face flux. The rest
    of the sync runs with no capture. The sub-step boundaries are those of a single call; only
    where in the sync the capture lands has moved, which is the same operator splitting the
    coupling already makes when it freezes the exchange for the interval. The network still
    integrates the same volume over the sync, so both sides move one volume.

    When the first sub-step already spans the sync - a grid shallower than about 1.8 m at 30 m,
    which is most of a monsoon morning - this is one call with the caller's arrays, bitwise what
    it was before.
    """
    kernel = stepper.kernel
    dt0 = _first_substep_s(stepper.state.h, kernel.res_m, duration_s, stepper.cfl_scope)
    if dt0 >= duration_s:
        run = stepper.advance(
            duration_s,
            q_inlet_ms=q_inlet_ms,
            q_surcharge_ms=q_surcharge_ms,
            tide_stage_m=tide_stage_m,
            max_dt_s=duration_s,
        )
        return (
            run.n_steps,
            run.volume_rain_m3,
            run.volume_surcharge_m3,
            run.volume_inlet_m3,
            run.volume_tide_in_m3,
            run.volume_tide_out_m3,
            run.volume_created_m3,
        )
    np.multiply(q_inlet_ms, duration_s / dt0, out=capture_buffer)
    first = stepper.advance(
        dt0,
        q_inlet_ms=capture_buffer,
        q_surcharge_ms=q_surcharge_ms,
        tide_stage_m=tide_stage_m,
        max_dt_s=duration_s,
    )
    rest = stepper.advance(
        duration_s - dt0,
        q_inlet_ms=no_capture,
        q_surcharge_ms=q_surcharge_ms,
        tide_stage_m=tide_stage_m,
        max_dt_s=duration_s,
    )
    return (
        first.n_steps + rest.n_steps,
        first.volume_rain_m3 + rest.volume_rain_m3,
        first.volume_surcharge_m3 + rest.volume_surcharge_m3,
        first.volume_inlet_m3 + rest.volume_inlet_m3,
        first.volume_tide_in_m3 + rest.volume_tide_in_m3,
        first.volume_tide_out_m3 + rest.volume_tide_out_m3,
        first.volume_created_m3 + rest.volume_created_m3,
    )


def _first_substep_s(
    h: NDArray[np.floating],
    res_m: float,
    max_dt_s: float,
    scope: swe2d.CflScope | None = None,
) -> float:
    """The length of the first CFL sub-step :meth:`swe2d.SurfaceStepper.advance` will take.

    :func:`swe2d.cfl_dt`'s own arithmetic (``swe2d._cfl_step``), so the two agree to the bit
    (``tests/test_inlet_exchange.py`` pins it). Not :func:`swe2d.cfl_dt` itself because deciding
    where to split a sync is not a sub-step: ``tools/profile_twin.py`` counts every call to
    ``swe2d.cfl_dt`` as one, and a second call per sync doubled its ``cfl_substeps``. ``scope``
    is the stepper's own :attr:`~varuna_twin.swe2d.SurfaceStepper.cfl_scope`, so a deep hole in
    the sea moves neither the split nor the step.
    """
    return swe2d._cfl_step(h, res_m, max_dt_s, scope)


def scatter_node_volumes_to_cells(
    network: DrainNetwork,
    solver: DrainSolver,
    volume_m3: NDArray[np.floating],
    duration_s: float,
    cell_area_m2: float,
    out: NDArray[np.floating],
) -> NDArray[np.floating]:
    """Turn a per-node volume over ``duration_s`` into the per-cell rate in m/s the 2D solver reads.

    The mirror of the scatter inside :func:`compute_exchange`, and it exists for the mass balance
    (task P4.5). ``compute_exchange`` says how much surcharge each node *should* emit; the drain's
    supply check then scales that down wherever a node's pipes and manhole together want more
    water than the node holds. Handing the surface the requested rate while the drain applied the
    scaled one invents exactly the difference - 6,052.7 m3 on the 08:40 cycle of 2 July 2019,
    which was 99.8 % of that run's 0.204 % mass-balance failure. So the runner steps the drain
    first and passes what it actually emitted through here.

    ``out`` is zeroed and filled in place; it is the caller's buffer for the same reason
    :class:`ExchangeBuffers` is. Nodes without a 2D cell and fixed-head outfalls are skipped,
    which matches :func:`compute_exchange` - they never emitted onto a street to begin with.
    """
    active_rows, active_cols, active = _scatter_index(network, solver)
    out[:, :] = 0.0
    dt = max(float(duration_s), 1e-9)
    factor = 1.0 / (dt * cell_area_m2)
    # `np.add.at`, not fancy-index assignment: inlets sit every 40 m along a road on a 30 m grid,
    # so several nodes routinely share a cell and a buffered scatter would keep only one of them.
    np.add.at(out, (active_rows, active_cols), np.asarray(volume_m3)[active] * factor)
    return out


_SCATTER_CACHE: dict[
    int, tuple[DrainNetwork, DrainSolver, NDArray[np.int64], NDArray[np.int64], NDArray[np.bool_]]
] = {}


def _scatter_index(
    network: DrainNetwork, solver: DrainSolver
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.bool_]]:
    """The rows, columns and node mask the surcharge scatter uses, built once per network.

    The runner scatters 2,160 times a cycle; rebuilding the mask and its two index arrays each
    time is three 50,110-element allocations per sync for something that never changes. Keyed and
    length-checked exactly as :func:`_node_arrays` is, and for the reason written there: CPython
    reuses `id()`, and a stale entry of the wrong length would be read past the end.
    """
    key = id(network)
    found = _SCATTER_CACHE.get(key)
    # Both objects, by identity: the mask reads `solver.fixed_head`, so a solver rebuilt on the
    # same network - which VARUNA-Pulse does every cycle when the posterior moves - is a miss.
    if found is not None and found[0] is network and found[1] is solver:
        return found[2:]
    nodes = _node_arrays(network)
    row = cast("NDArray[np.int64]", nodes["row"])
    col = cast("NDArray[np.int64]", nodes["col"])
    active = (row >= 0) & (col >= 0) & ~np.asarray(solver.fixed_head)
    made = (row[active], col[active], active)
    _SCATTER_CACHE.clear()
    _SCATTER_CACHE[key] = (network, solver, *made)
    return made


def coupling_to_surface_rates(
    exchange: ExchangeResult,
) -> tuple[NDArray[np.floating], NDArray[np.floating]]:
    """Extract the rates the 2D solver needs: ``(q_inlet_ms, q_surcharge_ms)``."""
    return exchange.q_inlet_cell, exchange.q_surcharge_cell


def coupling_to_drain_rates(
    exchange: ExchangeResult,
) -> tuple[NDArray[np.floating], NDArray[np.floating]]:
    """Extract the rates the 1D solver needs: ``(q_inlet, q_surcharge)`` per node in m3/s."""
    return exchange.q_inlet_node, exchange.q_surcharge_node


_EMPTY_MASK: dict[tuple[int, int], NDArray[np.bool_]] = {}


def _mask_or_empty(mask: NDArray[np.bool_] | None, shape: tuple[int, ...]) -> NDArray[np.bool_]:
    """A cell mask the kernel reads: the caller's, or an all-False one cached per grid shape.

    Numba needs a typed array rather than ``None``, and allocating a 323 x 522 array of zeros on
    each of the 2,160 syncs to say "no buildings" or "no sea" would cost more than the mask saves.
    The cache is keyed on the shape, so a second grid gets its own; the kernel only reads it, so
    the building and the sea masks can share one."""
    if mask is not None:
        return np.ascontiguousarray(mask, dtype=np.bool_)
    grid = (int(shape[0]), int(shape[1]))
    found = _EMPTY_MASK.get(grid)
    if found is None:
        found = np.zeros(grid, dtype=np.bool_)
        _EMPTY_MASK[grid] = found
    return found


def _compute_exchange_compiled(
    surface_h: NDArray[np.floating],
    surface_z: NDArray[np.floating],
    drain_head: NDArray[np.floating],
    network: DrainNetwork,
    solver: DrainSolver,
    cell_area_m2: float,
    sync_s: float,
    out: ExchangeBuffers | None,
    blocked: NDArray[np.bool_] | None = None,
    sea: NDArray[np.bool_] | None = None,
) -> ExchangeResult:
    """:func:`compute_exchange` through the compiled kernel (task P4.6)."""
    from varuna_twin.coupling_kernel import exchange_kernel

    nodes = _node_arrays(network)
    # Fresh buffers unless the caller supplied its own; see `ExchangeBuffers`.
    buffers = out or ExchangeBuffers.allocate(network.n_nodes, surface_h.shape)

    def arr(name: str) -> NDArray[np.generic]:
        # `_node_arrays` stores every constant as `object`; each is the array its builder made.
        return cast("NDArray[np.generic]", nodes[name])

    exchange_kernel(
        arr("row"),
        arr("col"),
        arr("z_ground"),
        arr("kappa"),
        arr("inlet_length"),
        arr("inlet_area"),
        arr("storage_area"),
        np.ascontiguousarray(solver.fixed_head),
        _mask_or_empty(blocked, surface_h.shape),
        _mask_or_empty(sea, surface_h.shape),
        np.ascontiguousarray(surface_h, dtype=np.float64),
        np.ascontiguousarray(surface_z, dtype=np.float64),
        np.ascontiguousarray(drain_head, dtype=np.float64),
        float(cell_area_m2),
        max(float(sync_s), 1e-9),
        GRAVITY,
        WEIR_CD,
        ORIFICE_CD,
        SURCHARGE_CD,
        DRY_STREET_M,
        buffers.q_inlet,
        buffers.q_surcharge,
        buffers.q_inlet_cell,
        buffers.q_surcharge_cell,
    )
    return ExchangeResult(
        q_inlet_node=buffers.q_inlet,
        q_surcharge_node=buffers.q_surcharge,
        q_inlet_cell=buffers.q_inlet_cell,
        q_surcharge_cell=buffers.q_surcharge_cell,
    )

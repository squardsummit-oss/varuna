"""The coupled Twin run: rain cube -> depth maps end to end
(SPEC.md 11.3-11.5, Appendix A).

This is the top-level entry point that Phase 5's cycle orchestrator calls. It takes
everything Sky produced (a rain cube) and everything the city pipeline built (terrain,
drain graph), and returns a :class:`~varuna_twin.types.TwinResult` with depth snapshots
every 5 minutes, drain heads, surcharge discharges and edge flows.

**What it does, in order:**

For each 5-minute forecast step:

1. **Effective rain**  -  ``hydrology.effective_rain`` turns the raw rain rate from Sky into
   the net rate that reaches the surface, after depression storage and SCS-CN infiltration.

2. **Coupling loop**  -  the step is divided into ``sync_s`` intervals (5 s default). At each
   interval boundary:

   a. ``coupling.compute_exchange`` reads the surface depth and drain heads and computes
      the inlet capture and surcharge fluxes (SPEC.md 11.5, Appendix A);
   b. ``swe2d.SurfaceStepper.advance`` sub-steps the 2D solver under the CFL rule for
      ``sync_s``, with the rain, capture and surcharge frozen as source terms - the same
      arithmetic as ``swe2d.run_surface``, with its per-call setup done once per run. When the
      CFL rule splits the sync, the whole capture is taken in its first sub-step
      (``coupling.advance_surface_with_capture``), so the street gives up exactly what the
      drains accepted;
   c. ``drain1d.simulate`` sub-steps the 1D solver at ``inner_dt_s`` for ``sync_s``,
      with the same capture and surcharge frozen.

   The two solvers run in series within each sync interval, not in parallel  -  the
   alternation is the coupling's consistency guarantee.

3. **Snapshot**  -  the depth field and drain state are recorded at the output time.

**Hot start** (task P4.2). ``TwinInputs.initial_state`` resumes from a :class:`TwinState`
instead of a dry city with empty pipes, and every run returns ``final_state`` at its last
output time. Six steps then a six-step resume from that state are array-equal to twelve steps
in one run. The state's fingerprint must match the city it is loaded against, and its
``valid_ts`` must equal ``t0``; either mismatch raises naming what differs. Outfall heads are
still pinned at the resume ``t0``, so a tide series that changed since the checkpoint sets the
boundary rather than the checkpoint's copy of it.

**Mass balance** (SPEC.md 11.3). The combined surface + drain volume is audited at the
end of the run: ``|(V_end - V_start) - (V_in - V_out)| / V_in < 0.1%``, where ``V_in`` is what
entered during this run only. ``V_start`` is zero on a cold start. The audited system is the
city - land cells and pipes; the sea cells are its boundary. They start at the ``t0`` stage, so
the sea's own volume is storage rather than inflow, and what they pass to the city is one term,
``sea_to_land = tide_in - tide_out - (change in sea storage)``, counted in ``V_in`` when positive
and ``V_out`` when negative, beside the rain and any backflow through a tidal outfall. Below
:data:`MASS_BALANCE_MIN_VOLUME_M3` of inflow the ratio means nothing, and the residual itself
is held to :data:`MASS_BALANCE_MIN_RESIDUAL_M3` instead.
The surface's own audit runs every 100 CFL sub-steps across the run inside the
``SurfaceStepper`` and once more at the end; the drain's runs inside ``simulate``. This final
audit is the coupled one that catches exchange leaks, and ``twin.run.ledger`` breaks its
residual into the surface's own, the drain's own and the two exchange gaps - because which
side lost the water is the difference between a number to report and a bug to fix. On the
08:40 cycle of 2 July 2019 both solvers were exactly closed and the whole 6,064.7 m3 sat in
the surcharge gap.

**Performance** (SPEC.md 14). The target is a 3-hour AOI run <= 8 s on an 8-core CPU.
The 2D solver is Numba-parallel; the 1D solver is vectorised numpy. The coupling loop adds
one ``compute_exchange`` call per sync interval (~ 60 per step, 2160 per run), each of which
scatters a few thousand nodes onto the grid  -  milliseconds.

Determinism (rule 8): same ``TwinInputs``, same ``TwinResult`` bytes. The rain cube is
seeded upstream (Sky); the hydrology is pure arithmetic; the 2D solver's parallel loop
writes per-row; the 1D solver uses ``np.bincount`` in edge order.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta
from time import perf_counter
from typing import TYPE_CHECKING, cast

import numpy as np
import structlog

from varuna_twin import drain1d, swe2d
from varuna_twin.coupling import (
    ExchangeBuffers,
    advance_surface_with_capture,
    compute_exchange,
    scatter_node_volumes_to_cells,
)
from varuna_twin.hydrology import HydrologyState, effective_rain
from varuna_twin.types import (
    DrainState,
    MassBalance,
    SurfaceState,
    TwinFingerprint,
    TwinInputs,
    TwinResult,
    TwinState,
)

if TYPE_CHECKING:  # pragma: no cover
    from numpy.typing import NDArray

    from varuna_twin.types import DrainNetwork

log = structlog.get_logger("varuna.twin.runner")

__all__ = [
    "run_twin",
]

MASS_BALANCE_TOLERANCE = 1e-3
"""Combined surface + drain mass balance budget: 0.1 % of the run's own inflow."""

MASS_BALANCE_MIN_VOLUME_M3 = 10.0
"""Inflow below which the relative audit is meaningless."""

MASS_BALANCE_MIN_RESIDUAL_M3 = MASS_BALANCE_TOLERANCE * MASS_BALANCE_MIN_VOLUME_M3
"""The absolute residual allowed when the inflow is below :data:`MASS_BALANCE_MIN_VOLUME_M3`.

Derived, not chosen: it is the largest residual the relative budget tolerates at the smallest
inflow it audits, so the two checks meet at the threshold instead of leaving a gap in which a
hot-started run with no new rain - large storage, nothing entering - could lose water unseen."""


def run_twin(
    inputs: TwinInputs,
    *,
    on_step: Callable[[int, int], None] | None = None,
    sea_ledger: dict[str, float] | None = None,
) -> TwinResult:
    """Run the coupled 2D+1D simulation from a rain cube to depth maps.

    This is the single function Phase 5's cycle orchestrator calls. Everything it needs
    is in ``inputs``; everything it produces is in the return value.

    Args:
        inputs: terrain, drain network, rain cube (n_steps, rows, cols) in mm/h,
                time origin, step cadence, tide series, sync and inner-step settings.
        on_step: called as ``on_step(k, n)`` after output step ``k`` of ``n`` has been
                snapshotted (``k`` counts from 1). A progress hook and nothing else: it is
                handed two integers, never the solver's state, so it cannot change a number,
                and a test pins the run bitwise equal with and without it. An exception it
                raises propagates out of the run, which is how a caller cancels one.
        sea_ledger: a dict the run fills, once it has finished, with the boundary volumes its
                closing log line carries - ``rain_in_m3``, ``tide_in_m3``, ``tide_out_m3``,
                the signed ``outfall_m3`` (negative is the sea pushing back up a pipe), and
                ``sea_to_land_m3`` with the two sea storages it is formed from. ``tide_in_m3``
                and ``tide_out_m3`` are the clamp's own tallies on the sea cells, *after* the
                sea was filled to its ``t0`` stage, so they are the tide's rise and fall and not
                the sea filling itself. On a terrain with its own sea raster (``terrain.sea``)
                the sea and the city exchange water by two paths and no other, and each has its
                own key: ``sea_to_land_m3`` is the face exchange alone - the water that crossed
                the faces between sea cells and land cells, net, positive inland - and
                ``outfall_m3`` is the pipe exchange, net, positive out to sea. ``sea_to_land_m3``
                carries no rain, because rain on a sea cell is zeroed before it is booked, and no
                drain exchange, because a node on a sea cell neither captures nor surcharges.
                On a terrain without the raster the sea is the tidal outfalls' cells, and the
                term also carries the rain that fell on them, which the clamp returns to the sea,
                and whatever the interior nodes on those cells exchanged - exactly as before.
                :class:`~varuna_twin.types.MassBalance` counts it in ``volume_in_m3`` when
                positive and ``volume_out_m3`` when negative. An
                out-parameter rather than a new result field so the result type, and every run
                written from it, stays exactly as it was; it is written after the last step and
                read by nothing inside the run.

    Returns:
        A :class:`~varuna_twin.types.TwinResult` with depth snapshots, drain heads,
        surcharge, edge flows, timing and mass balance.
    """
    started = perf_counter()
    terrain = inputs.terrain
    network = inputs.network
    rain_cube = np.asarray(inputs.rain_mm_h, dtype=np.float64)
    n_steps = rain_cube.shape[0]
    step_s = float(inputs.step_min) * 60.0
    sync_s = float(inputs.sync_s)
    inner_dt_s = float(inputs.inner_dt_s)

    initial = inputs.initial_state
    log.info(
        "twin.run.start",
        n_steps=n_steps,
        step_min=inputs.step_min,
        grid=terrain.shape,
        n_nodes=network.n_nodes,
        n_edges=network.n_edges,
        sync_s=sync_s,
        inner_dt_s=inner_dt_s,
        start="cold" if initial is None else "hot",
    )

    # The identity a checkpoint of this run carries, and the one a resumed state must match.
    fingerprint = TwinFingerprint.of(terrain, network, _sea_provenance(terrain, inputs.provenance))

    # ---- Prepare the solvers ------------------------------------------------
    kernel_terrain = swe2d.prepare_terrain(terrain)
    if initial is not None:
        _check_resumable(initial, inputs, fingerprint, kernel_terrain.shape)
    surface = (
        swe2d.dry_state(kernel_terrain)
        if initial is None
        else SurfaceState(
            h=_owned(initial.h),
            qx=_owned(initial.qx),
            qy=_owned(initial.qy),
        )
    )
    # Sized from the *kernel's* grid, which is the one the exchange scatters onto. Sizing it
    # from `terrain.z` instead wrote past the end of the buffer - `prepare_terrain` can pad -
    # and Numba does not bounds-check, so it was an access violation rather than an IndexError.
    exchange_buffers = ExchangeBuffers.allocate(network.n_nodes, kernel_terrain.z.shape)
    # The drain's applied surcharge for the current sync, and its scatter onto the grid. Owned
    # here rather than inside `simulate` and `scatter_node_volumes_to_cells` for the reason
    # `ExchangeBuffers` documents: both are consumed within the sync that filled them, and a
    # buffer the callee owned would alias across calls.
    applied_surcharge_m3 = np.zeros(network.n_nodes, dtype=np.float64)
    surcharge_cell_buffer = np.zeros(kernel_terrain.z.shape, dtype=np.float64)
    hydro_state = HydrologyState.for_terrain(terrain)
    if initial is not None:
        # `retention_s_mm` stays the one just derived from this city's `cn.tif`; only the two
        # event accumulators are memory.
        hydro_state.depression_remaining_mm = _owned(initial.depression_remaining_mm)
        hydro_state.cumulative_rain_mm = _owned(initial.cumulative_rain_mm)
    # `colour=True`: this is the caller P4.6's colouring was measured for - 10,800 inner steps
    # over 49,770 edges, where the coloured kernel measured 3.49x the serial one at the runner's
    # own call shape. `prepare` costs 55 ms more for it, once.
    drain_solver = drain1d.prepare(network, colour=True)

    # Initial drain state: heads at the inverts (empty pipes), or the checkpoint's.
    if initial is None:
        drain_state = DrainState(
            head=np.asarray(network.z_invert, dtype=np.float64).copy(),
            flow=np.zeros(network.n_edges, dtype=np.float64),
        )
    else:
        drain_state = DrainState(
            head=_owned(initial.drain_head),
            flow=_owned(initial.drain_flow),
        )
    # Sea boundary mask for the 2D solver
    sea_mask = _build_sea_mask(terrain, network)
    # The city's own sea raster, when the terrain carries one: the cells that exchange nothing
    # with the drains and take no rain into the city's ledger. `None` on a terrain without it,
    # which keeps such a run - every nest, every test grid, every city built before the sea step -
    # bit for bit what it was, tidal outfall cells and all.
    city_sea = _city_sea(terrain)

    # `_tide_at` needs to know whether the domain has a sea at all, so it can hold it at mean sea
    # level when the bundle has no tide series rather than leaving the solver without a level.
    has_sea = sea_mask is not None and bool(sea_mask.any())

    # Pinned on a resume too: the outfall heads belong to the boundary series this run was given,
    # not to the one the checkpoint was written under. The drain kernel pins them again at every
    # inner step, and the coupling takes nothing at a fixed-head node, so on an unchanged series
    # this changes no number - the resume-identity test covers a rising tide. It is given the same
    # stage the loop gives the drain: with a sea and no series that is mean sea level, not "none",
    # which would stand a tidal outfall below 0 m at its invert until the first inner step.
    drain1d.pin_boundaries(
        drain_solver, drain_state.head, _tide_at(inputs, inputs.t0, has_sea=has_sea)
    )

    # The sea starts at its own level, not empty. Its cells are clamped to the stage at the start
    # of every sub-step anyway, so filling them here is exactly the clamp the first sub-step would
    # make - no depth anywhere changes - but it books the sea's own volume as water already in the
    # domain rather than as tide that crossed the boundary during the run. Without it a real
    # coastline fills 19 km2 of sea in the first sync and reports that as 23 Mm3 of inflow against
    # 3.18 Mm3 of rain, which dilutes every error the audit exists to catch about eight times.
    # The index is the stepper's own (buildings excluded), so the two agree on which cells are sea.
    sea_index = swe2d._sea_index(sea_mask, kernel_terrain) if has_sea else None
    if sea_index is not None:
        _fill_sea(surface.h, kernel_terrain, sea_index, _tide_at(inputs, inputs.t0, has_sea=True))
    sea_stored_start = _sea_volume_m3(surface.h, sea_index, kernel_terrain.cell_area_m2)

    # Built once: the sea-cell index, the kernel workspace and the run-long mass ledger. Doing
    # that inside every one of the 2,160 surface calls cost more than the kernels (P4.6).
    surface_stepper = swe2d.SurfaceStepper(surface, kernel_terrain, sea_mask=sea_mask)
    # The capture front-loaded into each sync's first CFL sub-step (see
    # `coupling.advance_surface_with_capture`), and the zero raster the rest of the sync runs on.
    capture_buffer = np.zeros(kernel_terrain.z.shape, dtype=np.float64)
    no_capture = np.zeros(kernel_terrain.z.shape, dtype=np.float64)

    # Sinks (pumps/tanks)  -  not wired until Phase 7, but the interface is ready
    sinks, sink_state = drain1d.no_sinks()
    if initial is not None:
        filled = np.array(initial.sink_filled_m3, dtype=np.float64, order="C")
        if filled.shape != sink_state.filled_m3.shape:
            raise ValueError(
                f"initial_state carries {filled.shape[0] if filled.ndim else 0} pump or tank "
                f"volumes but this run has {sinks.n_units} units; a checkpoint written under a "
                "different pump plan cannot be resumed"
            )
        sink_state.filled_m3 = filled

    # Water already in the domain when the run starts: zero on a cold start but for the sea.
    # Taken after the boundary pin and the sea fill, though `stored_volume_m3` leaves fixed-head
    # nodes out either way. The city's own share, without the sea, is formed at the close.
    surface_stored_start = surface.volume_m3(kernel_terrain.cell_area_m2)
    drain_stored_start = drain1d.stored_volume_m3(drain_solver, drain_state.head)

    # ---- Output buffers -----------------------------------------------------
    depth_out = np.zeros((n_steps, terrain.n_rows, terrain.n_cols), dtype=np.float64)
    head_out = np.zeros((n_steps, network.n_nodes), dtype=np.float64)
    q_surcharge_out = np.zeros((n_steps, network.n_nodes), dtype=np.float64)
    edge_flow_out = np.zeros((n_steps, network.n_edges), dtype=np.float64)

    # ---- Volume tracking for the combined mass balance ----------------------
    total_rain_in_m3 = 0.0
    total_tide_in_m3 = 0.0
    total_tide_out_m3 = 0.0
    total_drain_inlet_m3 = 0.0
    """Volume the 1D network actually accepted at its inlets, summed over every sync.

    Not the same number as the surface's own inlet tally: the surface gives up
    ``min(wanted, available)`` and the drain accepts ``min(offered, node capacity)``, so the two
    can disagree and the difference is water neither solver holds. Carried separately so the
    closing ledger can name that gap instead of burying it in the residual."""

    total_drain_surcharge_m3 = 0.0
    """Volume the network pushed back out through its manholes, the mirror of the above."""

    total_sink_m3 = 0.0
    """Volume withdrawn by pumps and holding tanks. Zero until they are wired (Phase 7), and
    counted here so that wiring them cannot silently open a hole in the audit."""

    total_outfall_m3 = 0.0
    """Net volume the drain network discharged at its outfalls, positive out to sea.

    Without this the audit does not close: water that falls on a street, is captured by an
    inlet and leaves through a pipe simply disappears from it. The error that hides was small
    while the Twin ran on a rain field that barely wetted the city, and grew with the water."""

    times: list = []
    notes: list[str] = []
    stage_ms: dict[str, int] = {}

    t_hydro = 0
    t_surface = 0
    t_drain = 0
    t_coupling = 0

    # ---- Main loop: one iteration per 5-minute forecast step ----------------
    for step_idx in range(n_steps):
        step_time = inputs.t0 + timedelta(minutes=(step_idx + 1) * inputs.step_min)
        times.append(step_time)

        # 1. Effective rain for this step
        t0 = perf_counter()
        rain_rate = rain_cube[step_idx]  # mm/h
        r_eff_ms = effective_rain(rain_rate, terrain, hydro_state, step_s)
        if city_sea is not None:
            # Rain on the sea is the sea's. The clamp takes it straight back off every sea cell,
            # so booked as rain it only crossed the ledger twice - in as `rain_in`, out as
            # `tide_out` - and made `sea_to_land` mostly the sea's own rain: -498,972 m3 on a
            # 12-step Chennai run at 50 mm/h, 428,580 m3 of which had fallen on its 9,524 sea
            # cells. A land depth can move only where a sea cell's own water limits what it gives
            # a neighbour (`swe2d._update_depth` pass A); the Dirichlet sea is meant to be
            # unlimited there anyway.
            r_eff_ms = np.where(city_sea, 0.0, r_eff_ms)
        t_hydro += int((perf_counter() - t0) * 1000)

        # The rain raster is validated here, once per 5-minute step, not once per sync.
        t0 = perf_counter()
        surface_stepper.set_rain(r_eff_ms)
        t_surface += int((perf_counter() - t0) * 1000)

        # Track rain volume: r_eff_ms is m/s, over step_s seconds and cell_area
        total_rain_in_m3 += float(np.sum(r_eff_ms)) * step_s * kernel_terrain.cell_area_m2

        # 2. Coupling loop: divide the step into sync intervals
        n_syncs = max(round(step_s / sync_s), 1)
        actual_sync_s = step_s / n_syncs

        # Accumulate surcharge over the step for the snapshot
        step_surcharge = np.zeros(network.n_nodes, dtype=np.float64)
        step_surcharge_count = 0

        for sync_idx in range(n_syncs):
            # Time within the step for tide lookup
            sync_time = inputs.t0 + timedelta(seconds=step_idx * step_s + sync_idx * actual_sync_s)
            tide_stage = _tide_at(inputs, sync_time, has_sea=has_sea)

            # 2a. Compute exchange fluxes
            t0 = perf_counter()
            exchange = compute_exchange(
                surface_h=surface.h,
                surface_z=kernel_terrain.z,
                drain_head=drain_state.head,
                network=network,
                solver=drain_solver,
                cell_area_m2=kernel_terrain.cell_area_m2,
                sync_s=actual_sync_s,
                # The result is read and applied before the next sync computes another, so one
                # set of buffers serves the whole run rather than four allocations per sync.
                out=exchange_buffers,
                # A node under a building exchanges nothing: the cell it would exchange with is
                # one the 2D solver holds at zero depth and skips before it tallies, so anything
                # sent there vanishes from the audit (task P4.5).
                blocked=kernel_terrain.blocked,
                # Nor does a node on the city's sea: the clamp refills its cell every sub-step,
                # so its inlet was an ungated pipe from the sea at ground minus 1.5 m, past the
                # coast wall, the tidal-outfall invert and any flap gate, booked as `sea_to_land`
                # where the audit cannot see it. 148 of Mumbai's interior nodes sit on sea cells
                # (106 on the Mithi buffer, 42 on open sea), all 148 under water at the +2.218 m
                # crest. `None` without a sea raster, so such a run exchanges as it always did.
                sea=city_sea,
            )
            t_coupling += int((perf_counter() - t0) * 1000)

            # 2b. Advance the 1D drains, **before** the surface (task P4.5).
            #
            # The order is the mass balance, not a preference. Both solvers are given the same
            # frozen exchange rates, and neither reads the other's state while it sub-steps, so
            # stepping the drain first changes no depth by itself. What it changes is which of
            # the two gets the last word on how much water moved: the drain's supply check scales
            # a node's whole outflow - pipes, manhole and sinks together - when they would take
            # more than the node holds, so a node draining hard through its pipes surcharges less
            # than `compute_exchange` asked for. Handing the surface the requested rate while the
            # drain applied the scaled one put water on the street that no node ever gave up:
            # 6,052.7 m3 on this cycle, 99.8 % of a 0.204 % failure against a 0.1 % budget.
            t0 = perf_counter()
            drain_run = drain1d.simulate(
                drain_solver,
                drain_state,
                duration_s=actual_sync_s,
                dt_s=inner_dt_s,
                q_inlet=exchange.q_inlet_node,
                q_surcharge=exchange.q_surcharge_node,
                tide_stage_m=tide_stage,
                sinks=sinks,
                sink_state=sink_state,
                applied_surcharge_out=applied_surcharge_m3,
            )
            total_outfall_m3 += drain_run.boundary_m3
            total_drain_inlet_m3 += drain_run.inlet_m3
            total_drain_surcharge_m3 += drain_run.surcharge_m3
            total_sink_m3 += drain_run.sink_m3
            t_drain += int((perf_counter() - t0) * 1000)

            # The surcharge the snapshot reports is the one that reached the street, so that the
            # console's surcharge markers stand for water a manhole actually emitted.
            t0 = perf_counter()
            surcharge_cell = scatter_node_volumes_to_cells(
                network,
                drain_solver,
                applied_surcharge_m3,
                actual_sync_s,
                kernel_terrain.cell_area_m2,
                out=surcharge_cell_buffer,
            )
            t_coupling += int((perf_counter() - t0) * 1000)
            # In place: `step_surcharge += applied / sync` allocated a 50,110-element temporary
            # on each of the 2,160 syncs. Volumes here; the snapshot divides by the total time.
            np.add(step_surcharge, applied_surcharge_m3, out=step_surcharge)
            step_surcharge_count += 1

            # 2c. Advance the 2D surface with the volume the drains actually emitted, and hand over
            # the whole capture the drains were promised. The drains have already accepted it in
            # full, so a surface that gives up less - because its neighbours drained the cell in
            # an earlier CFL sub-step - invents the difference (`inlet_gap_m3`).
            t0 = perf_counter()
            moved = advance_surface_with_capture(
                surface_stepper,
                actual_sync_s,
                q_inlet_ms=exchange.q_inlet_cell,
                q_surcharge_ms=surcharge_cell,
                tide_stage_m=tide_stage,
                capture_buffer=capture_buffer,
                no_capture=no_capture,
            )
            t_surface += int((perf_counter() - t0) * 1000)
            total_tide_in_m3 += moved[4]
            total_tide_out_m3 += moved[5]

        # 3. Snapshot
        depth_out[step_idx] = surface.h.copy()
        head_out[step_idx] = drain_state.head.copy()
        if step_surcharge_count > 0:
            # Volume over the step, back to the mean rate the snapshot reports in m3/s.
            q_surcharge_out[step_idx] = step_surcharge / (step_surcharge_count * actual_sync_s)
        edge_flow_out[step_idx] = drain_state.flow.copy()

        # 4. Progress, after the snapshot so a step reported done is a step whose depth exists.
        if on_step is not None:
            on_step(step_idx + 1, n_steps)

    # The surface's closing audit: the window since its last 100-sub-step check, and the run.
    t0 = perf_counter()
    surface_run_total = surface_stepper.finish()
    t_surface += int((perf_counter() - t0) * 1000)

    # ---- Stage timings -------------------------------------------------------
    elapsed_ms = round((perf_counter() - started) * 1000.0)
    stage_ms = {
        "hydrology_ms": t_hydro,
        "surface_ms": t_surface,
        "drain_ms": t_drain,
        "coupling_ms": t_coupling,
        "total_ms": elapsed_ms,
    }

    # ---- Combined mass balance -----------------------------------------------
    surface_stored = surface.volume_m3(kernel_terrain.cell_area_m2)
    drain_stored = drain1d.stored_volume_m3(drain_solver, drain_state.head)

    # ---- The sea's side of the ledger ------------------------------------------------------
    # The audited system is the city - land cells and pipes - and the sea is its boundary. The
    # clamp tallies say what the stage put into and took out of the sea cells; what the sea cells
    # passed on to the city is that, less what they kept:
    #
    #     sea_to_land = tide_in - tide_out - (sea storage at the end - at the start)
    #
    # Positive is sea that crossed onto land; negative is the city draining to the sea. With the
    # terrain's own sea raster it is the face exchange alone - sea cells to land cells across the
    # shoreline - because nothing else changes a sea cell's water but the clamp: its rain was
    # zeroed before it was booked, and its nodes neither capture nor surcharge. The pipe exchange
    # is `outfall_m3`. On a terrain without the raster the sea is the tidal outfalls' cells and
    # the term also carries the rain that fell on them, which the clamp returns to the sea, and
    # what interior nodes on those cells exchanged. The residual below is algebraically the one
    # the whole-domain ledger gave; what changes is the denominator, which no longer counts the
    # sea filling and rising as water the city received.
    # With no sea cell ever below the stage both sea terms are exactly zero and every number is
    # bit for bit the one the whole-domain ledger gave. That held on the Mumbai graph built before
    # the sea step, whose three tidal outfalls sit at 1.42-12.50 m, only while MUM-2019-07-02's
    # tide stopped at 09:40 (+1.236 m); running on to 12:40 it crests at +2.218 m, so from about
    # 09:52 the lowest of them is a sea cell below the stage on every cycle from 07:10 to 09:10.
    sea_stored_end = _sea_volume_m3(surface.h, sea_index, kernel_terrain.cell_area_m2)
    sea_to_land = total_tide_in_m3 - total_tide_out_m3 - (sea_stored_end - sea_stored_start)
    land_stored = surface_stored - sea_stored_end
    total_stored = land_stored + drain_stored
    stored_start = (surface_stored_start - sea_stored_start) + drain_stored_start
    # At a tide-locked outfall the sea pushes water back up the trunk - a negative `boundary_m3`,
    # and therefore an inflow like the sea that crosses a shoreline.
    total_in = total_rain_in_m3 + max(sea_to_land, 0.0) + max(-total_outfall_m3, 0.0)
    total_out = max(-sea_to_land, 0.0) + max(total_outfall_m3, 0.0)

    # The change in storage against what crossed the boundary, over the run's own inflow. The
    # water a hot start carries in is subtracted from the stored side, never added to the
    # denominator: see `MassBalance.error_fraction` for why.
    residual = (total_stored - stored_start) - (total_in - total_out)
    residual_limit: float | None = None
    if total_in > MASS_BALANCE_MIN_VOLUME_M3:
        error_fraction = abs(residual) / total_in
    else:
        error_fraction = 0.0
        residual_limit = MASS_BALANCE_MIN_RESIDUAL_M3

    # ---- Where the residual sits (task P4.5) --------------------------------------------
    # The audit above says how much water is unaccounted for; these three say which side of the
    # coupling lost it, which is the difference between a number to report and a bug to fix.
    # Each solver is closed against its own sources, and the two exchange terms are the volumes
    # one side gave up and the other never received.
    surface_residual = (surface_stored - surface_stored_start) - (
        surface_run_total.volume_rain_m3
        + surface_run_total.volume_surcharge_m3
        + surface_run_total.volume_tide_in_m3
        + surface_run_total.volume_created_m3
        - surface_run_total.volume_inlet_m3
        - surface_run_total.volume_tide_out_m3
    )
    drain_residual = (drain_stored - drain_stored_start) - (
        total_drain_inlet_m3
        + max(-total_outfall_m3, 0.0)
        - total_drain_surcharge_m3
        - total_sink_m3
        - max(total_outfall_m3, 0.0)
    )
    # Positive means the surface let go of water the network never took, i.e. it was destroyed;
    # negative means the network accepted water the surface never gave up, i.e. it was invented.
    inlet_gap = surface_run_total.volume_inlet_m3 - total_drain_inlet_m3
    surcharge_gap = total_drain_surcharge_m3 - surface_run_total.volume_surcharge_m3
    log.info(
        "twin.run.ledger",
        residual_m3=round(residual, 3),
        surface_residual_m3=round(surface_residual, 3),
        drain_residual_m3=round(drain_residual, 3),
        inlet_gap_m3=round(inlet_gap, 3),
        surcharge_gap_m3=round(surcharge_gap, 3),
        clamp_created_m3=round(surface_run_total.volume_created_m3, 6),
        rain_in_m3=round(total_rain_in_m3, 1),
        tide_in_m3=round(total_tide_in_m3, 1),
        tide_out_m3=round(total_tide_out_m3, 1),
        sea_to_land_m3=round(sea_to_land, 1),
        sea_stored_start_m3=round(sea_stored_start, 1),
        sea_stored_end_m3=round(sea_stored_end, 1),
        outfall_m3=round(total_outfall_m3, 1),
        drain_inlet_m3=round(total_drain_inlet_m3, 1),
        drain_surcharge_m3=round(total_drain_surcharge_m3, 1),
        surface_inlet_m3=round(surface_run_total.volume_inlet_m3, 1),
        surface_surcharge_m3=round(surface_run_total.volume_surcharge_m3, 1),
    )

    mass_balance = MassBalance(
        volume_in_m3=total_in,
        volume_out_m3=total_out,
        volume_stored_m3=total_stored,
        error_fraction=error_fraction,
        volume_stored_start_m3=stored_start,
        residual_m3=residual,
        residual_limit_m3=residual_limit,
        surface_residual_m3=surface_residual,
        drain_residual_m3=drain_residual,
        inlet_gap_m3=inlet_gap,
        surcharge_gap_m3=surcharge_gap,
    )

    if inputs.tide is None and has_sea:
        notes.append(
            "No tide series in this bundle, so the sea was held at mean sea level (0.0 m). "
            "The tide-lock behaviour a real event shows is absent by assumption, not by result."
        )

    carried = "" if initial is None else f", stored at start {stored_start:.1f} m3"
    if error_fraction > MASS_BALANCE_TOLERANCE and total_in > MASS_BALANCE_MIN_VOLUME_M3:
        notes.append(
            f"Coupled mass balance error {error_fraction:.3%} exceeds the 0.1% budget "
            f"(in {total_in:.1f} m3, stored {total_stored:.1f} m3, out {total_out:.1f} m3"
            f"{carried})"
        )
        log.warning(
            "twin.run.mass_balance_warning",
            error_fraction=error_fraction,
            total_in_m3=total_in,
            total_stored_m3=total_stored,
            stored_start_m3=stored_start,
        )
    elif residual_limit is not None and abs(residual) > residual_limit:
        notes.append(
            f"Coupled mass balance residual {residual:+.3f} m3 exceeds the "
            f"{residual_limit:.3f} m3 limit that applies while inflow is below "
            f"{MASS_BALANCE_MIN_VOLUME_M3:.0f} m3 (in {total_in:.3f} m3, stored "
            f"{total_stored:.1f} m3, out {total_out:.3f} m3{carried})"
        )
        log.warning(
            "twin.run.mass_balance_warning",
            residual_m3=residual,
            residual_limit_m3=residual_limit,
            total_in_m3=total_in,
            stored_start_m3=stored_start,
        )

    final_state = TwinState(
        valid_ts=inputs.t0 + timedelta(minutes=n_steps * inputs.step_min),
        h=_frozen(surface.h),
        qx=_frozen(surface.qx),
        qy=_frozen(surface.qy),
        drain_head=_frozen(drain_state.head),
        drain_flow=_frozen(drain_state.flow),
        sink_filled_m3=_frozen(sink_state.filled_m3),
        depression_remaining_mm=_frozen(hydro_state.depression_remaining_mm),
        cumulative_rain_mm=_frozen(hydro_state.cumulative_rain_mm),
        fingerprint=fingerprint,
    )

    if sea_ledger is not None:
        sea_ledger.update(
            {
                "rain_in_m3": float(total_rain_in_m3),
                "tide_in_m3": float(total_tide_in_m3),
                "tide_out_m3": float(total_tide_out_m3),
                "outfall_m3": float(total_outfall_m3),
                "sea_to_land_m3": float(sea_to_land),
                "sea_stored_start_m3": float(sea_stored_start),
                "sea_stored_end_m3": float(sea_stored_end),
            }
        )

    log.info(
        "twin.run.done",
        n_steps=n_steps,
        elapsed_ms=elapsed_ms,
        mass_balance_error=round(error_fraction, 6),
        surface_stored_m3=round(surface_stored, 1),
        drain_stored_m3=round(drain_stored, 1),
        outfall_m3=round(total_outfall_m3, 1),
        # The land's peak, not the sea's: the sea is held at the tide, so on Mumbai's coastline
        # the whole-grid maximum is the deepest sea cell at the crest (2.58 m), never a street.
        peak_depth_m=round(_land_peak_m(depth_out, city_sea), 3),
        **stage_ms,
    )

    return TwinResult(
        depth_m=depth_out,
        head_m=head_out,
        q_surcharge=q_surcharge_out,
        edge_flow=edge_flow_out,
        times=tuple(times),
        mass_balance=mass_balance,
        final_state=final_state,
        stage_ms=stage_ms,
        notes=tuple(notes),
    )


# ============================================================================ helpers


def _check_resumable(
    state: TwinState,
    inputs: TwinInputs,
    expected: TwinFingerprint,
    grid_shape: tuple[int, int],
) -> None:
    """Refuse a state that does not belong to this city, this instant or these array sizes.

    Raises:
        ValueError: naming every difference, so a rebuilt city reads as a rebuilt city rather
            than as a shape error three calls deep in a kernel.
    """
    differences = list(state.fingerprint.differences(expected))
    if state.valid_ts != inputs.t0:
        differences.append(
            f"valid_ts: checkpoint {state.valid_ts.isoformat()}, "
            f"this run starts {inputs.t0.isoformat()}"
        )
    if differences:
        raise ValueError("initial_state cannot be resumed on this run: " + "; ".join(differences))
    # The fingerprint vouches for the sizes; a hand-built state could still disagree with it.
    sizes = {
        "h": grid_shape,
        "qx": grid_shape,
        "qy": grid_shape,
        "depression_remaining_mm": grid_shape,
        "cumulative_rain_mm": grid_shape,
        "drain_head": (inputs.network.n_nodes,),
        "drain_flow": (inputs.network.n_edges,),
    }
    for name, shape in sizes.items():
        array = np.asarray(getattr(state, name))
        if array.shape != shape:
            raise ValueError(
                f"initial_state.{name} has shape {array.shape}, expected {shape} for this run"
            )
        if not np.all(np.isfinite(array)):
            raise ValueError(f"initial_state.{name} contains non-finite values")
    if bool(np.any(np.asarray(state.h) < 0.0)):
        raise ValueError(
            "initial_state.h has negative depths; a surface cannot hold less than none"
        )


def _owned(array: NDArray[np.floating]) -> NDArray[np.float64]:
    """A private, writable, C-contiguous float64 copy, so a resume never mutates its checkpoint."""
    return np.array(array, dtype=np.float64, order="C", copy=True)


def _frozen(array: NDArray[np.floating]) -> NDArray[np.float64]:
    """A read-only float64 copy for :class:`TwinState`, detached from the solver's buffers."""
    out = np.array(array, dtype=np.float64, order="C", copy=True)
    out.flags.writeable = False
    return out


MEAN_SEA_LEVEL_M = 0.0
"""Where the sea is held when a bundle carries no tide series.

A design storm has no tide table - `CHN-IDF-25yr` is a synthetic hyetograph, not a day - but a
coastal city still has sea cells, and the 2D solver has to be told what level they sit at. Refusing
to run was the previous behaviour and it made a first forecast for a newly onboarded coastal city
impossible. Mean sea level is the neutral assumption, it is what a design storm is normally
evaluated against, and the run's notes say it was assumed rather than measured (rule 6)."""


def _tide_at(inputs: TwinInputs, when, *, has_sea: bool = False) -> float | None:
    """Tide stage at a given time.

    Falls back to mean sea level when there is no series but the domain has sea cells: see
    :data:`MEAN_SEA_LEVEL_M`. Without sea cells it stays None and no boundary is applied.
    """
    if inputs.tide is None:
        return MEAN_SEA_LEVEL_M if has_sea else None
    return inputs.tide.at(when)


def _fill_sea(
    h: NDArray[np.floating],
    kernel_terrain: swe2d.KernelTerrain,
    sea_index: tuple[NDArray[np.intp], NDArray[np.intp]],
    stage_m: float | None,
) -> None:
    """Stand the sea cells at the ``t0`` stage, in place, before any storage is measured.

    ``swe2d._apply_tide`` itself - the clamp every sub-step opens with - so the first sub-step's
    own clamp finds nothing left to do and the depths the run produces are the ones it always
    produced; the tallies it returns are dropped on purpose, because the sea's volume at ``t0`` is
    storage the domain starts with, not tide that crossed its edge during the run.
    """
    if stage_m is None:
        return
    depth = cast("NDArray[np.float64]", h)  # the solver's own float64 buffer
    swe2d._apply_tide(depth, kernel_terrain.z, sea_index, stage_m, kernel_terrain.cell_area_m2)


def _sea_volume_m3(
    h: NDArray[np.floating],
    sea_index: tuple[NDArray[np.intp], NDArray[np.intp]] | None,
    cell_area_m2: float,
) -> float:
    """Water standing on the sea cells, in m3; exactly ``0.0`` when there are none or all are dry.

    Exactly zero matters: the closing ledger subtracts this from the whole surface, and a dry sea
    has to leave that sum's bits alone for a run without a coastline to audit as it always did.
    """
    if sea_index is None:
        return 0.0
    rows, cols = sea_index
    return float(np.sum(h[rows, cols])) * cell_area_m2


SEA_PROVENANCE_KEY = "sea_mask_sha256"
"""The fingerprint provenance key a terrain's own sea raster is recorded under."""


def _sea_provenance(terrain, provenance: Mapping[str, str] | None) -> Mapping[str, str] | None:
    """``provenance`` plus the digest of ``terrain.sea`` when the terrain carries one.

    The sea is where the tide is imposed, so a checkpoint written under one coastline must not
    resume under another: it would hold sea water on cells that are now land. Recorded as
    provenance because :class:`TwinFingerprint` has no field for it yet; a terrain without a sea
    raster adds nothing, so its fingerprint is the one every earlier run carried.
    """
    sea = getattr(terrain, "sea", None)
    if sea is None:
        return provenance
    import hashlib

    data = np.ascontiguousarray(np.asarray(sea, dtype=np.bool_), dtype="u1")
    return {**(provenance or {}), SEA_PROVENANCE_KEY: hashlib.sha256(data.tobytes()).hexdigest()}


def _city_sea(terrain) -> NDArray[np.bool_] | None:
    """``terrain.sea`` as a boolean mask on the grid, or ``None`` when the terrain has no raster."""
    sea = getattr(terrain, "sea", None)
    if sea is None:
        return None
    sea = np.asarray(sea, dtype=np.bool_)
    if sea.shape != tuple(terrain.shape):
        raise ValueError(f"terrain.sea has shape {sea.shape}, expected {tuple(terrain.shape)}")
    return sea


def _land_peak_m(depth: NDArray[np.floating], sea: NDArray[np.bool_] | None) -> float:
    """Deepest water off the city's sea raster over the whole run, in metres; 0 on an empty grid.

    Without a raster every cell counts, as before.
    """
    if sea is None:
        return float(np.max(depth)) if depth.size else 0.0
    land = depth[:, ~sea]
    return float(np.max(land)) if land.size else 0.0


def _build_sea_mask(
    terrain,
    network: DrainNetwork,
) -> NDArray[np.bool_] | None:
    """Build the sea boundary mask for the 2D solver.

    With the terrain's own sea raster (``terrain.sea``, the open sea and tidal creeks the city
    build classifies from land cover and the DEM) the mask is that raster and nothing else. A
    tidal outfall is not added: its drain head is pinned to the stage already, which is the whole
    of its coupling to the tide, and the city build makes every outfall within two cells of the
    sea tidal - so its cell can be land. Added, that land became a Dirichlet sea cell: on the
    Mumbai build 4 of the 21 tidal outfalls sit on the walled shore ring and would be held dry
    for good, a drain hole through the wall, and MUM-N049187 and MUM-N049198 stand at 1.5 m
    behind it, where the extended tide would impose the sea from about +1.5 m up to its +2.218 m
    crest and flood the land around them.

    Without the raster the mask is the tidal outfalls' cells, which is all there was before a
    coastline existed - so such a run is exactly what it was. With neither, there is no sea.
    """
    sea = _city_sea(terrain)
    if sea is not None:
        return sea.copy() if sea.any() else None

    mask = np.zeros(terrain.shape, dtype=np.bool_)
    boundary = np.asarray(network.boundary)
    tidal = boundary == drain1d.BOUNDARY_TIDAL
    row = np.asarray(network.cell_row, dtype=np.intp)
    col = np.asarray(network.cell_col, dtype=np.intp)
    for i in np.flatnonzero(tidal):
        if row[i] >= 0 and col[i] >= 0:
            mask[int(row[i]), int(col[i])] = True

    if not np.any(mask):
        return None

    return mask

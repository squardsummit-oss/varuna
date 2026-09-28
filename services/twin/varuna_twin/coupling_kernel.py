"""The 1D-2D exchange, compiled (SPEC.md 11.5, task P4.6).

The companion to :mod:`varuna_twin.drain_kernel`, and for the same reason. `compute_exchange` runs
once per 5 s sync - 2,160 times in a three-hour cycle - over 50,110 nodes, and vectorised NumPy
does it in about twenty-five temporaries plus two `np.add.at` scatters. `np.add.at` is the
*unbuffered* scatter, which is correct where several inlets share a 30 m cell and roughly an order
of magnitude slower than a buffered one; at 108 million scattered nodes per run it was the single
largest remaining cost after the drain kernel landed.

This is one pass over nodes with the scatter written as an ordinary accumulating loop - which is
both faster and exactly as correct, because a serial loop accumulates duplicates by construction.

**`coupling.compute_exchange` stays and is the specification**, with `test_coupling_kernel_matches_numpy`
asserting the two agree. Appendix A's formulae are stated there, in one readable place.

**Determinism (rule 8).** Single-threaded, like the drain kernel: the cell scatter is a
read-modify-write that several nodes make to the same cell, and a parallel reduction would vary the
float addition order with the thread count.
"""

from __future__ import annotations

import numpy as np
from numba import njit

__all__ = ["exchange_kernel"]


@njit(fastmath=True, cache=True)
def exchange_kernel(
    # --- per-node topology and geometry ---------------------------------------------------
    row: np.ndarray,
    col: np.ndarray,
    z_ground: np.ndarray,
    kappa: np.ndarray,
    inlet_length: np.ndarray,
    inlet_area: np.ndarray,
    storage_area: np.ndarray,
    fixed_head: np.ndarray,
    blocked: np.ndarray,
    sea: np.ndarray,
    # --- state ----------------------------------------------------------------------------
    surface_h: np.ndarray,
    surface_z: np.ndarray,
    head: np.ndarray,
    # --- constants ------------------------------------------------------------------------
    cell_area_m2: float,
    dt: float,
    gravity: float,
    weir_cd: float,
    orifice_cd: float,
    surcharge_cd: float,
    dry_street_m: float,
    # --- outputs (allocated once by the caller) -------------------------------------------
    q_inlet: np.ndarray,
    q_surcharge: np.ndarray,
    q_inlet_cell: np.ndarray,
    q_surcharge_cell: np.ndarray,
) -> None:
    """Fill the four exchange arrays in place. Appendix A, node by node."""
    n_nodes = head.shape[0]
    rate_factor = 1.0 / cell_area_m2

    q_inlet_cell[:, :] = 0.0
    q_surcharge_cell[:, :] = 0.0

    for j in range(n_nodes):
        q_inlet[j] = 0.0
        q_surcharge[j] = 0.0

        r = row[j]
        c = col[j]
        if r < 0 or c < 0:
            continue  # no 2D cell: this node cannot exchange
        if blocked[r, c]:
            # A node under a building. `swe2d._update_depth` zeroes a blocked cell and skips it
            # before it tallies anything, so water sent there is destroyed silently: 5,185.4 m3
            # on the 08:40 cycle of 2 July 2019. There is no street here to surcharge onto.
            continue
        if sea[r, c]:
            # A node on the city's sea. The tide clamp refills its cell to the stage every
            # sub-step, so an inlet here is an ungated pipe from the sea, and surcharge here is
            # handed straight back to it. The pipes meet the sea only at tidal outfalls.
            continue

        h = surface_h[r, c]
        z = surface_z[r, c]
        h_pos = h if h > 0.0 else 0.0

        # ---- inlet capture -----------------------------------------------------------
        # Q_inlet = (1 - kappa) * min(1.66 L h^1.5, 0.6 A_o sqrt(2 g h), Q_avail)
        inlet = 0.0
        if not fixed_head[j] and h_pos >= dry_street_m:
            q_weir = weir_cd * inlet_length[j] * h_pos * np.sqrt(h_pos)
            q_orifice = orifice_cd * inlet_area[j] * np.sqrt(2.0 * gravity * h_pos)
            hydraulic = q_weir if q_weir < q_orifice else q_orifice

            # What the node can accept without going above ground, over this interval.
            depth_available = z_ground[j] - head[j]
            if depth_available > 0.0:
                q_avail = depth_available * storage_area[j] / dt
                capped = hydraulic if hydraulic < q_avail else q_avail
                inlet = (1.0 - kappa[j]) * capped

        # ---- surcharge, and its reverse ----------------------------------------------
        surface_level = z + h
        excess = head[j] - surface_level
        surcharge = 0.0

        if excess > 0.0:
            # The pipe pushes water onto the street. Capped at the volume that would bring the
            # head down to the street's water surface - unlimited, the orifice equation invents
            # water it does not hold (the 228x mass gain `coupling.py` records).
            manhole = storage_area[j]
            orifice = surcharge_cd * manhole * np.sqrt(2.0 * gravity * excess)
            emitted = excess * manhole / dt
            surcharge = orifice if orifice < emitted else emitted
        elif excess < 0.0 and head[j] > z_ground[j]:
            # The street drains into a surcharged pipe. Capped against what is standing on the
            # cell, or the surface goes negative and the deficit reappears downstream.
            manhole = storage_area[j]
            orifice = surcharge_cd * manhole * np.sqrt(2.0 * gravity * (-excess))
            on_street = h * cell_area_m2 / dt
            inlet += orifice if orifice < on_street else on_street

        if fixed_head[j]:
            # An outfall's head is imposed, not integrated: it neither captures nor surcharges.
            surcharge = 0.0
            inlet = 0.0

        q_inlet[j] = inlet
        q_surcharge[j] = surcharge

        # ---- scatter onto the grid ---------------------------------------------------
        # A serial accumulating loop, which is what `np.add.at` is doing more slowly. Several
        # inlets share a 30 m cell - they sit every 40 m along a road - so the accumulation is
        # the point, and a plain `cell[r, c] = ...` would drop every duplicate but the last.
        if not fixed_head[j]:
            q_inlet_cell[r, c] += inlet * rate_factor
            q_surcharge_cell[r, c] += surcharge * rate_factor

    # ---- the cell's water caps what all its nodes take together ------------------------
    # Each node above is limited against the street on its own, and up to eight share one 30 m
    # cell, so together they can ask for several times the water standing there. The surface then
    # hands over what it has and the drains accept what they asked for: the difference is water
    # neither solver holds (`coupling.cap_inlet_to_cell_water`). Scaled here, every node on an
    # over-asked cell by the same factor, so the cell's total is exactly its water.
    #
    # Capped nodes are marked by a negative value between the passes rather than by a second
    # array: an inlet is never negative, and a zero inlet stays -0.0, which is not below zero.
    any_capped = False
    for j in range(n_nodes):
        inlet = q_inlet[j]
        if inlet <= 0.0:
            continue
        r = row[j]
        c = col[j]
        asked = q_inlet_cell[r, c]
        limit = surface_h[r, c] / dt
        if asked > limit:
            q_inlet[j] = -(inlet * (limit / asked))
            any_capped = True
    if any_capped:
        # Rebuild the capped cells from their scaled nodes, in node order, so the cell still reads
        # as the sum of what the network is told it received.
        for j in range(n_nodes):
            if q_inlet[j] < 0.0:
                q_inlet_cell[row[j], col[j]] = 0.0
        for j in range(n_nodes):
            if q_inlet[j] < 0.0:
                q_inlet[j] = -q_inlet[j]
                q_inlet_cell[row[j], col[j]] += q_inlet[j] * rate_factor

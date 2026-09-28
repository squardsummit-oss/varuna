"""The 1D drain step, compiled (SPEC.md 11.4, task P4.6).

**Why this file exists.** A three-hour Mumbai cycle was ~115 s against an 8 s budget, and the cost
was not the 2D surface solver - that is already Numba - but `drain1d.step`, called 10,800 times at
a 1 s inner step over 49,770 edges and 50,110 nodes. Vectorised NumPy does that correctly and
slowly: every `np.where`, `np.minimum` and `np.bincount` in the step allocates a fresh array, so
one step walks 50k-element buffers about thirty times and the whole run moves tens of gigabytes
through memory to compute a few hundred megaflops.

This kernel fuses the same arithmetic into three passes - edges, nodes, edges - with no temporaries
at all. The algebra is unchanged, line for line.

**`drain1d.step` stays, and is the specification.** It is the readable statement of SPEC.md 11.4
and Appendix A, and `test_kernel_matches_numpy` asserts the two agree to floating-point tolerance
on a network with flap gates, tidal outfalls, pressurised nodes and a supply-limited node. If they
ever disagree, the NumPy one is right and this one has a bug.

**Determinism (rule 8).** `step_kernel` is single-threaded. The node accumulations are
scatter-adds - several edges write the same node - and `prange` over edges would race on them.
Per-thread buffers plus a reduction would fix the race and break the bake, because float addition
is not associative and the partial sums would depend on how many threads ran.

`step_kernel_parallel` (task P4.6) parallelises the two edge passes without either defect, by
colouring the edge graph so that no two edges in a colour touch the same node: the threads never
share an address, and across colours the order is the colour order, which is a function of the
network alone. It is held to the serial kernel bit for bit by `test_parallel_kernel_matches_serial`.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

__all__ = ["step_kernel", "step_kernel_parallel"]

# Kept in step with `drain1d`; imported there rather than duplicated, but the kernel needs them as
# compile-time constants, so they are passed in as arguments instead of closed over. A literal here
# would go stale silently the day `drain1d` changed one.


@njit(fastmath=True, cache=True)
def step_kernel(
    # --- topology -------------------------------------------------------------------------
    from_node: np.ndarray,
    to_node: np.ndarray,
    length: np.ndarray,
    # --- per-edge constants ---------------------------------------------------------------
    conveyance: np.ndarray,
    q_cap: np.ndarray,
    inv_diameter: np.ndarray,
    # --- per-node constants ---------------------------------------------------------------
    z_invert: np.ndarray,
    storage_base: np.ndarray,
    slot_area: np.ndarray,
    crown_depth: np.ndarray,
    fixed_head: np.ndarray,
    flap_gate: np.ndarray,
    boundary: np.ndarray,
    # --- state ----------------------------------------------------------------------------
    head: np.ndarray,
    flow_out: np.ndarray,
    # --- forcing --------------------------------------------------------------------------
    inlet: np.ndarray,
    surch: np.ndarray,
    draw: np.ndarray,
    dt_s: float,
    tide_stage: np.ndarray,
    have_tide: bool,
    # --- constants ------------------------------------------------------------------------
    min_head_gradient_m: float,
    min_flow_depth_m: float,
    boundary_free: int,
    boundary_tidal: int,
    # --- scratch (allocated once by the caller, reused every step) ------------------------
    q: np.ndarray,
    q_out: np.ndarray,
    scale: np.ndarray,
    net_edge: np.ndarray,
    applied_surch: np.ndarray,
    applied_draw: np.ndarray,
) -> np.ndarray:
    """One explicit step, in place on ``head`` and ``flow_out``.

    Returns the six report scalars as an array: inlet, surcharge, sink and boundary volumes in
    m3, the stored volume in m3, and the count of edges the stability limiter caught.
    """
    n_nodes = head.shape[0]
    n_edges = from_node.shape[0]

    # ---- pin boundaries ------------------------------------------------------------------
    for j in range(n_nodes):
        if boundary[j] == boundary_free:
            head[j] = z_invert[j]
        elif boundary[j] == boundary_tidal:
            if have_tide:
                stage = tide_stage[j]
                head[j] = stage if stage > z_invert[j] else z_invert[j]
            else:
                head[j] = z_invert[j]

    # ---- pass 1: the constitutive law, the flap gates and the stability limiter -----------
    limited = 0
    for j in range(n_nodes):
        q_out[j] = 0.0

    for e in range(n_edges):
        value, was_limited = _edge_flow_one(
            e,
            from_node,
            to_node,
            length,
            conveyance,
            q_cap,
            inv_diameter,
            z_invert,
            storage_base,
            slot_area,
            crown_depth,
            fixed_head,
            flap_gate,
            head,
            dt_s,
            min_head_gradient_m,
            min_flow_depth_m,
        )
        limited += was_limited
        q[e] = value
        # Accumulate what each node gives away, for the supply check. This is the scatter that
        # `prange` over edges would race on; `step_kernel_parallel` colours it instead.
        if value > 0.0:
            q_out[from_node[e]] += value
        elif value < 0.0:
            q_out[to_node[e]] += -value

    # ---- pass 2: the supply check ---------------------------------------------------------
    for j in range(n_nodes):
        if fixed_head[j]:
            scale[j] = 1.0  # an outfall is never short of water
            continue
        demand = dt_s * (q_out[j] + surch[j] + draw[j])
        depth = head[j] - z_invert[j]
        if depth < 0.0:
            depth = 0.0
        crown = crown_depth[j]
        below = depth if depth < crown else crown
        above = depth - crown
        if above < 0.0:
            above = 0.0
        supply = storage_base[j] * below + (storage_base[j] + slot_area[j]) * above
        if demand > supply and demand > 0.0:
            scale[j] = supply / demand
        else:
            scale[j] = 1.0

    # ---- pass 3: scale the flows, accumulate the node balance, integrate ------------------
    for j in range(n_nodes):
        net_edge[j] = 0.0

    for e in range(n_edges):
        value = q[e]
        if value == 0.0:
            continue
        # Scaled by whichever end is *giving* the water.
        value *= scale[from_node[e]] if value >= 0.0 else scale[to_node[e]]
        q[e] = value
        net_edge[to_node[e]] += value
        net_edge[from_node[e]] -= value

    inlet_m3 = 0.0
    surcharge_m3 = 0.0
    sink_m3 = 0.0
    boundary_m3 = 0.0
    stored_m3 = 0.0

    for j in range(n_nodes):
        applied_surch[j] = surch[j] * scale[j]
        applied_draw[j] = draw[j] * scale[j]
        sink_m3 += dt_s * applied_draw[j]

        if fixed_head[j]:
            boundary_m3 += dt_s * net_edge[j]
            continue

        inlet_m3 += dt_s * inlet[j]
        surcharge_m3 += dt_s * applied_surch[j]

        # Volume now, on the piecewise-linear storage curve.
        depth = head[j] - z_invert[j]
        if depth < 0.0:
            depth = 0.0
        crown = crown_depth[j]
        below = depth if depth < crown else crown
        above = depth - crown
        if above < 0.0:
            above = 0.0
        volume = storage_base[j] * below + (storage_base[j] + slot_area[j]) * above

        volume += dt_s * (net_edge[j] + inlet[j] - applied_surch[j] - applied_draw[j])
        if volume < 0.0:
            volume = 0.0

        # ... and back to a head.
        v_crown = storage_base[j] * crown
        if volume <= v_crown:
            new_depth = volume / storage_base[j]
        else:
            new_depth = crown + (volume - v_crown) / (storage_base[j] + slot_area[j])
        head[j] = z_invert[j] + new_depth

        # The stored volume is audited *after* the update, as the NumPy step does.
        depth2 = head[j] - z_invert[j]
        if depth2 < 0.0:
            depth2 = 0.0
        below2 = depth2 if depth2 < crown else crown
        above2 = depth2 - crown
        if above2 < 0.0:
            above2 = 0.0
        stored_m3 += storage_base[j] * below2 + (storage_base[j] + slot_area[j]) * above2

    # ---- pin boundaries again, and publish the flows --------------------------------------
    for j in range(n_nodes):
        if boundary[j] == boundary_free:
            head[j] = z_invert[j]
        elif boundary[j] == boundary_tidal:
            if have_tide:
                stage = tide_stage[j]
                head[j] = stage if stage > z_invert[j] else z_invert[j]
            else:
                head[j] = z_invert[j]

    for e in range(n_edges):
        flow_out[e] = q[e]

    report = np.empty(6, dtype=np.float64)
    report[0] = inlet_m3
    report[1] = surcharge_m3
    report[2] = sink_m3
    report[3] = boundary_m3
    report[4] = stored_m3
    report[5] = float(limited)
    return report


# ============================================================================ parallel edges
#
# ADR-0035 named this exactly: "the remaining factor of seven needs the edge scatter parallelised
# by colouring". It is worth about a factor of 3.5, and it is not the factor of seven, because the
# premise has gone stale: the drain is no longer most of the coupled run.
#
# **Measured**, Intel i5-1155G7 (4 physical cores, 8 logical), 10 python processes, the real
# Mumbai graph of 49,770 edges, and the shape `runner` actually calls - `duration_s=5, dt_s=1`,
# five inner steps a sync - alternating the two paths and taking the best of four, because
# between two *non*-interleaved full-cycle runs on this machine the unchanged surface solver
# moved by 13x:
#
#     8 threads: serial 7.68 ms/step, coloured 2.20 ms/step, 3.49x (2.64-3.85 over four trials)
#     4 threads: serial 7.62 ms/step, coloured 2.10 ms/step, 3.63x (3.46-3.71)
#
# A first attempt measured 0.69x and was wrong twice over: 200 inner steps a call, which
# amortises a launch overhead the runner does not amortise, and not interleaved, so it compared
# two different machine loads. The number above replaces it.
#
# **The 8 s budget is still missed, and not by a factor this can close.** On the 08:40 cycle of
# 2 July 2019 (36 steps, 18 python processes) the serial run was drain 61.8 s, surface 60.4 s,
# coupling 19.4 s of 147.8 s total. The drain is 41.8 % of it, so Amdahl caps the whole-run gain
# at 1.72x *even with an infinitely fast drain*. What remains for P4.6 is the surface solver and
# the 2,160 coupling calls, not this.
#
# **Why colouring rather than atomics or per-thread buffers.** The two edge passes scatter onto
# nodes, so `prange` over edges races. Atomics would serialise the contended nodes and, worse,
# leave the float addition order at the mercy of the schedule. Per-thread buffers plus a reduction
# have the same defect: float addition is not associative, so the result would depend on how many
# threads ran, and `make bake` must be byte-identical across machines (rule 8). Inside one colour
# every node is written by at most one edge, so the threads never touch the same address and the
# order across colours is the colour order - a function of the network alone.


@njit(fastmath=True, inline="always")
def _edge_flow_one(
    e,
    from_node,
    to_node,
    length,
    conveyance,
    q_cap,
    inv_diameter,
    z_invert,
    storage_base,
    slot_area,
    crown_depth,
    fixed_head,
    flap_gate,
    head,
    dt_s,
    min_head_gradient_m,
    min_flow_depth_m,
):
    """The constitutive law, flap gates and stability limiter for one edge.

    Lifted out of `step_kernel`'s pass 1 verbatim so the serial and parallel kernels cannot drift
    apart line by line - which they would, because the parallel one needs the body twice (once
    under `prange` for the large colours, once serially for the tail). Returns
    ``(value, was_limited)``; the caller does the scatter, which is the part that needed colouring.
    """
    a = from_node[e]
    b = to_node[e]
    dh = head[a] - head[b]
    forward = dh >= 0.0

    if forward:
        fill_depth = head[a] - z_invert[a]
    else:
        fill_depth = head[b] - z_invert[b]

    abs_dh = dh if dh >= 0.0 else -dh
    if abs_dh < min_head_gradient_m or fill_depth < min_flow_depth_m:
        return 0.0, 0

    fill = fill_depth * inv_diameter[e]
    if fill < 0.0:
        fill = 0.0
    elif fill > 1.0:
        fill = 1.0

    run = length[e]
    if run < min_head_gradient_m:
        run = min_head_gradient_m
    friction = conveyance[e] * fill * np.sqrt(abs_dh / run)
    magnitude = friction if friction < q_cap[e] else q_cap[e]
    value = magnitude if forward else -magnitude

    if flap_gate[b] and fixed_head[b] and value < 0.0:
        value = 0.0
    if flap_gate[a] and fixed_head[a] and value > 0.0:
        value = 0.0

    inv_a = 0.0
    if not fixed_head[a]:
        depth_a = head[a] - z_invert[a]
        area_a = storage_base[a] + (slot_area[a] if depth_a > crown_depth[a] else 0.0)
        inv_a = 1.0 / area_a
    inv_b = 0.0
    if not fixed_head[b]:
        depth_b = head[b] - z_invert[b]
        area_b = storage_base[b] + (slot_area[b] if depth_b > crown_depth[b] else 0.0)
        inv_b = 1.0 / area_b
    share = inv_a + inv_b
    was_limited = 0
    if share > 0.0:
        q_limit = abs_dh / (dt_s * share)
        magnitude_now = value if value >= 0.0 else -value
        if magnitude_now > q_limit:
            was_limited = 1
            value = q_limit if value >= 0.0 else -q_limit
    return value, was_limited


@njit(fastmath=True, cache=True, parallel=True)
def step_kernel_parallel(
    # --- topology -------------------------------------------------------------------------
    from_node: np.ndarray,
    to_node: np.ndarray,
    length: np.ndarray,
    # --- per-edge constants ---------------------------------------------------------------
    conveyance: np.ndarray,
    q_cap: np.ndarray,
    inv_diameter: np.ndarray,
    # --- per-node constants ---------------------------------------------------------------
    z_invert: np.ndarray,
    storage_base: np.ndarray,
    slot_area: np.ndarray,
    crown_depth: np.ndarray,
    fixed_head: np.ndarray,
    flap_gate: np.ndarray,
    boundary: np.ndarray,
    # --- state ----------------------------------------------------------------------------
    head: np.ndarray,
    flow_out: np.ndarray,
    # --- forcing --------------------------------------------------------------------------
    inlet: np.ndarray,
    surch: np.ndarray,
    draw: np.ndarray,
    dt_s: float,
    tide_stage: np.ndarray,
    have_tide: bool,
    # --- constants ------------------------------------------------------------------------
    min_head_gradient_m: float,
    min_flow_depth_m: float,
    boundary_free: int,
    boundary_tidal: int,
    # --- scratch (allocated once by the caller, reused every step) ------------------------
    q: np.ndarray,
    q_out: np.ndarray,
    scale: np.ndarray,
    net_edge: np.ndarray,
    applied_surch: np.ndarray,
    applied_draw: np.ndarray,
    # --- colouring (task P4.6) ------------------------------------------------------------
    colour_edges: np.ndarray,
    colour_start: np.ndarray,
    n_parallel_colours: int,
) -> np.ndarray:
    """:func:`step_kernel` with its two edge scatters parallelised by colouring (task P4.6).

    Identical arithmetic, and `test_parallel_kernel_matches_serial` asserts it bit for bit on a
    network with flap gates, a tide-locked outfall, pressurised nodes and a supply-limited node -
    the same four cases the serial kernel was landed against. If they disagree, the serial one is
    right and this one has a bug.

    Only the two edge passes are parallel. The node passes stay serial on purpose: the last of
    them accumulates four float totals, and a threaded reduction would make their addition order
    depend on the thread count, which is exactly what rule 8 forbids of a bake.
    """
    n_nodes = head.shape[0]
    n_edges = from_node.shape[0]

    # ---- pin boundaries ------------------------------------------------------------------
    for j in range(n_nodes):
        if boundary[j] == boundary_free:
            head[j] = z_invert[j]
        elif boundary[j] == boundary_tidal:
            if have_tide:
                stage = tide_stage[j]
                head[j] = stage if stage > z_invert[j] else z_invert[j]
            else:
                head[j] = z_invert[j]

    # ---- pass 1: the constitutive law, the flap gates and the stability limiter -----------
    limited = 0
    for j in range(n_nodes):
        q_out[j] = 0.0

    for c in range(n_parallel_colours):
        # Inside one colour every node is written by at most one edge, so the threads never
        # share an address and nothing has to be atomic or reduced.
        for k in prange(colour_start[c], colour_start[c + 1]):
            e = colour_edges[k]
            value, was_limited = _edge_flow_one(
                e,
                from_node,
                to_node,
                length,
                conveyance,
                q_cap,
                inv_diameter,
                z_invert,
                storage_base,
                slot_area,
                crown_depth,
                fixed_head,
                flap_gate,
                head,
                dt_s,
                min_head_gradient_m,
                min_flow_depth_m,
            )
            limited += was_limited
            q[e] = value
            if value > 0.0:
                q_out[from_node[e]] += value
            elif value < 0.0:
                q_out[to_node[e]] += -value

    # The tail: the colours too small to be worth a launch, serially and in colour order.
    for k in range(colour_start[n_parallel_colours], n_edges):
        e = colour_edges[k]
        value, was_limited = _edge_flow_one(
            e,
            from_node,
            to_node,
            length,
            conveyance,
            q_cap,
            inv_diameter,
            z_invert,
            storage_base,
            slot_area,
            crown_depth,
            fixed_head,
            flap_gate,
            head,
            dt_s,
            min_head_gradient_m,
            min_flow_depth_m,
        )
        limited += was_limited
        q[e] = value
        if value > 0.0:
            q_out[from_node[e]] += value
        elif value < 0.0:
            q_out[to_node[e]] += -value

    # ---- pass 2: the supply check ---------------------------------------------------------
    for j in range(n_nodes):
        if fixed_head[j]:
            scale[j] = 1.0  # an outfall is never short of water
            continue
        demand = dt_s * (q_out[j] + surch[j] + draw[j])
        depth = head[j] - z_invert[j]
        if depth < 0.0:
            depth = 0.0
        crown = crown_depth[j]
        below = depth if depth < crown else crown
        above = depth - crown
        if above < 0.0:
            above = 0.0
        supply = storage_base[j] * below + (storage_base[j] + slot_area[j]) * above
        if demand > supply and demand > 0.0:
            scale[j] = supply / demand
        else:
            scale[j] = 1.0

    # ---- pass 3: scale the flows, accumulate the node balance, integrate ------------------
    for j in range(n_nodes):
        net_edge[j] = 0.0

    for c in range(n_parallel_colours):
        for k in prange(colour_start[c], colour_start[c + 1]):
            e = colour_edges[k]
            value = q[e]
            if value != 0.0:
                # Scaled by whichever end is *giving* the water.
                value *= scale[from_node[e]] if value >= 0.0 else scale[to_node[e]]
                q[e] = value
                net_edge[to_node[e]] += value
                net_edge[from_node[e]] -= value

    for k in range(colour_start[n_parallel_colours], n_edges):
        e = colour_edges[k]
        value = q[e]
        if value == 0.0:
            continue
        value *= scale[from_node[e]] if value >= 0.0 else scale[to_node[e]]
        q[e] = value
        net_edge[to_node[e]] += value
        net_edge[from_node[e]] -= value

    inlet_m3 = 0.0
    surcharge_m3 = 0.0
    sink_m3 = 0.0
    boundary_m3 = 0.0
    stored_m3 = 0.0

    for j in range(n_nodes):
        applied_surch[j] = surch[j] * scale[j]
        applied_draw[j] = draw[j] * scale[j]
        sink_m3 += dt_s * applied_draw[j]

        if fixed_head[j]:
            boundary_m3 += dt_s * net_edge[j]
            continue

        inlet_m3 += dt_s * inlet[j]
        surcharge_m3 += dt_s * applied_surch[j]

        # Volume now, on the piecewise-linear storage curve.
        depth = head[j] - z_invert[j]
        if depth < 0.0:
            depth = 0.0
        crown = crown_depth[j]
        below = depth if depth < crown else crown
        above = depth - crown
        if above < 0.0:
            above = 0.0
        volume = storage_base[j] * below + (storage_base[j] + slot_area[j]) * above

        volume += dt_s * (net_edge[j] + inlet[j] - applied_surch[j] - applied_draw[j])
        if volume < 0.0:
            volume = 0.0

        # ... and back to a head.
        v_crown = storage_base[j] * crown
        if volume <= v_crown:
            new_depth = volume / storage_base[j]
        else:
            new_depth = crown + (volume - v_crown) / (storage_base[j] + slot_area[j])
        head[j] = z_invert[j] + new_depth

        # The stored volume is audited *after* the update, as the NumPy step does.
        depth2 = head[j] - z_invert[j]
        if depth2 < 0.0:
            depth2 = 0.0
        below2 = depth2 if depth2 < crown else crown
        above2 = depth2 - crown
        if above2 < 0.0:
            above2 = 0.0
        stored_m3 += storage_base[j] * below2 + (storage_base[j] + slot_area[j]) * above2

    # ---- pin boundaries again, and publish the flows --------------------------------------
    for j in range(n_nodes):
        if boundary[j] == boundary_free:
            head[j] = z_invert[j]
        elif boundary[j] == boundary_tidal:
            if have_tide:
                stage = tide_stage[j]
                head[j] = stage if stage > z_invert[j] else z_invert[j]
            else:
                head[j] = z_invert[j]

    for e in range(n_edges):
        flow_out[e] = q[e]

    report = np.empty(6, dtype=np.float64)
    report[0] = inlet_m3
    report[1] = surcharge_m3
    report[2] = sink_m3
    report[3] = boundary_m3
    report[4] = stored_m3
    report[5] = float(limited)
    return report

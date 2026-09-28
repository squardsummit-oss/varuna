"""Gravity audit of the inferred drain graph (SPEC.md 10.1 steps 7 and 10, P1.8, P1.10).

``check_connectivity`` proves every node has a path to an outfall. That path is only topological. A
path can still run uphill, and the drain solver (SPEC.md 11.4) cannot move water over a pipe
whose downstream invert stands above the water behind it. This module measures the difference
from the two drain tables alone, without re-running any step and without changing the graph:

* **adverse edges**: the downstream invert is above the upstream invert. Reported as a count, a
  share of edges and pipe length, and the rise;
* **edges under the minimum slope**: the invert fall is below 10.1 step 7's 0.3 %;
* **false slope fields**: the stored ``slope`` column disagrees with the fall its own inverts
  give. ``drains.py`` writes ``max(min_slope, fall)``, so the column never shows an adverse bed;
* **the downstream sill** of every node: the highest invert on its tree path to its outfall,
  the node itself excluded. A node is **sill-blocked** when that sill stands above its own
  ground. Water at that node reaches the outfall only by surcharging onto the street first;
* **per-hotspot counts**: sill-blocked nodes in a 5 x 5 cell window around each register point;
* **the outfall inventory** by boundary type, with ground and invert elevations and how many
  nodes each type drains.

These are properties of the graph as built, not of any storm. The audit states them. It does
not judge whether a sill is acceptable: SPEC.md 10.1 step 7 fixes both a 1.5 m / 3 m invert
depth and a 0.3 % minimum slope, and on terrain that rises toward the outfall both cannot hold.
The audit reports which one gave way.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any, Final

import numpy as np
import pandas as pd
from numpy.typing import NDArray

MIN_SLOPE: Final[float] = 0.003
"""SPEC.md 10.1 step 7: the minimum pipe slope, 0.3 %."""

FALL_TOL_M: Final[float] = 1e-6
"""An edge is adverse when its invert fall is below ``-FALL_TOL_M``; a sill must beat ground by it.

The parquet stores inverts to the millimetre, so anything this small is representation noise."""

SLOPE_FIELD_TOL: Final[float] = 1e-4
"""A stored slope disagrees with its inverts when the two differ by more than this."""

HOTSPOT_WINDOW_CELLS: Final[int] = 2
"""Half-width of the per-hotspot window in grid cells: 2 gives the 5 x 5 window."""

HIGH_GROUND_M: Final[float] = 5.0
"""Outfalls on ground above this are counted separately.

A free outfall 5 m above datum sits well inland of the coast. Where one is, the graph drains into
the end of an OSM waterway fragment rather than into the sea."""

QUANTILES: Final[tuple[tuple[str, float], ...]] = (
    ("min", 0.0),
    ("p25", 25.0),
    ("p50", 50.0),
    ("p75", 75.0),
    ("p90", 90.0),
    ("max", 100.0),
)


def _quantiles(values: NDArray[np.float64], digits: int = 3) -> dict[str, float] | None:
    if values.size == 0:
        return None
    return {k: round(float(np.percentile(values, q)), digits) for k, q in QUANTILES}


def _share(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


class DrainTree:
    """The drain graph as index arrays: one downstream parent per node, roots first in ``order``.

    Raises ``ValueError`` when an edge names a node that is not in the node table, or when a
    node has more than one downstream edge. The graph is then not a tree, and a sill along
    "the" path to the outfall is undefined.
    """

    def __init__(self, nodes: pd.DataFrame, edges: pd.DataFrame) -> None:
        node_ids = nodes["node_id"].astype(str).to_numpy()
        index = pd.Index(node_ids)
        if not index.is_unique:
            msg = "drain_nodes has duplicate node_id values"
            raise ValueError(msg)
        self.n_nodes = len(node_ids)
        self.node_ids = node_ids
        fu = index.get_indexer(edges["from_node"].astype(str).to_numpy())
        tn = index.get_indexer(edges["to_node"].astype(str).to_numpy())
        if (fu < 0).any() or (tn < 0).any():
            missing = int((fu < 0).sum() + (tn < 0).sum())
            msg = f"{missing} edge endpoint(s) name a node that is not in drain_nodes"
            raise ValueError(msg)
        out_degree = np.bincount(fu, minlength=self.n_nodes)
        if out_degree.size and int(out_degree.max()) > 1:
            worst = node_ids[int(np.argmax(out_degree))]
            msg = (
                f"node {worst} has {int(out_degree.max())} downstream edges; the drain graph "
                "must be a tree for a downstream sill to be defined"
            )
            raise ValueError(msg)
        self.from_idx: NDArray[np.int64] = fu.astype(np.int64)
        self.to_idx: NDArray[np.int64] = tn.astype(np.int64)
        self.parent: NDArray[np.int64] = np.full(self.n_nodes, -1, dtype=np.int64)
        self.parent[self.from_idx] = self.to_idx
        self.edge_of: NDArray[np.int64] = np.full(self.n_nodes, -1, dtype=np.int64)
        self.edge_of[self.from_idx] = np.arange(len(edges), dtype=np.int64)
        self.roots: NDArray[np.int64] = np.flatnonzero(self.parent < 0)

        children: list[list[int]] = [[] for _ in range(self.n_nodes)]
        for child, par in zip(self.from_idx.tolist(), self.to_idx.tolist(), strict=True):
            children[par].append(child)
        order: list[int] = []
        stack = self.roots.tolist()[::-1]
        while stack:
            node = stack.pop()
            order.append(node)
            stack.extend(reversed(children[node]))
        self.order: NDArray[np.int64] = np.asarray(order, dtype=np.int64)
        """Every node reachable from a root, each after its parent. Nodes on a cycle are absent."""

        root_of = np.full(self.n_nodes, -1, dtype=np.int64)
        for node in order:
            par = self.parent[node]
            root_of[node] = node if par < 0 else root_of[par]
        self.root_of: NDArray[np.int64] = root_of
        """The root each node drains to, or -1 for a node that no root reaches (a cycle)."""


def downstream_sill(tree: DrainTree, z_invert: NDArray[np.float64]) -> NDArray[np.float64]:
    """The highest invert strictly downstream of each node on its path to its root.

    A root has no downstream node and gets ``-inf``. So does a node no root reaches, because it
    is on a cycle. This is the level water behind a node must rise to before it can reach the
    outfall, ignoring friction. It is a lower bound on the head the solver needs.
    """
    sill = np.full(tree.n_nodes, -np.inf, dtype=np.float64)
    parent = tree.parent
    for node in tree.order.tolist():
        par = parent[node]
        if par >= 0:
            sill[node] = max(float(z_invert[par]), float(sill[par]))
    return sill


def _hotspot_rows(
    hotspots: Any,
    *,
    transform: Any,
    crs: str | None,
    cell_row: NDArray[np.int64],
    cell_col: NDArray[np.int64],
    excess: NDArray[np.float64],
    blocked: NDArray[np.bool_],
    window: int,
) -> list[dict[str, Any]]:
    frame = hotspots
    if crs is not None and frame.crs is not None and str(frame.crs) != str(crs):
        frame = frame.to_crs(crs)
    inverse = ~transform
    rows: list[dict[str, Any]] = []
    for position, feature in enumerate(frame.itertuples(index=False)):
        geom = feature.geometry
        col_f, row_f = inverse @ (float(geom.x), float(geom.y))
        row, col = math.floor(row_f), math.floor(col_f)
        in_window = np.flatnonzero(
            (np.abs(cell_row - row) <= window) & (np.abs(cell_col - col) <= window)
        )
        n_blocked = int(blocked[in_window].sum())
        rows.append(
            {
                "hotspot_id": str(getattr(feature, "hotspot_id", position)),
                "name": str(getattr(feature, "name", "")),
                "sourced": bool(getattr(feature, "sourced", False)),
                "cell": [row, col],
                "nodes_in_window": int(in_window.size),
                "sill_blocked": n_blocked,
                "max_excess_m": (
                    round(float(excess[in_window][blocked[in_window]].max()), 3)
                    if n_blocked
                    else None
                ),
            }
        )
    return rows


def audit_gravity(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    *,
    min_slope: float = MIN_SLOPE,
    hotspots: Any = None,
    transform: Any = None,
    crs: str | None = None,
    tide_max_m: float | None = None,
    window: int = HOTSPOT_WINDOW_CELLS,
) -> dict[str, Any]:
    """Audit the drain tables for gravity consistency. Pure: it reads and returns, nothing else.

    ``nodes`` needs ``node_id``, ``z_ground_m``, ``z_invert_m``, ``is_outfall`` and
    ``boundary_type``. ``cell_row`` and ``cell_col`` are needed for the hotspot windows, and
    ``tidal``, ``outfall_id`` and ``flap_gate`` are read when present. ``edges`` needs
    ``from_node``, ``to_node`` and ``length_m``; ``slope`` is read when present.

    Per-hotspot counts need ``hotspots`` (a GeoDataFrame of points) and the grid's affine
    ``transform``; ``crs`` reprojects the points onto the grid. ``tide_max_m`` adds how many
    outfalls sit with their invert above the highest tide stage. It compares numbers in whatever
    datum the tide series uses, and the result says so.
    """
    tree = DrainTree(nodes, edges)
    z_ground = nodes["z_ground_m"].to_numpy(dtype=np.float64)
    z_invert = nodes["z_invert_m"].to_numpy(dtype=np.float64)
    length = edges["length_m"].to_numpy(dtype=np.float64)
    n_edges = len(edges)
    total_km = float(length.sum()) / 1000.0

    fall = z_invert[tree.from_idx] - z_invert[tree.to_idx]
    with np.errstate(divide="ignore", invalid="ignore"):
        invert_slope = np.where(length > 0, fall / length, 0.0)
    adverse = fall < -FALL_TOL_M
    under = invert_slope < min_slope - FALL_TOL_M
    rise = -fall[adverse]

    slope_field: dict[str, Any] | None = None
    if "slope" in edges.columns:
        stored = edges["slope"].to_numpy(dtype=np.float64)
        disagree = np.abs(stored - invert_slope) > SLOPE_FIELD_TOL
        slope_field = {
            "tolerance": SLOPE_FIELD_TOL,
            "disagreeing_edges": int(disagree.sum()),
            "share": _share(int(disagree.sum()), n_edges),
            "stored_min": round(float(stored.min()), 6) if n_edges else None,
            "adverse_edges_reported_adverse": int((stored[adverse] < 0).sum()),
        }

    depth = z_ground - z_invert
    sill = downstream_sill(tree, z_invert)
    excess = sill - z_ground
    blocked = excess > FALL_TOL_M
    n_blocked = int(blocked.sum())
    blocked_edges = blocked[tree.from_idx]

    is_outfall = nodes["is_outfall"].to_numpy(dtype=bool)
    boundary = nodes["boundary_type"].astype(object).where(nodes["boundary_type"].notna(), None)
    boundary_arr = np.asarray(boundary.to_numpy(), dtype=object)
    served = np.bincount(tree.root_of[tree.root_of >= 0], minlength=tree.n_nodes)

    inventory: dict[str, Any] = {}
    outfall_idx = np.flatnonzero(is_outfall)
    for kind in sorted({str(b) for b in boundary_arr[outfall_idx]}):
        members = outfall_idx[np.asarray([str(b) == kind for b in boundary_arr[outfall_idx]])]
        entry: dict[str, Any] = {
            "outfalls": int(members.size),
            "nodes_served": int(served[members].sum()),
            "ground_m": _quantiles(z_ground[members]),
            "invert_m": _quantiles(z_invert[members]),
            f"ground_above_{HIGH_GROUND_M:g}_m": int((z_ground[members] > HIGH_GROUND_M).sum()),
        }
        if tide_max_m is not None:
            entry["invert_above_tide_max"] = int((z_invert[members] > tide_max_m).sum())
        inventory[kind] = entry

    tidal_rows: list[dict[str, Any]] = []
    if "tidal" in nodes.columns:
        for i in np.flatnonzero(nodes["tidal"].to_numpy(dtype=bool)).tolist():
            row: dict[str, Any] = {
                "node_id": str(tree.node_ids[i]),
                "outfall_id": _opt_str(nodes, "outfall_id", i),
                "flap_gate": bool(nodes["flap_gate"].iloc[i]) if "flap_gate" in nodes else None,
                "z_ground_m": round(float(z_ground[i]), 3),
                "z_invert_m": round(float(z_invert[i]), 3),
                "nodes_served": int(served[i]),
            }
            if tide_max_m is not None:
                row["invert_below_tide_max"] = bool(z_invert[i] < tide_max_m)
            tidal_rows.append(row)

    roots_not_outfall = int((~is_outfall[tree.roots]).sum()) if tree.roots.size else 0

    result: dict[str, Any] = {
        "nodes": tree.n_nodes,
        "edges": n_edges,
        "pipe_length_km": round(total_km, 2),
        "min_slope": min_slope,
        "tree": {
            "roots": int(tree.roots.size),
            "roots_not_outfall": roots_not_outfall,
            "nodes_unreached": int(tree.n_nodes - tree.order.size),
        },
        "adverse": {
            "edges": int(adverse.sum()),
            "share": _share(int(adverse.sum()), n_edges),
            "length_km": round(float(length[adverse].sum()) / 1000.0, 2),
            "length_share": round(float(length[adverse].sum()) / 1000.0 / total_km, 4)
            if total_km
            else 0.0,
            "rise_m": _quantiles(rise),
        },
        "under_min_slope": {
            "edges": int(under.sum()),
            "share": _share(int(under.sum()), n_edges),
            "length_km": round(float(length[under].sum()) / 1000.0, 2),
        },
        "slope_field": slope_field,
        "invert_depth_m": _quantiles(depth),
        "inverts_above_ground": int((z_invert > z_ground + FALL_TOL_M).sum()),
        "sill": {
            "blocked_nodes": n_blocked,
            "share": _share(n_blocked, tree.n_nodes),
            "excess_m": _quantiles(excess[blocked]),
            "pipe_km_from_blocked_nodes": round(float(length[blocked_edges].sum()) / 1000.0, 2),
        },
        "outfalls": {
            "by_boundary_type": inventory,
            "tidal": tidal_rows,
            "tide_max_m": tide_max_m,
            "datum_note": (
                "Tide stage and DEM elevations are compared as stored; the audit does not "
                "convert datums."
            )
            if tide_max_m is not None
            else None,
        },
        "hotspots": None,
    }

    if hotspots is not None and transform is not None and len(hotspots):
        rows = _hotspot_rows(
            hotspots,
            transform=transform,
            crs=crs,
            cell_row=nodes["cell_row"].to_numpy(dtype=np.int64),
            cell_col=nodes["cell_col"].to_numpy(dtype=np.int64),
            excess=excess,
            blocked=blocked,
            window=window,
        )
        result["hotspots"] = {
            "window_cells": 2 * window + 1,
            "points": len(rows),
            "with_blocked_nodes": sum(1 for r in rows if r["sill_blocked"]),
            "rows": rows,
        }
    return result


def _opt_str(frame: pd.DataFrame, column: str, i: int) -> str | None:
    if column not in frame.columns:
        return None
    value = frame[column].iloc[i]
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)


def summary_lines(audit: dict[str, Any]) -> Iterable[str]:
    """The audit as short plain-text lines, for the CLI and the pipeline log."""
    adv = audit["adverse"]
    under = audit["under_min_slope"]
    sill = audit["sill"]
    yield (
        f"{audit['nodes']:,} nodes, {audit['edges']:,} edges, {audit['pipe_length_km']:,.2f} km "
        f"of pipe; {audit['tree']['roots']:,} roots "
        f"({audit['tree']['roots_not_outfall']} not an outfall)"
    )
    yield (
        f"adverse edges: {adv['edges']:,} ({adv['share'] * 100:.1f} %), "
        f"{adv['length_km']:,.1f} km ({adv['length_share'] * 100:.1f} % of length)"
    )
    if adv["rise_m"]:
        yield (
            f"  rise m: p50 {adv['rise_m']['p50']:.2f}, p90 {adv['rise_m']['p90']:.2f}, "
            f"max {adv['rise_m']['max']:.2f}"
        )
    yield (
        f"edges falling under {audit['min_slope'] * 100:g} %: {under['edges']:,} "
        f"({under['share'] * 100:.1f} %), {under['length_km']:,.1f} km"
    )
    if audit["slope_field"] is not None:
        sf = audit["slope_field"]
        yield (
            f"stored slope disagrees with the invert fall: {sf['disagreeing_edges']:,} edges "
            f"(stored minimum {sf['stored_min']})"
        )
    yield f"inverts above ground: {audit['inverts_above_ground']:,}"
    depth = audit["invert_depth_m"]
    if depth:
        yield (
            f"invert depth m: min {depth['min']:.2f}, p50 {depth['p50']:.2f}, "
            f"p90 {depth['p90']:.2f}, max {depth['max']:.2f}"
        )
    yield (
        f"sill-blocked nodes: {sill['blocked_nodes']:,} ({sill['share'] * 100:.1f} %); "
        f"{sill['pipe_km_from_blocked_nodes']:,.1f} km of pipe starts at one"
    )
    if sill["excess_m"]:
        yield (
            f"  excess m: p50 {sill['excess_m']['p50']:.2f}, p90 {sill['excess_m']['p90']:.2f}, "
            f"max {sill['excess_m']['max']:.2f}"
        )
    for kind, entry in audit["outfalls"]["by_boundary_type"].items():
        ground = entry["ground_m"] or {}
        line = (
            f"outfalls '{kind}': {entry['outfalls']} draining {entry['nodes_served']:,} nodes; "
            f"ground p50 {ground.get('p50', float('nan')):.2f} m, "
            f"{entry[f'ground_above_{HIGH_GROUND_M:g}_m']} above {HIGH_GROUND_M:g} m"
        )
        if "invert_above_tide_max" in entry:
            line += f", {entry['invert_above_tide_max']} with invert above the tide maximum"
        yield line
    for row in audit["outfalls"]["tidal"]:
        yield (
            f"  tidal {row['outfall_id']} -> {row['node_id']}: ground {row['z_ground_m']:.2f} m, "
            f"invert {row['z_invert_m']:.2f} m, drains {row['nodes_served']:,} nodes"
            + (", flap gate" if row.get("flap_gate") else "")
        )
    hs = audit["hotspots"]
    if hs:
        yield (
            f"hotspots with sill-blocked nodes in their {hs['window_cells']}x{hs['window_cells']} "
            f"window: {hs['with_blocked_nodes']} of {hs['points']}"
        )
        for row in hs["rows"]:
            excess = "-" if row["max_excess_m"] is None else f"+{row['max_excess_m']:.2f} m"
            yield (
                f"  {row['hotspot_id']} {row['name'][:34]}: {row['sill_blocked']}/"
                f"{row['nodes_in_window']} blocked, max {excess}"
            )


__all__ = [
    "FALL_TOL_M",
    "HIGH_GROUND_M",
    "HOTSPOT_WINDOW_CELLS",
    "MIN_SLOPE",
    "SLOPE_FIELD_TOL",
    "DrainTree",
    "audit_gravity",
    "downstream_sill",
    "summary_lines",
]

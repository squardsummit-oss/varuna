"""Manholes pushing water back into the street, and pipes running backwards (P5.2, P6.6).

The demo's 1:40 moment (SPEC.md 15) is the map showing manholes surcharging and a tide-locked
outfall running in reverse. Both are states the coupled solver already computes every step - this
module is only what turns them into something the console can draw.

**Why only the surcharging nodes are written.** Mumbai's inferred graph has 49,897 nodes and a
run has 36 steps, so a full head-per-node-per-step table is 1.8 million rows to say that almost
all of them are fine. What the map draws is the exception, so the exception is what is stored:
each node that surcharges at least once, its position, and the steps at which it did.

**Backflow** is the same idea on the edges. A negative flow on an edge whose downstream end is a
tidal outfall is the physical signature SPEC.md 11.4 asks for - the sea holding the drain shut -
so the edges are tagged with whether they reach one, and the console can draw that subset alone.

**Where a reversed edge's line comes from.** The drain network the solver runs on has no node
coordinates - only the 30 m cell each node exchanges with - and 80-105 of the 500 stored reversed
edges per demo cycle have both ends in one cell, so a line built from cell centres would be zero
length for a fifth of them and snapped off the pipe the Drains layer draws for the rest. The line
is therefore the city export's own, ``city/<city>/map/drains.geojson``, joined by ``edge_id`` and
oriented from the edge's ``from_node`` to its ``to_node``. An edge the export does not carry gets
``path: null`` and is counted in the product's notes; a line is never invented for it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from numpy.typing import NDArray
    from varuna_twin.types import DrainNetwork

log = structlog.get_logger("varuna.products.surcharge")

__all__ = [
    "DRAINS_EXPORT",
    "MAX_NODES",
    "MIN_SURCHARGE_M3S",
    "attach_edge_paths",
    "drain_paths",
    "surcharge_product",
    "write_surcharge",
]

DRAINS_EXPORT = "map/drains.geojson"
"""The city export reversed-edge lines are read from, relative to ``city/<city>``.

It is the file the console's Drains layer draws, so a reversed edge lands on the pipe the operator
already sees rather than on a second, slightly different line. Stored relative, never absolute: an
absolute path would differ between two machines baking the same cycle (rule 8)."""

MAX_NODES = 500
"""How many surcharging manholes a run stores, worst first.

A heavy Mumbai cycle surcharges 9,524 of the 49,897 nodes, and writing all of them with a
per-step series produced a 17 MB JSON file the console had to download before it could draw
anything. The map cannot show 9,524 markers usefully at any zoom - they merge into a red
smear - so the top 500 by peak discharge is what is kept, and the total is reported beside it
so the count on screen is the true one."""

MIN_SURCHARGE_M3S = 1e-4
"""Below this, a node is not surcharging; it is arithmetic noise.

0.1 litres a second through a manhole is nothing a street would show and nothing an operator
would act on. Drawing it would put a red marker on the map for a number that rounds to zero,
which is the sort of thing rule 6 exists to stop."""


EdgeLine = tuple[str, str, tuple[tuple[float, float], ...]]
"""``(from_node, to_node, ((lon, lat), ...))`` as the export defines the edge."""


@lru_cache(maxsize=2)
def _load_drain_paths(path: str, mtime_ns: int, size: int) -> dict[str, EdgeLine]:
    """Parse the export once, keyed on the file's path *and* its stamp - never on ``id()``.

    A bake runs several cycles in one process and Mumbai's export is 18 MB, so parsing it per cycle
    would be the most expensive thing this module does. The stamp is in the key so a city rebuilt
    between two cycles is read again rather than served stale.
    """
    del mtime_ns, size  # cache key only
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    out: dict[str, EdgeLine] = {}
    for feature in data.get("features") or ():
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        edge_id = props.get("edge_id")
        if edge_id is None or geometry.get("type") != "LineString":
            continue
        coords = tuple(
            (round(float(pt[0]), 6), round(float(pt[1]), 6))
            for pt in geometry.get("coordinates") or ()
        )
        if len(coords) < 2:
            continue
        out[str(edge_id)] = (str(props.get("from_node")), str(props.get("to_node")), coords)
    return out


def drain_paths(city_root: Path) -> dict[str, EdgeLine] | None:
    """Every edge line in ``city_root / map/drains.geojson``; ``None`` when there is no export."""
    path = Path(city_root) / DRAINS_EXPORT
    try:
        stat = path.stat()
    except OSError:
        return None
    return _load_drain_paths(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def attach_edge_paths(
    edges: list[dict[str, Any]], lines: dict[str, EdgeLine] | None
) -> tuple[int, int]:
    """Give each edge its export line as ``path``, ordered from ``from_node`` to ``to_node``.

    Returns ``(n_missing, n_disagreeing)``. An edge gets ``path: None`` when the export has no
    line for its id, or when the export's endpoints are not this edge's nodes in either order: a
    line joined by id to a different pipe would be drawn in the wrong place, which is worse than
    drawing nothing. The orientation is the edge's own, so a dash animated from ``path[0]`` to
    ``path[-1]`` runs from ``from_node`` to ``to_node`` and a reversed flow runs it the other way.
    That is the direction the graph assigns, not necessarily downhill: ADR-0048 measured 38.2 % of
    Mumbai's edges falling uphill in it.
    """
    missing = 0
    disagreeing = 0
    for edge in edges:
        line = None if lines is None else lines.get(edge["edge_id"])
        if line is None:
            edge["path"] = None
            missing += 1
            continue
        src, dst, coords = line
        if (src, dst) == (edge["from_node"], edge["to_node"]):
            ordered = coords
        elif (dst, src) == (edge["from_node"], edge["to_node"]):
            ordered = coords[::-1]
        else:
            edge["path"] = None
            disagreeing += 1
            continue
        edge["path"] = [[lon, lat] for lon, lat in ordered]
    return missing, disagreeing


def _city_root_for(run_id: str) -> Path | None:
    """``city/<slug>`` for the city a run id names, or ``None`` when the id does not parse."""
    from varuna_schemas.models.run import RunIdError, parse_run_id
    from varuna_schemas.paths import city_dir

    try:
        return city_dir(parse_run_id(run_id).city)
    except (RunIdError, ValueError):
        return None


def _have(count: int) -> str:
    return "has" if count == 1 else "have"


def _path_notes(n_stored: int, has_export: bool, n_missing: int, n_disagreeing: int) -> list[str]:
    """What the product says about reversed edges it could not give a line to (rule 6)."""
    if not has_export:
        if n_stored == 0:
            return []
        return [
            f"No {DRAINS_EXPORT} for this city, so none of the {n_stored} stored reversed edges "
            "carries a path and the map cannot draw them until the city export is built."
        ]
    parts = []
    if n_missing:
        parts.append(f"{n_missing} {_have(n_missing)} no line in {DRAINS_EXPORT}")
    if n_disagreeing:
        parts.append(
            f"{n_disagreeing} {_have(n_disagreeing)} a line whose endpoints are not the edge's own "
            "nodes"
        )
    if not parts:
        return []
    return [
        f"Of the {n_stored} stored reversed edges, {' and '.join(parts)}; "
        "those carry path null and are not drawn."
    ]


def surcharge_product(
    q_surcharge: NDArray[np.floating],
    edge_flow: NDArray[np.floating],
    network: DrainNetwork,
    transform: tuple[float, float, float, float, float, float],
    crs: str,
    run_id: str,
    *,
    city_root: Path | None = None,
) -> dict[str, Any]:
    """Which manholes surcharge, when, and how hard; and which pipes run backwards.

    Positions come from each node's own 2D cell, which is the cell it exchanges water with, so a
    marker sits exactly where the water it emits arrives on the street. Reversed edges carry the
    city export's line instead (module docstring). ``city_root`` is ``city/<city>`` and defaults to
    the city ``run_id`` names.
    """
    from pyproj import Transformer

    n_steps = int(q_surcharge.shape[0])
    res, _, left, _, _, top = transform
    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    peak = q_surcharge.max(axis=0)
    active = np.flatnonzero(peak > MIN_SURCHARGE_M3S)
    # Worst first: the rail and the map both want the manholes that matter, and a run in a heavy
    # cycle can surcharge thousands of them.
    active = active[np.argsort(-peak[active])]

    nodes: list[dict[str, Any]] = []
    for index in active[:MAX_NODES]:
        row = int(network.cell_row[index])
        col = int(network.cell_col[index])
        if row < 0 or col < 0:
            # A node with no cell exchanges with no street, so it has nowhere to be drawn.
            continue
        lon, lat = to_wgs.transform(left + (col + 0.5) * res, top - (row + 0.5) * res)
        series = q_surcharge[:, index]
        steps = np.flatnonzero(series > MIN_SURCHARGE_M3S)
        nodes.append(
            {
                "node_id": network.node_ids[index],
                "lon": round(float(lon), 6),
                "lat": round(float(lat), 6),
                "peak_q_m3s": round(float(peak[index]), 4),
                "first_step": int(steps[0]),
                "last_step": int(steps[-1]),
                "n_steps": int(steps.size),
                # Two decimals in m3/s: enough to order them, small enough to keep the file
                # something the console can hold for every step of a 3-hour run.
                "q_m3s": [round(float(v), 2) for v in series],
            }
        )

    # Backflow: a negative flow is water moving against the edge's downhill orientation. The ones
    # that matter for the demo end at a tidal outfall, so they are marked rather than filtered -
    # a reversed pipe inland is a real result too, and hiding it would be a choice about physics.
    tidal_node = network.boundary == 1
    reversed_any = (edge_flow < 0.0).any(axis=0)
    edges: list[dict[str, Any]] = []
    for index in np.flatnonzero(reversed_any):
        downstream = int(network.to_node[index])
        edges.append(
            {
                "edge_id": network.edge_ids[index],
                "from_node": network.node_ids[int(network.from_node[index])],
                "to_node": network.node_ids[downstream],
                "tidal": bool(tidal_node[downstream]),
                "steps": [int(s) for s in np.flatnonzero(edge_flow[:, index] < 0.0)],
                "min_q_m3s": round(float(edge_flow[:, index].min()), 4),
            }
        )
    edges.sort(key=lambda e: (not e["tidal"], e["min_q_m3s"]))
    n_reversed_total = len(edges)
    # Counted before the cap, so the number on screen is the run's, not the file's.
    n_tidal_total = sum(1 for e in edges if e["tidal"])
    edges = edges[:MAX_NODES]

    # Geometry for the stored edges only: the cap has already chosen what the console draws.
    root = city_root if city_root is not None else _city_root_for(run_id)
    lines = drain_paths(root) if root is not None else None
    n_missing, n_disagreeing = attach_edge_paths(edges, lines)
    notes = _path_notes(len(edges), lines is not None, n_missing, n_disagreeing)

    product = {
        "run_id": run_id,
        "n_steps": n_steps,
        "n_nodes_total": int(network.n_nodes),
        "n_surcharging": int(active.size),
        "n_stored": len(nodes),
        "n_reversed_edges": n_reversed_total,
        "n_reversed_at_tidal_outfall": n_tidal_total,
        "min_surcharge_m3s": MIN_SURCHARGE_M3S,
        "nodes": nodes,
        "reversed_edge_geometry": DRAINS_EXPORT,
        "n_reversed_stored_without_path": sum(1 for e in edges if e["path"] is None),
        "reversed_edges": edges,
        "notes": notes,
    }
    log.info(
        "products.surcharge",
        run_id=run_id,
        surcharging=int(active.size),
        stored=len(nodes),
        of=int(network.n_nodes),
        reversed_edges=n_reversed_total,
        at_tidal=n_tidal_total,
        without_path=n_missing + n_disagreeing,
    )
    return product


def write_surcharge(run_dir: Path, product: dict[str, Any]) -> None:
    """Write ``node_surcharge.json`` into a run directory."""
    (run_dir / "node_surcharge.json").write_text(
        json.dumps(product, indent=2) + "\n", encoding="utf-8"
    )

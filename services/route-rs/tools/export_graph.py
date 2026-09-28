"""Export the Python router's inputs for the Rust routing service (task P8.12).

Run from the repository root::

    uv run python services/route-rs/tools/export_graph.py [--city mumbai] [--out PATH]

**Why export rather than re-derive.** The road graph the Python router searches is built by
:func:`varuna_route.graph.load_graph` from ``city/<city>/segments.parquet``: a reprojection from
the city's UTM zone to lon/lat through PROJ, a speed per road class, a lane count halved for
two-way streets, and a CSR index sorted by tail. Re-implementing that in Rust would make two
graphs that agree by luck - one PROJ build against another, one rounding of a lane count against
another. So the Rust service never reads the parquet. It reads *this* file, which is the Python
graph's own arrays written out verbatim, and the two routers cannot disagree about the network.

The same goes for the two other things the Python router derives rather than reads:

* the drain design intensity per segment (:func:`varuna_route.reasons.design_intensity_by_segment`),
  which joins two parquet files;
* the vehicle profiles and the router's constants, so a threshold changed in Python and not in Rust
  is refused at the Rust service's start rather than answered differently.

What is **not** exported, because it is read at request time by both routers from the same file:
the run's ``segments_wet.json`` and ``run.json`` (a run is chosen per request) and the ops log
``data/ops/<city>.jsonl`` (closures are an append-only overlay folded at the departure time).

Floats are written by :func:`json.dumps`, which uses ``repr`` - the shortest string that parses
back to the same double - so the Rust side, parsing with a correctly rounded parser, holds
bit-identical values. The file records the size, mtime and SHA-256 of every source it was built
from, so the service can refuse an export that no longer matches the city on disk.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1
"""Bumped whenever the layout below changes; the Rust loader refuses any other value."""

HERE = Path(__file__).resolve().parent
DEFAULT_CACHE = HERE.parent / "cache"


def default_out(city: str) -> Path:
    """``services/route-rs/cache/<city>/route_graph.json`` - gitignored, rebuilt on demand."""
    return DEFAULT_CACHE / city / "route_graph.json"


def _source(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "present": False}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    stat = path.stat()
    return {
        "path": str(path),
        "present": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest,
    }


def sources(city: str) -> dict[str, Any]:
    """The files an export is built from, with the fingerprint that proves which version."""
    from varuna_schemas.paths import city_dir

    root = city_dir(city)
    return {
        "segments": _source(root / "segments.parquet"),
        "drain_nodes": _source(root / "drain_nodes.parquet"),
        "drain_edges": _source(root / "drain_edges.parquet"),
    }


def build(city: str) -> dict[str, Any]:
    """The export document for one city, from the Python router's own loaders."""
    from varuna_route import router, spread
    from varuna_route.forecast import DRY_CM, STEP_MIN
    from varuna_route.graph import load_graph
    from varuna_route.profiles import PROFILES
    from varuna_route.reasons import MAX_AVOIDED_REASONS, design_intensity_by_segment
    from varuna_schemas.models.run import RunIdError, city_code

    graph = load_graph(city)
    design = design_intensity_by_segment(city)

    # Segment ids are interned: the edge table carries an index into this list. The list is in
    # order of first appearance along the CSR edge order, which is deterministic.
    index: dict[str, int] = {}
    segments: list[str] = []
    edge_segment: list[int] = []
    for sid in graph.edge_segment:
        j = index.get(sid)
        if j is None:
            j = len(segments)
            index[sid] = j
            segments.append(sid)
        edge_segment.append(j)

    try:
        code = city_code(city)
    except RunIdError:
        code = ""

    return {
        "format_version": FORMAT_VERSION,
        "city": city,
        "city_code": code,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version.split()[0],
        "sources": sources(city),
        "constants": {
            "dry_cm": DRY_CM,
            "step_min": STEP_MIN,
            "max_slowdown": router.MAX_SLOWDOWN,
            "alternate_penalty": router.ALTERNATE_PENALTY,
            "max_alternates": router.MAX_ALTERNATES,
            "max_avoided_reasons": MAX_AVOIDED_REASONS,
            "labels": list(spread.LABELS),
        },
        "profiles": [
            {
                "key": p.key,
                "label": p.label,
                "depth_cm": float(p.depth_cm),
                "risk_tolerance": float(p.risk_tolerance),
                "speed_scale": float(p.speed_scale),
                "hazard_rule": bool(p.hazard_rule),
            }
            for p in PROFILES.values()
        ],
        "nodes": {
            "id": [int(v) for v in graph.node_ids.tolist()],
            "lon": graph.lon.tolist(),
            "lat": graph.lat.tolist(),
        },
        "edges": {
            "indptr": [int(v) for v in graph.indptr.tolist()],
            "head": [int(v) for v in graph.head.tolist()],
            "tail": [int(v) for v in graph.edge_tail.tolist()],
            "segment": edge_segment,
            "length_m": graph.edge_length_m.tolist(),
            "time_s": graph.edge_time_s.tolist(),
            "name": list(graph.edge_name),
            "lanes": graph.edge_lanes.tolist(),
        },
        "segments": {
            "id": segments,
            "design_intensity_mm_h": [design.get(sid) for sid in segments],
        },
    }


def is_current(path: Path, city: str) -> bool:
    """True when ``path`` exists and was built from the city files now on disk."""
    if not path.is_file():
        return False
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if doc.get("format_version") != FORMAT_VERSION:
        return False
    now = sources(city)
    then = doc.get("sources", {})
    return all(now[key].get("sha256") == then.get(key, {}).get("sha256") for key in now)


def export(city: str, out: Path) -> Path:
    doc = build(city)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    tmp.replace(out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", default="mumbai")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--if-stale",
        action="store_true",
        help="do nothing when the export already matches the city files on disk",
    )
    args = parser.parse_args(argv)
    out: Path = args.out or default_out(args.city)
    if args.if_stale and is_current(out, args.city):
        print(f"{out} is current for {args.city}")
        return 0
    started = time.perf_counter()
    try:
        export(args.city, out)
    except FileNotFoundError as error:
        print(str(error), file=sys.stderr)
        return 2
    took = time.perf_counter() - started
    size = out.stat().st_size / 1e6
    print(f"wrote {out} ({size:.1f} MB) in {took:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

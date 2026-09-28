"""Measure the Rust routing service against the Python router on the same trips (task P8.12).

Run from the repository root, after ``cargo build --release`` in ``services/route-rs``::

    uv run python services/route-rs/tools/bench.py [--reps 15] [--naive dijkstra|ch|both]

Both routers are **warm**: every trip is routed once before timing (which loads the run and,
for Python, builds the graph), then ``--reps`` more times. Reported per trip:

* Python: wall time of ``plan()`` in this process, the same span its ``ms`` field reports;
* Rust: the service's own ``ms`` field (the same span: ``plan`` plus reading the ops log) and
  the client-side HTTP round trip over loopback, which adds serialisation and the socket.

p50 and p95 are nearest-rank over the reps. Graph load is measured cold for both: Python's
``load_graph`` from ``segments.parquet`` with its cache cleared, Rust's parse of the export as
reported by ``/healthz``. The count of python processes running on the machine is printed with
the results, because every number here moves with contention.

The trips are :data:`routers.TRIPS` without the three closure trips, which need an ops log built
from the naive route (the parity test builds it); a background log that folds to nothing is
written so both routers still read and fold one.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import routers
from routers import RUNS, TRIPS, RustServer, Trip


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100.0 * len(ordered)))
    return ordered[rank - 1]


def python_ms(trip: Trip) -> float:
    from varuna_route.router import plan

    started = time.perf_counter()
    plan(
        trip.origin,
        trip.destination,
        depart_at=datetime.fromisoformat(trip.depart_at) if trip.depart_at else None,
        vehicle=trip.profile,
        risk_tolerance=trip.risk_tolerance,
        run_id=RUNS[trip.run] if trip.run is not None else None,
        spread=trip.spread,
        trip_id=trip.trip_id,
        explain=trip.explain,
    )
    return (time.perf_counter() - started) * 1000.0


def rust_ms(server: RustServer, trip: Trip) -> tuple[float, float]:
    data = json.dumps(trip.body()).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/v1/route",
        data=data,
        headers={"content-type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.loads(response.read())
    wall = (time.perf_counter() - started) * 1000.0
    return float(body["ms"]), wall


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=15)
    parser.add_argument("--naive", choices=["dijkstra", "ch", "both"], default="both")
    parser.add_argument("--json", type=Path, help="also write the rows here")
    args = parser.parse_args()

    import os

    from varuna_schemas.paths import runs_dir

    if not routers.EXE.is_file():
        print(f"{routers.EXE} is missing - run `cargo build --release` in services/route-rs")
        return 2
    work = Path(tempfile.mkdtemp(prefix="route-rs-bench-"))
    present = routers.copy_runs(runs_dir(), work)
    routers.write_ops(
        work,
        [
            {
                "kind": "closure",
                "segment_id": "S-EXPIRED",
                "reason": "Tree fall",
                "user": "w1",
                "ts": "2019-07-02T05:00:00+05:30",
                "until": "2019-07-02T05:30:00+05:30",
                "id": "e1",
            }
        ],
    )
    os.environ["VARUNA_DATA_DIR"] = str(work)
    graph = routers.ensure_export("mumbai")

    # Cold graph loads.
    from varuna_route.graph import load_graph

    load_graph.cache_clear()
    started = time.perf_counter()
    load_graph("mumbai")
    py_graph_ms = (time.perf_counter() - started) * 1000.0

    engines = ["dijkstra", "ch"] if args.naive == "both" else [args.naive]
    servers = {name: RustServer(work, graph, name).start() for name in engines}
    health = {name: s.get("/healthz") for name, s in servers.items()}

    trips = [t for t in TRIPS if not t.closed and (t.run is None or present.get(t.run))]
    rows: list[dict[str, Any]] = []
    try:
        for trip in trips:
            python_ms(trip)  # warm: run loaded, graph built
            for s in servers.values():
                rust_ms(s, trip)
            py = [python_ms(trip) for _ in range(args.reps)]
            row: dict[str, Any] = {
                "trip": trip.name,
                "python_p50": pct(py, 50),
                "python_p95": pct(py, 95),
            }
            for name, s in servers.items():
                samples = [rust_ms(s, trip) for _ in range(args.reps)]
                row[f"rust_{name}_p50"] = pct([a for a, _ in samples], 50)
                row[f"rust_{name}_p95"] = pct([a for a, _ in samples], 95)
                row[f"rust_{name}_http_p95"] = pct([b for _, b in samples], 95)
            rows.append(row)
    finally:
        for s in servers.values():
            s.stop()

    procs = routers.python_process_count()
    print(f"{len(rows)} trips x {args.reps} warm reps; {procs} python processes running")
    print(
        f"graph load, cold: python load_graph {py_graph_ms:.0f} ms; rust export parse "
        f"{health[engines[0]]['graph']['load_ms']} ms"
    )
    for name in engines:
        print(
            f"rust naive engine {health[name]['engines']['naive']}: built in "
            f"{health[name]['engines']['naive_build_ms']} ms"
        )
    head = f"{'trip':<58} {'py p50':>7} {'py p95':>7}"
    for name in engines:
        head += f" {name + ' p50':>13} {name + ' p95':>13} {'http p95':>9}"
    print(head)
    for row in rows:
        line = f"{row['trip'][:58]:<58} {row['python_p50']:7.1f} {row['python_p95']:7.1f}"
        for name in engines:
            line += (
                f" {row[f'rust_{name}_p50']:13.1f} {row[f'rust_{name}_p95']:13.1f}"
                f" {row[f'rust_{name}_http_p95']:9.1f}"
            )
        print(line)
    worst_py = max(r["python_p95"] for r in rows)
    print(f"worst p95: python {worst_py:.1f} ms", end="")
    for name in engines:
        print(
            f"; rust {name} {max(r[f'rust_{name}_p95'] for r in rows):.1f} ms "
            f"(http {max(r[f'rust_{name}_http_p95'] for r in rows):.1f} ms)",
            end="",
        )
    print(" - budget 300 ms p95 (SPEC.md 14)")
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "python_processes": procs,
                    "python_graph_ms": py_graph_ms,
                    "health": health,
                    "rows": rows,
                },
                indent=1,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The Rust routing service gives the Python router's answers, field for field (task P8.12).

Run from the repository root::

    uv run pytest services/route-rs/tests/test_route_rs_parity.py -v -s

It builds ``services/route-rs`` in release mode, re-exports the graph if the city changed, copies
the demo runs into a private data directory, writes an ops log that closes streets on purpose,
and starts three Rust services: Dijkstra for the naive search, the contraction hierarchy for the
naive search, and Dijkstra against the closures. Every trip in :data:`routers.TRIPS` - KEM to
Sion at 08:40, a cross-city car trip, trips over closed streets, seven profiles, six cycles,
a one-member run and a dozen trip ids - is planned by Python's ``plan()`` in this process and
posted to each service, and the two JSON bodies are compared leaf by leaf with nothing but
``ms`` excluded: paths, minutes, distances, depths, safe-until, avoided sets, corridor ids,
shares and assignment, reasons and notes.

A difference is reported with the trip, the field and both values. No tolerance is applied
anywhere: the Rust port reproduces CPython's float floor division, round-half-even, Neumaier
``sum()`` and ``timedelta`` microsecond rounding precisely so that none is needed.

Skips, saying why, when cargo is not installed, when ``city/mumbai`` has not been built, or when
the demo runs are not baked.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import routers
from routers import RUNS, TRIPS, RustServer, Trip

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _city_ready() -> tuple[bool, str]:
    from varuna_schemas.paths import city_dir

    segments = city_dir("mumbai") / "segments.parquet"
    if not segments.is_file():
        return (
            False,
            f"{segments} is missing - run `make city CITY=mumbai` (or set VARUNA_CITY_DIR)",
        )
    return True, ""


@pytest.fixture(scope="module")
def harness() -> Iterator[dict[str, Any]]:
    cargo = routers.find_cargo()
    if cargo is None:
        pytest.skip("cargo is not installed (https://rustup.rs); the Rust service cannot be built")
    ready, why = _city_ready()
    if not ready:
        pytest.skip(why)

    from varuna_schemas.paths import runs_dir

    source_runs = runs_dir()
    work = Path(tempfile.mkdtemp(prefix="route-rs-parity-"))
    plain = work / "plain"
    closed = work / "closed"
    present = routers.copy_runs(source_runs, plain)
    routers.copy_runs(source_runs, closed)
    if not present.get("0840"):
        pytest.skip(
            f"the 08:40 demo run {RUNS['0840']} is not in {source_runs} - run "
            "`make bake BUNDLE=MUM-2019-07-02` (or set VARUNA_DATA_DIR)"
        )

    routers.build_release(cargo)
    graph = routers.ensure_export("mumbai")

    # The plain log: entries that fold to nothing at any demo departure, so the overlay is read
    # and folded by both routers but closes nothing.
    background = [
        {
            "kind": "closure",
            "segment_id": "S-EXPIRED",
            "reason": "Tree fall",
            "user": "w1",
            "ts": "2019-07-02T05:00:00+05:30",
            "until": "2019-07-02T05:30:00+05:30",
            "id": "e1",
        },
        {
            "kind": "closure",
            "segment_id": "S-REOPENED",
            "reason": "Crane",
            "user": "w1",
            "ts": "2019-07-02T05:00:00+05:30",
            "id": "e2",
        },
        {
            "kind": "reopen",
            "segment_id": "S-REOPENED",
            "user": "w1",
            "ts": "2019-07-02T05:10:00+05:30",
            "id": "e3",
        },
        {
            "kind": "pump_status",
            "pump_id": "P-01",
            "status": "available",
            "user": "w1",
            "ts": "2019-07-02T05:00:00+05:30",
            "id": "e4",
        },
    ]
    routers.write_ops(plain, background)

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("VARUNA_DATA_DIR", str(plain))

        # Close streets the naive way actually uses, found by asking the Python router.
        from varuna_route.router import plan

        def naive_segments(trip: Trip) -> list[str]:
            result = plan(
                trip.origin,
                trip.destination,
                depart_at=__import__("datetime").datetime.fromisoformat(trip.depart_at),
                vehicle=trip.profile,
                run_id=RUNS[trip.run],
            )
            assert result.naive is not None, trip.name
            return [leg.segment_id for leg in result.naive.legs]

        kem = naive_segments(next(t for t in TRIPS if t.trip_id == "closed-1"))
        worli = naive_segments(next(t for t in TRIPS if t.trip_id == "closed-2"))
        closures = [
            *background,
            {
                "kind": "closure",
                "segment_id": kem[len(kem) // 2],
                "reason": "Manhole cover lifted, crew on site",
                "user": "ward-officer-FN",
                "ts": "2019-07-02T08:12:00",
                "id": "c1",
            },  # naive: IST is assumed, as Python does
            {
                "kind": "closure",
                "segment_id": kem[len(kem) // 3],
                "reason": "  ",
                "user": "ward-officer-FN",
                "ts": "2019-07-02T08:14:00+05:30",
                "id": "c2",
            },
            {
                "kind": "closure",
                "segment_id": worli[len(worli) // 3],
                "reason": "Waterlogged, police barricade",
                "user": "traffic-police",
                "ts": "2019-07-02T08:20:00+05:30",
                "until": "2019-07-02T10:00:00+05:30",
                "id": "c3",
            },
            {
                "kind": "closure",
                "segment_id": worli[(2 * len(worli)) // 3],
                "reason": "Tree down",
                "ts": "2019-07-02T08:25:00+05:30",
                "id": "c4",
            },
        ]
        routers.write_ops(closed, closures)
        (closed / "ops" / "mumbai.jsonl").open("a", encoding="utf-8").write("not a json line\n")

        servers = {
            "dijkstra": RustServer(plain, graph, "dijkstra").start(),
            "ch": RustServer(plain, graph, "ch").start(),
            "closed": RustServer(closed, graph, "dijkstra").start(),
        }
        try:
            yield {
                "mp": mp,
                "plain": plain,
                "closed": closed,
                "present": present,
                "servers": servers,
                "timings": [],
            }
        finally:
            for server in servers.values():
                server.stop()


def _route_both(
    h: dict[str, Any], trip: Trip, server: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    h["mp"].setenv("VARUNA_DATA_DIR", str(h["closed"] if trip.closed else h["plain"]))
    python = routers.python_route(trip)
    status, rust = h["servers"][server].post(trip.body())
    assert status == 200, f"{trip.name}: Rust answered {status}: {rust}"
    return python, rust


def _report(trip: Trip, diffs: list[tuple[str, Any, Any]]) -> str:
    lines = [f"{trip.name}: {len(diffs)} field(s) differ"]
    for path, a, b in diffs[:25]:
        lines.append(
            f"  {path}\n    python: {json.dumps(a)[:300]}\n    rust:   {json.dumps(b)[:300]}"
        )
    return "\n".join(lines)


def test_there_are_at_least_twenty_trips_and_they_cover_the_named_cases() -> None:
    assert len(TRIPS) >= 20
    names = " ".join(t.name for t in TRIPS)
    assert "KEM to Sion, ambulance, 08:40" in names
    assert "cross-city" in names
    assert any(t.closed for t in TRIPS)
    assert len({t.profile for t in TRIPS}) == 7
    assert len({t.run for t in TRIPS}) >= 6


@pytest.mark.parametrize("trip", TRIPS, ids=[t.name for t in TRIPS])
def test_rust_gives_the_python_answer(harness: dict[str, Any], trip: Trip) -> None:
    if trip.run is not None and not harness["present"].get(trip.run):
        pytest.skip(f"run {RUNS[trip.run]} is not baked here")
    server = "closed" if trip.closed else "dijkstra"
    python, rust = _route_both(harness, trip, server)
    harness["timings"].append((trip.name, python["ms"], rust["ms"]))
    diffs = routers.differences(python, rust)
    assert not diffs, _report(trip, diffs)


@pytest.mark.parametrize(
    "trip", [t for t in TRIPS if not t.closed], ids=[t.name for t in TRIPS if not t.closed]
)
def test_the_contraction_hierarchy_gives_the_same_naive_route(
    harness: dict[str, Any], trip: Trip
) -> None:
    """The CH serves only the naive search; the whole response must still be identical."""
    if trip.run is not None and not harness["present"].get(trip.run):
        pytest.skip(f"run {RUNS[trip.run]} is not baked here")
    python, rust = _route_both(harness, trip, "ch")
    diffs = routers.differences(python, rust)
    assert not diffs, _report(trip, diffs)


def test_the_closed_trips_really_go_round_a_closure(harness: dict[str, Any]) -> None:
    """Guard against a vacuous pass: the closures must change the answer they are meant to."""
    trip = next(t for t in TRIPS if t.trip_id == "closed-1")
    _, rust = _route_both(harness, trip, "closed")
    reasons = [a.get("closed_reason") for a in rust["avoided"]]
    assert "Manhole cover lifted, crew on site" in reasons
    assert "" in reasons, "the closure with a blank reason is avoided with an empty reason"
    kinds = [r["kind"] for r in rust["reasons"]]
    with_text = [a for a in rust["avoided"] if a.get("closed_reason")]
    assert kinds.count("closure") == len(with_text) >= 1, (
        "every closure with a stated reason emits one closure reason, and the blank one none"
    )
    assert any("closed by an authority" in n for n in rust["notes"])


ERROR_BODIES: list[tuple[str, Any]] = [
    ("origin not a point", {"origin": 5, "destination": [72.85, 19.03]}),
    ("origin not numbers", {"origin": ["a", "b"], "destination": [72.85, 19.03]}),
    ("origin off the world", {"origin": [200, 19], "destination": [72.85, 19.03]}),
    (
        "unknown profile",
        {"origin": list(routers.KEM), "destination": list(routers.SION), "profile": "boat"},
    ),
    (
        "tolerance not a number",
        {"origin": list(routers.KEM), "destination": list(routers.SION), "risk_tolerance": "high"},
    ),
    (
        "tolerance out of range",
        {"origin": list(routers.KEM), "destination": list(routers.SION), "risk_tolerance": 1.5},
    ),
    (
        "bad departure",
        {"origin": list(routers.KEM), "destination": list(routers.SION), "depart_at": "08:40"},
    ),
    (
        "bad spread flag",
        {"origin": list(routers.KEM), "destination": list(routers.SION), "spread": "yes"},
    ),
    (
        "bad explain flag",
        {"origin": list(routers.KEM), "destination": list(routers.SION), "explain": 1},
    ),
    (
        "unknown run",
        {"origin": list(routers.KEM), "destination": list(routers.SION), "run_id": "MUM-NOPE"},
    ),
]


@pytest.mark.parametrize("case", ERROR_BODIES, ids=[c[0] for c in ERROR_BODIES])
def test_errors_carry_the_python_status_code_and_message(
    harness: dict[str, Any], case: tuple[str, Any]
) -> None:
    from fastapi import HTTPException
    from varuna_api.routers.route import route

    harness["mp"].setenv("VARUNA_DATA_DIR", str(harness["plain"]))
    _, body = case
    try:
        route(body)
    except HTTPException as error:
        expected = (error.status_code, error.detail["code"], error.detail["message"])
    else:  # pragma: no cover - every case is an error
        pytest.fail("the Python handler accepted a body that should be refused")
    status, rust = harness["servers"]["dijkstra"].post(body)
    got = (status, rust["error"]["code"], rust["error"]["message"])
    assert got == expected
    assert rust["error"]["run_id"] is None


def test_timings_are_reported(harness: dict[str, Any]) -> None:
    """Not a benchmark (tools/bench.py is) - a record of what this run measured, and the budget."""
    rows = harness["timings"]
    if not rows:
        pytest.skip("no trip was routed")
    py = [r[1] for r in rows]
    rs = [r[2] for r in rows]
    print(
        f"\n{len(rows)} trips, first pass (cold for each run): python plan() median "
        f"{statistics.median(py):.1f} ms, max {max(py):.1f} ms; rust median "
        f"{statistics.median(rs):.1f} ms, max {max(rs):.1f} ms; "
        f"{routers.python_process_count()} python processes"
    )
    # Section 14's route budget is 300 ms p95; the first call on each run includes loading it.
    assert max(rs) < 300.0 or os.environ.get("CI"), f"a Rust route took {max(rs):.1f} ms"

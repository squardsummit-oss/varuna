"""Authority edits are an overlay, and the products stay byte-identical (task D-06).

The contract other chunks write through is ``append`` / ``active`` /
``OpsOverlay.closed_segment_ids`` / ``OpsOverlay.reason_for``, so each of those is tested
directly rather than only through a route. The last test is the one rule 8 needs: a closure
changes what a route says and changes no byte of what the cycle wrote.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from varuna_route import ops_overlay as ops
from varuna_route.forecast import SegmentDepths
from varuna_route.graph import RoadGraph
from varuna_route.profiles import profile
from varuna_route.router import _search

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2019, 7, 2, 8, 40, tzinfo=IST)


def test_append_stamps_and_never_rewrites(ops_data_dir: Path) -> None:
    first = ops.append("mumbai", {"kind": "closure", "segment_id": "S1-000", "reason": "Water"})
    second = ops.append("mumbai", {"kind": "closure", "segment_id": "S2-000", "reason": "Tree"})

    assert first["id"] and second["id"] and first["id"] != second["id"]
    assert first["ts"] and second["ts"]

    path = ops.overlay_path("mumbai")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, "one JSON object per line, appended"
    assert json.loads(lines[0])["segment_id"] == "S1-000"
    assert json.loads(lines[1])["segment_id"] == "S2-000"

    ops.append("mumbai", {"kind": "reopen", "segment_id": "S1-000"})
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3, (
        "a reopen appends a third line; it does not delete the closure that made the first"
    )


def test_an_entry_that_names_nothing_is_refused(ops_data_dir: Path) -> None:
    with pytest.raises(ValueError, match="kind"):
        ops.append("mumbai", {"kind": "demolition", "segment_id": "S1-000"})
    with pytest.raises(ValueError, match="segment_id"):
        ops.append("mumbai", {"kind": "closure", "reason": "Water"})
    with pytest.raises(ValueError, match="pump_id"):
        ops.append("mumbai", {"kind": "pump_status", "status": "unavailable"})
    with pytest.raises(ValueError, match="status"):
        ops.append("mumbai", {"kind": "pump_status", "pump_id": "P-12", "status": "swimming"})
    assert not ops.overlay_path("mumbai").exists(), "a refused entry writes nothing"


def test_active_folds_the_log_and_applies_expiries(ops_data_dir: Path) -> None:
    ops.append(
        "mumbai",
        {
            "kind": "closure",
            "segment_id": "S1-000",
            "reason": "Water main work",
            "user": "ward-officer",
            "ts": T0.isoformat(),
            "until": (T0 + timedelta(hours=1)).isoformat(),
        },
    )
    ops.append(
        "mumbai",
        {"kind": "closure", "segment_id": "S2-000", "reason": "Tree down", "ts": T0.isoformat()},
    )
    ops.append("mumbai", {"kind": "closure", "segment_id": "S3-000", "reason": "Cable"})
    ops.append("mumbai", {"kind": "reopen", "segment_id": "S3-000"})

    during = ops.active("mumbai", at=T0 + timedelta(minutes=30))
    assert during.closed_segment_ids == {"S1-000", "S2-000"}
    assert during.reason_for("S1-000") == "Water main work"
    assert during.closures["S1-000"].user == "ward-officer"
    assert during.reason_for("S3-000") is None, "a reopen lifts the closure before it"
    assert during.n_entries == 4

    after = ops.active("mumbai", at=T0 + timedelta(hours=2))
    assert after.closed_segment_ids == {"S2-000"}, (
        "the expiry is applied, the open-ended one is not"
    )


def test_pump_status_keeps_only_the_latest(ops_data_dir: Path) -> None:
    ops.append("mumbai", {"kind": "pump_status", "pump_id": "P-12", "status": "unavailable"})
    ops.append("mumbai", {"kind": "pump_status", "pump_id": "P-15", "status": "unavailable"})
    ops.append("mumbai", {"kind": "pump_status", "pump_id": "P-12", "status": "available"})

    overlay = ops.active("mumbai")
    assert overlay.unavailable_pump_ids() == {"P-15"}
    assert overlay.pumps["P-12"].status == "available"


def test_a_corrupt_line_does_not_take_the_endpoint_down(ops_data_dir: Path) -> None:
    ops.append("mumbai", {"kind": "closure", "segment_id": "S1-000", "reason": "Water"})
    path = ops.overlay_path("mumbai")
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "closure", "segment_id": tru\n')
    ops.append("mumbai", {"kind": "closure", "segment_id": "S2-000", "reason": "Tree"})

    overlay = ops.active("mumbai")
    assert overlay.closed_segment_ids == {"S1-000", "S2-000"}


def test_no_log_is_an_empty_overlay(ops_data_dir: Path) -> None:
    overlay = ops.active("mumbai")
    assert overlay.empty
    assert overlay.closed_segment_ids == frozenset()
    assert overlay.reason_for("S1-000") is None


def _diamond() -> RoadGraph:
    arcs = [
        (0, 1, "SHORT-A", 500.0, 60.0, "Short Marg", 2.0),
        (0, 2, "LONG-A", 1250.0, 150.0, "Long Marg", 1.0),
        (1, 3, "SHORT-B", 500.0, 60.0, "Short Marg", 2.0),
        (2, 3, "LONG-B", 1250.0, 150.0, "Long Marg", 1.0),
    ]
    tails = np.array([a[0] for a in arcs], dtype=np.int64)
    indptr = np.zeros(5, dtype=np.int64)
    np.add.at(indptr, tails + 1, 1)
    np.cumsum(indptr, out=indptr)
    return RoadGraph(
        node_ids=np.arange(4, dtype=np.int64),
        lon=np.array([72.84, 72.845, 72.85, 72.855]),
        lat=np.array([19.00, 19.005, 19.01, 19.015]),
        indptr=indptr,
        head=np.array([a[1] for a in arcs], dtype=np.int64),
        edge_segment=[a[2] for a in arcs],
        edge_length_m=np.array([a[3] for a in arcs]),
        edge_time_s=np.array([a[4] for a in arcs]),
        edge_name=[a[5] for a in arcs],
        edge_tail=tails,
        edge_lanes=np.array([a[6] for a in arcs], dtype=np.float64),
    )


def _dry() -> SegmentDepths:
    return SegmentDepths(
        run_id="TEST",
        times=tuple(T0 + timedelta(minutes=5 * k) for k in range(6)),
        depth_cm={},
        n_steps=6,
        n_total=4,
        ensemble_n=20,
    )


def test_a_closed_street_is_impassable_on_a_dry_road() -> None:
    """The point of a closure: it beats the forecast, and the forecast here says 'dry'."""
    graph = _diamond()
    depths = _dry()
    car = profile("car")

    open_road = _search(graph, depths, source=0, target=3, depart=T0, vehicle=car, avoid_water=True)
    closed_road = _search(
        graph,
        depths,
        source=0,
        target=3,
        depart=T0,
        vehicle=car,
        avoid_water=True,
        closed=frozenset({"SHORT-B"}),
    )
    assert open_road is not None
    assert closed_road is not None
    assert [graph.edge_segment[e] for e in open_road] == ["SHORT-A", "SHORT-B"]
    assert [graph.edge_segment[e] for e in closed_road] == ["LONG-A", "LONG-B"]


def test_the_naive_route_still_walks_into_a_closure() -> None:
    """The naive route is the comparison; it honours neither water nor a closure."""
    graph = _diamond()
    naive = _search(
        graph,
        _dry(),
        source=0,
        target=3,
        depart=T0,
        vehicle=profile("car"),
        avoid_water=False,
        closed=frozenset({"SHORT-B"}),
    )
    assert naive is not None
    assert [graph.edge_segment[e] for e in naive] == ["SHORT-A", "SHORT-B"]


def _digest(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def test_writing_a_closure_changes_no_baked_product(demo_runs: Path, demo_run_id: str) -> None:
    """SPEC.md rule 8 and task P5.9: an authority edit must not touch a run.

    The sha256 of every file the cycle wrote is taken before and after a closure is appended,
    and they match. The overlay lands beside the runs, in ``data/ops/``, and is read at request
    time - which is the whole reason the desk does not rewrite products.
    """
    from varuna_route.forecast import load_depths

    run_folder = demo_runs / "runs" / demo_run_id
    before = _digest(run_folder)
    assert before, "the copied demo run has files to compare"

    entry = ops.append(
        "mumbai",
        {
            "kind": "closure",
            "segment_id": next(iter(load_depths(demo_run_id).depth_cm)),
            "reason": "Water main work",
            "user": "ward-officer",
        },
    )
    assert ops.overlay_path("mumbai").is_file()
    assert ops.active("mumbai").reason_for(entry["segment_id"]) == "Water main work"

    assert _digest(run_folder) == before, "a closure rewrote a baked product"
    assert ops.overlay_path("mumbai").parent.name == "ops"
    # The demo data dir is shared for the session; leave no closure behind for the next test.
    ops.overlay_path("mumbai").unlink()

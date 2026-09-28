"""The pedestrian hazard rule (SPEC.md 7.4 AC3, Appendix A): unsafe at ``h >= 0.3 m`` or
``h * v >= 0.5 m^2/s``.

No baked run carries a flow speed, so on real runs only the depth half can apply and the response
must say so. These tests pin both sides of that: with a speed on a synthetic network the velocity
half diverts a walker the depth half would have let through, and without one nothing changes and
nothing is assumed.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from varuna_route.forecast import SegmentDepths, load_depths
from varuna_route.graph import RoadGraph
from varuna_route.profiles import HAZARD_M2_S, PROFILES, hazard_unsafe, profile
from varuna_route.router import _blocking, _build_route, _search, hazard_note

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2019, 7, 2, 8, 40, tzinfo=IST)


def _diamond() -> RoadGraph:
    """0 -> 1 -> 3 short ("Short Marg"), 0 -> 2 -> 3 long ("Long Marg")."""
    arcs = [
        (0, 1, "SHORT-A", 100.0, 60.0, "Short Marg", 1.0),
        (0, 2, "LONG-A", 250.0, 150.0, "Long Marg", 1.0),
        (1, 3, "SHORT-B", 100.0, 60.0, "Short Marg", 1.0),
        (2, 3, "LONG-B", 250.0, 150.0, "Long Marg", 1.0),
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


def _depths(depth: dict[str, float], velocity: dict[str, float] | None = None) -> SegmentDepths:
    n = 6
    return SegmentDepths(
        run_id="TEST",
        times=tuple(T0 + timedelta(minutes=5 * k) for k in range(n)),
        depth_cm={k: [v] * n for k, v in depth.items()},
        n_steps=n,
        n_total=4,
        ensemble_n=1,
        velocity_ms={k: [v] * n for k, v in (velocity or {}).items()},
    )


def _path(graph: RoadGraph, edges: list[int] | None) -> list[str]:
    assert edges is not None
    return [graph.edge_segment[e] for e in edges]


def test_the_rule_is_appendix_a() -> None:
    assert HAZARD_M2_S == 0.5
    assert hazard_unsafe(25.0, 2.0)  # 0.25 m * 2 m/s = 0.5, on the line
    assert not hazard_unsafe(25.0, 1.9)
    assert hazard_unsafe(20.0, -3.0)  # direction does not matter, speed does
    assert not hazard_unsafe(0.0, 10.0)  # no water, no hazard
    # No speed is not a speed of zero dressed up: it is "this half cannot be checked".
    assert not hazard_unsafe(29.0, None)
    assert PROFILES["pedestrian"].hazard_rule
    assert PROFILES["pedestrian"].depth_cm == 30.0
    assert not any(p.hazard_rule for k, p in PROFILES.items() if k != "pedestrian")


def test_fast_shallow_water_diverts_a_walker_the_depth_rule_would_let_through() -> None:
    """20 cm is under a walker's 30 cm; at 3 m/s it is 0.6 m^2/s, over the hazard line."""
    graph = _diamond()
    walker = profile("pedestrian")
    fast = _depths({"SHORT-B": 20.0}, {"SHORT-B": 3.0})
    edges = _search(graph, fast, source=0, target=3, depart=T0, vehicle=walker, avoid_water=True)
    assert _path(graph, edges) == ["LONG-A", "LONG-B"]


def test_slow_water_of_the_same_depth_does_not() -> None:
    graph = _diamond()
    walker = profile("pedestrian")
    slow = _depths({"SHORT-B": 20.0}, {"SHORT-B": 2.0})  # 0.4 m^2/s
    edges = _search(graph, slow, source=0, target=3, depart=T0, vehicle=walker, avoid_water=True)
    assert _path(graph, edges) == ["SHORT-A", "SHORT-B"]


def test_without_a_speed_only_the_depth_half_applies() -> None:
    graph = _diamond()
    walker = profile("pedestrian")
    shallow = _depths({"SHORT-B": 20.0})
    deep = _depths({"SHORT-B": 31.0})
    assert _path(
        graph,
        _search(graph, shallow, source=0, target=3, depart=T0, vehicle=walker, avoid_water=True),
    ) == ["SHORT-A", "SHORT-B"]
    assert _path(
        graph,
        _search(graph, deep, source=0, target=3, depart=T0, vehicle=walker, avoid_water=True),
    ) == ["LONG-A", "LONG-B"]


def test_the_velocity_half_is_the_pedestrian_s_alone() -> None:
    """A car in 20 cm of fast water is slowed, not stopped: the hazard product is a walker's rule."""
    graph = _diamond()
    fast = _depths({"SHORT-B": 20.0}, {"SHORT-B": 3.0})
    for key in ("car", "bus", "ambulance"):
        edges = _search(
            graph, fast, source=0, target=3, depart=T0, vehicle=profile(key), avoid_water=True
        )
        assert _path(graph, edges) == ["SHORT-A", "SHORT-B"], key


def test_the_leg_reports_the_hazard_as_a_refusal_and_safe_until_honours_it() -> None:
    """The avoided list and safe-until read the same rule the search used, never a second one."""
    graph = _diamond()
    walker = profile("pedestrian")
    fast = _depths({"SHORT-B": 20.0}, {"SHORT-B": 3.0})
    assert _blocking(fast, "SHORT-B", walker, 0) == 1.0
    assert _blocking(fast, "SHORT-B", profile("car"), 0) == 0.0
    route = _build_route(graph, fast, [0, 2], depart=T0, vehicle=walker, source=0)
    assert route.legs[1].probability == 1.0
    assert route.safe_until is None
    still = _build_route(
        graph, _depths({"SHORT-B": 20.0}), [0, 2], depart=T0, vehicle=walker, source=0
    )
    assert still.legs[1].probability == 0.0
    assert still.safe_until is not None


def test_the_note_says_which_half_was_applied() -> None:
    assert "only the depth half" in hazard_note(_depths({"SHORT-B": 20.0}))
    assert "no speed is assumed" in hazard_note(_depths({"SHORT-B": 20.0}))
    with_speed = hazard_note(_depths({"SHORT-B": 20.0}, {"SHORT-B": 3.0}))
    assert "flow speed reaches 0.5 m2/s" in with_speed
    assert "only the depth half" not in with_speed


def test_no_committed_run_carries_a_velocity(demo_runs: Path, demo_run_id: str) -> None:
    """The measurement behind the note: the 08:40 demo cycle has depth and p_gt and no speed.

    When the products stage starts writing ``velocity_ms`` this fails, and the note and the
    SPEC.md 7.4 AC3 caveat both stop being true - which is the moment to read it again.
    """
    wet = json.loads(
        (demo_runs / "runs" / demo_run_id / "segments_wet.json").read_text(encoding="utf-8")
    )
    assert "velocity_ms" not in wet
    depths = load_depths(demo_run_id)
    assert not depths.has_velocity
    assert depths.velocity_at(next(iter(depths.depth_cm)), 0) is None


def test_a_velocity_block_in_the_run_is_read(
    demo_runs: Path, demo_run_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loader reads a ``velocity_ms`` block of the same shape as ``depth_cm`` when one exists."""
    source = demo_runs / "runs" / demo_run_id
    target_root = tmp_path / "data"
    target = target_root / "runs" / demo_run_id
    target.mkdir(parents=True)
    wet = json.loads((source / "segments_wet.json").read_text(encoding="utf-8"))
    segment = next(iter(wet["depth_cm"]))
    wet["velocity_ms"] = {segment: [1.5] * len(wet["depth_cm"][segment])}
    (target / "segments_wet.json").write_text(json.dumps(wet), encoding="utf-8")
    (target / "run.json").write_text((source / "run.json").read_text(encoding="utf-8"), "utf-8")
    monkeypatch.setenv("VARUNA_DATA_DIR", str(target_root))
    depths = load_depths(demo_run_id)
    assert depths.has_velocity
    assert depths.velocity_at(segment, 3) == 1.5
    assert depths.velocity_at("not-a-segment", 3) is None
    # Nothing else about the run moved.
    assert replace(depths, velocity_ms={}).depth_cm == depths.depth_cm

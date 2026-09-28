"""The router reads the run's own exceedance, and the risk tolerance bites (task D-01).

Before this, ``P(depth > threshold)`` was a comparison against the median depth, so every
probability the API printed was exactly 1.0 or 0.0 and ``risk_tolerance`` changed nothing - on
runs that have carried an ensemble ``p_gt`` block since the 2026-09-13 re-bake (50 members since 2026-09-26). Two claims are
tested here: the probability is really the ensemble's on a real demo run, and a route really
changes when the tolerance does.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from varuna_route.forecast import SegmentDepths, load_depths
from varuna_route.graph import RoadGraph
from varuna_route.profiles import Profile, profile
from varuna_route.router import _search

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2019, 7, 2, 8, 40, tzinfo=IST)


def test_the_demo_run_gives_probabilities_between_zero_and_one(
    demo_runs: Path, demo_run_id: str
) -> None:
    """A segment whose members disagree scores strictly between 0 and 1 on the 08:40 run.

    This is the whole of D-01 in one assertion: if the router were still comparing the median
    depth to the threshold, every one of these would be 0.0 or 1.0.
    """
    depths = load_depths(demo_run_id)
    assert depths.has_exceedance, "the 08:40 demo run carries a 50-member p_gt block"
    # 50 since P7.6: twenty Sky members crossed with blockage and storage draws (ADR-0076).
    assert depths.ensemble_n == 50

    strictly_between = [
        (segment_id, threshold, step, value)
        for threshold, by_segment in depths.p_gt.items()
        for segment_id, series in by_segment.items()
        for step, value in enumerate(series)
        if 0.0 < value < 1.0
    ]
    assert strictly_between, "no member disagreement anywhere in the run"

    segment_id, threshold, step, value = strictly_between[0]
    assert depths.exceedance(segment_id, threshold, step) == pytest.approx(value)
    assert 0.0 < depths.exceedance(segment_id, threshold, step) < 1.0

    # And the four thresholds the profiles use are all there, so no profile silently falls back.
    assert set(depths.p_gt) == {15.0, 30.0, 45.0, 60.0}


def test_probability_and_depth_disagree_on_the_demo_run(demo_runs: Path, demo_run_id: str) -> None:
    """The point of reading ``p_gt``: it says something the depth comparison cannot.

    A street can sit at 35 cm on the median and still be under a car's threshold on most
    members. The old code called that 1.0 - impassable, no argument - and diverted a car that
    the ensemble gives a one-in-ten chance of being stopped.
    """
    depths = load_depths(demo_run_id)
    disagreements = 0
    for segment_id, series in depths.depth_cm.items():
        for step, depth in enumerate(series):
            old = 1.0 if depth > 30.0 else 0.0
            new = depths.exceedance(segment_id, 30.0, step)
            if abs(new - old) > 0.25:
                disagreements += 1
    assert disagreements > 0, (
        "the run's exceedance never differs from the threshold comparison by more than 0.25, "
        "so reading it would be a no-op"
    )


def test_a_segment_the_run_never_wetted_cannot_exceed_anything(
    demo_runs: Path, demo_run_id: str
) -> None:
    depths = load_depths(demo_run_id)
    assert depths.exceedance("S-not-a-segment", 30.0, 0) == 0.0


def test_a_run_without_p_gt_falls_back_to_the_median_depth(
    demo_runs: Path, demo_run_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-member bake, or one older than the change, still routes - and says so."""
    source = load_depths(demo_run_id)
    del source

    root = tmp_path / "one-member"
    run_id = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"
    folder = root / "runs" / run_id
    folder.mkdir(parents=True)
    product = {
        "run_id": run_id,
        "valid_ts": [(T0 + timedelta(minutes=5 * k)).isoformat() for k in range(3)],
        "min_depth_cm": 5.0,
        "n_segments_total": 2,
        "n_segments_wet": 1,
        "depth_cm": {"S1-000": [10.0, 35.0, 40.0]},
    }
    (folder / "segments_wet.json").write_text(json.dumps(product), encoding="utf-8")
    (folder / "run.json").write_text(json.dumps({"ensemble_n": 1}), encoding="utf-8")
    monkeypatch.setenv("VARUNA_DATA_DIR", str(root))

    depths = load_depths(run_id)
    assert not depths.has_exceedance
    assert depths.exceedance("S1-000", 30.0, 0) == 0.0
    assert depths.exceedance("S1-000", 30.0, 1) == 1.0


def _diamond() -> RoadGraph:
    """Short way through node 1, long way through node 2 - as in ``test_router``."""
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


def _uncertain_depths() -> SegmentDepths:
    """The short way sits at 28 cm - under a car's 30 cm on the median, over it on 3 members."""
    return SegmentDepths(
        run_id="TEST-20",
        times=tuple(T0 + timedelta(minutes=5 * k) for k in range(6)),
        depth_cm={"SHORT-B": [28.0] * 6},
        n_steps=6,
        n_total=4,
        ensemble_n=20,
        p_gt={
            15.0: {"SHORT-B": [1.0] * 6},
            30.0: {"SHORT-B": [0.3] * 6},
            45.0: {"SHORT-B": [0.0] * 6},
            60.0: {"SHORT-B": [0.0] * 6},
        },
    )


def test_risk_tolerance_changes_the_route() -> None:
    """The acceptance criterion: two tolerances, two roads, one run.

    The short way carries a 0.3 chance of exceeding a car's 30 cm. A driver who accepts a coin
    flip takes it; a cautious one who accepts only 0.2 is sent the long way. Under the old
    threshold comparison both got 0.0 and both took the short way, whatever they asked for.
    """
    graph = _diamond()
    depths = _uncertain_depths()
    base = profile("car")

    relaxed = Profile(base.key, base.label, base.depth_cm, 0.5, base.speed_scale, base.hazard_rule)
    cautious = Profile(base.key, base.label, base.depth_cm, 0.2, base.speed_scale, base.hazard_rule)

    took = _search(graph, depths, source=0, target=3, depart=T0, vehicle=relaxed, avoid_water=True)
    avoided = _search(
        graph, depths, source=0, target=3, depart=T0, vehicle=cautious, avoid_water=True
    )
    assert took is not None
    assert avoided is not None
    assert [graph.edge_segment[e] for e in took] == ["SHORT-A", "SHORT-B"]
    assert [graph.edge_segment[e] for e in avoided] == ["LONG-A", "LONG-B"]


def test_the_median_depth_alone_would_have_diverted_nobody() -> None:
    """The counterfactual, so the test above cannot pass for the wrong reason.

    28 cm is under a car's 30 cm threshold, so the depth comparison scores it 0.0 at every
    tolerance and the short way is taken by both drivers.
    """
    depths = _uncertain_depths()
    assert depths.depth_at("SHORT-B", 0) < 30.0
    assert depths.exceedance("SHORT-B", 30.0, 0) == pytest.approx(0.3)


def test_step_after_answers_exactly_what_step_at_answers() -> None:
    """The search's integer-division shortcut and the datetime method must never disagree.

    `_search` asks :meth:`SegmentDepths.step_after` once per node it pops, because
    :meth:`step_at` built a `timedelta` twenty thousand times a route - 447 ms to 57.7 ms on the
    KEM-to-Sion ambulance trip, measured on the 08:40 demo run. The shortcut is only safe while
    it is the same function, including past both ends of the window, so this walks a minute at a
    time from an hour before a departure to four hours after it and compares the two.
    """
    depths = SegmentDepths(
        run_id="TEST-STEPS",
        times=tuple(T0 + timedelta(minutes=5 * k) for k in range(36)),
        depth_cm={},
        n_steps=36,
        n_total=1,
        ensemble_n=20,
    )
    for depart_min in (-30, 0, 7, 120, 400):
        depart = T0 + timedelta(minutes=depart_min)
        offset = depths.depart_offset_s(depart)
        for elapsed_min in range(-60, 240):
            elapsed_s = elapsed_min * 60.0
            when = depart + timedelta(seconds=elapsed_s)
            assert depths.step_after(offset, elapsed_s) == depths.step_at(when), (
                f"depart {depart_min} min, elapsed {elapsed_min} min"
            )

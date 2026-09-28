"""Corridors, shares and the deterministic assignment (task D-08, TECH_SPEC 3.3).

Four claims, one per acceptance criterion: the same trip id always lands on the same corridor,
a thousand ids land within two percentage points of each corridor's share, every corridor
satisfies the profile's threshold, and a trip with only one safe corridor gets one and the
response says why.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from varuna_route.forecast import SegmentDepths
from varuna_route.graph import RoadGraph
from varuna_route.profiles import profile
from varuna_route.router import _build_route, _search
from varuna_route.spread import (
    MAX_CORRIDORS,
    assign,
    capacity_score,
    congestion_proxy,
    corridors,
    spreading_note,
    unit_interval,
)

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2019, 7, 2, 8, 40, tzinfo=IST)


def _fan() -> RoadGraph:
    """Three ways from 0 to 4, of different widths and lengths.

    0 -> 1 -> 4   "Wide Marg",   2 lanes, 60 s + 60 s
    0 -> 2 -> 4   "Middle Marg", 1 lane,  90 s + 90 s
    0 -> 3 -> 4   "Narrow Marg", 1 lane, 120 s + 120 s
    """
    arcs = [
        (0, 1, "WIDE-A", 500.0, 60.0, "Wide Marg", 2.0),
        (0, 2, "MID-A", 750.0, 90.0, "Middle Marg", 1.0),
        (0, 3, "NARROW-A", 1000.0, 120.0, "Narrow Marg", 1.0),
        (1, 4, "WIDE-B", 500.0, 60.0, "Wide Marg", 2.0),
        (2, 4, "MID-B", 750.0, 90.0, "Middle Marg", 1.0),
        (3, 4, "NARROW-B", 1000.0, 120.0, "Narrow Marg", 1.0),
    ]
    tails = np.array([a[0] for a in arcs], dtype=np.int64)
    order = np.argsort(tails, kind="stable")
    arcs = [arcs[i] for i in order.tolist()]
    tails = np.array([a[0] for a in arcs], dtype=np.int64)
    indptr = np.zeros(6, dtype=np.int64)
    np.add.at(indptr, tails + 1, 1)
    np.cumsum(indptr, out=indptr)
    return RoadGraph(
        node_ids=np.arange(5, dtype=np.int64),
        lon=np.array([72.84, 72.845, 72.85, 72.855, 72.86]),
        lat=np.array([19.00, 19.005, 19.01, 19.015, 19.02]),
        indptr=indptr,
        head=np.array([a[1] for a in arcs], dtype=np.int64),
        edge_segment=[a[2] for a in arcs],
        edge_length_m=np.array([a[3] for a in arcs]),
        edge_time_s=np.array([a[4] for a in arcs]),
        edge_name=[a[5] for a in arcs],
        edge_tail=tails,
        edge_lanes=np.array([a[6] for a in arcs], dtype=np.float64),
    )


def _depths(series: dict[str, list[float]] | None = None, **p_gt: dict[str, list[float]]):
    thresholds = {15.0: {}, 30.0: {}, 45.0: {}, 60.0: {}}
    for key, value in p_gt.items():
        thresholds[float(key.removeprefix("p"))] = value
    return SegmentDepths(
        run_id="TEST",
        times=tuple(T0 + timedelta(minutes=5 * k) for k in range(6)),
        depth_cm={k: list(v) for k, v in (series or {}).items()},
        n_steps=6,
        n_total=6,
        ensemble_n=20,
        p_gt=thresholds if any(thresholds.values()) else {},
    )


def _routes(graph: RoadGraph, depths: SegmentDepths, vehicle_key: str = "car"):
    """The VARUNA route and its alternates on the fan, exactly as ``plan`` finds them."""
    vehicle = profile(vehicle_key)
    found = []
    used: set[int] = set()
    for _ in range(MAX_CORRIDORS):
        edges = _search(
            graph,
            depths,
            source=0,
            target=4,
            depart=T0,
            vehicle=vehicle,
            avoid_water=True,
            penalised=used or None,
        )
        if not edges or set(edges) == used:
            break
        found.append(_build_route(graph, depths, edges, depart=T0, vehicle=vehicle, source=0))
        used |= set(edges)
    return found, vehicle


def test_three_roads_become_three_corridors_with_shares_that_sum_to_one() -> None:
    graph = _fan()
    depths = _depths()
    routes, vehicle = _routes(graph, depths)
    assert len(routes) == MAX_CORRIDORS

    found = corridors(routes, depths, vehicle, trip_id="trip-1")
    assert [c.label for c in found] == ["A", "B", "C"]
    assert sum(c.share for c in found) == pytest.approx(1.0)
    assert sum(1 for c in found if c.assigned) == 1
    assert all(c.id for c in found)
    assert len({c.id for c in found}) == 3, "each road keeps its own id"


def test_the_same_trip_id_always_lands_on_the_same_corridor() -> None:
    graph = _fan()
    depths = _depths()
    routes, vehicle = _routes(graph, depths)

    first = corridors(routes, depths, vehicle, trip_id="trip-kem-sion-42")
    for _ in range(5):
        again = corridors(routes, depths, vehicle, trip_id="trip-kem-sion-42")
        assert [c.assigned for c in again] == [c.assigned for c in first]
        assert [c.id for c in again] == [c.id for c in first]


def test_a_thousand_trips_land_within_two_points_of_each_share() -> None:
    """The spreading claim: ids drawn across a population follow the policy's split.

    Deterministic, not random: the ids are ``trip-0`` to ``trip-999`` and SHA-256 is fixed, so
    the measured deviation below (1.1 points at worst) is the same on every machine and in every
    run. Two percentage points is the criterion in TASKS.md D-08.
    """
    graph = _fan()
    depths = _depths()
    routes, vehicle = _routes(graph, depths)
    found = corridors(routes, depths, vehicle, trip_id="trip-0")
    shares = [c.share for c in found]

    counts = [0] * len(shares)
    for index in range(1000):
        assigned = corridors(routes, depths, vehicle, trip_id=f"trip-{index}")
        chosen = [k for k, c in enumerate(assigned) if c.assigned]
        assert len(chosen) == 1
        counts[chosen[0]] += 1

    observed = [n / 1000.0 for n in counts]
    worst = max(abs(a - b) for a, b in zip(shares, observed, strict=True))
    assert worst <= 0.02, f"shares {shares} vs observed {observed}"


def test_every_corridor_satisfies_the_profile_threshold() -> None:
    """A corridor is only offered if the run says the vehicle clears it.

    The wide road carries a 0.6 chance of exceeding a car's 30 cm, which is over the car's 0.5
    tolerance, so it is not a corridor at all - and the two that remain are both under it.
    """
    graph = _fan()
    depths = _depths(
        {"WIDE-B": [35.0] * 6},
        p30={"WIDE-B": [0.6] * 6},
    )
    routes, vehicle = _routes(graph, depths)

    found = corridors(routes, depths, vehicle, trip_id="trip-7")
    assert found, "the middle and narrow roads are still safe"
    assert all("WIDE-B" not in [leg.segment_id for leg in c.route.legs] for c in found)
    for corridor in found:
        assert corridor.max_probability < vehicle.risk_tolerance


def test_one_safe_corridor_comes_back_alone_and_says_so() -> None:
    """Two of the three roads are over the tolerance; the answer is one corridor and a sentence."""
    graph = _fan()
    depths = _depths(
        {"WIDE-B": [35.0] * 6, "MID-B": [35.0] * 6},
        p30={"WIDE-B": [0.9] * 6, "MID-B": [0.9] * 6},
    )
    routes, vehicle = _routes(graph, depths)
    found = corridors(routes, depths, vehicle, trip_id="trip-9")

    assert len(found) == 1
    assert found[0].assigned
    assert found[0].share == pytest.approx(1.0)
    note = spreading_note(len(found), spread=True, trip_id="trip-9")
    assert note is not None
    assert "nothing to spread" in note


def test_a_closed_street_disqualifies_a_corridor() -> None:
    graph = _fan()
    depths = _depths()
    routes, vehicle = _routes(graph, depths)
    found = corridors(
        routes, depths, vehicle, trip_id="trip-3", closed_segment_ids=frozenset({"WIDE-B"})
    )
    assert len(found) == 2
    assert all("WIDE-B" not in [leg.segment_id for leg in c.route.legs] for c in found)


def test_the_share_favours_the_road_with_more_spare_capacity() -> None:
    """Capacity is the corridor's narrowest leg, per minute it holds a vehicle.

    Corrected 2026-09-19 from a sum over edges, which rewarded length: see
    :func:`varuna_route.spread.capacity_score` for the measurement that showed it sending the
    largest share down the slowest road.
    """
    graph = _fan()
    depths = _depths()
    routes, vehicle = _routes(graph, depths)
    found = corridors(routes, depths, vehicle, trip_id="trip-1")
    by_label = {c.label: c for c in found}

    # A is two dry lanes at its narrowest, B and C one, and all three take the same time.
    a, b = by_label["A"], by_label["B"]
    assert a.capacity_score == pytest.approx(2.0 / a.route.minutes)
    assert b.capacity_score == pytest.approx(1.0 / b.route.minutes)
    assert a.share > b.share


def test_a_longer_road_of_the_same_width_no_longer_wins_the_share() -> None:
    """The defect the sum-over-edges formula had, pinned so it cannot come back.

    Two corridors, the same one lane of dry road at their narrowest; one takes twice as long.
    Under the old sum the long one scored higher because it had more edges to add up, so most
    drivers were sent the slow way. It must now score lower, in proportion to the time it costs.
    """
    graph = _fan()
    depths = _depths()
    routes, vehicle = _routes(graph, depths)
    short, long_ = routes[1], routes[2]
    assert short.legs and long_.legs

    from dataclasses import replace

    slow = replace(long_, seconds=long_.seconds * 2.0)
    fast_score = capacity_score(short, vehicle.depth_cm)
    slow_score = capacity_score(slow, vehicle.depth_cm)
    assert slow_score == pytest.approx(fast_score * short.minutes / slow.minutes)
    assert slow_score < fast_score


def test_congestion_proxy_is_the_routers_own_slowdown() -> None:
    """No new traffic model: zero on a dry road, two thirds where phi is three."""
    assert congestion_proxy(0.0, 30.0) == pytest.approx(0.0)
    assert congestion_proxy(5.0, 30.0) == pytest.approx(0.0)
    assert congestion_proxy(30.0, 30.0) == pytest.approx(1.0 - 1.0 / 3.0)
    # Monotone: deeper water never returns capacity.
    values = [congestion_proxy(d, 30.0) for d in range(0, 40, 2)]
    assert values == sorted(values)


def test_capacity_score_is_zero_only_when_there_are_no_legs() -> None:
    graph = _fan()
    depths = _depths()
    routes, _ = _routes(graph, depths)
    assert capacity_score(routes[0], 30.0) > 0.0


def test_assignment_without_a_trip_id_is_the_fastest_road_and_not_a_spread() -> None:
    graph = _fan()
    depths = _depths()
    routes, vehicle = _routes(graph, depths)
    found = corridors(routes, depths, vehicle, trip_id=None)
    assert found[0].assigned and not any(c.assigned for c in found[1:])
    note = spreading_note(len(found), spread=True, trip_id=None)
    assert note is not None
    assert "not spread" in note


def test_the_disclosure_is_never_omitted_when_a_split_is_offered() -> None:
    """PRD 3.5 and SPEC.md rule 6: the split is a policy and the answer must say so."""
    note = spreading_note(3, spread=True, trip_id="trip-1")
    assert note is not None
    assert "policy" in note
    assert "not a measured traffic count" in note
    assert spreading_note(0, spread=True, trip_id="trip-1") is None
    assert spreading_note(3, spread=False, trip_id="trip-1") is None


def test_unit_interval_is_in_range_and_stable() -> None:
    values = [unit_interval(f"trip-{k}") for k in range(50)]
    assert all(0.0 <= v < 1.0 for v in values)
    assert unit_interval("trip-1") == unit_interval("trip-1")
    assert unit_interval("trip-1") != unit_interval("trip-2")


def test_assign_handles_the_degenerate_cases() -> None:
    assert assign([], "trip-1") == -1
    assert assign([1.0], "trip-1") == 0
    assert assign([0.5, 0.5], None) == 0

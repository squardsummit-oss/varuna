"""What the router must get right (SPEC.md 11.9 tests, task P8.2).

Built on a synthetic five-node network rather than Mumbai, so each property is testable in
isolation and the assertions are about the algorithm rather than about one city's geography.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from varuna_route.forecast import SegmentDepths
from varuna_route.graph import RoadGraph
from varuna_route.profiles import profile
from varuna_route.router import _phi, _search, plan

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2019, 7, 2, 8, 40, tzinfo=IST)


def _diamond() -> RoadGraph:
    """Two ways from 0 to 3: a short one through 1, a long one through 2.

    0 ->  1  -> 3      via "Short Marg", 60 s + 60 s
     \\-> 2  -> 3      via "Long Marg",  150 s + 150 s
    """
    arcs = [
        # (tail, head, segment, length_m, seconds, name, lanes)
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


def _depths(series: dict[str, list[float]], n_steps: int = 6) -> SegmentDepths:
    return SegmentDepths(
        run_id="TEST",
        times=tuple(T0 + timedelta(minutes=5 * k) for k in range(n_steps)),
        depth_cm={k: list(v) for k, v in series.items()},
        n_steps=n_steps,
        n_total=4,
        ensemble_n=1,
    )


def test_the_short_way_is_taken_when_it_is_dry() -> None:
    graph = _diamond()
    depths = _depths({})
    edges = _search(
        graph,
        depths,
        source=0,
        target=3,
        depart=T0,
        vehicle=profile("car"),
        avoid_water=True,
    )
    assert edges is not None
    assert [graph.edge_segment[e] for e in edges] == ["SHORT-A", "SHORT-B"]


def test_one_impassable_segment_forces_the_detour() -> None:
    """SPEC.md 11.9: with one impassable segment on the only short path, the route detours."""
    graph = _diamond()
    # 40 cm on the short way's second leg: over a car's 30 cm threshold, under a bus's 45.
    depths = _depths({"SHORT-B": [40.0] * 6})

    car = _search(
        graph, depths, source=0, target=3, depart=T0, vehicle=profile("car"), avoid_water=True
    )
    assert car is not None
    assert [graph.edge_segment[e] for e in car] == ["LONG-A", "LONG-B"]

    # A bus is not stopped by 40 cm, so it keeps the short way - slowed, not diverted.
    bus = _search(
        graph, depths, source=0, target=3, depart=T0, vehicle=profile("bus"), avoid_water=True
    )
    assert bus is not None
    assert [graph.edge_segment[e] for e in bus] == ["SHORT-A", "SHORT-B"]


def test_the_naive_route_ignores_the_water_the_safe_one_avoids() -> None:
    graph = _diamond()
    depths = _depths({"SHORT-B": [40.0] * 6})
    naive = _search(
        graph, depths, source=0, target=3, depart=T0, vehicle=profile("car"), avoid_water=False
    )
    assert naive is not None
    assert [graph.edge_segment[e] for e in naive] == ["SHORT-A", "SHORT-B"]


def test_the_cost_is_read_at_the_arrival_time_not_the_departure_time() -> None:
    """The point of time dependence: a street that floods later is open when you get there.

    ``SHORT-B`` is dry for the first two steps and 40 cm after. A car reaching it 60 s after
    departure is inside step 0 and passes; the same car departing 10 minutes later meets the
    water and is sent the long way round.
    """
    graph = _diamond()
    depths = _depths({"SHORT-B": [0.0, 0.0, 40.0, 40.0, 40.0, 40.0]})

    early = _search(
        graph, depths, source=0, target=3, depart=T0, vehicle=profile("car"), avoid_water=True
    )
    late = _search(
        graph,
        depths,
        source=0,
        target=3,
        depart=T0 + timedelta(minutes=10),
        vehicle=profile("car"),
        avoid_water=True,
    )
    assert early is not None
    assert late is not None
    assert [graph.edge_segment[e] for e in early] == ["SHORT-A", "SHORT-B"]
    assert [graph.edge_segment[e] for e in late] == ["LONG-A", "LONG-B"]


def test_fifo_holds_so_dijkstra_on_arrival_time_is_exact() -> None:
    """Leaving later never arrives earlier - the property time-dependent Dijkstra needs.

    Asserted rather than argued: it is what makes the search correct, and it would quietly stop
    holding if ``phi`` were ever allowed below one or waiting at a node became worthwhile.
    """
    from varuna_route.router import _build_route

    graph = _diamond()
    depths = _depths({"SHORT-B": [0.0, 5.0, 12.0, 25.0, 40.0, 40.0]})
    car = profile("car")

    arrivals: list[datetime] = []
    for minutes in range(0, 26, 5):
        depart = T0 + timedelta(minutes=minutes)
        edges = _search(
            graph, depths, source=0, target=3, depart=depart, vehicle=car, avoid_water=True
        )
        assert edges is not None
        route = _build_route(graph, depths, edges, depart=depart, vehicle=car, source=0)
        arrivals.append(route.arrive)

    assert arrivals == sorted(arrivals)


def test_phi_is_one_when_dry_and_three_at_the_threshold() -> None:
    car = profile("car")
    assert _phi(0.0, car.depth_cm) == pytest.approx(1.0)
    assert _phi(5.0, car.depth_cm) == pytest.approx(1.0)
    assert _phi(car.depth_cm, car.depth_cm) == pytest.approx(3.0)
    assert _phi(200.0, car.depth_cm) == pytest.approx(3.0)
    # Monotone in between, or the FIFO argument above does not hold.
    values = [_phi(d, car.depth_cm) for d in range(0, 40, 2)]
    assert values == sorted(values)


def test_an_unknown_profile_names_the_valid_ones() -> None:
    with pytest.raises(KeyError, match="ambulance"):
        profile("hovercraft")


def test_plan_refuses_a_trip_that_starts_where_it_ends() -> None:
    """Mumbai's own graph, so the fixture is the real city; the assertion is about the guard."""
    pytest.importorskip("geopandas")
    from pathlib import Path

    from varuna_schemas.paths import city_dir

    if not (Path(city_dir("mumbai")) / "segments.parquet").is_file():
        pytest.skip("Mumbai is not built; run `make city CITY=mumbai`.")
    try:
        result = plan((72.841, 19.003), (72.841, 19.003), vehicle="ambulance")
    except FileNotFoundError:
        pytest.skip("No baked run to route against.")
    assert result.varuna is None
    assert any("same junction" in note for note in result.notes)


def test_isochrones_only_shrink_as_the_water_rises() -> None:
    """SPEC.md 11.9: isochrone areas are monotone non-increasing as depth rises.

    On the diamond, with the dry sweep as the ceiling: water can close an edge or slow it, never
    open one, so the set of junctions reached within a band can only lose members.
    """
    from varuna_route.reach import _sweep

    graph = _diamond()
    car = profile("car")
    cap = 15 * 60.0

    dry = _sweep(graph, None, source=0, at=T0, vehicle=car, limit_s=cap)
    previous = int((dry <= cap).sum())
    for depth in (0.0, 10.0, 20.0, 29.0, 40.0):
        depths = _depths({"SHORT-A": [depth] * 6, "SHORT-B": [depth] * 6})
        wet = _sweep(graph, depths, source=0, at=T0, vehicle=car, limit_s=cap)
        reached = int((wet <= cap).sum())
        assert reached <= previous
        previous = reached


def test_collapse_fires_only_below_forty_percent_of_dry() -> None:
    from varuna_route.reach import CUTS, collapse

    last = CUTS[-1]
    assert collapse({last: 39}, {last: 100}) is True
    assert collapse({last: 41}, {last: 100}) is False
    # No baseline is not a collapse; it is a facility nothing can reach even when dry.
    assert collapse({last: 0}, {last: 0}) is False

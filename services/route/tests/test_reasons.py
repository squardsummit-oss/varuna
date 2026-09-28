"""Reasons are structured data, and a reason without its number is not emitted (task D-08).

UI_SPEC 4 gives four kinds and one rule: a reason whose number is missing is dropped rather
than softened. That rule is enforced here as well as in the frontend, so a screen is never
handed a reason it would have to throw away.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from varuna_route.forecast import SegmentDepths
from varuna_route.graph import RoadGraph
from varuna_route.ops_overlay import Closure, OpsOverlay
from varuna_route.profiles import profile
from varuna_route.reasons import (
    avoided_reason,
    build_reasons,
    closure_reason,
    design_reason,
    timing_reason,
)
from varuna_route.router import Avoided, _build_route, _search

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2019, 7, 2, 8, 40, tzinfo=IST)

REASON_PROSE = ("the ", "this road", "because", "avoids", "deeper than")
"""Words a sentence would carry. No value in a reason may read like one (TECH_SPEC 3.2)."""


def _graph() -> RoadGraph:
    arcs = [
        (0, 1, "SHORT-A", 500.0, 60.0, "Short Marg", 2.0),
        (1, 2, "SHORT-B", 500.0, 60.0, "Dr Ambedkar Road", 2.0),
    ]
    tails = np.array([a[0] for a in arcs], dtype=np.int64)
    indptr = np.zeros(4, dtype=np.int64)
    np.add.at(indptr, tails + 1, 1)
    np.cumsum(indptr, out=indptr)
    return RoadGraph(
        node_ids=np.arange(3, dtype=np.int64),
        lon=np.array([72.84, 72.845, 72.85]),
        lat=np.array([19.00, 19.005, 19.01]),
        indptr=indptr,
        head=np.array([a[1] for a in arcs], dtype=np.int64),
        edge_segment=[a[2] for a in arcs],
        edge_length_m=np.array([a[3] for a in arcs]),
        edge_time_s=np.array([a[4] for a in arcs]),
        edge_name=[a[5] for a in arcs],
        edge_tail=tails,
        edge_lanes=np.array([a[6] for a in arcs], dtype=np.float64),
    )


def _depths(series: dict[str, list[float]], rain: tuple[float, ...] = ()) -> SegmentDepths:
    return SegmentDepths(
        run_id="TEST",
        times=tuple(T0 + timedelta(minutes=5 * k) for k in range(6)),
        depth_cm={k: list(v) for k, v in series.items()},
        n_steps=6,
        n_total=2,
        ensemble_n=20,
        rain_aoi_mm_h=rain,
    )


def test_an_avoided_reason_carries_every_number_the_sentence_needs() -> None:
    entry = Avoided(
        segment_id="SHORT-B",
        name="Dr Ambedkar Road",
        depth_cm=47.0,
        probability=0.82,
        at=T0 + timedelta(minutes=12),
    )
    reason = avoided_reason(entry, 30.0)
    assert reason["kind"] == "avoided"
    assert reason["depth_cm"] == 47.0
    assert reason["threshold_cm"] == 30.0
    assert reason["probability"] == 0.82
    assert reason["at"].endswith("+05:30")
    assert set(reason) == {
        "kind",
        "segment_id",
        "name",
        "depth_cm",
        "threshold_cm",
        "at",
        "probability",
    }


def test_a_design_reason_without_a_peak_rain_is_not_emitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half the comparison says nothing, so nothing is said."""
    from varuna_route import reasons

    monkeypatch.setattr(reasons, "design_intensity_by_segment", lambda city: {"SHORT-B": 25.0})
    assert design_reason("SHORT-B", "Dr Ambedkar Road", "mumbai", _depths({})) is None

    with_rain = _depths({}, rain=(31.7, 61.2, 8.0))
    reason = design_reason("SHORT-B", "Dr Ambedkar Road", "mumbai", with_rain)
    assert reason is not None
    assert reason["design_intensity_mm_h"] == 25.0
    assert reason["forecast_peak_mm_h"] == 61.2


def test_a_design_reason_for_a_street_with_no_inferred_drain_is_not_emitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from varuna_route import reasons

    monkeypatch.setattr(reasons, "design_intensity_by_segment", lambda city: {})
    assert design_reason("SHORT-B", "X", "mumbai", _depths({}, rain=(61.2,))) is None


def test_a_timing_reason_names_when_the_road_stops_being_dry() -> None:
    graph = _graph()
    depths = _depths({"SHORT-B": [1.0, 2.0, 4.0, 20.0, 47.0, 47.0]})
    car = profile("car")
    edges = _search(graph, depths, source=0, target=2, depart=T0, vehicle=car, avoid_water=False)
    assert edges is not None
    route = _build_route(graph, depths, edges, depart=T0, vehicle=car, source=0)

    reason = timing_reason(route, depths, car)
    assert reason is not None
    assert reason["kind"] == "timing"
    assert reason["segment_id"] == "SHORT-B"
    assert reason["depth_cm"] == 47.0
    # Dry (<= 5 cm) through step 2, which is 08:50; the peak arrives at step 4, 09:00.
    assert reason["dry_until"] == (T0 + timedelta(minutes=10)).isoformat()
    assert reason["at"] == (T0 + timedelta(minutes=20)).isoformat()


def test_a_route_that_never_gets_wet_has_no_timing_reason() -> None:
    graph = _graph()
    depths = _depths({})
    car = profile("car")
    edges = _search(graph, depths, source=0, target=2, depart=T0, vehicle=car, avoid_water=True)
    assert edges is not None
    route = _build_route(graph, depths, edges, depart=T0, vehicle=car, source=0)
    assert timing_reason(route, depths, car) is None


def test_a_road_wet_from_the_first_step_has_no_until_to_name() -> None:
    graph = _graph()
    depths = _depths({"SHORT-B": [47.0] * 6})
    car = profile("car")
    edges = _search(graph, depths, source=0, target=2, depart=T0, vehicle=car, avoid_water=False)
    assert edges is not None
    route = _build_route(graph, depths, edges, depart=T0, vehicle=car, source=0)
    assert timing_reason(route, depths, car) is None


def test_a_closure_reason_uses_the_officers_own_words() -> None:
    overlay = OpsOverlay(
        city="mumbai",
        at=T0,
        closures={
            "SHORT-B": Closure(
                segment_id="SHORT-B",
                reason="Water main work",
                user="ward-officer",
                ts=T0 - timedelta(minutes=28),
            )
        },
    )
    reason = closure_reason("SHORT-B", "Dr Ambedkar Road", overlay)
    assert reason is not None
    assert reason["reason"] == "Water main work"
    assert reason["user"] == "ward-officer"
    assert reason["at"] == (T0 - timedelta(minutes=28)).isoformat()
    assert reason["until"] is None
    assert closure_reason("SHORT-A", "Short Marg", overlay) is None


def test_a_closure_with_no_stated_reason_is_dropped() -> None:
    overlay = OpsOverlay(
        city="mumbai",
        at=T0,
        closures={"SHORT-B": Closure("SHORT-B", "", "ward-officer", T0)},
    )
    assert closure_reason("SHORT-B", "Dr Ambedkar Road", overlay) is None


def test_no_reason_value_is_a_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    """The contract with the frontend: structured data only, no prose from Python."""
    from varuna_route import reasons

    monkeypatch.setattr(reasons, "design_intensity_by_segment", lambda city: {"SHORT-B": 25.0})
    graph = _graph()
    depths = _depths({"SHORT-B": [1.0, 2.0, 4.0, 20.0, 47.0, 47.0]}, rain=(61.2, 8.0))
    car = profile("car")
    edges = _search(graph, depths, source=0, target=2, depart=T0, vehicle=car, avoid_water=False)
    assert edges is not None
    route = _build_route(graph, depths, edges, depart=T0, vehicle=car, source=0)
    avoided = [
        Avoided("SHORT-B", "Dr Ambedkar Road", 47.0, 0.82, T0 + timedelta(minutes=12)),
    ]

    built = build_reasons(avoided=avoided, route=route, depths=depths, vehicle=car, city="mumbai")
    kinds = [r["kind"] for r in built]
    assert kinds == ["avoided", "design", "timing"]

    for reason in built:
        for key, value in reason.items():
            if key in {"name", "reason"} or not isinstance(value, str):
                continue
            lowered = value.lower()
            assert not any(word in lowered for word in REASON_PROSE), (
                f"{key}={value!r} reads like a sentence; the frontend writes those"
            )


def test_at_most_three_avoided_streets_are_named() -> None:
    car = profile("car")
    avoided = [
        Avoided(f"S{k}", f"Road {k}", 60.0 - k, 0.9, T0 + timedelta(minutes=k)) for k in range(8)
    ]
    built = build_reasons(
        avoided=avoided, route=None, depths=_depths({}), vehicle=car, city="mumbai"
    )
    assert sum(1 for r in built if r["kind"] == "avoided") == 3

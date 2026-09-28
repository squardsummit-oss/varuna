"""Spreading traffic across the safe roads, as a stated policy (task D-08, TECH_SPEC 3.3).

If every driver is handed the same safe corridor, the safe corridor becomes the jam. So a route
request comes back with up to three corridors - the VARUNA route and its alternates, each one
genuinely under the vehicle's depth threshold - and the request is assigned to one of them,
weighted by spare road capacity.

**What is real and what is policy.** The corridors are real: three different roads, each checked
against the run's own exceedance. The *split* is not a traffic model. There are no live counts in
this prototype, so demand is unmeasured and the share is a policy this module states out loud in
the response's ``notes`` (SPEC.md rule 6). No screen may call it a modelled traffic volume.

**The capacity score**::

    capacity_score(c) = min over edges of lanes * (1 - congestion_proxy(depth)) / minutes(c)

``congestion_proxy`` is the router's own ``phi(h)`` slowdown and nothing new: a road whose
traversal time is multiplied by ``phi`` passes ``1 / phi`` of the vehicles it would pass dry, so
the fraction of capacity water has taken is ``1 - 1 / phi`` - zero on a dry road, two thirds at
the depth where the profile stops. No traffic model is invented anywhere in this file.

TECH_SPEC 3.3 first wrote that as a **sum** over edges, and the sum was wrong in effect: it
rewards length, so on the 08:40 demo run a Worli-to-Chembur car trip gave its largest share,
0.4038, to the 23.9-minute corridor while the equally safe 13.5-minute one took 0.2458 - most
drivers sent the slowest way for no gain in safety. The minimum is used instead, because a road
is as wide as its narrowest point, and it is divided by the corridor's travel time, because a
corridor that holds each vehicle twice as long absorbs half the flow at the same width. The spec
is corrected to match.

One thing to know about the inputs: lane counts are OSM's where OSM has them and a class default
on the 90.8 % of Mumbai segments where it does not
(:data:`varuna_route.graph.CLASS_LANES`).

**Assignment is deterministic**: ``sha256(trip_id)`` picks a point in ``[0, 1)`` and the
cumulative shares pick the corridor. The same trip id always lands on the same road - a reader
who reloads is not sent somewhere else - while ids drawn across a population land on each
corridor in proportion to its share.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from varuna_route.router import _phi

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    from varuna_route.forecast import SegmentDepths
    from varuna_route.profiles import Profile
    from varuna_route.router import Route

__all__ = [
    "LABELS",
    "MAX_CORRIDORS",
    "Corridor",
    "assign",
    "capacity_score",
    "congestion_proxy",
    "corridors",
    "spreading_note",
    "unit_interval",
]

LABELS = ("A", "B", "C")
MAX_CORRIDORS = len(LABELS)


@dataclass(frozen=True, slots=True)
class Corridor:
    """One safe road a request may be sent down, with the share of traffic policy gives it."""

    id: str
    """Stable across requests: the first 8 hex of a digest of the corridor's segment sequence,
    so the same road keeps the same id between two calls and a client can remember a choice."""

    label: str
    route: Route
    share: float
    """Fraction of requests this corridor is meant to take. A policy, not a measurement."""

    assigned: bool
    capacity_score: float
    max_probability: float
    """Highest ``P(depth > threshold)`` met on this corridor, at the time it is reached."""


_MIN_MINUTES = 1.0 / 60.0
"""A second, as the floor on a corridor's travel time, so the capacity is never divided by nothing."""


def congestion_proxy(depth_cm: float, threshold_cm: float) -> float:
    """Fraction of a road's capacity the water has taken: ``1 - 1 / phi(h)``.

    Zero on a dry road; ``1 - 1/3`` at the depth that stops the profile, because that is where
    the router already says the traversal takes three times as long.
    """
    return 1.0 - 1.0 / _phi(depth_cm, threshold_cm)


def capacity_score(route: Route, threshold_cm: float) -> float:
    """How much traffic a corridor can absorb: its bottleneck lanes, per minute it holds a car.

    **Corrected 2026-09-19, measured.** TECH_SPEC 3.3 first said "sum over edges of lanes times
    what the water has left of them", and a sum over edges rewards length: on the 08:40 demo run
    a Worli-to-Chembur car trip put its largest share, 0.4038, on the 23.9-minute corridor while
    the equally safe 13.5-minute one took 0.2458. That is a policy that sends most drivers the
    slowest way for no gain in safety, which is worse advice than not spreading at all.

    A road is as wide as its narrowest point, so the capacity is the **minimum** over the legs
    rather than the sum - that alone makes the score length-independent. It is then divided by
    the corridor's own travel time, because a corridor that holds each vehicle twice as long
    absorbs half the flow at the same width. Both factors are quantities the run and the graph
    already carry; no traffic model is invented, and the split remains a stated policy (ADR-0060).
    """
    if not route.legs:
        return 0.0
    bottleneck = min(
        leg.lanes * (1.0 - congestion_proxy(leg.depth_cm, threshold_cm)) for leg in route.legs
    )
    return max(bottleneck, 0.0) / max(route.minutes, _MIN_MINUTES)


def unit_interval(trip_id: str) -> float:
    """A point in ``[0, 1)`` from a trip id, by SHA-256.

    Deterministic across processes and platforms (unlike ``hash()``), and near enough uniform
    that a population of ids lands on each corridor in proportion to its share.
    """
    digest = hashlib.sha256(trip_id.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) / float(1 << 64)


def assign(shares: Sequence[float], trip_id: str | None) -> int:
    """Index of the corridor a trip is assigned to.

    Without a trip id there is nothing to be deterministic about, so the first corridor is taken
    and the caller says in its notes that the request was not spread. Inventing an id here would
    make two identical requests land differently and look like a bug.
    """
    if not shares:
        return -1
    if trip_id is None or not str(trip_id).strip():
        return 0
    point = unit_interval(str(trip_id))
    cumulative = 0.0
    for index, share in enumerate(shares):
        cumulative += share
        if point < cumulative:
            return index
    return len(shares) - 1


def _corridor_id(route: Route) -> str:
    key = "|".join(leg.segment_id for leg in route.legs)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def _max_probability(route: Route, depths: SegmentDepths, vehicle: Profile) -> float:
    worst = 0.0
    for leg in route.legs:
        step = depths.step_at(leg.arrive)
        worst = max(worst, depths.exceedance(leg.segment_id, vehicle.depth_cm, step))
    return worst


def corridors(
    routes: Sequence[Route],
    depths: SegmentDepths,
    vehicle: Profile,
    *,
    trip_id: str | None = None,
    closed_segment_ids: frozenset[str] | None = None,
) -> list[Corridor]:
    """Score, share and assign the safe corridors for one request.

    Args:
        routes: the VARUNA route first, then its alternates, in the order the router found them.
        depths: the run being routed against.
        vehicle: the profile whose threshold and risk tolerance decide what counts as safe.
        trip_id: the client's stable id for this trip; ``None`` means no spreading.
        closed_segment_ids: segments an authority has closed, which disqualify a corridor
            whatever the forecast says.

    Returns:
        Up to three corridors, worst-first by nothing - they keep the router's own order, so "A"
        is always the fastest safe road and a reader who ignores the assignment is not punished.
        Empty when no corridor clears the profile's threshold.
    """
    closed = closed_segment_ids or frozenset()
    kept: list[tuple[Route, float, float]] = []
    seen: set[str] = set()

    for route in routes:
        if not route.legs:
            continue
        identity = _corridor_id(route)
        if identity in seen:
            continue
        if any(leg.segment_id in closed for leg in route.legs):
            continue
        # Step 3 of TECH_SPEC 3.3: only corridors the profile actually accepts. The search
        # already refuses these edges, so this is a guard rather than a filter - and it is the
        # line that would catch a route built by some future path that did not check.
        worst = _max_probability(route, depths, vehicle)
        if worst >= vehicle.risk_tolerance:
            continue
        seen.add(identity)
        kept.append((route, capacity_score(route, vehicle.depth_cm), worst))
        if len(kept) == MAX_CORRIDORS:
            break

    if not kept:
        return []

    total = sum(score for _, score, _ in kept)
    if total <= 0.0:
        # Every corridor scored zero (every edge at the threshold on every lane). An equal split
        # is the only honest answer left; it is still a policy and still says so.
        shares = [1.0 / len(kept)] * len(kept)
    else:
        shares = [score / total for _, score, _ in kept]

    chosen = assign(shares, trip_id)
    return [
        Corridor(
            id=_corridor_id(route),
            label=LABELS[index],
            route=route,
            share=shares[index],
            assigned=index == chosen,
            capacity_score=score,
            max_probability=worst,
        )
        for index, (route, score, worst) in enumerate(kept)
    ]


def spreading_note(n_corridors: int, *, spread: bool, trip_id: str | None) -> str | None:
    """The disclosure that must travel with any corridor split (PRD 3.5, SPEC.md rule 6)."""
    if not spread or n_corridors == 0:
        return None
    if n_corridors == 1:
        return (
            "Only one road to this destination stays under the vehicle's depth threshold on this "
            "run, so there is nothing to spread traffic across."
        )
    if trip_id is None or not str(trip_id).strip():
        return (
            f"{n_corridors} safe roads were found and the share beside each is a policy, not a "
            "measured traffic count: demand is not observed anywhere in this prototype. This "
            "request carried no trip id, so it was not spread - it was given the fastest road."
        )
    return (
        f"Traffic is spread across {n_corridors} safe roads so the safe road does not become the "
        "next jam. The share beside each is a policy, not a measured traffic count: demand is "
        "not observed anywhere in this prototype, and every corridor is shown so the split can "
        "be refused."
    )

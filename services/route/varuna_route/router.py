"""Time-dependent routing around water (SPEC.md 11.9, Appendix A; tasks P8.2, P8.5).

**Why the time dependence matters.** A naive router asks "is this street flooded?" and gets an
answer for now. The question an ambulance leaving KEM at 08:40 actually has is "will Hindmata be
flooded *when I get there*, twelve minutes from now" - and on the 2 July storm those are different
answers. So the cost of an edge is evaluated at the arrival time the search has reached:

    c_e(tau, v) = t_e * phi(h_e(tau))    if P(h_e(tau) > theta_v) < p_max
                = infinity                otherwise

with ``phi(h) = 1`` below 5 cm rising linearly to 3x at the profile's threshold - water slows a
vehicle long before it stops it.

**Why plain Dijkstra is still correct here.** Time-dependent shortest paths need the FIFO (no
overtaking) property: leaving later must never arrive earlier. It holds because ``phi >= 1`` and
the edge is either passable or infinite, so waiting at a node can never help - and where it holds,
Dijkstra keyed on arrival time is exact. The test suite asserts FIFO on a synthetic network rather
than trusting the argument.

**Alternates** are the same search with the chosen edges penalised threefold, which is the
standard cheap way to get a genuinely different road rather than a detour of one block.

**The probability is the run's** (task D-01). ``P(h > theta_v)`` used to be a threshold
comparison on the median depth, so it was 1 or 0 and ``risk_tolerance`` was a placebo. It now
comes from :meth:`varuna_route.forecast.SegmentDepths.exceedance`, which reads the 20-member
``p_gt`` series the cycle writes and falls back to the comparison only for a run that has no
spread. Everything else about the search is unchanged.

**Closures beat the forecast** (task D-06). A street an authority has closed is impassable
whatever the water is doing, so the VARUNA search skips it and the reason travels with the
answer. The naive route does **not** honour closures, for the same reason it does not honour
water: it is the comparison - what a navigation app that knows neither would do - and a closure
the naive way walks into is exactly the thing the explanation exists to name.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from varuna_route.forecast import DRY_CM, SegmentDepths, load_depths
from varuna_route.graph import RoadGraph, load_graph
from varuna_route.profiles import Profile, hazard_unsafe, profile

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable

    from varuna_route.ops_overlay import OpsOverlay

log = structlog.get_logger("varuna.route.router")

__all__ = [
    "MAX_SLOWDOWN",
    "Route",
    "RouteResult",
    "plan",
]

MAX_SLOWDOWN = 3.0
"""Traversal time at the profile's threshold depth, as a multiple of free flow.

Three, from SPEC.md 11.9. A car in 25 cm of water is not stopped, it is crawling behind
everybody else who is also crawling; three times is what the traffic baselines in the replay
bundle show at that depth."""

ALTERNATE_PENALTY = 3.0
"""Cost multiplier on edges the previous route used, when searching for an alternate."""

MAX_ALTERNATES = 2


def _phi(depth_cm: float, threshold_cm: float) -> float:
    """Slowdown factor for water on an edge: 1 when dry, :data:`MAX_SLOWDOWN` at the threshold."""
    if depth_cm <= DRY_CM:
        return 1.0
    span = max(threshold_cm - DRY_CM, 1.0)
    return 1.0 + (MAX_SLOWDOWN - 1.0) * min((depth_cm - DRY_CM) / span, 1.0)


def _exceedance(depths: SegmentDepths, segment_id: str, threshold_cm: float, step: int) -> float:
    """``P(depth > threshold)`` for one segment at one step, from the run (task D-01)."""
    return depths.exceedance(segment_id, threshold_cm, step)


def _blocking(depths: SegmentDepths, segment_id: str, vehicle: Profile, step: int) -> float:
    """The probability a profile is refused a segment at a step.

    The run's own exceedance of the profile's threshold, except that a pedestrian is also
    refused - with certainty - water whose median depth times flow speed reaches 0.5 m^2/s
    (:func:`varuna_route.profiles.hazard_unsafe`, Appendix A). Where the run carries no speed that
    half cannot fire, so for every run baked so far this is exactly :func:`_exceedance`.
    """
    p = depths.exceedance(segment_id, vehicle.depth_cm, step)
    if vehicle.hazard_rule and p < 1.0:
        velocity = depths.velocity_at(segment_id, step)
        if velocity is not None and hazard_unsafe(depths.depth_at(segment_id, step), velocity):
            return 1.0
    return p


@dataclass(frozen=True, slots=True)
class Leg:
    """One edge of a route, with the water on it at the moment the vehicle gets there."""

    segment_id: str
    name: str
    length_m: float
    seconds: float
    depth_cm: float
    arrive: datetime
    probability: float = 0.0
    """``P(depth > this profile's threshold)`` on this edge at :attr:`arrive` - or 1.0 where a
    pedestrian meets the hazard product (:func:`_blocking`)."""

    lanes: float = 1.0
    """Lanes in this direction, for the corridor capacity score (:mod:`varuna_route.spread`)."""


@dataclass(frozen=True, slots=True)
class Avoided:
    """A street the route did not take because it is predicted impassable."""

    segment_id: str
    name: str
    depth_cm: float
    probability: float
    at: datetime
    closed_reason: str | None = None
    """Set when the street was refused because an authority closed it, not because of water."""

    path: tuple[tuple[float, float], ...] = ()
    """The street's own geometry, so the map can draw what the detour went around.

    Without it the console can list "Dr Ambedkar Marg, 71 cm" and draw a route bending away from
    nothing in particular; with it the red segment and the bend are visibly the same place."""


@dataclass(frozen=True, slots=True)
class Route:
    """One path from origin to destination."""

    legs: tuple[Leg, ...]
    seconds: float
    distance_m: float
    max_depth_cm: float
    depart: datetime
    arrive: datetime
    path: tuple[tuple[float, float], ...]
    """Node coordinates along the route, for the map."""

    safe_until: datetime | None
    """Last departure time for which this exact path is still passable, or None if it is not."""

    @property
    def minutes(self) -> float:
        return self.seconds / 60.0


@dataclass
class RouteResult:
    """The answer to one routing request: what a naive router does, and what VARUNA does."""

    run_id: str
    profile: str
    depart: datetime
    naive: Route | None
    varuna: Route | None
    alternates: list[Route] = field(default_factory=list)
    avoided: list[Avoided] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ms: float = 0.0
    corridors: list[Any] = field(default_factory=list)
    """:class:`varuna_route.spread.Corridor` objects; typed loosely to keep the import lazy and
    the module cycle (router -> spread -> router) out of import time."""

    reasons: list[dict[str, Any]] = field(default_factory=list)
    """Structured reasons, never prose (:mod:`varuna_route.reasons`, TECH_SPEC 3.2)."""

    trip_id: str | None = None


def _search(
    graph: RoadGraph,
    depths: SegmentDepths,
    *,
    source: int,
    target: int,
    depart: datetime,
    vehicle: Profile,
    avoid_water: bool,
    penalised: set[int] | None = None,
    record_blocked: dict[int, tuple[float, float, datetime]] | None = None,
    closed: frozenset[str] = frozenset(),
) -> list[int] | None:
    """Time-dependent Dijkstra from ``source``, returning the out-edge indices of the path.

    ``avoid_water`` off is the naive router: shortest by dry-weather time, water ignored. That is
    the comparison the screen makes, and it is what a navigation app does today.
    """
    n = graph.n_nodes
    best = [float("inf")] * n
    best[source] = 0.0
    came: list[int] = [-1] * n
    heap: list[tuple[float, int]] = [(0.0, source)]
    speed = max(vehicle.speed_scale, 0.05)

    # Hoisted out of the per-edge path. The search relaxes about twenty thousand edges a route
    # and pops as many nodes, so anything resolved inside the loop is paid twenty thousand
    # times: the depth table, the threshold's exceedance table, the profile's own numbers, and
    # the departure as an offset the step arithmetic can use without building a timedelta.
    # Every lookup below reproduces SegmentDepths.step_at, .depth_at and .exceedance exactly,
    # and tests pin them against those methods rather than against a remembered answer.
    depth_by_segment = depths.depth_cm
    p_by_segment = depths.p_gt.get(float(vehicle.depth_cm))
    threshold = vehicle.depth_cm
    tolerance = vehicle.risk_tolerance
    depart_offset_s = depths.depart_offset_s(depart)
    # The pedestrian's velocity half of the hazard rule, only when there is a speed to apply it
    # to. None for every other profile and for every run baked so far, so the loop below is the
    # loop it was and the answers are identical.
    velocity_by_segment = depths.velocity_ms if vehicle.hazard_rule and depths.velocity_ms else None

    while heap:
        elapsed, node = heapq.heappop(heap)
        if elapsed > best[node] + 1e-9:
            continue
        if node == target:
            break
        step = depths.step_after(depart_offset_s, elapsed)

        for e in range(int(graph.indptr[node]), int(graph.indptr[node + 1])):
            segment_id = graph.edge_segment[e]
            free = float(graph.edge_time_s[e]) / speed
            cost = free

            if avoid_water:
                if segment_id in closed:
                    continue
                series = depth_by_segment.get(segment_id)
                if not series:
                    depth = 0.0
                else:
                    depth = series[step] if step < len(series) else series[-1]
                if p_by_segment is None:
                    p = 1.0 if depth > threshold else 0.0
                else:
                    ps = p_by_segment.get(segment_id)
                    if ps is not None:
                        p = ps[step] if step < len(ps) else ps[-1]
                    elif series is None:
                        p = 0.0
                    else:
                        p = 1.0 if depth > threshold else 0.0
                if velocity_by_segment is not None and p < tolerance:
                    vs = velocity_by_segment.get(segment_id)
                    if vs:
                        v = vs[step] if step < len(vs) else vs[-1]
                        if hazard_unsafe(depth, v):
                            p = 1.0
                if p >= tolerance:
                    if record_blocked is not None and e not in record_blocked:
                        record_blocked[e] = (depth, p, depart + timedelta(seconds=elapsed))
                    continue
                cost = free * _phi(depth, threshold)

            if penalised and e in penalised:
                cost *= ALTERNATE_PENALTY

            nxt = int(graph.head[e])
            candidate = elapsed + cost
            if candidate < best[nxt] - 1e-9:
                best[nxt] = candidate
                came[nxt] = e
                heapq.heappush(heap, (candidate, nxt))

    if best[target] == float("inf"):
        return None

    # Walk the predecessor edges back to the source.
    edges: list[int] = []
    node = target
    while node != source:
        e = came[node]
        if e < 0:
            return None
        edges.append(e)
        node = int(graph.edge_tail[e])
    edges.reverse()
    return edges


def _build_route(
    graph: RoadGraph,
    depths: SegmentDepths,
    edges: Iterable[int],
    *,
    depart: datetime,
    vehicle: Profile,
    source: int,
) -> Route:
    legs: list[Leg] = []
    coords: list[tuple[float, float]] = [(float(graph.lon[source]), float(graph.lat[source]))]
    elapsed = 0.0
    distance = 0.0
    speed = max(vehicle.speed_scale, 0.05)
    worst = 0.0
    # Times and lengths come out of numpy arrays; float() at the boundary keeps numpy scalars out
    # of the dataclass and therefore out of the JSON encoder, which cannot serialise them.

    for e in edges:
        arrive_at_edge = depart + timedelta(seconds=elapsed)
        step = depths.step_at(arrive_at_edge)
        segment_id = graph.edge_segment[e]
        depth = depths.depth_at(segment_id, step)
        seconds = float(graph.edge_time_s[e] / speed) * _phi(depth, vehicle.depth_cm)
        elapsed += seconds
        distance += float(graph.edge_length_m[e])
        worst = max(worst, depth)
        head = int(graph.head[e])
        coords.append((float(graph.lon[head]), float(graph.lat[head])))
        legs.append(
            Leg(
                segment_id=segment_id,
                name=graph.edge_name[e],
                length_m=float(graph.edge_length_m[e]),
                seconds=seconds,
                depth_cm=depth,
                arrive=depart + timedelta(seconds=elapsed),
                probability=_blocking(depths, segment_id, vehicle, step),
                lanes=float(graph.edge_lanes[e]),
            )
        )

    return Route(
        legs=tuple(legs),
        seconds=elapsed,
        distance_m=distance,
        max_depth_cm=worst,
        depart=depart,
        arrive=depart + timedelta(seconds=elapsed),
        path=tuple(coords),
        safe_until=_safe_until(graph, depths, legs, vehicle, depart),
    )


def _safe_until(
    graph: RoadGraph,
    depths: SegmentDepths,
    legs: list[Leg],
    vehicle: Profile,
    depart: datetime,
) -> datetime | None:
    """The last departure time at which every street on this path is still passable.

    Answers the dispatcher's real question - "how long is this route good for?" - by walking the
    forecast forward and stopping at the first step where any segment on the path exceeds the
    profile's threshold. Each segment is checked at the time the vehicle would reach *it*, not at
    the departure time, so a route whose last mile floods late stays good longer than one whose
    first mile floods soon.
    """
    del graph
    if not legs:
        return None
    offsets = [(leg.segment_id, (leg.arrive - depart).total_seconds()) for leg in legs]
    last_good: datetime | None = None
    for step in range(depths.n_steps):
        candidate = depths.time_of(step)
        if candidate < depart:
            continue
        blocked = False
        for segment_id, offset in offsets:
            at = depths.step_at(candidate + timedelta(seconds=offset))
            depth = depths.depth_at(segment_id, at)
            if depth > vehicle.depth_cm or (
                vehicle.hazard_rule and hazard_unsafe(depth, depths.velocity_at(segment_id, at))
            ):
                blocked = True
                break
        if blocked:
            break
        last_good = candidate
    return last_good


def plan(
    origin: tuple[float, float],
    destination: tuple[float, float],
    *,
    depart_at: datetime | None = None,
    vehicle: str = "ambulance",
    risk_tolerance: float | None = None,
    city: str = "mumbai",
    run_id: str | None = None,
    spread: bool = True,
    trip_id: str | None = None,
    explain: bool = True,
    overlay: OpsOverlay | None = None,
) -> RouteResult:
    """Route from one point to another, naively and around the forecast water.

    Args:
        origin: ``(lon, lat)``.
        destination: ``(lon, lat)``.
        depart_at: when the vehicle leaves; defaults to the run's first forecast step.
        vehicle: a key of :data:`varuna_route.profiles.PROFILES`.
        risk_tolerance: override the profile's default acceptance of exceedance probability.
        city: which built city to route on.
        run_id: which run's forecast to route against; defaults to the newest baked one.
        spread: return up to three safe corridors and an assignment (task D-08).
        trip_id: the client's stable id for this trip, so the assignment survives a reload.
        explain: build the structured reasons (:mod:`varuna_route.reasons`).
        overlay: authority closures to honour; ``None`` reads the city's own ops log at
            ``depart_at``. Tests pass one explicitly.
    """
    from time import perf_counter

    from varuna_route import ops_overlay as ops
    from varuna_route.reasons import build_reasons
    from varuna_route.spread import corridors as build_corridors
    from varuna_route.spread import spreading_note

    started = perf_counter()
    graph = load_graph(city)
    depths = load_depths(run_id, city)
    base = profile(vehicle)
    if risk_tolerance is not None:
        base = Profile(
            base.key,
            base.label,
            base.depth_cm,
            float(risk_tolerance),
            base.speed_scale,
            base.hazard_rule,
        )

    depart = depart_at or depths.valid_ts
    source = graph.nearest_node(*origin)
    target = graph.nearest_node(*destination)

    notes: list[str] = []
    if depths.has_exceedance:
        notes.append(
            f"Probabilities are this run's own, across {depths.ensemble_n} members; the risk "
            f"tolerance applied is {base.risk_tolerance:.2f}."
        )
    else:
        notes.append(
            f"This run carries no per-member exceedance ({depths.ensemble_n} member(s), and no "
            "p_gt in its segment forecast), so a street is either predicted impassable or it is "
            "not and the risk tolerance has nothing to weigh."
        )

    if base.hazard_rule:
        notes.append(hazard_note(depths))

    if overlay is None:
        overlay = ops.active(city, at=depart)
    closed = overlay.closed_segment_ids
    if closed:
        notes.append(
            f"{len(closed)} street(s) closed by an authority are treated as impassable whatever "
            "the forecast says. Closures are an append-only overlay read at request time; no "
            "forecast product was changed."
        )

    if source == target:
        notes.append("Origin and destination snap to the same junction; there is nothing to route.")
        return RouteResult(
            run_id=depths.run_id,
            profile=base.key,
            depart=depart,
            naive=None,
            varuna=None,
            notes=notes,
            ms=(perf_counter() - started) * 1000.0,
        )

    naive_edges = _search(
        graph, depths, source=source, target=target, depart=depart, vehicle=base, avoid_water=False
    )
    blocked: dict[int, tuple[float, float, datetime]] = {}
    varuna_edges = _search(
        graph,
        depths,
        source=source,
        target=target,
        depart=depart,
        vehicle=base,
        avoid_water=True,
        record_blocked=blocked,
        closed=closed,
    )

    naive = (
        _build_route(graph, depths, naive_edges, depart=depart, vehicle=base, source=source)
        if naive_edges
        else None
    )
    varuna = (
        _build_route(graph, depths, varuna_edges, depart=depart, vehicle=base, source=source)
        if varuna_edges
        else None
    )

    if varuna_edges is None and naive_edges is not None:
        notes.append(
            f"Every route to this destination crosses water deeper than {base.depth_cm:.0f} cm "
            f"for a {base.label.lower()}. The shortest way is shown; it is not passable."
        )

    # What the naive route walks into and VARUNA does not: the honest content of "avoided".
    # What the naive route walks into and VARUNA does not, refused on exactly the criterion the
    # search used: the run's own probability against the profile's tolerance, or an authority's
    # closure. Listing "depth over the threshold" instead would name streets the search happily
    # took (a 20-member run can put 35 cm on a street at probability 0.1) and miss ones it
    # refused, so the list would disagree with the route beside it.
    avoided: list[Avoided] = []
    closed_on_naive: list[tuple[str, str]] = []
    if naive is not None:
        chosen = {leg.segment_id for leg in (varuna.legs if varuna else ())}
        seen: set[str] = set()
        for leg in naive.legs:
            if leg.segment_id in chosen or leg.segment_id in seen:
                continue
            closure_reason = overlay.reason_for(leg.segment_id)
            refused = closure_reason is not None or leg.probability >= base.risk_tolerance
            if refused:
                seen.add(leg.segment_id)
                if closure_reason is not None:
                    closed_on_naive.append((leg.segment_id, leg.name or "Unnamed road"))
                avoided.append(
                    Avoided(
                        segment_id=leg.segment_id,
                        name=leg.name or "Unnamed road",
                        depth_cm=leg.depth_cm,
                        probability=leg.probability,
                        at=leg.arrive,
                        closed_reason=closure_reason,
                        path=_segment_path(graph, leg.segment_id),
                    )
                )
        avoided.sort(key=lambda a: -a.depth_cm)

    alternates: list[Route] = []
    if varuna_edges:
        used = set(varuna_edges)
        for _ in range(MAX_ALTERNATES):
            more = _search(
                graph,
                depths,
                source=source,
                target=target,
                depart=depart,
                vehicle=base,
                avoid_water=True,
                penalised=used,
                closed=closed,
            )
            if not more or set(more) == used:
                break
            alternates.append(
                _build_route(graph, depths, more, depart=depart, vehicle=base, source=source)
            )
            used |= set(more)

    corridors: list[Any] = []
    if spread and varuna is not None:
        corridors = build_corridors(
            [varuna, *alternates],
            depths,
            base,
            trip_id=trip_id,
            closed_segment_ids=closed,
        )
        note = spreading_note(len(corridors), spread=spread, trip_id=trip_id)
        if note:
            notes.append(note)

    reasons: list[dict[str, Any]] = []
    if explain:
        assigned = next((c.route for c in corridors if c.assigned), varuna)
        reasons = build_reasons(
            avoided=avoided,
            route=assigned,
            depths=depths,
            vehicle=base,
            city=city,
            overlay=overlay,
            closed_on_naive=closed_on_naive,
        )
        if any(r["kind"] == "design" for r in reasons):
            notes.append(
                "The design intensity is the drain under that street, from the inferred drain "
                "graph; the peak rain beside it is this run's AOI mean, not the rain over that "
                "one junction."
            )

    ms = (perf_counter() - started) * 1000.0
    log.info(
        "route.planned",
        run_id=depths.run_id,
        profile=base.key,
        ms=round(ms, 1),
        naive_min=round(naive.minutes, 1) if naive else None,
        varuna_min=round(varuna.minutes, 1) if varuna else None,
        avoided=len(avoided),
        blocked_edges=len(blocked),
        closed=len(closed),
        corridors=len(corridors),
        reasons=len(reasons),
    )
    return RouteResult(
        run_id=depths.run_id,
        profile=base.key,
        depart=depart,
        naive=naive,
        varuna=varuna,
        alternates=alternates,
        avoided=avoided,
        notes=notes,
        ms=ms,
        corridors=corridors,
        reasons=reasons,
        trip_id=trip_id,
    )


def as_dict(result: RouteResult) -> dict[str, Any]:
    """The API's shape for a route result (SPEC.md 12)."""

    def route(r: Route | None) -> dict[str, Any] | None:
        if r is None:
            return None
        return {
            "minutes": round(float(r.minutes), 1),
            "distance_m": round(float(r.distance_m), 1),
            "max_depth_cm": round(float(r.max_depth_cm), 1),
            "depart": r.depart.isoformat(),
            "arrive": r.arrive.isoformat(),
            "safe_until": r.safe_until.isoformat() if r.safe_until else None,
            "path": [[round(x, 6), round(y, 6)] for x, y in r.path],
            "streets": _street_names(r),
        }

    return {
        "run_id": result.run_id,
        "profile": result.profile,
        "depart_at": result.depart.isoformat(),
        "naive": route(result.naive),
        "varuna": route(result.varuna),
        "alternates": [route(r) for r in result.alternates],
        "avoided": [
            {
                "segment_id": a.segment_id,
                "name": a.name,
                "depth_cm": round(float(a.depth_cm), 1),
                "probability": round(float(a.probability), 3),
                "at": a.at.isoformat(),
                "closed_reason": a.closed_reason,
                "path": [[round(x, 6), round(y, 6)] for x, y in a.path],
            }
            for a in result.avoided
        ],
        "corridors": [
            {
                "id": c.id,
                "label": c.label,
                "route": route(c.route),
                "share": round(float(c.share), 4),
                "assigned": bool(c.assigned),
                "capacity_score": round(float(c.capacity_score), 4),
                "max_probability": round(float(c.max_probability), 3),
            }
            for c in result.corridors
        ],
        "reasons": result.reasons,
        "trip_id": result.trip_id,
        "notes": result.notes,
        "ms": round(result.ms, 1),
    }


def hazard_note(depths: SegmentDepths) -> str:
    """What a pedestrian answer applied of Appendix A's rule, said on the response.

    Two halves: ``h >= 0.3 m``, or ``h * v >= 0.5 m^2/s``. The second needs a flow speed, and a
    run either carries one or it does not; the note says which, so a walker is never told a
    street is safe on the strength of a rule that was only half checked without being told so.
    """
    if depths.has_velocity:
        return (
            "Pedestrian: a street is refused at 30 cm, or where the median depth times this "
            "run's flow speed reaches 0.5 m2/s (Appendix A), whichever comes first."
        )
    return (
        "Pedestrian: only the depth half of the hazard rule is applied here, refusing a street "
        "at 30 cm. The other half, depth times flow speed at or above 0.5 m2/s, needs a speed, "
        "and this run carries none - the Twin computes surface fluxes, but no product keeps "
        "them - so it is not checked and no speed is assumed. Fast, shallow water is not "
        "caught."
    )


def _segment_path(graph: RoadGraph, segment_id: str) -> tuple[tuple[float, float], ...]:
    """The endpoints of one segment, from the first out-edge that carries it."""
    for e, sid in enumerate(graph.edge_segment):
        if sid == segment_id:
            tail = int(graph.edge_tail[e])
            head = int(graph.head[e])
            return (
                (float(graph.lon[tail]), float(graph.lat[tail])),
                (float(graph.lon[head]), float(graph.lat[head])),
            )
    return ()


def _street_names(route: Route, limit: int = 12) -> list[str]:
    """The named streets a route uses, in order, without repeating consecutive names."""
    out: list[str] = []
    for leg in route.legs:
        if leg.name and (not out or out[-1] != leg.name):
            out.append(leg.name)
        if len(out) >= limit:
            break
    return out

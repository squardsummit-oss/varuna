"""Routing, reachability and the road-condition feed (SPEC.md 12; tasks P8.4, P8.6).

Three endpoints and one feed. The route and the isochrones are the console's; the feed is the
one a navigation app or a transit operator would consume, which is the fifth deliverable the
ministry asked for (SPEC.md 2.2) and the reason the contract is GeoJSON with validity windows
rather than something VARUNA-shaped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Body, Query

from varuna_api.state import api_error

log = structlog.get_logger("varuna.api.route")

router = APIRouter(prefix="/v1", tags=["route"])

MAX_FEED_SEGMENTS = 800
"""Segments in one road-conditions document. The whole wet set is 1,498 on the 2 July storm; the
feed carries the ones a driver would be stopped by, worst first."""


def _point(value: Any, field: str) -> tuple[float, float]:
    """Parse ``[lon, lat]`` or ``{"lon":, "lat":}``, refusing anything outside the world."""
    if isinstance(value, dict):
        pair = (value.get("lon"), value.get("lat"))
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        pair = (value[0], value[1])
    else:
        raise api_error(422, "bad_point", f"{field} must be [lon, lat] or {{lon, lat}}.")
    try:
        lon, lat = float(pair[0]), float(pair[1])  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise api_error(422, "bad_point", f"{field} must be two numbers, [lon, lat].") from None
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise api_error(422, "bad_point", f"{field} is not a coordinate: [{lon}, {lat}].")
    return lon, lat


def _when(value: Any, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        raise api_error(
            422,
            "bad_time",
            f"{field} must be ISO 8601 with an offset, e.g. 2019-07-02T08:40:00+05:30.",
        ) from None


def _flag(value: Any, field: str, *, default: bool) -> bool:
    """A boolean body field that refuses anything that is not one, rather than guessing."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise api_error(422, "bad_flag", f"{field} must be true or false, got {value!r}.")


@router.post("/route", summary="Route around the forecast water, beside what a naive router does")
def route(body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """Plan one trip.

    Body: ``{origin, destination, depart_at?, profile?, risk_tolerance?, run_id?, spread?,
    trip_id?, explain?}``.

    Returns both routes, because the comparison is the product: a dispatcher who only sees the
    safe route has no way to judge whether the detour was worth it.

    ``spread`` (default true) adds ``corridors[]`` - up to three safe roads with the share of
    traffic the policy gives each, and the one this request is assigned to. ``trip_id`` makes
    that assignment stable for a trip, so a reader who reloads is not sent somewhere else; it is
    the client's own random id and nothing is stored against it. ``explain`` (default true) adds
    ``reasons[]``, which is structured data - the frontend writes the sentences (TECH_SPEC 3.2).
    """
    from varuna_route.profiles import PROFILES
    from varuna_route.router import as_dict, plan

    origin = _point(body.get("origin"), "origin")
    destination = _point(body.get("destination"), "destination")
    vehicle = str(body.get("profile") or "ambulance")
    if vehicle not in PROFILES:
        raise api_error(
            422,
            "unknown_profile",
            f"No vehicle profile {vehicle!r}. Valid profiles: {', '.join(sorted(PROFILES))}.",
        )

    tolerance = body.get("risk_tolerance")
    if tolerance is not None:
        try:
            tolerance = float(tolerance)
        except (TypeError, ValueError):
            raise api_error(
                422, "bad_tolerance", "risk_tolerance must be a number between 0 and 1."
            ) from None
        if not 0.0 <= tolerance <= 1.0:
            raise api_error(
                422, "bad_tolerance", f"risk_tolerance must be between 0 and 1, got {tolerance}."
            )

    trip_id = body.get("trip_id")
    if trip_id is not None and not str(trip_id).strip():
        trip_id = None

    try:
        result = plan(
            origin,
            destination,
            depart_at=_when(body.get("depart_at"), "depart_at"),
            vehicle=vehicle,
            risk_tolerance=tolerance,
            run_id=body.get("run_id"),
            spread=_flag(body.get("spread"), "spread", default=True),
            trip_id=str(trip_id) if trip_id is not None else None,
            explain=_flag(body.get("explain"), "explain", default=True),
        )
    except FileNotFoundError as error:
        raise api_error(404, "no_run", str(error)) from error
    return name_streets(as_dict(result))


def name_streets(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace every "Unnamed road" a route response carries with the segment's display name.

    The router writes "Unnamed road" for a street OSM does not name - and the Rust port writes
    the same, which is why the rule lives here and not in ``varuna_route``: the two routers are
    held to each other leaf by leaf (ADR-0063). ``avoided[]`` and ``reasons[]`` both carry a
    ``segment_id`` beside the name, so the name is looked up rather than guessed. Every other
    field is untouched.
    """
    from varuna_schemas.settings import get_settings

    from varuna_api import street_names
    from varuna_api.runs_util import city_of_run

    run_id = payload.get("run_id")
    city = (city_of_run(str(run_id)) if run_id else None) or get_settings().varuna_city
    for key in ("avoided", "reasons"):
        for entry in payload.get(key) or []:
            if not isinstance(entry, dict) or "name" not in entry:
                continue
            if street_names.is_unnamed(entry.get("name")) and entry.get("segment_id"):
                entry["name"] = street_names.display_name_for(city, entry["segment_id"])
    return payload


@router.get("/route/facilities", summary="Hospitals and fire stations the isochrones can start at")
def route_facilities(city: str = "mumbai") -> dict[str, Any]:
    """The pickable facilities, from the city's own asset layer."""
    from varuna_route.reach import facilities

    try:
        found = facilities(city)
    except FileNotFoundError as error:
        raise api_error(404, "no_city", str(error)) from error
    return {
        "city": city,
        "count": len(found),
        "facilities": [
            {
                "asset_id": f.asset_id,
                "name": f.name,
                "kind": f.kind,
                "lon": f.lon,
                "lat": f.lat,
            }
            for f in found
        ],
    }


@router.get("/reachability", summary="One facility's 5/10/15-minute catchment, against dry")
def reachability(
    facility: Annotated[str, Query(description="asset_id or exact name from /v1/route/facilities")],
    t: Annotated[str | None, Query(description="Instant to measure at, ISO 8601.")] = None,
    profile: str = "ambulance",
    city: str = "mumbai",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Isochrones and the collapse flag."""
    from varuna_route.reach import as_dict
    from varuna_route.reach import reachability as compute

    try:
        result = compute(facility, at=_when(t, "t"), vehicle=profile, city=city, run_id=run_id)
    except KeyError as error:
        raise api_error(404, "unknown_facility", str(error).strip("'")) from error
    except FileNotFoundError as error:
        raise api_error(404, "no_run", str(error)) from error
    return as_dict(result)


@router.get("/feeds/road-conditions", summary="Impassable and degraded roads, as GeoJSON")
def road_conditions(
    profile: str = "car",
    run_id: str | None = None,
    city: str = "mumbai",
) -> dict[str, Any]:
    """The provider feed: every segment this run predicts will stop the given vehicle.

    One feature per segment, with the window it is impassable for. A navigation app does not want
    a depth in centimetres, it wants "closed from 08:20 to 10:05", so that is what this carries -
    with the depth beside it for anyone who does.
    """
    from varuna_route import ops_overlay as ops
    from varuna_route.forecast import load_depths
    from varuna_route.graph import load_graph
    from varuna_route.profiles import PROFILES
    from varuna_route.profiles import profile as get_profile

    from varuna_api import street_names

    if profile not in PROFILES:
        raise api_error(
            422,
            "unknown_profile",
            f"No vehicle profile {profile!r}. Valid profiles: {', '.join(sorted(PROFILES))}.",
        )
    try:
        # `city` was accepted and then dropped on the way to the forecast, so a Chennai feed
        # joined Mumbai depths to Chennai geometry. Both sides take the city now.
        depths = load_depths(run_id, city)
        graph = load_graph(city)
    except FileNotFoundError as error:
        raise api_error(404, "no_run", str(error)) from error

    vehicle = get_profile(profile)
    overlay = ops.active(city, at=depths.valid_ts)
    # OSM names barely half of Mumbai's streets; `display_name` names the rest (street_names).
    names = street_names.street_names(city)
    # One representative edge per segment carries its name and geometry endpoints.
    first_edge: dict[str, int] = {}
    for e, segment_id in enumerate(graph.edge_segment):
        first_edge.setdefault(segment_id, e)

    # A street an authority closed is impassable for the whole run window whatever the water is
    # doing, and the feed says which of the two put it there (task D-06). The overlay is read
    # here; no product file is touched, so a re-bake is still byte-identical.
    wet_ids = set(depths.depth_cm)
    subjects = sorted(wet_ids | set(overlay.closed_segment_ids))

    features: list[dict[str, Any]] = []
    for segment_id in subjects:
        series = depths.depth_cm.get(segment_id, [])
        over = [k for k, value in enumerate(series) if value > vehicle.depth_cm]
        closure = overlay.closures.get(segment_id)
        if not over and closure is None:
            continue
        edge = first_edge.get(segment_id)
        if edge is None:
            continue
        tail = int(graph.edge_tail[edge])
        head = int(graph.head[edge])
        first_step = over[0] if over else 0
        last_step = over[-1] if over else depths.n_steps - 1
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [round(float(graph.lon[tail]), 6), round(float(graph.lat[tail]), 6)],
                        [round(float(graph.lon[head]), 6), round(float(graph.lat[head]), 6)],
                    ],
                },
                "properties": {
                    "segment_id": segment_id,
                    "name": graph.edge_name[edge] or None,
                    "display_name": (
                        names.for_segment(segment_id)
                        if names is not None
                        else street_names.display_name_for(city, segment_id, graph.edge_name[edge])
                    ),
                    "condition": "impassable",
                    "cause": "closure" if closure is not None else "forecast",
                    "closed_reason": closure.reason if closure is not None else None,
                    "profile": vehicle.key,
                    "peak_depth_cm": round(max(series), 1) if series else 0.0,
                    "from": depths.time_of(first_step).isoformat(),
                    "to": (
                        closure.until.isoformat()
                        if closure is not None and closure.until is not None
                        else depths.time_of(last_step).isoformat()
                    ),
                },
            }
        )

    # Closures first, then the deepest water: a street an authority shut is not a forecast and
    # must not be dropped by the truncation that protects the document's size.
    features.sort(
        key=lambda f: (
            0 if f["properties"]["cause"] == "closure" else 1,
            -float(f["properties"]["peak_depth_cm"]),
        )
    )
    truncated = len(features) > MAX_FEED_SEGMENTS
    n_closures = sum(1 for f in features if f["properties"]["cause"] == "closure")
    log.info(
        "api.road_conditions",
        run_id=depths.run_id,
        profile=vehicle.key,
        segments=len(features),
        closures=n_closures,
    )
    return {
        "type": "FeatureCollection",
        "run_id": depths.run_id,
        "valid_ts": depths.valid_ts.isoformat(),
        "profile": vehicle.key,
        "threshold_cm": vehicle.depth_cm,
        "count": len(features),
        "closures": n_closures,
        "truncated": truncated,
        "features": features[:MAX_FEED_SEGMENTS],
        "notes": [
            "Forecast from a reconstructed replay of 2 July 2019, not a live observation.",
            f"A segment is listed when its predicted depth exceeds {vehicle.depth_cm:.0f} cm, "
            f"the depth at which a {vehicle.label.lower()} stops.",
            f"{n_closures} of these are closed by an authority rather than by the forecast; each "
            "carries cause='closure' and the reason given. Closures are an append-only overlay "
            "read at request time and change no forecast product.",
        ],
    }

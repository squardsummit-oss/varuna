"""Reachability: how much of the city a hospital can still reach (SPEC.md 11.9, task P8.3).

A depth map tells an operator which street is flooded. This tells them what that *costs*: KEM
Hospital's fifteen-minute catchment on a dry morning against its catchment at 08:40 on 2 July.
When the second is a third of the first, the sentence "the hospital is cut off" stops being
rhetoric and becomes a number with a denominator.

**How.** One forward time-dependent Dijkstra from the facility per time slice - the same search
the router runs, without a target - keeping every node reached within 5, 10 and 15 minutes, then a
concave hull around each set. The dry baseline is the same sweep with the water switched off, so
the ratio compares like with like rather than against a figure from another day.

**The collapse flag** fires when the 15-minute catchment falls below 40 % of that dry baseline
(SPEC.md 7.4). It is an area ratio, not a claim about patients: it says the roads that reach
this hospital have shrunk, which is what the road network can honestly report.
"""

from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import structlog
from varuna_schemas.paths import city_dir

from varuna_route.forecast import SegmentDepths, load_depths
from varuna_route.graph import RoadGraph, load_graph
from varuna_route.profiles import Profile, profile

log = structlog.get_logger("varuna.route.reach")

__all__ = ["CUTS", "Facility", "Reachability", "collapse", "facilities", "reachability"]

CUTS: tuple[int, ...] = (5, 10, 15)
"""Isochrone bands in minutes (SPEC.md 6.2 `--reach-5`, `--reach-10`, `--reach-15`)."""

COLLAPSE_RATIO = 0.4
"""Below this share of the dry 15-minute catchment, the facility is flagged as collapsed."""

HULL_RATIO = 0.3
"""`shapely.concave_hull` tightness. Loose enough to be one shape, tight enough not to swallow
the bay: a convex hull around a coastal hospital's catchment would claim the sea."""

MIN_HULL_POINTS = 4


@dataclass(frozen=True, slots=True)
class Facility:
    """A hospital or fire station the console can pick."""

    asset_id: str
    name: str
    kind: str
    lon: float
    lat: float


@dataclass(frozen=True, slots=True)
class Reachability:
    """One facility's catchment at one instant, against its own dry baseline."""

    facility: Facility
    run_id: str
    at: datetime
    profile: str
    rings: dict[int, list[list[tuple[float, float]]]]
    """Minutes -> polygon rings (lon/lat) reachable within that many minutes."""

    area_km2: dict[int, float]
    dry_area_km2: dict[int, float]
    n_reached: dict[int, int]
    n_dry: dict[int, int]
    collapsed: bool
    ms: float
    notes: tuple[str, ...] = ()
    """Caveats specific to this answer, such as which half of the pedestrian rule applied."""

    @property
    def share_of_dry(self) -> float:
        """The 15-minute catchment as a share of the dry baseline; 1.0 when nothing has changed.

        Counted in **junctions reached**, not in hull area. Water can only ever remove junctions
        from the catchment, so the count is monotone by construction and the share is bounded by
        one. The concave hull is not: it is drawn around whichever points survive, and dropping an
        interior junction can leave a hull that is fractionally *larger* than the dry one. That
        artefact put a share of 1.018 on a facility that had plainly lost roads. The polygons stay
        as the picture; the count is the measurement.
        """
        dry = self.n_dry.get(CUTS[-1], 0)
        return self.n_reached.get(CUTS[-1], 0) / dry if dry > 0 else 1.0


@lru_cache(maxsize=2)
def facilities(city: str = "mumbai") -> tuple[Facility, ...]:
    """Hospitals and fire stations from the city's asset layer, north to south.

    Only assets inside the AOI: a hospital whose catchment is mostly outside the modelled area
    would report a shrinking number that the model has no basis for.
    """
    path = Path(city_dir(city)) / "map" / "assets.geojson"
    if not path.is_file():
        msg = f"No assets for {city}: {path} is missing. Run `make city CITY={city}`."
        raise FileNotFoundError(msg)
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[Facility] = []
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        kind = str(props.get("kind", ""))
        if kind not in {"hospital", "fire_station"}:
            continue
        if props.get("in_aoi") is False:
            continue
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point":
            continue
        lon, lat = geometry["coordinates"][:2]
        out.append(
            Facility(
                asset_id=str(props.get("asset_id", "")),
                name=str(props.get("name") or "Unnamed facility"),
                kind=kind,
                lon=float(lon),
                lat=float(lat),
            )
        )
    out.sort(key=lambda f: (f.kind, f.name))
    return tuple(out)


def facility(asset_id: str, city: str = "mumbai") -> Facility:
    """Look one up by id, or by an exact name when the caller has only that."""
    known = facilities(city)
    for f in known:
        if f.asset_id == asset_id or f.name == asset_id:
            return f
    msg = f"No facility {asset_id!r} in {city}. There are {len(known)} hospitals and fire stations."
    raise KeyError(msg)


def _sweep(
    graph: RoadGraph,
    depths: SegmentDepths | None,
    *,
    source: int,
    at: datetime,
    vehicle: Profile,
    limit_s: float,
) -> np.ndarray:
    """Travel time in seconds from ``source`` to every node, capped at ``limit_s``.

    ``depths`` of None is the dry baseline. The cap is what keeps this inside its 2 s budget: the
    search stops expanding as soon as it is beyond the largest band, so on a 15,546-node graph it
    settles a few thousand nodes rather than all of them.
    """
    from varuna_route.router import _blocking, _phi

    best = np.full(graph.n_nodes, np.inf)
    best[source] = 0.0
    heap: list[tuple[float, int]] = [(0.0, source)]
    speed = max(vehicle.speed_scale, 0.05)
    step = depths.step_at(at) if depths is not None else 0

    while heap:
        elapsed, node = heapq.heappop(heap)
        if elapsed > best[node] + 1e-9 or elapsed > limit_s:
            continue
        for e in range(int(graph.indptr[node]), int(graph.indptr[node + 1])):
            cost = graph.edge_time_s[e] / speed
            if depths is not None:
                segment_id = graph.edge_segment[e]
                depth = depths.depth_at(segment_id, step)
                # The run's own exceedance since task D-01, so an isochrone shrinks on the same
                # criterion a route diverts on rather than on a threshold comparison beside it.
                # And a pedestrian's velocity half of the hazard rule where the run has a speed.
                if _blocking(depths, segment_id, vehicle, step) >= vehicle.risk_tolerance:
                    continue
                cost *= _phi(depth, vehicle.depth_cm)
            nxt = int(graph.head[e])
            candidate = elapsed + cost
            if candidate < best[nxt] - 1e-9 and candidate <= limit_s:
                best[nxt] = candidate
                heapq.heappush(heap, (candidate, nxt))
    return best


@lru_cache(maxsize=64)
def _dry_sweep(city: str, source: int, vehicle: str, limit_s: float) -> np.ndarray:
    """The dry-weather baseline sweep, cached.

    It depends on the road network and the vehicle, and on neither the run nor the time, so a
    facility scrubbed across three hours pays for it once instead of thirty-six times. That is
    what brings a scrub inside the 2 s reachability budget.
    """
    return _sweep(
        load_graph(city),
        None,
        source=source,
        at=datetime.now(),
        vehicle=profile(vehicle),
        limit_s=limit_s,
    )


def _hull(lon: np.ndarray, lat: np.ndarray) -> tuple[list[list[tuple[float, float]]], float]:
    """A concave hull around the reached nodes, and its area in km^2."""
    from shapely import concave_hull
    from shapely.geometry import MultiPoint

    if lon.size < MIN_HULL_POINTS:
        return [], 0.0
    shape = concave_hull(MultiPoint(list(zip(lon.tolist(), lat.tolist(), strict=True))), HULL_RATIO)
    if shape.is_empty:
        return [], 0.0

    polygons = list(getattr(shape, "geoms", [shape]))
    rings: list[list[tuple[float, float]]] = []
    area_deg2 = 0.0
    for poly in polygons:
        exterior = getattr(poly, "exterior", None)
        if exterior is None:
            continue
        rings.append([(round(float(x), 6), round(float(y), 6)) for x, y in exterior.coords])
        area_deg2 += float(poly.area)

    # Degrees squared to km^2 at this latitude. Equirectangular, which over a 15-minute catchment
    # (a few kilometres) is well inside the precision the ratio needs.
    mean_lat = float(np.mean(lat))
    km_per_deg_lat = 110.574
    km_per_deg_lon = 111.320 * math.cos(math.radians(mean_lat))
    return rings, area_deg2 * km_per_deg_lat * km_per_deg_lon


def reachability(
    asset_id: str,
    *,
    at: datetime | None = None,
    vehicle: str = "ambulance",
    city: str = "mumbai",
    run_id: str | None = None,
) -> Reachability:
    """One facility's isochrones at one instant, with the dry baseline beside them."""
    from time import perf_counter

    from varuna_route.router import hazard_note

    started = perf_counter()
    graph = load_graph(city)
    # The run the city had issued by `at`, not the newest: a scrub to 07:40 asks what the
    # facility could reach at 07:40 on the forecast of the time (forecast.run_dir_at).
    depths = load_depths(run_id, city, at=at)
    target = facility(asset_id, city)
    vehicle_profile = profile(vehicle)
    when = at or depths.valid_ts
    source = graph.nearest_node(target.lon, target.lat)
    limit_s = max(CUTS) * 60.0

    wet = _sweep(graph, depths, source=source, at=when, vehicle=vehicle_profile, limit_s=limit_s)
    dry = _dry_sweep(city, source, vehicle_profile.key, limit_s)

    rings: dict[int, list[list[tuple[float, float]]]] = {}
    area: dict[int, float] = {}
    dry_area: dict[int, float] = {}
    reached: dict[int, int] = {}
    n_dry: dict[int, int] = {}
    for minutes in CUTS:
        cap = minutes * 60.0
        mask = wet <= cap
        polygons, km2 = _hull(graph.lon[mask], graph.lat[mask])
        rings[minutes] = polygons
        area[minutes] = km2
        reached[minutes] = int(mask.sum())
        dry_mask = dry <= cap
        _, dry_km2 = _hull(graph.lon[dry_mask], graph.lat[dry_mask])
        dry_area[minutes] = dry_km2
        n_dry[minutes] = int(dry_mask.sum())

    result = Reachability(
        facility=target,
        run_id=depths.run_id,
        at=when,
        profile=vehicle_profile.key,
        rings=rings,
        area_km2=area,
        dry_area_km2=dry_area,
        n_reached=reached,
        n_dry=n_dry,
        collapsed=collapse(reached, n_dry),
        ms=(perf_counter() - started) * 1000.0,
        notes=(hazard_note(depths),) if vehicle_profile.hazard_rule else (),
    )
    log.info(
        "route.reachability",
        facility=target.name,
        run_id=result.run_id,
        ms=round(result.ms, 1),
        km2_15=round(area[CUTS[-1]], 2),
        dry_km2_15=round(dry_area[CUTS[-1]], 2),
        collapsed=result.collapsed,
    )
    return result


def collapse(reached: dict[int, int], dry: dict[int, int]) -> bool:
    """Whether the 15-minute catchment has fallen below :data:`COLLAPSE_RATIO` of dry.

    On junction counts, for the reason given on :attr:`Reachability.share_of_dry`.
    """
    baseline = dry.get(CUTS[-1], 0)
    if baseline <= 0:
        return False
    return reached.get(CUTS[-1], 0) < COLLAPSE_RATIO * baseline


def as_dict(result: Reachability) -> dict[str, Any]:
    """GeoJSON-with-context, the shape `GET /v1/reachability` returns (SPEC.md 12)."""
    return {
        "run_id": result.run_id,
        "facility": {
            "asset_id": result.facility.asset_id,
            "name": result.facility.name,
            "kind": result.facility.kind,
            "lon": result.facility.lon,
            "lat": result.facility.lat,
        },
        "valid_ts": result.at.isoformat(),
        "profile": result.profile,
        "collapsed": result.collapsed,
        "share_of_dry": round(result.share_of_dry, 3),
        "bands": [
            {
                "minutes": minutes,
                "area_km2": round(result.area_km2[minutes], 3),
                "dry_area_km2": round(result.dry_area_km2[minutes], 3),
                "n_junctions": result.n_reached[minutes],
                "n_junctions_dry": result.n_dry[minutes],
                "rings": result.rings[minutes],
            }
            for minutes in CUTS
        ],
        "ms": round(result.ms, 1),
        "notes": [
            "Catchment measured on the road network the city pipeline derived, against this "
            "facility's own dry-weather catchment. The share is junctions reached, not hull area.",
            *result.notes,
        ],
    }

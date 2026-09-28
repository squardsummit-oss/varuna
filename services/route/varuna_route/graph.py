"""The road graph the router runs on (SPEC.md 11.9, task P8.1).

Built from ``city/<city>/segments.parquet`` - the same 21,296 segments the map draws and the
products forecast, joined by ``segment_id``. Using one geometry for the picture, the forecast and
the route is what makes "this street is impassable at 08:20" and "your route avoids it" provably
the same street rather than two datasets that agree by luck.

**Stored as arrays, not as a networkx graph.** A route has a 300 ms budget (SPEC.md 14) and a
reachability sweep visits every one of the 46,000 directed edges. Python object traversal spends
most of its time on attribute lookups; flat arrays with a CSR-style index cost one list index per
neighbour. The graph is built once per process and cached - it takes about four seconds and never
changes within a run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import structlog
from varuna_schemas.paths import city_dir

if TYPE_CHECKING:  # pragma: no cover - typing only
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.route.graph")

__all__ = ["CLASS_LANES", "CLASS_SPEED_KMH", "RoadGraph", "load_graph"]

CLASS_SPEED_KMH: dict[str, float] = {
    "motorway": 60.0,
    "motorway_link": 40.0,
    "trunk": 45.0,
    "trunk_link": 30.0,
    "primary": 35.0,
    "primary_link": 25.0,
    "secondary": 30.0,
    "secondary_link": 22.0,
    "tertiary": 25.0,
    "tertiary_link": 20.0,
    "residential": 20.0,
    "living_street": 12.0,
    "unclassified": 20.0,
    "service": 12.0,
}
"""Free-flow speeds by OSM road class, km/h.

Mumbai speeds, not design speeds: a "primary" road here is Dr Ambedkar Road at 35 km/h in dry
weather, not a 60 km/h arterial. The city pipeline already writes a `speed_kmh` per segment and
that is preferred; this fills in where it did not."""

DEFAULT_SPEED_KMH = 20.0

CLASS_LANES: dict[str, float] = {
    "motorway": 3.0,
    "motorway_link": 1.0,
    "trunk": 3.0,
    "trunk_link": 1.0,
    "primary": 2.0,
    "primary_link": 1.0,
    "secondary": 2.0,
    "secondary_link": 1.0,
    "tertiary": 2.0,
    "tertiary_link": 1.0,
    "residential": 1.0,
    "living_street": 1.0,
    "unclassified": 1.0,
    "service": 1.0,
}
"""Lanes **per direction** by road class, used where OSM gives no count.

It gives one on 19,344 of Mumbai's 21,296 segments (90.8 %), because that is how many carry no
`lanes` tag. The number only ever weights one corridor's spare capacity against another's
(:mod:`varuna_route.spread`), never a travel time, so a class default is a stated policy rather
than a measurement - and it is listed as such in `docs/SIMPLIFICATIONS.md`."""

DEFAULT_LANES = 1.0


@dataclass(frozen=True, slots=True)
class RoadGraph:
    """A directed road network in flat arrays, indexed for one-hop neighbour lookup.

    Node ids are the OSM ids from the segment table, compacted to ``0..n_nodes-1``; ``node_ids``
    maps back. Edges are directed: a two-way street contributes two, both carrying the same
    ``segment_id`` so the forecast joins to either direction.
    """

    node_ids: NDArray[np.int64]
    lon: NDArray[np.float64]
    lat: NDArray[np.float64]

    # CSR adjacency: the out-edges of node i are indices `indptr[i] : indptr[i + 1]`.
    indptr: NDArray[np.int64]
    head: NDArray[np.int64]
    """Destination node of each out-edge."""

    edge_segment: list[str]
    """`segment_id` of each out-edge, for joining the forecast."""

    edge_length_m: NDArray[np.float64]
    edge_time_s: NDArray[np.float64]
    """Free-flow traversal time in seconds, before any water."""

    edge_name: list[str]
    """Street name, or "" where OSM has none. What the "avoided" list reads out."""

    edge_tail: NDArray[np.int64]
    """Origin node of each out-edge - the inverse of the CSR index, for path reconstruction."""

    edge_lanes: NDArray[np.float64]
    """Lanes in this direction of travel.

    OSM's ``lanes`` counts both directions of a two-way street, and this graph gives a two-way
    street two directed edges, so the tagged count is halved (floor one) rather than given to each
    direction whole. Where OSM tags nothing, :data:`CLASS_LANES` fills in by road class."""

    @property
    def n_nodes(self) -> int:
        return int(self.lon.size)

    @property
    def n_edges(self) -> int:
        return int(self.head.size)

    def nearest_node(self, lon: float, lat: float) -> int:
        """Index of the node closest to a point, by equirectangular distance.

        Good enough at city scale (the error over 15 km of latitude is centimetres) and it costs
        one vectorised pass over 20,000 nodes rather than building a spatial index for a lookup
        that happens twice per request.
        """
        k = math.cos(math.radians(lat))
        dx = (self.lon - lon) * k
        dy = self.lat - lat
        return int(np.argmin(dx * dx + dy * dy))


def _speed_kmh(road_class: object, given: object) -> float:
    try:
        value = float(given)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = float("nan")
    if math.isfinite(value) and value > 1.0:
        return value
    return CLASS_SPEED_KMH.get(str(road_class), DEFAULT_SPEED_KMH)


def _lanes(road_class: object, given: object, *, oneway: bool) -> float:
    """Lanes in one direction of travel, from OSM where it says and by class where it does not."""
    try:
        total = float(given)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        total = float("nan")
    if math.isfinite(total) and total >= 1.0:
        return total if oneway else max(1.0, math.floor(total / 2.0))
    return CLASS_LANES.get(str(road_class), DEFAULT_LANES)


def _endpoints(geometry: object) -> tuple[tuple[float, float], tuple[float, float]] | None:
    coords = getattr(geometry, "coords", None)
    if coords is None:
        return None
    points = list(coords)
    if len(points) < 2:
        return None
    return (float(points[0][0]), float(points[0][1])), (float(points[-1][0]), float(points[-1][1]))


@lru_cache(maxsize=2)
def load_graph(city: str = "mumbai") -> RoadGraph:
    """Build (and cache) the road graph for a city.

    Raises:
        FileNotFoundError: the city has not been built. The message names the make target,
            because a missing city is a setup step and not a bug (SPEC.md 6.8).
    """
    import geopandas as gpd

    path = Path(city_dir(city)) / "segments.parquet"
    if not path.is_file():
        msg = f"No road graph for {city}: {path} is missing. Run `make city CITY={city}`."
        raise FileNotFoundError(msg)

    # The city stores geometry in its metric CRS (EPSG:32643 for Mumbai), because that is where
    # the hydraulics run. The router works in lon/lat, because that is what the map draws and what
    # a request carries; converting once here is cheaper and less error-prone than converting the
    # two points of every request into UTM.
    frame = gpd.read_parquet(path).to_crs(4326)
    n_segments = len(frame)

    segment_ids = frame["segment_id"].astype(str).tolist()
    us = frame["u"].astype("int64").tolist()
    vs = frame["v"].astype("int64").tolist()
    lengths = frame["length_m"].astype(float).tolist()
    oneways = frame["oneway"].astype(bool).tolist()
    # `class` is a Python keyword, so it survives as a column but never as an attribute.
    classes = frame["class"].astype(str).tolist()
    speeds = frame["speed_kmh"].tolist()
    names = frame["name"].tolist() if "name" in frame.columns else [None] * n_segments
    lane_counts = frame["lanes"].tolist() if "lanes" in frame.columns else [None] * n_segments
    geoms = frame.geometry.tolist()

    raw: list[tuple[int, int, str, float, float, str, float]] = []
    coords: dict[int, tuple[float, float]] = {}
    for i in range(n_segments):
        ends = _endpoints(geoms[i])
        if ends is None:
            continue
        (lon_u, lat_u), (lon_v, lat_v) = ends
        u, v = us[i], vs[i]
        coords.setdefault(u, (lon_u, lat_u))
        coords.setdefault(v, (lon_v, lat_v))

        length = lengths[i]
        seconds = length / max(_speed_kmh(classes[i], speeds[i]) / 3.6, 0.5)
        raw_name = names[i]
        name: str = raw_name if isinstance(raw_name, str) else ""
        lanes = _lanes(classes[i], lane_counts[i], oneway=bool(oneways[i]))
        raw.append((u, v, segment_ids[i], length, seconds, name, lanes))
        if not oneways[i]:
            raw.append((v, u, segment_ids[i], length, seconds, name, lanes))

    node_ids = np.array(sorted(coords), dtype=np.int64)
    index = {int(nid): j for j, nid in enumerate(node_ids)}
    lon = np.array([coords[int(nid)][0] for nid in node_ids], dtype=np.float64)
    lat = np.array([coords[int(nid)][1] for nid in node_ids], dtype=np.float64)

    # Sort by tail so the CSR index is a single cumulative count.
    arcs = sorted(raw, key=lambda a: index[a[0]])
    tails = np.array([index[a[0]] for a in arcs], dtype=np.int64)
    head = np.array([index[a[1]] for a in arcs], dtype=np.int64)
    indptr = np.zeros(node_ids.size + 1, dtype=np.int64)
    np.add.at(indptr, tails + 1, 1)
    np.cumsum(indptr, out=indptr)

    graph = RoadGraph(
        node_ids=node_ids,
        lon=lon,
        lat=lat,
        indptr=indptr,
        head=head,
        edge_segment=[a[2] for a in arcs],
        edge_length_m=np.array([a[3] for a in arcs], dtype=np.float64),
        edge_time_s=np.array([a[4] for a in arcs], dtype=np.float64),
        edge_name=[a[5] for a in arcs],
        edge_tail=tails,
        edge_lanes=np.array([a[6] for a in arcs], dtype=np.float64),
    )
    log.info(
        "route.graph_built",
        city=city,
        segments=n_segments,
        nodes=graph.n_nodes,
        edges=graph.n_edges,
    )
    return graph

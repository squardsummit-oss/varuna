"""P1.6 — road segments split at intersections, with terrain and exposure attributes.

The OSMnx ``drive_service`` graph is already split at intersections: every graph edge runs
from one junction to the next, so one edge is one VARUNA *segment*. This module turns that
graph into the table every downstream engine keys on (SPEC.md 10.1 step 5):

``segment_id``, ``osm_way_id``, ``geometry``, ``class``, ``lanes``, ``oneway``, ``length_m``,
``z_min``, ``z_mean`` (sampled from the conditioned DEM), ``ward`` (from an OSM admin
boundary when one is available, else null) and ``exposure_weight``.

Segment ids are stable by construction: they are built from the OSM way id plus an ordinal
assigned in a deterministic order (``u``, ``v``, ``key``), so re-running ``make city`` on the
same OSM extract keeps every id — run artifacts from an older bake still join.

Exposure weight (SPEC.md 10.1 step 5, "exposure weight = f(class, hospital/station within
300 m, building density)") is a weighted sum, already inside ``[0, 1]``:

``0.50 * class weight + 0.30 * asset proximity + 0.20 * building density``.
"""

from __future__ import annotations

import ast
import time
from collections.abc import Iterable, Mapping
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import structlog
from numpy.typing import NDArray
from rasterio.transform import Affine
from shapely.geometry import LineString

log = structlog.get_logger(__name__)

#: OSM ``highway`` values kept as road segments, coarse class -> the tags that map onto it.
CLASS_TAGS: dict[str, tuple[str, ...]] = {
    "motorway": ("motorway", "motorway_link"),
    "trunk": ("trunk", "trunk_link"),
    "primary": ("primary", "primary_link"),
    "secondary": ("secondary", "secondary_link"),
    "tertiary": ("tertiary", "tertiary_link"),
    "residential": ("residential", "living_street", "unclassified", "road"),
    "service": ("service", "track", "busway"),
}

#: Class contribution to the exposure weight: a trunk road carries far more people than a
#: service lane, so flooding it costs more.
CLASS_WEIGHT: dict[str, float] = {
    "motorway": 1.00,
    "trunk": 0.90,
    "primary": 0.80,
    "secondary": 0.65,
    "tertiary": 0.50,
    "residential": 0.35,
    "service": 0.20,
}

#: Nominal free-flow speeds (km/h) by class - the Route engine's base travel times (P8.1).
CLASS_SPEED_KMH: dict[str, float] = {
    "motorway": 60.0,
    "trunk": 50.0,
    "primary": 40.0,
    "secondary": 35.0,
    "tertiary": 30.0,
    "residential": 25.0,
    "service": 15.0,
}

ASSET_RADIUS_M = 300.0
"""A hospital, fire station or railway station within this distance raises exposure."""

BUILDING_RADIUS_M = 100.0
"""Buildings within this distance of the centreline count towards building density."""

BUILDING_DENSITY_CAP = 40.0
"""Building count that saturates the density term (a dense Dadar block reaches it)."""

_EXPOSURE_WEIGHTS = (0.50, 0.30, 0.20)
"""Class, asset proximity, building density - they sum to 1, so the result is in [0, 1]."""

SEGMENT_COLUMNS = (
    "segment_id",
    "osm_way_id",
    "name",
    "name_aliases",
    "u",
    "v",
    "key",
    "class",
    "lanes",
    "oneway",
    "length_m",
    "z_min",
    "z_mean",
    "ward",
    "exposure_weight",
    "speed_kmh",
    "geometry",
)


_LITERAL_BRACKETS = frozenset({"[]", "{}", "()"})
"""How a stringified list, set or tuple starts and ends. A set is written ``"{400, 500}"``, and
splitting that on its comma would read the way ids as ``"{400"`` and ``"500}"``."""


def osm_items(value: Any) -> list[Any]:
    """The items of an OSM tag value that may carry several, whatever shape it arrived in.

    OSMnx merges ways when it simplifies the graph, and the merged edge carries every way's tag
    as a collection. That collection has three shapes in this repository, and they must all mean
    the same thing or a rebuild from cache disagrees with the build that did the download:

    * a live fetch hands over a real ``list`` (or ``set``/``tuple``);
    * ``osm.gpkg``'s ``roads`` layer has no list type, so the same value comes back as the text
      ``"[123, 456]"`` or ``"['residential', 'service']"`` - 1,886 of Mumbai's 34,539 road
      edges carry their ``osmid`` that way, 171 their ``highway`` and 17 their ``lanes``;
    * the feature layers go through ``osm._sanitize``, which writes ``"a,b"``.

    Treating only the first shape was the defect behind 1,247 of Mumbai's segment ids reading
    ``S0-*`` (the way id collapsed to 0 when the text was not an int), and behind 839 more that
    kept their form but shifted ordinal and so named a different street than the same id did in
    every baked run. Anything that reads a list-valued OSM column goes through here.

    Not for ``name``: a street name may legitimately contain a comma, so names are parsed by
    :func:`split_names`, which only unpacks a genuine list literal.
    """
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if text[:1] + text[-1:] in _LITERAL_BRACKETS:
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                parsed = None
            if isinstance(parsed, (list, tuple, set)):
                return list(parsed)
        if "," in text:
            return [part.strip() for part in text.split(",")]
    return [value]


def classify_highway(value: Any) -> str:
    """Map an OSM ``highway`` tag (possibly a list, or a list as text) onto a VARUNA road class.

    Unknown or missing tags fall back to ``"service"``, the least exposed class. A merged edge
    takes the highest class among its ways, because :data:`CLASS_TAGS` is walked in order.
    """
    tags = osm_items(value)
    for name, members in CLASS_TAGS.items():
        for tag in tags:
            if isinstance(tag, str) and tag in members:
                return name
    return "service"


def _first_way_id(value: Any) -> int:
    """The stable OSM way id for an edge; osmnx merges ways, so take the smallest.

    ``osm_items`` makes the GeoPackage text ``"[123, 456]"`` and the live list ``[123, 456]``
    give the same id, which is what keeps a segment id the same street across ``make city``
    runs (the module docstring's promise, broken until 2026-09-26 for every merged edge).
    """
    ids = [int(v) for v in osm_items(value) if _is_int(v)]
    return min(ids) if ids else 0


def _is_int(value: Any) -> bool:
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True


def _as_int(value: Any) -> int | None:
    """Lane count from an OSM ``lanes`` value (``"2"``, ``["2", "3"]``, a set, ``None``).

    OSMnx hands back a collection whenever it simplifies several ways into one edge, and that
    collection is sometimes a ``set``, whose iteration order changes with Python's per-process
    string hashing. Taking the first item therefore made ``make city`` non-reproducible
    (SPEC.md rule 8). We take the maximum instead: it is order-independent, and where a
    simplified edge spans a widening the widest cross-section is what carries the traffic the
    exposure weight is meant to represent. The GeoPackage text ``"['2', '4']"`` is the same
    collection (:func:`osm_items`).
    """
    found = [int(item) for item in osm_items(value) if _is_int(item)]
    return max(found) if found else None


def _as_bool(value: Any) -> bool:
    items = osm_items(value)
    if len(items) != 1 or items[0] is not value:
        return any(_as_bool(v) for v in items)
    if isinstance(value, str):
        return value.strip().lower() in {"yes", "true", "1", "-1"}
    return bool(value)


def edges_to_frame(graph: Any, *, crs: str | None = None) -> gpd.GeoDataFrame:
    """One row per graph edge, geometry in the graph's (projected) CRS.

    Works on a plain ``networkx.MultiDiGraph`` so this module never imports osmnx - the
    edge geometry is taken from the ``geometry`` attribute when osmnx stored one, and built
    from the two node coordinates otherwise.
    """
    if isinstance(graph, gpd.GeoDataFrame):
        return graph.reset_index() if graph.index.nlevels > 1 else graph.copy()

    graph_crs = crs or graph.graph.get("crs")
    if graph_crs is None:
        msg = "graph has no crs; project it with ox.projection.project_graph first"
        raise ValueError(msg)
    nodes = graph.nodes
    records: list[dict[str, Any]] = []
    for u, v, key, data in graph.edges(keys=True, data=True):
        geom = data.get("geometry")
        if geom is None:
            geom = LineString([(nodes[u]["x"], nodes[u]["y"]), (nodes[v]["x"], nodes[v]["y"])])
        records.append(
            {
                "u": u,
                "v": v,
                "key": key,
                "osmid": data.get("osmid"),
                "highway": data.get("highway"),
                "lanes": data.get("lanes"),
                "oneway": data.get("oneway", False),
                "length": data.get("length"),
                "geometry": geom,
            }
        )
    return gpd.GeoDataFrame(records, geometry="geometry", crs=str(graph_crs))


def sample_dem_along(
    geom: Any,
    dem: NDArray[np.floating] | None,
    transform: Affine | None,
    *,
    step_m: float | None = None,
) -> tuple[float, float]:
    """``(z_min, z_mean)`` of the DEM under one segment.

    The centreline is sampled every half cell so a 30 m segment still gets both ends, and
    ``z_min`` is a minimum over exactly the samples ``z_mean`` averages - so ``z_min`` can
    never exceed ``z_mean``. Returns ``(nan, nan)`` when there is no DEM or every sample
    falls on nodata.
    """
    if dem is None or transform is None or geom is None or geom.is_empty:
        return (float("nan"), float("nan"))
    res = abs(float(transform.a))
    step = step_m if step_m is not None else max(res / 2.0, 1.0)
    length = float(geom.length)
    count = max(2, int(length // step) + 1)
    distances = np.linspace(0.0, length, count)
    points = [geom.interpolate(float(d)) for d in distances]
    xs = np.fromiter((p.x for p in points), dtype=float, count=len(points))
    ys = np.fromiter((p.y for p in points), dtype=float, count=len(points))
    values = sample_raster(dem, transform, xs, ys)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return (float("nan"), float("nan"))
    return (float(finite.min()), float(finite.mean()))


def sample_raster(
    raster: NDArray[np.floating],
    transform: Affine,
    xs: NDArray[np.floating],
    ys: NDArray[np.floating],
) -> NDArray[np.float64]:
    """Nearest-cell raster values at map coordinates; outside the grid yields ``nan``."""
    inverse = ~transform
    cols = np.asarray(inverse.a * xs + inverse.b * ys + inverse.c, dtype=float)
    rows = np.asarray(inverse.d * xs + inverse.e * ys + inverse.f, dtype=float)
    col_index = np.floor(cols).astype(np.int64)
    row_index = np.floor(rows).astype(np.int64)
    height, width = raster.shape
    inside = (row_index >= 0) & (row_index < height) & (col_index >= 0) & (col_index < width)
    out = np.full(xs.shape, np.nan, dtype=np.float64)
    if inside.any():
        picked = raster[row_index[inside], col_index[inside]].astype(np.float64)
        out[inside] = picked
    return out


def _asset_proximity(
    segments: gpd.GeoDataFrame, assets: gpd.GeoDataFrame | None
) -> NDArray[np.float64]:
    """1 at an asset's door, 0 at :data:`ASSET_RADIUS_M` and beyond (linear in between)."""
    n = len(segments)
    if assets is None or len(assets) == 0 or n == 0:
        return np.zeros(n, dtype=np.float64)
    points = assets.geometry
    if str(assets.crs) != str(segments.crs):
        points = points.to_crs(segments.crs)
    points = points.representative_point()
    probe = segments.geometry.representative_point()
    try:
        _, distance = gpd.GeoSeries(points, crs=segments.crs).sindex.nearest(
            probe, return_all=False, return_distance=True
        )
    except (TypeError, ValueError):  # pragma: no cover - very old geopandas
        distance = np.array([probe.distance(points.union_all()) for _ in range(n)])
    distance = np.asarray(distance, dtype=np.float64)
    return np.clip(1.0 - distance / ASSET_RADIUS_M, 0.0, 1.0)


def _building_density(
    segments: gpd.GeoDataFrame, buildings: gpd.GeoDataFrame | None
) -> NDArray[np.float64]:
    """Buildings within :data:`BUILDING_RADIUS_M` of the centreline, capped and normalised."""
    n = len(segments)
    if buildings is None or len(buildings) == 0 or n == 0:
        return np.zeros(n, dtype=np.float64)
    footprints = buildings.geometry
    if str(buildings.crs) != str(segments.crs):
        footprints = footprints.to_crs(segments.crs)
    centroids = gpd.GeoSeries(footprints.representative_point(), crs=segments.crs)
    buffers = segments.geometry.buffer(BUILDING_RADIUS_M)
    hits = centroids.sindex.query(buffers, predicate="intersects")
    counts = np.bincount(np.asarray(hits[0], dtype=np.int64), minlength=n).astype(np.float64)
    return np.clip(counts / BUILDING_DENSITY_CAP, 0.0, 1.0)


def _wards(segments: gpd.GeoDataFrame, wards: gpd.GeoDataFrame | None) -> list[str | None]:
    """Ward name per segment from an OSM admin boundary layer; ``None`` when unavailable."""
    n = len(segments)
    if wards is None or len(wards) == 0 or n == 0:
        return [None] * n
    frame = wards.to_crs(segments.crs) if str(wards.crs) != str(segments.crs) else wards
    name_column = next(
        (c for c in ("ward", "name", "ref", "admin_ref") if c in frame.columns), None
    )
    if name_column is None:
        return [None] * n
    probe = gpd.GeoDataFrame(geometry=segments.geometry.representative_point(), crs=segments.crs)
    joined = gpd.sjoin(probe, frame[[name_column, "geometry"]], how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    values = joined[name_column].reindex(probe.index)
    return [None if pd.isna(v) else str(v) for v in values]


def split_names(value: object) -> tuple[str | None, tuple[str, ...]]:
    """An OSM ``name`` value as ``(the name we print, the other names the way carries)``.

    OSM gives a way several names often enough that this is not an edge case - a flyover that is
    also the road beneath it, a road renamed but still signed both ways - and OSMnx hands that
    over as a Python list. A list reaching a headline is the defect this function exists to stop:
    "['Dr Ambedkar Road', 'Kalachowki Road'] is impassable" is not a sentence a ward officer can
    act on, and 95 of Mumbai's 21,296 segments carried exactly that, two of them on the
    KEM-to-Sion demo route.

    **The repr string is the real source.** A live OSMnx fetch yields a genuine ``list``; the
    GeoPackage cache the pipeline reads on every later build has no list type, so the same value
    comes back as the *text* ``"['Dr Ambedkar Road', 'Kalachowki Road']"``. Handling only the list
    was therefore a fix that worked exactly once, on the machine that did the download. Both
    shapes are parsed here, and a string that merely starts with a bracket but is not a list
    literal ("[Closed] Link Road") is left alone rather than mangled.

    The first name is taken rather than a joined one invented: "Dr Ambedkar Road" is a street a
    person can find, "Dr Ambedkar Road / Kalachowki Road" is a string VARUNA made up. The rest
    come back so nothing is discarded (rule 6) - the export keeps them in ``name_aliases``.

    An unnamed way stays ``None`` rather than becoming "Unnamed road": a product that needs a
    name can then choose its own fallback, and none of them has to guess whether a literal
    "Unnamed road" came from OSM or from us.
    """
    names = [text for text in (_one_name(item) for item in _as_name_list(value)) if text]
    # A way can list the same name twice (two transliterations that strip to the same text);
    # keep the first of each, in order, so the aliases are deterministic.
    unique = list(dict.fromkeys(names))
    if not unique:
        return None, ()
    return unique[0], tuple(unique[1:])


def _as_name_list(value: object) -> list[object]:
    """The candidate names in an OSM ``name`` value, in order, whatever shape it arrived in."""
    if isinstance(value, list | tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return [value]  # "[Closed] Link Road" and friends: a name, not a list
            if isinstance(parsed, list | tuple):
                return list(parsed)
        return [value]
    return [value]


def _one_name(value: object) -> str | None:
    """One candidate as text, or ``None`` when it is missing, NaN or blank."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    text = str(value).strip()
    return text or None


def _first_name(value: object) -> str | None:
    """The name a product prints. See :func:`split_names` for what happens to the others."""
    return split_names(value)[0]


def build_segments(
    graph: Any,
    dem: NDArray[np.floating] | None = None,
    transform: Affine | None = None,
    buildings: gpd.GeoDataFrame | None = None,
    assets: gpd.GeoDataFrame | None = None,
    *,
    wards: gpd.GeoDataFrame | None = None,
    crs: str | None = None,
    class_weights: Mapping[str, float] | None = None,
) -> gpd.GeoDataFrame:
    """Build the road-segment table for one city (P1.6).

    Args:
        graph: projected OSMnx ``drive_service`` graph (a ``networkx.MultiDiGraph``) or an
            edges GeoDataFrame carrying ``u``, ``v``, ``key``, ``osmid`` and ``highway``.
        dem: conditioned DEM on the city grid; ``None`` leaves ``z_min``/``z_mean`` as nan.
        transform: the city grid's affine transform (required with ``dem``).
        buildings: building footprints, for the density term of the exposure weight.
        assets: hospitals, fire stations and railway stations as points or polygons.
        wards: OSM admin boundaries; the first of ``ward``/``name``/``ref`` names the ward.
        crs: overrides the graph CRS when the graph does not carry one.
        class_weights: overrides :data:`CLASS_WEIGHT` (tests and city-specific tuning).

    Returns:
        A GeoDataFrame with :data:`SEGMENT_COLUMNS`, one row per segment, sorted by
        ``segment_id``. Reciprocal edges of a two-way street collapse into one segment.
    """
    started = time.perf_counter()
    edges = edges_to_frame(graph, crs=crs)
    if edges.empty:
        empty = gpd.GeoDataFrame(
            {name: pd.Series(dtype="object") for name in SEGMENT_COLUMNS},
            geometry="geometry",
            crs=crs,
        )
        log.warning("segments.empty_graph")
        return empty

    weights = dict(CLASS_WEIGHT if class_weights is None else class_weights)
    split = [split_names(v) for v in edges.get("name", pd.Series([None] * len(edges)))]
    frame = pd.DataFrame(
        {
            "u": edges["u"].to_numpy(),
            "v": edges["v"].to_numpy(),
            "key": edges["key"].to_numpy() if "key" in edges.columns else 0,
            "osm_way_id": [
                _first_way_id(v) for v in edges.get("osmid", pd.Series([0] * len(edges)))
            ],
            # The street's own name, straight from OSM. Without it every product that has to
            # say *where* - an alert headline, a map tooltip, a route's avoided list - can only
            # offer a segment id, which is useless to the ward officer reading it.
            "name": [primary for primary, _ in split],
            # The other names the way carries, kept rather than dropped: a headline needs one
            # street, a person searching may know it by another (`split_names`).
            "name_aliases": [list(aliases) for _, aliases in split],
            "class": [
                classify_highway(v) for v in edges.get("highway", pd.Series([None] * len(edges)))
            ],
            "lanes": [_as_int(v) for v in edges.get("lanes", pd.Series([None] * len(edges)))],
            "oneway": [_as_bool(v) for v in edges.get("oneway", pd.Series([False] * len(edges)))],
        }
    )
    frame["geometry"] = edges.geometry.to_numpy()

    # A two-way street appears twice (u->v and v->u). Keep one, deterministically.
    node_a = np.minimum(frame["u"].to_numpy(), frame["v"].to_numpy())
    node_b = np.maximum(frame["u"].to_numpy(), frame["v"].to_numpy())
    frame["_pair_a"] = node_a
    frame["_pair_b"] = node_b
    frame = frame.sort_values(["osm_way_id", "_pair_a", "_pair_b", "key", "u", "v"], kind="stable")
    frame = frame.drop_duplicates(subset=["osm_way_id", "_pair_a", "_pair_b", "key"], keep="first")

    ordinal = frame.groupby("osm_way_id", sort=False).cumcount()
    frame["segment_id"] = [
        f"S{way}-{index:03d}" for way, index in zip(frame["osm_way_id"], ordinal, strict=True)
    ]

    segments = gpd.GeoDataFrame(
        frame.drop(columns=["_pair_a", "_pair_b"]), geometry="geometry", crs=edges.crs
    ).reset_index(drop=True)
    segments["length_m"] = segments.geometry.length.astype(float)
    segments["speed_kmh"] = [CLASS_SPEED_KMH.get(name, 15.0) for name in segments["class"]]

    z_values = [sample_dem_along(geom, dem, transform) for geom in segments.geometry]
    segments["z_min"] = [z[0] for z in z_values]
    segments["z_mean"] = [z[1] for z in z_values]

    segments["ward"] = _wards(segments, wards)

    class_term = np.array([weights.get(name, 0.2) for name in segments["class"]], dtype=float)
    asset_term = _asset_proximity(segments, assets)
    density_term = _building_density(segments, buildings)
    w_class, w_asset, w_density = _EXPOSURE_WEIGHTS
    exposure = w_class * class_term + w_asset * asset_term + w_density * density_term
    segments["exposure_weight"] = np.clip(exposure, 0.0, 1.0)

    segments = segments[list(SEGMENT_COLUMNS)].sort_values("segment_id", kind="stable")
    segments = gpd.GeoDataFrame(segments, geometry="geometry", crs=edges.crs).reset_index(drop=True)
    log.info(
        "segments.built",
        segments=len(segments),
        classes=int(segments["class"].nunique()),
        wards=int(segments["ward"].notna().sum()),
        stage_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return segments


def segments_near(
    segments: gpd.GeoDataFrame, points: Iterable[Any], *, max_distance_m: float = 60.0
) -> list[str | None]:
    """Nearest ``segment_id`` for each point, or ``None`` past ``max_distance_m``.

    Used to hang inlets and surface units off a street (P1.7, P1.8).
    """
    probe = gpd.GeoSeries(list(points), crs=segments.crs)
    if segments.empty or probe.empty:
        return [None] * len(probe)
    index, distance = segments.sindex.nearest(probe, return_all=False, return_distance=True)
    ids = segments["segment_id"].to_numpy()
    out: list[str | None] = []
    for position, dist in zip(index[1], np.asarray(distance, dtype=float), strict=True):
        out.append(str(ids[position]) if dist <= max_distance_m else None)
    return out


__all__ = [
    "ASSET_RADIUS_M",
    "BUILDING_DENSITY_CAP",
    "BUILDING_RADIUS_M",
    "CLASS_SPEED_KMH",
    "CLASS_TAGS",
    "CLASS_WEIGHT",
    "SEGMENT_COLUMNS",
    "build_segments",
    "classify_highway",
    "edges_to_frame",
    "osm_items",
    "sample_dem_along",
    "sample_raster",
    "segments_near",
    "split_names",
]

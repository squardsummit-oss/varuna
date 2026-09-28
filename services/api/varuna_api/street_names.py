"""A name a person can read for every road segment, so no screen ever prints "Unnamed road".

OSM names 11,200 of Mumbai's 21,296 segments not at all (52.6 %). Every screen that listed
streets then either printed "Unnamed road" - a row nobody can find on the ground - or dropped
the row. The water on those streets is real, so the row stays and gets the best true
description the city build supports, in this order:

1. ``osm``  - the segment's own OSM name. A way carrying several names, stored by an older build
   as the text ``"['A', 'B']"``, reads "A / B" - the same rule the drain X-ray applies to pipes
   (``varuna_pulse.health``), so one street is written one way on every screen.
2. ``off``  - "off Dr Babasaheb Ambedkar Marg": the named street with a vertex nearest any of
   this segment's vertices, within :data:`OFF_RADIUS_M`. An unnamed lane is split from the named
   street it branches off at a shared junction, so the distance is usually zero. "off" is the
   honest word: the segment is not that street, it leads off it.
3. ``near`` - "Service road near Worli Depot": the road class in words and the nearest chronic
   hotspot of the register or station of the city's assets within :data:`NEAR_RADIUS_M` of the
   segment's midpoint; failing both, the nearest hospital or fire station in the same radius.
4. ``in``   - "Residential street in Mumbai": the class and the city, when nothing is closer.

Nothing here is invented: every word is the segment's own class, an OSM name, a sourced
register point or an OSM station. The labels are deterministic (rule 8): the trees are built
from the tables in file order and ties break on the first vertex.

Built once per process per city and rebuilt only when ``segments.parquet``, the hotspot register
or the asset layer changes on disk (keyed on their mtime and size).
"""

from __future__ import annotations

import ast
import functools
import hashlib
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import numpy as np
import structlog

log = structlog.get_logger("varuna.api.street_names")

NDArrayF = Any
"""A numpy float array; named for readability only."""

__all__ = [
    "CLASS_WORDS",
    "LANDMARK_KINDS",
    "NEAR_RADIUS_M",
    "OFF_RADIUS_M",
    "PIPE_ROAD_RADIUS_M",
    "LabelKind",
    "StreetNames",
    "build_pipe_names",
    "build_street_names",
    "city_label",
    "class_words",
    "clean_name",
    "clear_cache",
    "display_name_for",
    "fallback_name",
    "is_unnamed",
    "pipe_names",
    "signature",
    "street_names",
]

LabelKind = Literal["osm", "off", "near", "in"]

OFF_RADIUS_M = 200.0
"""How far the nearest named street may be and still name a segment "off <street>".

The drain X-ray uses the same 200 m for pipes (``varuna_pulse.health.NAME_RADIUS_M``): past it a
street name stops saying where the segment is and starts describing a neighbourhood."""

NEAR_RADIUS_M = 1500.0
"""How far the nearest hotspot or station may be and still place a segment "near" it."""

LANDMARK_KINDS: tuple[str, ...] = ("hospital", "fire_station")
"""Assets that place a street only when no hotspot or station is within :data:`NEAR_RADIUS_M`.
Every one is an OSM feature with its own name; shelters are left out because the city build
takes schools and community centres as their proxies."""

CLASS_WORDS: dict[str, str] = {
    "motorway": "Expressway",
    "motorway_link": "Expressway ramp",
    "trunk": "Arterial road",
    "trunk_link": "Arterial road ramp",
    "primary": "Main road",
    "primary_link": "Main road link",
    "secondary": "Secondary road",
    "secondary_link": "Secondary road link",
    "tertiary": "Local road",
    "tertiary_link": "Local road link",
    "residential": "Residential street",
    "living_street": "Residential lane",
    "unclassified": "Minor road",
    "service": "Service road",
    "road": "Road",
}
"""OSM's ``highway`` class in the words a reader uses. Anything else reads "Road"."""

UNNAMED_SPELLINGS = frozenset(
    {"", "unnamed road", "unnamed way", "unnamed street", "none", "nan", "null", "[]"}
)
"""Values an upstream product writes when it has no name. None of them is a name."""


def class_words(road_class: object) -> str:
    """The class in words: "Service road" for ``service``, "Road" for one this does not know."""
    key = str(road_class or "").strip().lower()
    return CLASS_WORDS.get(key, "Road")


def clean_name(value: Any) -> str | None:
    """A street name fit to print, or None.

    The rule ``varuna_pulse.health`` applies to pipes, repeated here because it is private there;
    ``test_street_names.py`` pins the two to one table of cases. A list, or a list's text, is
    joined with " / ", each name once, in OSM's order.
    """
    if isinstance(value, (list, tuple, np.ndarray)):
        parts = [clean_name(v) for v in value]
        unique = list(dict.fromkeys(p for p in parts if p))
        return " / ".join(unique) or None
    if value is None or value != value:  # None or NaN
        return None
    text = str(value).strip()
    if len(text) > 1 and text[0] == "[" and text[-1] == "]":
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, (list, tuple)):
            return clean_name(list(parsed))
    return text or None


def is_unnamed(value: object) -> bool:
    """True for anything a product writes when a street has no name."""
    if value is None:
        return True
    return str(value).strip().lower() in UNNAMED_SPELLINGS


@dataclass(frozen=True, slots=True)
class StreetNames:
    """One city's display names, by segment id, with the kind of label each one is."""

    city: str
    city_label: str
    names: dict[str, str]
    kinds: dict[str, LabelKind]
    classes: dict[str, str] = field(default_factory=dict)
    build_ms: float = 0.0
    anchors: dict[str, str] = field(default_factory=dict)
    """The proper noun inside each non-OSM label: the street of an "off" label, the place of a
    "near" label, the city of an "in" label. A screen in Hindi or Marathi composes its own sentence
    around it, so only the connective and the class word are translated, never the name."""

    def get(self, segment_id: object) -> str | None:
        """The display name of one segment, or None when the city has no such segment."""
        return self.names.get(str(segment_id))

    def label(self, segment_id: object) -> tuple[LabelKind, str | None] | None:
        """How one segment's name was made: its kind and, unless it is OSM's own, its anchor."""
        sid = str(segment_id)
        kind = self.kinds.get(sid)
        if kind is None:
            return None
        return kind, self.anchors.get(sid)

    def for_segment(self, segment_id: object, road_class: object = None) -> str:
        """A display name even for an id this build does not have: its class, in the city."""
        found = self.names.get(str(segment_id))
        if found is not None:
            return found
        return f"{class_words(road_class)} in {self.city_label}"

    def counts(self) -> dict[str, int]:
        """How many segments carry each kind of label."""
        tally = Counter(self.kinds.values())
        return {kind: int(tally.get(kind, 0)) for kind in ("osm", "off", "near", "in")}


def _read_places(
    city_root: Path, crs: Any, asset_kinds: tuple[str, ...] = ("station",), hotspots: bool = True
) -> tuple[tuple[str, ...], NDArrayF | None]:
    """The register's hotspots (short names) then the city's assets of ``asset_kinds``, in the
    segments' CRS. The first call places a street by a chronic spot or a station; the second, for
    what is still unplaced, by a hospital or a fire station (:data:`LANDMARK_KINDS`)."""
    import geopandas as gpd

    names: list[str] = []
    points: list[tuple[float, float]] = []

    def take(frame: Any) -> None:
        if frame is None or frame.empty or "name" not in frame.columns:
            return
        if crs is not None and frame.crs is not None and frame.crs != crs:
            frame = frame.to_crs(crs)
        for geom, raw in zip(frame.geometry, frame["name"], strict=True):
            name = clean_name(raw)
            if geom is None or geom.is_empty or not name:
                continue
            point = geom if geom.geom_type == "Point" else geom.representative_point()
            names.append(name.split(" (", 1)[0].strip())
            points.append((float(point.x), float(point.y)))

    for candidate in (city_root / "hotspots.geojson", city_root / "export" / "hotspots.parquet"):
        if not hotspots:
            break
        if candidate.is_file():
            take(
                gpd.read_parquet(candidate)
                if candidate.suffix == ".parquet"
                else gpd.read_file(candidate)
            )
            break
    assets = city_root / "assets.geojson"
    if assets.is_file():
        frame = gpd.read_file(assets)
        if "kind" in frame.columns:
            take(frame[frame["kind"].astype(str).isin(asset_kinds)])

    if not points:
        return (), None
    return tuple(names), np.asarray(points, dtype=np.float64)


def build_street_names(city_root: Path, city: str, label: str | None = None) -> StreetNames:
    """Every segment's display name, from ``<city_root>/segments.parquet``.

    Pure: reads the city build and returns names, writes nothing. A city without
    ``segments.parquet`` returns an empty set rather than raising, since a name is a courtesy.
    """
    import geopandas as gpd
    import shapely
    from scipy.spatial import cKDTree

    started = perf_counter()
    label = label or city.replace("_", " ").title()
    path = city_root / "segments.parquet"
    if not path.is_file():
        return StreetNames(city, label, {}, {}, {}, 0.0, {})

    import pyarrow.parquet as pq

    present = set(pq.read_schema(path).names)
    wanted = [c for c in ("segment_id", "name", "class", "geometry") if c in present]
    frame = gpd.read_parquet(path, columns=wanted)
    if "name" not in frame.columns:
        frame["name"] = None
    if "class" not in frame.columns:
        frame["class"] = None
    if frame.crs is not None and not frame.crs.is_projected:
        frame = frame.to_crs(frame.estimate_utm_crs())
    ids = [str(s) for s in frame["segment_id"]]
    own = [clean_name(v) for v in frame["name"]]
    classes = [str(c) if c is not None and c == c else "" for c in frame["class"]]
    geoms = frame.geometry.to_numpy()

    names: dict[str, str] = {}
    kinds: dict[str, LabelKind] = {}
    for sid, name in zip(ids, own, strict=True):
        if name is not None:
            names.setdefault(sid, name)
            kinds.setdefault(sid, "osm")

    unnamed_rows = np.array(
        [i for i, n in enumerate(own) if n is None and ids[i] not in names], dtype=np.int64
    )
    named_rows = np.array([i for i, n in enumerate(own) if n is not None], dtype=np.int64)

    # 2. "off <street>": nearest named vertex to any vertex of the unnamed segment.
    off: dict[int, str] = {}
    if unnamed_rows.size and named_rows.size:
        named_xy, named_owner = shapely.get_coordinates(geoms[named_rows], return_index=True)
        tree = cKDTree(named_xy[:, :2]) if len(named_xy) else None
        if tree is not None:
            xy, owner = shapely.get_coordinates(geoms[unnamed_rows], return_index=True)
            if len(xy):
                dist, found = tree.query(xy[:, :2], k=1, distance_upper_bound=OFF_RADIUS_M)
                hit = np.isfinite(dist)
                if hit.any():
                    d, o, f = dist[hit], owner[hit], found[hit]
                    # Per unnamed segment, its closest vertex; ties keep the first vertex.
                    order = np.lexsort((np.arange(d.size), d, o))
                    first = np.ones(order.size, dtype=bool)
                    first[1:] = o[order][1:] != o[order][:-1]
                    for k in order[first]:
                        row = int(unnamed_rows[int(o[k])])
                        street = own[int(named_rows[int(named_owner[int(f[k])])])]
                        if street:
                            off[row] = street

    # 3. "<class> near <place>": nearest hotspot or station to the midpoint, then - for what is
    # still unplaced - nearest hospital or fire station.
    near: dict[int, str] = {}
    for place_kinds, with_hotspots in ((("station",), True), (LANDMARK_KINDS, False)):
        rest = np.array(
            [r for r in unnamed_rows.tolist() if r not in off and r not in near], dtype=np.int64
        )
        if not rest.size:
            break
        place_names, place_xy = _read_places(city_root, frame.crs, place_kinds, with_hotspots)
        if place_xy is None or not len(place_names):
            continue
        mids = shapely.get_coordinates(
            shapely.line_interpolate_point(geoms[rest], 0.5, normalized=True)
        )
        finite = np.isfinite(mids).all(axis=1)
        if finite.any():
            dist, found = cKDTree(place_xy).query(
                mids[finite], k=1, distance_upper_bound=NEAR_RADIUS_M
            )
            for row, d, f in zip(rest[finite], dist, found, strict=True):
                if np.isfinite(d) and int(f) < len(place_names):
                    near[int(row)] = place_names[int(f)]

    anchors: dict[str, str] = {}
    for raw_row in unnamed_rows.tolist():
        row = int(raw_row)
        sid = ids[row]
        if sid in names:
            continue
        if row in off:
            names[sid], kinds[sid], anchors[sid] = f"off {off[row]}", "off", off[row]
        elif row in near:
            names[sid], kinds[sid] = f"{class_words(classes[row])} near {near[row]}", "near"
            anchors[sid] = near[row]
        else:
            names[sid], kinds[sid] = f"{class_words(classes[row])} in {label}", "in"
            anchors[sid] = label

    by_class = {sid: cls for sid, cls in zip(ids, classes, strict=True)}
    ms = (perf_counter() - started) * 1000.0
    result = StreetNames(city, label, names, kinds, by_class, ms, anchors)
    log.info(
        "street_names.built", city=city, segments=len(names), ms=round(ms, 1), **result.counts()
    )
    return result


# ---- per-process cache -------------------------------------------------------------------------

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[tuple[Any, ...], StreetNames]] = {}


def _signature(city_root: Path) -> tuple[Any, ...]:
    """What the names depend on, by mtime and size: segments, the register and the assets."""
    parts: list[Any] = [str(city_root)]
    for name in ("segments.parquet", "hotspots.geojson", "assets.geojson"):
        path = city_root / name
        try:
            stat = path.stat()
        except OSError:
            parts.append((name, None))
            continue
        parts.append((name, stat.st_mtime_ns, stat.st_size))
    return tuple(parts)


@functools.lru_cache(maxsize=16)
def city_label(city: str) -> str:
    """The city's own name from its config ("Mumbai"), else its id in title case."""
    try:
        from varuna_city.config import load_city_config

        return str(load_city_config(city).name)
    except Exception:  # a missing config still names the city
        return city.replace("_", " ").title()


def street_names(city: str) -> StreetNames | None:
    """The cached display names for a built city; None when the city id is not a path or unbuilt."""
    from varuna_schemas.paths import city_dir

    try:
        root = city_dir(city)
    except ValueError:
        return None
    if not (root / "segments.parquet").is_file():
        return None
    key = _signature(root)
    with _LOCK:
        cached = _CACHE.get(city)
        if cached is not None and cached[0] == key:
            return cached[1]
        built = build_street_names(root, city, city_label(city))
        _CACHE[city] = (key, built)
        return built


def signature(city: str) -> str:
    """A short token that changes whenever the names would, for an ETag."""
    from varuna_schemas.paths import city_dir

    try:
        key = _signature(city_dir(city))
    except ValueError:
        return "0"
    return hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:10]


def display_name_for(
    city: str | None, segment_id: object, name: object = None, road_class: object = None
) -> str:
    """The name to print for one segment: its own name when it has one, else its display name.

    Never "Unnamed road": a segment the city build does not know reads by its class and city.
    """
    own = None if is_unnamed(name) else clean_name(name)
    if own:
        return own
    names = street_names(city) if city else None
    if names is not None:
        return names.for_segment(segment_id, road_class)
    return fallback_name(None, road_class, city_label(city) if city else "the city")


def fallback_name(name: object, road_class: object, label: str) -> str:
    """A segment's own name when it has one, else its class in words and the city."""
    own = None if is_unnamed(name) else clean_name(name)
    return own or f"{class_words(road_class)} in {label}"


def clear_cache() -> None:
    """Forget every city's names (tests)."""
    with _LOCK:
        _CACHE.clear()
        _PIPE_CACHE.clear()


# ---- pipes -------------------------------------------------------------------------------------

PIPE_ROAD_RADIUS_M = 30.0
"""How far a pipe's midpoint may be from a road segment and still be named by that road.

The inferred drain graph is laid along the road centrelines (inlets every 40 m along roads), so a
pipe under a road sits on it; one grid cell is the tolerance. 98.6 % of Mumbai's 49,770 pipes have
a segment within 60 m of their midpoint; the rest are trunks along waterways and are placed "near"
a hotspot or station instead."""

_PIPE_CACHE: dict[str, tuple[tuple[Any, ...], dict[str, str]]] = {}


def build_pipe_names(city_root: Path, names: StreetNames) -> dict[str, str]:
    """Every inferred pipe's display name, by ``edge_id``: the display name of the road above it.

    A pipe under a named street is named by it; a pipe under a lane OSM does not name reads "off
    Dr Ambedkar Road" exactly as the lane does, because it is the lane's name. A pipe with no road
    within :data:`PIPE_ROAD_RADIUS_M` reads "<class> near <place>", or "<class> in <city>", from the
    road class the drain synthesis gave it. Pure: reads the city build, writes nothing.
    """
    import geopandas as gpd
    import shapely
    from scipy.spatial import cKDTree

    edges_path = city_root / "drain_edges.parquet"
    segments_path = city_root / "segments.parquet"
    if not edges_path.is_file() or not segments_path.is_file():
        return {}
    import pyarrow.parquet as pq

    present = set(pq.read_schema(edges_path).names)
    wanted = [c for c in ("edge_id", "road_class", "geometry") if c in present]
    edges = gpd.read_parquet(edges_path, columns=wanted)
    if "road_class" not in edges.columns:
        edges["road_class"] = None
    segments = gpd.read_parquet(segments_path, columns=["segment_id", "geometry"])
    if segments.crs is not None and not segments.crs.is_projected:
        segments = segments.to_crs(segments.estimate_utm_crs())
    if edges.crs is not None and segments.crs is not None and edges.crs != segments.crs:
        edges = edges.to_crs(segments.crs)

    ids = [str(e) for e in edges["edge_id"]]
    classes = [str(c) if c is not None and c == c else "" for c in edges["road_class"]]
    mids = shapely.line_interpolate_point(edges.geometry.to_numpy(), 0.5, normalized=True)
    out: dict[str, str] = {}

    # The road above the pipe: nearest segment to its midpoint; a tie keeps the first segment.
    found, _dist = segments.sindex.nearest(
        mids, return_distance=True, max_distance=PIPE_ROAD_RADIUS_M
    )
    segment_ids = [str(s) for s in segments["segment_id"]]
    for pipe_row, segment_row in zip(found[0].tolist(), found[1].tolist(), strict=True):
        eid = ids[int(pipe_row)]
        if eid in out:
            continue
        label = names.get(segment_ids[int(segment_row)])
        if label:
            out[eid] = label

    # What no road names: its class near a hotspot or station, else near a hospital or fire
    # station, else in the city - the same order a segment is placed in.
    rest = np.array([i for i, eid in enumerate(ids) if eid not in out], dtype=np.int64)
    for place_kinds, with_hotspots in ((("station",), True), (LANDMARK_KINDS, False)):
        if not rest.size:
            break
        place_names, place_xy = _read_places(city_root, segments.crs, place_kinds, with_hotspots)
        if place_xy is None or not len(place_names):
            continue
        xy = shapely.get_coordinates(mids[rest])
        dist, near = cKDTree(place_xy).query(xy, k=1, distance_upper_bound=NEAR_RADIUS_M)
        placed = np.isfinite(dist) & (near < len(place_names))
        for row, place in zip(rest[placed].tolist(), near[placed].tolist(), strict=True):
            out[ids[int(row)]] = f"{class_words(classes[int(row)])} near {place_names[int(place)]}"
        rest = rest[~placed]
    for row in rest.tolist():
        out[ids[int(row)]] = f"{class_words(classes[int(row)])} in {names.city_label}"
    return out


def pipe_names(city: str) -> dict[str, str]:
    """The cached pipe display names of a built city; empty when it has no drain graph."""
    from varuna_schemas.paths import city_dir

    names = street_names(city)
    if names is None:
        return {}
    root = city_dir(city)
    edges = root / "drain_edges.parquet"
    try:
        stat = edges.stat()
    except OSError:
        return {}
    key = (*_signature(root), ("drain_edges.parquet", stat.st_mtime_ns, stat.st_size))
    with _LOCK:
        cached = _PIPE_CACHE.get(city)
        if cached is not None and cached[0] == key:
            return cached[1]
    started = perf_counter()
    built = build_pipe_names(root, names)
    log.info(
        "street_names.pipes_built",
        city=city,
        pipes=len(built),
        ms=round((perf_counter() - started) * 1000.0, 1),
    )
    with _LOCK:
        _PIPE_CACHE[city] = (key, built)
    return built

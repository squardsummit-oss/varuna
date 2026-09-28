"""Static city layers (SPEC.md 12, task P1.12): ``GET /v1/city/{city}/layers/{name}``.

The city-in-a-box pipeline writes one simplified WGS84 GeoJSON per layer into
``city/<city>/map/`` (SPEC.md 10.1 step 11). This router serves those files: the console's
basemap furniture - streets, the inferred drain graph, assets, the chronic register - loaded
once when a city opens and then restyled from run products without another request.

Layers are files, so the fast path is bytes: without a ``bbox`` the file is streamed
verbatim with an ETag, and the browser's next request answers 304. With a ``bbox`` the
collection is parsed and filtered, which is what a nest or a mobile viewport asks for.

A city that has not been built answers 404 with the command that builds it - never an empty
collection, which would look like a city with no streets.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Query, Request, Response
from varuna_schemas.models import ErrorEnvelope, FeatureCollection
from varuna_schemas.paths import city_config_path, city_dir
from varuna_schemas.settings import get_settings

from varuna_api import street_names
from varuna_api.runs_util import latest_run_for
from varuna_api.state import api_error

log = structlog.get_logger("varuna.api.city")

router = APIRouter(prefix="/v1", tags=["city"])

LayerName = Literal[
    "segments",
    "drains",
    "drain_nodes",
    "assets",
    "hotspots",
    "buildings",
    "units",
    "depressions",
]
"""The layers ``varuna_city.export`` writes into ``city/<city>/map/``."""

LAYER_HINTS: dict[str, str] = {
    "segments": "road segments with class, length and exposure weight",
    "drains": "inferred drain edges with diameter and blockage prior",
    "drain_nodes": "inferred manholes, inlets and outfalls",
    "assets": "hospitals, fire stations, stations, pumping stations, tanks and pumps",
    "hotspots": "the chronic waterlogging register",
    "buildings": "building footprints",
    "units": "surface units draining to each inlet",
    "depressions": "depressions left by the conditioned DEM",
}

GEOJSON_MEDIA_TYPE = "application/geo+json"

CACHE_CONTROL = "public, max-age=3600"
"""City layers only change when ``make city`` runs, so an hour of browser cache is safe."""

NOT_FOUND = {404: {"model": ErrorEnvelope, "description": "City or layer not built yet"}}


def layer_path(city: str, name: str) -> Path:
    """``city/<city>/map/<name>.geojson``; raises for a city id that is not a path segment."""
    return city_dir(city) / "map" / f"{name}.geojson"


def _city_not_built(city: str) -> Exception:
    return api_error(
        404,
        "city_not_built",
        f"No layers for {city}. Run `make city CITY={city}` to build them into city/{city}/.",
    )


def _layer_not_built(city: str, name: str) -> Exception:
    return api_error(
        404,
        "layer_not_built",
        f"City {city} has no {name} layer. Run `make city CITY={city}` to rebuild "
        f"city/{city}/map/{name}.geojson.",
    )


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    parts = bbox.replace(" ", "").split(",")
    if len(parts) != 4:
        raise api_error(
            400,
            "bad_bbox",
            "bbox must be four numbers: minlon,minlat,maxlon,maxlat in WGS84.",
        )
    try:
        minlon, minlat, maxlon, maxlat = (float(p) for p in parts)
    except ValueError:
        raise api_error(
            400,
            "bad_bbox",
            f"bbox {bbox!r} is not four numbers: minlon,minlat,maxlon,maxlat in WGS84.",
        ) from None
    if minlon > maxlon or minlat > maxlat:
        raise api_error(
            400,
            "bad_bbox",
            "bbox corners are the wrong way round: give minlon,minlat,maxlon,maxlat.",
        )
    return (minlon, minlat, maxlon, maxlat)


def _coords_bounds(coordinates: Any) -> tuple[float, float, float, float] | None:
    """Bounding box of any GeoJSON coordinate nesting, or ``None`` when there is none."""
    if isinstance(coordinates, (int, float)):
        return None
    if coordinates and isinstance(coordinates[0], (int, float)):
        lon, lat = float(coordinates[0]), float(coordinates[1])
        return (lon, lat, lon, lat)
    box: tuple[float, float, float, float] | None = None
    for part in coordinates or ():
        found = _coords_bounds(part)
        if found is None:
            continue
        box = (
            found
            if box is None
            else (
                min(box[0], found[0]),
                min(box[1], found[1]),
                max(box[2], found[2]),
                max(box[3], found[3]),
            )
        )
    return box


def feature_in_bbox(feature: dict[str, Any], bbox: tuple[float, float, float, float]) -> bool:
    """True when the feature's own bounding box overlaps ``bbox`` (WGS84)."""
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates")
    if coordinates is None:
        return False
    box = _coords_bounds(coordinates)
    if box is None:
        return False
    return not (box[2] < bbox[0] or box[0] > bbox[2] or box[3] < bbox[1] or box[1] > bbox[3])


def filter_collection(
    payload: dict[str, Any], bbox: tuple[float, float, float, float]
) -> dict[str, Any]:
    """The same collection with only the features whose extent meets ``bbox``."""
    features = [f for f in payload.get("features", []) if feature_in_bbox(f, bbox)]
    return {**payload, "features": features, "bbox": list(bbox)}


@router.get(
    "/city/{city}/layers/{name}",
    response_model=FeatureCollection,
    responses=NOT_FOUND,
    summary="Static layer simplified for the map (segments, drains, assets, hotspots, buildings)",
    response_description="GeoJSON FeatureCollection in WGS84, simplified with a 2 m tolerance",
)
def city_layer(
    request: Request,
    city: str,
    name: LayerName,
    bbox: Annotated[
        str | None, Query(description="minlon,minlat,maxlon,maxlat (WGS84); omit for the AOI")
    ] = None,
) -> Response:
    """One static city layer as GeoJSON, straight from ``city/<city>/map/``."""
    try:
        path = layer_path(city, name)
    except ValueError:
        raise _city_not_built(city) from None
    if not path.is_file():
        raise (_city_not_built(city) if not path.parent.is_dir() else _layer_not_built(city, name))

    stat = path.stat()
    etag = f'W/"{name}-{int(stat.st_mtime)}-{stat.st_size}"'
    if name == "segments":
        # The display names are part of the bytes, so they are part of the tag.
        etag = f'W/"{name}-{int(stat.st_mtime)}-{stat.st_size}-{street_names.signature(city)}"'
    window = _parse_bbox(bbox)
    if request.headers.get("if-none-match") == etag and window is None:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": CACHE_CONTROL})

    headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL, "X-Layer": name}
    if window is None:
        body = named_segments_bytes(city, path) if name == "segments" else path.read_bytes()
        log.info("city.layer_served", city=city, layer=name, bytes=len(body))
        return Response(content=body, media_type=GEOJSON_MEDIA_TYPE, headers=headers)

    payload = json.loads(path.read_text(encoding="utf-8"))
    if name == "segments":
        with_display_names(city, payload)
    filtered = filter_collection(payload, window)
    log.info(
        "city.layer_served",
        city=city,
        layer=name,
        features=len(filtered["features"]),
        of=len(payload.get("features", [])),
        bbox=bbox,
    )
    return Response(
        content=json.dumps(filtered, separators=(",", ":")),
        media_type=GEOJSON_MEDIA_TYPE,
        headers=headers,
    )


_NAMED_LOCK = threading.Lock()
_NAMED: dict[str, tuple[tuple[Any, ...], bytes]] = {}


def with_display_names(city: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Give every segment feature a ``display_name``, in place; ``name`` stays OSM's.

    52.6 % of Mumbai's segments carry no OSM name, and every screen that listed them printed
    "Unnamed road". ``display_name`` is the segment's own name, else "off <street>", else
    "<class> near <place>", else "<class> in <city>" (:mod:`varuna_api.street_names`). A city
    built without ``segments.parquet`` still gets a class-and-city name from the feature itself.

    A label that is not OSM's own name also carries its parts: ``display_kind`` ("off", "near"
    or "in") and ``display_anchor``, the proper noun inside it (the street, the place, the city).
    A screen in Hindi or Marathi builds its own sentence from them and the feature's ``class``, so
    "off" and "Service road" are translated and only the name stays in Latin. An OSM-named
    feature carries neither: its ``display_name`` is its name, and 10,096 more keys would only
    add bytes to the layer.
    """
    names = street_names.street_names(city)
    label = names.city_label if names is not None else street_names.city_label(city)
    for feature in payload.get("features", []):
        props = feature.get("properties")
        if not isinstance(props, dict):
            continue
        sid = props.get("segment_id")
        found = names.get(sid) if names is not None else None
        parts = names.label(sid) if found is not None and names is not None else None
        if found is None:
            # Not in segments.parquet: its own name, else its class in words, in the city.
            raw = props.get("name")
            own = None if street_names.is_unnamed(raw) else street_names.clean_name(raw)
            found = own or f"{street_names.class_words(props.get('class'))} in {label}"
            parts = ("osm", None) if own else ("in", label)
        props["display_name"] = found
        if parts is not None and parts[0] != "osm" and parts[1]:
            props["display_kind"], props["display_anchor"] = parts
    return payload


def named_segments_bytes(city: str, path: Path) -> bytes:
    """The segments layer with display names, serialised once per process per file version."""
    import orjson

    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size, street_names.signature(city))
    with _NAMED_LOCK:
        cached = _NAMED.get(city)
        if cached is not None and cached[0] == key:
            return cached[1]
    payload = orjson.loads(path.read_bytes())
    body = orjson.dumps(with_display_names(city, payload))
    with _NAMED_LOCK:
        _NAMED[city] = (key, body)
    return body


def _config_ids() -> list[str]:
    """Every city VARUNA has a config for, alphabetically."""
    try:
        configs = city_config_path("mumbai").parent
    except ValueError:  # pragma: no cover - "mumbai" is always a valid segment
        return []
    return sorted(path.stem for path in configs.glob("*.yaml"))


def _city_row(city: str) -> dict[str, Any]:
    """One switcher row: what the config says, and whether the pipeline has run yet.

    ``built`` is the one thing the switcher gates on, and it is a file test rather than a flag:
    ``city/<city>/map/segments.geojson`` is what every screen draws first, so a city that has it
    can be opened and a city that does not cannot, whatever anything else claims. ``latest_run``
    is reported separately because a city can be built and still have nothing baked - Chennai is
    exactly that until the wizard's first forecast lands, and a switcher that said "ready" would
    be promising water it has not got.
    """
    name: str = city.title()
    code: str | None = None
    bbox: list[float] | None = None
    try:
        from varuna_city.config import load_city_config

        config = load_city_config(city)
        box = config.bbox
        name, code = config.name, config.code
        # As four numbers, the order every map library wants; `list(BBox)` yields field pairs.
        bbox = [box.min_lon, box.min_lat, box.max_lon, box.max_lat]
    except Exception:  # a missing or unreadable config is a row, not a 500
        log.info("city.config_unreadable", city=city)

    try:
        built = layer_path(city, "segments").is_file()
    except ValueError:
        built = False
    # Not `resolve_city`: a config with no run-id code is still a row the switcher lists, not a
    # 404. `latest_run_for` gives such a city no run rather than every city's newest (task D-09),
    # so the row reads "nothing baked" instead of borrowing Chennai's or Mumbai's water.
    latest = latest_run_for(city, lambda p: (p / "depth" / "bounds.json").is_file())
    return {
        "id": city,
        "name": name,
        "code": code,
        "bbox": bbox,
        "built": built,
        "latest_run_id": latest.name if latest is not None else None,
    }


@router.get("/cities", summary="Cities VARUNA has a config for, and which are built")
def cities() -> dict[str, Any]:
    """The city switcher's rows (SPEC.md 7.2, task D-09).

    The switcher used to carry Mumbai and a hard-coded disabled Chennai, which meant it could not
    tell the jury the truth after the wizard ran: Chennai was built and the row still said
    "Onboard first". This reads the configs and the files on disk instead, so the row changes
    when the pipeline does.
    """
    rows = [_city_row(city) for city in _config_ids()]
    log.info("city.list", n=len(rows), built=sum(1 for row in rows if row["built"]))
    return {"cities": rows, "default": get_settings().varuna_city}


__all__ = [
    "CACHE_CONTROL",
    "GEOJSON_MEDIA_TYPE",
    "LAYER_HINTS",
    "LayerName",
    "cities",
    "city_layer",
    "feature_in_bbox",
    "filter_collection",
    "layer_path",
    "named_segments_bytes",
    "router",
    "with_display_names",
]

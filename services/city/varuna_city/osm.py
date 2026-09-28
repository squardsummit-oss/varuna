"""P1.3 — OpenStreetMap extraction for one city area of interest.

Pulls, through a warm OSMnx cache (``city/cache/osmnx``), every OSM layer the
city-in-a-box pipeline needs (SPEC.md 10.1 step 2):

- the ``drive_service`` road graph,
- building footprints,
- waterways (``drain``, ``canal``, ``stream``, ``river``),
- culverts (``tunnel=culvert``) and bridges (``bridge=yes``),
- railway stations, hospitals and fire stations,
- shelters — schools and community centres, kept as **proxies** and labelled so.

Every layer is reprojected to the city CRS and written to ``city/<city>/osm.gpkg``
as its own GeoPackage layer. A tag set that yields nothing is logged and returned
as an empty GeoDataFrame with the right columns, so one missing layer never stops
the pipeline.

Networking rule (docs/CONVENTIONS.md, ADR-0006): ``truststore.inject_into_ssl()``
must run **before** osmnx is imported, otherwise the first Overpass call fails on
this machine's intercepted TLS.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import geopandas as gpd
import pandas as pd
import structlog
from shapely.geometry import box

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

log = structlog.get_logger(__name__)

#: Tag sets pulled with ``ox.features_from_bbox``. Layer name -> OSM tags.
OVERPASS_TRIES = 3
"""Attempts per layer before giving up. Overpass is a shared public service and resets under load."""

OVERPASS_BACKOFF_S = 4.0
"""Pause before a retry, multiplied by the attempt number."""

FEATURE_TAGS: dict[str, dict[str, Any]] = {
    "buildings": {"building": True},
    "waterways": {"waterway": ["drain", "canal", "stream", "river"]},
    "culverts": {"tunnel": "culvert", "bridge": "yes"},
    "stations": {"railway": ["station", "halt"], "public_transport": "station"},
    "hospitals": {"amenity": "hospital"},
    "fire_stations": {"amenity": "fire_station"},
    "shelters": {"amenity": ["school", "community_centre"]},
}

#: Attribute columns kept per layer (everything else is dropped before writing).
_KEEP_COLUMNS: dict[str, tuple[str, ...]] = {
    "buildings": ("building", "name", "building:levels", "height"),
    "waterways": ("waterway", "name", "width", "tunnel", "layer"),
    "culverts": ("tunnel", "bridge", "highway", "layer", "name"),
    "stations": ("railway", "public_transport", "name", "network"),
    "hospitals": ("amenity", "name", "emergency", "operator"),
    "fire_stations": ("amenity", "name", "operator"),
    "shelters": ("amenity", "name", "operator"),
}

#: Layers whose features stand in for something we cannot get from OSM directly.
PROXY_LAYERS: dict[str, str] = {
    "shelters": "Shelter proxy: OSM schools and community centres (SPEC.md 10.1)",
}


def _osm_cache_folder() -> Path:
    """``city/cache/osmnx`` as an absolute path, so the CLI works from any directory."""
    from varuna_city.cache import cached_osmnx_folder

    return cached_osmnx_folder()


@runtime_checkable
class CityConfigLike(Protocol):
    """The slice of the city config this module needs (P1.1 owns the full model)."""

    @property
    def city(self) -> str: ...

    @property
    def bbox(self) -> tuple[float, float, float, float]: ...

    @property
    def crs(self) -> str: ...


@dataclass(slots=True)
class OsmLayers:
    """Every OSM layer for one area of interest, in the city CRS."""

    city: str
    crs: str
    graph: Any = None
    roads: gpd.GeoDataFrame | None = None
    buildings: gpd.GeoDataFrame | None = None
    waterways: gpd.GeoDataFrame | None = None
    culverts: gpd.GeoDataFrame | None = None
    stations: gpd.GeoDataFrame | None = None
    hospitals: gpd.GeoDataFrame | None = None
    fire_stations: gpd.GeoDataFrame | None = None
    shelters: gpd.GeoDataFrame | None = None
    stage_ms: dict[str, float] = field(default_factory=dict)
    fetch_errors: dict[str, str] = field(default_factory=dict)
    """Layers whose download failed, and why.

    An empty layer means one of two very different things - "this AOI has no canals" or "Overpass
    reset the connection" - and only the second is a reason to distrust everything downstream. On
    Chennai the second happened to `waterways`, which is where the drain graph gets its river
    outfalls, and without this it was recorded as the first. Anything in here is reported rather
    than swallowed (SPEC.md 6)."""

    def layer_names(self) -> list[str]:
        """Names of the GeoDataFrame layers, in write order."""
        skip = {"city", "crs", "graph", "stage_ms", "fetch_errors"}
        return [f.name for f in fields(self) if f.name not in skip]

    def assets(self) -> gpd.GeoDataFrame:
        """Hospitals, fire stations, stations and shelter proxies as one point layer.

        Used by :func:`varuna_city.segments.build_segments` for exposure weights.
        """
        frames: list[gpd.GeoDataFrame] = []
        for name in ("hospitals", "fire_stations", "stations", "shelters"):
            layer = getattr(self, name)
            if layer is None or layer.empty:
                continue
            part = layer[["geometry"]].copy()
            part["asset_kind"] = name
            part["name"] = layer["name"] if "name" in layer.columns else None
            part["proxy"] = name in PROXY_LAYERS
            frames.append(part)
        if not frames:
            return _empty_gdf(("asset_kind", "name", "proxy"), self.crs)
        out = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=self.crs)
        out["geometry"] = out.geometry.representative_point()
        return out


def _import_osmnx() -> Any:
    """Import osmnx with truststore injected and the warm cache configured."""
    import truststore

    truststore.inject_into_ssl()
    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(_osm_cache_folder())
    ox.settings.log_console = False
    return ox


def _empty_gdf(columns: tuple[str, ...], crs: str) -> gpd.GeoDataFrame:
    """An empty layer with the right columns, so a cache miss cannot crash callers."""
    frame = pd.DataFrame({name: pd.Series(dtype="object") for name in columns})
    frame["geometry"] = pd.Series(dtype="object")
    return gpd.GeoDataFrame(frame, geometry="geometry", crs=crs)


def _sanitize(gdf: gpd.GeoDataFrame, keep: tuple[str, ...], crs: str) -> gpd.GeoDataFrame:
    """Keep the wanted columns, stringify list values, and reproject to the city CRS."""
    columns = [c for c in keep if c in gdf.columns]
    out = gdf[[*columns, "geometry"]].copy()
    out = out[~out.geometry.isna()]
    for column in columns:
        out[column] = out[column].map(_scalar)
    if "osmid" not in out.columns:
        out.insert(0, "osmid", [_osmid(index) for index in out.index])
    out = out.reset_index(drop=True)
    if out.crs is not None and str(out.crs) != str(crs):
        out = out.to_crs(crs)
    return gpd.GeoDataFrame(out, geometry="geometry", crs=crs)


def _scalar(value: Any) -> Any:
    """Collapse OSM list values to a comma string so GeoPackage can store them."""
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(v) for v in value)
    return value


def _osmid(index: Any) -> str:
    """OSM element id from a features index entry (``(element_type, id)`` in osmnx 2.x)."""
    if isinstance(index, tuple):
        return "".join(str(part) for part in index)
    return str(index)


def fetch_osm(
    config: CityConfigLike | Any,
    *,
    out_dir: Path | None = None,
    write_gpkg: bool = True,
) -> OsmLayers:
    """Fetch every OSM layer for ``config``'s area of interest (P1.3).

    Args:
        config: city config with ``city``/``name``, ``bbox`` (lon_min, lat_min,
            lon_max, lat_max in WGS84) and ``crs`` (the metric city CRS).
        out_dir: where ``osm.gpkg`` is written; defaults to ``city/<city>/``.
        write_gpkg: set ``False`` to skip writing (tests, dry runs).

    Returns:
        :class:`OsmLayers` with every layer reprojected to the city CRS.
    """
    city, bbox, crs = _read_config(config)
    left, bottom, right, top = bbox
    ox = _import_osmnx()
    layers = OsmLayers(city=city, crs=crs)

    t0 = time.perf_counter()
    try:
        graph = ox.graph_from_bbox(bbox=(left, bottom, right, top), network_type="drive_service")
        graph = ox.projection.project_graph(graph, to_crs=crs)
        layers.graph = graph
        layers.roads = ox.graph_to_gdfs(graph, nodes=False).reset_index()
    except Exception as exc:
        log.warning("osm.graph_failed", city=city, error=str(exc))
        layers.roads = _empty_gdf(("u", "v", "key", "osmid", "highway"), crs)
    layers.stage_ms["graph"] = round((time.perf_counter() - t0) * 1000, 1)

    for name, tags in FEATURE_TAGS.items():
        t1 = time.perf_counter()
        keep = _KEEP_COLUMNS[name]
        layer = None
        last_error: Exception | None = None
        # Overpass drops connections under load, and a single reset used to leave the layer
        # permanently empty - a network flake recorded as a fact about the city. Three tries with
        # a widening pause is enough for every reset seen on Chennai, and a genuinely empty layer
        # costs nothing extra because it succeeds on the first.
        for attempt in range(OVERPASS_TRIES):
            try:
                found = ox.features_from_bbox(bbox=(left, bottom, right, top), tags=tags)
                layer = (
                    _sanitize(found, keep, crs) if len(found) else _empty_gdf(("osmid", *keep), crs)
                )
                break
            except Exception as exc:
                last_error = exc
                if attempt + 1 < OVERPASS_TRIES:
                    pause = OVERPASS_BACKOFF_S * (attempt + 1)
                    log.warning(
                        "osm.layer_retry",
                        city=city,
                        layer=name,
                        attempt=attempt + 1,
                        of=OVERPASS_TRIES,
                        pause_s=pause,
                        error=str(exc),
                    )
                    time.sleep(pause)
        if layer is None:
            log.warning("osm.layer_failed", city=city, layer=name, error=str(last_error))
            layers.fetch_errors[name] = str(last_error)
            layer = _empty_gdf(("osmid", *keep), crs)
        if name in PROXY_LAYERS:
            layer["proxy_note"] = PROXY_LAYERS[name]
        setattr(layers, name, layer)
        layers.stage_ms[name] = round((time.perf_counter() - t1) * 1000, 1)
        log.info("osm.layer", city=city, layer=name, features=len(layer))

    if write_gpkg:
        write_osm_gpkg(layers, out_dir)
    return layers


def write_osm_gpkg(layers: OsmLayers, out_dir: Path | None = None) -> Path:
    """Write every non-empty layer of ``layers`` to ``<out_dir>/osm.gpkg``."""
    from varuna_schemas.paths import city_dir

    target = Path(out_dir) if out_dir is not None else city_dir(layers.city)
    target.mkdir(parents=True, exist_ok=True)
    path = target / "osm.gpkg"
    if path.exists():
        path.unlink()
    for name in ("roads", *layers.layer_names()):
        layer = getattr(layers, name, None)
        if layer is None or layer.empty:
            log.info("osm.skip_empty_layer", city=layers.city, layer=name)
            continue
        layer.to_file(path, layer=name, driver="GPKG")
    log.info("osm.gpkg_written", city=layers.city, path=str(path))
    return path


def aoi_polygon(config: CityConfigLike | Any) -> gpd.GeoSeries:
    """The area of interest as a one-element GeoSeries in the city CRS."""
    _, bbox, crs = _read_config(config)
    left, bottom, right, top = bbox
    return gpd.GeoSeries([box(left, bottom, right, top)], crs="EPSG:4326").to_crs(crs)


def _read_config(config: Any) -> tuple[str, tuple[float, float, float, float], str]:
    """Duck-type the city config so ``CityConfig`` (P1.1) and a plain object both work.

    ``CityConfig`` carries ``id`` (the slug), a ``BBox`` model and an integer EPSG code;
    a test double may carry ``city``, a plain 4-tuple and an ``EPSG:`` string.
    """
    city = str(
        getattr(config, "id", None) or getattr(config, "city", None) or getattr(config, "name", "")
    )
    if not city:
        msg = "city config needs an id (slug), for example 'mumbai'"
        raise ValueError(msg)
    bbox = getattr(config, "bbox", None)
    if bbox is None:
        msg = "city config needs a bbox (lon_min, lat_min, lon_max, lat_max) in WGS84"
        raise ValueError(msg)
    as_tuple = getattr(bbox, "as_tuple", None)
    values = as_tuple() if callable(as_tuple) else tuple(bbox)
    left, bottom, right, top = (float(v) for v in values)
    crs = getattr(config, "crs_string", None) or getattr(config, "crs", None)
    crs = crs if crs is not None else getattr(config, "epsg", None)
    if crs is None:
        msg = "city config needs a metric crs, for example 'EPSG:32643'"
        raise ValueError(msg)
    crs = f"EPSG:{crs}" if isinstance(crs, int) else str(crs)
    return city, (left, bottom, right, top), crs


__all__ = [
    "FEATURE_TAGS",
    "PROXY_LAYERS",
    "CityConfigLike",
    "OsmLayers",
    "aoi_polygon",
    "fetch_osm",
    "write_osm_gpkg",
]

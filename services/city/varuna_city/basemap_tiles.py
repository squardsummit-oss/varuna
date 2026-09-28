"""An offline vector basemap for the public map, built from the city's own open data (task P9.10).

The public map's aerial imagery is Esri's, and Esri's tiles are not redistributable (P10.6), so a
phone that has lost its connection cannot be handed a cached copy of them. What it can be handed
is a basemap VARUNA derived itself from data whose licences allow it:

- **roads, buildings and waterways** from the OpenStreetMap extract `make city` already wrote to
  ``city/<city>/osm.gpkg`` - ODbL, which requires the credit "(c) OpenStreetMap contributors";
- **open water** (sea, creeks, lakes) vectorised from ESA WorldCover class 80 in
  ``city/<city>/landcover.tif`` - CC BY 4.0, which requires its own credit.

Both credits are written into the archive's metadata, so the attribution travels with the file,
and the map prints them while the basemap is on screen.

The output is one PMTiles v3 archive of gzipped Mapbox Vector Tiles at
``city/<city>/map/basemap.pmtiles``, served by ``GET /v1/city/{city}/basemap.pmtiles``. It is
standalone on purpose: the pipeline's step list is not touched, and a city that has been built
gets its basemap with

    uv run python -m varuna_city.basemap_tiles --city mumbai

**Deterministic** (SPEC.md rule 8): features are written in source order, tiles in tile-id
order, and every gzip member with ``mtime=0``, so two builds from the same inputs are
byte-identical - which the tests check.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import mapbox_vector_tile
import numpy as np
import rasterio
import shapely
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer
from rasterio import features as rio_features
from varuna_schemas.paths import city_dir

from varuna_city.config import load_city_config

#: Web Mercator half-width in metres.
ORIGIN = 20037508.342789244
#: MVT coordinate extent per tile.
EXTENT = 4096
#: Clip buffer around each tile, in tile pixels (of 4096), so strokes do not end at tile seams.
BUFFER_PX = 64

MIN_ZOOM = 11
MAX_ZOOM = 16
#: Buildings only from here: below it a footprint is under a pixel and 39,000 of them are noise.
BUILDINGS_MIN_ZOOM = 14
#: Waterway centre-lines from here; open water polygons from the minimum zoom.
WATERWAYS_MIN_ZOOM = 12

#: ESA WorldCover 2021 class "Permanent water bodies".
WORLDCOVER_WATER = 80

#: Road classes kept at each low zoom; every class is kept from zoom 14.
ROADS_BY_ZOOM: dict[int, frozenset[str]] = {
    11: frozenset({"motorway", "trunk", "primary"}),
    12: frozenset({"motorway", "trunk", "primary", "secondary"}),
    13: frozenset({"motorway", "trunk", "primary", "secondary", "tertiary"}),
}

OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"
WORLDCOVER_ATTRIBUTION = "Water: ESA WorldCover 2021 (CC BY 4.0)"
ATTRIBUTION = f"{OSM_ATTRIBUTION}; {WORLDCOVER_ATTRIBUTION}"
#: The same credit in ASCII, for an HTTP header (header values are Latin-1 at best).
ATTRIBUTION_ASCII = ATTRIBUTION.replace("©", "(c)")

BASEMAP_NAME = "basemap.pmtiles"


def basemap_path(city: str) -> Path:
    """``city/<city>/map/basemap.pmtiles``."""
    return city_dir(city) / "map" / BASEMAP_NAME


# ---------------------------------------------------------------------------------------------
# Tile arithmetic
# ---------------------------------------------------------------------------------------------


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    """The XYZ tile holding a WGS84 point at zoom ``z``."""
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Web Mercator bounds of one tile as ``(minx, miny, maxx, maxy)``."""
    size = 2 * ORIGIN / 2**z
    minx = -ORIGIN + x * size
    maxy = ORIGIN - y * size
    return minx, maxy - size, minx + size, maxy


def tiles_covering(bbox: Sequence[float], z: int) -> Iterator[tuple[int, int]]:
    """Every tile at zoom ``z`` that touches a WGS84 ``(west, south, east, north)`` box."""
    west, south, east, north = bbox
    x0, y0 = lonlat_to_tile(west, north, z)
    x1, y1 = lonlat_to_tile(east, south, z)
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            yield x, y


# ---------------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Layer:
    """One MVT layer: geometry in Web Mercator plus the properties each feature carries."""

    name: str
    geoms: np.ndarray
    props: list[dict[str, Any]]
    min_zoom: int
    road_class: np.ndarray | None = None


def _first(value: object) -> str:
    """OSMnx writes a merged way's tags as ``"['primary', 'secondary']"``; keep the first."""
    text = "" if value is None else str(value)
    if text in {"", "nan", "None"}:
        return ""
    if text.startswith("["):
        text = text.strip("[]").split(",")[0].strip().strip("'\"")
    return text


def _read(path: Path, layer: str) -> gpd.GeoDataFrame:
    frame = gpd.read_file(path, layer=layer, engine="pyogrio")
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty]
    return frame.to_crs(3857)


def load_roads(gpkg: Path) -> Layer:
    frame = _read(gpkg, "roads")
    classes = np.array([_first(v).removesuffix("_link") or "road" for v in frame["highway"]])
    names = [_first(v) for v in frame["name"]] if "name" in frame else [""] * len(frame)
    props = [
        {"class": str(c), **({"name": n} if n else {})} for c, n in zip(classes, names, strict=True)
    ]
    return Layer("roads", frame.geometry.to_numpy(), props, MIN_ZOOM, road_class=classes)


def load_buildings(gpkg: Path) -> Layer:
    frame = _read(gpkg, "buildings")
    frame = frame[frame.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    return Layer("buildings", frame.geometry.to_numpy(), [{}] * len(frame), BUILDINGS_MIN_ZOOM)


def load_waterways(gpkg: Path) -> Layer:
    frame = _read(gpkg, "waterways")
    kinds = [_first(v) or "waterway" for v in frame["waterway"]]
    props = [{"kind": k} for k in kinds]
    return Layer("waterways", frame.geometry.to_numpy(), props, WATERWAYS_MIN_ZOOM)


def load_water(landcover: Path) -> Layer:
    """Open water as polygons, vectorised from the 30 m WorldCover raster the pipeline kept."""
    with rasterio.open(landcover) as src:
        grid = src.read(1)
        mask = grid == WORLDCOVER_WATER
        shapes = rio_features.shapes(
            mask.astype(np.uint8), mask=mask, transform=src.transform, connectivity=4
        )
        polys = [shapely.geometry.shape(geom) for geom, _ in shapes]
        crs = src.crs
    frame = gpd.GeoDataFrame(geometry=polys, crs=crs).to_crs(3857)
    # Half a cell: removes the staircase of the 30 m grid without moving a coastline.
    geoms = shapely.simplify(frame.geometry.to_numpy(), 15.0)
    geoms = geoms[~shapely.is_empty(geoms)]
    return Layer("water", geoms, [{"kind": "open water"}] * len(geoms), MIN_ZOOM)


# ---------------------------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ZoomLayer:
    """A layer prepared for one zoom: simplified once, indexed once."""

    name: str
    geoms: np.ndarray
    props: list[dict[str, Any]]
    tree: shapely.STRtree


def prepare(layer: Layer, z: int) -> ZoomLayer | None:
    if z < layer.min_zoom:
        return None
    keep = np.ones(len(layer.geoms), dtype=bool)
    if layer.road_class is not None and z in ROADS_BY_ZOOM:
        keep = np.isin(layer.road_class, sorted(ROADS_BY_ZOOM[z]))
    px_m = 2 * ORIGIN / 2**z / EXTENT
    geoms = shapely.simplify(layer.geoms[keep], px_m, preserve_topology=True)
    props = [p for p, k in zip(layer.props, keep, strict=True) if k]
    return ZoomLayer(layer.name, geoms, props, shapely.STRtree(geoms))


def encode_tile(layers: Iterable[ZoomLayer], z: int, x: int, y: int) -> bytes | None:
    """One gzipped MVT, or None when nothing falls in the tile."""
    minx, miny, maxx, maxy = tile_bounds(z, x, y)
    pad = (maxx - minx) * BUFFER_PX / EXTENT
    clip = (minx - pad, miny - pad, maxx + pad, maxy + pad)
    out: list[dict[str, Any]] = []
    for layer in layers:
        hits = np.sort(layer.tree.query(shapely.box(*clip)))
        if hits.size == 0:
            continue
        clipped = shapely.clip_by_rect(layer.geoms[hits], *clip)
        feats = [
            {"geometry": g, "properties": layer.props[i]}
            for g, i in zip(clipped, hits, strict=True)
            if not g.is_empty and g.geom_type != "GeometryCollection"
        ]
        if feats:
            out.append({"name": layer.name, "features": feats})
    if not out:
        return None
    data = mapbox_vector_tile.encode(
        out, default_options={"quantize_bounds": (minx, miny, maxx, maxy), "extents": EXTENT}
    )
    return gzip.compress(data, mtime=0)


# ---------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildResult:
    path: Path
    bytes: int
    tiles: int
    tiles_by_zoom: dict[int, int]
    features: dict[str, int]
    seconds: float


def build(
    layers: Sequence[Layer],
    bbox: Sequence[float],
    out: Path,
    *,
    min_zoom: int = MIN_ZOOM,
    max_zoom: int = MAX_ZOOM,
    name: str = "VARUNA basemap",
) -> BuildResult:
    """Write the PMTiles archive for ``bbox`` from already-loaded layers."""
    started = time.perf_counter()
    tiles: list[tuple[int, bytes]] = []
    by_zoom: dict[int, int] = {}
    for z in range(min_zoom, max_zoom + 1):
        prepared = [p for p in (prepare(layer, z) for layer in layers) if p is not None]
        count = 0
        for x, y in tiles_covering(bbox, z):
            data = encode_tile(prepared, z, x, y)
            if data is not None:
                tiles.append((zxy_to_tileid(z, x, y), data))
                count += 1
        by_zoom[z] = count
    tiles.sort(key=lambda t: t[0])
    if not tiles:
        msg = f"Nothing to draw inside {tuple(bbox)}: every tile from zoom {min_zoom} was empty."
        raise ValueError(msg)

    west, south, east, north = bbox
    header = {
        "tile_type": TileType.MVT,
        "tile_compression": Compression.GZIP,
        "min_zoom": min_zoom,
        "max_zoom": max_zoom,
        "min_lon_e7": int(west * 1e7),
        "min_lat_e7": int(south * 1e7),
        "max_lon_e7": int(east * 1e7),
        "max_lat_e7": int(north * 1e7),
        "center_zoom": min(max_zoom, 13),
        "center_lon_e7": int((west + east) / 2 * 1e7),
        "center_lat_e7": int((south + north) / 2 * 1e7),
    }
    metadata = {
        "name": name,
        "format": "pbf",
        "attribution": ATTRIBUTION,
        "description": (
            "Offline basemap derived by VARUNA from OpenStreetMap and ESA WorldCover. "
            "No third-party imagery."
        ),
        "vector_layers": [
            {"id": layer.name, "minzoom": max(layer.min_zoom, min_zoom), "maxzoom": max_zoom}
            for layer in layers
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".pmtiles.tmp")
    with tmp.open("wb") as handle:
        writer = Writer(handle)
        for tile_id, data in tiles:
            writer.write_tile(tile_id, data)
        writer.finalize(header, metadata)
    tmp.replace(out)
    return BuildResult(
        path=out,
        bytes=out.stat().st_size,
        tiles=len(tiles),
        tiles_by_zoom=by_zoom,
        features={layer.name: len(layer.geoms) for layer in layers},
        seconds=round(time.perf_counter() - started, 2),
    )


def build_city(city: str, out: Path | None = None, max_zoom: int = MAX_ZOOM) -> BuildResult:
    """Build ``city/<city>/map/basemap.pmtiles`` from the city's OSM extract and land cover."""
    config = load_city_config(city)
    root = city_dir(city)
    gpkg = root / "osm.gpkg"
    landcover = root / "landcover.tif"
    missing = [p for p in (gpkg, landcover) if not p.is_file()]
    if missing:
        names = ", ".join(str(p) for p in missing)
        msg = f"{names} not found. Run `make city CITY={city}` first."
        raise FileNotFoundError(msg)
    layers = [
        load_water(landcover),
        load_waterways(gpkg),
        load_roads(gpkg),
        load_buildings(gpkg),
    ]
    return build(
        layers,
        config.bbox.as_tuple(),
        out or basemap_path(city),
        max_zoom=max_zoom,
        name=f"VARUNA basemap: {config.name}",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m varuna_city.basemap_tiles",
        description="Build the offline PMTiles basemap for one city from its OSM extract.",
    )
    parser.add_argument("--city", default="mumbai")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--max-zoom", type=int, default=MAX_ZOOM)
    args = parser.parse_args(argv)
    try:
        result = build_city(args.city, args.out, args.max_zoom)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "path": str(result.path),
                "bytes": result.bytes,
                "tiles": result.tiles,
                "tiles_by_zoom": result.tiles_by_zoom,
                "features": result.features,
                "seconds": result.seconds,
                "attribution": ATTRIBUTION,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

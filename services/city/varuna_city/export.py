"""City layer exports for the API, the map and Flash (SPEC.md P1.11, 10.1 step 11).

Three consumers, three shapes:

* **API** - GeoParquet under ``city/<city>/export/`` (full attributes, city metric CRS) plus
  the GeoJSON the console reads directly.
* **Map** - ``city/<city>/map/<layer>.geojson`` in WGS84, simplified with a 2 m tolerance in
  the city's metric CRS so the console can load 30k segments without a tile server. These
  are the files ``GET /v1/city/{city}/layers/{name}`` serves.
* **Flash** - ``city/<city>/graph/{nodes,edges,inlet_links}.parquet``, the drain graph as
  plain tables.

:func:`export_city` prefers the layers the pipeline already has in memory (``frames=``) and
falls back to reading them off disk, so it also works as a standalone step on a city folder
somebody else built. A layer that is not there is reported as missing, never faked.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import structlog
from varuna_schemas.paths import city_dir

log = structlog.get_logger("varuna.city.export")

WGS84 = "EPSG:4326"

SIMPLIFY_TOLERANCE_M = 2.0
"""SPEC.md P1.11: the map GeoJSON is simplified with a 2 m tolerance."""

MAP_LAYERS: tuple[str, ...] = (
    "segments",
    "drains",
    "drain_nodes",
    "units",
    "assets",
    "hotspots",
    "buildings",
    "depressions",
)
"""Layers the console asks for through ``GET /v1/city/{city}/layers/{name}``."""

LAYER_SOURCES: dict[str, tuple[tuple[str, str | None], ...]] = {
    "segments": (("segments.parquet", None),),
    "drains": (("drain_edges.parquet", None),),
    "drain_nodes": (("drain_nodes.parquet", None),),
    "units": (("units.parquet", None),),
    "assets": (("assets.geojson", None),),
    "hotspots": (("hotspots.geojson", None),),
    "depressions": (("depressions.parquet", None), ("depressions.geojson", None)),
    "buildings": (("buildings.parquet", None), ("osm.gpkg", "buildings")),
}
"""Layer -> the files it can be read from, as ``(file name, GeoPackage layer or None)``."""

GRAPH_TABLES: dict[str, str] = {
    "nodes": "drain_nodes",
    "edges": "drains",
    "inlet_links": "drain_nodes",
}
"""Flash table -> the layer it is derived from (``inlet_links`` is built from the nodes)."""

MAP_KEEP_COLUMNS: dict[str, tuple[str, ...]] = {
    "segments": (
        "segment_id",
        "osm_way_id",
        "name",
        "class",
        "length_m",
        "lanes",
        "oneway",
        "z_min",
        "ward",
        "exposure_weight",
        "speed_kmh",
    ),
    "drains": (
        "edge_id",
        "from_node",
        "to_node",
        "length_m",
        "shape",
        "diameter_m",
        "width_m",
        "height_m",
        "is_trunk",
        "beta_mean",
        "beta_sd",
        "confidence",
        # The three the underground X-ray draws with. The geometry is 2D, so without these a
        # pipe can only be drawn lying on the street it follows; with them it sits at the depth
        # it was sized for. Measured on the Mumbai graph before adding them: all 49,770 edges
        # run from_node -> to_node (endpoint match 0.0000 m against the node positions) and
        # z_invert_up_m / z_invert_dn_m equal the from/to nodes' own z_invert_m on every one of
        # them, so the up invert belongs to the first vertex and the dn invert to the last.
        # `slope` is the *design* slope Manning capacity was sized on, clamped at 10.1 step 7's
        # 0.3 % and therefore never negative: it is not the bed slope, and anything asking
        # "does this pipe climb?" has to take the fall from the two inverts instead. On the
        # current graph the two disagree on 26,622 edges and 18,994 (38.2 %) run adverse
        # (ADR-0048, re-measured 2026-09-23 with `varuna city audit-gravity`).
        "z_invert_up_m",
        "z_invert_dn_m",
        "slope",
    ),
    "drain_nodes": (
        "node_id",
        "kind",
        "is_outfall",
        "tidal",
        "flap_gate",
        "z_ground_m",
        "z_invert_m",
        "kappa_mean",
        "segment_id",
        "surface_unit_id",
        "downstream_node",
        "confidence",
    ),
    "units": ("unit_id", "area_m2", "imperviousness", "cn", "manning_n", "segment_id", "method"),
    "depressions": ("depression_id", "rank", "depth_m", "area_m2", "volume_m3", "bottom_z_m"),
    "buildings": ("osmid", "building", "height"),
}
"""Properties the map layer keeps. The GeoParquet under ``export/`` keeps every column; the
console only needs what it draws and what a popover shows, and a 50k-edge GeoJSON is worth
trimming. A layer that is not listed keeps everything (assets and hotspots are small and
their provenance fields are the point).

The drain elevations are the one place that trimming was paid for rather than assumed.
Adding ``z_invert_up_m``, ``z_invert_dn_m`` and ``slope`` takes Mumbai's ``map/drains.geojson``
from 17,916,346 to 20,905,959 bytes (+2.99 MB, +16.7 %), and 1,341,898 to 1,874,404 gzipped
(+533 KB, +39.7 %), which is what actually crosses the wire since the API gzips anything over
1 KB. 72 % of that growth is the three key names and their punctuation, 43 bytes on every one
of the 49,770 features, so it cannot be compressed away by shortening values. Rounding the
inverts to centimetres and the slope to 1e-4 was measured and **not** taken: it saves 147,287
bytes plain (0.70 %) and 77,109 gzipped (4.11 %), the stored values already carry at most
three decimals rather than float64 noise, and :func:`round_coordinates` deliberately walks
only the geometry so that a float property keeps the precision the pipeline wrote. Spending a
new per-property rounding mechanism on 77 KB is not a trade worth making; if the layer ever
has to shrink, the honest lever is vector tiles, not fewer decimals."""

COORD_DIGITS = 6
"""Decimal places kept in map GeoJSON coordinates: 1e-6 degrees is about 0.11 m."""


@dataclass(slots=True)
class ExportResult:
    """What :func:`export_city` wrote, and what it could not find."""

    city: str
    written: dict[str, Path] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    bytes_written: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "city": self.city,
            "written": {k: str(v) for k, v in self.written.items()},
            "missing": sorted(self.missing),
            "counts": dict(self.counts),
            "bytes": dict(self.bytes_written),
        }


def find_layer_source(layer: str, *, out_dir: Path) -> tuple[Path, str | None] | None:
    """The file (and GeoPackage layer) a city layer can be read from, or ``None``."""
    for name, gpkg_layer in LAYER_SOURCES.get(layer, ()):
        path = out_dir / name
        if path.is_file():
            return path, gpkg_layer
    return None


def read_layer(path: Path, layer: str | None = None) -> gpd.GeoDataFrame:
    """Read a vector layer from GeoParquet, GeoJSON or a GeoPackage layer."""
    if path.suffix.lower() == ".parquet":
        return gpd.read_parquet(path)
    if layer is not None:
        return gpd.read_file(path, layer=layer)
    return gpd.read_file(path)


def _to_crs(gdf: gpd.GeoDataFrame, crs: str) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        return gdf.set_crs(crs, allow_override=True)
    if str(gdf.crs) == str(crs):
        return gdf
    return gdf.to_crs(crs)


def _json_safe(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Turn values GeoJSON cannot carry (lists, dicts, numpy arrays) into JSON strings."""
    import numpy as np

    out = gdf.copy()
    for column in out.columns:
        if column == out.geometry.name:
            continue
        sample = out[column].dropna()
        if sample.empty:
            continue
        first = sample.iloc[0]
        if isinstance(first, (list, dict, tuple, set, np.ndarray)):
            out[column] = out[column].map(
                lambda v: None if v is None else json.dumps(_plain(v), default=str)
            )
    return out


def _plain(value: Any) -> Any:
    """numpy scalars and arrays down to plain Python, so ``json.dumps`` succeeds."""
    import numpy as np

    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, np.generic):
        return value.item()
    return value


def simplify_for_map(
    gdf: gpd.GeoDataFrame,
    *,
    metric_crs: str,
    tolerance_m: float = SIMPLIFY_TOLERANCE_M,
    keep_columns: tuple[str, ...] | None = None,
) -> gpd.GeoDataFrame:
    """Simplify in the metric CRS, keep the map's columns, then hand back WGS84."""
    metric = _to_crs(gdf, metric_crs)
    geometry_name = metric.geometry.name
    keep = (
        [c for c in keep_columns if c in metric.columns and c != geometry_name]
        if keep_columns
        else [c for c in metric.columns if c != geometry_name]
    )
    simplified = metric[[*keep, geometry_name]].copy()
    simplified = simplified.set_geometry(geometry_name)
    simplified.geometry = metric.geometry.simplify(tolerance_m, preserve_topology=True)
    simplified = simplified[~simplified.geometry.is_empty & simplified.geometry.notna()]
    return _to_crs(simplified, WGS84)


def write_geoparquet(gdf: gpd.GeoDataFrame, path: Path) -> Path:
    """Write a GeoParquet file (geopandas 1.x, geoarrow-compatible metadata)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(path, index=False)
    return path


def round_coordinates(value: Any, digits: int = COORD_DIGITS) -> Any:
    """Round every float in a parsed GeoJSON payload; returns a new payload.

    Full float64 longitudes cost about a third of a map layer's bytes and buy nothing: six
    decimals is 0.11 m, an order of magnitude finer than the 2 m simplification. Only the
    geometry is walked, so a property that happens to be a float keeps its precision.
    """
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, list):
        return [round_coordinates(item, digits) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"features", "geometry", "geometries", "coordinates", "bbox"}:
                out[key] = round_coordinates(item, digits)
            else:
                out[key] = item
        return out
    return value


def write_geojson(gdf: gpd.GeoDataFrame, path: Path, *, compact: bool = True) -> Path:
    """Write WGS84 GeoJSON with LF endings; ``compact`` drops the indentation (map layers)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = round_coordinates(json.loads(_json_safe(_to_crs(gdf, WGS84)).to_json(drop_id=True)))
    text = json.dumps(payload, separators=(",", ":")) if compact else json.dumps(payload, indent=1)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")
    return path


def map_export_fingerprint() -> str:
    """A digest of everything that decides the *shape* of ``city/<city>/map/*.geojson``.

    The deployed API serves these files from a 500 MB volume, and the entrypoint only rebuilds a
    city whose ``pipeline.json`` says a step failed. So a change to :data:`MAP_KEEP_COLUMNS`
    ships in the image, the city on the volume is still complete, the export is skipped, and the
    API serves last month's columns from new code - silently, because nothing is broken enough to
    notice. That is exactly what happened on 2026-09-23: the drain invert elevations were added
    here, the build succeeded, and ``/v1/city/mumbai/layers/drains`` went on serving twelve
    properties without them.

    A fingerprint rather than a hand-bumped version number, because a version number is a thing
    somebody has to remember on the one commit where they are thinking about columns and not
    about deployment. This cannot be forgotten: change what the map layers contain and the digest
    moves by construction.
    """
    payload = json.dumps(
        {
            "layers": list(MAP_LAYERS),
            "keep": {k: list(v) for k, v in sorted(MAP_KEEP_COLUMNS.items())},
            "tolerance_m": SIMPLIFY_TOLERANCE_M,
            "coord_digits": COORD_DIGITS,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def export_city(
    city: str = "mumbai",
    *,
    metric_crs: str = "EPSG:32643",
    out_dir: Path | None = None,
    layers: tuple[str, ...] | None = MAP_LAYERS,
    tolerance_m: float = SIMPLIFY_TOLERANCE_M,
    frames: dict[str, gpd.GeoDataFrame] | None = None,
) -> ExportResult:
    """Write GeoParquet, simplified map GeoJSON and the Flash graph tables.

    Args:
        city: city slug, used for the default output folder and the log lines.
        metric_crs: the city's computation CRS; simplification happens there.
        out_dir: city folder (default ``city/<city>/``).
        layers: which layers to export; ``None`` means :data:`MAP_LAYERS`.
        tolerance_m: simplification tolerance in metres for the map exports.
        frames: layers the caller already has in memory, keyed by layer name.
    """
    root = Path(out_dir) if out_dir is not None else city_dir(city)
    wanted = tuple(layers) if layers else MAP_LAYERS
    supplied = dict(frames or {})
    result = ExportResult(city=city)

    for layer in wanted:
        gdf = supplied.get(layer)
        if gdf is None:
            found = find_layer_source(layer, out_dir=root)
            if found is None:
                result.missing.append(layer)
                log.info("export.layer_missing", city=city, layer=layer)
                continue
            gdf = read_layer(*found)
        if gdf is None or gdf.empty:
            result.missing.append(layer)
            log.warning("export.layer_empty", city=city, layer=layer)
            continue
        result.counts[layer] = len(gdf)
        parquet = write_geoparquet(_to_crs(gdf, metric_crs), root / "export" / f"{layer}.parquet")
        result.written[f"export/{layer}"] = parquet
        result.bytes_written[f"export/{layer}"] = parquet.stat().st_size
        simplified = simplify_for_map(
            gdf,
            metric_crs=metric_crs,
            tolerance_m=tolerance_m,
            keep_columns=MAP_KEEP_COLUMNS.get(layer),
        )
        map_path = write_geojson(simplified, root / "map" / f"{layer}.geojson")
        result.written[f"map/{layer}"] = map_path
        result.bytes_written[f"map/{layer}"] = map_path.stat().st_size

    result.written.update(export_graph_tables(city, out_dir=root, result=result, frames=supplied))
    # The marker the deployed entrypoint reads to decide whether these files came from this
    # code. Written last, so a run that dies half way leaves no claim that it finished.
    marker = root / "map" / "EXPORT.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "fingerprint": map_export_fingerprint(),
                "layers": sorted(result.counts),
                "keep_columns": {k: list(v) for k, v in sorted(MAP_KEEP_COLUMNS.items())},
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    result.written["map/EXPORT"] = marker

    manifest = root / "export" / "MANIFEST.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(result.to_dict(), indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    log.info(
        "export.done",
        city=city,
        written=len(result.written),
        missing=result.missing,
        counts=result.counts,
    )
    return result


def export_graph_tables(
    city: str = "mumbai",
    *,
    out_dir: Path | None = None,
    result: ExportResult | None = None,
    frames: dict[str, gpd.GeoDataFrame] | None = None,
) -> dict[str, Path]:
    """Write ``graph/{nodes,edges,inlet_links}.parquet`` for Flash (SPEC.md 10.1 step 11)."""
    root = Path(out_dir) if out_dir is not None else city_dir(city)
    supplied = dict(frames or {})
    written: dict[str, Path] = {}
    for table, layer in GRAPH_TABLES.items():
        gdf = supplied.get(layer)
        if gdf is None:
            found = find_layer_source(layer, out_dir=root)
            gdf = read_layer(*found) if found is not None else None
        if gdf is None or gdf.empty:
            if result is not None:
                result.missing.append(f"graph/{table}")
            continue
        frame: Any = gdf
        if table == "inlet_links":
            from varuna_city.drains import inlet_links_table

            frame = inlet_links_table(gdf)
        target = root / "graph" / f"{table}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target, index=False)
        written[f"graph/{table}"] = target
        if result is not None:
            result.counts[f"graph/{table}"] = len(frame)
            result.bytes_written[f"graph/{table}"] = target.stat().st_size
    return written


__all__ = [
    "COORD_DIGITS",
    "GRAPH_TABLES",
    "LAYER_SOURCES",
    "MAP_KEEP_COLUMNS",
    "MAP_LAYERS",
    "SIMPLIFY_TOLERANCE_M",
    "ExportResult",
    "export_city",
    "export_graph_tables",
    "find_layer_source",
    "map_export_fingerprint",
    "read_layer",
    "round_coordinates",
    "simplify_for_map",
    "write_geojson",
    "write_geoparquet",
]


def main(argv: list[str] | None = None) -> int:
    """Re-export the map layers from the parquet the city folder already holds.

    This exists for the deployed entrypoint. Going through ``varuna city --only export`` runs the
    pipeline's step graph - which loads every upstream step, then runs ``report`` after this one -
    and on the deployed volume that failed in four seconds for a reason the log threw away
    (2026-09-23). The export itself needs none of that: :func:`export_city` reads each layer off
    disk through :func:`find_layer_source`, and the parquet is what a built city folder is made
    of. Fewer moving parts between a stale layer and a fresh one.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m varuna_city.export",
        description="Re-write city/<city>/map/*.geojson and export/*.parquet from the city folder.",
    )
    parser.add_argument("--city", default="mumbai")
    parser.add_argument("--out-dir", default=None, help="City folder; defaults to city/<city>/.")
    args = parser.parse_args(argv)
    try:
        result = export_city(city=args.city, out_dir=Path(args.out_dir) if args.out_dir else None)
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    if result.missing:
        print(f"missing layers: {', '.join(sorted(result.missing))}", file=sys.stderr)
    counted = ", ".join(f"{k} {v}" for k, v in sorted(result.counts.items()))
    print(f"Exported {args.city}: {counted}")
    print(f"Fingerprint {map_export_fingerprint()} written to map/EXPORT.json")
    return 0 if result.counts else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""The console's 3D heightmap, from the city's own conditioned DEM (SPEC.md 6.7, task P6.15).

Writes ``city/<city>/map/terrain.png`` and ``terrain.json``. The PNG is **Terrarium** encoded:

    height_m = (R * 256 + G + B / 256) - 32768

Terrarium rather than Mapbox terrain-RGB because it needs no base or interval to decode, keeps
a sub-centimetre step (1/256 m) over the whole signed range, and the negative heights the
conditioned DEM carries (carved road centrelines, a few metres below datum at the creek mouths)
round-trip without an offset constant that could drift between writer and reader.

**Same grid as the depth rasters.** The PNG is the conditioned DEM's own 30 m grid, pixel for
pixel - not reprojected - and ``terrain.json`` places it with the corner rule the run's depth
rasters use (``varuna_products.depth.depth_bounds``). That is what lets the console drape step
``k``'s depth PNG on this mesh as a texture with no resampling: both images are the same
``n_rows x n_cols`` grid stretched over the same lon/lat rectangle, so texel ``(i, j)`` of the
water is vertex ``(i, j)`` of the ground. Reprojecting one of them would put the water a cell
or two off its street at the AOI's corners.

The heights are the **conditioned** surface the solver routes water over (buildings burned
+5 m, road centrelines carved -0.15 m, spurious pits breached; SPEC.md 10.1 step 4), not a
bare-earth model. That is deliberate: it is the terrain the forecast was computed on. Heights
are Copernicus GLO-30's EGM2008 orthometric heights; the residual to local mean sea level is not
quantified (ADR-0055).

Standalone and idempotent: ``uv run python -m varuna_city.terrain_export --city mumbai``. It
reads one raster and writes two small files, so the API also calls :func:`ensure_terrain` on
the first request for a city that has a DEM and no heightmap yet.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from varuna_schemas.paths import city_dir

TERRAIN_SOURCE = "dem_conditioned.tif"
"""The raster the solver routes over (SPEC.md 10.1 step 4)."""

TERRAIN_PNG = "terrain.png"
TERRAIN_JSON = "terrain.json"

TERRARIUM_OFFSET_M = 32768.0
"""Terrarium's fixed offset: ``R * 256 + G + B / 256 - 32768``."""

ENCODING = "terrarium"


@dataclass(frozen=True)
class TerrainExport:
    """What was written, for the CLI and the API."""

    png: Path
    meta: Path
    meta_json: dict[str, Any]


def terrarium_rgb(height_m: NDArray[np.floating]) -> NDArray[np.uint8]:
    """Encode heights in metres to Terrarium RGB, rounding to the format's 1/256 m step.

    Non-finite cells (the DEM's nodata) are encoded as 0 m, which is what the console should draw
    for a cell nothing measured: sea level, not a spike.
    """
    h = np.where(np.isfinite(height_m), height_m, 0.0).astype(np.float64)
    # One integer in units of 1/256 m, then split into three bytes. Doing it in integers is what
    # makes the encoding exact: floor/mod on floats drifts by a unit at some heights.
    units = np.rint((h + TERRARIUM_OFFSET_M) * 256.0).astype(np.int64)
    units = np.clip(units, 0, 256**3 - 1)
    r = (units >> 16) & 0xFF
    g = (units >> 8) & 0xFF
    b = units & 0xFF
    return np.stack([r, g, b], axis=-1).astype(np.uint8)


def decode_terrarium(rgb: NDArray[np.uint8]) -> NDArray[np.float64]:
    """The inverse of :func:`terrarium_rgb`, as the console's decoder computes it."""
    c = rgb.astype(np.float64)
    return c[..., 0] * 256.0 + c[..., 1] + c[..., 2] / 256.0 - TERRARIUM_OFFSET_M


def _read_dem(path: Path) -> tuple[NDArray[np.float64], tuple[float, ...], str, tuple[int, int]]:
    import rasterio

    with rasterio.open(path) as src:
        band = src.read(1, masked=True).astype(np.float64)
        heights = np.asarray(band.filled(np.nan), dtype=np.float64)
        t = src.transform
        # (res, 0, left, 0, -res, top) - the six-number form the products writer takes.
        transform = (float(t.a), float(t.b), float(t.c), float(t.d), float(t.e), float(t.f))
        crs = str(src.crs)
        shape = (int(src.height), int(src.width))
    return heights, transform, crs, shape


def _wgs84_bounds(transform: tuple[float, ...], shape: tuple[int, int], crs: str) -> list[float]:
    """The corner rule of ``varuna_products.depth.depth_bounds``, repeated rather than imported.

    Imported, the city package would depend on the products package that depends on it. The rule
    is four lines and a test pins the two to the same numbers.
    """
    from pyproj import Transformer

    n_rows, n_cols = shape
    res, _, left, _, _, top = transform
    right = left + n_cols * res
    bottom = top - n_rows * res
    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    west, south = to_wgs.transform(left, bottom)
    east, north = to_wgs.transform(right, top)
    return [float(west), float(south), float(east), float(north)]


def _png_bytes(rgb: NDArray[np.uint8]) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    # No metadata chunks and a fixed compression level, so two exports are byte-identical
    # (SPEC.md rule 8).
    Image.fromarray(rgb, mode="RGB").save(buffer, format="PNG", optimize=False, compress_level=9)
    return buffer.getvalue()


def export_terrain(city: str, *, out_dir: Path | None = None) -> TerrainExport:
    """Write ``terrain.png`` and ``terrain.json`` for ``city``. Raises if the DEM is missing."""
    base = city_dir(city)
    source = base / TERRAIN_SOURCE
    if not source.exists():
        raise FileNotFoundError(
            f"No conditioned DEM at {source}. Run `make city CITY={city}` first."
        )
    heights, transform, crs, shape = _read_dem(source)
    rgb = terrarium_rgb(heights)
    png = _png_bytes(rgb)

    finite = heights[np.isfinite(heights)]
    decoded = decode_terrarium(rgb)
    round_trip = (
        float(np.max(np.abs(decoded[np.isfinite(heights)] - finite))) if finite.size else 0.0
    )

    meta: dict[str, Any] = {
        "city": city,
        "encoding": ENCODING,
        "decoder": {
            "r_scaler": 256.0,
            "g_scaler": 1.0,
            "b_scaler": 1.0 / 256.0,
            "offset": -32768.0,
        },
        "bounds": _wgs84_bounds(transform, shape, crs),
        "shape": [shape[0], shape[1]],
        "res_m": float(transform[0]),
        "crs": crs,
        "min_m": round(float(finite.min()), 3) if finite.size else 0.0,
        "max_m": round(float(finite.max()), 3) if finite.size else 0.0,
        "p50_m": round(float(np.percentile(finite, 50)), 3) if finite.size else 0.0,
        "nodata_cells": int(heights.size - finite.size),
        "round_trip_max_err_m": round(round_trip, 6),
        "source": TERRAIN_SOURCE,
        "surface": (
            "Conditioned DEM the solver routes over: buildings burned +5 m, road centrelines "
            "carved -0.15 m, spurious pits breached. Copernicus GLO-30, EGM2008 heights."
        ),
        "placement": (
            "Same grid as the run's depth rasters, stretched corner to corner over these bounds "
            "(varuna_products.depth.depth_bounds), so a depth PNG drapes on it texel for vertex."
        ),
        "png_sha256": hashlib.sha256(png).hexdigest(),
    }

    target = out_dir or (base / "map")
    target.mkdir(parents=True, exist_ok=True)
    png_path = target / TERRAIN_PNG
    meta_path = target / TERRAIN_JSON
    # Written through a temporary name and renamed, so the API never serves half a PNG.
    tmp_png = png_path.with_suffix(".png.tmp")
    tmp_png.write_bytes(png)
    tmp_png.replace(png_path)
    tmp_meta = meta_path.with_suffix(".json.tmp")
    tmp_meta.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_meta.replace(meta_path)
    return TerrainExport(png=png_path, meta=meta_path, meta_json=meta)


def ensure_terrain(city: str) -> TerrainExport | None:
    """The heightmap for ``city``, exported on first use. ``None`` when the city has no DEM.

    Re-exported when the DEM is newer than the heightmap, so a re-run of ``make city`` is never
    shadowed by a stale PNG.
    """
    base = city_dir(city)
    source = base / TERRAIN_SOURCE
    png_path = base / "map" / TERRAIN_PNG
    meta_path = base / "map" / TERRAIN_JSON
    if not source.exists():
        return None
    fresh = (
        png_path.exists()
        and meta_path.exists()
        and png_path.stat().st_mtime >= source.stat().st_mtime
    )
    if fresh:
        return TerrainExport(
            png=png_path,
            meta=meta_path,
            meta_json=json.loads(meta_path.read_text(encoding="utf-8")),
        )
    return export_terrain(city)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m varuna_city.terrain_export",
        description="Write the console's 3D heightmap (Terrarium PNG) from the conditioned DEM.",
    )
    parser.add_argument("--city", default="mumbai")
    args = parser.parse_args(argv)
    try:
        result = export_terrain(args.city)
    except FileNotFoundError as error:
        print(str(error), file=sys.stderr)
        return 2
    m = result.meta_json
    print(
        f"Wrote {result.png} ({m['shape'][1]} x {m['shape'][0]} px, {m['encoding']}), "
        f"heights {m['min_m']} to {m['max_m']} m, round trip within {m['round_trip_max_err_m']} m."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

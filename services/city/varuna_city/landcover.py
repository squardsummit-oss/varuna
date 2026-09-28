"""Imperviousness and curve number from ESA WorldCover (SPEC.md 10.1 step 3, task P1.4).

The 10 m WorldCover tile in ``city/cache/worldcover/`` is read through a window over the AOI
- never over the network, never through ``/vsicurl/`` (ADR-0006) - and turned into three
rasters on the city grid:

* ``landcover.tif`` - the WorldCover class codes, nearest-neighbour, for the Manning
  roughness raster (:mod:`varuna_city.roughness`) and the drain-graph land-use priors.
* ``imperviousness.tif`` - the sealed fraction 0-1. The 10 m class is mapped to a per-class
  sealed fraction first and then **averaged** down to 30 m, so a 30 m cell that is half
  built-up and half park comes out near 0.5 instead of jumping to one class or the other.
  Building footprints and road centrelines are burned on top, because WorldCover's built-up
  class already includes the gaps between buildings.
* ``cn.tif`` - the SCS curve number, linear in imperviousness across the city config's
  ``cn_range`` (90-98 for Mumbai: saturated monsoon soils, SPEC.md 11.2). Open water is
  pinned to the top of the range.

Imperviousness feeds the rational-method sizing of the inferred drains (SPEC.md 10.1
step 7) and the effective-rainfall split of :mod:`varuna_twin.hydrology`; the curve number
feeds the infiltration term of the same equation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import structlog
from numpy.typing import NDArray
from rasterio.warp import Resampling, reproject
from rasterio.windows import from_bounds
from varuna_schemas.models.city import CityConfig

from varuna_city.cache import cached_tile
from varuna_city.condition import rasterize_mask
from varuna_city.config import CityGrid, city_grid, city_out_dir
from varuna_city.rasters import NODATA, write_grid_raster

log = structlog.get_logger("varuna.city.landcover")

SEALED_FRACTION: dict[int, float] = {
    10: 0.05,  # tree cover
    20: 0.10,  # shrubland
    30: 0.15,  # grassland
    40: 0.20,  # cropland
    50: 0.85,  # built-up - streets, roofs and the gaps between them
    60: 0.35,  # bare / sparse vegetation (compacted ground)
    70: 0.90,  # snow and ice (not in these AOIs)
    80: 1.00,  # permanent water - no infiltration
    90: 0.05,  # herbaceous wetland
    95: 0.05,  # mangroves
    100: 0.15,  # moss and lichen
}
"""ESA WorldCover v200 class code -> sealed fraction of the cell (SPEC.md 10.1 step 3)."""

DEFAULT_SEALED = 0.30
"""Sealed fraction for a cell whose class code is missing from :data:`SEALED_FRACTION`."""

BUILDING_SEALED = 0.95
"""Sealed fraction burned under a building footprint."""

ROAD_SEALED = 0.90
"""Sealed fraction burned under a road centreline (one cell wide at 30 m)."""

WATER_CLASS = 80
"""WorldCover code for permanent water; its curve number is pinned to the top of the range."""

_TILE_BUFFER_DEG = 0.02
"""Degrees of margin kept around the AOI when windowing the tile, so averaging has neighbours."""


@dataclass(frozen=True, slots=True)
class LandcoverResult:
    """What :func:`build_landcover` produced, on the city grid."""

    classes: NDArray[np.int16]
    """WorldCover class codes (nearest neighbour); 0 where the tile had no data."""
    imperviousness: NDArray[np.float32]
    """Sealed fraction 0-1, area-averaged from 10 m, buildings and roads burned on top."""
    cn: NDArray[np.float32]
    """SCS curve number on the city config's ``cn_range``."""
    grid: CityGrid
    paths: dict[str, Path]
    stage_ms: float
    stats: dict[str, Any]


def sealed_fraction_10m(classes: NDArray[np.integer[Any]]) -> NDArray[np.float32]:
    """Per-cell sealed fraction of a WorldCover class raster (any resolution)."""
    out = np.full(classes.shape, DEFAULT_SEALED, dtype=np.float32)
    for code, fraction in SEALED_FRACTION.items():
        out[classes == code] = fraction
    out[classes == 0] = np.float32(DEFAULT_SEALED)  # tile nodata
    return out


def curve_number(
    imperviousness: NDArray[np.floating[Any]],
    classes: NDArray[np.integer[Any]] | None = None,
    *,
    cn_range: tuple[int, int] = (90, 98),
) -> NDArray[np.float32]:
    """Curve number linear in imperviousness across ``cn_range``; water pinned to the top.

    Monsoon soils are treated as antecedent-moisture class III, which is why the whole range
    sits at 90-98 rather than the textbook 60-98 (SPEC.md 10.1 step 4).
    """
    low, high = (float(cn_range[0]), float(cn_range[1]))
    frac = np.clip(np.asarray(imperviousness, dtype=np.float32), 0.0, 1.0)
    cn = (low + (high - low) * frac).astype(np.float32)
    if classes is not None:
        cn[np.asarray(classes) == WATER_CLASS] = np.float32(high)
    return cn


def _read_tiles_window(
    config: CityConfig,
) -> tuple[NDArray[np.uint8], Any, str]:
    """Mosaic the AOI window of every configured WorldCover tile, in the tile CRS (WGS84)."""
    if not config.landcover_tiles:
        msg = f"city config {config.id!r} lists no landcover_tiles"
        raise ValueError(msg)
    min_lon, min_lat, max_lon, max_lat = config.bbox.as_tuple()
    bounds = (
        min_lon - _TILE_BUFFER_DEG,
        min_lat - _TILE_BUFFER_DEG,
        max_lon + _TILE_BUFFER_DEG,
        max_lat + _TILE_BUFFER_DEG,
    )
    mosaic: NDArray[np.uint8] | None = None
    transform = None
    crs = ""
    for name in config.landcover_tiles:
        path = cached_tile("worldcover", name)
        with rasterio.open(path) as src:
            window = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
            window = window.intersection(
                rasterio.windows.Window(0, 0, src.width, src.height)  # type: ignore[arg-type]
            )
            if window.width <= 0 or window.height <= 0:
                log.info("landcover.tile_outside_aoi", tile=name)
                continue
            data = src.read(1, window=window)
            win_transform = src.window_transform(window)
            crs = str(src.crs)
        if mosaic is None:
            mosaic, transform = data, win_transform
            continue
        # Two tiles covering one AOI is rare (a 3-degree tile covers both these cities), so
        # the merge is the simple one: paste non-zero data onto the first window.
        if data.shape == mosaic.shape:
            mosaic = np.where(mosaic == 0, data, mosaic)
    if mosaic is None or transform is None:
        msg = (
            f"No WorldCover tile in city/cache/worldcover/ covers {config.aoi_id}. "
            f"Tiles named by the config: {config.landcover_tiles}."
        )
        raise FileNotFoundError(msg)
    return mosaic, transform, crs


def build_landcover(
    config: CityConfig,
    *,
    grid: CityGrid | None = None,
    buildings: Any = None,
    roads: Any = None,
    out_dir: Path | None = None,
) -> LandcoverResult:
    """Write ``landcover.tif``, ``imperviousness.tif`` and ``cn.tif`` for one city (P1.4).

    Args:
        config: the loaded city config; its ``landcover_tiles`` must be in the cache.
        grid: the city grid (derived from the config when omitted).
        buildings: building footprints burned to :data:`BUILDING_SEALED`.
        roads: road centrelines burned to :data:`ROAD_SEALED`.
        out_dir: city folder (default ``city/<city>/``).
    """
    started = time.perf_counter()
    grid = grid or city_grid(config)
    target = Path(out_dir) if out_dir is not None else city_out_dir(config)
    source, src_transform, src_crs = _read_tiles_window(config)

    classes = np.zeros(grid.shape, dtype=np.uint8)
    reproject(
        source=source,
        destination=classes,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=grid.transform,
        dst_crs=grid.crs,
        resampling=Resampling.nearest,
        src_nodata=0,
        dst_nodata=0,
    )

    sealed = sealed_fraction_10m(source.astype(np.int16))
    imperviousness = np.zeros(grid.shape, dtype=np.float32)
    reproject(
        source=sealed,
        destination=imperviousness,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=grid.transform,
        dst_crs=grid.crs,
        resampling=Resampling.average,
    )
    imperviousness = np.clip(imperviousness, 0.0, 1.0).astype(np.float32)

    buildings_mask = rasterize_mask(buildings, grid.transform, grid.shape, crs=grid.crs)
    roads_mask = rasterize_mask(roads, grid.transform, grid.shape, crs=grid.crs)
    imperviousness = np.maximum(
        imperviousness, np.where(buildings_mask, BUILDING_SEALED, 0.0).astype(np.float32)
    )
    imperviousness = np.maximum(
        imperviousness, np.where(roads_mask, ROAD_SEALED, 0.0).astype(np.float32)
    )

    classes_i16 = classes.astype(np.int16)
    cn = curve_number(imperviousness, classes_i16, cn_range=tuple(config.cn_range))

    paths = {
        "landcover": write_grid_raster(
            classes_i16, grid, target / "landcover.tif", dtype="int16", nodata=0
        ),
        "imperviousness": write_grid_raster(
            imperviousness, grid, target / "imperviousness.tif", dtype="float32", nodata=NODATA
        ),
        "cn": write_grid_raster(cn, grid, target / "cn.tif", dtype="float32", nodata=NODATA),
    }

    unique, counts = np.unique(classes_i16, return_counts=True)
    stats: dict[str, Any] = {
        "cells": int(classes.size),
        "class_cells": {int(code): int(count) for code, count in zip(unique, counts, strict=True)},
        "imperviousness_mean": round(float(imperviousness.mean()), 4),
        "imperviousness_p90": round(float(np.percentile(imperviousness, 90)), 4),
        "buildings_burned_cells": int(buildings_mask.sum()),
        "roads_burned_cells": int(roads_mask.sum()),
        "cn_mean": round(float(cn.mean()), 2),
        "cn_range": [int(config.cn_range[0]), int(config.cn_range[1])],
    }
    stage_ms = round((time.perf_counter() - started) * 1000.0, 1)
    log.info("landcover.built", city=config.id, stage_ms=stage_ms, **stats)
    return LandcoverResult(
        classes=classes_i16,
        imperviousness=imperviousness,
        cn=cn,
        grid=grid,
        paths=paths,
        stage_ms=stage_ms,
        stats=stats,
    )


__all__ = [
    "BUILDING_SEALED",
    "DEFAULT_SEALED",
    "ROAD_SEALED",
    "SEALED_FRACTION",
    "WATER_CLASS",
    "LandcoverResult",
    "build_landcover",
    "curve_number",
    "sealed_fraction_10m",
]

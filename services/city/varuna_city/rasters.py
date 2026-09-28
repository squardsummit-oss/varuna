"""DEM mosaic, reprojection and hillshade (SPEC.md 10.1 step 1, task P1.2).

Reads the Copernicus GLO-30 tiles from ``city/cache/dem/`` - never over the network, never
through ``/vsicurl/`` (ADR-0006) - mosaics them, reprojects to the city's metric CRS with
bilinear resampling onto the snapped 30 m grid, and writes ``city/<city>/dem.tif``
(float32, nodata -9999). The hillshade PNG is what the onboarding wizard stacks first and
what the map draws under the water.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import structlog
from PIL import Image
from rasterio.merge import merge
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject
from varuna_schemas.models.city import CityConfig

from varuna_city.cache import cached_tile
from varuna_city.config import CityGrid, city_grid, city_out_dir

log = structlog.get_logger(__name__)

NODATA = -9999.0
"""Nodata value written into every city raster."""

_TILE_BUFFER_DEG = 0.05
"""Degrees of margin kept around the AOI when cropping the mosaic, so bilinear has neighbours."""

# Plausible elevation window for an Indian coastal AOI; anything outside is a broken read.
ELEV_MIN_M = -50.0
ELEV_MAX_M = 400.0


@dataclass(frozen=True, slots=True)
class DemResult:
    """What :func:`build_dem` produced."""

    dem: np.ndarray
    """float32 elevations on the city grid, nodata as NaN."""
    grid: CityGrid
    path: Path
    stage_ms: float
    min_m: float
    max_m: float
    mean_m: float
    nodata_cells: int

    @property
    def transform(self) -> Affine:
        """Convenience: the grid's affine transform."""
        return self.grid.transform


def expected_grid_shape(config: CityConfig) -> tuple[int, int]:
    """Rough ``(height, width)`` from the bbox in degrees - the sanity check for the projection.

    Mumbai's MUM-CENTRAL comes out at about 517 x 316 cells at 30 m (SPEC.md 3.3).
    """
    min_lon, min_lat, max_lon, max_lat = config.bbox.as_tuple()
    mid_lat = (min_lat + max_lat) / 2.0
    m_per_deg_lat = 110_574.0
    m_per_deg_lon = 111_320.0 * float(np.cos(np.deg2rad(mid_lat)))
    width = round((max_lon - min_lon) * m_per_deg_lon / config.grid_m)
    height = round((max_lat - min_lat) * m_per_deg_lat / config.grid_m)
    return (height, width)


def check_grid_shape(config: CityConfig, grid: CityGrid, *, tol_cells: int | None = None) -> None:
    """Log the grid against :func:`expected_grid_shape` and raise if it is off by more than ``tol``.

    :func:`expected_grid_shape` measures the bbox on a flat earth, but the grid is the
    *bounding rectangle* of the bbox projected into a UTM zone, snapped outward to whole
    cells. Mumbai sits about 2.1 degrees west of the 43N central meridian, so the graticule
    is rotated by roughly that convergence angle and the bounding rectangle grows by about
    2 % on each side (Mumbai: 323 x 522 cells against a flat-earth 316 x 516). The default
    tolerance is therefore 3 % of the larger dimension, floor four cells - still an order of
    magnitude tighter than the error a wrong CRS or a wrong ``grid_m`` would produce.
    """
    exp_h, exp_w = expected_grid_shape(config)
    if tol_cells is None:
        tol_cells = max(4, round(0.03 * max(exp_h, exp_w)))
    d_h, d_w = abs(grid.height - exp_h), abs(grid.width - exp_w)
    log.info(
        "city.grid_shape_check",
        city=config.id,
        expected=f"{exp_w} x {exp_h}",
        actual=f"{grid.width} x {grid.height}",
        delta_cells=f"{d_w} x {d_h}",
        tol_cells=tol_cells,
        ok=d_h <= tol_cells and d_w <= tol_cells,
    )
    if d_h > tol_cells or d_w > tol_cells:
        msg = (
            f"{config.aoi_id} grid is {grid.width} x {grid.height} cells at {grid.res:g} m but "
            f"the bbox implies about {exp_w} x {exp_h} (tolerance {tol_cells} cells). "
            "Check bbox, crs and grid_m in the city config."
        )
        raise ValueError(msg)


def build_dem(config: CityConfig, *, out_dir: Path | None = None) -> DemResult:
    """Mosaic, reproject and write the city DEM.

    Args:
        config: the loaded city config; its ``dem_tiles`` must be in ``city/cache/dem/``.
        out_dir: override the output folder (defaults to ``city/<city>/``).

    Returns:
        A :class:`DemResult` with the array on the city grid and its provenance.
    """
    started = time.perf_counter()
    if not config.dem_tiles:
        msg = f"city config {config.id!r} lists no dem_tiles"
        raise ValueError(msg)

    grid = city_grid(config)
    check_grid_shape(config, grid)

    paths = [cached_tile("dem", name) for name in config.dem_tiles]
    min_lon, min_lat, max_lon, max_lat = config.bbox.as_tuple()
    crop = (
        min_lon - _TILE_BUFFER_DEG,
        min_lat - _TILE_BUFFER_DEG,
        max_lon + _TILE_BUFFER_DEG,
        max_lat + _TILE_BUFFER_DEG,
    )

    datasets = [rasterio.open(path) for path in paths]
    try:
        mosaic, src_transform = merge(datasets, bounds=crop)
        src_crs = datasets[0].crs
        src_nodata = datasets[0].nodata
    finally:
        for dataset in datasets:
            dataset.close()

    source = mosaic[0].astype("float32")
    if src_nodata is not None:
        source = np.where(source == np.float32(src_nodata), np.float32(np.nan), source)

    dem = np.full(grid.shape, np.nan, dtype="float32")
    reproject(
        source=source,
        destination=dem,
        src_transform=src_transform,
        src_crs=src_crs,
        src_nodata=np.nan,
        dst_transform=grid.transform,
        dst_crs=grid.crs,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
        num_threads=4,
    )

    valid = np.isfinite(dem)
    if not valid.any():
        msg = (
            f"The reprojected DEM for {config.aoi_id} is entirely nodata. "
            f"Do the tiles {config.dem_tiles} cover the bbox {config.bbox.as_tuple()}?"
        )
        raise ValueError(msg)
    lo, hi = float(dem[valid].min()), float(dem[valid].max())
    if lo < ELEV_MIN_M or hi > ELEV_MAX_M:
        msg = (
            f"{config.aoi_id} elevations {lo:.1f} to {hi:.1f} m fall outside the plausible "
            f"[{ELEV_MIN_M:.0f}, {ELEV_MAX_M:.0f}] m window; the mosaic or the CRS is wrong."
        )
        raise ValueError(msg)

    out = Path(out_dir) if out_dir is not None else city_out_dir(config)
    out.mkdir(parents=True, exist_ok=True)
    dem_path = out / "dem.tif"
    write_grid_raster(dem, grid, dem_path)

    stage_ms = (time.perf_counter() - started) * 1000.0
    result = DemResult(
        dem=dem,
        grid=grid,
        path=dem_path,
        stage_ms=stage_ms,
        min_m=lo,
        max_m=hi,
        mean_m=float(dem[valid].mean()),
        nodata_cells=int((~valid).sum()),
    )
    log.info(
        "city.dem_built",
        city=config.id,
        shape=f"{grid.width} x {grid.height}",
        crs=grid.crs,
        res_m=grid.res,
        min_m=round(lo, 1),
        max_m=round(hi, 1),
        nodata_cells=result.nodata_cells,
        path=str(dem_path),
        stage_ms=round(stage_ms, 1),
    )
    return result


def write_grid_raster(
    array: np.ndarray,
    grid: CityGrid,
    path: Path,
    *,
    dtype: str = "float32",
    nodata: float = NODATA,
) -> Path:
    """Write one band on the city grid as a tiled, deflated GeoTIFF. NaN becomes ``nodata``."""
    if array.shape != grid.shape:
        msg = f"array shape {array.shape} does not match the city grid {grid.shape}"
        raise ValueError(msg)
    data = np.asarray(array)
    if np.issubdtype(data.dtype, np.floating):
        data = np.where(np.isfinite(data), data, nodata)
    data = data.astype(dtype, copy=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=grid.height,
        width=grid.width,
        count=1,
        dtype=dtype,
        crs=grid.crs,
        transform=grid.transform,
        nodata=nodata,
        tiled=True,
        blockxsize=256,
        blockysize=256,
        compress="deflate",
        predictor=2 if dtype.startswith("float") else 1,
    ) as dst:
        dst.write(data, 1)
    return path


def hillshade(
    dem: np.ndarray,
    *,
    res: float = 30.0,
    azimuth_deg: float = 315.0,
    altitude_deg: float = 45.0,
    z_factor: float = 2.0,
) -> np.ndarray:
    """Standard Horn hillshade in 0..1. NaN cells come back as 0."""
    filled = np.where(
        np.isfinite(dem), dem, np.nanmin(dem[np.isfinite(dem)]) if np.isfinite(dem).any() else 0.0
    )
    dy, dx = np.gradient(filled.astype("float64") * z_factor, res, res)
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    az = np.deg2rad(360.0 - azimuth_deg + 90.0)
    alt = np.deg2rad(altitude_deg)
    shaded = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    shaded = np.clip(shaded, 0.0, 1.0)
    return np.where(np.isfinite(dem), shaded, 0.0)


def hillshade_png(dem: np.ndarray, out: Path, *, res: float = 30.0, **kwargs: float) -> Path:
    """Write the hillshade as an 8-bit greyscale PNG for the wizard and the map."""
    shaded = hillshade(dem, res=res, **kwargs)
    image = Image.fromarray((shaded * 255.0).round().astype("uint8"), mode="L")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, format="PNG", optimize=True)
    log.info("city.hillshade_written", path=str(out), shape=f"{dem.shape[1]} x {dem.shape[0]}")
    return out


__all__ = [
    "ELEV_MAX_M",
    "ELEV_MIN_M",
    "NODATA",
    "DemResult",
    "build_dem",
    "check_grid_shape",
    "expected_grid_shape",
    "hillshade",
    "hillshade_png",
    "write_grid_raster",
]

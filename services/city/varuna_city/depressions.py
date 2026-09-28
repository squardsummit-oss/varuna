"""Depression analysis for the conditioned DEM (SPEC.md 10.1 step 4, task P1.5).

Two public entry points:

* :func:`fill_depressions` - a Wang & Liu priority-flood fill. pyflwdir does it in compiled
  code when available; a pure-Python heap implementation is the fallback so the module never
  depends on an optional binary.
* :func:`find_depressions` - the remaining pits of the conditioned DEM as a GeoDataFrame with
  depth, area, volume and the bottom point. These are the hotspot candidates the Phase 1
  validation report compares against the chronic-spot register.
"""

from __future__ import annotations

import heapq
import time
from typing import Any

import geopandas as gpd
import numpy as np
import structlog
from numpy.typing import NDArray
from rasterio.transform import xy
from scipy import ndimage
from shapely.geometry import Point

log = structlog.get_logger(__name__)

#: Fill depths below this (metres) are numerical noise, not a depression.
DEPTH_TOL_M = 1e-3

_NEIGHBOURS_8 = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


def _fill_priority_flood(dem: NDArray[np.float64]) -> NDArray[np.float64]:
    """Pure-Python Wang & Liu (2006) priority flood. Deterministic, no optional deps."""
    ny, nx = dem.shape
    filled = np.array(dem, dtype=np.float64, copy=True)
    valid = np.isfinite(filled)
    done = ~valid
    heap: list[tuple[float, int, int]] = []

    for i in range(ny):
        for j in range(nx):
            if not valid[i, j] or done[i, j]:
                continue
            on_edge = i == 0 or j == 0 or i == ny - 1 or j == nx - 1
            if not on_edge:
                # a cell touching no-data is also an outlet
                on_edge = any(
                    not valid[i + di, j + dj]
                    for di, dj in _NEIGHBOURS_8
                    if 0 <= i + di < ny and 0 <= j + dj < nx
                )
            if on_edge:
                heapq.heappush(heap, (float(filled[i, j]), i, j))
                done[i, j] = True

    while heap:
        z, i, j = heapq.heappop(heap)
        for di, dj in _NEIGHBOURS_8:
            ni, nj = i + di, j + dj
            if not (0 <= ni < ny and 0 <= nj < nx) or done[ni, nj]:
                continue
            zn = float(filled[ni, nj])
            if zn < z:
                filled[ni, nj] = z
                zn = z
            done[ni, nj] = True
            heapq.heappush(heap, (zn, ni, nj))
    return filled


def fill_depressions(
    dem: NDArray[np.floating[Any]], *, use_pyflwdir: bool = True
) -> NDArray[np.float64]:
    """Return the depression-filled DEM (same shape, no-data preserved as NaN)."""
    arr = np.asarray(dem, dtype=np.float64)
    if use_pyflwdir:
        try:
            from pyflwdir import dem as _pfd

            nodata = -9999.0
            work = np.where(np.isfinite(arr), arr, nodata)
            out = _pfd.fill_depressions(work, nodata=nodata, connectivity=8)
            filled = np.asarray(out[0] if isinstance(out, tuple) else out, dtype=np.float64)
            filled = np.where(np.isfinite(arr), filled, np.nan)
            # pyflwdir may leave pits where it sets an outlet; never return below the input
            return np.fmax(filled, arr)
        except Exception as exc:  # pragma: no cover - exercised only when pyflwdir misbehaves
            log.warning("fill_depressions.pyflwdir_failed", error=str(exc))
    return _fill_priority_flood(arr)


def label_pits(
    dem: NDArray[np.floating[Any]],
    *,
    use_pyflwdir: bool = True,
) -> tuple[NDArray[np.int32], NDArray[np.float64]]:
    """Label connected depressions. Returns (labels, fill depth in metres)."""
    arr = np.asarray(dem, dtype=np.float64)
    depth = fill_depressions(arr, use_pyflwdir=use_pyflwdir) - arr
    depth = np.where(np.isfinite(depth), depth, 0.0)
    mask = depth > DEPTH_TOL_M
    labels, _ = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    return labels.astype(np.int32), depth


def find_depressions(
    conditioned_dem: NDArray[np.floating[Any]],
    transform: Any,
    crs: Any,
    min_area_m2: float = 900.0,
    *,
    use_pyflwdir: bool = True,
) -> gpd.GeoDataFrame:
    """Remaining pits of a conditioned DEM, ranked by depth x area (hotspot candidates).

    Columns: ``depression_id``, ``rank``, ``depth_m`` (deepest fill depth), ``area_m2``,
    ``volume_m3``, ``cells``, ``score`` (depth x area), ``bottom_z_m``; geometry is the
    bottom point (cell centre of the lowest cell), in ``crs``.
    """
    t0 = time.perf_counter()
    arr = np.asarray(conditioned_dem, dtype=np.float64)
    cell_area = abs(transform.a) * abs(transform.e)
    labels, depth = label_pits(arr, use_pyflwdir=use_pyflwdir)
    n_labels = int(labels.max())

    records: list[dict[str, Any]] = []
    if n_labels:
        index = np.arange(1, n_labels + 1)
        counts = ndimage.sum_labels(np.ones_like(depth), labels, index=index)
        depth_sum = ndimage.sum_labels(depth, labels, index=index)
        depth_max = ndimage.maximum(depth, labels, index=index)
        bottom_flat = ndimage.minimum_position(
            np.where(labels > 0, arr, np.inf), labels, index=index
        )
        for k, lab in enumerate(index):
            area = float(counts[k]) * cell_area
            if area < min_area_m2:
                continue
            row, col = (int(v) for v in bottom_flat[k])
            x, y = xy(transform, row, col, offset="center")
            records.append(
                {
                    "depression_id": f"DEP-{int(lab):05d}",
                    "depth_m": round(float(depth_max[k]), 4),
                    "area_m2": round(area, 2),
                    "volume_m3": round(float(depth_sum[k]) * cell_area, 2),
                    "cells": int(counts[k]),
                    "bottom_z_m": round(float(arr[row, col]), 3),
                    "bottom_row": row,
                    "bottom_col": col,
                    "geometry": Point(x, y),
                }
            )

    columns = [
        "depression_id",
        "depth_m",
        "area_m2",
        "volume_m3",
        "cells",
        "bottom_z_m",
        "bottom_row",
        "bottom_col",
        "geometry",
    ]
    if records:
        gdf = gpd.GeoDataFrame(records, columns=columns, geometry="geometry", crs=crs)
        gdf["score"] = gdf["depth_m"] * gdf["area_m2"]
        gdf = gdf.sort_values("score", ascending=False, kind="mergesort").reset_index(drop=True)
    else:
        empty = {c: [] for c in [*columns, "score"]}
        gdf = gpd.GeoDataFrame(empty, geometry=gpd.GeoSeries([], crs=crs), crs=crs)
    gdf.insert(1, "rank", np.arange(1, len(gdf) + 1, dtype=int))

    log.info(
        "find_depressions.done",
        pits=len(gdf),
        min_area_m2=min_area_m2,
        stage_ms=round((time.perf_counter() - t0) * 1000, 1),
    )
    return gdf

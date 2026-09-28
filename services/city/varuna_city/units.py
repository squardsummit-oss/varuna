"""P1.7 — surface units: the small catchments that feed the inferred drain inlets.

SPEC.md 10.1 step 6: "watersheds draining to each inlet node (D8 from the conditioned
DEM) capped to 0.5-2 ha; fallback 150 m hexagons. Attributes: area, imperviousness, CN,
mean n, depression depth, segment_id, cells."

How the watershed derivation works:

1. **D8 receivers** - every cell points at its steepest downhill neighbour (distance
   weighted, so a diagonal step is compared over ``res*sqrt(2)``).
2. **Pointer jumping** - each inlet cell is made its own receiver, then ``recv = recv[recv]``
   is iterated until it stops changing, so every cell knows the terminal it drains to in
   ``log2(n)`` vectorised passes rather than a Python walk per cell.
3. **Nearest fill** - cells that reach no inlet (they leave the domain or sit in a pit
   without an inlet) are attached to the nearest labelled unit, so the units tile the AOI.
4. **Cap** - units below ``min_area_m2`` are merged into the neighbour they share the most
   boundary with; units above ``max_area_m2`` are split into equal row-major chunks. Both
   passes are deterministic, so two runs of ``make city`` produce identical units.

When the derivation fails or is degenerate (no inlets, a flat or empty DEM, nothing
labelled), :func:`hex_units` produces the documented fallback: a 150 m hexagon grid over
the AOI, clipped to it. The ``method`` column always says which path produced the row.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable, Mapping
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import structlog
from numpy.typing import NDArray
from rasterio.features import shapes as raster_shapes
from rasterio.transform import Affine, array_bounds
from shapely.geometry import Polygon, box, shape
from shapely.ops import unary_union

log = structlog.get_logger(__name__)

MIN_UNIT_AREA_M2 = 5_000.0
"""0.5 ha - the floor from SPEC.md 10.1 step 6."""

MAX_UNIT_AREA_M2 = 20_000.0
"""2 ha - the ceiling from SPEC.md 10.1 step 6."""

HEX_ACROSS_FLATS_M = 150.0
"""Fallback hexagon width across flats (about 1.95 ha, just inside the 2 ha cap)."""

MERGE_PASSES = 6
"""How many merge sweeps to run before accepting whatever units remain."""

MAX_FALLBACK_CELLS_PER_AXIS = 20_000
"""Guard on a grid inferred from an extent (600 km at 30 m): no city AOI is bigger."""

UNIT_COLUMNS = (
    "unit_id",
    "inlet_node_id",
    "segment_id",
    "area_m2",
    "n_cells",
    "imperviousness",
    "cn",
    "manning_n",
    "depression_depth_m",
    "method",
    "cells",
    "geometry",
)

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


class DegenerateUnitsError(RuntimeError):
    """The D8 derivation produced nothing usable; the caller falls back to hexagons."""


def d8_receivers(dem: NDArray[np.floating], res: float) -> NDArray[np.int64]:
    """Flat index of each cell's steepest downhill neighbour (itself when it has none).

    Args:
        dem: conditioned DEM, nodata as ``nan``.
        res: cell size in metres.
    """
    z = np.asarray(dem, dtype=np.float64)
    ny, nx = z.shape
    n = ny * nx
    index = np.arange(n, dtype=np.int64).reshape(ny, nx)

    padded_z = np.full((ny + 2, nx + 2), np.nan, dtype=np.float64)
    padded_z[1:-1, 1:-1] = z
    padded_index = np.full((ny + 2, nx + 2), -1, dtype=np.int64)
    padded_index[1:-1, 1:-1] = index

    best_slope = np.zeros((ny, nx), dtype=np.float64)
    receiver = index.copy()
    for di, dj in _NEIGHBOURS_8:
        neighbour_z = padded_z[1 + di : 1 + di + ny, 1 + dj : 1 + dj + nx]
        neighbour_index = padded_index[1 + di : 1 + di + ny, 1 + dj : 1 + dj + nx]
        distance = res * math.hypot(di, dj)
        slope = (z - neighbour_z) / distance
        better = np.isfinite(slope) & (slope > best_slope) & (neighbour_index >= 0)
        best_slope = np.where(better, slope, best_slope)
        receiver = np.where(better, neighbour_index, receiver)
    return receiver.reshape(-1)


def _terminals(receiver: NDArray[np.int64]) -> NDArray[np.int64]:
    """Pointer-jump ``receiver`` until every cell points at its terminal cell."""
    current = receiver.copy()
    for _ in range(int(math.log2(max(current.size, 2))) + 2):
        nxt = current[current]
        if np.array_equal(nxt, current):
            break
        current = nxt
    return current


def _inlet_xy_ids(inlet_points: Any) -> tuple[list[float], list[float], list[Any]]:
    """``(xs, ys, ids)`` from inlets given as a GeoDataFrame, shapely points or pairs.

    ``None``, an empty list and an empty GeoDataFrame all mean "no inlets" and return three
    empty lists, so callers can test emptiness once instead of guessing at the container.
    """
    if inlet_points is None:
        return [], [], []
    if isinstance(inlet_points, gpd.GeoDataFrame):
        if inlet_points.empty:
            return [], [], []
        geoms = list(inlet_points.geometry)
        id_column = next(
            (c for c in ("node_id", "inlet_id", "id") if c in inlet_points.columns), None
        )
        ids = (
            list(inlet_points[id_column])
            if id_column is not None
            else [f"IN-{i:06d}" for i in range(len(geoms))]
        )
    else:
        geoms = list(inlet_points)
        ids = [f"IN-{i:06d}" for i in range(len(geoms))]

    xs: list[float] = []
    ys: list[float] = []
    kept_ids: list[Any] = []
    for geom, node_id in zip(geoms, ids, strict=True):
        if geom is None or (hasattr(geom, "is_empty") and geom.is_empty):
            continue
        if hasattr(geom, "x") and hasattr(geom, "y"):
            xs.append(float(geom.x))
            ys.append(float(geom.y))
        else:
            x, y = geom
            xs.append(float(x))
            ys.append(float(y))
        kept_ids.append(node_id)
    return xs, ys, kept_ids


def _inlet_cells(
    inlet_points: Any, transform: Affine, shape_hw: tuple[int, int]
) -> tuple[NDArray[np.int64], list[Any]]:
    """Flat cell index per inlet plus the inlet ids, dropping inlets outside the grid."""
    height, width = shape_hw
    xs, ys, ids = _inlet_xy_ids(inlet_points)
    if not xs:
        return np.empty(0, dtype=np.int64), []

    inverse = ~transform
    cols = np.floor(
        np.array([inverse.a * x + inverse.b * y + inverse.c for x, y in zip(xs, ys, strict=True)])
    ).astype(np.int64)
    rows = np.floor(
        np.array([inverse.d * x + inverse.e * y + inverse.f for x, y in zip(xs, ys, strict=True)])
    ).astype(np.int64)
    inside = (rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)
    flat = (rows[inside] * width + cols[inside]).astype(np.int64)
    kept_ids = [ids[i] for i in np.flatnonzero(inside)]

    # Two inlets can land in one 30 m cell; keep the first so labels stay one-to-one.
    seen: dict[int, int] = {}
    unique_flat: list[int] = []
    unique_ids: list[Any] = []
    for position, cell in enumerate(flat.tolist()):
        if cell in seen:
            continue
        seen[cell] = position
        unique_flat.append(cell)
        unique_ids.append(kept_ids[position])
    return np.array(unique_flat, dtype=np.int64), unique_ids


def _adjacency(labels: NDArray[np.int64]) -> dict[int, list[tuple[int, int]]]:
    """``label -> [(neighbour, shared boundary cells)]`` over 4-connected boundaries."""
    pairs: list[tuple[NDArray[np.int64], NDArray[np.int64]]] = [
        (labels[:, :-1].ravel(), labels[:, 1:].ravel()),
        (labels[:-1, :].ravel(), labels[1:, :].ravel()),
    ]
    left = np.concatenate([p[0] for p in pairs])
    right = np.concatenate([p[1] for p in pairs])
    keep = (left >= 0) & (right >= 0) & (left != right)
    left, right = left[keep], right[keep]
    both_a = np.concatenate([left, right])
    both_b = np.concatenate([right, left])
    if both_a.size == 0:
        return {}
    key = both_a * (int(both_b.max()) + 1) + both_b
    unique, counts = np.unique(key, return_counts=True)
    divisor = int(both_b.max()) + 1
    table: dict[int, list[tuple[int, int]]] = {}
    for value, count in zip(unique.tolist(), counts.tolist(), strict=True):
        a, b = divmod(value, divisor)
        table.setdefault(a, []).append((b, count))
    return table


def _merge_small(
    labels: NDArray[np.int64], min_cells: int, *, passes: int = MERGE_PASSES
) -> NDArray[np.int64]:
    """Merge units below ``min_cells`` into the neighbour they share the most edge with.

    A unit only ever merges into a strictly larger one (ties broken by label id), so the
    merges form a forest and can never cycle.
    """
    out = labels
    for _ in range(passes):
        counts = np.bincount(out[out >= 0].ravel())
        small = np.flatnonzero(counts < min_cells)
        small = small[counts[small] > 0]
        if small.size == 0:
            break
        table = _adjacency(out)
        remap = np.arange(counts.size, dtype=np.int64)
        merged = 0
        for label in small.tolist():
            neighbours = table.get(label)
            if not neighbours:
                continue
            candidates = [
                (n, shared) for n, shared in neighbours if (counts[n], -n) > (counts[label], -label)
            ]
            if not candidates:
                continue
            target = max(candidates, key=lambda item: (item[1], -item[0]))[0]
            remap[label] = target
            merged += 1
        if merged == 0:
            break
        for _ in range(int(math.log2(max(remap.size, 2))) + 2):  # path compression
            nxt = remap[remap]
            if np.array_equal(nxt, remap):
                break
            remap = nxt
        out = np.where(out >= 0, remap[np.clip(out, 0, None)], -1)
    return out


def _split_large(labels: NDArray[np.int64], max_cells: int) -> NDArray[np.int64]:
    """Split units above ``max_cells`` into equal row-major chunks (deterministic)."""
    out = labels.reshape(-1).copy()
    counts = np.bincount(out[out >= 0])
    big = np.flatnonzero(counts > max_cells)
    if big.size == 0:
        return labels
    next_label = int(counts.size)
    order = np.argsort(out, kind="stable")
    sorted_labels = out[order]
    starts = np.searchsorted(sorted_labels, big, side="left")
    ends = np.searchsorted(sorted_labels, big, side="right")
    for start, end in zip(starts.tolist(), ends.tolist(), strict=True):
        cells = order[start:end]
        parts = math.ceil(cells.size / max_cells)
        if parts <= 1:
            continue
        for offset, chunk in enumerate(np.array_split(cells, parts)):
            if offset == 0:
                continue
            out[chunk] = next_label
            next_label += 1
    return out.reshape(labels.shape)


def _compact(labels: NDArray[np.int64]) -> NDArray[np.int64]:
    """Renumber labels to ``0..k-1`` in ascending order; ``-1`` stays unlabelled."""
    used = np.unique(labels[labels >= 0])
    lookup = np.full(int(used.max()) + 1 if used.size else 1, -1, dtype=np.int64)
    lookup[used] = np.arange(used.size, dtype=np.int64)
    return np.where(labels >= 0, lookup[np.clip(labels, 0, None)], -1)


def _fill_nearest(labels: NDArray[np.int64]) -> NDArray[np.int64]:
    """Attach unlabelled cells to the nearest labelled unit so the units tile the AOI."""
    unlabelled = labels < 0
    if not unlabelled.any() or unlabelled.all():
        return labels
    from scipy import ndimage

    _, indices = ndimage.distance_transform_edt(unlabelled, return_indices=True)
    return labels[indices[0], indices[1]]


def _polygonise(labels: NDArray[np.int64], transform: Affine, crs: str | None) -> gpd.GeoDataFrame:
    """One polygon per label (parts of a split unit are unioned)."""
    parts: dict[int, list[Polygon]] = {}
    mask = labels >= 0
    for geom, value in raster_shapes(
        labels.astype(np.int32), mask=mask, transform=transform, connectivity=4
    ):
        parts.setdefault(int(value), []).append(shape(geom))
    rows = [
        {"label": label, "geometry": geoms[0] if len(geoms) == 1 else unary_union(geoms)}
        for label, geoms in sorted(parts.items())
    ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


def _cell_stat(
    labels_flat: NDArray[np.int64],
    n_labels: int,
    raster: NDArray[np.floating] | None,
    *,
    how: str = "mean",
) -> NDArray[np.float64]:
    """Per-unit mean (or max) of a city raster; ``nan`` where the raster is missing."""
    if raster is None:
        return np.full(n_labels, np.nan, dtype=np.float64)
    values = np.asarray(raster, dtype=np.float64).reshape(-1)
    if values.size != labels_flat.size:
        raise ValueError(
            f"raster has {values.size} cells but the unit labels have {labels_flat.size}"
        )
    valid = (labels_flat >= 0) & np.isfinite(values)
    out = np.full(n_labels, np.nan, dtype=np.float64)
    if not valid.any():
        return out
    idx = labels_flat[valid]
    if how == "max":
        # A unit's ponding capacity is set by its deepest pit, not by its average dip, so
        # depression depth reduces with max. The accumulator starts at -inf rather than at
        # nan (nan poisons np.maximum.at, which is what made every depth come back nan);
        # a unit whose cells are all flat therefore keeps 0.0, and only a unit with no
        # valid cell at all stays nan.
        peak = np.full(n_labels, -np.inf, dtype=np.float64)
        np.maximum.at(peak, idx, values[valid])
        return np.where(np.isfinite(peak), peak, np.nan)
    totals = np.bincount(idx, weights=values[valid], minlength=n_labels)
    counts = np.bincount(idx, minlength=n_labels)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(counts > 0, totals / np.maximum(counts, 1), np.nan)
    return out


_EMPTY_UNIT_DTYPES: dict[str, str] = {
    "area_m2": "float64",
    "n_cells": "int64",
    "imperviousness": "float64",
    "cn": "float64",
    "manning_n": "float64",
    "depression_depth_m": "float64",
}


def _empty_units(crs: str | None) -> gpd.GeoDataFrame:
    """A well-formed, empty units frame: right columns, right geometry column, right CRS."""
    data: dict[str, Any] = {
        name: pd.Series(dtype=_EMPTY_UNIT_DTYPES.get(name, "object"))
        for name in UNIT_COLUMNS
        if name != "geometry"
    }
    data["geometry"] = gpd.GeoSeries([], dtype="geometry", crs=crs)
    return gpd.GeoDataFrame(data, geometry="geometry", crs=crs)[list(UNIT_COLUMNS)]


def hex_units(
    transform: Affine,
    shape_hw: tuple[int, int],
    *,
    crs: str | None = None,
    across_flats_m: float = HEX_ACROSS_FLATS_M,
    min_area_m2: float = MIN_UNIT_AREA_M2,
    max_area_m2: float = MAX_UNIT_AREA_M2,
    aoi: Any | None = None,
    imperviousness: NDArray[np.floating] | None = None,
    cn: NDArray[np.floating] | None = None,
    manning_n: NDArray[np.floating] | None = None,
    depression_depth: NDArray[np.floating] | None = None,
) -> gpd.GeoDataFrame:
    """The documented fallback: a hexagon grid over the AOI (SPEC.md 10.1 step 6).

    Hexagons are laid out analytically in the city's metric CRS (not in H3's spherical
    cells) so the tiling stays square with the computation grid and is byte-reproducible.
    ``across_flats_m`` is shrunk if a 150 m hexagon would exceed ``max_area_m2``. Edge
    hexagons clipped below ``min_area_m2`` are dropped, so every unit respects the cap.

    The frame carries the same columns as the D8 path, always with a geometry column even
    when no hexagon survives. City rasters, when given, are aggregated onto the hexagons
    exactly as they are onto watersheds, so a fallback city still feeds Flash real CN,
    imperviousness, roughness and ponding depth instead of a column of nans.
    """
    height, width = shape_hw
    left, bottom, right, top = array_bounds(height, width, transform)
    span = float(across_flats_m)
    if (math.sqrt(3.0) / 2.0) * span * span > max_area_m2:
        span = math.sqrt(max_area_m2 / (math.sqrt(3.0) / 2.0))
    radius = span / math.sqrt(3.0)  # circumradius of a pointy-top hexagon
    step_x = span
    step_y = 1.5 * radius

    aoi_geom = aoi if aoi is not None else box(left, bottom, right, top)
    rows: list[dict[str, Any]] = []
    n_rows = math.ceil((top - bottom) / step_y) + 1
    n_cols = math.ceil((right - left) / step_x) + 1
    for row in range(n_rows):
        cy = bottom + row * step_y
        offset = 0.0 if row % 2 == 0 else step_x / 2.0
        for col in range(n_cols):
            cx = left + offset + col * step_x
            hexagon = Polygon(
                [
                    (
                        cx + radius * math.sin(math.pi / 3.0 * k),
                        cy + radius * math.cos(math.pi / 3.0 * k),
                    )
                    for k in range(6)
                ]
            )
            clipped = hexagon.intersection(aoi_geom)
            if clipped.is_empty or clipped.area < min_area_m2:
                continue
            rows.append({"row": row, "col": col, "geometry": clipped})
    if not rows:
        log.warning("units.hex_fallback.empty", across_flats_m=round(span, 1))
        return _empty_units(crs)

    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)
    frame = frame.sort_values(["row", "col"], kind="stable").reset_index(drop=True)
    frame["unit_id"] = [f"U-HEX-{i:06d}" for i in range(len(frame))]
    frame["area_m2"] = frame.geometry.area.astype(float)
    frame["inlet_node_id"] = None
    frame["segment_id"] = None
    frame["method"] = "hex_fallback"

    rasters = {
        "imperviousness": imperviousness,
        "cn": cn,
        "manning_n": manning_n,
        "depression_depth_m": depression_depth,
    }
    if any(raster is not None for raster in rasters.values()):
        labels_flat = _rasterise_units(frame.geometry, transform, (height, width))
        n_labels = len(frame)
        order = np.argsort(labels_flat, kind="stable")
        sorted_labels = labels_flat[order]
        wanted = np.arange(n_labels, dtype=np.int64)
        starts = np.searchsorted(sorted_labels, wanted, side="left")
        ends = np.searchsorted(sorted_labels, wanted, side="right")
        frame["cells"] = [order[start:end] for start, end in zip(starts, ends, strict=True)]
        frame["n_cells"] = (ends - starts).astype(int)
        for name, raster in rasters.items():
            frame[name] = _cell_stat(
                labels_flat,
                n_labels,
                raster,
                how="max" if name == "depression_depth_m" else "mean",
            )
    else:
        frame["n_cells"] = 0
        frame["cells"] = [np.empty(0, dtype=np.int64) for _ in range(len(frame))]
        for column in ("imperviousness", "cn", "manning_n", "depression_depth_m"):
            frame[column] = np.nan

    log.info("units.hex_fallback", units=len(frame), across_flats_m=round(span, 1))
    return gpd.GeoDataFrame(frame[list(UNIT_COLUMNS)], geometry="geometry", crs=crs)


def _rasterise_units(
    geometries: Any, transform: Affine, shape_hw: tuple[int, int]
) -> NDArray[np.int64]:
    """Flat per-cell unit label for a set of unit polygons (``-1`` where none covers it)."""
    from rasterio.features import rasterize

    shapes = [(geom, index) for index, geom in enumerate(geometries)]
    labels = rasterize(
        shapes,
        out_shape=shape_hw,
        transform=transform,
        fill=-1,
        dtype="int32",
        all_touched=False,
    )
    return labels.reshape(-1).astype(np.int64)


def _grid_shape(candidate: Any) -> tuple[int, int] | None:
    """``(height, width)`` of a 2-D raster, or ``None`` when it is missing or degenerate."""
    if candidate is None:
        return None
    array = np.asarray(candidate)
    if array.ndim != 2 or array.size == 0:
        return None
    return (int(array.shape[0]), int(array.shape[1]))


def _shape_covering(
    bounds: tuple[float, float, float, float], transform: Affine
) -> tuple[int, int] | None:
    """Smallest grid anchored at the transform origin that still covers ``bounds``."""
    minx, miny, maxx, maxy = bounds
    inverse = ~transform
    cols = []
    rows = []
    for x, y in ((minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)):
        cols.append(inverse.a * x + inverse.b * y + inverse.c)
        rows.append(inverse.d * x + inverse.e * y + inverse.f)
    width = math.ceil(max(cols))
    height = math.ceil(max(rows))
    if width < 1 or height < 1:
        return None
    limit = MAX_FALLBACK_CELLS_PER_AXIS
    return (min(height, limit), min(width, limit))


def _fallback_shape(
    conditioned_dem: NDArray[np.floating] | None,
    transform: Affine,
    inlet_points: Any,
    rasters: tuple[NDArray[np.floating] | None, ...],
    aoi: Any | None,
) -> tuple[int, int] | None:
    """The grid the hexagon fallback tiles, when the D8 path could not run.

    The DEM is the natural source, but the fallback exists precisely for the case where
    there is no DEM, so fall back in turn to any other city raster, then to the AOI
    polygon, then to the extent of the inlets. ``None`` means nothing said how big the
    city is, and the caller returns an empty units frame rather than an arbitrary one.
    """
    shape = _grid_shape(conditioned_dem)
    if shape is not None:
        return shape
    for raster in rasters:
        shape = _grid_shape(raster)
        if shape is not None:
            return shape
    if aoi is not None and getattr(aoi, "bounds", None):
        shape = _shape_covering(tuple(aoi.bounds), transform)
        if shape is not None:
            return shape
    xs, ys, _ = _inlet_xy_ids(inlet_points)
    if xs:
        return _shape_covering((min(xs), min(ys), max(xs), max(ys)), transform)
    return None


def build_surface_units(
    conditioned_dem: NDArray[np.floating] | None,
    transform: Affine,
    inlet_points: Any,
    segments: gpd.GeoDataFrame | None = None,
    *,
    crs: str | None = None,
    imperviousness: NDArray[np.floating] | None = None,
    cn: NDArray[np.floating] | None = None,
    manning_n: NDArray[np.floating] | None = None,
    depression_depth: NDArray[np.floating] | None = None,
    min_area_m2: float = MIN_UNIT_AREA_M2,
    max_area_m2: float = MAX_UNIT_AREA_M2,
    aoi: Any | None = None,
    rasters: Mapping[str, NDArray[np.floating]] | None = None,
) -> gpd.GeoDataFrame:
    """Build the surface units for one city (P1.7).

    Args:
        conditioned_dem: hydro-conditioned DEM on the city grid (nodata as ``nan``).
        transform: the city grid's affine transform.
        inlet_points: inlet nodes - a GeoDataFrame (``node_id`` used when present) or an
            iterable of points / ``(x, y)`` pairs in the city CRS.
        segments: road segments, used to hang each unit off the nearest street.
        crs: city CRS; taken from ``segments`` when omitted.
        imperviousness, cn, manning_n: city rasters averaged per unit.
        depression_depth: depression depth raster; the unit keeps its deepest cell.
        min_area_m2, max_area_m2: the 0.5-2 ha cap.
        aoi: optional AOI polygon used to clip the hexagon fallback.
        rasters: alternative way to pass the four rasters, keyed by attribute name.

    Returns:
        A GeoDataFrame with :data:`UNIT_COLUMNS`. ``method`` is ``"d8_watershed"`` or
        ``"hex_fallback"``; ``cells`` holds the flat cell indices of each unit.
    """
    started = time.perf_counter()
    if rasters:
        imperviousness = (
            imperviousness if imperviousness is not None else rasters.get("imperviousness")
        )
        cn = cn if cn is not None else rasters.get("cn")
        manning_n = manning_n if manning_n is not None else rasters.get("manning_n")
        depression_depth = (
            depression_depth if depression_depth is not None else rasters.get("depression_depth")
        )
    if crs is None and segments is not None:
        crs = str(segments.crs) if segments.crs is not None else None
    res = abs(float(transform.a))
    min_cells = max(1, round(float(min_area_m2) / (res * res)))
    max_cells = max(min_cells + 1, round(float(max_area_m2) / (res * res)))

    try:
        units = _watershed_units(
            conditioned_dem,
            transform,
            inlet_points,
            crs=crs,
            min_cells=min_cells,
            max_cells=max_cells,
            imperviousness=imperviousness,
            cn=cn,
            manning_n=manning_n,
            depression_depth=depression_depth,
        )
    except DegenerateUnitsError as exc:
        log.warning("units.fallback", reason=str(exc))
        shape_hw = _fallback_shape(
            conditioned_dem,
            transform,
            inlet_points,
            (imperviousness, cn, manning_n, depression_depth),
            aoi,
        )
        if shape_hw is None:
            log.warning("units.fallback.no_extent", reason=str(exc))
            units = _empty_units(crs)
        else:
            units = hex_units(
                transform,
                shape_hw,
                crs=crs,
                min_area_m2=min_area_m2,
                max_area_m2=max_area_m2,
                aoi=aoi,
                imperviousness=imperviousness,
                cn=cn,
                manning_n=manning_n,
                depression_depth=depression_depth,
            )

    if segments is not None and not segments.empty and not units.empty:
        from varuna_city.segments import segments_near

        units["segment_id"] = segments_near(
            segments, list(units.geometry.representative_point()), max_distance_m=250.0
        )
    log.info(
        "units.built",
        units=len(units),
        method=str(units["method"].iloc[0]) if len(units) else "none",
        stage_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return units


def _watershed_units(
    conditioned_dem: NDArray[np.floating] | None,
    transform: Affine,
    inlet_points: Any,
    *,
    crs: str | None,
    min_cells: int,
    max_cells: int,
    imperviousness: NDArray[np.floating] | None,
    cn: NDArray[np.floating] | None,
    manning_n: NDArray[np.floating] | None,
    depression_depth: NDArray[np.floating] | None,
) -> gpd.GeoDataFrame:
    """The D8 path of :func:`build_surface_units`; raises :class:`DegenerateUnitsError`."""
    xs, _, _ = _inlet_xy_ids(inlet_points)
    if not xs:
        # No inlets at all: ``None``, an empty list and an empty GeoDataFrame alike.
        raise DegenerateUnitsError("no inlets given")
    if conditioned_dem is None:
        raise DegenerateUnitsError("no conditioned DEM")
    dem = np.asarray(conditioned_dem, dtype=np.float64)
    if dem.ndim != 2 or dem.size == 0:
        raise DegenerateUnitsError("DEM is not a 2-D grid")
    height, width = dem.shape
    res = abs(float(transform.a))

    inlet_flat, inlet_ids = _inlet_cells(inlet_points, transform, (height, width))
    if inlet_flat.size == 0:
        raise DegenerateUnitsError("no inlet falls inside the city grid")

    receiver = d8_receivers(dem, res)
    receiver[inlet_flat] = inlet_flat  # an inlet swallows whatever reaches it
    terminal = _terminals(receiver)

    inlet_of_cell = np.full(dem.size, -1, dtype=np.int64)
    inlet_of_cell[inlet_flat] = np.arange(inlet_flat.size, dtype=np.int64)
    labels_flat = inlet_of_cell[terminal]
    labels_flat[~np.isfinite(dem).reshape(-1)] = -1
    if (labels_flat >= 0).sum() < min_cells:
        raise DegenerateUnitsError("no cell drains to an inlet")

    labels = _fill_nearest(labels_flat.reshape(height, width))
    inlet_seed = labels.reshape(-1)[inlet_flat]  # which unit each inlet ended up in

    labels = _merge_small(labels, min_cells)
    labels = _split_large(labels, max_cells)
    labels = _compact(labels)

    counts = np.bincount(labels[labels >= 0].ravel())
    if counts.size == 0:
        raise DegenerateUnitsError("every unit vanished during the cap passes")

    frame = _polygonise(labels, transform, crs)
    if frame.empty:
        raise DegenerateUnitsError("polygonisation produced no geometry")

    labels_1d = labels.reshape(-1)
    label_values = frame["label"].to_numpy()
    n_labels = int(counts.size)
    stats = {
        "imperviousness": _cell_stat(labels_1d, n_labels, imperviousness),
        "cn": _cell_stat(labels_1d, n_labels, cn),
        "manning_n": _cell_stat(labels_1d, n_labels, manning_n),
        "depression_depth_m": _cell_stat(labels_1d, n_labels, depression_depth, how="max"),
    }

    # Which inlet each unit drains: the first inlet that survived into that unit.
    inlet_for_label: dict[int, Any] = {}
    for position, seed in enumerate(inlet_seed.tolist()):
        if seed < 0:
            continue
        final = int(labels_1d[inlet_flat[position]])
        inlet_for_label.setdefault(final, inlet_ids[position])

    order = np.argsort(labels_1d, kind="stable")
    sorted_labels = labels_1d[order]
    starts = np.searchsorted(sorted_labels, label_values, side="left")
    ends = np.searchsorted(sorted_labels, label_values, side="right")

    frame["unit_id"] = [f"U-{int(label):06d}" for label in label_values]
    frame["inlet_node_id"] = [inlet_for_label.get(int(label)) for label in label_values]
    frame["segment_id"] = None
    frame["n_cells"] = counts[label_values]
    frame["area_m2"] = (counts[label_values] * res * res).astype(float)
    for name, values in stats.items():
        frame[name] = values[label_values]
    frame["method"] = "d8_watershed"
    frame["cells"] = [order[start:end] for start, end in zip(starts, ends, strict=True)]
    return gpd.GeoDataFrame(frame[list(UNIT_COLUMNS)], geometry="geometry", crs=crs)


def units_to_parquet(units: gpd.GeoDataFrame, path: Any) -> Any:
    """Write units to GeoParquet, turning the ``cells`` arrays into plain lists."""
    out = units.copy()
    if "cells" in out.columns:
        out["cells"] = [np.asarray(cells, dtype=np.int64).tolist() for cells in out["cells"]]
    out.to_parquet(path, index=False)
    return path


def unit_areas(units: gpd.GeoDataFrame) -> NDArray[np.float64]:
    """Geometric areas in m^2 - the check the validation report prints."""
    return np.asarray(units.geometry.area, dtype=np.float64)


def iter_points(values: Iterable[Any]) -> list[Any]:
    """Small helper so callers can pass shapely points or ``(x, y)`` pairs alike."""
    return list(values)


__all__ = [
    "HEX_ACROSS_FLATS_M",
    "MAX_UNIT_AREA_M2",
    "MERGE_PASSES",
    "MIN_UNIT_AREA_M2",
    "UNIT_COLUMNS",
    "DegenerateUnitsError",
    "build_surface_units",
    "d8_receivers",
    "hex_units",
    "unit_areas",
    "units_to_parquet",
]

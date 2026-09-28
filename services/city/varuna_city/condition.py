"""Hydro-conditioning of the city DEM (SPEC.md 10.1 step 4, task P1.5).

A raw 30 m Copernicus DEM does not know that a building blocks water, that a road is a
shallow channel, that a rail embankment has a culvert under it, or that an underpass is a
real sink rather than a data artefact. :func:`condition_dem` applies, in this order:

1. **Burn buildings** ``+5 m`` - footprints become walls the 2D solver cannot cross.
2. **Carve road centrelines** ``-0.15 m`` - one cell wide, so streets route water.
3. **Keep true sinks** - underpasses, subways and the chronic-spot register are passed in
   as points; the depressions holding them are protected from step 5 and stay pits.
4. **Breach culverts and bridges** - each way is lowered to the minimum of its two end
   cells, so an embankment carrying a road over a nullah does not dam the flow.
5. **Breach spurious pits** smaller than ``min_area_m2`` - WhiteboxTools
   ``BreachDepressionsLeastCost`` when its binary is on disk, otherwise a deterministic
   pure-Python least-cost breach that carves a descending channel from each small pit to
   the first lower cell outside it.

With the city's sea mask (:mod:`varuna_city.sea`) three coastline rules join them: a culvert or
bridge end on the sea does not set a breach level (inside step 4); land held at sea level beside
the sea is raised to the median of the land around it (between steps 4 and 5); and the land that
fronts the sea - behind the mangroves, wetland and open water the tide may cover, where there
are any - is raised to the config's coast wall level (after step 5, so no breach cuts back
through it). The closed basins the wall makes are measured and reported, not hidden. The wet land
the wall stands behind is returned as ``intertidal_mask``, which the city build writes as
``intertidal_mask.tif`` so the products never read the tide on a mangrove as a street's depth.

The function is pure: it copies its input, writes nothing, and returns the conditioned DEM
together with a ``changes`` dict that ``city/<city>/REPORT.md`` (P1.10) prints.
"""

from __future__ import annotations

import heapq
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import structlog
from numpy.typing import NDArray
from rasterio.features import rasterize
from rasterio.transform import Affine, rowcol

from varuna_city.depressions import label_pits

log = structlog.get_logger(__name__)

BUILDING_BURN_M = 5.0
"""Metres added to every building footprint cell (SPEC.md 10.1 step 4)."""

ROAD_CARVE_M = 0.15
"""Metres removed along every road centreline, one cell wide."""

MIN_PIT_AREA_M2 = 900.0
"""Pits smaller than this are DEM artefacts and get breached; bigger ones are kept."""

_BREACH_STEP_M = 1e-3
"""Downhill step per cell along a breached channel - enough to beat the fill tolerance."""

_MAX_SEARCH_CELLS = 20_000
"""Guard on the per-pit least-cost search so one pathological pit cannot stall a run."""

_NEIGHBOURS_8: tuple[tuple[int, int], ...] = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


@dataclass(frozen=True, slots=True)
class ConditionedDem:
    """What :func:`condition_dem` produced."""

    dem: NDArray[np.float64]
    """The conditioned elevations, same shape as the input, no-data still NaN."""
    buildings_mask: NDArray[np.bool_]
    roads_mask: NDArray[np.bool_]
    sink_mask: NDArray[np.bool_]
    changes: dict[str, Any] = field(default_factory=dict)
    stage_ms: float = 0.0
    intertidal_mask: NDArray[np.bool_] | None = None
    """The wet land the coast wall stands behind: :func:`intertidal_zone` less the sea itself.

    ``None`` when no wall was built behind an intertidal zone (no sea, no ``coast_wall_m`` or no
    land cover). The Twin keeps these cells as land, so the tide walks onto them twice a day;
    the city build persists them (``intertidal_mask.tif``) so the products can keep that water
    out of what they read as a street's depth."""


# --------------------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------------------


def _geometries(source: Any, crs: Any) -> list[Any]:
    """Shapely geometries from a GeoDataFrame, GeoSeries, iterable or ``None``.

    A GeoDataFrame/GeoSeries carrying a CRS different from ``crs`` is reprojected first, so
    callers may hand over layers straight from :mod:`varuna_city.osm`.
    """
    if source is None:
        return []
    geometry = getattr(source, "geometry", None)
    if geometry is not None:
        if len(source) == 0:
            return []
        src_crs = getattr(source, "crs", None)
        if src_crs is not None and crs is not None and str(src_crs) != str(crs):
            source = source.to_crs(crs)
            geometry = source.geometry
        return [g for g in geometry if g is not None and not g.is_empty]
    return [g for g in source if g is not None and not getattr(g, "is_empty", False)]


def rasterize_mask(
    geometries: Any,
    transform: Affine,
    shape: tuple[int, int],
    *,
    crs: Any = None,
    all_touched: bool = False,
) -> NDArray[np.bool_]:
    """Boolean mask of the cells covered by ``geometries``.

    ``all_touched=False`` keeps a line one cell wide, which is what the road carving and the
    culvert breaching want.
    """
    geoms = _geometries(geometries, crs)
    if not geoms:
        return np.zeros(shape, dtype=bool)
    burned = rasterize(
        ((g, 1) for g in geoms),
        out_shape=shape,
        transform=transform,
        fill=0,
        all_touched=all_touched,
        dtype="uint8",
    )
    return burned.astype(bool)


def _point_cells(
    points: Any,
    transform: Affine,
    shape: tuple[int, int],
    *,
    crs: Any = None,
) -> list[tuple[int, int]]:
    """``(row, col)`` of every point that falls inside the grid."""
    cells: list[tuple[int, int]] = []
    height, width = shape
    for geom in _geometries(points, crs):
        rep = geom if geom.geom_type == "Point" else geom.representative_point()
        row, col = rowcol(transform, rep.x, rep.y)
        row, col = int(row), int(col)
        if 0 <= row < height and 0 <= col < width:
            cells.append((row, col))
    return cells


# --------------------------------------------------------------------------------------
# the individual conditioning steps
# --------------------------------------------------------------------------------------


def burn_buildings(
    dem: NDArray[np.float64],
    mask: NDArray[np.bool_],
    *,
    height_m: float = BUILDING_BURN_M,
) -> NDArray[np.float64]:
    """Raise building cells by ``height_m`` (in place on a copy of ``dem``)."""
    out = np.array(dem, dtype=np.float64, copy=True)
    out[mask] += height_m
    return out


def carve_roads(
    dem: NDArray[np.float64],
    mask: NDArray[np.bool_],
    *,
    depth_m: float = ROAD_CARVE_M,
) -> NDArray[np.float64]:
    """Lower road-centreline cells by ``depth_m``."""
    out = np.array(dem, dtype=np.float64, copy=True)
    out[mask] -= depth_m
    return out


def breach_culverts(
    dem: NDArray[np.float64],
    transform: Affine,
    ways: Any,
    *,
    crs: Any = None,
    sea: NDArray[np.bool_] | None = None,
) -> tuple[NDArray[np.float64], dict[str, int]]:
    """Lower each culvert/bridge way to the minimum elevation of its two end cells.

    An OSM ``tunnel=culvert`` or ``bridge=yes`` way crosses an embankment that the DEM sees
    as a solid dam. Carving the way down to its lower end lets the flow through without
    inventing a channel anywhere else.

    **An end on the sea is not an end of an embankment** (``sea``, the city's sea mask). A bridge
    that runs out over the water - the Trans Harbour Link's viaduct leaving Sewri, a creek
    bridge whose way stops mid-span - has its lower "end" at the DSM's flattened 0 m, and taking
    that as the target floored the whole landward length of the way to sea level: on Mumbai 143
    of the 163 land cells the conditioned DEM held at or below 0 m while the raw DEM had them
    above 0.5 m sat on culvert or bridge lines, and the Sewri port flooded 1.2-1.4 m from the
    tide alone. So ends on sea cells are ignored, and a way with no end on land is left alone:
    the water it crosses is already open to the flow.
    """
    out = np.array(dem, dtype=np.float64, copy=True)
    height, width = out.shape
    geoms = _geometries(ways, crs)
    sea_mask = None if sea is None else np.asarray(sea, dtype=bool)
    breached_ways = 0
    breached_cells = 0
    sea_ends = 0
    all_sea = 0

    for geom in geoms:
        parts = list(geom.geoms) if geom.geom_type.startswith("Multi") else [geom]
        for part in parts:
            coords = list(getattr(part, "coords", []))
            if len(coords) < 2:
                continue
            ends: list[float] = []
            ends_on_sea = 0
            for x, y in (coords[0], coords[-1]):
                row, col = rowcol(transform, x, y)
                row, col = int(row), int(col)
                if 0 <= row < height and 0 <= col < width and np.isfinite(out[row, col]):
                    if sea_mask is not None and sea_mask[row, col]:
                        ends_on_sea += 1
                        continue
                    ends.append(float(out[row, col]))
            sea_ends += ends_on_sea
            if not ends:
                all_sea += int(ends_on_sea > 0)
                continue
            target = min(ends)
            line_mask = rasterize_mask([part], transform, out.shape, crs=None, all_touched=False)
            if not line_mask.any():
                continue
            affected = line_mask & np.isfinite(out) & (out > target)
            breached_cells += int(affected.sum())
            out[affected] = target
            breached_ways += 1

    stats = {"culvert_ways_breached": breached_ways, "culvert_cells_breached": breached_cells}
    if sea_mask is not None:
        stats["culvert_ends_on_sea_ignored"] = sea_ends
        stats["culvert_ways_with_no_land_end"] = all_sea
    return out, stats


# --------------------------------------------------------------------------------------
# the coastline: flattened land and the shore ring
# --------------------------------------------------------------------------------------

FLATTENED_REPAIR_RADIUS_M = 150.0
"""A flattened land cell takes the median of the unflattened land within this distance."""


def repair_flattened_land(
    dem: NDArray[np.float64],
    sea: NDArray[np.bool_],
    landcover: NDArray[Any],
    transform: Affine,
    *,
    buildings: NDArray[np.bool_] | None = None,
    protect: NDArray[np.bool_] | None = None,
    radius_m: float = FLATTENED_REPAIR_RADIUS_M,
    flat_m: float = 0.0,
) -> tuple[NDArray[np.float64], dict[str, Any]]:
    """Raise land held at sea level beside the sea to the median of the land around it.

    A cell is *flattened land* when it is not sea, its WorldCover class is not water, wetland or
    mangrove (:data:`varuna_city.sea.WET_CLASSES`), it stands at or below ``flat_m`` and it is
    8-connected to the sea through other such cells. The sea mask is read off the raw DEM, so
    these cells are the DSM's water flattening spilling onto land and whatever the earlier
    conditioning steps pulled down to 0 m. Left alone they are sea-level land touching the sea,
    and the tide walks onto them.

    Each takes the median of the land within ``radius_m`` that is neither sea, nor flattened,
    nor a building (a burned footprint is 5 m of wall, not ground). A cell with no such land in
    reach is left as it is and counted. ASSUMPTION: the median of the neighbourhood stands in
    for a ground level the DSM does not have.

    ``protect`` cells (underpasses, the register) are never raised.
    """
    from varuna_city.sea import WET_CLASSES

    out = np.array(dem, dtype=np.float64, copy=True)
    sea_mask = np.asarray(sea, dtype=bool)
    finite = np.isfinite(out)
    zf = np.where(finite, out, np.inf)
    classes = np.asarray(landcover)
    candidate = ~sea_mask & finite & (zf <= flat_m) & ~np.isin(classes, WET_CLASSES)
    if protect is not None:
        candidate &= ~np.asarray(protect, dtype=bool)
    stats: dict[str, Any] = {
        "flattened_cells": 0,
        "flattened_repaired": 0,
        "flattened_unrepaired": 0,
        "flattened_repair_radius_m": radius_m,
    }
    if not candidate.any() or not sea_mask.any():
        return out, stats

    from scipy import ndimage

    labels, _ = ndimage.label(candidate | sea_mask, structure=np.ones((3, 3), dtype=bool))
    touching = np.unique(labels[sea_mask])
    flattened = candidate & np.isin(labels, touching[touching > 0])
    stats["flattened_cells"] = int(flattened.sum())
    if not flattened.any():
        return out, stats

    donor = ~sea_mask & finite & ~candidate & (zf > flat_m)
    if buildings is not None:
        donor &= ~np.asarray(buildings, dtype=bool)
    res = abs(float(transform.a))
    reach = max(int(radius_m // res), 1)
    offsets = [
        (dr, dc)
        for dr in range(-reach, reach + 1)
        for dc in range(-reach, reach + 1)
        if (dr * dr + dc * dc) * res * res <= radius_m * radius_m
    ]
    height, width = out.shape
    source = np.array(out, copy=True)  # medians read the surface before any repair
    rises: list[float] = []
    for row, col in zip(*np.nonzero(flattened), strict=True):
        values = [
            source[row + dr, col + dc]
            for dr, dc in offsets
            if 0 <= row + dr < height and 0 <= col + dc < width and donor[row + dr, col + dc]
        ]
        if not values:
            stats["flattened_unrepaired"] += 1
            continue
        level = float(np.median(values))
        rises.append(level - float(out[row, col]))
        out[row, col] = level
        stats["flattened_repaired"] += 1
    if rises:
        stats["flattened_rise_m"] = {
            "median": round(float(np.median(rises)), 3),
            "max": round(float(np.max(rises)), 3),
        }
    log.info("condition.flattened_land", **stats)
    return out, stats


def intertidal_zone(
    dem: NDArray[np.float64],
    sea: NDArray[np.bool_],
    landcover: NDArray[Any],
    *,
    wall_m: float,
    blocked: NDArray[np.bool_] | None = None,
) -> NDArray[np.bool_]:
    """The sea and the wet land the tide may cover: the side of the coast wall it stands against.

    Wet land is a cell that is not sea and not a building, whose WorldCover class is water,
    herbaceous wetland or mangrove (:data:`varuna_city.sea.WET_CLASSES`, the classes the sea may
    legitimately cover), standing at or below ``wall_m``, and 8-connected to the sea through
    other such cells. A mangrove is intertidal - the tide floods it twice a day - so a wall
    stands behind it, never on it. Wet cells above the wall level, or cut off from the sea by
    dry land, are ordinary land.
    """
    from scipy import ndimage

    from varuna_city.sea import WET_CLASSES

    sea_mask = np.asarray(sea, dtype=bool)
    z = np.asarray(dem, dtype=np.float64)
    finite = np.isfinite(z)
    zf = np.where(finite, z, np.inf)
    wet = ~sea_mask & finite & (zf <= wall_m) & np.isin(np.asarray(landcover), WET_CLASSES)
    if blocked is not None:
        wet &= ~np.asarray(blocked, dtype=bool)
    if not wet.any() or not sea_mask.any():
        return sea_mask.copy()
    labels, _ = ndimage.label(wet | sea_mask, structure=np.ones((3, 3), dtype=bool))
    touching = np.unique(labels[sea_mask])
    return sea_mask | (wet & np.isin(labels, touching[touching > 0]))


def raise_coast_wall(
    dem: NDArray[np.float64],
    sea: NDArray[np.bool_],
    *,
    wall_m: float,
    blocked: NDArray[np.bool_] | None = None,
    landcover: NDArray[Any] | None = None,
    zone: NDArray[np.bool_] | None = None,
) -> tuple[NDArray[np.float64], dict[str, Any]]:
    """Raise the land that fronts the sea to at least ``wall_m``.

    ``zone``, when given, is the :func:`intertidal_zone` the caller already computed from
    ``landcover`` on this same ``dem``; :func:`condition_dem` passes it so the wall and the
    intertidal raster the city build writes are one array rather than two computations.

    ASSUMPTION: a ring of raised cells stands in for the sea walls, promenades and embankments
    the 30 m DSM smooths away; the level is the city config's ``coast_wall_m`` and says where it
    came from. Only the ring moves, and only upwards: the sea still reaches the city through the
    tidal outfalls and the creek, which is where Mumbai's tide actually gets in.

    **Where the ring stands.** With ``landcover`` it is the land 8-adjacent to the
    :func:`intertidal_zone` - behind the mangroves, wetland and open water the tide may cover -
    buildings excluded, so no wet cell is ever raised. Without it, it is the land 8-adjacent to
    the sea itself, the rule this function first shipped with. That rule put the wall on the
    mangroves: on Mumbai 412 of the 736 cells it raised were water, wetland or mangrove, and the
    mangroves behind them became closed basins, 162 of the 212 land cells whose closed depth the
    wall deepened by more than 1 cm. Behind the intertidal zone the wall raises 382 cells, none
    of them wet, and leaves 54 cells in basins it made (:func:`wall_basins`). The tide still
    reaches no dry land at the replay window's +1.236 m or at the +2.218 m crest; it floods 124
    and 346 wet cells, as it does twice a day.
    """
    from varuna_city.sea import shore_ring

    out = np.array(dem, dtype=np.float64, copy=True)
    sea_mask = np.asarray(sea, dtype=bool)
    rule = "landward edge of the intertidal zone"
    if zone is not None:
        zone = np.asarray(zone, dtype=bool)
    elif landcover is not None:
        zone = intertidal_zone(out, sea_mask, landcover, wall_m=wall_m, blocked=blocked)
    else:
        zone = sea_mask
        rule = "shore ring"
    ring = shore_ring(zone, blocked) & np.isfinite(out)
    low = ring & (out < wall_m)
    rise = wall_m - out[low]
    out[low] = wall_m
    stats: dict[str, Any] = {
        "coast_wall_m": wall_m,
        "coast_wall_rule": rule,
        "shore_ring_cells": int(ring.sum()),
        "coast_wall_cells_raised": int(low.sum()),
    }
    if rule != "shore ring":
        stats["intertidal_cells"] = int((zone & ~sea_mask).sum())
    if rise.size:
        stats["coast_wall_rise_m"] = {
            "median": round(float(np.median(rise)), 3),
            "max": round(float(rise.max()), 3),
        }
    log.info("condition.coast_wall", **stats)
    return out, stats


WALL_BASIN_MIN_M = 0.01
"""A land cell is in a basin the wall made when the wall deepened its closed depression by more."""


def _fill_levels(
    dem: NDArray[np.float64], outlets: NDArray[np.bool_], barrier: NDArray[np.bool_]
) -> NDArray[np.float64]:
    """The level water stands at before it can leave each cell, 4-connected like the solver.

    A priority flood from ``outlets`` and the domain edge; ``barrier`` cells and no-data are
    never entered and keep ``inf``. Pure Python on purpose: pyflwdir's fill is not
    bit-reproducible between processes (``pipeline._step_depressions``), and this number is
    printed in REPORT.md. Ties pop in (level, row, column) order, so it never depends on the run.
    """
    height, width = dem.shape
    z = np.where(barrier | ~np.isfinite(dem), np.inf, dem)
    zz = z.tolist()
    level = np.full(dem.shape, np.inf).tolist()
    edge = np.zeros(dem.shape, dtype=bool)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    seeds = (np.asarray(outlets, dtype=bool) | edge) & np.isfinite(z)
    seen = seeds.tolist()
    heap: list[tuple[float, int, int]] = []
    for row, col in zip(*np.nonzero(seeds), strict=True):
        r, c = int(row), int(col)
        level[r][c] = zz[r][c]
        heap.append((zz[r][c], r, c))
    heapq.heapify(heap)
    inf = float("inf")
    while heap:
        lv, r, c = heapq.heappop(heap)
        for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if 0 <= rr < height and 0 <= cc < width and not seen[rr][cc]:
                seen[rr][cc] = True
                v = zz[rr][cc]
                if v == inf:
                    continue
                if v < lv:
                    v = lv
                level[rr][cc] = v
                heapq.heappush(heap, (v, rr, cc))
    return np.asarray(level, dtype=np.float64)


def wall_basins(
    before: NDArray[np.float64],
    after: NDArray[np.float64],
    sea: NDArray[np.bool_],
    *,
    cell_area_m2: float,
    blocked: NDArray[np.bool_] | None = None,
    min_gain_m: float = WALL_BASIN_MIN_M,
) -> dict[str, Any]:
    """The closed basins a coast wall made: land that no longer drains overland to the sea.

    For every land cell (not sea, not a building) the depth of its closed depression - the fill
    level with the sea and the domain edge as outlets, less its ground - is taken ``before`` and
    ``after`` the wall; a cell whose depth grew by more than ``min_gain_m`` is in a basin the wall
    made. Rain that lands there leaves only through the inlets ``drains.py`` places, and at high
    water those are tide-locked. ASSUMPTION: that is how low ground behind a real sea wall
    drains, through its outfalls; it is also where the wall's own inaccuracy shows, so the number
    is reported rather than engineered away.
    """
    from scipy import ndimage

    sea_mask = np.asarray(sea, dtype=bool)
    barrier = (
        np.zeros(sea_mask.shape, dtype=bool) if blocked is None else np.asarray(blocked, dtype=bool)
    )
    z0 = np.asarray(before, dtype=np.float64)
    z1 = np.asarray(after, dtype=np.float64)
    level0 = _fill_levels(z0, sea_mask, barrier)
    level1 = _fill_levels(z1, sea_mask, barrier)
    # Land the flood reaches on both surfaces; a cell walled in by buildings is reached by
    # neither, and has no depression depth to compare.
    land = ~sea_mask & ~barrier & np.isfinite(level0) & np.isfinite(level1)
    gain = np.zeros(z0.shape, dtype=np.float64)
    gain[land] = (level1[land] - z1[land]) - (level0[land] - z0[land])
    inside = gain > min_gain_m
    _, n_basins = ndimage.label(inside, structure=np.ones((3, 3), dtype=bool))
    cells = int(inside.sum())
    return {
        "coast_wall_basin_cells": cells,
        "coast_wall_basin_km2": round(cells * cell_area_m2 / 1e6, 3),
        "coast_wall_basins": int(n_basins),
        "coast_wall_basin_m3": round(float(gain[inside].sum()) * cell_area_m2, 1),
        "coast_wall_basin_max_m": round(float(gain[inside].max()), 2) if cells else 0.0,
        "coast_wall_basin_cells_over_30cm": int((gain > 0.3).sum()),
    }


# --------------------------------------------------------------------------------------
# spurious-pit breaching
# --------------------------------------------------------------------------------------


def _whitebox_exe() -> Path | None:
    """Path to the WhiteboxTools binary if it is already on disk, else ``None``.

    We never let the ``whitebox`` package download it: the demo laptop runs offline and TLS
    is intercepted here (ADR-0006).
    """
    try:
        import whitebox
    except Exception:  # pragma: no cover - whitebox is installed in this workspace
        return None
    base = Path(whitebox.__file__).parent / "WBT"
    for name in ("whitebox_tools.exe", "whitebox_tools"):
        exe = base / name
        if exe.is_file():
            return exe
    return None


def _breach_whitebox(dem: NDArray[np.float64], transform: Affine) -> NDArray[np.float64] | None:
    """Run ``BreachDepressionsLeastCost`` on a temporary GeoTIFF; ``None`` if unavailable."""
    if _whitebox_exe() is None:
        log.info("condition.whitebox_unavailable", reason="binary not downloaded")
        return None
    try:
        import rasterio
        from whitebox import WhiteboxTools

        with tempfile.TemporaryDirectory(prefix="varuna-wbt-") as tmp:
            tmp_dir = Path(tmp)
            src = tmp_dir / "dem.tif"
            dst = tmp_dir / "breached.tif"
            nodata = -9999.0
            data = np.where(np.isfinite(dem), dem, nodata).astype("float32")
            profile = {
                "driver": "GTiff",
                "height": dem.shape[0],
                "width": dem.shape[1],
                "count": 1,
                "dtype": "float32",
                "transform": transform,
                "nodata": nodata,
            }
            with rasterio.open(src, "w", **profile) as handle:
                handle.write(data, 1)
            wbt = WhiteboxTools()
            wbt.verbose = False
            wbt.set_working_dir(str(tmp_dir))
            wbt.breach_depressions_least_cost(str(src), str(dst), dist=100, fill=True)
            with rasterio.open(dst) as handle:
                out = handle.read(1).astype(np.float64)
        return np.where(np.isfinite(dem), np.fmin(out, dem), np.nan)
    except Exception as exc:  # pragma: no cover - only when WBT misbehaves on the laptop
        log.warning("condition.whitebox_failed", error=str(exc))
        return None


def _breach_pit_python(
    dem: NDArray[np.float64],
    pit_cells: set[tuple[int, int]],
    bottom: tuple[int, int],
    neighbours: tuple[tuple[int, int], ...],
) -> int:
    """Carve a least-cost channel from one pit to the first lower cell outside it.

    Dijkstra with "cost = highest ground crossed so far": the cheapest path is the lowest
    saddle out of the pit, which is exactly what a least-cost breach digs through. Returns
    the number of cells lowered (0 when no outlet was found).
    """
    height, width = dem.shape
    z_bottom = float(dem[bottom])
    best: dict[tuple[int, int], float] = {bottom: 0.0}
    parent: dict[tuple[int, int], tuple[int, int]] = {}
    heap: list[tuple[float, int, tuple[int, int]]] = [(0.0, 0, bottom)]
    counter = 1
    visited = 0
    outlet: tuple[int, int] | None = None

    while heap:
        cost, _, cell = heapq.heappop(heap)
        if cost > best.get(cell, np.inf):
            continue
        visited += 1
        if visited > _MAX_SEARCH_CELLS:
            break
        if cell not in pit_cells and dem[cell] < z_bottom - _BREACH_STEP_M:
            outlet = cell
            break
        row, col = cell
        for d_row, d_col in neighbours:
            nb = (row + d_row, col + d_col)
            if not (0 <= nb[0] < height and 0 <= nb[1] < width):
                continue
            z_nb = dem[nb]
            if not np.isfinite(z_nb):
                continue
            nb_cost = max(cost, float(z_nb) - z_bottom)
            if nb_cost < best.get(nb, np.inf):
                best[nb] = nb_cost
                parent[nb] = cell
                heapq.heappush(heap, (nb_cost, counter, nb))
                counter += 1

    if outlet is None:
        return 0

    path: list[tuple[int, int]] = []
    node = outlet
    while node != bottom:
        path.append(node)
        node = parent[node]
    path.reverse()

    lowered = 0
    for step, cell in enumerate(path, start=1):
        target = z_bottom - step * _BREACH_STEP_M
        if dem[cell] > target:
            dem[cell] = target
            lowered += 1
    return lowered


def breach_spurious_pits(
    dem: NDArray[np.float64],
    transform: Affine,
    *,
    min_area_m2: float = MIN_PIT_AREA_M2,
    protect: NDArray[np.bool_] | None = None,
    use_whitebox: bool = True,
    seed: int = 0,
    use_pyflwdir: bool = True,
) -> tuple[NDArray[np.float64], dict[str, Any]]:
    """Breach every depression no larger than ``min_area_m2`` that holds no protected cell.

    Larger depressions and anything under ``protect`` (underpasses, subways, the chronic-spot
    register) are left exactly as they are - they are the flooding we are trying to predict.

    The comparison is deliberately inclusive at the threshold: at 30 m a single cell is exactly
    ``MIN_PIT_AREA_M2``, and a one-cell pit is a DEM artefact rather than a place that floods.
    ``protect`` is what keeps the real one-cell sinks - Andheri and Milan subways among them.
    """
    out = np.array(dem, dtype=np.float64, copy=True)
    cell_area = abs(transform.a) * abs(transform.e)
    labels, _ = label_pits(out, use_pyflwdir=use_pyflwdir)
    n_labels = int(labels.max())
    stats: dict[str, Any] = {
        "pits_before": 0,
        "pits_spurious": 0,
        "pits_protected": 0,
        "pits_breached": 0,
        "breach_cells": 0,
        "breach_method": "none",
        "seed": seed,
    }
    if n_labels == 0:
        return out, stats

    protect_mask = (
        np.zeros(out.shape, dtype=bool) if protect is None else np.asarray(protect, dtype=bool)
    )
    spurious: list[int] = []
    protected_labels: list[int] = []
    kept_large = 0
    kept_protected = 0
    for lab in range(1, n_labels + 1):
        cells = labels == lab
        area = float(cells.sum()) * cell_area
        # `>` and not `>=`. On a 30 m grid one cell is exactly 900 m², so `>=` kept every
        # single-cell pit - the most obviously spurious kind there is, and 47 % of all the
        # depressions in Mumbai. Each one then filled to 60 cm and over in a 3-hour run,
        # putting deep water on hillsides while the real, larger sinks drained through their
        # inlets. The threshold means "a pit no bigger than one cell is a DEM artefact".
        if area > min_area_m2:
            protected_labels.append(lab)
            kept_large += 1
            continue
        if bool((cells & protect_mask).any()):
            protected_labels.append(lab)
            kept_protected += 1
            continue
        spurious.append(lab)
    stats["pits_before"] = n_labels
    stats["pits_spurious"] = len(spurious)
    # Counted apart, because conflating them is what hid the bug above: every pit was reported
    # as "protected" and 4,004 protected pits reads perfectly plausible.
    stats["pits_large"] = kept_large
    stats["pits_protected"] = kept_protected
    if not spurious:
        return out, stats

    breached = _breach_whitebox(out, transform) if use_whitebox else None
    if breached is not None:
        keep = np.zeros(out.shape, dtype=bool)
        for lab in protected_labels:
            keep |= labels == lab
        keep |= protect_mask
        merged = np.where(keep, out, breached)
        stats["breach_method"] = "whitebox_least_cost"
        stats["breach_cells"] = int(np.count_nonzero(merged < out - 1e-9))
        stats["pits_breached"] = len(spurious)
        return merged, stats

    rng = np.random.default_rng(seed)
    order = tuple(_NEIGHBOURS_8[i] for i in rng.permutation(len(_NEIGHBOURS_8)))
    lowered_total = 0
    breached_pits = 0
    for lab in spurious:
        rows, cols = np.nonzero(labels == lab)
        cells = set(zip(rows.tolist(), cols.tolist(), strict=True))
        flat = np.where(labels == lab, out, np.inf)
        bottom_flat = int(np.argmin(flat))
        bottom = (bottom_flat // out.shape[1], bottom_flat % out.shape[1])
        lowered = _breach_pit_python(out, cells, bottom, order)
        if lowered:
            breached_pits += 1
            lowered_total += lowered
    stats["breach_method"] = "python_least_cost"
    stats["breach_cells"] = lowered_total
    stats["pits_breached"] = breached_pits
    return out, stats


# --------------------------------------------------------------------------------------
# the pipeline step
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildingMasks:
    """What :func:`resolve_building_mask` resolved."""

    buildings: NDArray[np.bool_]
    """Footprint cells to burn - and, via roughness, the solver's blocked cells."""
    sinks: NDArray[np.bool_]
    """Sink-flagged points: their pits are protected from breaching."""
    sink_points: int
    """Sink points that fell inside the grid."""
    cleared: int
    """Footprint cells cleared because a sink or register point sits on them."""


def resolve_building_mask(
    buildings: Any,
    transform: Affine,
    shape: tuple[int, int],
    *,
    crs: Any = None,
    sinks: Any = None,
    register: Any = None,
) -> BuildingMasks:
    """The building footprint mask, cleared of the chronic-spot register and the sinks.

    One function because two steps need the same answer: :func:`condition_dem` burns it, and
    :mod:`varuna_city.sea` keeps the sea off it before conditioning runs.

    The sinks are resolved *before* the burn, because a protected sink must not be treated as
    a building. SPEC.md 10.1 step 4 says to "keep underpasses/subways as sinks (OSM
    tunnel/layer<0 + the hotspot register)", and a cell cannot be both a place water is known
    to pool and an impermeable obstacle. Two sourced chronic points - Khar Subway and Parel /
    Bharat Mata Cinema - sit under an OSM building footprint, so burning first raised them
    5 m and, because the building mask is also what roughness turns into the solver's blocked
    mask, left them with no flux at all: the Twin could never put water on two of the ten
    hotspots section 3.3 names. Clearing them from the mask fixes both at once.

    Pit protection is about topography, so it stays on the sink-flagged points. Building
    clearing is about evidence, so it covers the whole register: Parel / Bharat Mata Cinema
    is a sourced chronic point that is not a subway, and it sits on a footprint.
    """
    footprints = rasterize_mask(buildings, transform, shape, crs=crs, all_touched=False)
    sink_cells = _point_cells(sinks, transform, shape, crs=crs)
    sink_mask = np.zeros(shape, dtype=bool)
    for row, col in sink_cells:
        sink_mask[row, col] = True
    no_build_mask = sink_mask.copy()
    for row, col in _point_cells(
        sinks if register is None else register, transform, shape, crs=crs
    ):
        no_build_mask[row, col] = True
    cleared = int((footprints & no_build_mask).sum())
    return BuildingMasks(
        buildings=footprints & ~no_build_mask,
        sinks=sink_mask,
        sink_points=len(sink_cells),
        cleared=cleared,
    )


def condition_dem(
    dem: NDArray[np.floating[Any]],
    transform: Affine,
    crs: Any,
    *,
    buildings: Any = None,
    roads: Any = None,
    culverts: Any = None,
    bridges: Any = None,
    sinks: Any = None,
    register: Any = None,
    seed: int = 0,
    building_burn_m: float = BUILDING_BURN_M,
    road_carve_m: float = ROAD_CARVE_M,
    min_pit_area_m2: float = MIN_PIT_AREA_M2,
    use_whitebox: bool = True,
    use_pyflwdir: bool = True,
    sea: NDArray[np.bool_] | None = None,
    landcover: NDArray[Any] | None = None,
    coast_wall_m: float | None = None,
) -> ConditionedDem:
    """Hydro-condition a city DEM (SPEC.md 10.1 step 4).

    With a ``sea`` mask (:mod:`varuna_city.sea`) three coastline rules join the five steps of
    the module docstring: culvert and bridge ends on the sea do not set a breach level
    (:func:`breach_culverts`); land held at sea level beside the sea is raised to the median of
    the land around it (:func:`repair_flattened_land`, needs ``landcover``); and, when
    ``coast_wall_m`` is given, the land fronting the sea - behind the intertidal zone when
    ``landcover`` is given - is raised to it (:func:`raise_coast_wall`) and the basins that makes
    are counted (:func:`wall_basins`). Without one the output is what it always was. When the wall
    stands behind an intertidal zone, that zone less the sea is returned as ``intertidal_mask``.

    Args:
        dem: elevations on the city grid, no-data as NaN.
        transform: the grid's affine transform (metric CRS, north-up).
        crs: the grid's CRS; vector inputs carrying another CRS are reprojected to it.
        buildings: footprint polygons (GeoDataFrame/GeoSeries/iterable of geometries).
        roads: road centrelines - rasterised one cell wide.
        culverts: ``tunnel=culvert`` ways to breach.
        bridges: ``bridge=yes`` ways to breach (same treatment as culverts).
        sinks: points that must stay pits - OSM underpasses/subways plus the register points
            flagged ``is_sink``. These override the pit-area rule.
        register: every chronic-waterlogging point, sink-flagged or not. These are cleared from
            the building mask but do not override the pit-area rule: being a known flood point
            is evidence that water pools there, which is incompatible with the cell being an
            impermeable obstacle, but it is not on its own evidence of a topographic sink.
            Defaults to ``sinks``.
        seed: tie-breaking seed for the pure-Python least-cost breach; two runs with the
            same seed and inputs produce byte-identical output.

    Returns:
        :class:`ConditionedDem` with the new elevations, the masks the roughness raster and
        the solver need, and a ``changes`` dict for ``REPORT.md``.
    """
    t0 = time.perf_counter()
    work = np.asarray(dem, dtype=np.float64)
    shape = (work.shape[0], work.shape[1])

    roads_mask = rasterize_mask(roads, transform, shape, crs=crs, all_touched=False)
    masks = resolve_building_mask(
        buildings, transform, shape, crs=crs, sinks=sinks, register=register
    )
    buildings_mask, sink_mask = masks.buildings, masks.sinks

    out = burn_buildings(work, buildings_mask, height_m=building_burn_m)
    out = carve_roads(out, roads_mask, depth_m=road_carve_m)

    culvert_geoms = _geometries(culverts, crs) + _geometries(bridges, crs)
    out, culvert_stats = breach_culverts(out, transform, culvert_geoms, crs=None, sea=sea)

    # Before the pit breach, so a pit the repair leaves behind is breached like any other.
    coast_stats: dict[str, Any] = {}
    if sea is not None and landcover is not None:
        out, flat_stats = repair_flattened_land(
            out,
            sea,
            landcover,
            transform,
            buildings=buildings_mask,
            protect=sink_mask,
        )
        coast_stats.update(flat_stats)

    out, pit_stats = breach_spurious_pits(
        out,
        transform,
        min_area_m2=min_pit_area_m2,
        protect=sink_mask,
        use_whitebox=use_whitebox,
        seed=seed,
        use_pyflwdir=use_pyflwdir,
    )

    # After the pit breach, or the breach would carve its way back through the wall.
    intertidal: NDArray[np.bool_] | None = None
    if sea is not None and coast_wall_m is not None:
        unwalled = out
        zone = None
        if landcover is not None:
            zone = intertidal_zone(out, sea, landcover, wall_m=coast_wall_m, blocked=buildings_mask)
            intertidal = zone & ~np.asarray(sea, dtype=bool)
        out, wall_stats = raise_coast_wall(
            out,
            sea,
            wall_m=coast_wall_m,
            blocked=buildings_mask,
            landcover=landcover,
            zone=zone,
        )
        coast_stats.update(wall_stats)
        coast_stats.update(
            wall_basins(
                unwalled,
                out,
                sea,
                blocked=buildings_mask,
                cell_area_m2=abs(float(transform.a) * float(transform.e)),
            )
        )

    stage_ms = round((time.perf_counter() - t0) * 1000, 1)
    changes: dict[str, Any] = {
        "cells_burned": int(buildings_mask.sum()),
        "building_burn_m": building_burn_m,
        "cells_carved": int(roads_mask.sum()),
        "road_carve_m": road_carve_m,
        "sinks_protected": masks.sink_points,
        "sinks_cleared_of_building": masks.cleared,
        "min_pit_area_m2": min_pit_area_m2,
        "seed": seed,
        "stage_ms": stage_ms,
        **culvert_stats,
        **pit_stats,
        **coast_stats,
    }
    log.info("condition_dem.done", **changes)
    return ConditionedDem(
        dem=out,
        buildings_mask=buildings_mask,
        roads_mask=roads_mask,
        sink_mask=sink_mask,
        changes=changes,
        stage_ms=stage_ms,
        intertidal_mask=intertidal,
    )


__all__ = [
    "BUILDING_BURN_M",
    "FLATTENED_REPAIR_RADIUS_M",
    "MIN_PIT_AREA_M2",
    "ROAD_CARVE_M",
    "WALL_BASIN_MIN_M",
    "BuildingMasks",
    "ConditionedDem",
    "breach_culverts",
    "breach_spurious_pits",
    "burn_buildings",
    "carve_roads",
    "condition_dem",
    "intertidal_zone",
    "raise_coast_wall",
    "rasterize_mask",
    "repair_flattened_land",
    "resolve_building_mask",
    "wall_basins",
]

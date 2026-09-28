"""The sea and its tidal creeks on the city grid (wave B coastline).

The Twin imposes the tide on every cell this module calls sea. Before it existed the only sea the
solver knew was the cell under each configured tidal outfall - three cells for Mumbai - so the
19 km2 of Arabian Sea, Mahim bay and the harbour inside the AOI were land to it: rain ponded on
them (829 cells above 5 cm, up to 0.76 m, on the 08:40 cycle) and the tide reached nothing.

**Open sea.** The 8-connected components of

    (ESA WorldCover class 80 AND raw DEM <= 2.0 m)  OR  (raw DEM <= 0.0 m)

that touch the domain edge *and* hold at least one cell at or below 0.0 m. Both halves are needed.
WorldCover's water class alone takes in Powai Lake (624 cells at 33.5 m in Mumbai's north-east),
which the 2.0 m cap removes; the DSM's water flattening alone (Copernicus sets open water to a
flat level at or near 0 m) misses the shallow margins WorldCover sees. The edge-and-at-zero rule
drops inland water that happens to reach the frame, such as a 2-cell pond on Mumbai's south edge
at 0.37-0.41 m. The DEM is the **raw** ``dem.tif``: conditioning carves and breaches, and a
coastline read off a carved surface would follow the carving.

**Tidal creeks.** Config-gated, because the 30 m DSM cannot show where the tide reaches up a
channel. For each OSM waterway named in the city's ``tidal_rivers``, the line's cells with a
1-cell buffer (the channel floor sits beside a line drawn on a bank as often as under it), plus
WorldCover water, at raw DEM <= 2.0 m, in 8-connected components of at least 50 cells that touch
the line. For Mumbai that is the Mithi, which Copernicus holds at flat steps of 0.5-2.0 m for
7.2 km upstream of Mahim causeway; nothing in the open-sea rule reaches it, because a 2.6-3.4 m
ridge (the causeway, Dharavi bridge and mangrove canopy in the DSM) separates it from the bay.

Blocked cells (building footprints, after the chronic-spot register is cleared of them exactly as
:func:`varuna_city.condition.condition_dem` does) are never sea: the solver gives them no flux, so
a boundary level on one would be a level nobody can reach.

Every threshold here is an assumption about open data, recorded in ``sea.json`` next to the
counts it produced, and none of it is a surveyed coastline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import numpy as np
import structlog
from numpy.typing import NDArray
from scipy import ndimage

log = structlog.get_logger(__name__)

SEA_LAND: Final[int] = 0
"""``sea_mask.tif`` code for a cell that is not sea."""
SEA_OPEN: Final[int] = 1
"""``sea_mask.tif`` code for open sea (the bay, the harbour, the Arabian Sea)."""
SEA_CREEK: Final[int] = 2
"""``sea_mask.tif`` code for a tidal creek reached from a named river."""

SEA_MASK_FILE: Final[str] = "sea_mask.tif"
SEA_JSON_FILE: Final[str] = "sea.json"

WATER_CLASS: Final[int] = 80
"""ESA WorldCover "permanent water bodies"."""

WET_CLASSES: Final[tuple[int, ...]] = (80, 90, 95)
"""WorldCover water, herbaceous wetland and mangroves: the classes the sea may legitimately cover.

A cell of any other class held at or below 0 m is land the DSM's water flattening, or a breach,
has pulled down to sea level (:func:`varuna_city.condition.repair_flattened_land`)."""

SEA_Z_MAX_M: Final[float] = 2.0
"""Water higher than this is not the sea. Mumbai's creek steps reach 2.0 m; Powai Lake is 33.5 m."""

FLAT_Z_M: Final[float] = 0.0
"""The level at or below which the DSM's water flattening puts open water."""

CREEK_MIN_CELLS: Final[int] = 50
"""Smallest tidal-creek component kept (4.5 ha); smaller hits are a line crossing a puddle."""

TIDAL_OUTFALL_CELLS: Final[int] = 2
"""An outfall within this many cells (chessboard) of the sea discharges into it."""

TIDAL_OUTFALL_INVERT_M: Final[float] = 0.0
"""A tidal outfall's invert in the DEM frame: mean sea level.

ASSUMPTION, no BMC outfall level is sourced in the repository. At mean sea level the outfall is
submerged for about six of every 12.4 hours, which is the blueprint's own description of the
problem (section 2: "a nullah outfall that is below high-tide level for six hours a day")."""

_S8: Final[NDArray[np.bool_]] = np.ones((3, 3), dtype=bool)

SOURCES: Final[tuple[dict[str, str], ...]] = (
    {
        "name": "Copernicus DEM GLO-30 (raw dem.tif)",
        "url": "https://registry.opendata.aws/copernicus-dem/",
        "used_for": "Cells at or below 0.0 m (the DSM's water flattening) and the 2.0 m cap.",
    },
    {
        "name": "ESA WorldCover 10 m 2021 v200, class 80",
        "url": "https://esa-worldcover.org/en",
        "used_for": "Permanent water, capped at 2.0 m so an inland lake is not sea.",
    },
    {
        "name": "OpenStreetMap waterways (named tidal rivers only)",
        "url": "https://www.openstreetmap.org/copyright",
        "used_for": "Where a tidal creek runs, for the rivers the city config names.",
    },
)


# --------------------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeaConfig:
    """The coastline settings for one city.

    Read from :func:`sea_config_path` (``services/city/configs/sea/<city>.yaml``) until the city
    schema carries a ``sea`` block of its own; see :func:`load_sea_config`.
    """

    tidal_rivers: tuple[str, ...] = ()
    """OSM waterway names whose channel the tide reaches (Mumbai: Mithi River)."""
    coast_wall_m: float | None = None
    """Level the land fronting the sea is raised to, in the DEM frame; ``None`` builds no wall.

    No default: the right level is the city's own sourced high water plus freeboard, and
    borrowing Mumbai's would put Mumbai's tide on every city onboarded after it."""
    tidal_outfall_invert_m: float = TIDAL_OUTFALL_INVERT_M
    tidal_outfall_cells: int = TIDAL_OUTFALL_CELLS
    notes: dict[str, str] = field(default_factory=dict)
    """Free-text provenance per key, copied into ``sea.json``."""
    source: str = "defaults"
    """Where the settings came from: a file path, ``"city config"`` or ``"defaults"``."""

    @classmethod
    def from_mapping(cls, data: dict[str, Any], *, source: str) -> SeaConfig:
        known = {"tidal_rivers", "coast_wall_m", "tidal_outfall_invert_m", "tidal_outfall_cells"}
        unknown = sorted(set(data) - known - {"notes"})
        if unknown:
            msg = f"{source}: unknown sea setting(s) {unknown}; known: {sorted(known)}"
            raise ValueError(msg)
        rivers = data.get("tidal_rivers") or ()
        if isinstance(rivers, str):
            rivers = (rivers,)
        wall = data.get("coast_wall_m")
        cells = int(data.get("tidal_outfall_cells", TIDAL_OUTFALL_CELLS))
        if cells < 0:
            msg = f"{source}: tidal_outfall_cells must be >= 0, got {cells}"
            raise ValueError(msg)
        notes = data.get("notes") or {}
        return cls(
            tidal_rivers=tuple(str(name).strip() for name in rivers if str(name).strip()),
            coast_wall_m=None if wall is None else float(wall),
            tidal_outfall_invert_m=float(
                data.get("tidal_outfall_invert_m", TIDAL_OUTFALL_INVERT_M)
            ),
            tidal_outfall_cells=cells,
            notes={str(k): str(v).strip() for k, v in dict(notes).items()},
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tidal_rivers": list(self.tidal_rivers),
            "coast_wall_m": self.coast_wall_m,
            "tidal_outfall_invert_m": self.tidal_outfall_invert_m,
            "tidal_outfall_cells": self.tidal_outfall_cells,
            "notes": dict(sorted(self.notes.items())),
            "source": self.source,
        }


def sea_config_path(city: str) -> Path:
    """``services/city/configs/sea/<city>.yaml``.

    A sub-folder on purpose: the API lists cities by globbing ``configs/*.yaml``, and a sea file
    beside the city files would appear as a city called ``mumbai.sea``.
    """
    from varuna_schemas.paths import city_config_path

    return city_config_path(city).parent / "sea" / f"{city}.yaml"


def load_sea_config(config: Any) -> SeaConfig:
    """The coastline settings for ``config``'s city.

    A ``sea`` field on the city config wins when the schema carries one; until then the settings
    live in :func:`sea_config_path`. A city with neither gets the open-sea rule, no tidal creeks
    and no coast wall - the rules that need nothing city-specific.
    """
    spec = getattr(config, "sea", None)
    if spec is not None:
        data = spec.model_dump() if hasattr(spec, "model_dump") else dict(spec)
        return SeaConfig.from_mapping(data, source="city config")
    path = sea_config_path(str(config.id))
    if path.is_file():
        import yaml

        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        # The repository-relative path, so sea.json reads the same on every machine (rule 8).
        return SeaConfig.from_mapping(data, source=f"services/city/configs/sea/{config.id}.yaml")
    return SeaConfig()


# --------------------------------------------------------------------------------------
# the rules
# --------------------------------------------------------------------------------------


def _edge_labels(labels: NDArray[np.int32]) -> NDArray[np.int32]:
    edge = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    return np.unique(edge[edge > 0])


def open_sea(
    dem: NDArray[np.floating],
    landcover: NDArray[np.integer],
    *,
    z_max_m: float = SEA_Z_MAX_M,
    flat_m: float = FLAT_Z_M,
) -> NDArray[np.bool_]:
    """The open-sea rule of the module docstring, on the raw DEM. NaN cells are never sea."""
    z = np.asarray(dem, dtype=np.float64)
    finite = np.isfinite(z)
    zf = np.where(finite, z, np.inf)
    classes = np.asarray(landcover)
    candidate = finite & (((classes == WATER_CLASS) & (zf <= z_max_m)) | (zf <= flat_m))
    labels, n = ndimage.label(candidate, structure=_S8)
    if n == 0:
        return np.zeros(z.shape, dtype=bool)
    at_edge = np.zeros(n + 1, dtype=bool)
    at_edge[_edge_labels(labels)] = True
    has_flat = np.zeros(n + 1, dtype=bool)
    has_flat[np.unique(labels[candidate & (zf <= flat_m)])] = True
    keep = at_edge & has_flat
    keep[0] = False
    return keep[labels]


def river_lines(
    waterways: Any,
    names: tuple[str, ...],
    transform: Any,
    shape: tuple[int, int],
    *,
    crs: Any = None,
) -> tuple[NDArray[np.bool_], dict[str, int]]:
    """Cells touched by the waterways carrying one of ``names``, and the ways found per name.

    A name matches exactly (case and surrounding space aside) or as one item of an OSM ``;``
    list. Nothing is matched by substring: "Mithi" must not pick up a "Mithi Nagar Nala".
    """
    from varuna_city.condition import rasterize_mask

    found = {name: 0 for name in names}
    if waterways is None or not len(waterways) or not names or "name" not in waterways.columns:
        return np.zeros(shape, dtype=bool), found
    wanted = {name.casefold(): name for name in names}
    picked = []
    for geom, value in zip(waterways.geometry, waterways["name"], strict=True):
        if value is None or (isinstance(value, float) and np.isnan(value)):
            continue
        parts = {part.strip().casefold() for part in str(value).split(";")}
        hits = [wanted[p] for p in parts if p in wanted]
        if not hits or geom is None or geom.is_empty:
            continue
        if geom.geom_type not in {"LineString", "MultiLineString"}:
            continue
        for hit in hits:
            found[hit] += 1
        picked.append(geom)
    if not picked:
        return np.zeros(shape, dtype=bool), found
    import geopandas as gpd

    frame = gpd.GeoDataFrame(geometry=picked, crs=getattr(waterways, "crs", None))
    return rasterize_mask(frame, transform, shape, crs=crs, all_touched=True), found


def tidal_creeks(
    dem: NDArray[np.floating],
    landcover: NDArray[np.integer],
    lines: NDArray[np.bool_],
    *,
    z_max_m: float = SEA_Z_MAX_M,
    min_cells: int = CREEK_MIN_CELLS,
) -> NDArray[np.bool_]:
    """The tidal-creek rule of the module docstring, for the rasterised river ``lines``."""
    z = np.asarray(dem, dtype=np.float64)
    finite = np.isfinite(z)
    zf = np.where(finite, z, np.inf)
    lines = np.asarray(lines, dtype=bool)
    if not lines.any():
        return np.zeros(z.shape, dtype=bool)
    buffered = ndimage.binary_dilation(lines, structure=_S8)
    channel = finite & (buffered | (np.asarray(landcover) == WATER_CLASS)) & (zf <= z_max_m)
    labels, n = ndimage.label(channel, structure=_S8)
    if n == 0:
        return np.zeros(z.shape, dtype=bool)
    sizes = np.bincount(labels.ravel(), minlength=n + 1)
    touched = np.zeros(n + 1, dtype=bool)
    touched[np.unique(labels[lines & channel])] = True
    keep = touched & (sizes >= min_cells)
    keep[0] = False
    return keep[labels]


@dataclass(frozen=True, slots=True)
class SeaMask:
    """What :func:`build_sea_mask` produced: the coded raster and the numbers ``sea.json`` keeps."""

    codes: NDArray[np.uint8]
    """``SEA_LAND`` / ``SEA_OPEN`` / ``SEA_CREEK`` per cell."""
    stats: dict[str, Any]

    @property
    def sea(self) -> NDArray[np.bool_]:
        """Every cell the tide is imposed on: open sea and tidal creek alike."""
        return self.codes != SEA_LAND


def build_sea_mask(
    dem: NDArray[np.floating],
    landcover: NDArray[np.integer],
    *,
    transform: Any,
    crs: Any,
    waterways: Any = None,
    blocked: NDArray[np.bool_] | None = None,
    sea_config: SeaConfig | None = None,
    res_m: float | None = None,
) -> SeaMask:
    """Classify the sea and the tidal creeks on the city grid (module docstring)."""
    cfg = sea_config or SeaConfig()
    z = np.asarray(dem, dtype=np.float64)
    shape = (int(z.shape[0]), int(z.shape[1]))
    cell_m = float(res_m if res_m is not None else abs(transform.a))
    cell_km2 = cell_m * cell_m / 1e6

    is_open = open_sea(z, landcover)
    lines, found = river_lines(waterways, cfg.tidal_rivers, transform, shape, crs=crs)
    creek = tidal_creeks(z, landcover, lines) & ~is_open

    codes = np.zeros(shape, dtype=np.uint8)
    codes[is_open] = SEA_OPEN
    codes[creek] = SEA_CREEK
    removed_blocked = 0
    if blocked is not None:
        block = np.asarray(blocked, dtype=bool)
        removed_blocked = int(np.count_nonzero(block & (codes != SEA_LAND)))
        codes[block] = SEA_LAND

    missing = sorted(name for name, count in found.items() if count == 0)
    if missing:
        log.warning("sea.tidal_river_not_in_osm", names=missing)

    stats: dict[str, Any] = {
        "cells": int(np.count_nonzero(codes)),
        "km2": round(float(np.count_nonzero(codes)) * cell_km2, 3),
        "open_sea": {
            "cells": int(np.count_nonzero(codes == SEA_OPEN)),
            "km2": round(float(np.count_nonzero(codes == SEA_OPEN)) * cell_km2, 3),
        },
        "tidal_creek": {
            "cells": int(np.count_nonzero(codes == SEA_CREEK)),
            "km2": round(float(np.count_nonzero(codes == SEA_CREEK)) * cell_km2, 3),
            "rivers": {name: {"osm_ways": count} for name, count in sorted(found.items())},
        },
        "blocked_cells_removed": removed_blocked,
        "components": _components(codes, transform, crs, cell_km2),
        "rules": {
            "open_sea": (
                "8-connected components of (WorldCover 80 and raw DEM <= "
                f"{SEA_Z_MAX_M} m) or (raw DEM <= {FLAT_Z_M} m) that touch the domain edge "
                f"and hold a cell at or below {FLAT_Z_M} m"
            ),
            "tidal_creek": (
                "cells of the named OSM waterways with a 1-cell buffer, or WorldCover 80, at "
                f"raw DEM <= {SEA_Z_MAX_M} m, in 8-connected components of at least "
                f"{CREEK_MIN_CELLS} cells that touch the line"
            ),
            "blocked": "building cells are never sea",
            "assumption": (
                "Thresholds on open data, not a surveyed coastline; no cell was checked against "
                "a chart."
            ),
        },
        "sources": [dict(source) for source in SOURCES],
        "config": cfg.to_dict(),
    }
    log.info(
        "sea.mask",
        cells=stats["cells"],
        km2=stats["km2"],
        open_cells=stats["open_sea"]["cells"],
        creek_cells=stats["tidal_creek"]["cells"],
        blocked_removed=removed_blocked,
    )
    return SeaMask(codes=codes, stats=stats)


def _components(
    codes: NDArray[np.uint8], transform: Any, crs: Any, cell_km2: float
) -> list[dict[str, Any]]:
    """Each 8-connected sea component: kind, size and centroid, largest first."""
    sea = codes != SEA_LAND
    labels, n = ndimage.label(sea, structure=_S8)
    if n == 0:
        return []
    from pyproj import Transformer

    to_wgs = Transformer.from_crs(crs, "EPSG:4326", always_xy=True) if crs is not None else None
    index = np.arange(1, n + 1)
    sizes = ndimage.sum(sea, labels, index)
    rows = ndimage.mean(np.indices(codes.shape)[0], labels, index)
    cols = ndimage.mean(np.indices(codes.shape)[1], labels, index)
    creek_cells = ndimage.sum(codes == SEA_CREEK, labels, index)
    out = []
    for k in range(n):
        x = transform.c + (float(cols[k]) + 0.5) * transform.a
        y = transform.f + (float(rows[k]) + 0.5) * transform.e
        lon, lat = to_wgs.transform(x, y) if to_wgs is not None else (x, y)
        cells = int(sizes[k])
        out.append(
            {
                "cells": cells,
                "km2": round(cells * cell_km2, 3),
                "creek_cells": int(creek_cells[k]),
                "centroid_lon": round(float(lon), 5),
                "centroid_lat": round(float(lat), 5),
            }
        )
    out.sort(key=lambda c: (-c["cells"], c["centroid_lon"], c["centroid_lat"]))
    return out


def chessboard_distance(sea: NDArray[np.bool_]) -> NDArray[np.int32]:
    """Cells to the nearest sea cell, 8-connected; a large number everywhere when there is none."""
    mask = np.asarray(sea, dtype=bool)
    if not mask.any():
        return np.full(mask.shape, np.iinfo(np.int32).max, dtype=np.int32)
    return ndimage.distance_transform_cdt(~mask, metric="chessboard").astype(np.int32)


def shore_ring(sea: NDArray[np.bool_], blocked: NDArray[np.bool_] | None = None) -> NDArray:
    """Land cells 8-adjacent to the sea, buildings excluded."""
    mask = np.asarray(sea, dtype=bool)
    ring = ndimage.binary_dilation(mask, structure=_S8) & ~mask
    if blocked is not None:
        ring &= ~np.asarray(blocked, dtype=bool)
    return ring


__all__ = [
    "CREEK_MIN_CELLS",
    "FLAT_Z_M",
    "SEA_CREEK",
    "SEA_JSON_FILE",
    "SEA_LAND",
    "SEA_MASK_FILE",
    "SEA_OPEN",
    "SEA_Z_MAX_M",
    "SOURCES",
    "TIDAL_OUTFALL_CELLS",
    "TIDAL_OUTFALL_INVERT_M",
    "WATER_CLASS",
    "WET_CLASSES",
    "SeaConfig",
    "SeaMask",
    "build_sea_mask",
    "chessboard_distance",
    "load_sea_config",
    "open_sea",
    "river_lines",
    "sea_config_path",
    "shore_ring",
    "tidal_creeks",
]

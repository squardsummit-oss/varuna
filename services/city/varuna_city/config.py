"""Loading a city config and deriving its computation grid (SPEC.md 3.3, 10.1, P1.1).

``CityConfig`` (in ``varuna_schemas``) is the validated file on disk. ``CityGrid`` is what
every downstream engine actually needs: the metric raster the DEM, the roughness, the
curve number and the depth fields all share, snapped to whole ``grid_m`` multiples so two
runs of ``make city`` produce byte-identical products.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import structlog
from pyproj import Transformer
from rasterio.coords import BoundingBox
from rasterio.transform import Affine, array_bounds
from varuna_schemas.models.city import CityConfig
from varuna_schemas.paths import city_config_path, city_dir

log = structlog.get_logger(__name__)

WGS84 = "EPSG:4326"


class UnknownCityError(FileNotFoundError):
    """No ``services/city/configs/<city>.yaml`` for the requested slug."""


def load_city_config(city: str | Path) -> CityConfig:
    """Load ``services/city/configs/<city>.yaml`` (or a path) and validate it.

    Args:
        city: city slug (``"mumbai"``) or a path to a YAML file.
    """
    path = Path(city) if isinstance(city, Path) or str(city).endswith(".yaml") else None
    if path is None:
        path = city_config_path(str(city))
    if not path.is_file():
        available = (
            sorted(p.stem for p in path.parent.glob("*.yaml")) if path.parent.is_dir() else []
        )
        msg = f"No city config at {path}. Configured cities: {available or 'none'}."
        raise UnknownCityError(msg)
    config = CityConfig.from_yaml(path)
    log.info("city.config_loaded", city=config.id, aoi=config.aoi_id, path=str(path))
    return config


@lru_cache(maxsize=8)
def _transformer(dst_crs: str) -> Transformer:
    return Transformer.from_crs(WGS84, dst_crs, always_xy=True)


@dataclass(frozen=True, slots=True)
class CityGrid:
    """The city's metric raster grid: north-up, square cells, snapped to ``res`` multiples."""

    crs: str
    res: float
    width: int
    height: int
    transform: Affine

    @property
    def shape(self) -> tuple[int, int]:
        """``(height, width)`` - the numpy shape of every city raster."""
        return (self.height, self.width)

    @property
    def bounds(self) -> BoundingBox:
        """``(left, bottom, right, top)`` in the metric CRS."""
        return BoundingBox(*array_bounds(self.height, self.width, self.transform))

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly description for ``REPORT.md`` and run provenance."""
        left, bottom, right, top = self.bounds
        return {
            "crs": self.crs,
            "res_m": self.res,
            "width": self.width,
            "height": self.height,
            "bounds": [left, bottom, right, top],
            "transform": list(self.transform)[:6],
        }


def city_grid(config: CityConfig) -> CityGrid:
    """The computation grid for a city: bbox reprojected to ``config.crs``, snapped outward.

    The bbox edges are sampled (not just the four corners) because a UTM zone bends the
    graticule; sampling 32 points per edge keeps the whole AOI inside the grid.
    """
    res = float(config.grid_m)
    transformer = _transformer(config.crs_string)
    min_lon, min_lat, max_lon, max_lat = config.bbox.as_tuple()

    n = 32
    lons = [min_lon + (max_lon - min_lon) * i / n for i in range(n + 1)]
    lats = [min_lat + (max_lat - min_lat) * i / n for i in range(n + 1)]
    edge_lon: list[float] = []
    edge_lat: list[float] = []
    for lon in lons:
        edge_lon += [lon, lon]
        edge_lat += [min_lat, max_lat]
    for lat in lats:
        edge_lon += [min_lon, max_lon]
        edge_lat += [lat, lat]
    xs, ys = transformer.transform(edge_lon, edge_lat)

    left = math.floor(min(xs) / res) * res
    right = math.ceil(max(xs) / res) * res
    bottom = math.floor(min(ys) / res) * res
    top = math.ceil(max(ys) / res) * res

    width = round((right - left) / res)
    height = round((top - bottom) / res)
    grid = CityGrid(
        crs=config.crs_string,
        res=res,
        width=width,
        height=height,
        transform=Affine(res, 0.0, left, 0.0, -res, top),
    )
    log.info(
        "city.grid",
        city=config.id,
        crs=grid.crs,
        res_m=res,
        width=width,
        height=height,
        cells=width * height,
    )
    return grid


def city_out_dir(config: CityConfig) -> Path:
    """``city/<city>/`` - the only folder this service writes into. Created if absent."""
    out = city_dir(config.id)
    out.mkdir(parents=True, exist_ok=True)
    return out


__all__ = [
    "WGS84",
    "CityGrid",
    "UnknownCityError",
    "city_grid",
    "city_out_dir",
    "load_city_config",
]

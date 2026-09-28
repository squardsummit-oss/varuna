"""The storm-designer grid: the 60 km x 60 km, 500 m radar domain (SPEC.md 3.3, 10.2).

Both cubes a bundle carries - ``truth/rain.zarr`` (mm/h every 5 min) and ``radar/frames.zarr``
(dBZ every 10 min) - live on this one grid, so the validator can insist they agree and Sky can
resample from it to the 30 m city grid without guessing.

The grid is square, north-up and snapped to whole ``res_m`` multiples of the metric CRS so that
two runs of the generator produce byte-identical coordinates (rule 8). Row 0 is the northern
row: ``transform`` is the usual ``Affine(res, 0, left, 0, -res, top)`` GIS convention, the same
one ``varuna_city`` uses for the city grid.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from pyproj import Transformer
from varuna_schemas.models.city import CityConfig, RadarDomain
from varuna_schemas.models.common import BBox

WGS84 = "EPSG:4326"

_EDGE_SAMPLES = 32
"""Points sampled along each bbox edge when projecting an AOI: a UTM graticule bends."""


@lru_cache(maxsize=8)
def _transformer(dst_crs: str) -> Transformer:
    return Transformer.from_crs(WGS84, dst_crs, always_xy=True)


@dataclass(frozen=True, slots=True)
class StormDomain:
    """The square radar/truth grid in a metric CRS.

    Attributes:
        crs: EPSG code of the metric CRS (32643 for Mumbai, 32644 for Chennai).
        res_m: pixel size in metres (500 for pySTEPS).
        n_px: pixels per side (120 for a 60 km domain at 500 m).
        left: western edge in metric coordinates.
        top: northern edge in metric coordinates.
    """

    crs: int
    res_m: float
    n_px: int
    left: float
    top: float

    # ------------------------------------------------------------------ constructors
    @classmethod
    def from_radar_domain(cls, radar: RadarDomain, crs: int) -> StormDomain:
        """Build the grid a :class:`RadarDomain` describes, centred on its lon/lat."""
        center_x, center_y = _transformer(f"EPSG:{crs}").transform(
            radar.center_lon, radar.center_lat
        )
        res = float(radar.res_m)
        n = radar.n_px
        half = n * res / 2.0
        left = np.floor((center_x - half) / res) * res
        top = np.ceil((center_y + half) / res) * res
        return cls(crs=crs, res_m=res, n_px=n, left=float(left), top=float(top))

    @classmethod
    def from_city_config(cls, config: CityConfig) -> StormDomain:
        """The storm domain of a city: its ``radar_domain`` block in its computation CRS."""
        return cls.from_radar_domain(config.radar_domain, config.crs)

    # ------------------------------------------------------------------ geometry
    @property
    def crs_string(self) -> str:
        """``EPSG:32643`` form, for rasterio, pyproj and the cube attributes."""
        return f"EPSG:{self.crs}"

    @property
    def shape(self) -> tuple[int, int]:
        """``(n_px, n_px)`` - the numpy shape of one frame."""
        return (self.n_px, self.n_px)

    @property
    def size_km(self) -> float:
        return self.n_px * self.res_m / 1000.0

    @property
    def transform(self) -> tuple[float, float, float, float, float, float]:
        """The six affine coefficients ``(a, b, c, d, e, f)`` of a north-up grid."""
        return (self.res_m, 0.0, self.left, 0.0, -self.res_m, self.top)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """``(left, bottom, right, top)`` in the metric CRS."""
        extent = self.n_px * self.res_m
        return (self.left, self.top - extent, self.left + extent, self.top)

    def x_coords(self) -> np.ndarray:
        """Cell-centre eastings, west to east."""
        return self.left + (np.arange(self.n_px, dtype=np.float64) + 0.5) * self.res_m

    def y_coords(self) -> np.ndarray:
        """Cell-centre northings, north to south (row 0 is the northern row)."""
        return self.top - (np.arange(self.n_px, dtype=np.float64) + 0.5) * self.res_m

    def cell_centres(self) -> tuple[np.ndarray, np.ndarray]:
        """``(X, Y)`` cell-centre coordinate arrays of shape :attr:`shape`."""
        return np.meshgrid(self.x_coords(), self.y_coords())

    def center(self) -> tuple[float, float]:
        """Domain centre in metric coordinates."""
        extent = self.n_px * self.res_m
        return (self.left + extent / 2.0, self.top - extent / 2.0)

    def coverage_mask(self, radius_km: float) -> np.ndarray:
        """Boolean mask of the radar coverage circle centred on the domain."""
        cx, cy = self.center()
        x, y = self.cell_centres()
        radius_m = radius_km * 1000.0
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius_m**2

    def aoi_mask(self, bbox: BBox) -> np.ndarray:
        """Boolean mask of the pixels whose centre falls inside a WGS84 bbox.

        The bbox edges are sampled (not just the corners) before projecting, because a UTM
        zone bends the graticule and a corner-only box would clip the AOI.
        """
        left, bottom, right, top = self.project_bbox(bbox)
        x, y = self.cell_centres()
        return (x >= left) & (x <= right) & (y >= bottom) & (y <= top)

    def project_bbox(self, bbox: BBox) -> tuple[float, float, float, float]:
        """A WGS84 bbox as ``(left, bottom, right, top)`` in the domain's metric CRS."""
        min_lon, min_lat, max_lon, max_lat = bbox.as_tuple()
        n = _EDGE_SAMPLES
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
        xs, ys = _transformer(self.crs_string).transform(edge_lon, edge_lat)
        return (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)))

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly description written into every cube's attributes."""
        return {
            "crs": self.crs_string,
            "res_m": self.res_m,
            "n_px": self.n_px,
            "shape": list(self.shape),
            "transform": list(self.transform),
            "bounds": list(self.bounds),
        }


def step_times_min(t0_min: float, t1_min: float, step_min: float) -> np.ndarray:
    """Instants from ``t0_min`` to ``t1_min`` inclusive, every ``step_min`` minutes.

    Raises:
        ValueError: when the window is not a whole number of steps, which would silently
            drop the last frame of a cube.
    """
    if step_min <= 0:
        msg = f"step_min must be positive, got {step_min}"
        raise ValueError(msg)
    span = t1_min - t0_min
    if span < 0:
        msg = f"t1_min ({t1_min}) must not precede t0_min ({t0_min})"
        raise ValueError(msg)
    n_steps = span / step_min
    if abs(n_steps - round(n_steps)) > 1e-9:
        msg = f"window {span} min is not a whole number of {step_min}-minute steps"
        raise ValueError(msg)
    return t0_min + np.arange(round(n_steps) + 1, dtype=np.float64) * step_min


__all__ = ["WGS84", "StormDomain", "step_times_min"]

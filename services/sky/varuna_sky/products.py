"""The rain-cube products a cycle publishes, and the Zarr stores they live in.

SPEC.md 11.1 step 6 in full: the ensemble is reduced to ``p10/p50/p90`` and
``P(> 20, 40 mm/h)`` per pixel, resampled to the 30 m AOI grid, and summarised as one
AOI-mean hyetograph per member for the console's spread band (SPEC.md 7.2). SPEC.md 10.3
then fixes where the two Zarr stores live: ``data/runs/<run_id>/rain/cube.zarr`` and
``rain/quantiles.zarr``.

**Two grids, and which product lives on which.** The Sky grid is the 500 m, 120 x 120,
60 km radar domain that pySTEPS forecasts on; the AOI grid is the city's own 30 m raster,
about 323 x 522 cells for Mumbai, that VARUNA-Twin routes water across (SPEC.md 3.3). The
statistics on :class:`~varuna_sky.types.SkyProducts` carry a
:class:`~varuna_sky.types.RadarGrid`, so they are the Sky-grid products; "resampled to the
500 m Sky grid" is therefore the identity, and the AOI half of step 6 is
:func:`resample_to_aoi`, which the Twin calls on the field it is about to force with. The AOI
grid is never assumed: it is read from the record the city pipeline itself writes
(:func:`load_aoi_grid`), so a re-run of ``make city`` with a different bbox cannot leave Sky
resampling onto a grid the city no longer has.

**Why the 30 m ensemble is not written to disk.** Twenty members x 36 steps x 323 x 522 cells
is 121 million float32 values, 485 MB per cycle, and a bake is dozens of cycles. SPEC.md
10.3 asks for the 500 m cube and its quantiles, and that is what is written; the 30 m field is
produced on demand, one array at a time, for whoever needs it.

**Why the hyetographs are computed with a weight field.** The AOI-mean of a bilinear resample
is a *linear* functional of the Sky field, and both grids are north-up, unrotated and in the
same metric CRS, so the bilinear weights separate into a row factor and a column factor.
:func:`aoi_mean_weights` therefore collapses the whole resample into one ``(n_px, n_px)``
weight field that sums to one, and the 720 member-step means become a single ``tensordot``.
The result is not an approximation of the mean of the resampled cube - it is equal to it, to
floating-point round-off, which ``tests/test_products.py`` asserts.

**Precision.** Products are computed in float64 and published as float32: the stores, the
in-memory :class:`~varuna_sky.types.SkyProducts` and the API all carry the same numbers, so
nothing is silently rounded between the run and the screen. Float32 resolves a rain rate to
about six significant digits, far past the 0.1 mm/h that means anything.

Determinism (rule 8): nothing here is random, the arrays are cast before they are stored, and
Zarr writes the same bytes for the same values, so two bakes of one cycle produce
byte-identical stores. :func:`write_rain_products` writes in place; the temp-dir-then-rename
that makes a whole run directory atomic is the cycle orchestrator's job (SPEC.md 11.11).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import structlog
from varuna_schemas.constants import IST, STEP_MIN
from varuna_schemas.paths import city_dir

from varuna_sky.types import RadarGrid, RainEnsemble, SkyProducts

if TYPE_CHECKING:  # pragma: no cover - keeps numpy out of the runtime type surface
    from collections.abc import Mapping, Sequence

    from numpy.typing import NDArray

log = structlog.get_logger("varuna.sky.products")

__all__ = [
    "AXIS_DTYPE",
    "CUBE_VARIABLE",
    "EXCEEDANCE_MM_H",
    "GENERATOR",
    "HYETOGRAPH_VARIABLE",
    "PIPELINE_JSON",
    "PRODUCT_DTYPE",
    "QUANTILES",
    "QUANTILE_METHOD",
    "QUANTILE_VARIABLES",
    "RAIN_CUBE",
    "RAIN_QUANTILES",
    "AoiGrid",
    "aoi_hyetographs",
    "aoi_mean_weights",
    "exceedance",
    "load_aoi_grid",
    "quantiles",
    "read_attrs",
    "read_rain_cube",
    "read_sky_products",
    "resample_to_aoi",
    "sky_products",
    "write_quantiles",
    "write_rain_cube",
    "write_rain_products",
]

# ----------------------------------------------------------------------------- statistics
QUANTILES: tuple[float, float, float] = (0.10, 0.50, 0.90)
"""The three quantiles SPEC.md 11.1 step 6 names, in the order they are published."""

QUANTILE_METHOD: Literal["linear"] = "linear"
"""How a quantile between two order statistics is taken.

numpy's default, kept deliberately: with 20 members the 10th percentile falls between the
second and third smallest, and linear interpolation is the least surprising reading of that.
A different method would change published numbers, so it is named here rather than left to a
library default that could move."""

EXCEEDANCE_MM_H: tuple[float, float] = (20.0, 40.0)
"""Rain-rate thresholds for the exceedance products (SPEC.md 11.1 step 6).

They are the same two the verification's rain CSI uses (SPEC.md 11.12), which is the point:
the probability on the screen and the score on ``/verify`` ask the same question."""

PRODUCT_DTYPE = "float32"
"""Every published rain field, in memory and on disk (see the module docstring)."""

AXIS_DTYPE = "float64"
"""Coordinate axes stay float64: they are metres, and a 500 m grid 300 km from the CRS origin
would lose the half-pixel offset in float32."""

# ----------------------------------------------------------------------------- run layout
RAIN_CUBE = "rain/cube.zarr"
"""``R[m, t, y, x]``, 20 members x 36 steps on the Sky grid (SPEC.md 10.3)."""

RAIN_QUANTILES = "rain/quantiles.zarr"
"""Quantiles, exceedances and the per-member AOI hyetographs (SPEC.md 10.3)."""

CUBE_VARIABLE = "rain"
"""Array name inside ``cube.zarr``, matching the bundle cubes' ``rain``/``dbz`` convention."""

QUANTILE_VARIABLES: tuple[str, ...] = ("p10", "p50", "p90", "mean", "p_gt_20", "p_gt_40")
"""Array names inside ``quantiles.zarr``, in the order :class:`SkyProducts` declares them."""

HYETOGRAPH_VARIABLE = "aoi_hyetographs"
"""The ``(n_members, n_steps)`` AOI-mean rain rate array, stored beside the quantiles because
it is a product of the same reduction and the time bar fetches both together."""

GENERATOR = "varuna_sky.products"
"""Written into every store so a run can say which code produced it (as bundles do)."""

PIPELINE_JSON = "pipeline.json"
"""The city pipeline's own summary; its ``grid`` block is ``CityGrid.to_dict()`` and is where
:func:`load_aoi_grid` reads the AOI's affine transform and shape from."""

_EPS = 1e-6
"""Metres of slack when comparing two affine coefficients. The city and storm grids are both
snapped to whole cells, so any real disagreement is far larger than this."""


# ============================================================================ the AOI grid
@dataclass(frozen=True, slots=True)
class AoiGrid:
    """The city's 30 m computation grid, as the city pipeline recorded it.

    A plain mirror of ``varuna_city.config.CityGrid``: Sky may not import the city service
    (see ``types.py`` on layering), so the grid is re-read from the city's own artifacts
    rather than recomputed from a bbox, which would drift the moment either side changed how
    it snaps. ``transform`` is the six affine coefficients ``(res, 0, left, 0, -res, top)``:
    north-up and unrotated, which is what makes the bilinear weights separable.
    """

    crs: str
    res_m: float
    width: int
    height: int
    transform: tuple[float, float, float, float, float, float]

    def __post_init__(self) -> None:
        if self.res_m <= 0.0:
            msg = f"AOI resolution must be positive, got {self.res_m}"
            raise ValueError(msg)
        if self.width < 1 or self.height < 1:
            msg = f"AOI grid must have at least one cell, got {self.width} x {self.height}"
            raise ValueError(msg)
        if len(self.transform) != 6:
            msg = f"an affine transform has six coefficients, got {len(self.transform)}"
            raise ValueError(msg)
        a, b, _, d, e, _ = self.transform
        if abs(b) > _EPS or abs(d) > _EPS:
            msg = f"the AOI grid must be unrotated; transform has b={b}, d={d}"
            raise ValueError(msg)
        if abs(a - self.res_m) > _EPS or abs(e + self.res_m) > _EPS:
            msg = (
                f"transform pixel size ({a}, {e}) disagrees with res_m={self.res_m}; "
                "the grid record is inconsistent"
            )
            raise ValueError(msg)

    # ------------------------------------------------------------------ constructors
    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> AoiGrid:
        """Build from ``CityGrid.to_dict()`` - the ``grid`` block of ``pipeline.json``."""
        res = mapping.get("res_m", mapping.get("res"))
        if res is None:
            msg = f"grid record has neither 'res_m' nor 'res'; keys are {sorted(mapping)}"
            raise KeyError(msg)
        transform = tuple(float(v) for v in list(mapping["transform"])[:6])
        return cls(
            crs=str(mapping["crs"]),
            res_m=float(res),
            width=int(mapping["width"]),
            height=int(mapping["height"]),
            transform=transform,  # type: ignore[arg-type]
        )

    @classmethod
    def from_raster(cls, path: Path | str) -> AoiGrid:
        """Read the grid from one of the city's own GeoTIFFs (``city/<city>/dem.tif``).

        The second place the city pipeline stores its transform, and the authoritative one for
        the rasters themselves. It needs ``rasterio``, which is a ``varuna-city`` dependency
        and not a Sky one, so the import is local and its absence is reported plainly rather
        than crashing a cycle that only needed ``pipeline.json``.
        """
        try:
            import rasterio
        except ImportError as exc:  # pragma: no cover - rasterio is present in the workspace
            msg = (
                f"reading an AOI grid from {path} needs rasterio, which varuna-sky does not require"
            )
            raise RuntimeError(msg) from exc
        with rasterio.open(str(path)) as src:
            transform = tuple(float(v) for v in list(src.transform)[:6])
            return cls(
                crs=str(src.crs),
                res_m=float(transform[0]),
                width=int(src.width),
                height=int(src.height),
                transform=transform,  # type: ignore[arg-type]
            )

    # ------------------------------------------------------------------ geometry
    @property
    def shape(self) -> tuple[int, int]:
        """``(height, width)`` - the numpy shape of every city raster."""
        return (self.height, self.width)

    @property
    def left(self) -> float:
        return self.transform[2]

    @property
    def top(self) -> float:
        return self.transform[5]

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """``(left, bottom, right, top)`` in the metric CRS."""
        return (
            self.left,
            self.top - self.height * self.res_m,
            self.left + self.width * self.res_m,
            self.top,
        )

    def x_coords(self) -> NDArray[np.floating]:
        """Cell-centre eastings, west to east."""
        return self.left + (np.arange(self.width, dtype=np.float64) + 0.5) * self.res_m

    def y_coords(self) -> NDArray[np.floating]:
        """Cell-centre northings, north to south (row 0 is the northern row)."""
        return self.top - (np.arange(self.height, dtype=np.float64) + 0.5) * self.res_m

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly description, written into ``quantiles.zarr`` as provenance."""
        return {
            "crs": self.crs,
            "res_m": self.res_m,
            "width": self.width,
            "height": self.height,
            "bounds": list(self.bounds),
            "transform": list(self.transform),
        }


def load_aoi_grid(city: str | Path) -> AoiGrid:
    """The AOI grid of a built city, read from ``city/<city>/pipeline.json``.

    Args:
        city: city slug (``"mumbai"``), or a path to a ``pipeline.json`` or to a city folder.

    Raises:
        FileNotFoundError: the city has not been built, with the command that builds it.
    """
    path = Path(city) if isinstance(city, Path) or str(city).endswith(".json") else None
    if path is None:
        path = city_dir(str(city)) / PIPELINE_JSON
    elif path.is_dir():
        path = path / PIPELINE_JSON
    if not path.is_file():
        msg = (
            f"No city grid record at {path}. Run `make city CITY={Path(str(city)).name}` "
            "first: Sky resamples onto the grid the city pipeline built, never a guessed one."
        )
        raise FileNotFoundError(msg)
    record = json.loads(path.read_text(encoding="utf-8"))
    if "grid" not in record:
        msg = f"{path} has no 'grid' block; keys are {sorted(record)}"
        raise KeyError(msg)
    grid = AoiGrid.from_mapping(record["grid"])
    log.info(
        "sky.aoi_grid",
        path=str(path),
        crs=grid.crs,
        res_m=grid.res_m,
        shape=f"{grid.width} x {grid.height}",
    )
    return grid


# ============================================================================ resampling
@dataclass(frozen=True, slots=True)
class _Coefficients:
    """Separable bilinear weights taking a Sky-grid field to the AOI grid.

    ``row0``/``col0`` index the north-west neighbour of each AOI cell centre on the Sky grid
    and ``wrow``/``wcol`` are the weights of that neighbour's southern and eastern partners,
    so the interpolation is ``(1 - w) * near + w * far`` on each axis. ``row_in``/``col_in``
    mark the AOI rows and columns whose centres fall inside the Sky domain; because both grids
    are axis-aligned, coverage separates the same way the weights do.
    """

    row0: NDArray[np.intp]
    wrow: NDArray[np.floating]
    col0: NDArray[np.intp]
    wcol: NDArray[np.floating]
    row_in: NDArray[np.bool_]
    col_in: NDArray[np.bool_]

    @property
    def covered_fraction(self) -> float:
        """Share of AOI cells the Sky domain actually covers; 1.0 when the AOI is inside."""
        return float(self.row_in.mean() * self.col_in.mean())


def _axis_terms(
    frac: NDArray[np.floating], n: int
) -> tuple[NDArray[np.intp], NDArray[np.floating]]:
    """Neighbour index and far-neighbour weight for fractional coordinates ``frac``.

    Clipping the index to ``[0, n - 2]`` and the weight to ``[0, 1]`` makes the half-pixel
    band between the domain edge and the first cell centre a nearest-edge extrapolation
    rather than a hole. Cells outside the domain proper are handled by the caller's coverage
    mask, not here.
    """
    i0 = np.clip(np.floor(frac).astype(np.intp), 0, n - 2)
    return i0, np.clip(frac - i0, 0.0, 1.0)


def _coefficients(sky: RadarGrid, aoi: AoiGrid) -> _Coefficients:
    """Bilinear coefficients from the Sky grid to the AOI grid.

    Raises:
        ValueError: if the two grids are in different CRSs, if the Sky grid is smaller than
            two pixels, or if the AOI does not intersect the domain at all. All three mean the
            city config and the radar domain disagree, which is a configuration error and not
            something to paper over with an on-the-fly reprojection: both grids are derived
            from the same city config, so their CRS is the same by construction.
    """
    if sky.crs.strip().casefold() != aoi.crs.strip().casefold():
        msg = (
            f"the Sky domain is in {sky.crs} but the AOI grid is in {aoi.crs}; "
            "both are built from one city config and must share a CRS"
        )
        raise ValueError(msg)
    if sky.n_px < 2:
        msg = f"bilinear resampling needs at least a 2 x 2 Sky grid, got {sky.n_px}"
        raise ValueError(msg)

    x = aoi.x_coords()
    y = aoi.y_coords()
    col0, wcol = _axis_terms((x - sky.left) / sky.res_m - 0.5, sky.n_px)
    row0, wrow = _axis_terms((sky.top - y) / sky.res_m - 0.5, sky.n_px)

    left, bottom, right, top = sky.bounds
    col_in = (x >= left) & (x <= right)
    row_in = (y >= bottom) & (y <= top)
    if not (col_in.any() and row_in.any()):
        msg = (
            f"the AOI {aoi.bounds} lies outside the Sky domain {sky.bounds}; "
            "check the city bbox against radar_domain in the city config"
        )
        raise ValueError(msg)
    return _Coefficients(row0=row0, wrow=wrow, col0=col0, wcol=wcol, row_in=row_in, col_in=col_in)


def resample_to_aoi(
    field: NDArray[np.floating],
    sky: RadarGrid,
    aoi: AoiGrid,
    *,
    fill_value: float = float("nan"),
) -> NDArray[np.floating]:
    """Bilinearly resample a Sky-grid field onto the 30 m AOI grid (SPEC.md 11.1 step 6).

    Args:
        field: ``(..., n_px, n_px)`` on the Sky grid; leading axes (member, step, or both)
            are carried through untouched.
        sky: the Sky grid ``field`` is on.
        aoi: the city grid to land on.
        fill_value: what AOI cells outside the Sky domain get. The default ``nan`` is
            deliberate: an AOI cell the radar domain does not reach has no forecast, and
            filling it with zero would publish a dry street that was never observed (rule 6).

    Returns:
        ``(..., height, width)`` on the AOI grid, float64.
    """
    values = np.asarray(field, dtype=np.float64)
    if values.ndim < 2 or values.shape[-2:] != sky.shape:
        msg = f"field must end in the Sky grid shape {sky.shape}, got {values.shape}"
        raise ValueError(msg)
    terms = _coefficients(sky, aoi)

    north = values[..., terms.row0, :]
    south = values[..., terms.row0 + 1, :]
    rows = north + (south - north) * terms.wrow[:, None]
    west = rows[..., terms.col0]
    east = rows[..., terms.col0 + 1]
    out = west + (east - west) * terms.wcol

    if not (terms.row_in.all() and terms.col_in.all()):
        outside = ~(terms.row_in[:, None] & terms.col_in[None, :])
        out[..., outside] = fill_value
        log.warning(
            "sky.aoi_partial_coverage",
            covered_fraction=round(terms.covered_fraction, 4),
            aoi_bounds=[round(v, 1) for v in aoi.bounds],
            sky_bounds=[round(v, 1) for v in sky.bounds],
        )
    return out


def aoi_mean_weights(sky: RadarGrid, aoi: AoiGrid) -> NDArray[np.floating]:
    """Sky-pixel weights whose dot product with a field is the AOI mean of its resample.

    The bilinear operator is linear and separable, so averaging its output over the AOI is a
    fixed weighted sum over Sky pixels (see the module docstring). The weights sum to one over
    the AOI cells the domain covers, so the result is a mean, never a total.
    """
    terms = _coefficients(sky, aoi)
    row = np.zeros(sky.n_px, dtype=np.float64)
    col = np.zeros(sky.n_px, dtype=np.float64)
    np.add.at(row, terms.row0[terms.row_in], 1.0 - terms.wrow[terms.row_in])
    np.add.at(row, terms.row0[terms.row_in] + 1, terms.wrow[terms.row_in])
    np.add.at(col, terms.col0[terms.col_in], 1.0 - terms.wcol[terms.col_in])
    np.add.at(col, terms.col0[terms.col_in] + 1, terms.wcol[terms.col_in])
    cells = float(terms.row_in.sum()) * float(terms.col_in.sum())
    return np.outer(row, col) / cells


def aoi_hyetographs(
    cube: NDArray[np.floating], sky: RadarGrid, aoi: AoiGrid
) -> NDArray[np.floating]:
    """AOI-mean rain rate per member and step: ``(n_members, n_steps)`` in mm/h.

    This is the console time bar's spread band (SPEC.md 7.2): one line per member, so the
    band is the ensemble's own disagreement rather than a smoothed envelope.
    """
    values = np.asarray(cube, dtype=np.float64)
    if values.ndim != 4 or values.shape[-2:] != sky.shape:
        msg = f"cube must be (member, step, y, x) on the Sky grid {sky.shape}, got {values.shape}"
        raise ValueError(msg)
    weights = aoi_mean_weights(sky, aoi)
    return np.tensordot(values, weights, axes=((2, 3), (0, 1)))


# ============================================================================ statistics
def quantiles(cube: NDArray[np.floating]) -> NDArray[np.floating]:
    """``(3, n_steps, n_px, n_px)``: the :data:`QUANTILES` across the member axis."""
    values = np.asarray(cube, dtype=np.float64)
    return np.quantile(values, list(QUANTILES), axis=0, method=QUANTILE_METHOD)


def exceedance(cube: NDArray[np.floating], threshold_mm_h: float) -> NDArray[np.floating]:
    """``P(R > threshold)`` per pixel and step: the fraction of members strictly above it.

    Strictly above, so a member sitting exactly on 20.0 mm/h does not count as exceeding
    20 mm/h. With 20 members the probability is a multiple of 5 %, which is what an ensemble
    of that size can honestly resolve.
    """
    values = np.asarray(cube, dtype=np.float64)
    return (values > float(threshold_mm_h)).mean(axis=0)


def _published(array: NDArray[np.floating]) -> NDArray[np.floating]:
    """Cast to the published dtype, C-contiguous, so memory and disk carry one number."""
    return np.ascontiguousarray(array, dtype=PRODUCT_DTYPE)


def sky_products(ensemble: RainEnsemble, aoi: AoiGrid) -> SkyProducts:
    """Reduce one cycle's ensemble to what the rest of VARUNA reads (SPEC.md 11.1 step 6).

    Args:
        ensemble: the 20-member, 3-hour cube on the Sky grid.
        aoi: the city grid the hyetographs are averaged over - required, because a spread band
            drawn over the radar domain rather than over the city would not be about the city.

    Raises:
        ValueError: the cube is not ``(member, step, y, x)`` on the ensemble's own grid, its
            time axis does not match its steps, or it carries a non-finite value. The last one
            is a bug upstream, and a silently zero-filled forecast is worse than a stopped
            cycle (rule 6).
    """
    cube = np.asarray(ensemble.rain_mm_h, dtype=np.float64)
    grid = ensemble.grid
    if cube.ndim != 4 or cube.shape[-2:] != grid.shape:
        msg = (
            f"ensemble must be (member, step, y, x) on the Sky grid {grid.shape}, got {cube.shape}"
        )
        raise ValueError(msg)
    if len(ensemble.times) != cube.shape[1]:
        msg = f"{cube.shape[1]} steps but {len(ensemble.times)} valid times"
        raise ValueError(msg)
    bad = int((~np.isfinite(cube)).sum())
    if bad:
        msg = f"the rain cube carries {bad} non-finite values; Sky must publish rain, not nan"
        raise ValueError(msg)

    q10, q50, q90 = quantiles(cube)
    hyetographs = aoi_hyetographs(cube, grid, aoi)
    products = SkyProducts(
        p10=_published(q10),
        p50=_published(q50),
        p90=_published(q90),
        mean=_published(cube.mean(axis=0)),
        p_gt_20=_published(exceedance(cube, EXCEEDANCE_MM_H[0])),
        p_gt_40=_published(exceedance(cube, EXCEEDANCE_MM_H[1])),
        aoi_hyetographs=_published(hyetographs),
        times=ensemble.times,
        grid=grid,
    )
    log.info(
        "sky.products",
        n_members=int(cube.shape[0]),
        n_steps=int(cube.shape[1]),
        aoi_shape=f"{aoi.width} x {aoi.height}",
        aoi_peak_mm_h=round(float(hyetographs.max()), 2),
        aoi_mean_mm_h=round(float(hyetographs.mean()), 2),
        p90_max_mm_h=round(float(q90.max()), 2),
        mean_max_mm_h=round(float(cube.mean(axis=0).max()), 2),
        source=ensemble.source,
    )
    return products


# ========================================================================== the Zarr stores
def _grid_attrs(grid: RadarGrid) -> dict[str, Any]:
    """The georeference every VARUNA cube carries, keyed exactly as the bundle cubes key it."""
    return {
        "crs": grid.crs,
        "res_m": grid.res_m,
        "n_px": grid.n_px,
        "shape": list(grid.shape),
        "transform": list(grid.transform),
        "bounds": list(grid.bounds),
    }


def _step_minutes(times: Sequence[datetime]) -> float:
    """Spacing of the forecast steps in minutes, checked to be uniform.

    Raises:
        ValueError: the steps are unevenly spaced, which would make ``time_min`` a lie.
    """
    if len(times) < 2:
        log.info("sky.step_min_assumed", step_min=STEP_MIN, n_steps=len(times))
        return float(STEP_MIN)
    gaps = {round((b - a).total_seconds() / 60.0, 6) for a, b in pairwise(times)}
    if len(gaps) != 1:
        msg = f"forecast steps must be evenly spaced; found gaps of {sorted(gaps)} minutes"
        raise ValueError(msg)
    return float(gaps.pop())


def _time_attrs(times: Sequence[datetime]) -> tuple[dict[str, Any], NDArray[np.floating]]:
    """``t0``/``step_min``/``cycle_ts`` attributes and the ``time_min`` axis.

    ``t0`` is the first *forecast* instant, so ``t0 + time_min`` reproduces ``times`` exactly,
    the same contract the bundle cubes keep. ``cycle_ts`` is one step earlier, which is the
    definition in ``types.py`` (step ``k`` is valid at ``cycle_ts + (k + 1) * step_min``), and
    is recorded so a store can be traced back to its cycle without ``run.json``.
    """
    if not times:
        msg = "a rain store needs at least one forecast step; got none"
        raise ValueError(msg)
    step_min = _step_minutes(times)
    t0 = times[0]
    offsets = np.array([(ts - t0).total_seconds() / 60.0 for ts in times], dtype=AXIS_DTYPE)
    attrs = {
        "t0": t0.astimezone(IST).isoformat(),
        "step_min": step_min,
        "cycle_ts": (t0 - timedelta(minutes=step_min)).astimezone(IST).isoformat(),
        "n_steps": len(times),
    }
    return attrs, offsets


def _write_axes(group: Any, grid: RadarGrid, times_min: NDArray[np.floating]) -> None:
    """Write the ``time_min``, ``x`` and ``y`` coordinate axes of a store."""
    x = grid.left + (np.arange(grid.n_px, dtype=np.float64) + 0.5) * grid.res_m
    y = grid.top - (np.arange(grid.n_px, dtype=np.float64) + 0.5) * grid.res_m
    for name, values, dim in (("time_min", times_min, "time"), ("x", x, "x"), ("y", y, "y")):
        axis = group.create_array(
            name, shape=values.shape, dtype=AXIS_DTYPE, dimension_names=(dim,)
        )
        axis[:] = values


def _write_field(
    group: Any,
    name: str,
    values: NDArray[np.floating],
    *,
    dims: tuple[str, ...],
    chunks: tuple[int, ...],
    units: str,
    shards: tuple[int, ...] | None = None,
) -> None:
    """Write one product array with its units, its chunking and its shard layout.

    ``shards`` groups many chunks into one file without changing what a read decompresses:
    the chunk is still the unit of decompression, so a member-step slice costs the same, but
    a 20 x 36 cube lands as 20 files instead of 720. On the demo laptop that is the
    difference between a 1.7 s and a 0.5 s write of one cycle, and 720 fewer files per run
    for ``make pack`` to carry. Sharded stores are still byte-identical between two writes.
    """
    array = group.create_array(
        name,
        shape=values.shape,
        dtype=PRODUCT_DTYPE,
        chunks=chunks,
        shards=shards,
        dimension_names=dims,
    )
    array[:] = np.asarray(values, dtype=PRODUCT_DTYPE)
    array.attrs["units"] = units


def _open(path: Path) -> Any:
    """Create (or replace) the Zarr group at ``path``."""
    import zarr

    path.parent.mkdir(parents=True, exist_ok=True)
    return zarr.open_group(str(path), mode="w")


def write_rain_cube(run_dir: Path, ensemble: RainEnsemble) -> Path:
    """Write ``rain/cube.zarr``: ``R[m, t, y, x]`` on the Sky grid (SPEC.md 10.3).

    Chunked ``(1, 1, n_px, n_px)`` because the access pattern is one member-step slice at a
    time - a what-if member, a verification frame, a Twin forcing step - and a chunk that
    spanned members or steps would decompress twenty or thirty-six times the wanted data.
    The chunks are sharded one member per file, which changes nothing about what a read
    decompresses and turns 720 small files into 20 (see :func:`_write_field`).

    The store carries the whole provenance of the forecast: which nowcaster ran, the seed it
    ran with, and the Z-R relation the analysis came through, so a cube on disk can answer
    "what produced this?" without its ``run.json``.
    """
    cube = np.asarray(ensemble.rain_mm_h)
    grid = ensemble.grid
    if cube.ndim != 4 or cube.shape[-2:] != grid.shape:
        msg = (
            f"ensemble must be (member, step, y, x) on the Sky grid {grid.shape}, got {cube.shape}"
        )
        raise ValueError(msg)
    if cube.shape[1] != len(ensemble.times):
        msg = f"{cube.shape[1]} steps but {len(ensemble.times)} valid times"
        raise ValueError(msg)
    time_attrs, times_min = _time_attrs(ensemble.times)

    path = Path(run_dir) / RAIN_CUBE
    group = _open(path)
    _write_field(
        group,
        CUBE_VARIABLE,
        cube,
        dims=("member", "time", "y", "x"),
        chunks=(1, 1, grid.n_px, grid.n_px),
        shards=(1, cube.shape[1], grid.n_px, grid.n_px),
        units="mm/h",
    )
    members = group.create_array(
        "member", shape=(cube.shape[0],), dtype="int64", dimension_names=("member",)
    )
    members[:] = np.arange(cube.shape[0], dtype=np.int64)
    _write_axes(group, grid, times_min)

    group.attrs["variable"] = CUBE_VARIABLE
    group.attrs["units"] = "mm/h"
    group.attrs["generator"] = GENERATOR
    group.attrs["n_members"] = int(cube.shape[0])
    group.attrs["nowcast_source"] = ensemble.source
    group.attrs["seed"] = int(ensemble.seed)
    group.attrs["zr_a"] = float(ensemble.zr.a)
    group.attrs["zr_b"] = float(ensemble.zr.b)
    group.attrs["zr_source"] = ensemble.zr.source
    group.attrs["motion_method"] = ensemble.motion.method if ensemble.motion else "none"
    for key, value in {**_grid_attrs(grid), **time_attrs}.items():
        group.attrs[key] = value
    log.info("sky.cube_written", path=str(path), shape=list(cube.shape), source=ensemble.source)
    return path


def write_quantiles(run_dir: Path, products: SkyProducts, aoi: AoiGrid | None = None) -> Path:
    """Write ``rain/quantiles.zarr``: the five per-pixel fields and the hyetographs.

    Chunked one step per chunk, because the console draws one step at a time as the operator
    scrubs, and sharded one file per field. ``aoi`` is optional and purely provenance: recording which city grid the
    hyetographs were averaged over is what stops a spread band being read as belonging to a
    different AOI than the one it was computed on.
    """
    grid = products.grid
    fields = {
        "p10": products.p10,
        "p50": products.p50,
        "p90": products.p90,
        "mean": products.mean,
        "p_gt_20": products.p_gt_20,
        "p_gt_40": products.p_gt_40,
    }
    for name, values in fields.items():
        array = np.asarray(values)
        if array.ndim != 3 or array.shape[-2:] != grid.shape:
            msg = f"{name} must be (step, y, x) on the Sky grid {grid.shape}, got {array.shape}"
            raise ValueError(msg)
        if array.shape[0] != len(products.times):
            msg = f"{name} has {array.shape[0]} steps but {len(products.times)} valid times"
            raise ValueError(msg)
    time_attrs, times_min = _time_attrs(products.times)

    path = Path(run_dir) / RAIN_QUANTILES
    group = _open(path)
    for name, values in fields.items():
        _write_field(
            group,
            name,
            np.asarray(values),
            dims=("time", "y", "x"),
            chunks=(1, grid.n_px, grid.n_px),
            shards=(len(products.times), grid.n_px, grid.n_px),
            units="mm/h" if name in ("p10", "p50", "p90", "mean") else "probability",
        )
    hyetographs = np.asarray(products.aoi_hyetographs)
    _write_field(
        group,
        HYETOGRAPH_VARIABLE,
        hyetographs,
        dims=("member", "time"),
        chunks=tuple(int(n) for n in hyetographs.shape),
        units="mm/h",
    )
    _write_axes(group, grid, times_min)

    group.attrs["variables"] = [*QUANTILE_VARIABLES, HYETOGRAPH_VARIABLE]
    group.attrs["generator"] = GENERATOR
    group.attrs["quantiles"] = list(QUANTILES)
    group.attrs["quantile_method"] = QUANTILE_METHOD
    group.attrs["exceedance_mm_h"] = list(EXCEEDANCE_MM_H)
    group.attrs["n_members"] = int(hyetographs.shape[0])
    for key, value in {**_grid_attrs(grid), **time_attrs}.items():
        group.attrs[key] = value
    if aoi is not None:
        group.attrs["aoi"] = aoi.to_dict()
    log.info(
        "sky.quantiles_written",
        path=str(path),
        n_steps=int(np.asarray(products.p50).shape[0]),
        n_members=int(hyetographs.shape[0]),
    )
    return path


def write_rain_products(
    run_dir: Path,
    ensemble: RainEnsemble,
    products: SkyProducts,
    aoi: AoiGrid | None = None,
) -> tuple[Path, Path]:
    """Write both rain stores of a run and return their paths, cube first."""
    return (write_rain_cube(run_dir, ensemble), write_quantiles(run_dir, products, aoi))


# ============================================================================ reading back
def read_attrs(path: Path) -> dict[str, Any]:
    """The group attributes of a store, for provenance and for rebuilding a grid."""
    import zarr

    return dict(zarr.open_group(str(path), mode="r").attrs)


def read_rain_cube(path: Path) -> NDArray[np.floating]:
    """The ``(member, step, y, x)`` cube from ``rain/cube.zarr``."""
    import zarr

    group: Any = zarr.open_group(str(path), mode="r")
    return np.asarray(group[CUBE_VARIABLE][:], dtype=PRODUCT_DTYPE)


def _grid_from_attrs(attrs: Mapping[str, Any]) -> RadarGrid:
    """Rebuild the Sky grid a store was written on from its attributes."""
    return RadarGrid(
        crs=str(attrs["crs"]),
        res_m=float(attrs["res_m"]),
        n_px=int(attrs["n_px"]),
        transform=tuple(float(v) for v in attrs["transform"]),  # type: ignore[arg-type]
    )


def read_sky_products(path: Path) -> SkyProducts:
    """Rebuild :class:`~varuna_sky.types.SkyProducts` from ``rain/quantiles.zarr``.

    The store keeps its own georeference and time axis, so the products come back identical
    to the ones written - which is what makes the round trip testable and what lets the API
    serve a baked run without re-running Sky.
    """
    import zarr

    group: Any = zarr.open_group(str(path), mode="r")
    attrs = dict(group.attrs)
    t0 = datetime.fromisoformat(str(attrs["t0"])).astimezone(IST)
    offsets = np.asarray(group["time_min"][:], dtype=np.float64)
    return SkyProducts(
        mean=np.asarray(group["mean"][:], dtype=PRODUCT_DTYPE),
        p10=np.asarray(group["p10"][:], dtype=PRODUCT_DTYPE),
        p50=np.asarray(group["p50"][:], dtype=PRODUCT_DTYPE),
        p90=np.asarray(group["p90"][:], dtype=PRODUCT_DTYPE),
        p_gt_20=np.asarray(group["p_gt_20"][:], dtype=PRODUCT_DTYPE),
        p_gt_40=np.asarray(group["p_gt_40"][:], dtype=PRODUCT_DTYPE),
        aoi_hyetographs=np.asarray(group[HYETOGRAPH_VARIABLE][:], dtype=PRODUCT_DTYPE),
        times=tuple(t0 + timedelta(minutes=float(m)) for m in offsets),
        grid=_grid_from_attrs(attrs),
    )

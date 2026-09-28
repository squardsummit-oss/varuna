"""Manning roughness raster for the 2D solver (SPEC.md 10.1 step 4, task P1.5).

The local-inertial kernel (SPEC.md 11.3) needs an ``n`` per cell and a list of cells it
must not route water through. Both come from here:

* ``n`` follows the spec's four surface classes - asphalt 0.016, open ground 0.04,
  vegetation 0.07, water 0.03 - taken from the ESA WorldCover class of the cell and
  overridden by the road mask.
* Buildings are **blocked**: they get ``n = 0.3`` so any solver that ignores the mask still
  stalls the flow, and they are flagged in a separate boolean raster
  (:attr:`Roughness.blocked`) that the kernel uses to zero the face fluxes.

Precedence, lowest first: WorldCover class, then roads, then buildings.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import structlog
from numpy.typing import NDArray

log = structlog.get_logger(__name__)

MANNING_N: dict[str, float] = {
    "asphalt": 0.016,
    "open_ground": 0.04,
    "vegetation": 0.07,
    "water": 0.03,
    "building": 0.3,
}
"""The surface classes of SPEC.md 10.1 step 4 and their Manning ``n``."""

N_ASPHALT = MANNING_N["asphalt"]
N_OPEN_GROUND = MANNING_N["open_ground"]
N_VEGETATION = MANNING_N["vegetation"]
N_WATER = MANNING_N["water"]
N_BUILDING = MANNING_N["building"]

WORLDCOVER_CLASS: dict[int, str] = {
    10: "vegetation",  # tree cover
    20: "vegetation",  # shrubland
    30: "open_ground",  # grassland
    40: "open_ground",  # cropland
    50: "asphalt",  # built-up
    60: "open_ground",  # bare / sparse vegetation
    70: "open_ground",  # snow and ice (not in these AOIs)
    80: "water",  # permanent water bodies
    90: "vegetation",  # herbaceous wetland
    95: "vegetation",  # mangroves
    100: "open_ground",  # moss and lichen
}
"""ESA WorldCover v200 class code -> surface class."""


@dataclass(frozen=True, slots=True)
class Roughness:
    """The roughness raster and the blocked mask that go with it."""

    n: NDArray[np.float32]
    """Manning ``n`` per cell."""
    blocked: NDArray[np.bool_]
    """Cells the 2D solver must not route water through (buildings)."""
    counts: dict[str, int]
    """Cells per surface class, for ``REPORT.md``."""
    stage_ms: float = 0.0

    def __array__(self, dtype: Any = None, copy: bool | None = None) -> NDArray[Any]:
        """``np.asarray(roughness)`` gives the ``n`` raster, so it reads like one."""
        out = self.n if dtype is None else self.n.astype(dtype)
        return np.array(out, copy=True) if copy else out


def manning_n(
    landcover: NDArray[np.integer[Any]] | NDArray[np.floating[Any]] | None,
    buildings_mask: NDArray[np.bool_] | None = None,
    roads_mask: NDArray[np.bool_] | None = None,
    *,
    shape: tuple[int, int] | None = None,
    class_map: dict[int, str] | None = None,
    default: str = "open_ground",
) -> Roughness:
    """Build the Manning ``n`` raster from land cover, roads and buildings.

    Args:
        landcover: ESA WorldCover class codes on the city grid, or ``None`` to treat every
            cell that is neither road nor building as ``default``.
        buildings_mask: cells covered by a building footprint - blocked, ``n = 0.3``.
        roads_mask: cells on a road centreline - ``n = 0.016``.
        shape: grid shape; required only when ``landcover`` is ``None`` and no mask is given.
        class_map: override for :data:`WORLDCOVER_CLASS`.
        default: surface class for cells whose land-cover code is unknown or no-data.

    Returns:
        :class:`Roughness` - ``n`` (float32), the ``blocked`` mask and per-class cell counts.
    """
    t0 = time.perf_counter()
    grid_shape = _resolve_shape(landcover, buildings_mask, roads_mask, shape)
    buildings = _as_mask(buildings_mask, grid_shape)
    roads = _as_mask(roads_mask, grid_shape)
    mapping = WORLDCOVER_CLASS if class_map is None else class_map

    if default not in MANNING_N:
        msg = f"Unknown surface class {default!r}. Known classes: {sorted(MANNING_N)}."
        raise ValueError(msg)

    n = np.full(grid_shape, MANNING_N[default], dtype=np.float32)
    counts: dict[str, int] = dict.fromkeys(MANNING_N, 0)

    if landcover is not None:
        codes = np.asarray(landcover)
        if codes.shape != grid_shape:
            msg = f"landcover shape {codes.shape} does not match the grid {grid_shape}."
            raise ValueError(msg)
        codes = np.where(np.isfinite(codes), codes, -1) if codes.dtype.kind == "f" else codes
        codes = codes.astype(np.int32, copy=False)
        for code, surface in mapping.items():
            n[codes == code] = MANNING_N[surface]

    n[roads] = N_ASPHALT
    n[buildings] = N_BUILDING

    for surface, value in MANNING_N.items():
        counts[surface] = int(np.count_nonzero(np.isclose(n, value)))

    stage_ms = round((time.perf_counter() - t0) * 1000, 1)
    log.info(
        "manning_n.done",
        cells=int(n.size),
        blocked=int(buildings.sum()),
        roads=int(roads.sum()),
        stage_ms=stage_ms,
        **{f"cells_{k}": v for k, v in counts.items()},
    )
    return Roughness(n=n, blocked=buildings, counts=counts, stage_ms=stage_ms)


def _resolve_shape(
    landcover: Any,
    buildings_mask: Any,
    roads_mask: Any,
    shape: tuple[int, int] | None,
) -> tuple[int, int]:
    for candidate in (landcover, buildings_mask, roads_mask):
        if candidate is not None:
            arr = np.asarray(candidate)
            return (int(arr.shape[0]), int(arr.shape[1]))
    if shape is None:
        msg = "manning_n needs land cover, a mask or an explicit shape."
        raise ValueError(msg)
    return shape


def _as_mask(mask: Any, shape: tuple[int, int]) -> NDArray[np.bool_]:
    if mask is None:
        return np.zeros(shape, dtype=bool)
    arr = np.asarray(mask, dtype=bool)
    if arr.shape != shape:
        msg = f"Mask shape {arr.shape} does not match the grid {shape}."
        raise ValueError(msg)
    return arr


__all__ = [
    "MANNING_N",
    "N_ASPHALT",
    "N_BUILDING",
    "N_OPEN_GROUND",
    "N_VEGETATION",
    "N_WATER",
    "WORLDCOVER_CLASS",
    "Roughness",
    "manning_n",
]

"""5 m nests over the two worst chronic spots (SPEC.md 11.3, 10.1 step 1; task P4.8).

SPEC.md 11.3 words the feature in one line: "same kernel on 5 m crops at Hindmata and King's
Circle with boundary heads interpolated from the 30 m run". This module is that sentence and
nothing more - it crops the conditioned city grid, refines it, and drives
:class:`varuna_twin.swe2d.SurfaceStepper` over the crop with a Dirichlet water depth on the
outer ring taken from the city run. The kernel is not forked and not copied: every sub-step
here is the same ``_step`` the 30 m run takes, reached through the same public stepper the
coupled runner uses.

**What a nest is not.** Three things, and each one bounds what a number out of this module is
allowed to claim:

* **It carries no 5 m terrain, and this bounds what a nest may be quoted for.** SPEC.md 10.1
  step 1 specifies "5 m nests by bicubic resampling", so the nest DEM is an interpolation of
  the 30 m conditioned DEM, not a survey. A kerb, a subway ramp or a 4 m dip under a rail
  bridge that the 30 m grid never saw is still invisible at 5 m. Any difference a nest shows
  against the city run is a *numerical* difference - finer discretisation, a shorter CFL step,
  friction on smaller cells, and the microtopography the interpolant drew - and never new
  ground truth.

  How much that matters was measured rather than assumed, and the answer is: enough to forbid
  one kind of claim. Driving both grids with the 50 mm/h upgraded design intensity for three
  hours over the Hindmata window, the 30 m run holds **25.1 cm** on the parent cell containing
  the register point. The shipped bicubic nest holds **47.1 cm** as the mean of that cell's 36
  children, and 201.3 cm in its deepest one. The *same nest built with a bilinear DEM* - the
  monotone interpolant, which cannot ring across the building burn - holds **19.5 cm**, and
  119.9 cm in its deepest child. The per-junction answer therefore swings from +22 cm to
  -5.6 cm on the choice of interpolant alone, with the 30 m answer in between and no 5 m survey
  to arbitrate. **A nest must not be quoted as a better depth at a named junction.**

  What survives the interpolant is the aggregate, and it is the finding worth having: over the
  1156 parent cells of the window the nest differs from the parent by a mean of **6.14 cm**
  (bicubic) or **6.10 cm** (bilinear) with a bias of **-0.22 cm** or **-0.61 cm**, and by over
  a metre at the worst cell either way. Near-zero bias with centimetres of mean difference is
  the whole result: the finer grid does not add or remove water, it **redistributes** it into
  hollows the 30 m cell averages flat. That is what a nest is for, and it is all a nest earns.

  :attr:`Nest.notes` carries the interpolation label on every nest, and it is the first thing
  to say out loud when a nest depth is quoted.
* **It has no drains.** The 1D graph and the coupling (SPEC.md 11.4, 11.5) are node-on-cell
  structures built against the 30 m grid; re-deriving them at 5 m is a separate piece of work.
  A nest run is surface-only, so it must be compared against a surface-only city run if the
  comparison is to isolate resolution. Comparing a 5 m surface nest against the 30 m *coupled*
  run measures the drains, not the grid.
* **It does not feed back.** Boundary information travels one way, from the city run into the
  nest. The city run does not see the nest, so a nest cannot correct the parent's routing -
  it can only show, at 5 m, what the parent's own water did inside the window.

**The window.** :func:`build_nest` snaps the crop to whole parent cells. SPEC.md 3.3 asks for
1 km2 and 30 m does not divide 1000 m, so the window is the smallest whole number of parent
cells covering the requested size - 34 x 34 cells, 1020 m, 1.04 km2 on Mumbai - centred on the
verified register point to within half a parent cell. Snapping is not tidiness: it makes the
refinement ratio an exact integer, which is what lets the rain be block-replicated without
inventing or destroying any, and lets a nest cell be compared with the parent cell that
contains it rather than with a weighted blend of four.

**The centre comes from the register.** SPEC.md 3.3 lists approximate coordinates and then
instructs that each one be verified against OSM and stored with its ``source_url``; the city
pipeline did that, and ``city/<city>/hotspots.geojson`` is the result.
:func:`varuna_twin.city.load_nests` resolves each nest's centre from that file and refuses a
nest it cannot resolve, so the 72.841/19.012 literals in the config are labels, never
coordinates. They are 242.0 m from the verified Hindmata point and 127.2 m from the verified King's
Circle point - a quarter of the window, on a feature the size of a junction.

**The boundary, and what the interpolation costs.** The ring of nest cells one cell deep along
the window edge is Dirichlet: its water depth is imposed from the parent, by the arithmetic
``swe2d._apply_tide`` imposes the sea stage, and the volume that crosses is counted in both
directions so the run stays auditable.

* *In space*, the parent's depth is interpolated bilinearly to the ring cell centres, and it is
  the **depth** that is imposed, not the water level. The level is the more principled choice
  on paper - a level is what is continuous across an interface, and two grids that disagree
  about the ground should agree about the water surface - and it was what this module did
  first. It is wrong here, measurably: the nest's ground is a bicubic resampling of the
  parent's and the two interpolants of *the same terrain* disagree by up to **4.09 m** across
  the +5 m building burn on the Hindmata window (SPEC.md 10.1 step 4), so ``level - z_nest``
  put metres of water on ring cells the parent had 2 cm on. Measured on the 06:00 IST cycle of
  2 July 2019: **17,994,252 m3 of boundary inflow into a 1.04 km2 window against 1,123 m3 of
  rain**, and a nest peak of **6.58 m** where the parent's was 2.4 cm. The audit reported all
  of it as conserved, correctly, because the water genuinely crossed the boundary - it was the
  boundary that was wrong, not the ledger. Imposing the depth puts the nest's water surface at
  the wrong absolute height by exactly that ground discrepancy, which is a real cost and is the
  smaller one: depth is what every product downstream reports, and a level-matched ring invents
  the water instead of misplacing it. A nest that ever carries a genuine 5 m survey DEM rather
  than an interpolation would have to revisit this, with the two grounds reconciled first.
* *The wet/dry gate.* Where the interpolated parent depth is at or below
  :data:`~varuna_twin.swe2d.DRY_DEPTH_M`, the ring cell is set dry rather than to the parent's
  the parent's residual film. It costs nothing now that the depth is imposed, and it is kept
  because it is what stops a parent cell holding 0.4 mm of numerical residue from being written
  onto 36 nest cells at every sub-step of a three-hour run.
* *In time*, the parent writes one field every ``step_min`` minutes (5 by default) and the
  nest's inner step is 0.5-10 s, so the ring depth is linearly interpolated between the two
  bracketing parent snapshots. That is the accuracy cost of the scheme and it is one-sided: a
  ramp cannot represent a wave that arrives and drains inside one parent interval, so a surge
  crossing the window boundary between two 5-minute outputs reaches the nest as a slower,
  shallower rise. It is the boundary condition the available data supports - the parent keeps
  no sub-step history - and the fix is to record the parent's ring levels at the coupling's
  own cadence, which would mean the city run knowing about the nest.

**The CFL step binds differently at 5 m.** SPEC.md 11.3 sets ``dt = 0.7*dx/sqrt(g*h_max)``
clamped to [0.5, 10] s, and ``dx`` is six times smaller here. The upper clamp, which holds the
30 m grid at 10 s until about 1 cm of water stands anywhere on it, stops binding on a 5 m grid
at about 0.4 mm; the lower clamp, which the 30 m grid reaches only above 180 m of water, is
reached at 5 m above 5 m of water - still not a city, but no longer absurd. In between, the
nest takes six times as many sub-steps per output as the parent does for the same depth, on
1/36th of the parent's cells per parent cell covered. :attr:`NestRun.dt_min_s`,
:attr:`NestRun.dt_max_s` and :attr:`NestRun.n_steps` report what actually happened rather than
what this paragraph predicts.

**The audit.** SPEC.md 11.3 requires the mass balance inside 0.1 % of inflow, and the
decomposed :class:`~varuna_twin.types.MassBalance` that task P4.5 added is how a nest shows it:
a nest has no drain and no exchange, so ``drain_residual_m3``, ``inlet_gap_m3`` and
``surcharge_gap_m3`` are identically zero and the whole residual is ``surface_residual_m3``.
Anything non-zero in those three fields on a nest run is a bug in this module, not a finding.
The ledger is checked every :data:`AUDIT_EVERY` sub-steps and once at the end, and it raises
:class:`~varuna_twin.swe2d.MassBalanceError` rather than returning a run that means nothing.

**Determinism** (rule 8). Everything here is pure arithmetic on float64 in a fixed order: the
resampling is a single ``map_coordinates`` call, the ring is built by ``np.nonzero`` in row
order, and the sub-stepping loop is the stepper's own. Two runs on the same inputs produce the
same bits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import ceil
from time import perf_counter
from typing import TYPE_CHECKING, Literal

import numpy as np
import structlog

from varuna_twin.swe2d import (
    DRY_DEPTH_M,
    DT_MAX_S,
    MASS_BALANCE_MIN_VOLUME_M3,
    MASS_BALANCE_TOLERANCE,
    KernelTerrain,
    MassBalanceError,
    SurfaceStepper,
    cfl_dt,
    prepare_terrain,
)
from varuna_twin.types import MassBalance, SurfaceState, TerrainGrid

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datetime import datetime

    from numpy.typing import NDArray

log = structlog.get_logger("varuna.twin.nests")

__all__ = [
    "AUDIT_EVERY",
    "BoundaryMode",
    "Nest",
    "NestComparison",
    "NestRun",
    "NestSpec",
    "build_nest",
    "compare_with_parent",
    "run_nest",
]

AUDIT_EVERY = 100
"""Sub-steps between ledger audits inside :func:`run_nest` (SPEC.md 11.3)."""

BoundaryMode = Literal["parent", "closed"]
"""``"parent"`` imposes the city run's level on the ring; ``"closed"`` seals the window.

``"closed"`` exists for the SPEC.md 11.3 assertions - still water, conservation on a closed
basin, radial symmetry - which are statements about the kernel on this grid and would be
answered by the boundary rather than by the solver if the ring were driven.
"""


# ============================================================================ what a nest is
@dataclass(frozen=True, slots=True)
class NestSpec:
    """One nest as the city config and the hotspot register jointly define it.

    :func:`varuna_twin.city.load_nests` builds these: ``id``, ``size_m``, ``res_m`` and ``tier``
    come from ``services/city/configs/<city>.yaml``; ``lon``, ``lat``, ``register_name`` and
    ``source_url`` come from the register, never from the config's approximate literals
    (SPEC.md 3.3).
    """

    id: str
    name: str
    lon: float
    lat: float
    size_m: float
    res_m: float
    tier: str = "P1"
    hotspot_id: str | None = None
    """The register's own ``MUM-HS-nn``, when the config named one to match on."""

    register_name: str | None = None
    """The register's name for this point, which is usually longer than the config's label."""

    source_url: str | None = None
    """Where the register's coordinate was verified (rule 7). ``None`` means unsourced."""


@dataclass(frozen=True, slots=True)
class Nest:
    """A refined crop of the city grid, ready for the same kernel the city run uses.

    ``terrain`` is a full :class:`~varuna_twin.types.TerrainGrid` at :attr:`NestSpec.res_m` with
    its own affine transform, so everything that takes a terrain - the stepper, the hydrology,
    a raster writer - takes a nest without knowing it is one.
    """

    spec: NestSpec
    terrain: TerrainGrid
    row0: int
    """First parent row of the window."""

    col0: int
    """First parent column of the window."""

    n_parent: int
    """Window side in parent cells (square)."""

    refine: int
    """Parent cell size divided by nest cell size; an exact integer by construction."""

    parent_shape: tuple[int, int]
    parent_res_m: float
    centre_xy: tuple[float, float]
    """The register point projected into the parent CRS, in metres."""

    centre_rc: tuple[float, float]
    """The same point as fractional (row, col) on the parent grid."""

    z_overshoot_cells: int
    """Nest cells whose bicubic elevation lies outside its 3x3 parent neighbourhood's range.

    Bicubic interpolation is not monotone: across the +5 m cliff the city pipeline burns at a
    building edge (SPEC.md 10.1 step 4) it overshoots, which puts a rim above and a trough
    below the true step. A trough is a spurious depression that holds water no street holds, so
    this is counted rather than assumed small, and reported in :attr:`notes`."""

    notes: tuple[str, ...] = ()
    """Honesty labels this nest earned (rule 6), each one true of *this* nest's numbers."""

    @property
    def shape(self) -> tuple[int, int]:
        return self.terrain.shape

    @property
    def size_m(self) -> float:
        """The window's actual side length in metres, after snapping to parent cells."""
        return self.n_parent * self.parent_res_m

    def parent_slice(self) -> tuple[slice, slice]:
        """The parent rows and columns this nest covers."""
        return (
            slice(self.row0, self.row0 + self.n_parent),
            slice(self.col0, self.col0 + self.n_parent),
        )

    def coarsen(self, field: NDArray[np.floating]) -> NDArray[np.float64]:
        """Average a nest-grid field back onto the parent cells of the window.

        The refinement is exact, so this is the block mean of ``refine x refine`` children and
        it is what makes a like-for-like comparison with the parent possible: the parent cell
        holds one depth over 900 m2 and the nest holds 36 depths over the same 900 m2, so the
        only fair scalar to set against the parent's is their mean.
        """
        data = np.asarray(field, dtype=np.float64)
        if data.shape != self.shape:
            raise ValueError(f"field has shape {data.shape}, expected {self.shape}")
        r = self.refine
        return data.reshape(self.n_parent, r, self.n_parent, r).mean(axis=(1, 3))


# ============================================================================ building a nest
def build_nest(parent: TerrainGrid, spec: NestSpec) -> Nest:
    """Crop the city grid around the nest centre and refine it to ``spec.res_m``.

    The elevation, roughness, imperviousness and curve-number rasters are resampled bicubically
    (SPEC.md 10.1 step 1) from the **full** parent arrays rather than from the crop, so the
    4 x 4 bicubic stencil of a cell near the window edge reads the parent cells just outside the
    window instead of a clamped copy of the edge - a clamped stencil tilts the first few metres
    of the nest inwards, which is precisely where the boundary condition is imposed.

    ``blocked`` is block-replicated, not interpolated: it is a mask, and a bicubic building is
    a building with a soft edge. That does mean a nest building keeps the 30 m footprint it had
    in the parent, stepped at 5 m - the footprint is not refined, only redrawn.

    Raises:
        ValueError: the parent resolution is not an integer multiple of the nest resolution,
            the centre falls outside the parent grid, or the window is larger than the grid.
    """
    from rasterio.warp import transform as warp_transform
    from scipy.ndimage import map_coordinates, maximum_filter, minimum_filter

    if spec.res_m <= 0.0:
        raise ValueError(f"nest {spec.id}: res_m must be positive, got {spec.res_m}")
    ratio = float(parent.res_m) / float(spec.res_m)
    refine = round(ratio)
    if refine < 1 or abs(ratio - refine) > 1e-9:
        msg = (
            f"nest {spec.id}: the parent grid is {parent.res_m} m and the nest asks for "
            f"{spec.res_m} m, a ratio of {ratio:.6f}. The refinement must be a whole number "
            "or a nest cell does not sit inside exactly one parent cell, and neither the rain "
            "replication nor the comparison with the parent is well defined."
        )
        raise ValueError(msg)

    xs, ys = warp_transform("EPSG:4326", parent.crs, [float(spec.lon)], [float(spec.lat)])
    x, y = float(xs[0]), float(ys[0])
    res, _b, left, _d, _e, top = parent.transform
    col_f = (x - left) / float(res)
    row_f = (top - y) / float(res)
    if not (0.0 <= row_f < parent.n_rows and 0.0 <= col_f < parent.n_cols):
        msg = (
            f"nest {spec.id}: the register point ({spec.lon}, {spec.lat}) projects to "
            f"row {row_f:.1f}, col {col_f:.1f}, which is outside the "
            f"{parent.n_rows} x {parent.n_cols} city grid"
        )
        raise ValueError(msg)

    n_parent = ceil(float(spec.size_m) / float(parent.res_m))
    if n_parent > min(parent.n_rows, parent.n_cols):
        msg = (
            f"nest {spec.id}: a {spec.size_m:.0f} m window needs {n_parent} parent cells but "
            f"the city grid is only {parent.n_rows} x {parent.n_cols}"
        )
        raise ValueError(msg)

    notes: list[str] = []
    row0 = round(row_f - n_parent / 2.0)
    col0 = round(col_f - n_parent / 2.0)
    clamped_row0 = min(max(row0, 0), parent.n_rows - n_parent)
    clamped_col0 = min(max(col0, 0), parent.n_cols - n_parent)
    if (clamped_row0, clamped_col0) != (row0, col0):
        shift_m = max(abs(clamped_row0 - row0), abs(clamped_col0 - col0)) * float(parent.res_m)
        notes.append(
            f"The window was pushed {shift_m:.0f} m to stay inside the city grid, so the nest "
            "is not centred on the register point."
        )
        row0, col0 = clamped_row0, clamped_col0

    # A nest has no sea boundary. Its ring imposes the parent's depth and its interior is land
    # under rain, so a window holding any of the city's sea (``terrain.sea``) would treat seawater
    # held at the tide as street water. Refused rather than run: Mumbai's two nests are 1,549 m
    # (Hindmata) and 2,154 m (King's Circle) from the nearest sea cell of the rebuilt coastline,
    # against a half-width of about 510 m, so this guards a nest nobody has placed yet.
    sea = getattr(parent, "sea", None)
    if sea is not None:
        on_sea = int(
            np.count_nonzero(np.asarray(sea)[row0 : row0 + n_parent, col0 : col0 + n_parent])
        )
        if on_sea:
            msg = (
                f"nest {spec.id}: the window holds {on_sea} of the city's sea cells, and a nest "
                "has no sea boundary - its interior would take the sea held at the tide for "
                "street water. Place it inland, or give the nest a sea boundary first."
            )
            raise ValueError(msg)

    n_nest = n_parent * refine
    # Nest cell centres as fractional parent indices. Cell (i, j) of the nest has its centre at
    # parent index row0 + (i + 0.5)/refine - 0.5, because parent index k addresses the *centre*
    # of parent cell k while the window starts at its edge.
    offset = (np.arange(n_nest, dtype=np.float64) + 0.5) / refine - 0.5
    rows = row0 + offset
    cols = col0 + offset
    grid_r, grid_c = np.meshgrid(rows, cols, indexing="ij")
    coords = np.stack((grid_r.ravel(), grid_c.ravel()))

    def _bicubic(field: NDArray[np.floating]) -> NDArray[np.float64]:
        out = map_coordinates(
            np.asarray(field, dtype=np.float64),
            coords,
            order=3,
            mode="nearest",
            prefilter=True,
        )
        return np.ascontiguousarray(out.reshape(n_nest, n_nest), dtype=np.float64)

    z = _bicubic(parent.z)
    manning_n = _bicubic(parent.manning_n)
    imperviousness = np.clip(_bicubic(parent.imperviousness), 0.0, 1.0)
    cn = _bicubic(parent.cn)
    window = (slice(row0, row0 + n_parent), slice(col0, col0 + n_parent))
    blocked = np.repeat(np.repeat(np.asarray(parent.blocked)[window], refine, 0), refine, 1)
    blocked = np.ascontiguousarray(blocked, dtype=np.bool_)

    # How far the bicubic interpolant left the range its parent neighbourhood allows. The 3x3
    # filters are taken on the whole parent grid for the same reason the resampling is: a
    # window-edge cell's neighbourhood includes parent cells outside the window.
    z_parent = np.asarray(parent.z, dtype=np.float64)
    lo = np.repeat(np.repeat(minimum_filter(z_parent, size=3)[window], refine, 0), refine, 1)
    hi = np.repeat(np.repeat(maximum_filter(z_parent, size=3)[window], refine, 0), refine, 1)
    overshoot = int(np.count_nonzero((z < lo - 1e-9) | (z > hi + 1e-9)))
    if overshoot:
        worst = float(np.max(np.maximum(lo - z, z - hi)))
        notes.append(
            f"Bicubic resampling put {overshoot} of {z.size} nest cells "
            f"({100.0 * overshoot / z.size:.1f} %) outside the elevation range of their 3x3 "
            f"parent neighbourhood, by up to {worst:.2f} m. Interpolation is not monotone "
            "across the building burn, so some of those cells are troughs that hold water no "
            "street holds."
        )
    notes.append(
        "The 5 m DEM is a bicubic interpolation of the 30 m conditioned DEM (SPEC.md 10.1 "
        "step 1), not a 5 m survey: the nest resolves flow at 5 m on terrain that still only "
        "knows 30 m features."
    )
    notes.append(
        "The nest is surface-only. It has no drain graph and no inlet capture, so its depths "
        "are what the street holds before the pipes take any of it."
    )

    terrain = TerrainGrid(
        z=z,
        manning_n=manning_n,
        blocked=blocked,
        imperviousness=imperviousness,
        cn=cn,
        res_m=float(spec.res_m),
        crs=parent.crs,
        transform=(
            float(spec.res_m),
            0.0,
            float(left) + col0 * float(parent.res_m),
            0.0,
            -float(spec.res_m),
            float(top) - row0 * float(parent.res_m),
        ),
    )
    nest = Nest(
        spec=spec,
        terrain=terrain,
        row0=row0,
        col0=col0,
        n_parent=n_parent,
        refine=refine,
        parent_shape=parent.shape,
        parent_res_m=float(parent.res_m),
        centre_xy=(x, y),
        centre_rc=(row_f, col_f),
        z_overshoot_cells=overshoot,
        notes=tuple(notes),
    )
    log.info(
        "twin.nest.built",
        nest=spec.id,
        centre=f"{spec.lon:.6f}, {spec.lat:.6f}",
        register=spec.register_name,
        window_m=round(nest.size_m, 1),
        shape=f"{n_nest} x {n_nest}",
        refine=refine,
        blocked_fraction=round(float(blocked.mean()), 4),
        z_overshoot_cells=overshoot,
    )
    return nest


# ============================================================================ running a nest
@dataclass(frozen=True, slots=True)
class NestRun:
    """One nest run: depth at the parent's own output times, plus the volume audit."""

    nest: Nest
    depth_m: NDArray[np.float64]
    """``(n_steps, n, n)`` surface depth in metres, at :attr:`times`."""

    times: tuple[datetime, ...]
    mass_balance: MassBalance
    n_steps: int
    """CFL sub-steps taken across the whole run, not output steps."""

    dt_min_s: float
    dt_max_s: float
    elapsed_ms: int
    volume_rain_m3: float
    volume_boundary_in_m3: float
    volume_boundary_out_m3: float
    volume_created_m3: float
    """Water the kernel's positivity clamp had to invent. Zero unless the scheme has a bug."""

    n_boundary_cells: int
    ring_ground_gap_m: float = 0.0
    """Largest ``|z_parent - z_nest|`` on the ring: how far the nest's water surface can sit
    from the parent's, given that the ring imposes depth and not level. Zero on a closed run."""

    notes: tuple[str, ...] = ()


def run_nest(
    nest: Nest,
    *,
    r_eff_ms: NDArray[np.floating] | float,
    parent_terrain: TerrainGrid | None = None,
    parent_depth_m: NDArray[np.floating] | None = None,
    parent_depth_t0: NDArray[np.floating] | None = None,
    t0: datetime | None = None,
    step_min: int = 5,
    n_steps: int | None = None,
    boundary: BoundaryMode = "parent",
    halo_cells: int = 1,
    max_dt_s: float = DT_MAX_S,
    audit_every: int = AUDIT_EVERY,
    initial_h: NDArray[np.floating] | None = None,
) -> NestRun:
    """Run the 2D kernel over the nest, with the city run holding its boundary.

    The loop is the city run's loop at a finer grid: one iteration per ``step_min`` output, rain
    frozen across it, the surface sub-stepped under the CFL rule, a snapshot at the end. The one
    addition is the Dirichlet ring, imposed at the start of the run and again after every
    sub-step - the same double application ``swe2d._step`` makes for the sea boundary, and for
    the same reason: a level that is only applied after continuity has no head difference across
    the interface for the next flux update to see, so every change at the boundary would arrive
    one sub-step late.

    Args:
        nest: from :func:`build_nest`.
        r_eff_ms: effective rain in m/s (SPEC.md 11.2, already through the hydrology), as a
            scalar, one raster, or one raster per output step. A parent-shaped raster is
            block-replicated onto the nest, which is exact: every nest cell takes its parent
            cell's rate, so the window receives precisely the rain the parent gave that window.
            Rain is a 500 m Sky field resampled to 30 m; there is no 5 m rain to replicate.
        parent_terrain: the city grid the nest was cut from. Required for ``boundary="parent"``.
        parent_depth_m: ``(n_steps, parent rows, parent cols)`` depth from the city run, at
            ``t0 + (i+1)*step_min`` - ``TwinResult.depth_m`` and its ``times``, unchanged.
        parent_depth_t0: the parent's depth at ``t0`` itself, which ``TwinResult`` does not
            carry because its first snapshot is one step in. ``None`` means a dry city at
            ``t0``, which is what a cold city run started from.
        n_steps: output steps to run. Defaults to the parent series' length.
        boundary: ``"parent"`` or ``"closed"`` (:data:`BoundaryMode`).
        halo_cells: depth of the Dirichlet ring in nest cells. One cell is the boundary; more
            than one pins water further inside the window and shrinks what the nest is free to
            compute.
        initial_h: depth to start from, nest-shaped. ``None`` starts dry.

    Returns:
        A :class:`NestRun`.

    Raises:
        ValueError: the inputs disagree with the nest's grid, or ``boundary="parent"`` was asked
            for without a parent run.
        MassBalanceError: the ledger left SPEC.md 11.3's 0.1 % budget.
    """
    started = perf_counter()
    kernel = prepare_terrain(nest.terrain)
    shape = kernel.shape
    step_s = float(step_min) * 60.0
    if step_s <= 0.0:
        raise ValueError(f"step_min must be positive, got {step_min}")

    levels, n_parent_steps = _boundary_levels(
        nest,
        kernel,
        boundary,
        parent_terrain,
        parent_depth_m,
        parent_depth_t0,
        halo_cells,
        step_s,
    )
    total_steps = _output_steps(n_steps, n_parent_steps, r_eff_ms)
    rain = _rain_series(nest, r_eff_ms, total_steps)

    state = SurfaceState(
        h=np.zeros(shape, dtype=np.float64) if initial_h is None else _depth(initial_h, shape),
        qx=np.zeros(shape, dtype=np.float64),
        qy=np.zeros(shape, dtype=np.float64),
    )
    # The stepper's own periodic audit is switched off and replaced by this module's, because
    # its ledger cannot see the ring: water imposed on a boundary cell is neither rain nor
    # surcharge nor sea, so every audit it ran would report the boundary exchange as a leak.
    stepper = SurfaceStepper(state, kernel, sea_mask=None, audit_every=0)
    zeros = np.zeros(shape, dtype=np.float64)

    area = kernel.cell_area_m2
    stored_start = state.volume_m3(area)
    boundary_in = 0.0
    boundary_out = 0.0
    if levels is not None:
        gained, lost = _impose(state.h, levels.target_at(0.0), levels.index, area)
        boundary_in += gained
        boundary_out += lost
        stored_start = state.volume_m3(area)

    depth = np.empty((total_steps, shape[0], shape[1]), dtype=np.float64)
    times: list[datetime] = []
    elapsed_s = 0.0
    audited_at = 0
    rain_m3 = 0.0
    created_m3 = 0.0

    for step_idx in range(total_steps):
        stepper.set_rain(rain[step_idx])
        target_s = (step_idx + 1) * step_s
        while elapsed_s < target_s - _TIME_EPS_S:
            # One sub-step per iteration, because the ring has to be re-imposed after each
            # continuity update: `max_dt_s=dt` makes the stepper's own CFL choice land on this
            # `dt` exactly, so `advance` takes one step and not two.
            dt = min(cfl_dt(state.h, kernel.res_m, max_dt_s), target_s - elapsed_s)
            if not dt > 0.0:
                # `cfl_dt` divides by sqrt(g * h_max), so a NaN anywhere in the depth comes back
                # as a NaN step. Written `not dt > 0.0` for `_audit`'s reason: `nan > 0.0` is
                # false, as is every other comparison with it. Without this the run does not
                # crash - it *stops*: `elapsed_s += nan` makes the loop condition false, every
                # remaining output step repeats the same non-step, and the ledger closes at
                # 0.0 % because `SurfaceState.volume_m3` sums with `nansum` and never sees the
                # cell that broke. A field of NaN would be returned with a clean audit.
                raise MassBalanceError(
                    f"nest {nest.spec.id}: the CFL rule returned {dt} at sub-step "
                    f"{stepper.n_steps}, so the depth is no longer finite. The run is stopped "
                    "here rather than returned: a non-finite depth makes the sub-stepping loop "
                    "exit silently and the volume audit, which sums with nansum, would report "
                    "it as conserved."
                )
            moved = stepper.advance(dt, q_inlet_ms=zeros, q_surcharge_ms=zeros, max_dt_s=dt)
            rain_m3 += moved.volume_rain_m3
            created_m3 += moved.volume_created_m3
            elapsed_s += dt
            if levels is not None:
                gained, lost = _impose(state.h, levels.target_at(elapsed_s), levels.index, area)
                boundary_in += gained
                boundary_out += lost
            if audit_every > 0 and stepper.n_steps - audited_at >= audit_every:
                _audit(
                    state.volume_m3(area),
                    stored_start,
                    rain_m3,
                    created_m3,
                    boundary_in,
                    boundary_out,
                    stepper.n_steps,
                )
                audited_at = stepper.n_steps
        depth[step_idx] = state.h
        if t0 is not None:
            times.append(t0 + timedelta(minutes=(step_idx + 1) * step_min))

    surface = stepper.summary()
    stored_end = state.volume_m3(area)
    _audit(
        stored_end, stored_start, rain_m3, created_m3, boundary_in, boundary_out, stepper.n_steps
    )

    volume_in = rain_m3 + boundary_in + created_m3
    residual = (stored_end - stored_start) - (volume_in - boundary_out)
    notes = list(nest.notes) + list(surface.notes)
    if levels is not None:
        notes.append(
            f"The parent held {levels.index[0].size} ring cells at its own depth, interpolated "
            f"linearly between {step_min}-minute outputs: {boundary_in:.0f} m3 in, "
            f"{boundary_out:.0f} m3 out. Depth and not water level, because the nest's ground "
            f"is a bicubic resampling of the parent's and the two disagree by up to "
            f"{levels.z_gap_m:.2f} m on this ring; the nest's water surface therefore sits up "
            "to that far from the parent's."
        )
    else:
        notes.append("The window was run closed: no water crossed its edge in either direction.")

    run = NestRun(
        nest=nest,
        depth_m=depth,
        times=tuple(times),
        mass_balance=MassBalance(
            volume_in_m3=volume_in,
            volume_out_m3=boundary_out,
            volume_stored_m3=stored_end,
            error_fraction=abs(residual) / volume_in if volume_in > 0.0 else 0.0,
            volume_stored_start_m3=stored_start,
            residual_m3=residual,
            residual_limit_m3=(
                MASS_BALANCE_MIN_VOLUME_M3 if volume_in <= MASS_BALANCE_MIN_VOLUME_M3 else None
            ),
            # A nest has no pipes and no exchange, so the whole residual is the surface's own.
            surface_residual_m3=residual,
        ),
        n_steps=stepper.n_steps,
        dt_min_s=surface.dt_min_s,
        dt_max_s=surface.dt_max_s,
        elapsed_ms=round((perf_counter() - started) * 1000.0),
        volume_rain_m3=rain_m3,
        volume_boundary_in_m3=boundary_in,
        volume_boundary_out_m3=boundary_out,
        volume_created_m3=created_m3,
        n_boundary_cells=0 if levels is None else int(levels.index[0].size),
        ring_ground_gap_m=0.0 if levels is None else levels.z_gap_m,
        notes=tuple(notes),
    )
    log.info(
        "twin.nest.run",
        nest=nest.spec.id,
        output_steps=total_steps,
        sub_steps=run.n_steps,
        dt_min_s=round(run.dt_min_s, 3),
        dt_max_s=round(run.dt_max_s, 3),
        boundary=boundary,
        error_fraction=run.mass_balance.error_fraction,
        elapsed_ms=run.elapsed_ms,
    )
    return run


# ============================================================================ comparison
@dataclass(frozen=True, slots=True)
class NestComparison:
    """What the finer grid changed, on the parent's own cells.

    This is the answer to the question SPEC.md 2.3 says the MoES panel asks: what does 30 m
    cost you. Every figure is computed after :meth:`Nest.coarsen` has averaged the nest back
    onto the parent cells of the window, because that is the only like-for-like comparison - a
    5 m cell and a 30 m cell do not describe the same piece of street.
    """

    n_cells: int
    mean_abs_diff_m: float
    max_abs_diff_m: float
    bias_m: float
    """Nest minus parent, signed: positive means the nest holds more water."""

    parent_peak_m: float
    nest_peak_m: float
    parent_at_centre_m: float
    nest_at_centre_m: float
    """The nest's block mean over the parent cell holding the register point."""

    nest_cell_max_at_centre_m: float
    """The deepest single 5 m cell inside that parent cell, which is what a street sees."""


def compare_with_parent(
    nest: Nest,
    nest_depth: NDArray[np.floating],
    parent_depth: NDArray[np.floating],
) -> NestComparison:
    """Compare one nest depth field with the parent's over the same window."""
    coarse = nest.coarsen(nest_depth)
    window = nest.parent_slice()
    parent = np.asarray(parent_depth, dtype=np.float64)
    if parent.shape != nest.parent_shape:
        raise ValueError(f"parent_depth has shape {parent.shape}, expected {nest.parent_shape}")
    crop = parent[window]
    diff = coarse - crop
    row_c = min(max(int(nest.centre_rc[0]) - nest.row0, 0), nest.n_parent - 1)
    col_c = min(max(int(nest.centre_rc[1]) - nest.col0, 0), nest.n_parent - 1)
    r = nest.refine
    centre_block = np.asarray(nest_depth, dtype=np.float64)[
        row_c * r : (row_c + 1) * r, col_c * r : (col_c + 1) * r
    ]
    return NestComparison(
        n_cells=int(crop.size),
        mean_abs_diff_m=float(np.mean(np.abs(diff))),
        max_abs_diff_m=float(np.max(np.abs(diff))),
        bias_m=float(np.mean(diff)),
        parent_peak_m=float(np.max(crop)),
        nest_peak_m=float(np.max(coarse)),
        parent_at_centre_m=float(crop[row_c, col_c]),
        nest_at_centre_m=float(coarse[row_c, col_c]),
        nest_cell_max_at_centre_m=float(np.max(centre_block)),
    )


# ============================================================================ internals
_TIME_EPS_S = 1e-9
"""Slack on the sub-stepping loop, in seconds: an output time is reached, not overshot."""


@dataclass(frozen=True, slots=True)
class _RingLevels:
    """The parent's ring state, pre-interpolated in space and lerped in time on demand.

    The spatial interpolation is done once per parent snapshot, for the few hundred ring cells,
    rather than once per sub-step for a whole raster: a three-hour nest run takes thousands of
    sub-steps and would otherwise pay a ``map_coordinates`` call for each.
    """

    index: tuple[NDArray[np.intp], NDArray[np.intp]]
    depth: NDArray[np.float64]
    """``(n_snapshots, n_ring)`` parent depth, bilinear at the ring cell centres."""

    z_gap_m: float
    """Largest ``|z_parent - z_nest|`` on the ring, in metres: what level-matching would cost.

    Reported rather than used. It is the measurement that decided this boundary condition - the
    two interpolants of the same 30 m terrain reach 4.09 m apart across a burned building edge
    on the Hindmata window - and it belongs beside the run so a depth quoted from a nest can be
    read with the knowledge that its water surface sits up to this far from the parent's.
    """

    step_s: float

    def target_at(self, elapsed_s: float) -> NDArray[np.float64]:
        """The depth to impose on the ring at ``elapsed_s`` seconds into the run."""
        n = self.depth.shape[0]
        raw = elapsed_s / self.step_s
        lo = min(int(raw), n - 1)
        hi = min(lo + 1, n - 1)
        w = 0.0 if hi == lo else raw - lo
        depth = self.depth[lo] * (1.0 - w) + self.depth[hi] * w
        # The wet/dry gate: a parent film at or below the solver's own dry threshold leaves the
        # ring dry rather than being written onto 36 nest cells at every sub-step of the run.
        return np.where(depth > DRY_DEPTH_M, depth, 0.0)


def _boundary_levels(
    nest: Nest,
    kernel: KernelTerrain,
    boundary: BoundaryMode,
    parent_terrain: TerrainGrid | None,
    parent_depth_m: NDArray[np.floating] | None,
    parent_depth_t0: NDArray[np.floating] | None,
    halo_cells: int,
    step_s: float,
) -> tuple[_RingLevels | None, int]:
    """Build the Dirichlet ring, or nothing when the window is run closed."""
    if boundary == "closed":
        return None, 0
    if boundary != "parent":
        raise ValueError(f"boundary must be 'parent' or 'closed', got {boundary!r}")
    if parent_terrain is None or parent_depth_m is None:
        raise ValueError(
            "boundary='parent' needs parent_terrain and parent_depth_m: the ring holds the "
            "city run's own depth, and there is nothing to fall back on"
        )
    if halo_cells < 1:
        raise ValueError(f"halo_cells must be at least 1, got {halo_cells}")

    from scipy.ndimage import map_coordinates

    series = np.asarray(parent_depth_m, dtype=np.float64)
    if series.ndim != 3 or series.shape[1:] != nest.parent_shape:
        raise ValueError(
            f"parent_depth_m has shape {series.shape}, expected (n_steps, "
            f"{nest.parent_shape[0]}, {nest.parent_shape[1]})"
        )
    first = (
        np.zeros(nest.parent_shape, dtype=np.float64)
        if parent_depth_t0 is None
        else _owned(parent_depth_t0, nest.parent_shape)
    )
    series = np.concatenate((first[None, ...], series), axis=0)

    n_rows, n_cols = kernel.shape
    mask = np.zeros(kernel.shape, dtype=np.bool_)
    mask[:halo_cells, :] = True
    mask[n_rows - halo_cells :, :] = True
    mask[:, :halo_cells] = True
    mask[:, n_cols - halo_cells :] = True
    rows, cols = np.nonzero(mask & ~kernel.blocked)
    if rows.size == 0:
        raise ValueError(
            f"nest {nest.spec.id}: every boundary cell is a building, so the city run has no "
            "way into the window"
        )

    row_offset = (np.arange(n_rows, dtype=np.float64) + 0.5) / nest.refine - 0.5
    col_offset = (np.arange(n_cols, dtype=np.float64) + 0.5) / nest.refine - 0.5
    coords = np.stack((nest.row0 + row_offset[rows], nest.col0 + col_offset[cols]))
    z_parent = map_coordinates(
        np.asarray(parent_terrain.z, dtype=np.float64), coords, order=1, mode="nearest"
    )
    depth = np.empty((series.shape[0], rows.size), dtype=np.float64)
    for i in range(series.shape[0]):
        depth[i] = map_coordinates(series[i], coords, order=1, mode="nearest")
    z_gap = float(np.max(np.abs(z_parent - kernel.z[rows, cols])))
    if z_gap > 0.25:
        log.info(
            "twin.nest.ground_gap",
            nest=nest.spec.id,
            max_gap_m=round(z_gap, 3),
            note="the parent's ground and the nest's bicubic resampling of it disagree by this "
            "much on the ring; the ring imposes depth, so the nest's water surface sits up to "
            "this far from the parent's",
        )
    return (
        _RingLevels(
            index=(rows, cols),
            depth=depth,
            z_gap_m=z_gap,
            step_s=step_s,
        ),
        int(series.shape[0] - 1),
    )


def _impose(
    h: NDArray[np.float64],
    target: NDArray[np.float64],
    index: tuple[NDArray[np.intp], NDArray[np.intp]],
    cell_area_m2: float,
) -> tuple[float, float]:
    """Set the ring to ``target`` and return the volume that crossed, in and out.

    The arithmetic is ``swe2d._apply_tide``'s, with a target that varies along the ring instead
    of one sea stage. Both directions are counted because both are water crossing the model's
    edge, and a boundary whose outflow is not counted cannot be audited (SPEC.md 11.3).
    """
    rows, cols = index
    delta = target - h[rows, cols]
    h[rows, cols] = target
    return (
        float(np.sum(np.maximum(delta, 0.0))) * cell_area_m2,
        -float(np.sum(np.minimum(delta, 0.0))) * cell_area_m2,
    )


def _audit(
    stored_m3: float,
    stored_start_m3: float,
    rain_m3: float,
    created_m3: float,
    boundary_in_m3: float,
    boundary_out_m3: float,
    n_steps: int,
) -> None:
    """SPEC.md 11.3's 0.1 % assertion over the nest's own ledger.

    Written ``not (error < budget)`` rather than ``error >= budget`` for the reason
    ``swe2d._audit`` gives: every comparison with NaN is false, and the one check whose job is
    to stop a meaningless run must not be the check a NaN slips through.
    """
    total_in = stored_start_m3 + rain_m3 + boundary_in_m3 + created_m3
    if total_in <= MASS_BALANCE_MIN_VOLUME_M3:
        return
    error = abs(stored_m3 - (total_in - boundary_out_m3)) / total_in
    if not error < MASS_BALANCE_TOLERANCE:
        raise MassBalanceError(
            f"nest mass balance broke at sub-step {n_steps}: stored {stored_m3:.3f} m3 against "
            f"in {total_in:.3f} m3 minus out {boundary_out_m3:.3f} m3, error {error:.3%}; the "
            f"budget is {MASS_BALANCE_TOLERANCE:.1%}"
        )


def _output_steps(
    n_steps: int | None,
    n_parent_steps: int,
    r_eff_ms: NDArray[np.floating] | float,
) -> int:
    """How many ``step_min`` outputs to produce, from whichever input said so."""
    if n_steps is not None:
        if n_steps < 1:
            raise ValueError(f"n_steps must be at least 1, got {n_steps}")
        return int(n_steps)
    if n_parent_steps:
        return n_parent_steps
    rain = np.asarray(r_eff_ms)
    if rain.ndim == 3:
        return int(rain.shape[0])
    raise ValueError(
        "n_steps was not given and nothing else says how long to run: pass parent_depth_m, a "
        "rain series with a leading step axis, or n_steps"
    )


def _rain_series(
    nest: Nest,
    r_eff_ms: NDArray[np.floating] | float,
    n_steps: int,
) -> list[NDArray[np.float64]]:
    """One nest-grid rain raster per output step, in m/s.

    A parent-shaped raster is block-replicated rather than interpolated. That is exact - the
    window receives precisely the volume the parent gave it - and it is also honest: the rain
    reaching this grid is a 500 m Sky field already resampled to 30 m (SPEC.md 11.1), so
    smoothing it again at 5 m would draw structure the forecast never had.
    """
    shape = nest.shape
    if np.isscalar(r_eff_ms):
        return [np.full(shape, float(r_eff_ms), dtype=np.float64)] * n_steps  # type: ignore[arg-type]
    rain = np.asarray(r_eff_ms, dtype=np.float64)
    if rain.ndim == 2:
        rain = rain[None, ...]
    if rain.ndim != 3:
        raise ValueError(f"r_eff_ms has {rain.ndim} dimensions, expected 2 or 3")
    if rain.shape[0] not in (1, n_steps):
        raise ValueError(f"r_eff_ms has {rain.shape[0]} steps but the run is {n_steps} steps long")
    frames = []
    for i in range(n_steps):
        frame = rain[0] if rain.shape[0] == 1 else rain[i]
        if frame.shape == shape:
            frames.append(np.ascontiguousarray(frame, dtype=np.float64))
        elif frame.shape == nest.parent_shape:
            crop = frame[nest.parent_slice()]
            frames.append(
                np.ascontiguousarray(
                    np.repeat(np.repeat(crop, nest.refine, 0), nest.refine, 1), dtype=np.float64
                )
            )
        else:
            raise ValueError(
                f"r_eff_ms frame has shape {frame.shape}, expected the nest's {shape} or the "
                f"parent's {nest.parent_shape}"
            )
    return frames


def _owned(array: NDArray[np.floating], shape: tuple[int, int]) -> NDArray[np.float64]:
    """A C-contiguous float64 copy of the caller's array, shape checked."""
    out = np.ascontiguousarray(array, dtype=np.float64)
    if out.shape != shape:
        raise ValueError(f"array has shape {out.shape}, expected {shape}")
    return out


def _depth(array: NDArray[np.floating], shape: tuple[int, int]) -> NDArray[np.float64]:
    """:func:`_owned` plus the checks a starting depth has to pass to mean anything.

    Non-finite is refused here for the same reason ``SurfaceStepper._exchange`` refuses it in an
    exchange raster - the solver cannot use it - and for one more that is specific to this loop:
    a NaN depth makes :func:`~varuna_twin.swe2d.cfl_dt` return NaN, which stops the sub-stepping
    without raising, and the volume audit cannot catch it because ``nansum`` steps over the very
    cell that broke. Negative is refused because a negative depth is not a shallower puddle, it
    is a hole in the ledger that the kernel's positivity clamp would then "create" water to fill.
    """
    out = _owned(array, shape)
    if not np.isfinite(out).all():
        raise ValueError(
            "initial_h contains non-finite values; the solver cannot use it, and a NaN depth "
            "stops the CFL loop without raising"
        )
    if float(out.min()) < 0.0:
        raise ValueError(f"initial_h has a negative depth ({float(out.min()):.3e} m)")
    return out

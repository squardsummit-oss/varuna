"""The types VARUNA-Twin's surface, drain and coupling solvers pass to each other
(SPEC.md 11.3-11.5, Appendix A).

Twin is two solvers that exchange water every few seconds: a local-inertial shallow-water
model on the 30 m city grid, and a head-driven 1D model on the inferred drain graph. The
structures here are what they hand each other, and they are shaped for the solver, not for
the API: **struct of arrays**, integer indices, no Python objects inside a hot loop, so the
kernels can be compiled with ``numba.njit``. The Pydantic contract surface that leaves the
service lives in ``varuna_schemas.models`` instead.

Layering: Twin depends on ``varuna_schemas`` and nothing else in the workspace. Georeference
and terrain arrive from the city rasters' own affine transform; the drain graph arrives from
``city/<city>/graph/*.parquet``. Neither is guessed.

Units, fixed once here:

* depth ``h`` and head ``H`` are **metres**; the API converts to cm at the boundary;
* elevations ``z`` are metres in the DEM's vertical datum;
* discharge ``Q`` is m3/s, unit-width flux ``q`` is m2/s, rain is m/s inside the solver
  (converted from mm/h once, at the edge);
* time is seconds inside the solver, IST datetimes on the contract surface;
* grids are ``(row, col)`` with row 0 northernmost, matching the GeoTIFFs and ``varuna_city``.

Sign conventions that have bitten this kind of model before, fixed here:

* ``qx`` is the flux through the **eastern** face of a cell, positive eastward;
* ``qy`` is the flux through the **southern** face of a cell, positive southward
  (i.e. toward increasing row), so both fluxes point toward increasing index;
* on a drain edge, ``Q`` is positive when water flows ``from_node -> to_node``. Negative ``Q``
  is backflow, which is exactly what a tide-locked outfall produces and what the console
  draws as a reversed-flow edge (SPEC.md 6.2, 7.2).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover - kept off the type surface, as varuna_schemas does
    import numpy as np
    from numpy.typing import NDArray

__all__ = [
    "BoundaryKind",
    "CouplingFluxes",
    "DrainNetwork",
    "DrainState",
    "MassBalance",
    "SurfaceState",
    "TerrainGrid",
    "TideSeries",
    "TwinFingerprint",
    "TwinInputs",
    "TwinResult",
    "TwinState",
]

GRAVITY = 9.81
"""Acceleration due to gravity in m/s2, used by every formula in Appendix A."""


# ============================================================================ terrain
@dataclass(frozen=True, slots=True)
class TerrainGrid:
    """The hydro-conditioned city grid the 2D solver runs on (SPEC.md 10.1 step 4).

    Every array is ``(n_rows, n_cols)`` float32/float64 on the same grid, read from
    ``city/<city>/*.tif``. ``blocked`` cells are buildings: no flux crosses their faces and
    they never hold water.
    """

    z: NDArray[np.floating]
    """Conditioned ground elevation in metres (``dem_conditioned.tif``)."""

    manning_n: NDArray[np.floating]
    """Manning roughness per cell (``roughness.tif``)."""

    blocked: NDArray[np.bool_]
    """True where a building blocks flow (``blocked.tif``)."""

    imperviousness: NDArray[np.floating]
    """Impervious fraction 0-1 (``imperviousness.tif``)."""

    cn: NDArray[np.floating]
    """SCS curve number 90-98 (``cn.tif``)."""

    res_m: float
    """Cell size in metres (30 for the city grid)."""

    crs: str
    """EPSG string of the computation CRS, e.g. ``EPSG:32643``."""

    transform: tuple[float, float, float, float, float, float]
    """North-up affine ``(res, 0, left, 0, -res, top)``, from the GeoTIFF."""

    @property
    def shape(self) -> tuple[int, int]:
        return (int(self.z.shape[0]), int(self.z.shape[1]))

    @property
    def n_rows(self) -> int:
        return int(self.z.shape[0])

    @property
    def n_cols(self) -> int:
        return int(self.z.shape[1])

    @property
    def cell_area_m2(self) -> float:
        return float(self.res_m * self.res_m)


# ============================================================================ surface
@dataclass(slots=True)
class SurfaceState:
    """Mutable state of the 2D solver: depth and the two face fluxes.

    ``h`` is ``(n_rows, n_cols)`` in metres. ``qx`` and ``qy`` are unit-width fluxes in m2/s
    on the eastern and southern faces, so they are also ``(n_rows, n_cols)`` with the last
    column / last row holding the domain-edge face.
    """

    h: NDArray[np.floating]
    qx: NDArray[np.floating]
    qy: NDArray[np.floating]

    def volume_m3(self, cell_area_m2: float) -> float:
        """Water currently stored on the surface."""
        import numpy as np

        return float(np.nansum(self.h) * cell_area_m2)


# ============================================================================ drains
BoundaryKind = Literal["none", "tidal", "free"]
"""How a node exchanges water with the world beyond the model."""


@dataclass(frozen=True, slots=True)
class DrainNetwork:
    """The inferred drain graph as flat arrays, ready for a compiled kernel.

    Nodes and edges are addressed by integer index. ``node_ids`` and ``edge_ids`` keep the
    string ids (``MUM-N000000``) so a product can be joined back to
    ``city/<city>/graph/*.parquet``; the solver itself never sees a string.

    Every element carries ``confidence = "inferred"`` upstream (SPEC.md 10.1 step 7), which
    is why the UI must label the graph inferred everywhere it is drawn.
    """

    # --- nodes -------------------------------------------------------------
    node_ids: tuple[str, ...]
    z_ground: NDArray[np.floating]
    """Ground level at the manhole in metres; surcharge begins above it."""

    z_invert: NDArray[np.floating]
    """Pipe invert level in metres."""

    storage_area: NDArray[np.floating]
    """Manhole plan area in m2, the ``A_s`` of ``dH/dt = net Q / A_s``."""

    inlet_length: NDArray[np.floating]
    """Grate length in metres, the ``L`` of the weir term."""

    inlet_area: NDArray[np.floating]
    """Grate opening area in m2, the ``A_o`` of the orifice term."""

    kappa: NDArray[np.floating]
    """Inlet clogging 0-1; ``(1 - kappa)`` scales the captured flow."""

    boundary: NDArray[np.int8]
    """0 = interior, 1 = tidal outfall, 2 = free outfall. See :data:`BoundaryKind`."""

    flap_gate: NDArray[np.bool_]
    """True where a flap gate blocks reverse flow at an outfall."""

    cell_row: NDArray[np.int32]
    """Row of the 2D cell this node exchanges water with; -1 when it has none."""

    cell_col: NDArray[np.int32]

    # --- edges -------------------------------------------------------------
    edge_ids: tuple[str, ...]
    from_node: NDArray[np.int32]
    to_node: NDArray[np.int32]
    length: NDArray[np.floating]
    area: NDArray[np.floating]
    """Full-bore cross-sectional area in m2, before blockage."""

    hydraulic_radius: NDArray[np.floating]
    """``R_h`` in metres at full bore."""

    diameter: NDArray[np.floating]
    """Pipe diameter (or box height) in metres, the ``D`` of the fill fraction."""

    edge_manning_n: NDArray[np.floating]
    q_full: NDArray[np.floating]
    """Manning full-flow capacity in m3/s at beta = 0, from the city pipeline."""

    beta: NDArray[np.floating]
    """Blockage fraction 0-1. ``A_eff = (1 - beta) * area``. Pulse learns this."""

    @property
    def n_nodes(self) -> int:
        return len(self.node_ids)

    @property
    def n_edges(self) -> int:
        return len(self.edge_ids)


@dataclass(slots=True)
class DrainState:
    """Mutable state of the 1D solver: a head per node and a flow per edge."""

    head: NDArray[np.floating]
    """Hydraulic head in metres at each node."""

    flow: NDArray[np.floating]
    """Discharge in m3/s on each edge, positive ``from_node -> to_node``."""

    def volume_m3(self, network: DrainNetwork) -> float:
        """Water stored in the manholes above their inverts."""
        import numpy as np

        depth = np.maximum(self.head - network.z_invert, 0.0)
        return float(np.sum(depth * network.storage_area))


@dataclass(frozen=True, slots=True)
class CouplingFluxes:
    """What crossed between the street and the pipe in one sync interval
    (SPEC.md 11.5, Appendix A).

    Both arrays are per node in m3/s and non-negative; they are separate rather than one
    signed array so a product can report capture and surcharge independently, which is what
    the drain X-ray and the surcharge markers show.
    """

    q_inlet: NDArray[np.floating]
    """Street to pipe: ``(1 - kappa) * min(weir, orifice, available)``."""

    q_surcharge: NDArray[np.floating]
    """Pipe to street: ``0.6 * A_m * sqrt(2 g (H - z_g - h))`` when ``H > z_g + h``."""

    @property
    def surcharging(self) -> NDArray[np.bool_]:
        """Nodes pushing water back onto the street this interval."""
        return self.q_surcharge > 0.0


# ============================================================================ boundaries
@dataclass(frozen=True, slots=True)
class TideSeries:
    """Sea stage at the tidal outfalls over the run (SPEC.md 10.2, 11.4).

    ``stage_m`` is in the same vertical datum as the DEM: :func:`varuna_twin.city.load_tide`
    converts a chart-datum series when the bundle manifest declares one, and ``datum_note`` says
    what it did (``None`` when the manifest declared no datum and the series is read as
    written). ``source`` is carried so the UI can say whether the series came from a public
    tide table or is labelled illustrative - rule 7 forbids presenting the second as the first.
    """

    times: tuple[datetime, ...]
    stage_m: NDArray[np.floating]
    source: str
    datum_note: str | None = None

    def at(self, when: datetime) -> float:
        """Linearly interpolated stage; clamped to the ends outside the series."""
        import numpy as np

        if not self.times:
            return 0.0
        xs = np.array([t.timestamp() for t in self.times], dtype=np.float64)
        return float(np.interp(when.timestamp(), xs, np.asarray(self.stage_m, dtype=np.float64)))


# ============================================================================ run
@dataclass(frozen=True, slots=True)
class MassBalance:
    """The conservation audit SPEC.md 11.3 requires: error below 0.1 % of inflow.

    Checked every 100 steps during a run and reported once at the end; a run that fails it is
    a bug, not a warning, because every number the console shows is derived from this volume.
    """

    volume_in_m3: float
    """Water that entered the domain **during the run**. Never the water already in it."""

    volume_out_m3: float
    volume_stored_m3: float
    """Water standing at the end of the run, surface plus pipes."""

    error_fraction: float
    """``|(stored_end - stored_start) - (in - out)| / in``; the budget is 1e-3.

    The denominator is the run's own inflow and stays so on a hot start (task P4.2): dividing
    by ``max(in, stored_start)`` would relax the budget by exactly the ratio of carried water to
    new water, which on a late cycle of a storm is the case the audit most needs to see. Set to
    0.0 when the inflow is too small for a ratio to mean anything; :attr:`residual_limit_m3`
    then carries the check instead."""

    volume_stored_start_m3: float = 0.0
    """Water standing when the run began: zero on a cold start, the checkpoint on a hot one."""

    residual_m3: float = 0.0
    """``(stored_end - stored_start) - (in - out)``, signed: positive means water was invented."""

    residual_limit_m3: float | None = None
    """Set when the inflow was too small for :attr:`error_fraction` to be meaningful, and the
    audit fell back to an absolute limit on :attr:`residual_m3`. ``None`` means the relative
    budget applies."""

    # -------------------------------------------------------------- where the residual sits
    # A residual is a number to report; *which side lost the water* is what says whether it is
    # a bug. These four sum to `residual_m3` on a coupled run and are zero on an uncoupled one.
    # They exist because the 2026-09-23 re-bake put every demo cycle over budget and the only
    # way to tell a leak from an honest denominator was to decompose it (task P4.5).

    surface_residual_m3: float = 0.0
    """The 2D solver against its own sources. Non-zero means `swe2d` is losing water."""

    drain_residual_m3: float = 0.0
    """The 1D solver against its own sources. Non-zero means `drain1d` is losing water."""

    inlet_gap_m3: float = 0.0
    """Capture the surface gave up minus capture the network took.

    Positive destroys water, negative invents it. It is not identically zero: the surface hands
    over ``min(wanted, available)`` at each CFL sub-step and a cell can be drained by its
    neighbours part-way through a sync, while the network accepts whatever it was offered.
    Measured at 14.5 m3 on the 08:40 cycle of 2 July 2019 against 2,970,218 m3 of rain -
    0.00049 %, the whole of that run's remaining error and 205x inside SPEC.md 11.3's budget.
    Closing it exactly needs the offer limited per *cell* rather than per node, which is not
    built."""

    surcharge_gap_m3: float = 0.0
    """Surcharge the network gave up minus surcharge the surface received.

    Zero by construction since the runner began stepping the drain first and passing on what it
    actually emitted. Before that it was -6,052.7 m3 on the 08:40 cycle - water that appeared on
    a street without leaving a pipe, because the coupling's request and the drain's supply check
    disagreed and each solver was told a different number."""

    @property
    def ok(self) -> bool:
        if self.residual_limit_m3 is not None:
            return abs(self.residual_m3) <= self.residual_limit_m3
        return self.error_fraction < 1e-3


@dataclass(frozen=True, slots=True)
class TwinFingerprint:
    """What a :class:`TwinState` must match before a run may resume from it (task P4.2).

    A checkpoint is a set of arrays indexed by cell and by node; loaded against a city that
    was rebuilt since, it would not fail - it would put water in the wrong places and publish
    depths from it. So the state carries the identity of the grid and network it was written
    on, and :meth:`differences` names every field that no longer agrees.

    ``z_invert`` and ``blocked`` are hashed rather than compared by size alone because the
    rebuilds that matter keep the sizes: a drain regrade (ADR-0048) moves inverts, and the
    building-burn fix (ADR-0039) changed blocked cells, both on the same 522 x 323 grid and
    the same node count.

    ``provenance`` is optional and opaque here: key-value strings the caller vouches for, such
    as the bundle manifest hash, the tide datum and the Twin version. The cycle's checkpoint
    fills it; the Twin only requires that both sides agree, key for key.
    """

    shape: tuple[int, int]
    transform: tuple[float, float, float, float, float, float]
    crs: str
    n_nodes: int
    n_edges: int
    z_invert_sha256: str
    blocked_sha256: str
    provenance: tuple[tuple[str, str], ...] = ()
    """Sorted ``(key, value)`` pairs, so two fingerprints built from equal mappings are equal."""

    @classmethod
    def of(
        cls,
        terrain: TerrainGrid,
        network: DrainNetwork,
        provenance: Mapping[str, str] | None = None,
    ) -> TwinFingerprint:
        """The fingerprint of a terrain grid and a drain network, with optional provenance."""
        import hashlib

        import numpy as np

        def digest(array: NDArray, dtype: str) -> str:
            data = np.ascontiguousarray(np.asarray(array), dtype=dtype)
            return hashlib.sha256(data.tobytes()).hexdigest()

        pairs: list[tuple[str, str]] = []
        for key, value in (provenance or {}).items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError(
                    f"provenance must map str to str; got {key!r}: {value!r} "
                    f"({type(key).__name__}: {type(value).__name__})"
                )
            pairs.append((key, value))
        return cls(
            shape=terrain.shape,
            transform=tuple(float(v) for v in terrain.transform),  # type: ignore[arg-type]
            crs=str(terrain.crs),
            n_nodes=network.n_nodes,
            n_edges=network.n_edges,
            # Little-endian float64 and uint8 whatever the rasters were read as, so a city
            # written as float32 on one run and float64 on the next hashes the same values alike.
            z_invert_sha256=digest(network.z_invert, "<f8"),
            blocked_sha256=digest(terrain.blocked, "u1"),
            provenance=tuple(sorted(pairs)),
        )

    def differences(self, other: TwinFingerprint) -> tuple[str, ...]:
        """One sentence per field on which ``self`` (the checkpoint) and ``other`` disagree."""
        found: list[str] = []
        for name in ("shape", "transform", "crs", "n_nodes", "n_edges"):
            mine, theirs = getattr(self, name), getattr(other, name)
            if mine != theirs:
                found.append(f"{name}: checkpoint {mine!r}, this run {theirs!r}")
        for name, what in (
            ("z_invert_sha256", "drain invert levels"),
            ("blocked_sha256", "blocked (building) cells"),
        ):
            mine, theirs = getattr(self, name), getattr(other, name)
            if mine != theirs:
                found.append(
                    f"{name}: the {what} differ (checkpoint {mine[:12]}, this run {theirs[:12]})"
                )
        mine_p, theirs_p = dict(self.provenance), dict(other.provenance)
        for key in sorted(set(mine_p) | set(theirs_p)):
            a, b = mine_p.get(key), theirs_p.get(key)
            if a != b:
                found.append(
                    f"provenance[{key!r}]: checkpoint "
                    f"{'absent' if a is None else repr(a)}, this run "
                    f"{'absent' if b is None else repr(b)}"
                )
        return tuple(found)


@dataclass(frozen=True, slots=True)
class TwinState:
    """Everything the coupled Twin carries from one instant to the next (task P4.2).

    A run started from this state continues exactly where the run that wrote it stopped: the
    resume-identity test holds twelve steps in one run array-equal to six plus a six-step resume.
    That requires every piece of memory the solvers keep, not only the depth:

    * the surface depth and both face fluxes - the local-inertial scheme carries ``q`` forward,
      so restarting it from rest is a different run;
    * the drain heads and edge flows;
    * the pumps' and tanks' filled volume, empty until pump plans are wired but carried now so a
      plan can hot start without a format change;
    * the hydrology accumulators - the depression store left to fill and the cumulative rain
      the SCS curve reads, without which a resumed storm would infiltrate as if it had just begun.

    ``retention_s_mm`` is not here: it is derived from ``cn.tif``, which the fingerprint's grid
    identity already covers by way of the terrain it is rebuilt from.

    Arrays are float64. The runner copies them in, so resuming never mutates a checkpoint, and
    the ``final_state`` it returns is read-only.
    """

    valid_ts: datetime
    """The instant this state describes; a run resumes from it only when ``t0`` equals it."""

    h: NDArray[np.floating]
    """``(n_rows, n_cols)`` surface depth in metres."""

    qx: NDArray[np.floating]
    qy: NDArray[np.floating]
    drain_head: NDArray[np.floating]
    """``(n_nodes,)`` hydraulic head in metres."""

    drain_flow: NDArray[np.floating]
    """``(n_edges,)`` pipe discharge in m3/s."""

    sink_filled_m3: NDArray[np.floating]
    """``(n_units,)`` volume each pump or tank has taken; length 0 while none are wired."""

    depression_remaining_mm: NDArray[np.floating]
    cumulative_rain_mm: NDArray[np.floating]
    fingerprint: TwinFingerprint


@dataclass(frozen=True, slots=True)
class TwinInputs:
    """Everything one Twin run consumes.

    ``rain_mm_h`` is ``(n_steps, n_rows, n_cols)`` already resampled onto the city grid -
    Sky owns that resampling (``varuna_sky.products.resample_to_aoi``), Twin does not
    reach across to it.
    """

    terrain: TerrainGrid
    network: DrainNetwork
    rain_mm_h: NDArray[np.floating]
    t0: datetime
    step_min: int = 5
    tide: TideSeries | None = None
    sync_s: float = 5.0
    """Surface-drain exchange interval in seconds (SPEC.md 11.5)."""

    inner_dt_s: float = 1.0
    """Explicit inner step of the 1D solver in seconds (SPEC.md 11.4)."""

    initial_state: TwinState | None = None
    """Resume from this state instead of a dry city with empty pipes. ``None`` is the cold
    start every run used before task P4.2, and its output is unchanged."""

    provenance: Mapping[str, str] | None = None
    """Stamped into ``final_state.fingerprint`` and required to match ``initial_state``'s."""


@dataclass(frozen=True, slots=True)
class TwinResult:
    """One Twin run: a depth field every 5 minutes, the drain state that produced it, and
    the audit trail (SPEC.md 10.3, 11.3).
    """

    depth_m: NDArray[np.floating]
    """``(n_steps, n_rows, n_cols)`` surface depth in metres."""

    head_m: NDArray[np.floating]
    """``(n_steps, n_nodes)`` hydraulic head at each drain node."""

    q_surcharge: NDArray[np.floating]
    """``(n_steps, n_nodes)`` surcharge discharge in m3/s."""

    edge_flow: NDArray[np.floating]
    """``(n_steps, n_edges)`` pipe discharge; negative entries are backflow."""

    times: tuple[datetime, ...]
    mass_balance: MassBalance
    final_state: TwinState
    """The state at the last output time, read-only; pass it as the next run's
    ``initial_state`` to continue this one."""

    stage_ms: dict[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def n_steps(self) -> int:
        return int(self.depth_m.shape[0])

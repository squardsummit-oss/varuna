"""The types every VARUNA-Sky stage passes to the next (SPEC.md 11.1).

Sky is a chain: decoded radar frames and recent gauge readings go in, a 20-member,
3-hour rain ensemble comes out. Each stage in SPEC.md 11.1 is a pure function over the
structures here, so a stage can be tested on its own and swapped (pySTEPS for the fallback
nowcaster, adaptive Z-R for Marshall-Palmer) without the callers noticing.

Layering: Sky depends on ``varuna_schemas`` and nothing else in the workspace. Georeference
is never guessed - it arrives on :class:`RadarGrid`, read from the Zarr attributes of the cube
the frames came from (replay) or from the decoder (live). The AOI grid arrives the same way,
from the city rasters' own affine transform.

Units, fixed once here so no stage has to ask:

* reflectivity is ``dBZ``; rain rate is ``mm/h``; accumulation is ``mm``;
* time is timezone-aware IST (``varuna_schemas.constants.IST``);
* motion is **pixels per frame interval**, the convention pySTEPS uses, with the frame
  interval carried alongside so it can be converted to m/s for a display;
* the ensemble axis is always first: ``(member, step, row, col)``.

Determinism (rule 8): every structure that a seeded generator produces records the seed it
came from, so a rerun of the same cycle yields the same cube byte for byte.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from varuna_schemas.constants import N_STEPS, SKY_MEMBERS, STEP_MIN

if TYPE_CHECKING:  # pragma: no cover - numpy is a runtime dep, kept out of the type surface
    import numpy as np
    import pandas as pd
    from numpy.typing import NDArray

__all__ = [
    "AttenuationFlag",
    "GaugePair",
    "MergeResult",
    "MotionField",
    "NowcastSource",
    "QCResult",
    "RadarFrames",
    "RadarGrid",
    "RainEnsemble",
    "SkyInputs",
    "SkyProducts",
    "SkyResult",
    "ZRParams",
    "ZRSource",
]


# ============================================================================ geometry
@dataclass(frozen=True, slots=True)
class RadarGrid:
    """Georeference of the square Sky domain (60 km at 500 m for Mumbai, SPEC.md 3.3).

    ``transform`` is the six affine coefficients ``(a, b, c, d, e, f)`` of a north-up grid,
    the same convention ``varuna_city`` and ``varuna_replay.domain`` use: row 0 is the
    northern row, so ``e`` is negative.
    """

    crs: str
    """EPSG string, e.g. ``EPSG:32643``."""

    res_m: float
    """Pixel size in metres (500 for pySTEPS)."""

    n_px: int
    """Pixels per side."""

    transform: tuple[float, float, float, float, float, float]
    """``(res, 0, left, 0, -res, top)``."""

    @property
    def shape(self) -> tuple[int, int]:
        """``(n_px, n_px)`` - the numpy shape of one frame."""
        return (self.n_px, self.n_px)

    @property
    def left(self) -> float:
        return self.transform[2]

    @property
    def top(self) -> float:
        return self.transform[5]

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """``(left, bottom, right, top)`` in the metric CRS."""
        extent = self.n_px * self.res_m
        return (self.left, self.top - extent, self.left + extent, self.top)

    def xy(self, row: float, col: float) -> tuple[float, float]:
        """Metric coordinates of a cell centre."""
        return (
            self.left + (col + 0.5) * self.res_m,
            self.top - (row + 0.5) * self.res_m,
        )

    def rowcol(self, x: float, y: float) -> tuple[int, int]:
        """Row and column containing a metric coordinate (may fall outside the grid)."""
        col = int((x - self.left) // self.res_m)
        row = int((self.top - y) // self.res_m)
        return (row, col)

    def contains(self, row: int, col: int) -> bool:
        return 0 <= row < self.n_px and 0 <= col < self.n_px


# ============================================================================ inputs
@dataclass(frozen=True, slots=True)
class RadarFrames:
    """The recent radar history a cycle sees: at least three frames for optical flow.

    ``dbz`` is ``(n_frames, n_px, n_px)`` with the **newest frame last**, matching the order
    pySTEPS expects. Missing pixels are ``nan``, never a sentinel like -99.
    """

    dbz: NDArray[np.floating]
    times: tuple[datetime, ...]
    grid: RadarGrid

    @property
    def n_frames(self) -> int:
        return int(self.dbz.shape[0])

    @property
    def latest_ts(self) -> datetime:
        """Valid time of the newest frame - the cycle's ``radar_frame_ts``."""
        return self.times[-1]

    @property
    def interval(self) -> timedelta:
        """Spacing between frames; 10 minutes for the bundles (SPEC.md 10.2)."""
        if len(self.times) < 2:
            return timedelta(minutes=10)
        return self.times[-1] - self.times[-2]


@dataclass(frozen=True, slots=True)
class SkyInputs:
    """Everything one Sky cycle consumes (SPEC.md 11.1).

    ``gauges`` is a frame with columns ``ts``, ``station_id``, ``lon``, ``lat``, ``mm_5min``
    covering the last 60 minutes - the bundle's ``gauges.csv`` schema (SPEC.md 10.2).
    ``nwp`` stays ``None`` in P0: the blend of SPEC.md Appendix A is disabled and the run
    is labelled accordingly, rather than pretending a forecast field exists.
    """

    frames: RadarFrames
    gauges: pd.DataFrame
    cycle_ts: datetime
    nwp: NDArray[np.floating] | None = None
    seed: int = 2019
    n_members: int = SKY_MEMBERS
    n_steps: int = N_STEPS
    step_min: int = STEP_MIN


# ============================================================================ QC
AttenuationFlag = Literal["none", "partial", "severe"]
"""How much of the domain sits in the shadow behind a >50 dBZ core."""


@dataclass(frozen=True, slots=True)
class QCResult:
    """Quality control of the newest frames (SPEC.md 11.1 step 1).

    Every mask is ``(n_px, n_px)`` and ``True`` means *the pixel has this problem*, except
    ``coverage``, where ``True`` means *the radar sees this pixel*. Attenuation is a flag
    only: the prototype marks the shadow, it does not correct it.
    """

    coverage: NDArray[np.bool_]
    clutter: NDArray[np.bool_]
    attenuation: NDArray[np.bool_]
    attenuation_flag: AttenuationFlag
    dbz: NDArray[np.floating]
    """The newest frame with clutter and out-of-coverage pixels set to ``nan``."""

    @property
    def coverage_fraction(self) -> float:
        return float(self.coverage.mean())

    @property
    def clutter_fraction(self) -> float:
        return float(self.clutter.mean())


# ============================================================================ Z-R
ZRSource = Literal["adaptive", "marshall_palmer"]
"""Whether ``(a, b)`` were fitted this cycle or fell back to Marshall-Palmer."""


@dataclass(frozen=True, slots=True)
class ZRParams:
    """The ``Z = a R^b`` relation used this cycle (SPEC.md 11.1 step 2, Appendix A).

    ``a`` is clamped to [100, 400] and ``b`` to [1.1, 1.8]; when fewer than
    :data:`MIN_ZR_PAIRS` co-located gauge-radar pairs exist the fit is abandoned and
    ``source`` reads ``marshall_palmer`` so the run can say which relation it used.
    """

    a: float
    b: float
    source: ZRSource
    n_pairs: int
    clamped: bool = False
    r2: float | None = None
    reason: str | None = None
    """Why the fit was abandoned, when ``source`` is ``marshall_palmer``; ``None`` for a real fit.

    The console prints this. It exists because the pair count alone does not explain the
    fallback: :mod:`varuna_sky.zr` abandons the fit for three different reasons, and only one of
    them is "too few pairs". A cycle with twenty gauges that all read within a factor of two of
    each other falls back with twenty pairs in hand, and a caption inferring "fewer than eight"
    from ``source`` would be stating something false on screen (rule 6)."""


@dataclass(frozen=True, slots=True)
class GaugePair:
    """One co-located gauge reading and radar estimate, the unit the Z-R fit consumes."""

    ts: datetime
    station_id: str
    row: int
    col: int
    gauge_mm_h: float
    dbz: float


# ============================================================================ gauge merge
@dataclass(frozen=True, slots=True)
class MergeResult:
    """The gauge-adjusted rain field (SPEC.md 11.1 step 3).

    Mean-field bias first, then inverse-distance interpolation of the residuals. KED via
    ``pykrige`` is P1; ``method`` records which ran so the UI never implies more than
    happened.
    """

    rain_mm_h: NDArray[np.floating]
    mfb: float
    """``sum(G) / sum(R)`` over the co-located pairs; 1.0 when no pairs were usable."""

    n_gauges: int
    method: Literal["mfb+idw", "mfb", "none"]
    max_gauge_error_pct: float | None = None
    """Largest relative miss at a gauge after merging; the contract is within 5 %."""


# ============================================================================ motion
@dataclass(frozen=True, slots=True)
class MotionField:
    """Advection field from optical flow (SPEC.md 11.1 step 4).

    ``u`` and ``v`` are ``(n_px, n_px)`` in **pixels per frame interval**, the pySTEPS
    convention: ``u`` is column displacement (east positive), ``v`` is row displacement
    (south positive, because row 0 is north).
    """

    u: NDArray[np.floating]
    v: NDArray[np.floating]
    interval: timedelta
    res_m: float
    method: Literal["lucas_kanade", "zero"]

    def speed_ms(self) -> NDArray[np.floating]:
        """Displacement magnitude converted to metres per second."""
        import numpy as np

        seconds = max(self.interval.total_seconds(), 1.0)
        return np.hypot(self.u, self.v) * self.res_m / seconds


# ============================================================================ ensemble
NowcastSource = Literal["pysteps_steps", "fallback_steps", "nwp_ensemble"]
"""Which nowcaster produced the ensemble. The fallback is labelled, never hidden, and
``nwp_ensemble`` is a live cycle's NWP forecast standing in for radar (``varuna_cycle.live``)."""


@dataclass(frozen=True, slots=True)
class RainEnsemble:
    """The 20-member, 3-hour rain forecast on the Sky grid (SPEC.md 11.1 step 5).

    ``rain_mm_h`` is ``(n_members, n_steps, n_px, n_px)``. ``times`` holds the valid time of
    each step, so step ``k`` is ``cycle_ts + (k + 1) * step_min``: the first step is the
    first *forecast* instant, not the analysis.
    """

    rain_mm_h: NDArray[np.floating]
    times: tuple[datetime, ...]
    grid: RadarGrid
    source: NowcastSource
    seed: int
    zr: ZRParams
    motion: MotionField | None = None

    @property
    def n_members(self) -> int:
        return int(self.rain_mm_h.shape[0])

    @property
    def n_steps(self) -> int:
        return int(self.rain_mm_h.shape[1])


# ============================================================================ products
@dataclass(frozen=True, slots=True)
class SkyProducts:
    """What the rest of VARUNA reads from a Sky cycle (SPEC.md 11.1 step 6, 10.3).

    Quantiles and exceedances are per pixel and per step, ``(n_steps, n_px, n_px)``. The
    hyetographs are the AOI-mean rain rate per member, ``(n_members, n_steps)``, which is
    what the time bar draws as its spread band (SPEC.md 7.2).
    """

    p10: NDArray[np.floating]
    p50: NDArray[np.floating]
    p90: NDArray[np.floating]
    mean: NDArray[np.floating]
    """Ensemble mean rain rate, ``(n_steps, n_px, n_px)``, and the field the Twin runs on.

    Kept beside the quantiles because it answers a different question and the two disagree
    sharply for convective rain. At a pixel and a lead time the members disagree about *where*
    the cell is, so the pixelwise median can be near zero while every member carries a
    downpour - on the 2 July storm the p50 field delivers 13 mm over three hours where the mean
    delivers what the members actually carry. The mean is the only reduction here that
    conserves volume, which is what a water balance needs (SPEC.md 11.11)."""

    p_gt_20: NDArray[np.floating]
    p_gt_40: NDArray[np.floating]
    aoi_hyetographs: NDArray[np.floating]
    times: tuple[datetime, ...]
    grid: RadarGrid

    @property
    def n_steps(self) -> int:
        return int(self.p50.shape[0])


@dataclass(frozen=True, slots=True)
class SkyResult:
    """One cycle's complete Sky output, and the timings the cycle budget bar shows."""

    ensemble: RainEnsemble
    products: SkyProducts
    qc: QCResult
    merge: MergeResult
    stage_ms: dict[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    """Honesty labels for the run, e.g. that the NWP blend is disabled in P0."""

    @property
    def total_ms(self) -> int:
        return int(sum(self.stage_ms.values()))

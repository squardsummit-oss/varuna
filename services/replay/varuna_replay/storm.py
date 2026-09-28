"""The storm designer: a reconstructed rain field and the radar frames it would produce.

SPEC.md 10.2 specifies it exactly, and this module implements that and nothing more:

* ``N`` convective cells, each with a birth time, a lifetime, a start point, a velocity
  (the demo wind is from the south-west at about 8 m/s), a Gaussian radius sigma of 2-6 km,
  a peak intensity of 40-120 mm/h and a sine growth-and-decay envelope;
* a stratiform background of 2-5 mm/h;
* the field ``R(x, y, t) = background + sum_c peak_c * g_c(t) * exp(-d_c^2 / 2 sigma_c^2)``;
* radar frames through the Marshall-Palmer inverse ``Z = 200 R^1.6``, plus multiplicative
  speckle noise, a coverage circle and quantisation to 5 dBZ classes, so the frames look
  like decoded imagery rather than a clean model field.

**What is measured and what is designed.** Nothing here is a measurement. For
``MUM-2019-07-02`` the only primary rainfall number in hand is the IMD Santacruz total of
375.2 mm for the 24 hours ending 08:30 IST on 2 July 2019, with two sub-daily anchors from
officials (183 mm in 3 h over the Kurla-Thane belt, 63 mm in 6 h to 05:30 IST). **No hourly
or three-hourly hyetograph exists for any Mumbai station over the replay window**, so any
accumulation this designer produces for 05:40-09:40 IST is an *inference* from the 24-hour
total, never a gauge reading. :func:`calibrate` therefore returns what it achieved and the
caller must write that, its target and its reasoning into ``manifest.calibration_basis``.
See ``docs/research/rain_gauges_tide_MUM-2019-07-02.md`` and ADR-0007.

Determinism (rule 8): every draw comes from ``numpy.random.default_rng(seed)`` and every cell
parameter is rounded when it is generated, so the manifest holds exactly the numbers the
field was built from and regenerating from the manifest reproduces the cube byte for byte.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import structlog
from varuna_schemas.models.bundle import StormCellSpec, StormDesign

from varuna_replay.domain import StormDomain

log = structlog.get_logger("varuna.replay.storm")

# ----------------------------------------------------------------------- Marshall-Palmer
MP_A = 200.0
"""``Z = a R^b`` prefactor (Marshall-Palmer, SPEC.md Appendix A)."""

MP_B = 1.6
"""``Z = a R^b`` exponent."""

# ----------------------------------------------------------------------- designer ranges
SIGMA_RANGE_M = (2000.0, 6000.0)
"""Cell Gaussian radius, 2-6 km (SPEC.md 10.2)."""

PEAK_RANGE_MM_H = (40.0, 120.0)
"""Cell peak intensity, 40-120 mm/h (SPEC.md 10.2)."""

BACKGROUND_RANGE_MM_H = (2.0, 5.0)
"""Stratiform background, 2-5 mm/h (SPEC.md 10.2)."""

LIFETIME_RANGE_MIN = (30.0, 90.0)
"""Cell lifetime. SPEC.md does not fix this; 30-90 minutes is a designer choice and is
recorded in ``StormDesign.notes`` so it is never mistaken for an observation."""

WIND_FROM_DEG = 225.0
"""The demo wind comes from the south-west (SPEC.md 10.2)."""

WIND_SPEED_MS = 8.0
"""About 8 m/s (SPEC.md 10.2)."""

DEFAULT_N_CELLS = 8
ACROSS_TRACK_SD_M = 3000.0
"""Lateral scatter of cell tracks about the AOI centre, so cells sweep across the AOI."""

ALONG_TRACK_SD_M = 4000.0
"""Along-track scatter of the point each cell passes at its peak."""

PEAK_WINDOW_FRACTION = (0.15, 0.85)
"""Cell peaks are drawn inside this fraction of the window, so no cell peaks off-screen."""

# ----------------------------------------------------------------------- radar rendering
COVERAGE_RADIUS_KM = 30.0
SPECKLE_SIGMA = 0.12
DBZ_CLASS_WIDTH = 5.0
MIN_DBZ = 5.0

DESIGNER_NOTES: tuple[str, ...] = (
    "Reconstructed replay: the rain field is a storm-designer reconstruction, not a measurement.",
    "Cell lifetimes are drawn from 30-90 minutes; no public gauge trace resolves them.",
    "Cell tracks are aimed across the area of interest so the storm crosses the demo streets.",
    "Radar frames are rendered from the rain field, quantised to 5 dBZ classes to mimic "
    "decoded imagery; no archived radar volume for this event is publicly retrievable.",
)


# ============================================================================ geometry
def wind_vector(from_deg: float, speed_ms: float) -> tuple[float, float]:
    """Meteorological ``from`` direction to an ``(east, north)`` velocity in m/s.

    Wind *from* 225 degrees (south-west) blows *towards* the north-east, so both components
    are positive.
    """
    radians = math.radians(from_deg)
    return (-speed_ms * math.sin(radians), -speed_ms * math.cos(radians))


def envelope(cell: StormCellSpec, t_min: float) -> float:
    """The sine growth-and-decay envelope ``g(t)``: 0 at birth, 1 at mid-life, 0 at death."""
    u = (t_min - cell.birth_min) / cell.lifetime_min
    if u < 0.0 or u > 1.0:
        return 0.0
    return math.sin(math.pi * u)


def cell_center(cell: StormCellSpec, t_min: float) -> tuple[float, float]:
    """Where the cell centre has advected to at ``t_min`` (minutes from t0)."""
    dt_s = (t_min - cell.birth_min) * 60.0
    return (cell.start_x_m + cell.u_ms * dt_s, cell.start_y_m + cell.v_ms * dt_s)


# ============================================================================ generation
def random_storm(
    domain: StormDomain,
    *,
    seed: int,
    window_min: float,
    aoi_center_m: tuple[float, float] | None = None,
    n_cells: int = DEFAULT_N_CELLS,
    wind_from_deg: float = WIND_FROM_DEG,
    wind_speed_ms: float = WIND_SPEED_MS,
    coverage_radius_km: float = COVERAGE_RADIUS_KM,
    speckle_sigma: float = SPECKLE_SIGMA,
) -> StormDesign:
    """Draw a seeded convective storm over ``domain``.

    Args:
        domain: the radar/truth grid the cells live on.
        seed: every draw comes from ``default_rng(seed)``; the demo bundle uses 2019.
        window_min: length of the replay window in minutes; cell peaks land inside it.
        aoi_center_m: the point cell tracks are aimed at (the AOI centre in the domain CRS).
            Defaults to the domain centre.
        n_cells: number of convective cells.

    Returns:
        A :class:`StormDesign` whose numbers are already rounded, so the manifest records
        exactly what the field was built from.
    """
    rng = np.random.default_rng(seed)
    u_ms, v_ms = wind_vector(wind_from_deg, wind_speed_ms)
    speed = math.hypot(u_ms, v_ms)
    if speed > 0:
        head_x, head_y = u_ms / speed, v_ms / speed
    else:  # a stationary storm still needs an axis for the lateral scatter
        head_x, head_y = 1.0, 0.0
    perp_x, perp_y = -head_y, head_x
    target_x, target_y = aoi_center_m if aoi_center_m is not None else domain.center()

    background = round(float(rng.uniform(*BACKGROUND_RANGE_MM_H)), 3)
    low, high = PEAK_WINDOW_FRACTION
    cells: list[StormCellSpec] = []
    for index in range(n_cells):
        peak_min = float(rng.uniform(low * window_min, high * window_min))
        lifetime = float(rng.uniform(*LIFETIME_RANGE_MIN))
        sigma = float(rng.uniform(*SIGMA_RANGE_M))
        peak = float(rng.uniform(*PEAK_RANGE_MM_H))
        across = float(rng.normal(0.0, ACROSS_TRACK_SD_M))
        along = float(rng.normal(0.0, ALONG_TRACK_SD_M))
        # Where the cell is when its envelope peaks, then back-track to its birth point.
        px = target_x + across * perp_x + along * head_x
        py = target_y + across * perp_y + along * head_y
        birth_min = peak_min - lifetime / 2.0
        travel_s = (peak_min - birth_min) * 60.0
        cells.append(
            StormCellSpec(
                id=f"cell-{index + 1:02d}",
                birth_min=round(birth_min, 3),
                lifetime_min=round(lifetime, 3),
                start_x_m=round(px - u_ms * travel_s, 1),
                start_y_m=round(py - v_ms * travel_s, 1),
                u_ms=round(u_ms, 4),
                v_ms=round(v_ms, 4),
                sigma_m=round(sigma, 1),
                peak_mm_h=round(peak, 3),
            )
        )
    design = StormDesign(
        crs=domain.crs,
        seed=seed,
        background_mm_h=background,
        wind_from_deg=wind_from_deg,
        wind_speed_ms=wind_speed_ms,
        cells=cells,
        coverage_radius_km=coverage_radius_km,
        speckle_sigma=speckle_sigma,
        dbz_class_width=DBZ_CLASS_WIDTH,
        min_dbz=MIN_DBZ,
        notes=list(DESIGNER_NOTES),
    )
    log.info(
        "storm.designed",
        seed=seed,
        cells=len(cells),
        background_mm_h=background,
        window_min=window_min,
    )
    return design


# ============================================================================ the field
def rain_field(
    design: StormDesign,
    domain: StormDomain,
    times_min: np.ndarray,
    *,
    include_background: bool = True,
    intensity_scale: float | None = None,
) -> np.ndarray:
    """``R(x, y, t)`` in mm/h, shape ``(len(times_min), n_px, n_px)``, float32.

    Args:
        design: the storm parameters.
        domain: the grid.
        times_min: instants in minutes from ``t0``.
        include_background: set False to get the convective contribution alone, which is what
            :func:`calibrate` scales.
        intensity_scale: overrides ``design.intensity_scale`` (used while calibrating).
    """
    scale = design.intensity_scale if intensity_scale is None else intensity_scale
    x, y = domain.cell_centres()
    times = np.asarray(times_min, dtype=np.float64)
    out = np.zeros((times.size, domain.n_px, domain.n_px), dtype=np.float64)
    if include_background:
        out += design.background_mm_h
    for cell in design.cells:
        two_sigma_sq = 2.0 * cell.sigma_m**2
        amplitude = cell.peak_mm_h * scale
        if amplitude <= 0.0:
            continue
        for k, t_min in enumerate(times):
            g = envelope(cell, float(t_min))
            if g <= 0.0:
                continue
            cx, cy = cell_center(cell, float(t_min))
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            out[k] += amplitude * g * np.exp(-d2 / two_sigma_sq)
    return out.astype(np.float32)


def accumulation_mm(
    rain_mm_h: np.ndarray, step_min: float, *, rule: str = "trapezoid"
) -> np.ndarray:
    """Integrate a rain-rate cube over time into a depth field in mm.

    Args:
        rain_mm_h: ``(t, y, x)`` rain rates in mm/h at equally spaced instants.
        step_min: spacing of those instants in minutes.
        rule: ``"trapezoid"`` for a field sampled at instants (the convective storm), or
            ``"left"`` for a block-constant field where each sample holds until the next
            (the Chicago design storm, whose blocks then integrate exactly).
    """
    hours = step_min / 60.0
    if rule == "left":
        return np.asarray(rain_mm_h[:-1], dtype=np.float64).sum(axis=0) * hours
    if rule != "trapezoid":
        msg = f"rule must be 'trapezoid' or 'left', got {rule!r}"
        raise ValueError(msg)
    return np.trapezoid(np.asarray(rain_mm_h, dtype=np.float64), dx=hours, axis=0)


def aoi_mean_accumulation(
    rain_mm_h: np.ndarray, mask: np.ndarray, step_min: float, *, rule: str = "trapezoid"
) -> float:
    """Mean accumulated depth in mm over the masked pixels."""
    depth = accumulation_mm(rain_mm_h, step_min, rule=rule)
    if not mask.any():
        msg = "the area-of-interest mask selects no pixel of the storm domain"
        raise ValueError(msg)
    return float(depth[mask].mean())


# ============================================================================ calibration
@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """What :func:`calibrate` achieved, and how far from the target it landed.

    The caller writes :attr:`achieved_mm`, :attr:`target_mm` and the reasoning into the
    manifest. Never present :attr:`achieved_mm` as a measurement: it is the accumulation of
    a designed field that was scaled to meet an inferred target.
    """

    design: StormDesign
    target_mm: float
    achieved_mm: float
    intensity_scale: float
    background_mm: float
    tolerance_frac: float
    window_min: tuple[float, float]
    step_min: float
    window_label: str
    peak_mm_h_range: tuple[float, float]
    notes: list[str] = field(default_factory=list)

    @property
    def error_mm(self) -> float:
        return self.achieved_mm - self.target_mm

    @property
    def relative_error(self) -> float:
        return abs(self.error_mm) / self.target_mm if self.target_mm else float("inf")

    @property
    def within_tolerance(self) -> bool:
        return self.relative_error <= self.tolerance_frac

    @property
    def peaks_outside_design_range(self) -> bool:
        """True when scaling pushed a cell peak outside the 40-120 mm/h range SPEC.md states."""
        low, high = PEAK_RANGE_MM_H
        return self.peak_mm_h_range[0] < low or self.peak_mm_h_range[1] > high

    def summary(self) -> str:
        """One human-readable line for a log or the bundle report."""
        verdict = "within" if self.within_tolerance else "OUTSIDE"
        return (
            f"{self.window_label}: target {self.target_mm:.1f} mm, achieved "
            f"{self.achieved_mm:.1f} mm ({self.relative_error * 100:.1f} % {verdict} "
            f"{self.tolerance_frac * 100:.0f} % tolerance), intensity scale "
            f"{self.intensity_scale:.4f}, background contributes {self.background_mm:.1f} mm"
        )


class CalibrationError(ValueError):
    """The storm cannot reach the target (no convective rain over the area, or a target
    below what the stratiform background alone already delivers)."""


def calibrate(
    design: StormDesign,
    domain: StormDomain,
    *,
    mask: np.ndarray,
    target_mm: float,
    t0_min: float,
    t1_min: float,
    step_min: float,
    window_label: str,
    tolerance_frac: float = 0.05,
) -> CalibrationResult:
    """Scale the cell intensities so the area mean accumulation meets ``target_mm``.

    The accumulation is linear in the cell peaks, so the required multiplier is solved in
    one step, rounded to six decimals (so the manifest records exactly the number used) and
    then verified by re-integrating the scaled field.

    Args:
        mask: pixels the area mean is taken over (usually ``domain.aoi_mask(city.bbox)``).
        target_mm: the depth the window should accumulate. For a reconstruction this is an
            **inferred** target, not a gauge reading; state that in the manifest.
        window_label: what the window is, for the result summary and the manifest.
        tolerance_frac: the agreement the caller will state, 5 % by default.

    Raises:
        CalibrationError: when the cells contribute nothing over the mask, or the background
            alone already exceeds the target.
    """
    times = np.arange(0, round((t1_min - t0_min) / step_min) + 1) * step_min + t0_min
    background_mm = design.background_mm_h * (t1_min - t0_min) / 60.0
    cells_only = rain_field(design, domain, times, include_background=False, intensity_scale=1.0)
    cells_mm = aoi_mean_accumulation(cells_only, mask, step_min)
    if cells_mm <= 0.0:
        msg = (
            "the storm's cells deliver no rain over the area of interest in "
            f"{window_label}; move the cell tracks or widen the window"
        )
        raise CalibrationError(msg)
    if target_mm <= background_mm:
        msg = (
            f"target {target_mm:.1f} mm is at or below the stratiform background alone "
            f"({background_mm:.1f} mm over {window_label}); lower background_mm_h"
        )
        raise CalibrationError(msg)

    scale = round((target_mm - background_mm) / cells_mm, 6)
    calibrated = design.model_copy(update={"intensity_scale": scale})
    achieved = aoi_mean_accumulation(rain_field(calibrated, domain, times), mask, step_min)
    peaks = [cell.peak_mm_h * scale for cell in calibrated.cells] or [0.0]
    result = CalibrationResult(
        design=calibrated,
        target_mm=float(target_mm),
        achieved_mm=achieved,
        intensity_scale=scale,
        background_mm=float(background_mm),
        tolerance_frac=tolerance_frac,
        window_min=(float(t0_min), float(t1_min)),
        step_min=float(step_min),
        window_label=window_label,
        peak_mm_h_range=(round(min(peaks), 3), round(max(peaks), 3)),
    )
    if result.peaks_outside_design_range:
        result.notes.append(
            f"Scaled cell peaks span {result.peak_mm_h_range[0]:.0f}-"
            f"{result.peak_mm_h_range[1]:.0f} mm/h, outside the 40-120 mm/h range "
            "SPEC.md 10.2 states for the designer."
        )
    if not result.within_tolerance:
        result.notes.append(
            f"Accumulation missed the target by {result.relative_error * 100:.1f} %."
        )
    log.info("storm.calibrated", **{"summary": result.summary()})
    return result


# ============================================================================ radar
@dataclass(frozen=True, slots=True)
class RadarRender:
    """How a rain field is turned into frames that look like decoded radar imagery."""

    seed: int
    coverage_radius_km: float = COVERAGE_RADIUS_KM
    speckle_sigma: float = SPECKLE_SIGMA
    dbz_class_width: float = DBZ_CLASS_WIDTH
    min_dbz: float = MIN_DBZ

    @classmethod
    def from_design(cls, design: StormDesign) -> RadarRender:
        """The rendering parameters a storm design carries."""
        return cls(
            seed=design.seed,
            coverage_radius_km=design.coverage_radius_km,
            speckle_sigma=design.speckle_sigma,
            dbz_class_width=design.dbz_class_width,
            min_dbz=design.min_dbz,
        )


def radar_dbz(rain_mm_h: np.ndarray, domain: StormDomain, render: RadarRender) -> np.ndarray:
    """Render rain rates as decoded-looking radar reflectivity in dBZ.

    ``Z = 200 R^1.6`` (Marshall-Palmer inverse), multiplicative log-normal speckle on ``Z``,
    a coverage circle, then quantisation down to 5 dBZ classes. Pixels outside the coverage
    circle and classes below ``render.min_dbz`` are ``NaN``: an image decoder reads them as
    no echo, and Sky must treat them as missing rather than as zero rain.

    The speckle stream is ``default_rng([seed, 1])``, independent of the stream that drew the
    cells, so re-rendering the frames never disturbs the storm.
    """
    rng = np.random.default_rng([render.seed, 1])
    rain = np.asarray(rain_mm_h, dtype=np.float64)
    z = MP_A * np.power(np.clip(rain, 0.0, None), MP_B)
    if render.speckle_sigma > 0:
        z = z * np.exp(rng.normal(0.0, render.speckle_sigma, size=z.shape))
    with np.errstate(divide="ignore", invalid="ignore"):
        dbz = 10.0 * np.log10(z)
    dbz = np.where(np.isfinite(dbz), dbz, -np.inf)
    classes = np.floor(dbz / render.dbz_class_width) * render.dbz_class_width
    classes = np.where(classes >= render.min_dbz, classes, np.nan)
    coverage = domain.coverage_mask(render.coverage_radius_km)
    classes = np.where(coverage[None, :, :], classes, np.nan)
    return classes.astype(np.float32)


def rain_from_dbz(dbz: np.ndarray) -> np.ndarray:
    """Invert :func:`radar_dbz`: ``R = (10^(dBZ/10) / 200)^(1/1.6)``, no echo meaning no rain.

    Quantisation to 5 dBZ classes rounds a rate down by at most a factor
    ``10 ** (5 / (10 * 1.6)) = 2.05``; that is the recovery error to expect.
    """
    values = np.asarray(dbz, dtype=np.float64)
    z = np.power(10.0, values / 10.0)
    rain = np.power(z / MP_A, 1.0 / MP_B)
    return np.where(np.isfinite(values), rain, 0.0).astype(np.float32)


QUANTISATION_RATIO = 10.0 ** (DBZ_CLASS_WIDTH / (10.0 * MP_B))
"""Worst-case factor by which :func:`rain_from_dbz` under-reads because of 5 dBZ classes."""


__all__ = [
    "BACKGROUND_RANGE_MM_H",
    "COVERAGE_RADIUS_KM",
    "DBZ_CLASS_WIDTH",
    "DEFAULT_N_CELLS",
    "DESIGNER_NOTES",
    "LIFETIME_RANGE_MIN",
    "MIN_DBZ",
    "MP_A",
    "MP_B",
    "PEAK_RANGE_MM_H",
    "QUANTISATION_RATIO",
    "SIGMA_RANGE_M",
    "SPECKLE_SIGMA",
    "WIND_FROM_DEG",
    "WIND_SPEED_MS",
    "CalibrationError",
    "CalibrationResult",
    "RadarRender",
    "accumulation_mm",
    "aoi_mean_accumulation",
    "calibrate",
    "cell_center",
    "envelope",
    "radar_dbz",
    "rain_field",
    "rain_from_dbz",
    "random_storm",
    "wind_vector",
]

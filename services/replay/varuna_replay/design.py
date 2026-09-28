"""Design storms: a Chicago hyetograph built from a stated intensity (P2.8, SPEC.md 10.2).

**The honesty constraint that shapes this module.** ``docs/research/data_sources.md`` section 6
records that no published intensity-duration-frequency curve for Mumbai or Chennai could be
sourced, and that turning a 24-hour depth into a 25-year one-hour intensity needs an assumed
depth-duration ratio - a number nobody has published, so inventing one would break rule 7.

So the *depth* of these storms comes from the one design intensity that is already stated and
cited: the rational-method design intensity in ``services/city/configs/<city>.yaml``
(``design_intensity_mm_h``: 25 mm/h legacy, 50 mm/h for the BRIMSTOWAD-upgraded corridors,
SPEC.md 3.3 and 10.1). That number sizes the pipes; here it also sets the storm depth, and
:data:`DESIGN_STORM_BASIS` says exactly that, names the gap and names what would settle it.
The ``25yr`` in the bundle ids ``MUM-IDF-25yr`` and ``CHN-IDF-25yr`` is a **name**, not a
fitted return period.

The *shape* is the classical Chicago form with the peak at a stated position; its two shape
parameters are stated, not fitted, and the block series is normalised so the total depth is
exactly ``intensity x duration``.
"""

from __future__ import annotations

import numpy as np
import structlog
from varuna_schemas.models.bundle import DesignStorm
from varuna_schemas.models.city import CityConfig

from varuna_replay.domain import StormDomain

log = structlog.get_logger("varuna.replay.design")

DEFAULT_DURATION_MIN = 180
"""Three hours: the forecast horizon VARUNA works to (SPEC.md 3.3)."""

DEFAULT_STEP_MIN = 5
"""Hyetograph block length; the same 5-minute step the truth cube and the cycle use."""

DEFAULT_PEAK_POSITION = 0.4
"""Peak at 40 % of the duration: the usual Chicago choice, stated here rather than fitted."""

SHAPE_B_MIN = 10.0
"""Chicago shape parameter ``b`` in minutes. Stated, not fitted to an IDF curve."""

SHAPE_C = 0.8
"""Chicago shape exponent ``c``. Stated, not fitted to an IDF curve."""

RETURN_PERIOD_LABEL = "25yr"
"""The label in the bundle id. It is a name, not a fitted return period - see the basis text."""

DESIGN_STORM_BASIS = (
    "Design storm - synthetic. Built from the drainage-norm design intensity "
    "({intensity:.0f} mm/h, {source}) held over {duration} minutes, shaped as a Chicago "
    "hyetograph with the peak at {peak:.0f} % of the duration and stated shape parameters "
    "b = {b:.0f} min, c = {c:.2f}. It is NOT derived from a published "
    "intensity-duration-frequency curve: no citable IDF value for this city was obtained "
    "(docs/research/data_sources.md section 6), and converting a 24-hour depth into a "
    "25-year short-duration intensity needs an assumed depth-duration ratio, which would be "
    "an invented number. The '25yr' in the bundle id is a name, not a fitted return period. "
    "What would settle it: the return-period and IDF tables in the CPHEEO Manual on Storm "
    "Water Drainage Systems (2019); IMD's short-duration rainfall IDF atlas; or a published "
    "Gumbel/GEV IDF fit for this city with its parameters printed."
)
"""UI-ready honesty label for a design-storm bundle (SPEC.md 0.6, 0.7)."""


def chicago_shape(
    t_min: np.ndarray, *, duration_min: float, peak_position_r: float, b_min: float, c: float
) -> np.ndarray:
    """The unnormalised Chicago intensity shape, peaking at ``peak_position_r * duration``.

    ``f(x) = ((1 - c) x + b) / (x + b)^(c + 1)`` where ``x`` is the time from the peak,
    stretched by ``r`` before it and ``1 - r`` after it. ``f`` is strictly decreasing in
    ``x >= 0``, so the maximum sits exactly at the peak and nowhere else. The prefactor of
    the usual Chicago formula is dropped: it carries the IDF constant we do not have, and
    the series is normalised to a stated depth instead.
    """
    if not 0.0 < peak_position_r < 1.0:
        msg = f"peak_position_r must be strictly between 0 and 1, got {peak_position_r}"
        raise ValueError(msg)
    t = np.asarray(t_min, dtype=np.float64)
    t_peak = peak_position_r * duration_min
    before = (t_peak - t) / peak_position_r
    after = (t - t_peak) / (1.0 - peak_position_r)
    x = np.where(t < t_peak, before, after)
    x = np.clip(x, 0.0, None)
    return ((1.0 - c) * x + b_min) / np.power(x + b_min, c + 1.0)


def chicago_hyetograph(
    intensity_mm_h: float,
    duration_min: float,
    *,
    peak_position_r: float = DEFAULT_PEAK_POSITION,
    step_min: float = DEFAULT_STEP_MIN,
    b_min: float = SHAPE_B_MIN,
    c: float = SHAPE_C,
) -> np.ndarray:
    """Block intensities in mm/h, one per ``step_min`` block, for the whole duration.

    The blocks are the shape sampled at block centres and then scaled so that
    ``sum(blocks) * step_min / 60`` equals ``intensity_mm_h * duration_min / 60`` exactly.
    The largest block is the one containing ``peak_position_r * duration_min``.
    """
    if intensity_mm_h < 0:
        msg = f"intensity_mm_h must not be negative, got {intensity_mm_h}"
        raise ValueError(msg)
    n_blocks = duration_min / step_min
    if abs(n_blocks - round(n_blocks)) > 1e-9:
        msg = f"duration {duration_min} min is not a whole number of {step_min}-minute blocks"
        raise ValueError(msg)
    n = round(n_blocks)
    centres = (np.arange(n, dtype=np.float64) + 0.5) * step_min
    shape = chicago_shape(
        centres, duration_min=duration_min, peak_position_r=peak_position_r, b_min=b_min, c=c
    )
    total = shape.sum()
    if total <= 0:
        msg = "the Chicago shape summed to zero; check b_min and c"
        raise ValueError(msg)
    return shape * (intensity_mm_h * n / total)


def design_storm(
    config: CityConfig,
    *,
    intensity_key: str = "upgraded",
    duration_min: int = DEFAULT_DURATION_MIN,
    step_min: int = DEFAULT_STEP_MIN,
    peak_position_r: float = DEFAULT_PEAK_POSITION,
    b_min: float = SHAPE_B_MIN,
    c: float = SHAPE_C,
) -> DesignStorm:
    """The design storm for a city, built from its config's stated design intensity.

    Args:
        config: the city config; its ``design_intensity_mm_h`` supplies the depth.
        intensity_key: ``"upgraded"`` (50 mm/h) or ``"legacy"`` (25 mm/h).
    """
    if intensity_key not in {"legacy", "upgraded"}:
        msg = f"intensity_key must be 'legacy' or 'upgraded', got {intensity_key!r}"
        raise ValueError(msg)
    intensity = float(getattr(config.design_intensity_mm_h, intensity_key))
    source = (
        f"services/city/configs/{config.id}.yaml design_intensity_mm_h.{intensity_key} "
        "(SPEC.md 10.1: the rational-method intensity the inferred drains are sized to)"
    )
    blocks = chicago_hyetograph(
        intensity,
        duration_min,
        peak_position_r=peak_position_r,
        step_min=step_min,
        b_min=b_min,
        c=c,
    )
    storm = DesignStorm(
        intensity_mm_h=intensity,
        intensity_source=source,
        duration_min=duration_min,
        step_min=step_min,
        peak_position_r=peak_position_r,
        shape_b_min=b_min,
        shape_c=c,
        total_depth_mm=round(intensity * duration_min / 60.0, 6),
        hyetograph_mm_h=[round(float(value), 4) for value in blocks],
        basis=DESIGN_STORM_BASIS.format(
            intensity=intensity,
            source=source,
            duration=duration_min,
            peak=peak_position_r * 100,
            b=b_min,
            c=c,
        ),
    )
    log.info(
        "design_storm.built",
        city=config.id,
        intensity_mm_h=intensity,
        duration_min=duration_min,
        total_depth_mm=storm.total_depth_mm,
    )
    return storm


def hyetograph_at(storm: DesignStorm, t_min: float) -> float:
    """The block intensity in force at ``t_min`` minutes from the storm start; 0 after it."""
    if t_min < 0.0 or t_min >= storm.duration_min:
        return 0.0
    index = int(t_min // storm.step_min)
    if index >= len(storm.hyetograph_mm_h):
        return 0.0
    return storm.hyetograph_mm_h[index]


def design_storm_field(
    storm: DesignStorm, domain: StormDomain, times_min: np.ndarray
) -> np.ndarray:
    """The design storm as a rain-rate cube, uniform over the domain.

    A design storm has no spatial structure to claim: it is a depth held over an area, so the
    field is uniform and each instant carries the block intensity in force from that instant
    until the next. Integrate it with ``accumulation_mm(..., rule="left")`` and the total is
    exactly :attr:`DesignStorm.total_depth_mm`.
    """
    times = np.asarray(times_min, dtype=np.float64)
    values = np.array([hyetograph_at(storm, float(t)) for t in times], dtype=np.float32)
    return np.broadcast_to(values[:, None, None], (times.size, *domain.shape)).copy()


def design_bundle_id(config: CityConfig) -> str:
    """``MUM-IDF-25yr`` / ``CHN-IDF-25yr`` (SPEC.md 4.3, 10.2)."""
    return f"{config.code}-IDF-{RETURN_PERIOD_LABEL}"


__all__ = [
    "DEFAULT_DURATION_MIN",
    "DEFAULT_PEAK_POSITION",
    "DEFAULT_STEP_MIN",
    "DESIGN_STORM_BASIS",
    "RETURN_PERIOD_LABEL",
    "SHAPE_B_MIN",
    "SHAPE_C",
    "chicago_hyetograph",
    "chicago_shape",
    "design_bundle_id",
    "design_storm",
    "design_storm_field",
    "hyetograph_at",
]

"""Optical flow over the recent radar frames (SPEC.md 11.1 step 4).

pySTEPS' Lucas-Kanade tracker, ``pysteps.motion.get_method("LK")``, is written for a rain
field in decibels rather than raw reflectivity: its Shi-Tomasi corner detector, its outlier
filter and its declustering scale are all tuned on dBR, ten times the base-ten logarithm of
rain rate in mm/h. So this module converts the frames to rain with the cycle's Z-R relation
(SPEC.md Appendix A, ``R = (Z / a)^(1 / b)``), takes the logarithm, and feeds pySTEPS that.

The returned :class:`~varuna_sky.types.MotionField` is in pixels per frame interval, which is
also the pySTEPS convention: ``u`` is the column displacement (east positive) and ``v`` the
row displacement (south positive, because row 0 is the northern row).

Degenerate input never raises. Fewer than two frames, an all-missing history or a dry domain
carries no flow to recover, so the field comes back zero with ``method="zero"`` and the run
can say as much rather than implying a tracked storm.

**Pixels the radar cannot see are missing, not dry.** A decoded frame is ``nan`` both beyond
the radar's range and wherever there is no echo, and :func:`rain_history` floors both to dry,
which is right for the nowcast's rain field and wrong for tracking. Beyond the range the floor
draws a disc edge that sits still in every frame, a 20 dBR step wherever the stratiform shield
reaches it, and pySTEPS' Shi-Tomasi detector finds its best corners there. Every one of them
tracks to zero, and the dense field interpolated from them drags the storm's own vectors toward
a standstill. Measured on the 2 July 2019 reconstruction (``bundles/MUM-2019-07-02``, cycles
06:40 to 09:10, mean flow over wet pixels as a fraction of the storm designer's 8 m/s): 0.17 to
0.36 with the disc floored to dry, 0.71 to 1.15 with it passed as missing, against 0.72 to 1.27
for the same tracker on the truth field itself. The designer's frames with the disc removed
track at 0.75 to 1.37, so it is the disc and not the speckle or the 5 dBZ classes. So
:func:`optical_flow` takes the QC coverage mask and hands pySTEPS the pixels outside it as
``nan``, which pySTEPS masks, buffers and never detects a feature in.

The dBR transform and the analysis stack live here rather than in a nowcaster because optical
flow is the first stage that needs them and neither nowcaster - pySTEPS or the fallback - may
import the other.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np
import structlog

from varuna_sky.types import MotionField, RadarFrames, ZRParams

if TYPE_CHECKING:  # pragma: no cover - typing only
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.sky.motion")

__all__ = [
    "DBR_THRESHOLD",
    "DBR_ZEROVALUE",
    "MAX_RAIN_MM_H",
    "MIN_FLOW_FRAMES",
    "RAIN_FLOOR_MM_H",
    "from_dbr",
    "optical_flow",
    "optical_flow_from_rain",
    "rain_from_dbz",
    "rain_history",
    "to_dbr",
]

RAIN_FLOOR_MM_H = 0.1
"""Rain rate below which a pixel counts as dry, in mm/h. SPEC.md does not fix this; 0.1 is
the pySTEPS convention for radar QPE and is a choice made here, recorded so it is not read as
a specified threshold."""

DBR_ZEROVALUE = -15.0
"""dBR assigned to dry pixels. A choice, and the pySTEPS example value: it sits well below
``DBR_THRESHOLD`` so the cascade sees a floor rather than an infinity."""

DBR_THRESHOLD = 10.0 * np.log10(RAIN_FLOOR_MM_H)
"""``RAIN_FLOOR_MM_H`` expressed in dBR (-10.0), the threshold pySTEPS is told about."""

MAX_RAIN_MM_H = 400.0
"""Physical ceiling on an instantaneous rain rate, in mm/h, applied to every nowcast cube.

The wettest instantaneous rates ever measured anywhere are a few hundred mm/h over minutes,
and the 2 July 2019 Mumbai cloudburst peaked near 100 mm/h. SPEC.md does not fix a ceiling;
400 is a choice made here, set well above anything the replay can produce so it never shapes a
forecast, and it exists only to stop a numerically ill-conditioned nowcast from publishing an
impossible number (rule 6: every number on screen must be one we can defend).

It matters because the nowcasters work in dBR and come back through :func:`from_dbr`, which is
an exponential: a cascade that rings by 25 dBR - which an AR(2) fitted to poorly aligned frames
can do - becomes hundreds of times the rain that went in. Clipping in rain space rather than
dBR keeps the cap a statement about rainfall, not about the transform.
"""

MIN_FLOW_FRAMES = 2
"""Fewest frames Lucas-Kanade can track across. SPEC.md 11.1 step 4 asks for the last
three; two is the point below which there is no displacement to measure at all."""


# ============================================================================ transforms
def to_dbr(rain_mm_h: NDArray[np.floating]) -> NDArray[np.floating]:
    """Rain rate in mm/h to dBR, with dry and missing pixels set to :data:`DBR_ZEROVALUE`."""
    rain = np.asarray(rain_mm_h, dtype=np.float64)
    wet = np.isfinite(rain) & (rain >= RAIN_FLOOR_MM_H)
    dbr = np.full(rain.shape, DBR_ZEROVALUE, dtype=np.float64)
    dbr[wet] = 10.0 * np.log10(rain[wet])
    return dbr


def from_dbr(dbr: NDArray[np.floating]) -> NDArray[np.floating]:
    """dBR back to mm/h; anything under the dry floor (or missing) comes back as exactly 0."""
    values = np.asarray(dbr, dtype=np.float64)
    rain = np.zeros(values.shape, dtype=np.float64)
    wet = np.isfinite(values) & (values >= DBR_THRESHOLD)
    rain[wet] = np.power(10.0, values[wet] / 10.0)
    return rain


def rain_from_dbz(dbz: NDArray[np.floating], zr: ZRParams) -> NDArray[np.floating]:
    """Reflectivity to rain rate through ``Z = a R^b`` (SPEC.md Appendix A).

    Missing pixels stay missing (``nan``); the caller decides whether to treat them as dry.
    """
    values = np.asarray(dbz, dtype=np.float64)
    z = np.power(10.0, values / 10.0)
    with np.errstate(invalid="ignore"):
        return np.power(z / zr.a, 1.0 / zr.b)


def rain_history(
    frames: RadarFrames,
    zr: ZRParams,
    latest_rain_mm_h: NDArray[np.floating] | None = None,
    n_frames: int = 3,
) -> NDArray[np.floating]:
    """The last ``n_frames`` rain fields, oldest first, as the nowcasters want them.

    The older frames come from radar alone through the cycle's Z-R relation. When
    ``latest_rain_mm_h`` is given it replaces the newest frame: the gauge merge (SPEC.md
    11.1 step 3) adjusts the analysis, and only the analysis, so the history is not
    retro-fitted with a bias factor that was measured for a later instant.

    A history shorter than ``n_frames`` is padded by repeating its oldest frame, which reads
    to the AR model as "nothing changed before this", not as an invented earlier storm.
    """
    rain = rain_from_dbz(frames.dbz, zr)
    rain = np.where(np.isfinite(rain), rain, 0.0)
    if latest_rain_mm_h is not None:
        rain = rain.copy()
        rain[-1] = np.where(np.isfinite(latest_rain_mm_h), latest_rain_mm_h, 0.0)
    if rain.shape[0] >= n_frames:
        return np.ascontiguousarray(rain[-n_frames:], dtype=np.float64)
    pad = np.repeat(rain[:1], n_frames - rain.shape[0], axis=0)
    return np.ascontiguousarray(np.concatenate([pad, rain], axis=0), dtype=np.float64)


# ============================================================================ optical flow
def _zero_field(
    shape: tuple[int, int], interval: timedelta, res_m: float, reason: str
) -> MotionField:
    log.info("motion.zero", reason=reason, shape=shape)
    zeros = np.zeros(shape, dtype=np.float64)
    return MotionField(u=zeros, v=zeros.copy(), interval=interval, res_m=res_m, method="zero")


def optical_flow_from_rain(
    rain_mm_h: NDArray[np.floating],
    interval: timedelta,
    res_m: float,
    missing: NDArray[np.bool_] | None = None,
) -> MotionField:
    """Lucas-Kanade over a ``(n_frames, n_px, n_px)`` stack of rain fields in mm/h.

    pySTEPS is handed the dBR transform of the stack, dry pixels floored at
    :data:`DBR_ZEROVALUE`. Its dense field is ``(2, n_px, n_px)`` in pixels per input
    interval with ``V[0]`` along columns and ``V[1]`` along rows, which is exactly the
    :class:`~varuna_sky.types.MotionField` convention, so nothing is transposed or negated.

    ``missing``, when given, is ``(n_px, n_px)`` and ``True`` where no frame can be believed -
    outside the radar's coverage. Those pixels go to pySTEPS as ``nan`` in every frame rather
    than as dry, so no feature is detected on the static edge between the two (module
    docstring). The returned field still covers the whole domain: pySTEPS interpolates it from
    the vectors it tracked inside.
    """
    stack = np.asarray(rain_mm_h, dtype=np.float64)
    if stack.ndim != 3:
        msg = f"rain stack must be (n_frames, n_px, n_px), got {stack.shape}"
        raise ValueError(msg)
    shape = (int(stack.shape[1]), int(stack.shape[2]))
    unseen = None
    if missing is not None:
        unseen = np.asarray(missing, dtype=bool)
        if unseen.shape != shape:
            msg = f"missing mask must be {shape}, got {unseen.shape}"
            raise ValueError(msg)
    if stack.shape[0] < MIN_FLOW_FRAMES:
        return _zero_field(shape, interval, res_m, "fewer than two frames")
    seen = np.ones(shape, dtype=bool) if unseen is None else ~unseen
    if not np.any(np.isfinite(stack) & (stack >= RAIN_FLOOR_MM_H) & seen[None, :, :]):
        return _zero_field(shape, interval, res_m, "no wet pixels in the history")

    dbr = to_dbr(stack)
    if unseen is not None:
        dbr[:, unseen] = np.nan
    try:
        from pysteps.motion import get_method

        uv = np.asarray(get_method("LK")(dbr), dtype=np.float64)
    except Exception as exc:
        log.warning("motion.lk_failed", error=str(exc))
        return _zero_field(shape, interval, res_m, f"lucas-kanade failed: {exc}")

    if uv.shape != (2, *shape) or not np.all(np.isfinite(uv)):
        log.warning("motion.lk_degenerate", shape=tuple(uv.shape))
        return _zero_field(shape, interval, res_m, "lucas-kanade returned an unusable field")
    if not np.any(uv):
        # Shi-Tomasi found no trackable feature, so pySTEPS returns exact zeros. That is an
        # absence of evidence, not a measurement of a stationary storm: label it "zero".
        return _zero_field(shape, interval, res_m, "no trackable features")

    log.info(
        "motion.lucas_kanade",
        u_mean=round(float(uv[0].mean()), 3),
        v_mean=round(float(uv[1].mean()), 3),
        interval_min=round(interval.total_seconds() / 60.0, 1),
    )
    return MotionField(
        u=np.ascontiguousarray(uv[0]),
        v=np.ascontiguousarray(uv[1]),
        interval=interval,
        res_m=res_m,
        method="lucas_kanade",
    )


def optical_flow(
    frames: RadarFrames,
    zr: ZRParams,
    latest_rain_mm_h: NDArray[np.floating] | None = None,
    coverage: NDArray[np.bool_] | None = None,
) -> MotionField:
    """Track the storm across the last three frames (SPEC.md 11.1 step 4).

    ``latest_rain_mm_h``, when given, is the gauge-merged analysis and replaces the newest
    radar-only frame, so the flow is measured on the same field the nowcast starts from.

    ``coverage`` is QC's mask (:func:`varuna_sky.qc.coverage_mask`, ``True`` where the radar
    sees). The pipeline always passes it; pixels outside it are tracked as missing, never as
    dry (module docstring). Without it every pixel is treated as seen, which is only right for
    a field with no range edge inside the domain.
    """
    stack = rain_history(frames, zr, latest_rain_mm_h, n_frames=min(3, frames.n_frames))
    missing = None if coverage is None else ~np.asarray(coverage, dtype=bool)
    return optical_flow_from_rain(stack, frames.interval, frames.grid.res_m, missing)

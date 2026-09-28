"""The nowcaster VARUNA falls back to when pySTEPS is unavailable (SPEC.md 17).

SPEC.md 17 names the fallback for "pySTEPS install fails": *own advection + AR(2) + noise*,
behind the same function as the real thing. This module is that, and it stands behind
:func:`varuna_sky.steps.nowcast`, which calls it when pySTEPS cannot be imported or raises.
A caller cannot tell the two apart except through ``RainEnsemble.source``, which reads
``fallback_steps`` here so the console can label the run honestly (rule 6).

What it does, per SPEC.md 11.1 step 5 as far as a hand-rolled scheme can go:

* a six-level band-pass cascade of the dBR analysis, coarsening by powers of two;
* per-level AR(2) with the coefficients from the lag-1 and lag-2 correlations of the
  Lagrangian-frame history (Yule-Walker), so small scales decay fast and large scales persist;
* spatially correlated noise, drawn white and pushed through the same filter bank so each
  level's perturbation has that level's spatial scale;
* semi-Lagrangian advection of each forecast step along the motion field, by bilinear
  interpolation of the backward trajectory.

**Two deliberate departures from pySTEPS**, both simplifications and both listed in
``docs/SIMPLIFICATIONS.md``:

* the AR recursion runs in the Lagrangian frame for the whole horizon and each step is
  advected once at the end, rather than the cascade being advected between every step. With
  a motion field that is constant over the horizon - which is all optical flow gives us -
  the two agree; with a strongly sheared field they drift apart;
* there is no probability matching. pySTEPS re-imposes the observed intensity distribution on
  every member; here the AR(2) recursion is left to set the distribution, which keeps the
  members' domain means genuinely different and the ensemble spread honest.

Determinism (rule 8): every perturbation comes from ``numpy.random.default_rng(seed)`` and
members are drawn in order, so the same seed gives the same cube byte for byte.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np
import structlog
from scipy.ndimage import gaussian_filter, map_coordinates

from varuna_sky.motion import MAX_RAIN_MM_H, from_dbr, rain_history, to_dbr
from varuna_sky.types import MotionField, RainEnsemble, SkyInputs, ZRParams

if TYPE_CHECKING:  # pragma: no cover - typing only
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.sky.fallback_steps")

__all__ = ["CASCADE_SIGMAS_PX", "N_CASCADE_LEVELS", "cascade", "nowcast"]

N_CASCADE_LEVELS = 6
"""Cascade levels, matching the six SPEC.md 11.1 step 5 asks pySTEPS for."""

CASCADE_SIGMAS_PX: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)
"""Gaussian widths separating the levels, in pixels, doubling from one to sixteen. Five cuts
make six bands. SPEC.md fixes the number of levels, not the widths: powers of two are the
usual scale cascade and are a choice made here."""

AR_ORDER = 2
"""AR(2), as SPEC.md 11.1 step 5 specifies."""

MIN_LEVEL_SD = 1e-6
"""Below this a level is flat, so it is left alone instead of being divided by ~zero."""

MAX_PHI2 = 0.98
"""Stationarity guard on the AR(2) coefficients: a fit from three noisy frames can land
outside the stable triangle, and an unstable recursion would blow up over 36 steps."""


# ============================================================================ cascade
def cascade(field: NDArray[np.floating]) -> list[NDArray[np.floating]]:
    """Split a field into :data:`N_CASCADE_LEVELS` band-pass levels, finest first.

    Level ``k`` is the difference between two successive Gaussian smoothings; the last level
    is the coarsest smoothing itself, so the levels sum back to the field exactly.
    """
    levels: list[NDArray[np.floating]] = []
    previous = np.asarray(field, dtype=np.float64)
    for sigma in CASCADE_SIGMAS_PX:
        smoothed = gaussian_filter(previous, sigma=sigma, mode="nearest")
        levels.append(previous - smoothed)
        previous = smoothed
    levels.append(previous)
    return levels


def _standardise(
    levels: list[NDArray[np.floating]],
) -> tuple[list[NDArray[np.floating]], NDArray[np.floating]]:
    """Divide each level by its own standard deviation; return the levels and those scales."""
    sds = np.array([max(float(level.std()), MIN_LEVEL_SD) for level in levels])
    return [level / sd for level, sd in zip(levels, sds, strict=True)], sds


def _yule_walker(r1: float, r2: float) -> tuple[float, float, float]:
    """AR(2) coefficients from lag-1 and lag-2 correlations, clamped to the stable triangle.

    ``phi0`` is the innovation scale that keeps the recursion at unit variance.

    **Stationary is not enough; the recursion must also not ring.** A rain cascade in the
    Lagrangian frame is a persistence process: its autocorrelation decays, it does not
    oscillate. That is the real-root corner of the stable triangle, where pySTEPS' own fits
    sit - its level-1 coefficients on the test storm are ``phi1 = 1.999, phi2 = -0.999``, a
    double root at 1.0, which is slow decay rather than oscillation despite the negative
    ``phi2``. So the sign of ``phi2`` is not the thing to guard.

    The characteristic equation is ``z^2 - phi1 z - phi2 = 0``; the roots are complex when
    ``phi1^2 + 4 phi2 < 0``, and a complex pair of modulus ``sqrt(-phi2)`` makes the level
    ring with period ``2 pi / arccos(phi1 / (2 sqrt(-phi2)))``. Fitted on frames that optical
    flow failed to align, the lag-2 correlation collapses below ``r1^2`` and the fit lands
    exactly there - measured at ``phi1 = 0.02, phi2 = -0.98``, a barely damped four-step
    ringing that reached 10,589 mm/h over a six-step horizon before this guard existed.

    A collapsed lag-2 correlation means the history cannot support a second-order model, not
    that the rain oscillates. So the fit degrades to AR(1) on the lag-1 correlation alone,
    which is the honest reading of that history, and :data:`~varuna_sky.motion.MAX_RAIN_MM_H`
    backstops whatever still gets through.
    """
    r1 = float(np.clip(r1, -0.995, 0.995))
    r2 = float(np.clip(r2, -0.995, 0.995))
    denominator = 1.0 - r1 * r1
    phi1 = r1 * (1.0 - r2) / denominator
    phi2 = (r2 - r1 * r1) / denominator
    phi2 = float(np.clip(phi2, -MAX_PHI2, MAX_PHI2))
    limit = 1.0 - abs(phi2) - 1e-3
    phi1 = float(np.clip(phi1, -limit, limit))
    if phi1 * phi1 + 4.0 * phi2 < 0.0:  # complex roots: this level would ring, so drop to AR(1)
        phi1, phi2 = r1, 0.0
    variance = 1.0 - phi1 * r1 - phi2 * r2
    return phi1, phi2, float(np.sqrt(max(variance, 1e-6)))


def _correlation(a: NDArray[np.floating], b: NDArray[np.floating]) -> float:
    """Pearson correlation of two levels, 0 when either is flat."""
    a_flat = a.ravel() - a.mean()
    b_flat = b.ravel() - b.mean()
    denominator = float(np.linalg.norm(a_flat) * np.linalg.norm(b_flat))
    if denominator < MIN_LEVEL_SD:
        return 0.0
    return float(np.clip(np.dot(a_flat, b_flat) / denominator, -1.0, 1.0))


# ============================================================================ advection
def _advect(
    field: NDArray[np.floating],
    motion: MotionField,
    intervals: float,
) -> NDArray[np.floating]:
    """Semi-Lagrangian displacement of ``field`` by ``intervals`` frame intervals.

    Backward trajectory: what arrives at ``(row, col)`` left ``(row - v*n, col - u*n)``,
    sampled bilinearly. ``mode="nearest"`` holds the domain edge rather than wrapping the
    storm around to the other side of the city.
    """
    if intervals == 0.0 or motion.method == "zero":
        return np.array(field, dtype=np.float64, copy=True)
    rows, cols = np.indices(field.shape, dtype=np.float64)
    source = np.stack([rows - motion.v * intervals, cols - motion.u * intervals])
    return map_coordinates(field, source, order=1, mode="nearest")


# ============================================================================ nowcast
def nowcast(
    rain_mm_h: NDArray[np.floating],
    motion: MotionField,
    inputs: SkyInputs,
    zr: ZRParams,
) -> RainEnsemble:
    """A seeded ensemble nowcast without pySTEPS; the signature of :func:`varuna_sky.steps.nowcast`.

    ``rain_mm_h`` is the gauge-merged analysis, either the latest field ``(n_px, n_px)`` or a
    history ``(n_frames, n_px, n_px)`` whose last entry is that analysis.
    """
    analysis = np.asarray(rain_mm_h, dtype=np.float64)
    history = (
        analysis
        if analysis.ndim == 3
        else rain_history(inputs.frames, zr, analysis, n_frames=AR_ORDER + 1)
    )
    if history.shape[0] < AR_ORDER + 1:
        pad = np.repeat(history[:1], AR_ORDER + 1 - history.shape[0], axis=0)
        history = np.concatenate([pad, history], axis=0)
    history = history[-(AR_ORDER + 1) :]

    grid = inputs.frames.grid
    n_members = int(inputs.n_members)
    n_steps = int(inputs.n_steps)
    rng = np.random.default_rng(inputs.seed)

    # The history brought to the analysis time, so the AR fit sees the same storm three times
    # rather than three storms in three places.
    dbr_history = [
        _advect(to_dbr(history[i]), motion, float(history.shape[0] - 1 - i))
        for i in range(history.shape[0])
    ]
    latest = dbr_history[-1]
    mean = float(latest.mean())
    sd = max(float(latest.std()), MIN_LEVEL_SD)
    normalised = [(frame - mean) / sd for frame in dbr_history]

    standard: list[list[NDArray[np.floating]]] = []
    scales = np.ones(N_CASCADE_LEVELS)
    for index, frame in enumerate(normalised):
        levels, level_sds = _standardise(cascade(frame))
        standard.append(levels)
        if index == len(normalised) - 1:
            scales = level_sds

    phis = [
        _yule_walker(
            _correlation(standard[-1][k], standard[-2][k]),
            _correlation(standard[-1][k], standard[-3][k]),
        )
        for k in range(N_CASCADE_LEVELS)
    ]
    log.info(
        "fallback_steps.ar",
        phi1=[round(p[0], 3) for p in phis],
        phi2=[round(p[1], 3) for p in phis],
    )

    # Steps are 5 minutes; the motion field is per frame interval, so a step advances the
    # storm by this fraction of an interval.
    interval_min = max(inputs.frames.interval.total_seconds() / 60.0, 1.0)
    step_ratio = float(inputs.step_min) / interval_min

    cube = np.empty((n_members, n_steps, grid.n_px, grid.n_px), dtype=np.float64)
    for member in range(n_members):
        previous = [level.copy() for level in standard[-2]]
        current = [level.copy() for level in standard[-1]]
        for step in range(n_steps):
            white = rng.standard_normal(latest.shape)
            noise, _ = _standardise(cascade(white))
            updated = []
            for k in range(N_CASCADE_LEVELS):
                phi1, phi2, phi0 = phis[k]
                updated.append(phi1 * current[k] + phi2 * previous[k] + phi0 * noise[k])
            previous, current = current, updated
            field = mean + sd * sum(
                scale * level for scale, level in zip(scales, current, strict=True)
            )
            advected = from_dbr(_advect(field, motion, (step + 1) * step_ratio))
            cube[member, step] = np.minimum(advected, MAX_RAIN_MM_H)

    times = tuple(
        inputs.cycle_ts + timedelta(minutes=inputs.step_min * (step + 1)) for step in range(n_steps)
    )
    log.info(
        "fallback_steps.done",
        members=n_members,
        steps=n_steps,
        max_mm_h=round(float(cube.max()), 2),
    )
    return RainEnsemble(
        rain_mm_h=cube,
        times=times,
        grid=grid,
        source="fallback_steps",
        seed=inputs.seed,
        zr=zr,
        motion=motion,
    )

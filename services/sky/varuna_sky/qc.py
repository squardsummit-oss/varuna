"""Radar quality control: coverage, ground clutter and attenuation shadow.

This is SPEC.md 11.1 step 1 and nothing more. One cycle's recent frames go in; the newest
frame comes out with the pixels VARUNA-Sky must not believe set to ``nan``, plus the three
masks that say why:

* **coverage** - the radar's usable disc, measured from the frames rather than assumed. A
  decoded frame is ``nan`` both outside the radar's range and wherever there is no echo
  (``varuna_replay.storm.radar_dbz`` writes ``nan`` for both), so "finite somewhere in the
  recent history" on its own would shrink the disc to whatever happened to be raining. The
  disc is therefore taken as every pixel within the greatest radius, from the domain centre,
  at which any recent frame carries an echo. The radius is measured; only the *shape* - a
  disc centred on the domain, where SPEC.md 3.3 puts the radar - is assumed.
* **clutter** - ground returns: an echo that is there frame after frame and never moves,
  detected as near-zero temporal variance over the last :data:`CLUTTER_FRAMES` frames.
* **attenuation** - the sector behind a convective core above :data:`ATTENUATION_DBZ`,
  where the beam has already given up its power. The prototype **flags** the shadow and
  leaves the values alone; correcting attenuation is not in scope (SPEC.md 11.1 step 1).

Design choices, not spec. SPEC.md fixes the tests (near-zero variance, six frames, 50 dBZ)
but not the numbers that make them decidable: :data:`CLUTTER_VAR_DBZ2`,
:data:`CLUTTER_MIN_DBZ`, :data:`N_AZIMUTH`, :data:`AZIMUTH_CLOSE_BINS` and the two
attenuation-flag fractions are ours, and each says so in its own docstring.

Determinism (rule 8): nothing here is random. The same frames give the same masks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import structlog

from varuna_sky.types import AttenuationFlag, QCResult, RadarFrames, RadarGrid

if TYPE_CHECKING:  # pragma: no cover - keeps numpy out of the runtime type surface
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.sky.qc")

# ----------------------------------------------------------------------------- clutter
CLUTTER_FRAMES = 6
"""How many recent frames the temporal variance is taken over (SPEC.md 11.1 step 1).

With fewer frames than this the variance is simply taken over the frames given - the
contract guarantees at least three. Fewer frames is a weaker test, not a different one: a
slow-moving cell has had less time to change a pixel, so a short history flags more
persistent-looking rain as clutter. The count actually used is logged."""

CLUTTER_VAR_DBZ2 = 1.0
"""Variance below which an echo counts as "near-zero", in dBZ squared.

A design choice. Decoded frames are quantised to 5 dBZ classes, so a genuinely unmoving echo
has a variance of exactly zero while anything that changes class at all has a variance of at
least 25/4; 1.0 dBZ^2 (a standard deviation of 1 dBZ) sits comfortably between the two and
also tolerates un-quantised live frames that dither by a fraction of a class."""

CLUTTER_MIN_DBZ = 35.0
"""Weakest mean echo that may be called ground clutter.

A design choice, and the one that keeps the variance test usable. Ground clutter is a strong
return from buildings or hills; a broad stratiform background is not, yet after quantisation
it sits in one 5 dBZ class frame after frame and would fail the variance test wholesale,
blanking most of the domain. 35 dBZ is above the stratiform background the bundles render
(2-5 mm/h, quantised to the 25 and 30 dBZ classes) and below the convective cores the demo
depends on. The limit is honest: rain that both exceeds 35 dBZ and holds one class for the
whole hour would still be called clutter. Convective cells move, so it does not arise."""

# ------------------------------------------------------------------------- attenuation
ATTENUATION_DBZ = 50.0
"""Reflectivity above which a cell casts a shadow along its radial (SPEC.md 11.1 step 1)."""

N_AZIMUTH = 360
"""Azimuth bins the shadow sector is built in - one degree each.

A design choice, taken to be about the beam width of an operational S-band radar, so the
flagged sector is never finer than the physical beam that would be blocked."""

AZIMUTH_CLOSE_BINS = 1
"""Bins either side of a shadowed azimuth that are shadowed with it.

A design choice. A core a few pixels across does not land in every azimuth bin it
geometrically subtends, which would stripe the shadow; closing across one bin either side
removes the striping at the cost of widening the flagged sector by one degree. Widening is
the safe direction for a flag that is never used to correct the data."""

ATTENUATION_PARTIAL_FRACTION = 0.01
"""Shadowed share of the covered domain at which the flag leaves ``none``. A design choice:
below one per cent the shadow is a nuisance, not a caveat on the cycle."""

ATTENUATION_SEVERE_FRACTION = 0.10
"""Shadowed share of the covered domain at which the flag becomes ``severe``. A design
choice: above a tenth of the disc, enough of the nowcast sits behind a core that the console
should say so."""


# ============================================================================ geometry
def _polar(grid: RadarGrid) -> tuple[NDArray[np.floating], NDArray[np.intp]]:
    """Radius in metres and azimuth bin of every pixel, about the domain centre.

    SPEC.md 3.3 centres the Sky domain on the radar, so the domain centre is the origin
    that the coverage disc and the attenuation radials are measured from.
    """
    n = grid.n_px
    offsets = np.arange(n, dtype=np.float64) + 0.5 - n / 2.0
    dy = offsets[:, None]  # rows run north to south
    dx = offsets[None, :]
    radius = np.hypot(dx, dy) * grid.res_m
    bearing = np.arctan2(dx, -dy)  # clockwise from north, in [-pi, pi]
    bins = np.floor((bearing % (2.0 * np.pi)) / (2.0 * np.pi) * N_AZIMUTH).astype(np.intp)
    return radius, np.minimum(bins, N_AZIMUTH - 1)


# ============================================================================ coverage
def coverage_mask(dbz: NDArray[np.floating], grid: RadarGrid) -> NDArray[np.bool_]:
    """The radar's usable disc, measured from the frames themselves.

    ``True`` means the radar sees the pixel. The disc radius is the greatest distance from
    the domain centre at which any of the given frames carries a finite value; filling the
    disc, rather than taking the finite pixels directly, is what stops a dry pixel *inside*
    the range ring being mistaken for a gap in coverage.
    """
    seen = np.any(np.isfinite(dbz), axis=0)
    if not bool(seen.any()):
        return np.zeros(grid.shape, dtype=bool)
    radius, _ = _polar(grid)
    return radius <= float(radius[seen].max())


# ============================================================================ clutter
def clutter_mask(dbz: NDArray[np.floating]) -> NDArray[np.bool_]:
    """Pixels with a strong echo of near-zero temporal variance over the recent frames.

    A pixel qualifies only if it has a finite echo in *every* one of the last
    :data:`CLUTTER_FRAMES` frames - clutter is persistent by definition - whose mean is at
    least :data:`CLUTTER_MIN_DBZ` and whose variance is below :data:`CLUTTER_VAR_DBZ2`.
    """
    recent = np.asarray(dbz, dtype=np.float64)[-CLUTTER_FRAMES:]
    if recent.shape[0] < 2:
        return np.zeros(recent.shape[1:], dtype=bool)
    persistent = np.all(np.isfinite(recent), axis=0)
    values = np.where(persistent[None, :, :], recent, 0.0)
    variance = values.var(axis=0)
    mean = values.mean(axis=0)
    return persistent & (mean >= CLUTTER_MIN_DBZ) & (variance < CLUTTER_VAR_DBZ2)


# ============================================================================ attenuation
def attenuation_shadow(
    frame: NDArray[np.floating],
    coverage: NDArray[np.bool_],
    grid: RadarGrid,
) -> NDArray[np.bool_]:
    """Covered pixels that sit behind a core above :data:`ATTENUATION_DBZ`.

    "Behind" is along the radial away from the domain centre: within each azimuth bin, every
    covered pixel further out than the nearest such core is shadowed. The cores themselves
    are the cause, not the victim, so they are never flagged.
    """
    radius, azimuth = _polar(grid)
    with np.errstate(invalid="ignore"):
        strong = coverage & np.isfinite(frame) & (frame >= ATTENUATION_DBZ)
    if not bool(strong.any()):
        return np.zeros(grid.shape, dtype=bool)
    nearest = np.full(N_AZIMUTH, np.inf, dtype=np.float64)
    np.minimum.at(nearest, azimuth[strong], radius[strong])
    for shift in range(1, AZIMUTH_CLOSE_BINS + 1):
        nearest = np.minimum(nearest, np.minimum(np.roll(nearest, shift), np.roll(nearest, -shift)))
    return coverage & (radius > nearest[azimuth]) & ~strong


def attenuation_flag(
    shadow: NDArray[np.bool_],
    coverage: NDArray[np.bool_],
) -> AttenuationFlag:
    """How much of the covered domain the shadow costs us, as the console's three-way label."""
    covered = float(coverage.sum())
    if covered <= 0.0:
        return "none"
    fraction = float(shadow.sum()) / covered
    if fraction < ATTENUATION_PARTIAL_FRACTION:
        return "none"
    if fraction < ATTENUATION_SEVERE_FRACTION:
        return "partial"
    return "severe"


# ============================================================================ entry point
def run_qc(frames: RadarFrames) -> QCResult:
    """Quality control one cycle's radar history (SPEC.md 11.1 step 1).

    Returns the three masks and the newest frame with clutter and out-of-coverage pixels set
    to ``nan``. Shadowed pixels keep their values: attenuation is flagged, never corrected.
    """
    dbz = np.asarray(frames.dbz, dtype=np.float64)
    if dbz.ndim != 3 or dbz.shape[0] < 1:
        raise ValueError(
            f"Radar frames must be (n_frames, n_px, n_px) with at least one frame; got {dbz.shape}."
        )
    if dbz.shape[1:] != frames.grid.shape:
        raise ValueError(f"Frames {dbz.shape[1:]} do not match the grid {frames.grid.shape}.")

    coverage = coverage_mask(dbz, frames.grid)
    clutter = clutter_mask(dbz) & coverage
    latest = np.where(coverage & ~clutter, dbz[-1], np.nan)
    shadow = attenuation_shadow(latest, coverage, frames.grid)
    flag = attenuation_flag(shadow, coverage)

    log.info(
        "sky.qc",
        latest_ts=frames.latest_ts.isoformat(),
        n_frames=int(dbz.shape[0]),
        variance_frames=min(int(dbz.shape[0]), CLUTTER_FRAMES),
        coverage_fraction=round(float(coverage.mean()), 4),
        clutter_fraction=round(float(clutter.mean()), 6),
        attenuation_flag=flag,
    )
    return QCResult(
        coverage=coverage,
        clutter=clutter,
        attenuation=shadow,
        attenuation_flag=flag,
        dbz=latest,
    )


__all__ = [
    "ATTENUATION_DBZ",
    "ATTENUATION_PARTIAL_FRACTION",
    "ATTENUATION_SEVERE_FRACTION",
    "AZIMUTH_CLOSE_BINS",
    "CLUTTER_FRAMES",
    "CLUTTER_MIN_DBZ",
    "CLUTTER_VAR_DBZ2",
    "N_AZIMUTH",
    "attenuation_flag",
    "attenuation_shadow",
    "clutter_mask",
    "coverage_mask",
    "run_qc",
]

"""Gauge merge: mean-field bias, then inverse-distance residuals (SPEC.md 11.1 step 3).

Radar measures reflectivity everywhere and rain nowhere; a gauge measures rain at one point.
This stage spends the gauges to correct the radar field, in the two steps SPEC.md 11.1
step 3 names and no others: a single multiplicative **mean-field bias** ``MFB = sum(G)/sum(R)``
over the co-located pairs, then **inverse-distance interpolation of what is still left over**
at each gauge. Kriging with external drift (``pykrige``) is P1; :class:`MergeResult.method`
records which of the two steps actually ran, so the console never implies more than happened.

**The residual is additive, not a ratio.** ``G - R``, never ``G / R``. Three reasons, and the
third is the one that matters:

* the multiplicative part of the error is what MFB has just removed, so a second ratio field
  stacked on top of it is correcting the same thing twice;
* the ratio's denominator is the radar field itself, which is zero over most of a convective
  domain, so ``G / R`` is unbounded exactly where the gauges are most informative;
* a ratio can never add rain where the radar saw none - it multiplies zero by anything and
  gets zero. "The radar missed this cell" is precisely the failure a gauge merge exists to
  catch, and only an additive residual can express it.

The price of additive residuals is that they are signed and unbounded, so two guards apply.
The merged field is clipped at zero (a negative rain rate is not a rain rate), and the
residual is interpolated only within :data:`IDW_RADIUS_M` of a gauge. Beyond that radius the
field is the bias-corrected radar and nothing else - which is what keeps a dry corner of the
domain dry when a gauge 30 km away reports a cloudburst.

**Exactness at the gauge, and the bullseyes that come with it.** Inverse-distance weighting is
an exact interpolator: at a gauge's own pixel that gauge's residual dominates, so the merged
field reproduces the reading and SPEC.md's "honours gauges within 5 %" is met with room.
:attr:`MergeResult.max_gauge_error_pct` is *measured* from the returned field at the gauge
pixels rather than asserted, because the cases where the merge cannot honour a gauge are real
and must be visible: two gauges disagreeing inside one 500 m pixel, a reading the
non-negativity clip has to cut, and the single-gauge path below. The known cost of exactness
is a bullseye around each gauge; smoothing it is what KED buys in P1.

**One gauge is not an interpolation.** With a single station, inverse-distance weighting has
one datum and returns it everywhere inside the radius - a constant offset dressed up as a
field. That is a claim about spatial structure that one gauge cannot support, so the residual
step is skipped and ``method`` reads ``mfb``: the honest description of a global bias
correction. With no usable station at all, ``method`` reads ``none``, ``mfb`` is exactly 1.0
and the field is handed back as it arrived.

Determinism (rule 8): nothing here is random, and the two places where floating-point
addition order could leak the caller's input order - the MFB sums and the weighted residual
sum - are taken over pairs sorted by a total key, so a reshuffled input gives a byte-identical
field.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

import numpy as np
import structlog

from varuna_sky.motion import RAIN_FLOOR_MM_H, rain_from_dbz
from varuna_sky.types import GaugePair, MergeResult, RadarGrid, ZRParams

if TYPE_CHECKING:  # pragma: no cover - keeps numpy out of the runtime type surface
    from collections.abc import Sequence

    from numpy.typing import NDArray

log = structlog.get_logger("varuna.sky.merge")

# ----------------------------------------------------------------------------- contract
GAUGE_TOLERANCE_PCT = 5.0
"""The acceptance criterion: "merged field honours gauges within 5 %" (SPEC.md 11.1 step 3).

Nothing in this module enforces it - it is the number the tests and ``/verify`` check
:attr:`MergeResult.max_gauge_error_pct` against. A merge that misses it must say so, not
quietly widen the tolerance."""

# --------------------------------------------------------------------- mean-field bias
MFB_MIN = 0.2
MFB_MAX = 5.0
"""Bounds on ``sum(G)/sum(R)``. A design choice; SPEC.md fixes the formula, not its range.

Quantisation to 5 dBZ classes alone makes the bundles' radar under-read by up to a factor
2.05 (``varuna_replay.storm.QUANTISATION_RATIO``), so a legitimate MFB near 2 is expected and
must not be clipped. Beyond a factor of five in either direction the ratio is describing a
broken input rather than a wet-bias: a gauge network reading zero through a storm is far more
likely to be blocked, frozen or simply not reporting than to be right about a radar that sees
50 dBZ. The bias is then held at the bound, and the clamp is logged at warning level - there
is no field on :class:`~varuna_sky.types.MergeResult` to carry it, so the log is the record."""

# ------------------------------------------------------------------------------- IDW
IDW_POWER = 2.0
"""Exponent of the inverse-distance weight. A design choice, not spec.

Two is the usual choice for interpolating a radar-QPE residual: one leaves a gauge's influence
audible across the whole search radius, three collapses it into its own pixel and makes the
field a set of spikes. It is a keyword argument so a city configuration can differ."""

IDW_RADIUS_M = 20_000.0
"""How far a gauge's residual reaches, in metres. A design choice, not spec.

It is the knob that decides how much rain a gauge may invent where the radar saw none, so it
is set by two bounds rather than by a literature value we cannot cite. It must be small
against the 60 km Sky domain (SPEC.md 3.3) - at a third of the domain width no single
station can rewrite the field - and large against the spacing of the Mumbai gauge network,
which is a few kilometres, so that the discs overlap and the residual field is continuous
across the area of interest instead of a scatter of islands. 20 km satisfies both."""

IDW_MIN_DIST_M = 1.0
"""Distance floor, in metres, that keeps ``1 / d^p`` finite at a gauge's own pixel.

A numerical choice. :class:`~varuna_sky.types.GaugePair` carries the gauge's row and column
and not its sub-pixel position, so a gauge is at its pixel centre by construction and the
distance there is exactly zero. Flooring at one metre gives that gauge a weight some 2.5e5
times its nearest plausible neighbour's, which is the textbook coincident-point rule -
"return the datum" - reached with one closed-form weight instead of a special case. When two
stations genuinely share a pixel they both sit on the floor and the pixel gets their mean,
which is also the textbook rule, and is one of the cases
:attr:`~varuna_sky.types.MergeResult.max_gauge_error_pct` exists to expose."""


# ============================================================================ pair hygiene
def _has_usable_gauge(pair: GaugePair, grid: RadarGrid) -> bool:
    """A reading that can anchor anything: finite, non-negative, and on the grid."""
    return (
        math.isfinite(pair.gauge_mm_h)
        and pair.gauge_mm_h >= 0.0
        and grid.contains(pair.row, pair.col)
    )


def mfb_pairs(pairs: Sequence[GaugePair], grid: RadarGrid) -> list[GaugePair]:
    """The pairs the mean-field bias may be summed over, in a fixed order.

    ``sum(G)/sum(R)`` needs both halves, so a pair whose radar value is missing - the gauge
    sits under clutter, or outside the coverage disc - is dropped here even though it can
    still anchor a residual. The sort is what makes the sums independent of the order the
    caller happened to build the pairs in (rule 8).
    """
    kept = [p for p in pairs if _has_usable_gauge(p, grid) and math.isfinite(p.dbz)]
    kept.sort(key=lambda p: (p.ts, p.station_id, p.gauge_mm_h, p.dbz))
    return kept


def gauge_anchors(pairs: Sequence[GaugePair], grid: RadarGrid) -> list[GaugePair]:
    """One reading per station - the latest - sorted by station id.

    The bias is a statement about the last hour, so it is summed over the whole history; the
    residual is a statement about *this* analysis instant, so each station contributes only
    its newest reading. A station that reports twice for the same instant is a duplicate, and
    the tie is broken on the reading itself rather than on arrival order so the choice is
    reproducible.

    A missing radar value is no obstacle here: the residual is measured against the analysis
    field, not against the pair's own reflectivity, so a gauge the radar missed entirely is
    exactly the gauge this stage exists for.
    """
    latest: dict[str, GaugePair] = {}
    for pair in pairs:
        if not _has_usable_gauge(pair, grid):
            continue
        held = latest.get(pair.station_id)
        key = (pair.ts, pair.gauge_mm_h, pair.row, pair.col)
        if held is None or key > (held.ts, held.gauge_mm_h, held.row, held.col):
            latest[pair.station_id] = pair
    return [latest[station_id] for station_id in sorted(latest)]


# ======================================================================== mean-field bias
def mean_field_bias(pairs: Sequence[GaugePair], zr: ZRParams) -> float:
    """``sum(G) / sum(R)`` over co-located pairs (SPEC.md 11.1 step 3, Appendix A).

    ``R`` is each pair's reflectivity through the cycle's ``Z = a R^b``, so the bias is
    measured against the same relation the analysis field was built with. The ratio of sums,
    rather than the mean of ratios, is what SPEC.md specifies and is also the stabler
    statistic: it weights each pair by how much rain it saw, so a near-dry gauge cannot
    dominate the correction with a large but meaningless ratio.

    Returns exactly 1.0 - "no correction" - when there is nothing to measure it from, which
    is the value :class:`~varuna_sky.types.MergeResult` documents for that case.
    """
    if not pairs:
        return 1.0
    gauge = np.array([p.gauge_mm_h for p in pairs], dtype=np.float64)
    radar = rain_from_dbz(np.array([p.dbz for p in pairs], dtype=np.float64), zr)
    total_g = float(gauge.sum())
    total_r = float(radar.sum())
    if not math.isfinite(total_g) or not math.isfinite(total_r) or total_r <= 0.0:
        log.warning("sky.merge.mfb_undefined", n_pairs=len(pairs), radar_total_mm_h=total_r)
        return 1.0
    raw = total_g / total_r
    used = min(max(raw, MFB_MIN), MFB_MAX)
    if used != raw:
        log.warning(
            "sky.merge.mfb_clamped",
            raw=round(raw, 4),
            used=used,
            n_pairs=len(pairs),
        )
    return used


# =============================================================================== residual
def idw_residual(
    anchors: Sequence[GaugePair],
    residuals: Sequence[float],
    grid: RadarGrid,
    *,
    power: float = IDW_POWER,
    radius_m: float = IDW_RADIUS_M,
) -> NDArray[np.floating]:
    """Interpolate gauge residuals across the grid by inverse-distance weighting.

    Two departures from a bare ``sum(w r) / sum(w)`` with ``w = 1 / d^p``, both of them there
    to stop the search radius drawing a ring on the map:

    * the weight is Shepard's tapered form, ``((radius - d) / (radius * d))^p``, which equals
      ``1 / d^p`` close to a gauge and falls to exactly zero at the radius;
    * the denominator carries one extra term, ``radius^-p`` - the plain inverse-distance
      weight of a point sitting *at* the search radius, standing in for the zero residual that
      applies where no gauge reaches. It is not a free parameter: it is the weight function
      evaluated at the only distance the radius names.

    Normalising by the gauge weights alone would have cancelled the taper - divide a vanishing
    numerator by a vanishing denominator and the residual comes back at full strength right up
    to the boundary, then drops to zero the pixel after. With the extra term the interpolant
    decays smoothly to zero: it keeps essentially all of its value within a few kilometres of a
    gauge (2 % is given up at 2.5 km, with the 20 km default), gives up half of it at half the
    radius, and reaches the radius already at nothing. The denominator can never be zero, so a
    pixel out of every gauge's reach gets exactly zero without a special case.

    Anchors are consumed in the order given, which :func:`gauge_anchors` fixes, so the
    weighted sum is bit-reproducible.
    """
    rows = np.arange(grid.n_px, dtype=np.float64)[:, None]
    cols = np.arange(grid.n_px, dtype=np.float64)[None, :]
    weight_total = np.full(grid.shape, radius_m**-power, dtype=np.float64)
    value_total = np.zeros(grid.shape, dtype=np.float64)
    for anchor, residual in zip(anchors, residuals, strict=True):
        distance = np.hypot(rows - anchor.row, cols - anchor.col) * grid.res_m
        distance = np.maximum(distance, IDW_MIN_DIST_M)
        weight = (np.clip(radius_m - distance, 0.0, None) / (radius_m * distance)) ** power
        weight_total += weight
        value_total += weight * residual
    return value_total / weight_total


# ============================================================================== the metric
def gauge_error_pct(
    merged: NDArray[np.floating],
    anchors: Sequence[GaugePair],
) -> float | None:
    """Largest relative miss at a gauge, measured from the field that is actually returned.

    A dry gauge has no percentage to be within, so a reading below the house dry floor
    (:data:`~varuna_sky.motion.RAIN_FLOOR_MM_H`) is compared against the floor itself. That
    turns "the merge put rain on a gauge that reported none" into a large number rather than
    into an infinity or, worse, a silent pass.
    """
    if not anchors:
        return None
    worst = 0.0
    for anchor in anchors:
        value = float(merged[anchor.row, anchor.col])
        denominator = max(anchor.gauge_mm_h, RAIN_FLOOR_MM_H)
        worst = max(worst, abs(value - anchor.gauge_mm_h) / denominator * 100.0)
    return worst


# ============================================================================ entry point
def merge_gauges(
    rain_mm_h: NDArray[np.floating],
    pairs: Sequence[GaugePair],
    grid: RadarGrid,
    zr: ZRParams,
    *,
    power: float = IDW_POWER,
    radius_m: float = IDW_RADIUS_M,
) -> MergeResult:
    """Adjust one analysis field to the gauges (SPEC.md 11.1 step 3).

    Args:
        rain_mm_h: the radar analysis on the Sky grid in mm/h - the quality-controlled newest
            frame put through the cycle's Z-R relation. Missing pixels may be ``nan``; they
            come back as exactly zero, the same convention
            :func:`~varuna_sky.motion.rain_history` uses, because a decoded frame writes
            ``nan`` for "no echo" and quality control writes it for "do not believe this".
        pairs: the co-located gauge-radar pairs of the last 60 minutes, as built for the Z-R
            fit (SPEC.md 11.1 step 2). They are taken as an argument rather than rebuilt so
            the cycle pairs its gauges to its radar exactly once.
        grid: the Sky grid the field and the pairs' rows and columns live on.
        zr: the relation the pairs' reflectivities are converted with.
        power: inverse-distance exponent, defaulting to :data:`IDW_POWER`.
        radius_m: reach of a gauge's residual, defaulting to :data:`IDW_RADIUS_M`.

    Returns:
        The merged field with the bias that was applied, the number of stations that anchored
        it, an honest label for which steps ran, and the largest relative miss the result
        still has at a gauge.
    """
    radar = np.asarray(rain_mm_h, dtype=np.float64)
    if radar.shape != grid.shape:
        msg = f"Analysis field {radar.shape} does not match the grid {grid.shape}."
        raise ValueError(msg)
    radar = np.where(np.isfinite(radar), radar, 0.0)

    anchors = gauge_anchors(pairs, grid)
    if not anchors:
        log.info("sky.merge", method="none", n_gauges=0, mfb=1.0, n_pairs=len(pairs))
        return MergeResult(
            rain_mm_h=radar,
            mfb=1.0,
            n_gauges=0,
            method="none",
            max_gauge_error_pct=None,
        )

    mfb = mean_field_bias(mfb_pairs(pairs, grid), zr)
    base = radar * mfb

    method: Literal["mfb", "mfb+idw"]
    if len(anchors) == 1:
        # One datum is a constant, not a field. Say "mfb" and mean it.
        merged = np.clip(base, 0.0, None)
        method = "mfb"
    else:
        residuals = [a.gauge_mm_h - float(base[a.row, a.col]) for a in anchors]
        correction = idw_residual(anchors, residuals, grid, power=power, radius_m=radius_m)
        merged = np.clip(base + correction, 0.0, None)
        method = "mfb+idw"

    error_pct = gauge_error_pct(merged, anchors)
    log.info(
        "sky.merge",
        method=method,
        n_gauges=len(anchors),
        n_pairs=len(pairs),
        mfb=round(mfb, 4),
        max_gauge_error_pct=None if error_pct is None else round(error_pct, 3),
        within_tolerance=error_pct is not None and error_pct <= GAUGE_TOLERANCE_PCT,
        wet_fraction=round(float((merged >= RAIN_FLOOR_MM_H).mean()), 4),
    )
    return MergeResult(
        rain_mm_h=merged,
        mfb=mfb,
        n_gauges=len(anchors),
        method=method,
        max_gauge_error_pct=error_pct,
    )


__all__ = [
    "GAUGE_TOLERANCE_PCT",
    "IDW_MIN_DIST_M",
    "IDW_POWER",
    "IDW_RADIUS_M",
    "MFB_MAX",
    "MFB_MIN",
    "gauge_anchors",
    "gauge_error_pct",
    "idw_residual",
    "mean_field_bias",
    "merge_gauges",
    "mfb_pairs",
]

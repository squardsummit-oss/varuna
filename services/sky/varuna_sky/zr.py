"""The adaptive Z-R relation: what this cycle's reflectivity is worth in millimetres.

SPEC.md 11.1 step 2 and nothing more. ``Z = 10^(dBZ/10)`` is a definition; ``Z = a R^b`` is
not, and the pair ``(a, b)`` that holds over Mumbai during a monsoon cloudburst is not the pair
that holds over stratiform drizzle. So every cycle re-earns its own relation from the gauges
that reported in the last hour, and says in :class:`~varuna_sky.types.ZRParams` whether it
managed to:

1. **pair** - each gauge reading in the window is matched to the radar pixel it stands in and
   the frame nearest its accumulation window, giving a :class:`~varuna_sky.types.GaugePair`;
2. **fit** - ``log Z = log a + b log R`` by least squares over those pairs, with ``a`` clamped
   to [100, 400] and ``b`` to [1.1, 1.8];
3. **fall back** - fewer than :data:`MIN_ZR_PAIRS` usable pairs and the cycle uses
   Marshall-Palmer (200, 1.6) with ``source="marshall_palmer"``, so the run never implies a
   relation it did not measure.

**Which variable is regressed on which, and why it matters.** SPEC.md says "least squares on
log R_gauge vs log Z", which is the useful orientation: the gauge is the predictor and the
radar the response. Reflectivity carries the larger error - speckle, beam geometry, the 5 dBZ
classes a decoded image arrives in - and least squares assumes the error lives in the response.
Regressing the other way would attenuate ``b`` towards zero by exactly the ratio of radar noise
to gauge spread.

**The frames are fitted as they arrive.** A decoded radar image is quantised into 5 dBZ classes
labelled by their *lower* edge (``varuna_replay.storm.radar_dbz`` floors to
:data:`DBZ_CLASS_WIDTH`), which under-reads rain by up to a factor of 2.05 and biases the fitted
``a`` low by about ``10^(w/20)``. That bias is not corrected by default and the choice is
deliberate: the relation fitted here is applied to those same quantised frames by
:func:`varuna_sky.motion.rain_from_dbz` and by both nowcasters, so a fit that undoes the
quantisation while the application does not would put the bias straight back into the rain
field. Absorbing the artefact is what makes the product unbiased. ``dbz_class_width`` is there
for the one question the raw fit cannot answer - *which relation generated this field?* - which
is what the SPEC.md 11.1 acceptance test asks, and it is off unless the caller asks for it.

Design choices, not spec. SPEC.md fixes the clamps, the eight-pair floor and Marshall-Palmer;
it does not say when a pair is too dry to carry information, how far a gauge may sit from a
frame in time, or what to do when every gauge reads the same rate. :data:`MIN_PAIR_MM_H`,
:data:`MIN_LOG10_SPAN` and the half-interval pairing tolerance are ours, and each says so where
a reader will hit it.

Determinism (rule 8): nothing here is random and the pairs are ordered by ``(ts, station_id)``
before they are summed, so the same frames and the same gauge table give the same ``(a, b)``
bit for bit however the rows arrived.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np
import structlog
from pyproj import Transformer

from varuna_sky.types import GaugePair, QCResult, RadarFrames, ZRParams

if TYPE_CHECKING:  # pragma: no cover - keeps numpy and pandas out of the runtime type surface
    import pandas as pd
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.sky.zr")

WGS84 = "EPSG:4326"
"""Gauge coordinates are lon/lat; the radar grid is metric (SPEC.md 3.3, EPSG:32643)."""

# ------------------------------------------------------------------------ the relation
MP_A = 200.0
"""Marshall-Palmer prefactor, the fallback relation (SPEC.md 11.1 step 2, Appendix A).

The same number ``varuna_replay.storm.MP_A`` renders the bundles' frames with; it is restated
here rather than imported because Sky depends on ``varuna_schemas`` and nothing else in the
workspace (``types.py``), and a nowcaster must not need the replay service to run."""

MP_B = 1.6
"""Marshall-Palmer exponent (SPEC.md 11.1 step 2, Appendix A)."""

A_RANGE = (100.0, 400.0)
"""Clamp on the fitted prefactor (SPEC.md 11.1 step 2)."""

B_RANGE = (1.1, 1.8)
"""Clamp on the fitted exponent (SPEC.md 11.1 step 2)."""

MIN_ZR_PAIRS = 8
"""Fewest co-located pairs an adaptive fit may be made from (SPEC.md 11.1 step 2).

Below this the cycle uses Marshall-Palmer. This is the constant
:class:`~varuna_sky.types.ZRParams` names; it lives here because the fit is the only thing that
can honour it, and it is not in ``varuna_schemas.constants`` because no other service needs to
agree on it."""

# ------------------------------------------------------------------------- the pairing
PAIR_WINDOW_MIN = 60.0
"""How far back gauge readings are drawn from, in minutes (SPEC.md 11.1 step 2: "the last
60 min"). The window ends at the newest frame's valid time."""

GAUGE_ACCUMULATION_MIN = 5.0
"""The ``mm_5min`` column is the depth accumulated in the five minutes ending at ``ts``
(SPEC.md 10.2, ``varuna_replay.streams.GAUGE_ACCUMULATION_MIN``).

Two things follow, and both are done in :func:`gauge_pairs`. The mean rate over those minutes is
twelve times the depth, because the fit works in mm/h. And the instant the reading describes is
the *middle* of its window, ``ts - 2.5 min``, not its end, so that is the instant a radar frame
is matched to."""

MIN_PAIR_MM_H = 0.1
"""Gauge rate below which a pair carries no information about ``(a, b)``, in mm/h.

A design choice, and the reason the fit does not divide by zero: ``log R`` is undefined at a dry
gauge, and a gauge reading nothing tells us only that it was not raining there, never how
reflectivity converts to rain. 0.1 mm/h is the pySTEPS radar-QPE dry floor, the same floor
``varuna_sky.motion.RAIN_FLOOR_MM_H`` uses; it is restated rather than imported so that step 2
does not depend on step 4."""

MIN_LOG10_SPAN = 0.301
"""Decades of gauge rain rate the pairs must span before an exponent may be fitted.

A design choice, extending the spec's fallback rule. ``0.301`` is ``log10(2)``: a factor of two.
SPEC.md only says to fall back below eight pairs, but eight gauges all reading within a factor
of two of each other pin an intercept and say nothing about a slope - and the slope is then
extrapolated across the three decades (0.1 to 200 mm/h) the relation is actually used over. When
the span is short the cycle uses Marshall-Palmer and logs the reason."""

DBZ_CLASS_WIDTH = 5.0
"""Width of the reflectivity classes a decoded radar image arrives in, in dBZ.

SPEC.md 11.1 and P2.9 both fix 5 dBZ classes, and ``varuna_replay.storm`` renders the bundles
that way, so this is the value a caller passes as ``dbz_class_width`` when it wants the class
artefact undone. It is **not** the default for that argument - the module docstring explains why
the fit normally wants the frames exactly as they arrive - it is the number to pass when it
does."""


# ============================================================================ projection
@lru_cache(maxsize=8)
def _to_metric(crs: str) -> Transformer:
    """Cached lon/lat to grid-CRS transformer; ``always_xy`` so the order is (x, y)."""
    return Transformer.from_crs(WGS84, crs, always_xy=True)


# ============================================================================ pairing
REQUIRED_COLUMNS = ("ts", "station_id", "lon", "lat", "mm_5min")
"""The bundle's ``gauges.csv`` schema (SPEC.md 10.2)."""


def _require_columns(gauges: pd.DataFrame) -> None:
    missing = [name for name in REQUIRED_COLUMNS if name not in gauges.columns]
    if missing:
        msg = (
            f"The gauge table is missing {', '.join(missing)}. It must carry the bundle's "
            f"gauges.csv columns: {', '.join(REQUIRED_COLUMNS)}."
        )
        raise ValueError(msg)


def _as_datetime(value: object) -> datetime:
    """A pandas ``Timestamp`` is already a ``datetime``; anything else is not a reading time."""
    if isinstance(value, datetime):
        return value
    msg = f"Gauge timestamps must be datetimes, got {type(value).__name__} ({value!r})."
    raise TypeError(msg)


def _nearest_frame(times: tuple[datetime, ...], instant: datetime, tolerance: timedelta) -> int:
    """Index of the frame closest to ``instant``, or ``-1`` when the closest is too far.

    A gauge reading and a radar frame are never simultaneous, so "co-located" has to mean
    co-located in time as well. The tolerance is half a frame interval (a design choice): it is
    the largest gap for which the matched frame is still the *nearest* one, so widening it would
    only pair readings with frames that a future cycle will match better.
    """
    gaps = [abs(frame_ts - instant) for frame_ts in times]
    best = min(range(len(gaps)), key=gaps.__getitem__)
    return best if gaps[best] <= tolerance else -1


def gauge_pairs(
    frames: RadarFrames,
    gauges: pd.DataFrame,
    qc: QCResult,
    *,
    window_min: float = PAIR_WINDOW_MIN,
    dbz_class_width: float = 0.0,
) -> list[GaugePair]:
    """Co-located gauge readings and radar samples from the last ``window_min`` minutes.

    A row becomes a pair only if every one of these holds, and each rejection is counted into
    the log line so a cycle that falls back can say which of them starved it:

    * the reading's ``ts`` lies in the window ending at the newest frame's valid time;
    * a frame lies within half a frame interval of the middle of the reading's accumulation
      window (see :data:`GAUGE_ACCUMULATION_MIN`);
    * the station projects into the radar grid at all;
    * the pixel is inside the radar's coverage disc and is not ground clutter - the QC masks
      (SPEC.md 11.1 step 1) describe the geometry, not one instant, so they hold for every
      frame in the hour, not only the newest;
    * that frame's pixel is finite. ``nan`` means no echo, which against a wet gauge is a real
      miss and worth knowing about, but it is not a value the logarithm of a reflectivity can be
      taken of;
    * the gauge rate is at least :data:`MIN_PAIR_MM_H`.

    Pixels in the attenuation shadow are **kept**. The flag is a whole radial sector behind any
    core above 50 dBZ, and in a monsoon cloudburst that is most of the downrange domain; dropping
    it would leave the fit with the gauges nearest the radar and nothing else. The count is
    logged so the shadow's share of the fit is visible, which is what the flag is for.

    Args:
        frames: the cycle's radar history; the newest frame's time ends the window.
        gauges: the bundle's ``gauges.csv`` columns (SPEC.md 10.2) covering at least the
            window. Row order does not matter: pairs come back ordered by ``(ts, station_id)``.
        qc: the masks from :func:`varuna_sky.qc.run_qc`.
        window_min: how far back readings are taken from.
        dbz_class_width: when non-zero, each radar sample is moved to the middle of the
            reflectivity class it was decoded into (``dbz + width / 2``), because the class is
            labelled by its lower edge. Off by default - the module docstring explains why the
            pipeline wants the frames as they arrive, and why the SPEC.md acceptance test is
            the caller that does not.

    Returns:
        Pairs ordered by ``(ts, station_id)``, so the fit that consumes them sums in a fixed
        order whatever order the rows arrived in (rule 8).
    """
    _require_columns(gauges)
    if len(gauges.index) == 0:
        log.info("sky.zr.pairs", n_rows=0, n_pairs=0)
        return []

    grid = frames.grid
    dbz = np.asarray(frames.dbz, dtype=np.float64)
    usable = np.asarray(qc.coverage, dtype=bool) & ~np.asarray(qc.clutter, dtype=bool)
    shadow = np.asarray(qc.attenuation, dtype=bool)
    tolerance = max(frames.interval / 2, timedelta(seconds=1))
    window_start = frames.latest_ts - timedelta(minutes=window_min)
    half_window = timedelta(minutes=GAUGE_ACCUMULATION_MIN / 2.0)
    per_hour = 60.0 / GAUGE_ACCUMULATION_MIN
    offset = dbz_class_width / 2.0

    times = [_as_datetime(value) for value in gauges["ts"]]
    if times and times[0].tzinfo is None:
        msg = (
            "Gauge timestamps have no time zone. Every VARUNA timestamp carries an offset "
            "(+05:30); parse gauges.csv with a timezone-aware parser before pairing."
        )
        raise ValueError(msg)
    stations = [str(value) for value in gauges["station_id"]]
    depths = np.asarray(gauges["mm_5min"], dtype=np.float64)
    lons = np.asarray(gauges["lon"], dtype=np.float64)
    lats = np.asarray(gauges["lat"], dtype=np.float64)
    xs, ys = _to_metric(grid.crs).transform(lons, lats)
    xs = np.atleast_1d(np.asarray(xs, dtype=np.float64))
    ys = np.atleast_1d(np.asarray(ys, dtype=np.float64))

    order = sorted(range(len(times)), key=lambda i: (times[i], stations[i]))
    rejected = dict.fromkeys(("window", "no_frame", "off_grid", "masked", "no_echo", "dry"), 0)
    shadowed = 0
    pairs: list[GaugePair] = []
    for i in order:
        ts = times[i]
        if ts < window_start or ts > frames.latest_ts:
            rejected["window"] += 1
            continue
        frame = _nearest_frame(frames.times, ts - half_window, tolerance)
        if frame < 0:
            rejected["no_frame"] += 1
            continue
        row, col = grid.rowcol(float(xs[i]), float(ys[i]))
        if not grid.contains(row, col):
            rejected["off_grid"] += 1
            continue
        if not bool(usable[row, col]):
            rejected["masked"] += 1
            continue
        sample = float(dbz[frame, row, col])
        if not math.isfinite(sample):
            rejected["no_echo"] += 1
            continue
        rate = float(depths[i]) * per_hour
        if not math.isfinite(rate) or rate < MIN_PAIR_MM_H:
            rejected["dry"] += 1
            continue
        shadowed += int(bool(shadow[row, col]))
        pairs.append(
            GaugePair(
                ts=ts,
                station_id=stations[i],
                row=row,
                col=col,
                gauge_mm_h=rate,
                dbz=sample + offset,
            )
        )

    log.info(
        "sky.zr.pairs",
        n_rows=len(times),
        n_pairs=len(pairs),
        n_shadowed=shadowed,
        window_min=window_min,
        dbz_class_width=dbz_class_width,
        **{f"rejected_{reason}": count for reason, count in rejected.items()},
    )
    return pairs


# ============================================================================ the fit
def marshall_palmer(n_pairs: int = 0, reason: str | None = None) -> ZRParams:
    """The fallback relation, labelled as such (SPEC.md 11.1 step 2).

    ``n_pairs`` records how many pairs the cycle *did* have, so the console can tell a cycle
    with no gauges at all from one that was two readings short. ``reason`` records *why* the
    fit was abandoned, because the pair count does not imply it: a cycle can fall back holding
    twenty pairs when they all read within a factor of two. The console prints this, so a
    caption cannot infer a reason that was not the real one (rule 6).
    """
    return ZRParams(
        a=MP_A,
        b=MP_B,
        source="marshall_palmer",
        n_pairs=n_pairs,
        clamped=False,
        reason=reason,
    )


def _r_squared(x: NDArray[np.floating], y: NDArray[np.floating], a: float, b: float) -> float:
    """How much of the spread in ``log Z`` the returned relation explains.

    Computed from the relation actually returned, not from the unconstrained least-squares line,
    so a fit whose clamp bit reports the lower agreement the clamp costs. It can go negative -
    a clamped relation can fit worse than the mean of the pairs - and that is the point: it says
    the clamp is doing work.
    """
    predicted = math.log10(a) + b * x
    residual = float(np.sum((y - predicted) ** 2))
    total = float(np.sum((y - y.mean()) ** 2))
    if total <= 0.0:
        return 0.0
    return 1.0 - residual / total


def fit_zr(pairs: list[GaugePair]) -> ZRParams:
    """Least squares for ``(a, b)`` in ``Z = a R^b`` over co-located pairs.

    ``log10 Z`` is the response and ``log10 R_gauge`` the predictor, so the fitted line is
    ``log10 Z = log10 a + b log10 R``. ``log10 Z`` is ``dBZ / 10`` exactly, which is why no
    reflectivity is ever exponentiated here.

    Clamping is done in the order that keeps the result the best relation available inside the
    box: ``b`` is clamped first, then the intercept is re-solved at the clamped exponent (the
    constrained optimum, ``mean(y) - b mean(x)``), then ``a`` is clamped. A relation that comes
    back clamped is still a fit; one that comes back ``marshall_palmer`` is not.

    Falls back to :func:`marshall_palmer` when there are fewer than :data:`MIN_ZR_PAIRS` pairs
    (SPEC.md 11.1 step 2), when the pairs span less than :data:`MIN_LOG10_SPAN` decades of rain
    rate, or when the arithmetic does not come back finite. Every fallback is logged with its
    reason.
    """
    if len(pairs) < MIN_ZR_PAIRS:
        reason = (
            f"only {len(pairs)} usable gauge-radar pairs, fewer than the {MIN_ZR_PAIRS} a fit needs"
        )
        log.info("sky.zr.fallback", reason="too few pairs", n_pairs=len(pairs))
        return marshall_palmer(len(pairs), reason)

    x = np.log10(np.array([pair.gauge_mm_h for pair in pairs], dtype=np.float64))
    y = np.array([pair.dbz for pair in pairs], dtype=np.float64) / 10.0
    span = float(x.max() - x.min())
    if span < MIN_LOG10_SPAN:
        log.info(
            "sky.zr.fallback",
            reason="gauge rates span less than a factor of two",
            n_pairs=len(pairs),
            log10_span=round(span, 4),
        )
        return marshall_palmer(
            len(pairs),
            f"{len(pairs)} gauges span only a factor of "
            f"{10.0**span:.1f} in rain rate, too little to fit an exponent",
        )

    x_mean, y_mean = float(x.mean()), float(y.mean())
    variance = float(np.sum((x - x_mean) ** 2))
    slope = float(np.sum((x - x_mean) * (y - y_mean)) / variance)
    b = min(max(slope, B_RANGE[0]), B_RANGE[1])
    intercept = y_mean - b * x_mean  # re-solved at the clamped exponent
    a = min(max(10.0**intercept, A_RANGE[0]), A_RANGE[1])
    if not (math.isfinite(a) and math.isfinite(b)):
        log.warning("sky.zr.fallback", reason="fit did not converge", n_pairs=len(pairs))
        return marshall_palmer(
            len(pairs), f"the least-squares fit over {len(pairs)} pairs did not come back finite"
        )

    clamped = b != slope or a != 10.0**intercept
    params = ZRParams(
        a=a,
        b=b,
        source="adaptive",
        n_pairs=len(pairs),
        clamped=clamped,
        r2=_r_squared(x, y, a, b),
    )
    log.info(
        "sky.zr.fit",
        a=round(params.a, 2),
        b=round(params.b, 4),
        raw_a=round(10.0**intercept, 2),
        raw_b=round(slope, 4),
        n_pairs=params.n_pairs,
        clamped=params.clamped,
        r2=round(params.r2 or 0.0, 4),
        log10_span=round(span, 4),
    )
    return params


# ============================================================================ entry point
def run_zr(
    frames: RadarFrames,
    gauges: pd.DataFrame,
    qc: QCResult,
    *,
    window_min: float = PAIR_WINDOW_MIN,
    dbz_class_width: float = 0.0,
) -> tuple[ZRParams, list[GaugePair]]:
    """Pair, then fit: this cycle's Z-R relation (SPEC.md 11.1 step 2).

    The pairs come back with the relation because step 3, the gauge merge, needs exactly the
    same co-located set to compute its mean-field bias ``sum(G) / sum(R)``; pairing twice would
    be both wasteful and a chance for the two stages to disagree about which gauges counted.
    """
    pairs = gauge_pairs(frames, gauges, qc, window_min=window_min, dbz_class_width=dbz_class_width)
    return fit_zr(pairs), pairs


__all__ = [
    "A_RANGE",
    "B_RANGE",
    "DBZ_CLASS_WIDTH",
    "GAUGE_ACCUMULATION_MIN",
    "MIN_LOG10_SPAN",
    "MIN_PAIR_MM_H",
    "MIN_ZR_PAIRS",
    "MP_A",
    "MP_B",
    "PAIR_WINDOW_MIN",
    "REQUIRED_COLUMNS",
    "fit_zr",
    "gauge_pairs",
    "marshall_palmer",
    "run_zr",
]

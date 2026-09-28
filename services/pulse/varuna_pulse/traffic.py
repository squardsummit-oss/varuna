"""Reading flooded streets out of traffic that has stopped moving (SPEC.md 11.6, task P7.1).

This is the observation that makes VARUNA self-correcting. Nobody instruments a drain, but every
city already measures its own roads continuously, and a street under 20 cm of water stops
carrying traffic. The inference is indirect and the whole job here is to keep it honest: a jam is
evidence of flooding only when the jam has no other explanation.

**Three filters, in order.**

1. **Depth of the anomaly.** ``z = (v - mu_wd,hr) / sigma`` against the segment's own weekday-hour
   baseline, and the anomaly has to reach ``z < -2.5``. A segment's normal speed is its own; a
   trunk road at 12 km/h is congested and a lane in Dharavi at 12 km/h is a Tuesday. The feed
   carries the mean but not the spread, so sigma is estimated from the segment's own scatter
   over the hour *before* the window being scored (:data:`SIGMA_WINDOW`); that substitution is
   recorded in ``docs/SIMPLIFICATIONS.md``.
2. **Persistence.** Two consecutive snapshots. One slow reading is a bus stopping.
3. **Spatial confounding.** Network-wide congestion is not flooding. An anomaly is rejected when
   its dry neighbours within 500 m are slow too - if the whole area has stopped, the cause is the
   area, not this street.

Only during rain, because the prior below is about water.

**The depth prior** is SPEC.md 11.6's, and it is deliberately vague: `v < 5 km/h` implies at
least 20 cm with a standard deviation of 8, `5-15 km/h` implies 10-20 cm. A stopped car says the
street is impassable, not how deep it is, and the EnKF should receive that uncertainty rather
than a made-up number.

**Vectorised, and held to the loop it replaced.** The detector used to walk every segment's
group twice and sort each one, which on the 2 July feed (444,136 rows, 9,064 segments) was most
of Pulse's stage time. It now parses the feed's timestamps once per distinct string, factorises
the segment ids once, and scores a snapshot with array operations over integer codes. The rules
above did not change, and neither did the arithmetic: sigma is still pandas' grouped standard
deviation over the same rows in the same order, and z is the same element-wise expression, so the
observations are identical to the loop's - which ``services/pulse/tests/test_traffic.py`` checks
against a copy of it.

**One snapshot or a window.** ``detect_anomalies(speeds, at=t)`` scores the latest snapshot at or
before ``t``, as the cycle has always called it. ``detect_anomalies(speeds, start=s, end=t)``
scores every snapshot in ``(s, t]``, which is what a cycle needs once Pulse carries its posterior
forward and must assimilate only what arrived since the previous cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING

import numpy as np
import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datetime import datetime

    import pandas as pd
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.pulse.traffic")

__all__ = [
    "ANOMALY_Z",
    "CONFOUNDER_RADIUS_M",
    "MIN_CONSECUTIVE",
    "NEIGHBOUR_Z",
    "SIGMA_WINDOW",
    "TrafficObservation",
    "depth_prior_cm",
    "detect_anomalies",
]

ANOMALY_Z = -2.5
"""How far below its own baseline a segment must fall to count (SPEC.md 11.6).

2.5 standard deviations is about one snapshot in 160 by chance on a normal distribution. Mumbai
traffic is not normal and the synthetic feed carries 3 % deliberate confounders, which is what
the persistence and neighbour tests below are for."""

MIN_CONSECUTIVE = 2
"""Snapshots the anomaly must persist for. One is a bus at a stop; two is a street."""

SIGMA_WINDOW = 12
"""Snapshots the baseline's spread is estimated over, ending before the persistence window.

11.6 scores against the segment's weekday-hour sigma; the feed carries `baseline_kmh` and no
spread, so sigma has to be estimated. Estimating it over the two snapshots being scored makes
the denominator ``|dv|/sqrt(2)`` - a difference of two readings, not an estimate - and on the
2 July feed 42.4 % of rows then fell through to the floor below, because a segment that is
equally slow twice has no scatter at all. Twelve snapshots is an hour of 5-minute probes.

They end *before* the scored window because a sigma that contains the anomaly is inflated by
it: on the same feed, including the two scored snapshots takes the 03:40Z cycle from 22
anomalies to 3 and the 02:40Z cycle to none. Excluding them, the floor is taken by 1.8 % of
rows at 03:40Z and the counts hold (22 -> 24)."""

CONFOUNDER_RADIUS_M = 500.0
"""How far to look for the "is the whole area slow?" test (SPEC.md 11.6)."""

NEIGHBOUR_Z = -1.0
"""A neighbour above this is behaving normally, so the anomaly is local and survives.

If *every* neighbour within 500 m is below it, the slowdown is regional - a procession, a
signal failure, the tail of a jam somewhere else - and attributing it to water on this segment
would teach the drain model something false."""


@dataclass(frozen=True, slots=True)
class TrafficObservation:
    """One segment inferred to be flooded, with the depth that inference supports."""

    segment_id: str
    ts: datetime
    speed_kmh: float
    baseline_kmh: float
    z: float
    depth_cm: float
    depth_sd_cm: float
    n_consecutive: int
    kind: str = "traffic"

    @property
    def weight(self) -> float:
        """Inverse variance, which is what the EnKF wants."""
        return 1.0 / max(self.depth_sd_cm**2, 1e-6)


def depth_prior_cm(speed_kmh: float) -> tuple[float, float]:
    """Depth implied by a speed, as (mean, sd) in cm (SPEC.md 11.6).

    Returns ``(0, 0)`` above 15 km/h: a street moving at 20 km/h is not evidence of any depth,
    and returning a small positive number would let normal traffic quietly pull the posterior.
    """
    if speed_kmh < 5.0:
        return 20.0, 8.0
    if speed_kmh < 15.0:
        return 15.0, 6.0
    return 0.0, 0.0


def detect_anomalies(
    speeds: pd.DataFrame,
    *,
    at: datetime | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    raining: bool = True,
    neighbours: dict[str, list[str]] | None = None,
    incident_segments: set[str] | None = None,
) -> list[TrafficObservation]:
    """Segments whose slowdown is best explained by water, at one snapshot or over a window.

    Args:
        speeds: the feed, with ``ts``, ``segment_id``, ``kmh`` and ``baseline_kmh``.
        at: score the latest snapshot at or before this instant. Earlier snapshots are read for
            the persistence test and for sigma. Each observation's ``ts`` is ``at`` itself.
        start, end: score **every** snapshot in ``(start, end]`` instead (``start=None`` means
            from the feed's first snapshot). Each snapshot is scored exactly as ``at=`` would
            score it, and each observation's ``ts`` is its snapshot, in ``end``'s timezone - so a
            street that stays flooded for six snapshots comes back six times, and collapsing
            those into one observation per window is the caller's decision, not this function's.
            Observations are ordered by snapshot, then by segment id.
        raining: when false, nothing is returned - the depth prior is about rain.
        neighbours: segment id to the ids within :data:`CONFOUNDER_RADIUS_M`, for the spatial
            test. Without it that test is skipped and the result says so in the log.
        incident_segments: segments the feed has tagged with a non-weather incident; rejected
            outright (SPEC.md 11.6's confounder handling).

    Exactly one of ``at`` and ``end`` must be given.
    """
    if (at is None) == (end is None):
        msg = "detect_anomalies takes either at= (one snapshot) or end= (a window), not both."
        raise ValueError(msg)
    if at is not None and start is not None:
        msg = "detect_anomalies: start= opens a window and only applies with end=, not with at=."
        raise ValueError(msg)
    if at is None and start is not None and end is not None and start >= end:
        return []
    if not raining or speeds.empty:
        return []

    feed = _Feed.from_frame(speeds)
    if feed.stamps.size == 0:
        return []

    if at is not None:
        # `_as_ts` drops the offset after normalising to UTC, so the cutoff has to be made naive
        # the same way - comparing a tz-aware instant against a naive numpy datetime warns and
        # then gives the wrong answer by the offset, which on IST is five and a half hours.
        target = int(np.searchsorted(feed.stamps, _naive_utc(at), side="right")) - 1
        if target < 0:
            return []
        return _score_snapshot(feed, target, at, neighbours, incident_segments, str(at))

    assert end is not None  # for the type checker; guarded above
    last = int(np.searchsorted(feed.stamps, _naive_utc(end), side="right"))
    first = 0 if start is None else int(np.searchsorted(feed.stamps, _naive_utc(start), "right"))
    observations: list[TrafficObservation] = []
    for target in range(first, last):
        stamp = _aware(feed.stamps[target], end)
        observations.extend(
            _score_snapshot(feed, target, stamp, neighbours, incident_segments, str(stamp))
        )
    return observations


@dataclass(frozen=True, slots=True)
class _Feed:
    """The feed reduced to integer codes, so a snapshot is scored without touching strings.

    ``stamp_code`` indexes ``stamps`` (sorted, UTC-naive); ``segment_code`` indexes ``segments``
    (sorted, which is the order ``groupby("segment_id")`` iterates in). Rows keep the frame's own
    order, because pandas' grouped standard deviation accumulates in row order and the result has
    to match it to the last bit.
    """

    stamps: NDArray[np.datetime64]
    segments: NDArray[np.object_]
    stamp_code: NDArray[np.int64]
    segment_code: NDArray[np.int64]
    kmh: NDArray[np.float64]
    baseline: NDArray[np.float64]

    @classmethod
    def from_frame(cls, speeds: pd.DataFrame) -> _Feed:
        import pandas as pd

        parsed = _as_ts_by_value(speeds["ts"])
        valid = ~np.isnat(parsed)
        stamps = np.unique(parsed[valid])
        stamp_code = np.full(parsed.shape, -1, dtype=np.int64)
        stamp_code[valid] = np.searchsorted(stamps, parsed[valid])

        codes, uniques = pd.factorize(speeds["segment_id"], sort=True)
        return cls(
            stamps=stamps,
            segments=np.asarray(uniques, dtype=object),
            stamp_code=stamp_code,
            segment_code=np.asarray(codes, dtype=np.int64),
            kmh=speeds["kmh"].to_numpy(dtype=np.float64),
            baseline=speeds["baseline_kmh"].to_numpy(dtype=np.float64),
        )


def _score_snapshot(
    feed: _Feed,
    target: int,
    stamp: datetime,
    neighbours: dict[str, list[str]] | None,
    incident_segments: set[str] | None,
    label: str,
) -> list[TrafficObservation]:
    """Score snapshot ``stamps[target]`` exactly as the per-segment loop did."""
    import pandas as pd

    n_prior = target + 1
    if n_prior < MIN_CONSECUTIVE:
        return []
    first_scored = n_prior - MIN_CONSECUTIVE
    first_sigma = max(first_scored - SIGMA_WINDOW, 0)
    sigma_snapshots = first_scored - first_sigma
    grouped = feed.segment_code >= 0

    # ---- sigma over the hour before the scored window ------------------------------------
    n_segments = feed.segments.size
    sigma_by_code = np.full(n_segments, np.nan)
    in_sigma = grouped & (feed.stamp_code >= first_sigma) & (feed.stamp_code < first_scored)
    if sigma_snapshots > 0 and in_sigma.any():
        # pandas' own grouped std, over the same rows in the same order as before - only the key
        # is an integer code now rather than the string it stands for.
        std = pd.Series(feed.kmh[in_sigma]).groupby(feed.segment_code[in_sigma]).std()
        sigma_by_code[std.index.to_numpy(dtype=np.int64)] = std.to_numpy(dtype=np.float64)

    # ---- z over the scored window ----------------------------------------------------------
    rows = np.flatnonzero(grouped & (feed.stamp_code >= first_scored) & (feed.stamp_code <= target))
    code = feed.segment_code[rows]
    baseline = feed.baseline[rows]
    deviation = feed.kmh[rows] - baseline
    sigma = sigma_by_code[code]
    estimated = np.isfinite(sigma) & (sigma > 1.0)
    sigma = np.where(estimated, sigma, np.maximum(baseline * 0.2, 3.0))
    z = deviation / sigma
    floored = int((~estimated).sum())

    # ---- per segment -----------------------------------------------------------------------
    scored = np.bincount(code, minlength=n_segments)
    below = np.bincount(code[z < ANOMALY_Z], minlength=n_segments)
    present = np.flatnonzero(scored)
    # Grouped min skips NaN and is a selection, so it is the loop's `group["z"].min()` exactly.
    z_min = pd.Series(z).groupby(code).min()
    slow = dict(zip((str(s) for s in feed.segments[present]), z_min.to_numpy(), strict=True))

    # The latest row of each segment: sorted by segment, then snapshot, stable on row order, so a
    # tie on the snapshot always keeps the later row. The old `sort_values("ts").iloc[-1]` used
    # quicksort and kept it only while a group was small enough for numpy's insertion sort.
    order = np.lexsort((feed.stamp_code[rows], code))
    block = code[order]
    ends = np.flatnonzero(np.append(block[1:] != block[:-1], True))
    last_of = np.full(n_segments, -1, dtype=np.int64)
    last_of[block[ends]] = order[ends]

    incidents = incident_segments or set()
    observations: list[TrafficObservation] = []
    rejected_incident = rejected_regional = 0
    for segment in np.flatnonzero((scored > 0) & (below == scored)):
        key = str(feed.segments[segment])
        if key in incidents:
            rejected_incident += 1
            continue

        if neighbours is not None:
            nearby = [slow[n] for n in neighbours.get(key, []) if n in slow]
            # Every neighbour also slow -> the area is slow, not this street.
            if nearby and max(nearby) < NEIGHBOUR_Z:
                rejected_regional += 1
                continue

        latest = int(last_of[segment])
        speed = float(feed.kmh[rows[latest]])
        depth, sd = depth_prior_cm(speed)
        if depth <= 0.0:
            continue
        observations.append(
            TrafficObservation(
                segment_id=key,
                ts=stamp,
                speed_kmh=speed,
                baseline_kmh=float(baseline[latest]),
                z=float(z[latest]),
                depth_cm=depth,
                depth_sd_cm=sd,
                n_consecutive=int(scored[segment]),
            )
        )

    log.info(
        "pulse.traffic_anomalies",
        at=label,
        n=len(observations),
        segments_scored=int(present.size),
        rejected_incident=rejected_incident,
        rejected_regional=rejected_regional,
        spatial_test=neighbours is not None,
        sigma_snapshots=sigma_snapshots,
        sigma_floored_pct=round(100.0 * floored / max(rows.size, 1), 1),
    )
    return observations


def _naive_utc(instant: datetime) -> np.datetime64:
    """An instant as the UTC-naive numpy datetime the feed's stamps are compared in."""
    return np.datetime64(instant.astimezone(UTC).replace(tzinfo=None))


def _aware(stamp: np.datetime64, like: datetime) -> datetime:
    """A feed stamp back as an aware datetime, in the timezone of ``like``."""
    import pandas as pd

    return pd.Timestamp(stamp).tz_localize(UTC).to_pydatetime().astimezone(like.tzinfo)


def _as_ts(column: pd.Series) -> pd.Series:
    import pandas as pd

    return pd.to_datetime(column, utc=True, format="mixed").dt.tz_localize(None)


def _as_ts_by_value(column: pd.Series) -> NDArray[np.datetime64]:
    """:func:`_as_ts` evaluated once per distinct value, as UTC-naive ``datetime64[ns]``.

    ``format="mixed"`` infers each element's format on its own, so parsing the distinct strings
    and scattering them back gives the same instants as parsing every row - and the feed repeats
    the same 49 strings 444,136 times.
    """
    import pandas as pd

    codes, uniques = pd.factorize(column, use_na_sentinel=True)
    parsed = _as_ts(pd.Series(uniques)).to_numpy(dtype="datetime64[ns]")
    out = np.full(codes.shape, np.datetime64("NaT"), dtype="datetime64[ns]")
    known = codes >= 0
    out[known] = parsed[codes[known]]
    return out

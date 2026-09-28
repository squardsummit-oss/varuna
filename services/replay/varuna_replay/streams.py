"""The streams a reconstructed replay carries: gauges, tide, traffic, reports, ground truth.

SPEC.md 10.2 fixes what each stream is and this module produces exactly that, for tasks
P2.4 (gauges and tide), P2.5 (traffic) and P2.6 (ground truth and reports). Every generator
is pure, takes a ``seed``, and returns plain rows; :mod:`varuna_replay.bundle` writes them and
:mod:`varuna_replay.build` decides what goes into which bundle.

**What is real in here and what is not.** The *coordinates* are real and sourced - gauge sites
from ``docs/research/bmc_aws_stations.json``, hotspots and road segments from the city build,
ground-truth pins from ``docs/research/ground_truth_MUM-2019-07-02.draft.geojson`` with a
``source_url`` each. The *values* are not: gauge readings are sampled from the reconstructed
rain field, the tide is one harmonic anchored to a single civic statement, the traffic feed is
a designed baseline with designed anomalies, and the citizen reports are placed by a stated
rule. Everything generated is flagged ``synthetic`` in its own row, and the reasoning is in
:mod:`varuna_replay.evidence` and in the manifest.

Determinism (rule 8): every draw comes from ``numpy.random.default_rng`` seeded from the
bundle seed and a per-stream integer, so a stream can be regenerated without disturbing any
other, and two builds with the same seed produce byte-identical files.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog
from pyproj import Transformer

from varuna_replay.domain import WGS84, StormDomain

if TYPE_CHECKING:  # pandas is imported lazily: only the traffic feed needs it
    import pandas as pd

log = structlog.get_logger("varuna.replay.streams")

# --------------------------------------------------------------------- seed streams
SEED_GAUGES = 11
SEED_TRAFFIC_BASELINE = 21
SEED_TRAFFIC_ANOMALY = 22
SEED_TRAFFIC_CONFOUNDER = 23
SEED_REPORTS = 31
"""Per-stream seed offsets. ``default_rng([seed, offset])`` gives each stream an independent
sequence, so adding a gauge never moves a citizen report."""


@lru_cache(maxsize=8)
def _to_metric(crs: int) -> Transformer:
    return Transformer.from_crs(WGS84, f"EPSG:{crs}", always_xy=True)


def project(
    lons: Sequence[float], lats: Sequence[float], crs: int
) -> tuple[np.ndarray, np.ndarray]:
    """WGS84 lon/lat to the metric CRS of the storm domain."""
    x, y = _to_metric(crs).transform(np.asarray(lons, dtype=float), np.asarray(lats, dtype=float))
    return (np.atleast_1d(np.asarray(x, dtype=float)), np.atleast_1d(np.asarray(y, dtype=float)))


def pixel_index(domain: StormDomain, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Row and column of the domain pixels containing metric points ``(x, y)``, clamped."""
    col = np.clip(((x - domain.left) / domain.res_m).astype(int), 0, domain.n_px - 1)
    row = np.clip(((domain.top - y) / domain.res_m).astype(int), 0, domain.n_px - 1)
    return (row, col)


def _stamps(t0: datetime, window_min: float, cadence_min: float) -> list[datetime]:
    """Every reporting instant from ``t0`` to ``t0 + window_min`` inclusive."""
    n = round(window_min / cadence_min)
    if abs(n * cadence_min - window_min) > 1e-9:
        msg = f"window {window_min} min is not a whole number of {cadence_min}-minute steps"
        raise ValueError(msg)
    return [t0 + timedelta(minutes=cadence_min * step) for step in range(n + 1)]


# ============================================================================ gauges
GAUGE_CADENCE_MIN = 15
"""Gauges report every 15 minutes (SPEC.md 10.2)."""

GAUGE_ACCUMULATION_MIN = 5.0
"""``mm_5min`` is the depth accumulated in the five minutes ending at ``ts``: the column
SPEC.md 10.2 names, reported at the 15-minute cadence the same section states."""

GAUGE_NOISE_FRAC = 0.10
"""10 % multiplicative noise on every reading (SPEC.md 10.2)."""


@dataclass(frozen=True, slots=True)
class GaugeSite:
    """One rain-gauge site: a real, sourced coordinate that a synthetic reading is taken at."""

    id: str
    name: str
    lon: float
    lat: float
    operator: str
    source_url: str
    in_aoi: bool


def load_gauge_sites(path: Path) -> list[GaugeSite]:
    """Read ``docs/research/bmc_aws_stations.json``, keeping only sites with a coordinate.

    Sites with ``lon``/``lat`` null are the ones whose coordinates the municipal portal keeps
    behind a POST-only endpoint; they carry a real name and no position, so they cannot host a
    reading and are dropped here rather than placed by guesswork (rule 7).
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    sites: list[GaugeSite] = []
    for entry in payload.get("stations", []):
        lon, lat = entry.get("lon"), entry.get("lat")
        if lon is None or lat is None:
            continue
        sites.append(
            GaugeSite(
                id=str(entry["id"]),
                name=str(entry["name"]),
                lon=float(lon),
                lat=float(lat),
                operator=str(entry.get("operator", "")),
                source_url=str(entry.get("source_url", "")),
                in_aoi=bool(entry.get("in_aoi", False)),
            )
        )
    sites.sort(key=lambda site: site.id)
    log.info("streams.gauge_sites", path=str(path), sites=len(sites))
    return sites


def gauge_rows(
    sites: Sequence[GaugeSite],
    truth_mm_h: np.ndarray,
    times_min: np.ndarray,
    domain: StormDomain,
    t0: datetime,
    *,
    seed: int,
    cadence_min: int = GAUGE_CADENCE_MIN,
    noise_frac: float = GAUGE_NOISE_FRAC,
) -> list[dict[str, Any]]:
    """Synthetic gauge readings sampled from the truth field at real station coordinates.

    Args:
        sites: stations with a published coordinate (see :func:`load_gauge_sites`).
        truth_mm_h: the truth rain cube ``(t, y, x)`` in mm/h.
        times_min: the cube's instants, minutes from ``t0``, evenly spaced.
        seed: the bundle seed; the noise stream is ``default_rng([seed, 11])``.

    Returns:
        One row per site per reporting instant, sorted by time then station id. ``mm_5min`` is
        the depth in the five minutes ending at ``ts``, trapezoidal between the two cube
        instants that bracket it. The first row has no preceding five minutes inside the
        bundle, so it holds the rate at ``t0`` for five minutes; that is stated rather than
        hidden, and it is one row per station.
    """
    times = np.asarray(times_min, dtype=float)
    step = float(times[1] - times[0]) if times.size > 1 else GAUGE_ACCUMULATION_MIN
    if abs(step - GAUGE_ACCUMULATION_MIN) > 1e-9:
        msg = (
            f"the truth cube steps every {step} min; mm_5min needs the 5-minute cube "
            "SPEC.md 10.2 specifies"
        )
        raise ValueError(msg)
    x, y = project([site.lon for site in sites], [site.lat for site in sites], domain.crs)
    row, col = pixel_index(domain, x, y)
    series = np.asarray(truth_mm_h, dtype=float)[:, row, col]  # (t, site)

    window_min = float(times[-1] - times[0])
    stamps = _stamps(t0, window_min, cadence_min)
    rng = np.random.default_rng([seed, SEED_GAUGES])
    rows: list[dict[str, Any]] = []
    for stamp in stamps:
        minutes = (stamp - t0).total_seconds() / 60.0
        index = round(minutes / step)
        previous = series[max(index - 1, 0)]
        depth = (previous + series[index]) / 2.0 * (GAUGE_ACCUMULATION_MIN / 60.0)
        noise = 1.0 + noise_frac * rng.standard_normal(len(sites))
        readings = np.clip(depth * noise, 0.0, None)
        for site, value in zip(sites, readings, strict=True):
            rows.append(
                {
                    "ts": stamp,
                    "station_id": site.id,
                    "name": site.name,
                    "lat": round(site.lat, 7),
                    "lon": round(site.lon, 7),
                    "mm_5min": round(float(value), 3),
                    "synthetic": True,
                }
            )
    log.info("streams.gauges", sites=len(sites), rows=len(rows), cadence_min=cadence_min)
    return rows


# ============================================================================ tide
TIDE_CADENCE_MIN = 15
"""The tide stage is published every 15 minutes, as the gauges are."""

TIDE_LOW_WATER_M = 0.0
"""The trough of the modelled curve. Chart datum is by convention about the lowest
astronomical tide, so a curve whose trough sits at 0.00 m is the simplest one consistent with
the single sourced height. It is an assumption; ``evidence.TIDE_BASIS`` says so."""


def tide_rows(
    t0: datetime,
    window_min: float,
    *,
    high_water_m: float,
    high_water_at: datetime,
    period_min: float,
    cadence_min: int = TIDE_CADENCE_MIN,
    low_water_m: float = TIDE_LOW_WATER_M,
    source: str = "illustrative",
) -> list[dict[str, Any]]:
    """One semi-diurnal harmonic through the documented high water.

    ``stage(t) = Z0 + A cos(2 pi (t - t_high) / T)`` with ``Z0`` and ``A`` fixed by the crest
    (``high_water_m``, the one sourced height) and the trough (``low_water_m``, an assumption).
    Nothing here is measured: ``source`` is ``illustrative`` on every row, which is what
    SPEC.md 3.2 requires when no tide table could be cited.

    The demo needs the stage rising through the window so the tide-locked outfall surcharges;
    with high water at 11:30 IST and the window opening at 05:40 it does, and that rise is a
    consequence of the anchor, not an observation of the morning.

    ``window_min`` is the length of the series, not of the replay window: a reconstruction asks
    for the window plus the forecast horizon (``bundle.TIDE_LOOKAHEAD_MIN``) so the last cycle's
    Twin has a stage to read for all three hours. Each row is a function of its own instant only,
    so a longer series holds the shorter one's rows unchanged and appends to them.
    """
    amplitude = (high_water_m - low_water_m) / 2.0
    mean_level = (high_water_m + low_water_m) / 2.0
    rows: list[dict[str, Any]] = []
    for stamp in _stamps(t0, window_min, cadence_min):
        offset_min = (stamp - high_water_at).total_seconds() / 60.0
        stage = mean_level + amplitude * math.cos(2.0 * math.pi * offset_min / period_min)
        rows.append({"ts": stamp, "stage_m": round(stage, 3), "source": source})
    log.info(
        "streams.tide",
        rows=len(rows),
        first_m=rows[0]["stage_m"],
        last_m=rows[-1]["stage_m"],
        source=source,
    )
    return rows


# ============================================================================ traffic
TRAFFIC_CADENCE_MIN = 5
"""Speed snapshots every 5 minutes, the cycle period."""

TRAFFIC_CLASSES: tuple[str, ...] = ("motorway", "trunk", "primary", "secondary", "tertiary")
"""Classes the synthetic feed covers. A probe-speed provider sees classified roads, not every
residential lane or service road, so covering them would overstate what a real feed gives."""

WEEKDAY_HOUR_FACTOR: dict[int, float] = {
    0: 1.00, 1: 1.00, 2: 1.00, 3: 1.00, 4: 0.98, 5: 0.95,
    6: 0.90, 7: 0.78, 8: 0.68, 9: 0.66, 10: 0.72, 11: 0.78,
    12: 0.80, 13: 0.80, 14: 0.80, 15: 0.78, 16: 0.74, 17: 0.66,
    18: 0.60, 19: 0.60, 20: 0.68, 21: 0.78, 22: 0.88, 23: 0.95,
}  # fmt: skip
"""Fraction of free-flow speed by hour on a weekday. 2 July 2019 was a Tuesday, and the
replay window sits in the morning peak. These are designer numbers, not measurements: no
probe feed for the day was obtainable, and the manifest says so."""

SEGMENT_OFFSET_RANGE = (0.92, 1.08)
"""Per-segment multiplier on the baseline, drawn once, so the network is not uniform."""

SPEED_NOISE_FRAC = 0.08
"""Snapshot-to-snapshot noise on an unaffected segment."""

MIN_KMH = 1.0
"""A speed the feed never reports below: a stopped probe still reports something."""

ANOMALY_HALF_WINDOW_MIN = 10.0
"""Anomalies run for ten minutes either side of the pin's time (SPEC.md 10.2)."""

FLOODED_KMH = (2.0, 4.5)
"""The flooded segment collapses below 5 km/h, which SPEC.md 11.6 reads as depth >= 20 cm."""

SPREAD_KMH = (5.0, 9.0)
"""First-hop neighbours: slowed into the 5-15 km/h band, which reads as 10-20 cm."""

SPREAD_2_FACTOR = 0.70
"""Second-hop neighbours: queueing, not flooding."""

ANOMALY_PLATEAU = 0.6
"""Fraction of the half-window over which the collapse holds flat before easing back to the
baseline. Without a plateau a 5-minute snapshot grid could miss the bottom entirely, and
VARUNA-Pulse needs the segment below 5 km/h for at least two consecutive snapshots before it
will call an anomaly an observation (SPEC.md 11.6)."""

SPREAD_HOPS = 2
"""How far an anomaly spreads along the graph."""

CONFOUNDER_FRACTION = 0.03
"""3 % of covered segments carry an unrelated slowdown (SPEC.md 10.2), so Pulse's rejection
logic has something to reject. They are not labelled in the feed - that is the point."""

CONFOUNDER_KMH = (3.0, 8.0)
CONFOUNDER_DURATION_MIN = (15.0, 30.0)

PIN_SNAP_MAX_M = 300.0
"""A pin further than this from any covered segment gets no anomaly, and is reported."""


@dataclass(frozen=True, slots=True)
class SegmentTable:
    """The road segments a traffic feed covers, with their centroids in the domain CRS."""

    segment_id: np.ndarray
    road_class: np.ndarray
    free_flow_kmh: np.ndarray
    u: np.ndarray
    v: np.ndarray
    x: np.ndarray
    y: np.ndarray

    def __len__(self) -> int:
        return int(self.segment_id.size)

    def index_of(self) -> dict[str, int]:
        return {str(value): index for index, value in enumerate(self.segment_id)}

    def neighbours(self) -> list[list[int]]:
        """Segments sharing an endpoint node, as an adjacency list of row indices."""
        by_node: dict[int, list[int]] = {}
        for index, (a, b) in enumerate(zip(self.u.tolist(), self.v.tolist(), strict=True)):
            by_node.setdefault(int(a), []).append(index)
            by_node.setdefault(int(b), []).append(index)
        adjacency: list[list[int]] = []
        for index, (a, b) in enumerate(zip(self.u.tolist(), self.v.tolist(), strict=True)):
            joined = set(by_node.get(int(a), ())) | set(by_node.get(int(b), ()))
            joined.discard(index)
            adjacency.append(sorted(joined))
        return adjacency


def load_segments(
    path: Path, crs: int, *, classes: Sequence[str] = TRAFFIC_CLASSES
) -> SegmentTable:
    """Read ``city/<city>/segments.parquet`` and keep the classes a probe feed would see."""
    import pandas as pd
    import shapely

    frame = pd.read_parquet(
        path, columns=["segment_id", "class", "u", "v", "speed_kmh", "geometry"]
    )
    frame = (
        frame[frame["class"].isin(list(classes))].sort_values("segment_id").reset_index(drop=True)
    )
    centroids = shapely.centroid(shapely.from_wkb(frame["geometry"].to_numpy()))
    table = SegmentTable(
        segment_id=frame["segment_id"].to_numpy(dtype=object),
        road_class=frame["class"].to_numpy(dtype=object),
        free_flow_kmh=frame["speed_kmh"].to_numpy(dtype=float),
        u=frame["u"].to_numpy(dtype=np.int64),
        v=frame["v"].to_numpy(dtype=np.int64),
        x=shapely.get_x(centroids),
        y=shapely.get_y(centroids),
    )
    log.info("streams.segments", path=str(path), kept=len(table), classes=list(classes))
    return table


@dataclass(frozen=True, slots=True)
class TrafficAnomaly:
    """One slowdown episode written into the feed, and what it is meant to represent."""

    segment_index: int
    centre_min: float
    half_width_min: float
    hop: int
    cause: str

    @property
    def is_confounder(self) -> bool:
        return self.cause == "confounder"


@dataclass(frozen=True, slots=True)
class PinSnap:
    """A ground-truth pin matched to the covered segment nearest it."""

    pin_id: str
    segment_index: int
    distance_m: float
    minutes: float


def snap_pins(
    segments: SegmentTable,
    pins: Sequence[Mapping[str, Any]],
    domain_crs: int,
    t0: datetime,
    window_min: float,
    *,
    max_distance_m: float = PIN_SNAP_MAX_M,
) -> tuple[list[PinSnap], list[str]]:
    """Match each pin inside the replay window to its nearest covered segment.

    Only pins whose timestamp falls inside the window can appear in a stream that covers the
    window, so the rest are returned as ids in the second element rather than silently dropped.
    """
    snaps: list[PinSnap] = []
    skipped: list[str] = []
    for pin in pins:
        stamp = datetime.fromisoformat(str(pin["ts"]))
        minutes = (stamp - t0).total_seconds() / 60.0
        if minutes < 0.0 or minutes > window_min:
            skipped.append(f"{pin['id']} outside the window")
            continue
        x, y = project([float(pin["lon"])], [float(pin["lat"])], domain_crs)
        distances = np.hypot(segments.x - x[0], segments.y - y[0])
        index = int(np.argmin(distances))
        distance = float(distances[index])
        if distance > max_distance_m:
            skipped.append(f"{pin['id']} is {distance:.0f} m from the nearest covered segment")
            continue
        snaps.append(
            PinSnap(
                pin_id=str(pin["id"]),
                segment_index=index,
                distance_m=round(distance, 1),
                minutes=minutes,
            )
        )
    snaps.sort(key=lambda snap: (snap.minutes, snap.pin_id))
    return (snaps, skipped)


def pin_anomalies(
    segments: SegmentTable,
    snaps: Sequence[PinSnap],
    *,
    half_width_min: float = ANOMALY_HALF_WINDOW_MIN,
    hops: int = SPREAD_HOPS,
) -> list[TrafficAnomaly]:
    """A flooded segment per pin, spreading ``hops`` steps along the road graph."""
    adjacency = segments.neighbours()
    anomalies: list[TrafficAnomaly] = []
    for snap in snaps:
        reached = {snap.segment_index: 0}
        frontier = [snap.segment_index]
        for hop in range(1, hops + 1):
            nxt: list[int] = []
            for index in frontier:
                for neighbour in adjacency[index]:
                    if neighbour not in reached:
                        reached[neighbour] = hop
                        nxt.append(neighbour)
            frontier = nxt
        for index, hop in sorted(reached.items()):
            anomalies.append(
                TrafficAnomaly(
                    segment_index=index,
                    centre_min=snap.minutes,
                    half_width_min=half_width_min,
                    hop=hop,
                    cause=snap.pin_id,
                )
            )
    return anomalies


def confounder_anomalies(
    segments: SegmentTable,
    window_min: float,
    *,
    seed: int,
    exclude: Sequence[int],
    fraction: float = CONFOUNDER_FRACTION,
) -> list[TrafficAnomaly]:
    """Unrelated slowdowns on a share of the covered segments, at times no flood explains.

    They are deliberately indistinguishable from a flood in the feed itself: the feed carries
    no incident column, so Pulse must reject them on the evidence around them (no rain, dry
    neighbours) rather than on a label. Regenerate them with this function and the bundle seed
    to score that rejection.
    """
    rng = np.random.default_rng([seed, SEED_TRAFFIC_CONFOUNDER])
    candidates = np.array(sorted(set(range(len(segments))) - {int(i) for i in exclude}))
    count = round(fraction * len(segments))
    chosen = np.sort(rng.choice(candidates, size=min(count, candidates.size), replace=False))
    lo, hi = CONFOUNDER_DURATION_MIN
    anomalies: list[TrafficAnomaly] = []
    for index in chosen.tolist():
        duration = float(rng.uniform(lo, hi))
        half = duration / 2.0
        centre = float(rng.uniform(half, window_min - half))
        anomalies.append(
            TrafficAnomaly(
                segment_index=int(index),
                centre_min=centre,
                half_width_min=half,
                hop=0,
                cause="confounder",
            )
        )
    log.info("streams.confounders", count=len(anomalies), of=len(segments))
    return anomalies


def _anomaly_speed(hop: int, ratio: float, baseline: float, draw: float) -> float:
    """Speed on an affected segment: deepest at the centre of the episode, easing to the edge.

    ``ratio`` is ``|t - centre| / half_width`` in [0, 1]; ``draw`` is one uniform number that
    fixes where in the band this episode sits, so an episode is internally consistent.
    """
    eased = 0.0 if ratio <= ANOMALY_PLATEAU else (ratio - ANOMALY_PLATEAU) / (1.0 - ANOMALY_PLATEAU)
    if hop >= 2:
        return baseline * (SPREAD_2_FACTOR + (1.0 - SPREAD_2_FACTOR) * eased)
    lo, hi = FLOODED_KMH if hop == 0 else SPREAD_KMH
    low = lo + (hi - lo) * draw
    return low + (baseline - low) * eased if baseline > low else baseline


def traffic_frame(
    segments: SegmentTable,
    anomalies: Sequence[TrafficAnomaly],
    t0: datetime,
    window_min: float,
    *,
    seed: int,
    cadence_min: int = TRAFFIC_CADENCE_MIN,
) -> pd.DataFrame:
    """The whole synthetic speed feed: baseline, noise, flood anomalies and confounders.

    ``baseline_kmh`` is the weekday-by-hour baseline the detector compares against; ``kmh`` is
    what the feed reports. Where episodes overlap the slowest wins, because a queue does not
    speed a street up.

    This is the one stream with hundreds of thousands of rows - every covered segment at every
    5-minute snapshot - so it is assembled column-wise and returned as a frame rather than as
    dictionaries; :func:`varuna_replay.bundle.write_traffic` takes either.
    """
    import pandas as pd

    stamps = _stamps(t0, window_min, cadence_min)
    minutes = np.array([(stamp - t0).total_seconds() / 60.0 for stamp in stamps])
    factor = np.array([WEEKDAY_HOUR_FACTOR[stamp.hour] for stamp in stamps])

    rng = np.random.default_rng([seed, SEED_TRAFFIC_BASELINE])
    offsets = rng.uniform(*SEGMENT_OFFSET_RANGE, size=len(segments))
    baseline = np.round(np.outer(factor, segments.free_flow_kmh * offsets), 2)  # (t, segment)
    noise = 1.0 + SPEED_NOISE_FRAC * rng.standard_normal(baseline.shape)
    observed = np.clip(baseline * noise, MIN_KMH, None)

    draws = np.random.default_rng([seed, SEED_TRAFFIC_ANOMALY]).uniform(size=len(anomalies))
    for anomaly, draw in zip(anomalies, draws.tolist(), strict=True):
        distance = np.abs(minutes - anomaly.centre_min)
        inside = distance <= anomaly.half_width_min
        if not inside.any():
            continue
        ratio = np.clip(distance / max(anomaly.half_width_min, 1e-9), 0.0, 1.0)
        column = anomaly.segment_index
        for step in np.flatnonzero(inside).tolist():
            value = _anomaly_speed(
                anomaly.hop, float(ratio[step]), float(baseline[step, column]), draw
            )
            observed[step, column] = min(observed[step, column], max(value, MIN_KMH))

    n_segments = len(segments)
    frame = pd.DataFrame(
        {
            "ts": np.repeat(
                np.array([stamp.isoformat() for stamp in stamps], dtype=object), n_segments
            ),
            "segment_id": np.tile(segments.segment_id.astype(str), len(stamps)),
            "kmh": np.round(observed, 2).reshape(-1),
            "baseline_kmh": baseline.reshape(-1),
            "synthetic": np.ones(len(stamps) * n_segments, dtype=bool),
        }
    )
    log.info(
        "streams.traffic",
        rows=len(frame),
        segments=n_segments,
        snapshots=len(stamps),
        anomalies=len(anomalies),
    )
    return frame


# ============================================================================ reports
DEPTH_CHIPS: tuple[tuple[float, str, int], ...] = (
    (25.0, "ankle", 10),
    (55.0, "knee", 45),
    (85.0, "waist", 90),
)
"""Accumulated rain in mm over a hotspot, the chip a citizen would pick, and the centimetres
SPEC.md 11.6 reads that chip as. The thresholds are a **stated rule**, not a hydraulic
result: no depth was computed here, and none was measured on the day."""

REPORT_DELAY_MIN = (5, 26)
"""Minutes between the water arriving and somebody reporting it."""

REPORT_JITTER_M = 120.0
"""A reporter stands near the junction, not on the survey point."""

N_SYNTHETIC_REPORTS = 24
"""Inside the 15-30 SPEC.md 10.2 asks for."""


def synthetic_reports(
    hotspots: Sequence[Mapping[str, Any]],
    truth_mm_h: np.ndarray,
    times_min: np.ndarray,
    domain: StormDomain,
    t0: datetime,
    *,
    seed: int,
    count: int = N_SYNTHETIC_REPORTS,
) -> list[dict[str, Any]]:
    """Citizen reports at the chronic hotspots, with chips that follow the rain that fell.

    A hotspot produces a candidate report each time its accumulated rain crosses a chip
    threshold in :data:`DEPTH_CHIPS`; a seeded sample of ``count`` candidates becomes the
    stream, so reports spread across the window and across the three chips instead of all
    arriving at once. Every row carries ``synthetic: true``.
    """
    times = np.asarray(times_min, dtype=float)
    lons = [float(spot["lon"]) for spot in hotspots]
    lats = [float(spot["lat"]) for spot in hotspots]
    x, y = project(lons, lats, domain.crs)
    row, col = pixel_index(domain, x, y)
    rates = np.asarray(truth_mm_h, dtype=float)[:, row, col]  # (t, hotspot)
    step_h = float(times[1] - times[0]) / 60.0 if times.size > 1 else 0.0
    accumulated = np.concatenate(
        [np.zeros((1, rates.shape[1])), np.cumsum((rates[:-1] + rates[1:]) / 2.0 * step_h, axis=0)]
    )

    rng = np.random.default_rng([seed, SEED_REPORTS])
    window_min = float(times[-1] - times[0])
    candidates: list[dict[str, Any]] = []
    for index, spot in enumerate(hotspots):
        for threshold, chip, centimetres in DEPTH_CHIPS:
            crossings = np.flatnonzero(accumulated[:, index] >= threshold)
            if crossings.size == 0:
                continue
            crossed_min = float(times[int(crossings[0])])
            delay = int(rng.integers(*REPORT_DELAY_MIN))
            minutes = crossed_min + delay
            if minutes > window_min:
                continue
            jitter = rng.uniform(-REPORT_JITTER_M, REPORT_JITTER_M, size=2)
            lon, lat = _offset_lonlat(lons[index], lats[index], jitter[0], jitter[1])
            candidates.append(
                {
                    "id": f"RPT-{spot.get('hotspot_id') or spot.get('slug')}-{chip}",
                    "ts": t0 + timedelta(minutes=minutes),
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "depth_hint": chip,
                    "depth_cm_prior": centimetres,
                    "place": str(spot.get("name", "")),
                    "text": _report_text(chip, str(spot.get("name", ""))),
                    "synthetic": True,
                }
            )
    candidates.sort(key=lambda row_: (row_["ts"], row_["id"]))
    if len(candidates) > count:
        chosen = np.sort(rng.choice(len(candidates), size=count, replace=False))
        candidates = [candidates[int(index)] for index in chosen.tolist()]
    log.info("streams.reports_synthetic", candidates=len(candidates), hotspots=len(hotspots))
    return candidates


def _offset_lonlat(lon: float, lat: float, east_m: float, north_m: float) -> tuple[float, float]:
    """Shift a WGS84 point by metres, to the accuracy a hundred-metre jitter needs."""
    lat_out = lat + north_m / 111_320.0
    lon_out = lon + east_m / (111_320.0 * math.cos(math.radians(lat)))
    return (lon_out, lat_out)


def _report_text(chip: str, place: str) -> str:
    phrase = {
        "ankle": "Water over the kerb",
        "knee": "Water up to my knees",
        "waist": "Waist-deep water, road impassable",
    }[chip]
    return f"{phrase} at {place}." if place else f"{phrase}."


def pin_reports(
    pins: Sequence[Mapping[str, Any]], t0: datetime, window_min: float
) -> list[dict[str, Any]]:
    """The sourced pins that fall inside the window, carried into the report stream.

    They arrive as observations the way a citizen report does, but they are not synthetic and
    they carry no depth chip: no cached source states a depth in centimetres, so inventing one
    would break rule 7. ``depth_phrase`` carries whatever the source did say, so the console
    can show "vehicles submerged" honestly.
    """
    rows: list[dict[str, Any]] = []
    for pin in pins:
        stamp = datetime.fromisoformat(str(pin["ts"]))
        minutes = (stamp - t0).total_seconds() / 60.0
        if minutes < 0.0 or minutes > window_min:
            continue
        rows.append(
            {
                "id": str(pin["id"]),
                "ts": stamp,
                "lat": round(float(pin["lat"]), 7),
                "lon": round(float(pin["lon"]), 7),
                "depth_hint": None,
                "depth_phrase": pin.get("depth_phrase"),
                "place": str(pin.get("name", "")),
                "text": pin.get("text"),
                "ts_uncertainty_min": int(pin.get("ts_uncertainty_min", 0)),
                "source_url": str(pin["source_url"]),
                "synthetic": False,
            }
        )
    rows.sort(key=lambda row: (row["ts"], row["id"]))
    log.info("streams.reports_sourced", rows=len(rows))
    return rows


# ============================================================================ ground truth
GROUND_TRUTH_KIND_MAP: dict[str, str] = {"rail": "news"}
"""The research draft uses ``rail`` for railway waterlogging. The bundle contract's kinds say
*where a pin came from* - log, news, social, platform - and all three of those pins came from
news live blogs, so they are carried as ``news`` and the mapping is stated on the feature."""

GROUND_TRUTH_PROPERTY_ORDER: tuple[str, ...] = (
    "id",
    "ts",
    "ts_uncertainty_min",
    "name",
    "lon",
    "lat",
    "depth_cm",
    "depth_phrase",
    "kind",
    "text",
    "source_url",
    "source_title",
    "cached_path",
    "geocode",
    "geocode_precision",
    "inside_aoi",
    "synthetic",
    "note",
)
"""Fixed key order, so two builds write byte-identical GeoJSON."""


def load_ground_truth_pins(path: Path, *, inside_aoi_only: bool = True) -> list[dict[str, Any]]:
    """Read the curated draft and return its pin properties with the geometry folded in.

    Features with no geometry, or outside the area of interest when ``inside_aoi_only``, are
    dropped: the draft flags them itself so the bundle builder does not have to guess.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    pins: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        properties = dict(feature.get("properties") or {})
        if geometry.get("type") != "Point" or len(coordinates) < 2:
            continue
        if inside_aoi_only and not properties.get("inside_aoi"):
            continue
        properties["lon"] = float(coordinates[0])
        properties["lat"] = float(coordinates[1])
        pins.append(properties)
    pins.sort(key=lambda pin: str(pin["id"]))
    log.info("streams.ground_truth", path=str(path), pins=len(pins))
    return pins


def ground_truth_features(pins: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Contract-shaped GeoJSON features for ``ground_truth.geojson``.

    Nothing is added: the depth stays ``null`` on every pin because no cached source states
    one, and the time uncertainty the researcher recorded is carried through untouched.
    """
    features: list[dict[str, Any]] = []
    for pin in pins:
        properties: dict[str, Any] = dict(pin)
        kind = str(properties.get("kind", ""))
        mapped = GROUND_TRUTH_KIND_MAP.get(kind)
        if mapped is not None:
            properties["kind"] = mapped
            existing = properties.get("note")
            mapping_note = (
                f"The research draft records this pin's kind as '{kind}' (railway "
                f"waterlogging); the bundle contract names the medium a pin came from, and "
                f"this one came from a news live blog, so it is carried as '{mapped}'."
            )
            properties["note"] = f"{existing} {mapping_note}".strip() if existing else mapping_note
        properties["synthetic"] = False
        ordered = {key: properties[key] for key in GROUND_TRUTH_PROPERTY_ORDER if key in properties}
        ordered.update({k: v for k, v in properties.items() if k not in ordered})
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [ordered["lon"], ordered["lat"]],
                },
                "properties": ordered,
            }
        )
    return features


def load_hotspots(path: Path) -> list[dict[str, Any]]:
    """Read ``city/<city>/hotspots.geojson`` into plain property dictionaries with lon/lat."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    spots: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        properties = dict(feature.get("properties") or {})
        if geometry.get("type") != "Point" or len(coordinates) < 2:
            continue
        properties.setdefault("lon", float(coordinates[0]))
        properties.setdefault("lat", float(coordinates[1]))
        spots.append(properties)
    spots.sort(key=lambda spot: str(spot.get("hotspot_id") or spot.get("name")))
    return spots


__all__ = [
    "ANOMALY_HALF_WINDOW_MIN",
    "ANOMALY_PLATEAU",
    "CONFOUNDER_FRACTION",
    "DEPTH_CHIPS",
    "GAUGE_CADENCE_MIN",
    "GAUGE_NOISE_FRAC",
    "GROUND_TRUTH_KIND_MAP",
    "N_SYNTHETIC_REPORTS",
    "SPREAD_HOPS",
    "TIDE_CADENCE_MIN",
    "TIDE_LOW_WATER_M",
    "TRAFFIC_CADENCE_MIN",
    "TRAFFIC_CLASSES",
    "WEEKDAY_HOUR_FACTOR",
    "GaugeSite",
    "PinSnap",
    "SegmentTable",
    "TrafficAnomaly",
    "confounder_anomalies",
    "gauge_rows",
    "ground_truth_features",
    "load_gauge_sites",
    "load_ground_truth_pins",
    "load_hotspots",
    "load_segments",
    "pin_anomalies",
    "pin_reports",
    "pixel_index",
    "project",
    "snap_pins",
    "synthetic_reports",
    "tide_rows",
    "traffic_frame",
]

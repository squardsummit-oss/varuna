"""Scoring one event against sourced ground truth (SPEC.md 11.12, 7.10; task P9.7).

**What can honestly be scored, and what cannot.** The 2 July 2019 bundle carries 29 curated pins,
every one with a `source_url` and a stated time uncertainty (SPEC.md 7). None of them states a
depth: they are civic logs and news reports that say a street was waterlogged, not gauges. So:

* **Detection is scorable.** For each pin inside the forecast window, did VARUNA predict water
  over the threshold on a street near it, within the pin's own time uncertainty? That gives hits,
  misses and - from the streets VARUNA flagged where no pin ever landed - false alarms, and from
  those CSI, POD and FAR.
* **Timing is scorable.** How long before the pin did VARUNA first flag that street? This is the
  number the whole product claims ("three hours early"), so it is the one to be most careful with.
* **Depth MAE is not.** No pin carries a depth, so there is nothing to take a difference against.
  It is reported as unavailable with that reason rather than as a number, because a depth error
  computed against depths nobody published would be an invention (rule 6).
* **Brier and reliability of street depth are not, yet.** The demo runs carry a street ensemble
  (20 members from 13 September 2026, 50 since the 26 September re-bake, ADR-0076) and
  ``segments_wet.json`` holds its exceedance probabilities, but this scorer reads only the depth
  series and never those probabilities, so no probabilistic depth score is computed. The served
  reason reads the member count from each run's ``run.json`` rather than stating one. The
  probability of *rain* is scored, Brier and reliability both, by :mod:`varuna_verify.rain_event`.

**A false alarm needs care.** Absence of a pin is not absence of flooding: nobody logged most of
Mumbai that morning. Counting every unpinned wet street as a false alarm would score the city's
record-keeping, not the forecast. So false alarms are counted only within
:data:`FALSE_ALARM_RADIUS_M` of a pin - places the record demonstrably covers - and the report
says so. The number is a lower bound and is labelled one.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from varuna_schemas.paths import bundles_dir, runs_dir

log = structlog.get_logger("varuna.verify.event")

__all__ = ["EventScores", "Pin", "score_event"]

THRESHOLD_CM = 30.0
"""Depth a pin is scored against: `--depth-3`, where cars stop (SPEC.md 6.2, 11.12)."""

MATCH_RADIUS_M = 250.0
"""How far from a pin a wet street counts as the same place.

A pin is geocoded to a junction; the street that floods is the one running through it, and a
segment centroid can sit a block away. 250 m is about one Mumbai block, and tight enough that a
hit at King's Circle cannot be claimed by water at Sion."""

FALSE_ALARM_RADIUS_M = 250.0
"""Only streets this close to some pin can be counted as false alarms; see the module docstring."""

TIME_SLACK_MIN = 30.0
"""Added to each pin's own `ts_uncertainty_min` when matching in time.

A civic log is written when somebody gets round to it, not when the water arrives, and the
forecast step is five minutes wide. Thirty minutes is the smallest slack that does not turn a
correct forecast into a miss on bookkeeping."""


@dataclass(frozen=True, slots=True)
class Pin:
    """One sourced ground-truth observation."""

    id: str
    ts: datetime
    lon: float
    lat: float
    name: str
    kind: str
    ts_uncertainty_min: float
    depth_cm: float | None
    source_url: str


@dataclass
class EventScores:
    """Everything `/verify` shows for one event."""

    event: str
    run_ids: list[str]
    window: tuple[datetime, datetime] | None
    threshold_cm: float
    n_pins_total: int
    n_pins_in_window: int
    hits: int = 0
    misses: int = 0
    false_alarms: int = 0
    lead_minutes: list[float] = field(default_factory=list)
    matched: list[dict[str, Any]] = field(default_factory=list)
    missed: list[dict[str, Any]] = field(default_factory=list)
    unavailable: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def csi(self) -> float | None:
        denominator = self.hits + self.misses + self.false_alarms
        return self.hits / denominator if denominator else None

    @property
    def pod(self) -> float | None:
        denominator = self.hits + self.misses
        return self.hits / denominator if denominator else None

    @property
    def far(self) -> float | None:
        denominator = self.hits + self.false_alarms
        return self.false_alarms / denominator if denominator else None

    @property
    def median_lead_min(self) -> float | None:
        """Median warning time over the pins VARUNA flagged **before** they were reported.

        Hits with a negative lead are excluded from this number and counted separately. They are
        real hits - a pin whose own stated uncertainty is twelve hours cannot rule out a forecast
        an hour after the log was written - but averaging them into a figure the product sells as
        "three hours early" would flatter it. :attr:`n_hits_after` is what they are reported as.
        """
        early = [lead for lead in self.lead_minutes if lead >= 0]
        if not early:
            return None
        ordered = sorted(early)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return 0.5 * (ordered[middle - 1] + ordered[middle])

    @property
    def n_hits_early(self) -> int:
        return sum(1 for lead in self.lead_minutes if lead >= 0)

    @property
    def n_hits_after(self) -> int:
        """Hits the forecast made after the pin was logged, inside the pin's own uncertainty."""
        return sum(1 for lead in self.lead_minutes if lead < 0)


def _metres(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Equirectangular distance. At city scale the error against the great circle is centimetres."""
    k = math.cos(math.radians(0.5 * (lat1 + lat2)))
    dx = (lon2 - lon1) * k * 111_320.0
    dy = (lat2 - lat1) * 110_574.0
    return math.hypot(dx, dy)


def load_pins(event: str) -> list[Pin]:
    """The bundle's curated ground truth.

    Raises:
        FileNotFoundError: the bundle has not been generated.
    """
    path = Path(bundles_dir()) / event / "ground_truth.geojson"
    if not path.is_file():
        msg = f"No ground truth for {event}: {path} is missing. Run `make bundle BUNDLE={event}`."
        raise FileNotFoundError(msg)
    data = json.loads(path.read_text(encoding="utf-8"))
    pins: list[Pin] = []
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point":
            continue
        lon, lat = geometry["coordinates"][:2]
        pins.append(
            Pin(
                id=str(props.get("id", "")),
                ts=datetime.fromisoformat(str(props["ts"])),
                lon=float(lon),
                lat=float(lat),
                name=str(props.get("name", "")),
                kind=str(props.get("kind", "")),
                ts_uncertainty_min=float(props.get("ts_uncertainty_min") or 0.0),
                depth_cm=(float(props["depth_cm"]) if props.get("depth_cm") is not None else None),
                source_url=str(props.get("source_url", "")),
            )
        )
    return pins


def _runs_for(event: str) -> list[Path]:
    root = runs_dir()
    if not root.is_dir():
        return []
    return [
        p
        for p in sorted(root.iterdir())
        if p.is_dir() and (p / "segments_wet.json").is_file() and event.split("-")[1] in p.name
    ]


def _ensemble_sizes(root: Path, run_ids: list[str]) -> list[int]:
    """The distinct ``ensemble_n`` the scored runs record, ascending; unreadable runs are left out.

    Read from each run's ``run.json`` so the text served beside the scores says what the runs are,
    not what they were when the text was written.
    """
    sizes: set[int] = set()
    for run_id in run_ids:
        try:
            meta = json.loads((root / run_id / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        n = meta.get("ensemble_n") if isinstance(meta, dict) else None
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            sizes.add(n)
    return sorted(sizes)


@dataclass(frozen=True, slots=True)
class _Wet:
    """One run's wet streets, with each street's position and its series."""

    run_id: str
    times: list[datetime]
    points: dict[str, tuple[float, float]]
    depth: dict[str, list[float]]


def _load_wet(path: Path) -> _Wet | None:
    from varuna_route.graph import load_graph

    wet = json.loads((path / "segments_wet.json").read_text(encoding="utf-8"))
    stamps = wet.get("valid_ts") or []
    if not stamps:
        return None
    graph = load_graph("mumbai")
    # A segment's position: the midpoint of the first edge carrying its id, which is the same
    # geometry the map draws it at.
    first: dict[str, int] = {}
    for e, segment_id in enumerate(graph.edge_segment):
        first.setdefault(segment_id, e)
    points: dict[str, tuple[float, float]] = {}
    for segment_id in wet.get("depth_cm", {}):
        e = first.get(segment_id)
        if e is None:
            continue
        tail = int(graph.edge_tail[e])
        head = int(graph.head[e])
        points[segment_id] = (
            0.5 * (float(graph.lon[tail]) + float(graph.lon[head])),
            0.5 * (float(graph.lat[tail]) + float(graph.lat[head])),
        )
    return _Wet(
        run_id=str(wet.get("run_id", path.name)),
        times=[datetime.fromisoformat(str(t)) for t in stamps],
        points=points,
        depth={k: [float(x) for x in v] for k, v in wet.get("depth_cm", {}).items()},
    )


def score_event(event: str = "MUM-2019-07-02", threshold_cm: float = THRESHOLD_CM) -> EventScores:
    """Score every baked run of an event against its sourced pins."""
    pins = load_pins(event)
    runs = [w for w in (_load_wet(p) for p in _runs_for(event)) if w is not None]

    if not runs:
        return EventScores(
            event=event,
            run_ids=[],
            window=None,
            threshold_cm=threshold_cm,
            n_pins_total=len(pins),
            n_pins_in_window=0,
            notes=[
                "No baked runs for this event. Run `make bake` and score again.",
            ],
        )

    start = min(w.times[0] for w in runs)
    end = max(w.times[-1] for w in runs)

    scores = EventScores(
        event=event,
        run_ids=[w.run_id for w in runs],
        window=(start, end),
        threshold_cm=threshold_cm,
        n_pins_total=len(pins),
        n_pins_in_window=0,
    )

    # Across every run, the earliest time each segment is forecast over the threshold. Runs
    # overlap - a 06:10 cycle forecasts to 09:10, a 06:40 cycle to 09:40 - and the earliest
    # forecast is the one that matters for "how early did you know".
    first_over: dict[str, datetime] = {}
    positions: dict[str, tuple[float, float]] = {}
    for w in runs:
        for segment_id, series in w.depth.items():
            point = w.points.get(segment_id)
            if point is None:
                continue
            positions.setdefault(segment_id, point)
            for k, value in enumerate(series):
                if value > threshold_cm:
                    when = w.times[min(k, len(w.times) - 1)]
                    if segment_id not in first_over or when < first_over[segment_id]:
                        first_over[segment_id] = when
                    break

    flagged_near_a_pin: set[str] = set()
    for pin in pins:
        if not (start - timedelta(hours=1) <= pin.ts <= end + timedelta(hours=1)):
            continue
        scores.n_pins_in_window += 1
        slack = timedelta(minutes=pin.ts_uncertainty_min + TIME_SLACK_MIN)

        near = [
            segment_id
            for segment_id, (lon, lat) in positions.items()
            if _metres(pin.lon, pin.lat, lon, lat) <= MATCH_RADIUS_M
        ]
        flagged = [s for s in near if s in first_over]
        flagged_near_a_pin.update(flagged)

        # A hit needs the flag to land inside the pin's own uncertainty, or before it: a forecast
        # that called the street at 08:20 for a pin logged at 09:47 is early, not wrong.
        in_time = [s for s in flagged if first_over[s] <= pin.ts + slack]
        if in_time:
            earliest = min(first_over[s] for s in in_time)
            lead = (pin.ts - earliest).total_seconds() / 60.0
            scores.hits += 1
            scores.lead_minutes.append(lead)
            scores.matched.append(
                {
                    "pin_id": pin.id,
                    "name": pin.name,
                    "kind": pin.kind,
                    "pin_ts": pin.ts.isoformat(),
                    "forecast_ts": earliest.isoformat(),
                    "lead_min": round(lead, 1),
                    "n_segments": len(in_time),
                    "source_url": pin.source_url,
                }
            )
        else:
            scores.misses += 1
            deepest = max(
                (max(w.depth.get(s, [0.0])) for w in runs for s in near),
                default=0.0,
            )
            scores.missed.append(
                {
                    "pin_id": pin.id,
                    "name": pin.name,
                    "kind": pin.kind,
                    "pin_ts": pin.ts.isoformat(),
                    "n_segments_near": len(near),
                    "deepest_nearby_cm": round(deepest, 1),
                    "reason": (
                        "No street within 250 m was forecast over the threshold"
                        if not flagged
                        else "Flagged, but later than the pin's time uncertainty allows"
                    ),
                    "source_url": pin.source_url,
                }
            )

    # False alarms: streets flagged near a pin's location that no pin ever corroborated. See the
    # module docstring on why the radius is there.
    corroborated = {entry["pin_id"] for entry in scores.matched}
    del corroborated
    near_any_pin = {
        segment_id
        for segment_id, (lon, lat) in positions.items()
        if segment_id in first_over
        and any(_metres(p.lon, p.lat, lon, lat) <= FALSE_ALARM_RADIUS_M for p in pins)
    }
    scores.false_alarms = len(near_any_pin - flagged_near_a_pin)

    sizes = _ensemble_sizes(runs_dir(), scores.run_ids)
    if not sizes:
        ensemble = "a street ensemble"
    elif len(sizes) == 1:
        ensemble = f"a {sizes[0]}-member street ensemble"
    else:
        ensemble = f"street ensembles of {sizes[0]} to {sizes[-1]} members"
    scores.unavailable = {
        "depth_mae_cm": (
            "None of the 29 sourced pins states a depth - they are civic logs and news reports, "
            "not gauges - so there is nothing to take a difference against."
        ),
        "brier_score": (
            f"The runs carry {ensemble} and its probabilities of street depth above each "
            "threshold, but this scorer reads only the depth series and does not score those "
            "probabilities yet. The probability of rain is scored: Rain skill by lead time "
            "serves its Brier score."
        ),
        "reliability_diagram": (
            "For street depth, this needs the scorer to read the same probabilities as the Brier "
            "score. The reliability of the rain probability is drawn under Rain skill by lead "
            "time."
        ),
    }
    scores.notes = [
        f"Scored at {threshold_cm:.0f} cm, the depth at which cars stop.",
        f"A pin matches a street within {MATCH_RADIUS_M:.0f} m and inside its own stated time "
        f"uncertainty plus {TIME_SLACK_MIN:.0f} minutes.",
        "False alarms are counted only near a pin, because nobody logged most of Mumbai that "
        "morning and an unpinned wet street is not evidence of a wrong forecast. The count is a "
        "lower bound.",
        "Ground truth is 29 curated public records, each with a source URL and a stated time "
        "uncertainty. The radar and the gauges in this replay are reconstructed.",
    ]

    log.info(
        "verify.event",
        # `event` is structlog's own key for the message, so the bundle id goes under `bundle`.
        bundle=event,
        runs=len(runs),
        pins=scores.n_pins_in_window,
        hits=scores.hits,
        misses=scores.misses,
        false_alarms=scores.false_alarms,
        csi=None if scores.csi is None else round(scores.csi, 3),
        median_lead=scores.median_lead_min,
    )
    return scores


THRESHOLD_SWEEP: tuple[float, ...] = (5.0, 15.0, 30.0)
"""Depths the event is scored at (SPEC.md 6.2's ramp: water on the street, two-wheelers slow,
cars stop).

**Why a sweep and not one number.** The 29 pins record *waterlogging* - "traffic diverted",
"water in the subway" - and not a depth. Scoring them against a single 30 cm threshold treats a
civic log as if it said "over thirty centimetres", which it does not. Three thresholds make the
modelling choice visible instead of hiding it inside one headline figure, and the spread between
them is itself informative: if the forecast finds the streets at 5 cm and loses them at 30 cm,
the pattern is right and the level is low, which is a different problem from missing them
entirely."""


def sweep(event: str = "MUM-2019-07-02") -> dict[str, Any]:
    """Score the event at every threshold in :data:`THRESHOLD_SWEEP`.

    The headline is the 15 cm row: it is the depth at which a two-wheeler slows, which is about
    the least water a Mumbai civic log would call waterlogging.
    """
    rows = {f"{int(t)}": as_dict(score_event(event, threshold_cm=t)) for t in THRESHOLD_SWEEP}
    headline = rows["15"]
    return {
        **headline,
        "headline_threshold_cm": 15.0,
        "by_threshold": {
            key: {
                "threshold_cm": row["threshold_cm"],
                "contingency": row["contingency"],
                "scores": row["scores"],
            }
            for key, row in rows.items()
        },
        "notes": [
            *headline["notes"],
            "Scored at 5, 15 and 30 cm because the pins record waterlogging and not a depth; the "
            "headline is 15 cm, the depth at which a two-wheeler slows.",
        ],
    }


def as_dict(scores: EventScores) -> dict[str, Any]:
    """The shape `GET /v1/verification` returns and `public/verification.json` holds."""
    return {
        "event": scores.event,
        "run_ids": scores.run_ids,
        "window": (
            [scores.window[0].isoformat(), scores.window[1].isoformat()] if scores.window else None
        ),
        "threshold_cm": scores.threshold_cm,
        "ground_truth": {
            "n_pins": scores.n_pins_total,
            "n_in_window": scores.n_pins_in_window,
            "sourced": True,
        },
        "contingency": {
            "hits": scores.hits,
            "misses": scores.misses,
            "false_alarms": scores.false_alarms,
        },
        "scores": {
            "csi": None if scores.csi is None else round(scores.csi, 3),
            "pod": None if scores.pod is None else round(scores.pod, 3),
            "far": None if scores.far is None else round(scores.far, 3),
            "median_lead_min": (
                None if scores.median_lead_min is None else round(scores.median_lead_min, 1)
            ),
            "n_hits_early": scores.n_hits_early,
            "n_hits_after": scores.n_hits_after,
        },
        "matched": scores.matched,
        "missed": scores.missed,
        "unavailable": scores.unavailable,
        "notes": scores.notes,
    }

"""Turning a forecast into something a ward officer can act on (SPEC.md 11.10, P8.7).

An alert is the point where VARUNA stops describing water and starts asking someone to do
something, so the bar for raising one is higher than "a number went up".

**Hysteresis across cycles (SPEC.md 11.10, 7.5 AC1).** A level is raised when
``P(h > θ) >= 0.6`` in **two consecutive cycles** and cleared when it falls to ``<= 0.3``. The
memory is the previous run's own ``alerts.json``: every run writes a ``hysteresis`` record of
each situation (scope and place) and each level's state - ``pending`` after one cycle at or
above 0.6, ``raised`` after two, carried while the probability stays above 0.3 - and the next
cycle reads it (:func:`previous_record`, :func:`apply_cycle_hysteresis`). The previous run is an
input like the radar, so a bake stays byte-identical for identical inputs (rule 8,
``tests/test_alert_hysteresis.py`` bakes the same pair twice).

**What ``P`` is on these runs.** One cycle's exceedance is still read along its own forecast:
the depth has to stay above the threshold for two consecutive 5-minute steps
(:data:`MIN_PERSIST_STEPS`), because one step is a single cell's arithmetic. The Twin that feeds
the queue is one deterministic run, so that ``P`` is 0 or 1 and the 0.3-0.6 band that holds a
raised alert open is empty until the ensemble's probabilities reach this module; the state machine
carries the band anyway so nothing changes shape when they do. ``trigger_p`` says which number
triggered, and ``persists_unit`` says the count is in cycles.

**The first cycle raises nothing.** With no previous run within :data:`MAX_CYCLE_GAP_MIN`, every
exceedance is ``pending``: two consecutive cycles means two, and a bake that starts at 06:10 has
seen one. The queue says so rather than inventing a history.

**Scope.** SPEC.md 11.10 puts the state machine "per segment/ward". The chronic register leads
the queue: those are the named, sourced places a judge recognises. But on a cycle where the
register stays dry and 226 ordinary streets go over 45 cm, a queue of hotspots alone would report
an all-clear over a flooding city - so the streets follow, deduplicated by name so one road is one
alert rather than forty.

**Exercise, not Actual.** Every alert from a replay carries CAP ``status=Exercise`` (SPEC.md
11.10). A replay of 2 July 2019 must never produce a document that could be mistaken for a live
civil warning.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET

import numpy as np
import structlog
from varuna_schemas.constants import IST

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from numpy.typing import NDArray

log = structlog.get_logger("varuna.products.alerts")

__all__ = [
    "CLEAR_P",
    "ESCALATION_PATH",
    "LEVELS",
    "LOCALITY_RADIUS_M",
    "MAX_ALERTS",
    "MAX_CYCLE_GAP_MIN",
    "MIN_PERSIST_STEPS",
    "RAISE_CYCLES",
    "RAISE_P",
    "AlertQueue",
    "alert_identity",
    "apply_cycle_hysteresis",
    "build_alerts",
    "cap_xml",
    "escalation_by_level",
    "forecast_phrase",
    "landmark",
    "load_escalation",
    "members_crossing",
    "nearest_locality",
    "previous_record",
    "run_cycle_ts",
    "served_queue",
    "situation_key",
    "street_member_series",
    "street_series",
    "write_alerts",
]

LEVELS: tuple[tuple[str, int], ...] = (
    ("severe", 45),
    ("moderate", 30),
    ("watch", 15),
)
"""Level and its depth threshold in cm, worst first (SPEC.md 11.10).

The thresholds are the depth ramp's own bands, so an alert level and the colour of the street it
is about can never disagree."""

MIN_PERSIST_STEPS = 2
"""Steps a threshold must stay crossed for one cycle to count it as an exceedance: 10 minutes.

This is what makes one cycle's ``P`` 1 rather than 0 on a deterministic run; the hysteresis of
SPEC.md 11.10 is then applied across cycles (:data:`RAISE_CYCLES`). One step is a single 30 m
cell's arithmetic; two is a trend."""

RAISE_P = 0.6
"""``P(h > θ)`` at or above which a cycle counts towards raising a level (SPEC.md 11.10)."""

CLEAR_P = 0.3
"""``P(h > θ)`` at or below which a raised level clears (SPEC.md 11.10)."""

RAISE_CYCLES = 2
"""Consecutive cycles at or above :data:`RAISE_P` before a level is raised (SPEC.md 11.10)."""

MAX_CYCLE_GAP_MIN = 60
"""How far back a previous run may be and still count as the previous *cycle*.

The live cadence is five minutes and the shipped demo bake is every thirty (``make bake ARGS=
"--every 30"``), so an hour admits both. A run from yesterday is not the cycle before this one,
and carrying its state forward would raise an alert on the strength of a storm that ended."""

HYSTERESIS_VERSION = 1
"""Written into the record so a reader can tell the rule it was produced under."""

MAX_ALERTS = 60
"""How many alerts a run's queue carries, worst first.

A heavy cycle puts 226 segments over 45 cm. Deduplicated by street that is a few dozen roads,
which an operator can read; without a cap a bad hour produces a list nobody scrolls to the
bottom of, and the alerts that matter are buried in it."""

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"

SENDER = "varuna@sih2026.example"
"""CAP requires a sender identifier. It is deliberately an example domain: VARUNA is a prototype
and must not appear to originate from a municipal or IMD address (rule 7)."""


def alert_identity(alert: Mapping[str, Any]) -> str:
    """The cycle-independent identity of one alert: scope, place and level.

    An alert's ``id`` is ``VARUNA-{run_id}-{key}-{level}``, so it names the cycle that raised it
    and no two cycles share one - measured on the seven baked demo cycles, not a single id is
    common to any two consecutive ones, while twelve situations carry from 03:10Z to 03:40Z. An
    id is therefore the right key for *this queue* and the wrong key for *this junction*: an
    officer who has acknowledged Hindmata at severe has not un-acknowledged it because a new
    cycle landed.

    The place is the register's ``hotspot_id`` where there is one and the ``area_desc`` otherwise,
    which for a street alert is the street's own name (``build_alerts`` passes ``area=street``).
    The level is part of the identity on purpose: a junction stepping from moderate to severe is
    a new situation and deserves fresh eyes.

    **This is one half of a pair.** ``apps/command/lib/alert-identity.ts`` computes the same
    string in the browser to decide which cards are new (motion M16), and
    ``services/api/varuna_api/routers/ops.py`` stores it on every acknowledgement so the state
    can be found again next cycle. The two must not drift, so the fallback below mirrors the
    TypeScript ``??`` exactly - absent, not merely falsy - and
    ``services/products/tests/test_alert_identity.py`` runs the TypeScript file's own cases.
    """
    hotspot_id = alert.get("hotspot_id")
    place = alert.get("area_desc", "") if hotspot_id is None else hotspot_id
    return f"{alert.get('scope', '')}|{place}|{alert.get('level', '')}"


def _runs(above: list[bool], min_steps: int) -> list[tuple[int, int]]:
    """Index ranges where ``above`` stays true for at least ``min_steps`` steps."""
    windows: list[tuple[int, int]] = []
    start: int | None = None
    for i, flag in enumerate([*above, False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            if i - start >= min_steps:
                windows.append((start, i - 1))
            start = None
    return windows


def street_series(
    depth_cm: dict[str, list[float]],
    names: dict[str, str],
    points: dict[str, tuple[float, float]] | None = None,
) -> dict[str, list[float]]:
    """Collapse per-segment depth series onto street names, keeping the worst step by step.

    A named road is dozens of segments and they flood at different depths; the alert is about the
    road, so each step takes the deepest segment on it. Unnamed ways are dropped rather than
    given a placeholder: an alert that cannot say where it is cannot be acted on.
    """
    out: dict[str, list[float]] = {}
    deepest: dict[str, tuple[float, str]] = {}
    for segment_id, series in depth_cm.items():
        name = names.get(segment_id)
        if not name or not series:
            continue
        current = out.get(name)
        if current is None:
            out[name] = list(series)
        else:
            for i, value in enumerate(series[: len(current)]):
                if value > current[i]:
                    current[i] = value
        # The road's pin goes on its worst segment: that is where a pump would be sent and what
        # the CAP circle should cover, not the road's midpoint two kilometres away.
        peak = max(series)
        if name not in deepest or peak > deepest[name][0]:
            deepest[name] = (peak, segment_id)

    if points is not None:
        STREET_POINTS.clear()
        for name, (_peak, segment_id) in deepest.items():
            point = points.get(segment_id)
            if point:
                STREET_POINTS[name] = point
    return out


STREET_POINTS: dict[str, tuple[float, float]] = {}
"""Street name to the lon/lat of its worst-flooding segment, filled by :func:`street_series`.

A module-level cache rather than a second return value, so the existing callers of
``street_series`` keep their shape; ``build_alerts`` and the pump plan read it straight after."""


def _level_alerts(
    series: list[float],
    *,
    key: str,
    name: str,
    area: str,
    run_id: str,
    cycle_ts: datetime,
    times: tuple[datetime, ...],
    mode: str,
    scope: str,
    scope_id: str | None,
    hotspot_id: str | None = None,
    lon: float | None = None,
    lat: float | None = None,
    source_url: str | None = None,
    escalation: Mapping[str, list[str]] | None = None,
    locality: str | None = None,
    members: NDArray[np.floating] | None = None,
) -> dict[str, dict[str, Any]]:
    """One candidate alert per level this series crosses for :data:`MIN_PERSIST_STEPS`, worst first.

    Every level, not only the worst, because the cross-cycle rule runs per level: a junction that
    has been over 30 cm for two cycles and over 45 cm for one is a raised *moderate* and a pending
    *severe*, and only a per-level record can say so.

    **The window's end is said as what it is.** Measured on the 2 July cycles, 59 of 60 alerts at
    08:40 (60 of 60 at 08:10, 32 of 32 at 09:10) are still over their threshold at the last step
    of the forecast, so "from 09:20 to 11:40" read as a forecast that the water goes at 11:40 when
    it only means the forecast stops there. Such a headline says "from 09:20 until at least
    11:40" instead: the water is still over the threshold where the forecast stops. ``window_to``
    and the CAP ``expires`` keep the horizon.

    The phrase is the short one on purpose. The SMS is one 160-character segment
    (``notify.sms_text``), and the longer "still above at 11:40, the end of the forecast" cut the
    instruction off 29 of the 152 listed SMS across the seven demo cycles once the locality was in
    the headline; this form cuts none of them, the longest headline being 116 characters.

    ``locality`` is a short "near Hindmata junction" for a street the register does not name
    (:func:`nearest_locality`); it goes into the headline after the street and into its own
    field, and never into ``area_desc``, which is half of the alert's cross-cycle identity.

    ``members`` is this place's depth per ensemble member, ``(n_members, n_steps)`` in cm
    (:func:`street_member_series`). With it each level says how many members also cross it by the
    same rule the raise uses (``members_above`` of ``members_total``, :func:`members_crossing`);
    without it both are None. **It is reported, never used to raise**: the raise and ``trigger_p``
    stay the Twin's own series, so which alerts a cycle raises is the same with or without it.
    """
    out: dict[str, dict[str, Any]] = {}
    for level, threshold in LEVELS:
        windows = _runs([cm > threshold for cm in series], MIN_PERSIST_STEPS)
        if not windows:
            continue

        start, end = max(windows, key=lambda w: w[1] - w[0])
        peak = max(series[start : end + 1])
        from_ts = times[start] if start < len(times) else cycle_ts
        to_ts = times[end] if end < len(times) else cycle_ts
        # Over the threshold at the forecast's last step: the window does not end, the forecast does.
        open_ended = len(times) > 0 and end >= len(times) - 1
        place = f"{name}, {locality}" if locality else name
        when = (
            f"from {from_ts.strftime('%H:%M')} until at least {to_ts.strftime('%H:%M')}"
            if open_ended
            else f"from {from_ts.strftime('%H:%M')} to {to_ts.strftime('%H:%M')}"
        )

        alert: dict[str, Any] = {
            "id": f"VARUNA-{run_id}-{key}-{level}".upper().replace("_", "-"),
            "run_id": run_id,
            "scope": scope,
            "scope_id": scope_id,
            "hotspot_id": hotspot_id,
            "level": level,
            "threshold_cm": threshold,
            "headline": f"{place}: depth above {threshold} cm {when}",
            "instruction": (
                f"Avoid {name}. Peak forecast {peak:.0f} cm. Route emergency "
                "vehicles around it; see the reachability tab for the affected catchment."
            ),
            "area_desc": area,
            # The place without the sentence around it, for a screen that lays the row out
            # itself, and the locality a street is read against when the register has no name
            # for it.
            "name": name,
            "locality": locality,
            "window_open_ended": open_ended,
            "lon": lon,
            "lat": lat,
            # 0 or 1 on a deterministic run. Reported rather than dressed up.
            "trigger_p": 1.0,
            # How much of the ensemble agrees, beside the deterministic raise: "32 of 50 members
            # also go over 45 cm here". None when the products stage had no member stack.
            "members_above": (
                members_crossing(members, threshold) if members is not None else None
            ),
            "members_total": int(members.shape[0]) if members is not None else None,
            "window_from": from_ts.isoformat(),
            "window_to": to_ts.isoformat(),
            "peak_cm": round(peak, 1),
            "raised_ts": cycle_ts.isoformat(),
            "persists_cycles": end - start + 1,
            "persists_unit": "forecast steps of 5 minutes",
            "window_steps": end - start + 1,
            "state": "raised",
            "channels": ["dashboard"],
            "source_url": source_url,
            "cap_status": "Exercise" if mode != "live" else "Actual",
        }
        if escalation is not None:
            alert["notify"] = list(escalation.get(level, []))
        out[level] = alert
    return out


EARTH_RADIUS_M = 6_371_008.8
"""Mean Earth radius (IUGG), for :func:`nearest_locality`."""

LOCALITY_RADIUS_M = 1500.0
"""How close a registered hotspot must be for a street alert to be read as "near" it.

Measured on 2 July at 08:40: every one of the 60 queued alerts is an ordinary street, none is a
register name, and ``ward`` is null on all 21,296 Mumbai segments - so without this a ward officer
reads "Pipeline Road" with nothing to place it by. 1.5 km is about the walk from one chronic
junction to the next along Dadar's arterials, close enough to be a landmark and no further."""


def landmark(name: str) -> str:
    """A register name cut to the landmark a reader knows it by, for "near <landmark>".

    The register's names carry their disambiguation - "Hindmata junction (Hindmata Cinema, Dr B.
    Ambedkar Marg)", "King's Circle / Maheshwari Udyan junction", "Postal Colony, Chembur" - which
    is right on the register and too long after a street name in a headline, where a comma would
    also read as the end of the place. The landmark is the text before the first bracket, slash
    or comma: "Hindmata junction", "King's Circle", "Postal Colony". All 28 Mumbai names keep a
    non-empty landmark; a name that would not keeps itself.
    """
    short = re.split(r"\s*[(/,]", name, maxsplit=1)[0].strip()
    return short or name.strip()


def nearest_locality(
    lon: float | None,
    lat: float | None,
    hotspots: list[dict[str, Any]],
    *,
    radius_m: float = LOCALITY_RADIUS_M,
) -> str | None:
    """The closest registered hotspot within ``radius_m`` as "near <landmark>", else None.

    The distance is equirectangular on the WGS84 point, which is exact to well under a metre at
    this range and latitude. Ties go to the name that sorts first, so the answer is a function of
    the register and never of its order (rule 8). The name is cut by :func:`landmark`.
    """
    if lon is None or lat is None:
        return None
    best: tuple[float, str] | None = None
    k = math.cos(math.radians(lat))
    for hotspot in hotspots:
        hlon, hlat, name = hotspot.get("lon"), hotspot.get("lat"), hotspot.get("name")
        if hlon is None or hlat is None or not name:
            continue
        dx = math.radians(float(hlon) - lon) * k * EARTH_RADIUS_M
        dy = math.radians(float(hlat) - lat) * EARTH_RADIUS_M
        distance = math.hypot(dx, dy)
        if distance > radius_m:
            continue
        if best is None or (distance, str(name)) < best:
            best = (distance, str(name))
    return f"near {landmark(best[1])}" if best else None


def members_crossing(members: NDArray[np.floating], threshold: float) -> int:
    """How many members stay above ``threshold`` for :data:`MIN_PERSIST_STEPS` consecutive steps.

    ``members`` is ``(n_members, n_steps)`` in cm. The rule is the raise rule applied to each
    member on its own - strictly above, for two 5-minute steps in a row, anywhere in the forecast
    - so a member counts exactly when the deterministic series would have counted had it been
    that member's.
    """
    above = np.asarray(members) > threshold
    if above.ndim != 2:
        msg = f"members must be (n_members, n_steps); got shape {above.shape}"
        raise ValueError(msg)
    n_steps = above.shape[1]
    if n_steps < MIN_PERSIST_STEPS:
        return 0
    width = n_steps - MIN_PERSIST_STEPS + 1
    held = above[:, :width].copy()
    for k in range(1, MIN_PERSIST_STEPS):
        held &= above[:, k : k + width]
    return int(held.any(axis=1).sum())


def street_member_series(
    depth_cm: NDArray[np.floating],
    member_depth_cm: NDArray[np.floating],
    segment_ids: Sequence[str],
    names: Mapping[str, str],
) -> dict[str, NDArray[np.float32]]:
    """Each named street's depth per ensemble member, ``(n_members, n_steps)``, deepest segment.

    The member analogue of :func:`street_series`: the street takes, step by step and member by
    member, its deepest segment, so ``members_above`` is counted on the same collapse as the
    Twin series the alert is raised from. The members are the products stage's own - the Twin
    level plus each member's spread, re-centred by ``depth._member_levels`` itself so the two
    cannot drift (ADR-0025) - which puts the member mean on the Twin and makes the count an
    agreement with the deterministic raise rather than a second forecast. Unnamed segments are
    dropped, as :func:`street_series` drops them.

    Cost: the re-centred stack is the stage's largest array (153 MB at 50 x 36 x 21,296 in
    float32), and this makes it a second time, then reduces it member by member into ``n_steps x
    n_streets``. Measured on a synthetic float64 stack at Mumbai's full width and its 1,238 named
    streets: 0.50-1.24 s over 13 calls, on a machine running other work. Cutting to the 10,096
    named segments before re-centring was measured beside it and was no faster (0.55-2.0 s):
    gathering columns from the last axis of the whole stack costs about what it saves.

    Raises:
        ValueError: if the stack, the ids and ``depth_cm`` do not share one segment axis.
    """
    from varuna_products.depth import _member_levels

    depth = np.asarray(depth_cm)
    members = np.asarray(member_depth_cm)
    if (
        members.ndim != 3
        or members.shape[2] != depth.shape[1]
        or len(segment_ids) != depth.shape[1]
    ):
        msg = (
            f"member_depth_cm {members.shape} and segment_ids ({len(segment_ids)}) must share "
            f"depth_cm's segment axis {depth.shape}"
        )
        raise ValueError(msg)
    columns: dict[str, list[int]] = {}
    for k, segment_id in enumerate(segment_ids):
        name = names.get(segment_id)
        if name:
            columns.setdefault(name, []).append(k)
    if not columns:
        return {}
    streets = sorted(columns)
    order = np.fromiter((k for s in streets for k in columns[s]), dtype=np.intp)
    sizes = np.fromiter((len(columns[s]) for s in streets), dtype=np.intp)
    starts = np.concatenate(([0], np.cumsum(sizes)[:-1])).astype(np.intp)
    level = _member_levels(depth, members)
    out = np.empty((level.shape[0], level.shape[1], len(streets)), dtype=np.float32)
    for m in range(level.shape[0]):
        # One member's (n_steps, n_segments) slab, its named columns grouped by street, then the
        # deepest of each group: small and contiguous, where a gather from the whole stack is not.
        out[m] = np.maximum.reduceat(level[m][:, order], starts, axis=1)
    return {street: out[:, :, j] for j, street in enumerate(streets)}


def _alert_from_series(series: list[float], **kwargs: Any) -> dict[str, Any] | None:
    """The worst level a depth series reaches, as one alert, or None if it stays below `watch`.

    Worst level only. A street that goes over 45 cm is also over 30 and over 15, and sending a
    ward officer three messages about one road is how a queue gets ignored.
    """
    levels = _level_alerts(series, **kwargs)
    return next(iter(levels.values()), None)


def situation_key(alert: Mapping[str, Any]) -> str:
    """The place an alert is about, without its level: :func:`alert_identity` minus the level.

    The cross-cycle record is kept per situation and per level inside it, so a junction stepping
    from moderate to severe is one situation with two levels rather than two unrelated alerts.
    """
    return alert_identity(alert).rsplit("|", 1)[0]


class AlertQueue(list[dict[str, Any]]):
    """The queue :func:`build_alerts` returns: a list, plus what the next step needs.

    ``candidates`` is every level every situation crossed this cycle, uncapped by
    :data:`MAX_ALERTS` - the cap is for a reader, and a hysteresis record that forgot the 61st
    street would raise it from scratch next cycle. ``cycle_ts`` is the instant the queue speaks for.
    ``final`` is True once the cross-cycle rule has been applied to it.
    """

    candidates: dict[str, dict[str, dict[str, Any]]]
    cycle_ts: datetime | None
    run_id: str | None
    final: bool
    pending: list[dict[str, Any]]
    cleared: list[dict[str, Any]]
    n_raised: int
    n_raised_by_level: dict[str, int]
    n_pending: int
    n_pending_new: int
    n_pending_step_up: int
    n_cleared: int
    first_onset: dict[str, Any] | None
    record: dict[str, Any] | None

    def __init__(self, items: list[dict[str, Any]] | None = None) -> None:
        super().__init__(items or [])
        self.candidates = {}
        self.cycle_ts = None
        self.run_id = None
        self.final = False
        self.pending = []
        self.cleared = []
        self.n_raised = 0
        self.n_raised_by_level = {level: 0 for level, _ in LEVELS}
        self.n_pending = 0
        self.n_pending_new = 0
        self.n_pending_step_up = 0
        self.n_cleared = 0
        self.first_onset = None
        self.record = None


def _sort_queue(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {level: i for i, (level, _) in enumerate(LEVELS)}
    # Hotspots first inside a level: they are the named, sourced places, and a judge scanning the
    # queue should meet Hindmata before an arterial road they have not heard of.
    return sorted(
        alerts, key=lambda a: (order[a["level"]], a["scope"] != "hotspot", -a["peak_cm"], a["id"])
    )


def build_alerts(
    hotspots: list[dict[str, Any]],
    run_id: str,
    cycle_ts: datetime,
    times: tuple[datetime, ...],
    mode: str = "baked",
    streets: dict[str, list[float]] | None = None,
    *,
    previous: Mapping[str, Any] | None = None,
    escalation: Mapping[str, list[str]] | None = None,
    street_members: Mapping[str, NDArray[np.floating]] | None = None,
) -> AlertQueue:
    """The run's alert queue: the chronic register first, then the streets behind it.

    Without ``previous`` this is what *this cycle alone* says - the worst level each place crosses
    - and the returned :class:`AlertQueue` carries every candidate so :func:`write_alerts` can
    apply the cross-cycle rule against the previous run on disk. With ``previous`` (a record from
    :func:`previous_record`, or ``{}`` for "there was no previous cycle") the rule is applied here
    and the queue is final.

    ``escalation`` is level to the tiers it reaches (:func:`escalation_by_level`); absent, it is
    read from ``config/escalation.yaml`` when that file exists.

    ``street_members`` is :func:`street_member_series` of the products stage's member stack. It
    only adds ``members_above`` and ``members_total`` to street alerts; the queue, its order and
    every raise are the same with or without it. Register alerts carry None: a junction's series
    is its cells' 90th percentile, not a street's deepest segment, and a count taken on another
    collapse would not be the same question.
    """
    if escalation is None:
        escalation = escalation_by_level()
    candidates: dict[str, dict[str, dict[str, Any]]] = {}

    for hotspot in hotspots:
        series = [float(v) for v in hotspot.get("depth_cm", [])]
        if not series:
            continue
        name = str(hotspot.get("name"))
        levels = _level_alerts(
            series,
            key=str(hotspot.get("slug") or hotspot.get("hotspot_id") or "spot"),
            name=name,
            area=f"Ward {hotspot['ward']}, {name}" if hotspot.get("ward") else name,
            run_id=run_id,
            cycle_ts=cycle_ts,
            times=times,
            mode=mode,
            scope="hotspot",
            scope_id=hotspot.get("hotspot_id"),
            hotspot_id=hotspot.get("hotspot_id"),
            lon=hotspot.get("lon"),
            lat=hotspot.get("lat"),
            source_url=hotspot.get("source_url"),
            escalation=escalation,
        )
        if levels:
            candidates[situation_key(next(iter(levels.values())))] = levels

    for index, (street, series) in enumerate(sorted((streets or {}).items())):
        lon, lat = STREET_POINTS.get(street, (None, None))
        levels = _level_alerts(
            list(series),
            key=f"street-{index:04d}",
            name=street,
            area=street,
            run_id=run_id,
            cycle_ts=cycle_ts,
            times=times,
            mode=mode,
            scope="segment",
            scope_id=None,
            lon=lon,
            lat=lat,
            escalation=escalation,
            # A street carries no ward (null on every Mumbai segment), so it is placed by the
            # nearest chronic junction instead, when one is close enough to be a landmark.
            locality=nearest_locality(lon, lat, hotspots),
            members=None if street_members is None else street_members.get(street),
        )
        if levels:
            candidates.setdefault(situation_key(next(iter(levels.values()))), levels)

    worst = [next(iter(levels.values())) for levels in candidates.values()]
    queue = AlertQueue(_sort_queue(worst)[:MAX_ALERTS])
    queue.candidates = candidates
    queue.cycle_ts = cycle_ts
    queue.run_id = run_id

    if previous is not None:
        queue = apply_cycle_hysteresis(queue, previous)

    log.info(
        "products.alerts",
        run_id=run_id,
        n=len(queue),
        final=queue.final,
        pending=len(queue.pending),
        cleared=len(queue.cleared),
        severe=sum(1 for a in queue if a["level"] == "severe"),
        moderate=sum(1 for a in queue if a["level"] == "moderate"),
        watch=sum(1 for a in queue if a["level"] == "watch"),
        hotspot_scoped=sum(1 for a in queue if a["scope"] == "hotspot"),
    )
    return queue


# ---- cross-cycle hysteresis ------------------------------------------------------------------
_RUN_ID = re.compile(r"^(?P<city>[A-Z0-9]+)-(?P<ts>\d{8}T\d{4})Z-")


def run_cycle_ts(run_id: str) -> tuple[str, datetime] | None:
    """The city prefix and UTC cycle time a run id encodes (SPEC.md 10.3), or None."""
    match = _RUN_ID.match(run_id)
    if match is None:
        return None
    moment = datetime.strptime(match["ts"], "%Y%m%dT%H%M").replace(tzinfo=UTC)
    return match["city"], moment


def forecast_phrase(run_id: str) -> str:
    """ "Forecast from 08:40 IST, 2 Jul 2019": when the run spoke, in the words an officer reads.

    The run id stays in the CAP identifier and the API; a message to a person names the time
    instead of an 80-character id. An id that does not parse gives the id itself.
    """
    parsed = run_cycle_ts(run_id)
    if parsed is None:
        return f"Forecast run {run_id}"
    local = parsed[1].astimezone(IST)
    return f"Forecast from {local:%H:%M} IST, {local.day} {local:%b %Y}"


def _record_from_legacy(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    """A hysteresis record reconstructed from a queue written before the record existed.

    Such a queue raised on one cycle's evidence, so each of its alerts is read as one cycle at or
    above :data:`RAISE_P` for its level and every level below it - ``pending``, not ``raised``.
    Reading it as raised would carry a single-cycle alert straight into a second cycle's queue
    as if it had already met the two-cycle rule.
    """
    order = [level for level, _ in LEVELS]
    situations: dict[str, dict[str, Any]] = {}
    for alert in alerts:
        level = str(alert.get("level", ""))
        if level not in order:
            continue
        entry = situations.setdefault(situation_key(alert), {"levels": {}})
        for lower in order[order.index(level) :]:
            entry["levels"][lower] = {
                "p": float(alert.get("trigger_p", 1.0)),
                "state": "pending",
                "cycles": 1,
                "since_ts": alert.get("raised_ts"),
                "raised_ts": None,
            }
    return {"situations": situations}


def previous_record(
    run_id: str, runs_root: Path | None = None, *, max_gap_min: int = MAX_CYCLE_GAP_MIN
) -> dict[str, Any]:
    """The hysteresis record of the cycle before ``run_id``, or ``{}`` when there was none.

    The previous cycle is the run of the same city whose cycle time is the latest one before
    this run's, no more than ``max_gap_min`` minutes earlier. Folders whose name starts with a dot
    are the registry's in-flight temporaries and are never read. Ties on the cycle time (a baked
    and a live run of the same instant) go to the name that sorts first, so the choice is a
    function of the directory and not of the file system's listing order.
    """
    here = run_cycle_ts(run_id)
    if here is None:
        return {}
    city, cycle = here
    if runs_root is None:
        from varuna_schemas.paths import runs_dir

        runs_root = runs_dir()
    if not runs_root.is_dir():
        return {}

    best: tuple[datetime, str] | None = None
    for child in runs_root.iterdir():
        name = child.name
        if name.startswith(".") or name == run_id or not child.is_dir():
            continue
        parsed = run_cycle_ts(name)
        if parsed is None or parsed[0] != city:
            continue
        moment = parsed[1]
        if not (cycle - timedelta(minutes=max_gap_min) <= moment < cycle):
            continue
        if not (child / "alerts.json").is_file():
            continue
        if best is None or moment > best[0] or (moment == best[0] and name < best[1]):
            best = (moment, name)
    if best is None:
        return {}

    try:
        body = json.loads((runs_root / best[1] / "alerts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        log.warning("products.alerts_previous_unreadable", run_id=best[1], error=str(error))
        return {}
    record = body.get("hysteresis")
    if not isinstance(record, dict):
        record = _record_from_legacy(list(body.get("alerts", [])))
        record["legacy"] = True
    return {**record, "run_id": best[1]}


def _cleared_entry(
    level: str, prior: Mapping[str, Any], meta: Mapping[str, Any], cycle_iso: str
) -> dict[str, Any]:
    return {
        "situation": meta.get("situation"),
        "scope": meta.get("scope"),
        "hotspot_id": meta.get("hotspot_id"),
        "area_desc": meta.get("area_desc"),
        "name": meta.get("name"),
        "level": level,
        "raised_ts": prior.get("raised_ts"),
        "cleared_ts": cycle_iso,
        "persists_cycles": int(prior.get("cycles", 0)),
        "persists_unit": "cycles",
        "state": "cleared",
    }


def apply_cycle_hysteresis(queue: AlertQueue, previous: Mapping[str, Any]) -> AlertQueue:
    """SPEC.md 11.10's rule, per situation and level, against the previous cycle's record.

    For each level: ``P >= RAISE_P`` moves nothing to ``pending``, ``pending`` to ``raised`` and
    keeps ``raised`` raised; ``CLEAR_P < P < RAISE_P`` keeps a raised level raised and drops a
    pending one; ``P <= CLEAR_P`` clears a raised level and drops a pending one. A situation's
    card is its worst raised level; a situation whose worst crossing is still pending is listed
    under ``pending`` so the screen can say "raises next cycle if it holds".
    """
    cycle = queue.cycle_ts
    if cycle is None:
        msg = "apply_cycle_hysteresis needs the queue's cycle_ts; build it with build_alerts"
        raise ValueError(msg)
    cycle_iso = cycle.isoformat()
    order = [level for level, _ in LEVELS]
    prior_situations: Mapping[str, Any] = previous.get("situations", {}) or {}

    record: dict[str, dict[str, Any]] = {}
    raised: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    cleared: list[dict[str, Any]] = []

    for key in sorted(set(queue.candidates) | set(prior_situations)):
        now = queue.candidates.get(key, {})
        prior = (prior_situations.get(key) or {}).get("levels", {}) or {}
        sample = next(iter(now.values()), None)
        meta = {
            "situation": key,
            "scope": (sample or {}).get("scope") or (prior_situations.get(key) or {}).get("scope"),
            "hotspot_id": (sample or {}).get("hotspot_id")
            or (prior_situations.get(key) or {}).get("hotspot_id"),
            "area_desc": (sample or {}).get("area_desc")
            or (prior_situations.get(key) or {}).get("area_desc"),
            # The candidate's own name first: since the locality went into the headline, its
            # prefix reads "Pipeline Road, near Sion Circle" and is no longer the place's name.
            "name": (sample or {}).get("name")
            or (prior_situations.get(key) or {}).get("name")
            or ((sample or {}).get("headline", "").split(":", 1)[0] or None),
        }
        levels: dict[str, dict[str, Any]] = {}
        for level in order:
            candidate = now.get(level)
            p = float(candidate["trigger_p"]) if candidate else 0.0
            before = prior.get(level) or {}
            state = before.get("state")
            if p >= RAISE_P:
                if state == "raised":
                    levels[level] = {
                        **before,
                        "p": p,
                        "cycles": int(before.get("cycles", 1)) + 1,
                    }
                elif state == "pending" and int(before.get("cycles", 1)) + 1 >= RAISE_CYCLES:
                    levels[level] = {
                        "p": p,
                        "state": "raised",
                        "cycles": int(before.get("cycles", 1)) + 1,
                        "since_ts": before.get("since_ts"),
                        "raised_ts": cycle_iso,
                    }
                else:
                    levels[level] = {
                        "p": p,
                        "state": "pending" if RAISE_CYCLES > 1 else "raised",
                        "cycles": 1,
                        "since_ts": cycle_iso,
                        "raised_ts": cycle_iso if RAISE_CYCLES <= 1 else None,
                    }
            elif p > CLEAR_P and state == "raised":
                levels[level] = {**before, "p": p, "cycles": int(before.get("cycles", 1)) + 1}
            elif state == "raised":
                cleared.append(_cleared_entry(level, before, meta, cycle_iso))

        if levels:
            record[key] = {
                "scope": meta["scope"],
                "hotspot_id": meta["hotspot_id"],
                "area_desc": meta["area_desc"],
                "name": meta["name"],
                "levels": levels,
            }

        worst_raised = next(
            (lv for lv in order if levels.get(lv, {}).get("state") == "raised"), None
        )
        if worst_raised is not None and worst_raised in now:
            state = levels[worst_raised]
            card = dict(now[worst_raised])
            card["raised_ts"] = state["raised_ts"]
            card["sent_ts"] = cycle_iso
            card["first_seen_ts"] = state.get("since_ts")
            card["persists_cycles"] = int(state["cycles"])
            card["persists_unit"] = "cycles"
            card["hysteresis"] = {
                "rule": "cycles",
                "raise_p": RAISE_P,
                "clear_p": CLEAR_P,
                "raise_cycles": RAISE_CYCLES,
            }
            raised.append(card)
        worst_pending = next(
            (lv for lv in order if levels.get(lv, {}).get("state") == "pending"), None
        )
        if worst_pending is not None and (
            worst_raised is None or order.index(worst_pending) < order.index(worst_raised)
        ):
            candidate = now[worst_pending]
            pending.append(
                {
                    "id": candidate["id"],
                    "situation": key,
                    "scope": candidate["scope"],
                    "hotspot_id": candidate.get("hotspot_id"),
                    "area_desc": candidate["area_desc"],
                    "level": worst_pending,
                    "threshold_cm": candidate["threshold_cm"],
                    "headline": candidate["headline"],
                    "name": candidate.get("name"),
                    "locality": candidate.get("locality"),
                    "peak_cm": candidate["peak_cm"],
                    "trigger_p": candidate["trigger_p"],
                    "members_above": candidate.get("members_above"),
                    "members_total": candidate.get("members_total"),
                    "since_ts": levels[worst_pending]["since_ts"],
                    "persists_cycles": int(levels[worst_pending]["cycles"]),
                    "persists_unit": "cycles",
                    "state": "pending",
                    # The level the place is already raised at, or None when this would be its
                    # first. The queue is capped at MAX_ALERTS, so a place raised at watch can be
                    # missing from it; without this a screen read "no row" as "not raised yet".
                    "raised_level": worst_raised,
                }
            )

    out = AlertQueue(_sort_queue(raised)[:MAX_ALERTS])
    out.candidates = queue.candidates
    out.cycle_ts = cycle
    out.run_id = queue.run_id
    out.final = True
    level_rank = {level: i for i, level in enumerate(order)}
    # The lists are capped for a reader like the queue is; the counts are not, so a screen can say
    # "and 180 more" rather than implying sixty was all there was.
    out.n_raised = len(raised)
    out.n_raised_by_level = {level: 0 for level in order}
    for card in raised:
        out.n_raised_by_level[card["level"]] += 1
    out.n_pending = len(pending)
    out.n_pending_step_up = sum(1 for p in pending if p["raised_level"] is not None)
    out.n_pending_new = out.n_pending - out.n_pending_step_up
    out.n_cleared = len(cleared)
    # The first onset over every raised alert, not the listed sixty: the cap keeps the worst
    # levels, so an early watch street can fall off the list while being the first to flood.
    earliest = min(
        (c for c in raised if c.get("window_from")),
        key=lambda c: (c["window_from"], level_rank[c["level"]], -c["peak_cm"], c["id"]),
        default=None,
    )
    out.first_onset = (
        {
            "ts": earliest["window_from"],
            "name": earliest.get("name") or earliest.get("area_desc"),
            "level": earliest["level"],
        }
        if earliest is not None
        else None
    )
    # A pending level above a place the queue already shows comes first: the alert centre prints
    # it on that place's row ("Severe next cycle if it holds"), and a cap that dropped it would
    # leave the row saying nothing about the step up. At most one per shown alert, so they fit.
    shown = {(a["scope"], a["area_desc"]) for a in out}
    out.pending = sorted(
        pending,
        key=lambda a: (
            (a["scope"], a["area_desc"]) not in shown,
            level_rank[a["level"]],
            a["scope"] != "hotspot",
            -a["peak_cm"],
            a["id"],
        ),
    )[:MAX_ALERTS]
    out.cleared = sorted(cleared, key=lambda a: (level_rank[a["level"]], str(a["situation"])))[
        :MAX_ALERTS
    ]
    out.record = {
        "version": HYSTERESIS_VERSION,
        "rule": {
            "raise_p": RAISE_P,
            "clear_p": CLEAR_P,
            "raise_cycles": RAISE_CYCLES,
            "min_persist_steps": MIN_PERSIST_STEPS,
            "max_cycle_gap_min": MAX_CYCLE_GAP_MIN,
        },
        "cycle_ts": cycle_iso,
        "previous_run_id": previous.get("run_id"),
        "previous_legacy": bool(previous.get("legacy", False)),
        "situations": record,
    }
    return out


def _worst_state(levels: Mapping[str, Any], state: str) -> str | None:
    return next(
        (lv for lv, _ in LEVELS if (levels.get(lv) or {}).get("state") == state),
        None,
    )


def served_queue(body: Mapping[str, Any]) -> dict[str, Any]:
    """What ``GET /v1/alerts`` serves beside the capped lists, from a written ``alerts.json``.

    The product lists at most :data:`MAX_ALERTS` alerts and pending places for a reader, and a
    screen that counts the lists presents the cap as the cycle: at 08:40 on 2 July the queue
    lists 60 of 213 raised (Severe 13, Moderate 35, Watch 165), and 24 of the 32 pending places
    with no listed row were already raised at a lower level. This returns the uncapped counts and
    the pending list with each entry's ``raised_level``:

    - ``n_raised``, ``n_raised_by_level``: every raised alert, by its worst raised level.
    - ``n_pending_new``, ``n_pending_step_up``: pending places raised at nothing yet, and pending
      levels above a place already raised at a lower one.
    - ``first_onset``: the earliest window over every raised alert, or None when the product did
      not record it - a queue cannot recover it, since the cap may have dropped it.
    - ``pending``: the listed pending entries, each with ``raised_level`` (None when not raised).
    - ``counts_source``: ``"product"`` when the file carries them, ``"hysteresis_record"`` when
      they are rebuilt from the per-situation record a run written before them keeps (exact:
      the record holds every situation, uncapped), ``"listed"`` on a run from before the
      cross-cycle rule, where the list is all there is.
    """
    alerts = list(body.get("alerts") or [])
    pending = [dict(p) for p in body.get("pending") or []]
    record = body.get("hysteresis")
    situations: Mapping[str, Any] = (
        record.get("situations") or {} if isinstance(record, dict) else {}
    )

    if "n_raised_by_level" in body:
        return {
            "n_raised": int(body.get("n_raised", len(alerts))),
            "n_raised_by_level": dict(body["n_raised_by_level"]),
            "n_pending_new": body.get("n_pending_new"),
            "n_pending_step_up": body.get("n_pending_step_up"),
            "first_onset": body.get("first_onset"),
            "pending": pending,
            "counts_source": "product",
        }

    if situations:
        by_level = {level: 0 for level, _ in LEVELS}
        order = [level for level, _ in LEVELS]
        n_new = n_step_up = 0
        for entry in situations.values():
            levels = (entry or {}).get("levels") or {}
            worst_raised = _worst_state(levels, "raised")
            worst_pending = _worst_state(levels, "pending")
            if worst_raised is not None:
                by_level[worst_raised] += 1
            if worst_pending is not None and (
                worst_raised is None or order.index(worst_pending) < order.index(worst_raised)
            ):
                if worst_raised is None:
                    n_new += 1
                else:
                    n_step_up += 1
        for entry in pending:
            levels = (situations.get(str(entry.get("situation"))) or {}).get("levels") or {}
            entry["raised_level"] = _worst_state(levels, "raised")
        return {
            "n_raised": sum(by_level.values()),
            "n_raised_by_level": by_level,
            "n_pending_new": n_new,
            "n_pending_step_up": n_step_up,
            "first_onset": None,
            "pending": pending,
            "counts_source": "hysteresis_record",
        }

    by_level = {level: 0 for level, _ in LEVELS}
    for alert in alerts:
        if alert.get("level") in by_level:
            by_level[alert["level"]] += 1
    return {
        "n_raised": len(alerts),
        "n_raised_by_level": by_level,
        "n_pending_new": None,
        "n_pending_step_up": None,
        "first_onset": None,
        "pending": pending,
        "counts_source": "listed",
    }


# ---- escalation matrix -------------------------------------------------------------------------
ESCALATION_PATH = "config/escalation.yaml"
"""Where the escalation matrix lives, relative to the repository root (SPEC.md 11.10)."""


def load_escalation(path: Path | None = None) -> dict[str, Any] | None:
    """``config/escalation.yaml`` as a dict, or None when the file is not there.

    None rather than a default matrix: a matrix typed into this module would be a second copy of
    the config that nobody edits, and the screen should say the file is missing instead.
    """
    import yaml
    from varuna_schemas.paths import repo_root

    target = path if path is not None else repo_root() / ESCALATION_PATH
    if not target.is_file():
        return None
    body = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    tiers = body.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        msg = f"{target} has no tiers; it must list the escalation matrix in order"
        raise ValueError(msg)
    known = {level for level, _ in LEVELS}
    for tier in tiers:
        if not isinstance(tier, dict) or not tier.get("id"):
            msg = f"{target}: every tier needs an id"
            raise ValueError(msg)
        unknown = set(tier.get("levels") or []) - known
        if unknown:
            msg = f"{target}: tier {tier['id']} names unknown levels {sorted(unknown)}"
            raise ValueError(msg)
    return {"version": body.get("version", 1), "tiers": tiers, "path": ESCALATION_PATH}


def escalation_by_level(path: Path | None = None) -> dict[str, list[str]] | None:
    """Alert level to the tier ids it reaches when raised, in matrix order; None without config."""
    matrix = load_escalation(path)
    if matrix is None:
        return None
    return {
        level: [str(t["id"]) for t in matrix["tiers"] if level in (t.get("levels") or [])]
        for level, _ in LEVELS
    }


def _cap_datetime(value: str) -> str:
    """An ISO timestamp in the one form CAP 1.2 accepts: whole seconds and an explicit offset.

    The CAP 1.2 schema restricts ``sent``, ``onset`` and ``expires`` to the pattern
    ``YYYY-MM-DDThh:mm:ss+hh:mm``. ``datetime.isoformat()`` meets it only by accident: a cycle
    time carrying microseconds writes ``06:40:00.123456+05:30`` and a naive one writes no offset,
    and either document fails validation (``tests/test_cap_schema.py``). Truncating to seconds
    loses nothing a warning needs. A missing offset is refused rather than assumed, because
    guessing IST would put a time on a civil warning that nothing measured.
    """
    moment = datetime.fromisoformat(value)
    if moment.utcoffset() is None:
        raise ValueError(
            f"CAP 1.2 needs an explicit UTC offset on every time, and {value!r} has none. "
            "Pass timezone-aware datetimes (varuna_schemas.constants.IST) to build_alerts."
        )
    return moment.isoformat(timespec="seconds")


def cap_xml(alert: dict[str, Any]) -> str:
    """One alert as a CAP 1.2 document.

    ``status`` is ``Exercise`` for every replay alert (SPEC.md 11.10): the document is valid
    CAP and can be pasted into any CAP reader, and it says on its face that it is a drill.
    Validity is checked against the vendored OASIS schema, not asserted: see
    :func:`varuna_products.schemas.validate_cap`.
    """
    ET.register_namespace("", CAP_NS)
    root = ET.Element(f"{{{CAP_NS}}}alert")

    def child(parent: ET.Element, tag: str, text: str) -> ET.Element:
        node = ET.SubElement(parent, f"{{{CAP_NS}}}{tag}")
        node.text = text
        return node

    child(root, "identifier", alert["id"])
    child(root, "sender", SENDER)
    # `sent` is when *this document* went out. An alert carried across cycles keeps the cycle it
    # was raised on in `raised_ts` for the card, and each cycle's document is sent at that cycle
    # (`sent_ts`); a queue written before the cross-cycle rule has only `raised_ts`, which was
    # the same instant.
    child(root, "sent", _cap_datetime(alert.get("sent_ts") or alert["raised_ts"]))
    child(root, "status", alert.get("cap_status", "Exercise"))
    child(root, "msgType", "Alert")
    child(root, "scope", "Public")

    info = ET.SubElement(root, f"{{{CAP_NS}}}info")
    child(info, "category", "Met")
    child(info, "event", "Street flooding")
    child(info, "urgency", "Expected")
    child(
        info,
        "severity",
        {"severe": "Severe", "moderate": "Moderate", "watch": "Minor"}[alert["level"]],
    )
    # "Likely" and not "Observed": this is a forecast, and CAP has a word for that.
    child(info, "certainty", "Likely")
    child(info, "onset", _cap_datetime(alert["window_from"]))
    child(info, "expires", _cap_datetime(alert["window_to"]))
    child(info, "headline", alert["headline"])
    # Unchanged since the documents in demo/runs were written: the test that holds each of them
    # to this function byte for byte is what makes their validation a statement about the
    # generator (rule 8). A friendlier description lands with those documents regenerated.
    child(info, "description", f"VARUNA nowcast run {alert['run_id']}.")
    if alert.get("instruction"):
        child(info, "instruction", alert["instruction"])

    area = ET.SubElement(info, f"{{{CAP_NS}}}area")
    child(area, "areaDesc", alert["area_desc"])
    if alert.get("lon") is not None and alert.get("lat") is not None:
        # A 500 m circle around the junction: CAP's own shorthand for "about here", and honest
        # about the resolution a 30 m grid supports at a point.
        child(area, "circle", f"{alert['lat']},{alert['lon']} 0.5")

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def write_alerts(
    run_dir: Path,
    alerts: list[dict[str, Any]],
    *,
    runs_root: Path | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Write ``alerts.json`` and one CAP document per alert into the run directory.

    **This is where the cross-cycle rule meets the disk.** Given the :class:`AlertQueue`
    :func:`build_alerts` returns, not yet final, the previous cycle's record is read from
    ``runs_root`` - by default the folder ``run_dir`` sits in, which is the registry's root while
    the cycle writes into its temporary directory - and :func:`apply_cycle_hysteresis` decides
    what is raised. The run id comes from the queue itself unless given. A plain list is written
    as it is, which is what a caller that has already decided (or a test) hands in.

    Returns the queue as written, so a caller can read what was raised.
    """
    queue: list[dict[str, Any]] = alerts
    if isinstance(alerts, AlertQueue) and not alerts.final:
        name = run_id or alerts.run_id
        root = runs_root if runs_root is not None else run_dir.parent
        previous = previous_record(name, root) if name else {}
        queue = apply_cycle_hysteresis(alerts, previous)

    body: dict[str, Any] = {"alerts": list(queue)}
    if isinstance(queue, AlertQueue) and queue.final:
        body["n_raised"] = queue.n_raised
        body["n_raised_by_level"] = queue.n_raised_by_level
        body["first_onset"] = queue.first_onset
        body["pending"] = queue.pending
        body["n_pending"] = queue.n_pending
        body["n_pending_new"] = queue.n_pending_new
        body["n_pending_step_up"] = queue.n_pending_step_up
        body["cleared"] = queue.cleared
        body["n_cleared"] = queue.n_cleared
        body["hysteresis"] = queue.record
    (run_dir / "alerts.json").write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
    if not queue:
        return queue
    folder = run_dir / "alerts"
    folder.mkdir(exist_ok=True)
    for alert in queue:
        (folder / f"{alert['id']}.cap.xml").write_text(cap_xml(alert), encoding="utf-8")
    return queue

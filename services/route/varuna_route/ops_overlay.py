"""Authority edits as a read-time overlay, never a rewrite of a product (task D-06).

A ward officer closing a street is a fact about the world, not a new forecast. If it were
written back into ``data/runs/<run_id>/`` then ``make bake`` would stop being byte-identical
(SPEC.md rule 8, task P5.9) and the run would no longer be the thing the engines produced.
So every authority edit is appended to one file per city, ``data/ops/<city>.jsonl``, and the
router and the road-conditions feed *read* it at request time. A product file is never touched;
a re-bake is unaffected; a restart replays the log.

**Append-only.** Nothing in this module rewrites or truncates the file. A closure is lifted by
appending a ``reopen``, not by deleting the line that made it, so the log is the audit trail the
desk needs. :func:`active` folds the log forward to the state at an instant.

**The contract other chunks write through**::

    append(city, entry) -> dict          # stamps ts and id, appends one JSON line
    active(city, at=None) -> OpsOverlay  # closures + pump status, expiries applied
    OpsOverlay.closed_segment_ids -> frozenset[str]
    OpsOverlay.reason_for(segment_id) -> str | None

``reason_for`` returns the text the officer typed, never a sentence this module composed: the
API returns structured reasons and the frontend words them (TECH_SPEC 3.2).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from varuna_schemas.paths import data_dir

log = structlog.get_logger("varuna.route.ops_overlay")

__all__ = [
    "KINDS",
    "PUMP_STATES",
    "REPORT_STATES",
    "Closure",
    "OpsOverlay",
    "PumpStatus",
    "active",
    "append",
    "entries",
    "overlay_path",
]

IST = timezone(timedelta(hours=5, minutes=30))
"""Every time this repository writes carries an offset (SPEC.md 12); ops entries are IST."""

KINDS = frozenset(
    {
        "closure",
        "reopen",
        "pump_status",
        "alert_ack",
        "alert_escalate",
        "dispatch",
        "report_status",
    }
)
"""Entry kinds the log accepts.

:func:`active` folds the first three; the rest are audit records other screens read, kept in the
same file so one append-only log is the whole history. ``report_status`` is the desk's answer to
a citizen report (received, seen, crew sent, resolved, dismissed); the report itself stays as it
was posted in ``data/reports/inbox.jsonl`` and ``GET /v1/reports`` folds the latest status onto it
at read time, the way the alert queue folds ``alert_ack``."""

PUMP_STATES = frozenset({"available", "unavailable", "moved"})

REPORT_STATES = ("received", "seen", "crew_sent", "resolved", "dismissed")
"""A citizen report's life at the desk, in the order it usually moves. Any may follow any: a
dismissal can be reversed by a later ``seen``, because the log is append-only and later wins."""


def overlay_path(city: str) -> Path:
    """``data/ops/<city>.jsonl``. Never created by reading, only by :func:`append`."""
    if not city or "/" in city or "\\" in city or city in {".", ".."}:
        msg = f"city must be a single path segment, got {city!r}"
        raise ValueError(msg)
    return data_dir() / "ops" / f"{city}.jsonl"


@dataclass(frozen=True, slots=True)
class Closure:
    """One street closed by an authority, with the officer's own words for why."""

    segment_id: str
    reason: str
    user: str
    ts: datetime
    """When the closure was entered - the '08:12' in 'closed by the ward officer at 08:12'."""

    until: datetime | None = None
    """Expiry; ``None`` means until it is reopened."""

    entry_id: str = ""

    def is_active(self, at: datetime) -> bool:
        return self.until is None or self.until > at


@dataclass(frozen=True, slots=True)
class PumpStatus:
    """A pump's availability as the desk last set it."""

    pump_id: str
    status: str
    user: str
    ts: datetime
    lon: float | None = None
    lat: float | None = None
    entry_id: str = ""


@dataclass(frozen=True, slots=True)
class OpsOverlay:
    """The state of a city's authority edits at one instant."""

    city: str
    at: datetime
    closures: dict[str, Closure] = field(default_factory=dict)
    """Active closures, keyed by ``segment_id``."""

    pumps: dict[str, PumpStatus] = field(default_factory=dict)
    """Latest status per pump id."""

    n_entries: int = 0
    """Lines read from the log, active or not - so a response can say the overlay was consulted."""

    @property
    def closed_segment_ids(self) -> frozenset[str]:
        """Segments the router must treat as impassable whatever the forecast says."""
        return frozenset(self.closures)

    def reason_for(self, segment_id: str) -> str | None:
        """The officer's stated reason for a closure, or ``None`` if the segment is open."""
        closure = self.closures.get(segment_id)
        return closure.reason if closure else None

    def unavailable_pump_ids(self) -> frozenset[str]:
        """Pumps the optimiser must not assign."""
        return frozenset(p.pump_id for p in self.pumps.values() if p.status != "available")

    @property
    def empty(self) -> bool:
        return not self.closures and not self.pumps


def _now() -> datetime:
    return datetime.now(IST).replace(microsecond=0)


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)


def append(city: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Append one entry to a city's ops log, stamping ``ts`` and ``id``.

    The stamps are added here rather than by the caller so the log's ordering is the server's and
    two desks cannot disagree about when something happened. An entry that already carries a
    ``ts`` keeps it (replaying a log, or a closure the officer backdated); its ``id`` is always
    minted here.

    Args:
        city: which city's log.
        entry: at least ``kind``; the rest depends on the kind (``segment_id``/``reason`` for a
            closure, ``pump_id``/``status`` for a pump).

    Returns:
        The stored entry, exactly as the line that was written.

    Raises:
        ValueError: unknown ``kind``, or a kind missing the field it is about.
    """
    kind = str(entry.get("kind", "")).strip()
    if kind not in KINDS:
        msg = f"Unknown ops entry kind {kind!r}. Valid kinds: {', '.join(sorted(KINDS))}."
        raise ValueError(msg)
    if kind in {"closure", "reopen"} and not str(entry.get("segment_id", "")).strip():
        msg = f"An ops entry of kind {kind!r} must name the segment_id it applies to."
        raise ValueError(msg)
    if kind == "pump_status":
        if not str(entry.get("pump_id", "")).strip():
            msg = "An ops entry of kind 'pump_status' must name the pump_id it applies to."
            raise ValueError(msg)
        status = str(entry.get("status", "")).strip()
        if status not in PUMP_STATES:
            msg = (
                f"Unknown pump status {status!r}. Valid statuses: {', '.join(sorted(PUMP_STATES))}."
            )
            raise ValueError(msg)

    if kind == "report_status":
        if not str(entry.get("report_id", "")).strip():
            msg = "An ops entry of kind 'report_status' must name the report_id it applies to."
            raise ValueError(msg)
        state = str(entry.get("status", "")).strip()
        if state not in REPORT_STATES:
            msg = f"Unknown report status {state!r}. Valid statuses: {', '.join(REPORT_STATES)}."
            raise ValueError(msg)

    stored = dict(entry)
    stored["kind"] = kind
    stored["id"] = uuid4().hex[:12]
    stored.setdefault("ts", _now().isoformat())
    stored.setdefault("user", "unknown")

    path = overlay_path(city)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(stored, separators=(",", ":"), sort_keys=True, default=str)
    # Opened per write: the log is small, the write is one line, and a handle held open across
    # requests is a handle that outlives a crash with half a line in it.
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    log.info("ops.appended", city=city, kind=kind, entry_id=stored["id"])
    return stored


def entries(city: str) -> list[dict[str, Any]]:
    """Every line of a city's ops log, oldest first; ``[]`` when there is no log.

    A line that will not parse is skipped and logged rather than raising: one corrupt line left
    by a crash must not take the routing endpoint down with it.
    """
    path = overlay_path(city)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            log.warning("ops.unreadable_line", city=city, line=number)
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def active(city: str, at: datetime | None = None) -> OpsOverlay:
    """Fold a city's log forward to the state at an instant.

    Later entries win: a ``reopen`` clears the closure before it, a second ``pump_status``
    replaces the first. A closure whose ``until`` has passed at ``at`` is dropped, which is why
    the overlay is computed per request rather than cached for the process.

    Args:
        city: which city's log.
        at: the instant to evaluate at; defaults to now in IST. Pass the route's departure time
            to ask "what is closed while this vehicle is out".
    """
    when = at or _now()
    closures: dict[str, Closure] = {}
    pumps: dict[str, PumpStatus] = {}
    rows = entries(city)

    for item in rows:
        kind = str(item.get("kind", ""))
        ts = _parse_time(item.get("ts")) or when
        if kind == "closure":
            segment_id = str(item.get("segment_id", "")).strip()
            if not segment_id:
                continue
            closures[segment_id] = Closure(
                segment_id=segment_id,
                reason=str(item.get("reason", "")).strip(),
                user=str(item.get("user", "unknown")),
                ts=ts,
                until=_parse_time(item.get("until")),
                entry_id=str(item.get("id", "")),
            )
        elif kind == "reopen":
            closures.pop(str(item.get("segment_id", "")).strip(), None)
        elif kind == "pump_status":
            pump_id = str(item.get("pump_id", "")).strip()
            if not pump_id:
                continue
            lon, lat = item.get("lon"), item.get("lat")
            pumps[pump_id] = PumpStatus(
                pump_id=pump_id,
                status=str(item.get("status", "available")),
                user=str(item.get("user", "unknown")),
                ts=ts,
                lon=float(lon) if lon is not None else None,
                lat=float(lat) if lat is not None else None,
                entry_id=str(item.get("id", "")),
            )

    live = {sid: c for sid, c in closures.items() if c.is_active(when)}
    return OpsOverlay(city=city, at=when, closures=live, pumps=pumps, n_entries=len(rows))

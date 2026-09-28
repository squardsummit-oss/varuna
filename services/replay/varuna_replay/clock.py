"""The replay clock: walks a bundle's window in simulated time and publishes its streams.

SPEC.md 10.2 asks for a service that "publishes bundle streams to the in-process bus at real
or accelerated speed, exposes play/pause/seek/speed, and triggers a cycle every 5 sim-minutes;
in ``baked`` mode it publishes the pre-computed run for that cycle instead of computing it".
This module is that service. It owns no data: :class:`ReplayStreams` reads a bundle's members
through :mod:`varuna_replay.bundle` and turns them into a schedule of
``(timestamp, topic, payload)``; :class:`ReplayClock` decides *when* each of those is due.

Design notes worth knowing before changing anything here:

Simulated time comes from an anchor, never from accumulated deltas. Every control (play, pause,
seek, speed) re-anchors ``(_anchor_sim, _anchor_mono)``; ``sim_time`` is then
``_anchor_sim + (monotonic() - _anchor_mono) * speed``. A 4-hour replay at 30x therefore ends
exactly 8 wall-clock minutes after it started, however often it was polled, and a slow poll
loses nothing.

The clock publishes, it does not compute. In ``baked`` mode a cycle boundary publishes the
pre-computed run for that instant on ``runs.published``; when no such run exists (Phase 5 bakes
them, task P5.6) it publishes nothing and says so in :attr:`ReplayClock.note`, which the replay
panel shows. In ``live`` mode it calls the ``on_cycle`` handler the orchestrator will pass in;
until Phase 5 there is none, and the note says that too.

Every stream event fires once per clock session. Seeking backwards rewinds the clock but does
not re-publish what the console already has - the requirement is that a cycle never triggers
twice for the same instant. :meth:`ReplayClock.rewind` clears the fired sets for a fresh pass.

Payload rule: small streams travel inline (gauges, tide, reports), large ones travel as a
pointer to the bundle member plus a row count (radar frames, traffic speeds). The bus is an
event bus, not a data channel; Sky and Pulse read the cubes and tables from disk.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import threading
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import structlog
from varuna_cycle.bus import Bus, BusEvent, get_bus
from varuna_cycle.registry import RunRegistry
from varuna_schemas.constants import CYCLE_PERIOD_MIN, IST
from varuna_schemas.models import ReplayClock as ReplayClockState
from varuna_schemas.models import RunMeta
from varuna_schemas.models.bundle import BundleManifest, TideDatum
from varuna_schemas.models.run import RunMode

from varuna_replay.bundle import (
    GAUGES_CSV,
    MANIFEST_NAME,
    RADAR_VARIABLE,
    RADAR_ZARR,
    REPORTS_JSONL,
    TIDE_CSV,
    TRAFFIC_PARQUET,
    Bundle,
    BundleLayout,
    load_bundle,
    load_manifest,
    read_cube_info,
)

log = structlog.get_logger("varuna.replay.clock")

# --------------------------------------------------------------------------- constants
REPLAY_SPEEDS: Final[tuple[float, ...]] = (1.0, 10.0, 30.0, 60.0)
"""Speeds the time bar and the replay panel offer (SPEC.md 7.2)."""

DEFAULT_SPEED: Final[float] = 30.0
"""The demo speed: "Replay 30x" on the mode banner."""

TICK_S: Final[float] = 0.2
"""Wall-clock period of the background poll. At 60x that is 12 simulated seconds per tick,
so no 5-minute cycle boundary can be skipped."""

CLOCK_TOPIC: Final[str] = "replay.clock"
RUNS_TOPIC: Final[str] = "runs.published"

STREAM_TOPICS: Final[tuple[str, ...]] = (
    "radar.frames",
    "gauges.obs",
    "traffic.speeds",
    "reports.raw",
    "tide.stage",
)
"""Bundle streams, in the blueprint's topic names (SPEC.md 11.11)."""

END_NOTE: Final[str] = "The replay reached the end of the window. Press Play to run it again."
LIVE_NOTE: Final[str] = (
    "Live compute lands in Phase 5 (task P5.6). The clock is triggering cycles; "
    "nothing is computing them yet."
)

TimeSource = Callable[[], float]
"""A monotonic clock in seconds. Tests pass a fake one."""

CycleHandler = Callable[["ReplayClock", datetime], Awaitable[Any] | Any]
"""What ``live`` mode calls at a cycle boundary; Phase 5 passes the orchestrator."""


def _iso(value: datetime) -> str:
    return value.astimezone(IST).isoformat()


def _parse_ts(value: Any) -> datetime | None:
    """Read one timestamp from a stream cell; ``None`` when it will not do."""
    if isinstance(value, datetime):
        return value.astimezone(IST) if value.tzinfo else value.replace(tzinfo=IST)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(IST) if parsed.tzinfo else parsed.replace(tzinfo=IST)


def _plain(value: Any) -> Any:
    """A JSON-safe copy of a pandas / numpy scalar (the bus payload is serialised as JSON)."""
    if value is None or isinstance(value, str | bool | int | float):
        return value
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (ValueError, TypeError):  # pragma: no cover - exotic dtypes only
            return str(value)
    return str(value)


def _truthy(value: Any) -> bool:
    """Read a ``synthetic`` cell strictly: a blank is not a claim of being synthetic."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if isinstance(value, int | float):
        return bool(value) if value == value else False  # NaN is not True
    return False


# --------------------------------------------------------------------------- schedule
@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    """One bundle event and the simulated instant it is due at."""

    ts: datetime
    topic: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReplayStreams:
    """A bundle's streams as one time-ordered schedule.

    ``missing`` names the members that are not on disk (a design storm has no gauges, and a
    half-built bundle has nothing); the clock reports them so a silent stream is never
    mistaken for a quiet one.
    """

    events: tuple[ScheduledEvent, ...] = ()
    missing: tuple[str, ...] = ()

    @classmethod
    def empty(cls) -> ReplayStreams:
        return cls()

    @classmethod
    def from_bundle(cls, bundle: Bundle | BundleLayout | str | Path) -> ReplayStreams:
        """Read every stream member of a bundle into a schedule, skipping what is absent."""
        layout = (
            bundle.layout
            if isinstance(bundle, Bundle)
            else bundle
            if isinstance(bundle, BundleLayout)
            else BundleLayout.for_bundle(bundle)
        )
        bundle_id = layout.bundle_id
        events: list[ScheduledEvent] = []
        missing: list[str] = []
        for member, reader in (
            (RADAR_ZARR, _radar_events),
            (GAUGES_CSV, _gauge_events),
            (TIDE_CSV, _tide_events),
            (TRAFFIC_PARQUET, _traffic_events),
            (REPORTS_JSONL, _report_events),
        ):
            path = layout.root / member
            if not path.exists():
                missing.append(member)
                continue
            try:
                events.extend(reader(path, bundle_id))
            except Exception as exc:
                # A half-written member must not stop the clock: report it as absent and let
                # `varuna bundle validate` be the thing that explains what is wrong with it.
                log.warning(
                    "replay.stream_unreadable",
                    member=member,
                    error=f"{type(exc).__name__}: {exc}"[:200],
                )
                missing.append(member)
        order = {topic: i for i, topic in enumerate(STREAM_TOPICS)}
        events.sort(key=lambda event: (event.ts, order.get(event.topic, 99)))
        return cls(events=tuple(events), missing=tuple(missing))

    def counts(self) -> dict[str, int]:
        """Events per topic, for the replay panel and the clock's log line."""
        counts = {topic: 0 for topic in STREAM_TOPICS}
        for event in self.events:
            counts[event.topic] = counts.get(event.topic, 0) + 1
        return counts

    def due(self, sim_time: datetime, skip: Iterable[int] = ()) -> list[tuple[int, ScheduledEvent]]:
        """Indexed events at or before ``sim_time`` that are not in ``skip``."""
        seen = set(skip)
        return [
            (i, event)
            for i, event in enumerate(self.events)
            if event.ts <= sim_time and i not in seen
        ]


def _radar_events(path: Path, bundle_id: str) -> list[ScheduledEvent]:
    """One ``radar.frames`` event per frame, carrying the frame's index, not its pixels."""
    info = read_cube_info(path, RADAR_VARIABLE)
    return [
        ScheduledEvent(
            ts=ts,
            topic="radar.frames",
            payload={
                "bundle": bundle_id,
                "member": RADAR_ZARR,
                "variable": RADAR_VARIABLE,
                "frame_ts": _iso(ts),
                "index": index,
                "n_frames": info.n_times,
                "res_m": info.res_m,
                "n_px": info.n_px,
            },
        )
        for index, ts in enumerate(info.timestamps())
    ]


def _gauge_events(path: Path, bundle_id: str) -> list[ScheduledEvent]:
    """One ``gauges.obs`` event per instant with every station's reading inline."""
    import pandas as pd

    frame = pd.read_csv(path)
    grouped: dict[datetime, list[dict[str, Any]]] = {}
    synthetic: dict[datetime, bool] = {}
    for row in frame.to_dict("records"):
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            continue
        grouped.setdefault(ts, []).append(
            {
                "station_id": _plain(row.get("station_id")),
                "name": _plain(row.get("name")),
                "lat": _plain(row.get("lat")),
                "lon": _plain(row.get("lon")),
                "mm_5min": _plain(row.get("mm_5min")),
                "synthetic": _truthy(row.get("synthetic")),
            }
        )
        synthetic[ts] = synthetic.get(ts, False) or _truthy(row.get("synthetic"))
    return [
        ScheduledEvent(
            ts=ts,
            topic="gauges.obs",
            payload={
                "bundle": bundle_id,
                "member": GAUGES_CSV,
                "ts": _iso(ts),
                "n": len(readings),
                "synthetic": synthetic.get(ts, False),
                "readings": readings,
            },
        )
        for ts, readings in sorted(grouped.items())
    ]


def _tide_datum(tide_csv: Path) -> TideDatum | None:
    """The ``tide_datum`` block of the manifest beside ``tide.csv``, or ``None`` when undeclared.

    A manifest that cannot be read here is reported as undeclared rather than stopping the
    clock; ``varuna bundle validate`` is what explains a broken manifest. A declared block that
    does not validate raises, because publishing a stage with the wrong datum label is worse
    than publishing no tide.
    """
    manifest = tide_csv.parent / MANIFEST_NAME
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    block = payload.get("tide_datum") if isinstance(payload, dict) else None
    return None if block is None else TideDatum.model_validate(block)


def _window_end(tide_csv: Path) -> datetime | None:
    """The manifest's ``t1`` beside ``tide.csv``, or ``None`` when it cannot be read."""
    try:
        payload = json.loads((tide_csv.parent / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _parse_ts(payload.get("t1")) if isinstance(payload, dict) else None


def _tide_events(path: Path, bundle_id: str) -> list[ScheduledEvent]:
    """One ``tide.stage`` event per instant; ``source`` carries the tide table or
    ``illustrative`` so the boundary never claims more than it can (SPEC.md 3.2).

    ``stage_m`` is the series as written, which for a tide table is above chart datum. The
    Twin reads the same series in the DEM's frame (``varuna_twin.city.load_tide``), so every
    event also carries the datum it is in and, when the manifest declares an offset, the stage
    in that frame - a screen that shows the tide cannot then disagree with the physics without
    saying so. ``stage_datum`` is ``None`` when the manifest declares no datum.

    Only the stages up to the manifest's ``t1`` are scheduled. The series runs three hours past
    it (``bundle.TIDE_LOOKAHEAD_MIN``) as the Twin's forecast boundary, but the clock stops at
    ``t1``, so those rows would never be published and would only inflate the replay panel's
    count. A manifest that cannot be read here schedules every row.
    """
    import pandas as pd

    datum = _tide_datum(path)
    offset = datum.offset_to_dem_m if datum is not None else None
    window_end = _window_end(path)
    frame = pd.read_csv(path)
    events: list[ScheduledEvent] = []
    for row in frame.to_dict("records"):
        ts = _parse_ts(row.get("ts"))
        if ts is None or (window_end is not None and ts > window_end):
            continue
        stage = _plain(row.get("stage_m"))
        in_dem_frame = (
            round(float(stage) - offset, 6)
            if offset is not None and isinstance(stage, int | float) and stage == stage
            else None
        )
        events.append(
            ScheduledEvent(
                ts=ts,
                topic="tide.stage",
                payload={
                    "bundle": bundle_id,
                    "member": TIDE_CSV,
                    "ts": _iso(ts),
                    "stage_m": stage,
                    "source": _plain(row.get("source")),
                    "stage_datum": datum.stage_datum if datum is not None else None,
                    "datum": datum.stage_reference if datum is not None else None,
                    "offset_to_dem_m": offset,
                    "stage_dem_frame_m": in_dem_frame,
                },
            )
        )
    return events


def _traffic_events(path: Path, bundle_id: str) -> list[ScheduledEvent]:
    """One ``traffic.speeds`` event per snapshot: a pointer plus the row count.

    A snapshot covers every road segment in the area of interest, so the rows stay in the
    Parquet file and Pulse reads them from there.
    """
    import pandas as pd

    frame = pd.read_parquet(path, columns=["ts"])
    counts: dict[datetime, int] = {}
    for value in frame["ts"].tolist():
        ts = _parse_ts(value)
        if ts is None:
            continue
        counts[ts] = counts.get(ts, 0) + 1
    return [
        ScheduledEvent(
            ts=ts,
            topic="traffic.speeds",
            payload={
                "bundle": bundle_id,
                "member": TRAFFIC_PARQUET,
                "ts": _iso(ts),
                "rows": rows,
                "synthetic": True,
            },
        )
        for ts, rows in sorted(counts.items())
    ]


def _report_events(path: Path, bundle_id: str) -> list[ScheduledEvent]:
    """One ``reports.raw`` event per citizen report, at the instant it was made."""
    import json

    events: list[ScheduledEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            continue
        payload = dict(row)
        payload["ts"] = _iso(ts)
        payload["bundle"] = bundle_id
        payload["member"] = REPORTS_JSONL
        events.append(ScheduledEvent(ts=ts, topic="reports.raw", payload=payload))
    return events


# --------------------------------------------------------------------------- the clock
class ReplayClock:
    """One bundle's simulation clock: play, pause, seek, speed, and the events it publishes.

    The clock is safe to start, seek and stop repeatedly and stops cleanly on shutdown.
    Control methods are coroutines because each of them announces the new state on the bus;
    :meth:`poll` is the one that publishes due stream events and triggers cycles, and the
    background task created by :meth:`start` simply calls it every :data:`TICK_S`.
    """

    def __init__(
        self,
        manifest: BundleManifest,
        *,
        bus: Bus | None = None,
        layout: BundleLayout | None = None,
        registry: RunRegistry | None = None,
        streams: ReplayStreams | None = None,
        speed: float = DEFAULT_SPEED,
        mode: RunMode = "baked",
        time_source: TimeSource = time.monotonic,
        on_cycle: CycleHandler | None = None,
        tick_s: float = TICK_S,
        start_at: datetime | None = None,
        autostart: bool = True,
    ) -> None:
        self.manifest = manifest
        self.layout = layout or BundleLayout.for_bundle(manifest.id)
        self.bus = bus if bus is not None else get_bus()
        self.registry = registry if registry is not None else RunRegistry()
        self._streams = streams
        self._time = time_source
        self._on_cycle = on_cycle
        self._tick_s = max(0.01, float(tick_s))
        self._autostart = autostart
        self._lock = threading.RLock()

        self._speed = self._check_speed(speed)
        self._mode: RunMode = mode
        self._playing = False
        self._anchor_sim = self._clamp(start_at or manifest.t0)
        self._anchor_mono = self._time()
        self._fired_events: set[int] = set()
        self._fired_cycles: set[int] = set()
        self._last_run_id: str | None = None
        self._note: str | None = None
        self._announced_minute: datetime | None = None
        self._task: asyncio.Task[None] | None = None

    # ---- construction ----------------------------------------------------
    @classmethod
    def for_bundle(cls, bundle_id: str | Path, **kwargs: Any) -> ReplayClock:
        """Load ``bundles/<id>/manifest.json`` and build a clock for it.

        Raises :class:`~varuna_replay.bundle.BundleNotFoundError` when the folder or its
        manifest is missing, so the caller can say "run make bundle" rather than guess.
        """
        layout = BundleLayout.for_bundle(bundle_id)
        return cls(load_manifest(layout.root), layout=layout, **kwargs)

    # ---- identity and window ---------------------------------------------
    @property
    def bundle_id(self) -> str:
        return self.manifest.id

    @property
    def t0(self) -> datetime:
        return self.manifest.t0

    @property
    def t1(self) -> datetime:
        return self.manifest.t1

    @property
    def cycle_period(self) -> timedelta:
        return timedelta(minutes=self.manifest.cadences.get("cycle", CYCLE_PERIOD_MIN))

    @property
    def streams(self) -> ReplayStreams:
        """The bundle's schedule, read on first use so ``GET /v1/replay/clock`` stays cheap."""
        with self._lock:
            if self._streams is None:
                self._streams = ReplayStreams.from_bundle(self.layout)
                log.info(
                    "replay.streams_loaded",
                    bundle=self.bundle_id,
                    counts=self._streams.counts(),
                    missing=list(self._streams.missing),
                )
            return self._streams

    # ---- state -----------------------------------------------------------
    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def mode(self) -> RunMode:
        return self._mode

    @property
    def note(self) -> str | None:
        """What the clock wants the operator to know, in UI copy, or ``None``."""
        return self._note

    @property
    def last_run_id(self) -> str | None:
        return self._last_run_id

    @property
    def sim_time(self) -> datetime:
        with self._lock:
            return self._sim_locked()

    @property
    def cycle_index(self) -> int:
        return self._cycle_index_at(self.sim_time)

    @property
    def running(self) -> bool:
        """True while the background poll task is alive."""
        task = self._task
        return task is not None and not task.done()

    # ---- controls --------------------------------------------------------
    async def play(self) -> ReplayClockState:
        """Start advancing. At the end of the window, go back to the start and run it again.

        The clock is shared: one visitor who plays the window to its end must not leave the next
        one parked at 09:40 with a Play button that does nothing.
        """
        with self._lock:
            now = self._sim_locked()
            if now >= self.t1:
                self._rewind_locked()
                now = self.t0
            if not self._playing:
                self._playing = True
                self._note = None
                self._anchor(now)
        if self._autostart:
            await self.start()
        await self.announce()
        return self.snapshot()

    async def pause(self) -> ReplayClockState:
        """Freeze simulated time. Nothing is published while paused."""
        with self._lock:
            if self._playing:
                self._anchor(self._sim_locked())
                self._playing = False
        await self.announce()
        return self.snapshot()

    async def seek(self, when: datetime | str) -> ReplayClockState:
        """Move the clock to ``when``, clamped to the bundle window."""
        target = when if isinstance(when, datetime) else datetime.fromisoformat(str(when))
        with self._lock:
            self._anchor(self._clamp(target))
            self._note = None
        await self.announce()
        return self.snapshot()

    async def set_speed(self, speed: float) -> ReplayClockState:
        """Set the acceleration factor; one of :data:`REPLAY_SPEEDS`."""
        checked = self._check_speed(speed)
        with self._lock:
            self._anchor(self._sim_locked())
            self._speed = checked
        await self.announce()
        return self.snapshot()

    async def set_mode(self, mode: RunMode) -> ReplayClockState:
        """Switch between publishing baked runs and computing live ones."""
        if mode not in ("baked", "live"):
            msg = f"Replay mode must be 'baked' or 'live', got {mode!r}."
            raise ValueError(msg)
        with self._lock:
            self._mode = mode
            self._note = None
        await self.announce()
        return self.snapshot()

    async def rewind(self) -> ReplayClockState:
        """Back to the start of the window for a fresh pass: every stream and cycle fires again."""
        with self._lock:
            self._rewind_locked()
        await self.announce()
        return self.snapshot()

    def _rewind_locked(self) -> None:
        self._playing = False
        self._anchor(self.t0)
        self._fired_events.clear()
        self._fired_cycles.clear()
        self._last_run_id = None
        self._note = None
        self._announced_minute = None

    # ---- the loop --------------------------------------------------------
    async def start(self) -> None:
        """Run :meth:`poll` every tick in the background. Calling it twice is a no-op."""
        if self.running:
            return
        self._task = asyncio.create_task(self._run(), name=f"replay-clock-{self.bundle_id}")

    async def stop(self) -> None:
        """Cancel the background task and freeze the clock. Safe to call when not started."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        with self._lock:
            if self._playing:
                self._anchor(self._sim_locked())
                self._playing = False

    async def _run(self) -> None:
        while True:
            try:
                await self.poll()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a bad frame must not kill the clock
                log.error("replay.poll_failed", bundle=self.bundle_id, error=repr(exc))
            await asyncio.sleep(self._tick_s)

    async def poll(self) -> list[BusEvent]:
        """Publish everything due at the current simulated time. Returns what was published.

        A paused clock publishes nothing at all. A playing one publishes every stream event it
        has passed and, at most, the cycle the clock is inside: pressing Play at 06:40 of a
        window that opens at 05:40 publishes the 06:40 cycle, not the twelve behind it.
        """
        streams = self.streams if self._playing else ReplayStreams.empty()
        with self._lock:
            sim = self._sim_locked()
            if not self._playing:
                return []
            reached_end = sim >= self.t1
            if reached_end:
                self._playing = False
                self._anchor(self.t1)
                sim = self.t1
            due_events = streams.due(sim, skip=self._fired_events)
            for index, _event in due_events:
                self._fired_events.add(index)
            current = self._cycle_index_at(sim)
            due_cycles = [current] if current not in self._fired_cycles else []
            self._fired_cycles.update(range(current + 1))
            minute = sim.replace(second=0, microsecond=0)
            announce = minute != self._announced_minute
            self._announced_minute = minute

        published: list[BusEvent] = []
        for _index, event in due_events:
            published.append(await self.bus.publish(event.topic, event.payload))
        for index in due_cycles:
            published.extend(await self._trigger_cycle(index))
        if reached_end:
            # The last cycle of the window may have set a note of its own; reaching the end
            # is the thing the operator needs to read.
            self._note = END_NOTE
        if announce or due_cycles or reached_end:
            published.append(await self.announce())
        return published

    # ---- cycles ----------------------------------------------------------
    async def _trigger_cycle(self, index: int) -> list[BusEvent]:
        """One cycle boundary: publish the baked run, or call the live handler, or say why not."""
        cycle_ts = self.cycle_ts(index)
        log.info(
            "replay.cycle",
            bundle=self.bundle_id,
            index=index,
            cycle_ts=_iso(cycle_ts),
            mode=self._mode,
        )
        if self._mode == "live":
            if self._on_cycle is None:
                self._note = LIVE_NOTE
                return []
            result = self._on_cycle(self, cycle_ts)
            if inspect.isawaitable(result):
                await result
            return []

        meta = self._baked_run(cycle_ts)
        if meta is None:
            self._note = (
                f"No baked run for {cycle_ts.astimezone(IST):%H:%M} IST. "
                f"Run make bake BUNDLE={self.bundle_id}, or switch the replay to live."
            )
            return []
        self._last_run_id = meta.run_id
        self._note = None
        event = await self.bus.publish(
            RUNS_TOPIC,
            {
                "run_id": meta.run_id,
                "bundle": self.bundle_id,
                "cycle_ts": _iso(cycle_ts),
                "cycle_index": index,
                "mode": "baked",
                "city": meta.city,
            },
            run_id=meta.run_id,
        )
        return [event]

    def _baked_run(self, cycle_ts: datetime) -> RunMeta | None:
        """The run baked for this cycle instant, or ``None`` while nothing is baked."""
        for meta in self.registry.list(bundle=self.bundle_id, limit=10_000):
            if abs((meta.cycle_ts - cycle_ts).total_seconds()) < 1.0:
                return meta
        return None

    def cycle_ts(self, index: int) -> datetime:
        """The simulated instant of cycle ``index`` (cycle 0 is ``t0``)."""
        return self.t0 + self.cycle_period * index

    def next_cycle_ts(self, after: datetime | None = None) -> datetime | None:
        """The next cycle boundary strictly after ``after``, or ``None`` past the window."""
        now = after or self.sim_time
        index = self._cycle_index_at(now) + 1
        candidate = self.cycle_ts(index)
        return candidate if candidate <= self.t1 else None

    def _cycle_index_at(self, sim: datetime) -> int:
        seconds = (sim - self.t0).total_seconds()
        period = self.cycle_period.total_seconds()
        return max(0, int(seconds // period)) if period > 0 else 0

    # ---- publishing ------------------------------------------------------
    async def announce(self) -> BusEvent:
        """Publish the current state on ``replay.clock`` (the console follows this event)."""
        state = self.snapshot()
        return await self.bus.publish(CLOCK_TOPIC, state.model_dump(mode="json"))

    def snapshot(self) -> ReplayClockState:
        """The clock as the API and the console see it."""
        with self._lock:
            sim = self._sim_locked()
            return ReplayClockState(
                bundle_id=self.bundle_id,
                sim_time=sim,
                playing=self._playing,
                speed=self._speed,
                t0=self.t0,
                t1=self.t1,
                cycle_index=self._cycle_index_at(sim),
                n_cycles=self.manifest.n_cycles,
                mode=self._mode,
                last_run_id=self._last_run_id,
                next_cycle_ts=self.next_cycle_ts(sim),
                note=self._note,
            )

    # ---- internals -------------------------------------------------------
    def _sim_locked(self) -> datetime:
        """Simulated time from the anchor. Never accumulates, so it cannot drift."""
        if not self._playing:
            return self._anchor_sim
        elapsed = max(0.0, self._time() - self._anchor_mono)
        sim = self._anchor_sim + timedelta(seconds=elapsed * self._speed)
        return self.t1 if sim >= self.t1 else sim

    def _anchor(self, sim: datetime) -> None:
        self._anchor_sim = self._clamp(sim)
        self._anchor_mono = self._time()

    def _clamp(self, when: datetime) -> datetime:
        value = when if when.tzinfo else when.replace(tzinfo=IST)
        if value < self.t0:
            return self.t0
        if value > self.t1:
            return self.t1
        return value

    @staticmethod
    def _check_speed(speed: float) -> float:
        value = float(speed)
        if value not in REPLAY_SPEEDS:
            offered = ", ".join(f"{s:g}x" for s in REPLAY_SPEEDS)
            msg = f"Replay speed must be one of {offered}, got {value:g}x."
            raise ValueError(msg)
        return value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ReplayClock({self.bundle_id} {_iso(self.sim_time)} "
            f"{'playing' if self._playing else 'paused'} {self._speed:g}x {self._mode})"
        )


def open_clock(bundle_id: str, **kwargs: Any) -> ReplayClock:
    """Build a clock for a bundle on disk, loading its streams eagerly.

    ``make demo`` uses this: the schedule is read once, up front, so the first Play does not
    stall on a Parquet read.
    """
    bundle = load_bundle(bundle_id)
    return ReplayClock(
        bundle.manifest,
        layout=bundle.layout,
        streams=ReplayStreams.from_bundle(bundle),
        **kwargs,
    )


__all__ = [
    "CLOCK_TOPIC",
    "DEFAULT_SPEED",
    "END_NOTE",
    "LIVE_NOTE",
    "REPLAY_SPEEDS",
    "RUNS_TOPIC",
    "STREAM_TOPICS",
    "TICK_S",
    "CycleHandler",
    "ReplayClock",
    "ReplayStreams",
    "ScheduledEvent",
    "TimeSource",
    "open_clock",
]

"""In-process asyncio publish/subscribe bus (SPEC.md 4.2 and 11.11).

The prototype replaces Redis Streams / Redpanda with this module: the topic names are the
blueprint's, so swapping in a broker later is a config change. Every published event is a
:class:`BusEvent` (a :class:`~varuna_schemas.models.LiveEvent` whose ``topic`` may also be a
bus-internal topic such as ``radar.frames``); the API relays the subset in
:data:`~varuna_schemas.constants.WS_TOPICS` to ``WS /v1/live`` unchanged.

Usage::

    bus = get_bus()
    sub = bus.subscribe(["runs.published"])
    await bus.publish("runs.published", {"run_id": run_id}, run_id=run_id)
    async for event in sub: ...
    sub.close()

Engines running in worker threads call :meth:`Bus.publish_threadsafe`.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import Iterable
from concurrent.futures import Future
from datetime import datetime
from typing import Any, Final

import structlog
from varuna_schemas.constants import BUS_TOPICS, IST, WS_TOPICS
from varuna_schemas.models import LiveEvent

log = structlog.get_logger("varuna.bus")

TOPICS: Final[tuple[str, ...]] = tuple(
    dict.fromkeys(
        (
            *BUS_TOPICS,
            "obs.assimilated",
            "replay.clock",
            "onboard.progress",
            "alert.raised",
            "alert.cleared",
            *WS_TOPICS,
        )
    )
)
"""Every topic the bus accepts: the blueprint's stream topics plus the WebSocket events."""

HISTORY_PER_TOPIC: Final[int] = 200
"""Events kept per topic for :meth:`Bus.history` (late subscribers, reconnects)."""

QUEUE_MAXSIZE: Final[int] = 1000
"""Per-subscription buffer; the oldest event is dropped when a consumer falls behind."""


class UnknownTopicError(ValueError):
    """A topic that is not in :data:`TOPICS` (usually a typo)."""


class BusEvent(LiveEvent):
    """A :class:`LiveEvent` that may carry any bus topic, not only the WebSocket ones."""

    topic: str  # type: ignore[assignment]

    @property
    def is_ws_topic(self) -> bool:
        """True when the event belongs on ``WS /v1/live``."""
        return self.topic in WS_TOPICS


class Subscription:
    """An async iterator of :class:`BusEvent` for a set of topics. Close it when done."""

    def __init__(self, bus: Bus, topics: frozenset[str] | None, maxsize: int = QUEUE_MAXSIZE):
        self._bus = bus
        self._topics = topics
        self._queue: asyncio.Queue[BusEvent | None] = asyncio.Queue(maxsize=maxsize)
        self._closed = False
        self.dropped = 0

    @property
    def topics(self) -> frozenset[str] | None:
        """Subscribed topics; ``None`` means every topic."""
        return self._topics

    @property
    def closed(self) -> bool:
        return self._closed

    def matches(self, topic: str) -> bool:
        return self._topics is None or topic in self._topics

    def _deliver(self, event: BusEvent) -> None:
        if self._closed:
            return
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self.dropped += 1
            except asyncio.QueueEmpty:  # pragma: no cover - race only
                pass
        self._queue.put_nowait(event)

    async def get(self, timeout: float | None = None) -> BusEvent | None:
        """Next event, or ``None`` when closed (or when ``timeout`` seconds pass)."""
        if self._closed and self._queue.empty():
            return None
        try:
            if timeout is None:
                return await self._queue.get()
            return await asyncio.wait_for(self._queue.get(), timeout)
        except TimeoutError:
            return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._bus._unsubscribe(self)
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            self._queue.get_nowait()
            self._queue.put_nowait(None)

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> BusEvent:
        if self._closed and self._queue.empty():
            raise StopAsyncIteration
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()


class Bus:
    """Topic-based fan-out to in-process subscribers with a short per-topic history."""

    def __init__(self, history_per_topic: int = HISTORY_PER_TOPIC):
        self._subs: set[Subscription] = set()
        self._history: dict[str, deque[BusEvent]] = {
            topic: deque(maxlen=history_per_topic) for topic in TOPICS
        }
        self._seq = 0
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- loop binding ----------------------------------------------------
    def bind(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Remember the event loop that owns the subscribers (called from the API lifespan)."""
        self._loop = loop or asyncio.get_running_loop()

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    # ---- publish ---------------------------------------------------------
    @staticmethod
    def check_topic(topic: str) -> str:
        if topic not in TOPICS:
            msg = f"Unknown bus topic {topic!r}; expected one of {', '.join(TOPICS)}"
            raise UnknownTopicError(msg)
        return topic

    def _make_event(self, topic: str, payload: dict[str, Any], run_id: str | None) -> BusEvent:
        with self._lock:
            self._seq += 1
            seq = self._seq
        return BusEvent(
            topic=topic, ts=datetime.now(IST), payload=dict(payload), run_id=run_id, seq=seq
        )

    def _fan_out(self, event: BusEvent) -> int:
        self._history[event.topic].append(event)
        delivered = 0
        for sub in tuple(self._subs):
            if sub.matches(event.topic):
                sub._deliver(event)
                delivered += 1
        return delivered

    async def publish(
        self, topic: str, payload: dict[str, Any] | None = None, run_id: str | None = None
    ) -> BusEvent:
        """Publish from the event loop that owns the subscribers; returns the event sent."""
        self.check_topic(topic)
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        event = self._make_event(topic, payload or {}, run_id)
        n = self._fan_out(event)
        log.debug("bus.publish", topic=topic, seq=event.seq, subscribers=n)
        return event

    def publish_threadsafe(
        self,
        topic: str,
        payload: dict[str, Any] | None = None,
        run_id: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> Future[BusEvent]:
        """Publish from a worker thread. Uses the bound loop unless ``loop`` is given."""
        self.check_topic(topic)
        target = loop or self._loop
        if target is None:
            msg = (
                "Bus is not bound to an event loop. Call get_bus().bind() from the API "
                "lifespan or pass loop=... to publish_threadsafe()."
            )
            raise RuntimeError(msg)
        return asyncio.run_coroutine_threadsafe(self.publish(topic, payload, run_id), target)

    # ---- subscribe -------------------------------------------------------
    def subscribe(
        self, topics: Iterable[str] | None = None, maxsize: int = QUEUE_MAXSIZE
    ) -> Subscription:
        """Subscribe to ``topics`` (``None`` = all). Must be called inside a running loop."""
        wanted = None if topics is None else frozenset(self.check_topic(t) for t in topics)
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        sub = Subscription(self, wanted, maxsize=maxsize)
        self._subs.add(sub)
        return sub

    def _unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    # ---- history ---------------------------------------------------------
    def history(self, topic: str, n: int = 50) -> list[BusEvent]:
        """The last ``n`` events on ``topic``, oldest first."""
        self.check_topic(topic)
        items = self._history[topic]
        return list(items)[-n:] if n > 0 else []

    def last(self, topic: str) -> BusEvent | None:
        events = self.history(topic, 1)
        return events[0] if events else None

    def clear(self) -> None:
        """Drop history and close every subscription (tests, bundle switch)."""
        for sub in tuple(self._subs):
            sub.close()
        for items in self._history.values():
            items.clear()


_BUS: Bus | None = None
_BUS_LOCK = threading.Lock()


def get_bus() -> Bus:
    """The process-wide bus."""
    global _BUS
    with _BUS_LOCK:
        if _BUS is None:
            _BUS = Bus()
        return _BUS


def reset_bus() -> Bus:
    """Replace the singleton with a fresh bus (tests)."""
    global _BUS
    with _BUS_LOCK:
        if _BUS is not None:
            _BUS.clear()
        _BUS = Bus()
        return _BUS


__all__ = [
    "HISTORY_PER_TOPIC",
    "QUEUE_MAXSIZE",
    "TOPICS",
    "Bus",
    "BusEvent",
    "Subscription",
    "UnknownTopicError",
    "get_bus",
    "reset_bus",
]

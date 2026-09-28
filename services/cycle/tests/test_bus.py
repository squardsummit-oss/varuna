from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future

import pytest
from varuna_cycle.bus import TOPICS, Bus, BusEvent, UnknownTopicError, get_bus, reset_bus
from varuna_schemas.constants import BUS_TOPICS, WS_TOPICS
from varuna_schemas.models import LiveEvent


def test_topics_cover_blueprint_and_websocket() -> None:
    for topic in (*BUS_TOPICS, *WS_TOPICS, "alert.raised", "alert.cleared", "onboard.progress"):
        assert topic in TOPICS
    assert len(TOPICS) == len(set(TOPICS))


async def test_publish_delivers_live_event_to_subscriber() -> None:
    bus = Bus()
    sub = bus.subscribe(["runs.published"])
    sent = await bus.publish("runs.published", {"run_id": "r1"}, run_id="r1")
    got = await sub.get(timeout=1)
    assert got is sent
    assert isinstance(got, LiveEvent)
    assert isinstance(got, BusEvent)
    assert got.topic == "runs.published"
    assert got.payload == {"run_id": "r1"}
    assert got.run_id == "r1"
    assert got.seq == 1
    assert got.ts.utcoffset() is not None
    sub.close()
    assert bus.subscriber_count == 0


async def test_subscription_filters_topics_and_none_means_all() -> None:
    bus = Bus()
    only_alerts = bus.subscribe(["alert.raised"])
    everything = bus.subscribe()
    await bus.publish("cycle.stage", {"stage": "sky"})
    await bus.publish("alert.raised", {"id": "a1"})
    assert (await only_alerts.get(timeout=1)).topic == "alert.raised"
    assert [
        e.topic for e in [await everything.get(timeout=1), await everything.get(timeout=1)]
    ] == [
        "cycle.stage",
        "alert.raised",
    ]
    only_alerts.close()
    everything.close()


async def test_async_iteration_stops_on_close() -> None:
    bus = Bus()
    sub = bus.subscribe(["replay.clock"])
    await bus.publish("replay.clock", {"sim_time": "17:40"})
    await bus.publish("replay.clock", {"sim_time": "17:45"})
    sub.close()
    seen = [event.payload["sim_time"] async for event in sub]
    assert seen == ["17:40", "17:45"]


async def test_history_keeps_order_and_limit() -> None:
    bus = Bus()
    for i in range(5):
        await bus.publish("cycle.stage", {"i": i})
    assert [e.payload["i"] for e in bus.history("cycle.stage")] == [0, 1, 2, 3, 4]
    assert [e.payload["i"] for e in bus.history("cycle.stage", n=2)] == [3, 4]
    assert bus.history("alerts") == []
    assert bus.last("cycle.stage") is not None


async def test_unknown_topic_is_rejected() -> None:
    bus = Bus()
    with pytest.raises(UnknownTopicError):
        await bus.publish("radar.frame", {})
    with pytest.raises(UnknownTopicError):
        bus.subscribe(["nope"])


async def test_slow_consumer_drops_oldest() -> None:
    bus = Bus()
    sub = bus.subscribe(["gauges.obs"], maxsize=2)
    for i in range(4):
        await bus.publish("gauges.obs", {"i": i})
    assert sub.dropped == 2
    assert (await sub.get(timeout=1)).payload["i"] == 2
    sub.close()


async def test_publish_threadsafe_from_worker_thread() -> None:
    bus = Bus()
    bus.bind()
    sub = bus.subscribe(["obs.assimilated"])
    futures: list[Future[BusEvent]] = []

    def worker() -> None:
        futures.append(bus.publish_threadsafe("obs.assimilated", {"from": "thread"}))

    thread = threading.Thread(target=worker)
    thread.start()
    got = await sub.get(timeout=2)
    await asyncio.get_running_loop().run_in_executor(None, thread.join)
    sent = await asyncio.wrap_future(futures[0])
    assert got is sent
    assert got.payload == {"from": "thread"}
    sub.close()


def test_publish_threadsafe_requires_a_loop() -> None:
    bus = Bus()
    with pytest.raises(RuntimeError, match="not bound"):
        bus.publish_threadsafe("cycle.stage", {})


def test_singleton_and_reset() -> None:
    first = get_bus()
    assert get_bus() is first
    fresh = reset_bus()
    assert fresh is not first
    assert get_bus() is fresh


async def test_clear_closes_subscriptions() -> None:
    bus = Bus()
    sub = bus.subscribe()
    await bus.publish("tide.stage", {"m": 1.2})
    bus.clear()
    assert sub.closed
    assert bus.history("tide.stage") == []
    await asyncio.sleep(0)

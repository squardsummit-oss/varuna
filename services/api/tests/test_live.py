from __future__ import annotations

import time

from fastapi.testclient import TestClient
from varuna_cycle.bus import Bus
from varuna_schemas.constants import WS_TOPICS


def test_hello_then_ping_pong(client: TestClient) -> None:
    with client.websocket_connect("/v1/live") as ws:
        hello = ws.receive_json()
        assert hello["topic"] == "hello"
        assert hello["ts"].endswith("+05:30")
        assert hello["payload"]["version"] == "0.1.0"
        assert hello["payload"]["mode"] == "replay"
        assert hello["payload"]["city"] == "mumbai"
        assert hello["payload"]["bundle"] == "MUM-2019-07-02"
        assert hello["payload"]["topics"] == list(WS_TOPICS)
        ws.send_json({"type": "ping", "id": 7})
        pong = ws.receive_json()
        assert pong["type"] == "pong"
        assert pong["echo"] == 7


def test_bus_events_are_relayed_as_live_events(client: TestClient, bus: Bus) -> None:
    with client.websocket_connect("/v1/live") as ws:
        assert ws.receive_json()["topic"] == "hello"
        deadline = time.monotonic() + 2
        while bus.subscriber_count == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert bus.subscriber_count == 1
        # A bus-internal topic must not reach the socket; a WS topic must.
        bus.publish_threadsafe("radar.frames", {"frame": 1}).result(timeout=2)
        bus.publish_threadsafe("runs.published", {"run_id": "r1"}, run_id="r1").result(timeout=2)
        event = ws.receive_json()
        assert event["topic"] == "runs.published"
        assert event["payload"] == {"run_id": "r1"}
        assert event["run_id"] == "r1"
        assert event["seq"] == 2
        assert event["ts"].endswith("+05:30")
    deadline = time.monotonic() + 2
    while bus.subscriber_count and time.monotonic() < deadline:
        time.sleep(0.01)
    assert bus.subscriber_count == 0


def test_heartbeat_arrives(client: TestClient) -> None:
    with client.websocket_connect("/v1/live") as ws:
        assert ws.receive_json()["topic"] == "hello"
        beat = ws.receive_json()
        assert beat["topic"] == "heartbeat"
        assert beat["payload"]["uptime_s"] >= 0

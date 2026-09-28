"""Alert centre and pump board endpoints closed in P8.8 / P8.10 (SPEC.md 7.5, 7.6).

Against a temporary data and city dir, like ``test_ops.py``: the escalation matrix served from
``config/escalation.yaml``, the sender switch that decides whether "Send to my phone" exists, the
delivery log that never calls a mock a delivery, ``GET /v1/alerts`` reflecting an acknowledgement
after a reload, a hand-made pump plan priced by the API, and a dispatch that produces the alert
instruction and the phone message. No test here sends a real message: there are no keys, and
the one test that exercises a "configured" sender patches the transport.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from varuna_api.routers import ops
from varuna_schemas.constants import IST

RUN_ID = "MUM-20190702T1200Z-sky1.0-twin1.0-flash0.1-baked"
PASSPHRASE = "monsoon desk 2026"
AUTH = {ops.OPS_HEADER: PASSPHRASE}
T0 = datetime(2019, 7, 2, 17, 30, tzinfo=IST)
HINDMATA_ALERT = "VARUNA-MUM-TEST-HINDMATA-SEVERE"
STREET_ALERT = "VARUNA-MUM-TEST-STREET-0001-WATCH"
SENDER_KEYS = (
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM",
    "TWILIO_TO",
    "WHATSAPP_CLOUD_TOKEN",
    "WHATSAPP_CLOUD_PHONE_ID",
    "WHATSAPP_CLOUD_TO",
)


def _flooding() -> list[float]:
    return [10.0] * 6 + [52.0] * 24 + [10.0] * 6


def _alert(alert_id: str, level: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": alert_id,
        "run_id": RUN_ID,
        "scope": "hotspot",
        "hotspot_id": "MUM-HS-01",
        "level": level,
        "threshold_cm": {"severe": 45, "moderate": 30, "watch": 15}[level],
        "headline": "Hindmata junction: depth above 45 cm from 18:00 to 20:00",
        "instruction": "Avoid Hindmata junction for the window. Peak forecast 52 cm.",
        "area_desc": "Ward F/S, Hindmata junction",
        "trigger_p": 1.0,
        "state": "raised",
        "raised_ts": T0.isoformat(),
        "sent_ts": T0.isoformat(),
        "persists_cycles": 2,
        "persists_unit": "cycles",
        "notify": ["ward_officer", "control_room", "police_traffic"],
        "cap_status": "Exercise",
        **extra,
    }


@pytest.fixture
def desk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    data, city = tmp_path / "data", tmp_path / "city" / "mumbai"
    monkeypatch.setenv("VARUNA_DATA_DIR", str(data))
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path / "city"))
    monkeypatch.setenv(ops.PASSPHRASE_ENV, PASSPHRASE)
    for key in SENDER_KEYS:
        monkeypatch.delenv(key, raising=False)
    ops.reset_rate_limit()

    run = data / "runs" / RUN_ID
    (run / "depth").mkdir(parents=True)
    (run / "depth" / "bounds.json").write_text("{}", encoding="utf-8")
    (run / "run.json").write_text(
        json.dumps({"run_id": RUN_ID, "city": "mumbai", "cycle_ts": T0.isoformat(), "notes": []}),
        encoding="utf-8",
    )
    street = {
        **_alert(STREET_ALERT, "watch"),
        "scope": "segment",
        "hotspot_id": None,
        "area_desc": "Dr Ambedkar Road",
        "headline": "Dr Ambedkar Road: depth above 15 cm from 18:00 to 19:00",
    }
    (run / "alerts.json").write_text(
        json.dumps(
            {
                "alerts": [_alert(HINDMATA_ALERT, "severe"), street],
                "pending": [{"id": "P1", "level": "severe", "state": "pending"}],
                "n_pending": 1,
                "cleared": [],
                "n_cleared": 0,
                "hysteresis": {
                    "rule": {"raise_p": 0.6, "clear_p": 0.3, "raise_cycles": 2},
                    "previous_run_id": "MUM-20190702T1130Z-sky1.0-twin1.0-flash0.1-baked",
                    "situations": {},
                },
            }
        ),
        encoding="utf-8",
    )
    (run / "hotspots.json").write_text(
        json.dumps(
            [
                {
                    "hotspot_id": "MUM-HS-01",
                    "name": "Hindmata junction",
                    "lon": 72.841,
                    "lat": 19.012,
                    "segment_ids": ["S-NOT-IN-THE-EMULATOR"],
                    "exposure": {"weight": 0.9},
                    "depth_cm": _flooding(),
                }
            ]
        ),
        encoding="utf-8",
    )
    (run / "segments_wet.json").write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "valid_ts": [(T0 + timedelta(minutes=5 * k)).isoformat() for k in range(36)],
                "depth_cm": {},
                "p_gt": {},
            }
        ),
        encoding="utf-8",
    )
    city.mkdir(parents=True)
    (city / "assets.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [lon, lat]},
                        "properties": {
                            "asset_id": pid,
                            "kind": "mobile_pump",
                            "capacity_m3_per_h": 2400.0,
                            "depot": depot,
                            "status": "available",
                            "synthetic": True,
                        },
                    }
                    for pid, depot, lon, lat in (
                        ("P-01", "Parel depot", 72.838, 19.005),
                        ("P-02", "Dadar depot", 72.848, 19.020),
                    )
                ],
            }
        ),
        encoding="utf-8",
    )
    yield tmp_path
    ops.reset_rate_limit()


# ---- the alert queue --------------------------------------------------------------------
def test_alerts_carry_the_cross_cycle_state(desk: Path, client: TestClient) -> None:
    body = client.get("/v1/alerts", params={"run_id": RUN_ID}).json()
    assert body["n_pending"] == 1 and body["pending"][0]["state"] == "pending"
    assert body["hysteresis"]["rule"]["raise_cycles"] == 2
    assert "situations" not in body["hysteresis"], (
        "the record is the next cycle's, not the screen's"
    )
    assert body["alerts"][0]["persists_unit"] == "cycles"


def test_a_legacy_queue_says_it_decided_on_one_cycle(desk: Path, client: TestClient) -> None:
    record = desk / "data" / "runs" / RUN_ID / "alerts.json"
    record.write_text(json.dumps({"alerts": [_alert(HINDMATA_ALERT, "severe")]}), encoding="utf-8")
    body = client.get("/v1/alerts", params={"run_id": RUN_ID}).json()
    assert body["hysteresis"] is None and body["pending"] == []
    assert any("two consecutive cycles" in n for n in body["notes"])


def test_an_acknowledgement_survives_a_reload_of_get_alerts(desk: Path, client: TestClient) -> None:
    """D-07's open criterion: the queue itself, not only the desk's view, shows who saw it."""
    ack = client.post(
        f"/v1/alerts/{HINDMATA_ALERT}/ack",
        params={"run_id": RUN_ID},
        json={"user": "ward officer F/S", "city": "mumbai"},
        headers=AUTH,
    )
    assert ack.status_code == 200, ack.text
    for _ in range(2):  # a reload is a fresh read of the same endpoint
        alert = next(
            a
            for a in client.get("/v1/alerts", params={"run_id": RUN_ID}).json()["alerts"]
            if a["id"] == HINDMATA_ALERT
        )
        assert alert["state"] == "acknowledged"
        assert alert["acknowledged_by"] == "ward officer F/S"
        assert alert["acknowledged_ts"], "logged with the time"
    escalate = client.post(
        f"/v1/alerts/{HINDMATA_ALERT}/escalate",
        params={"run_id": RUN_ID},
        json={"user": "control room", "city": "mumbai", "escalate_to": "transit"},
        headers=AUTH,
    )
    assert escalate.status_code == 200, escalate.text
    alert = client.get("/v1/alerts", params={"run_id": RUN_ID}).json()["alerts"][0]
    assert alert["state"] == "escalated" and alert["escalated_to"] == "transit"
    assert [h["user"] for h in alert["history"]] == ["ward officer F/S", "control room"]


# ---- escalation matrix --------------------------------------------------------------------
def test_the_escalation_matrix_is_served_from_config(desk: Path, client: TestClient) -> None:
    body = client.get("/v1/alerts/escalation").json()
    assert body["path"] == "config/escalation.yaml"
    assert [t["id"] for t in body["tiers"]] == [
        "ward_officer",
        "control_room",
        "police_traffic",
        "transit",
        "public",
    ]


def test_a_missing_matrix_says_where_it_should_be(
    desk: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("varuna_products.alerts.ESCALATION_PATH", "config/absent.yaml")
    res = client.get("/v1/alerts/escalation")
    assert res.status_code == 404 and res.json()["error"]["code"] == "no_escalation_config"


# ---- the sender and the delivery log -------------------------------------------------------
def test_no_sender_means_no_button_and_a_named_refusal(desk: Path, client: TestClient) -> None:
    sender = client.get("/v1/alerts/sender").json()
    assert sender["configured"] is False and "TWILIO_TO" in sender["needs"]["twilio"]
    res = client.post(
        f"/v1/alerts/{HINDMATA_ALERT}/send",
        params={"run_id": RUN_ID},
        json={"city": "mumbai"},
        headers=AUTH,
    )
    assert res.status_code == 503 and res.json()["error"]["code"] == "no_sender"
    assert not (desk / "data" / "ops" / "mumbai.deliveries.jsonl").exists()


def test_the_delivery_log_never_calls_a_mock_a_delivery(desk: Path, client: TestClient) -> None:
    body = client.get("/v1/alerts/delivery", params={"run_id": RUN_ID}).json()
    assert body["n_real"] == 0 and body["sender"]["configured"] is False
    statuses = {r["status"] for r in body["rows"]}
    assert statuses == {
        "Shown on the alert queue",
        "Shown on the on-screen phone",
        "Rendered, not sent",
    }
    assert not any("deliver" in r["status"].lower() or r["status"] == "Sent" for r in body["rows"])
    assert any("no message has been sent" in n for n in body["notes"])


def test_a_configured_sender_sends_and_the_log_records_it(
    desk: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A *fake* provider: the transport is patched, so nothing leaves the machine."""
    from varuna_products import notify

    for key, value in {
        "TWILIO_ACCOUNT_SID": "AC0000test",
        "TWILIO_AUTH_TOKEN": "secret-token",
        "TWILIO_FROM": "whatsapp:+14155238886",
        "TWILIO_TO": "+919820012345",
    }.items():
        monkeypatch.setenv(key, value)
    calls: list[str] = []

    def fake(method: str, url: str, headers: Any, body: bytes) -> tuple[int, bytes]:
        calls.append(url)
        return 201, json.dumps({"sid": "SMfake"}).encode()

    monkeypatch.setattr(notify, "_urllib_transport", fake)
    assert client.get("/v1/alerts/sender").json()["to_masked"] == "whatsapp:+91******2345"
    res = client.post(
        f"/v1/alerts/{HINDMATA_ALERT}/send",
        params={"run_id": RUN_ID},
        json={"city": "mumbai", "user": "ward officer F/S"},
        headers=AUTH,
    )
    assert res.status_code == 200, res.text
    assert res.json()["delivery"]["status"] == "sent" and len(calls) == 1
    log = client.get("/v1/alerts/delivery", params={"run_id": RUN_ID}).json()
    real = [r for r in log["rows"] if r["kind"] == "real"]
    assert [(r["status"], r["provider_id"], r["user"]) for r in real] == [
        ("Sent", "SMfake", "ward officer F/S")
    ]


def test_sending_is_gated(desk: Path, client: TestClient) -> None:
    res = client.post(
        f"/v1/alerts/{HINDMATA_ALERT}/send", params={"run_id": RUN_ID}, json={"city": "mumbai"}
    )
    assert res.status_code == 401


# ---- pumps ----------------------------------------------------------------------------------
def test_a_hand_made_plan_is_priced_by_the_api(desk: Path, client: TestClient) -> None:
    res = client.post(
        "/v1/pumps/price",
        json={"run_id": RUN_ID, "placements": [{"pump_id": "P-02", "hotspot_id": "MUM-HS-01"}]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["targets"][0]["minutes_before"] == 120
    assert body["placements"][0]["minutes_saved"] > 0
    assert body["benefit_label"] and body["inventory"] == "synthetic"
    assert isinstance(body["price_ms"], int)


def test_a_bad_placement_is_a_422(desk: Path, client: TestClient) -> None:
    res = client.post(
        "/v1/pumps/price", json={"run_id": RUN_ID, "placements": [{"pump_id": "P-01"}]}
    )
    assert res.status_code == 422


def test_dispatch_produces_the_alert_instruction_and_the_phone_message(
    desk: Path, client: TestClient
) -> None:
    res = client.post(
        "/v1/pumps/dispatch",
        json={"run_id": RUN_ID, "user": "control room", "city": "mumbai"},
        headers=AUTH,
    )
    assert res.status_code == 202, res.text
    body = res.json()
    pumps = [o["pump_id"] for o in body["orders"]]
    assert body["alert_instructions"] == [f"Pump {pumps[0]} dispatched."]
    message = body["phone_messages"][0]
    assert message["alert_id"] == HINDMATA_ALERT
    assert f"Pump {pumps[0]} dispatched." in message["text"]
    # And the queue carries it from now on, so /alerts' phone says it too.
    alert = client.get("/v1/alerts", params={"run_id": RUN_ID}).json()["alerts"][0]
    assert alert["pumps"] == pumps and alert["dispatch_note"] == f"Pump {pumps[0]} dispatched."

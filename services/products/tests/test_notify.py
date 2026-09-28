"""The real sender behind env keys (task P8.8, SPEC.md 7.5 AC3).

No keys exist on the machine these tests run on, and no message is ever sent from them: every
adapter is driven against a fake transport that records the request and answers as the provider
would. The tests pin what the adapter asks the provider for and, above all, that nothing is called
``sent`` without the provider's own message id.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
from varuna_products import notify
from varuna_products.notify import (
    configured_sender,
    deliveries,
    mask_number,
    record_delivery,
    send_alert,
    sms_text,
    whatsapp_text,
)

ALERT: dict[str, Any] = {
    "id": "VARUNA-MUM-20190702T0310Z-HINDMATA-SEVERE",
    "run_id": "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
    "level": "severe",
    "headline": "Hindmata junction: depth above 45 cm from 08:45 to 09:40",
    "instruction": "Avoid Hindmata junction for the window. Peak forecast 61 cm.",
    "area_desc": "Ward F/S, Hindmata junction",
    "cap_status": "Exercise",
}

TWILIO_ENV = {
    "TWILIO_ACCOUNT_SID": "AC0000test",
    "TWILIO_AUTH_TOKEN": "secret-token",
    "TWILIO_FROM": "whatsapp:+14155238886",
    "TWILIO_TO": "+919820012345",
}

CLOUD_ENV = {
    "WHATSAPP_CLOUD_TOKEN": "EAAG-test",
    "WHATSAPP_CLOUD_PHONE_ID": "1234567890",
    "WHATSAPP_CLOUD_TO": "919820012345",
}


class FakeTransport:
    """Records every request; answers with the status and body it was given."""

    def __init__(self, status: int, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body or {}
        self.calls: list[tuple[str, str, dict[str, str], bytes]] = []

    def __call__(self, method: str, url: str, headers: Any, body: bytes) -> tuple[int, bytes]:
        self.calls.append((method, url, dict(headers), body))
        return self.status, json.dumps(self.body).encode()


def test_no_keys_no_sender() -> None:
    assert configured_sender({}) is None
    partial = dict(TWILIO_ENV)
    partial["TWILIO_TO"] = " "
    assert configured_sender(partial) is None, "a sender missing one key is not configured"


def test_twilio_whatsapp_is_chosen_and_masks_the_number() -> None:
    sender = configured_sender({**TWILIO_ENV, **CLOUD_ENV})
    assert sender is not None
    assert (sender.provider, sender.channel) == ("twilio", "whatsapp")
    assert sender.to == "whatsapp:+919820012345"
    public = sender.public()
    assert public["to_masked"] == "whatsapp:+91******2345"
    assert "secret-token" not in json.dumps(public) and "9820012345" not in json.dumps(public)


def test_a_bare_twilio_from_number_is_sms() -> None:
    sender = configured_sender({**TWILIO_ENV, "TWILIO_FROM": "+15005550006"})
    assert sender is not None and sender.channel == "sms" and sender.to == "+919820012345"


def test_without_a_sender_nothing_is_called_and_nothing_is_claimed() -> None:
    transport = FakeTransport(201, {"sid": "SM1"})
    delivery = send_alert(ALERT, config=configured_sender({}), transport=transport)
    assert delivery.status == "refused" and not delivery.sent
    assert "TWILIO_ACCOUNT_SID" in (delivery.error or "")
    assert transport.calls == []


def test_twilio_request_shape_and_a_sent_answer() -> None:
    sender = configured_sender(TWILIO_ENV)
    transport = FakeTransport(201, {"sid": "SM0123456789", "status": "queued"})
    delivery = send_alert(
        ALERT, config=sender, transport=transport, dispatch_note="Pumps P-05 dispatched."
    )
    assert delivery.sent and delivery.provider_id == "SM0123456789"
    method, url, headers, body = transport.calls[0]
    assert method == "POST"
    assert url == "https://api.twilio.com/2010-04-01/Accounts/AC0000test/Messages.json"
    assert (
        headers["Authorization"] == "Basic " + base64.b64encode(b"AC0000test:secret-token").decode()
    )
    form = urllib.parse.parse_qs(body.decode())
    assert form["From"] == ["whatsapp:+14155238886"]
    assert form["To"] == ["whatsapp:+919820012345"]
    assert "Pumps P-05 dispatched." in form["Body"][0]
    assert "(exercise)" in form["Body"][0]


def test_whatsapp_cloud_request_shape() -> None:
    sender = configured_sender(CLOUD_ENV)
    assert sender is not None and sender.provider == "whatsapp_cloud"
    transport = FakeTransport(200, {"messages": [{"id": "wamid.HBgM"}]})
    delivery = send_alert(ALERT, config=sender, transport=transport)
    assert delivery.sent and delivery.provider_id == "wamid.HBgM"
    _, url, headers, body = transport.calls[0]
    assert url == "https://graph.facebook.com/v20.0/1234567890/messages"
    assert headers["Authorization"] == "Bearer EAAG-test"
    payload = json.loads(body)
    assert payload["to"] == "919820012345" and payload["type"] == "text"
    assert payload["text"]["body"].startswith("VARUNA severe alert (exercise)")


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (401, {"message": "Authenticate"}, "HTTP 401"),
        (200, {}, "without a message id"),
        (400, {"error": {"message": "Recipient not in allowed list"}}, "not in allowed list"),
    ],
)
def test_a_provider_answer_without_an_id_is_never_sent(
    status: int, body: dict, reason: str
) -> None:
    delivery = send_alert(
        ALERT, config=configured_sender(TWILIO_ENV), transport=FakeTransport(status, body)
    )
    assert delivery.status == "failed" and not delivery.sent
    assert reason in (delivery.error or "")


def test_an_unreachable_provider_is_failed() -> None:
    def broken(*_: Any) -> tuple[int, bytes]:
        raise OSError("connection refused")

    delivery = send_alert(ALERT, config=configured_sender(TWILIO_ENV), transport=broken)
    assert delivery.status == "failed" and "connection refused" in (delivery.error or "")


def test_offline_refuses_before_any_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VARUNA_OFFLINE", "1")
    delivery = send_alert(ALERT, config=configured_sender(TWILIO_ENV))
    assert delivery.status == "refused" and "VARUNA_OFFLINE" in (delivery.error or "")


def test_templates_come_from_the_alert() -> None:
    text = whatsapp_text(ALERT)
    assert ALERT["headline"] in text and ALERT["instruction"] in text
    assert text.splitlines()[0] == "VARUNA severe alert (exercise)"
    live = whatsapp_text({**ALERT, "cap_status": "Actual"})
    assert "(exercise)" not in live
    sms = sms_text({**ALERT, "headline": "x" * 300})
    assert len(sms) <= notify.SMS_LIMIT and sms.endswith("... Avoid the street.")
    assert sms_text(ALERT).startswith("VARUNA severe exercise: Hindmata junction")


def test_mask_number() -> None:
    assert mask_number("+919820012345") == "+91******2345"
    assert mask_number("919820012345") == "91******2345"
    assert mask_number("123") == "***"


def test_every_attempt_is_recorded_as_what_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    refused = send_alert(ALERT, config=configured_sender({}))
    sent = send_alert(
        ALERT, config=configured_sender(TWILIO_ENV), transport=FakeTransport(201, {"sid": "SM9"})
    )
    for delivery in (refused, sent):
        record_delivery(
            "mumbai",
            delivery,
            alert=ALERT,
            identity="hotspot|MUM-HS-01|severe",
            user="ward officer",
        )
    rows = deliveries("mumbai")
    assert [r["status"] for r in rows] == ["refused", "sent"]
    assert rows[1]["provider_id"] == "SM9" and rows[1]["to_masked"] == "whatsapp:+91******2345"
    assert "secret-token" not in (tmp_path / "ops" / "mumbai.deliveries.jsonl").read_text()
    with pytest.raises(ValueError):
        notify.delivery_log_path("../etc")

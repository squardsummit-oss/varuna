"""Sending an alert to a real phone, when - and only when - someone has configured a sender.

SPEC.md 7.5 and task P8.8. The prototype's channels are the dashboard and an on-screen phone
mock; a real WhatsApp or SMS message is P2 and needs keys this repository never holds. So this
module is an adapter that is **inert until its environment says otherwise**:

* **Twilio** (WhatsApp or SMS through Twilio's Messages API) when ``TWILIO_ACCOUNT_SID``,
  ``TWILIO_AUTH_TOKEN``, ``TWILIO_FROM`` and ``TWILIO_TO`` are all set. A ``TWILIO_FROM`` of the
  form ``whatsapp:+14155238886`` sends WhatsApp; a bare number sends SMS. ``TWILIO_TO`` takes the
  same prefix.
* **WhatsApp Cloud API** (Meta) when ``WHATSAPP_CLOUD_TOKEN``, ``WHATSAPP_CLOUD_PHONE_ID`` and
  ``WHATSAPP_CLOUD_TO`` are all set.

Twilio wins when both are complete, so the choice is a function of the environment and not of
dictionary order. **The recipient is configuration, never a request field**: "Send to my phone"
sends to the number whoever deployed the API wrote down, so the endpoint cannot be turned into a
way of messaging strangers.

**Nothing here claims a send it did not see.** A message is ``sent`` only when the provider
answered 2xx and returned its own message id; a refusal (no sender, ``VARUNA_OFFLINE=1``), a
network failure and a provider error are each recorded as what they were. Every attempt, sent or
not, goes to ``data/ops/<city>.deliveries.jsonl`` - append-only, like the ops log - so the
delivery log on /alerts is a record, not a claim. The mock channels are never written there: they
are renders on this screen, and the log says so in its own words.

The HTTP call goes through an injectable ``transport`` so the adapters are tested against a fake
one; the default uses the standard library and honours ``VARUNA_OFFLINE``.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping
    from pathlib import Path

log = structlog.get_logger("varuna.products.notify")

__all__ = [
    "SENDER_ENV",
    "Delivery",
    "SenderConfig",
    "Transport",
    "configured_sender",
    "deliveries",
    "delivery_log_path",
    "mask_number",
    "record_delivery",
    "send_alert",
    "sms_text",
    "whatsapp_text",
]

SENDER_ENV: dict[str, tuple[str, ...]] = {
    "twilio": ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "TWILIO_TO"),
    "whatsapp_cloud": ("WHATSAPP_CLOUD_TOKEN", "WHATSAPP_CLOUD_PHONE_ID", "WHATSAPP_CLOUD_TO"),
}
"""The variables each adapter needs, all of them, in the order a refusal names them."""

TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
WHATSAPP_CLOUD_URL = "https://graph.facebook.com/v20.0/{phone_id}/messages"

SMS_LIMIT = 160
"""One GSM-7 segment. An alert that needs two is an alert that arrives in the wrong order."""

TIMEOUT_S = 10.0


class Transport(Protocol):
    """``(method, url, headers, body) -> (status, body)``; raises ``OSError`` on no answer."""

    def __call__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes
    ) -> tuple[int, bytes]: ...


@dataclass(frozen=True)
class SenderConfig:
    """A complete sender, read from the environment. ``to`` is never shown unmasked."""

    provider: str
    channel: str
    to: str
    settings: dict[str, str]

    @property
    def to_masked(self) -> str:
        return mask_number(self.to)

    def public(self) -> dict[str, Any]:
        """What an endpoint may say about the sender: never a key, never the whole number."""
        return {
            "configured": True,
            "provider": self.provider,
            "channel": self.channel,
            "to_masked": self.to_masked,
        }


@dataclass(frozen=True)
class Delivery:
    """One attempt to reach a phone. ``status`` is sent, failed or refused - never assumed."""

    status: str
    provider: str | None
    channel: str | None
    to_masked: str | None
    provider_id: str | None
    error: str | None
    text: str

    @property
    def sent(self) -> bool:
        return self.status == "sent"


def mask_number(number: str) -> str:
    """``whatsapp:+919820012345`` -> ``whatsapp:+91******2345``: enough to recognise, not to dial."""
    prefix, _, digits = number.rpartition(":")
    head = f"{prefix}:" if prefix else ""
    if len(digits) <= 4:
        return head + "*" * len(digits)
    keep = 3 if digits.startswith("+") else 2
    return head + digits[:keep] + "*" * (len(digits) - keep - 4) + digits[-4:]


def configured_sender(env: Mapping[str, str] | None = None) -> SenderConfig | None:
    """The sender the environment configures completely, or None. Twilio first.

    With no ``env`` given this reads the process environment, and fills the five keys
    :class:`~varuna_schemas.settings.Settings` already knows from it too - which is where a key
    written into ``.env`` rather than exported lands. ``TWILIO_TO`` and ``WHATSAPP_CLOUD_TO`` are
    read from the environment only.
    """
    source: Mapping[str, str]
    if env is None:
        from varuna_schemas.settings import get_settings

        settings = get_settings()
        merged = {
            "TWILIO_ACCOUNT_SID": settings.twilio_account_sid or "",
            "TWILIO_AUTH_TOKEN": settings.twilio_auth_token or "",
            "TWILIO_FROM": settings.twilio_from or "",
            "WHATSAPP_CLOUD_TOKEN": settings.whatsapp_cloud_token or "",
            "WHATSAPP_CLOUD_PHONE_ID": settings.whatsapp_cloud_phone_id or "",
        }
        merged.update({k: v for k, v in os.environ.items() if v})
        source = merged
    else:
        source = env

    def value(name: str) -> str:
        return str(source.get(name, "") or "").strip()

    for provider, names in SENDER_ENV.items():
        values = {name: value(name) for name in names}
        if not all(values.values()):
            continue
        if provider == "twilio":
            to = values["TWILIO_TO"]
            channel = "whatsapp" if values["TWILIO_FROM"].startswith("whatsapp:") else "sms"
            if channel == "whatsapp" and not to.startswith("whatsapp:"):
                to = f"whatsapp:{to}"
        else:
            to = values["WHATSAPP_CLOUD_TO"]
            channel = "whatsapp"
        return SenderConfig(provider=provider, channel=channel, to=to, settings=values)
    return None


# ---- templates -------------------------------------------------------------------------------
def _level_word(alert: Mapping[str, Any]) -> str:
    return {"severe": "Severe", "moderate": "Moderate", "watch": "Watch"}.get(
        str(alert.get("level")), "Alert"
    )


def whatsapp_text(alert: Mapping[str, Any], dispatch_note: str | None = None) -> str:
    """The WhatsApp card the ward officer reads: the same words the phone mock shows.

    Rendered from the alert, never composed separately, so the mock and a real send cannot say
    two different things. A replay alert says it is an exercise on its first line, the same way
    its CAP document carries ``status=Exercise``.
    """
    exercise = str(alert.get("cap_status", "Exercise")) != "Actual"
    lines = [
        f"VARUNA {_level_word(alert).lower()} alert{' (exercise)' if exercise else ''}",
        f"{alert.get('headline', '')}.",
    ]
    if alert.get("instruction"):
        lines.append(str(alert["instruction"]))
    note = dispatch_note or alert.get("dispatch_note")
    if note:
        lines.append(str(note))
    from varuna_products.alerts import forecast_phrase

    lines.append(
        f"Area: {alert.get('area_desc', '')}. {forecast_phrase(str(alert.get('run_id', '')))}."
    )
    return "\n".join(lines)


def sms_text(alert: Mapping[str, Any]) -> str:
    """One SMS segment: level, headline and the exercise flag, the headline cut to fit, never split."""
    exercise = str(alert.get("cap_status", "Exercise")) != "Actual"
    prefix = f"VARUNA {_level_word(alert).lower()}{' exercise' if exercise else ''}: "
    headline = str(alert.get("headline", ""))
    text = f"{prefix}{headline}. Avoid the street."
    if len(text) <= SMS_LIMIT:
        return text
    # The instruction is what the reader acts on, so a long headline is cut, never the tail.
    tail = "... Avoid the street."
    return f"{prefix}{headline[: SMS_LIMIT - len(prefix) - len(tail)].rstrip()}{tail}"


# ---- transport -------------------------------------------------------------------------------
def _urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes
) -> tuple[int, bytes]:
    from varuna_schemas.net import OfflineRefused, offline, use_system_trust_store

    if offline():
        msg = "VARUNA_OFFLINE=1 blocks outbound requests, so no message was sent."
        raise OfflineRefused(msg)
    use_system_trust_store()
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as error:
        return int(error.code), error.read()


def _twilio(config: SenderConfig, text: str, transport: Transport) -> tuple[int, dict[str, Any]]:
    s = config.settings
    token = base64.b64encode(
        f"{s['TWILIO_ACCOUNT_SID']}:{s['TWILIO_AUTH_TOKEN']}".encode()
    ).decode()
    from_ = s["TWILIO_FROM"]
    status, raw = transport(
        "POST",
        TWILIO_URL.format(sid=urllib.parse.quote(s["TWILIO_ACCOUNT_SID"], safe="")),
        {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        urllib.parse.urlencode({"From": from_, "To": config.to, "Body": text}).encode(),
    )
    return status, _json(raw)


def _whatsapp_cloud(
    config: SenderConfig, text: str, transport: Transport
) -> tuple[int, dict[str, Any]]:
    s = config.settings
    status, raw = transport(
        "POST",
        WHATSAPP_CLOUD_URL.format(
            phone_id=urllib.parse.quote(s["WHATSAPP_CLOUD_PHONE_ID"], safe="")
        ),
        {
            "Authorization": f"Bearer {s['WHATSAPP_CLOUD_TOKEN']}",
            "Content-Type": "application/json",
        },
        json.dumps(
            {
                "messaging_product": "whatsapp",
                "to": config.to.removeprefix("whatsapp:"),
                "type": "text",
                "text": {"preview_url": False, "body": text},
            }
        ).encode(),
    )
    return status, _json(raw)


def _json(raw: bytes) -> dict[str, Any]:
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def _provider_id(provider: str, body: Mapping[str, Any]) -> str | None:
    if provider == "twilio":
        sid = body.get("sid")
        return str(sid) if sid else None
    messages = body.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        mid = messages[0].get("id")
        return str(mid) if mid else None
    return None


def _provider_error(body: Mapping[str, Any]) -> str:
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(body.get("message") or "no message in the response")


def send_alert(
    alert: Mapping[str, Any],
    *,
    config: SenderConfig | None = None,
    transport: Transport | None = None,
    dispatch_note: str | None = None,
) -> Delivery:
    """Send one alert to the configured phone, and say exactly what happened.

    With no sender configured this returns ``refused`` and touches no network. ``sent`` needs a
    2xx *and* the provider's own id for the message, because a 2xx with no id is not evidence
    that anything left the provider.
    """
    sender = config if config is not None else configured_sender()
    if sender is None:
        return Delivery(
            status="refused",
            provider=None,
            channel=None,
            to_masked=None,
            provider_id=None,
            error=(
                "No sender is configured: set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM "
                "and TWILIO_TO, or WHATSAPP_CLOUD_TOKEN, WHATSAPP_CLOUD_PHONE_ID and "
                "WHATSAPP_CLOUD_TO, where the API runs."
            ),
            text="",
        )
    text = sms_text(alert) if sender.channel == "sms" else whatsapp_text(alert, dispatch_note)
    call = transport if transport is not None else _urllib_transport
    adapter = _twilio if sender.provider == "twilio" else _whatsapp_cloud
    try:
        status, body = adapter(sender, text, call)
    except OSError as error:
        # OfflineRefused is a RuntimeError, not an OSError; both mean nothing left this machine.
        return _failed(sender, text, "failed", f"The provider could not be reached: {error}")
    except RuntimeError as error:
        return _failed(sender, text, "refused", str(error))

    provider_id = _provider_id(sender.provider, body)
    if 200 <= status < 300 and provider_id:
        log.info("notify.sent", provider=sender.provider, to=sender.to_masked, id=provider_id)
        return Delivery(
            status="sent",
            provider=sender.provider,
            channel=sender.channel,
            to_masked=sender.to_masked,
            provider_id=provider_id,
            error=None,
            text=text,
        )
    reason = (
        f"The provider answered HTTP {status} without a message id."
        if 200 <= status < 300
        else f"The provider refused it with HTTP {status}: {_provider_error(body)}"
    )
    return _failed(sender, text, "failed", reason)


def _failed(sender: SenderConfig, text: str, status: str, error: str) -> Delivery:
    log.warning("notify.not_sent", provider=sender.provider, status=status, error=error)
    return Delivery(
        status=status,
        provider=sender.provider,
        channel=sender.channel,
        to_masked=sender.to_masked,
        provider_id=None,
        error=error,
        text=text,
    )


# ---- the delivery log ------------------------------------------------------------------------
def delivery_log_path(city: str) -> Path:
    """``data/ops/<city>.deliveries.jsonl``, beside the ops log it complements."""
    from varuna_schemas.paths import data_dir

    if not city or any(c in city for c in "/\\.") or city != city.strip():
        msg = f"{city!r} is not a city slug"
        raise ValueError(msg)
    return data_dir() / "ops" / f"{city}.deliveries.jsonl"


def record_delivery(
    city: str,
    delivery: Delivery,
    *,
    alert: Mapping[str, Any],
    identity: str,
    user: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one attempt to the delivery log and return the row as written."""
    from varuna_schemas.constants import IST

    moment = (now or datetime.now(IST)).isoformat(timespec="seconds")
    row = {
        "ts": moment,
        "alert_id": alert.get("id"),
        "identity": identity,
        "run_id": alert.get("run_id"),
        "channel": delivery.channel,
        "provider": delivery.provider,
        "to_masked": delivery.to_masked,
        "status": delivery.status,
        "provider_id": delivery.provider_id,
        "error": delivery.error,
        "user": user,
    }
    path = delivery_log_path(city)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    return row


def deliveries(city: str) -> list[dict[str, Any]]:
    """Every recorded attempt for a city, oldest first. A torn last line is skipped, not fatal."""
    path = delivery_log_path(city)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows

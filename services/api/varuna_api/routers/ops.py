"""The authority write path: the one thing in this product an official can actually do.

TECH_SPEC 3.6 and task D-07. Everything else VARUNA serves is a forecast being read; this is a
ward officer telling the system something it could not know - that a street is closed, that a
lorry is broken, that an alert has been seen - and having the next answer change because of it.

**Nothing here rewrites a product.** Every edit is one line appended to ``data/ops/<city>.jsonl``
through :mod:`varuna_route.ops_overlay`, and the router, the road-conditions feed and the alert
queue apply it at read time. SPEC.md rule 8 wants byte-identical bakes and P5.9 checks them, so
a closure that edited ``segments_wet.json`` would break the one property that makes a run
trustworthy. ``services/api/tests/test_ops.py`` takes the sha256 of every file in a baked run
before and after a closure, a pump status change and a dispatch, and proves not one byte moved.

**The gate.** ``VARUNA_OPS_PASSPHRASE`` unset means writes are refused, and the refusal names the
variable. It is read from the environment or from ``.env`` through Settings, and an exported
value wins. On the deployed API it stays unset, so the desk is read-only there and
``GET /v1/ops/log`` says so in ``writes_enabled``. When it is set, a write must carry it in
``X-Varuna-Ops``; the browser prompts for it locally and never stores it. Thirty writes a minute
per process, which is far above a human at a desk and far below anything that could fill a disk.

**Why reads are ungated.** A closure is a public fact - the citizen dashboard has to be able to
say why a street is refused - so ``GET /v1/ops/closures``, ``/v1/ops/log`` and ``/v1/ops/alerts``
answer anyone. Only the acts are gated, plus two things only the desk may read: a reporter's
exact position (``GET /v1/ops/reports``) and the officer's name on a report status, which the
ungated log withholds unless the request carries the passphrase.
"""

from __future__ import annotations

import hmac
import json
import os
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header, Query
from pydantic import Field
from varuna_schemas.constants import IST, STEP_MIN
from varuna_schemas.models import VarunaModel
from varuna_schemas.paths import city_dir, run_dir
from varuna_schemas.settings import Settings, get_settings

from varuna_api.routers.stubs import AlertActionRequest, PumpDispatchRequest, PumpOptimiseRequest
from varuna_api.runs_util import (
    bake_hint,
    city_of_run,
    latest_run_for,
    no_run_hint,
    resolve_city,
)
from varuna_api.state import api_error

log = structlog.get_logger("varuna.api.ops")

router = APIRouter(prefix="/v1")

PASSPHRASE_ENV = "VARUNA_OPS_PASSPHRASE"
"""The one variable that decides whether this API can be written to at all (TECH_SPEC 3.6)."""

OPS_HEADER = "X-Varuna-Ops"
"""Header the passphrase travels in. Never a cookie, never localStorage: a desk that leaves the
passphrase in the browser is a desk anybody who borrows the laptop can act as."""

WRITES_PER_MINUTE = 30
"""Writes per process per rolling minute, as TECH_SPEC 3.6 sets it."""

WINDOW_S = 60.0

NO_FORECAST_CHANGED = (
    "This changed no forecast. Authority edits are an append-only overlay applied when a route "
    "or a feed is read; every baked product is byte-identical."
)
"""Printed with every write, because the desk has to know what it did *not* do (rule 6)."""

_writes: deque[float] = deque()
"""Monotonic stamps of the authorised writes in the current window."""


def reset_rate_limit() -> None:
    """Forget the window. Tests call it; nothing in the app does."""
    _writes.clear()


def _configured_passphrase() -> str:
    """The passphrase a write is checked against, or ``""`` when none is configured.

    Merged the way :func:`varuna_products.notify.configured_sender` merges its keys: an exported
    ``VARUNA_OPS_PASSPHRASE`` wins, and otherwise the value :class:`Settings` reads, which is
    where one written into ``.env`` lands. Settings reads that file and never exports it, so a
    gate that looked only at ``os.environ`` ignored a passphrase put exactly where SPEC.md 4.4
    says configuration goes. Settings is read fresh here rather than through the cached
    :func:`get_settings`, because that copy holds the environment of its first read: a
    passphrase removed since, from ``.env`` or from the environment, would still open the gate.
    A fresh read measured 5.1-6.0 ms with a ``.env`` present and 0.8-1.1 ms without one
    (2026-09-26, i5-1155G7), and writes are capped at thirty a minute. The value is never logged
    and never returned.
    """
    exported = os.environ.get(PASSPHRASE_ENV, "")
    if exported:
        return exported.strip()
    return (Settings().varuna_ops_passphrase or "").strip()


def writes_enabled() -> bool:
    """Whether this process holds a passphrase at all, from its environment or its ``.env``."""
    return bool(_configured_passphrase())


def _refuse_disabled() -> None:
    raise api_error(
        503,
        "ops_writes_disabled",
        f"This API cannot accept authority edits: {PASSPHRASE_ENV} is not set in its "
        "environment, so there is nothing to check a request against. Set it where the API "
        "runs and restart it; the deployed API leaves it unset on purpose and is read-only.",
    )


def _check_rate_limit() -> None:
    now = time.monotonic()
    while _writes and now - _writes[0] > WINDOW_S:
        _writes.popleft()
    if len(_writes) >= WRITES_PER_MINUTE:
        wait = round(WINDOW_S - (now - _writes[0]))
        raise api_error(
            429,
            "rate_limited",
            f"{WRITES_PER_MINUTE} authority edits a minute is the limit and this process has "
            f"used them. Wait {max(wait, 1)} s and send it again; nothing was written.",
        )
    _writes.append(now)


def require_ops(
    x_varuna_ops: Annotated[str | None, Header(description="The desk passphrase.")] = None,
) -> None:
    """Gate every write: passphrase configured, passphrase presented, rate limit not spent.

    In that order. An unauthenticated caller cannot spend the window, because a refusal that
    consumed the budget would let anyone lock the real desk out of its own API; only writes that
    got past the passphrase are counted.
    """
    expected = _configured_passphrase()
    if not expected:
        _refuse_disabled()
    if not x_varuna_ops:
        raise api_error(
            401,
            "ops_passphrase_required",
            f"This is an authority edit and it carries no passphrase. Send it in the "
            f"{OPS_HEADER} header.",
        )
    if not hmac.compare_digest(x_varuna_ops.strip(), expected):
        raise api_error(
            403,
            "ops_passphrase_rejected",
            f"The {OPS_HEADER} passphrase does not match this API's {PASSPHRASE_ENV}. "
            "Nothing was written.",
        )
    _check_rate_limit()


OpsWrite = Annotated[None, Depends(require_ops)]
"""Dependency alias, so every gated route reads the same and none can forget the gate."""


def require_ops_read(
    x_varuna_ops: Annotated[str | None, Header(description="The desk passphrase.")] = None,
) -> None:
    """Gate a read only the desk may make: a reporter's exact position, an officer's name.

    The same passphrase and the same refusals as :func:`require_ops`, and not counted against
    the write window: reading the inbox is not an act, and a desk refreshing it must not use up
    the minute's closures.
    """
    expected = _configured_passphrase()
    if not expected:
        _refuse_disabled()
    if not x_varuna_ops:
        raise api_error(
            401,
            "ops_passphrase_required",
            f"This read is for the ward desk and it carries no passphrase. Send it in the "
            f"{OPS_HEADER} header.",
        )
    if not hmac.compare_digest(x_varuna_ops.strip(), expected):
        raise api_error(
            403,
            "ops_passphrase_rejected",
            f"The {OPS_HEADER} passphrase does not match this API's {PASSPHRASE_ENV}.",
        )


OpsRead = Annotated[None, Depends(require_ops_read)]
"""The desk's gated reads: passphrase checked, write window untouched."""


def presents_passphrase(x_varuna_ops: str | None) -> bool:
    """Whether an ungated read also carries the desk's passphrase, so it may see what only the
    desk sees. Never refuses: a missing or wrong passphrase just gets the public answer."""
    expected = _configured_passphrase()
    if not expected or not x_varuna_ops:
        return False
    return hmac.compare_digest(x_varuna_ops.strip(), expected)


# ---- request bodies ---------------------------------------------------------------------
class ClosureRequest(VarunaModel):
    """Body of ``POST /v1/ops/closures``."""

    segment_id: str = Field(description="The segment the officer is closing or reopening.")
    reason: str = Field(
        default="",
        max_length=300,
        description=(
            "Why, in the officer's own words. Required to close: a closure with no reason "
            "reaches the citizen screen with nothing to say after the comma, and UI_SPEC 4 "
            "refuses to soften it into 'closed'."
        ),
    )
    until: str | None = Field(
        default=None, description="ISO 8601 expiry with an offset; omit for until-reopened."
    )
    user: str = Field(default="ward officer", max_length=80)
    city: str | None = Field(default=None, description="Defaults to VARUNA_CITY.")
    reopen: bool = Field(default=False, description="Lift the closure instead of making one.")


class PumpStatusRequest(VarunaModel):
    """Body of ``POST /v1/ops/pumps/{pump_id}/status``."""

    status: Literal["available", "unavailable", "moved"]
    user: str = Field(default="ward officer", max_length=80)
    note: str | None = Field(default=None, max_length=300)
    city: str | None = None
    lon: float | None = Field(default=None, ge=-180.0, le=180.0)
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)


# ---- shared helpers ---------------------------------------------------------------------
def _city(raw: str | None) -> str:
    """A city slug that can be a file name, or a 422 that says so."""
    from varuna_route import ops_overlay as ops

    name = (raw or get_settings().varuna_city or "").strip().lower()
    try:
        ops.overlay_path(name)
    except ValueError as error:
        raise api_error(422, "bad_city", f"{error}. Use a city slug such as 'mumbai'.") from None
    return name


def _time(raw: str | None, field: str) -> datetime | None:
    if raw in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        raise api_error(
            422,
            "bad_time",
            f"{field} must be ISO 8601 with an offset, e.g. 2019-07-02T10:00:00+05:30.",
        ) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)


def _append(city: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Append through the overlay, turning its refusals into the section 12 envelope."""
    from varuna_route import ops_overlay as ops

    try:
        return ops.append(city, entry)
    except ValueError as error:
        raise api_error(422, "bad_ops_entry", str(error)) from None
    except OSError as error:
        raise api_error(
            503,
            "ops_log_unwritable",
            f"The ops log for {city} could not be written: {error}. Check that data/ops is "
            "writable where the API runs; nothing was recorded.",
        ) from None


def _run_path(run_id: str | None, city: str, needs: str) -> Path:
    """The run to act on: the one named, or the newest of this city carrying ``needs``.

    The city goes through :func:`~varuna_api.runs_util.resolve_city`, the helper every
    run-reading route shares, before anything is read (task D-09). A city with no run-id code is
    refused as 404 ``unknown_city`` instead of being handed another city's newest run, beside a
    ``run_id`` as well, the way the depth routes refuse it. A missing run or product names the
    command that bakes that city's own bundle rather than Mumbai's.
    """
    name = resolve_city(city)
    if run_id:
        path = run_dir(run_id)
        if not (path / needs).is_file():
            raise api_error(
                404,
                "run_not_found",
                f"Run {run_id} has no {needs}. {bake_hint(city_of_run(run_id) or name)}",
                run_id=run_id,
            )
        return path
    latest = latest_run_for(name, lambda p: (p / needs).is_file())
    if latest is None:
        raise api_error(404, "no_run", no_run_hint(name, needs))
    return latest


# ---- alert state ------------------------------------------------------------------------
def apply_alert_state(alerts: list[dict[str, Any]], city: str | None) -> list[dict[str, Any]]:
    """Overlay the desk's acknowledgements onto a run's alert queue, at read time.

    The queue is a product: the cycle computed it and no officer may edit it. What an officer
    changes is the *state* of an alert, which lives in the ops log, so the two are folded
    together here and nowhere else. Later entries win, and the history keeps both, so an alert
    escalated after it was acknowledged still says who acknowledged it.

    **An action is matched by identity as well as by id.** An alert id embeds the run that raised
    it, so the next cycle renames every alert it is still raising and an id match alone lost the
    officer's acknowledgement five simulated minutes after it was made. Every action records the
    alert's :func:`~varuna_products.alerts.alert_identity` - scope, place and level - and an alert
    carries the state of any action with its id *or* its identity. Both are kept: an entry written
    before identities existed has no identity and still matches its own alert exactly as it did.

    The two matches overlap on the alert that was acted on, so entries are gathered by their
    position in the log, which both deduplicates them and restores the order they were written in.

    ``city`` is resolved here rather than by the caller, so ``GET /v1/alerts`` and
    ``GET /v1/ops/alerts`` cannot reach different logs by resolving it two ways. ``None`` means
    the configured city, which is what a caller with no ``?city=`` hands in.
    """
    from varuna_products.alerts import alert_identity
    from varuna_route import ops_overlay as ops

    city = _city(city)
    by_id: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    by_identity: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    by_place: dict[str, list[dict[str, Any]]] = {}
    for position, entry in enumerate(ops.entries(city)):
        kind = entry.get("kind")
        if kind == "dispatch":
            place = str(entry.get("hotspot_id", "")).strip()
            if place:
                by_place.setdefault(place, []).append(entry)
            continue
        if kind not in {"alert_ack", "alert_escalate"}:
            continue
        alert_id = str(entry.get("alert_id", "")).strip()
        if alert_id:
            by_id.setdefault(alert_id, []).append((position, entry))
        identity = str(entry.get("identity", "")).strip()
        if identity:
            by_identity.setdefault(identity, []).append((position, entry))
    if not by_id and not by_identity and not by_place:
        return alerts

    out: list[dict[str, Any]] = []
    for alert in alerts:
        found = dict(
            (
                *by_id.get(str(alert.get("id")), ()),
                *by_identity.get(alert_identity(alert), ()),
            )
        )
        history = [found[position] for position in sorted(found)]
        dispatched = by_place.get(_alert_place(alert), [])
        if not history and not dispatched:
            out.append(alert)
            continue
        updated = dict(alert)
        if dispatched:
            # SPEC.md 7.5's "Pumps P-12, P-15 dispatched": the order the desk gave for this place,
            # carried on the alert about it so the phone says it. Matched by place rather than by
            # alert, because a pump sent to Hindmata at 08:40 is still there at 09:10.
            pumps = list(dict.fromkeys(str(e.get("pump_id")) for e in dispatched))
            updated["pumps"] = pumps
            updated["dispatch_note"] = (
                f"Pump{'s' if len(pumps) > 1 else ''} {', '.join(pumps)} dispatched."
            )
            updated["dispatch_orders"] = [e.get("order_text") for e in dispatched]
        if not history:
            out.append(updated)
            continue
        updated["history"] = [
            {
                "ts": e.get("ts"),
                "state": "acknowledged" if e["kind"] == "alert_ack" else "escalated",
                "user": e.get("user", "unknown"),
                "note": e.get("note"),
            }
            for e in history
        ]
        for e in history:
            if e["kind"] == "alert_ack":
                updated["state"] = "acknowledged"
                updated["acknowledged_by"] = e.get("user", "unknown")
                updated["acknowledged_ts"] = e.get("ts")
            else:
                updated["state"] = "escalated"
                updated["escalated_to"] = e.get("to")
        out.append(updated)
    return out


def _alert_place(alert: dict[str, Any]) -> str:
    """The id a pump dispatch names for the place an alert is about.

    The pump plan calls a register hotspot by its ``hotspot_id`` and a street ``street:<name>``
    (``varuna_products.pumps``); the alert names a street by its ``area_desc``.
    """
    if alert.get("hotspot_id"):
        return str(alert["hotspot_id"])
    return f"street:{alert.get('area_desc', '')}"


def _alert_queue(path: Path) -> list[dict[str, Any]]:
    record = path / "alerts.json"
    if not record.is_file():
        raise api_error(
            404,
            "no_alerts",
            f"Run {path.name} has no alert product. "
            f"{bake_hint(city_of_run(path.name) or resolve_city(None))}",
            run_id=path.name,
        )
    body = json.loads(record.read_text(encoding="utf-8"))
    return list(body.get("alerts", []))


def _alert_action(
    alert_id: str, body: AlertActionRequest, run_id: str | None, kind: str
) -> dict[str, Any]:
    from varuna_products.alerts import alert_identity

    city = _city(body.city)
    path = _run_path(run_id, city, "alerts.json")
    queue = _alert_queue(path)
    target = next((a for a in queue if str(a.get("id")) == alert_id), None)
    if target is None:
        raise api_error(
            404,
            "alert_not_found",
            f"Run {path.name} raised no alert {alert_id}. Open /v1/alerts for the queue it did.",
            run_id=path.name,
        )

    # The id says which alert was on screen; the identity says which *situation* was acted on, and
    # only the identity is still true next cycle. Both are recorded (see `apply_alert_state`).
    entry: dict[str, Any] = {
        "kind": kind,
        "alert_id": alert_id,
        "identity": alert_identity(target),
        "run_id": path.name,
        "user": body.user,
        "note": body.note,
    }
    if kind == "alert_escalate":
        entry["to"] = body.escalate_to
    stored = _append(city, entry)

    overlaid = apply_alert_state(queue, city)
    alert = next(a for a in overlaid if str(a.get("id")) == alert_id)
    log.info("api.alert_action", kind=kind, alert_id=alert_id, run_id=path.name, user=body.user)
    return {"run_id": path.name, "entry": stored, "alert": alert, "notes": [NO_FORECAST_CHANGED]}


@router.post(
    "/alerts/{alert_id}/ack",
    tags=["alerts"],
    summary="Acknowledge an alert (recorded in the ops log)",
)
def alert_ack(
    alert_id: str,
    body: AlertActionRequest,
    _gate: OpsWrite,
    run_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Record that an officer has seen this alert, and answer with the alert as it now reads.

    The response is the product's own alert with the ops log folded in - not a model of one -
    because the queue carries fields the drafted ``Alert`` schema forbids, the same reason
    ``/v1/route`` serves its own flatter shape (ADR-0027).
    """
    return _alert_action(alert_id, body, run_id, "alert_ack")


@router.post(
    "/alerts/{alert_id}/escalate",
    tags=["alerts"],
    summary="Escalate an alert up the matrix (recorded in the ops log)",
)
def alert_escalate(
    alert_id: str,
    body: AlertActionRequest,
    _gate: OpsWrite,
    run_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Escalate to the next step of the matrix. ``escalate_to`` names it; the log keeps who."""
    return _alert_action(alert_id, body, run_id, "alert_escalate")


@router.get("/ops/alerts", tags=["alerts"], summary="A run's alerts with the desk's state applied")
def ops_alerts(
    run_id: Annotated[str | None, Query()] = None,
    city: Annotated[str | None, Query()] = None,
    level: Annotated[Literal["severe", "moderate", "watch"] | None, Query()] = None,
) -> dict[str, Any]:
    """The alert queue as the authority desk sees it: the run's own alerts, plus their state.

    ``GET /v1/alerts`` serves the product untouched and is not this endpoint's to change - it
    belongs to the depth router - so the desk reads the overlaid queue here and an
    acknowledgement survives a reload.
    """
    name = _city(city)
    path = _run_path(run_id, name, "alerts.json")
    queue = apply_alert_state(_alert_queue(path), name)
    if level:
        queue = [a for a in queue if a.get("level") == level]
    return {
        "run_id": path.name,
        "city": name,
        "n_total": len(queue),
        "alerts": queue,
        "writes_enabled": writes_enabled(),
        "notes": [NO_FORECAST_CHANGED],
    }


# ---- escalation matrix, sender, delivery log ----------------------------------------------
@router.get(
    "/alerts/escalation",
    tags=["alerts"],
    summary="The escalation matrix from config/escalation.yaml",
)
def alert_escalation() -> dict[str, Any]:
    """Ward officer to public, in order, as ``config/escalation.yaml`` states it (SPEC.md 7.5).

    Each tier's ``id`` is what ``POST /v1/alerts/{id}/escalate`` takes in ``escalate_to``, and its
    ``levels`` are the alert levels that reach it when raised - the same list every alert carries
    in ``notify``.
    """
    from varuna_products.alerts import load_escalation

    try:
        matrix = load_escalation()
    except ValueError as error:
        raise api_error(
            500, "bad_escalation_config", f"{error}. Fix the file and reload."
        ) from None
    if matrix is None:
        raise api_error(
            404,
            "no_escalation_config",
            "config/escalation.yaml is not where this API runs. It is committed at the repository "
            "root; an image built without it needs `COPY config/ config/`.",
        )
    return matrix


@router.get("/alerts/sender", tags=["alerts"], summary="Whether a real phone sender is configured")
def alert_sender() -> dict[str, Any]:
    """Whether "Send to my phone" can do anything here, and never a key or a whole number.

    ``configured`` is false unless every variable one adapter needs is set where the API runs
    (``varuna_products.notify.SENDER_ENV``); the screen draws the button only when it is true
    (SPEC.md 7.5 AC3).
    """
    from varuna_products.notify import SENDER_ENV, configured_sender

    sender = configured_sender()
    if sender is None:
        return {
            "configured": False,
            "provider": None,
            "channel": None,
            "to_masked": None,
            "needs": {provider: list(names) for provider, names in SENDER_ENV.items()},
        }
    return {**sender.public(), "needs": None}


class AlertSendRequest(VarunaModel):
    """Body of ``POST /v1/alerts/{id}/send``. The recipient is configuration, not a field."""

    user: str = Field(default="ward officer", max_length=80)
    city: str | None = None


@router.post(
    "/alerts/{alert_id}/send",
    tags=["alerts"],
    summary="Send one alert to the configured phone (real WhatsApp or SMS)",
)
def alert_send(
    alert_id: str,
    body: AlertSendRequest,
    _gate: OpsWrite,
    run_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Send the alert to the phone named in the API's environment, and record what happened.

    Gated with the desk's writes: a real message costs money and reaches a person. With no sender
    configured this answers 503 and sends nothing; a provider refusal is 502 with its reason. The
    attempt is appended to the delivery log either way, so the log is the record and the
    response never claims more than the log does.
    """
    from varuna_products.alerts import alert_identity
    from varuna_products.notify import configured_sender, record_delivery, send_alert

    city = _city(body.city)
    path = _run_path(run_id, city, "alerts.json")
    queue = apply_alert_state(_alert_queue(path), city)
    alert = next((a for a in queue if str(a.get("id")) == alert_id), None)
    if alert is None:
        raise api_error(
            404,
            "alert_not_found",
            f"Run {path.name} raised no alert {alert_id}. Open /v1/alerts for the queue it did.",
            run_id=path.name,
        )
    if configured_sender() is None:
        raise api_error(
            503,
            "no_sender",
            "No real sender is configured where this API runs, so nothing was sent. Set "
            "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM and TWILIO_TO, or "
            "WHATSAPP_CLOUD_TOKEN, WHATSAPP_CLOUD_PHONE_ID and WHATSAPP_CLOUD_TO, and restart it. "
            "The on-screen phone mock needs none of them.",
            run_id=path.name,
        )
    delivery = send_alert(alert)
    row = record_delivery(
        city, delivery, alert=alert, identity=alert_identity(alert), user=body.user
    )
    log.info("api.alert_send", alert_id=alert_id, status=delivery.status, user=body.user)
    if not delivery.sent:
        raise api_error(
            502,
            "not_sent",
            f"The message was not sent: {delivery.error} The attempt is in the delivery log.",
            run_id=path.name,
        )
    return {"run_id": path.name, "delivery": row, "text": delivery.text}


MOCK_CHANNELS: tuple[tuple[str, str, str], ...] = (
    ("dashboard", "Dashboard", "Shown on the alert queue"),
    ("whatsapp_mock", "WhatsApp mock", "Shown on the on-screen phone"),
    ("sms_mock", "SMS mock", "Rendered, not sent"),
)
"""The prototype's three channels and what each actually did - a render, never a delivery."""


@router.get("/alerts/delivery", tags=["alerts"], summary="What happened to each alert, per channel")
def alert_delivery(
    run_id: Annotated[str | None, Query()] = None,
    city: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict[str, Any]:
    """The delivery log for a run's queue: three mock renders per alert, plus every real attempt.

    The mock rows say what the mock did - shown on the queue, shown on the phone mock, an SMS
    rendered and not sent - and are never called delivered. Real rows come from
    ``data/ops/<city>.deliveries.jsonl`` and match this queue by alert id or by identity, so a
    send made on the previous cycle is still listed against the same situation.
    """
    from varuna_products.alerts import alert_identity
    from varuna_products.notify import configured_sender, deliveries, sms_text, whatsapp_text

    name = _city(city)
    path = _run_path(run_id, name, "alerts.json")
    queue = apply_alert_state(_alert_queue(path), name)[:limit]
    by_id = {str(a.get("id")): a for a in queue}
    by_identity = {alert_identity(a): a for a in queue}

    rows: list[dict[str, Any]] = []
    for alert in queue:
        when = alert.get("sent_ts") or alert.get("raised_ts")
        for channel, label, status in MOCK_CHANNELS:
            rows.append(
                {
                    "id": f"{alert['id']}-{channel}",
                    "alert_id": alert["id"],
                    "channel": channel,
                    "label": label,
                    "kind": "mock",
                    "status": status,
                    "ts": when,
                    "text": sms_text(alert)
                    if channel == "sms_mock"
                    else whatsapp_text(alert)
                    if channel == "whatsapp_mock"
                    else None,
                }
            )
    real = 0
    for index, entry in enumerate(deliveries(name)):
        alert = by_id.get(str(entry.get("alert_id"))) or by_identity.get(str(entry.get("identity")))
        if alert is None:
            continue
        real += 1
        provider = entry.get("provider") or "no sender"
        rows.append(
            {
                "id": f"real-{index}",
                "alert_id": alert["id"],
                "channel": entry.get("channel") or "none",
                "label": f"Real send ({provider})",
                "kind": "real",
                "status": {"sent": "Sent", "failed": "Failed", "refused": "Refused"}.get(
                    str(entry.get("status")), str(entry.get("status"))
                ),
                "ts": entry.get("ts"),
                "to_masked": entry.get("to_masked"),
                "provider_id": entry.get("provider_id"),
                "error": entry.get("error"),
                "user": entry.get("user"),
                "text": None,
            }
        )
    sender = configured_sender()
    return {
        "run_id": path.name,
        "city": name,
        "n_alerts": len(queue),
        "n_real": real,
        "rows": rows,
        "sender": sender.public() if sender else {"configured": False},
        "notes": [
            "Dashboard, WhatsApp mock and SMS mock are renders on this screen: nothing left the "
            "machine for them.",
            (
                "A real sender is configured; real attempts are listed with the provider's answer."
                if sender
                else "No real sender is configured, so no message has been sent to any phone."
            ),
        ],
    }


# ---- closures ---------------------------------------------------------------------------
def _closure_view(city: str, at: datetime | None = None) -> dict[str, Any]:
    from varuna_route import ops_overlay as ops

    overlay = ops.active(city, at=at)
    return {
        "city": city,
        "at": overlay.at.isoformat(),
        "n_closed": len(overlay.closures),
        "closures": [
            {
                "segment_id": c.segment_id,
                "reason": c.reason,
                "user": c.user,
                "ts": c.ts.isoformat(),
                "until": c.until.isoformat() if c.until else None,
                "id": c.entry_id,
            }
            for c in sorted(overlay.closures.values(), key=lambda c: c.ts, reverse=True)
        ],
        "n_entries": overlay.n_entries,
        "writes_enabled": writes_enabled(),
    }


@router.post("/ops/closures", tags=["ops"], summary="Close or reopen a street")
def post_closure(body: ClosureRequest, _gate: OpsWrite) -> dict[str, Any]:
    """Close a street, or reopen one. Both are appends; nothing is ever deleted.

    A closure beats the forecast: the router treats the segment as impassable whatever the depth
    says, and the reason the officer typed comes back on the route as a structured reason for
    the screen to word (TECH_SPEC 3.2).
    """
    city = _city(body.city)
    segment_id = body.segment_id.strip()
    if not segment_id:
        raise api_error(422, "bad_segment", "Name the segment_id to close.")
    reason = body.reason.strip()
    if not body.reopen and not reason:
        raise api_error(
            422,
            "closure_needs_a_reason",
            "A closure needs a reason: it is shown to drivers as the street's explanation, and "
            "there is nothing honest to print without one.",
        )
    until = _time(body.until, "until")
    if until is not None and not body.reopen and until <= datetime.now(IST):
        # Accepting it would append a closure that `active` drops on the way back out: the
        # officer would see "0 closed" beside their own entry and have no idea why.
        raise api_error(
            422,
            "closure_already_expired",
            f"That closure expires at {until.isoformat()}, which has already passed, so it "
            "would close nothing. Give a later `until`, or omit it to close until reopened.",
        )
    entry = _append(
        city,
        {
            "kind": "reopen" if body.reopen else "closure",
            "segment_id": segment_id,
            "reason": reason,
            "user": body.user,
            **({"until": until.isoformat()} if until else {}),
        },
    )
    view = _closure_view(city)
    log.info(
        "api.ops_closure",
        city=city,
        segment_id=segment_id,
        reopen=body.reopen,
        n_closed=view["n_closed"],
    )
    return {"entry": entry, **view, "notes": [NO_FORECAST_CHANGED]}


@router.get("/ops/closures", tags=["ops"], summary="Streets an authority has closed")
def get_closures(
    city: Annotated[str | None, Query()] = None,
    at: Annotated[str | None, Query(description="Evaluate expiries at this time.")] = None,
) -> dict[str, Any]:
    """The live closure set, folded from the log and with expiries applied at ``at``."""
    return _closure_view(_city(city), _time(at, "at"))


# ---- pump status ------------------------------------------------------------------------
@router.post("/ops/pumps/{pump_id}/status", tags=["ops"], summary="Set a pump's status")
def post_pump_status(pump_id: str, body: PumpStatusRequest, _gate: OpsWrite) -> dict[str, Any]:
    """Mark a pump available, unavailable, or moved to a new depot.

    An unavailable pump is not assigned by the next optimise. A moved one still is, from the
    point given here - see ``varuna_products.pumps.ASSIGNABLE_STATES``.
    """
    from varuna_route import ops_overlay as ops

    city = _city(body.city)
    identifier = pump_id.strip()
    if not identifier:
        raise api_error(422, "bad_pump", "Name the pump to set a status on.")
    if (body.lon is None) != (body.lat is None):
        raise api_error(
            422,
            "bad_point",
            "A moved pump needs both lon and lat, or neither. Half a coordinate is not a depot.",
        )
    entry = _append(
        city,
        {
            "kind": "pump_status",
            "pump_id": identifier,
            "status": body.status,
            "user": body.user,
            "note": body.note,
            **({"lon": body.lon, "lat": body.lat} if body.lon is not None else {}),
        },
    )
    overlay = ops.active(city)
    log.info("api.ops_pump_status", city=city, pump_id=identifier, status=body.status)
    return {
        "entry": entry,
        "city": city,
        "pumps": {
            pid: {
                "status": p.status,
                "user": p.user,
                "ts": p.ts.isoformat(),
                "lon": p.lon,
                "lat": p.lat,
            }
            for pid, p in sorted(overlay.pumps.items())
        },
        "notes": [
            NO_FORECAST_CHANGED,
            "The next POST /v1/pumps/optimise honours this; the run's own pump_plan.json still "
            "holds the plan the cycle computed.",
        ],
    }


# ---- citizen reports ----------------------------------------------------------------------
class ReportStatusRequest(VarunaModel):
    """Body of ``POST /v1/ops/reports/{report_id}/status``."""

    status: Literal["received", "seen", "crew_sent", "resolved", "dismissed"]
    note: str | None = Field(
        default=None,
        max_length=300,
        description="Shown to the reporter beside the status, so write it for them.",
    )
    user: str = Field(default="ward officer", max_length=80, description="Kept on the desk only.")
    role: Literal["ward officer", "control room", "field crew"] = Field(
        default="ward officer", description="What the reporter sees in place of the name."
    )


@router.post(
    "/ops/reports/{report_id}/status",
    tags=["ops"],
    summary="Set a citizen report's status (recorded in the ops log)",
)
def post_report_status(
    report_id: str, body: ReportStatusRequest, _gate: OpsWrite
) -> dict[str, Any]:
    """Tell a reporter what the desk did: seen, crew sent, resolved, or dismissed.

    An append, like every desk act: the report stays exactly as it was sent, and ``GET
    /v1/reports`` folds the latest status onto it when it is read. A dismissed report leaves
    every public list and its photo stops being served; it stays here, and a later status brings
    it back.
    """
    from varuna_api.routers.reports import find_report, report_histories, report_view, status_city

    identifier = report_id.strip()
    row = find_report(identifier) if identifier and len(identifier) <= 80 else None
    if row is None:
        raise api_error(
            404,
            "report_not_found",
            f"No report {identifier} is kept here, so there is nothing to set a status on. "
            "GET /v1/ops/reports lists the ones that are.",
        )
    # The report's own city's log; one from outside every city goes to the configured city's,
    # which is one of the logs `report_histories` reads back.
    city = _city(status_city(row))
    entry = _append(
        city,
        {
            "kind": "report_status",
            "report_id": identifier,
            "status": body.status,
            "note": body.note or None,
            "user": body.user,
            "role": body.role,
        },
    )
    history = report_histories().get(identifier, [])
    log.info("api.report_status", report_id=identifier, status=body.status, city=city)
    return {
        "entry": entry,
        "city": city,
        "report": report_view(row, history, exact=True),
        "notes": [
            "The status is appended to the ops log; the report is unchanged and no forecast moved.",
            (
                "Dismissed: the report is hidden from every public list and its photo is no longer "
                "served. A later status brings it back."
                if body.status == "dismissed"
                else "Everyone who opens the citizen dashboard sees this status, your role and your note, never your name."
            ),
        ],
    }


@router.get(
    "/ops/reports",
    tags=["ops"],
    summary="Citizen reports as the desk sees them (exact coordinates; gated)",
)
def ops_reports(
    _gate: OpsRead,
    city: Annotated[str | None, Query(description="mumbai or chennai.")] = None,
    bbox: Annotated[str | None, Query(description="min_lon,min_lat,max_lon,max_lat")] = None,
    status_filter: Annotated[
        Literal["received", "seen", "crew_sent", "resolved", "dismissed"] | None,
        Query(alias="status"),
    ] = None,
    since: Annotated[str | None, Query(description="Received at or after, ISO 8601.")] = None,
    origin: Annotated[Literal["citizen", "seed"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """Every report, dismissed and out-of-area ones included, with exact coordinates and names.

    Behind the passphrase because a reporter's exact position is often their home. Not counted
    against the write window.
    """
    from varuna_api.routers.reports import select_reports

    body = select_reports(
        exact=True,
        city=city,
        bbox=bbox,
        status_filter=status_filter,
        since=since,
        origin=origin,
        limit=limit,
    )
    return {**body, "writes_enabled": writes_enabled()}


# ---- the log ----------------------------------------------------------------------------
def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """An ops entry as the ungated log prints it.

    A report status names the role and never the officer: ``POST /v1/ops/reports/{id}/status``
    tells the officer so, and ``GET /v1/reports`` keeps it. The ungated log has to keep it too,
    or ``?kind=report_status`` hands anyone every officer's name beside the report they handled.
    Other kinds are unchanged; a closure is a public fact (module docstring).
    """
    if entry.get("kind") != "report_status":
        return entry
    public = {key: value for key, value in entry.items() if key != "user"}
    public["user_withheld"] = True
    return public


@router.get("/ops/log", tags=["ops"], summary="The append-only authority log")
def ops_log(
    city: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 200,
    kind: Annotated[str | None, Query(description="Filter to one entry kind.")] = None,
    x_varuna_ops: Annotated[
        str | None,
        Header(description="Optional. With the desk passphrase, report statuses keep the name."),
    ] = None,
) -> dict[str, Any]:
    """Every authority edit for a city, newest first - the audit trail the desk is judged on.

    Ungated, with one exception: a report status's officer name is printed only when the request
    carries the desk passphrase. Without it the entry keeps its role and says the name was
    withheld; a wrong passphrase is not refused, it just gets the public answer.
    """
    from varuna_route import ops_overlay as ops

    name = _city(city)
    rows = ops.entries(name)
    if kind:
        if kind not in ops.KINDS:
            raise api_error(
                422,
                "bad_kind",
                f"No ops entry kind {kind!r}. Valid kinds: {', '.join(sorted(ops.KINDS))}.",
            )
        rows = [r for r in rows if r.get("kind") == kind]
    newest = list(reversed(rows))[:limit]
    desk = presents_passphrase(x_varuna_ops)
    if not desk:
        newest = [_public_entry(row) for row in newest]
    return {
        "city": name,
        "n_entries": len(rows),
        "n_returned": len(newest),
        "entries": newest,
        "officer_names": "shown" if desk else "withheld",
        "writes_enabled": writes_enabled(),
        "passphrase_env": PASSPHRASE_ENV,
        # The sentence a screen prints when writes are off, as its own field so no client has to
        # find it among the notes by matching its words.
        "writes_disabled_reason": (
            None
            if writes_enabled()
            else f"This API is read-only: {PASSPHRASE_ENV} is not set where it runs."
        ),
        "notes": [
            NO_FORECAST_CHANGED,
            (
                "Writes are enabled on this API."
                if writes_enabled()
                else f"This API is read-only: {PASSPHRASE_ENV} is not set where it runs."
            ),
            (
                "Officer names on report statuses are shown because this request carries the "
                "desk passphrase."
                if desk
                else "Report statuses name the officer's role, not the officer. Send the desk "
                f"passphrase in {OPS_HEADER} to see the name."
            ),
        ],
    }


# ---- pumps: optimise and dispatch --------------------------------------------------------
def _pump_overrides(city: str) -> tuple[dict[str, str], dict[str, tuple[float, float]]]:
    """The desk's pump statuses and moved depots, for :func:`build_pump_plan`."""
    from varuna_route import ops_overlay as ops

    overlay = ops.active(city)
    statuses = {pid: p.status for pid, p in overlay.pumps.items()}
    depots = {
        pid: (p.lon, p.lat)
        for pid, p in overlay.pumps.items()
        if p.lon is not None and p.lat is not None
    }
    return statuses, depots


def _plan_inputs(run_id: str | None, city: str) -> dict[str, Any]:
    """What a pump plan is computed from, for a run on disk: the same for optimise and price.

    The run's register hotspots and wet streets, the storm it was driven by (so the benefit is
    the emulator's) and the desk's pump statuses. ``notes`` says what was missing and what the
    benefit fell back to, because every number the board prints carries its model (rule 6).
    """
    from varuna_products.alerts import STREET_POINTS, street_series
    from varuna_products.depth import segment_names, segment_points
    from varuna_products.pumps import rain_for_run

    root = city_dir(city)
    if not (root / "assets.geojson").is_file():
        raise api_error(
            503,
            "no_city",
            f"There is no pump inventory for {city}: {root / 'assets.geojson'} is missing, so "
            f"there is no fleet to assign. Run `make city CITY={city}`.",
        )
    path = _run_path(run_id, city, "hotspots.json")
    notes: list[str] = [
        "The plan was computed now and not written into the run; the run's own pump_plan.json "
        "is the one the cycle produced and is unchanged."
    ]

    hotspots = json.loads((path / "hotspots.json").read_text(encoding="utf-8"))
    if isinstance(hotspots, dict):
        hotspots = hotspots.get("hotspots", [])

    streets: dict[str, list[float]] = {}
    points: dict[str, tuple[float, float]] = {}
    segments_readable = True
    wet = path / "segments_wet.json"
    try:
        if wet.is_file():
            depth = json.loads(wet.read_text(encoding="utf-8")).get("depth_cm", {})
            streets = street_series(
                {sid: [float(v) for v in series] for sid, series in depth.items()},
                segment_names(root),
                segment_points(root),
            )
            points = dict(STREET_POINTS)
        else:
            notes.append(
                f"Run {path.name} carries no segments_wet.json, so only the chronic register "
                "was a candidate; named streets were not."
            )
    except (OSError, ValueError, KeyError) as error:
        segments_readable = False
        notes.append(
            f"Street candidates were skipped: the city's segment table could not be read "
            f"({error}). Only the chronic hotspot register was considered."
        )

    rain = rain_for_run(path.name)
    if rain and not segments_readable:
        # The emulator prices a candidate by slicing itself down to that candidate's road
        # segments, which it finds through the same table that just failed to read. Handing it
        # the storm anyway would make it raise mid-plan; withholding it falls back to the
        # bathtub model, which needs no city at all, and the label says which one ran.
        rain = None
        notes.append(
            "The emulator was not used: it prices a pump on the candidate's own road segments "
            "and this city's segment table could not be read."
        )
    if not rain:
        notes.append(
            "The benefit is the bathtub estimate rather than the emulator; benefit_model and "
            "benefit_label say so beside every number."
        )
    statuses, depots = _pump_overrides(city)
    return {
        "path": path,
        "root": root,
        "hotspots": hotspots,
        "streets": streets,
        "points": points,
        "rain": rain,
        "statuses": statuses,
        "depots": depots,
        "notes": notes,
    }


def _optimise(run_id: str | None, city: str, solver: str) -> dict[str, Any]:
    """Re-run the greedy for a run, honouring the desk's pump statuses.

    The plan is returned and **not written into the run**: the run directory is what the cycle
    produced, and an officer pressing Optimise must not change it (rule 8).
    """
    from time import perf_counter

    from varuna_products.pumps import build_pump_plan

    if solver == "milp":
        raise api_error(
            501,
            "not_implemented",
            "The MILP solver is P1 (task P8.9). The greedy of SPEC.md 11.10 is what runs "
            "today; send solver='greedy'.",
        )

    inputs = _plan_inputs(run_id, city)
    path: Path = inputs["path"]
    notes: list[str] = inputs["notes"]

    started = perf_counter()
    plan = build_pump_plan(
        inputs["hotspots"],
        inputs["root"],
        path.name,
        STEP_MIN,
        inputs["streets"],
        inputs["points"],
        rain_mm_h=inputs["rain"],
        pump_status=inputs["statuses"],
        pump_depots=inputs["depots"],
    )
    plan["solver"] = "greedy"
    plan["solve_ms"] = round((perf_counter() - started) * 1000.0)
    plan["city"] = city
    if plan.get("withheld"):
        held = ", ".join(f"{w['pump_id']} ({w['status']})" for w in plan["withheld"])
        notes.append(f"Withheld by the desk and not assigned: {held}.")
    plan["notes"] = notes
    log.info(
        "api.pumps_optimise",
        run_id=path.name,
        assigned=len(plan.get("assignments", [])),
        withheld=len(plan.get("withheld", [])),
        ms=plan["solve_ms"],
    )
    return plan


@router.post("/pumps/optimise", tags=["pumps"], summary="Re-run the greedy optimiser on demand")
def pumps_optimise(_gate: OpsWrite, body: PumpOptimiseRequest | None = None) -> dict[str, Any]:
    """Assign the fleet to the places that flood, honouring what the desk has marked.

    Gated with the writes even though it stores nothing: it is one of the desk's acts, and the
    greedy re-prices every candidate through the emulator, which is not something an
    unauthenticated caller should be able to ask for thirty times a second.
    """
    request = body or PumpOptimiseRequest()
    return _optimise(request.run_id, _city(request.city), request.solver)


class PumpPriceRequest(VarunaModel):
    """Body of ``POST /v1/pumps/price``: the board as the operator arranged it."""

    run_id: str | None = None
    city: str | None = None
    placements: list[dict[str, str]] = Field(
        default_factory=list,
        max_length=64,
        description="[{pump_id, hotspot_id}] - where each pump sits on the board now.",
    )


@router.post(
    "/pumps/price",
    tags=["pumps"],
    summary="Price a pump plan the operator arranged by hand (emulator)",
)
def pumps_price(body: PumpPriceRequest) -> dict[str, Any]:
    """The benefit of the plan on the board, whoever made it (SPEC.md 7.6 AC2).

    The same model and arithmetic the greedy uses, so an unmoved plan prices to the optimiser's
    own minutes; a moved one gets its own figure instead of the optimiser's stale one. Ungated,
    like ``/v1/whatif``: it answers a question about a plan, records nothing and changes nothing,
    and a board anyone can drag has to be able to ask it. Bounded at 64 placements.
    """
    from time import perf_counter

    from varuna_products.pumps import price_placements

    city = _city(body.city)
    inputs = _plan_inputs(body.run_id, city)
    path: Path = inputs["path"]
    placements: list[tuple[str, str]] = []
    for row in body.placements:
        pump_id = str(row.get("pump_id", "")).strip()
        target = str(row.get("hotspot_id", "")).strip()
        if not pump_id or not target:
            raise api_error(
                422,
                "bad_placement",
                "Every placement needs a pump_id and the hotspot_id it sits on.",
                run_id=path.name,
            )
        placements.append((pump_id, target))

    started = perf_counter()
    priced = price_placements(
        inputs["hotspots"],
        inputs["root"],
        path.name,
        placements,
        STEP_MIN,
        inputs["streets"],
        inputs["points"],
        rain_mm_h=inputs["rain"],
        pump_status=inputs["statuses"],
        pump_depots=inputs["depots"],
    )
    priced["price_ms"] = round((perf_counter() - started) * 1000.0)
    priced["city"] = city
    priced["notes"] = [
        *inputs["notes"],
        "Priced now for the board as arranged; nothing was recorded and the run is unchanged.",
    ]
    log.info(
        "api.pumps_price",
        run_id=path.name,
        placements=len(placements),
        saved=priced["total_minutes_saved"],
        ms=priced["price_ms"],
    )
    return priced


@router.get(
    "/pumps/map",
    tags=["pumps"],
    summary="The run's pump plan on the city: depots, roads, and depth with and without each pump",
)
def pumps_map(
    run_id: Annotated[str | None, Query()] = None,
    city: Annotated[str | None, Query()] = None,
    routes: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    """Each assignment of the run's own ``pump_plan.json``, drawn (Jalayantra, SPEC.md 7.6).

    Per leg: the depot and the place with coordinates, the plan's ETA, the depth series at the
    place with and without the pump (recomputed with the optimiser's own functions, with
    ``agrees`` saying whether the recount reproduces the plan's minutes), its window above 45 cm,
    and - with ``routes`` - the road a truck would take at the cycle time and what the pump buys
    if it arrives when that road says. Read-only and ungated; the first answer for a run is slow
    (the city's segment table and twelve routes) and is remembered, ``cached`` says which.
    """
    from varuna_api.pump_map import cached_dispatch_map

    return cached_dispatch_map(run_id, _city(city), routes=routes)


@router.get(
    "/pumps/cycles",
    tags=["pumps"],
    summary="Which cycles carry a pump plan, and how many pumps each sends",
)
def pumps_cycles(city: Annotated[str | None, Query()] = None) -> dict[str, Any]:
    """Every run of the city with a ``pump_plan.json``, oldest first, with its counts.

    For the cycle that sends no pump: Jalayantra names the cycles that do and offers the one whose
    plan avoids the most minutes above 45 cm (``busiest_run_id``). Read-only and ungated; nothing
    is recomputed.
    """
    from varuna_api.pump_map import pump_cycles

    return pump_cycles(_city(city))


def _dispatch_messages(
    run_id: str, city: str, orders: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The phone-mock message and alert instruction each dispatched place now carries.

    Read back through :func:`apply_alert_state` after the dispatch was appended, so the message is
    the one ``GET /v1/alerts`` will serve for that alert - not a second composition of it. A place
    this run raised no alert about still gets the order itself as its message.
    """
    from varuna_products.notify import whatsapp_text

    path = run_dir(run_id)
    # A run with no alert product still dispatches; its places just have no alert to carry it.
    queue = apply_alert_state(_alert_queue(path), city) if (path / "alerts.json").is_file() else []
    by_place = {_alert_place(a): a for a in queue}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for order in orders:
        place = str(order["hotspot_id"])
        if place in seen:
            continue
        seen.add(place)
        alert = by_place.get(place)
        pumps = [o["pump_id"] for o in orders if o["hotspot_id"] == place]
        instruction = f"Pump{'s' if len(pumps) > 1 else ''} {', '.join(pumps)} dispatched."
        text = (
            whatsapp_text(alert)
            if alert is not None
            else "\n".join(
                [
                    "VARUNA pump order (exercise)",
                    *(o["order_text"] for o in orders if o["hotspot_id"] == place),
                ]
            )
        )
        out.append(
            {
                "hotspot_id": place,
                "hotspot_name": order.get("hotspot_name"),
                "alert_id": alert.get("id") if alert else None,
                "instruction": instruction,
                "text": text,
            }
        )
    return out


@router.post(
    "/pumps/dispatch",
    tags=["pumps"],
    status_code=202,
    summary="Dispatch the plan (records the order; sends no lorry)",
)
def pumps_dispatch(body: PumpDispatchRequest, _gate: OpsWrite) -> dict[str, Any]:
    """Record a dispatch order for the current plan, and return it in plain language.

    202 rather than 200: the order is accepted and recorded, and nothing downstream of this
    prototype moves because of it. The inventory is synthetic, and the response says so.
    """
    city = _city(body.city)
    plan = _optimise(body.run_id, city, "greedy")
    wanted = {p.strip() for p in body.pump_ids if p.strip()}
    if wanted:
        unknown = sorted(wanted - {a["pump_id"] for a in plan["assignments"]})
        if unknown:
            raise api_error(
                422,
                "pump_not_in_plan",
                f"The current plan assigns nothing to {', '.join(unknown)}, so there is no "
                "order to dispatch for it. Optimise first, then dispatch what it assigned.",
            )
    chosen = [a for a in plan["assignments"] if not wanted or a["pump_id"] in wanted]
    if not chosen:
        raise api_error(
            409,
            "nothing_to_dispatch",
            "The optimiser assigned no pump on this run - no candidate crosses 45 cm, or every "
            "pump is withheld. There is no order to give.",
        )

    orders: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for assignment in chosen:
        text = (
            f"Move {assignment['pump_id']} from {assignment['depot']} to "
            f"{assignment['hotspot_name']} now; ETA {assignment['eta_min']} min; prevents about "
            f"{assignment['minutes_saved']} min above {plan['threshold_cm']:.0f} cm."
        )
        orders.append({**assignment, "order_text": text})
        entries.append(
            _append(
                city,
                {
                    "kind": "dispatch",
                    "pump_id": assignment["pump_id"],
                    "hotspot_id": assignment["hotspot_id"],
                    "hotspot_name": assignment["hotspot_name"],
                    "run_id": plan["run_id"],
                    "eta_min": assignment["eta_min"],
                    "minutes_saved": assignment["minutes_saved"],
                    "benefit_model": assignment["benefit_model"],
                    "order_text": text,
                    "user": body.user,
                    "note": body.note,
                },
            )
        )

    log.info("api.pumps_dispatch", run_id=plan["run_id"], n=len(orders), user=body.user, city=city)
    messages = _dispatch_messages(plan["run_id"], city, orders)
    return {
        "alert_instructions": [m["instruction"] for m in messages],
        "phone_messages": messages,
        "run_id": plan["run_id"],
        "city": city,
        "dispatched": True,
        "dispatched_by": body.user,
        "dispatched_ts": entries[-1]["ts"],
        "n_dispatched": len(orders),
        "orders": orders,
        "entries": entries,
        "benefit_model": plan["benefit_model"],
        "benefit_label": plan["benefit_label"],
        "synthetic_inventory": True,
        "notes": [
            *plan["notes"],
            NO_FORECAST_CHANGED,
            "The pump inventory is synthetic: this order is recorded in the ops log and no "
            "lorry is called.",
        ],
    }


__all__ = [
    "NO_FORECAST_CHANGED",
    "OPS_HEADER",
    "PASSPHRASE_ENV",
    "WRITES_PER_MINUTE",
    "ClosureRequest",
    "PumpStatusRequest",
    "ReportStatusRequest",
    "apply_alert_state",
    "presents_passphrase",
    "require_ops",
    "require_ops_read",
    "reset_rate_limit",
    "router",
    "writes_enabled",
]

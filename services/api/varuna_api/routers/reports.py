"""Citizen and field observation ingestion (SPEC.md 12, 7.11; task P9.4, completing P7.2).

A report is stored, de-duplicated through the same code path Pulse uses on the replay stream, and
acknowledged with the number of streets it will inform.

**What the acknowledgement can honestly say.** SPEC.md 7.11 wants "Thanks - your report improved
the forecast for 3 streets", and that number is Pulse's: segments whose p50 moves by more than
3 cm once the EnKF has assimilated the observation. That happens on the *next* cycle, not inside
this request - assimilating one report against the whole drain graph is seconds of work, and a
citizen pressing Send on a phone should not wait for it.

So the response returns what is true now: how many streets this run already has water on within
the radius the report can inform, and a message that says the forecast is updated on the next
cycle. `feedback_streets` stays null until a cycle has actually run, rather than carrying a
number this request did not compute (rule 6).

**Which city.** A report belongs to the city whose computation box holds it
(:data:`varuna_pulse.reports.REPORT_AOIS`, SPEC.md 3.3). One from anywhere else is kept, tagged
``outside_aoi`` with no city, and never mapped or assimilated - and the message says so rather
than thanking the reporter for improving a forecast nobody runs there.

**Photos.** A photo arrives as a JPEG, PNG or WebP data URL of at most 700 kB,
inside a body of at most 1 MB that :class:`ReportBodyLimit` measures before anything parses it. It
is decoded only after its magic bytes say what it is, refused above 40 million pixels before the
pixels are read, turned upright from its EXIF orientation, and re-encoded from raw RGB to a JPEG
that carries no metadata at all - a phone photo's GPS tag names where the reporter lives as often
as where the water is. Two copies are kept, 1280 px and 330 px, each written to a temporary name
and renamed. ``VARUNA_REPORT_PHOTOS=0`` turns storage off (the deployed image sets it: its volume
is 500 MB and the team decided photos are for the demo laptop), and a 100 MB total budget
(``VARUNA_REPORT_PHOTO_BUDGET_MB``) stops the disk filling. Either way the report is kept and the
response says the photo was not.

**What is public.** ``GET /v1/reports`` rounds every coordinate to three decimals (about 110 m),
names an officer by role and never by name, hides dismissed reports, and never lists one from
outside the boxes. Exact coordinates and names are in ``GET /v1/ops/reports``, behind the desk's
passphrase. A report's status is not part of the report: the desk appends ``report_status`` to its
city's ops log and it is folded in here at read time, so the inbox stays exactly what people sent.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import math
import os
import re
import secrets
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from varuna_pulse.reports import REPORT_AOIS, city_for_point
from varuna_schemas.models import ErrorEnvelope
from varuna_schemas.paths import data_dir
from varuna_schemas.settings import get_settings

from varuna_api.runs_util import latest_run_for
from varuna_api.state import api_error

log = structlog.get_logger("varuna.api.reports")

router = APIRouter(prefix="/v1", tags=["observations"])

IST = timezone(timedelta(hours=5, minutes=30))

INFORM_RADIUS_M = 400.0
"""How far a report can speak for.

A person reports the water they are standing in. The pipes under the next four hundred metres are
the ones an assimilation can plausibly move from it - beyond that the hydraulic connection is
weaker than the noise. The same order as `varuna_pulse.enkf`'s three-hop localisation."""

DEPTH_CHIPS: dict[str, float] = {"ankle": 10.0, "knee": 45.0, "waist": 90.0}
"""Body landmarks to centimetres (SPEC.md 11.6). Kept in step with `varuna_pulse.reports`."""

REPORT_STATUSES = ("received", "seen", "crew_sent", "resolved", "dismissed")
"""Kept in step with ``varuna_route.ops_overlay.REPORT_STATES``; a report nobody has acted on
reads ``received``."""

OFFICER_ROLES = ("ward officer", "control room", "field crew")
"""Who set a status, as the public sees it. The officer's name stays on the desk."""

# ---- limits -------------------------------------------------------------------------------
REPORT_BODY_LIMIT = 1_000_000
"""Bytes a ``POST /v1/reports`` body may carry, photo included. Checked before parsing."""

PHOTO_DATA_URL_MAX = 700_000
"""Characters of ``photo_data_url``: about 520 kB of image once the base64 is undone. The report
flow downscales to 1280 px on the phone first, which lands at 120-300 kB."""

REPORTS_PER_MINUTE = 6
"""Per client (the first ``X-Forwarded-For`` address, else the socket's), per rolling minute."""

REPORTS_PER_MINUTE_ALL = 120
"""Per process. ``X-Forwarded-For`` is whatever the client wrote when no proxy rewrites it, so a
sender rotating fake addresses would never meet the per-client limit; this one it meets."""

PHOTO_ENV = "VARUNA_REPORT_PHOTOS"
PHOTO_BUDGET_ENV = "VARUNA_REPORT_PHOTO_BUDGET_MB"
DEFAULT_PHOTO_BUDGET_MB = 100.0

MAX_PHOTO_PIXELS = 40_000_000
"""Pixels a photo may declare before it is decoded: a 40 MP phone camera, and nowhere near the
tens of gigabytes a crafted header can ask a decoder to allocate."""

FULL_PX = 1280
THUMB_PX = 330
FULL_QUALITY = 82
THUMB_QUALITY = 76

PUBLIC_DECIMALS = 3
"""Three decimals is about 110 m: enough to put a report on the right junction, not on a door."""

REPORT_ID_RE = re.compile(r"rpt-\d{10,16}-[0-9a-f]{6}")
"""The ids :func:`create_report` mints. Anything else never reaches the file system."""

_DATA_URL_RE = re.compile(r"data:image/(jpeg|jpg|png|webp);base64,([A-Za-z0-9+/=\s]+)", re.I)

_PIL_FORMATS: dict[str, frozenset[str]] = {
    "JPEG": frozenset({"JPEG", "MPO"}),
    "PNG": frozenset({"PNG"}),
    "WEBP": frozenset({"WEBP"}),
}
"""What Pillow may call each magic-byte format. Phones write multi-picture JPEGs, which Pillow
opens as MPO; the first picture is the photo."""

_MAGIC: tuple[tuple[str, bytes, int], ...] = (
    ("JPEG", b"\xff\xd8\xff", 0),
    ("PNG", b"\x89PNG\r\n\x1a\n", 0),
    ("WEBP", b"WEBP", 8),
)
"""Leading bytes of the three formats accepted, and where they sit. WebP is ``RIFF....WEBP``."""


# ---- paths -----------------------------------------------------------------------------------
def _inbox() -> Path:
    return Path(data_dir()) / "reports" / "inbox.jsonl"


def photos_dir() -> Path:
    """``data/reports/photos``: ``<id>.jpg`` (1280 px) and ``<id>.thumb.jpg`` (330 px)."""
    return Path(data_dir()) / "reports" / "photos"


def _photo_path(report_id: str, size: Literal["full", "thumb"]) -> Path:
    if not REPORT_ID_RE.fullmatch(report_id):
        msg = f"not a report id: {report_id!r}"
        raise ValueError(msg)
    return photos_dir() / (f"{report_id}.thumb.jpg" if size == "thumb" else f"{report_id}.jpg")


# ---- body size and rate --------------------------------------------------------------------
class ReportBodyLimit:
    """Refuse a ``POST /v1/reports`` body over :data:`REPORT_BODY_LIMIT` before it is parsed.

    ASGI rather than a dependency, because FastAPI has read and JSON-decoded the whole body by
    the time a dependency runs. A declared ``Content-Length`` over the limit is refused without
    reading a byte; a chunked body is counted as it arrives and refused the moment it passes the
    limit. What fits is buffered - it is at most a megabyte - and replayed to the app. Add it
    inside the CORS middleware, so a browser can read the 413.
    """

    def __init__(self, app: ASGIApp, limit: int = REPORT_BODY_LIMIT) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path", "").rstrip("/") != "/v1/reports"
        ):
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                await _refuse(
                    scope, send, 400, "bad_content_length", "Content-Length is not a number."
                )
                return
            if size > self.limit:
                await self._too_large(scope, send, size)
                return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.limit:
                await self._too_large(scope, send, total)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _too_large(self, scope: Scope, send: Send, size: int) -> None:
        await _refuse(
            scope,
            send,
            413,
            "report_too_large",
            f"A report with its photo can be at most {self.limit // 1000} kB and this one is "
            f"{math.ceil(size / 1000)} kB. Nothing was stored. Send it again with a smaller "
            "photo, or without one.",
        )


async def _refuse(scope: Scope, send: Send, status_code: int, code: str, message: str) -> None:
    """Answer with the section 12 envelope without reading the request any further."""
    body = ErrorEnvelope.make(code, message, None).model_dump(mode="json")
    response = JSONResponse(status_code=status_code, content=body, headers={"Connection": "close"})
    await response(scope, _no_receive, send)


async def _no_receive() -> Message:  # pragma: no cover - a JSONResponse never reads
    return {"type": "http.disconnect"}


_rate_lock = threading.Lock()
_per_client: dict[str, deque[float]] = {}
_all_posts: deque[float] = deque()
_WINDOW_S = 60.0


def reset_rate_limit() -> None:
    """Forget every window. Tests call it; nothing in the app does."""
    with _rate_lock:
        _per_client.clear()
        _all_posts.clear()


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip() if forwarded else ""
    if first:
        return first[:64]
    return request.client.host if request.client else "unknown"


def limit_reports(request: Request) -> None:
    """Six reports a minute from one client, 120 from everyone - checked before the body is
    validated or a photo decoded.

    Every attempt counts, a malformed one included: the limit exists for a sender that is not a
    person at a flooded junction, and that sender's malformed posts cost the same work.
    """
    now = time.monotonic()
    key = _client_key(request)
    with _rate_lock:
        while _all_posts and now - _all_posts[0] > _WINDOW_S:
            _all_posts.popleft()
        window = _per_client.setdefault(key, deque())
        while window and now - window[0] > _WINDOW_S:
            window.popleft()
        # A client whose newest post is over a minute old holds nothing worth keeping. Pruning by
        # the newest stamp bounds the table by the global limit, even for a sender rotating
        # forged X-Forwarded-For addresses.
        for stale in [
            k for k, w in _per_client.items() if k != key and (not w or now - w[-1] > _WINDOW_S)
        ]:
            del _per_client[stale]
        if len(window) >= REPORTS_PER_MINUTE:
            wait = max(1, round(_WINDOW_S - (now - window[0])))
            raise api_error(
                429,
                "rate_limited",
                f"{REPORTS_PER_MINUTE} reports a minute from one phone is the limit. Wait "
                f"{wait} s and send it again; nothing was stored.",
            )
        if len(_all_posts) >= REPORTS_PER_MINUTE_ALL:
            wait = max(1, round(_WINDOW_S - (now - _all_posts[0])))
            raise api_error(
                429,
                "rate_limited",
                f"This API is taking {REPORTS_PER_MINUTE_ALL} reports a minute, which is its "
                f"limit. Wait {wait} s and send it again; nothing was stored.",
            )
        window.append(now)
        _all_posts.append(now)


# ---- request -------------------------------------------------------------------------------
class ReportRequest(BaseModel):
    """Body of ``POST /v1/reports``.

    Unknown fields are ignored rather than refused: a report queued offline by an older build of
    the report flow is still a report when the connection comes back.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    ts: str | None = Field(
        default=None,
        max_length=40,
        description="When the water was seen, ISO 8601 with an offset. Omitted means now.",
    )
    lat: float = Field(ge=-90.0, le=90.0, allow_inf_nan=False)
    lon: float = Field(ge=-180.0, le=180.0, allow_inf_nan=False)
    depth_hint: Literal["ankle", "knee", "waist"] = Field(
        description="ankle (about 10 cm), knee (about 45 cm) or waist (about 90 cm)."
    )
    text: str | None = Field(default=None, max_length=280, description="The reporter's own words.")
    photo_data_url: str | None = Field(
        default=None,
        max_length=PHOTO_DATA_URL_MAX,
        description="data:image/jpeg|png|webp;base64,... - re-encoded without metadata if kept.",
    )
    source: str = Field(default="public-map", max_length=40)

    @field_validator("depth_hint", mode="before")
    @classmethod
    def _lower(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value


def _report_time(raw: str | None, now: datetime) -> str:
    if not raw:
        return now.isoformat()
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise api_error(
            422,
            "bad_time",
            "ts must be ISO 8601 with an offset, e.g. 2019-07-02T08:40:00+05:30.",
        ) from None
    if parsed.tzinfo is None:
        # Pulse cannot put a time with no zone on the cycle clock, and guessing one would invent
        # the thing the report is evidence about (see `varuna_pulse.reports.observations_from`).
        raise api_error(
            422,
            "bad_time",
            "ts carries no offset, so it cannot be placed on the forecast clock. Send it with one, "
            "e.g. 2019-07-02T08:40:00+05:30, or leave ts out to mean now.",
        )
    return parsed.isoformat()


# ---- photos --------------------------------------------------------------------------------
def photos_enabled() -> bool:
    """``VARUNA_REPORT_PHOTOS``: on unless it says 0, false, no or off."""
    raw = os.environ.get(PHOTO_ENV, "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def photo_budget_bytes() -> int:
    raw = os.environ.get(PHOTO_BUDGET_ENV, "")
    try:
        mb = float(raw) if raw.strip() else DEFAULT_PHOTO_BUDGET_MB
    except ValueError:
        mb = DEFAULT_PHOTO_BUDGET_MB
    return max(0, int(mb * 1_000_000))


def _photos_bytes() -> int:
    folder = photos_dir()
    if not folder.is_dir():
        return 0
    return sum(p.stat().st_size for p in folder.glob("*.jpg") if p.is_file())


def _bad_photo(why: str) -> Exception:
    return api_error(
        422,
        "bad_photo",
        f"{why} Nothing was stored. Send the report again without the photo, or attach a JPEG, "
        "PNG or WebP image.",
    )


def _decode_photo(data_url: str) -> tuple[bytes, bytes, int, int]:
    """A data URL to ``(full_jpeg, thumb_jpeg, width, height)``, or a 422 naming what was wrong.

    Returns JPEG bytes with no EXIF, no GPS, no ICC and no comment: the pixels are copied out as
    raw RGB into a fresh image before either copy is encoded, so nothing the phone wrote can
    ride along.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    match = _DATA_URL_RE.fullmatch(data_url.strip())
    if match is None:
        raise _bad_photo("The photo is not a base64 data URL of a JPEG, PNG or WebP image.")
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
    except (binascii.Error, ValueError):
        raise _bad_photo("The photo's base64 does not decode.") from None

    actual = next((name for name, magic, at in _MAGIC if raw[at : at + len(magic)] == magic), None)
    if actual is None:
        raise _bad_photo("The photo's bytes are not a JPEG, PNG or WebP image, whatever it says.")

    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.format not in _PIL_FORMATS[actual]:
                raise _bad_photo("The photo's contents do not match its own header.")
            width, height = opened.size
            if width < 1 or height < 1 or width * height > MAX_PHOTO_PIXELS:
                raise _bad_photo(
                    f"The photo declares {width} x {height} pixels; the limit is "
                    f"{MAX_PHOTO_PIXELS // 1_000_000} million."
                )
            opened.seek(0)
            opened.load()
            upright = ImageOps.exif_transpose(opened)
            if upright.mode in {"RGBA", "LA"} or (
                upright.mode == "P" and "transparency" in upright.info
            ):
                rgba = upright.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.getchannel("A"))
            else:
                flat = upright.convert("RGB")
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise _bad_photo("The photo could not be read as an image.") from None

    clean = Image.frombytes("RGB", flat.size, flat.tobytes())

    def encode(longest: int, quality: int) -> bytes:
        copy = clean.copy()
        copy.thumbnail((longest, longest), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        copy.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()

    full = encode(FULL_PX, FULL_QUALITY)
    thumb = encode(THUMB_PX, THUMB_QUALITY)
    return full, thumb, clean.width, clean.height


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _store_photo(report_id: str, data_url: str) -> dict[str, Any]:
    """Decode, check the budget, store. Returns what the row and the response say about it."""
    full, thumb, width, height = _decode_photo(data_url)
    budget = photo_budget_bytes()
    used = _photos_bytes()
    if used + len(full) + len(thumb) > budget:
        log.warning("api.report_photo_budget", used=used, budget=budget, report_id=report_id)
        return {
            "stored": False,
            "note": (
                f"The photo store on this API is full ({budget // 1_000_000} MB), so this report "
                "was kept without its photo."
            ),
        }
    _write_atomic(_photo_path(report_id, "thumb"), thumb)
    _write_atomic(_photo_path(report_id, "full"), full)
    return {
        "stored": True,
        "note": None,
        "meta": {"w": width, "h": height, "bytes": len(full), "thumb_bytes": len(thumb)},
    }


# ---- streets near --------------------------------------------------------------------------
def _metres(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    lon_m = 111_320.0 * math.cos(math.radians(lat1))
    return math.hypot((lon2 - lon1) * lon_m, (lat2 - lat1) * 110_540.0)


def _streets_near(lon: float, lat: float, city: str) -> tuple[int, str | None]:
    """Wet streets within :data:`INFORM_RADIUS_M` of a point in the city's newest run.

    Zero and no run when the city has no run with a street product, or its road graph is not
    built: a report is kept either way, and a count this request could not compute is not
    invented.
    """
    try:
        path = latest_run_for(city, lambda p: (p / "segments_wet.json").is_file())
    except Exception:
        return 0, None
    if path is None:
        return 0, None
    try:
        from varuna_route.graph import load_graph

        wet = json.loads((path / "segments_wet.json").read_text(encoding="utf-8"))
        graph = load_graph(city)
    except Exception as error:
        log.warning("api.report_streets_skipped", city=city, error=repr(error))
        return 0, None
    first: dict[str, int] = {}
    for e, segment_id in enumerate(graph.edge_segment):
        first.setdefault(segment_id, e)

    count = 0
    for segment_id in wet.get("depth_cm", {}):
        e = first.get(segment_id)
        if e is None:
            continue
        tail = int(graph.edge_tail[e])
        if _metres(lon, lat, float(graph.lon[tail]), float(graph.lat[tail])) <= INFORM_RADIUS_M:
            count += 1
    return count, str(wet.get("run_id", path.name))


# ---- create --------------------------------------------------------------------------------
@router.post(
    "/reports",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Citizen or field observation (depth chips ankle/knee/waist, optional photo)",
    dependencies=[Depends(limit_reports)],
)
def create_report(body: ReportRequest) -> dict[str, Any]:
    """Accept one report and queue it for the next cycle's assimilation."""
    now = datetime.now(tz=IST).replace(microsecond=0)
    ts = _report_time(body.ts, now)
    city = city_for_point(body.lon, body.lat)
    # A millisecond clock is not unique enough to key a report on. Windows' system clock
    # advances in ~15.6 ms steps, so two reports submitted in the same tick - two people at the
    # same junction, or one person's double tap - would mint the same id, and Pulse dedupes on
    # id before it dedupes on place and time. The random suffix is per report, not per process,
    # so retries of the same submission stay distinct too.
    report_id = f"rpt-{int(time.time() * 1000):d}-{secrets.token_hex(3)}"

    photo_attached = bool(body.photo_data_url)
    photo: dict[str, Any] = {"stored": False, "note": None}
    if photo_attached and not photos_enabled():
        photo["note"] = (
            "Photos are stored only on the demo laptop; this API kept your report without its "
            "photo."
        )
    elif photo_attached and body.photo_data_url:
        photo = _store_photo(report_id, body.photo_data_url)

    row = {
        "id": report_id,
        "ts": ts,
        "received_at": now.isoformat(),
        "lat": body.lat,
        "lon": body.lon,
        "city": city,
        "outside_aoi": city is None,
        "depth_hint": body.depth_hint,
        "depth_cm": DEPTH_CHIPS[body.depth_hint],
        "text": body.text or None,
        "photo_attached": photo_attached,
        # Whether a photo can be served for this report. It used to mean "one was attached",
        # when nothing was stored; `photo_attached` says that now.
        "has_photo": bool(photo["stored"]),
        "photo": photo.get("meta"),
        "photo_note": photo["note"],
        "source": body.source or "public-map",
        "synthetic": False,
    }

    inbox = _inbox()
    inbox.parent.mkdir(parents=True, exist_ok=True)
    with inbox.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    near, run_id = _streets_near(body.lon, body.lat, city) if city else (0, None)
    log.info(
        "api.report",
        report_id=report_id,
        chip=body.depth_hint,
        city=city,
        near=near,
        run_id=run_id,
        photo=photo["stored"],
    )

    if city is None:
        message = (
            "Thanks. Your report is kept, but it is outside the areas VARUNA forecasts (central "
            "Mumbai and south Chennai), so it is not shown on the map or used in a forecast."
        )
    elif near:
        message = (
            f"Thanks. Your report is queued against {near} street{'' if near == 1 else 's'} "
            "VARUNA is already forecasting water on; the next cycle assimilates it."
        )
    else:
        message = "Thanks. Your report is queued; the next cycle assimilates it."
    if photo["note"]:
        message = f"{message} {photo['note']}"

    return {
        "id": report_id,
        "accepted": True,
        "status": "received",
        "city": city,
        "outside_aoi": city is None,
        "run_id": run_id,
        "streets_nearby": near,
        # Null on purpose: the count of streets whose forecast this report *changed* is Pulse's,
        # and Pulse runs on the next cycle. See the module docstring.
        "feedback_streets": None,
        "photo_attached": photo_attached,
        "photo_stored": bool(photo["stored"]),
        "photo_note": photo["note"],
        "photo_url": _served_url(report_id, "full") if photo["stored"] else None,
        "thumb_url": _served_url(report_id, "thumb") if photo["stored"] else None,
        "message": message,
    }


# ---- reading -------------------------------------------------------------------------------
def _served_url(report_id: str, size: Literal["full", "thumb"]) -> str:
    """Relative to the API's base URL; the console prefixes it."""
    return f"/v1/reports/{report_id}/photo?size={size}"


def _read_inbox() -> list[dict[str, Any]]:
    inbox = _inbox()
    if not inbox.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in inbox.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("id"):
            rows.append({**item, "origin": "citizen"})
    return rows


@lru_cache(maxsize=1)
def _seed_rows() -> tuple[dict[str, Any], ...]:
    path = Path(__file__).resolve().parent.parent / "seed_reports.json"
    if not path.is_file():
        return ()
    body = json.loads(path.read_text(encoding="utf-8"))
    return tuple({**row, "origin": "seed"} for row in body.get("reports", []))


def _seed_note() -> str:
    """What every list says about the seed, counted from the seed rather than written down."""
    rows = _seed_rows()
    with_photo = sum(1 for row in rows if row.get("photo"))
    seeded = sum(1 for row in rows if seeded_history(row))
    return (
        f"The {len(rows)} seed reports are synthetic: the 2 July 2019 replay bundle's own "
        f"synthetic reports at registered hotspots. {with_photo} carry an illustrative Wikimedia "
        "Commons photo, credited to its author and not taken at that spot or on that day. The "
        f"statuses on {seeded} of them are seeded demo statuses that no officer set; each is "
        "marked seeded."
    )


def all_reports() -> list[dict[str, Any]]:
    """Every report as stored: the inbox, then the committed demo seed."""
    return [*_read_inbox(), *(dict(row) for row in _seed_rows())]


def find_report(report_id: str) -> dict[str, Any] | None:
    """One stored report by id, from the inbox or the seed, or ``None``."""
    return next((row for row in all_reports() if str(row.get("id")) == report_id), None)


def report_city(row: dict[str, Any]) -> str | None:
    """The city a report belongs to: its tag, else the box it lies in (rows older than tags)."""
    if row.get("outside_aoi") is True:
        return None
    tagged = row.get("city")
    if isinstance(tagged, str) and tagged:
        return tagged
    try:
        return city_for_point(float(row["lon"]), float(row["lat"]))
    except (KeyError, TypeError, ValueError):
        return None


def status_city(row: dict[str, Any]) -> str:
    """Whose ops log holds a report's statuses: its city's, else the configured city's."""
    return report_city(row) or (get_settings().varuna_city or "mumbai").strip().lower()


def seeded_history(row: dict[str, Any]) -> list[dict[str, Any]]:
    """A seed report's demo statuses, as ``report_status`` entries marked ``seeded``.

    They live in ``seed_reports.json`` beside the report, never in an ops log: no officer set
    them, and every read says so. Only a seed row carries them.
    """
    if row.get("origin") != "seed":
        return []
    report_id = str(row.get("id"))
    out: list[dict[str, Any]] = []
    for entry in row.get("seeded_history") or []:
        if not isinstance(entry, dict) or entry.get("status") not in REPORT_STATUSES:
            continue
        out.append(
            {
                "kind": "report_status",
                "report_id": report_id,
                "status": entry["status"],
                "ts": entry.get("ts"),
                "role": entry.get("role"),
                "note": entry.get("note"),
                "user": None,
                "id": None,
                "seeded": True,
            }
        )
    return out


def report_histories() -> dict[str, list[dict[str, Any]]]:
    """Every report's statuses by id: a seed's demo statuses first, then every ``report_status``
    entry in the ops logs, in the order it was appended - so a real act at the desk is always the
    latest and always wins."""
    from varuna_route import ops_overlay as ops

    cities = {*REPORT_AOIS, (get_settings().varuna_city or "mumbai").strip().lower()}
    out: dict[str, list[dict[str, Any]]] = {}
    for row in _seed_rows():
        seeded = seeded_history(row)
        if seeded:
            out[str(row["id"])] = seeded
    for city in sorted(cities):
        try:
            rows = ops.entries(city)
        except ValueError:
            continue
        for entry in rows:
            if entry.get("kind") != "report_status":
                continue
            report_id = str(entry.get("report_id", "")).strip()
            if report_id:
                out.setdefault(report_id, []).append(entry)
    return out


def report_view(
    row: dict[str, Any], history: list[dict[str, Any]], *, exact: bool
) -> dict[str, Any]:
    """A stored report with its status folded in.

    ``exact`` is the desk's view: coordinates as sent and the officer's name on each status. The
    public view rounds coordinates to :data:`PUBLIC_DECIMALS` and names the role only.
    """
    report_id = str(row.get("id"))
    origin = str(row.get("origin", "citizen"))
    latest = history[-1] if history else None
    city = report_city(row)

    credit = None
    if origin == "seed":
        seed_photo = row.get("photo") or None
        has_photo = bool(seed_photo)
        photo_url = seed_photo.get("url") if seed_photo else None
        thumb_url = seed_photo.get("thumb_url") if seed_photo else None
        credit = seed_photo.get("credit") if seed_photo else None
        attached = has_photo
    else:
        has_photo = REPORT_ID_RE.fullmatch(report_id) is not None and (
            _photo_path(report_id, "full").is_file()
        )
        photo_url = _served_url(report_id, "full") if has_photo else None
        thumb_url = _served_url(report_id, "thumb") if has_photo else None
        # Rows written before `photo_attached` used `has_photo` for it.
        attached = bool(row.get("photo_attached", row.get("has_photo", False)))

    lat = float(row.get("lat", 0.0))
    lon = float(row.get("lon", 0.0))
    view: dict[str, Any] = {
        "id": report_id,
        "origin": origin,
        "synthetic": bool(row.get("synthetic", origin == "seed")),
        "ts": row.get("ts"),
        "received_at": row.get("received_at"),
        "lat": lat if exact else round(lat, PUBLIC_DECIMALS),
        "lon": lon if exact else round(lon, PUBLIC_DECIMALS),
        "coordinates": "exact" if exact else f"rounded to {PUBLIC_DECIMALS} decimals",
        "city": city,
        "outside_aoi": city is None,
        "place": row.get("place"),
        "depth_hint": row.get("depth_hint"),
        "depth_cm": row.get("depth_cm", DEPTH_CHIPS.get(str(row.get("depth_hint")), None)),
        "text": row.get("text") or None,
        "source": row.get("source") or ("seed" if origin == "seed" else "public-map"),
        "photo_attached": attached,
        "has_photo": has_photo,
        "photo_url": photo_url,
        "thumb_url": thumb_url,
        "photo_note": row.get("photo_note"),
        "credit": credit,
        "status": str(latest.get("status")) if latest else "received",
        "status_ts": latest.get("ts") if latest else None,
        # True when the status on screen is a seeded demo status no officer set.
        "status_seeded": bool(latest.get("seeded")) if latest else False,
        "history": [_history_view(entry, exact=exact) for entry in history],
    }
    if origin == "seed":
        view["bundle_report_id"] = row.get("bundle_report_id")
        view["hotspot_id"] = row.get("hotspot_id")
    return view


def _history_view(entry: dict[str, Any], *, exact: bool) -> dict[str, Any]:
    role = str(entry.get("role") or "ward officer")
    out: dict[str, Any] = {
        "status": entry.get("status"),
        "ts": entry.get("ts"),
        "role": role if role in OFFICER_ROLES else "ward officer",
        "note": entry.get("note") or None,
        "seeded": bool(entry.get("seeded")),
    }
    if exact:
        out["user"] = entry.get("user")
        out["entry_id"] = entry.get("id")
    return out


def _dismissed_view(row: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    """What the public sees of a dismissed report: that it was dismissed, and nothing it said."""
    latest = history[-1]
    return {
        "id": str(row.get("id")),
        "origin": str(row.get("origin", "citizen")),
        "status": "dismissed",
        "status_ts": latest.get("ts"),
        "history": [_history_view(entry, exact=False) for entry in history],
        "note": "The ward desk dismissed this report, so it is no longer shown on any map.",
    }


def _received(row: dict[str, Any]) -> datetime | None:
    for key in ("received_at", "ts"):
        value = row.get(key)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)
    return None


def _parse_bbox(raw: str | None) -> tuple[float, float, float, float] | None:
    if not raw:
        return None
    try:
        parts = [float(p) for p in raw.split(",")]
    except ValueError:
        parts = []
    if len(parts) != 4 or not all(math.isfinite(p) for p in parts):
        raise api_error(
            422,
            "bad_bbox",
            "bbox is min_lon,min_lat,max_lon,max_lat, e.g. 72.83,19.00,72.86,19.03.",
        )
    min_lon, min_lat, max_lon, max_lat = parts
    if min_lon > max_lon or min_lat > max_lat:
        raise api_error(422, "bad_bbox", "bbox's minimum is greater than its maximum.")
    return min_lon, min_lat, max_lon, max_lat


def _parse_since(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise api_error(
            422, "bad_time", "since must be ISO 8601, e.g. 2026-09-26T18:00:00+05:30."
        ) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)


def _check_city(city: str | None) -> str | None:
    if city is None:
        return None
    name = city.strip().lower()
    if name not in REPORT_AOIS:
        raise api_error(
            422,
            "bad_city",
            f"No reports are kept for {city!r}. Cities with a report area: "
            f"{', '.join(sorted(REPORT_AOIS))}.",
        )
    return name


def select_reports(
    *,
    exact: bool,
    city: str | None = None,
    bbox: str | None = None,
    status_filter: str | None = None,
    since: str | None = None,
    origin: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """The filtered, status-folded list both ``GET /v1/reports`` and the desk read."""
    name = _check_city(city)
    box = _parse_bbox(bbox)
    after = _parse_since(since)
    histories = report_histories()

    selected: list[tuple[datetime | None, dict[str, Any]]] = []
    n_dismissed = 0
    n_outside = 0
    for row in all_reports():
        history = histories.get(str(row.get("id")), [])
        state = str(history[-1].get("status")) if history else "received"
        row_city = report_city(row)
        # City, origin, box and time first, so the hidden counts below are of reports this query
        # would otherwise have returned. Counted before them, "1 dismissed report hidden" on the
        # Chennai view would be a Mumbai report.
        if name is not None and row_city != name:
            continue
        if origin is not None and str(row.get("origin")) != origin:
            continue
        if box is not None:
            try:
                lon, lat = float(row["lon"]), float(row["lat"])
            except (KeyError, TypeError, ValueError):
                continue
            if not exact:
                # The box is tested against the point the public view prints. Against the stored
                # one, halving a box thirty times recovers the position the rounding hides.
                lon, lat = round(lon, PUBLIC_DECIMALS), round(lat, PUBLIC_DECIMALS)
            if not (box[0] <= lon <= box[2] and box[1] <= lat <= box[3]):
                continue
        when = _received(row)
        if after is not None and (when is None or when < after):
            continue
        if not exact:
            # Hidden from the public before the status filter runs, so no status can select a
            # dismissed or out-of-area report. Each is counted only where the status filter would
            # have let it through: a dismissed report never matches ?status=seen.
            if state == "dismissed":
                if status_filter in (None, "dismissed"):
                    n_dismissed += 1
                continue
            if row_city is None:
                if status_filter is None or state == status_filter:
                    n_outside += 1
                continue
        if status_filter is not None and state != status_filter:
            continue
        selected.append((when, report_view(row, history, exact=exact)))

    floor = datetime.min.replace(tzinfo=UTC)
    selected.sort(key=lambda item: item[0] or floor, reverse=True)
    rows = [view for _, view in selected]
    notes = [_seed_note()]
    if not exact:
        notes.append(
            f"Coordinates are rounded to {PUBLIC_DECIMALS} decimals (about 110 m). Dismissed "
            "reports and reports outside the forecast areas are not listed."
        )
    return {
        "count": len(rows),
        "n_returned": min(len(rows), limit),
        "n_dismissed_hidden": n_dismissed,
        "n_outside_hidden": n_outside,
        "reports": rows[:limit],
        "notes": notes,
    }


@router.get("/reports", summary="Citizen reports with their status (public: rounded coordinates)")
def list_reports(
    city: Annotated[str | None, Query(description="mumbai or chennai.")] = None,
    bbox: Annotated[str | None, Query(description="min_lon,min_lat,max_lon,max_lat")] = None,
    status_filter: Annotated[
        Literal["received", "seen", "crew_sent", "resolved"] | None,
        Query(alias="status", description="Only reports whose latest status is this."),
    ] = None,
    since: Annotated[str | None, Query(description="Received at or after, ISO 8601.")] = None,
    origin: Annotated[Literal["citizen", "seed"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict[str, Any]:
    """Reports newest first, each with its latest status and the history that led to it.

    What the drain X-ray's timeline, the citizen dashboard and the desk's public inbox read. Every
    coordinate is rounded; the exact ones are in ``GET /v1/ops/reports``, behind the passphrase.
    """
    return select_reports(
        exact=False,
        city=city,
        bbox=bbox,
        status_filter=status_filter,
        since=since,
        origin=origin,
        limit=limit,
    )


@router.get("/reports/{report_id}", summary="One report and its status history (public)")
def get_report(report_id: str) -> dict[str, Any]:
    """What the reporter polls after pressing Send: the report, its status and who set it, by role.

    A dismissed report answers with its status and none of its content. One from outside the
    forecast areas answers in full, so the reporter can see it was kept and why it is not mapped.
    """
    if len(report_id) > 80:
        raise api_error(404, "report_not_found", "No report has that id.")
    row = find_report(report_id)
    if row is None:
        raise api_error(
            404,
            "report_not_found",
            f"No report {report_id} is kept here. A report queued offline gets its id when the "
            "connection returns.",
        )
    history = report_histories().get(report_id, [])
    if history and history[-1].get("status") == "dismissed":
        return _dismissed_view(row, history)
    return report_view(row, history, exact=False)


@router.get(
    "/reports/{report_id}/photo",
    summary="A report's photo, re-encoded without metadata (JPEG)",
    response_class=FileResponse,
    responses={200: {"content": {"image/jpeg": {}}}},
)
def report_photo(
    report_id: str,
    size: Annotated[Literal["thumb", "full"], Query()] = "thumb",
) -> FileResponse:
    """The stored photo, 330 px (``thumb``) or 1280 px (``full``) on its longest side.

    Only ids this API minted reach the file system. A dismissed report's photo is not served.
    Seed reports' photos are Wikimedia Commons links and are never served from here.
    """
    if not REPORT_ID_RE.fullmatch(report_id):
        raise api_error(404, "no_photo", "No photo is kept for that id.")
    history = report_histories().get(report_id, [])
    if history and history[-1].get("status") == "dismissed":
        raise api_error(
            404, "no_photo", "The ward desk dismissed this report; its photo is not shown."
        )
    path = _photo_path(report_id, size)
    if not path.is_file():
        raise api_error(404, "no_photo", f"Report {report_id} has no stored photo.")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=600",
            "Content-Disposition": "inline",
            "Cross-Origin-Resource-Policy": "cross-origin",
        },
    )


__all__ = [
    "MAX_PHOTO_PIXELS",
    "PHOTO_BUDGET_ENV",
    "PHOTO_DATA_URL_MAX",
    "PHOTO_ENV",
    "REPORTS_PER_MINUTE",
    "REPORT_BODY_LIMIT",
    "REPORT_STATUSES",
    "ReportBodyLimit",
    "ReportRequest",
    "all_reports",
    "find_report",
    "photos_dir",
    "report_histories",
    "report_view",
    "reset_rate_limit",
    "router",
    "select_reports",
    "status_city",
]

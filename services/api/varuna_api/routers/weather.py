"""Live weather for a city, proxied from Open-Meteo (TECH_SPEC 3.1; task D-05).

Everything else VARUNA serves is a forecast it computed. This is the one number on the citizen
screen that comes from outside: what the sky is doing now, and how much rain the next four hours
are expected to bring. It exists because a reader who opens the dashboard on a dry afternoon needs
to see something true immediately, before any baked run means anything to them.

**Why a proxy and not a browser fetch.** Three reasons, in order: the licence requires attribution
and a server can carry it in the payload rather than trusting each client to print it; one process
cache keeps the whole demo inside the free tier's 10,000 calls a day however many people load the
page; and the browser cannot fall back to a copy on our disk when the venue's network is gone.

**What it will and will not claim.** Open-Meteo answers for the grid cell containing the point,
not the point, so the response carries both `point` and `grid_point` and the distance between
them. `fetched_at`, `age_s` and `stale` are on every response, including the fresh ones, so a
screen never has to guess whether it is showing live weather or the last copy from before the
network went. Nothing here is mixed into a VARUNA product: it is context beside the forecast, not
an input to it.

**Caching, three deep.** The in-process cache (15 minutes, modelled on `routers/verify.py`'s
sweep cache, ADR-0058) answers the second reader. The last good response is also written to
`data/cache/weather-<city>.json`, so a restart, an upstream outage or `VARUNA_OFFLINE=1` still
has something honest to serve - stamped with its real age and flagged `stale`. When there is no
copy at all, the refusal is the section 12 envelope naming the reason, never a fabricated sky.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from varuna_schemas import net
from varuna_schemas.constants import IST
from varuna_schemas.models import CityConfig
from varuna_schemas.net import get_json
from varuna_schemas.paths import city_config_path, data_dir

from varuna_api.state import api_error

log = structlog.get_logger("varuna.api.weather")

router = APIRouter(prefix="/v1", tags=["weather"])

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE = "open-meteo"
SOURCE_URL = "https://open-meteo.com/"
LICENCE = "CC BY 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
"""The licence requires attribution wherever the data is shown. Every response carries it."""

TTL_S = 900.0
"""Fifteen minutes. Open-Meteo updates hourly and its `current` block every 15 minutes."""

HOURLY_STEPS = 4
REQUEST_HOURS = 6
"""Six asked for, four returned: the first steps can already be in the past (see `_hourly`).

Four hours after "now" is also exactly what `/v1/outlook` needs: a three-hour horizon that starts
part-way through an hour ends part-way through the fourth, and each hourly value is the sum over
the hour *before* its timestamp (Open-Meteo's definition), so the fourth step is the one that
covers the tail."""

MINUTELY_STEPS = 16
REQUEST_MINUTELY = 20
"""Four hours of Open-Meteo's 15-minute precipitation, asked for with a little margin for the same
reason as :data:`REQUEST_HOURS`. Outside Europe and North America that series is interpolated
from the hourly models (Open-Meteo's own documentation), so for Mumbai it carries no information
the hourly one does not; it is kept so `/v1/outlook` can say how the two compare rather than
assert it."""

MINUTELY_FIELDS = ("precipitation",)

TIMEOUT_S = 6.0
RETRIES = 1
"""One retry. A dashboard waiting on weather is worse than a dashboard saying it is stale."""

CURRENT_FIELDS = "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m"
HOURLY_FIELDS = ("precipitation", "precipitation_probability")

_CITY_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

# WMO 4677 present-weather codes as Open-Meteo groups them (https://open-meteo.com/en/docs).
# A label, not a judgement: the numbers are the source's and the words are the WMO table's.
WMO_LABELS: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


class GeoPoint(BaseModel):
    """A WGS84 point."""

    lon: float
    lat: float


class WeatherNow(BaseModel):
    """Open-Meteo's `current` block, renamed to VARUNA's vocabulary and units."""

    ts: datetime = Field(description="Observation time, ISO 8601 with the city's offset.")
    temperature_c: float | None = None
    humidity_pct: float | None = None
    precipitation_mm: float | None = Field(
        default=None, description="Rain in the source's last interval (15 minutes), millimetres."
    )
    wind_kmh: float | None = None
    weather_code: int | None = Field(default=None, description="WMO 4677 present-weather code.")
    weather: str | None = Field(default=None, description="The WMO table's label for the code.")


class WeatherStep(BaseModel):
    """One step of the short forecast: hourly, or 15-minutely in `Weather.minutely_15`."""

    ts: datetime = Field(description="End of the interval the precipitation was summed over.")
    precipitation_mm: float | None = Field(
        default=None,
        description="Precipitation summed over the interval before `ts` (the preceding hour for "
        "`hourly`, the preceding 15 minutes for `minutely_15`), millimetres.",
    )
    precipitation_probability_pct: float | None = None


class Weather(BaseModel):
    """Current conditions and the next four hours, with its provenance and its age."""

    city: str
    point: GeoPoint = Field(description="The city AOI centre this was asked for.")
    grid_point: GeoPoint = Field(description="The centre of the model cell that answered.")
    grid_offset_km: float = Field(description="Distance between the two, kilometres.")
    elevation_m: float | None = None
    current: WeatherNow
    hourly: list[WeatherStep]
    minutely_15: list[WeatherStep] = Field(
        default_factory=list,
        description="Open-Meteo's 15-minute precipitation for the next four hours. Outside Europe "
        "and North America it is interpolated from hourly models; empty in copies fetched before "
        "it was asked for.",
    )
    fetched_at: datetime = Field(description="When this copy was retrieved from Open-Meteo.")
    age_s: float = Field(description="Seconds since `fetched_at`, computed per request.")
    stale: bool = Field(description=f"True once `age_s` exceeds the {int(TTL_S)} s cache window.")
    ttl_s: float = TTL_S
    source: str = SOURCE
    source_url: str = SOURCE_URL
    licence: str = LICENCE
    licence_url: str = LICENCE_URL
    attribution: str = ATTRIBUTION
    notes: list[str] = Field(
        default_factory=list, description="Why this copy is what it is; printed by the UI."
    )


_cache: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    """Forget every kept response (tests)."""
    with _lock:
        _cache.clear()


class WeatherRefused(HTTPException):
    """The 503 for "no copy to serve", with the cause kept apart from any one page's framing.

    ``reason`` says what happened and how to fix it, and nothing about who is asking. The weather
    route appends the dashboard's note ("the flood forecast on this page does not depend on it");
    ``/v1/outlook``, whose answer is made of this rain, words its own refusal around ``reason``
    alone, so it never repeats a sentence that is false on its screen.
    """

    def __init__(self, code: str, reason: str, note: str = "") -> None:
        message = f"{reason} {note}".strip()
        super().__init__(status_code=503, detail={"code": code, "message": message, "run_id": None})
        self.code = code
        self.reason = reason


DASHBOARD_NOTE = "The flood forecast on this page does not depend on it."
"""Appended by the weather route only: true of the dashboard, false of the live outlook."""


def cache_path(city: str) -> Path:
    """Last good response on disk: ``data/cache/weather-<city>.json``."""
    return Path(data_dir()) / "cache" / f"weather-{city}.json"


def _city(raw: str) -> str:
    city = raw.strip().lower()
    if not _CITY_RE.match(city):
        raise api_error(
            400,
            "bad_city",
            f"{raw!r} is not a city slug. Use a lower-case name such as mumbai or chennai.",
        )
    return city


def _config(city: str) -> CityConfig:
    """The city's build config; its AOI centre is the point we ask about (never a literal)."""
    path = city_config_path(city)
    if not path.is_file():
        raise api_error(
            404,
            "city_not_configured",
            f"No city config at services/city/configs/{city}.yaml, so there is no point to ask "
            "Open-Meteo about. Configured cities: " + ", ".join(_configured_cities()) + ".",
        )
    try:
        return CityConfig.from_yaml(path)
    except (OSError, ValueError) as error:
        raise api_error(
            500, "city_config_invalid", f"{path.name} could not be read: {error}"
        ) from error


def _configured_cities() -> list[str]:
    folder = city_config_path("mumbai").parent
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.yaml"))


def _km(a: GeoPoint, b: GeoPoint) -> float:
    lon_km = 111.320 * math.cos(math.radians(a.lat))
    return math.hypot((b.lon - a.lon) * lon_km, (b.lat - a.lat) * 110.540)


def _at_offset(raw: str | None, offset_s: int) -> datetime | None:
    """Open-Meteo returns local wall-clock times plus the offset; SPEC.md 12 wants the offset on."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed
    return parsed.replace(tzinfo=_tz(offset_s))


def _tz(offset_s: int) -> timezone:
    """The source's own offset; IST when it is the expected +05:30, so the label reads "IST"."""
    return IST if offset_s == 19800 else timezone(timedelta(seconds=offset_s))


def _hourly(payload: dict[str, Any], offset_s: int, after: datetime | None) -> list[WeatherStep]:
    """The next :data:`HOURLY_STEPS` steps strictly after ``after``.

    Open-Meteo's `forecast_hours=n` counts from the top of the current hour, so the first step is
    usually already in the past. Six are requested and the ones that have happened are dropped,
    which is why "the next four hours" is the next four, not "this hour and three more".
    """
    return _series(payload, "hourly", offset_s, after, HOURLY_STEPS)


def _minutely(payload: dict[str, Any], offset_s: int, after: datetime | None) -> list[WeatherStep]:
    """The next :data:`MINUTELY_STEPS` 15-minute steps strictly after ``after``; see `_hourly`."""
    return _series(payload, "minutely_15", offset_s, after, MINUTELY_STEPS)


def _series(
    payload: dict[str, Any], block_name: str, offset_s: int, after: datetime | None, limit: int
) -> list[WeatherStep]:
    """One of Open-Meteo's time blocks as steps strictly after ``after``, at most ``limit``."""
    block = payload.get(block_name) or {}
    times = block.get("time") or []
    rain = block.get("precipitation") or []
    prob = block.get("precipitation_probability") or []
    steps: list[WeatherStep] = []
    for index, raw in enumerate(times):
        ts = _at_offset(raw, offset_s)
        if ts is None or (after is not None and ts <= after):
            continue
        steps.append(
            WeatherStep(
                ts=ts,
                precipitation_mm=_number(rain, index),
                precipitation_probability_pct=_number(prob, index),
            )
        )
        if len(steps) == limit:
            break
    return steps


def _number(values: list[Any], index: int) -> float | None:
    if index >= len(values):
        return None
    value = values[index]
    return float(value) if isinstance(value, (int, float)) else None


def _fetch(city: str, config: CityConfig) -> dict[str, Any]:
    """One live call to Open-Meteo, rendered into :class:`Weather` as a JSON-ready dict."""
    lon, lat = config.bbox.center
    raw = get_json(
        OPEN_METEO_URL,
        {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "current": CURRENT_FIELDS,
            "hourly": list(HOURLY_FIELDS),
            "forecast_hours": REQUEST_HOURS,
            "minutely_15": list(MINUTELY_FIELDS),
            "forecast_minutely_15": REQUEST_MINUTELY,
            "timezone": "Asia/Kolkata",
        },
        timeout=TIMEOUT_S,
        retries=RETRIES,
    )
    if not isinstance(raw, dict) or "current" not in raw:
        msg = "Open-Meteo answered without a `current` block"
        raise net.NetworkError(msg)

    offset_s = int(raw.get("utc_offset_seconds") or 19800)
    current = raw.get("current") or {}
    now_ts = _at_offset(current.get("time"), offset_s) or datetime.now(IST)
    code = current.get("weather_code")
    code_int = int(code) if isinstance(code, (int, float)) else None
    point = GeoPoint(lon=round(lon, 6), lat=round(lat, 6))
    grid = GeoPoint(
        lon=float(raw.get("longitude", lon)),
        lat=float(raw.get("latitude", lat)),
    )
    weather = Weather(
        city=city,
        point=point,
        grid_point=grid,
        grid_offset_km=round(_km(point, grid), 2),
        elevation_m=_as_float(raw.get("elevation")),
        current=WeatherNow(
            ts=now_ts,
            temperature_c=_as_float(current.get("temperature_2m")),
            humidity_pct=_as_float(current.get("relative_humidity_2m")),
            precipitation_mm=_as_float(current.get("precipitation")),
            wind_kmh=_as_float(current.get("wind_speed_10m")),
            weather_code=code_int,
            weather=WMO_LABELS.get(code_int) if code_int is not None else None,
        ),
        hourly=_hourly(raw, offset_s, now_ts),
        minutely_15=_minutely(raw, offset_s, now_ts),
        fetched_at=datetime.now(IST),
        age_s=0.0,
        stale=False,
        notes=[],
    )
    return weather.model_dump(mode="json")


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _write_disk(city: str, payload: dict[str, Any]) -> None:
    """Last good copy, written atomically so a crash mid-write cannot leave half a file."""
    path = cache_path(city)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as error:  # a read-only volume must not fail the request
        log.warning("weather.cache_write_failed", city=city, error=str(error))


def _read_disk(city: str) -> dict[str, Any] | None:
    path = cache_path(city)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(payload, dict) and payload.get("fetched_at"):
        return payload
    return None


def _cached(city: str) -> dict[str, Any] | None:
    """The newest copy this process can reach: memory first, then the last good file."""
    with _lock:
        hit = _cache.get(city)
    if hit is not None:
        return hit
    disk = _read_disk(city)
    if disk is not None:
        with _lock:
            _cache.setdefault(city, disk)
    return disk


def _store(city: str, payload: dict[str, Any]) -> None:
    with _lock:
        _cache[city] = payload
    _write_disk(city, payload)


def _age_s(payload: dict[str, Any]) -> float:
    fetched = _at_offset(str(payload.get("fetched_at")), 19800)
    if fetched is None:
        return float("inf")
    return max(0.0, (datetime.now(IST) - fetched).total_seconds())


def _stamp(payload: dict[str, Any], notes: list[str]) -> Weather:
    """Re-age a kept copy at serve time; the stored `age_s` is never the one returned."""
    age = _age_s(payload)
    body = {**payload, "age_s": round(age, 1), "stale": age > TTL_S}
    body["notes"] = [*payload.get("notes", []), *notes]
    return Weather.model_validate(body)


@router.get(
    "/weather",
    response_model=Weather,
    summary="Current conditions and the next four hours from Open-Meteo",
)
def weather(
    city: Annotated[str, Query(description="City slug, e.g. mumbai or chennai.")] = "mumbai",
) -> Weather:
    """Live weather for the city's AOI centre, cached for fifteen minutes.

    Degrades rather than fails: an upstream error, a timeout or `VARUNA_OFFLINE=1` serves the last
    good copy with its true age and `stale: true`. Only when no copy has ever been fetched does
    this answer 503, with the reason named.
    """
    return get_weather(city)


def get_weather(city: str) -> Weather:
    """The route's whole answer as a function, so `/v1/outlook` reads the same copy and cache.

    Raises the section 12 envelope (400, 404 or 503) exactly as the route does; a 503 is a
    :class:`WeatherRefused`, whose ``reason`` a caller can re-word for its own screen.
    """
    slug = _city(city)
    config = _config(slug)
    kept = _cached(slug)

    if kept is not None and _age_s(kept) < TTL_S:
        return _stamp(kept, [])

    if net.offline():
        if kept is not None:
            return _stamp(
                kept,
                [
                    "VARUNA_OFFLINE=1, so Open-Meteo was not contacted. This is the last copy "
                    "fetched before the network was switched off."
                ],
            )
        raise WeatherRefused(
            "offline",
            "VARUNA_OFFLINE=1 blocks outbound requests and no weather has been cached for "
            f"{slug} yet. Unset VARUNA_OFFLINE, or run once with the network to fill "
            f"data/cache/weather-{slug}.json.",
        )

    try:
        fresh = _fetch(slug, config)
    except net.NetworkError as error:
        log.warning("weather.upstream_failed", city=slug, error=str(error))
        if kept is not None:
            return _stamp(
                kept,
                [f"Open-Meteo could not be reached ({error}); showing the last good copy."],
            )
        raise WeatherRefused(
            "weather_unavailable",
            f"Open-Meteo could not be reached ({error}) and nothing has been cached for {slug}.",
            DASHBOARD_NOTE,
        ) from error

    _store(slug, fresh)
    log.info("weather.fetched", city=slug, steps=len(fresh.get("hourly", [])))
    return _stamp(fresh, [])


__all__ = [
    "ATTRIBUTION",
    "LICENCE",
    "LICENCE_URL",
    "OPEN_METEO_URL",
    "SOURCE",
    "SOURCE_URL",
    "TTL_S",
    "GeoPoint",
    "Weather",
    "WeatherNow",
    "WeatherRefused",
    "WeatherStep",
    "cache_path",
    "clear_cache",
    "get_weather",
    "router",
]

"""`GET /v1/weather`: the Open-Meteo proxy (TECH_SPEC 3.1; task D-05).

Every test here stubs `get_json`, so the suite never touches the network - and the counter that
stub keeps is the point of the first test: the second reader of the dashboard must cost nothing.
The rest are the three ways this endpoint is allowed to be less than live (cached, offline,
upstream down) and the one thing it must never do, which is answer 500 because someone else's
server did.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from varuna_api.routers import weather as weather_router
from varuna_schemas import net
from varuna_schemas.constants import IST

OPEN_METEO_SAMPLE: dict[str, Any] = {
    "latitude": 19.086115,
    "longitude": 72.85291,
    "utc_offset_seconds": 19800,
    "timezone": "Asia/Kolkata",
    "elevation": 5.0,
    "current": {
        "time": "2026-09-19T00:30",
        "interval": 900,
        "temperature_2m": 24.7,
        "relative_humidity_2m": 96,
        "precipitation": 0.4,
        "weather_code": 80,
        "wind_speed_10m": 10.4,
    },
    "hourly": {
        "time": [
            "2026-09-19T00:00",
            "2026-09-19T01:00",
            "2026-09-19T02:00",
            "2026-09-19T03:00",
            "2026-09-19T04:00",
            "2026-09-19T05:00",
        ],
        "precipitation": [1.5, 2.8, 5.3, 3.4, 1.1, 0.2],
        "precipitation_probability": [58, 81, 98, 100, 62, 30],
    },
}


class Upstream:
    """A stand-in for `varuna_schemas.net.get_json` that counts what would have left the process."""

    def __init__(self, payload: Any = None, error: Exception | None = None) -> None:
        self.payload = payload if payload is not None else OPEN_METEO_SAMPLE
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        self.calls.append({"url": url, "params": params or {}})
        if self.error is not None:
            raise self.error
        return self.payload


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A private data folder and an empty process cache, so tests cannot see each other."""
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VARUNA_OFFLINE", "0")
    weather_router.clear_cache()
    yield
    weather_router.clear_cache()


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> Upstream:
    stub = Upstream()
    monkeypatch.setattr(weather_router, "get_json", stub)
    return stub


def _fail_with(monkeypatch: pytest.MonkeyPatch, error: Exception) -> Upstream:
    stub = Upstream(error=error)
    monkeypatch.setattr(weather_router, "get_json", stub)
    return stub


# ---- the live path ---------------------------------------------------------


def test_returns_current_conditions_and_four_hourly_steps(
    client: TestClient, upstream: Upstream
) -> None:
    response = client.get("/v1/weather", params={"city": "mumbai"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["city"] == "mumbai"
    assert body["current"]["temperature_c"] == 24.7
    assert body["current"]["precipitation_mm"] == 0.4
    assert body["current"]["weather_code"] == 80
    assert body["current"]["weather"] == "Slight rain showers"
    assert body["current"]["wind_kmh"] == 10.4
    assert len(body["hourly"]) == 4
    assert [step["precipitation_probability_pct"] for step in body["hourly"]] == [81, 98, 100, 62]
    assert all(step["precipitation_mm"] is not None for step in body["hourly"])
    assert body["stale"] is False
    assert body["age_s"] < 5


def test_the_next_four_hours_start_after_now(client: TestClient, upstream: Upstream) -> None:
    """Open-Meteo counts from the top of the current hour, so its first step is already past."""
    body = client.get("/v1/weather").json()
    first = datetime.fromisoformat(body["hourly"][0]["ts"])
    now = datetime.fromisoformat(body["current"]["ts"])
    assert first > now
    assert first.utcoffset() == timedelta(hours=5, minutes=30)


def test_asks_open_meteo_about_the_city_config_centre(
    client: TestClient, upstream: Upstream
) -> None:
    """The coordinates come from services/city/configs/<city>.yaml, never a literal."""
    from varuna_schemas.models import CityConfig
    from varuna_schemas.paths import city_config_path

    client.get("/v1/weather", params={"city": "mumbai"})
    lon, lat = CityConfig.from_yaml(city_config_path("mumbai")).bbox.center

    assert len(upstream.calls) == 1
    params = upstream.calls[0]["params"]
    assert upstream.calls[0]["url"] == weather_router.OPEN_METEO_URL
    assert params["latitude"] == pytest.approx(lat, abs=1e-4)
    assert params["longitude"] == pytest.approx(lon, abs=1e-4)
    assert params["forecast_hours"] == weather_router.REQUEST_HOURS
    assert "precipitation_probability" in params["hourly"]
    assert "api_key" not in params and "key" not in params


def test_carries_the_attribution_the_licence_requires(
    client: TestClient, upstream: Upstream
) -> None:
    """CC BY 4.0 requires attribution wherever the data is shown, so it travels with the data."""
    body = client.get("/v1/weather").json()
    assert body["source"] == "open-meteo"
    assert body["licence"] == "CC BY 4.0"
    assert body["attribution"] == "Weather data by Open-Meteo.com (CC BY 4.0)"
    assert body["source_url"].startswith("https://open-meteo.com")
    assert body["licence_url"].startswith("https://creativecommons.org/licenses/by/4.0")


def test_says_how_far_the_answering_grid_cell_is(client: TestClient, upstream: Upstream) -> None:
    """The answer is for a model cell, not the point; the distance is stated rather than hidden."""
    body = client.get("/v1/weather").json()
    assert body["grid_point"]["lat"] == pytest.approx(19.086115)
    assert body["grid_offset_km"] > 0
    assert body["grid_offset_km"] < 20


# ---- the cache -------------------------------------------------------------


def test_second_call_makes_no_outbound_request(client: TestClient, upstream: Upstream) -> None:
    first = client.get("/v1/weather").json()
    second = client.get("/v1/weather").json()

    assert len(upstream.calls) == 1
    assert second["fetched_at"] == first["fetched_at"]
    assert second["current"] == first["current"]
    assert second["age_s"] >= first["age_s"]


def test_writes_a_last_good_copy_to_disk(client: TestClient, upstream: Upstream) -> None:
    client.get("/v1/weather")
    path = weather_router.cache_path("mumbai")
    assert path.is_file()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["city"] == "mumbai"
    assert saved["fetched_at"]


def test_an_expired_copy_is_refetched(
    client: TestClient, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.get("/v1/weather")
    assert len(upstream.calls) == 1
    monkeypatch.setattr(weather_router, "TTL_S", 0.0)
    client.get("/v1/weather")
    assert len(upstream.calls) == 2


def test_a_cold_process_serves_the_file_a_previous_one_wrote(
    client: TestClient, upstream: Upstream
) -> None:
    """A restart during the demo must not lose the weather that was already fetched."""
    client.get("/v1/weather")
    weather_router.clear_cache()  # what a restart looks like from in here
    body = client.get("/v1/weather").json()
    assert len(upstream.calls) == 1
    assert body["current"]["temperature_c"] == 24.7


# ---- offline ---------------------------------------------------------------


def test_offline_serves_the_stamped_copy(
    client: TestClient, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.get("/v1/weather")
    _age_the_copy(hours=3)
    monkeypatch.setenv("VARUNA_OFFLINE", "1")

    body = client.get("/v1/weather").json()
    assert len(upstream.calls) == 1
    assert body["stale"] is True
    assert body["age_s"] > 10_000
    assert any("VARUNA_OFFLINE=1" in note for note in body["notes"])


def test_offline_without_a_copy_names_the_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VARUNA_OFFLINE", "1")
    stub = Upstream()
    monkeypatch.setattr(weather_router, "get_json", stub)

    response = client.get("/v1/weather")
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "offline"
    assert "VARUNA_OFFLINE" in error["message"]
    assert stub.calls == []


def test_offline_is_decided_by_the_helper(client: TestClient) -> None:
    """The endpoint asks `varuna_schemas.net.offline()`; it does not re-read the variable itself."""
    assert net.offline() is False


# ---- upstream failure ------------------------------------------------------


def test_upstream_failure_degrades_to_the_copy(
    client: TestClient, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.get("/v1/weather")
    _age_the_copy(hours=1)
    _fail_with(monkeypatch, net.HttpStatusError(500, weather_router.OPEN_METEO_URL))

    response = client.get("/v1/weather")
    assert response.status_code == 200
    body = response.json()
    assert body["stale"] is True
    assert body["current"]["temperature_c"] == 24.7
    assert any("could not be reached" in note for note in body["notes"])


def test_upstream_failure_without_a_copy_is_503_not_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_with(monkeypatch, net.NetworkError("Could not reach api.open-meteo.com: timed out"))
    response = client.get("/v1/weather")
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "weather_unavailable"
    assert "open-meteo" in error["message"]
    assert "does not depend on it" in error["message"]


def test_a_malformed_upstream_body_is_not_served(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 with no `current` block is an outage, not a sky with no temperature."""
    monkeypatch.setattr(weather_router, "get_json", Upstream(payload={"latitude": 19.0}))
    assert client.get("/v1/weather").status_code == 503


# ---- the city argument -----------------------------------------------------


def test_an_unconfigured_city_is_404_with_the_list(client: TestClient, upstream: Upstream) -> None:
    response = client.get("/v1/weather", params={"city": "atlantis"})
    assert response.status_code == 404
    message = response.json()["error"]["message"]
    assert "mumbai" in message
    assert upstream.calls == []


@pytest.mark.parametrize("city", ["../etc", "MUM/bai", ""])
def test_a_city_that_is_not_a_slug_is_refused(
    client: TestClient, upstream: Upstream, city: str
) -> None:
    response = client.get("/v1/weather", params={"city": city})
    assert response.status_code == 400
    assert upstream.calls == []


def _age_the_copy(hours: float) -> None:
    """Rewrite the kept copy's `fetched_at` so it is `hours` old, in memory and on disk."""
    older = (datetime.now(IST) - timedelta(hours=hours)).isoformat()
    path = weather_router.cache_path("mumbai")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["fetched_at"] = older
    path.write_text(json.dumps(payload), encoding="utf-8")
    weather_router.clear_cache()

"""`GET /v1/outlook`: today's Open-Meteo rain through Flash-lite, above the emulator's base state.

The weather goes through the real `/v1/weather` code with `get_json` stubbed, so the cache, the
disk copy and the stale path are the dashboard's own. The emulator, the street table and the
blockage are small stand-ins, so the suite needs neither `city/mumbai` nor a fitted model - except
the last test, which runs the shipped emulator when it is on disk and is skipped otherwise.

The stand-in emulator carries a 40 cm base state on every street. That is the point of the first
test: with no rain the outlook must list nothing, because the base state is a training window's
tide and upstream runoff, not today's water.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from varuna_api.routers import outlook as outlook_router
from varuna_api.routers import weather as weather_router
from varuna_flash.model import FlashModel
from varuna_schemas import net
from varuna_schemas.constants import IST
from varuna_schemas.paths import repo_root

BASE_CM = 40.0
IDS = ("S101-000", "S102-000", "S103-000", "S104-000")
NAMES = {"S101-000": "Dr Ambedkar Road", "S102-000": "LBS Marg", "S104-000": "SV Road"}


def _model() -> FlashModel:
    """Four streets; the third has no rain response, every one sits on a 40 cm base state."""
    return FlashModel(
        segment_ids=IDS,
        k_steps=np.array([2.0, 3.0, 3.0, 1.5]),
        gain=np.array([0.4, 0.2, 0.0, 0.8]),
        drain_cm_per_step=np.array([0.5, 0.5, 0.5, 0.2]),
        beta_ref=np.full(4, 0.2),
        baseline_cm=np.full((36, 4), BASE_CM),
        n_training_runs=6,
        rmse_cm=5.704,
        csi_30cm=0.0854,
        fitted_segments=3,
    )


def _payload(rain_by_hour: list[float]) -> dict[str, Any]:
    """An Open-Meteo answer built around the real clock, as `/v1/forecast` would give it now.

    `hourly.time` starts at the top of the current hour, the way `forecast_hours` counts, so the
    first value is already past and the second covers the hour the outlook starts in.
    """
    now = datetime.now(IST).replace(tzinfo=None)
    top = now.replace(minute=0, second=0, microsecond=0)
    quarter = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    hours = [top + timedelta(hours=i) for i in range(6)]
    quarters = [quarter + timedelta(minutes=15 * i) for i in range(20)]
    hourly = [0.0, *rain_by_hour]
    per_quarter = [hourly[1 + i // 4] / 4 if 1 + i // 4 < len(hourly) else 0.0 for i in range(20)]
    return {
        "latitude": 19.086115,
        "longitude": 72.85291,
        "utc_offset_seconds": 19800,
        "elevation": 5.0,
        "current": {
            "time": quarter.isoformat(timespec="minutes"),
            "interval": 900,
            "temperature_2m": 27.1,
            "relative_humidity_2m": 88,
            "precipitation": 0.0,
            "weather_code": 3,
            "wind_speed_10m": 12.0,
        },
        "hourly": {
            "time": [h.isoformat(timespec="minutes") for h in hours],
            "precipitation": (hourly + [0.0] * 6)[:6],
            "precipitation_probability": [10, 20, 30, 30, 20, 10],
        },
        "minutely_15": {
            "time": [q.isoformat(timespec="minutes") for q in quarters],
            "precipitation": per_quarter,
        },
    }


class Upstream:
    """A stand-in for `get_json` that counts what would have left the process."""

    def __init__(self, payload: dict[str, Any], error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls = 0

    def __call__(self, url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.payload


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A private data folder, empty caches, and the stand-in emulator, streets and blockage."""
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VARUNA_OFFLINE", "0")
    weather_router.clear_cache()
    outlook_router.clear_cache()
    model = _model()
    monkeypatch.setattr(outlook_router, "_load_model", lambda: ("test@1", model))
    monkeypatch.setattr(
        outlook_router,
        "_city_streets",
        lambda city: outlook_router._Streets(ids=frozenset(IDS), count=len(IDS), names=NAMES),
    )
    blockage = outlook_router._Blockage(
        mean=np.full(4, 0.2),
        sd=np.full(4, 0.05),
        info=outlook_router.OutlookBlockage(
            kind="city_prior", run_id=None, from_posterior=0, from_prior=4, flat=0
        ),
        key=("mumbai", None, 0, "test@1"),
    )
    monkeypatch.setattr(outlook_router, "_blockage", lambda city, model, key: blockage)
    yield
    weather_router.clear_cache()
    outlook_router.clear_cache()


def _upstream(monkeypatch: pytest.MonkeyPatch, rain_by_hour: list[float]) -> Upstream:
    stub = Upstream(_payload(rain_by_hour))
    monkeypatch.setattr(weather_router, "get_json", stub)
    return stub


def _age_the_weather(minutes: float) -> None:
    """Make the kept weather copy `minutes` old, in memory and on disk (see test_weather.py).

    Its forecast moves back with it: a copy fetched 40 minutes ago forecast the hours after *its*
    fetch, and the outlook has to cover those, not the ones after now.
    """
    import json

    shift = timedelta(minutes=minutes)

    def back(raw: str) -> str:
        return (datetime.fromisoformat(raw) - shift).isoformat()

    path = weather_router.cache_path("mumbai")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["fetched_at"] = back(payload["fetched_at"])
    payload["current"]["ts"] = back(payload["current"]["ts"])
    for block in ("hourly", "minutely_15"):
        for step in payload.get(block, []):
            step["ts"] = back(step["ts"])
    path.write_text(json.dumps(payload), encoding="utf-8")
    weather_router.clear_cache()


# ---- the answer ------------------------------------------------------------------------------


def test_no_rain_lists_no_street_although_the_base_state_is_40_cm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(monkeypatch, [0.0, 0.0, 0.0, 0.0])
    response = client.get("/v1/outlook", params={"city": "mumbai"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["mode"] == "outlook"
    assert body["run_id"] is None
    assert body["segments"] == []
    assert body["n_segments_over_1cm"] == 0
    assert body["summary"]["n_ge_5"] == 0
    assert body["summary"]["max_cm"] == 0.0
    assert body["summary"]["sentence"].startswith("No rain forecast")
    # What was taken out is reported, not hidden: every stand-in street sits at 40 cm with no rain.
    assert body["baseline_removed"] == {"n_ge_5": 4, "n_ge_15": 4, "n_ge_30": 4, "max_cm": 40.0}
    assert body["rain_total_mm"] == 0.0
    assert set(body["rain_mm_h"]) == {0.0}


def test_a_30_mm_hour_wets_streets_above_their_base_state_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(monkeypatch, [30.0, 0.0, 0.0, 0.0])
    body = client.get("/v1/outlook").json()

    assert body["rain_mm_h"][0] == 30.0
    assert body["rain_total_mm"] > 0
    assert body["segments"], "a 30 mm/h hour must put water on the responsive streets"
    listed = {row["segment_id"] for row in body["segments"]}
    assert "S103-000" not in listed  # no rain response, so exactly 0 cm above base
    assert body["no_rain_response"] == 1
    for row in body["segments"]:
        assert len(row["p50_cm"]) == len(row["p90_cm"]) == body["n_steps"] == 36
        assert all(0.0 <= p <= 1.0 for p in row["p_gt_15"] + row["p_gt_30"] + row["p_gt_45"])
        assert all(lo <= hi for lo, hi in zip(row["p50_cm"], row["p90_cm"], strict=True))
        # The cascade lags the rain, so the first five minutes are shallow. With the base state
        # added back they would read 40 cm or more.
        assert row["p50_cm"][0] < BASE_CM
        assert row["name"] == NAMES.get(row["segment_id"])
    assert body["summary"]["worst"][0]["name"] in NAMES.values()
    assert len(body["valid_ts"]) == 36
    first = datetime.fromisoformat(body["valid_ts"][0])
    assert first - datetime.fromisoformat(body["valid_from"]) == timedelta(minutes=5)
    assert first.utcoffset() == timedelta(hours=5, minutes=30)


def test_labels_the_source_the_method_and_the_series(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(monkeypatch, [2.0, 1.0, 0.0, 0.0])
    body = client.get("/v1/outlook").json()

    source = body["source"]
    assert source["name"] == "Open-Meteo"
    assert source["licence"] == "CC BY 4.0"
    assert source["series"] == "hourly"
    assert "interpolates the 15-minute series" in source["series_note"]
    assert source["stale"] is False
    assert source["grid_cell_km"] >= 0
    assert body["method"] == outlook_router.METHOD
    assert "not a radar nowcast" in body["method"]
    assert body["members"] == 50
    assert body["skill"]["csi_30cm"] == 0.0854
    assert any("base state" in note and "removed" in note for note in body["notes"])
    assert any("Kept out of the run registry" in note for note in body["notes"])


def test_a_second_reader_reuses_the_answer_and_the_weather(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _upstream(monkeypatch, [5.0, 5.0, 0.0, 0.0])
    computed: list[int] = []
    real = outlook_router._compute

    def counting(*args: Any, **kwargs: Any) -> dict[str, Any]:
        computed.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(outlook_router, "_compute", counting)
    first = client.get("/v1/outlook").json()
    second = client.get("/v1/outlook").json()

    assert first["cached"] is False
    assert second["cached"] is True
    assert len(computed) == 1
    assert upstream.calls == 1
    assert second["segments"] == first["segments"]


# ---- degraded ---------------------------------------------------------------------------------


def test_a_stale_weather_copy_is_served_with_its_age_and_its_own_window(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(monkeypatch, [4.0, 0.0, 0.0, 0.0])
    fresh = client.get("/v1/outlook").json()
    assert fresh["source"]["stale"] is False

    _age_the_weather(40)
    outlook_router.clear_cache()
    monkeypatch.setattr(
        weather_router,
        "get_json",
        Upstream({}, error=net.NetworkError("Could not reach api.open-meteo.com: timed out")),
    )
    response = client.get("/v1/outlook")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["source"]["stale"] is True
    assert body["source"]["age_s"] >= 40 * 60 - 5
    assert "40 min old" in body["notes"][0]
    # The window is the old copy's: it starts before the copy was 40 minutes old, not at now.
    assert datetime.fromisoformat(body["valid_from"]) <= datetime.now(IST) - timedelta(minutes=35)
    assert body["expired"] is False


def test_a_copy_older_than_its_window_says_it_has_expired(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(monkeypatch, [4.0, 0.0, 0.0, 0.0])
    client.get("/v1/outlook")
    _age_the_weather(4 * 60)
    outlook_router.clear_cache()
    monkeypatch.setenv("VARUNA_OFFLINE", "1")

    body = client.get("/v1/outlook").json()
    assert body["expired"] is True
    assert body["source"]["stale"] is True
    assert body["notes"][0].startswith("That window has passed")


def test_no_weather_at_all_is_503_and_nothing_is_invented(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        weather_router,
        "get_json",
        Upstream({}, error=net.NetworkError("Could not reach api.open-meteo.com: timed out")),
    )
    response = client.get("/v1/outlook")
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "weather_unavailable"
    assert "Nothing was invented" in error["message"]
    assert "Open-Meteo could not be reached" in error["message"]
    assert "nothing has been cached for mumbai" in error["message"]
    # The weather route's own note is the dashboard's, and it is false here: the outlook is a
    # flood forecast made of this rain.
    assert "does not depend on it" not in error["message"]
    assert error["message"].count("Nothing was invented") == 1


def test_offline_with_no_copy_names_the_switch_and_its_fix(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _upstream(monkeypatch, [30.0, 0.0, 0.0, 0.0])
    monkeypatch.setenv("VARUNA_OFFLINE", "1")
    response = client.get("/v1/outlook")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "offline"
    assert "VARUNA_OFFLINE=1" in error["message"]
    assert "Unset VARUNA_OFFLINE" in error["message"]
    assert "does not depend on it" not in error["message"]
    assert upstream.calls == 0


# ---- refusals ---------------------------------------------------------------------------------


def test_chennai_is_refused_with_the_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _upstream(monkeypatch, [30.0, 0.0, 0.0, 0.0])
    response = client.get("/v1/outlook", params={"city": "chennai"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "no_emulator_for_city"
    assert "Chennai" in error["message"]
    assert "design storm" in error["message"]
    assert upstream.calls == 0  # refused before any weather was asked for


def test_an_emulator_fitted_to_another_build_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(monkeypatch, [30.0, 0.0, 0.0, 0.0])
    other = frozenset(f"S9{i}-000" for i in range(4))
    monkeypatch.setattr(
        outlook_router,
        "_city_streets",
        lambda city: outlook_router._Streets(ids=other, count=4, names={}),
    )
    response = client.get("/v1/outlook")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "emulator_mismatch"


def test_nothing_is_written_to_the_run_registry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _upstream(monkeypatch, [30.0, 10.0, 0.0, 0.0])
    assert client.get("/v1/outlook").status_code == 200
    runs = tmp_path / "data" / "runs"
    assert not runs.exists() or not any(runs.iterdir())


# ---- the shipped emulator ---------------------------------------------------------------------

REAL = (outlook_router._load_model, outlook_router._city_streets, outlook_router._blockage)
"""The real loaders, taken before the autouse fixture swaps them; the data folder stays private,
so no run's posterior is found and the blockage is the city's prior."""
SHIPPED = repo_root() / "data" / "train" / "flash_lite.npz"
STREETS = repo_root() / "city" / "mumbai" / "segments.parquet"


@pytest.mark.skipif(
    not (SHIPPED.is_file() and STREETS.is_file()),
    reason="needs the fitted emulator (make train) and the Mumbai street table (make city)",
)
def test_the_shipped_emulator_paints_nothing_on_a_dry_day(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured 2026-09-26: with its base state the emulator puts 1,083 Mumbai streets above 5 cm
    with no rain at all. The outlook must put none."""
    load_model, city_streets, blockage = REAL
    monkeypatch.setattr(outlook_router, "_load_model", load_model)
    monkeypatch.setattr(outlook_router, "_city_streets", city_streets)
    monkeypatch.setattr(outlook_router, "_blockage", blockage)
    _upstream(monkeypatch, [0.0, 0.0, 0.0, 0.0])

    body = client.get("/v1/outlook").json()
    assert body["summary"]["n_ge_5"] == 0
    assert body["segments"] == []
    assert body["baseline_removed"]["n_ge_5"] > 1000
    assert body["blockage"]["kind"] == "city_prior"

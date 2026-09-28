"""The verification sweep is scored once per set of inputs (routers/verify.py)."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from varuna_api.routers import verify

EVENT = "MUM-2019-07-02"


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, list[str]]]:
    bundles = tmp_path / "bundles"
    (bundles / EVENT).mkdir(parents=True)
    (bundles / EVENT / "ground_truth.geojson").write_text('{"features": []}', encoding="utf-8")
    runs = tmp_path / "data" / "runs"
    (runs / "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked").mkdir(parents=True)
    (runs / "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked" / "segments_wet.json").write_text(
        "{}", encoding="utf-8"
    )
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(bundles))
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path / "data"))

    calls: list[str] = []

    def fake_sweep(event: str) -> dict[str, Any]:
        calls.append(event)
        return {"event": event, "calls": len(calls)}

    import varuna_verify.event

    monkeypatch.setattr(varuna_verify.event, "sweep", fake_sweep)
    verify.clear_cache()
    yield runs, calls
    verify.clear_cache()


def test_a_second_request_with_unchanged_inputs_is_not_rescored(
    scratch: tuple[Path, list[str]],
) -> None:
    _, calls = scratch
    first = verify.scored_sweep(EVENT)
    second = verify.scored_sweep(EVENT)
    assert calls == [EVENT]
    assert second == first


def test_a_new_run_is_scored_again(scratch: tuple[Path, list[str]]) -> None:
    runs, _ = scratch
    verify.scored_sweep(EVENT)
    newer = runs / "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked"
    newer.mkdir()
    (newer / "segments_wet.json").write_text("{}", encoding="utf-8")
    assert verify.scored_sweep(EVENT)["calls"] == 2


def test_a_rewritten_run_is_scored_again(scratch: tuple[Path, list[str]]) -> None:
    runs, calls = scratch
    verify.scored_sweep(EVENT)
    wet = runs / "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked" / "segments_wet.json"
    wet.write_text('{"rewritten": true}', encoding="utf-8")
    later = time.time_ns() + 5_000_000_000
    os.utime(wet, ns=(later, later))
    assert verify.scored_sweep(EVENT)["calls"] == 2
    assert len(calls) == 2


# ------------------------------------------------------------------ rain skill by lead time
@pytest.fixture
def rain_calls(
    scratch: tuple[Path, list[str]], monkeypatch: pytest.MonkeyPatch
) -> Iterator[list[str]]:
    calls: list[str] = []

    def fake_rain(event: str) -> dict[str, Any]:
        calls.append(event)
        return {"event": event, "available": True, "calls": len(calls)}

    import varuna_verify.rain_event

    monkeypatch.setattr(varuna_verify.rain_event, "event_rain_skill", fake_rain)
    yield calls


def test_rain_skill_is_kept_until_a_run_changes(
    scratch: tuple[Path, list[str]], rain_calls: list[str]
) -> None:
    runs, _ = scratch
    run = runs / "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"
    (run / "run.json").write_text('{"bundle": "MUM-2019-07-02"}', encoding="utf-8")
    (run / "rain" / "quantiles.zarr").mkdir(parents=True)
    meta = run / "rain" / "quantiles.zarr" / "zarr.json"
    meta.write_text("{}", encoding="utf-8")

    first = verify.scored_rain_skill(EVENT)
    assert verify.scored_rain_skill(EVENT) == first
    assert rain_calls == [EVENT]

    meta.write_text('{"rebaked": true}', encoding="utf-8")
    later = time.time_ns() + 5_000_000_000
    os.utime(meta, ns=(later, later))
    assert verify.scored_rain_skill(EVENT)["calls"] == 2


def test_rain_skill_route_serves_the_scorer_and_refuses_an_unknown_event(
    scratch: tuple[Path, list[str]], rain_calls: list[str], client: Any
) -> None:
    served = client.get("/v1/verification/rain-skill", params={"event": EVENT})
    assert served.status_code == 200
    assert served.json()["event"] == EVENT

    missing = client.get("/v1/verification/rain-skill", params={"event": "NOT-A-BUNDLE"})
    assert missing.status_code == 404
    assert "make bundle BUNDLE=NOT-A-BUNDLE" in missing.json()["error"]["message"]


def test_rain_skill_without_a_truth_field_is_unavailable_not_an_error(
    scratch: tuple[Path, list[str]], client: Any
) -> None:
    body = client.get("/v1/verification/rain-skill", params={"event": EVENT}).json()
    assert body["available"] is False
    assert "truth/rain.zarr" in body["reason"]
    assert body["missing"] == "truth"
    assert body["command"] == f"make bundle BUNDLE={EVENT}"

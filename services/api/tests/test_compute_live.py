"""Compute live (SPEC.md 7.2, 11.11; task P6.11): the flag, the refusal and the stage events.

The cycle itself is replaced by a fake that calls the same four module functions `run_cycle` calls
to open its stages, so the test exercises the wrappers, the bus and the status endpoint without a
minute of Sky and Twin.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pytest
import varuna_pulse.cycle as pulse_cycle
import varuna_twin.runner as twin_runner
from fastapi.testclient import TestClient
from varuna_api import rain
from varuna_api.routers import cycle as cycle_router
from varuna_cycle import twin_cycle


@dataclass
class FakeResult:
    run_id: str = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-live"
    stage_ms: dict[str, int] = field(
        default_factory=lambda: {"sky": 11, "twin": 22, "flash": 3, "products": 4, "pulse": 5}
    )


@pytest.fixture
def fake_cycle(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"order": []}

    def stage(name: str):
        def run(*_: Any, **__: Any) -> str:
            calls["order"].append(name)
            time.sleep(0.01)
            return name

        return run

    monkeypatch.setattr(twin_cycle, "_sky_rain_on_city", stage("sky"))
    monkeypatch.setattr(twin_runner, "run_twin", stage("twin"))
    monkeypatch.setattr(twin_cycle, "_flash_members", stage("flash"))
    monkeypatch.setattr(pulse_cycle, "run_pulse", stage("pulse"))

    def run_cycle(bundle: str, cycle_ts: Any, **kwargs: Any) -> FakeResult:
        calls["args"] = (bundle, cycle_ts, kwargs)
        # The same order `run_cycle` opens its stages in, through the module attributes.
        twin_cycle._sky_rain_on_city()
        twin_runner.run_twin()
        twin_cycle._flash_members()
        pulse_cycle.run_pulse()
        return FakeResult()

    monkeypatch.setattr(twin_cycle, "run_cycle", run_cycle)
    monkeypatch.setattr(cycle_router, "_WRAPPED", False)
    monkeypatch.setattr(cycle_router, "_JOB", None)
    return calls


def _wait_idle(timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = cycle_router.current_job()
        if job is not None and not job.running:
            return
        time.sleep(0.02)
    raise AssertionError("the live cycle never finished")


def test_compute_is_refused_when_the_host_has_not_turned_it_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(cycle_router.COMPUTE_ENV, raising=False)
    info = client.get("/v1/cycle/compute").json()
    assert info["enabled"] is False
    assert cycle_router.COMPUTE_ENV in info["reason"]
    assert info["budget_ms"] == 15_000

    res = client.post("/v1/cycle/compute", json={})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "compute_disabled"


def test_expected_time_is_read_from_the_runs_on_this_host(
    client: TestClient, baked_run: Any
) -> None:
    info = client.get("/v1/cycle/compute").json()
    expected = info["expected"]
    assert expected["n_runs"] == 1
    assert expected["median_ms"] == expected["min_ms"] == expected["max_ms"] > 0


def test_a_live_cycle_reports_every_stage_and_ends_on_the_numbers_of_record(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, fake_cycle: dict[str, Any]
) -> None:
    monkeypatch.setenv(cycle_router.COMPUTE_ENV, "1")
    state = client.app.state.varuna  # type: ignore[attr-defined]

    res = client.post("/v1/cycle/compute", json={"bundle": "MUM-2019-07-02"})
    assert res.status_code == 202, res.text
    _wait_idle()

    assert fake_cycle["order"] == ["sky", "twin", "flash", "pulse"]
    assert fake_cycle["args"][2]["mode"] == "live"

    deadline = time.monotonic() + 2
    events: list[Any] = []
    while time.monotonic() < deadline:
        events = state.bus.history("cycle.stage")
        if len([e for e in events if e.payload["status"] == "finished"]) >= 5:
            break
        time.sleep(0.02)
    finished = [e.payload["stage"] for e in events if e.payload["status"] == "finished"]
    assert finished == ["sky", "twin", "flash", "products", "pulse"]
    assert all(e.payload["ms"] >= 0 for e in events if e.payload["status"] == "finished")
    started = [e.payload["stage"] for e in events if e.payload["status"] == "started"]
    assert started == ["sky", "twin", "flash", "products", "pulse"]

    status = client.get("/v1/cycle/status").json()
    assert status["stage"] == "idle"
    job = cycle_router.current_job()
    assert job is not None and job.run_id == FakeResult().run_id
    assert job.stage_ms == FakeResult().stage_ms
    # The lock is free again: a second press may run.
    assert not rain._LIVE_CYCLE.locked()


def test_a_second_press_while_one_runs_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, fake_cycle: dict[str, Any]
) -> None:
    monkeypatch.setenv(cycle_router.COMPUTE_ENV, "1")
    assert rain._LIVE_CYCLE.acquire(blocking=False)
    try:
        res = client.post("/v1/cycle/compute", json={})
        assert res.status_code == 503
        assert res.json()["error"]["code"] == "cycle_busy"
    finally:
        rain._LIVE_CYCLE.release()


def test_wrappers_do_nothing_off_the_compute_thread(fake_cycle: dict[str, Any]) -> None:
    cycle_router._install_stage_wrappers()
    # A bake or a physics check in the same process calls straight through.
    assert twin_runner.run_twin() == "twin"
    assert fake_cycle["order"] == ["twin"]

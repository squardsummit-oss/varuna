from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from varuna_api.main import create_app
from varuna_api.state import AppState
from varuna_cycle.bus import Bus
from varuna_cycle.registry import RunRegistry
from varuna_schemas.models import RunMeta
from varuna_schemas.samples import sample
from varuna_schemas.settings import Settings


@pytest.fixture(autouse=True)
def _no_seeding_into_the_real_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    """Starting the app seeds `demo/runs` into `VARUNA_DATA_DIR`, which is the developer's own
    `data/runs` unless a test has already moved it - and fixtures run in signature order, so a
    `client` requested before a `tmp_path` data dir starts the app first. Tests do not seed; the
    seeder has its own tests (`test_seed.py`), which call it directly."""
    monkeypatch.setenv("VARUNA_SEED_DEMO_RUNS", "0")


@pytest.fixture(autouse=True)
def _no_real_dot_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The desk's write gate builds a fresh `Settings()` on every write, which reads the
    repository's real `.env`; the `settings` fixture's `_env_file=None` isolates only the app's
    own state. Without this, a passphrase set in the demo laptop's `.env` - where `.env.example`
    tells it to go - would turn the gate's 503 tests into 403s. A test that wants a `.env` points
    `env_file` at its own temporary file after this runs (`test_ops_passphrase_settings.py`)."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        varuna_city="mumbai",
        varuna_bundle="MUM-2019-07-02",
        varuna_mode="replay",
        varuna_offline=True,
        cors_origins=["http://localhost:3000"],
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def registry(tmp_path: Path) -> RunRegistry:
    return RunRegistry(tmp_path / "runs")


@pytest.fixture
def bus() -> Bus:
    return Bus()


@pytest.fixture
def state(settings: Settings, registry: RunRegistry, bus: Bus) -> AppState:
    return AppState(settings=settings, registry=registry, bus=bus, ws_heartbeat_s=0.2)


@pytest.fixture
def app(state: AppState) -> FastAPI:
    return create_app(state=state)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def run_meta() -> RunMeta:
    meta = sample("RunMeta")
    assert isinstance(meta, RunMeta)
    return meta


@pytest.fixture
def baked_run(registry: RunRegistry, run_meta: RunMeta) -> RunMeta:
    registry.write_meta(run_meta)
    return run_meta

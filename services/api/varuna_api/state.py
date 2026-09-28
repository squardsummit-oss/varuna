"""Process state shared by every router: settings, the bus, the run registry, uptime.

Routers receive it through :func:`get_state` (a FastAPI dependency) so tests can build an
app around a temporary runs folder and a private bus.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import HTTPException, Request
from varuna_cycle.bus import Bus, get_bus
from varuna_cycle.registry import RunRegistry
from varuna_schemas.constants import IST
from varuna_schemas.models import RunMeta
from varuna_schemas.settings import Settings, get_settings

from varuna_api import __version__
from varuna_api.replay import ReplayController

WS_HEARTBEAT_S: float = 15.0
"""Heartbeat interval on ``WS /v1/live`` (SPEC.md P0.4)."""


@dataclass
class AppState:
    settings: Settings = field(default_factory=get_settings)
    bus: Bus = field(default_factory=get_bus)
    registry: RunRegistry = field(default_factory=RunRegistry)
    replay: ReplayController = field(default_factory=ReplayController)
    version: str = __version__
    started_at: datetime = field(default_factory=lambda: datetime.now(IST))
    started_monotonic: float = field(default_factory=time.monotonic)
    ws_heartbeat_s: float = WS_HEARTBEAT_S

    @property
    def uptime_s(self) -> float:
        return max(0.0, time.monotonic() - self.started_monotonic)

    def latest_run(self) -> RunMeta | None:
        """Newest run for the configured city (any bundle, so live runs count too)."""
        return self.registry.latest(city=self.settings.varuna_city)


def get_state(request: Request) -> AppState:
    """FastAPI dependency returning the app's :class:`AppState`."""
    state: AppState | None = getattr(request.app.state, "varuna", None)
    if state is None:  # pragma: no cover - create_app always sets it
        msg = "API state missing; build the app with varuna_api.main.create_app()"
        raise RuntimeError(msg)
    return state


def api_error(status: int, code: str, message: str, run_id: str | None = None) -> HTTPException:
    """An :class:`HTTPException` whose detail becomes the error envelope (SPEC.md 12)."""
    return HTTPException(
        status_code=status, detail={"code": code, "message": message, "run_id": run_id}
    )


def not_implemented(what: str, phase: int, task: str | None = None) -> HTTPException:
    """HTTP 501 for a contract endpoint whose engine is built in a later phase."""
    suffix = f" (task {task})" if task else ""
    return api_error(
        501,
        "not_implemented",
        f"{what} lands in Phase {phase}{suffix}. The contract is final; the engine is not built yet.",
    )


def run_not_found(run_id: str) -> HTTPException:
    return api_error(
        404,
        "run_not_found",
        f"Run {run_id} is not in data/runs. Run `make bake BUNDLE=MUM-2019-07-02`, "
        "press Play on the replay, or Compute live.",
        run_id=run_id,
    )


__all__ = [
    "WS_HEARTBEAT_S",
    "AppState",
    "api_error",
    "get_state",
    "not_implemented",
    "run_not_found",
]

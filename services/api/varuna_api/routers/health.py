"""``GET /healthz``: liveness, mode, bundle, city and the last published run."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from varuna_cycle.registry import summarize
from varuna_schemas.constants import IST
from varuna_schemas.models import HealthStatus, RunSummary
from varuna_schemas.models.common import Timestamp

from varuna_api.state import AppState, get_state

router = APIRouter(tags=["health"])


class HealthResponse(HealthStatus):
    """:class:`HealthStatus` plus the send time and the last run's summary row."""

    ts: Timestamp
    last_run: RunSummary | None = None


@router.get("/healthz", response_model=HealthResponse, summary="Liveness and mode")
def healthz(state: Annotated[AppState, Depends(get_state)]) -> HealthResponse:
    settings = state.settings
    last = state.latest_run()
    clock = state.replay.clock
    return HealthResponse(
        status="ok" if last is not None else "starting",
        version=state.version,
        mode=settings.varuna_mode,
        city=settings.varuna_city,
        bundle=settings.varuna_bundle if settings.is_replay else None,
        last_run_id=last.run_id if last else None,
        last_run_ts=last.cycle_ts if last else None,
        last_run_mode=last.mode if last else None,
        replay_mode=last.replay_mode if last else None,
        degraded_feeds=list(last.degraded_feeds) if last else [],
        replay=clock.snapshot() if clock is not None else None,
        uptime_s=state.uptime_s,
        offline=settings.varuna_offline,
        ts=datetime.now(IST),
        last_run=summarize(last) if last else None,
    )


__all__ = ["HealthResponse", "router"]

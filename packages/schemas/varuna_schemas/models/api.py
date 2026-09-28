"""API envelopes shared by every endpoint: the error format, the WebSocket event envelope,
``GET /healthz`` and the run registry listing (SPEC.md 11.11, 12).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from varuna_schemas.constants import WsTopic
from varuna_schemas.models.common import Timestamp, VarunaModel
from varuna_schemas.models.replay import ReplayClock
from varuna_schemas.models.run import ReplayMode, RunMode

HealthState = Literal["ok", "degraded", "starting"]


class ErrorDetail(VarunaModel):
    """``code`` is machine-readable; ``message`` says what happened and what to do."""

    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$", description="e.g. run_not_found")
    message: str = Field(
        description="e.g. 'Run MUM-... is not baked yet. Press Play on the replay or Compute live.'"
    )
    run_id: str | None = None
    details: dict[str, Any] | None = None


class ErrorEnvelope(VarunaModel):
    """Every error response: ``{"error": {"code", "message", "run_id"}}``."""

    error: ErrorDetail

    @classmethod
    def make(cls, code: str, message: str, run_id: str | None = None) -> ErrorEnvelope:
        return cls(error=ErrorDetail(code=code, message=message, run_id=run_id))


class LiveEvent(VarunaModel):
    """Envelope of every message on ``WS /v1/live``."""

    topic: WsTopic
    ts: Timestamp = Field(description="Wall-clock send time (IST).")
    payload: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = None
    seq: int | None = Field(default=None, ge=0, description="Monotonic sequence for reconnects.")


class HealthStatus(VarunaModel):
    """``GET /healthz``: liveness, mode, bundle and the last published run."""

    status: HealthState
    version: str
    mode: Literal["replay", "live"] = Field(description="Ingestion mode from settings.")
    city: str
    bundle: str | None = None
    last_run_id: str | None = None
    last_run_ts: Timestamp | None = None
    last_run_mode: RunMode | None = None
    replay_mode: ReplayMode | None = None
    degraded_feeds: list[str] = Field(default_factory=list)
    replay: ReplayClock | None = None
    uptime_s: float = Field(ge=0)
    offline: bool = Field(default=False, description="True when outbound network is blocked.")


class RunSummary(VarunaModel):
    """One row of ``GET /v1/runs``."""

    run_id: str
    city: str
    cycle_ts: Timestamp
    mode: RunMode
    replay_mode: ReplayMode
    bundle: str | None = None
    created_at: Timestamp
    total_ms: int = Field(ge=0)
    mass_balance_err: float = Field(ge=0)
    ensemble_n: int = Field(ge=1)


class RunList(VarunaModel):
    """``GET /v1/runs``."""

    runs: list[RunSummary] = Field(default_factory=list)
    count: int = Field(ge=0)
    latest_run_id: str | None = None


__all__ = [
    "ErrorDetail",
    "ErrorEnvelope",
    "HealthState",
    "HealthStatus",
    "LiveEvent",
    "RunList",
    "RunSummary",
]

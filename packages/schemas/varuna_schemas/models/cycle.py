"""Cycle orchestrator status and events (``GET /v1/cycle/status``, ``POST /v1/cycle/compute``,
the WebSocket ``cycle.stage`` event and the replay panel's cycle log; SPEC.md 7.8, 11.11).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, computed_field

from varuna_schemas.constants import STAGE_BUDGET_MS, TOTAL_CYCLE_BUDGET_MS, CycleStage
from varuna_schemas.models.common import Timestamp, VarunaModel
from varuna_schemas.models.run import ReplayMode, RunMode, stage_total_ms

StageStatus = Literal["started", "finished", "failed", "skipped"]


class CycleStatus(VarunaModel):
    """What the orchestrator is doing now, with the per-stage budget for the budget bar."""

    run_id: str | None = Field(default=None, description="Run being produced or last published.")
    stage: CycleStage = "idle"
    stage_ms: dict[str, int] = Field(
        default_factory=dict, description="Completed stages and their wall-clock in ms."
    )
    started_at: Timestamp | None = None
    finished_at: Timestamp | None = None
    cycle_ts: Timestamp | None = Field(default=None, description="Simulation time of the cycle.")
    mode: RunMode | None = Field(default=None, description="baked or live.")
    replay_mode: ReplayMode | None = None
    bundle: str | None = None
    budget_ms: dict[str, int] = Field(
        default_factory=lambda: dict(STAGE_BUDGET_MS),
        description="Per-stage budgets in ms (SPEC.md 11.11).",
    )
    total_budget_ms: int = Field(default=TOTAL_CYCLE_BUDGET_MS, gt=0)
    degraded_feeds: list[str] = Field(default_factory=list)
    error: str | None = Field(
        default=None,
        description="What failed and what to do, e.g. 'Twin exceeded 8 s; using Flash-lite'.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def elapsed_ms(self) -> int:
        # Each top-level stage once; the Twin's sub-timings are provenance (stage_total_ms).
        return stage_total_ms(self.stage_ms)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def busy(self) -> bool:
        return self.stage != "idle"

    @property
    def over_budget(self) -> list[str]:
        """Stages whose timing exceeded their budget."""
        return [
            stage
            for stage, ms in self.stage_ms.items()
            if stage in self.budget_ms and ms > self.budget_ms[stage]
        ]


class CycleStageEvent(VarunaModel):
    """Payload of the WebSocket ``cycle.stage`` event (one per stage start and finish)."""

    run_id: str | None = None
    cycle_ts: Timestamp
    stage: CycleStage
    status: StageStatus
    ms: int | None = Field(default=None, ge=0, description="Set on finished/failed.")
    budget_ms: int | None = Field(default=None, ge=0)
    message: str | None = None


class CycleLogEntry(VarunaModel):
    """One row of the replay panel's cycle log."""

    cycle_ts: Timestamp
    run_id: str
    mode: RunMode
    stage_ms: dict[str, int] = Field(default_factory=dict)
    mass_balance_err: float = Field(ge=0, description="Relative Twin mass-balance error.")
    degraded_feeds: list[str] = Field(default_factory=list)
    published_ts: Timestamp | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_ms(self) -> int:
        # Each top-level stage once; the Twin's sub-timings are provenance (stage_total_ms).
        return stage_total_ms(self.stage_ms)


class ComputeRequest(VarunaModel):
    """Body of ``POST /v1/cycle/compute`` ('Compute live')."""

    bundle: str | None = Field(
        default=None, description="Bundle to read streams from; None = current."
    )
    cycle_ts: Timestamp | None = Field(
        default=None, description="Cycle time; None = the replay clock's current time."
    )
    force: bool = Field(default=False, description="Recompute even when a baked run exists.")


__all__ = ["ComputeRequest", "CycleLogEntry", "CycleStageEvent", "CycleStatus", "StageStatus"]

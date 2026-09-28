"""Alerts with hysteresis state and CAP 1.2 provenance (SPEC.md 7.5, 11.10; blueprint 6.10, 9.4)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, computed_field, model_validator

from varuna_schemas.constants import ALERT_LEVEL_THRESHOLDS_CM, AlertLevel
from varuna_schemas.models.common import IdStr, Polygon, Probability, Timestamp, VarunaModel

AlertScope = Literal["segment", "ward", "facility"]
AlertState = Literal["raised", "acknowledged", "escalated", "cleared"]
AlertChannel = Literal["dashboard", "cap", "whatsapp", "sms"]
CapStatus = Literal["Actual", "Exercise"]
"""CAP ``status``: replay runs emit Exercise, live runs emit Actual (SPEC.md 11.10)."""

EscalationTarget = Literal["ward_officer", "control_room", "police_traffic", "transit", "public"]
"""Escalation matrix in order (blueprint 6.10)."""


class AlertStateChange(VarunaModel):
    """One entry of the alert's audit log (acknowledge and escalate are logged with user and time)."""

    ts: Timestamp
    state: AlertState
    user: str = Field(description="Operator id or 'system'.")
    note: str | None = None


class Alert(VarunaModel):
    """One alert as stored in ``alerts.json`` and served by ``GET /v1/alerts``."""

    id: IdStr = Field(
        description="Also the CAP identifier stem, e.g. VARUNA-MUM-20190702T1745-FN-0031."
    )
    run_id: str = Field(description="Run that raised (or last updated) the alert.")
    scope: AlertScope
    scope_id: IdStr | None = Field(
        default=None, description="Segment, ward or facility id the alert is about."
    )
    hotspot_id: IdStr | None = Field(default=None, description="Hotspot register id, if any.")
    level: AlertLevel
    threshold_cm: int = Field(
        gt=0, description="Depth threshold that defines the level (15 / 30 / 45 cm)."
    )
    headline: str = Field(
        description="e.g. 'Hindmata junction: depth likely above 45 cm from 18:20 to 20:00'."
    )
    instruction: str | None = Field(
        default=None,
        description="Route hint and pump plan, e.g. 'Avoid Hindmata and Parel TT ...'.",
    )
    area_desc: str = Field(description="e.g. 'Ward F/North, Hindmata'.")
    polygon: Polygon | None = Field(default=None, description="CAP area polygon (WGS84).")
    trigger_p: Probability = Field(description="P(depth > threshold) at the trigger cycle.")
    window_from: Timestamp | None = Field(
        default=None, description="Start of the exceedance window."
    )
    window_to: Timestamp | None = Field(default=None, description="End of the exceedance window.")
    raised_ts: Timestamp
    persists_cycles: int = Field(
        ge=1, description="Consecutive cycles above the raise probability (hysteresis)."
    )
    state: AlertState = "raised"
    channels: list[AlertChannel] = Field(
        default_factory=lambda: ["dashboard"], description="Channels the alert was sent to."
    )
    acknowledged_by: str | None = None
    acknowledged_ts: Timestamp | None = None
    escalated_to: EscalationTarget | None = None
    cleared_ts: Timestamp | None = None
    cap_status: CapStatus = Field(description="Exercise on replay, Actual when live.")
    cap_path: str | None = Field(
        default=None, description="Relative path of the CAP XML inside the run directory."
    )
    pumps: list[IdStr] = Field(default_factory=list, description="Pumps dispatched for this alert.")
    history: list[AlertStateChange] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Alert:
        expected = ALERT_LEVEL_THRESHOLDS_CM[self.level]
        if self.threshold_cm != expected:
            msg = f"level {self.level!r} implies threshold {expected} cm, got {self.threshold_cm}"
            raise ValueError(msg)
        if self.state == "acknowledged" and not self.acknowledged_by:
            msg = "an acknowledged alert needs acknowledged_by"
            raise ValueError(msg)
        if self.state == "cleared" and self.cleared_ts is None:
            msg = "a cleared alert needs cleared_ts"
            raise ValueError(msg)
        if self.window_from and self.window_to and self.window_to < self.window_from:
            msg = "window_to must not be before window_from"
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return self.state != "cleared"


__all__ = [
    "Alert",
    "AlertChannel",
    "AlertScope",
    "AlertState",
    "AlertStateChange",
    "CapStatus",
    "EscalationTarget",
]

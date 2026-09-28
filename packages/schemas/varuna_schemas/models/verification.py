"""Verification scores per event (``verification.json``, ``GET /v1/verification?event=``;
SPEC.md 7.10, 11.12). Every number is computed by ``services/verify`` from run artifacts and
sourced ground truth, never typed in.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, computed_field, model_validator

from varuna_schemas.models.common import HttpUrlStr, IdStr, Probability, Timestamp, VarunaModel


class ContingencyTable(VarunaModel):
    """Hits / misses / false alarms / correct negatives for one threshold."""

    threshold_cm: int = Field(default=30, gt=0)
    hits: int = Field(ge=0)
    misses: int = Field(ge=0)
    false_alarms: int = Field(ge=0)
    correct_negatives: int = Field(ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def n(self) -> int:
        return self.hits + self.misses + self.false_alarms + self.correct_negatives

    @computed_field  # type: ignore[prop-decorator]
    @property
    def csi(self) -> float | None:
        """Critical success index = hits / (hits + misses + false alarms)."""
        denom = self.hits + self.misses + self.false_alarms
        return None if denom == 0 else self.hits / denom

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pod(self) -> float | None:
        """Probability of detection = hits / (hits + misses)."""
        denom = self.hits + self.misses
        return None if denom == 0 else self.hits / denom

    @computed_field  # type: ignore[prop-decorator]
    @property
    def far(self) -> float | None:
        """False-alarm ratio = false alarms / (hits + false alarms)."""
        denom = self.hits + self.false_alarms
        return None if denom == 0 else self.false_alarms / denom


class HotspotScore(VarunaModel):
    """Scores at one chronic spot."""

    hotspot_id: IdStr
    name: str
    contingency: ContingencyTable
    timing_err_min: float | None = Field(
        default=None, description="Forecast peak minus observed onset in minutes (signed)."
    )
    mae_cm: float | None = Field(
        default=None, ge=0, description="Depth MAE where pins carry depth."
    )
    lead_time_gained_min: float | None = Field(
        default=None, description="Minutes between the first alert and the first observed report."
    )
    n_pins: int = Field(default=0, ge=0)


class SkillByLead(VarunaModel):
    """Rain CSI at one lead time and threshold (the 'confidence decays after 90 min' chart)."""

    lead_min: int = Field(ge=0)
    threshold_mm_h: Literal[20, 40]
    csi: float | None = Field(default=None, ge=0, le=1)
    pod: float | None = Field(default=None, ge=0, le=1)
    far: float | None = Field(default=None, ge=0, le=1)
    n: int = Field(default=0, ge=0, description="Pixels or gauge-times scored.")


class ReliabilityBin(VarunaModel):
    """One bin of the reliability diagram for P(> 30 cm)."""

    p_lo: Probability
    p_hi: Probability
    forecast_p_mean: Probability
    observed_freq: Probability
    n: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> ReliabilityBin:
        if self.p_hi < self.p_lo:
            msg = "p_hi must not be below p_lo"
            raise ValueError(msg)
        return self


class MissedPin(VarunaModel):
    """A ground-truth pin the model missed, with the likely reason ('Where we are wrong')."""

    pin_id: IdStr
    name: str
    ts: Timestamp
    observed_depth_cm: float | None = Field(default=None, ge=0)
    forecast_p50_cm: float = Field(ge=0)
    forecast_p_gt_30: Probability
    reason: str = Field(description="e.g. 'Drain outfall not in the inferred graph'.")
    source_url: HttpUrlStr


class FlashLiteScore(VarunaModel):
    """Emulator skill versus held-out Twin runs (``docs/verification/flash_lite.json``)."""

    rmse_cm: float = Field(ge=0)
    csi_30: float = Field(ge=0, le=1, description="CSI at 30 cm versus Twin.")
    n_train_runs: int = Field(ge=0)
    n_heldout_runs: int = Field(ge=0)
    fitted_at: Timestamp | None = None


class VerificationSummary(VarunaModel):
    """Headline scores for one event."""

    event: str = Field(description="Bundle id, e.g. MUM-2019-07-02.")
    computed_at: Timestamp
    run_ids: list[str] = Field(default_factory=list, description="Runs scored (one per cycle).")
    threshold_cm: int = Field(default=30, gt=0, description="Depth threshold for CSI/POD/FAR.")
    csi: float | None = Field(default=None, ge=0, le=1)
    pod: float | None = Field(default=None, ge=0, le=1)
    far: float | None = Field(default=None, ge=0, le=1)
    mae_cm: float | None = Field(default=None, ge=0, description="Depth MAE at pins with depth.")
    bias_cm: float | None = Field(default=None, description="Mean forecast minus observed depth.")
    timing_err_min: float | None = Field(default=None, description="Median timing error at spots.")
    brier: float | None = Field(default=None, ge=0, le=1, description="Brier score for P(> 30 cm).")
    roc_auc: float | None = Field(default=None, ge=0, le=1)
    latency_s: float | None = Field(default=None, ge=0, description="Frame-to-product latency.")
    routing_value: Probability | None = Field(
        default=None,
        description="Share of naive emergency trips crossing an observed-impassable segment that "
        "VARUNA routes avoid.",
    )
    lead_time_gained_min: float | None = Field(
        default=None, description="Median minutes between first alert and first observed report."
    )
    n_ground_truth: int = Field(ge=0, description="Sourced pins inside the AOI.")
    n_pins_with_depth: int = Field(default=0, ge=0)
    contingency: ContingencyTable | None = None
    per_hotspot: list[HotspotScore] = Field(default_factory=list)
    skill_by_lead: list[SkillByLead] = Field(default_factory=list)
    reliability: list[ReliabilityBin] = Field(default_factory=list)
    missed: list[MissedPin] = Field(default_factory=list)
    flash_lite: FlashLiteScore | None = None
    limitations: list[str] = Field(
        default_factory=list, description="Blueprint 15.1 limitations in prototype terms."
    )
    label: str = Field(
        default="Computed on a reconstructed replay with sourced ground truth",
        description="Footnote shown next to the numbers.",
    )


__all__ = [
    "ContingencyTable",
    "FlashLiteScore",
    "HotspotScore",
    "MissedPin",
    "ReliabilityBin",
    "SkillByLead",
    "VerificationSummary",
]

"""Per-segment and per-node forecast rows (``segment_forecast.parquet``, ``node_forecast.parquet``)
and the fan-chart series served by ``GET /v1/nowcast/segments/{id}/series`` (SPEC.md 10.3, 12).
"""

from __future__ import annotations

from pydantic import Field, computed_field, model_validator

from varuna_schemas.constants import PROFILE_THRESHOLDS_CM, VehicleProfile
from varuna_schemas.models.common import IdStr, Probability, Timestamp, VarunaModel


class SegmentForecastRow(VarunaModel):
    """One (segment, valid time) row: depth quantiles in cm, exceedance probabilities and
    safe-until per profile (blueprint 9.1 ``segment_forecast``)."""

    run_id: str
    segment_id: IdStr
    valid_ts: Timestamp = Field(description="Forecast valid time (IST).")
    depth_p10_cm: float = Field(ge=0, description="10th percentile depth in cm.")
    depth_p50_cm: float = Field(ge=0, description="Median depth in cm.")
    depth_p90_cm: float = Field(ge=0, description="90th percentile depth in cm.")
    p_gt_15: Probability = Field(description="P(depth > 15 cm): two-wheelers impassable.")
    p_gt_30: Probability = Field(description="P(depth > 30 cm): cars impassable.")
    p_gt_45: Probability = Field(description="P(depth > 45 cm): buses and trucks impassable.")
    p_gt_60: Probability = Field(description="P(depth > 60 cm): rescue vehicles only.")
    safe_until: dict[VehicleProfile, Timestamp | None] = Field(
        default_factory=dict,
        description="First time P(depth > profile threshold) exceeds the risk tolerance; None = "
        "passable through the horizon.",
    )
    velocity_p50_ms: float | None = Field(
        default=None, ge=0, description="Median flow speed in m/s (pedestrian h*v hazard)."
    )

    @model_validator(mode="after")
    def _quantiles_ordered(self) -> SegmentForecastRow:
        if not self.depth_p10_cm <= self.depth_p50_cm <= self.depth_p90_cm:
            msg = "depth quantiles must satisfy p10 <= p50 <= p90"
            raise ValueError(msg)
        if not self.p_gt_15 >= self.p_gt_30 >= self.p_gt_45 >= self.p_gt_60:
            msg = "exceedance probabilities must be non-increasing with threshold"
            raise ValueError(msg)
        return self

    def p_gt(self, threshold_cm: int) -> Probability:
        """Exceedance probability for one of the four thresholds."""
        table = {15: self.p_gt_15, 30: self.p_gt_30, 45: self.p_gt_45, 60: self.p_gt_60}
        try:
            return table[threshold_cm]
        except KeyError as exc:
            msg = f"threshold_cm must be one of 15, 30, 45, 60; got {threshold_cm}"
            raise ValueError(msg) from exc

    def p_impassable(self, profile: VehicleProfile) -> Probability:
        """P(depth > profile threshold) from :data:`PROFILE_THRESHOLDS_CM`."""
        return self.p_gt(PROFILE_THRESHOLDS_CM[profile])


class NodeForecastRow(VarunaModel):
    """One (drain node, valid time) row (blueprint 9.1 ``node_forecast``)."""

    run_id: str
    node_id: IdStr
    valid_ts: Timestamp
    head_p50_m: float = Field(
        description="Median hydraulic head at the node in metres above datum."
    )
    p_surcharge: Probability = Field(description="P(head above ground level).")
    q_surcharge_p50_m3s: float = Field(
        ge=0, description="Median surcharge discharge onto the street in m^3/s."
    )
    responsible_edges: list[IdStr] = Field(
        default_factory=list, description="Pipes ranked by attribution for this node's surcharge."
    )
    reversed_flow: bool = Field(
        default=False,
        description="True when the downstream trunk carries negative flow (tide lock).",
    )


class SeriesPoint(VarunaModel):
    """One time step of a fan chart (depth in cm)."""

    valid_ts: Timestamp
    lead_min: int = Field(description="Minutes from the cycle time; negative = observed half.")
    p10_cm: float = Field(ge=0)
    p50_cm: float = Field(ge=0)
    p90_cm: float = Field(ge=0)
    observed: bool = Field(default=False, description="True for the observed (analysed) half.")


class SafeUntilEntry(VarunaModel):
    profile: VehicleProfile
    threshold_cm: int = Field(gt=0)
    safe_until: Timestamp | None = Field(
        default=None, description="None = passable through the forecast horizon."
    )
    risk_tolerance: Probability


class SegmentSeries(VarunaModel):
    """Fan-chart series and safe-until table for one segment (or hotspot)."""

    run_id: str
    valid_ts: Timestamp = Field(description="Cycle time of the run (IST).")
    segment_id: IdStr
    name: str = Field(description="Street or junction name, e.g. Hindmata junction.")
    points: list[SeriesPoint] = Field(default_factory=list)
    safe_until: list[SafeUntilEntry] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def peak_p50_cm(self) -> float:
        return max((pt.p50_cm for pt in self.points), default=0.0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def time_to_peak(self) -> Timestamp | None:
        if not self.points:
            return None
        return max(self.points, key=lambda pt: pt.p50_cm).valid_ts


__all__ = [
    "NodeForecastRow",
    "SafeUntilEntry",
    "SegmentForecastRow",
    "SegmentSeries",
    "SeriesPoint",
]

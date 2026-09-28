"""Ranked hotspots with exposure and attribution (``hotspots.json``, ``GET /v1/nowcast/hotspots``;
SPEC.md 7.2, 11.7, 11.8)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, computed_field

from varuna_schemas.constants import EMULATOR_LABEL
from varuna_schemas.models.common import (
    Beta,
    IdStr,
    Latitude,
    Longitude,
    Probability,
    Timestamp,
    VarunaModel,
)
from varuna_schemas.models.forecast import SeriesPoint

FacilityKind = Literal["hospital", "fire_station", "station", "transit", "school", "shelter"]


class HotspotExposure(VarunaModel):
    """What is at stake around a hotspot (blueprint 6.8: traffic, hospitals, transit, population)."""

    weight: float = Field(
        ge=0, description="Exposure weight from the city pipeline (dimensionless)."
    )
    traffic_volume_proxy: float | None = Field(
        default=None, ge=0, description="Relative traffic volume proxy by road class."
    )
    nearest_hospital: str | None = Field(default=None, description="e.g. KEM Hospital, Parel.")
    nearest_hospital_m: float | None = Field(default=None, ge=0)
    nearest_station: str | None = Field(default=None, description="e.g. Dadar station.")
    nearest_station_m: float | None = Field(default=None, ge=0)
    transit_lines: list[str] = Field(
        default_factory=list, description="Bus and rail lines affected."
    )
    population_300m: int | None = Field(default=None, ge=0)
    facilities: list[FacilityKind] = Field(
        default_factory=list, description="Icon set shown on the hotspot row."
    )


class AttributionItem(VarunaModel):
    """One responsible pipe from the finite-difference cleaning sensitivity (SPEC.md 11.7)."""

    rank: int = Field(ge=1)
    edge_id: IdStr
    street: str | None = Field(default=None, description="Street the pipe runs under.")
    beta: Beta = Field(description="Posterior mean blockage of the pipe.")
    depth_explained_cm: float = Field(
        ge=0, description="Peak-depth reduction at the hotspot if this pipe alone were cleaned."
    )


class CleanTopNEffect(VarunaModel):
    """Combined effect of cleaning the top N pipes by attribution (the demo's 'clean 14')."""

    n: int = Field(ge=1)
    depth_before_cm: float = Field(ge=0)
    depth_after_cm: float = Field(ge=0)
    minutes_impassable_before: float = Field(ge=0, description="Minutes above the car threshold.")
    minutes_impassable_after: float = Field(ge=0)
    edge_ids: list[IdStr] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def delta_cm(self) -> float:
        return self.depth_after_cm - self.depth_before_cm


class Hotspot(VarunaModel):
    """One ranked hotspot at a selected valid time."""

    rank: int = Field(ge=1)
    id: IdStr = Field(description="Hotspot register id, e.g. hindmata.")
    name: str = Field(description="e.g. Hindmata junction.")
    lon: Longitude
    lat: Latitude
    segment_ids: list[IdStr] = Field(
        default_factory=list, description="Segments forming the hotspot."
    )
    selected_ts: Timestamp | None = Field(
        default=None, description="Valid time the p50 depth refers to (None = peak)."
    )
    depth_p50_cm: float = Field(ge=0, description="Median depth at the selected time in cm.")
    peak_depth_cm: float = Field(ge=0, description="Peak median depth over the horizon in cm.")
    time_to_peak: Timestamp = Field(description="When the peak is reached (IST).")
    p_impassable: Probability = Field(description="P(depth > car threshold) at the peak.")
    impassable_threshold_cm: int = Field(default=30, gt=0)
    minutes_impassable: float = Field(
        default=0.0, ge=0, description="Minutes above the threshold over the horizon."
    )
    expected_impact: float = Field(
        ge=0, description="P(impassable at peak) times exposure weight (ranking key)."
    )
    exposure: HotspotExposure
    attribution: list[AttributionItem] = Field(
        default_factory=list, description="Responsible pipes, best first."
    )
    clean_top_n_effect: CleanTopNEffect | None = None
    sparkline: list[SeriesPoint] = Field(
        default_factory=list, description="3-hour p10/p50/p90 series for the hotspot row."
    )
    source_url: str | None = Field(
        default=None, description="Register source for the chronic-spot location."
    )
    surcharging_nodes: list[IdStr] = Field(
        default_factory=list, description="Manholes predicted to surcharge at this hotspot."
    )


class HotspotList(VarunaModel):
    """``hotspots.json``: ranked hotspots for one run."""

    run_id: str
    valid_ts: Timestamp
    hotspots: list[Hotspot] = Field(default_factory=list)
    attribution_label: str = Field(
        default=EMULATOR_LABEL, description="Honesty label for the attribution method."
    )


__all__ = [
    "AttributionItem",
    "CleanTopNEffect",
    "FacilityKind",
    "Hotspot",
    "HotspotExposure",
    "HotspotList",
]

"""Flood-safe routing, reachability and the road-conditions feed
(``POST /v1/route``, ``GET /v1/reachability``, ``GET /v1/feeds/road-conditions``;
SPEC.md 7.4, 11.9, 12; blueprint 9.3).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, computed_field, model_validator

from varuna_schemas.constants import (
    PROFILE_RISK_TOLERANCE,
    PROFILE_THRESHOLDS_CM,
    REACH_COLLAPSE_RATIO,
    VehicleProfile,
)
from varuna_schemas.models.common import (
    IdStr,
    Latitude,
    LineString,
    Longitude,
    LonLat,
    MultiPolygon,
    Polygon,
    Probability,
    Timestamp,
    VarunaModel,
)
from varuna_schemas.models.hotspot import FacilityKind

RouteConfidence = Literal["high", "medium", "low"]
"""Falls with lead time: high below 60 min, medium to 120 min, low beyond (Sky skill decays)."""

RouteLabel = Literal["VARUNA", "Naive (shortest)", "Alternate"]
RoadStatus = Literal["passable", "degraded", "impassable"]
ReachMinutes = Literal[5, 10, 15]


class RouteRequest(VarunaModel):
    """Body of ``POST /v1/route``."""

    origin: LonLat = Field(description="[lon, lat], e.g. KEM Hospital [72.8419, 19.0035].")
    destination: LonLat = Field(description="[lon, lat], e.g. Sion Hospital [72.8628, 19.0176].")
    depart_at: Timestamp | None = Field(
        default=None, description="Departure (IST); None = the console's scrub time."
    )
    profile: VehicleProfile = "car"
    risk_tolerance: Probability | None = Field(
        default=None, description="Max P(impassable) accepted per segment; None = profile default."
    )
    run_id: str | None = Field(default=None, description="None = the latest published run.")
    alternates: int = Field(default=2, ge=0, le=2, description="Number of alternates to return.")
    origin_name: str | None = None
    destination_name: str | None = None
    spread: bool = Field(
        default=True,
        description="Return up to three safe corridors and an assignment (TECH_SPEC 3.2-3.3).",
    )
    trip_id: str | None = Field(
        default=None,
        description=(
            "The client's own stable id for this trip, so the corridor assignment survives a "
            "reload. Nothing is stored against it; None means the request is not spread."
        ),
    )
    explain: bool = Field(default=True, description="Include structured reasons.")

    @property
    def effective_risk_tolerance(self) -> float:
        return (
            self.risk_tolerance
            if self.risk_tolerance is not None
            else PROFILE_RISK_TOLERANCE[self.profile]
        )

    @property
    def threshold_cm(self) -> int:
        return PROFILE_THRESHOLDS_CM[self.profile]


class AvoidedSegment(VarunaModel):
    """A segment the naive route uses that VARUNA avoids, with why."""

    segment_id: IdStr
    name: str = Field(description="e.g. Hindmata junction")
    reached_ts: Timestamp = Field(description="When the naive route would have reached it (IST).")
    threshold_cm: int = Field(gt=0, description="Profile threshold applied.")
    p_exceed: Probability = Field(description="P(depth > threshold) at reached_ts.")
    depth_p50_cm: float | None = Field(default=None, ge=0)
    closed_reason: str | None = Field(
        default=None,
        description=(
            "Set when an authority closed the street rather than the forecast refusing it; the "
            "officer's own words, never a sentence composed by the API."
        ),
    )


class RouteResult(VarunaModel):
    """One route with its forecast-aware summary."""

    label: RouteLabel = "VARUNA"
    geometry: LineString
    eta_min: float = Field(ge=0, description="Travel time in minutes under the forecast.")
    distance_km: float = Field(ge=0)
    max_expected_depth_cm: float = Field(ge=0, description="Max p50 depth met along the route.")
    safe_until: Timestamp | None = Field(
        default=None,
        description="Latest departure for which the route stays passable; None = horizon.",
    )
    arrival_ts: Timestamp | None = None
    segment_ids: list[IdStr] = Field(default_factory=list)
    max_p_exceed: Probability | None = Field(
        default=None, description="Highest P(impassable) on any segment of the route."
    )


class Corridor(VarunaModel):
    """One of up to three safe roads a request may be spread across (TECH_SPEC 3.3, task D-08).

    The corridors are real - each is a different road and each clears the profile's threshold on
    this run. The ``share`` is **policy**: demand is not measured anywhere in this prototype, and
    every response that carries corridors also carries that disclosure in ``notes``.
    """

    id: str = Field(description="Stable per road: a digest of the corridor's segment sequence.")
    label: Literal["A", "B", "C"]
    route: RouteResult
    share: Probability = Field(description="Fraction of requests policy sends this way.")
    assigned: bool = Field(description="True on the corridor this request was assigned to.")
    capacity_score: float = Field(
        ge=0, description="sum(lanes * (1 - congestion_proxy(depth))) over the corridor's edges."
    )
    max_p_exceed: Probability | None = Field(
        default=None, description="Highest P(impassable) met on this corridor."
    )


ReasonKind = Literal["avoided", "design", "timing", "closure"]
"""The four reasons a route can give for going the way it did (UI_SPEC 4)."""


class RouteReason(VarunaModel):
    """One structured reason. **Never prose** - the frontend words it (TECH_SPEC 3.2).

    Fields not relevant to a ``kind`` are absent. A reason whose number is missing is not
    emitted at all, so a screen never has to soften one into "this road may flood".
    """

    kind: ReasonKind
    segment_id: IdStr
    name: str
    depth_cm: float | None = Field(default=None, ge=0)
    threshold_cm: float | None = Field(default=None, gt=0)
    at: Timestamp | None = Field(default=None, description="The instant the number belongs to.")
    probability: Probability | None = None
    dry_until: Timestamp | None = Field(
        default=None, description="timing: last step under 5 cm on the road actually taken."
    )
    dry_below_cm: float | None = Field(default=None, gt=0)
    design_intensity_mm_h: float | None = Field(
        default=None, gt=0, description="design: what the drain under this street was sized for."
    )
    forecast_peak_mm_h: float | None = Field(
        default=None, ge=0, description="design: this run's peak AOI-mean rain, not local rain."
    )
    reason: str | None = Field(default=None, description="closure: the officer's own words.")
    user: str | None = Field(default=None, description="closure: who entered it.")
    until: Timestamp | None = Field(default=None, description="closure: expiry, if one was set.")


class RouteResponse(VarunaModel):
    """Response of ``POST /v1/route`` (blueprint 9.3 shape, with the naive route for comparison)."""

    run_id: str
    valid_ts: Timestamp = Field(description="Cycle time of the run used (IST).")
    profile: VehicleProfile
    depart_at: Timestamp
    risk_tolerance: Probability
    route: RouteResult
    naive: RouteResult | None = Field(
        default=None, description="Shortest route ignoring the forecast."
    )
    avoided: list[AvoidedSegment] = Field(default_factory=list)
    alternates: list[RouteResult] = Field(default_factory=list)
    corridors: list[Corridor] = Field(
        default_factory=list, description="Up to three safe roads and the share policy gives each."
    )
    reasons: list[RouteReason] = Field(
        default_factory=list, description="Structured, never prose; the frontend words them."
    )
    trip_id: str | None = Field(
        default=None, description="Echoed back so a client can confirm which id was assigned."
    )
    notes: list[str] = Field(
        default_factory=list,
        description=(
            "Honesty labels that must travel with the answer: which probability answered, the "
            "spreading disclosure, and any authority closure applied (SPEC.md rule 6)."
        ),
    )
    confidence: RouteConfidence
    confidence_note: str = Field(description="e.g. 'high (lead 30 min)'.")
    explanation: str | None = Field(
        default=None,
        description="Plain language, e.g. 'Avoids Hindmata (82 % above 45 cm at 18:10) via Bharatmata'.",
    )
    compute_ms: int = Field(ge=0, description="Routing wall-clock (budget 300 ms).")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def eta_delta_min(self) -> float | None:
        """Extra minutes versus the naive route (positive = VARUNA is slower but safer)."""
        return None if self.naive is None else self.route.eta_min - self.naive.eta_min


class Isochrone(VarunaModel):
    """Area reachable from a facility within ``minutes`` under the forecast."""

    minutes: ReachMinutes
    polygon: Polygon | MultiPolygon | None = Field(
        default=None, description="Concave hull of reached nodes; None when nothing is reachable."
    )
    area_km2: float = Field(ge=0)
    reachable_nodes: int | None = Field(default=None, ge=0)


class ReachabilityResponse(VarunaModel):
    """``GET /v1/reachability`` and one feature group of ``reachability.geojson``."""

    run_id: str
    valid_ts: Timestamp = Field(description="Cycle time of the run (IST).")
    t: Timestamp = Field(description="Forecast slice the isochrones are computed for (IST).")
    facility_id: IdStr
    facility_name: str = Field(description="e.g. KEM Hospital, Parel")
    facility_kind: FacilityKind
    lon: Longitude
    lat: Latitude
    profile: VehicleProfile
    isochrones: list[Isochrone] = Field(default_factory=list)
    dry_area_15_km2: float = Field(ge=0, description="15-min catchment area with no flooding.")

    @model_validator(mode="after")
    def _one_per_level(self) -> ReachabilityResponse:
        levels = [iso.minutes for iso in self.isochrones]
        if len(levels) != len(set(levels)):
            msg = "one isochrone per minutes level"
            raise ValueError(msg)
        return self

    def area_km2(self, minutes: int) -> float | None:
        for iso in self.isochrones:
            if iso.minutes == minutes:
                return iso.area_km2
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def catchment_ratio(self) -> float | None:
        """15-min area divided by the dry baseline (the ring gauge)."""
        area = self.area_km2(15)
        if area is None or self.dry_area_15_km2 <= 0:
            return None
        return area / self.dry_area_15_km2

    @computed_field  # type: ignore[prop-decorator]
    @property
    def collapse(self) -> bool:
        """True when the 15-min catchment is below 40 % of the dry baseline."""
        ratio = self.catchment_ratio
        return ratio is not None and ratio < REACH_COLLAPSE_RATIO


class RoadCondition(VarunaModel):
    """Feature properties of the ``road-conditions`` provider feed (impassable / degraded segments
    with validity windows) for navigation and transit apps."""

    segment_id: IdStr
    name: str | None = None
    status: RoadStatus
    profile: VehicleProfile = Field(description="Profile the status applies to.")
    valid_from: Timestamp
    valid_to: Timestamp
    depth_p50_cm: float = Field(ge=0)
    p_exceed: Probability = Field(description="P(depth > profile threshold) in the window.")
    run_id: str

    @model_validator(mode="after")
    def _window(self) -> RoadCondition:
        if self.valid_to < self.valid_from:
            msg = "valid_to must not be before valid_from"
            raise ValueError(msg)
        return self


__all__ = [
    "AvoidedSegment",
    "Corridor",
    "Isochrone",
    "ReachMinutes",
    "ReachabilityResponse",
    "ReasonKind",
    "RoadCondition",
    "RoadStatus",
    "RouteConfidence",
    "RouteLabel",
    "RouteReason",
    "RouteRequest",
    "RouteResponse",
    "RouteResult",
]

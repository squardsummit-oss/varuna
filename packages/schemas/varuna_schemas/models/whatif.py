"""What-if scenarios and the physics check (``POST /v1/whatif``, ``POST /v1/whatif/physics-check``;
SPEC.md 7.7, 11.7).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, computed_field, model_validator

from varuna_schemas.constants import (
    DELTA_UNCHANGED_CM,
    EMULATOR_LABEL,
    PHYSICS_CHECK_TOLERANCE_CM,
    WHATIF_RAIN_SCALE_RANGE,
    WHATIF_TIDE_OFFSET_RANGE_M,
)
from varuna_schemas.models.common import IdStr, Timestamp, VarunaModel

DeltaClass = Literal["improved", "worse", "unchanged"]
"""Diff-layer classes: improved (blue), worse (red), unchanged (grey) at the 3 cm threshold."""


class WhatIfRequest(VarunaModel):
    """Body of ``POST /v1/whatif``."""

    run_id: str | None = Field(default=None, description="Base run; None = latest published.")
    rain_scale: float = Field(
        default=1.0,
        ge=WHATIF_RAIN_SCALE_RANGE[0],
        le=WHATIF_RAIN_SCALE_RANGE[1],
        description="Rain multiplier 0.5-2.0.",
    )
    tide_offset_m: float = Field(
        default=0.0,
        ge=WHATIF_TIDE_OFFSET_RANGE_M[0],
        le=WHATIF_TIDE_OFFSET_RANGE_M[1],
        description="Tide stage offset in metres, -0.5 to +1.0.",
    )
    cleaned_edges: list[IdStr] = Field(
        default_factory=list, description="Pipes set to beta = 0.05 (cleaned)."
    )
    clean_top_n: int | None = Field(
        default=None, ge=1, description="Alternative to cleaned_edges: clean the top N by beta."
    )
    pump_plan: bool = Field(
        default=False, description="Apply the current pump plan as extra outflow."
    )

    @property
    def is_baseline(self) -> bool:
        return (
            self.rain_scale == 1.0
            and self.tide_offset_m == 0.0
            and not self.cleaned_edges
            and self.clean_top_n is None
            and not self.pump_plan
        )


class HotspotDelta(VarunaModel):
    """One row of the delta table: before -> after at a hotspot."""

    hotspot_id: IdStr
    name: str = Field(description="e.g. Hindmata junction")
    depth_before_cm: float = Field(ge=0, description="Peak p50 depth in the base run.")
    depth_after_cm: float = Field(ge=0, description="Peak p50 depth under the scenario.")
    minutes_impassable_before: float = Field(ge=0, description="Minutes above the car threshold.")
    minutes_impassable_after: float = Field(ge=0)
    peak_ts_before: Timestamp | None = None
    peak_ts_after: Timestamp | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def delta_cm(self) -> float:
        """after - before (negative = improved)."""
        return self.depth_after_cm - self.depth_before_cm


class SegmentDelta(VarunaModel):
    """Per-segment change of peak p50 depth for the diff layer."""

    segment_id: IdStr
    delta_p50_cm: float = Field(description="after - before in cm (negative = improved).")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def change(self) -> DeltaClass:
        if self.delta_p50_cm <= -DELTA_UNCHANGED_CM:
            return "improved"
        if self.delta_p50_cm >= DELTA_UNCHANGED_CM:
            return "worse"
        return "unchanged"


class WhatIfResponse(VarunaModel):
    """Response of ``POST /v1/whatif`` (emulator, under a second)."""

    run_id: str = Field(description="Base run the scenario was applied to.")
    whatif_id: str = Field(description="Handle for the physics check and the diff raster.")
    valid_ts: Timestamp
    request: WhatIfRequest
    cleaned_edges: list[IdStr] = Field(
        default_factory=list, description="Pipes actually cleaned (resolved from clean_top_n)."
    )
    hotspots: list[HotspotDelta] = Field(default_factory=list)
    segments_improved: int = Field(default=0, ge=0)
    segments_worse: int = Field(default=0, ge=0)
    segments_unchanged: int = Field(default=0, ge=0)
    segment_deltas: list[SegmentDelta] = Field(
        default_factory=list, description="Optional per-segment deltas (large; omitted by default)."
    )
    diff_raster_path: str | None = Field(
        default=None, description="Relative path of the Δdepth PNG inside the run directory."
    )
    emulator_ms: int = Field(ge=0, description="Emulator wall-clock (budget 1000 ms).")
    label: str = Field(default=EMULATOR_LABEL, description="Honesty badge for the panel.")


class PhysicsCheckRequest(VarunaModel):
    """Body of ``POST /v1/whatif/physics-check``: either a previous what-if or a fresh scenario."""

    whatif_id: str | None = None
    request: WhatIfRequest | None = None

    @model_validator(mode="after")
    def _one_of(self) -> PhysicsCheckRequest:
        if (self.whatif_id is None) == (self.request is None):
            msg = "give exactly one of whatif_id or request"
            raise ValueError(msg)
        return self


class PhysicsCheckHotspot(VarunaModel):
    """Emulator versus Twin peak depth at one hotspot."""

    hotspot_id: IdStr
    name: str
    emulator_cm: float = Field(ge=0)
    twin_cm: float = Field(ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def diff_cm(self) -> float:
        return abs(self.emulator_cm - self.twin_cm)


class PhysicsCheckResponse(VarunaModel):
    """Agreement report shown as a small bar; disagreement is displayed, never hidden."""

    run_id: str
    whatif_id: str
    valid_ts: Timestamp
    hotspots: list[PhysicsCheckHotspot] = Field(default_factory=list)
    tolerance_cm: float = Field(default=PHYSICS_CHECK_TOLERANCE_CM, gt=0)
    twin_ms: int = Field(ge=0, description="Twin wall-clock (budget 10 s).")
    mass_balance_err: float = Field(ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_diff_cm(self) -> float:
        return max((h.diff_cm for h in self.hotspots), default=0.0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_diff_hotspot(self) -> str | None:
        if not self.hotspots:
            return None
        return max(self.hotspots, key=lambda h: h.diff_cm).name

    @computed_field  # type: ignore[prop-decorator]
    @property
    def agrees(self) -> bool:
        return self.max_diff_cm <= self.tolerance_cm

    @computed_field  # type: ignore[prop-decorator]
    @property
    def summary(self) -> str:
        """UI copy, e.g. 'Emulator vs physics: max difference 4 cm at Sion Circle'."""
        if self.max_diff_hotspot is None:
            return "Emulator vs physics: no hotspots compared"
        return (
            f"Emulator vs physics: max difference {round(self.max_diff_cm)} cm "
            f"at {self.max_diff_hotspot}"
        )


__all__ = [
    "DeltaClass",
    "HotspotDelta",
    "PhysicsCheckHotspot",
    "PhysicsCheckRequest",
    "PhysicsCheckResponse",
    "SegmentDelta",
    "WhatIfRequest",
    "WhatIfResponse",
]

"""Observations assimilated by VARUNA-Pulse and the citizen report flow (SPEC.md 7.11, 11.6, 12)."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from varuna_schemas.constants import DEPTH_HINT_CM, DepthHint
from varuna_schemas.models.common import IdStr, Latitude, Longitude, Timestamp, VarunaModel

ObservationType = Literal["traffic", "report", "sensor", "cctv", "sar"]
"""Observation sources (blueprint 9.1 ``observation``); colours are ``--obs-<type>``."""

ObservationQuality = Literal["high", "medium", "low", "rejected"]


class ObservationEffect(VarunaModel):
    """What assimilating one observation changed. Extra keys are allowed so Pulse can add detail."""

    model_config = ConfigDict(extra="allow")

    beta_changes: dict[str, float] = Field(
        default_factory=dict, description="edge_id -> change in posterior mean beta."
    )
    segments_changed: int = Field(
        default=0, ge=0, description="Segments whose p50 moved by more than 3 cm."
    )
    hotspot_depth_deltas_cm: dict[str, float] = Field(
        default_factory=dict, description="hotspot_id -> change in peak p50 depth in cm."
    )
    assimilated_run_id: str | None = Field(
        default=None, description="Run in which the observation was assimilated."
    )
    innovation_cm: float | None = Field(
        default=None, description="Observation minus model depth before the update, in cm."
    )


class Observation(VarunaModel):
    """A single depth observation (``observations.parquet``, ``GET /v1/observations``)."""

    id: IdStr
    ts: Timestamp
    type: ObservationType
    lon: Longitude
    lat: Latitude
    value_cm: float = Field(ge=0, description="Inferred water depth in cm.")
    sd_cm: float = Field(gt=0, description="Standard deviation of the depth in cm.")
    quality: ObservationQuality = "medium"
    source: str = Field(description="Feed or reporter, e.g. 'synthetic traffic' or 'BMC log'.")
    synthetic: bool = Field(description="True for generated streams; the UI labels these.")
    source_url: str | None = Field(
        default=None, description="Mandatory for real ground truth; None for synthetic."
    )
    segment_id: IdStr | None = Field(default=None, description="Nearest road segment, if snapped.")
    text: str | None = Field(default=None, description="Free text from a report or a log line.")
    weight: float = Field(default=1.0, ge=0, description="Reporter trust weight.")
    effect: ObservationEffect | None = Field(
        default=None, description="Filled once assimilated; None while pending."
    )

    @field_validator("source_url")
    @classmethod
    def _http_only(cls, value: str | None) -> str | None:
        if value is not None and not value.lower().startswith(("http://", "https://")):
            msg = "source_url must be an http(s) URL"
            raise ValueError(msg)
        return value


class ReportIn(VarunaModel):
    """Body of ``POST /v1/reports`` from the citizen report flow."""

    lat: Latitude
    lon: Longitude
    depth_hint: DepthHint = Field(description="ankle (~10 cm), knee (~45 cm) or waist (~90 cm).")
    text: str = Field(default="", max_length=500, description="Optional description.")
    photo_b64: str | None = Field(
        default=None, description="Optional JPEG/PNG as base64 (no data: prefix)."
    )
    ts: Timestamp | None = Field(
        default=None, description="Observation time; None = now (server clock or replay clock)."
    )
    reporter_id: str | None = Field(
        default=None, description="Anonymous device token for de-duplication; never a name."
    )

    @property
    def depth_cm(self) -> int:
        return DEPTH_HINT_CM[self.depth_hint][0]

    @property
    def sd_cm(self) -> int:
        return DEPTH_HINT_CM[self.depth_hint][1]


class ReportAck(VarunaModel):
    """Response to ``POST /v1/reports``. The feedback count arrives after the next cycle."""

    id: IdStr = Field(description="Observation id assigned to the report.")
    received_ts: Timestamp
    status: Literal["queued", "assimilated", "duplicate", "rejected"] = "queued"
    message: str = Field(
        description="Plain-language status, e.g. 'Thanks. Your report is queued for the next cycle.'"
    )
    queued_for_run: str | None = Field(default=None, description="Run id it will enter, if known.")
    improved_segments: int | None = Field(
        default=None,
        ge=0,
        description="Segments whose forecast changed by more than 3 cm; None until assimilated.",
    )
    duplicate_of: IdStr | None = Field(
        default=None, description="Existing observation within 50 m and 10 min, if any."
    )


__all__ = [
    "Observation",
    "ObservationEffect",
    "ObservationQuality",
    "ObservationType",
    "ReportAck",
    "ReportIn",
]

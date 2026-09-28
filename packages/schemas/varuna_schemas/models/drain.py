"""Drain graph elements and the drain-health product.

Static graph (blueprint 9.1 ``drain_node``, ``drain_edge``, ``boundary``; SPEC.md 10.1 step 7)
is written by the city pipeline with ``confidence = "inferred"`` on every element. The
drain-health product (``drain_health.geojson``, ``GET /v1/drains/health``; SPEC.md 7.3, 11.6)
carries the posterior blockage learned by VARUNA-Pulse; its feature properties follow
:class:`DrainEdgeHealth`.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, computed_field, model_validator

from varuna_schemas.constants import INFERRED_DRAINS_LABEL
from varuna_schemas.models.common import (
    Beta,
    IdStr,
    Kappa,
    Latitude,
    Longitude,
    Timestamp,
    VarunaModel,
)

DrainConfidence = Literal["inferred", "surveyed"]
"""``inferred`` = synthesised from roads and terrain; ``surveyed`` = from a utility GIS/SWMM model."""

DrainNodeType = Literal[
    "inlet", "manhole", "junction", "outfall", "depression", "hotspot", "tank", "pump_station"
]
DrainShape = Literal["circular", "box"]
BoundaryType = Literal["tide", "river", "fixed", "free"]


class DrainNode(VarunaModel):
    """A node of the drain graph (inlet, manhole, outfall, ...)."""

    id: IdStr
    lon: Longitude
    lat: Latitude
    type: DrainNodeType
    z_ground_m: float = Field(description="Ground level in metres above datum.")
    z_invert_m: float = Field(description="Invert level in metres above datum.")
    storage_area_m2: float = Field(gt=0, description="Manhole plan area for node continuity.")
    inlet_type: str | None = Field(
        default=None, description="e.g. kerb, grate; None if not an inlet."
    )
    inlet_len_m: float | None = Field(default=None, ge=0, description="Weir length L_i.")
    inlet_area_m2: float | None = Field(default=None, ge=0, description="Orifice area A_o.")
    kappa_mean: Kappa = Field(default=0.25, description="Prior mean inlet clogging.")
    kappa_sd: float = Field(default=0.15, ge=0)
    confidence: DrainConfidence = "inferred"
    tidal: bool = Field(default=False, description="Outfall stage follows the tide series.")
    flap_gate: bool = Field(default=False, description="Outfall blocks reverse flow.")

    @model_validator(mode="after")
    def _levels(self) -> DrainNode:
        if self.z_invert_m > self.z_ground_m:
            msg = "z_invert_m must not be above z_ground_m"
            raise ValueError(msg)
        return self


class DrainEdge(VarunaModel):
    """A pipe or box drain between two nodes, sized by the rational method."""

    id: IdStr
    from_node: IdStr
    to_node: IdStr
    street: str | None = Field(
        default=None, description="Street it runs under, e.g. Dr Ambedkar Road."
    )
    length_m: float = Field(gt=0)
    shape: DrainShape = "circular"
    diameter_mm: int | None = Field(
        default=None, gt=0, description="Circular pipes: 450/600/900/1200/1500 mm."
    )
    width_m: float | None = Field(default=None, gt=0, description="Box drains only.")
    height_m: float | None = Field(default=None, gt=0, description="Box drains only.")
    n_manning: float = Field(default=0.013, gt=0)
    slope: float = Field(gt=0, description="Bed slope (dimensionless); minimum 0.003.")
    q_full_m3s: float = Field(gt=0, description="Manning full-flow capacity at beta = 0, m^3/s.")
    beta_prior_mean: Beta = Field(description="Prior blockage mean from land use (0.15-0.35).")
    beta_prior_sd: float = Field(ge=0)
    last_desilted: date | None = None
    confidence: DrainConfidence = "inferred"
    trunk: bool = Field(default=False, description="True for trunks along waterways.")

    @model_validator(mode="after")
    def _geometry(self) -> DrainEdge:
        if self.shape == "circular" and self.diameter_mm is None:
            msg = "a circular pipe needs diameter_mm"
            raise ValueError(msg)
        if self.shape == "box" and (self.width_m is None or self.height_m is None):
            msg = "a box drain needs width_m and height_m"
            raise ValueError(msg)
        return self


class Boundary(VarunaModel):
    """A boundary condition attached to a node (blueprint 9.1 ``boundary``)."""

    id: IdStr
    node_id: IdStr
    type: BoundaryType
    series_source: str = Field(
        description="'tide table <url>', 'illustrative' or 'fixed <stage m>'."
    )


class DrainEdgeHealth(VarunaModel):
    """Posterior blockage state of one pipe (feature properties of ``drain_health.geojson``)."""

    edge_id: IdStr
    street: str | None = None
    beta_mean: Beta = Field(description="Posterior mean blockage.")
    beta_sd: float = Field(ge=0, description="Posterior standard deviation of beta.")
    beta_prior_mean: Beta | None = Field(default=None, description="Prior mean, for before/after.")
    kappa: Kappa | None = Field(default=None, description="Inlet clogging at the upstream inlet.")
    capacity_reduction_pct: float = Field(
        ge=0, le=100, description="100 x (1 - effective capacity / full capacity)."
    )
    confidence: DrainConfidence = "inferred"
    last_update: Timestamp = Field(description="Cycle time of the last posterior update (IST).")
    explains: list[IdStr] = Field(
        default_factory=list, description="Hotspot ids this pipe helps explain."
    )
    observations_n: int = Field(default=0, ge=0, description="Observations that touched this pipe.")
    diameter_mm: int | None = Field(default=None, gt=0)
    q_full_m3s: float | None = Field(default=None, gt=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blocked(self) -> bool:
        """True in the top band of the drain ramp (beta >= 0.75)."""
        return self.beta_mean >= 0.75


class DrainHealthProduct(VarunaModel):
    """Drain-health product for one run in list form (the GeoJSON adds geometry per edge)."""

    run_id: str
    valid_ts: Timestamp
    edges: list[DrainEdgeHealth] = Field(default_factory=list)
    prior_run_id: str | None = Field(
        default=None, description="Run whose posterior is the 'before' state, if any."
    )
    label: str = Field(default=INFERRED_DRAINS_LABEL, description="Honesty label for the panel.")

    def top_by_beta(self, n: int = 25) -> list[DrainEdgeHealth]:
        """The desilting priority list: highest posterior blockage first."""
        return sorted(self.edges, key=lambda e: (-e.beta_mean, e.edge_id))[:n]


__all__ = [
    "Boundary",
    "BoundaryType",
    "DrainConfidence",
    "DrainEdge",
    "DrainEdgeHealth",
    "DrainHealthProduct",
    "DrainNode",
    "DrainNodeType",
    "DrainShape",
]

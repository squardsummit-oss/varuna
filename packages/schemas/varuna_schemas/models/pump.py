"""Mobile pump inventory, dispatch assignments and the dispatch plan (SPEC.md 7.6, 11.10)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, computed_field

from varuna_schemas.models.common import IdStr, Latitude, Longitude, Timestamp, VarunaModel

PumpStatus = Literal["available", "assigned", "en_route", "pumping", "maintenance"]
PumpSolver = Literal["greedy", "milp"]


class Pump(VarunaModel):
    """A mobile pump (``GET /v1/pumps``). The prototype inventory is synthetic and labelled."""

    id: IdStr = Field(description="e.g. P-12")
    name: str = Field(description="Display name, e.g. 'Pump P-12'.")
    capacity_m3h: float = Field(gt=0, description="Rated discharge in m^3/h.")
    depot: str = Field(description="Depot name, e.g. 'Parel depot'.")
    lon: Longitude
    lat: Latitude
    status: PumpStatus = "available"
    synthetic: bool = Field(description="True for the prototype's synthetic inventory.")
    assigned_hotspot_id: IdStr | None = None
    eta_min: int | None = Field(
        default=None, ge=0, description="Travel time to the selected hotspot in minutes."
    )


class PumpAssignment(VarunaModel):
    """One pump sent to one hotspot (blueprint 9.1 ``pump_assignment``)."""

    run_id: str
    pump_id: IdStr
    hotspot_id: IdStr
    hotspot_name: str
    eta_min: int = Field(ge=0, description="Travel time from the depot in minutes.")
    arrive_ts: Timestamp | None = Field(default=None, description="Expected arrival (IST).")
    expected_benefit_min: float = Field(
        ge=0, description="Minutes above 45 cm avoided at the hotspot (emulator estimate)."
    )
    order_text: str = Field(
        description="Plain-language order, e.g. 'Move P-12 from Parel depot to Hindmata now; ETA 25 "
        "min; prevents about 40 min above 45 cm'."
    )


class HotspotPumpBenefit(VarunaModel):
    """Per-hotspot outcome of a plan: minutes above 45 cm before and after."""

    hotspot_id: IdStr
    hotspot_name: str
    minutes_above_45_before: float = Field(ge=0)
    minutes_above_45_after: float = Field(ge=0)
    excess_volume_m3: float = Field(
        ge=0, description="Inflow minus drain outflow over the horizon."
    )
    assigned_pumps: list[IdStr] = Field(default_factory=list)


class PumpPlan(VarunaModel):
    """``pump_plan.json`` and the response of ``POST /v1/pumps/optimise``."""

    run_id: str
    valid_ts: Timestamp = Field(description="Cycle time the plan was solved for (IST).")
    solver: PumpSolver = Field(description="greedy in the prototype; milp is P1.")
    solve_ms: int = Field(ge=0, description="Solver wall-clock in ms (budget 1000).")
    assignments: list[PumpAssignment] = Field(default_factory=list)
    benefits: list[HotspotPumpBenefit] = Field(default_factory=list)
    dispatched: bool = Field(default=False, description="True once 'Dispatch pumps' was pressed.")
    dispatched_ts: Timestamp | None = None
    dispatched_by: str | None = None
    synthetic_inventory: bool = Field(
        default=True, description="Whether the inventory is synthetic (labelled in the UI)."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_benefit_min(self) -> float:
        """Total minutes above 45 cm avoided across hotspots."""
        return float(sum(a.expected_benefit_min for a in self.assignments))


__all__ = ["HotspotPumpBenefit", "Pump", "PumpAssignment", "PumpPlan", "PumpSolver", "PumpStatus"]

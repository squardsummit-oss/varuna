"""Static city layers written by the city-in-a-box pipeline and served by
``GET /v1/city/{city}/layers/{name}`` (SPEC.md 10.1 steps 5, 6 and 8; blueprint 9.1
``road_segment`` and ``surface_unit``).

Each model is the property set of one feature; geometry travels alongside in GeoJSON or
GeoParquet and is optional here so parquet rows validate without it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from varuna_schemas.models.common import (
    IdStr,
    Latitude,
    LineString,
    Longitude,
    Polygon,
    VarunaModel,
)

RoadClass = Literal[
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "residential",
    "unclassified",
    "living_street",
    "service",
]
"""OSM ``highway`` classes folded by the city pipeline (``*_link`` joins its parent class)."""

AssetKind = Literal[
    "hospital",
    "fire_station",
    "station",
    "shelter",
    "pumping_station",
    "holding_tank",
    "depot",
]
"""Curated and OSM assets. Shelters are schools and community centres used as proxies."""


class RoadSegment(VarunaModel):
    """One road segment split at intersections (blueprint 9.1 ``road_segment``)."""

    id: IdStr
    osm_way_id: IdStr | None = Field(default=None, description="Source OSM way, if any.")
    name: str | None = Field(default=None, description="Street name, e.g. Dr Ambedkar Road.")
    road_class: RoadClass
    highway: str | None = Field(default=None, description="Raw OSM highway tag.")
    lanes: int | None = Field(default=None, ge=1)
    oneway: bool = False
    length_m: float = Field(gt=0)
    z_min_m: float = Field(description="Lowest DEM elevation along the segment, m above datum.")
    z_mean_m: float = Field(description="Mean DEM elevation along the segment, m above datum.")
    ward_id: str | None = Field(default=None, description="Municipal ward, e.g. F/North.")
    exposure_weight: float = Field(
        ge=0,
        description="f(class, hospital or station within 300 m, building density); ranks hotspots.",
    )
    speed_kmh: float | None = Field(
        default=None, gt=0, description="Free-flow speed by class for the routing graph."
    )
    underpass: bool = Field(
        default=False, description="Subway or underpass kept as a sink in the conditioned DEM."
    )
    hotspot_id: IdStr | None = Field(
        default=None, description="Register hotspot the segment belongs to, if any."
    )
    geometry: LineString | None = None

    @model_validator(mode="after")
    def _elevations(self) -> RoadSegment:
        if self.z_min_m > self.z_mean_m:
            msg = "z_min_m must not exceed z_mean_m"
            raise ValueError(msg)
        return self


class SurfaceUnit(VarunaModel):
    """A micro-catchment draining to one inlet (blueprint 9.1 ``surface_unit``; SPEC.md 10.1
    step 6: D8 watersheds capped to 0.5-2 ha, or 150 m hexagons as the fallback)."""

    id: IdStr
    inlet_node_id: IdStr | None = Field(default=None, description="Drain node it drains to.")
    segment_id: IdStr | None = Field(default=None, description="Road segment it fronts.")
    area_m2: float = Field(gt=0)
    imperviousness: float = Field(ge=0, le=1, description="Fraction of impervious cover.")
    cn: float = Field(ge=30, le=100, description="SCS curve number (90-98 in the monsoon).")
    n_manning: float = Field(gt=0, description="Area-mean Manning roughness.")
    depression_depth_m: float = Field(
        default=0.0, ge=0, description="Depth of the residual pit inside the unit, if any."
    )
    depression_area_m2: float = Field(default=0.0, ge=0)
    cells: list[int] = Field(
        default_factory=list, description="Flat indices of the 30 m cells in the unit."
    )
    method: Literal["watershed", "hexagon"] = Field(
        default="watershed", description="How the unit was delineated."
    )
    geometry: Polygon | None = None


class Asset(VarunaModel):
    """A facility on the map (hospital, fire station, pumping station, holding tank, depot).

    Real assets carry a ``source_url``; the mobile-pump depots of the prototype are synthetic
    and say so (SPEC.md 3.3, 10.1 step 8).
    """

    id: IdStr
    name: str = Field(description="e.g. KEM Hospital, Parel")
    kind: AssetKind
    lon: Longitude
    lat: Latitude
    source_url: str | None = Field(
        default=None, description="Public source for the location; required unless synthetic."
    )
    synthetic: bool = False
    capacity_m3h: float | None = Field(
        default=None, gt=0, description="Pumping stations: rated discharge in m^3/h."
    )
    volume_m3: float | None = Field(
        default=None, gt=0, description="Holding tanks: storage volume in m^3."
    )
    note: str | None = None

    @model_validator(mode="after")
    def _sourced_or_synthetic(self) -> Asset:
        if not self.synthetic and not self.source_url:
            msg = "a real asset needs a source_url; mark generated assets synthetic=True"
            raise ValueError(msg)
        if self.source_url is not None and not self.source_url.lower().startswith(
            ("http://", "https://")
        ):
            msg = "source_url must be an http(s) URL"
            raise ValueError(msg)
        return self


__all__ = ["Asset", "AssetKind", "RoadClass", "RoadSegment", "SurfaceUnit"]

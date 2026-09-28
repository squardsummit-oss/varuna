"""City-in-a-box configuration: the shape of ``services/city/configs/<city>.yaml``
(SPEC.md 3.3, 10.1). Loaded with :meth:`CityConfig.from_yaml`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, model_validator

from varuna_schemas.constants import CITY_CODES
from varuna_schemas.models.common import BBox, IdStr, Latitude, Longitude, VarunaModel

Tier = Literal["P0", "P1", "P2"]

DEFAULT_MANNING_N: dict[str, float] = {
    "asphalt": 0.016,
    "open_ground": 0.04,
    "vegetation": 0.07,
    "water": 0.03,
}
"""Manning roughness by land-cover class (SPEC.md 10.1 step 4); buildings are blocked cells."""


class RadarDomain(VarunaModel):
    """The Sky domain: a square centred on the AOI (60 km at 500 m for Mumbai, SPEC.md 3.3)."""

    center_lon: Longitude
    center_lat: Latitude
    size_km: float = Field(gt=0, description="Side length of the square domain in km.")
    res_m: float = Field(gt=0, description="Pixel size in metres (500 for pySTEPS).")

    @property
    def n_px(self) -> int:
        """Pixels per side (120 for 60 km at 500 m)."""
        return round(self.size_km * 1000.0 / self.res_m)


class NestSpec(VarunaModel):
    """A fine-resolution nest (5 m at Hindmata and King's Circle, P1)."""

    id: IdStr
    name: str = Field(description="e.g. Hindmata junction")
    lon: Longitude
    lat: Latitude
    size_m: float = Field(default=1000.0, gt=0, description="Side length in metres (1 km^2 nest).")
    res_m: float = Field(default=5.0, gt=0, description="Cell size in metres.")
    tier: Tier = "P1"


class DesignIntensity(VarunaModel):
    """Rational-method design rain intensities in mm/h used to size inferred drains."""

    legacy: float = Field(default=25.0, gt=0, description="Legacy BMC drains, mm/h.")
    upgraded: float = Field(
        default=50.0, gt=0, description="Upgraded (BRIMSTOWAD) corridors, mm/h."
    )


class TidalOutfall(VarunaModel):
    """A coastal or creek outfall whose stage follows the tide (the demo's tide-locked outfall)."""

    id: IdStr
    name: str = Field(description="e.g. Worli sea face outfall")
    lon: Longitude
    lat: Latitude
    flap_gate: bool = Field(default=False, description="True blocks reverse flow.")
    stage_source: str = Field(
        default="illustrative",
        description="'tide table <url>' when public tables were used, else 'illustrative'.",
    )
    note: str | None = None


class CityConfig(VarunaModel):
    """One city's build configuration (``mumbai.yaml``)."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", description="City slug, e.g. mumbai.")
    code: str = Field(
        default="",
        pattern=r"^([A-Z]{3})?$",
        description="Three-letter code used in run ids; defaults from the slug (MUM, CHN).",
    )
    name: str = Field(description="Display name, e.g. Mumbai.")
    aoi_id: str = Field(description="AOI identifier, e.g. MUM-CENTRAL.")
    bbox: BBox = Field(description="WGS84 extent of the AOI.")
    crs: int = Field(ge=1024, le=32767, description="EPSG code of the metric computation CRS.")
    grid_m: float = Field(default=30.0, gt=0, description="City grid cell size in metres.")
    nests: list[NestSpec] = Field(default_factory=list)
    tidal_outfalls: list[TidalOutfall] = Field(default_factory=list)
    design_intensity_mm_h: DesignIntensity = Field(default_factory=DesignIntensity)
    hotspots_path: str = Field(
        default="hotspots.geojson",
        description="Hotspot register, relative to the city folder; every point carries a source_url.",
    )
    infra_path: str | None = Field(
        default=None, description="Hand-curated infrastructure JSON (pumping stations, tanks)."
    )
    radar_domain: RadarDomain
    dem_tiles: list[str] = Field(
        default_factory=list,
        description=(
            "Copernicus GLO-30 tile file names under city/cache/dem/ that cover the AOI "
            "(SPEC.md 10.1 step 1). Names only, never URLs: the pipeline reads the cache."
        ),
    )
    landcover_tiles: list[str] = Field(
        default_factory=list,
        description=(
            "ESA WorldCover 10 m tile file names under city/cache/worldcover/ covering the AOI "
            "(SPEC.md 10.1 step 3)."
        ),
    )
    manning_n: dict[str, float] = Field(
        default_factory=lambda: dict(DEFAULT_MANNING_N),
        description="Manning n by land-cover class.",
    )
    cn_range: tuple[int, int] = Field(
        default=(90, 98), description="SCS curve-number range for saturated monsoon soils."
    )
    depression_min_area_m2: float = Field(
        default=900.0, gt=0, description="Pits smaller than this are breached as spurious."
    )
    building_burn_m: float = Field(default=5.0, ge=0, description="Height added to building cells.")
    road_carve_m: float = Field(
        default=0.15, ge=0, description="Depth carved along road centrelines."
    )
    min_drain_slope: float = Field(default=0.003, gt=0, description="Minimum inferred pipe slope.")
    inlet_spacing_m: float = Field(default=40.0, gt=0, description="Inlet spacing along roads.")

    @model_validator(mode="before")
    @classmethod
    def _default_code(cls, value: Any) -> Any:
        if isinstance(value, dict) and not value.get("code"):
            slug = value.get("id")
            if isinstance(slug, str) and slug.lower() in CITY_CODES:
                value = {**value, "code": CITY_CODES[slug.lower()]}
        return value

    @model_validator(mode="after")
    def _code_present(self) -> CityConfig:
        if not self.code:
            msg = f"city {self.id!r} is not in CITY_CODES; set 'code' (three upper-case letters)"
            raise ValueError(msg)
        if self.cn_range[0] > self.cn_range[1]:
            msg = "cn_range must be (low, high)"
            raise ValueError(msg)
        return self

    @property
    def crs_string(self) -> str:
        """``EPSG:32643`` form for rasterio and pyproj."""
        return f"EPSG:{self.crs}"

    @classmethod
    def from_yaml(cls, path: Path) -> CityConfig:
        """Load and validate a city config file."""
        with Path(path).open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls.model_validate(data)


__all__ = [
    "DEFAULT_MANNING_N",
    "CityConfig",
    "DesignIntensity",
    "NestSpec",
    "RadarDomain",
    "TidalOutfall",
    "Tier",
]

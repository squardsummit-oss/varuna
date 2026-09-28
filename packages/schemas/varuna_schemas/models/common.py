"""Base model, shared field types and GeoJSON geometry models.

Conventions enforced here for every VARUNA model:

- ``extra="forbid"``: an unknown field is a contract error, not silently dropped.
- Timestamps are timezone-aware and normalised to IST (+05:30) on validation, so
  artifacts on disk and API responses always read as "2019-07-02T17:40:00+05:30".
- Ids (segment, node, edge, hotspot, pump, alert) are strings; integers are coerced.
- Coordinates are WGS84 ``[lon, lat]``; lengths in metres unless the name says otherwise.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from varuna_schemas.constants import IST


class VarunaModel(BaseModel):
    """Base for every model in ``varuna_schemas``."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        ser_json_inf_nan="null",
    )

    @model_validator(mode="before")
    @classmethod
    def _strip_computed_fields(cls, value: Any) -> Any:
        """Let serialised output round-trip.

        ``computed_field`` values are written on dump (``total_ms`` in ``run.json``) but are
        derived, not inputs. They are dropped here so ``extra="forbid"`` still rejects
        genuinely unknown keys while artifacts and API responses re-validate cleanly.
        """
        computed = cls.model_computed_fields
        if computed and isinstance(value, dict) and any(key in value for key in computed):
            return {key: item for key, item in value.items() if key not in computed}
        return value


# ----------------------------------------------------------------------------- timestamps
def to_ist(value: datetime) -> datetime:
    """Convert an aware datetime to IST (+05:30). Naive datetimes are rejected upstream."""
    return value.astimezone(IST)


Timestamp = Annotated[AwareDatetime, AfterValidator(to_ist)]
"""An ISO 8601 timestamp with offset, normalised to +05:30 (SPEC.md 12)."""


# ----------------------------------------------------------------------------- ids
def _stringify_id(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    return value


IdStr = Annotated[str, BeforeValidator(_stringify_id), Field(min_length=1)]
"""A stable identifier. OSM-derived integers (``88213``) are accepted and stored as strings."""


# ----------------------------------------------------------------------------- numbers
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
"""A probability in [0, 1] (shown as a percentage in the UI)."""

Beta = Annotated[float, Field(ge=0.0, le=1.0)]
"""Pipe blockage fraction beta in [0, 1] (1 = fully blocked)."""

Kappa = Annotated[float, Field(ge=0.0, le=1.0)]
"""Inlet clogging fraction kappa in [0, 1]."""

Longitude = Annotated[float, Field(ge=-180.0, le=180.0)]
Latitude = Annotated[float, Field(ge=-90.0, le=90.0)]

LonLat = tuple[Longitude, Latitude]
"""A WGS84 position as ``[lon, lat]`` (GeoJSON order)."""

Position = tuple[float, float] | tuple[float, float, float]
"""A GeoJSON position: ``[lon, lat]`` or ``[lon, lat, z]``."""


# ----------------------------------------------------------------------------- urls
def _check_http_url(value: str) -> str:
    if not value.lower().startswith(("http://", "https://")):
        msg = "must be an http(s) URL"
        raise ValueError(msg)
    return value


HttpUrlStr = Annotated[str, Field(min_length=8), AfterValidator(_check_http_url)]
"""A public ``http(s)://`` source URL (mandatory on every ground-truth pin, SPEC.md 0.7)."""


# ----------------------------------------------------------------------------- bbox
class BBox(VarunaModel):
    """A WGS84 bounding box. Accepts ``[min_lon, min_lat, max_lon, max_lat]`` as well as an object."""

    min_lon: Longitude
    min_lat: Latitude
    max_lon: Longitude
    max_lat: Latitude

    @model_validator(mode="before")
    @classmethod
    def _from_sequence(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            if len(value) != 4:
                msg = "A bbox list needs exactly four numbers: min_lon, min_lat, max_lon, max_lat"
                raise ValueError(msg)
            return dict(zip(("min_lon", "min_lat", "max_lon", "max_lat"), value, strict=True))
        return value

    @model_validator(mode="after")
    def _ordered(self) -> BBox:
        if self.min_lon >= self.max_lon or self.min_lat >= self.max_lat:
            msg = "bbox must satisfy min_lon < max_lon and min_lat < max_lat"
            raise ValueError(msg)
        return self

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_lon + self.max_lon) / 2.0, (self.min_lat + self.max_lat) / 2.0)

    def contains(self, lon: float, lat: float) -> bool:
        return self.min_lon <= lon <= self.max_lon and self.min_lat <= lat <= self.max_lat


# ----------------------------------------------------------------------------- GeoJSON
class Point(VarunaModel):
    type: Literal["Point"] = "Point"
    coordinates: Position


class LineString(VarunaModel):
    type: Literal["LineString"] = "LineString"
    coordinates: list[Position] = Field(min_length=2)


class MultiLineString(VarunaModel):
    type: Literal["MultiLineString"] = "MultiLineString"
    coordinates: list[list[Position]]


class Polygon(VarunaModel):
    """Rings of positions; the first ring is the exterior. Rings must close (first == last)."""

    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[Position]] = Field(min_length=1)

    @model_validator(mode="after")
    def _rings_closed(self) -> Polygon:
        for ring in self.coordinates:
            if len(ring) < 4:
                msg = "A polygon ring needs at least four positions (closed triangle)"
                raise ValueError(msg)
            if tuple(ring[0]) != tuple(ring[-1]):
                msg = "A polygon ring must close: first position must equal the last"
                raise ValueError(msg)
        return self


class MultiPolygon(VarunaModel):
    type: Literal["MultiPolygon"] = "MultiPolygon"
    coordinates: list[list[list[Position]]]


Geometry = Point | LineString | MultiLineString | Polygon | MultiPolygon


class Feature(VarunaModel):
    type: Literal["Feature"] = "Feature"
    id: IdStr | None = None
    geometry: Geometry | None
    properties: dict[str, Any] = Field(default_factory=dict)


class FeatureCollection(VarunaModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[Feature] = Field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None


__all__ = [
    "BBox",
    "Beta",
    "Feature",
    "FeatureCollection",
    "Geometry",
    "HttpUrlStr",
    "IdStr",
    "Kappa",
    "Latitude",
    "LineString",
    "LonLat",
    "Longitude",
    "MultiLineString",
    "MultiPolygon",
    "Point",
    "Polygon",
    "Position",
    "Probability",
    "Timestamp",
    "VarunaModel",
    "to_ist",
]

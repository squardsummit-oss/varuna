"""City assets: hospitals, fire stations, stations, pumps and tanks (SPEC.md 10.1 step 8).

Curated infrastructure lives in ``services/city/assets/<city>_infra.json``: every hand-curated
entry carries a ``source_url``, and the mobile pump inventory is synthetic and says so on every
record (``synthetic: true``), because the UI must label it "Synthetic pump inventory".
OSM assets extracted by :mod:`varuna_city.osm` are merged in on top of the curated list.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from varuna_schemas.paths import city_dir, repo_root

log = structlog.get_logger("varuna.city.assets")

INFRA_KINDS: dict[str, str] = {
    "hospitals": "hospital",
    "fire_stations": "fire_station",
    "stations": "station",
    "pumping_stations": "pumping_station",
    "holding_tanks": "holding_tank",
    "depots": "depot",
    "mobile_pumps": "mobile_pump",
}
"""Section in the infra JSON -> ``kind`` written on the feature."""


def infra_path(city: str = "mumbai") -> Path:
    """``services/city/assets/<city>_infra.json``."""
    return repo_root() / "services" / "city" / "assets" / f"{city}_infra.json"


def load_infra(city: str = "mumbai", *, path: Path | None = None) -> dict[str, Any]:
    """Read the curated infrastructure file (raises if it is missing)."""
    target = path or infra_path(city)
    if not target.is_file():
        msg = f"No curated infrastructure for {city}: expected {target}"
        raise FileNotFoundError(msg)
    return json.loads(target.read_text(encoding="utf-8"))


def _feature(entry: dict[str, Any], kind: str, index: int) -> dict[str, Any] | None:
    try:
        lon, lat = float(entry["lon"]), float(entry["lat"])
    except (KeyError, TypeError, ValueError):
        log.warning("assets.skip_no_coords", kind=kind, name=entry.get("name"))
        return None
    synthetic = bool(entry.get("synthetic", False))
    asset_id = str(entry.get("id") or f"{kind}-{index + 1:03d}")
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "asset_id": asset_id,
            "kind": kind,
            "name": entry.get("name") or asset_id,
            "operator": entry.get("operator"),
            "ward": entry.get("ward"),
            "capacity_m3_per_h": entry.get("capacity_m3_per_h"),
            "capacity_m3": entry.get("capacity_m3"),
            "status": entry.get("status"),
            "depot": entry.get("depot"),
            "synthetic": synthetic,
            "source_url": entry.get("source_url"),
            "source": "synthetic" if synthetic else "curated",
            "notes": entry.get("notes"),
        },
    }


def infra_features(city: str = "mumbai", *, path: Path | None = None) -> list[dict[str, Any]]:
    """Curated + synthetic assets as GeoJSON features."""
    infra = load_infra(city, path=path)
    features: list[dict[str, Any]] = []
    for section, kind in INFRA_KINDS.items():
        for i, entry in enumerate(infra.get(section) or []):
            feature = _feature(entry, kind, i)
            if feature is not None:
                features.append(feature)
    return features


OSM_KIND: dict[str, str] = {
    "hospitals": "hospital",
    "fire_stations": "fire_station",
    "stations": "station",
    "shelters": "shelter",
}
"""``OsmLayers.assets()`` layer name -> the ``kind`` written on the feature."""


def _osm_features(osm_assets: Any) -> list[dict[str, Any]]:
    """Accept a GeoDataFrame, a GeoJSON mapping or a list of features from :mod:`osm`.

    A GeoDataFrame arrives in the city's metric CRS; GeoJSON is WGS84 by definition, so it
    is reprojected before anything reads a coordinate.
    """
    if osm_assets is None:
        return []
    if hasattr(osm_assets, "to_crs") and getattr(osm_assets, "crs", None) is not None:
        osm_assets = osm_assets.to_crs("EPSG:4326")
    if hasattr(osm_assets, "__geo_interface__"):
        osm_assets = osm_assets.__geo_interface__
    if isinstance(osm_assets, dict):
        osm_assets = osm_assets.get("features", [])
    out: list[dict[str, Any]] = []
    for i, feature in enumerate(osm_assets):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        props = dict(feature.get("properties") or {})
        raw_kind = (
            props.get("kind") or props.get("asset_kind") or props.get("amenity") or "osm_asset"
        )
        kind = OSM_KIND.get(str(raw_kind), str(raw_kind))
        osm_id = props.get("osm_id") or props.get("id") or i
        point = geometry
        if geometry.get("type") != "Point":
            continue  # osm.py centroids its polygons; anything else is skipped, not guessed
        out.append(
            {
                "type": "Feature",
                "geometry": point,
                "properties": {
                    "asset_id": f"osm-{osm_id}",
                    "kind": str(kind),
                    "name": props.get("name") or f"OSM {kind} {osm_id}",
                    "operator": props.get("operator"),
                    "ward": props.get("ward"),
                    "synthetic": False,
                    "source_url": props.get("source_url")
                    or f"https://www.openstreetmap.org/node/{osm_id}",
                    "source": "osm",
                    "notes": None,
                },
            }
        )
    return out


def build_assets(
    city: str = "mumbai",
    *,
    osm_assets: Any = None,
    out_dir: Path | None = None,
    path: Path | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    curated: bool = True,
) -> Path:
    """Merge curated infrastructure with OSM assets into ``city/<city>/assets.geojson``.

    ``bbox`` (WGS84 ``minlon, minlat, maxlon, maxlat``) does not filter: it flags every
    feature with ``in_aoi``. Three of the BMC pumping stations sit just outside the
    MUM-CENTRAL box and still matter to an operator, so they are kept and labelled rather
    than silently dropped. OSM assets already come from the AOI query.

    ``curated=False`` skips the hand-curated infrastructure file, for a city onboarded on stage
    that has never had one written. The OSM hospitals, fire stations and stations still come
    through - which is what reachability and exposure weights actually read - and the metadata
    records that the curated half is missing rather than implying it was empty.
    """
    features = infra_features(city, path=path) if curated else []
    seen = {(round(f["geometry"]["coordinates"][0], 5), f["properties"]["kind"]) for f in features}
    for feature in _osm_features(osm_assets):
        key = (round(feature["geometry"]["coordinates"][0], 5), feature["properties"]["kind"])
        if key in seen:
            continue
        seen.add(key)
        features.append(feature)
    for feature in features:
        lon, lat = feature["geometry"]["coordinates"][:2]
        feature["properties"]["in_aoi"] = (
            True if bbox is None else bool(bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3])
        )
    features.sort(key=lambda f: (f["properties"]["kind"], f["properties"]["asset_id"]))
    synthetic = sum(1 for f in features if f["properties"]["synthetic"])
    collection = {
        "type": "FeatureCollection",
        "name": f"{city}_assets",
        "metadata": {
            "city": city,
            "task": "P1.9",
            "count": len(features),
            "synthetic": synthetic,
            "curated": sum(1 for f in features if f["properties"]["source"] == "curated"),
            "from_osm": sum(1 for f in features if f["properties"]["source"] == "osm"),
            "with_source_url": sum(1 for f in features if f["properties"]["source_url"]),
            "kinds": {
                kind: sum(1 for f in features if f["properties"]["kind"] == kind)
                for kind in sorted({f["properties"]["kind"] for f in features})
            },
            "curated_infrastructure": curated,
            "honesty": (
                "Mobile pumps are a synthetic inventory; every one carries synthetic=true."
                if curated
                else "OSM facilities only: no curated infrastructure has been researched for this "
                "city, so there are no pumping stations, holding tanks or mobile pumps."
            ),
        },
        "features": features,
    }
    target = (out_dir or city_dir(city)) / "assets.geojson"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(collection, indent=1) + "\n", encoding="utf-8", newline="\n")
    log.info("assets.written", path=str(target), count=len(features), synthetic=synthetic)
    return target


__all__ = ["INFRA_KINDS", "OSM_KIND", "build_assets", "infra_features", "infra_path", "load_infra"]

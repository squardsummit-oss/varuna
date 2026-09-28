"""Contract tests for ``GET /v1/city/{city}/layers/{name}`` (SPEC.md 12, task P1.12).

The router reads files the city pipeline wrote, so the fixtures write a tiny city folder and
point ``VARUNA_CITY_DIR`` at it: the tests never depend on a real Mumbai build.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from varuna_api.routers.city import feature_in_bbox, filter_collection

HINDMATA = (72.8421, 19.0101)
ANDHERI = (72.8470, 19.1193)


def _point(name: str, lon: float, lat: float) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"name": name, "sourced": True},
    }


def _line(segment_id: str, lon: float, lat: float) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[lon, lat], [lon + 0.001, lat + 0.001]],
        },
        "properties": {"segment_id": segment_id, "class": "primary"},
    }


@pytest.fixture
def city_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A city folder with two map layers, wired in through ``VARUNA_CITY_DIR``."""
    root = tmp_path / "city"
    maps = root / "mumbai" / "map"
    maps.mkdir(parents=True)
    (maps / "hotspots.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    _point("Hindmata junction", *HINDMATA),
                    _point("Andheri subway", *ANDHERI),
                ],
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
        newline="\n",
    )
    (maps / "segments.geojson").write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": [_line("S1-000", *HINDMATA)]},
            separators=(",", ":"),
        ),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("VARUNA_CITY_DIR", str(root))
    yield root


def test_layer_returns_geojson_with_cache_headers(client: TestClient, city_root: Path) -> None:
    res = client.get("/v1/city/mumbai/layers/hotspots")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/geo+json")
    assert res.headers["cache-control"] == "public, max-age=3600"
    assert res.headers["x-layer"] == "hotspots"
    body = res.json()
    assert body["type"] == "FeatureCollection"
    assert [f["properties"]["name"] for f in body["features"]] == [
        "Hindmata junction",
        "Andheri subway",
    ]


def test_etag_answers_304(client: TestClient, city_root: Path) -> None:
    first = client.get("/v1/city/mumbai/layers/hotspots")
    etag = first.headers["etag"]
    again = client.get("/v1/city/mumbai/layers/hotspots", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["etag"] == etag


def test_bbox_filters_features(client: TestClient, city_root: Path) -> None:
    res = client.get(
        "/v1/city/mumbai/layers/hotspots",
        params={"bbox": "72.83,19.00,72.85,19.02"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [f["properties"]["name"] for f in body["features"]] == ["Hindmata junction"]
    assert body["bbox"] == [72.83, 19.0, 72.85, 19.02]


def test_bad_bbox_is_a_400_envelope(client: TestClient, city_root: Path) -> None:
    res = client.get("/v1/city/mumbai/layers/hotspots", params={"bbox": "72.83,19.00"})
    assert res.status_code == 400
    error = res.json()["error"]
    assert error["code"] == "bad_bbox"
    assert "minlon,minlat,maxlon,maxlat" in error["message"]


def test_unbuilt_city_names_the_make_target(client: TestClient, city_root: Path) -> None:
    res = client.get("/v1/city/chennai/layers/segments")
    assert res.status_code == 404
    error = res.json()["error"]
    assert error["code"] == "city_not_built"
    assert "make city CITY=chennai" in error["message"]
    assert error["run_id"] is None


def test_missing_layer_names_the_rebuild(client: TestClient, city_root: Path) -> None:
    res = client.get("/v1/city/mumbai/layers/drains")
    assert res.status_code == 404
    error = res.json()["error"]
    assert error["code"] == "layer_not_built"
    assert "city/mumbai/map/drains.geojson" in error["message"]


def test_unknown_layer_name_is_rejected(client: TestClient, city_root: Path) -> None:
    res = client.get("/v1/city/mumbai/layers/tsunami")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"


def test_layer_path_traversal_is_refused(client: TestClient, city_root: Path) -> None:
    res = client.get("/v1/city/..%2F..%2Fetc/layers/segments")
    assert res.status_code in {404, 422}
    assert "error" in res.json()


def test_openapi_declares_the_layer_contract(client: TestClient) -> None:
    doc = client.get("/openapi.json").json()
    operation = doc["paths"]["/v1/city/{city}/layers/{name}"]["get"]
    assert "404" in operation["responses"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "FeatureCollection"
    )
    names = next(p for p in operation["parameters"] if p["name"] == "name")
    assert "segments" in names["schema"]["enum"]
    assert "drains" in names["schema"]["enum"]


@pytest.mark.parametrize(
    ("bbox", "expected"),
    [
        ((72.80, 18.99, 72.90, 19.05), True),
        ((72.90, 19.20, 72.95, 19.30), False),
    ],
)
def test_feature_in_bbox(bbox: tuple[float, float, float, float], expected: bool) -> None:
    assert feature_in_bbox(_point("Hindmata junction", *HINDMATA), bbox) is expected


def test_filter_collection_keeps_the_envelope() -> None:
    payload = {
        "type": "FeatureCollection",
        "name": "mumbai_hotspots",
        "features": [_point("Hindmata junction", *HINDMATA), _point("Andheri subway", *ANDHERI)],
    }
    out = filter_collection(payload, (72.80, 19.00, 72.86, 19.02))
    assert out["name"] == "mumbai_hotspots"
    assert len(out["features"]) == 1

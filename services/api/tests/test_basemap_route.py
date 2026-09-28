"""``GET /v1/city/{city}/basemap.pmtiles`` (task P9.10): whole file, byte ranges, 304, 404."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from varuna_city.basemap_tiles import ATTRIBUTION_ASCII

BODY = b"PMTiles" + bytes(range(256)) * 4


@pytest.fixture
def basemap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "city"
    maps = root / "mumbai" / "map"
    maps.mkdir(parents=True)
    path = maps / "basemap.pmtiles"
    path.write_bytes(BODY)
    monkeypatch.setenv("VARUNA_CITY_DIR", str(root))
    return path


def test_serves_the_whole_archive_with_its_credit(client: TestClient, basemap: Path) -> None:
    response = client.get("/v1/city/mumbai/basemap.pmtiles")
    assert response.status_code == 200
    assert response.content == BODY
    assert response.headers["content-type"] == "application/vnd.pmtiles"
    assert response.headers["x-attribution"] == ATTRIBUTION_ASCII
    assert response.headers["etag"]


def test_serves_byte_ranges(client: TestClient, basemap: Path) -> None:
    response = client.get("/v1/city/mumbai/basemap.pmtiles", headers={"Range": "bytes=0-6"})
    assert response.status_code == 206
    assert response.content == b"PMTiles"


def test_answers_304_to_a_matching_etag(client: TestClient, basemap: Path) -> None:
    etag = client.get("/v1/city/mumbai/basemap.pmtiles").headers["etag"]
    again = client.get("/v1/city/mumbai/basemap.pmtiles", headers={"If-None-Match": etag})
    assert again.status_code == 304


def test_missing_basemap_names_the_command(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path / "nowhere"))
    response = client.get("/v1/city/mumbai/basemap.pmtiles")
    assert response.status_code == 404
    body = response.json()["error"]
    assert body["code"] == "basemap_not_built"
    assert "varuna_city.basemap_tiles --city mumbai" in body["message"]


def test_a_city_that_is_not_a_path_segment_is_refused(client: TestClient, basemap: Path) -> None:
    response = client.get("/v1/city/..%2Fsecrets/basemap.pmtiles")
    assert response.status_code == 404

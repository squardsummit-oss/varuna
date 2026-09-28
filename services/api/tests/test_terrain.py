"""``GET /v1/city/{city}/terrain`` and ``terrain.png`` (SPEC.md 6.7, task P6.15)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

TRANSFORM = (30.0, 0.0, 269970.0, 0.0, -30.0, 2117220.0)


@pytest.fixture
def city_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "city"
    (root / "mumbai").mkdir(parents=True)
    monkeypatch.setenv("VARUNA_CITY_DIR", str(root))
    return root


def _write_dem(root: Path) -> None:
    import rasterio
    from rasterio.transform import Affine

    heights = np.linspace(-1.0, 12.0, 12).reshape(4, 3).astype("float32")
    with rasterio.open(
        root / "mumbai" / "dem_conditioned.tif",
        "w",
        driver="GTiff",
        height=4,
        width=3,
        count=1,
        dtype="float32",
        crs="EPSG:32643",
        transform=Affine(*TRANSFORM),
    ) as dst:
        dst.write(heights, 1)


def test_meta_carries_the_decoder_bounds_and_the_png_url(
    client: TestClient, city_root: Path
) -> None:
    _write_dem(city_root)
    response = client.get("/v1/city/mumbai/terrain")
    assert response.status_code == 200
    body = response.json()
    assert body["encoding"] == "terrarium"
    assert body["decoder"] == {
        "r_scaler": 256.0,
        "g_scaler": 1.0,
        "b_scaler": 1.0 / 256.0,
        "offset": -32768.0,
    }
    assert len(body["bounds"]) == 4
    assert body["shape"] == [4, 3]
    assert body["png_url"].endswith("/v1/city/mumbai/terrain.png")
    assert response.headers["cache-control"].startswith("public")


def test_png_is_served_with_an_etag_and_answers_304(client: TestClient, city_root: Path) -> None:
    _write_dem(city_root)
    first = client.get("/v1/city/mumbai/terrain.png")
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/png"
    assert first.content[:8] == b"\x89PNG\r\n\x1a\n"
    again = client.get(
        "/v1/city/mumbai/terrain.png", headers={"If-None-Match": first.headers["etag"]}
    )
    assert again.status_code == 304


def test_a_city_without_a_dem_names_the_command_that_builds_it(
    client: TestClient, city_root: Path
) -> None:
    response = client.get("/v1/city/mumbai/terrain")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "terrain_not_built"
    assert "make city CITY=mumbai" in error["message"]

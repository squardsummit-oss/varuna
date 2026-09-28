"""The console's 3D heightmap (task P6.15): encoding, placement and determinism."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from varuna_city.terrain_export import (
    decode_terrarium,
    ensure_terrain,
    export_terrain,
    main,
    terrarium_rgb,
)

CRS = "EPSG:32643"
# A 5 x 4 patch of the Mumbai grid: 30 m cells, the real AOI's top-left corner.
TRANSFORM = (30.0, 0.0, 269970.0, 0.0, -30.0, 2117220.0)


def _write_dem(city_root: Path, heights: np.ndarray) -> Path:
    import rasterio
    from rasterio.transform import Affine

    base = city_root / "testcity"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "dem_conditioned.tif"
    a, b, c, d, e, f = TRANSFORM
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=heights.shape[0],
        width=heights.shape[1],
        count=1,
        dtype="float32",
        crs=CRS,
        transform=Affine(a, b, c, d, e, f),
        nodata=-9999.0,
    ) as dst:
        dst.write(np.where(np.isfinite(heights), heights, -9999.0).astype("float32"), 1)
    return path


@pytest.fixture
def city_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path))
    return tmp_path


def test_terrarium_round_trips_signed_heights_to_the_format_step() -> None:
    heights = np.array([[-8.935, -0.15, 0.0], [10.008, 147.5, 0.00390625]])
    decoded = decode_terrarium(terrarium_rgb(heights))
    # One Terrarium unit is 1/256 m, so rounding costs at most half of one.
    assert np.max(np.abs(decoded - heights)) <= 0.5 / 256 + 1e-12


def test_nodata_is_encoded_as_sea_level_not_a_spike() -> None:
    rgb = terrarium_rgb(np.array([[np.nan, 5.0]]))
    assert decode_terrarium(rgb)[0, 0] == 0.0


def test_export_places_the_heightmap_on_the_depth_rasters_own_bounds(city_root: Path) -> None:
    from varuna_products.depth import depth_bounds

    heights = np.arange(20, dtype=float).reshape(5, 4) - 3.0
    _write_dem(city_root, heights)

    result = export_terrain("testcity")

    meta = result.meta_json
    expected = depth_bounds(TRANSFORM, (5, 4), CRS)["wgs84"]
    assert meta["bounds"] == pytest.approx(expected, abs=1e-12)
    assert meta["shape"] == [5, 4]
    assert meta["encoding"] == "terrarium"

    from PIL import Image

    with Image.open(result.png) as image:
        assert image.size == (4, 5)  # width, height: pixel for pixel, not resampled
        rgb = np.asarray(image.convert("RGB"))
    assert np.max(np.abs(decode_terrarium(rgb) - heights)) <= 0.5 / 256 + 1e-12
    assert json.loads(result.meta.read_text(encoding="utf-8")) == meta


def test_two_exports_are_byte_identical(city_root: Path) -> None:
    _write_dem(city_root, np.linspace(-2.0, 40.0, 20).reshape(5, 4))
    first = export_terrain("testcity").png.read_bytes()
    second = export_terrain("testcity").png.read_bytes()
    assert first == second


def test_ensure_terrain_is_none_without_a_dem_and_exports_once_with_one(city_root: Path) -> None:
    assert ensure_terrain("testcity") is None
    _write_dem(city_root, np.zeros((5, 4)))
    made = ensure_terrain("testcity")
    assert made is not None and made.png.exists()
    again = ensure_terrain("testcity")
    assert again is not None and again.meta_json == made.meta_json


def test_cli_names_the_command_that_builds_the_dem(
    city_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--city", "testcity"]) == 2
    assert "make city CITY=testcity" in capsys.readouterr().err

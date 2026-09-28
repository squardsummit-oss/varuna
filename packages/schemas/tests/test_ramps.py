from __future__ import annotations

import math

import pytest
from varuna_schemas import ramps
from varuna_schemas.tokens import depth_bands, drain_bands, rain_bands

HEX = {b.key: b.hex for b in depth_bands()}
DRAIN_HEX = {b.key: b.hex for b in drain_bands()}
RAIN_HEX = {b.key: b.hex for b in rain_bands()}


@pytest.mark.parametrize(
    ("cm", "key"),
    [
        (-3.0, "dry"),
        (0.0, "dry"),
        (4.99, "dry"),
        (5.0, "1"),
        (14.99, "1"),
        (15.0, "2"),
        (29.99, "2"),
        (30.0, "3"),
        (44.99, "3"),
        (45.0, "4"),
        (59.99, "4"),
        (60.0, "5"),
        (255.0, "5"),
        (1000.0, "5"),
        (math.nan, "dry"),
    ],
)
def test_depth_band_boundaries(cm: float, key: str) -> None:
    assert ramps.depth_band(cm).key == key
    assert ramps.depth_color_hex(cm) == HEX[key]
    assert ramps.depth_color_rgba(cm) == (*ramps.hex_to_rgb(HEX[key]), 255)


def test_hex_helpers() -> None:
    assert ramps.hex_to_rgb("#3B82F6") == (59, 130, 246)
    assert ramps.hex_to_rgb("3b82f6") == (59, 130, 246)
    assert ramps.hex_to_rgb("#FFF") == (255, 255, 255)
    assert ramps.rgb_to_hex((59, 130, 246)) == "#3B82F6"
    assert ramps.rgb_to_hex(ramps.hex_to_rgba("#B91C1C", 128)) == "#B91C1C"
    assert ramps.hex_to_rgba("#EF4444", 0)[3] == 0
    with pytest.raises(ValueError):
        ramps.hex_to_rgb("#12345")
    with pytest.raises(ValueError):
        ramps.hex_to_rgba("#EF4444", 300)


def test_palette_has_256_entries_with_transparent_dry() -> None:
    palette = ramps.depth_palette_png()
    assert len(palette) == 256
    for i in range(5):
        assert palette[i][3] == 0
    for i in range(5, 256):
        assert palette[i][3] == 255
    assert palette[5][:3] == ramps.hex_to_rgb(HEX["1"])
    assert palette[14][:3] == ramps.hex_to_rgb(HEX["1"])
    assert palette[15][:3] == ramps.hex_to_rgb(HEX["2"])
    assert palette[30][:3] == ramps.hex_to_rgb(HEX["3"])
    assert palette[45][:3] == ramps.hex_to_rgb(HEX["4"])
    assert palette[60][:3] == ramps.hex_to_rgb(HEX["5"])
    assert palette[255][:3] == ramps.hex_to_rgb(HEX["5"])
    rgb, alphas = ramps.depth_palette_bytes()
    assert len(rgb) == 768 and len(alphas) == 256
    assert alphas[:5] == b"\x00" * 5 and alphas[5] == 255


def test_palette_custom_alpha() -> None:
    palette = ramps.depth_palette_png(alpha=140, dry_alpha=20)
    assert palette[0][3] == 20 and palette[100][3] == 140


def test_depth_array_to_rgba_matches_scalar_ramp() -> None:
    np = pytest.importorskip("numpy")
    metres = np.array(
        [[0.0, 0.049, 0.05, 0.149], [0.15, 0.299, 0.30, 0.449], [0.45, 0.599, 0.60, 3.0]],
        dtype=np.float64,
    )
    rgba = ramps.depth_array_to_rgba(metres)
    assert rgba.shape == (3, 4, 4) and rgba.dtype == np.uint8
    for y in range(3):
        for x in range(4):
            cm = metres[y, x] * 100.0
            expected = ramps.depth_color_rgba(cm, 255 if cm >= 5 else 0)
            assert tuple(int(v) for v in rgba[y, x]) == expected, (y, x, cm)
    nan_field = np.full((2, 2), np.nan)
    assert ramps.depth_array_to_rgba(nan_field)[..., 3].max() == 0
    idx = ramps.depth_cm_to_palette_index(np.array([[0.0, 0.055, 9.0]]))
    assert idx.tolist() == [[0, 5, 255]]
    with pytest.raises(ValueError):
        ramps.depth_array_to_rgba(np.zeros(3))


@pytest.mark.parametrize(
    ("beta", "key"),
    [
        (-0.1, "0"),
        (0.0, "0"),
        (0.249, "0"),
        (0.25, "1"),
        (0.499, "1"),
        (0.5, "2"),
        (0.749, "2"),
        (0.75, "3"),
        (1.0, "3"),
        (1.5, "3"),
        (math.nan, "0"),
    ],
)
def test_drain_band_boundaries(beta: float, key: str) -> None:
    assert ramps.drain_band(beta).key == key
    assert ramps.drain_color_hex(beta) == DRAIN_HEX[key]
    assert ramps.drain_color_rgba(beta, 200) == (*ramps.hex_to_rgb(DRAIN_HEX[key]), 200)


def test_probability_opacity_floor_and_status() -> None:
    assert ramps.probability_opacity(0.0) == 0.15
    assert ramps.probability_opacity(0.5) == 0.5
    assert ramps.probability_opacity(1.7) == 1.0
    assert ramps.probability_opacity(math.nan) == 0.15
    assert ramps.status_color_hex("replay") == "#38BDF8"
    with pytest.raises(KeyError):
        ramps.status_color_hex("paused")


@pytest.mark.parametrize(
    ("mm_h", "key"),
    [
        (-1.0, None),
        (0.0, None),
        (0.49, None),
        (0.5, "1"),
        (1.99, "1"),
        (2.0, "2"),
        (7.99, "2"),
        (8.0, "3"),
        (19.99, "3"),
        (20.0, "4"),
        (39.99, "4"),
        (40.0, "5"),
        (79.99, "5"),
        (80.0, "6"),
        (250.0, "6"),
        (math.nan, None),
    ],
)
def test_rain_band_boundaries(mm_h: float, key: str | None) -> None:
    band = ramps.rain_band(mm_h)
    assert (None if band is None else band.key) == key
    assert ramps.rain_color_hex(mm_h) == (None if key is None else RAIN_HEX[key])
    if key is None:
        assert ramps.rain_color_rgba(mm_h) == (0, 0, 0, 0)
    else:
        assert ramps.rain_color_rgba(mm_h, 200) == (*ramps.hex_to_rgb(RAIN_HEX[key]), 200)


def test_rain_lut_matches_the_bands() -> None:
    bands = rain_bands()
    lut = ramps.rain_lut_rgba()
    assert len(lut) == len(bands) == 6
    for i, band in enumerate(bands):
        assert lut[i] == ramps.hex_to_rgba(band.hex)
        assert ramps.rgb_to_hex(lut[i]) == band.hex
        assert ramps.rain_band_index(band.min_mm_h) == i
    assert ramps.rain_lut_rgba(180)[0][3] == 180
    with pytest.raises(ValueError):
        ramps.rain_lut_rgba(300)


def test_rain_palette_bytes_leads_with_transparent_no_echo() -> None:
    rgb, alphas = ramps.rain_palette_bytes()
    assert len(rgb) == 21 and len(alphas) == 7
    assert alphas[0] == 0 and alphas[1:] == bytes([255] * 6)
    assert rgb[3:6] == bytes(ramps.hex_to_rgb(RAIN_HEX["1"]))
    assert rgb[18:21] == bytes(ramps.hex_to_rgb(RAIN_HEX["6"]))


def test_rain_array_to_rgba_matches_scalar_ramp() -> None:
    np = pytest.importorskip("numpy")
    mm_h = np.array(
        [[0.0, 0.49, 0.5, 1.99], [2.0, 7.99, 8.0, 19.99], [20.0, 40.0, 80.0, 250.0]],
        dtype=np.float64,
    )
    rgba = ramps.rain_array_to_rgba(mm_h)
    assert rgba.shape == (3, 4, 4) and rgba.dtype == np.uint8
    for y in range(3):
        for x in range(4):
            expected = ramps.rain_color_rgba(float(mm_h[y, x]))
            assert tuple(int(v) for v in rgba[y, x]) == expected, (y, x, mm_h[y, x])
    no_echo = np.full((2, 2), np.nan)
    assert ramps.rain_array_to_rgba(no_echo)[..., 3].max() == 0
    assert ramps.rain_array_to_rgba(np.full((1, 1), -3.0))[0, 0].tolist() == [0, 0, 0, 0]
    with pytest.raises(ValueError):
        ramps.rain_array_to_rgba(np.zeros(3))

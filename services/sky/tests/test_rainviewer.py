"""RainViewer's radar mosaic as Sky input: decoding, coverage, resampling, and a whole fetch."""

from __future__ import annotations

import io
from datetime import UTC, datetime

import numpy as np
import pytest
from PIL import Image
from varuna_sky import rainviewer as rv


def _png(rgba: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(rgba.astype(np.uint8), "RGBA").save(buffer, format="PNG")
    return buffer.getvalue()


def _colour(dbz: int) -> tuple[int, int, int, int]:
    code = next(c for c, d in rv.UNIVERSAL_BLUE.items() if d == dbz)
    value = int(code.lstrip("#"), 16)
    return (value >> 24) & 255, (value >> 16) & 255, (value >> 8) & 255, value & 255


def test_every_table_colour_decodes_to_its_own_dbz():
    rgba = np.zeros((1, len(rv.UNIVERSAL_BLUE), 4))
    for i, dbz in enumerate(rv.UNIVERSAL_BLUE.values()):
        rgba[0, i] = _colour(dbz)
    decoded = rv.decode_tile(_png(rgba))[0]
    assert decoded.tolist() == [float(d) for d in rv.UNIVERSAL_BLUE.values()]


def test_transparent_and_unknown_colours_are_missing_and_counted():
    rgba = np.zeros((1, 3, 4))
    rgba[0, 1] = _colour(35)
    rgba[0, 2] = (1, 2, 3, 255)  # not in the table
    tile = _png(rgba)
    decoded = rv.decode_tile(tile)[0]
    assert np.isnan(decoded[0]) and decoded[1] == 35.0 and np.isnan(decoded[2])
    assert rv.decode_stats(tile) == (2, 1)


def test_coverage_is_where_the_layer_is_transparent():
    rgba = np.zeros((1, 2, 4))
    rgba[0, 1] = (0, 0, 0, 255)
    assert rv.covered_tile(_png(rgba)).tolist() == [[True, False]]


def test_mercator_pixel_matches_the_tile_mumbai_sits_in():
    x, y = rv.mercator_px(np.array([72.86]), np.array([19.065]))
    assert (int(x[0] // 256), int(y[0] // 256)) == (89, 57)


def test_bilinear_interpolates_and_keeps_missing_missing():
    field = np.array([[0.0, 10.0], [np.nan, 10.0]])
    assert rv.bilinear(field, np.array([1.0]), np.array([0.5]))[0] == pytest.approx(5.0)
    assert np.isnan(rv.bilinear(field, np.array([1.0]), np.array([1.5]))[0])


def test_a_fetch_builds_frames_with_dry_floor_and_missing_outside_coverage():
    rain = np.zeros((256, 256, 4))
    rain[100:140, 100:140] = _colour(40)
    coverage = np.zeros((256, 256, 4))
    coverage[:, 200:] = (0, 0, 0, 255)  # the eastern strip has no radar
    index = {
        "host": "https://tiles.example",
        "radar": {
            "past": [
                {
                    "time": int(datetime(2026, 9, 30, 10, m, tzinfo=UTC).timestamp()),
                    "path": f"/r{m}",
                }
                for m in (0, 10, 20)
            ]
        },
    }
    asked: list[str] = []

    def get_bytes(url: str, **_: object) -> bytes:
        asked.append(url)
        return _png(coverage if "/coverage/" in url else rain)

    # Cell centres over the tile Mumbai sits in, at pixel columns 120 (rain), 60 (dry) and 230
    # (outside coverage) of tile (89, 57).
    px = np.array([[89 * 256 + 120.5, 89 * 256 + 60.5, 89 * 256 + 230.5]])
    py = np.full_like(px, 57 * 256 + 120.5)
    n = 256 * 2**rv.ZOOM
    lon = px / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * py / n))))
    radar = rv.fetch_radar(
        lon, lat, n_frames=3, get_json=lambda *a, **k: index, get_bytes=get_bytes
    )
    assert radar is not None
    assert radar.dbz.shape == (3, 1, 3)
    assert radar.dbz[-1, 0, 0] == pytest.approx(40.0)
    assert radar.dbz[-1, 0, 1] == rv.NO_ECHO_DBZ
    assert np.isnan(radar.dbz[-1, 0, 2])
    assert [t.minute for t in radar.times] == [0, 10, 20]
    assert any("rainviewer.com" in note for note in radar.notes)
    assert all(url.startswith("https://tiles.example") for url in asked)


def test_too_few_frames_returns_none_so_the_cycle_falls_back():
    index = {"host": "https://tiles.example", "radar": {"past": []}}
    assert (
        rv.fetch_radar(
            np.zeros((1, 1)),
            np.zeros((1, 1)),
            get_json=lambda *a, **k: index,
            get_bytes=lambda *a, **k: b"",
        )
        is None
    )

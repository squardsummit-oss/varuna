"""Bundle cubes rendered as preview images for the storm designer (SPEC.md 7.8, P2.10)."""

from __future__ import annotations

import io
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from varuna_replay import bundle as members
from varuna_replay.build import RADAR_CADENCE_MIN
from varuna_replay.domain import StormDomain, step_times_min
from varuna_replay.preview import accumulation_png, radar_frame_png, radar_index
from varuna_schemas.models.bundle import BundleManifest
from varuna_schemas.models.city import RadarDomain

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

MUMBAI_RADAR_DOMAIN = RadarDomain(center_lon=72.86, center_lat=19.065, size_km=60.0, res_m=500.0)
"""The real 60 km domain: 120 x 120 px at 500 m, what the demo bundle carries."""


def _rgba(png: bytes) -> np.ndarray:
    """Decode PNG bytes back to an ``[H, W, 4]`` uint8 array."""
    with Image.open(io.BytesIO(png)) as image:
        return np.asarray(image.convert("RGBA"), dtype=np.uint8)


def test_rendering_the_same_frame_twice_is_byte_identical(full_bundle: Path) -> None:
    """Rule 8: two renders of one cube produce the same bytes, so a bake stays reproducible."""
    layout = members.BundleLayout(root=full_bundle)
    first = radar_frame_png(layout, 0)
    second = radar_frame_png(layout, 0)
    assert first.startswith(PNG_MAGIC), "the renderer must return a PNG, not raw pixels"
    assert first == second
    assert accumulation_png(layout) == accumulation_png(layout)


def test_every_frame_renders_at_the_size_of_the_cube(full_bundle: Path) -> None:
    layout = members.BundleLayout(root=full_bundle)
    info = members.read_cube_info(layout.radar, members.RADAR_VARIABLE)
    for index in range(info.n_times):
        pixels = _rgba(radar_frame_png(layout, index))
        assert pixels.shape == (info.shape[1], info.shape[2], 4)


def test_a_frame_with_no_echo_renders_fully_transparent(
    tmp_path: Path, domain: StormDomain, t0: datetime
) -> None:
    """A radar pixel below the lowest class is no echo, not a colour: it must not paint."""
    layout = members.BundleLayout(root=tmp_path / "MUM-NOECHO")
    times = step_times_min(0.0, 20.0, RADAR_CADENCE_MIN)
    members.write_cube(
        layout.radar,
        np.full((times.size, *domain.shape), np.nan, dtype=np.float32),
        variable=members.RADAR_VARIABLE,
        times_min=times,
        domain=domain,
        t0=t0,
        step_min=RADAR_CADENCE_MIN,
        units="dBZ",
    )
    pixels = _rgba(radar_frame_png(layout, 0))
    assert pixels[..., 3].max() == 0, "no echo must render fully transparent"


def test_the_area_of_interest_rectangle_lies_inside_the_grid(
    tmp_path: Path, make_bundle: Callable[..., BundleManifest]
) -> None:
    """The rectangle comes from the cube's own transform, so it must fit the cube's 120 px."""
    domain = StormDomain.from_radar_domain(MUMBAI_RADAR_DOMAIN, 32643)
    assert domain.n_px == 120
    root = tmp_path / "MUM-AOI-2019"
    manifest = make_bundle(root, domain, bundle_id="MUM-AOI-2019")
    index = radar_index(manifest, members.BundleLayout(root=root))

    assert (index["width"], index["height"]) == (120, 120)
    aoi = index["aoi_px"]
    assert 0 <= aoi["left"] < aoi["right"] <= 120
    assert 0 <= aoi["top"] < aoi["bottom"] <= 120
    assert aoi["width"] > 0 and aoi["height"] > 0
    assert aoi["height"] > aoi["width"], "MUM-CENTRAL is taller than it is wide (SPEC.md 3.3)"


def test_the_index_counts_the_frames_the_cube_holds(full_bundle: Path) -> None:
    layout = members.BundleLayout(root=full_bundle)
    manifest = members.load_manifest(full_bundle)
    info = members.read_cube_info(layout.radar, members.RADAR_VARIABLE)
    index = radar_index(manifest, layout)

    assert index["n_frames"] == info.shape[0]
    assert len(index["frames"]) == info.shape[0]
    assert index["step_min"] == pytest.approx(info.step_min)
    assert index["bundle"] == manifest.id


def test_every_frame_row_carries_its_indian_standard_time_stamp(full_bundle: Path) -> None:
    layout = members.BundleLayout(root=full_bundle)
    manifest = members.load_manifest(full_bundle)
    frames = radar_index(manifest, layout)["frames"]

    assert [row["index"] for row in frames] == list(range(len(frames)))
    stamps = [datetime.fromisoformat(row["ts"]) for row in frames]
    assert stamps[0] == manifest.t0
    assert stamps[1] - stamps[0] == timedelta(minutes=RADAR_CADENCE_MIN)
    assert all(stamp.utcoffset() == timedelta(hours=5, minutes=30) for stamp in stamps)


def test_the_legend_bands_come_from_the_shared_rain_ramp(full_bundle: Path) -> None:
    """No hex is written in the renderer: the bands are the tokens (SPEC.md 6.2, 6.7)."""
    layout = members.BundleLayout(root=full_bundle)
    bands = radar_index(members.load_manifest(full_bundle), layout)["bands"]

    assert [band["min_mm_h"] for band in bands] == [0.5, 2.0, 8.0, 20.0, 40.0, 80.0]
    assert all(band["hex"].startswith("#") and band["label"] for band in bands)


def test_a_frame_outside_the_cube_says_how_many_frames_there_are(full_bundle: Path) -> None:
    layout = members.BundleLayout(root=full_bundle)
    n_frames = members.read_cube_info(layout.radar, members.RADAR_VARIABLE).n_times
    with pytest.raises(IndexError, match=f"{n_frames} radar frames"):
        radar_frame_png(layout, n_frames)


def test_the_accumulation_paints_where_the_storm_rained(full_bundle: Path) -> None:
    """It integrates the mm/h truth cube, so wet pixels are opaque and dry ones are not."""
    layout = members.BundleLayout(root=full_bundle)
    pixels = _rgba(accumulation_png(layout))
    info = members.read_cube_info(layout.truth, members.TRUTH_VARIABLE)

    assert pixels.shape == (info.shape[1], info.shape[2], 4)
    assert pixels[..., 3].max() == 255, "the demo storm accumulates above the ramp floor"

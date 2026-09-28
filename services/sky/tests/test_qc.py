"""Radar quality control (SPEC.md 11.1 step 1, ``varuna_sky.qc``).

The fields here are built in the test, not loaded from a bundle, so each property the spec
names is checked against a scene whose answer is known by construction: a clean moving storm
(no clutter, full coverage), a coverage disc with a dry hole inside it, one pixel held
constant while the storm moves past, and a single core above 50 dBZ east of the radar.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest
from varuna_schemas.constants import IST
from varuna_sky.qc import (
    ATTENUATION_DBZ,
    CLUTTER_MIN_DBZ,
    attenuation_flag,
    clutter_mask,
    coverage_mask,
    run_qc,
)
from varuna_sky.types import RadarFrames, RadarGrid

N_PX = 80
RES_M = 500.0
T0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)


def _grid(n_px: int = N_PX, res_m: float = RES_M) -> RadarGrid:
    """A small square domain, north-up, in the Mumbai CRS."""
    extent = n_px * res_m
    return RadarGrid(
        crs="EPSG:32643",
        res_m=res_m,
        n_px=n_px,
        transform=(res_m, 0.0, 0.0, 0.0, -res_m, extent),
    )


def _radius_px(n_px: int = N_PX) -> np.ndarray:
    """Distance of every pixel centre from the domain centre, in pixels."""
    offsets = np.arange(n_px, dtype=np.float64) + 0.5 - n_px / 2.0
    return np.hypot(offsets[None, :], offsets[:, None])


def _moving_storm(
    n_frames: int = 6,
    n_px: int = N_PX,
    background: float = 20.0,
    peak: float = 25.0,
    sigma_px: float = 5.0,
    speed_px: float = 4.0,
) -> np.ndarray:
    """A Gaussian cell crossing a uniform background; finite everywhere, nothing above 45 dBZ."""
    rows, cols = np.mgrid[0:n_px, 0:n_px].astype(np.float64)
    frames = []
    for k in range(n_frames):
        centre = 20.0 + speed_px * k
        d2 = (rows - centre) ** 2 + (cols - centre) ** 2
        frames.append(background + peak * np.exp(-d2 / (2.0 * sigma_px**2)))
    return np.stack(frames)


def _frames(dbz: np.ndarray, n_px: int = N_PX) -> RadarFrames:
    times = tuple(T0 - timedelta(minutes=10) * (dbz.shape[0] - 1 - k) for k in range(dbz.shape[0]))
    return RadarFrames(dbz=dbz, times=times, grid=_grid(n_px))


# ============================================================================ coverage
def test_clean_field_is_fully_covered_with_no_clutter() -> None:
    """A finite, moving field: the whole domain is usable and nothing is ground clutter."""
    result = run_qc(_frames(_moving_storm()))

    assert result.coverage.all()
    assert result.coverage_fraction == pytest.approx(1.0)
    assert not result.clutter.any()
    assert result.clutter_fraction == pytest.approx(0.0)
    assert result.attenuation_flag == "none"
    assert not np.isnan(result.dbz).any()


def test_coverage_recovers_the_disc_including_its_dry_pixels() -> None:
    """Coverage is the radar's disc, not the wet part of it.

    The frames are ``nan`` outside a 30-pixel range ring *and* over a dry block inside it -
    which is how a decoded frame looks (``varuna_replay.storm.radar_dbz``). The dry block
    must still come back as covered.
    """
    radius = _radius_px()
    inside = radius <= 30.0
    field = np.where(inside[None, :, :], _moving_storm(), np.nan)
    field[:, 44:50, 44:50] = np.nan  # a dry block well inside the ring

    coverage = coverage_mask(field, _grid())

    assert not coverage[radius > 30.0].any(), "coverage must not claim pixels beyond the ring"
    assert coverage[radius <= 29.0].all(), "the disc inside the ring must be covered"
    assert coverage[44:50, 44:50].all(), "a dry pixel inside the ring is not a gap in coverage"
    assert coverage.mean() == pytest.approx(np.pi * 30.0**2 / N_PX**2, abs=0.02)


def test_a_domain_with_no_echo_at_all_has_no_coverage() -> None:
    field = np.full((3, N_PX, N_PX), np.nan)

    assert not coverage_mask(field, _grid()).any()


# ============================================================================ clutter
def test_a_constant_pixel_is_clutter_and_the_moving_storm_is_not() -> None:
    """Ground clutter is the echo that never changes; a cell crossing a pixel does change it."""
    field = _moving_storm()
    field[:, 10, 70] = CLUTTER_MIN_DBZ + 10.0  # a persistent, unmoving return

    result = run_qc(_frames(field))

    assert result.clutter[10, 70]
    assert np.array_equal(np.argwhere(result.clutter), np.array([[10, 70]]))
    assert not result.clutter[40, 40], "the pixel the storm crossed is not clutter"


def test_a_persistent_but_weak_echo_is_not_clutter() -> None:
    """A flat stratiform background sits in one 5 dBZ class frame after frame; it is rain."""
    field = np.full((6, N_PX, N_PX), CLUTTER_MIN_DBZ - 10.0)

    assert not clutter_mask(field).any()


def test_a_single_frame_flags_no_clutter() -> None:
    """With one frame there is no variance to measure, so nothing is claimed."""
    field = np.full((1, N_PX, N_PX), CLUTTER_MIN_DBZ + 10.0)

    assert not clutter_mask(field).any()


def test_a_pixel_that_comes_and_goes_is_not_clutter() -> None:
    """Clutter is persistent: an echo missing from one recent frame does not qualify."""
    field = np.full((6, N_PX, N_PX), CLUTTER_MIN_DBZ + 10.0)
    field[2, 30, 30] = np.nan

    assert not clutter_mask(field)[30, 30]


# ============================================================================ attenuation
def test_shadow_falls_strictly_behind_a_core_above_50_dbz() -> None:
    """The sector beyond a >50 dBZ core, away from the radar, is flagged - and only that."""
    field = np.full((6, N_PX, N_PX), 20.0)
    # A core east and slightly north of the radar. Its value drifts frame to frame so it is
    # a growing convective cell rather than a ground return, which QC would strip first.
    field[:, 34:37, 55:58] = ATTENUATION_DBZ + 5.0 + np.arange(6.0)[:, None, None]

    result = run_qc(_frames(field))

    radius = _radius_px()
    core_min_radius = radius[34:37, 55:58].min()
    shadow = result.attenuation

    assert shadow.any(), "a core above 50 dBZ must cast a shadow"
    assert (radius[shadow] > core_min_radius).all(), "the shadow starts beyond the core"
    assert not shadow[34:37, 55:58].any(), "the core is the cause, not the victim"

    # Every shadowed pixel is on the core's side of the radar, i.e. the far side of it.
    offsets = np.arange(N_PX, dtype=np.float64) + 0.5 - N_PX / 2.0
    d_col = np.broadcast_to(offsets[None, :], shadow.shape)
    d_row = np.broadcast_to(offsets[:, None], shadow.shape)
    towards_core = d_col * (56.5 - 40.0) + d_row * (35.5 - 40.0)
    assert (towards_core[shadow] > 0.0).all()
    assert not shadow[:, :40].any(), "nothing west of the radar is behind an eastern core"

    # A pixel on the same radial but nearer than the core keeps its data.
    assert not shadow[37, 48]


def test_no_core_means_no_shadow_and_no_flag() -> None:
    result = run_qc(_frames(_moving_storm()))

    assert not result.attenuation.any()
    assert result.attenuation_flag == "none"


def test_attenuation_flag_reads_the_shadowed_fraction() -> None:
    """The three-way label is a fraction of the covered domain (thresholds are our choice)."""
    coverage = np.ones((10, 10), dtype=bool)
    shadow = np.zeros((10, 10), dtype=bool)

    assert attenuation_flag(shadow, coverage) == "none"

    shadow.flat[:5] = True  # 5 %
    assert attenuation_flag(shadow, coverage) == "partial"

    shadow.flat[:40] = True  # 40 %
    assert attenuation_flag(shadow, coverage) == "severe"

    assert attenuation_flag(shadow, np.zeros((10, 10), dtype=bool)) == "none"


# ============================================================================ masked frame
def test_masked_frame_is_nan_exactly_where_clutter_or_no_coverage() -> None:
    """``QCResult.dbz`` is the newest frame minus what QC rejected - and nothing else."""
    radius = _radius_px()
    field = np.where((radius <= 30.0)[None, :, :], _moving_storm(), np.nan)
    field[:, 30, 45] = CLUTTER_MIN_DBZ + 10.0  # well inside the ring, so it is real clutter

    result = run_qc(_frames(field))

    rejected = ~result.coverage | result.clutter
    assert np.array_equal(np.isnan(result.dbz), rejected)
    assert result.clutter[30, 45]
    kept = ~rejected
    assert np.allclose(result.dbz[kept], field[-1][kept], equal_nan=False)


def test_shadowed_pixels_keep_their_values() -> None:
    """Attenuation is a flag: the prototype marks the shadow, it does not correct it."""
    field = np.full((6, N_PX, N_PX), 20.0)
    field[:, 34:37, 55:58] = ATTENUATION_DBZ + 5.0 + np.arange(6.0)[:, None, None]

    result = run_qc(_frames(field))

    assert result.attenuation.any()
    assert np.isfinite(result.dbz[result.attenuation]).all()
    assert np.allclose(result.dbz[result.attenuation], 20.0)


# ============================================================================ contract
def test_run_qc_is_deterministic() -> None:
    frames = _frames(_moving_storm())

    first = run_qc(frames)
    second = run_qc(frames)

    assert np.array_equal(first.coverage, second.coverage)
    assert np.array_equal(first.clutter, second.clutter)
    assert np.array_equal(first.attenuation, second.attenuation)
    assert np.array_equal(first.dbz, second.dbz, equal_nan=True)


def test_frames_must_match_their_grid() -> None:
    frames = RadarFrames(
        dbz=np.zeros((3, 10, 10)),
        times=(T0, T0, T0),
        grid=_grid(),
    )

    with pytest.raises(ValueError, match="do not match the grid"):
        run_qc(frames)

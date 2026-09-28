"""The storm designer (task P2.2, SPEC.md 10.2, Appendix A).

What these tests pin down: the same seed gives the same storm and the same bytes; the cells
travel in the wind direction; a dry storm stays dry; the calibration reports the accumulation
the cube actually holds; and the Marshall-Palmer round trip loses only the quantisation.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from varuna_replay import bundle as members
from varuna_replay.domain import StormDomain, step_times_min
from varuna_replay.storm import (
    BACKGROUND_RANGE_MM_H,
    MP_A,
    MP_B,
    PEAK_RANGE_MM_H,
    QUANTISATION_RATIO,
    SIGMA_RANGE_M,
    CalibrationError,
    RadarRender,
    accumulation_mm,
    aoi_mean_accumulation,
    calibrate,
    cell_center,
    envelope,
    radar_dbz,
    rain_field,
    rain_from_dbz,
    random_storm,
    wind_vector,
)
from varuna_schemas.models.common import BBox

WINDOW_MIN = 120.0


# --------------------------------------------------------------------------- determinism
def test_the_same_seed_gives_the_same_storm(domain: StormDomain) -> None:
    first = random_storm(domain, seed=2019, window_min=WINDOW_MIN)
    second = random_storm(domain, seed=2019, window_min=WINDOW_MIN)
    assert first == second
    assert first != random_storm(domain, seed=2020, window_min=WINDOW_MIN)


def test_the_same_seed_gives_the_same_field_and_the_same_frames(domain: StormDomain) -> None:
    design = random_storm(domain, seed=2019, window_min=WINDOW_MIN)
    times = step_times_min(0.0, WINDOW_MIN, 5.0)
    assert np.array_equal(rain_field(design, domain, times), rain_field(design, domain, times))
    render = RadarRender.from_design(design)
    first = radar_dbz(rain_field(design, domain, times), domain, render)
    second = radar_dbz(rain_field(design, domain, times), domain, render)
    assert np.array_equal(first, second, equal_nan=True)


def test_two_writes_of_the_same_cube_are_byte_identical(
    domain: StormDomain, tmp_path: Path, t0: datetime, digest: Callable[[Path], str]
) -> None:
    """Rule 8: two runs of the generator on the same inputs produce identical products."""
    design = random_storm(domain, seed=2019, window_min=WINDOW_MIN)
    times = step_times_min(0.0, WINDOW_MIN, 5.0)
    field = rain_field(design, domain, times)
    for name in ("a", "b"):
        members.write_cube(
            tmp_path / name / "rain.zarr",
            field,
            variable=members.TRUTH_VARIABLE,
            times_min=times,
            domain=domain,
            t0=t0,
            step_min=5.0,
            units="mm/h",
        )
    assert digest(tmp_path / "a") == digest(tmp_path / "b")


def test_generated_cells_stay_inside_the_ranges_the_spec_states(domain: StormDomain) -> None:
    design = random_storm(domain, seed=2019, window_min=WINDOW_MIN, n_cells=12)
    assert BACKGROUND_RANGE_MM_H[0] <= design.background_mm_h <= BACKGROUND_RANGE_MM_H[1]
    for cell in design.cells:
        assert SIGMA_RANGE_M[0] <= cell.sigma_m <= SIGMA_RANGE_M[1]
        assert PEAK_RANGE_MM_H[0] <= cell.peak_mm_h <= PEAK_RANGE_MM_H[1]
        assert 0.0 <= cell.peak_min <= WINDOW_MIN


# --------------------------------------------------------------------------- kinematics
def test_wind_from_the_south_west_blows_towards_the_north_east() -> None:
    u, v = wind_vector(225.0, 8.0)
    assert u == pytest.approx(8.0 / math.sqrt(2), rel=1e-6)
    assert v == pytest.approx(8.0 / math.sqrt(2), rel=1e-6)
    assert math.hypot(u, v) == pytest.approx(8.0)
    # wind from the north blows southward
    u_n, v_n = wind_vector(0.0, 5.0)
    assert u_n == pytest.approx(0.0, abs=1e-9)
    assert v_n == pytest.approx(-5.0)


def test_the_envelope_grows_and_decays_as_a_sine(domain: StormDomain) -> None:
    cell = random_storm(domain, seed=7, window_min=WINDOW_MIN, n_cells=1).cells[0]
    assert envelope(cell, cell.birth_min - 1.0) == 0.0
    assert envelope(cell, cell.birth_min) == pytest.approx(0.0, abs=1e-12)
    assert envelope(cell, cell.peak_min) == pytest.approx(1.0)
    assert envelope(cell, cell.birth_min + cell.lifetime_min) == pytest.approx(0.0, abs=1e-12)
    assert envelope(cell, cell.birth_min + cell.lifetime_min + 1.0) == 0.0
    quarter = cell.birth_min + cell.lifetime_min / 4
    assert 0.0 < envelope(cell, quarter) < 1.0


def test_a_cell_advects_the_field_in_the_wind_direction(
    domain: StormDomain, centroid: Callable[..., tuple[float, float]]
) -> None:
    """The rain centroid must move north-east at the wind speed (SPEC.md 10.2)."""
    design = random_storm(domain, seed=11, window_min=WINDOW_MIN, n_cells=1)
    design = design.model_copy(update={"background_mm_h": 0.0})
    cell = design.cells[0]
    early, late = cell.peak_min - 10.0, cell.peak_min + 10.0
    field = rain_field(design, domain, np.array([early, late]))

    x0, y0 = centroid(field[0], domain)
    x1, y1 = centroid(field[1], domain)
    assert x1 > x0 and y1 > y0, "a south-west wind must carry the cell north-east"

    expected_dx = cell.u_ms * (late - early) * 60.0
    expected_dy = cell.v_ms * (late - early) * 60.0
    assert x1 - x0 == pytest.approx(expected_dx, rel=0.05)
    assert y1 - y0 == pytest.approx(expected_dy, rel=0.05)
    # and the analytic centre agrees with the field's centroid
    cx, cy = cell_center(cell, late)
    assert x1 == pytest.approx(cx, abs=domain.res_m)
    assert y1 == pytest.approx(cy, abs=domain.res_m)


# --------------------------------------------------------------------------- dry storm
def test_a_dry_storm_gives_a_dry_cube_and_no_echo(domain: StormDomain) -> None:
    design = random_storm(domain, seed=2019, window_min=WINDOW_MIN, n_cells=0)
    design = design.model_copy(update={"background_mm_h": 0.0})
    times = step_times_min(0.0, WINDOW_MIN, 5.0)
    rain = rain_field(design, domain, times)
    assert rain.shape == (times.size, *domain.shape)
    assert not rain.any()

    frames = radar_dbz(rain, domain, RadarRender.from_design(design))
    assert np.isnan(frames).all(), "no rain must read as no echo, not as 0 dBZ"
    assert not rain_from_dbz(frames).any()


# --------------------------------------------------------------------------- radar
def test_marshall_palmer_round_trip_loses_only_the_quantisation(domain: StormDomain) -> None:
    """R -> Z -> 5 dBZ classes -> R recovers the rate, low by at most one class."""
    design = random_storm(domain, seed=2019, window_min=WINDOW_MIN, n_cells=4)
    design = design.model_copy(update={"speckle_sigma": 0.0})
    times = step_times_min(0.0, WINDOW_MIN, 10.0)
    rain = rain_field(design, domain, times)
    frames = radar_dbz(rain, domain, RadarRender.from_design(design))
    recovered = rain_from_dbz(frames)

    inside = domain.coverage_mask(design.coverage_radius_km)[None, :, :]
    echo = np.isfinite(frames) & inside
    assert echo.any(), "the storm must produce some echo to test the round trip"
    truth = rain[echo].astype(np.float64)
    back = recovered[echo].astype(np.float64)
    assert (back <= truth * (1 + 1e-6)).all(), "quantisation rounds a class down, never up"
    assert (back >= truth / QUANTISATION_RATIO * (1 - 1e-6)).all()
    assert QUANTISATION_RATIO == pytest.approx(10.0 ** (5.0 / 16.0))


def test_frames_are_quantised_to_five_dbz_classes_inside_the_coverage_circle(
    domain: StormDomain,
) -> None:
    design = random_storm(domain, seed=3, window_min=WINDOW_MIN, n_cells=3)
    times = step_times_min(0.0, WINDOW_MIN, 10.0)
    frames = radar_dbz(rain_field(design, domain, times), domain, RadarRender.from_design(design))
    values = frames[np.isfinite(frames)]
    assert values.size
    assert np.allclose(np.mod(values, design.dbz_class_width), 0.0)
    assert values.min() >= design.min_dbz
    outside = ~domain.coverage_mask(design.coverage_radius_km)
    assert np.isnan(frames[:, outside]).all(), "outside the coverage circle there is no echo"


def test_reflectivity_follows_z_equals_200_r_to_the_1_6(domain: StormDomain) -> None:
    design = random_storm(domain, seed=5, window_min=WINDOW_MIN, n_cells=1)
    design = design.model_copy(
        update={"speckle_sigma": 0.0, "dbz_class_width": 1e-9, "min_dbz": -100.0}
    )
    rain = np.full((1, *domain.shape), 30.0, dtype=np.float32)
    frames = radar_dbz(rain, domain, RadarRender.from_design(design))
    expected = 10.0 * math.log10(MP_A * 30.0**MP_B)
    inside = domain.coverage_mask(design.coverage_radius_km)
    assert frames[0][inside] == pytest.approx(expected, abs=1e-3)


# --------------------------------------------------------------------------- accumulation
def test_accumulation_integrates_rate_over_time() -> None:
    rain = np.full((13, 2, 2), 12.0, dtype=np.float32)  # 12 mm/h for one hour
    assert accumulation_mm(rain, 5.0) == pytest.approx(12.0)
    assert accumulation_mm(rain, 5.0, rule="left") == pytest.approx(12.0)
    with pytest.raises(ValueError, match="trapezoid"):
        accumulation_mm(rain, 5.0, rule="simpson")


# --------------------------------------------------------------------------- calibration
def test_calibrate_reports_the_accumulation_the_written_cube_holds(
    domain: StormDomain, tmp_path: Path, t0: datetime, aoi: BBox
) -> None:
    """The number the manifest will quote must be the number in truth/rain.zarr."""
    design = random_storm(domain, seed=2019, window_min=WINDOW_MIN, n_cells=6)
    mask = domain.aoi_mask(aoi)
    result = calibrate(
        design,
        domain,
        mask=mask,
        target_mm=95.0,
        t0_min=0.0,
        t1_min=WINDOW_MIN,
        step_min=5.0,
        window_label="05:40-07:40 IST",
    )
    assert result.achieved_mm == pytest.approx(95.0, rel=1e-6)
    assert result.within_tolerance
    assert result.intensity_scale > 0
    assert "95.0 mm" in result.summary()

    times = step_times_min(0.0, WINDOW_MIN, 5.0)
    path = members.write_cube(
        tmp_path / "rain.zarr",
        rain_field(result.design, domain, times),
        variable=members.TRUTH_VARIABLE,
        times_min=times,
        domain=domain,
        t0=t0,
        step_min=5.0,
        units="mm/h",
    )
    from_disk = aoi_mean_accumulation(members.read_cube(path, members.TRUTH_VARIABLE), mask, 5.0)
    assert from_disk == pytest.approx(result.achieved_mm, rel=1e-6)


def test_calibration_is_reported_not_hidden_when_the_peaks_leave_the_stated_range(
    domain: StormDomain, aoi: BBox
) -> None:
    """Rule 6: a scale that pushes cells outside 40-120 mm/h is said out loud, not clamped."""
    design = random_storm(domain, seed=2019, window_min=WINDOW_MIN, n_cells=6)
    result = calibrate(
        design,
        domain,
        mask=domain.aoi_mask(aoi),
        target_mm=900.0,
        t0_min=0.0,
        t1_min=WINDOW_MIN,
        step_min=5.0,
        window_label="a cloudburst",
    )
    assert result.peaks_outside_design_range
    assert any("40-120 mm/h" in note for note in result.notes)
    assert max(cell.peak_mm_h for cell in result.design.cells) * result.intensity_scale > 120


def test_calibrate_refuses_an_impossible_target(domain: StormDomain, aoi: BBox) -> None:
    design = random_storm(domain, seed=2019, window_min=WINDOW_MIN, n_cells=4)
    with pytest.raises(CalibrationError, match="background"):
        calibrate(
            design,
            domain,
            mask=domain.aoi_mask(aoi),
            target_mm=0.5,
            t0_min=0.0,
            t1_min=WINDOW_MIN,
            step_min=5.0,
            window_label="too small a target",
        )
    dry = design.model_copy(update={"cells": [], "background_mm_h": 0.0})
    with pytest.raises(CalibrationError, match="no rain"):
        calibrate(
            dry,
            domain,
            mask=domain.aoi_mask(aoi),
            target_mm=50.0,
            t0_min=0.0,
            t1_min=WINDOW_MIN,
            step_min=5.0,
            window_label="a storm with no cells",
        )

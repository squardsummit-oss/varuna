"""Optical flow: does it measure the storm's displacement, and does it stay quiet when asked
to measure nothing at all (SPEC.md 11.1 step 4, P3.7)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from varuna_schemas.constants import IST
from varuna_sky.motion import (
    DBR_ZEROVALUE,
    RAIN_FLOOR_MM_H,
    from_dbr,
    optical_flow,
    optical_flow_from_rain,
    rain_from_dbz,
    rain_history,
    to_dbr,
)
from varuna_sky.types import RadarFrames, RadarGrid, SkyInputs, ZRParams

MP = ZRParams(a=200.0, b=1.6, source="marshall_palmer", n_pairs=0)
"""Marshall-Palmer, the relation the replay bundles were rendered with (storm.py MP_A/MP_B)."""

N_PX = 64
"""A 64 x 64 domain keeps every test in this file well under a second; the production
120 x 120 Sky grid is exercised by the integrator, not here."""


def grid(n_px: int = N_PX, res_m: float = 500.0) -> RadarGrid:
    return RadarGrid(
        crs="EPSG:32643",
        res_m=res_m,
        n_px=n_px,
        transform=(res_m, 0.0, 300_000.0, 0.0, -res_m, 2_140_000.0),
    )


def translating_rain(
    n_frames: int = 3,
    du: float = 3.0,
    dv: float = 1.0,
    n_px: int = N_PX,
) -> np.ndarray:
    """A Gaussian cell of 30 mm/h moving ``(du, dv)`` pixels east and south per frame."""
    rows, cols = np.mgrid[0:n_px, 0:n_px]
    frames = []
    for k in range(n_frames):
        r2 = (cols - (20.0 + du * k)) ** 2 + (rows - (30.0 + dv * k)) ** 2
        frames.append(30.0 * np.exp(-r2 / (2 * 6.0**2)) + 1.0)
    return np.stack(frames)


def frames_from_rain(rain: np.ndarray, n_px: int = N_PX) -> RadarFrames:
    """Render rain back to dBZ through Marshall-Palmer, as the bundles do."""
    dbz = 10.0 * np.log10(MP.a * np.power(np.maximum(rain, 1e-6), MP.b))
    t0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
    times = tuple(t0 + timedelta(minutes=10 * k) for k in range(rain.shape[0]))
    return RadarFrames(dbz=dbz, times=times, grid=grid(n_px))


def inputs_for(frames: RadarFrames, **kwargs: object) -> SkyInputs:
    defaults: dict[str, object] = {
        "gauges": pd.DataFrame(columns=["ts", "station_id", "lon", "lat", "mm_5min"]),
        "cycle_ts": frames.latest_ts,
        "n_members": 6,
        "n_steps": 6,
    }
    defaults.update(kwargs)
    return SkyInputs(frames=frames, **defaults)  # type: ignore[arg-type]


# ============================================================================ transforms
def test_dbr_round_trip_keeps_wet_rain_and_floors_the_dry() -> None:
    rain = np.array([[0.0, RAIN_FLOOR_MM_H, 1.0], [5.0, 40.0, np.nan]])
    back = from_dbr(to_dbr(rain))
    assert back[0, 0] == 0.0
    assert back[1, 2] == 0.0, "missing pixels come back dry, never as a sentinel"
    assert np.allclose(back[0, 1:], [RAIN_FLOOR_MM_H, 1.0])
    assert np.allclose(back[1, :2], [5.0, 40.0])
    assert to_dbr(np.zeros((2, 2)))[0, 0] == DBR_ZEROVALUE


def test_rain_from_dbz_inverts_marshall_palmer() -> None:
    rain = np.array([[0.5, 5.0, 50.0]])
    dbz = 10.0 * np.log10(MP.a * rain**MP.b)
    assert np.allclose(rain_from_dbz(dbz, MP), rain)


def test_rain_history_uses_the_merged_analysis_for_the_newest_frame_only() -> None:
    frames = frames_from_rain(translating_rain())
    merged = np.full(frames.grid.shape, 7.0)
    history = rain_history(frames, MP, merged)
    assert history.shape == (3, N_PX, N_PX)
    assert np.allclose(history[-1], 7.0)
    assert not np.allclose(history[-2], 7.0), "the merge adjusts the analysis, not the past"


def test_rain_history_pads_a_short_history_by_repeating_its_oldest_frame() -> None:
    frames = frames_from_rain(translating_rain(n_frames=2))
    history = rain_history(frames, MP, n_frames=3)
    assert history.shape == (3, N_PX, N_PX)
    assert np.allclose(history[0], history[1])


# ============================================================================ flow
@pytest.mark.parametrize(("du", "dv"), [(3.0, 1.0), (-2.0, -2.0)])
def test_lucas_kanade_recovers_a_uniform_translation(du: float, dv: float) -> None:
    """A cell moving (du, dv) pixels per frame must come back as (u, v) of that sign and size."""
    field = optical_flow_from_rain(translating_rain(du=du, dv=dv), timedelta(minutes=10), 500.0)
    assert field.method == "lucas_kanade"
    assert field.u.shape == (N_PX, N_PX)
    assert field.u.mean() == pytest.approx(du, abs=0.5)
    assert field.v.mean() == pytest.approx(dv, abs=0.5)


def test_speed_is_reported_in_metres_per_second() -> None:
    field = optical_flow_from_rain(translating_rain(du=3.0, dv=0.0), timedelta(minutes=10), 500.0)
    # 3 px per 10 min at 500 m per px is 1500 m / 600 s = 2.5 m/s.
    assert float(np.median(field.speed_ms())) == pytest.approx(2.5, abs=0.5)


def test_optical_flow_reads_the_frames_through_the_zr_relation() -> None:
    frames = frames_from_rain(translating_rain(du=3.0, dv=1.0))
    field = optical_flow(frames, MP)
    assert field.method == "lucas_kanade"
    assert field.interval == timedelta(minutes=10)
    assert field.res_m == 500.0
    assert field.u.mean() == pytest.approx(3.0, abs=0.6)
    assert field.v.mean() == pytest.approx(1.0, abs=0.6)


# ============================================================================ degenerate
def test_a_dry_domain_gives_a_zero_field_rather_than_an_error() -> None:
    field = optical_flow_from_rain(np.zeros((3, 32, 32)), timedelta(minutes=10), 500.0)
    assert field.method == "zero"
    assert not np.any(field.u) and not np.any(field.v)


def test_a_single_frame_gives_a_zero_field() -> None:
    field = optical_flow_from_rain(translating_rain(n_frames=1), timedelta(minutes=10), 500.0)
    assert field.method == "zero"


def test_an_all_missing_history_gives_a_zero_field() -> None:
    field = optical_flow_from_rain(np.full((3, 32, 32), np.nan), timedelta(minutes=10), 500.0)
    assert field.method == "zero"


def test_flow_is_deterministic() -> None:
    stack = translating_rain()
    first = optical_flow_from_rain(stack, timedelta(minutes=10), 500.0)
    second = optical_flow_from_rain(stack, timedelta(minutes=10), 500.0)
    assert np.array_equal(first.u, second.u)
    assert np.array_equal(first.v, second.v)

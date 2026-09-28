"""The adaptive Z-R relation (SPEC.md 11.1 step 2, ``varuna_sky.zr``).

The centrepiece is the test SPEC.md names: *on synthetic data generated with Marshall-Palmer,
the fit recovers a, b within 10 %*. It is built the way the prototype actually works rather than
from a straight line with noise on it - a moving convective field is generated in mm/h, sampled
at station coordinates to make the gauge readings, and rendered to reflectivity exactly the way
``varuna_replay.storm.radar_dbz`` renders the bundles (Marshall-Palmer inverse, log-normal
speckle on Z, a coverage circle, then a floor to 5 dBZ classes). Only then is the fit asked to
find its way back to (200, 1.6).

``render_dbz`` here mirrors ``varuna_replay.storm.radar_dbz`` step for step. It is reimplemented
rather than imported because ``varuna-sky`` does not depend on ``varuna-replay`` and a test must
not be the thing that adds a dependency. The two were run against each other on this scene while
this file was written - same ``nan`` mask, identical values elsewhere - and every constant copied
across names the one it came from, so the mirror can be re-checked by eye when the renderer
changes.

The smaller tests build :class:`~varuna_sky.types.GaugePair` lists by hand from an exact
relation, so what each one asserts - the clamps, the fallbacks, the dry-pair rule, determinism -
is decidable by construction rather than by tolerance.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from pyproj import Transformer
from varuna_schemas.constants import IST
from varuna_sky.qc import run_qc
from varuna_sky.types import GaugePair, QCResult, RadarFrames, RadarGrid
from varuna_sky.zr import (
    A_RANGE,
    B_RANGE,
    DBZ_CLASS_WIDTH,
    MIN_ZR_PAIRS,
    MP_A,
    MP_B,
    fit_zr,
    gauge_pairs,
    marshall_palmer,
    run_zr,
)

# --------------------------------------------------------------------------- the scene
N_PX = 80
"""An 80 x 80 domain at 500 m: 40 km across, a quarter of the production grid's pixels, which
keeps every test in this file well under a second."""

RES_M = 500.0
CRS = "EPSG:32643"
LEFT = 280_000.0
TOP = 2_130_000.0
"""A UTM 43N patch over the Mumbai AOI, so the lon/lat the gauges carry are plausible."""

COVERAGE_RADIUS_M = 15_000.0
"""The rendered coverage circle, as ``varuna_replay.storm.COVERAGE_RADIUS_KM`` is for the real
domain: 30 px of an 80 px grid, so there is room for a station outside the disc."""

SPECKLE_SIGMA = 0.12
"""``varuna_replay.storm.SPECKLE_SIGMA``: multiplicative log-normal speckle on Z."""

MIN_DBZ = 5.0
"""``varuna_replay.storm.MIN_DBZ``: a class below this decodes as no echo."""

N_FRAMES = 6
FRAME_INTERVAL = timedelta(minutes=10)
T_LATEST = datetime(2019, 7, 2, 8, 40, tzinfo=IST)
SEED = 2019


def grid() -> RadarGrid:
    return RadarGrid(
        crs=CRS, res_m=RES_M, n_px=N_PX, transform=(RES_M, 0.0, LEFT, 0.0, -RES_M, TOP)
    )


def frame_times(n_frames: int = N_FRAMES) -> tuple[datetime, ...]:
    """Frame valid times, newest last, as :class:`RadarFrames` requires."""
    return tuple(T_LATEST - FRAME_INTERVAL * (n_frames - 1 - k) for k in range(n_frames))


def radius_m() -> np.ndarray:
    """Distance of every pixel centre from the domain centre, in metres."""
    offsets = (np.arange(N_PX, dtype=np.float64) + 0.5 - N_PX / 2.0) * RES_M
    return np.hypot(offsets[None, :], offsets[:, None])


# ------------------------------------------------------------------------ the rain field
BACKGROUND_MM_H = 0.4
"""The stratiform floor of the scene, well below the bundles' 2-5 mm/h.

A test choice, and the one the acceptance test depends on. The pairs have to span enough
reflectivity classes for the 5 dBZ staircase to average out: 0.4 mm/h is 16.6 dBZ and the cell
peaks are 47.7 dBZ, so the fit sees seven classes rather than the three a 3 mm/h background
would leave it with, and the recovered prefactor stops depending on where the class edges happen
to fall."""

CELLS = ((30.0, 14.0, 35.0, 6.0), (50.0, 26.0, 28.0, 8.0), (20.0, 40.0, 22.0, 5.0))
"""``(row, col, peak mm/h, sigma px)`` of the three convective cells at the first frame."""

CELL_DRIFT_PX = (-2.0, 5.0)
"""Rows and columns a cell moves per frame: north-east, and fast enough that no strong pixel
holds one 5 dBZ class for six frames, which the QC would - correctly - call ground clutter."""


def truth_rain(n_frames: int = N_FRAMES) -> np.ndarray:
    """A convective field in mm/h: three Gaussian cells drifting north-east over a background.

    Peaks stay at 35 mm/h, which is 47.7 dBZ through Marshall-Palmer. Speckle never lifts that
    to the 50 dBZ that would cast an attenuation shadow (SPEC.md 11.1 step 1), so the shadow is
    not a confounder in the scene the acceptance test is measured on.
    """
    rows, cols = np.mgrid[0:N_PX, 0:N_PX].astype(np.float64)
    drift_row, drift_col = CELL_DRIFT_PX
    frames = []
    for k in range(n_frames):
        field = np.full((N_PX, N_PX), BACKGROUND_MM_H)
        for row0, col0, peak, sigma in CELLS:
            d2 = (rows - (row0 + drift_row * k)) ** 2 + (cols - (col0 + drift_col * k)) ** 2
            field += peak * np.exp(-d2 / (2.0 * sigma**2))
        frames.append(field)
    return np.stack(frames)


def render_dbz(rain_mm_h: np.ndarray, *, seed: int = SEED, quantise: bool = True) -> np.ndarray:
    """Render rain as decoded-looking reflectivity, step for step as ``storm.radar_dbz`` does.

    ``Z = 200 R^1.6``, multiplicative log-normal speckle from ``default_rng([seed, 1])``, a
    coverage circle, then a floor to 5 dBZ classes with anything below 5 dBZ read as no echo.
    ``quantise=False`` keeps the continuous reflectivity, which is how a NetCDF volume would
    arrive and how the class artefact is isolated in the tests below.

    Checked against the real function while this test was written: on the field
    :func:`truth_rain` builds, ``radar_dbz`` and this produce the same ``nan`` mask and identical
    values elsewhere.
    """
    rng = np.random.default_rng([seed, 1])
    z = MP_A * np.power(np.clip(np.asarray(rain_mm_h, dtype=np.float64), 0.0, None), MP_B)
    z = z * np.exp(rng.normal(0.0, SPECKLE_SIGMA, size=z.shape))
    with np.errstate(divide="ignore", invalid="ignore"):
        dbz = 10.0 * np.log10(z)
    dbz = np.where(np.isfinite(dbz), dbz, -np.inf)
    if quantise:
        dbz = np.floor(dbz / DBZ_CLASS_WIDTH) * DBZ_CLASS_WIDTH
    dbz = np.where(dbz >= MIN_DBZ, dbz, np.nan)
    return np.where(radius_m()[None, :, :] <= COVERAGE_RADIUS_M, dbz, np.nan)


# ---------------------------------------------------------------------------- the gauges
def station_pixels() -> list[tuple[int, int]]:
    """Station pixels on two rings inside the coverage disc, plus the domain centre.

    Rings rather than a block: the fit needs stations in the cells and stations in the
    background, and a ring crossed by a drifting cell gives both over the hour.
    """
    centre = N_PX / 2.0
    pixels: list[tuple[int, int]] = [(int(centre), int(centre))]
    for radius_px, count, phase in ((12.0, 8, 0.0), (24.0, 12, 0.26)):
        for k in range(count):
            angle = 2.0 * np.pi * (k / count + phase)
            pixels.append(
                (int(centre + radius_px * np.sin(angle)), int(centre + radius_px * np.cos(angle)))
            )
    return pixels


def to_lonlat(row: int, col: int) -> tuple[float, float]:
    """The lon/lat of a pixel centre, so a station lands exactly where the test wants it."""
    x, y = grid().xy(row, col)
    lon, lat = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(x, y)
    return (float(lon), float(lat))


def gauge_table(
    rain: np.ndarray,
    pixels: list[tuple[int, int]] | None = None,
    *,
    n_frames: int = N_FRAMES,
) -> pd.DataFrame:
    """The bundle's ``gauges.csv`` columns, sampled from the truth field at station pixels.

    ``mm_5min`` is the depth in the five minutes ending at ``ts`` (SPEC.md 10.2), so a reading
    stamped 2.5 minutes after a frame describes exactly that frame's instant and pairs with it.
    The newest frame is the exception - a reading stamped after the newest frame is outside the
    window - so its reading is stamped at the frame time itself, which is still within half a
    frame interval of the instant it describes.
    """
    times = frame_times(n_frames)
    half = timedelta(minutes=2.5)
    rows = []
    for k, frame_ts in enumerate(times):
        ts = frame_ts + half if k < len(times) - 1 else frame_ts
        for index, (row, col) in enumerate(pixels or station_pixels()):
            rows.append(
                {
                    "ts": ts,
                    "station_id": f"S{index:02d}",
                    "lon": to_lonlat(row, col)[0],
                    "lat": to_lonlat(row, col)[1],
                    "mm_5min": float(rain[k, row, col]) * 5.0 / 60.0,
                }
            )
    return pd.DataFrame(rows)


def scene(
    *, quantise: bool = True, n_frames: int = N_FRAMES, seed: int = SEED
) -> tuple[RadarFrames, pd.DataFrame, QCResult]:
    """The whole chain: truth rain, gauge readings from it, radar frames rendered from it.

    ``seed`` moves only the speckle, so changing it asks the same question of a different draw
    of radar noise.
    """
    rain = truth_rain(n_frames)
    frames = RadarFrames(
        dbz=render_dbz(rain, seed=seed, quantise=quantise), times=frame_times(n_frames), grid=grid()
    )
    return (frames, gauge_table(rain, n_frames=n_frames), run_qc(frames))


# ------------------------------------------------------------------------- exact pairs
def exact_pairs(
    rates: list[float], a: float = MP_A, b: float = MP_B, ts: datetime = T_LATEST
) -> list[GaugePair]:
    """Pairs that obey ``Z = a R^b`` exactly, for the tests that must be decidable, not close."""
    return [
        GaugePair(
            ts=ts,
            station_id=f"S{index:02d}",
            row=index,
            col=index,
            gauge_mm_h=rate,
            dbz=10.0 * np.log10(a * rate**b),
        )
        for index, rate in enumerate(rates)
    ]


SPREAD = [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 90.0, 120.0]
"""Ten rates spanning more than two decades: enough pairs, and enough spread, to fit."""


# ============================================================== the SPEC.md acceptance test
@pytest.mark.parametrize("seed", [2019, 7, 101])
def test_fit_recovers_marshall_palmer_within_ten_percent(seed: int) -> None:
    """SPEC.md 11.1 step 2: on data generated with MP, the fit recovers a and b within 10 %.

    The frames are rendered exactly as the bundles are, so they arrive floored into 5 dBZ
    classes; the class width is declared to the pairing, which reads each class at its middle
    instead of its lower edge. Without that the fit would recover the relation between the gauge
    and the *decoded* frame, which is the next test but one.

    Three speckle draws, because a recovery that only works for one noise realisation is not a
    recovery.
    """
    frames, gauges, qc = scene(seed=seed)

    params, pairs = run_zr(frames, gauges, qc, dbz_class_width=DBZ_CLASS_WIDTH)

    assert params.source == "adaptive"
    assert len(pairs) >= MIN_ZR_PAIRS
    assert params.n_pairs == len(pairs)
    assert params.a == pytest.approx(MP_A, rel=0.10)
    assert params.b == pytest.approx(MP_B, rel=0.10)
    assert not params.clamped
    assert params.r2 is not None and params.r2 > 0.95


@pytest.mark.parametrize("seed", [2019, 7, 101])
def test_fit_recovers_marshall_palmer_from_continuous_reflectivity(seed: int) -> None:
    """The same recovery with no class artefact at all: nothing but speckle stands in the way.

    This is the control for the test above. A NetCDF radar volume (P1) arrives continuous, and
    then no correction is needed or asked for.
    """
    frames, gauges, qc = scene(quantise=False, seed=seed)

    params, _ = run_zr(frames, gauges, qc)

    assert params.source == "adaptive"
    assert params.a == pytest.approx(MP_A, rel=0.10)
    assert params.b == pytest.approx(MP_B, rel=0.10)


def test_raw_fit_absorbs_the_class_artefact_instead_of_inheriting_it() -> None:
    """Fitted on the frames as they arrive, the relation is the one that inverts them.

    Flooring to 5 dBZ classes drops every reading by half a class on average, so the raw fit
    lands well below Marshall-Palmer's 200 - and that is the number the pipeline wants, because
    the same quantised frames are what the relation is applied to. The assertion is on the thing
    that matters: rain recovered from the decoded frames through the raw relation matches the
    gauges, while the de-quantised relation under-reads them by tens of per cent.
    """
    frames, gauges, qc = scene()

    raw, pairs = run_zr(frames, gauges, qc)
    corrected, _ = run_zr(frames, gauges, qc, dbz_class_width=DBZ_CLASS_WIDTH)

    assert raw.a < 0.8 * corrected.a
    decoded_dbz = np.array([pair.dbz for pair in pairs])
    gauge_rain = np.array([pair.gauge_mm_h for pair in pairs])
    raw_rain = np.power(np.power(10.0, decoded_dbz / 10.0) / raw.a, 1.0 / raw.b)
    corrected_rain = np.power(np.power(10.0, decoded_dbz / 10.0) / corrected.a, 1.0 / corrected.b)
    assert float(np.median(raw_rain / gauge_rain)) == pytest.approx(1.0, abs=0.1)
    assert float(np.median(corrected_rain / gauge_rain)) < 0.85


# ============================================================================ the fit
def test_exact_relation_is_recovered_with_r2_of_one() -> None:
    """Pairs that lie on a line come back as that line, and the fit says it explains all of it."""
    params = fit_zr(exact_pairs(SPREAD, a=250.0, b=1.4))

    assert params.source == "adaptive"
    assert params.a == pytest.approx(250.0, rel=1e-9)
    assert params.b == pytest.approx(1.4, rel=1e-9)
    assert params.r2 == pytest.approx(1.0, abs=1e-12)
    assert not params.clamped


def test_prefactor_above_the_range_is_clamped_and_says_so() -> None:
    """A relation at a = 1000 comes back at the 400 ceiling with ``clamped`` set."""
    params = fit_zr(exact_pairs(SPREAD, a=1000.0, b=1.5))

    assert params.a == A_RANGE[1]
    assert params.b == pytest.approx(1.5, rel=1e-9)
    assert params.clamped
    assert params.source == "adaptive"
    assert params.r2 is not None and params.r2 < 1.0


def test_prefactor_below_the_range_is_clamped_to_the_floor() -> None:
    params = fit_zr(exact_pairs(SPREAD, a=40.0, b=1.3))

    assert params.a == A_RANGE[0]
    assert params.clamped


def test_exponent_outside_the_range_is_clamped_and_the_prefactor_is_re_solved() -> None:
    """When b is clamped the intercept is re-solved at the clamped b, not left where it was.

    That is the constrained least-squares optimum: with b fixed, the best intercept is
    ``mean(y) - b mean(x)``. Leaving the unconstrained intercept in place would give a line that
    is worse than the box allows.
    """
    pairs = exact_pairs(SPREAD, a=30.0, b=2.5)
    x = np.log10([pair.gauge_mm_h for pair in pairs])
    y = np.array([pair.dbz for pair in pairs]) / 10.0

    params = fit_zr(pairs)

    assert params.b == B_RANGE[1]
    assert params.clamped
    assert A_RANGE[0] < params.a < A_RANGE[1]  # the prefactor itself is not at a bound
    assert np.log10(params.a) == pytest.approx(float(y.mean() - B_RANGE[1] * x.mean()), rel=1e-9)


def test_a_clamped_fit_can_report_a_worse_than_useless_r2() -> None:
    """The r2 describes the relation returned, so a hard clamp is visible as a collapse in it."""
    params = fit_zr(exact_pairs(SPREAD, a=5000.0, b=3.0))

    assert params.clamped
    assert params.r2 is not None and params.r2 < 0.0


# ============================================================================ the fallback
def test_fewer_than_eight_pairs_falls_back_to_marshall_palmer() -> None:
    """SPEC.md 11.1 step 2: below :data:`MIN_ZR_PAIRS` the cycle does not fit at all."""
    params = fit_zr(exact_pairs(SPREAD[: MIN_ZR_PAIRS - 1], a=350.0, b=1.2))

    assert params.source == "marshall_palmer"
    assert (params.a, params.b) == (MP_A, MP_B)
    assert params.n_pairs == MIN_ZR_PAIRS - 1
    assert params.r2 is None
    assert not params.clamped


def test_exactly_eight_pairs_is_enough_to_fit() -> None:
    """The floor is inclusive: eight pairs fit, seven do not."""
    params = fit_zr(exact_pairs(SPREAD[:MIN_ZR_PAIRS], a=350.0, b=1.2))

    assert params.source == "adaptive"
    assert params.n_pairs == MIN_ZR_PAIRS


def test_no_pairs_at_all_falls_back_and_records_the_zero() -> None:
    params = fit_zr([])

    assert params.source == "marshall_palmer"
    assert params.n_pairs == 0


def test_gauges_that_all_read_the_same_rate_cannot_fit_an_exponent() -> None:
    """Ten pairs at one rate pin an intercept and say nothing about a slope, so: fall back."""
    params = fit_zr(exact_pairs([6.0] * 10, a=300.0, b=1.3))

    assert params.source == "marshall_palmer"
    assert params.n_pairs == 10


def test_rates_spanning_less_than_a_factor_of_two_fall_back() -> None:
    """The span rule is on the rates, not the count: ten pairs inside 1.5x is still not a fit."""
    params = fit_zr(exact_pairs([4.0 + 0.2 * k for k in range(10)], a=300.0, b=1.3))

    assert params.source == "marshall_palmer"


def test_marshall_palmer_helper_reports_the_pairs_the_cycle_did_have() -> None:
    params = marshall_palmer(3)

    assert (params.a, params.b, params.source, params.n_pairs) == (MP_A, MP_B, "marshall_palmer", 3)


# ============================================================================ pairing
def test_pairs_cover_every_station_and_frame_of_the_scene() -> None:
    """Every station is inside the disc and raining, so every reading becomes a pair."""
    frames, gauges, qc = scene()

    pairs = gauge_pairs(frames, gauges, qc)

    assert len(pairs) == len(gauges.index)
    assert {pair.station_id for pair in pairs} == set(gauges["station_id"])
    assert len({pair.ts for pair in pairs}) == N_FRAMES


def test_a_dry_gauge_is_never_paired() -> None:
    """``log R`` is undefined at zero: a dry reading must be dropped, not turned into -inf."""
    frames, gauges, qc = scene()
    gauges = gauges.copy()
    gauges.loc[gauges["station_id"] == "S00", "mm_5min"] = 0.0

    pairs = gauge_pairs(frames, gauges, qc)

    assert "S00" not in {pair.station_id for pair in pairs}
    assert len(pairs) == len(gauges.index) - N_FRAMES
    assert all(np.isfinite(pair.gauge_mm_h) and pair.gauge_mm_h > 0 for pair in pairs)
    assert all(np.isfinite(pair.dbz) for pair in pairs)


def test_a_trace_below_the_dry_floor_is_not_paired_either() -> None:
    """0.005 mm in five minutes is 0.06 mm/h, under the 0.1 mm/h floor: not a usable pair."""
    frames, gauges, qc = scene()
    gauges = gauges.copy()
    gauges.loc[gauges["station_id"] == "S01", "mm_5min"] = 0.005

    pairs = gauge_pairs(frames, gauges, qc)

    assert "S01" not in {pair.station_id for pair in pairs}


def test_a_gauge_outside_the_coverage_disc_is_not_a_pair() -> None:
    """The radar cannot see it, so there is no radar estimate to pair the reading with."""
    rain = truth_rain()
    frames = RadarFrames(dbz=render_dbz(rain), times=frame_times(), grid=grid())
    qc = run_qc(frames)
    inside, outside = (40, 40), (4, 4)
    gauges = gauge_table(rain, [inside, outside])

    pairs = gauge_pairs(frames, gauges, qc)

    assert not qc.coverage[outside]
    assert {pair.station_id for pair in pairs} == {"S00"}


def test_a_gauge_on_a_clutter_pixel_is_not_a_pair() -> None:
    """Ground clutter is a building, not rain; its reflectivity must not calibrate anything."""
    frames, gauges, qc = scene()
    row, col = station_pixels()[3]
    clutter = np.asarray(qc.clutter).copy()
    clutter[row, col] = True
    qc = QCResult(
        coverage=qc.coverage,
        clutter=clutter,
        attenuation=qc.attenuation,
        attenuation_flag=qc.attenuation_flag,
        dbz=qc.dbz,
    )

    pairs = gauge_pairs(frames, gauges, qc)

    assert "S03" not in {pair.station_id for pair in pairs}


def test_a_pixel_with_no_echo_is_not_a_pair() -> None:
    """A ``nan`` pixel is missing data. It is not zero reflectivity and it is not a pair."""
    frames, gauges, qc = scene()
    row, col = station_pixels()[2]
    dbz = np.asarray(frames.dbz).copy()
    dbz[:, row, col] = np.nan
    frames = RadarFrames(dbz=dbz, times=frames.times, grid=frames.grid)

    pairs = gauge_pairs(frames, gauges, qc)

    assert "S02" not in {pair.station_id for pair in pairs}


def test_readings_older_than_the_window_are_dropped() -> None:
    """SPEC.md 11.1 step 2 fits on the last 60 minutes; a two-hour-old reading is not in it."""
    frames, gauges, qc = scene()
    stale = gauges.copy()
    stale["ts"] = stale["ts"] - timedelta(hours=2)

    assert gauge_pairs(frames, stale, qc) == []
    assert gauge_pairs(frames, pd.concat([gauges, stale]), qc) == gauge_pairs(frames, gauges, qc)


def test_a_reading_inside_the_window_but_before_the_oldest_frame_is_dropped() -> None:
    """The window is an hour; the frames only reach back fifty minutes. The gap is real.

    A reading stamped 57 minutes ago describes 59.5 minutes ago, and the oldest frame in hand is
    50 minutes old - nine and a half minutes away, well past the half-interval tolerance. Pairing
    it anyway would calibrate the relation against a storm that has since moved.
    """
    frames, gauges, qc = scene()
    early = gauges.copy()
    early["ts"] = T_LATEST - timedelta(minutes=57)

    assert gauge_pairs(frames, early, qc) == []
    assert early["ts"].min() >= T_LATEST - timedelta(minutes=60)


def test_the_gauge_rate_is_the_five_minute_depth_in_millimetres_per_hour() -> None:
    """``mm_5min`` is an accumulation; the fit needs a rate, so the pair carries 12x the depth."""
    frames, gauges, qc = scene()

    pairs = gauge_pairs(frames, gauges, qc)

    by_key = {(pair.station_id, pair.ts): pair for pair in pairs}
    for row in gauges.itertuples():
        pair = by_key.get((row.station_id, row.ts))
        if pair is not None:
            assert pair.gauge_mm_h == pytest.approx(row.mm_5min * 12.0)


def test_the_pixel_a_pair_records_is_the_one_the_station_stands_in() -> None:
    """The station coordinates are lon/lat and the grid is metric: the projection has to be
    right, or every pair would be sampled from the wrong part of the storm."""
    frames, gauges, qc = scene()
    expected = station_pixels()

    pairs = gauge_pairs(frames, gauges, qc)

    for pair in pairs:
        assert (pair.row, pair.col) == expected[int(pair.station_id[1:])]


def test_pairing_is_deterministic_however_the_rows_arrive() -> None:
    """Rule 8. Shuffling the gauge table changes neither the pairs nor the last bit of the fit."""
    frames, gauges, qc = scene()
    shuffled = gauges.sample(frac=1.0, random_state=7).reset_index(drop=True)

    first, pairs = run_zr(frames, gauges, qc)
    second, shuffled_pairs = run_zr(frames, shuffled, qc)

    assert pairs == shuffled_pairs
    assert (first.a, first.b, first.r2) == (second.a, second.b, second.r2)


def test_pairs_are_ordered_by_time_then_station() -> None:
    frames, gauges, qc = scene()

    pairs = gauge_pairs(frames, gauges, qc)

    assert [(pair.ts, pair.station_id) for pair in pairs] == sorted(
        (pair.ts, pair.station_id) for pair in pairs
    )


def test_an_empty_gauge_table_pairs_nothing_and_falls_back() -> None:
    frames, gauges, qc = scene()

    params, pairs = run_zr(frames, gauges.iloc[0:0], qc)

    assert pairs == []
    assert params.source == "marshall_palmer"


def test_a_gauge_table_missing_a_column_says_which_one() -> None:
    frames, gauges, qc = scene()

    with pytest.raises(ValueError, match="mm_5min"):
        gauge_pairs(frames, gauges.drop(columns=["mm_5min"]), qc)


def test_naive_timestamps_are_refused_with_the_fix_in_the_message() -> None:
    """Every VARUNA timestamp carries +05:30; a naive one would silently mis-window."""
    frames, gauges, qc = scene()
    naive = gauges.copy()
    naive["ts"] = [ts.replace(tzinfo=None) for ts in naive["ts"]]

    with pytest.raises(ValueError, match="time zone"):
        gauge_pairs(frames, naive, qc)

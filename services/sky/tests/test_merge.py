"""Gauge merge: does it remove the bias it is given, honour the gauges it is handed, and
stay quiet where nobody measured anything (SPEC.md 11.1 step 3, P3.3).

The radar half of every fixture is rendered through Marshall-Palmer, ``Z = 200 R^1.6``, the
relation ``varuna_replay.storm`` builds the bundles with, so a pair the tests construct is the
same object the pipeline will hand this stage.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest
from varuna_schemas.constants import IST
from varuna_sky.merge import (
    GAUGE_TOLERANCE_PCT,
    IDW_RADIUS_M,
    MFB_MAX,
    MFB_MIN,
    gauge_anchors,
    gauge_error_pct,
    idw_residual,
    mean_field_bias,
    merge_gauges,
)
from varuna_sky.types import GaugePair, RadarGrid, ZRParams

MP = ZRParams(a=200.0, b=1.6, source="marshall_palmer", n_pairs=0)
"""Marshall-Palmer, the relation the replay bundles were rendered with (storm.py MP_A/MP_B)."""

N_PX = 64
"""A 64 x 64 domain at 500 m is 32 km across - wider than the 20 km search radius, so a pixel
can be genuinely out of every gauge's reach. The production 120 x 120 Sky grid is the
integrator's business, not this file's."""

RES_M = 500.0
T0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)


def grid(n_px: int = N_PX, res_m: float = RES_M) -> RadarGrid:
    return RadarGrid(
        crs="EPSG:32643",
        res_m=res_m,
        n_px=n_px,
        transform=(res_m, 0.0, 300_000.0, 0.0, -res_m, 2_140_000.0),
    )


def dbz_of(rain_mm_h: float) -> float:
    """Rain rate to reflectivity through Marshall-Palmer, as the storm designer renders it."""
    return float(10.0 * np.log10(MP.a * max(rain_mm_h, 1e-9) ** MP.b))


def pair(
    station_id: str,
    row: int,
    col: int,
    gauge_mm_h: float,
    radar_mm_h: float,
    minutes: int = 0,
) -> GaugePair:
    """One co-located reading. ``radar_mm_h`` is what the radar saw, stored as dBZ."""
    return GaugePair(
        ts=T0 + timedelta(minutes=minutes),
        station_id=station_id,
        row=row,
        col=col,
        gauge_mm_h=gauge_mm_h,
        dbz=dbz_of(radar_mm_h),
    )


def cell_field(
    peak: float = 60.0,
    background: float = 3.0,
    centre: tuple[int, int] = (30, 28),
    sigma: float = 9.0,
    n_px: int = N_PX,
) -> np.ndarray:
    """A convective cell over a stratiform background - the shape a bundle frame has."""
    rows, cols = np.mgrid[0:n_px, 0:n_px]
    r2 = (rows - centre[0]) ** 2 + (cols - centre[1]) ** 2
    return peak * np.exp(-r2 / (2.0 * sigma**2)) + background


STATIONS: tuple[tuple[str, int, int], ...] = (
    ("colaba", 12, 40),
    ("santacruz", 44, 18),
    ("hindmata", 30, 28),
    ("kings_circle", 26, 34),
    ("sion", 34, 24),
    ("kurla", 40, 44),
    ("andheri", 52, 12),
    ("khar", 48, 30),
    ("parel", 22, 22),
    ("dadar", 28, 20),
    ("milan", 50, 40),
    ("gandhi_market", 18, 32),
)
"""Twelve stations spread across the domain, named for the demo's own geography (SPEC.md
3.3) so nothing in this file reads as placeholder data."""


# ==================================================================== the mean-field bias
def test_a_uniform_bias_is_removed_exactly() -> None:
    """The whole point of MFB: a radar that under-reads by a constant factor is rescaled by
    that factor, and the residual step then has nothing left to do."""
    truth = cell_field()
    radar = truth / 1.5
    pairs = [
        pair(name, row, col, float(truth[row, col]), float(radar[row, col]))
        for name, row, col in STATIONS
    ]

    result = merge_gauges(radar, pairs, grid(), MP)

    assert result.method == "mfb+idw"
    assert result.n_gauges == len(STATIONS)
    assert result.mfb == pytest.approx(1.5, rel=1e-9)
    # The residual is zero at every gauge, so the merge is the bias correction alone and the
    # merged field reproduces the truth everywhere - not only where a gauge stands.
    assert np.allclose(result.rain_mm_h, truth, rtol=1e-6, atol=1e-6)
    assert result.max_gauge_error_pct is not None
    assert result.max_gauge_error_pct < 1e-6


def test_mfb_is_the_ratio_of_sums_not_the_mean_of_ratios() -> None:
    """A near-dry gauge with a wild ratio must not outweigh the gauge that saw the storm."""
    pairs = [
        pair("dry", 10, 10, 0.4, 0.1),  # ratio 4.0, but only 0.4 mm/h of rain in it
        pair("wet", 40, 40, 30.0, 30.0),  # ratio 1.0, and it carries the event
    ]
    mfb = mean_field_bias(pairs, MP)
    assert mfb == pytest.approx(30.4 / 30.1, rel=1e-9)
    assert mfb < np.mean([4.0, 1.0]), "the mean of ratios would have been 2.5"


def test_mfb_is_clamped_when_the_gauges_and_the_radar_disagree_absurdly() -> None:
    """A network reading a hundred times the radar is broken, not informative."""
    high = [pair(f"s{i}", 10 + i, 10, 100.0, 1.0) for i in range(4)]
    assert mean_field_bias(high, MP) == MFB_MAX

    low = [pair(f"s{i}", 10 + i, 10, 0.0, 40.0) for i in range(4)]
    assert mean_field_bias(low, MP) == MFB_MIN


def test_mfb_is_one_when_no_pair_carries_a_radar_value() -> None:
    """Gauges the radar missed still anchor residuals, but they cannot measure a bias."""
    radar = np.zeros((N_PX, N_PX))
    blind = [
        GaugePair(ts=T0, station_id=name, row=row, col=col, gauge_mm_h=14.0, dbz=float("nan"))
        for name, row, col in STATIONS[:4]
    ]

    result = merge_gauges(radar, blind, grid(), MP)

    assert result.mfb == 1.0
    assert result.method == "mfb+idw"
    assert result.n_gauges == 4
    # An additive residual is the only kind that can put rain back where the radar saw none.
    for name, row, col in STATIONS[:4]:
        assert result.rain_mm_h[row, col] == pytest.approx(14.0, rel=1e-6), name


# ========================================================================= the 5 % contract
def test_merged_field_honours_every_gauge_within_five_percent() -> None:
    """SPEC.md 11.1 step 3's acceptance criterion, on a field where every gauge disagrees
    with the radar differently, so the bias correction alone cannot satisfy it."""
    truth = cell_field()
    radar = truth * 0.7
    rng = np.random.default_rng(2019)
    readings = {
        name: float(truth[row, col] * (1.0 + 0.35 * rng.standard_normal()))
        for name, row, col in STATIONS
    }
    pairs = [
        pair(name, row, col, max(readings[name], 0.0), float(radar[row, col]))
        for name, row, col in STATIONS
    ]

    result = merge_gauges(radar, pairs, grid(), MP)

    assert result.method == "mfb+idw"
    assert result.max_gauge_error_pct is not None
    assert result.max_gauge_error_pct <= GAUGE_TOLERANCE_PCT

    # Measured, not taken on trust: re-derive the miss from the field that came back.
    for name, row, col in STATIONS:
        expected = max(readings[name], 0.0)
        got = float(result.rain_mm_h[row, col])
        miss = abs(got - expected) / max(expected, 0.1) * 100.0
        assert miss <= GAUGE_TOLERANCE_PCT, f"{name}: merged {got:.2f} vs gauge {expected:.2f}"
        assert miss <= result.max_gauge_error_pct + 1e-9, f"{name} is worse than reported"


def test_the_error_metric_reports_a_miss_instead_of_hiding_it() -> None:
    """Two stations inside one 500 m pixel cannot both be honoured. The merge splits the
    difference - the coincident-point rule - and says how badly it missed."""
    radar = cell_field()
    pairs = [
        pair("kings_circle_a", 26, 34, 5.0, float(radar[26, 34])),
        pair("kings_circle_b", 26, 34, 25.0, float(radar[26, 34])),
        pair("sion", 34, 24, float(radar[34, 24]), float(radar[34, 24])),
    ]

    result = merge_gauges(radar, pairs, grid(), MP)

    assert result.n_gauges == 3
    assert result.max_gauge_error_pct is not None
    assert result.max_gauge_error_pct > GAUGE_TOLERANCE_PCT
    at_pixel = float(result.rain_mm_h[26, 34])
    assert 5.0 < at_pixel < 25.0, "the shared pixel gets the mean of the two residuals"


def test_gauge_error_pct_measures_a_dry_gauge_against_the_dry_floor() -> None:
    """A gauge reading zero has no percentage to be within, and must not silently pass."""
    merged = np.zeros((N_PX, N_PX))
    merged[10, 10] = 1.0
    anchors = [pair("dry", 10, 10, 0.0, 0.0)]
    assert gauge_error_pct(merged, anchors) == pytest.approx(1000.0)
    assert gauge_error_pct(np.zeros((N_PX, N_PX)), anchors) == 0.0
    assert gauge_error_pct(merged, []) is None


# =============================================================== degradation: one, and none
def test_one_gauge_degrades_to_mfb_and_says_so() -> None:
    """A single station cannot describe a spatial residual, so only the bias is applied - and
    the label, and the reported miss, both admit it."""
    radar = cell_field()
    history = [
        pair("santacruz", 44, 18, 2.0, 2.0, minutes=0),
        pair("santacruz", 44, 18, 4.0, 4.0, minutes=15),
        pair("santacruz", 44, 18, 8.0, 8.0, minutes=30),
        pair("santacruz", 44, 18, 16.0, 8.0, minutes=45),
    ]

    result = merge_gauges(radar, history, grid(), MP)

    assert result.method == "mfb"
    assert result.n_gauges == 1
    assert result.mfb == pytest.approx(30.0 / 22.0, rel=1e-6)
    # No bullseye: the field is the radar scaled, nothing more.
    assert np.allclose(result.rain_mm_h, radar * result.mfb)
    # And the miss at the one gauge is real and reported, not smoothed away.
    expected = abs(radar[44, 18] * result.mfb - 16.0) / 16.0 * 100.0
    assert result.max_gauge_error_pct == pytest.approx(expected)
    assert result.max_gauge_error_pct > GAUGE_TOLERANCE_PCT


def test_no_gauges_leaves_the_field_untouched() -> None:
    radar = cell_field()
    result = merge_gauges(radar, [], grid(), MP)

    assert result.method == "none"
    assert result.mfb == 1.0
    assert result.n_gauges == 0
    assert result.max_gauge_error_pct is None
    assert np.array_equal(result.rain_mm_h, radar)


def test_unusable_pairs_are_not_gauges() -> None:
    """Off-grid rows, negative rates and non-finite readings anchor nothing."""
    radar = cell_field()
    junk = [
        pair("off_north", -1, 10, 12.0, 4.0),
        pair("off_east", 10, N_PX, 12.0, 4.0),
        pair("negative", 10, 10, -3.0, 4.0),
        GaugePair(ts=T0, station_id="nan", row=11, col=11, gauge_mm_h=float("nan"), dbz=20.0),
    ]
    assert gauge_anchors(junk, grid()) == []

    result = merge_gauges(radar, junk, grid(), MP)
    assert result.method == "none"
    assert np.array_equal(result.rain_mm_h, radar)


# ============================================================================= dry pixels
def test_dry_pixels_out_of_every_gauge_s_reach_stay_exactly_dry() -> None:
    """The merge may put rain back where a gauge says the radar missed it. It may not invent
    rain in a corner of the domain where nobody measured anything."""
    radar = np.zeros((N_PX, N_PX))
    radar[5:21, 5:21] = 8.0
    stations = [("patch_a", 8, 8), ("patch_b", 12, 16), ("patch_c", 18, 10)]
    pairs = [pair(name, row, col, 40.0, 8.0) for name, row, col in stations]

    result = merge_gauges(radar, pairs, grid(), MP)

    rows, cols = np.mgrid[0:N_PX, 0:N_PX]
    out_of_reach = np.ones((N_PX, N_PX), dtype=bool)
    for _, row, col in stations:
        distance = np.hypot(rows - row, cols - col) * RES_M
        out_of_reach &= distance >= IDW_RADIUS_M
    assert out_of_reach.any(), "the fixture must contain pixels no gauge can reach"
    assert np.all(result.rain_mm_h[out_of_reach] == 0.0)
    # ...while the gauges themselves were still honoured.
    for _, row, col in stations:
        assert result.rain_mm_h[row, col] == pytest.approx(40.0, rel=1e-6)


def test_missing_radar_pixels_come_back_dry_not_as_a_sentinel() -> None:
    """Quality control writes ``nan`` for "do not believe this"; the nowcaster needs a number."""
    radar = cell_field()
    radar[0:4, :] = np.nan

    # With nothing to merge, "missing becomes dry" is the whole transformation.
    assert np.all(merge_gauges(radar, [], grid(), MP).rain_mm_h[0:4, :] == 0.0)

    pairs = [
        pair(name, row, col, float(radar[row, col]), float(radar[row, col]))
        for name, row, col in STATIONS[:4]
    ]
    result = merge_gauges(radar, pairs, grid(), MP)

    assert np.all(np.isfinite(result.rain_mm_h)), "no nan may reach the nowcaster"
    # The blanked rows are within reach of the gauges, so they may carry a residual - but the
    # gauges here agree with the radar, so what they carry is numerical dust, not invented rain.
    assert float(np.abs(result.rain_mm_h[0:4, :]).max()) < 1e-9


def test_the_merged_field_is_never_negative() -> None:
    """Dry gauges inside a wet radar field drive the residual below zero; rain rates stop at 0."""
    radar = cell_field(peak=80.0, background=10.0)
    pairs = [pair(name, row, col, 0.0, float(radar[row, col])) for name, row, col in STATIONS]

    result = merge_gauges(radar, pairs, grid(), MP)

    assert float(result.rain_mm_h.min()) >= 0.0
    assert result.max_gauge_error_pct is not None
    # A dry gauge is measured against the 0.1 mm/h dry floor, so this is a miss of a
    # nanometre of rain per hour: the clip cost the merge nothing at the gauges.
    assert result.max_gauge_error_pct < 0.01


# =================================================================== the weighting itself
def test_idw_residual_decays_to_zero_at_the_search_radius() -> None:
    """The failure this guards against: normalising by the gauge weights alone would hold the
    residual at full strength right up to the radius and then drop it, ringing the map."""
    g = grid()
    anchor = pair("hindmata", 32, 10, 10.0, 1.0)
    field = idw_residual([anchor], [10.0], g)
    edge = int(IDW_RADIUS_M / RES_M)  # 40 pixels at 500 m
    ray = field[32, 10 : 10 + edge + 2]

    assert ray[0] == pytest.approx(10.0, rel=1e-6), "exact at the gauge"
    assert np.all(np.diff(ray[: edge + 1]) < 0.0), "falls away strictly, never a plateau"
    assert ray[edge // 2] == pytest.approx(5.0, rel=1e-6), "half weight at half the radius"
    assert ray[edge - 1] < 0.05, "already nothing when it reaches the radius"
    assert ray[edge] == 0.0
    assert ray[edge + 1] == 0.0


def test_two_anchors_split_a_pixel_they_share() -> None:
    g = grid()
    anchors = [pair("a", 20, 20, 1.0, 1.0), pair("b", 20, 20, 1.0, 1.0)]
    field = idw_residual(anchors, [4.0, 10.0], g)
    assert field[20, 20] == pytest.approx(7.0, rel=1e-6)


# ============================================================================ determinism
def test_the_merge_is_byte_identical_under_a_reshuffled_input() -> None:
    """Rule 8. Neither the bias sums nor the weighted residual may depend on arrival order."""
    truth = cell_field()
    radar = truth * 0.8
    pairs = [
        pair(name, row, col, float(truth[row, col]) + 0.5 * k, float(radar[row, col]), k * 5)
        for k, (name, row, col) in enumerate(STATIONS)
    ]
    shuffled = list(pairs)
    np.random.default_rng(7).shuffle(shuffled)  # type: ignore[arg-type]

    first = merge_gauges(radar, pairs, grid(), MP)
    second = merge_gauges(radar, shuffled, grid(), MP)

    assert first.mfb == second.mfb
    assert first.n_gauges == second.n_gauges
    assert first.max_gauge_error_pct == second.max_gauge_error_pct
    assert np.array_equal(first.rain_mm_h, second.rain_mm_h)


def test_the_latest_reading_per_station_anchors_the_residual() -> None:
    """The bias is a statement about the last hour; the residual is about this instant."""
    history = [
        pair("hindmata", 30, 28, 5.0, 5.0, minutes=0),
        pair("hindmata", 30, 28, 45.0, 5.0, minutes=45),
        pair("sion", 34, 24, 6.0, 6.0, minutes=30),
    ]
    anchors = gauge_anchors(history, grid())
    assert [a.station_id for a in anchors] == ["hindmata", "sion"]
    assert anchors[0].gauge_mm_h == 45.0


# ============================================================================== contract
def test_a_field_that_does_not_match_the_grid_is_refused() -> None:
    with pytest.raises(ValueError, match="does not match the grid"):
        merge_gauges(np.zeros((8, 8)), [], grid(), MP)

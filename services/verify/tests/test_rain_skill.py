"""Rain skill against the truth field (SPEC.md 11.12, task P3.6).

The properties the spec names, not merely that the code runs: a perfect forecast scores
CSI 1, POD 1, FAR 0 and Brier 0; a forecast that always misses scores CSI 0 and POD 0; CSI
stays inside [0, 1] on random fields; the lead-time axis has one entry per scored step; time
axes that do not overlap raise rather than silently truncating; pixels neither field covers
drop out of the sample; and the exceedance probability is the fraction of members above the
threshold.

The fields here are tiny synthetic cubes on a 6-pixel Sky grid, so the file runs in seconds.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta

import numpy as np
import pytest
from varuna_schemas.constants import IST
from varuna_sky.types import RadarGrid, RainEnsemble, SkyProducts, ZRParams
from varuna_verify.rain_skill import (
    GridMismatchError,
    TimeAxisMismatchError,
    TruthCube,
    load_truth_cube,
    rain_skill,
    rain_skill_from_products,
)

N_PX = 6
RES_M = 500.0
CYCLE_TS = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
STEP = timedelta(minutes=5)


def _grid(n_px: int = N_PX, left: float = 300_000.0) -> RadarGrid:
    return RadarGrid(
        crs="EPSG:32643",
        res_m=RES_M,
        n_px=n_px,
        transform=(RES_M, 0.0, left, 0.0, -RES_M, 2_120_000.0),
    )


def _times(n_steps: int, *, start: datetime = CYCLE_TS + STEP) -> tuple[datetime, ...]:
    return tuple(start + i * STEP for i in range(n_steps))


def _zr() -> ZRParams:
    return ZRParams(a=200.0, b=1.6, source="marshall_palmer", n_pairs=0)


def _truth_field(n_steps: int, *, seed: int = 2019) -> np.ndarray:
    """A truth cube with a mix of dry, moderate and heavy pixels at every step."""
    rng = np.random.default_rng(seed)
    return rng.uniform(0.0, 60.0, size=(n_steps, N_PX, N_PX))


def _truth(field: np.ndarray, *, times: tuple[datetime, ...] | None = None) -> TruthCube:
    return TruthCube(
        rain_mm_h=field,
        times=times if times is not None else _times(field.shape[0]),
        grid=_grid(),
    )


def _ensemble(
    field: np.ndarray, *, n_members: int = 20, times: tuple[datetime, ...] | None = None
) -> RainEnsemble:
    """An ensemble whose every member is ``field``: the median is the field itself."""
    rain = np.repeat(field[None, ...], n_members, axis=0)
    return RainEnsemble(
        rain_mm_h=rain,
        times=times if times is not None else _times(field.shape[0]),
        grid=_grid(),
        source="fallback_steps",
        seed=2019,
        zr=_zr(),
    )


# ============================================================================ perfect
def test_perfect_forecast_scores_one() -> None:
    """Forecast equals truth: every event is a hit, none is a false alarm, Brier is 0."""
    truth_field = _truth_field(4)
    report = rain_skill(_ensemble(truth_field), _truth(truth_field), cycle_ts=CYCLE_TS)

    assert report.n_leads_unmatched == 0
    for lead in report.leads:
        assert lead.mae_mm_h == pytest.approx(0.0)
        for score in lead.thresholds:
            assert score.hits > 0, "the synthetic truth must exceed both thresholds somewhere"
            assert score.csi == pytest.approx(1.0)
            assert score.pod == pytest.approx(1.0)
            assert score.far == pytest.approx(0.0)
            assert score.brier == pytest.approx(0.0)


def test_perfect_forecast_maps_onto_the_contract_model() -> None:
    """The chart rows are ``SkillByLead``, not a parallel shape (SPEC.md 7.10)."""
    truth_field = _truth_field(3)
    report = rain_skill(_ensemble(truth_field), _truth(truth_field), cycle_ts=CYCLE_TS)

    rows = report.skill_by_lead
    assert len(rows) == 3 * 2
    assert {row.threshold_mm_h for row in rows} == {20, 40}
    assert [row.lead_min for row in rows[:2]] == [5, 5]
    assert all(row.csi == pytest.approx(1.0) for row in rows)
    assert all(row.n == N_PX * N_PX for row in rows)


# ============================================================================ always miss
def test_always_dry_forecast_scores_zero() -> None:
    """A dry forecast under a wet truth: no hits, no false alarms, so CSI and POD are 0."""
    truth_field = _truth_field(3)
    dry = np.zeros_like(truth_field)
    report = rain_skill(_ensemble(dry), _truth(truth_field), cycle_ts=CYCLE_TS)

    for lead in report.leads:
        for score in lead.thresholds:
            assert score.hits == 0
            assert score.misses > 0
            assert score.false_alarms == 0
            assert score.csi == pytest.approx(0.0)
            assert score.pod == pytest.approx(0.0)
            assert score.far is None, "no forecast events means the ratio has no denominator"
            assert score.brier is not None and score.brier > 0.0
            assert lead.mae_mm_h is not None and lead.mae_mm_h > 0.0


def test_scores_are_none_when_a_denominator_is_empty() -> None:
    """A dry truth and a dry forecast: nothing to detect, so no score is invented."""
    dry = np.zeros((2, N_PX, N_PX))
    report = rain_skill(_ensemble(dry), _truth(dry), cycle_ts=CYCLE_TS)

    for lead in report.leads:
        for score in lead.thresholds:
            assert score.csi is None
            assert score.pod is None
            assert score.far is None
            assert score.correct_negatives == N_PX * N_PX
            assert score.brier == pytest.approx(0.0)


# ============================================================================ bounds
def test_csi_stays_in_the_unit_interval_for_random_fields() -> None:
    """CSI, POD, FAR and Brier are all bounded scores; random members must not escape them."""
    rng = np.random.default_rng(7)
    truth_field = _truth_field(6, seed=11)
    rain = rng.uniform(0.0, 60.0, size=(20, 6, N_PX, N_PX))
    ensemble = RainEnsemble(
        rain_mm_h=rain,
        times=_times(6),
        grid=_grid(),
        source="fallback_steps",
        seed=7,
        zr=_zr(),
    )
    report = rain_skill(ensemble, _truth(truth_field), cycle_ts=CYCLE_TS)

    for lead in report.leads:
        assert lead.n == N_PX * N_PX
        assert lead.mae_mm_h is not None and lead.mae_mm_h >= 0.0
        for score in lead.thresholds:
            assert score.n == N_PX * N_PX
            for value in (score.csi, score.pod, score.far, score.brier):
                assert value is not None
                assert 0.0 <= value <= 1.0
            assert score.hits + score.misses + score.false_alarms + score.correct_negatives == (
                N_PX * N_PX
            )


# ============================================================================ lead axis
def test_lead_axis_has_one_entry_per_step() -> None:
    """36 steps of 5 minutes is a 5..180 minute axis (SPEC.md 10.3)."""
    truth_field = _truth_field(36)
    report = rain_skill(_ensemble(truth_field, n_members=20), _truth(truth_field))

    assert len(report.leads) == 36
    assert report.lead_minutes == tuple(range(5, 185, 5))
    assert report.cycle_ts == CYCLE_TS, "the cycle time is inferred from the step spacing"
    assert len(report.skill_by_lead) == 36 * 2


def test_steps_past_the_truth_cube_are_reported_not_dropped_silently() -> None:
    """A cycle near the end of the bundle forecasts past the truth; the report says so."""
    truth_field = _truth_field(3)
    forecast_field = _truth_field(6, seed=2019)[:6]
    forecast_field[:3] = truth_field
    ensemble = _ensemble(forecast_field, times=_times(6))
    report = rain_skill(ensemble, _truth(truth_field, times=_times(3)), cycle_ts=CYCLE_TS)

    assert len(report.leads) == 3
    assert report.n_leads_unmatched == 3
    assert any("not scored" in note for note in report.notes)


# ============================================================================ honesty
def test_report_labels_the_truth_field() -> None:
    """Rules 6 and 7: the report names the reconstruction it scored against."""
    truth_field = _truth_field(2)
    report = rain_skill(_ensemble(truth_field), _truth(truth_field), cycle_ts=CYCLE_TS)

    assert report.truth_source == "truth/rain.zarr"
    assert any("reconstructed" in note for note in report.notes)
    payload = report.to_dict()
    assert payload["cycle_ts"] == CYCLE_TS.isoformat()
    assert payload["thresholds_mm_h"] == [20.0, 40.0]
    assert len(payload["leads"]) == 2  # type: ignore[arg-type]


# ============================================================================ alignment
def test_disjoint_time_axes_raise() -> None:
    """No shared valid time is an error, never an empty score."""
    truth_field = _truth_field(3)
    ensemble = _ensemble(truth_field, times=_times(3, start=CYCLE_TS + timedelta(days=1)))

    with pytest.raises(TimeAxisMismatchError, match="share no valid time"):
        rain_skill(ensemble, _truth(truth_field), cycle_ts=CYCLE_TS + timedelta(days=1))


def test_offset_time_axes_raise() -> None:
    """Steps offset by half a step share no instant, even though the windows overlap."""
    truth_field = _truth_field(4)
    shifted = _times(4, start=CYCLE_TS + STEP + timedelta(seconds=150))
    ensemble = _ensemble(truth_field, times=shifted)

    with pytest.raises(TimeAxisMismatchError):
        rain_skill(ensemble, _truth(truth_field), cycle_ts=CYCLE_TS)


def test_naive_times_raise() -> None:
    """Contract times are timezone-aware IST; a naive clock is refused."""
    truth_field = _truth_field(2)
    naive = tuple(datetime(2019, 7, 2, 6, 45) + i * STEP for i in range(2))
    ensemble = _ensemble(truth_field, times=naive)

    with pytest.raises(ValueError, match="timezone-aware"):
        rain_skill(ensemble, _truth(truth_field))


def test_a_different_grid_raises() -> None:
    """Pixel-to-pixel scoring across two grids would be meaningless."""
    truth_field = _truth_field(2)
    other = TruthCube(rain_mm_h=truth_field, times=_times(2), grid=_grid(left=999_000.0))

    with pytest.raises(GridMismatchError, match="transform"):
        rain_skill(_ensemble(truth_field), other, cycle_ts=CYCLE_TS)


# ============================================================================ products path
def test_products_path_scores_the_published_numbers() -> None:
    """``SkyProducts`` carries p50 and the two exceedance fields; scoring them agrees."""
    truth_field = _truth_field(3)
    n_steps = truth_field.shape[0]
    products = SkyProducts(
        p10=truth_field,
        p50=truth_field,
        p90=truth_field,
        mean=truth_field,
        p_gt_20=(truth_field > 20.0).astype(np.float64),
        p_gt_40=(truth_field > 40.0).astype(np.float64),
        aoi_hyetographs=np.zeros((20, n_steps)),
        times=_times(n_steps),
        grid=_grid(),
    )
    report = rain_skill_from_products(products, _truth(truth_field), cycle_ts=CYCLE_TS)

    for lead in report.leads:
        for score in lead.thresholds:
            assert score.csi == pytest.approx(1.0)
            assert score.brier == pytest.approx(0.0)


# ============================================================================ coverage
def test_pixels_missing_in_either_field_are_not_scored() -> None:
    """The radar's coverage mask narrows the sample; it is never read as a dry forecast."""
    truth_field = _truth_field(2)
    rain = np.repeat(truth_field[None, ...], 5, axis=0)
    rain[:, :, 0, 0] = np.nan  # no member covers this pixel
    masked_truth = truth_field.copy()
    masked_truth[:, 1, 1] = np.nan  # the truth does not cover that one
    ensemble = RainEnsemble(
        rain_mm_h=rain,
        times=_times(2),
        grid=_grid(),
        source="fallback_steps",
        seed=2019,
        zr=_zr(),
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        report = rain_skill(ensemble, _truth(masked_truth), cycle_ts=CYCLE_TS)

    for lead in report.leads:
        assert lead.n == N_PX * N_PX - 2
        assert lead.mae_mm_h == pytest.approx(0.0), "the covered pixels are forecast exactly"
        for score in lead.thresholds:
            assert score.n == N_PX * N_PX - 2
            assert score.far == pytest.approx(0.0)


def test_exceedance_probability_is_the_member_fraction() -> None:
    """Half the members above the threshold is p = 0.5, so an observed event scores 0.25."""
    wet, dry = 50.0, 0.0
    rain = np.concatenate(
        [
            np.full((10, 1, N_PX, N_PX), wet),
            np.full((10, 1, N_PX, N_PX), dry),
        ],
        axis=0,
    )
    ensemble = RainEnsemble(
        rain_mm_h=rain,
        times=_times(1),
        grid=_grid(),
        source="fallback_steps",
        seed=2019,
        zr=_zr(),
    )
    truth_field = np.full((1, N_PX, N_PX), wet)

    report = rain_skill(ensemble, _truth(truth_field), cycle_ts=CYCLE_TS)

    for score in report.leads[0].thresholds:
        assert score.brier == pytest.approx(0.25), "p = 0.5 against an observed event"


# ============================================================================ determinism
def test_scoring_is_deterministic() -> None:
    """Rule 8: the same cubes give the same integers, run after run."""
    truth_field = _truth_field(4, seed=5)
    rng = np.random.default_rng(19)
    rain = rng.uniform(0.0, 60.0, size=(20, 4, N_PX, N_PX))
    ensemble = RainEnsemble(
        rain_mm_h=rain,
        times=_times(4),
        grid=_grid(),
        source="fallback_steps",
        seed=19,
        zr=_zr(),
    )
    truth = _truth(truth_field)

    first = rain_skill(ensemble, truth, cycle_ts=CYCLE_TS).to_dict()
    second = rain_skill(ensemble, truth, cycle_ts=CYCLE_TS).to_dict()

    assert first == second


# ============================================================================ loader
def test_load_truth_cube_round_trip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The loader reads the bundle writer's layout: rain, time_min and the grid attributes."""
    import zarr

    field = _truth_field(3).astype(np.float32)
    path = tmp_path / "rain.zarr"
    group = zarr.open_group(str(path), mode="w")
    cube = group.create_array("rain", shape=field.shape, dtype="float32")
    cube[:] = field
    axis = group.create_array("time_min", shape=(3,), dtype="float64")
    axis[:] = np.array([5.0, 10.0, 15.0])
    group.attrs["t0"] = CYCLE_TS.isoformat()
    group.attrs["crs"] = "EPSG:32643"
    group.attrs["res_m"] = RES_M
    group.attrs["n_px"] = N_PX
    group.attrs["transform"] = [RES_M, 0.0, 300_000.0, 0.0, -RES_M, 2_120_000.0]

    truth = load_truth_cube(path)

    assert truth.n_frames == 3
    assert truth.times == _times(3)
    assert truth.grid.n_px == N_PX
    assert np.allclose(truth.rain_mm_h, field)

    report = rain_skill(_ensemble(field.astype(np.float64)), truth, cycle_ts=CYCLE_TS)
    assert report.lead_minutes == (5, 10, 15)


def test_load_truth_cube_missing_path_raises(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(FileNotFoundError):
        load_truth_cube(tmp_path / "absent.zarr")

"""Event-level rain skill by lead time (varuna_verify.rain_event).

The synthetic case is small enough to count by hand. On a 4 x 4 Sky grid the truth at +5 min
rains above 20 mm/h at A, B and C; the ensemble mean forecasts it at A, B and D; persistence (the
held analysis) at A only. A, B and C sit inside the AOI and D outside it, so:

* domain, mean:        2 hits, 1 miss, 1 false alarm  -> CSI 2/4, POD 2/3, FAR 1/3
* AOI, mean:           2 hits, 1 miss, 0 false alarms -> CSI 2/3
* persistence (both):  1 hit, 2 misses                -> CSI 1/3

At +10 min the truth rains at A only, the forecast is dry and persistence still says A, so the
forecast falls below persistence and below the 0.5 floor: the horizon is +5 min.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from varuna_schemas.constants import IST
from varuna_sky.types import RadarGrid
from varuna_verify.rain_event import (
    CSI_FLOOR,
    CycleForecast,
    event_rain_skill,
    rain_event_inputs,
    score_cycles,
    skill_horizon,
)
from varuna_verify.rain_skill import TruthCube

GRID = RadarGrid(
    crs="EPSG:32643", res_m=500.0, n_px=4, transform=(500.0, 0, 0.0, 0, -500.0, 2000.0)
)
AOI_BOUNDS = (0.0, 1000.0, 1000.0, 2000.0)  # rows 0-1, cols 0-1
A, B, C, D = (0, 0), (0, 1), (1, 0), (3, 3)
CYCLE = datetime(2019, 7, 2, 6, 0, tzinfo=IST)
WET, DRY = 30.0, 1.0


def _field(*cells: tuple[int, int]) -> np.ndarray:
    out = np.full((4, 4), DRY)
    for row, col in cells:
        out[row, col] = WET
    return out


def _truth(n_frames: int = 3) -> TruthCube:
    frames = [_field(), _field(A, B, C), _field(A)] + [_field(A)] * (n_frames - 3)
    return TruthCube(
        rain_mm_h=np.stack(frames[:n_frames]).astype(np.float32),
        times=tuple(CYCLE + timedelta(minutes=5 * k) for k in range(n_frames)),
        grid=GRID,
    )


def _cycle(run_id: str = "run-a", n_steps: int = 2) -> CycleForecast:
    steps = [_field(A, B, D), _field()] + [_field()] * (n_steps - 2)
    mean = np.stack(steps[:n_steps])
    prob = (mean > 20).astype(np.float64)
    return CycleForecast(
        run_id=run_id,
        cycle_ts=CYCLE,
        times=tuple(CYCLE + timedelta(minutes=5 * (k + 1)) for k in range(n_steps)),
        grid=GRID,
        mean=mean,
        p50=mean.copy(),
        probs={10.0: prob, 20.0: prob, 40.0: None},
        persistence=_field(A),
        coverage=np.ones((4, 4), dtype=bool),
        aoi_bounds=AOI_BOUNDS,
        n_members=20,
        member_cube=False,
        products_source="rain/quantiles.zarr",
        persistence_facts={"matches_cycle": True},
    )


def _row(result: dict, scope: str, lead: int) -> dict:
    return next(r for r in result["by_scope"][scope]["by_lead"] if r["lead_min"] == lead)


def test_contingency_matches_the_hand_count() -> None:
    result = score_cycles([_cycle()], _truth())
    domain = _row(result, "domain", 5)["thresholds"]["20"]
    assert {k: domain["mean"][k] for k in ("hits", "misses", "false_alarms")} == {
        "hits": 2,
        "misses": 1,
        "false_alarms": 1,
    }
    assert domain["mean"]["correct_negatives"] == 12
    assert domain["mean"]["csi"] == pytest.approx(0.5)
    assert domain["mean"]["pod"] == pytest.approx(round(2 / 3, 4))
    assert domain["mean"]["far"] == pytest.approx(round(1 / 3, 4))
    assert domain["persistence"]["csi"] == pytest.approx(round(1 / 3, 4))
    assert domain["event_pixels"] == 3

    aoi = _row(result, "aoi", 5)["thresholds"]["20"]
    assert aoi["mean"]["csi"] == pytest.approx(round(2 / 3, 4))
    assert aoi["mean"]["false_alarms"] == 0
    assert result["by_scope"]["aoi"]["n_pixels"] == 4
    assert result["by_scope"]["domain"]["n_pixels"] == 16


def test_brier_and_its_skill_scores() -> None:
    result = score_cycles([_cycle()], _truth())
    cell = _row(result, "domain", 5)["thresholds"]["20"]
    # p is 1 at A, B, D: wrong at C (miss) and D (false alarm) out of 16 pixels.
    assert cell["brier"] == pytest.approx(2 / 16)
    # Persistence's yes at A only is wrong at B and C.
    assert cell["brier_persistence"] == pytest.approx(2 / 16)
    assert cell["brier_skill_vs_persistence"] == pytest.approx(0.0)
    base = 3 / 16
    assert cell["brier_climatology"] == pytest.approx(round(base * (1 - base), 4))
    # 40 mm/h has no probability in this run: no Brier, and no reliability entry for it.
    assert _row(result, "domain", 5)["thresholds"]["40"]["brier"] is None
    assert "40" not in result["by_scope"]["domain"]["reliability"]


def test_pooling_two_cycles_sums_the_tables_and_counts_the_cycles() -> None:
    result = score_cycles([_cycle("run-a"), _cycle("run-b")], _truth())
    row = _row(result, "domain", 5)
    assert row["n_cycles"] == 2
    assert row["n_pixels"] == 32
    assert row["thresholds"]["20"]["mean"]["hits"] == 4
    assert row["thresholds"]["20"]["mean"]["csi"] == pytest.approx(0.5)


def test_the_horizon_is_the_lead_before_the_first_failure() -> None:
    result = score_cycles([_cycle()], _truth())
    domain = next(
        h
        for h in result["by_scope"]["domain"]["horizons"]
        if h["forecast"] == "mean" and h["threshold_mm_h"] == 20.0
    )
    assert domain["status"] == "found"
    assert domain["lead_min"] == 5
    assert domain["first_failure"]["lead_min"] == 10
    assert domain["first_failure"]["reasons"] == ["below_persistence", "below_floor"]
    assert domain["beats_persistence_leads_min"] == [5]
    # The headline is the AOI's: CSI 2/3 at +5 beats persistence's 1/3 and the floor.
    assert result["horizon"]["lead_min"] == 5
    assert result["horizon"]["forecast"] == "mean"


def test_leads_past_the_truth_window_are_not_scored() -> None:
    result = score_cycles([_cycle(n_steps=6)], _truth(n_frames=3))
    leads = [row["lead_min"] for row in result["by_scope"]["domain"]["by_lead"]]
    assert leads == [5, 10]
    assert result["cycles"][0]["n_leads"] == 6
    assert result["cycles"][0]["n_leads_scored"] == 2
    assert result["cycles"][0]["max_lead_scored_min"] == 10


def test_reliability_bins_count_every_scored_pixel() -> None:
    result = score_cycles([_cycle()], _truth())
    band = result["by_scope"]["domain"]["reliability"]["20"][0]
    assert (band["lead_from_min"], band["lead_to_min"]) == (5, 60)
    assert band["n"] == 32  # 16 pixels at +5 and 16 at +10
    top = band["bins"][-1]
    assert top["n"] == 3  # A, B, D at p = 1
    assert top["observed_frequency"] == pytest.approx(round(2 / 3, 4))
    bottom = band["bins"][0]
    assert bottom["n"] == 29
    # At +5 C rains with p = 0; at +10 A rains with p = 0.
    assert bottom["observed_frequency"] == pytest.approx(round(2 / 29, 4))


def _rows(*pairs: tuple[float | None, float | None]) -> list[dict]:
    return [
        {
            "lead_min": 5 * (k + 1),
            "n_cycles": 1,
            "thresholds": {"20": {"mean": {"csi": f}, "persistence": {"csi": p}}},
        }
        for k, (f, p) in enumerate(pairs)
    ]


def test_horizon_is_zero_when_the_first_lead_fails() -> None:
    horizon = skill_horizon(_rows((0.58, 0.63), (0.7, 0.4)))
    assert horizon["status"] == "found"
    assert horizon["lead_min"] == 0
    assert horizon["first_failure"]["reasons"] == ["below_persistence"]
    assert horizon["beats_persistence_leads_min"] == [10]


def test_horizon_stops_as_undetermined_where_nothing_was_forecast_or_seen() -> None:
    horizon = skill_horizon(_rows((0.8, 0.6), (None, 0.2), (0.9, 0.1)))
    assert horizon["status"] == "undetermined"
    assert horizon["lead_min"] == 5
    assert horizon["first_failure"]["reasons"] == ["no_event"]


def test_horizon_beyond_the_scored_range_and_the_floor() -> None:
    assert skill_horizon(_rows((0.9, 0.5), (0.8, 0.5)))["status"] == "beyond_scored_range"
    assert skill_horizon(_rows((0.9, 0.5), (0.8, 0.5)))["lead_min"] == 10
    below = skill_horizon(_rows((CSI_FLOOR - 0.01, 0.1)))
    assert below["first_failure"]["reasons"] == ["below_floor"]
    # A tie with persistence is not a failure.
    assert skill_horizon(_rows((0.6, 0.6)))["status"] == "beyond_scored_range"


def _cycle_forecasting(run_id: str, *cells: tuple[int, int]) -> CycleForecast:
    mean = np.stack([_field(*cells), _field()])
    return replace(_cycle(run_id), mean=mean, p50=mean.copy())


def test_the_spread_across_cycles_is_the_p10_p90_of_each_cycles_own_score() -> None:
    # Two cycles as in the hand count (domain CSI 0.5 at +5) and one perfect cycle (CSI 1.0).
    cycles = [_cycle("run-a"), _cycle("run-b"), _cycle_forecasting("run-c", A, B, C)]
    cell = _row(score_cycles(cycles, _truth()), "domain", 5)["thresholds"]["20"]
    csi = cell["spread"]["mean"]["csi"]
    assert csi["n"] == 3
    # Linear percentiles of [0.5, 0.5, 1.0]: p10 at position 0.2, p90 at position 1.8.
    assert csi["p10"] == pytest.approx(0.5)
    assert csi["p90"] == pytest.approx(0.9)
    # FAR per cycle is 1/3, 1/3 and 0 (the perfect cycle): p10 sits a fifth of the way up.
    assert cell["spread"]["mean"]["far"]["p10"] == pytest.approx(round(0.2 / 3, 4))
    assert cell["spread"]["mean"]["far"]["p90"] == pytest.approx(round(1 / 3, 4))
    assert set(cell["spread"]) == {"mean", "p50"}


def test_no_spread_is_quoted_over_fewer_than_three_cycles() -> None:
    cell = _row(score_cycles([_cycle("run-a"), _cycle("run-b")], _truth()), "domain", 5)
    csi = cell["thresholds"]["20"]["spread"]["mean"]["csi"]
    assert csi == {"n": 2, "p10": None, "p90": None}
    # At +10 the forecast says nothing and truth rains at A: POD 0 is defined, FAR is not.
    later = _row(score_cycles([_cycle("run-a")] * 3, _truth()), "domain", 10)
    spread = later["thresholds"]["20"]["spread"]["mean"]
    assert spread["pod"]["n"] == 3
    assert spread["far"] == {"n": 0, "p10": None, "p90": None}


def test_an_event_without_a_truth_field_says_so(tmp_path: Path) -> None:
    (tmp_path / "bundles" / "NO-TRUTH").mkdir(parents=True)
    result = event_rain_skill(
        "NO-TRUTH", runs_root=tmp_path / "runs", bundles_root=tmp_path / "bundles"
    )
    assert result["available"] is False
    assert "truth/rain.zarr" in result["reason"]
    # The fix is served once as data, never guessed from the prose by a screen.
    assert result["missing"] == "truth"
    assert result["command"] == "make bundle BUNDLE=NO-TRUTH"
    assert "make " not in result["reason"] and "`" not in result["reason"]


def test_an_event_with_truth_but_no_baked_rain_names_make_bake(tmp_path: Path) -> None:
    (tmp_path / "bundles" / "NO-RUNS" / "truth" / "rain.zarr").mkdir(parents=True)
    result = event_rain_skill(
        "NO-RUNS", runs_root=tmp_path / "runs", bundles_root=tmp_path / "bundles"
    )
    assert result["available"] is False
    assert result["missing"] == "runs"
    assert result["command"] == "make bake BUNDLE=NO-RUNS"
    assert "rain/quantiles.zarr" in result["reason"]
    assert "make " not in result["reason"] and "`" not in result["reason"]


# ------------------------------------------------------------------ the shipped demo cycles
REPO = Path(__file__).resolve().parents[3]
DEMO_BUNDLE = REPO / "bundles" / "MUM-2019-07-02"
DEMO_RUNS = REPO / "data" / "runs"


def _demo_available() -> bool:
    if not (DEMO_BUNDLE / "truth" / "rain.zarr").exists():
        return False
    return any(DEMO_RUNS.glob("MUM-20190702T*/rain/quantiles.zarr"))


@pytest.mark.skipif(not _demo_available(), reason="needs the MUM-2019-07-02 bundle and baked runs")
def test_the_demo_event_is_scored_from_its_artifacts() -> None:
    result = event_rain_skill("MUM-2019-07-02", runs_root=DEMO_RUNS, bundles_root=REPO / "bundles")
    assert result["available"] is True
    assert result["n_cycles"] >= 1
    # Persistence is the very analysis each nowcast started from: the recomputed Z-R matches.
    assert all(cycle["persistence"]["matches_cycle"] for cycle in result["cycles"])
    rows = result["by_scope"]["aoi"]["by_lead"]
    assert rows[0]["lead_min"] == 5
    counts = [row["n_cycles"] for row in rows]
    assert counts == sorted(counts, reverse=True)
    assert result["horizon"]["status"] in {"found", "undetermined", "beyond_scored_range"}
    assert result["units"]["rain_rate"] == "mm/h"
    json.dumps(result)  # serialisable as served
    assert rain_event_inputs(
        "MUM-2019-07-02", runs_root=DEMO_RUNS, bundles_root=REPO / "bundles"
    ) == rain_event_inputs("MUM-2019-07-02", runs_root=DEMO_RUNS, bundles_root=REPO / "bundles")

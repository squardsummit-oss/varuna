"""Re-pricing a plan the operator made by hand (SPEC.md 7.6 AC2; task P8.10).

A drag on the pump board used to leave the optimiser's benefit beside a plan the optimiser had
not made, and the board said the figure was stale. ``price_placements`` prices the moved plan
with the same model and the same arithmetic the greedy uses, which these tests pin: an unmoved
plan prices to the optimiser's own numbers, a second lorry at a junction buys only its marginal
drawdown, and a place the forecast keeps dry is priced at zero with the reason.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
from varuna_flash.model import FlashModel
from varuna_products.pumps import MAX_PLACEMENTS, build_pump_plan, price_placements

if TYPE_CHECKING:
    from pathlib import Path

N_STEPS = 24
SEGMENTS = ("S-001", "S-002")


def _pump(pid: str, capacity: float, lon: float, lat: float, status: str = "available") -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "asset_id": pid,
            "kind": "mobile_pump",
            "capacity_m3_per_h": capacity,
            "depot": f"{pid} depot",
            "status": status,
            "synthetic": True,
        },
    }


def _city(tmp_path: Path) -> Path:
    features = [
        _pump("P-01", 600.0, 72.841, 19.012),
        _pump("P-02", 400.0, 72.845, 19.019),
        _pump("P-03", 250.0, 72.857, 19.027, status="unavailable"),
    ]
    (tmp_path / "assets.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    return tmp_path


def _hotspots() -> list[dict[str, Any]]:
    return [
        {
            "hotspot_id": "MUM-HS-01",
            "name": "Hindmata junction",
            "lon": 72.841,
            "lat": 19.012,
            "segment_ids": list(SEGMENTS),
            "exposure": {"weight": 0.8},
            "depth_cm": [50.0] * N_STEPS,
        },
        {
            "hotspot_id": "MUM-HS-02",
            "name": "Dadar TT",
            "lon": 72.845,
            "lat": 19.019,
            "segment_ids": [],
            "exposure": {"weight": 0.6},
            "depth_cm": [10.0] * N_STEPS,
        },
    ]


def _model(gain: float) -> FlashModel:
    n = len(SEGMENTS)
    return FlashModel(
        segment_ids=SEGMENTS,
        k_steps=np.full(n, 1.0),
        gain=np.full(n, gain),
        drain_cm_per_step=np.zeros(n),
        beta_ref=np.full(n, 0.2),
        baseline_cm=np.zeros((N_STEPS, n)),
        n_training_runs=8,
        rmse_cm=5.7,
        csi_30cm=0.085,
        fitted_segments=n,
    )


@pytest.fixture
def no_emulator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("varuna_products.pumps.MODEL_PATHS", ())


@pytest.mark.parametrize("use_emulator", [True, False])
def test_the_optimisers_own_plan_prices_to_its_own_numbers(
    tmp_path: Path, no_emulator: None, use_emulator: bool
) -> None:
    city = _city(tmp_path)
    kwargs: dict[str, Any] = (
        {"model": _model(1.0), "rain_mm_h": [40.0] * N_STEPS} if use_emulator else {}
    )
    plan = build_pump_plan(_hotspots(), city, "RUN", **kwargs)
    assert plan["assignments"], "the fixture must give the optimiser something to do"
    placements = [(a["pump_id"], a["hotspot_id"]) for a in plan["assignments"]]
    priced = price_placements(_hotspots(), city, "RUN", placements, **kwargs)
    assert priced["benefit_model"] == plan["benefit_model"]
    by_pump = {p["pump_id"]: p for p in priced["placements"]}
    for a in plan["assignments"]:
        assert by_pump[a["pump_id"]]["minutes_saved"] == a["minutes_saved"]
        assert by_pump[a["pump_id"]]["eta_min"] == a["eta_min"]
    assert priced["total_minutes_saved"] == plan["total_minutes_saved"]


def test_a_second_pump_buys_only_its_marginal_drawdown(tmp_path: Path) -> None:
    """The emulator saturates: once the rain that ponded is gone, more pumping removes nothing."""
    city = _city(tmp_path)
    kwargs: dict[str, Any] = {"model": _model(0.05), "rain_mm_h": [20.0] * N_STEPS}
    one = price_placements(_hotspots(), city, "RUN", [("P-01", "MUM-HS-01")], **kwargs)
    two = price_placements(
        _hotspots(), city, "RUN", [("P-01", "MUM-HS-01"), ("P-02", "MUM-HS-01")], **kwargs
    )
    first, second = sorted(two["placements"], key=lambda p: p["eta_min"])
    assert first["minutes_saved"] == one["placements"][0]["minutes_saved"]
    target = two["targets"][0]
    assert target["minutes_saved"] == first["minutes_saved"] + second["minutes_saved"]
    assert target["minutes_before"] - target["minutes_after"] == target["minutes_saved"]
    assert two["benefit_model"] == "emulator"


def test_rates_add_on_the_bathtub(tmp_path: Path, no_emulator: None) -> None:
    city = _city(tmp_path)
    two = price_placements(_hotspots(), city, "RUN", [("P-01", "MUM-HS-01"), ("P-02", "MUM-HS-01")])
    assert two["benefit_model"] == "reduced_model"
    assert two["targets"][0]["minutes_saved"] >= max(p["minutes_saved"] for p in two["placements"])


def test_a_dry_place_saves_nothing_and_says_why(tmp_path: Path, no_emulator: None) -> None:
    priced = price_placements(_hotspots(), _city(tmp_path), "RUN", [("P-01", "MUM-HS-02")])
    placed = priced["placements"][0]
    assert placed["minutes_saved"] == 0 and "does not cross 45 cm" in placed["note"]
    assert priced["total_minutes_saved"] == 0


def test_withheld_unknown_and_doubled_pumps_are_refused(tmp_path: Path, no_emulator: None) -> None:
    priced = price_placements(
        _hotspots(),
        _city(tmp_path),
        "RUN",
        [("P-03", "MUM-HS-01"), ("P-99", "MUM-HS-01"), ("P-01", "MUM-HS-01"), ("P-01", "x")],
    )
    reasons = {r["pump_id"] + "|" + r["target_id"]: r["reason"] for r in priced["refused"]}
    assert "unavailable" in reasons["P-03|MUM-HS-01"]
    assert "not in this city's fleet" in reasons["P-99|MUM-HS-01"]
    assert "placed twice" in reasons["P-01|x"]
    assert [p["pump_id"] for p in priced["placements"]] == ["P-01"]


def test_the_request_is_bounded(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at most"):
        price_placements([], _city(tmp_path), "RUN", [("P-01", "x")] * (MAX_PLACEMENTS + 1))


def test_no_inventory_refuses_every_placement(tmp_path: Path) -> None:
    priced = price_placements(_hotspots(), tmp_path, "RUN", [("P-01", "MUM-HS-01")])
    assert priced["placements"] == [] and priced["refused"][0]["pump_id"] == "P-01"

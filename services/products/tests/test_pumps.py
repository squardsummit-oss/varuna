"""What the pump board is allowed to claim about a pump (SPEC.md 11.10, rule 6).

The number under test is "minutes above 45 cm avoided". SPEC.md 11.10 says it comes from
re-running the emulator with the pump's extra outflow; the bathtub model is the fallback for a
clone that has not fitted Flash-lite yet. These tests pin which of the two produced a number, so
the label on the board cannot drift away from the arithmetic behind it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import numpy as np
import pytest
from varuna_flash.model import FlashModel
from varuna_products.pumps import PUMP_THRESHOLD_CM, build_pump_plan, rain_for_run

if TYPE_CHECKING:
    from pathlib import Path

N_STEPS = 12
SEGMENTS = ("S-001", "S-002")

OVER_THRESHOLD_CM = 50.0
"""A street five centimetres over the bus threshold: shallow enough that one pump can clear it
within the window, so a benefit of zero is a statement about the model and not about the depth."""


def _city(tmp_path: Path, *, capacity: float = 600.0) -> Path:
    """A city root with one synthetic pump, at the same place as the hotspot below."""
    assets = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [72.841, 19.012]},
                "properties": {
                    "asset_id": "P-01",
                    "kind": "mobile_pump",
                    "capacity_m3_per_h": capacity,
                    "depot": "Test depot",
                    "status": "available",
                    "synthetic": True,
                },
            }
        ],
    }
    (tmp_path / "assets.geojson").write_text(json.dumps(assets), encoding="utf-8")
    return tmp_path


def _hotspot(depth_cm: list[float]) -> dict:
    return {
        "hotspot_id": "test-junction",
        "name": "Test junction",
        "lon": 72.841,
        "lat": 19.012,
        "segment_ids": list(SEGMENTS),
        "exposure": {"weight": 0.8},
        "depth_cm": depth_cm,
    }


def _model(*, gain: float, drain: float = 0.0) -> FlashModel:
    """A two-segment emulator whose ponding is whatever ``gain`` makes of the rain."""
    n = len(SEGMENTS)
    return FlashModel(
        segment_ids=SEGMENTS,
        k_steps=np.full(n, 1.0),
        gain=np.full(n, gain),
        drain_cm_per_step=np.full(n, drain),
        beta_ref=np.full(n, 0.2),
        baseline_cm=np.zeros((N_STEPS, n)),
        n_training_runs=8,
        rmse_cm=5.7,
        csi_30cm=0.085,
        fitted_segments=n,
    )


@pytest.fixture
def no_emulator(monkeypatch: pytest.MonkeyPatch) -> None:
    """No fitted emulator anywhere, so the plan has to fall back and say so."""
    monkeypatch.setattr("varuna_products.pumps.MODEL_PATHS", ())


def test_fallback_says_which_model_produced_the_number(tmp_path: Path, no_emulator: None) -> None:
    city = _city(tmp_path)
    plan = build_pump_plan([_hotspot([OVER_THRESHOLD_CM] * N_STEPS)], city, "TEST-RUN")

    assert plan["benefit_model"] == "reduced_model"
    assert plan["benefit_label"] == "Bathtub estimate, not a physics run"
    assert "emulator" not in plan
    assert [a["benefit_model"] for a in plan["assignments"]] == ["reduced_model"]


def test_emulator_prices_the_pump_and_carries_its_skill(tmp_path: Path) -> None:
    """The emulator path is taken when a model and the run's own storm are both available."""
    city = _city(tmp_path)
    plan = build_pump_plan(
        [_hotspot([OVER_THRESHOLD_CM] * N_STEPS)],
        city,
        "TEST-RUN",
        model=_model(gain=1.0),
        rain_mm_h=[30.0] * N_STEPS,
    )

    assert plan["benefit_model"] == "emulator"
    assert plan["benefit_label"] == "Flash-lite emulator re-run with the pump's outflow"
    # Provenance, because a benefit from a surrogate has to say how good the surrogate is.
    assert plan["emulator"] == {"rmse_cm": 5.7, "csi_30cm": 0.085, "n_training_runs": 8}
    assert [a["benefit_model"] for a in plan["assignments"]] == ["emulator"]


def test_emulator_benefit_saturates_where_the_bathtub_does_not(
    tmp_path: Path, no_emulator: None
) -> None:
    """The defect this replaces: a bathtub drains a street the rain never put water on.

    Same street, same pump. The bathtub lowers the street at the pump's rated rate until it is
    dry; the emulator lowers it only by the water the storm actually ponded there, and stops when
    that is gone. The second number is the one SPEC.md 11.10 asks for. The fixture empties the
    search path so the first call has to fall back; the second is handed a model directly.
    """
    city = _city(tmp_path)
    deep = [OVER_THRESHOLD_CM] * N_STEPS
    args = ([_hotspot(deep)], city, "TEST-RUN")

    bathtub = build_pump_plan(*args, model=None, rain_mm_h=None)
    emulator = build_pump_plan(*args, model=_model(gain=0.02), rain_mm_h=[10.0] * N_STEPS)

    assert bathtub["benefit_model"] == "reduced_model"
    assert emulator["benefit_model"] == "emulator"
    # A fraction of a centimetre of ponded rain cannot take a 50 cm street below 45.
    assert emulator["total_minutes_saved"] == 0
    assert bathtub["total_minutes_saved"] > 0


def test_the_plan_does_not_read_the_storm_out_of_the_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 8: the plan is a function of its arguments, not of what is already on disk.

    Reading `rain_aoi_mm_h` back from ``run.json`` inside `build_pump_plan` looks harmless and is
    not: a first bake finds no run directory and prices the pumps with the fallback, a re-bake
    over the top of it finds the rain it had just written and prices them with the emulator, and
    the same cycle writes two different plans. `rain_for_run` exists so a caller can ask for that
    on purpose.
    """
    runs = tmp_path / "data" / "runs" / "TEST-RUN"
    runs.mkdir(parents=True)
    (runs / "run.json").write_text(
        json.dumps({"rain_aoi_mm_h": [30.0] * N_STEPS}), encoding="utf-8"
    )
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path / "data"))

    city = _city(tmp_path)
    plan = build_pump_plan(
        [_hotspot([OVER_THRESHOLD_CM] * N_STEPS)], city, "TEST-RUN", model=_model(gain=1.0)
    )

    assert plan["benefit_model"] == "reduced_model"
    assert rain_for_run("TEST-RUN") == [30.0] * N_STEPS


def test_a_street_that_never_crosses_the_threshold_gets_no_pump(tmp_path: Path) -> None:
    city = _city(tmp_path)
    plan = build_pump_plan(
        [_hotspot([PUMP_THRESHOLD_CM - 1.0] * N_STEPS)],
        city,
        "TEST-RUN",
        model=_model(gain=1.0),
        rain_mm_h=[30.0] * N_STEPS,
    )

    assert plan["assignments"] == []
    assert plan["unassigned"] == []


def test_a_candidate_with_no_segment_the_emulator_knows_falls_back(tmp_path: Path) -> None:
    """Mixed rather than "emulator": half the plan came from the other model and must say so."""
    city = _city(tmp_path)
    stranger = _hotspot([OVER_THRESHOLD_CM] * N_STEPS)
    stranger["segment_ids"] = ["S-999"]  # not in the fitted model
    # No segments.parquet in this city root either, so the street lookup finds nothing.
    plan = build_pump_plan(
        [stranger], city, "TEST-RUN", model=_model(gain=1.0), rain_mm_h=[30.0] * N_STEPS
    )

    assert plan["benefit_model"] == "mixed"
    assert [a["benefit_model"] for a in plan["assignments"]] == ["reduced_model"]

"""D-02 — the pump plan is priced by the emulator, because the cycle hands over its storm.

SPEC.md 11.10 measures a pump's benefit as "minutes above 45 cm avoided", re-measured by
running the emulator with the pump's extra outflow. `build_pump_plan` has done that since P8.9
and takes the storm as an argument, deliberately, so a plan is a function of what it was given
rather than of what happens to be on disk (rule 8). `run_cycle` never passed it - so every
shipped `pump_plan.json` says ``benefit_model: "reduced_model"`` while the run beside it carries
the very hyetograph that would have driven the emulator.

Checked on the seam rather than by running a cycle: a Mumbai cycle is a minute of Twin.
"""

from __future__ import annotations

import inspect
import json
from typing import TYPE_CHECKING

import numpy as np
import pytest
from varuna_cycle.twin_cycle import (
    PUMP_BENEFIT_NOTES,
    aoi_hyetograph,
    pump_benefit_notes,
    run_cycle,
)
from varuna_flash.model import FlashModel
from varuna_products.pumps import build_pump_plan

if TYPE_CHECKING:
    from pathlib import Path

N_STEPS = 12
SEGMENTS = ("S-001", "S-002")
DEEP_CM = 50.0
"""Five centimetres over the bus threshold: shallow enough for one pump to matter."""


def _city(tmp_path: Path) -> Path:
    assets = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [72.841, 19.012]},
                "properties": {
                    "asset_id": "P-01",
                    "kind": "mobile_pump",
                    "capacity_m3_per_h": 600.0,
                    "depot": "Parel depot",
                    "status": "available",
                    "synthetic": True,
                },
            }
        ],
    }
    (tmp_path / "assets.geojson").write_text(json.dumps(assets), encoding="utf-8")
    return tmp_path


def _hotspot() -> dict:
    return {
        "hotspot_id": "hindmata",
        "name": "Hindmata junction",
        "lon": 72.841,
        "lat": 19.012,
        "segment_ids": list(SEGMENTS),
        "exposure": {"weight": 0.8},
        "depth_cm": [DEEP_CM] * N_STEPS,
    }


def _model() -> FlashModel:
    n = len(SEGMENTS)
    return FlashModel(
        segment_ids=SEGMENTS,
        k_steps=np.full(n, 1.0),
        gain=np.full(n, 1.0),
        drain_cm_per_step=np.zeros(n),
        beta_ref=np.full(n, 0.2),
        baseline_cm=np.zeros((N_STEPS, n)),
        n_training_runs=8,
        rmse_cm=5.7,
        csi_30cm=0.085,
        fitted_segments=n,
    )


# ---------------------------------------------------------------- the seam run_cycle uses
def test_run_cycle_hands_the_storm_to_the_pump_plan() -> None:
    """The one argument the defect was. Worth pinning only while the call site still exists."""
    source = inspect.getsource(run_cycle)

    assert "rain_aoi_mm_h = aoi_hyetograph(rain_cube)" in source
    assert "rain_mm_h=rain_aoi_mm_h" in source
    # And the same list reaches run.json, so a later `rain_for_run` reads back what the plan was
    # priced on rather than a second, separately rounded, computation of it.
    assert "rain_aoi_mm_h=rain_aoi_mm_h" in source
    assert "pump_benefit_notes(pump_plan)" in source


def test_the_hyetograph_is_the_series_the_run_keeps() -> None:
    rng = np.random.default_rng(2019)
    cube = rng.uniform(0.0, 80.0, size=(N_STEPS, 4, 5))

    series = aoi_hyetograph(cube)

    assert len(series) == N_STEPS
    assert series == [round(float(v), 3) for v in cube.mean(axis=(1, 2))]
    assert all(isinstance(v, float) for v in series)


def test_a_dry_cycle_still_has_a_rain_series() -> None:
    """Zeros are a storm that did not happen, not a missing storm: the emulator can price
    against them and will honestly find nothing to remove. Only an empty cube has no series."""
    assert aoi_hyetograph(np.zeros((N_STEPS, 3, 3))) == [0.0] * N_STEPS


# ---------------------------------------------------------------- which model priced the plan
def test_with_the_storm_the_benefit_is_the_emulator(tmp_path: Path) -> None:
    plan = build_pump_plan(
        [_hotspot()],
        _city(tmp_path),
        "TEST-RUN",
        5,
        None,
        None,
        model=_model(),
        rain_mm_h=aoi_hyetograph(np.full((N_STEPS, 3, 3), 30.0)),
    )

    assert plan["benefit_model"] == "emulator"
    assert plan["benefit_label"] == "Flash-lite emulator re-run with the pump's outflow"
    assert pump_benefit_notes(plan) == [PUMP_BENEFIT_NOTES["emulator"]]


def test_the_bathtub_is_reached_only_when_there_is_no_rain_series(tmp_path: Path) -> None:
    """Same city, same pump, same fitted emulator - only the storm withheld."""
    city = _city(tmp_path)
    args = ([_hotspot()], city, "TEST-RUN", 5, None, None)

    bathtub = build_pump_plan(*args, model=_model(), rain_mm_h=None)
    emulator = build_pump_plan(
        *args, model=_model(), rain_mm_h=aoi_hyetograph(np.full((N_STEPS, 3, 3), 30.0))
    )

    assert bathtub["benefit_model"] == "reduced_model"
    assert emulator["benefit_model"] == "emulator"
    # Not the same number: the bathtub drains water the storm never put there.
    assert bathtub["total_minutes_saved"] != emulator["total_minutes_saved"]


def test_the_note_names_the_model_and_says_what_it_cannot_see() -> None:
    assert pump_benefit_notes({"benefit_model": "emulator", "assignments": [{}]}) == [
        PUMP_BENEFIT_NOTES["emulator"]
    ]
    assert "bathtub" in PUMP_BENEFIT_NOTES["reduced_model"]
    assert "overstates" in PUMP_BENEFIT_NOTES["reduced_model"]
    assert "saturates" in PUMP_BENEFIT_NOTES["emulator"]


def test_a_cycle_that_sends_no_pump_claims_no_benefit_model() -> None:
    """A note about which model priced a number nobody computed would be noise."""
    assert pump_benefit_notes({"benefit_model": "reduced_model", "assignments": []}) == []
    assert pump_benefit_notes({"run_id": "TEST-RUN", "pumps": [], "assignments": []}) == []


def test_every_benefit_model_the_plan_can_publish_has_a_note() -> None:
    """`benefit_model` and the note are keyed on the same string, so neither can outlive the
    other silently."""
    from varuna_products.pumps import BENEFIT_LABELS

    assert set(PUMP_BENEFIT_NOTES) == set(BENEFIT_LABELS)


@pytest.mark.parametrize("model_key", sorted(PUMP_BENEFIT_NOTES))
def test_no_note_claims_a_physics_run(model_key: str) -> None:
    """Flash-lite is a reduced-order emulator (RMSE 5.7 cm, CSI 0.085); no note may call it the
    Twin (rule 6)."""
    note = PUMP_BENEFIT_NOTES[model_key]

    assert "physics run" not in note or "not a physics run" in note

"""``POST /v1/whatif`` on the emulator: every lever, every changed street, no closed loop.

A five-street run on a hand-built Flash-lite model, so each assertion is about arithmetic a
reader can follow rather than a 21,296-segment fit:

* a tide offset is neither refused nor applied - the answer is the other levers, and says the
  tide needs the Twin; the physics check says a tide scenario runs on the Twin;
* every street that moved by 0.5 cm or more is returned, including one the run had dry, the
  counts are counted from that list, and a scenario with no lever answers "Nothing changed"
  without loading the emulator;
* each hotspot in ``hotspots.json`` gets a before and after with minutes above 30 and 45 cm;
* the pump plan drains from each pump's arrival and is labelled a lower bound, and "clean top N"
  desilts the worst pipes of the run's learned blockage, re-joined so a street drops to its
  next-worst pipe.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import varuna_pulse.join
from fastapi.testclient import TestClient
from varuna_api.routers import whatif
from varuna_flash.model import FlashModel
from varuna_pulse.health import capacity_reduction_pct

RUN_ID = "TST-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"
CITY = "testcity"
N_STEPS = 12
SEGMENTS = ("S-A", "S-B", "S-C", "S-D", "S-E")
"""S-A is the junction's deep street, S-D is dry in the run (not in segments_wet)."""

PAIRS = pd.DataFrame(
    {
        "segment_id": ["S-A", "S-A", "S-B", "S-C", "S-D"],
        "edge_id": ["E1", "E2", "E3", "E4", "E5"],
    }
)
POSTERIOR = {"E1": 0.9, "E2": 0.7, "E3": 0.6, "E4": 0.3, "E5": 0.2}
"""Pulse's learned blockage per pipe. S-A's worst pipe is E1; its next-worst is E2."""


def _model() -> FlashModel:
    """Five identical cascades that pond under steady rain, as `services/flash/tests` builds."""
    n = len(SEGMENTS)
    return FlashModel(
        segment_ids=SEGMENTS,
        k_steps=np.ones(n),
        gain=np.ones(n),
        drain_cm_per_step=np.full(n, 4.0),
        beta_ref=np.full(n, 0.5),
        baseline_cm=np.zeros((N_STEPS, n)),
        n_training_runs=6,
        rmse_cm=5.7,
        csi_30cm=0.085,
        fitted_segments=n,
    )


MODEL = _model()


def _ramp(peak: float) -> list[float]:
    return [round(peak * min(1.0, (t + 1) / 6), 1) for t in range(N_STEPS)]


def _write(root: Path) -> None:
    data = root / "data"
    run = data / "runs" / RUN_ID
    run.mkdir(parents=True)
    (run / "run.json").write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "city": CITY,
                "bundle": "TST-BUNDLE",
                "cycle_ts": "2019-07-02T08:40:00+05:30",
                "n_steps": N_STEPS,
                "step_min": 5,
                "stage_ms": {"sky": 1200, "twin": 3400},
                "rain_aoi_mm_h": [10.0] * N_STEPS,
            }
        ),
        encoding="utf-8",
    )
    wet = {"S-A": _ramp(60.0), "S-B": _ramp(20.0), "S-C": _ramp(10.0), "S-E": _ramp(8.0)}
    (run / "segments_wet.json").write_text(
        json.dumps({"run_id": RUN_ID, "min_depth_cm": 5.0, "depth_cm": wet}), encoding="utf-8"
    )
    hotspots = [
        {
            "hotspot_id": "TST-HS-1",
            "name": "Test junction",
            "lon": 72.84,
            "lat": 19.01,
            "segment_ids": ["S-A", "S-B", "S-MISSING"],
            "depth_cm": _ramp(55.0),
            "peak_depth_cm": 55.0,
        },
        {
            "hotspot_id": "TST-HS-2",
            "name": "Unmodelled junction",
            "lon": 72.85,
            "lat": 19.02,
            "segment_ids": ["S-NOT-FITTED"],
            "depth_cm": _ramp(12.0),
            "peak_depth_cm": 12.0,
        },
    ]
    (run / "hotspots.json").write_text(json.dumps(hotspots), encoding="utf-8")
    features = [
        {
            "type": "Feature",
            "geometry": None,
            "properties": {
                "edge_id": edge,
                "street": None,
                "beta_mean": beta,
                "beta_sd": 0.1,
                "capacity_reduction_pct": float(capacity_reduction_pct(np.array([beta]))[0]),
            },
        }
        for edge, beta in POSTERIOR.items()
    ]
    (run / "drain_health.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    city = root / "city" / CITY
    city.mkdir(parents=True)
    pd.DataFrame(
        {"edge_id": list(POSTERIOR), "beta_mean": [0.2] * 5, "beta_sd": [0.1] * 5}
    ).to_parquet(city / "drain_edges.parquet")


def _plan(run: Path, *, far: bool = False) -> None:
    """A pump plan: one pump at the junction, one on a street, one arriving after the window."""
    pumps = [
        {"pump_id": "P-01", "capacity_m3_per_h": 600.0, "lon": 72.84, "lat": 19.01},
        {"pump_id": "P-02", "capacity_m3_per_h": 400.0, "lon": 72.86, "lat": 19.03},
        # About 60 km away: at the board's 18 km/h it arrives long after a one-hour window.
        {"pump_id": "P-03", "capacity_m3_per_h": 400.0, "lon": 73.40, "lat": 19.01},
    ]
    assignments = [
        {
            "hotspot_id": "TST-HS-1",
            "hotspot_name": "Test junction",
            "lon": 72.84,
            "lat": 19.01,
            "pump_id": "P-01",
            "capacity_m3_per_h": 600.0,
            "eta_min": 0,
        },
        {
            "hotspot_id": "street:Beta Marg",
            "hotspot_name": "Beta Marg",
            "lon": 72.86,
            "lat": 19.03,
            "pump_id": "P-02",
            "capacity_m3_per_h": 400.0,
            "eta_min": 0,
        },
    ]
    if far:
        assignments.append(
            {
                "hotspot_id": "street:Gamma Road",
                "hotspot_name": "Gamma Road",
                "lon": 72.84,
                "lat": 19.01,
                "pump_id": "P-03",
                "capacity_m3_per_h": 400.0,
                "eta_min": 190,
            }
        )
    (run / "pump_plan.json").write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "inventory": "synthetic",
                "benefit_model": "emulator",
                "pumps": pumps,
                "assignments": assignments,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    _write(tmp_path)
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VARUNA_CITY_DIR", str(tmp_path / "city"))
    monkeypatch.setattr(whatif, "_model", lambda: MODEL)
    monkeypatch.setattr(varuna_pulse.join, "segment_edges", lambda city, **_: PAIRS.copy())
    monkeypatch.setattr(
        whatif,
        "_street_names",
        lambda city: {
            "S-A": "Alpha Road",
            "S-B": "Alpha Road",
            "S-C": "Beta Marg",
            "S-E": "Gamma Road",
        },
    )
    whatif._BETA_CACHE.clear()
    whatif._TOP_CLEAN_CACHE.clear()
    yield tmp_path / "data" / "runs" / RUN_ID
    whatif._BETA_CACHE.clear()
    whatif._TOP_CLEAN_CACHE.clear()


def _post(client: TestClient, path: str = "/v1/whatif", **body: Any) -> dict[str, Any]:
    res = client.post(path, json={"run_id": RUN_ID, **body})
    assert res.status_code == 200, res.text
    return res.json()


# ------------------------------------------------------------------ (a) no closed loop on tide
def test_a_tide_offset_is_left_out_of_the_emulator_answer_not_refused(
    run: Path, client: TestClient
) -> None:
    with_tide = _post(client, rain_scale=1.5, tide_offset_m=0.5)
    without = _post(client, rain_scale=1.5)

    tide = with_tide["tide"]
    assert tide["needs_twin"] is True
    assert tide["requested_m"] == 0.5 and tide["applied_m"] == 0.0
    assert tide["twin_endpoint"] == "/v1/whatif/twin"
    assert "not in this answer" in tide["message"]
    assert with_tide["summary"].endswith("Tide not included: it runs on the Twin.")
    # The rain answer is the same answer, tide or no tide: the offset was left out, not guessed.
    assert with_tide["segments"] == without["segments"]
    assert with_tide["hotspots"] == without["hotspots"]
    assert without["tide"]["needs_twin"] is False


def test_the_physics_check_says_a_tide_scenario_runs_on_the_twin(
    run: Path, client: TestClient
) -> None:
    body = _post(client, "/v1/whatif/physics-check", rain_scale=1.3, tide_offset_m=0.5)

    assert body["runs_on_twin"] is True
    assert body["agrees"] is None and body["max_diff_cm"] is None
    assert body["summary"].startswith("This scenario runs on the Twin")
    assert "physics" in body["summary"]
    job = body["twin_job"]
    assert job["endpoint"] == "/v1/whatif/twin"
    assert job["body"] == {
        "run_id": RUN_ID,
        "rain_scale": 1.3,
        "tide_offset_m": 0.5,
        "cleaned_segments": [],
    }
    assert job["cached"] is None
    # No comparison run is cached for this run yet, so the job runs the Twin twice.
    assert job["expected_ms"] == 1200 + 2 * 3400
    assert "twice" in job["expected_from"]


def test_the_tide_check_names_the_levers_the_twin_job_leaves_out(
    run: Path, client: TestClient
) -> None:
    body = _post(
        client,
        "/v1/whatif/physics-check",
        rain_scale=1.3,
        tide_offset_m=0.5,
        pump_plan=True,
        clean_top=True,
    )
    assert body["runs_on_twin"] is True
    assert body["levers_not_checked"] == ["pump_plan", "clean_top"]
    # The job body carries them, so the Twin job names them in its own `levers_left_out`
    # rather than answering as if they had never been asked for.
    assert body["twin_job"]["body"]["pump_plan"] is True
    assert body["twin_job"]["body"]["clean_top"] is True
    assert any("no pump sink" in note for note in body["notes"])


# --------------------------------------------------------------- (b) every changed street
def test_every_changed_street_is_returned_and_counted_from_that_list(
    run: Path, client: TestClient
) -> None:
    body = _post(client, rain_scale=2.0)

    rows = body["segments"]
    assert rows, "doubling the rain moved nothing"
    assert all(abs(r["delta_cm"]) >= 0.5 for r in rows)
    assert body["n_changed"] == len(rows)
    assert body["n_worse"] + body["n_improved"] == body["n_changed"]
    assert body["nothing_changed"] is False
    # Every street got deeper, so the sentence names only that side, never "0 shallower".
    assert body["n_improved"] == 0
    assert body["summary"] == f"{body['n_worse']} segments deeper, each by 0.5 cm or more."
    # S-D is not in the run's forecast (below 5 cm) and the emulator puts water on it: it is
    # returned, with no before depth rather than an invented one.
    dry = next(r for r in rows if r["segment_id"] == "S-D")
    assert dry["before_cm"] is None and dry["after_cm"] == dry["delta_cm"] > 0
    assert body["n_dry_before"] == sum(1 for r in rows if r["before_cm"] is None) >= 1
    # Every street of this model ponds under doubled rain, and all five are returned: there is
    # no cap, so nothing a map would draw is missing from the list the counts come from.
    assert {r["segment_id"] for r in rows} == set(SEGMENTS)
    # Largest change first, with the street named for the table.
    deltas = [abs(r["delta_cm"]) for r in rows]
    assert deltas == sorted(deltas, reverse=True)
    names = {r["segment_id"]: r["name"] for r in body["largest_changes"]}
    assert names["S-A"] == "Alpha Road" and names["S-D"] is None
    # `name` stays OSM's; `display_name` is what the table prints, a string for every row.
    shown = {r["segment_id"]: r["display_name"] for r in body["largest_changes"]}
    assert shown["S-A"] == "Alpha Road"
    assert all(isinstance(v, str) and v for v in shown.values())
    assert not any("nnamed" in v for v in shown.values())
    assert shown["S-D"].startswith("Road in ")


def test_a_scenario_with_no_lever_is_nothing_changed_without_the_emulator(
    run: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse() -> FlashModel:
        raise AssertionError("the emulator was loaded for a scenario of nothing")

    monkeypatch.setattr(whatif, "_model", refuse)
    body = _post(client, rain_scale=1.0, tide_offset_m=0.3)

    assert body["nothing_changed"] is True
    assert body["summary"] == "Nothing changed. Tide not included: it runs on the Twin."
    assert body["segments"] == [] and body["n_changed"] == 0
    # Every hotspot of the run is still tabled, each unchanged, from its own series: an empty
    # table beside "Nothing changed" would say the run has no hotspots.
    rows = {r["hotspot_id"]: r for r in body["hotspots"]}
    assert set(rows) == {"TST-HS-1", "TST-HS-2"}
    assert [r["hotspot_id"] for r in body["hotspots"]] == ["TST-HS-1", "TST-HS-2"]
    for row in rows.values():
        assert row["after_cm"] == row["before_cm"] and row["delta_cm"] == 0.0
        assert row["minutes_above_after"] == row["minutes_above_before"]
    assert rows["TST-HS-1"]["before_cm"] == 55.0
    assert rows["TST-HS-1"]["minutes_above_before"] == {"30": 45, "45": 40}


UI_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "command"
    / "lib"
    / "api"
    / "__tests__"
    / "whatif-emulator.fixture.json"
)
"""The bodies the console's what-if tests answer with. ``nothing`` is pinned to this handler."""


def test_the_consoles_nothing_fixture_is_this_handlers_body(
    run: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lab's "Nothing changed" state is tested against the body the handler really sends.

    Set ``VARUNA_WRITE_UI_FIXTURES=1`` to rewrite the fixture from the handler instead, then run
    ``npx prettier --write`` on it (from ``apps/command``), which the web lint checks.
    """
    import os

    monkeypatch.setattr(whatif, "_model", lambda: pytest.fail("the emulator was loaded"))
    body = _post(client, rain_scale=1.0, tide_offset_m=0.0)
    body["ms"] = 0.0
    fixture = json.loads(UI_FIXTURE.read_text(encoding="utf-8"))
    if os.environ.get("VARUNA_WRITE_UI_FIXTURES") == "1":
        fixture["nothing"] = body
        UI_FIXTURE.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    assert fixture["nothing"] == body


def test_a_scenario_that_moves_nothing_says_so(run: Path, client: TestClient) -> None:
    """A lever that is set but moves no street by 0.5 cm is also "Nothing changed"."""
    body = _post(client, rain_scale=1.002)
    assert body["n_changed"] == len(body["segments"]) == 0
    assert body["nothing_changed"] is True
    assert body["summary"] == "Nothing changed."


# ---------------------------------------------------------------- (c) per-hotspot rows
def test_each_hotspot_has_before_after_and_minutes_above(run: Path, client: TestClient) -> None:
    body = _post(client, rain_scale=1.5)

    rows = {r["hotspot_id"]: r for r in body["hotspots"]}
    assert set(rows) == {"TST-HS-1"}
    assert body["hotspots_not_modelled"] == ["Unmodelled junction"]
    row = rows["TST-HS-1"]
    assert row["name"] == "Test junction"
    assert row["before_cm"] == 55.0
    assert row["after_cm"] > row["before_cm"]
    assert row["delta_cm"] == pytest.approx(row["after_cm"] - row["before_cm"], abs=0.051)
    assert row["segments"] == 2 and row["segments_missing"] == 1
    assert set(row["minutes_above_before"]) == set(row["minutes_above_after"]) == {"30", "45"}
    # Counted on the run's own series: ramp to 55 cm over six 5-minute steps, then held.
    before = _ramp(55.0)
    assert row["minutes_above_before"]["45"] == 5 * sum(1 for v in before if v > 45)
    assert row["minutes_above_after"]["45"] >= row["minutes_above_before"]["45"]


# ------------------------------------------------------------------------ (d) the levers
def test_the_pump_plan_drains_from_arrival_and_is_a_lower_bound(
    run: Path, client: TestClient
) -> None:
    _plan(run, far=True)
    body = _post(client, pump_plan=True)

    lever = body["levers"]["pump_plan"]
    assert lever["applied"] is True
    assert lever["label"].startswith("Lower bound")
    assert lever["inventory"] == "synthetic"
    placed = {a["pump_id"]: a for a in lever["assignments"]}
    assert set(placed) == {"P-01", "P-02"}
    assert placed["P-01"]["segments"] == 2  # the register's S-A and S-B; S-MISSING is not fitted
    assert placed["P-02"]["segments"] == 1  # every segment named Beta Marg
    assert lever["arrives_after_window"] == ["Gamma Road"]
    assert lever["segments_drained"] == 3
    assert any("Lower bound" in note for note in body["notes"])
    # A pump only removes water: nothing got deeper, and the pumped streets got shallower.
    assert body["n_worse"] == 0 and body["n_improved"] >= 1
    n = body["n_improved"]
    assert body["summary"] == (
        f"{n} segment{'' if n == 1 else 's'} shallower, each by 0.5 cm or more."
    )
    moved = {r["segment_id"] for r in body["segments"]}
    assert moved <= {"S-A", "S-B", "S-C"}
    hotspot = body["hotspots"][0]
    assert hotspot["after_cm"] < hotspot["before_cm"]
    assert hotspot["minutes_above_after"]["45"] <= hotspot["minutes_above_before"]["45"]


def test_the_pump_lever_says_why_when_the_run_has_no_plan(run: Path, client: TestClient) -> None:
    body = _post(client, pump_plan=True)
    lever = body["levers"]["pump_plan"]
    assert lever["applied"] is False
    assert "pump_plan.json" in lever["reason"]
    assert body["nothing_changed"] is True


def test_clean_top_desilts_the_worst_learned_pipes_and_rejoins(
    run: Path, client: TestClient
) -> None:
    one = _post(client, clean_top=1)
    lever = one["levers"]["clean_top"]
    assert lever["applied"] is True
    assert lever["label"] == "Top 1 pipe by learned blockage, city-wide"
    assert [p["edge_id"] for p in lever["pipes"]] == ["E1"]
    assert lever["pipes"][0]["beta_before"] == 0.9
    assert lever["segments_changed"] == 1
    # S-A's worst pipe is gone, so S-A runs at its next-worst (E2, 0.7): better, but not as much
    # as cleaning the street outright to 0.05.
    by_top = {r["segment_id"]: r["delta_cm"] for r in one["segments"]}
    outright = {
        r["segment_id"]: r["delta_cm"] for r in _post(client, cleaned_segments=["S-A"])["segments"]
    }
    assert set(by_top) == {"S-A"}
    assert outright["S-A"] < by_top["S-A"] < 0.0

    fourteen = _post(client, clean_top=True)["levers"]["clean_top"]
    assert fourteen["label"] == "Top 14 pipes by learned blockage, city-wide"
    # Only five pipes are learned in this run, so five are cleaned, not fourteen invented ones.
    assert [p["edge_id"] for p in fourteen["pipes"]] == ["E1", "E2", "E3", "E4", "E5"]


# ------------------------------------------------------------------------------ refusals
@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"rain_scale": 2.5}, "out_of_range"),
        ({"rain_scale": "much"}, "bad_scenario"),
        ({"clean_top": 99}, "out_of_range"),
        ({"cleaned_segments": ["MUM-E035757"]}, "unknown_segments"),
    ],
)
def test_levers_outside_their_range_are_refused(
    run: Path, client: TestClient, body: dict[str, Any], code: str
) -> None:
    res = client.post("/v1/whatif", json={"run_id": RUN_ID, **body})
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == code

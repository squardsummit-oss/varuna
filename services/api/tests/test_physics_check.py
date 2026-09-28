"""``POST /v1/whatif/physics-check`` on a real baked run (task P7.8, SPEC.md 7.7, 14).

These tests run the coupled Twin twice on a 33 x 33 cell crop, so they need the Mumbai city grid
and a baked run and are skipped without them - a clean clone has neither, which is the same
arrangement the route tests use for `make city CITY=mumbai`.

What is asserted is what the endpoint promises rather than what it happened to measure. The
disagreement between the emulator and the physics is the *subject* of this endpoint: pinning it
to a value would make an honest re-bake fail a test for telling the truth. The cost against
section 14's budget is reported by the response itself and is checked here only for being
present and self-consistent, because it swings by nearly an order of magnitude with what else is
running on this machine: measured on 2026-09-24 with twelve python processes, the first call in
a process took 19.4-22.6 s (cold city load and a Numba compile) and the next four took
2.4-4.7 s, while the same crop timed 33-37 s a run earlier the same evening under another
job's load. These tests run cold by construction, so a duration assertion here would be a
coin toss.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[3]
CITY = REPO / "city" / "mumbai"
RUNS = REPO / "data" / "runs"


def _baked_run() -> str | None:
    """The newest Mumbai run that carries everything the check reads."""
    found = sorted(
        p.parent.name
        for p in RUNS.glob("MUM-*/run.json")
        if (p.parent / "segments_wet.json").is_file()
        and (p.parent / "hotspots.json").is_file()
        and json.loads(p.read_text(encoding="utf-8")).get("rain_aoi_mm_h")
    )
    return found[-1] if found else None


pytestmark = pytest.mark.skipif(
    not (CITY / "dem_conditioned.tif").is_file() or _baked_run() is None,
    reason="needs `make city CITY=mumbai` and a baked Mumbai run under data/runs",
)


@pytest.fixture(scope="module")
def run_id() -> str:
    found = _baked_run()
    assert found is not None
    return found


@pytest.fixture(scope="module")
def checked(run_id: str) -> dict:
    """One scenario answered once, shared by the tests that read different parts of it.

    Module-scoped because two coupled Twin runs is the expensive thing here; re-running them per
    assertion would put minutes on the suite for no extra coverage.
    """
    from varuna_api.main import create_app

    with TestClient(create_app()) as client:
        res = client.post("/v1/whatif/physics-check", json={"run_id": run_id, "rain_scale": 1.3})
    assert res.status_code == 200, res.text
    return res.json()


def test_it_reports_a_disagreement_at_a_named_junction(checked: dict) -> None:
    """Section 7.7's sentence, with a hotspot the run actually ranks behind it."""
    assert checked["method"] == "twin_crop_delta"
    assert checked["hotspots"], "no hotspot fell inside the window"
    assert checked["summary"].startswith("Emulator vs physics: max difference ")
    assert checked["max_diff_hotspot"] == checked["hotspots"][0]["name"]
    assert checked["max_diff_cm"] == checked["hotspots"][0]["diff_cm"]
    for row in checked["hotspots"]:
        # The reported difference is the two deltas' difference and nothing else, so a reader can
        # recompute it from the columns beside it.
        expected = abs(row["emulator_delta_cm"] - row["twin_delta_cm"])
        assert row["diff_cm"] == pytest.approx(expected, abs=0.011)
    assert checked["agrees"] == (checked["max_diff_cm"] <= checked["tolerance_cm"])


def test_the_window_is_small_and_says_what_is_outside_it(checked: dict) -> None:
    """The crop is the whole reason this endpoint can answer, so it is in the response.

    Mumbai's top hotspots are kilometres apart - Hindmata and Bandra Talao are about 5 km - so
    one 990 m window holds one of them. The ones it does not hold are named rather than silently
    dropped, because "max difference 3.3 cm" over a list of one reads very differently from the
    same sentence over a list of six.
    """
    window = checked["window"]
    assert window["cells"] == [33, 33]
    assert window["size_m"] == 990
    assert window["nodes"] > 0 and window["edges"] > 0
    assert window["centre_hotspot"]
    named = set(checked["hotspots_outside_window"])
    inside = {row["name"] for row in checked["hotspots"]}
    assert not (named & inside), "a hotspot reported as both checked and outside"


def test_the_cost_and_the_budget_are_both_in_the_response(checked: dict) -> None:
    """Section 14 gives this 10 s. Whether it made it is measured per call, never assumed."""
    assert checked["budget_ms"] == 10_000
    assert checked["twin_ms"] > 0
    assert checked["ms"] >= checked["twin_ms"], "the Twin cannot cost more than the whole call"
    assert checked["within_budget"] == (checked["ms"] <= checked["budget_ms"])


def test_the_crop_says_how_far_its_own_baseline_sits_from_the_run(checked: dict) -> None:
    """Deltas are compared because levels cannot be, and the response shows why.

    A 990 m crop cuts off contributing area, so its absolute depth is not the city's. Both
    numbers are carried per hotspot so a reader can see the gap instead of taking the crop's
    level for a forecast.
    """
    for row in checked["hotspots"]:
        assert row["twin_crop_before_cm"] >= 0.0
        assert row["run_peak_cm"] is not None
    assert any("Deltas are compared, not levels" in note for note in checked["notes"])
    assert set(checked["mass_balance"]) == {"baseline", "scenario", "budget"}
    assert checked["mass_balance"]["budget"] == 1e-3


def test_an_unchanged_scenario_disagrees_with_the_physics_by_nothing(run_id: str) -> None:
    """The identity case: same rain, same pipes, so both models must report a zero delta.

    This is the test that would catch the crop being rebuilt differently between the two runs -
    a different blockage vector, a different rain slice, a stray seed - because any of those
    would make the Twin's "no change" non-zero while the emulator's stayed at zero.
    """
    from varuna_api.main import create_app

    with TestClient(create_app()) as client:
        res = client.post("/v1/whatif/physics-check", json={"run_id": run_id})
    assert res.status_code == 200, res.text
    body = res.json()
    for row in body["hotspots"]:
        assert row["emulator_delta_cm"] == pytest.approx(0.0, abs=0.01)
        assert row["twin_delta_cm"] == pytest.approx(0.0, abs=0.01)
    assert body["max_diff_cm"] == pytest.approx(0.0, abs=0.02)
    assert body["agrees"] is True


def test_a_tide_offset_says_the_scenario_runs_on_the_twin(run_id: str) -> None:
    """The Twin answers a tide scenario; the emulator cannot, so there is nothing to check.

    It used to be a 422 that sent the user back to the what-if, which sent them here. Now the
    check says the scenario runs on the full-city Twin and hands over the job to start - and
    still publishes no crop level, because a 990 m crop with free outfalls at its edge cannot see
    the sea.
    """
    from varuna_api.main import create_app

    with TestClient(create_app()) as client:
        res = client.post("/v1/whatif/physics-check", json={"run_id": run_id, "tide_offset_m": 0.5})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["runs_on_twin"] is True
    assert body["agrees"] is None
    assert body["hotspots"] == []
    assert body["twin_job"]["endpoint"] == "/v1/whatif/twin"
    assert body["twin_job"]["body"]["tide_offset_m"] == 0.5
    assert body["levers_not_checked"] == []


def test_clean_top_is_checked_in_both_models_and_the_pump_plan_is_named(run_id: str) -> None:
    """The check runs the what-if's levers, or says which one it could not run.

    "Clean top 14" desilts the same pipes in the emulator's re-join and in the crop's graph, so
    the comparison is of one scenario. The pump plan has no sink in the Twin, so neither model
    runs it here and the response names it rather than comparing a scenario with pumps against
    one without.
    """
    from varuna_api.main import create_app

    with TestClient(create_app()) as client:
        res = client.post(
            "/v1/whatif/physics-check",
            json={"run_id": run_id, "clean_top": True, "pump_plan": True},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    top = body["clean_top"]
    assert top is not None and top["label"] == "Top 14 pipes by learned blockage, city-wide"
    if top["applied"]:
        assert {p["edge_id"] for p in top["pipes"]} <= set(body["cleaned_edges"])
    # The fourteen worst pipes are city-wide and the window is 990 m: how many it holds is said,
    # because a check that agrees about pipes it cannot see has checked nothing.
    inside = body["window"]["cleaned_edges_inside"]
    assert 0 <= inside <= len(body["cleaned_edges"])
    if body["cleaned_edges"] and inside == 0:
        assert any("None of the" in note and "inside the window" in note for note in body["notes"])
    assert body["levers_not_checked"] == ["pump_plan"]
    assert any("pump plan is not in this check" in note for note in body["notes"])


def test_drain_edge_ids_are_refused_the_way_the_what_if_refuses_them(run_id: str) -> None:
    """Both endpoints take road-segment ids, so both must reject the drain vocabulary alike."""
    from varuna_api.main import create_app

    with TestClient(create_app()) as client:
        res = client.post(
            "/v1/whatif/physics-check",
            json={"run_id": run_id, "cleaned_segments": ["MUM-E035757"]},
        )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "unknown_segments"


def test_the_route_is_no_longer_a_stub_in_the_contract() -> None:
    """SPEC.md 12 lists the path; it must now answer rather than declare a 501 as its only
    documented success."""
    from varuna_api.main import create_app

    with TestClient(create_app()) as client:
        doc = client.get("/openapi.json").json()
    operation = doc["paths"]["/v1/whatif/physics-check"]["post"]
    assert "200" in operation["responses"]
    assert "Twin" in operation["summary"]

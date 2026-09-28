"""``POST /v1/whatif/twin``: a scenario on the full-city Twin, as a job (the tide lever).

The real case is a 45-75 s Mumbai run, so these tests run the job on a 12 x 12 synthetic city
with a tidal outfall: the same `run_twin`, the same segment sampler and the same wet-segment
writer the bake uses, with the city loader replaced. What they pin is what the endpoint promises:

* a scenario of nothing reproduces the run it is compared with - here exactly, because the
  baseline was written by the same sampler and rounding from a Twin on the same inputs;
* the job's lifecycle: 202 with an estimate from the run's own stage timings, a step count that
  reaches ``n``, ``done`` with the result, and a GET that still answers afterwards;
* the cache: memory, then disk, then the shipped read-only copy, each named in the answer, and
  none of them served once ``run.json`` changes;
* the refusals: the host gate, a lever out of range, a tide offset on a bundle with no tide, a
  second heavy run while one holds the machine, and a job id nobody issued.

A slow-marked test runs the real Mumbai case when the city and a baked run are present.
"""

from __future__ import annotations

import importlib.util
import json
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from varuna_api.routers import whatif

REPO = Path(__file__).resolve().parents[3]
RUN_ID = "TST-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"
BUNDLE = "TST-BUNDLE"
N_STEPS = 6
SHAPE = (12, 12)


def _runner_helpers() -> Any:
    """The Twin's own test fixtures (terrain, tidal network, rain), loaded by path."""
    spec = importlib.util.spec_from_file_location(
        "_twin_test_runner", REPO / "services" / "twin" / "tests" / "test_runner.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPERS = _runner_helpers()


def _synthetic_inputs() -> whatif._ScenarioInputs:
    from varuna_twin.types import TideSeries

    terrain = HELPERS._make_terrain(shape=SHAPE)
    network = HELPERS._make_network(terrain, n_nodes=6, has_tidal_outfall=True)
    tide = TideSeries(
        times=(HELPERS.T0, HELPERS.T0 + timedelta(minutes=30)),
        stage_m=np.array([1.5, 2.4]),
        source="synthetic test tide (illustrative)",
    )
    rain = HELPERS._make_rain_cube(n_steps=N_STEPS, shape=SHAPE, peak_mm_h=400.0)
    rows, cols = SHAPE
    # One street per grid row, every cell of the row within its buffer.
    ids = tuple(f"S{r:02d}-000" for r in range(rows))
    offsets = np.arange(0, rows * cols + 1, cols, dtype=np.int64)
    cells = np.arange(rows * cols, dtype=np.int64)
    return whatif._ScenarioInputs(
        terrain=terrain,
        network=network,
        tide=tide,
        rain_mm_h=rain,
        index=(ids, offsets, cells),
        rain_ms=0,
        rain_cached=True,
    )


INPUTS = _synthetic_inputs()


def _write_run(root: Path) -> Path:
    """A run directory whose segment forecast is the Twin's own answer on the synthetic city."""
    from varuna_products import segment_table
    from varuna_products.depth import write_wet_segments
    from varuna_twin.runner import run_twin
    from varuna_twin.types import TwinInputs

    twin = run_twin(
        TwinInputs(
            terrain=INPUTS.terrain,
            network=INPUTS.network,
            rain_mm_h=INPUTS.rain_mm_h,
            t0=HELPERS.T0,
            step_min=5,
            tide=INPUTS.tide,
        )
    )
    path = root / "runs" / RUN_ID
    path.mkdir(parents=True)
    depth = segment_table.sample_segments(twin.depth_m, INPUTS.index)
    write_wet_segments(path, depth, INPUTS.index[0], twin.times, RUN_ID)
    meta = {
        "run_id": RUN_ID,
        "city": "testcity",
        "bundle": BUNDLE,
        "cycle_ts": HELPERS.T0.isoformat(),
        "n_steps": N_STEPS,
        "step_min": 5,
        "stage_ms": {"sky": 1200, "twin": 3400},
        "rain_aoi_mm_h": [round(float(v), 3) for v in INPUTS.rain_mm_h.mean(axis=(1, 2))],
        "mass_balance_err": float(twin.mass_balance.error_fraction),
        "mass_balance_ledger": {"volume_in_m3": float(twin.mass_balance.volume_in_m3)},
    }
    (path / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    hotspots = [
        {
            "hotspot_id": "TST-HS-1",
            "name": "Test junction",
            "lon": 72.84,
            "lat": 19.01,
            "segment_ids": ["S00-000", "S01-000", "S99-999"],
            "peak_depth_cm": 10.0,
        }
    ]
    (path / "hotspots.json").write_text(json.dumps(hotspots), encoding="utf-8")
    return path


@pytest.fixture
def twin_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    data = tmp_path / "data"
    run = _write_run(data)
    bundles = tmp_path / "bundles"
    (bundles / BUNDLE).mkdir(parents=True)
    (bundles / BUNDLE / "tide.csv").write_text("ts,stage_m,source\n", encoding="utf-8")
    (bundles / "NO-TIDE").mkdir(parents=True)
    monkeypatch.setenv("VARUNA_DATA_DIR", str(data))
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(bundles))
    monkeypatch.delenv(whatif.TWIN_JOB_ENV, raising=False)
    monkeypatch.setattr(whatif, "_load_scenario_inputs", lambda meta: INPUTS)
    monkeypatch.setattr(whatif, "_missing_sources", lambda city, bundle: None)
    monkeypatch.setattr(
        whatif,
        "_segment_edge_pairs",
        lambda city: pd.DataFrame(
            {"segment_id": ["S03-000", "S03-000", "S04-000"], "edge_id": ["E0", "E1", "E2"]}
        ),
    )
    # Every street of the synthetic city is a road segment, whether or not a pipe runs under it.
    monkeypatch.setattr(whatif, "_known_segments", lambda city: set(INPUTS.index[0]))
    monkeypatch.setattr(whatif, "_shipped_cache_root", lambda: tmp_path / "demo" / "whatif")
    whatif._MEMORY.clear()
    whatif._JOBS.clear()
    whatif._RAIN_CACHE.clear()
    whatif._BASELINES.clear()
    yield run
    # A job thread outlives a failed assertion; let it finish while the environment still points
    # at the temporary data dir, or it stores its answer in the developer's real data/whatif.
    deadline = time.monotonic() + 120.0
    while any(job.running for job in list(whatif._JOBS.values())):
        assert time.monotonic() < deadline, "a what-if job thread did not finish"
        time.sleep(0.1)
    whatif._MEMORY.clear()
    whatif._JOBS.clear()
    whatif._BASELINES.clear()


def _finish(client: TestClient, job: dict[str, Any], timeout_s: float = 180.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while job["state"] == "running":
        assert time.monotonic() < deadline, f"job still running: {job}"
        time.sleep(0.1)
        res = client.get(f"/v1/whatif/twin/{job['job_id']}")
        assert res.status_code == 200, res.text
        job = res.json()
    return job


def _post(client: TestClient, **body: Any) -> Any:
    return client.post("/v1/whatif/twin", json={"run_id": RUN_ID, **body})


def test_a_scenario_of_nothing_reproduces_the_run(twin_setup: Path, client: TestClient) -> None:
    res = _post(client)
    assert res.status_code == 202, res.text
    started = res.json()
    assert started["state"] == "running"
    # The estimate is the run's own Sky and Twin stages, which is what the job repeats.
    assert started["expected_ms"] == 1200 + 3400
    assert "twin 3,400 ms + sky 1,200 ms" in started["expected_from"]

    job = _finish(client, started)
    assert job["state"] == "done", job["error"]
    assert job["step"] == job["n_steps"] == N_STEPS
    assert job["cache"]["source"] == "computed"
    result = job["result"]
    assert result["method"] == "twin_full_aoi"
    assert result["blockage"] == "prior"
    assert result["n_segments_compared"] > 0, "the synthetic storm wetted no street"
    assert result["max_abs_delta_cm"] == 0.0
    assert result["n_changed"] == 0
    assert result["segments"] == []
    assert result["rain_reproduces_run"] is True
    assert result["mass_balance"]["within_budget"] is True
    assert result["sea"]["volume_in_change_m3"] == pytest.approx(0.0, abs=1e-6)
    hotspot = result["hotspots"][0]
    assert hotspot["name"] == "Test junction"
    assert hotspot["segments"] == 2 and hotspot["segments_missing"] == 1
    assert hotspot["minutes_above_before"] == hotspot["minutes_above_after"]
    # The scenario of nothing is the comparison run itself, and it reproduces the bake: the bake
    # was written by the same Twin, sampler and rounding on the same inputs.
    assert job["baseline_steps"] == 0
    assert result["baseline"]["method"] == "twin_nothing_changed"
    assert result["baseline"]["source"] == "computed"
    drift = result["baseline"]["drift"]
    assert drift["n_changed"] == 0 and drift["max_abs_delta_cm"] == 0.0
    assert drift["n_unsampled"] == 0
    assert any("reproduces the run's own forecast" in note for note in result["notes"])


def test_a_scenario_is_compared_with_the_twin_on_this_code_not_the_bake(
    twin_setup: Path, client: TestClient
) -> None:
    """A bake the Twin no longer reproduces is not credited to the scenario (rule 6).

    The bake's forecast for one street is moved by 20 cm, standing in for a bake made on older
    code. Compared with the bake, that street would read as changed by the scenario; compared
    with a Twin run with nothing changed on the same code, it does not, and the difference is
    reported as the bake's drift.
    """
    wet_path = twin_setup / "segments_wet.json"
    wet = json.loads(wet_path.read_text(encoding="utf-8"))
    moved = sorted(wet["depth_cm"])[0]
    twin_peak = max(wet["depth_cm"][moved])
    wet["depth_cm"][moved] = [round(v + 20.0, 1) for v in wet["depth_cm"][moved]]
    wet_path.write_text(json.dumps(wet), encoding="utf-8")

    started = _post(client, tide_offset_m=0.5).json()
    # No comparison run is cached, so the job runs the Twin twice and counts both.
    assert started["baseline_steps"] == N_STEPS
    assert started["n_steps"] == 2 * N_STEPS
    assert started["expected_ms"] == 1200 + 2 * 3400
    job = _finish(client, started)
    assert job["state"] == "done", job["error"]
    assert job["step"] == job["n_steps"] == 2 * N_STEPS
    result = job["result"]
    assert result["baseline"]["source"] == "computed"
    # The street's before is the Twin's, not the bake's 20 cm more.
    row = next((r for r in result["segments"] if r["segment_id"] == moved), None)
    assert row is None or row["before_cm"] == pytest.approx(twin_peak, abs=0.05)
    drift = result["baseline"]["drift"]
    assert drift["n_changed"] == 1
    assert drift["max_abs_delta_cm"] == pytest.approx(20.0, abs=0.1)
    assert drift["bake_code_matches"] is None, "the synthetic run records no code fingerprint"
    assert any("not the run's own forecast" in note for note in result["notes"])

    # The next scenario on the run reuses the comparison run: one Twin, from the cache.
    second = _post(client, rain_scale=1.3).json()
    assert second["baseline_steps"] == 0 and second["n_steps"] == N_STEPS
    second = _finish(client, second)
    assert second["state"] == "done", second["error"]
    assert second["result"]["baseline"]["source"] == "memory"
    assert second["result"]["timings"]["baseline_ms"] is None


def test_new_twin_code_is_never_answered_from_the_cache(
    twin_setup: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fingerprint carries the Twin's source and the city build, not only its version."""
    fingerprint = whatif.run_fingerprint(twin_setup)
    assert "-code" in fingerprint and "-city" in fingerprint
    done = _finish(client, _post(client, tide_offset_m=0.5).json())
    assert done["state"] == "done", done["error"]
    assert _post(client, tide_offset_m=0.5).status_code == 200

    monkeypatch.setattr(whatif, "_CODE_DIGEST", ["0" * 40])
    assert whatif.run_fingerprint(twin_setup) != fingerprint
    again = _post(client, tide_offset_m=0.5)
    assert again.status_code == 202, "an answer made on other Twin code was served"
    assert again.json()["baseline_steps"] == N_STEPS, "the comparison run was not recomputed"
    assert _finish(client, again.json())["cache"]["source"] == "computed"


def test_the_cache_answers_from_memory_then_disk_then_the_shipped_copy(
    twin_setup: Path, client: TestClient
) -> None:
    first = _finish(client, _post(client, tide_offset_m=0.5).json())
    assert first["state"] == "done", first["error"]

    from_memory = _post(client, tide_offset_m=0.5)
    assert from_memory.status_code == 200
    assert from_memory.json()["cache"]["source"] == "memory"
    assert from_memory.json()["result"] == first["result"]

    whatif._MEMORY.clear()
    from_disk = _post(client, tide_offset_m=0.5)
    assert from_disk.status_code == 200
    assert from_disk.json()["cache"]["source"] == "disk"

    # A shipped copy is used when this machine has none, and says it was not computed here.
    whatif.prewarm_scenario(RUN_ID, tide_offset_m=1.0, shipped=True)
    whatif._MEMORY.clear()
    shipped = _post(client, tide_offset_m=1.0)
    assert shipped.status_code == 200
    body = shipped.json()
    assert body["cache"]["source"] == "shipped"
    assert "not recomputed on this machine" in body["cache"]["label"]
    assert body["result"]["computed_on"]["cpus"]


def test_a_changed_run_is_never_answered_from_the_cache(
    twin_setup: Path, client: TestClient
) -> None:
    done = _finish(client, _post(client, rain_scale=1.3).json())
    assert done["state"] == "done", done["error"]
    stored = whatif._cache_root() / RUN_ID
    assert len(list(stored.glob("*.json.gz"))) == 1

    meta = json.loads((twin_setup / "run.json").read_text(encoding="utf-8"))
    meta["created_at"] = "re-baked"
    (twin_setup / "run.json").write_text(json.dumps(meta), encoding="utf-8")

    again = _post(client, rain_scale=1.3)
    assert again.status_code == 202, "a re-baked run was answered from the cache"
    assert _finish(client, again.json())["cache"]["source"] == "computed"


def test_the_emulator_only_levers_are_named_not_run_on_the_twin(
    twin_setup: Path, client: TestClient
) -> None:
    """A pump plan or "clean top 14" sent with a tide is not silently dropped by the Twin job."""
    res = _post(client, tide_offset_m=0.5, pump_plan=True, clean_top=True)
    assert res.status_code == 202, res.text
    scenario = res.json()["scenario"]
    assert scenario["levers_left_out"] == ["pump_plan", "clean_top"]
    assert any("no pump sink" in note for note in scenario["notes"])
    assert any("prior blockage" in note for note in scenario["notes"])
    # The run itself is the scenario without them: the same key as the tide alone.
    job = _finish(client, res.json())
    assert job["state"] == "done", job["error"]
    alone = _post(client, tide_offset_m=0.5)
    assert alone.status_code == 200 and alone.json()["cache"]["source"] == "memory"
    assert alone.json()["scenario"]["levers_left_out"] == []


def test_the_tide_lever_moves_the_sea_and_rain_moves_the_streets(
    twin_setup: Path, client: TestClient
) -> None:
    calm = _finish(client, _post(client).json())["result"]
    high = _finish(client, _post(client, tide_offset_m=1.0).json())["result"]
    assert high["scenario"]["tide_offset_m"] == 1.0
    # What crossed the shoreline is `sea_to_land_m3`, the net face exchange between the sea cells
    # and the land, not the clamp's gross tally on the sea cells (`tide_in_m3`), which counts the
    # sea's own rise. The pipes are the other path, `outfall_m3`, and the note names both.
    assert high["sea"]["sea_to_land_m3"] > calm["sea"]["sea_to_land_m3"]
    assert high["sea"]["sea_to_land_change_m3"] == pytest.approx(
        high["sea"]["sea_to_land_m3"] - calm["sea"]["sea_to_land_m3"], abs=0.2
    )
    assert calm["sea"]["sea_to_land_change_m3"] == 0.0
    for key in (
        "sea_stored_start_m3",
        "sea_stored_end_m3",
        "tide_in_m3",
        "tide_out_m3",
        "outfall_m3",
    ):
        assert key in high["sea"]
    note = next(n for n in high["notes"] if n.startswith("Tide offset +1.0 m"))
    assert "against the tide as forecast" in note
    assert "the shoreline" in note
    assert "at their outfalls, net" in note
    assert "entered the city" not in note
    assert any("illustrative" in note for note in high["notes"])

    wetter = _finish(client, _post(client, rain_scale=2.0).json())["result"]
    assert wetter["n_worse"] > 0
    assert all(row["newly_wet"] or row["delta_cm"] >= 0.0 for row in wetter["segments"]), (
        "doubling the rain lowered a street"
    )


def test_levers_are_rounded_to_the_cache_grid_and_refused_outside_the_range(
    twin_setup: Path, client: TestClient
) -> None:
    res = _post(client, rain_scale=1.26)
    assert res.status_code == 202
    body = res.json()
    assert body["scenario"]["rain_scale"] == 1.3
    assert "rounded" in body["scenario"]["notes"][0]
    _finish(client, body)

    for lever in ({"rain_scale": 2.5}, {"tide_offset_m": -0.8}, {"rain_scale": "much"}):
        refused = _post(client, **lever)
        assert refused.status_code == 422, refused.text


def test_a_bundle_with_no_tide_refuses_a_tide_offset(twin_setup: Path, client: TestClient) -> None:
    meta = json.loads((twin_setup / "run.json").read_text(encoding="utf-8"))
    meta["bundle"] = "NO-TIDE"
    (twin_setup / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    res = _post(client, tide_offset_m=0.5)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "no_tide"


def test_cleaning_resolves_to_pipes_and_refuses_ids_that_are_not_streets(
    twin_setup: Path, client: TestClient
) -> None:
    job = _finish(client, _post(client, cleaned_segments=["S03-000", "S05-000"]).json())
    scenario = job["result"]["scenario"]
    assert scenario["cleaned_edges"] == 2
    assert scenario["cleaned_segments"] == ["S03-000"]
    assert scenario["cleaned_no_pipe"] == ["S05-000"]

    # A street with no pipe under it is a street: cleaning only it changes nothing, and says so,
    # rather than being refused as "not a road segment".
    pipeless = _post(client, cleaned_segments=["S05-000"])
    assert pipeless.status_code in (200, 202), pipeless.text
    assert _finish(client, pipeless.json())["result"]["n_changed"] == 0

    refused = _post(client, cleaned_segments=["MUM-E035757"])
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "unknown_segments"


def test_the_host_gate_refuses_with_a_reason(
    twin_setup: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(whatif.TWIN_JOB_ENV, "0")
    res = _post(client, tide_offset_m=0.5)
    assert res.status_code == 403
    error = res.json()["error"]
    assert error["code"] == "whatif_twin_disabled"
    assert whatif.TWIN_JOB_ENV in error["message"]


def test_a_second_heavy_run_is_refused_while_one_holds_the_machine(
    twin_setup: Path, client: TestClient
) -> None:
    lock = whatif._heavy_lock()
    assert lock.acquire(blocking=False)
    try:
        res = _post(client, tide_offset_m=0.5)
    finally:
        lock.release()
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "whatif_busy"


def test_an_unknown_job_is_a_404(twin_setup: Path, client: TestClient) -> None:
    res = client.get("/v1/whatif/twin/nosuchjob")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "unknown_job"


def test_a_cancelled_job_stores_nothing_and_releases_the_lock(twin_setup: Path) -> None:
    import threading
    from datetime import datetime

    lock = threading.Lock()
    lock.acquire()
    job = whatif.TwinScenarioJob(
        job_id="cancelme",
        run_id=RUN_ID,
        key="k",
        scenario={},
        expected_ms=None,
        expected_from="",
        started_at=datetime.now(),
        cancel=True,
    )
    params = {"rain_scale": 1.0, "tide_offset_m": 0.0, "cleaned_segments": set()}
    whatif._run_job(job, twin_setup, params, "fp", None, lock)
    assert job.state == "cancelled"
    assert job.error is not None and "Nothing was stored" in job.error["message"]
    assert lock.acquire(blocking=False), "the heavy-run lock was not released"
    assert not (whatif._cache_root() / RUN_ID).exists()


def test_the_physics_check_points_a_tide_scenario_at_the_twin_job() -> None:
    """The emulator refusal and the physics-check refusal used to send the user to each other."""
    import inspect

    source = inspect.getsource(whatif.physics_check)
    assert "/v1/whatif/twin" in source


CITY = REPO / "city" / "mumbai"


def _real_run() -> str | None:
    runs = REPO / "data" / "runs"
    found = sorted(
        p.parent.name
        for p in runs.glob("MUM-*-baked/run.json")
        if (p.parent / "segments_wet.json").is_file()
    )
    return found[-1] if found else None


@pytest.mark.slow
@pytest.mark.skipif(
    not (CITY / "dem_conditioned.tif").is_file() or _real_run() is None,
    reason="needs `make city CITY=mumbai` and a baked Mumbai run under data/runs",
)
def test_real_mumbai_scenario_of_nothing_reproduces_the_bake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One full-city run (about a minute): a scenario of nothing against the bake's own forecast.

    Measured by the survey of 2026-09-26: 6,902 of 6,904 wet streets within the product's 0.05 cm
    rounding. The assertion is looser than that on purpose - the Twin's code may move between a
    bake and this test - and the result is written nowhere but a temporary directory.
    """
    run_id = _real_run()
    assert run_id is not None
    whatif._RAIN_CACHE.clear()
    monkeypatch.setattr(whatif, "_cache_root", lambda: tmp_path / "whatif")
    monkeypatch.setattr(whatif, "_shipped_cache_root", lambda: tmp_path / "shipped")
    whatif._BASELINES.clear()
    result = whatif.compute_twin_scenario(REPO / "data" / "runs" / run_id)
    assert result["method"] == "twin_full_aoi"
    assert result["rain_reproduces_run"] is True
    # Compared with itself, a scenario of nothing changes nothing, whatever the bake says.
    assert result["n_changed"] == 0
    # How far the bake is from today's Twin is reported, never absorbed. It is held to the old
    # bound only when the bake records the code and city it was made on and both still match.
    drift = result["baseline"]["drift"]
    if drift["bake_code_matches"] and drift["bake_city_matches"]:
        assert drift["n_changed"] <= max(10, drift["n_segments_compared"] // 100)

"""What the city-in-a-box wizard can show after the fact (SPEC.md 7.9).

Four defects these pin, all measured on the wizard before this change:

1. **Every line read the same time.** The job kept bare strings, so the screen stamped each one
   with the job's start. Lines now carry the instant the tap captured them.
2. **Every finished row read "Done 0 s".** The pipeline logs each step's milliseconds and the job
   threw them away. They are summed per wizard step now, with "loaded from disk" when every step
   behind a row was read from the city folder.
3. **Row five ran, stopped, and ran again.** Road segments mapped to "Build graph" while the
   pipeline runs segments, drains, units - so the rows oscillated. They advance in order now.
4. **An API restart erased the build.** Jobs lived only in memory. The last build is written to
   ``city/<city>/onboard_last.json``, and a failed rebuild never erases the run a good one made -
   which is also the run an onboarded city's console opens on, rather than whichever sorts newest.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient
from varuna_api import onboard, seed
from varuna_api.onboard import ORDER, OnboardState, _Tap
from varuna_city.pipeline import STEPS
from varuna_schemas.settings import get_settings

PIPELINE_STEPS: tuple[str, ...] = tuple(step.name for step in STEPS)
"""`varuna_city.pipeline.STEPS` by name, in order - the real list, never a copy of it."""

CHN_GOOD = "CHN-20260701T0040Z-sky1.0-twin1.0-flash0.0-baked"
CHN_STALE = "CHN-20260701T0120Z-sky1.0-twin1.0-flash0.0-baked"


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Path]]:
    """Isolated ``city/`` and ``data/runs`` folders, and a clean job table."""
    city_root = tmp_path / "city"
    data = tmp_path / "data"
    (city_root / "chennai").mkdir(parents=True)
    (data / "runs").mkdir(parents=True)
    monkeypatch.setenv("VARUNA_CITY_DIR", str(city_root))
    monkeypatch.setenv("VARUNA_DATA_DIR", str(data))
    monkeypatch.setenv("VARUNA_CITY", "mumbai")
    monkeypatch.setattr(onboard, "_JOBS", {})
    monkeypatch.setattr(onboard, "_LATEST", {})
    get_settings.cache_clear()
    yield city_root, data / "runs"
    get_settings.cache_clear()


def a_job(job_id: str = "onboard-chennai-0000aaaa") -> OnboardState:
    return OnboardState(
        job_id=job_id, city="chennai", design_storm="CHN-IDF-25yr", from_cache_only=True
    )


def a_run(runs: Path, run_id: str) -> Path:
    folder = runs / run_id
    (folder / "depth").mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    (folder / "depth" / "bounds.json").write_text("{}", encoding="utf-8")
    return folder


def replay_build(
    state: OnboardState,
    *,
    status: str = "ok",
    ms: float = 10.0,
    trace: list[str] | None = None,
) -> None:
    """Feed the tap what `run_city` logs for a build, recording the job's step after each line."""
    tap = _Tap()
    tap.attach(state)
    try:
        state.begin("fetch_open_data")
        for name in PIPELINE_STEPS:
            event = {"event": "city.step", "step": name, "status": status, "ms": ms}
            tap(None, "info", event)
            if trace is not None:
                trace.append(state.step)
    finally:
        tap.detach()


# ---- 1. timestamps -----------------------------------------------------------------------------
def test_every_line_carries_the_instant_it_was_captured() -> None:
    state = a_job()
    state.note("city.start city=chennai steps=14")
    state.note("osm.layer layer=roads rows=34410")
    payload = state.to_dict()

    assert [entry["text"] for entry in payload["log"]] == [
        "city.start city=chennai steps=14",
        "osm.layer layer=roads rows=34410",
    ]
    for entry in payload["log"]:
        # ISO 8601 with the IST offset (SPEC.md 12), to the millisecond.
        assert entry["ts"].endswith("+05:30")
        assert "." in entry["ts"]
        assert entry["level"] == "info"
    # The legacy field older consoles read is still the lines' text, newest last.
    assert payload["log_tail"] == [entry["text"] for entry in payload["log"]]


def test_the_log_keeps_the_newest_400_lines_and_counts_them_all() -> None:
    state = a_job()
    for i in range(onboard.MAX_LOG_LINES + 25):
        state.note(f"line {i}")
    payload = state.to_dict()
    assert len(payload["log"]) == onboard.MAX_LOG_LINES
    assert payload["log"][-1]["text"] == f"line {onboard.MAX_LOG_LINES + 24}"
    assert payload["log_total"] == onboard.MAX_LOG_LINES + 25
    assert len(payload["log_tail"]) == onboard.TAIL_LINES


# ---- 2. per-step milliseconds --------------------------------------------------------------------
def test_each_row_reports_the_pipelines_own_milliseconds() -> None:
    state = a_job()
    replay_build(state, status="ok", ms=10.0)
    steps = state.to_dict()["steps"]

    # Six pipeline steps behind "Fetch open data", four behind "Condition terrain" (the sea mask,
    # conditioning, roughness, depressions), two behind "Infer drains", three behind "Build graph".
    assert steps["fetch_open_data"]["ms"] >= 60.0
    assert steps["condition_terrain"]["ms"] == pytest.approx(40.0)
    assert steps["infer_drains"]["ms"] == pytest.approx(20.0)
    assert steps["build_graph"]["ms"] == pytest.approx(30.0)
    # Every pipeline step's time is in some row; none falls between them.
    rows_ms = sum(
        steps[name]["ms"] for name in ORDER if name not in {"choose_area", "first_forecast"}
    )
    assert rows_ms == pytest.approx(10.0 * len(PIPELINE_STEPS))
    for name in ("fetch_open_data", "condition_terrain", "infer_drains", "build_graph"):
        assert steps[name]["status"] == "done"
        assert steps[name]["progress"] == 1.0
        assert steps[name]["loaded_from_disk"] is False
    assert steps["first_forecast"]["status"] == "waiting"


def test_a_row_built_entirely_from_the_city_folder_says_loaded_from_disk() -> None:
    state = a_job()
    replay_build(state, status="cached", ms=5.0)
    steps = state.to_dict()["steps"]
    assert all(
        steps[name]["loaded_from_disk"]
        for name in ("fetch_open_data", "condition_terrain", "infer_drains", "build_graph")
    )


# ---- 3. the rows advance in order ----------------------------------------------------------------
def test_the_current_step_never_moves_backwards() -> None:
    """The regression: segments -> build_graph, drains -> infer_drains, units -> build_graph."""
    state = a_job()
    trace: list[str] = []
    replay_build(state, trace=trace)
    positions = [ORDER.index(step) for step in trace]
    assert positions == sorted(positions), trace
    assert trace[-1] == "build_graph"


def test_a_row_once_done_is_never_reopened() -> None:
    state = a_job()
    tap = _Tap()
    tap.attach(state)
    seen: dict[str, list[str]] = {name: [] for name in ORDER}
    try:
        state.begin("fetch_open_data")
        for name in PIPELINE_STEPS:
            tap(None, "info", {"event": "city.step", "step": name, "status": "ok", "ms": 1.0})
            for wizard_step, row in state.to_dict()["steps"].items():
                seen[wizard_step].append(row["status"])
    finally:
        tap.detach()
    for wizard_step, statuses in seen.items():
        if "done" in statuses:
            first = statuses.index("done")
            assert set(statuses[first:]) == {"done"}, (wizard_step, statuses)


# ---- the widened tap -----------------------------------------------------------------------------
def test_the_tap_streams_every_line_of_the_jobs_thread_but_debug_and_its_own_bookkeeping() -> None:
    state = a_job()
    tap = _Tap()
    tap.attach(state)
    try:
        tap(None, "info", {"event": "osm.layer", "layer": "roads", "rows": 34410})
        tap(None, "info", {"event": "landcover.built", "level": "info"})
        tap(None, "warning", {"event": "drains.orphans_repaired", "level": "warning", "n": 3})
        tap(None, "info", {"event": "cycle.stage", "stage": "twin", "ms": 42859})
        tap(None, "debug", {"event": "twin.substep", "level": "debug"})
        tap(None, "info", {"event": "onboard.started", "job": "x"})
        tap(None, "info", {"event": "city.step_failed", "exc_info": True, "step": None})
    finally:
        tap.detach()
    texts = state.texts
    assert texts[0] == "osm.layer layer=roads rows=34410"
    assert texts[1] == "landcover.built"
    assert texts[2] == "drains.orphans_repaired n=3"
    assert texts[3].startswith("cycle.stage")
    assert not any(t.startswith(("twin.substep", "onboard.started")) for t in texts)
    assert state.to_dict()["log"][2]["level"] == "warning"
    assert "exc_info" not in texts[-1]


def test_the_tap_ignores_other_threads() -> None:
    state = a_job()
    tap = _Tap()
    # Not attached on this thread: an API request logging beside a build is not the build.
    tap(None, "info", {"event": "api.request", "path": "/v1/runs"})
    assert state.texts == []


def test_a_very_long_line_is_cut_rather_than_dropped() -> None:
    state = a_job()
    state.note("x" * 5000)
    assert len(state.texts[0]) == onboard.MAX_LINE_CHARS
    assert state.texts[0].endswith("...")


def test_a_step_reported_failed_without_a_failure_line_still_freezes_there() -> None:
    """`_run_step` returns "failed" for a missing declared output with no `city.step_failed`."""
    state = a_job()
    tap = _Tap()
    tap.attach(state)
    try:
        state.begin("fetch_open_data")
        for name in PIPELINE_STEPS:
            status = "failed" if name == "drains" else "ok"
            tap(None, "info", {"event": "city.step", "step": name, "status": status, "ms": 1.0})
    finally:
        tap.detach()
    assert state.failed_step == "drains"
    assert state.step == "infer_drains"
    assert state.to_dict()["steps"]["infer_drains"]["status"] == "failed"
    assert state.to_dict()["steps"]["build_graph"]["status"] == "waiting"


# ---- the first forecast's stages ---------------------------------------------------------------
def test_the_first_forecast_carries_the_runs_own_stage_ms() -> None:
    state = a_job()
    state.begin("first_forecast")
    state.stage_started("sky")
    state.stage_finished("sky", 120.0)
    state.stage_started("twin")
    running = state.to_dict()["steps"]["first_forecast"]["stages"]
    assert running["sky"] == {"status": "done", "ms": 120.0}
    assert running["twin"]["status"] == "running"

    state.close_open_stages()
    state.apply_stage_ms(
        {"sky": 135, "twin": 42859, "pulse": 981, "flash": 0, "products": 2462, "decode": 7}
    )
    row = state.to_dict()["steps"]["first_forecast"]
    assert row["stage_ms"] == {
        "sky": 135.0,
        "twin": 42859.0,
        "pulse": 981.0,
        "flash": 0.0,
        "products": 2462.0,
    }
    assert row["stages"]["twin"] == {"status": "done", "ms": 42859.0}
    # Flash did not run: a design storm has no ensemble to spread, and the row says so.
    assert row["stages"]["flash"]["status"] == "skipped"


def test_products_is_reopened_after_flash_rather_than_timing_it() -> None:
    state = a_job()
    state.stage_started("products")  # opened where Pulse closed
    state.stage_reset("products")  # Flash's wrapper, on entry
    stages = state.to_dict()["steps"]["first_forecast"]["stages"]
    assert stages["products"] == {"status": "waiting", "ms": None}


# ---- 4. persistence ------------------------------------------------------------------------------
def finished_job(job_id: str, run_id: str | None, *, status: str = "finished") -> OnboardState:
    state = a_job(job_id)
    replay_build(state, status="cached")
    state.note("First forecast published")
    state.first_run_id = run_id
    state.status = status
    state.finished_at = onboard._now()
    return state


def test_a_finished_build_is_written_beside_the_city(folders: tuple[Path, Path]) -> None:
    city_root, _runs = folders
    onboard._persist(finished_job("onboard-chennai-11111111", CHN_GOOD))

    path = city_root / "chennai" / onboard.RECORD_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == onboard.RECORD_SCHEMA
    record = payload["last_finished"]
    assert payload["last_attempt"] == record
    for key in (
        "job_id",
        "city",
        "design_storm",
        "status",
        "started_at",
        "finished_at",
        "elapsed_s",
        "first_run_id",
        "error",
        "steps",
        "lines",
    ):
        assert key in record, key
    assert record["first_run_id"] == CHN_GOOD
    assert record["lines"][-1]["text"] == "First forecast published"
    assert record["lines"][-1]["ts"].endswith("+05:30")
    assert record["steps"]["infer_drains"]["loaded_from_disk"] is True
    # No temp file left behind by the atomic write.
    assert [p.name for p in (city_root / "chennai").iterdir()] == [onboard.RECORD_FILE]


def test_a_failed_rebuild_never_erases_the_run_a_good_build_made(
    folders: tuple[Path, Path],
) -> None:
    _city_root, runs = folders
    a_run(runs, CHN_GOOD)
    onboard._persist(finished_job("onboard-chennai-11111111", CHN_GOOD))

    failed = finished_job("onboard-chennai-22222222", None, status="failed")
    failed.error = "The chennai build failed at: drains."
    onboard._persist(failed)

    payload = onboard.read_record("chennai")
    assert payload is not None
    assert payload["last_finished"]["first_run_id"] == CHN_GOOD
    assert payload["last_attempt"]["job_id"] == "onboard-chennai-22222222"
    assert onboard.onboard_run_id("chennai") == CHN_GOOD

    previous = onboard.previous_build("chennai")
    assert previous["previous"]["job_id"] == "onboard-chennai-11111111"
    assert previous["previous"]["first_run_exists"] is True
    assert previous["previous"]["seeded"] is False
    assert previous["last_attempt"]["status"] == "failed"
    assert previous["last_attempt"]["error"] == "The chennai build failed at: drains."


def test_a_build_finished_without_a_forecast_does_not_replace_a_good_one(
    folders: tuple[Path, Path],
) -> None:
    onboard._persist(finished_job("onboard-chennai-11111111", CHN_GOOD))
    onboard._persist(finished_job("onboard-chennai-33333333", None))
    payload = onboard.read_record("chennai")
    assert payload is not None
    assert payload["last_finished"]["job_id"] == "onboard-chennai-11111111"


def test_a_job_no_longer_in_memory_is_found_by_its_id(folders: tuple[Path, Path]) -> None:
    onboard._persist(finished_job("onboard-chennai-11111111", CHN_GOOD))
    assert onboard.record_for_job("onboard-chennai-11111111")["first_run_id"] == CHN_GOOD
    # A different id for the same city never answers with that build.
    assert onboard.record_for_job("onboard-chennai-99999999") is None
    assert onboard.record_for_job("not-a-job") is None
    assert onboard.record_for_job("onboard-..-11111111") is None


def test_the_routes_answer_from_the_record_after_a_restart(
    folders: tuple[Path, Path], client: TestClient
) -> None:
    city_root, runs = folders
    a_run(runs, CHN_GOOD)
    (city_root / "chennai" / "segments.parquet").write_bytes(b"")
    onboard._persist(finished_job("onboard-chennai-11111111", CHN_GOOD))

    latest = client.get("/v1/onboard/city/chennai")
    assert latest.status_code == 200, latest.text
    body = latest.json()
    assert body["status"] == "none"
    assert body["job_id"] is None
    assert body["built"] is True
    assert body["previous"]["job_id"] == "onboard-chennai-11111111"
    assert body["previous"]["first_run_id"] == CHN_GOOD
    assert body["previous"]["first_run_exists"] is True
    assert body["previous"]["lines"][-1]["text"] == "First forecast published"
    assert body["last_attempt"] is None

    job = client.get("/v1/onboard/onboard-chennai-11111111")
    assert job.status_code == 200, job.text
    served = job.json()
    assert served["from_record"] is True
    assert served["status"] == "finished"
    assert served["log"][-1]["text"] == "First forecast published"
    assert served["log_tail"][-1] == "First forecast published"
    assert served["built"] is True

    missing = client.get("/v1/onboard/onboard-chennai-99999999")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "no_job"


def test_a_city_never_onboarded_has_no_previous_build(
    folders: tuple[Path, Path], client: TestClient
) -> None:
    body = client.get("/v1/onboard/city/chennai").json()
    assert body["status"] == "none"
    assert body["previous"] is None
    assert body["last_attempt"] is None


# ---- the whole job, end to end with the pipeline and the cycle stubbed ---------------------------
@dataclass
class FakeStep:
    name: str
    status: str


@dataclass
class FakeResult:
    steps: list[FakeStep]
    stats: dict[str, Any] = field(default_factory=dict)


def test_a_job_run_to_completion_persists_its_own_log_and_timings(
    folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_run` through the real structlog tap: the lines come from the logger, not from the test."""
    import varuna_city.pipeline as pipeline

    city_root, runs = folders
    a_run(runs, CHN_GOOD)
    logger = structlog.get_logger("varuna.city.pipeline")

    def fake_run_city(city: str) -> FakeResult:
        logger.info("osm.layer", layer="roads", rows=34410)
        for name in PIPELINE_STEPS:
            logger.info("city.step", city=city, step=name, status="cached", ms=12.5)
        return FakeResult(
            steps=[FakeStep(n, "cached") for n in PIPELINE_STEPS],
            stats={"osm": {"roads": 34410, "buildings": 72573}, "drains": {"edges": 51234}},
        )

    def fake_first_forecast(state: OnboardState) -> str:
        logger.info("cycle.stage", stage="twin", ms=42859)
        state.apply_stage_ms({"sky": 135, "twin": 42859, "pulse": 981, "products": 2462})
        return CHN_GOOD

    monkeypatch.setattr(pipeline, "run_city", fake_run_city)
    monkeypatch.setattr(onboard, "_warm_cache", lambda state: [])
    monkeypatch.setattr(onboard, "_first_forecast", fake_first_forecast)
    onboard.install_tap()

    state = a_job("onboard-chennai-44444444")
    onboard._run(state)

    assert state.status == "finished", state.error
    record = onboard.read_record("chennai")
    assert record is not None
    last = record["last_finished"]
    texts = [line["text"] for line in last["lines"]]
    assert "osm.layer layer=roads rows=34410" in texts
    assert any(t.startswith("city.step city=chennai step=drains status=cached") for t in texts)
    assert any(t.startswith("cycle.stage stage=twin") for t in texts)
    assert last["steps"]["choose_area"]["status"] == "done"
    assert last["steps"]["fetch_open_data"]["detail"] == "34,410 roads, 72,573 buildings from OSM"
    assert last["steps"]["infer_drains"]["detail"] == "51,234 inferred pipes"
    assert last["steps"]["condition_terrain"]["ms"] == pytest.approx(12.5 * 4)
    assert last["steps"]["build_graph"]["loaded_from_disk"] is True
    assert last["steps"]["first_forecast"]["status"] == "done"
    assert last["steps"]["first_forecast"]["stage_ms"]["twin"] == 42859.0
    assert last["first_run_id"] == CHN_GOOD
    assert (city_root / "chennai" / onboard.RECORD_FILE).is_file()


# ---- the run an onboarded city opens on ---------------------------------------------------------
def test_an_onboarded_city_opens_on_its_onboarding_run_not_the_newest(
    folders: tuple[Path, Path],
) -> None:
    from varuna_api.runs_util import latest_run_for

    _city_root, runs = folders
    a_run(runs, CHN_GOOD)
    a_run(runs, CHN_STALE)
    has_bounds = lambda p: (p / "depth" / "bounds.json").is_file()  # noqa: E731

    # No record: the newest by id, as before.
    assert latest_run_for("chennai", has_bounds).name == CHN_STALE

    onboard._persist(finished_job("onboard-chennai-11111111", CHN_GOOD))
    assert latest_run_for("chennai", has_bounds).name == CHN_GOOD
    assert latest_run_for("chennai").name == CHN_GOOD

    # The onboarded run lacks what the caller needs: fall back to the newest that has it.
    assert latest_run_for("chennai", lambda p: p.name == CHN_STALE).name == CHN_STALE


def test_an_onboarding_run_that_is_gone_falls_back_to_the_newest(
    folders: tuple[Path, Path],
) -> None:
    from varuna_api.runs_util import latest_run_for

    _city_root, runs = folders
    a_run(runs, CHN_STALE)
    onboard._persist(finished_job("onboard-chennai-11111111", CHN_GOOD))
    assert onboard.onboard_run_id("chennai") is None
    assert latest_run_for("chennai").name == CHN_STALE


def test_the_configured_city_keeps_its_newest_run(folders: tuple[Path, Path]) -> None:
    """Mumbai is the replay's city: an onboarding record never overrides its newest bake."""
    from varuna_api.runs_util import latest_run_for

    city_root, runs = folders
    older = "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked"
    newer = "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked"
    a_run(runs, older)
    a_run(runs, newer)
    (city_root / "mumbai").mkdir()
    job = finished_job("onboard-mumbai-11111111", older)
    job.city = "mumbai"
    onboard._persist(job)
    assert onboard.onboard_run_id("mumbai") == older
    assert latest_run_for("mumbai").name == newer
    assert latest_run_for(None).name == newer


# ---- the shipped record ---------------------------------------------------------------------------
@pytest.fixture
def shipped(tmp_path: Path, folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> Path:
    """A demo folder with runs and an onboarding record, isolated from the repo's."""
    demo_runs = tmp_path / "demo" / "runs"
    demo_runs.mkdir(parents=True)
    (tmp_path / "demo" / "onboard").mkdir()
    monkeypatch.setattr(seed, "demo_runs_dir", lambda: demo_runs)
    monkeypatch.setattr(seed, "runs_dir", lambda: folders[1])
    return tmp_path / "demo" / "onboard"


def a_shipped_record(folder: Path, city: str = "chennai") -> Path:
    record = finished_job(f"onboard-{city}-55555555", CHN_GOOD).record()
    record["city"] = city
    path = folder / f"{city}.json"
    path.write_text(
        json.dumps({"schema": 1, "city": city, "last_attempt": record, "last_finished": record}),
        encoding="utf-8",
    )
    return path


def test_a_shipped_record_is_seeded_into_a_built_city_and_marked(
    shipped: Path, folders: tuple[Path, Path]
) -> None:
    city_root, runs = folders
    (city_root / "chennai" / "segments.parquet").write_bytes(b"")
    a_run(runs, CHN_GOOD)
    a_shipped_record(shipped)

    assert seed.seed_onboard_records() == 1
    payload = onboard.read_record("chennai")
    assert payload is not None
    assert payload["seeded"] is True
    assert payload["last_finished"]["seeded"] is True
    assert onboard.previous_build("chennai")["previous"]["seeded"] is True
    assert onboard.onboard_run_id("chennai") == CHN_GOOD
    # Twice is once.
    assert seed.seed_onboard_records() == 0


def test_a_record_written_here_is_never_replaced_by_the_shipped_one(
    shipped: Path, folders: tuple[Path, Path]
) -> None:
    city_root, _runs = folders
    (city_root / "chennai" / "segments.parquet").write_bytes(b"")
    onboard._persist(finished_job("onboard-chennai-11111111", CHN_STALE))
    a_shipped_record(shipped)

    assert seed.seed_onboard_records() == 0
    payload = onboard.read_record("chennai")
    assert payload is not None
    assert payload["last_finished"]["job_id"] == "onboard-chennai-11111111"
    assert "seeded" not in payload


def test_a_record_for_a_city_not_built_here_or_misnamed_is_not_seeded(
    shipped: Path, folders: tuple[Path, Path]
) -> None:
    city_root, _runs = folders
    a_shipped_record(shipped)  # chennai has no segments.parquet on this volume
    wrong = shipped / "mumbai.json"
    wrong.write_text(json.dumps({"city": "chennai"}), encoding="utf-8")
    (city_root / "mumbai").mkdir()
    (city_root / "mumbai" / "segments.parquet").write_bytes(b"")

    assert seed.seed_onboard_records() == 0
    assert not (city_root / "chennai" / onboard.RECORD_FILE).exists()
    assert not (city_root / "mumbai" / onboard.RECORD_FILE).exists()


def test_seeding_the_runs_also_seeds_the_record(shipped: Path, folders: tuple[Path, Path]) -> None:
    city_root, _runs = folders
    (city_root / "chennai" / "segments.parquet").write_bytes(b"")
    a_shipped_record(shipped)
    seed.seed_demo_runs()
    assert (city_root / "chennai" / onboard.RECORD_FILE).is_file()

"""What the demo-run seeding is allowed to overwrite (ADR-0024)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from varuna_api import seed


def _demo_run(root: Path, name: str, extra: dict[str, str] | None = None) -> Path:
    run = root / name
    (run / "depth").mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"run_id": name}), encoding="utf-8")
    (run / "depth" / "bounds.json").write_text("{}", encoding="utf-8")
    for filename, content in (extra or {}).items():
        (run / filename).write_text(content, encoding="utf-8")
    return run


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A demo source and a run target, both isolated from the repo."""
    source = tmp_path / "demo" / "runs"
    source.mkdir(parents=True)
    target = tmp_path / "data" / "runs"
    monkeypatch.setattr(seed, "demo_runs_dir", lambda: source)
    monkeypatch.setattr(seed, "runs_dir", lambda: target)
    return source, target


def test_an_empty_volume_gets_the_whole_shipped_set(dirs: tuple[Path, Path]) -> None:
    source, target = dirs
    _demo_run(source, "RUN-A")
    _demo_run(source, "RUN-B")

    assert seed.seed_demo_runs() == 2
    assert (target / "RUN-A" / "run.json").is_file()
    assert (target / "RUN-B" / "depth" / "bounds.json").is_file()


def test_seeding_twice_writes_nothing_the_second_time(dirs: tuple[Path, Path]) -> None:
    source, _target = dirs
    _demo_run(source, "RUN-A")

    assert seed.seed_demo_runs() == 1
    assert seed.seed_demo_runs() == 0, "an unchanged set must not be recopied every boot"


def test_a_changed_demo_set_replaces_what_it_seeded(dirs: tuple[Path, Path]) -> None:
    """The bug this marker exists for: a shipped set that gains a product must land.

    `/v1/pumps` stayed 404 on a deployment whose image already carried `pump_plan.json`,
    because the volume was merely non-empty and the first version of this seeded only into an
    empty one.
    """
    source, target = dirs
    _demo_run(source, "RUN-A")
    assert seed.seed_demo_runs() == 1
    assert not (target / "RUN-A" / "pump_plan.json").exists()

    (source / "RUN-A" / "pump_plan.json").write_text('{"assignments":[]}', encoding="utf-8")

    assert seed.seed_demo_runs() == 1
    assert (target / "RUN-A" / "pump_plan.json").is_file()


def test_a_locally_baked_run_is_never_replaced(dirs: tuple[Path, Path]) -> None:
    """A cycle this deployment computed is its own work, whatever the shipped set holds.

    A baked run is recognised by being complete: it has everything the shipped set has and the
    19 MB `segment_forecast.parquet` besides, which the shipped set deliberately omits.
    """
    source, target = dirs
    _demo_run(source, "RUN-A", {"note.txt": "from the repo"})

    baked = _demo_run(target.parent / "runs", "RUN-A", {"note.txt": "baked here"})
    (baked / "segment_forecast.parquet").write_text("the product of record", encoding="utf-8")

    assert seed.seed_demo_runs() == 0
    assert (target / "RUN-A" / "note.txt").read_text(encoding="utf-8") == "baked here"


def test_a_copy_seeded_before_markers_existed_is_brought_up_to_date(
    dirs: tuple[Path, Path],
) -> None:
    """The migration this whole mechanism exists for.

    The volume already held runs copied by the first version of this function, which wrote no
    marker. Reading "no marker" as "baked here" left `/v1/pumps` 404 on a deployment whose image
    already carried the plan. An unmarked copy that is *missing* something the shipped set has
    is an old seed, and is replaced.
    """
    source, target = dirs
    _demo_run(source, "RUN-A", {"pump_plan.json": '{"assignments":[]}'})

    # What the old seeder left: the same run, without the product added since.
    _demo_run(target.parent / "runs", "RUN-A")

    assert seed.seed_demo_runs() == 1
    assert (target / "RUN-A" / "pump_plan.json").is_file()
    assert (target / "RUN-A" / seed.MARKER).is_file()


def test_no_demo_directory_is_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A checkout without `demo/` still boots; the console just shows its empty state."""
    monkeypatch.setattr(seed, "demo_runs_dir", lambda: tmp_path / "absent")
    monkeypatch.setattr(seed, "runs_dir", lambda: tmp_path / "runs")
    assert seed.seed_demo_runs() == 0


def test_a_run_the_shipped_set_replaced_leaves_the_volume(dirs: tuple[Path, Path]) -> None:
    """Re-baking the demo cycles with the ensemble changed every run id, flash0.0 to flash0.1.

    Seeding never removed anything, so the volume would have kept both runs of each cycle: the
    cycle picker listing every cycle twice, and the single-member run one click away from the
    ensemble that replaced it.
    """
    source, target = dirs
    _demo_run(source, "MUM-0040Z-flash0.0")
    assert seed.seed_demo_runs() == 1

    (source / "MUM-0040Z-flash0.0").rename(source / "MUM-0040Z-flash0.1")

    assert seed.seed_demo_runs() == 1
    assert (target / "MUM-0040Z-flash0.1" / "run.json").is_file()
    assert not (target / "MUM-0040Z-flash0.0").exists()


def test_a_run_the_deployment_baked_itself_is_never_pruned(dirs: tuple[Path, Path]) -> None:
    """Pruning takes back only what seeding put there. A run with no marker is this
    deployment's own work - a Compute live, say - and is not in the shipped set by nature."""
    source, target = dirs
    _demo_run(source, "RUN-A")
    own = _demo_run(target.parent / "runs", "MUM-LIVE")

    seed.seed_demo_runs()

    assert (own / "run.json").is_file()


def test_a_run_baked_here_survives_a_shipped_copy_with_different_alerts(
    dirs: tuple[Path, Path],
) -> None:
    """The defect that erased two re-bakes of the demo cycles (2026-09-24 and 2026-09-26).

    A shipped run's alert documents are named by the alerts its cycle raised. A fresh bake of the
    same cycle raises different ones, so it "lacked" a file the shipped copy had and was deleted
    as an old seed. A run carrying the product of record is this machine's work, whatever else.
    """
    source, target = dirs
    _demo_run(source, "RUN-A", {"alerts.json": "[]"})
    (source / "RUN-A" / "alerts").mkdir()
    (source / "RUN-A" / "alerts" / "VARUNA-OLD.cap.xml").write_text("<alert/>", encoding="utf-8")

    baked = _demo_run(target, "RUN-A", {"alerts.json": "[1]", seed.LOCAL_PRODUCT: "parquet"})
    (baked / "alerts").mkdir()
    (baked / "alerts" / "VARUNA-NEW.cap.xml").write_text("<alert/>", encoding="utf-8")

    assert seed.seed_demo_runs() == 0
    assert (baked / "alerts" / "VARUNA-NEW.cap.xml").is_file()
    assert (baked / "alerts.json").read_text(encoding="utf-8") == "[1]"
    assert not (baked / seed.MARKER).exists()


def test_a_local_bake_is_never_taken_back(dirs: tuple[Path, Path]) -> None:
    """Even one the shipped set no longer names, and even if a marker was left in it."""
    source, target = dirs
    _demo_run(source, "RUN-A")
    local = _demo_run(target, "RUN-OLD", {seed.LOCAL_PRODUCT: "parquet", seed.MARKER: "stale"})

    seed.seed_demo_runs()
    assert (local / seed.LOCAL_PRODUCT).is_file()


# ---- an onboarded city's first forecast ----------------------------------------------------------
CHN_RUN = "CHN-20260701T0040Z-sky1.0-twin1.0-flash0.0-baked"


@pytest.fixture
def cities(dirs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The city root the seeder reads, isolated from the repo's ``city/``."""
    root = tmp_path / "city"
    monkeypatch.setattr(seed, "city_dir", lambda city: root / city)
    return root


def _onboard_run(source: Path, name: str = CHN_RUN, city: str = "chennai") -> Path:
    """A first forecast shipped under ``demo/onboard/runs`` beside the replay's ``demo/runs``."""
    folder = source.parent / "onboard" / "runs"
    run = _demo_run(folder, name)
    (run / "run.json").write_text(json.dumps({"run_id": name, "city": city}), encoding="utf-8")
    return run


def _built(root: Path, city: str = "chennai") -> None:
    (root / city).mkdir(parents=True, exist_ok=True)
    (root / city / "segments.parquet").write_bytes(b"")


def test_an_onboarded_citys_first_forecast_is_seeded_where_the_city_is_built(
    dirs: tuple[Path, Path], cities: Path
) -> None:
    """The deployed Pravesh said the recorded build's run "is not on this API": it never shipped."""
    source, target = dirs
    _demo_run(source, "RUN-A")
    _onboard_run(source)
    _built(cities)

    assert seed.seed_demo_runs() == 2
    assert (target / CHN_RUN / "run.json").is_file()
    assert (target / CHN_RUN / seed.MARKER).is_file()
    assert seed.seed_demo_runs() == 0, "an unchanged onboarding run is not recopied every boot"


def test_a_first_forecast_is_not_seeded_for_a_city_the_volume_has_not_built(
    dirs: tuple[Path, Path], cities: Path
) -> None:
    source, target = dirs
    _demo_run(source, "RUN-A")
    _onboard_run(source)

    assert seed.seed_demo_runs() == 1
    assert not (target / CHN_RUN).exists()


def test_a_first_forecast_baked_here_is_never_replaced(
    dirs: tuple[Path, Path], cities: Path
) -> None:
    """ADR-0082 holds for the onboarding run too: a local bake carries the product of record."""
    source, target = dirs
    _onboard_run(source)
    _built(cities)
    local = target / CHN_RUN
    local.mkdir(parents=True)
    (local / "run.json").write_text(
        json.dumps({"run_id": CHN_RUN, "local": True}), encoding="utf-8"
    )
    (local / seed.LOCAL_PRODUCT).write_bytes(b"parquet")

    assert seed.seed_demo_runs() == 0
    assert json.loads((local / "run.json").read_text(encoding="utf-8"))["local"] is True
    assert not (local / seed.MARKER).exists()


def test_the_shipped_onboarding_run_is_the_one_the_shipped_record_names() -> None:
    """The record and its run ship together, and the run ships as the demo runs do."""
    from varuna_schemas.paths import repo_root

    record_path = repo_root() / "demo" / "onboard" / "chennai.json"
    if not record_path.is_file():
        pytest.skip("demo/onboard is not in this checkout")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    run_id = record["last_finished"]["first_run_id"]
    run = repo_root() / "demo" / "onboard" / "runs" / run_id
    assert (run / "run.json").is_file(), f"{run_id} is named by the record and not shipped"
    assert json.loads((run / "run.json").read_text(encoding="utf-8"))["city"] == "chennai"
    for product in ("segments_wet.json", "depth/bounds.json", "depth/p50_00.png"):
        assert (run / product).is_file(), product
    assert not (run / seed.LOCAL_PRODUCT).exists(), "the 15 MB product of record is not shipped"
    assert not (run / "rain").exists()
    assert not (run / seed.MARKER).exists()

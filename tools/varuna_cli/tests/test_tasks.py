"""Task-runner tests: every ``make`` target is ``uv run varuna <target>`` (SPEC.md 4.3).

The CLI is the only way the demo is driven on stage (rule 15: never open a terminal), so the
behaviour that matters is tested here: the phase gates exit cleanly, ``demo`` says what to do
when nothing is baked, and no command launches a real process under test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner
from varuna_cli import main as cli_main
from varuna_cli import tasks
from varuna_schemas.paths import repo_root

runner = CliRunner()


@pytest.fixture
def app() -> typer.Typer:
    """A root app with every task attached and no engine sub-apps."""
    fresh = typer.Typer(no_args_is_help=True, add_completion=False)
    tasks.register(fresh)
    return fresh


@pytest.fixture
def no_processes(monkeypatch: pytest.MonkeyPatch) -> list[list[tasks.Service]]:
    """Capture what ``dev``/``demo`` would launch instead of launching it.

    Building the service list resolves each tool's absolute path, so a machine without
    ``pnpm`` on PATH raised ``ToolMissingError`` before the fake launcher was ever reached.
    That is exactly the CI Python job, which installs uv but not Node, and it is why these
    tests passed locally and failed on the first real CI run. What they assert is *what* the
    task runner would launch, not what is installed on the machine, so tool resolution is
    stubbed as well; ``varuna doctor`` still reports a genuinely missing tool.
    """
    launched: list[list[tasks.Service]] = []

    def fake_run_concurrently(services: list[tasks.Service], **_: Any) -> int:
        launched.append(list(services))
        return 0

    def fake_resolve_executable(name: str) -> str:
        return f"/usr/bin/{name}"

    monkeypatch.setattr(tasks.procs, "run_concurrently", fake_run_concurrently)
    monkeypatch.setattr(tasks.procs, "resolve_executable", fake_resolve_executable)
    return launched


@pytest.fixture
def empty_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the run registry at an empty temporary folder."""
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(tasks, "runs_dir", lambda: runs)
    return runs


def write_run(runs: Path, run_id: str, **meta: Any) -> None:
    folder = runs / run_id
    folder.mkdir(parents=True)
    payload = {"run_id": run_id, "city": "mumbai", **meta}
    (folder / "run.json").write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# Help and registration
# ---------------------------------------------------------------------------


def test_help_lists_every_make_target(app: typer.Typer) -> None:
    """``varuna --help`` must offer every target in SPEC.md 4.3."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for target in (
        "setup",
        "city",
        "bundle",
        "bake",
        "train",
        "dev",
        "demo",
        "test",
        "e2e",
        "pack",
    ):
        assert target in result.stdout


def test_help_target_prints_the_makefile_table(app: typer.Typer) -> None:
    """``varuna help`` parses the Makefile so the printed table cannot drift from it."""
    result = runner.invoke(app, ["help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "demo" in result.stdout
    assert "BUNDLE=MUM-2019-07-02" in result.stdout


def test_every_documented_task_is_registered() -> None:
    names = {name for name, _ in tasks.TASKS}
    assert {
        "setup",
        "doctor",
        "city",
        "bundle",
        "bake",
        "train",
        "dev",
        "demo",
        "test",
        "e2e",
        "pack",
        "demo-video",
        "typegen",
        "openapi",
        "lint",
        "typecheck",
        "format",
        "clean",
        "help",
    } <= names


def test_register_skips_names_taken_by_an_engine() -> None:
    """When an engine ships ``varuna city``, the placeholder task must yield to it."""
    fresh = typer.Typer()
    tasks.register(fresh, skip={"city"})
    registered = {
        command.name or command.callback.__name__ for command in fresh.registered_commands
    }
    assert "city" not in registered
    assert "demo" in registered


# ---------------------------------------------------------------------------
# Phase gates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "phase"),
    # `pack` is deliberately absent: P10.6 implemented it, so invoking it here no longer refuses -
    # it builds the real 830 MB offline package, which is not something a unit test should do.
    # `train` left for the same reason on 2026-09-13: it now fits the emulator. Its own refusal -
    # an empty corpus - is tested below.
    # `bake` left on 2026-09-13 for the same reason: it computes cycles now, four minutes each.
    # Its wiring is tested in test_bake_task.py with the cycle engine replaced.
    [("city", 1), ("bundle", 2)],
)
def test_phase_gate_exits_with_two_and_no_traceback(
    app: typer.Typer, target: str, phase: int
) -> None:
    """A target whose engine is not built yet says which phase builds it and exits 2."""
    result = runner.invoke(app, [target], env={"COLUMNS": "200"})
    assert result.exit_code == 2, result.stdout
    assert f"Phase {phase}" in result.stdout
    assert "Traceback" not in result.stdout


def test_phase_gate_covers_every_placeholder() -> None:
    """Every placeholder task has a phase and a name, so ``not_implemented`` cannot KeyError."""
    for target, (phase, name) in tasks.PLACEHOLDER_PHASES.items():
        assert isinstance(phase, int) and 1 <= phase <= 10, target
        assert name


# ---------------------------------------------------------------------------
# train: the fit behind every what-if number (task P7.5)
# ---------------------------------------------------------------------------


def test_train_without_a_corpus_refuses_and_names_it(app: typer.Typer, tmp_path: Path) -> None:
    """An empty corpus must refuse, not write a skill report from nothing (SPEC.md rule 6)."""
    result = runner.invoke(
        app, ["train", "--train-dir", str(tmp_path / "absent")], env={"COLUMNS": "200"}
    )
    assert result.exit_code == 2, result.stdout
    assert "train-*.npz" in result.stdout
    assert "Nothing was written" in result.stdout
    assert "Traceback" not in result.stdout


def test_train_reads_the_corpus_before_it_fits(
    app: typer.Typer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fit is reached only with more runs than the holdout, and gets the CLI's arguments.

    The fit itself is ``varuna_flash``'s and is tested there; what belongs here is that the
    target counts the corpus the same way the fitter globs it, so the refusal and the fit
    cannot disagree about whether there is anything to train on.
    """
    corpus = tmp_path / "train"
    corpus.mkdir()
    for k in range(3):
        (corpus / f"train-0{k}-p25-b10.npz").write_bytes(b"")
    (corpus / "flash_lite.npz").write_bytes(b"")  # the fitted model is not training data

    calls: list[dict[str, Any]] = []

    def fake_fit(train_dir: Path, **kwargs: Any) -> dict[str, object]:
        calls.append({"train_dir": train_dir, **kwargs})
        return {
            "model": str(train_dir / "flash_lite.npz"),
            "n_segments": 21296,
            "fitted_segments": 13702,
            "n_training_runs": 1,
            "n_holdout_runs": 2,
            "rmse_cm": 5.704,
            "csi_30cm": 0.0854,
            "note": "Reduced-order emulator calibrated to VARUNA-Twin.",
        }

    monkeypatch.setattr("varuna_flash.train.train_from_runs", fake_fit)
    result = runner.invoke(
        app, ["train", "--train-dir", str(corpus), "--holdout", "2"], env={"COLUMNS": "200"}
    )
    assert result.exit_code == 0, result.stdout
    assert calls[0]["city"] == tasks.DEFAULT_CITY
    assert calls[0]["holdout"] == 2
    assert calls[0]["out_report"].name == "flash_lite.json"
    assert "5.704 cm" in result.stdout
    assert "0.0854" in result.stdout


# ---------------------------------------------------------------------------
# demo: the sacred path (SPEC.md rule 5)
# ---------------------------------------------------------------------------


def test_demo_without_a_bake_explains_what_to_run(
    app: typer.Typer, empty_runs: Path, no_processes: list[list[tasks.Service]]
) -> None:
    """No baked runs: print the two commands and still start the console in its empty state."""
    result = runner.invoke(app, ["demo"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.stdout
    assert "No bundle baked yet" in result.stdout
    assert "make bundle" in result.stdout and "make bake" in result.stdout
    assert "Traceback" not in result.stdout
    assert [service.name for service in no_processes[0]] == ["api", "ui"]
    assert no_processes[0][0].env["VARUNA_REPLAY_AUTOPLAY"] == "0"


def test_demo_with_baked_runs_autoplays(
    app: typer.Typer, empty_runs: Path, no_processes: list[list[tasks.Service]]
) -> None:
    write_run(
        empty_runs,
        "MUM-20190702T1740Z-sky1.0-twin1.0-flash0.3-baked",
        mode="baked",
        bundle="MUM-2019-07-02",
    )
    result = runner.invoke(app, ["demo", "--bundle", "MUM-2019-07-02"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.stdout
    assert "1 baked runs found" in result.stdout
    api_env = no_processes[0][0].env
    assert api_env["VARUNA_REPLAY_AUTOPLAY"] == "1"
    assert api_env["VARUNA_BUNDLE"] == "MUM-2019-07-02"


def test_demo_speed_reaches_the_services(
    app: typer.Typer, empty_runs: Path, no_processes: list[list[tasks.Service]]
) -> None:
    runner.invoke(app, ["demo", "--speed", "30"], env={"COLUMNS": "200"})
    assert no_processes[0][0].env["VARUNA_REPLAY_SPEED"] == "30"


def test_dev_starts_both_services_with_the_replay_paused(
    app: typer.Typer, no_processes: list[list[tasks.Service]]
) -> None:
    result = runner.invoke(app, ["dev"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.stdout
    services = no_processes[0]
    assert [service.name for service in services] == ["api", "ui"]
    assert services[0].env["VARUNA_REPLAY_AUTOPLAY"] == "0"
    assert "3000" in services[1].env["VARUNA_UI_PORT"]


def test_dev_can_start_one_side_only(
    app: typer.Typer, no_processes: list[list[tasks.Service]]
) -> None:
    runner.invoke(app, ["dev", "--no-ui"], env={"COLUMNS": "200"})
    assert [service.name for service in no_processes[0]] == ["api"]


def test_dev_ports_flow_into_the_ui_environment(
    app: typer.Typer, no_processes: list[list[tasks.Service]]
) -> None:
    runner.invoke(app, ["dev", "--api-port", "8123", "--ui-port", "3123"], env={"COLUMNS": "200"})
    ui = no_processes[0][1]
    assert ui.env["NEXT_PUBLIC_API_URL"] == "http://localhost:8123"
    assert ui.env["NEXT_PUBLIC_WS_URL"] == "ws://localhost:8123/v1/live"


# ---------------------------------------------------------------------------
# Run discovery
# ---------------------------------------------------------------------------


def test_list_runs_skips_folders_without_a_readable_run_json(empty_runs: Path) -> None:
    write_run(empty_runs, "MUM-A-baked", mode="baked", bundle="MUM-2019-07-02")
    (empty_runs / "not-a-run").mkdir()
    broken = empty_runs / "MUM-broken"
    broken.mkdir()
    (broken / "run.json").write_text("{ not json", encoding="utf-8")

    runs = tasks.list_runs()
    assert [run["run_id"] for run in runs] == ["MUM-A-baked"]


def test_list_runs_filters_by_bundle(empty_runs: Path) -> None:
    write_run(empty_runs, "MUM-A-baked", mode="baked", bundle="MUM-2019-07-02")
    write_run(empty_runs, "MUM-B-baked", mode="baked", bundle="MUM-IDF-25yr")
    assert [run["run_id"] for run in tasks.list_runs("MUM-IDF-25yr")] == ["MUM-B-baked"]


def test_baked_runs_accepts_the_run_id_suffix_or_the_mode_field(empty_runs: Path) -> None:
    write_run(empty_runs, "MUM-A-baked", bundle="MUM-2019-07-02")
    write_run(empty_runs, "MUM-B-live", mode="live", bundle="MUM-2019-07-02")
    assert [run["run_id"] for run in tasks.baked_runs("MUM-2019-07-02")] == ["MUM-A-baked"]


def test_list_runs_on_a_missing_folder_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tasks, "runs_dir", lambda: tmp_path / "nope")
    assert tasks.list_runs() == []


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_doctor_reports_tools_engines_and_data(app: typer.Typer, empty_runs: Path) -> None:
    result = runner.invoke(app, ["doctor"], env={"COLUMNS": "200"})
    assert result.exit_code in (0, 1), result.stdout
    for expected in ("node", "pnpm", "uv", "Python"):
        assert expected.lower() in result.stdout.lower()


def test_engine_status_is_true_for_a_real_engine() -> None:
    importable, has_cli = tasks.engine_status("api")
    assert importable is True
    assert has_cli is True


def test_engine_status_is_false_for_an_unknown_engine() -> None:
    assert tasks.engine_status("not_an_engine") == (False, False)


# ---------------------------------------------------------------------------
# Optional engine registration (main.py)
# ---------------------------------------------------------------------------


def test_load_sub_app_finds_the_api_cli() -> None:
    sub_app, reason = cli_main.load_sub_app("varuna_api.cli")
    assert isinstance(sub_app, typer.Typer)
    assert reason is None


def test_load_sub_app_reports_a_missing_module() -> None:
    sub_app, reason = cli_main.load_sub_app("varuna_does_not_exist.cli")
    assert sub_app is None
    assert reason and "ModuleNotFoundError" in reason


def test_load_sub_app_rejects_a_non_typer_attribute() -> None:
    sub_app, reason = cli_main.load_sub_app("varuna_cli.tasks", attr="TASKS")
    assert sub_app is None
    assert reason and "not a typer.Typer" in reason


def test_root_app_exposes_the_api_engine_group() -> None:
    """``varuna api serve`` must exist now that services/api ships a CLI."""
    result = runner.invoke(cli_main.app, ["--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "api" in result.stdout


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_load_config_falls_back_to_the_documented_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken .env must not stop the task runner (rule 5: the demo path is sacred)."""
    monkeypatch.setattr(
        tasks, "load_config", tasks.load_config
    )  # keep the real function; only the import inside it is exercised
    config = tasks.load_config()
    assert config.city
    assert config.bundle
    assert config.api_port > 0 and config.ui_port > 0


def test_no_bundle_message_names_the_bundle() -> None:
    message = tasks.NO_BUNDLE_MESSAGE.format(bundle="MUM-2019-07-02")
    assert "make bundle BUNDLE=MUM-2019-07-02" in message
    assert "make bake BUNDLE=MUM-2019-07-02" in message


def test_demo_video_refuses_to_write_inside_the_repository(app: typer.Typer) -> None:
    """P10.7: a 1080p take is ~100 MB of video and belongs on the demo laptops, not in git."""
    result = runner.invoke(
        app, ["demo-video", "--out", str(repo_root() / "take.webm")], env={"COLUMNS": "200"}
    )
    assert result.exit_code == 2, result.stdout
    assert "inside the repository" in result.stdout


def test_demo_video_defaults_beside_the_repository() -> None:
    from varuna_cli.tasks import default_video_path

    path = default_video_path()
    assert not path.is_relative_to(repo_root())
    assert path.suffix == ".webm"

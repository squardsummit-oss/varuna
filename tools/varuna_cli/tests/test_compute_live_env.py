"""``make dev`` and ``make demo`` switch Compute live on for the API they launch.

The API reads ``VARUNA_COMPUTE_LIVE`` from its process environment only
(``services/api/varuna_api/routers/cycle.py``), and nothing set it, so the console's Compute live
button read "off on this server" even on the demo laptop. These tests pin that both launchers set
it and that an explicit ``0`` in the shell or in ``.env`` still wins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner
from varuna_cli import tasks

runner = CliRunner()


@pytest.fixture
def app() -> typer.Typer:
    fresh = typer.Typer(no_args_is_help=True, add_completion=False)
    tasks.register(fresh)
    return fresh


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[tasks.Service]]:
    """What ``dev``/``demo`` would start, with no process started and no ``.env`` read."""
    captured: list[list[tasks.Service]] = []

    def fake_run_concurrently(services: list[tasks.Service], **_: Any) -> int:
        captured.append(list(services))
        return 0

    monkeypatch.setattr(tasks.procs, "run_concurrently", fake_run_concurrently)
    monkeypatch.setattr(tasks.procs, "resolve_executable", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(tasks, "repo_root", lambda: tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(tasks, "runs_dir", lambda: runs)
    monkeypatch.delenv(tasks.COMPUTE_LIVE_ENV, raising=False)
    return captured


@pytest.mark.parametrize("command", [["dev"], ["demo"]])
def test_the_local_api_gets_compute_live(
    app: typer.Typer, launched: list[list[tasks.Service]], command: list[str]
) -> None:
    result = runner.invoke(app, command, env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.stdout
    api = next(service for service in launched[0] if service.name == "api")
    assert api.env[tasks.COMPUTE_LIVE_ENV] == "1"
    assert "Compute live on" in result.stdout


def test_an_explicit_zero_in_the_shell_wins(
    app: typer.Typer, launched: list[list[tasks.Service]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(tasks.COMPUTE_LIVE_ENV, "0")
    result = runner.invoke(app, ["dev"], env={"COLUMNS": "200"})
    assert launched[0][0].env[tasks.COMPUTE_LIVE_ENV] == "0"
    assert "Compute live off" in result.stdout


def test_an_explicit_zero_in_dotenv_wins(
    app: typer.Typer, launched: list[list[tasks.Service]], tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("VARUNA_COMPUTE_LIVE=0\n", encoding="utf-8")
    runner.invoke(app, ["demo"], env={"COLUMNS": "200"})
    assert launched[0][0].env[tasks.COMPUTE_LIVE_ENV] == "0"


def test_the_example_env_documents_the_flag_and_its_cost() -> None:
    """``.env.example`` names the flag, the measured cost and that the deployment keeps it off."""
    from varuna_schemas.paths import repo_root

    text = (repo_root() / ".env.example").read_text(encoding="utf-8")
    assert "VARUNA_COMPUTE_LIVE=" in text
    assert "104 s" in text and "48-224 s" in text
    assert "Railway" in text

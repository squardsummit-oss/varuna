"""``make bake`` reaches the cycle engine (P5.6; SPEC.md 4.3).

It was a phase-gate placeholder - "not implemented until Phase 5", exit 2 - for three days after
P5.6 was ticked on it. These drive the task through Typer with the engine's planner and loop
replaced, so what is asserted is the wiring: the options reach the plan, a failed cycle fails the
command, and nothing runs a four-minute cycle under test.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
import typer
import varuna_cycle.bake as bake_module
from typer.testing import CliRunner
from varuna_cli import tasks
from varuna_schemas.constants import IST

runner = CliRunner()


@pytest.fixture
def app() -> typer.Typer:
    """A root app with every task attached and no engine sub-apps."""
    fresh = typer.Typer(no_args_is_help=True, add_completion=False)
    tasks.register(fresh)
    return fresh


def a_plan() -> bake_module.BakePlan:
    first = datetime(2019, 7, 2, 6, 10, tzinfo=IST)
    return bake_module.BakePlan(
        bundle="MUM-2019-07-02",
        city="mumbai",
        period=timedelta(minutes=5),
        first=first,
        last=first,
        instants=(first,),
    )


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record what the task asks of the planner and the loop, and answer with an empty report."""
    seen: dict[str, Any] = {"report": bake_module.BakeReport()}

    def fake_plan_bundle(bundle: str, **options: Any) -> bake_module.BakePlan:
        seen["plan"] = (bundle, options)
        return a_plan()

    def fake_bake_cycles(plan: bake_module.BakePlan, **options: Any) -> bake_module.BakeReport:
        seen["bake"] = (plan, options)
        return seen["report"]

    monkeypatch.setattr(bake_module, "plan_bundle", fake_plan_bundle)
    monkeypatch.setattr(bake_module, "bake_cycles", fake_bake_cycles)
    return seen


def test_bake_is_no_longer_a_phase_gate(app: typer.Typer, engine: dict[str, Any]) -> None:
    result = runner.invoke(app, ["bake"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.stdout
    assert "not implemented" not in result.stdout
    assert engine["plan"] == ("MUM-2019-07-02", {"every_min": None, "start": None, "end": None})
    # Section 11.11's carry-forward is not wired, and a bake says so rather than implying it.
    assert "P7.3" in result.stdout


def test_the_demo_stride_reaches_the_planner(app: typer.Typer, engine: dict[str, Any]) -> None:
    result = runner.invoke(
        app,
        ["bake", "--every", "30", "--from", "06:10", "--to", "09:10", "--overwrite"],
        env={"COLUMNS": "200"},
    )
    assert result.exit_code == 0, result.stdout
    assert engine["plan"] == ("MUM-2019-07-02", {"every_min": 30, "start": "06:10", "end": "09:10"})
    assert engine["bake"][1]["overwrite"] is True


def test_a_failed_cycle_fails_the_command(app: typer.Typer, engine: dict[str, Any]) -> None:
    engine["report"].failed.append((a_plan().first, "RuntimeError: Twin diverged"))
    result = runner.invoke(app, ["bake"], env={"COLUMNS": "200"})
    assert result.exit_code == 1, result.stdout
    assert "Traceback" not in result.stdout


def test_a_bundle_that_cannot_be_planned_says_why(
    app: typer.Typer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(bundle: str, **_: Any) -> bake_module.BakePlan:
        msg = f"{bundle} has 0 radar frames and a cycle needs 3. Run make bundle BUNDLE={bundle}."
        raise ValueError(msg)

    monkeypatch.setattr(bake_module, "plan_bundle", refuse)
    result = runner.invoke(app, ["bake"], env={"COLUMNS": "200"})
    assert result.exit_code == 1
    assert "make bundle" in result.stdout
    assert "Traceback" not in result.stdout

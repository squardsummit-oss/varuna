"""``varuna whatif prewarm``: every rain x tide combination asked for, stored where asked.

The scenario itself is `varuna_api.routers.whatif.prewarm_scenario`, which has its own tests on a
synthetic city (`services/api/tests/test_whatif_twin.py`); here it is replaced by a recorder, so
what is pinned is the command's contract - the combinations it runs, the flags it passes on, what
it prints, and that a refusal is reported and fails the command rather than being swallowed.
"""

from __future__ import annotations

from typing import Any

import pytest
import typer
from typer.testing import CliRunner
from varuna_api.routers import whatif as whatif_router
from varuna_api.state import api_error
from varuna_cli.main import build_app


def _result() -> dict[str, Any]:
    # `tide_in_m3` is the clamp's gross tally on the sea cells and is never printed as the sea
    # that entered the city; `sea_to_land_m3` is.
    return {
        "ms": 61_000,
        "n_changed": 1,
        "hotspots_moved": 0,
        "sea": {
            "tide_in_m3": 19_000_000.0,
            "sea_to_land_m3": 1_260_000.0,
            "sea_to_land_change_m3": 400_000.0,
        },
        "baseline": {"drift": {"n_changed": 952, "max_abs_delta_cm": 83.6}},
    }


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def fake(run_id: str, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        seen.append({"run_id": run_id, **kwargs})
        if kwargs["tide_offset_m"] > 5:
            raise api_error(422, "out_of_range", "Tide offset runs from -0.5 to +1.0 m.")
        return _result(), {"source": "computed", "path": "whatif/RUN/key.json.gz", "label": ""}

    monkeypatch.setattr(whatif_router, "prewarm_scenario", fake)
    return seen


def _app() -> typer.Typer:
    return build_app(typer.Typer())


def test_it_runs_each_tide_and_passes_the_flags_on(calls: list[dict[str, Any]]) -> None:
    result = CliRunner().invoke(
        _app(), ["whatif", "prewarm", "--run", "RUN", "--tide", "0.5", "--tide", "1.0", "--shipped"]
    )
    assert result.exit_code == 0, result.output
    assert [c["tide_offset_m"] for c in calls] == [0.5, 1.0]
    assert all(c["rain_scale"] == 1.0 and c["shipped"] is True for c in calls)
    assert "rain 1.0x, tide +0.5 m: computed in 61.0 s" in result.output
    assert "sea onto land 1.26 Mm3 (+0.40 Mm3 against the tide as forecast)" in result.output
    assert "19.00" not in result.output
    assert "differs from this Twin with nothing changed on 952 streets" in result.output


def test_a_refusal_is_reported_and_fails_the_command(calls: list[dict[str, Any]]) -> None:
    result = CliRunner().invoke(_app(), ["whatif", "prewarm", "--run", "RUN", "--tide", "9"])
    assert result.exit_code == 1
    assert "refused" in result.output
    assert "Tide offset runs from" in result.output

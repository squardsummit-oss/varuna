"""The text the pin scorer serves beside its scores says what the runs are (rule 6).

The reason for the missing depth Brier score used to state a 20-member ensemble "not the 50 the
spec asks for" long after the runs were re-baked at 50; it now reads ``ensemble_n`` from each
scored run's ``run.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from varuna_verify.event import _ensemble_sizes


def _run(root: Path, run_id: str, meta: object) -> None:
    (root / run_id).mkdir(parents=True)
    (root / run_id / "run.json").write_text(json.dumps(meta), encoding="utf-8")


def test_ensemble_sizes_come_from_the_scored_runs(tmp_path: Path) -> None:
    _run(tmp_path, "a", {"ensemble_n": 50})
    _run(tmp_path, "b", {"ensemble_n": 50})
    _run(tmp_path, "c", {"ensemble_n": 20})
    _run(tmp_path, "unscored", {"ensemble_n": 7})
    assert _ensemble_sizes(tmp_path, ["a", "b"]) == [50]
    assert _ensemble_sizes(tmp_path, ["a", "c"]) == [20, 50]


def test_an_unreadable_or_silent_run_is_left_out_not_guessed(tmp_path: Path) -> None:
    _run(tmp_path, "no-size", {"mode": "baked"})
    _run(tmp_path, "flag", {"ensemble_n": True})
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "run.json").write_text("{", encoding="utf-8")
    assert _ensemble_sizes(tmp_path, ["no-size", "flag", "broken", "missing"]) == []

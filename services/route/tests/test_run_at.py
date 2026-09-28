"""Which run a reachability scrub reads (SPEC.md 7.4 AC4).

A scrub to 07:40 asks what a facility could reach at 07:40 on the forecast the city had then. It
used to be answered from the newest run whatever the instant, whose first step is 09:15 on the
2 July demo set, so the whole morning before that clamped to one step of a later forecast.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from varuna_route.forecast import latest_run_dir, load_depths, run_dir_at

IST = timezone(timedelta(hours=5, minutes=30))

CYCLES = ["0110Z", "0140Z", "0210Z", "0240Z", "0310Z", "0340Z"]
"""06:40 to 09:10 IST, the shipped demo set's cycles after its first."""


def _write_run(root: Path, name: str, first_step: datetime) -> None:
    folder = root / "runs" / name
    folder.mkdir(parents=True)
    wet = {
        "run_id": name,
        "valid_ts": [(first_step + timedelta(minutes=5 * k)).isoformat() for k in range(36)],
        "n_segments_total": 1,
        "depth_cm": {"S1": [0.0] * 36},
    }
    (folder / "segments_wet.json").write_text(json.dumps(wet), encoding="utf-8")


@pytest.fixture
def runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    for stamp in CYCLES:
        cycle = datetime.strptime(f"20190702T{stamp}", "%Y%m%dT%H%MZ").replace(tzinfo=UTC)
        _write_run(
            tmp_path,
            f"MUM-20190702T{stamp}-sky1.0-twin1.0-flash0.1-baked",
            cycle + timedelta(minutes=5),
        )
    # An older bake of one cycle, and another city's newer run: neither may be picked.
    _write_run(
        tmp_path,
        "MUM-20190702T0210Z-sky1.0-twin1.0-flash0.0-baked",
        datetime(2019, 7, 2, 7, 45, tzinfo=IST),
    )
    _write_run(
        tmp_path,
        "CHN-20260701T0030Z-sky1.0-twin1.0-flash0.0-baked",
        datetime(2026, 7, 1, 6, 5, tzinfo=IST),
    )
    return tmp_path


def _cycle(path: Path) -> str:
    return path.name[13:18]


def test_a_scrub_reads_the_run_issued_by_then(runs: Path) -> None:
    at = lambda hh, mm: datetime(2019, 7, 2, hh, mm, tzinfo=IST)  # noqa: E731
    assert _cycle(run_dir_at(at(6, 40))) == "0110Z"
    assert _cycle(run_dir_at(at(7, 55))) == "0210Z"
    assert _cycle(run_dir_at(at(8, 40))) == "0310Z"
    assert _cycle(run_dir_at(at(11, 30))) == "0340Z"
    # The newest is what the old path answered at every one of those instants.
    assert _cycle(latest_run_dir()) == "0340Z"


def test_ties_break_as_the_newest_lookup_breaks_them(runs: Path) -> None:
    picked = run_dir_at(datetime(2019, 7, 2, 7, 45, tzinfo=IST))
    assert picked.name == "MUM-20190702T0210Z-sky1.0-twin1.0-flash0.1-baked"


def test_before_the_first_cycle_the_earliest_run_answers(runs: Path) -> None:
    assert _cycle(run_dir_at(datetime(2019, 7, 2, 5, 0, tzinfo=IST))) == "0110Z"


def test_a_city_reads_only_its_own_runs(runs: Path) -> None:
    assert run_dir_at(datetime(2026, 7, 1, 7, 0, tzinfo=IST), "chennai").name.startswith("CHN-")
    assert run_dir_at(datetime(2026, 7, 1, 7, 0, tzinfo=IST)).name.startswith("MUM-")


def test_load_depths_takes_the_instant_only_without_a_run_id(runs: Path) -> None:
    at = datetime(2019, 7, 2, 7, 10, tzinfo=IST)
    assert load_depths(at=at).run_id.startswith("MUM-20190702T0140Z")
    pinned = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"
    assert load_depths(pinned, at=at).run_id == pinned
    assert load_depths().run_id.startswith("MUM-20190702T0340Z")


def test_nothing_baked_names_the_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VARUNA_DATA_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="make bake"):
        run_dir_at(datetime(2019, 7, 2, 7, 10, tzinfo=IST))

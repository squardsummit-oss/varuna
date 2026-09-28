"""The tide runs past ``t1`` to the last cycle's forecast horizon.

``tide.csv`` is the one stream that does not stop at the window's end: a cycle at ``t1``
forecasts three hours on, the tide is its sea boundary, and the Twin holds the stage flat after
the last row. On MUM-2019-07-02 a series that stopped at 09:40 froze the sea at +1.236 m (DEM
frame) for the last two hours of the 08:40 cycle while the bundle's own harmonic reaches +2.22 m
at 11:30. Each test here changes the tide in an otherwise valid bundle and checks what the
generator, the validator and the replay clock make of it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from varuna_replay import bundle as members
from varuna_replay import evidence, streams
from varuna_replay.bundle import TIDE_CSV, TIDE_LOOKAHEAD_MIN, BundleLayout, load_manifest
from varuna_replay.clock import ReplayStreams
from varuna_replay.validate import validate_bundle
from varuna_schemas.constants import IST, LEAD_MAX_MIN


def _rewrite_tide(
    root: Path, *, start_min: int, end_min: int, skip: frozenset[int] = frozenset()
) -> int:
    """Replace the bundle's tide with a flat series from ``t0 + start_min`` to ``t0 + end_min``."""
    manifest = load_manifest(root)
    step = manifest.cadences["tide"]
    rows = [
        {"ts": manifest.t0 + timedelta(minutes=minute), "stage_m": 2.4, "source": "illustrative"}
        for minute in range(start_min, end_min + 1, step)
        if minute not in skip
    ]
    members.write_tide(BundleLayout(root=root).tide, rows)
    return len(rows)


def _tide_findings(root: Path, level: str) -> list[str]:
    report = validate_bundle(root)
    return [
        finding.message
        for finding in report.for_rule("B4")
        if finding.file == TIDE_CSV and finding.level == level
    ]


# ============================================================================ the constant
def test_the_lookahead_is_the_forecast_horizon() -> None:
    """Three hours: 36 steps of 5 minutes, the lead every cycle forecasts to."""
    assert TIDE_LOOKAHEAD_MIN == LEAD_MAX_MIN == 180


# ============================================================================ the generator
def test_a_longer_series_keeps_the_windows_rows_and_reaches_the_crest() -> None:
    """Every row is a function of its own instant, so extending the series appends to it and
    moves no stage the window already had; and the extension carries the sourced high water."""
    common = {
        "high_water_m": evidence.TIDE_HIGH_WATER_M,
        "high_water_at": evidence.TIDE_HIGH_WATER_IST,
        "period_min": evidence.TIDE_PERIOD_MIN,
    }
    window = streams.tide_rows(evidence.T0, float(evidence.WINDOW_MIN), **common)
    extended = streams.tide_rows(
        evidence.T0, float(evidence.WINDOW_MIN + TIDE_LOOKAHEAD_MIN), **common
    )
    assert extended[: len(window)] == window
    assert len(extended) == (evidence.WINDOW_MIN + TIDE_LOOKAHEAD_MIN) // 15 + 1 == 29
    assert extended[-1]["ts"] == datetime(2019, 7, 2, 12, 40, tzinfo=IST)
    assert all(row["source"] == "illustrative" for row in extended)

    crest = max(extended, key=lambda row: row["stage_m"])
    assert crest["ts"] == datetime(2019, 7, 2, 11, 25, tzinfo=IST), "the row nearest 11:30"
    assert crest["stage_m"] == pytest.approx(evidence.TIDE_HIGH_WATER_M, abs=0.005)
    assert max(row["stage_m"] for row in window) < crest["stage_m"] - 0.9


# ============================================================================ the validator
def test_a_tide_that_reaches_the_horizon_raises_nothing(full_bundle: Path) -> None:
    report = validate_bundle(full_bundle)
    assert report.ok, report.render()
    assert not [f for f in report.for_rule("B4") if f.file == TIDE_CSV], report.render()


def test_a_tide_that_stops_at_t1_is_a_warning_not_a_failure(full_bundle: Path) -> None:
    """What every bundle carried before the lookahead: valid, and now said out loud."""
    _rewrite_tide(full_bundle, start_min=0, end_min=load_manifest(full_bundle).duration_min)
    assert validate_bundle(full_bundle).ok
    warnings = _tide_findings(full_bundle, "warning")
    assert len(warnings) == 1, warnings
    assert "180 min before the last cycle's forecast horizon" in warnings[0]
    assert "holds the sea at the last stage" in warnings[0]


def test_a_tide_that_stops_before_t1_fails(full_bundle: Path) -> None:
    _rewrite_tide(full_bundle, start_min=0, end_min=load_manifest(full_bundle).duration_min - 15)
    errors = _tide_findings(full_bundle, "error")
    assert any("before manifest t1" in message for message in errors), errors


def test_a_tide_past_the_horizon_fails(full_bundle: Path) -> None:
    end = load_manifest(full_bundle).duration_min + TIDE_LOOKAHEAD_MIN + 15
    _rewrite_tide(full_bundle, start_min=0, end_min=end)
    errors = _tide_findings(full_bundle, "error")
    assert any("after the last cycle's forecast horizon" in message for message in errors), errors


def test_a_tide_that_opens_late_fails(full_bundle: Path) -> None:
    end = load_manifest(full_bundle).duration_min + TIDE_LOOKAHEAD_MIN
    _rewrite_tide(full_bundle, start_min=15, end_min=end)
    errors = _tide_findings(full_bundle, "error")
    assert any(message.startswith("starts at") for message in errors), errors


def test_a_gap_in_the_lookahead_fails(full_bundle: Path) -> None:
    duration = load_manifest(full_bundle).duration_min
    _rewrite_tide(
        full_bundle,
        start_min=0,
        end_min=duration + TIDE_LOOKAHEAD_MIN,
        skip=frozenset({duration + 60}),
    )
    errors = _tide_findings(full_bundle, "error")
    assert any("gap of 30.0 min" in message for message in errors), errors


# ============================================================================ the clock
def test_the_clock_schedules_only_the_windows_stages(full_bundle: Path) -> None:
    """The clock stops at ``t1``, so the stages after it are the Twin's and never published."""
    manifest = load_manifest(full_bundle)
    stages = [e for e in ReplayStreams.from_bundle(full_bundle).events if e.topic == "tide.stage"]
    assert len(stages) == manifest.duration_min // manifest.cadences["tide"] + 1
    assert max(event.ts for event in stages) == manifest.t1
    written = (manifest.duration_min + TIDE_LOOKAHEAD_MIN) // manifest.cadences["tide"] + 1
    assert written > len(stages)


def test_without_a_readable_manifest_the_clock_schedules_every_row(full_bundle: Path) -> None:
    manifest = load_manifest(full_bundle)
    written = _rewrite_tide(
        full_bundle, start_min=0, end_min=manifest.duration_min + TIDE_LOOKAHEAD_MIN
    )
    (full_bundle / "manifest.json").write_text("not a manifest", encoding="utf-8")
    stages = [e for e in ReplayStreams.from_bundle(full_bundle).events if e.topic == "tide.stage"]
    assert len(stages) == written

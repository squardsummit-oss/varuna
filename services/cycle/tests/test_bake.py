"""Which cycles ``make bake`` computes, in what order, and which it leaves alone (P5.6).

``make bake`` was a phase-gate placeholder while P5.6 was ticked on it. The planner is a pure
function of the bundle's forecastable window and the options, so it is tested on instants rather
than on a bundle; the loop is tested with the cycle replaced, because a real one is four minutes
of CPU and ``test_idempotence.py`` already bakes one for real.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from varuna_cycle.bake import BakePlan, bake_cycles, ladder, parse_instant, run_id_for
from varuna_schemas.constants import IST

FIVE = timedelta(minutes=5)
THIRTY = timedelta(minutes=30)


def ist(clock: str) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime(2019, 7, 2, hour, minute, tzinfo=IST)


# MUM-2019-07-02 opens at 05:40 IST with radar frames ten minutes apart, so the first cycle with
# the three frames optical flow needs behind it is 06:00; the window closes at 09:40.
FIRST, LAST = ist("06:00"), ist("09:40")
DEMO_STRIDE = {"every": THIRTY, "start": ist("06:10"), "end": ist("09:10")}


class TestTheLadder:
    def test_the_default_is_every_cycle_the_bundle_can_forecast(self) -> None:
        instants = ladder(FIRST, LAST, FIVE)
        assert instants[0] == FIRST
        assert instants[-1] == LAST
        assert len(instants) == 45
        assert all(instants[k + 1] - instants[k] == FIVE for k in range(len(instants) - 1))

    def test_a_stride_from_a_start_is_the_shipped_demo_set(self) -> None:
        instants = ladder(FIRST, LAST, FIVE, **DEMO_STRIDE)
        assert [f"{t.astimezone(IST):%H:%M}" for t in instants] == [
            "06:10",
            "06:40",
            "07:10",
            "07:40",
            "08:10",
            "08:40",
            "09:10",
        ]

    def test_the_demo_stride_names_the_cycles_that_ship(self) -> None:
        """The command rebuilds what `demo/runs` holds, rather than a set that merely looks like it."""
        from varuna_schemas.paths import repo_root

        shipped_dir = repo_root() / "demo" / "runs"
        shipped = (
            sorted(
                {p.name.split("-")[1] for p in shipped_dir.iterdir() if (p / "run.json").is_file()}
            )
            if shipped_dir.is_dir()
            else []
        )
        if not shipped:
            pytest.skip("demo/runs holds no runs in this checkout")
        instants = ladder(FIRST, LAST, FIVE, **DEMO_STRIDE)
        assert [f"{t.astimezone(UTC):%Y%m%dT%H%MZ}" for t in instants] == shipped

    def test_an_off_ladder_start_moves_up_to_the_next_cycle(self) -> None:
        assert ladder(FIRST, LAST, FIVE, start=ist("06:12"))[0] == ist("06:15")

    def test_a_start_before_the_first_forecastable_cycle_begins_at_it(self) -> None:
        assert ladder(FIRST, LAST, FIVE, start=ist("05:40"))[0] == FIRST

    def test_an_end_past_the_window_stops_at_its_last_cycle(self) -> None:
        assert ladder(FIRST, LAST, FIVE, end=ist("11:00"))[-1] == LAST

    def test_a_stride_off_the_cadence_is_refused(self) -> None:
        with pytest.raises(ValueError, match="multiple"):
            ladder(FIRST, LAST, FIVE, every=timedelta(minutes=7))

    def test_a_range_holding_no_cycle_is_refused_with_the_window(self) -> None:
        with pytest.raises(ValueError, match="06:00 to 09:40"):
            ladder(FIRST, LAST, FIVE, start=ist("09:41"))


class TestParsingAnInstant:
    T0 = datetime(2019, 7, 2, 0, 10, tzinfo=UTC)  # 05:40 IST

    def test_a_clock_time_is_ist_on_the_bundle_day(self) -> None:
        assert parse_instant("06:10", on=self.T0) == ist("06:10")

    def test_an_iso_instant_keeps_its_own_offset(self) -> None:
        assert parse_instant("2019-07-02T00:40Z", on=self.T0) == ist("06:10")

    def test_an_instant_without_an_offset_is_refused(self) -> None:
        """Read in the machine's zone, a naive instant bakes the wrong cycle on a UTC runner."""
        with pytest.raises(ValueError, match="offset"):
            parse_instant("2019-07-02T06:10", on=self.T0)

    def test_nonsense_says_which_forms_are_accepted(self) -> None:
        with pytest.raises(ValueError, match="HH:MM"):
            parse_instant("tea time", on=self.T0)


class FakeCycle:
    """Stands in for `run_cycle`: records each call, and raises where it is told to."""

    def __init__(self, fail_at: datetime | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_at = fail_at

    def __call__(self, bundle: str, cycle_ts: datetime, **options: Any) -> Any:
        self.calls.append({"bundle": bundle, "cycle_ts": cycle_ts, **options})
        if cycle_ts == self.fail_at:
            msg = "Twin diverged"
            raise RuntimeError(msg)
        return SimpleNamespace(run_id=run_id_for("mumbai", cycle_ts, "0.1"))


def plan_of(*clocks: str) -> BakePlan:
    instants = tuple(ist(clock) for clock in clocks)
    return BakePlan(
        bundle="MUM-2019-07-02",
        city="mumbai",
        period=FIVE,
        first=min(instants),
        last=max(instants),
        instants=instants,
    )


def a_run(root: Path, run_id: str) -> None:
    (root / run_id).mkdir(parents=True)
    (root / run_id / "run.json").write_text("{}", encoding="utf-8")


class TestTheLoop:
    def test_cycles_run_oldest_first_as_baked_runs(self, tmp_path: Path) -> None:
        cycle = FakeCycle()
        report = bake_cycles(
            plan_of("07:10", "06:10", "06:40"),
            runner=cycle,
            runs_root=tmp_path,
            flash_version="0.1",
        )
        assert [call["cycle_ts"] for call in cycle.calls] == [
            ist("06:10"),
            ist("06:40"),
            ist("07:10"),
        ]
        assert {call["mode"] for call in cycle.calls} == {"baked"}
        assert {call["city"] for call in cycle.calls} == {"mumbai"}
        assert len(report.baked) == 3
        assert report.ok

    def test_a_cycle_already_baked_is_skipped_so_a_bake_resumes(self, tmp_path: Path) -> None:
        done = run_id_for("mumbai", ist("06:40"), "0.1")
        a_run(tmp_path, done)
        cycle = FakeCycle()
        report = bake_cycles(
            plan_of("06:10", "06:40", "07:10"),
            runner=cycle,
            runs_root=tmp_path,
            flash_version="0.1",
        )
        assert [call["cycle_ts"] for call in cycle.calls] == [ist("06:10"), ist("07:10")]
        assert report.skipped == [(ist("06:40"), done)]

    def test_a_single_member_run_from_before_the_fit_is_not_this_bake(self, tmp_path: Path) -> None:
        """The demo set was baked before Flash-lite was fitted. Its flash0.0 runs must not stop
        the twenty-member flash0.1 runs that replace them from being made."""
        a_run(tmp_path, run_id_for("mumbai", ist("06:10"), "0.0"))
        cycle = FakeCycle()
        bake_cycles(plan_of("06:10"), runner=cycle, runs_root=tmp_path, flash_version="0.1")
        assert len(cycle.calls) == 1

    def test_overwrite_recomputes_what_exists(self, tmp_path: Path) -> None:
        a_run(tmp_path, run_id_for("mumbai", ist("06:10"), "0.1"))
        cycle = FakeCycle()
        bake_cycles(
            plan_of("06:10"),
            runner=cycle,
            runs_root=tmp_path,
            flash_version="0.1",
            overwrite=True,
        )
        assert len(cycle.calls) == 1
        assert cycle.calls[0]["overwrite"] is True

    def test_a_failed_cycle_is_reported_and_the_bake_carries_on(self, tmp_path: Path) -> None:
        cycle = FakeCycle(fail_at=ist("06:40"))
        report = bake_cycles(
            plan_of("06:10", "06:40", "07:10"),
            runner=cycle,
            runs_root=tmp_path,
            flash_version="0.1",
        )
        assert len(cycle.calls) == 3
        assert not report.ok
        assert report.failed == [(ist("06:40"), "RuntimeError: Twin diverged")]
        assert len(report.baked) == 2

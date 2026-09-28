"""The replay clock (task P2.7): simulated time, stream publication and cycle triggering.

Every test drives a fake monotonic source, so the suite is deterministic and instant: nothing
here sleeps. The rules being pinned are the ones SPEC.md 10.2 states and the ones a demo
depends on - 30x means 30 simulated minutes per wall-clock minute, a seek clamps to the
window, a paused clock publishes nothing, and a cycle never triggers twice for the same
instant even after seeking backwards.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from varuna_cycle.bus import Bus, BusEvent
from varuna_cycle.registry import RunRegistry
from varuna_replay.bundle import BundleLayout, BundleNotFoundError, load_manifest
from varuna_replay.clock import (
    DEFAULT_SPEED,
    END_NOTE,
    LIVE_NOTE,
    REPLAY_SPEEDS,
    STREAM_TOPICS,
    ReplayClock,
    ReplayStreams,
)
from varuna_schemas.constants import IST
from varuna_schemas.models import RunMeta
from varuna_schemas.models.run import RunMode, build_run_id
from varuna_schemas.samples import sample

T0 = datetime(2019, 7, 2, 5, 40, tzinfo=IST)
WINDOW_MIN = 60


class FakeMonotonic:
    """A monotonic clock the test moves by hand."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += float(seconds)
        return self.now


@pytest.fixture
def fake() -> FakeMonotonic:
    return FakeMonotonic()


@pytest.fixture
def bus() -> Bus:
    return Bus()


@pytest.fixture
def registry(tmp_path: Path) -> RunRegistry:
    return RunRegistry(tmp_path / "runs")


@pytest.fixture
def make_clock(
    full_bundle: Path, fake: FakeMonotonic, bus: Bus, registry: RunRegistry
) -> Callable[..., ReplayClock]:
    """Build a clock over the complete test bundle with the fake time source."""

    def _make(**kwargs: Any) -> ReplayClock:
        options: dict[str, Any] = {
            "bus": bus,
            "layout": BundleLayout.for_bundle(full_bundle),
            "registry": registry,
            "time_source": fake,
            "autostart": False,
        }
        options.update(kwargs)
        return ReplayClock(load_manifest(full_bundle), **options)

    return _make


def topics(events: list[BusEvent]) -> list[str]:
    return [event.topic for event in events]


def baked_run(cycle_ts: datetime, bundle_id: str) -> RunMeta:
    """A run.json for one baked cycle of the test bundle."""
    meta = sample("RunMeta")
    assert isinstance(meta, RunMeta)
    mode: RunMode = "baked"
    return meta.model_copy(
        update={
            "run_id": build_run_id("mumbai", cycle_ts, "1.0", "1.0", "0.3", mode),
            "cycle_ts": cycle_ts,
            "mode": mode,
            "bundle": bundle_id,
            "city": "mumbai",
        }
    )


# --------------------------------------------------------------------------- simulated time
async def test_thirty_times_advances_thirty_simulated_minutes_per_wall_minute(
    make_clock: Callable[..., ReplayClock], fake: FakeMonotonic
) -> None:
    clock = make_clock(speed=30)
    await clock.play()
    fake.advance(60)
    assert clock.sim_time == T0 + timedelta(minutes=30)
    fake.advance(30)
    assert clock.sim_time == T0 + timedelta(minutes=45)


async def test_simulated_time_is_anchored_so_it_cannot_drift(
    make_clock: Callable[..., ReplayClock], fake: FakeMonotonic
) -> None:
    """Polling often must not change where the clock is: sim time comes from one anchor."""
    clock = make_clock(speed=10)
    await clock.play()
    elapsed = 0.0
    for _ in range(37):
        fake.advance(1.37)
        elapsed += 1.37
        await clock.poll()
    assert clock.sim_time == T0 + timedelta(seconds=elapsed * 10)


async def test_pause_freezes_the_clock_and_play_resumes_from_there(
    make_clock: Callable[..., ReplayClock], fake: FakeMonotonic
) -> None:
    clock = make_clock(speed=30)
    await clock.play()
    fake.advance(20)
    await clock.pause()
    frozen = clock.sim_time
    fake.advance(600)
    assert clock.sim_time == frozen
    await clock.play()
    fake.advance(10)
    assert clock.sim_time == frozen + timedelta(minutes=5)


async def test_changing_speed_does_not_jump_the_clock(
    make_clock: Callable[..., ReplayClock], fake: FakeMonotonic
) -> None:
    clock = make_clock(speed=10)
    await clock.play()
    fake.advance(60)
    before = clock.sim_time
    await clock.set_speed(60)
    assert clock.sim_time == before
    fake.advance(10)
    assert clock.sim_time == before + timedelta(minutes=10)


async def test_speed_must_be_one_the_console_offers(
    make_clock: Callable[..., ReplayClock],
) -> None:
    clock = make_clock()
    with pytest.raises(ValueError, match="1x, 10x, 30x, 60x"):
        await clock.set_speed(7)
    assert clock.speed == DEFAULT_SPEED
    assert clock.speed in REPLAY_SPEEDS


async def test_seek_clamps_to_the_bundle_window(make_clock: Callable[..., ReplayClock]) -> None:
    clock = make_clock()
    t1 = T0 + timedelta(minutes=WINDOW_MIN)

    await clock.seek(T0 - timedelta(hours=3))
    assert clock.sim_time == T0

    await clock.seek(t1 + timedelta(hours=3))
    assert clock.sim_time == t1

    await clock.seek((T0 + timedelta(minutes=20)).isoformat())
    assert clock.sim_time == T0 + timedelta(minutes=20)


async def test_the_clock_stops_at_the_end_of_the_window_and_says_so(
    make_clock: Callable[..., ReplayClock], fake: FakeMonotonic
) -> None:
    clock = make_clock(speed=60, mode="live")
    await clock.play()
    fake.advance(600)  # 600 wall seconds at 60x = 10 simulated hours
    await clock.poll()
    assert clock.sim_time == T0 + timedelta(minutes=WINDOW_MIN)
    assert clock.playing is False
    assert clock.note == END_NOTE
    assert await clock.poll() == []


async def test_play_at_the_end_runs_the_window_again_from_the_start(
    make_clock: Callable[..., ReplayClock], fake: FakeMonotonic
) -> None:
    # The clock is shared by every visitor: one who plays to the end must not strand the next.
    clock = make_clock(speed=60, mode="live")
    await clock.play()
    fake.advance(600)
    await clock.poll()
    assert clock.note == END_NOTE

    await clock.play()
    assert clock.sim_time == T0
    assert clock.playing is True
    assert clock.note is None


# --------------------------------------------------------------------------- publication
async def test_a_paused_clock_publishes_nothing(
    make_clock: Callable[..., ReplayClock], fake: FakeMonotonic
) -> None:
    clock = make_clock(speed=30)
    fake.advance(120)
    assert await clock.poll() == []

    await clock.play()
    fake.advance(60)
    assert await clock.poll() != []

    await clock.pause()
    fake.advance(600)
    assert await clock.poll() == []


async def test_playing_publishes_every_bundle_stream_on_its_blueprint_topic(
    make_clock: Callable[..., ReplayClock], fake: FakeMonotonic
) -> None:
    clock = make_clock(speed=60, mode="live")
    await clock.play()
    fake.advance(60)  # the whole 60-minute window
    published = topics(await clock.poll())
    for topic in STREAM_TOPICS:
        assert topic in published, f"{topic} never reached the bus"
    assert "replay.clock" in published


async def test_a_stream_event_is_published_once_per_pass(
    make_clock: Callable[..., ReplayClock], fake: FakeMonotonic
) -> None:
    clock = make_clock(speed=60, mode="live")
    await clock.play()
    fake.advance(60)
    first = [t for t in topics(await clock.poll()) if t in STREAM_TOPICS]
    assert first
    fake.advance(60)
    assert [t for t in topics(await clock.poll()) if t in STREAM_TOPICS] == []


async def test_replay_clock_events_carry_the_snapshot(
    make_clock: Callable[..., ReplayClock], bus: Bus
) -> None:
    clock = make_clock(speed=30)
    await clock.seek(T0 + timedelta(minutes=25))
    event = bus.last("replay.clock")
    assert event is not None
    assert event.payload["bundle_id"] == clock.bundle_id
    assert event.payload["sim_time"] == (T0 + timedelta(minutes=25)).isoformat()
    assert event.payload["playing"] is False
    assert event.payload["speed"] == 30
    assert event.payload["mode"] == "baked"


# --------------------------------------------------------------------------- cycles
async def test_a_cycle_triggers_once_per_five_simulated_minutes(
    make_clock: Callable[..., ReplayClock], fake: FakeMonotonic
) -> None:
    fired: list[datetime] = []
    clock = make_clock(speed=30, mode="live", on_cycle=lambda _c, ts: fired.append(ts))
    await clock.play()

    await clock.poll()  # the cycle the clock opens in
    assert fired == [T0]

    for step in range(1, 5):
        for _ in range(5):  # poll far more often than a cycle boundary arrives
            fake.advance(2)
            await clock.poll()
        assert fired[-1] == T0 + timedelta(minutes=5 * step)
    assert fired == [T0 + timedelta(minutes=5 * i) for i in range(5)]


async def test_seeking_forward_triggers_only_the_cycle_the_clock_lands_in(
    make_clock: Callable[..., ReplayClock],
) -> None:
    fired: list[datetime] = []
    clock = make_clock(speed=30, mode="live", on_cycle=lambda _c, ts: fired.append(ts))
    await clock.seek(T0 + timedelta(minutes=30))
    await clock.play()
    await clock.poll()
    assert fired == [T0 + timedelta(minutes=30)]


async def test_a_cycle_never_triggers_twice_after_seeking_backwards(
    make_clock: Callable[..., ReplayClock], fake: FakeMonotonic
) -> None:
    fired: list[datetime] = []
    clock = make_clock(speed=30, mode="live", on_cycle=lambda _c, ts: fired.append(ts))
    await clock.play()
    for _ in range(6):
        await clock.poll()
        fake.advance(10)
    first_pass = list(fired)
    assert len(first_pass) > 1

    await clock.seek(T0)
    await clock.play()
    for _ in range(6):
        await clock.poll()
        fake.advance(10)
    assert fired == first_pass


async def test_rewind_lets_the_bundle_play_again_from_the_top(
    make_clock: Callable[..., ReplayClock], fake: FakeMonotonic
) -> None:
    fired: list[datetime] = []
    clock = make_clock(speed=30, mode="live", on_cycle=lambda _c, ts: fired.append(ts))
    await clock.play()
    await clock.poll()
    fake.advance(10)
    await clock.poll()
    assert len(fired) == 2

    await clock.rewind()
    assert clock.sim_time == T0
    assert clock.playing is False
    await clock.play()
    await clock.poll()
    assert fired[-1] == T0


async def test_live_mode_without_an_orchestrator_says_where_it_lands(
    make_clock: Callable[..., ReplayClock],
) -> None:
    clock = make_clock(mode="live")
    await clock.play()
    await clock.poll()
    assert clock.note == LIVE_NOTE
    assert "Phase 5" in clock.note


async def test_baked_mode_publishes_the_run_for_the_cycle(
    make_clock: Callable[..., ReplayClock], registry: RunRegistry, fake: FakeMonotonic
) -> None:
    cycle_ts = T0 + timedelta(minutes=10)
    clock = make_clock(speed=30, mode="baked")
    meta = baked_run(cycle_ts, clock.bundle_id)
    registry.write_meta(meta)

    await clock.play()
    await clock.poll()
    fake.advance(20)  # 10 simulated minutes
    published = await clock.poll()

    runs = [event for event in published if event.topic == "runs.published"]
    assert len(runs) == 1
    assert runs[0].payload["run_id"] == meta.run_id
    assert runs[0].payload["cycle_ts"] == cycle_ts.isoformat()
    assert runs[0].payload["mode"] == "baked"
    assert clock.last_run_id == meta.run_id
    assert clock.note is None


async def test_baked_mode_says_when_nothing_is_baked_instead_of_publishing(
    make_clock: Callable[..., ReplayClock],
) -> None:
    clock = make_clock(mode="baked")
    await clock.play()
    published = await clock.poll()
    assert [event for event in published if event.topic == "runs.published"] == []
    assert clock.note is not None
    assert "make bake" in clock.note
    assert clock.last_run_id is None


# --------------------------------------------------------------------------- lifecycle
async def test_start_and_stop_are_safe_to_repeat(
    make_clock: Callable[..., ReplayClock],
) -> None:
    clock = make_clock(tick_s=0.01)
    assert clock.running is False
    await clock.start()
    await clock.start()
    assert clock.running is True
    await clock.stop()
    await clock.stop()
    assert clock.running is False
    await clock.start()
    assert clock.running is True
    await clock.stop()


async def test_stopping_freezes_the_clock(
    make_clock: Callable[..., ReplayClock], fake: FakeMonotonic
) -> None:
    clock = make_clock(speed=60, tick_s=0.01)
    await clock.play()
    fake.advance(10)
    await clock.stop()
    frozen = clock.sim_time
    fake.advance(600)
    assert clock.playing is False
    assert clock.sim_time == frozen


async def test_snapshot_is_the_api_contract(
    make_clock: Callable[..., ReplayClock],
) -> None:
    clock = make_clock(speed=30)
    await clock.seek(T0 + timedelta(minutes=12))
    state = clock.snapshot()
    assert state.bundle_id == clock.bundle_id
    assert state.sim_time == T0 + timedelta(minutes=12)
    assert state.t0 == T0
    assert state.t1 == T0 + timedelta(minutes=WINDOW_MIN)
    assert state.cycle_index == 2
    assert state.n_cycles == WINDOW_MIN // 5 + 1
    assert state.next_cycle_ts == T0 + timedelta(minutes=15)
    assert 0.0 < state.progress < 1.0
    assert state.playing is False
    assert state.speed == 30


async def test_a_missing_bundle_says_how_to_make_it() -> None:
    with pytest.raises(BundleNotFoundError, match="No bundle folder"):
        ReplayClock.for_bundle("MUM-NOT-A-BUNDLE")


# --------------------------------------------------------------------------- the schedule
def test_streams_schedule_every_member_of_the_bundle(full_bundle: Path) -> None:
    manifest = load_manifest(full_bundle)
    streams = ReplayStreams.from_bundle(full_bundle)
    counts = streams.counts()

    def expected(key: str) -> int:
        return WINDOW_MIN // manifest.cadences[key] + 1

    assert streams.missing == ()
    assert counts["radar.frames"] == expected("radar")
    assert counts["gauges.obs"] == expected("gauges")
    assert counts["tide.stage"] == expected("tide")
    assert counts["traffic.speeds"] == expected("traffic")
    assert counts["reports.raw"] == 1
    assert [event.ts for event in streams.events] == sorted(e.ts for e in streams.events)


def test_stream_payloads_carry_small_data_inline_and_big_data_by_pointer(
    full_bundle: Path,
) -> None:
    streams = ReplayStreams.from_bundle(full_bundle)
    by_topic: dict[str, Any] = {}
    for event in streams.events:  # the first event on each topic
        by_topic.setdefault(event.topic, event)

    gauges = by_topic["gauges.obs"].payload
    assert gauges["n"] == len(gauges["readings"]) == 2
    assert gauges["synthetic"] is True
    assert gauges["readings"][0]["station_id"] in {"santacruz", "colaba"}

    tide = by_topic["tide.stage"].payload
    assert tide["stage_m"] == 2.4
    assert tide["source"] == "illustrative"

    traffic = by_topic["traffic.speeds"].payload
    assert traffic["member"] == "traffic/speeds.parquet"
    assert traffic["rows"] == 2
    assert "kmh" not in traffic

    radar = by_topic["radar.frames"].payload
    assert radar["member"] == "radar/frames.zarr"
    assert radar["index"] == 0
    assert radar["n_px"] > 0


def test_absent_members_are_reported_not_guessed(full_bundle: Path) -> None:
    (full_bundle / "gauges.csv").unlink()
    (full_bundle / "reports.jsonl").unlink()
    streams = ReplayStreams.from_bundle(full_bundle)
    assert set(streams.missing) == {"gauges.csv", "reports.jsonl"}
    assert streams.counts()["gauges.obs"] == 0
    assert streams.counts()["tide.stage"] > 0

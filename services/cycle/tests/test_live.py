"""Live cycles: the NWP forcing, the sea level, and the housekeeping around them."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest
from varuna_cycle import live
from varuna_schemas.constants import IST

CYCLE = datetime(2026, 9, 30, 14, 35, tzinfo=IST)


def _payload(stamps: list[int], members: dict[str, list[float]]) -> dict:
    return {"hourly": {"time": stamps, **members}}


def _hours(start: datetime, n: int) -> list[int]:
    return [int((start + timedelta(hours=h)).timestamp()) for h in range(n)]


def test_live_bundle_names():
    assert live.is_live_bundle("MUM-LIVE")
    assert not live.is_live_bundle("MUM-2019-07-02")
    assert not live.is_live_bundle(None)


def test_snap_cycle_floors_to_the_five_minute_cadence_in_ist():
    at = datetime(2026, 9, 30, 9, 8, 41, tzinfo=IST)
    assert live.snap_cycle(at) == datetime(2026, 9, 30, 9, 5, tzinfo=IST)


def test_a_step_rains_at_the_hour_that_ends_after_it():
    # Hourly totals: the hour ending 15:00 carried 6 mm, the hour ending 16:00 carried 12 mm.
    stamps = _hours(datetime(2026, 9, 30, 14, 0, tzinfo=IST), 4)
    payloads = [_payload(stamps, {"precipitation": [0.0, 6.0, 12.0, 0.0]}) for _ in range(9)]
    lons, lats = [72.5, 72.8, 73.1], [18.8, 19.1, 19.4]
    cell_lon = np.full((2, 2), 72.86)
    cell_lat = np.full((2, 2), 19.06)
    cube = live.forcing_from_points(payloads, lons, lats, cell_lon, cell_lat, CYCLE, n_steps=12)
    assert cube.shape == (1, 12, 2, 2)
    # Steps valid 14:40 .. 15:00 fall in the hour ending 15:00; 15:05 onwards in the next.
    assert cube[0, 0, 0, 0] == pytest.approx(6.0)
    assert cube[0, 4, 0, 0] == pytest.approx(6.0)
    assert cube[0, 5, 0, 0] == pytest.approx(12.0)


def test_rain_is_interpolated_between_points_and_never_negative():
    stamps = _hours(datetime(2026, 9, 30, 14, 0, tzinfo=IST), 4)
    lons, lats = [72.5, 72.8, 73.1], [18.8, 19.1, 19.4]
    payloads = []
    for _row in range(3):
        for col in range(3):
            value = 10.0 if col == 2 else 0.0  # rain only on the eastern column
            payloads.append(_payload(stamps, {"precipitation": [value] * 4}))
    cell_lon = np.array([[72.8, 72.95, 73.1]])
    cell_lat = np.array([[19.1, 19.1, 19.1]])
    cube = live.forcing_from_points(payloads, lons, lats, cell_lon, cell_lat, CYCLE, n_steps=1)
    assert cube[0, 0, 0].tolist() == pytest.approx([0.0, 5.0, 10.0])
    assert float(cube.min()) >= 0.0


def test_members_are_sampled_evenly_and_missing_values_are_dry():
    stamps = _hours(datetime(2026, 9, 30, 14, 0, tzinfo=IST), 3)
    members = {"precipitation": [1.0, 1.0, 1.0]}
    for m in range(1, 40):
        members[f"precipitation_member{m:02d}"] = [None, float(m), float(m)]
    payloads = [_payload(stamps, members) for _ in range(9)]
    cube = live.forcing_from_points(
        payloads,
        [72.5, 72.8, 73.1],
        [18.8, 19.1, 19.4],
        np.full((1, 1), 72.8),
        np.full((1, 1), 19.1),
        CYCLE,
        n_steps=1,
    )
    assert cube.shape[0] == live.N_MEMBERS
    assert cube[0, 0, 0, 0] == pytest.approx(1.0)  # the control member comes first
    assert cube[-1, 0, 0, 0] == pytest.approx(39.0)  # and the last member closes the sample


def test_tide_rows_interpolate_the_marine_hours():
    stamps = _hours(datetime(2026, 9, 30, 13, 0, tzinfo=IST), 6)
    rows = live.tide_from_marine(
        {"hourly": {"time": stamps, "sea_level_height_msl": [0.0, 1.0, 2.0, 1.0, 0.0, None]}},
        CYCLE,
        n_steps=12,
    )
    assert rows[0]["ts"].startswith("2026-09-30T13:35")
    at_1415 = next(r for r in rows if r["ts"].startswith("2026-09-30T14:20"))
    assert at_1415["stage_m"] == pytest.approx(1.333, abs=1e-3)
    assert live.tide_from_marine({"hourly": {}}, CYCLE, 12) == []


def test_prune_keeps_only_the_newest_live_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "data_dir", lambda: tmp_path)
    runs = tmp_path / "runs"
    for name in (
        "MUM-20260930T0800Z-sky1.0-twin1.0-flash0.1-live",
        "MUM-20260930T0830Z-sky1.0-twin1.0-flash0.1-live",
        "MUM-20260930T0900Z-sky1.0-twin1.0-flash0.1-live",
        "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
    ):
        (runs / name).mkdir(parents=True)
    removed = live.prune_live_runs("mumbai", keep=2)
    assert removed == ["MUM-20260930T0800Z-sky1.0-twin1.0-flash0.1-live"]
    assert (runs / "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked").is_dir()


def test_a_live_cycle_takes_only_this_citys_recent_reports():
    rows = [
        {"id": "a", "ts": "2026-09-30T13:10:00+05:30", "city": "mumbai", "depth_hint": "knee"},
        {"id": "b", "ts": "2026-09-30T10:00:00+05:30", "city": "mumbai", "depth_hint": "knee"},
        {"id": "c", "ts": "2019-07-02T08:40:00+05:30", "city": "mumbai", "depth_hint": "knee"},
        {"id": "d", "ts": "2026-09-30T14:00:00+05:30", "city": "chennai", "depth_hint": "ankle"},
        {"id": "e", "ts": "2026-09-30T14:00:00+05:30", "city": "mumbai", "status": "dismissed"},
        {"id": "f", "ts": "2026-09-30T14:40:00+05:30", "city": "mumbai", "depth_hint": "ankle"},
    ]
    kept = live.recent_reports(rows, "mumbai", CYCLE)
    assert [row["id"] for row in kept] == ["a"]
    assert kept[0]["synthetic"] is False

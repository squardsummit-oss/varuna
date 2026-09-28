"""Do citizen reports reach Pulse, from both sources? (SPEC.md 11.6, 7.11; task P7.2)

``POST /v1/reports`` appends to ``data/reports/inbox.jsonl``; the replay bundle carries its own
(labelled synthetic) ``reports.jsonl``. 7.11's acceptance criterion is that a report creates an
observation visible on the drain X-ray within one cycle, which needs both files read - and read
into the *same* dedupe pass, so a citizen and the bundle reporting one junction in one ten-minute
window are one observation rather than two.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from varuna_pulse.reports import DEPTH_CHIPS, read_reports

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
HINDMATA = (72.841, 19.012)
"""The junction the demo watches; the exact point comes from the city register, not from here."""

CYCLE_TS = datetime(2019, 7, 2, 8, 40, tzinfo=IST)


def _row(ts: datetime, lon: float, lat: float, chip: str = "knee", **extra: Any) -> dict[str, Any]:
    return {"ts": ts.isoformat(), "lon": lon, "lat": lat, "depth_hint": chip, **extra}


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8"
    )


def _north(lat: float, metres: float) -> float:
    return lat + metres / 110_540.0


def test_bundle_and_inbox_reports_dedupe_into_one_observation(tmp_path: Path) -> None:
    """30 m and 4 min apart is one junction, whichever file each report arrived in."""
    bundle = tmp_path / "bundle"
    inbox = tmp_path / "data" / "reports" / "inbox.jsonl"
    lon, lat = HINDMATA
    _write(
        bundle / "reports.jsonl",
        [_row(CYCLE_TS - timedelta(minutes=6), lon, lat, synthetic=True)],
    )
    _write(
        inbox,
        [_row(CYCLE_TS - timedelta(minutes=2), lon, _north(lat, 30.0), id="rpt-1")],
    )

    observations = read_reports(bundle, until=CYCLE_TS, inbox=inbox)

    assert len(observations) == 1
    assert observations[0].n_merged == 2
    assert observations[0].depth_cm == DEPTH_CHIPS["knee"][0]
    # A group a real person contributed to is not a synthetic observation, even though the
    # bundle's row is the earlier one and the one whose id survives the merge (rule 7).
    assert observations[0].synthetic is False


def test_inbox_only_report_becomes_an_observation(tmp_path: Path) -> None:
    """No bundle stream at all: the posted report is still assimilated."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    inbox = tmp_path / "data" / "reports" / "inbox.jsonl"
    lon, lat = HINDMATA
    _write(inbox, [_row(CYCLE_TS - timedelta(minutes=3), lon, lat, id="rpt-1")])

    observations = read_reports(bundle, until=CYCLE_TS, inbox=inbox)

    assert len(observations) == 1
    assert observations[0].report_id == "rpt-1"
    assert observations[0].n_merged == 1
    assert observations[0].synthetic is False


def test_distant_reports_stay_separate(tmp_path: Path) -> None:
    """300 m apart is two junctions; the dedupe radius is 50 m, not 'nearby'."""
    bundle = tmp_path / "bundle"
    inbox = tmp_path / "data" / "reports" / "inbox.jsonl"
    lon, lat = HINDMATA
    _write(bundle / "reports.jsonl", [_row(CYCLE_TS - timedelta(minutes=5), lon, lat)])
    _write(inbox, [_row(CYCLE_TS - timedelta(minutes=4), lon, _north(lat, 300.0), id="rpt-1")])

    assert len(read_reports(bundle, until=CYCLE_TS, inbox=inbox)) == 2


def test_missing_inbox_leaves_the_bundle_stream_alone(tmp_path: Path) -> None:
    """Before anyone has posted anything, the inbox file does not exist."""
    bundle = tmp_path / "bundle"
    lon, lat = HINDMATA
    _write(bundle / "reports.jsonl", [_row(CYCLE_TS - timedelta(minutes=5), lon, lat)])

    observations = read_reports(
        bundle, until=CYCLE_TS, inbox=tmp_path / "data" / "reports" / "inbox.jsonl"
    )

    assert len(observations) == 1


def test_a_report_later_than_the_cycle_is_not_assimilated(tmp_path: Path) -> None:
    """The known limitation, pinned rather than described.

    ``until`` compares a report's own ``ts`` against the cycle clock, so a report posted today
    while the 2019 replay runs is in the future of every cycle in the bundle and is dropped. It
    reaches Pulse in live mode, or when the client sends the replay clock's time as ``ts``.
    """
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    inbox = tmp_path / "data" / "reports" / "inbox.jsonl"
    lon, lat = HINDMATA
    _write(inbox, [_row(datetime(2026, 9, 12, 11, 0, tzinfo=IST), lon, lat, id="rpt-1")])

    assert read_reports(bundle, until=CYCLE_TS, inbox=inbox) == []


def test_a_report_with_no_timezone_is_dropped_rather_than_guessed(tmp_path: Path) -> None:
    """A client may post its own ``ts``; without an offset it cannot be put on the clock.

    The bundle and the endpoint both write +05:30. Comparing a naive timestamp with the cycle
    clock raises, and assuming IST would invent the one thing the report is evidence about.
    """
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    inbox = tmp_path / "data" / "reports" / "inbox.jsonl"
    lon, lat = HINDMATA
    _write(
        inbox,
        [
            {
                "id": "rpt-1",
                "ts": "2019-07-02T08:35:00",
                "lon": lon,
                "lat": lat,
                "depth_hint": "knee",
            }
        ],
    )

    assert read_reports(bundle, until=CYCLE_TS, inbox=inbox) == []


def test_an_unreadable_inbox_line_does_not_fail_the_stage(tmp_path: Path) -> None:
    """A truncated append - the API writes while a cycle reads - costs that row, not the cycle."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    inbox = tmp_path / "data" / "reports" / "inbox.jsonl"
    lon, lat = HINDMATA
    good = json.dumps(_row(CYCLE_TS - timedelta(minutes=1), lon, lat, id="rpt-1"))
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text(good + "\n" + good[: len(good) // 2], encoding="utf-8")

    observations = read_reports(bundle, until=CYCLE_TS, inbox=inbox)

    assert len(observations) == 1
    assert observations[0].report_id == "rpt-1"

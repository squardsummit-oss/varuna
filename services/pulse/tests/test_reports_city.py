"""A citizen report is evidence about the city it was made in, and no other.

The inbox ``POST /v1/reports`` appends to is one file for every city, and Pulse snaps each report
to its nearest drain node with no distance cutoff. Before this, a report from Chennai or from
anywhere else reached a Mumbai cycle as a knee-deep observation at whichever Mumbai node was least
far away. :func:`read_reports` now keeps only the inbox rows inside the cycle's own city box.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from varuna_pulse.reports import REPORT_AOIS, city_for_point, read_reports

IST = timezone(timedelta(hours=5, minutes=30))
CYCLE_TS = datetime(2019, 7, 2, 8, 40, tzinfo=IST)
HINDMATA = (72.841, 19.012)
VELACHERY = (80.22, 12.98)
PUNE = (73.856, 18.52)


def _row(lon: float, lat: float, report_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": report_id,
        "ts": (CYCLE_TS - timedelta(minutes=3)).isoformat(),
        "lon": lon,
        "lat": lat,
        "depth_hint": "knee",
        **extra,
    }


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _bundle(tmp_path: Path, city: str | None) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    if city is not None:
        (bundle / "manifest.json").write_text(json.dumps({"city": city}), encoding="utf-8")
    return bundle


@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "reports" / "inbox.jsonl"
    _write(
        path,
        [
            _row(*HINDMATA, "rpt-mumbai"),
            _row(*VELACHERY, "rpt-chennai"),
            _row(*PUNE, "rpt-pune"),
        ],
    )
    return path


def test_a_mumbai_cycle_keeps_only_mumbai_reports(tmp_path: Path, inbox: Path) -> None:
    bundle = _bundle(tmp_path, "mumbai")

    ids = [o.report_id for o in read_reports(bundle, until=CYCLE_TS, inbox=inbox)]

    assert ids == ["rpt-mumbai"]


def test_a_chennai_cycle_keeps_only_chennai_reports(tmp_path: Path, inbox: Path) -> None:
    bundle = _bundle(tmp_path, "chennai")

    ids = [o.report_id for o in read_reports(bundle, until=CYCLE_TS, inbox=inbox)]

    assert ids == ["rpt-chennai"]


def test_an_explicit_city_beats_the_manifest(tmp_path: Path, inbox: Path) -> None:
    bundle = _bundle(tmp_path, "mumbai")

    ids = [o.report_id for o in read_reports(bundle, until=CYCLE_TS, inbox=inbox, city="chennai")]

    assert ids == ["rpt-chennai"]


def test_with_no_city_known_a_report_outside_every_box_is_still_dropped(
    tmp_path: Path, inbox: Path
) -> None:
    """A bundle with no manifest (the older tests' fixture) keeps what is inside some city."""
    bundle = _bundle(tmp_path, None)

    ids = sorted(o.report_id for o in read_reports(bundle, until=CYCLE_TS, inbox=inbox))

    assert ids == ["rpt-chennai", "rpt-mumbai"]


def test_a_row_the_api_marked_outside_the_boxes_is_never_assimilated(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "mumbai")
    inbox = tmp_path / "inbox.jsonl"
    _write(inbox, [_row(*HINDMATA, "rpt-flagged", outside_aoi=True, city=None)])

    assert read_reports(bundle, until=CYCLE_TS, inbox=inbox) == []


def test_a_city_tag_that_disagrees_with_the_point_is_refused(tmp_path: Path) -> None:
    """The API tags by the same boxes, so a Mumbai point tagged Chennai was edited by hand."""
    bundle = _bundle(tmp_path, "mumbai")
    inbox = tmp_path / "inbox.jsonl"
    _write(inbox, [_row(*HINDMATA, "rpt-edited", city="chennai")])

    assert read_reports(bundle, until=CYCLE_TS, inbox=inbox) == []


def test_the_bundle_stream_itself_is_not_filtered(tmp_path: Path) -> None:
    """The bundle's synthetic stream was generated inside its city; only the inbox is checked."""
    bundle = _bundle(tmp_path, "mumbai")
    _write(bundle / "reports.jsonl", [_row(*HINDMATA, "RPT-bundle", synthetic=True)])

    ids = [o.report_id for o in read_reports(bundle, until=CYCLE_TS, inbox=None)]

    assert ids == ["RPT-bundle"]


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        (HINDMATA, "mumbai"),
        (VELACHERY, "chennai"),
        (PUNE, None),
        ((72.815, 18.995), "mumbai"),
        ((72.905, 19.135), "mumbai"),
        ((72.8149, 19.0), None),
        ((float("nan"), 19.0), None),
    ],
)
def test_the_city_of_a_point_is_the_box_it_falls_in(
    point: tuple[float, float], expected: str | None
) -> None:
    assert city_for_point(*point) == expected


def test_the_boxes_are_the_two_cities_section_3_3_names() -> None:
    assert set(REPORT_AOIS) == {"mumbai", "chennai"}

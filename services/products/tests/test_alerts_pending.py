"""Which pending places the capped list keeps (SPEC.md 7.5, 11.10).

The alert centre prints a pending level above a shown alert on that alert's own row ("Severe next
cycle if it holds"). The list of pending places is capped at ``MAX_ALERTS`` for a reader, so a
step up that sorted past the cap left its row silent. On 2 July at 08:40, 175 places were pending
and 60 were listed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from varuna_products.alerts import MAX_ALERTS, build_alerts, served_queue, write_alerts
from varuna_schemas.constants import IST

N_STEPS = 36
T0 = datetime(2019, 7, 2, 7, 40, tzinfo=IST)


def _series(peak: float) -> list[float]:
    return [0.0, 5.0] + [peak] * 6 + [0.0] * (N_STEPS - 8)


def _times(cycle: datetime) -> tuple[datetime, ...]:
    return tuple(cycle + timedelta(minutes=5 * (i + 1)) for i in range(N_STEPS))


def _bake(root: Path, cycle: datetime, streets: dict[str, list[float]]) -> dict:
    run_id = f"MUM-{cycle.astimezone(UTC):%Y%m%dT%H%M}Z-sky1.0-twin1.0-flash0.1-baked"
    queue = build_alerts([], run_id, cycle, _times(cycle), mode="baked", streets=streets)
    tmp = root / f".{run_id}.tmp-abcd1234"
    tmp.mkdir(parents=True)
    write_alerts(tmp, queue)
    final = root / run_id
    tmp.rename(final)
    return json.loads((final / "alerts.json").read_text(encoding="utf-8"))


def test_a_step_up_on_a_shown_alert_survives_the_cap(tmp_path: Path) -> None:
    """A moderate street going severe keeps its pending entry past 70 deeper new severe streets."""
    crowd = {f"New Road {i:02d}": _series(90.0) for i in range(MAX_ALERTS + 10)}
    _bake(tmp_path, T0, {"Upgrade Marg": _series(35.0)})
    _bake(tmp_path, T0 + timedelta(minutes=30), {"Upgrade Marg": _series(35.0)})
    body = _bake(tmp_path, T0 + timedelta(minutes=60), {"Upgrade Marg": _series(50.0), **crowd})

    assert [(a["area_desc"], a["level"]) for a in body["alerts"]] == [("Upgrade Marg", "moderate")]
    assert body["n_pending"] == MAX_ALERTS + 11
    assert len(body["pending"]) == MAX_ALERTS
    # Shallower than every new street, and still first: it is the one a row on screen names.
    assert (body["pending"][0]["area_desc"], body["pending"][0]["level"]) == (
        "Upgrade Marg",
        "severe",
    )


def test_the_order_is_unchanged_when_no_shown_alert_steps_up(tmp_path: Path) -> None:
    """With nothing raised, pending stays worst level first, then deepest."""
    body = _bake(tmp_path, T0, {"Shallow Road": _series(20.0), "Deep Road": _series(80.0)})
    assert body["alerts"] == []
    assert [(p["area_desc"], p["level"]) for p in body["pending"]] == [
        ("Deep Road", "severe"),
        ("Shallow Road", "watch"),
    ]


def _early(peak: float) -> list[float]:
    """Over its level from the first step: the earliest onset a cycle can have."""
    return [peak] * 6 + [0.0] * (N_STEPS - 6)


def _capped(tmp_path: Path) -> dict:
    """70 moderate streets fill the list; a street raised at watch steps up past the cap.

    ``Step Marg`` is raised at watch on the second cycle and crosses moderate on the third, with
    the earliest onset of the cycle. The list keeps the 60 worst - all moderate - so it is raised
    and not listed. ``Fresh Road`` crosses severe for the first time on the third cycle.
    """
    crowd = {f"Crowd Road {i:02d}": _series(40.0) for i in range(MAX_ALERTS + 10)}
    _bake(tmp_path, T0, {**crowd, "Step Marg": _early(20.0)})
    _bake(tmp_path, T0 + timedelta(minutes=30), {**crowd, "Step Marg": _early(20.0)})
    return _bake(
        tmp_path,
        T0 + timedelta(minutes=60),
        {**crowd, "Step Marg": _early(35.0), "Fresh Road": _series(50.0)},
    )


def test_a_capped_queue_counts_every_raised_alert_by_level(tmp_path: Path) -> None:
    body = _capped(tmp_path)
    assert len(body["alerts"]) == MAX_ALERTS
    assert {a["area_desc"] for a in body["alerts"]}.isdisjoint({"Step Marg", "Fresh Road"})
    assert body["n_raised"] == MAX_ALERTS + 11
    assert body["n_raised_by_level"] == {"severe": 0, "moderate": MAX_ALERTS + 10, "watch": 1}
    # The first onset is the unlisted watch street's, not the first of the sixty listed.
    assert body["first_onset"] == {
        "ts": "2019-07-02T08:45:00+05:30",
        "name": "Step Marg",
        "level": "watch",
    }


def test_a_pending_entry_says_the_level_its_place_is_already_raised_at(tmp_path: Path) -> None:
    body = _capped(tmp_path)
    by_place = {p["area_desc"]: p for p in body["pending"]}
    assert (by_place["Step Marg"]["level"], by_place["Step Marg"]["raised_level"]) == (
        "moderate",
        "watch",
    )
    assert (by_place["Fresh Road"]["level"], by_place["Fresh Road"]["raised_level"]) == (
        "severe",
        None,
    )
    assert (body["n_pending"], body["n_pending_new"], body["n_pending_step_up"]) == (2, 1, 1)


def test_served_queue_rebuilds_the_counts_for_a_run_written_before_them(tmp_path: Path) -> None:
    """A run baked before the counts existed gets the same ones from its hysteresis record."""
    body = _capped(tmp_path)
    served = served_queue(body)
    assert served["counts_source"] == "product"

    old = {
        k: v
        for k, v in body.items()
        if k not in {"n_raised_by_level", "first_onset", "n_pending_new", "n_pending_step_up"}
    }
    old["pending"] = [{k: v for k, v in p.items() if k != "raised_level"} for p in body["pending"]]
    rebuilt = served_queue(old)
    assert rebuilt["counts_source"] == "hysteresis_record"
    for key in ("n_raised", "n_raised_by_level", "n_pending_new", "n_pending_step_up"):
        assert rebuilt[key] == served[key], key
    assert [p["raised_level"] for p in rebuilt["pending"]] == [
        p["raised_level"] for p in served["pending"]
    ]
    # Not recoverable from the record: said as unknown, never as the first of the listed.
    assert rebuilt["first_onset"] is None


def test_served_queue_on_a_run_from_before_the_cross_cycle_rule_counts_the_list() -> None:
    body = {"alerts": [{"level": "severe"}, {"level": "watch"}, {"level": "watch"}]}
    served = served_queue(body)
    assert served["counts_source"] == "listed"
    assert served["n_raised"] == 3
    assert served["n_raised_by_level"] == {"severe": 1, "moderate": 0, "watch": 2}
    assert served["n_pending_new"] is None


def test_served_queue_on_the_committed_0840_cycle() -> None:
    """The numbers the alert centre was misreporting, rebuilt from the shipped record.

    On the bake this was written against the file listed 60 of 213 raised, and 24 of the 32
    pending places it listed with no row were already raised at a lower level: "Watching, not
    raised yet" named them. The re-bake on the rebuilt coast (2a0a634) raises 74 and lists 60, and
    none of its 22 pending places without a row is raised at another level.
    """
    path = (
        Path(__file__).resolve().parents[3]
        / "demo"
        / "runs"
        / "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"
        / "alerts.json"
    )
    if not path.is_file():
        pytest.skip("demo/runs is not in this checkout")
    body = json.loads(path.read_text(encoding="utf-8"))
    served = served_queue(body)
    assert len(body["alerts"]) == MAX_ALERTS
    assert served["n_raised"] == 74
    assert served["n_raised_by_level"] == {"severe": 1, "moderate": 9, "watch": 64}
    assert (served["n_pending_new"], served["n_pending_step_up"]) == (147, 38)
    listed = {(a["scope"], a["area_desc"]) for a in body["alerts"]}
    no_row = [p for p in served["pending"] if (p["scope"], p["area_desc"]) not in listed]
    assert (len(no_row), sum(p["raised_level"] is not None for p in no_row)) == (22, 0)

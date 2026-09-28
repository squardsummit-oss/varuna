"""Cross-cycle hysteresis for alerts (SPEC.md 11.10, 7.5 AC1; task P8.7).

Raise at ``P(h > θ) >= 0.6`` for two consecutive cycles, clear at ``<= 0.3``, with the state on
the card. Every test here drives the rule the way a bake does: cycles written one after another
into a registry-shaped folder, each reading the one before it off disk.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from varuna_products.alerts import (
    CLEAR_P,
    MAX_CYCLE_GAP_MIN,
    RAISE_P,
    AlertQueue,
    apply_cycle_hysteresis,
    build_alerts,
    escalation_by_level,
    load_escalation,
    previous_record,
    run_cycle_ts,
    write_alerts,
)
from varuna_schemas.constants import IST

N_STEPS = 36
T0 = datetime(2019, 7, 2, 6, 10, tzinfo=IST)


def _series(peak: float) -> list[float]:
    """A depth series that holds ``peak`` for six steps: over every threshold below it."""
    return [0.0, 5.0] + [peak] * 6 + [0.0] * (N_STEPS - 8)


def _hindmata(peak: float) -> dict[str, Any]:
    return {
        "hotspot_id": "MUM-HS-01",
        "slug": "hindmata",
        "name": "Hindmata junction",
        "ward": "F/S",
        "lon": 72.841,
        "lat": 19.012,
        "depth_cm": _series(peak),
    }


def _run_id(cycle: datetime) -> str:
    return f"MUM-{cycle.astimezone(UTC):%Y%m%dT%H%M}Z-sky1.0-twin1.0-flash0.1-baked"


def _times(cycle: datetime) -> tuple[datetime, ...]:
    return tuple(cycle + timedelta(minutes=5 * (i + 1)) for i in range(N_STEPS))


def _bake(root: Path, cycle: datetime, peak: float, streets: dict[str, list[float]] | None = None):
    """One cycle the way the cycle writes it: into a dot-temp folder, then renamed into place."""
    run_id = _run_id(cycle)
    queue = build_alerts(
        [_hindmata(peak)], run_id, cycle, _times(cycle), mode="baked", streets=streets
    )
    tmp = root / f".{run_id}.tmp-abcd1234"
    tmp.mkdir(parents=True)
    written = write_alerts(tmp, queue)
    final = root / run_id
    tmp.rename(final)
    body = json.loads((final / "alerts.json").read_text(encoding="utf-8"))
    return written, body


def test_run_id_encodes_city_and_utc_cycle() -> None:
    city, moment = run_cycle_ts("MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked") or ("", None)
    assert city == "MUM"
    assert moment is not None and moment.hour == 3 and moment.minute == 10
    assert run_cycle_ts("not-a-run") is None


def test_one_cycle_raises_nothing_it_is_pending(tmp_path: Path) -> None:
    written, body = _bake(tmp_path, T0, 60.0)
    assert written == [] and body["alerts"] == []
    assert [p["level"] for p in body["pending"]] == ["severe"]
    assert body["pending"][0]["persists_cycles"] == 1
    assert body["hysteresis"]["previous_run_id"] is None


def test_two_consecutive_cycles_raise_with_the_state_on_the_card(tmp_path: Path) -> None:
    _bake(tmp_path, T0, 60.0)
    written, body = _bake(tmp_path, T0 + timedelta(minutes=30), 60.0)
    assert len(written) == 1
    card = body["alerts"][0]
    assert card["level"] == "severe"
    assert card["persists_cycles"] == 2 and card["persists_unit"] == "cycles"
    # Raised on the second cycle, first seen on the first: "raised 06:40 - persists 2 cycles".
    assert card["raised_ts"] == (T0 + timedelta(minutes=30)).isoformat()
    assert card["first_seen_ts"] == T0.isoformat()
    assert card["trigger_p"] >= RAISE_P
    assert body["hysteresis"]["previous_run_id"] == _run_id(T0)
    assert (
        tmp_path / _run_id(T0 + timedelta(minutes=30)) / "alerts" / f"{card['id']}.cap.xml"
    ).is_file()


def test_a_raised_alert_carries_its_raise_time_and_counts_on(tmp_path: Path) -> None:
    for k in range(3):
        _, body = _bake(tmp_path, T0 + timedelta(minutes=30 * k), 60.0)
    card = body["alerts"][0]
    assert card["persists_cycles"] == 3
    assert card["raised_ts"] == (T0 + timedelta(minutes=30)).isoformat()
    assert card["sent_ts"] == (T0 + timedelta(minutes=60)).isoformat()


def test_a_dry_cycle_clears_a_raised_alert(tmp_path: Path) -> None:
    _bake(tmp_path, T0, 60.0)
    _bake(tmp_path, T0 + timedelta(minutes=30), 60.0)
    written, body = _bake(tmp_path, T0 + timedelta(minutes=60), 0.0)
    assert written == []
    assert [(c["level"], c["state"]) for c in body["cleared"]] == [
        ("severe", "cleared"),
        ("moderate", "cleared"),
        ("watch", "cleared"),
    ]
    assert body["cleared"][0]["cleared_ts"] == (T0 + timedelta(minutes=60)).isoformat()
    # Cleared means forgotten: a fresh crossing has to earn two cycles again.
    _, again = _bake(tmp_path, T0 + timedelta(minutes=90), 60.0)
    assert again["alerts"] == [] and again["pending"]


def test_a_single_cycle_blip_never_raises(tmp_path: Path) -> None:
    _bake(tmp_path, T0, 60.0)
    _bake(tmp_path, T0 + timedelta(minutes=30), 0.0)
    _, body = _bake(tmp_path, T0 + timedelta(minutes=60), 60.0)
    assert body["alerts"] == []


def test_levels_run_their_own_clocks(tmp_path: Path) -> None:
    """Moderate for two cycles and severe for one is a raised moderate and a pending severe."""
    _bake(tmp_path, T0, 35.0)
    _, body = _bake(tmp_path, T0 + timedelta(minutes=30), 60.0)
    assert [a["level"] for a in body["alerts"]] == ["moderate"]
    assert [p["level"] for p in body["pending"]] == ["severe"]
    _, body = _bake(tmp_path, T0 + timedelta(minutes=60), 60.0)
    assert [a["level"] for a in body["alerts"]] == ["severe"]


def test_the_band_between_clear_and_raise_holds_a_raised_level() -> None:
    """``CLEAR_P < P < RAISE_P`` keeps a raised level and drops a pending one.

    A deterministic run never produces such a P, so the band is exercised on the rule directly.
    """
    run_id = _run_id(T0)
    queue = build_alerts([_hindmata(60.0)], run_id, T0, _times(T0))
    band = (CLEAR_P + RAISE_P) / 2
    for levels in queue.candidates.values():
        for alert in levels.values():
            alert["trigger_p"] = band
    key = next(iter(queue.candidates))
    raised = {"state": "raised", "cycles": 2, "raised_ts": "x", "since_ts": "y", "p": 1.0}
    pending = {"state": "pending", "cycles": 1, "raised_ts": None, "since_ts": "y", "p": 1.0}
    previous = {"situations": {key: {"levels": {"severe": raised, "moderate": pending}}}}
    out = apply_cycle_hysteresis(queue, previous)
    assert [a["level"] for a in out] == ["severe"]
    assert out[0]["persists_cycles"] == 3
    assert "moderate" not in out.record["situations"][key]["levels"]


def test_a_run_too_far_back_is_not_the_previous_cycle(tmp_path: Path) -> None:
    _bake(tmp_path, T0, 60.0)
    _, body = _bake(tmp_path, T0 + timedelta(minutes=MAX_CYCLE_GAP_MIN + 5), 60.0)
    assert body["alerts"] == [] and body["hysteresis"]["previous_run_id"] is None


def test_temporary_folders_and_other_cities_are_never_read(tmp_path: Path) -> None:
    _bake(tmp_path, T0, 60.0)
    other = tmp_path / _run_id(T0).replace("MUM-", "CHN-", 1)
    other.mkdir()
    (other / "alerts.json").write_text("{}", encoding="utf-8")
    hidden = tmp_path / f".{_run_id(T0 + timedelta(minutes=20))}.tmp-00000000"
    hidden.mkdir()
    (hidden / "alerts.json").write_text("not json", encoding="utf-8")
    record = previous_record(_run_id(T0 + timedelta(minutes=30)), tmp_path)
    assert record["run_id"] == _run_id(T0)


def test_a_legacy_previous_queue_counts_as_one_cycle_not_as_raised(tmp_path: Path) -> None:
    """A queue written before the record existed raised on one cycle's evidence."""
    legacy = tmp_path / _run_id(T0)
    legacy.mkdir()
    old = build_alerts([_hindmata(60.0)], _run_id(T0), T0, _times(T0))
    (legacy / "alerts.json").write_text(json.dumps({"alerts": list(old)}), encoding="utf-8")
    _, body = _bake(tmp_path, T0 + timedelta(minutes=30), 60.0)
    assert [a["level"] for a in body["alerts"]] == ["severe"]
    assert body["alerts"][0]["persists_cycles"] == 2
    assert body["hysteresis"]["previous_legacy"] is True


def _digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_baking_the_same_cycles_twice_is_byte_identical(tmp_path: Path) -> None:
    """Rule 8: the previous run is an input, so the same inputs write the same bytes."""
    streets = {"Dr Ambedkar Road": _series(50.0), "Senapati Bapat Marg": _series(20.0)}
    for label in ("first", "second"):
        root = tmp_path / label
        for k, peak in enumerate((60.0, 60.0, 40.0, 0.0)):
            _bake(root, T0 + timedelta(minutes=30 * k), peak, streets=streets if k < 3 else None)
    assert _digest(tmp_path / "first") == _digest(tmp_path / "second")
    # And the sequence exercised every transition, so the equality is about something.
    last = json.loads(
        (tmp_path / "first" / _run_id(T0 + timedelta(minutes=90)) / "alerts.json").read_text()
    )
    assert last["cleared"]


def test_rebaking_a_cycle_in_place_reads_the_cycle_before_not_itself(tmp_path: Path) -> None:
    _bake(tmp_path, T0, 60.0)
    _, first = _bake(tmp_path, T0 + timedelta(minutes=30), 60.0)
    # The cycle bakes over itself (overwrite=True): its own folder must not be its own past.
    run = tmp_path / _run_id(T0 + timedelta(minutes=30))
    for path in sorted(run.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    run.rmdir()
    _, second = _bake(tmp_path, T0 + timedelta(minutes=30), 60.0)
    assert first == second


def test_a_plain_list_is_written_as_it_is(tmp_path: Path) -> None:
    alerts = list(build_alerts([_hindmata(60.0)], _run_id(T0), T0, _times(T0)))
    write_alerts(tmp_path, alerts)
    body = json.loads((tmp_path / "alerts.json").read_text(encoding="utf-8"))
    assert body == {"alerts": alerts}


def test_build_alerts_with_a_previous_record_is_final() -> None:
    queue = build_alerts([_hindmata(60.0)], _run_id(T0), T0, _times(T0), previous={})
    assert isinstance(queue, AlertQueue) and queue.final and list(queue) == []


# --- escalation matrix --------------------------------------------------------------------------


def test_the_committed_escalation_matrix_is_the_five_step_ladder() -> None:
    matrix = load_escalation()
    assert matrix is not None, "config/escalation.yaml must be committed"
    assert [t["id"] for t in matrix["tiers"]] == [
        "ward_officer",
        "control_room",
        "police_traffic",
        "transit",
        "public",
    ]


def test_alerts_carry_the_tiers_their_level_reaches() -> None:
    by_level = escalation_by_level()
    assert by_level is not None
    assert by_level["watch"] == ["ward_officer"]
    assert by_level["severe"] == ["ward_officer", "control_room", "police_traffic"]
    queue = build_alerts([_hindmata(60.0)], _run_id(T0), T0, _times(T0))
    assert queue[0]["notify"] == by_level["severe"]


def test_a_missing_matrix_is_none_and_a_bad_one_is_refused(tmp_path: Path) -> None:
    assert load_escalation(tmp_path / "absent.yaml") is None
    bad = tmp_path / "bad.yaml"
    bad.write_text("tiers:\n  - id: ward_officer\n    levels: [flood]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown levels"):
        load_escalation(bad)

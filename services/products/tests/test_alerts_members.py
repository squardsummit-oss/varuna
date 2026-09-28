"""How much of the ensemble agrees with an alert, carried beside it (SPEC.md 11.7, 11.10).

Every alert is raised on the Twin's one deterministic run, so ``trigger_p`` is 1.0 on all sixty
at 08:40 and says nothing a ward officer can weigh. The products stage holds fifty members for
every segment. These pin that the count of members crossing a level is carried as
``members_above`` of ``members_total``, by the raise's own rule, and that carrying it changes
nothing about which alerts a cycle raises.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pytest
from varuna_products import alerts as module
from varuna_products.alerts import (
    apply_cycle_hysteresis,
    build_alerts,
    cap_xml,
    members_crossing,
    street_member_series,
    write_alerts,
)
from varuna_products.depth import _member_levels
from varuna_products.schemas import validate_cap

IST = timezone(timedelta(hours=5, minutes=30))
CYCLE = datetime(2019, 7, 2, 8, 40, tzinfo=IST)
N_STEPS = 36
TIMES = tuple(CYCLE + timedelta(minutes=5 * (i + 1)) for i in range(N_STEPS))

TWIN = [0.0] * 8 + [50.0] * (N_STEPS - 8)
"""The deterministic street series: over 45 cm from the ninth step to the end."""


def _members() -> np.ndarray:
    """Five members: three keep the street over 45 cm, one touches it for one step, one stays dry."""
    rows = [
        TWIN,
        [0.0] * 10 + [60.0] * (N_STEPS - 10),
        [0.0] * 20 + [46.0, 46.0] + [0.0] * (N_STEPS - 22),
        [0.0] * 20 + [80.0] + [0.0] * (N_STEPS - 21),  # one step only: does not count
        [0.0] * N_STEPS,
    ]
    return np.asarray(rows, dtype=np.float32)


def _build(street_members: dict[str, Any] | None, **kwargs: Any) -> list[dict[str, Any]]:
    module.STREET_POINTS.clear()
    return list(
        build_alerts(
            [],
            "MUM-TEST",
            CYCLE,
            TIMES,
            mode="baked",
            streets={"Test Marg": TWIN},
            street_members=street_members,
            escalation={},
            **kwargs,
        )
    )


def test_members_crossing_is_the_raise_rule_per_member() -> None:
    members = _members()
    assert members_crossing(members, 45) == 3
    # Strictly above, as the raise is: a member sitting at exactly 46 is over 45 but not over 46.
    assert members_crossing(members, 46) == 2
    assert members_crossing(members, 15) == 3
    assert members_crossing(members, 100) == 0
    assert members_crossing(np.zeros((4, 1)), 0) == 0
    with pytest.raises(ValueError, match="n_members, n_steps"):
        members_crossing(np.zeros(3), 1)


def test_a_street_alert_carries_members_above_of_members_total() -> None:
    (alert,) = _build({"Test Marg": _members()})
    assert alert["level"] == "severe"
    assert alert["members_above"] == 3
    assert alert["members_total"] == 5


def test_without_members_both_fields_are_null() -> None:
    (alert,) = _build(None)
    assert alert["members_above"] is None
    assert alert["members_total"] is None


def test_members_never_change_which_alerts_raise() -> None:
    # A stack in which *no* member crosses anything: the Twin still raises, with 0 of 5 beside it.
    dry = np.zeros((5, N_STEPS), dtype=np.float32)
    with_members = _build({"Test Marg": dry})
    without = _build(None)
    strip = {"members_above", "members_total"}
    assert [{k: v for k, v in a.items() if k not in strip} for a in with_members] == [
        {k: v for k, v in a.items() if k not in strip} for a in without
    ]
    assert with_members[0]["members_above"] == 0
    assert with_members[0]["trigger_p"] == 1.0


def test_every_level_candidate_counts_against_its_own_threshold() -> None:
    module.STREET_POINTS.clear()
    queue = build_alerts(
        [],
        "MUM-TEST",
        CYCLE,
        TIMES,
        streets={"Test Marg": TWIN},
        street_members={"Test Marg": _members()},
        escalation={},
    )
    levels = queue.candidates["segment|Test Marg"]
    assert {level: c["members_above"] for level, c in levels.items()} == {
        "severe": 3,
        "moderate": 3,
        "watch": 3,
    }
    # The pending entry the first cycle lists carries the count too.
    final = apply_cycle_hysteresis(queue, {})
    assert len(final) == 0
    assert final.pending[0]["members_above"] == 3
    assert final.pending[0]["members_total"] == 5


def test_street_member_series_takes_each_members_deepest_segment() -> None:
    # Three segments on two streets and one unnamed, two members, three steps.
    depth = np.array([[10.0, 20.0, 5.0], [30.0, 10.0, 5.0], [0.0, 0.0, 5.0]])
    members = np.array(
        [
            [[12.0, 18.0, 1.0], [34.0, 12.0, 1.0], [2.0, 0.0, 1.0]],
            [[8.0, 22.0, 9.0], [26.0, 8.0, 9.0], [0.0, 0.0, 9.0]],
        ]
    )
    names = {"a": "Road one", "b": "Road one", "c": ""}
    out = street_member_series(depth, members, ["a", "b", "c"], names)
    assert sorted(out) == ["Road one"]
    level = _member_levels(depth, members)
    expected = level[:, :, :2].max(axis=2)
    np.testing.assert_array_equal(out["Road one"], expected)
    assert out["Road one"].shape == (2, 3)
    # Re-centred on the Twin: where no member is clipped at zero, the member mean is its level.
    np.testing.assert_allclose(level[:, :2].mean(axis=0), depth[:2], atol=1e-5)


def test_interleaved_streets_take_their_own_deepest_member_level() -> None:
    # Interleaved streets, unnamed segments between them, float64 in as Flash hands it over: each
    # street is exactly the maximum over its own columns of the re-centred stack.
    rng = np.random.default_rng(2019)
    n_seg = 40
    depth = rng.gamma(1.0, 20.0, size=(5, n_seg))
    members = depth[None] + rng.normal(0.0, 8.0, size=(7, 5, n_seg))
    ids = [f"s{k}" for k in range(n_seg)]
    names = {f"s{k}": ("A", "B", "C", "")[k % 4] for k in range(n_seg)}
    out = street_member_series(depth, members, ids, names)
    full = _member_levels(depth, members)
    assert sorted(out) == ["A", "B", "C"]
    for j, street in enumerate(("A", "B", "C")):
        np.testing.assert_array_equal(out[street], full[:, :, j::4].max(axis=2))


def test_street_member_series_refuses_a_stack_from_another_city() -> None:
    with pytest.raises(ValueError, match="segment axis"):
        street_member_series(np.zeros((2, 3)), np.zeros((4, 2, 5)), ["a", "b", "c"], {"a": "X"})
    with pytest.raises(ValueError, match="segment axis"):
        street_member_series(np.zeros((2, 3)), np.zeros((4, 2, 3)), ["a", "b"], {"a": "X"})


def test_street_member_series_with_no_named_segment_is_empty() -> None:
    depth = np.zeros((2, 2))
    members = np.zeros((3, 2, 2))
    assert street_member_series(depth, members, ["a", "b"], {}) == {}


def test_the_counts_leave_cap_valid_and_the_file_deterministic(tmp_path: Any) -> None:
    (alert,) = _build({"Test Marg": _members()})
    assert not validate_cap(cap_xml(alert))
    for name in ("one", "two"):
        run = tmp_path / name
        run.mkdir()
        write_alerts(run, [alert])
    assert (tmp_path / "one" / "alerts.json").read_bytes() == (
        tmp_path / "two" / "alerts.json"
    ).read_bytes()


def test_an_alert_written_before_the_fields_existed_still_reads() -> None:
    # Old runs carry neither the counts nor name, locality or window_open_ended.
    legacy = {
        "id": "VARUNA-MUM-OLD-STREET-0001-SEVERE",
        "run_id": "MUM-OLD",
        "scope": "segment",
        "hotspot_id": None,
        "level": "severe",
        "threshold_cm": 45,
        "headline": "Pipeline Road: depth above 45 cm from 09:55 to 11:40",
        "instruction": "Avoid Pipeline Road for the window. Peak forecast 61 cm.",
        "area_desc": "Pipeline Road",
        "lon": 72.86,
        "lat": 19.04,
        "trigger_p": 1.0,
        "window_from": "2019-07-02T09:55:00+05:30",
        "window_to": "2019-07-02T11:40:00+05:30",
        "raised_ts": "2019-07-02T08:40:00+05:30",
        "cap_status": "Exercise",
    }
    assert not validate_cap(cap_xml(legacy))
    # And a previous record written before `name` was carried still hands its situation on.
    queue = build_alerts(
        [], "MUM-TEST", CYCLE, TIMES, streets={"Pipeline Road": TWIN}, escalation={}
    )
    previous = {
        "situations": {
            "segment|Pipeline Road": {
                "scope": "segment",
                "levels": {"severe": {"state": "pending", "cycles": 1, "p": 1.0}},
            }
        }
    }
    final = apply_cycle_hysteresis(queue, previous)
    assert [a["area_desc"] for a in final] == ["Pipeline Road"]
    assert final[0]["name"] == "Pipeline Road"

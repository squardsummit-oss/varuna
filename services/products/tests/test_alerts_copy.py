"""The words an alert carries (SPEC.md 6.8, 7.5, 11.10): its window, its place, its instruction.

Measured on the 2 July re-bake: 59 of 60 alerts at 08:40 are still over their threshold when the
forecast ends, every one of them is an ordinary street, and ``ward`` is null on all 21,296 Mumbai
segments. So "Pipeline Road: depth above 45 cm from 09:55 to 11:40" said two untrue things to a
ward officer - that the water goes at 11:40, and nothing about where Pipeline Road is. These pin
the copy that replaced it, and that the CAP 1.2 document still validates with it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from varuna_products.alerts import (
    LOCALITY_RADIUS_M,
    alert_identity,
    build_alerts,
    cap_xml,
    landmark,
    nearest_locality,
)
from varuna_products.notify import SMS_LIMIT, sms_text
from varuna_products.schemas import validate_cap

IST = timezone(timedelta(hours=5, minutes=30))
CYCLE = datetime(2019, 7, 2, 8, 40, tzinfo=IST)
N_STEPS = 36

HINDMATA: dict[str, Any] = {
    "hotspot_id": "MUM-HS-01",
    "slug": "hindmata",
    "name": "Hindmata junction",
    "ward": "F/S",
    "lon": 72.841,
    "lat": 19.012,
    # Dry: the register point raises nothing, it is only here as a landmark.
    "depth_cm": [0.0] * N_STEPS,
}
"""A register point with a synthetic dry series; its position is the one in SPEC.md 3.3."""


def _times() -> tuple[datetime, ...]:
    return tuple(CYCLE + timedelta(minutes=5 * (i + 1)) for i in range(N_STEPS))


def _street_alert(series: list[float], point: tuple[float, float] | None) -> dict[str, Any]:
    from varuna_products import alerts as module

    module.STREET_POINTS.clear()
    if point is not None:
        module.STREET_POINTS["Test Marg"] = point
    queue = build_alerts(
        [HINDMATA], "MUM-TEST", CYCLE, _times(), mode="baked", streets={"Test Marg": series}
    )
    assert len(queue) == 1, "the fixture raises exactly one street alert"
    return dict(queue[0])


OPEN_ENDED = [0.0] * 8 + [50.0] * (N_STEPS - 8)
"""Over 45 cm from the ninth step to the last: the window ends with the forecast."""

CLOSED = [0.0] * 8 + [50.0] * 6 + [0.0] * (N_STEPS - 14)
"""Over 45 cm for six steps, then dry: a window with a real end."""


def test_a_window_that_runs_to_the_horizon_says_the_forecast_ends_there() -> None:
    alert = _street_alert(OPEN_ENDED, None)
    assert alert["headline"] == "Test Marg: depth above 45 cm from 09:25 until at least 11:40"
    assert alert["window_open_ended"] is True
    # The horizon stays in the product and in CAP `expires`: the words change, the times do not.
    assert alert["window_to"] == "2019-07-02T11:40:00+05:30"


def test_a_window_with_an_end_keeps_from_and_to() -> None:
    alert = _street_alert(CLOSED, None)
    assert alert["headline"] == "Test Marg: depth above 45 cm from 09:25 to 09:50"
    assert alert["window_open_ended"] is False


def test_the_instruction_no_longer_says_for_the_window() -> None:
    alert = _street_alert(OPEN_ENDED, None)
    assert "for the window" not in alert["instruction"]
    assert alert["instruction"].startswith("Avoid Test Marg. Peak forecast 50 cm.")


def test_a_street_near_a_register_point_is_placed_by_it() -> None:
    # About 350 m north-east of Hindmata: well inside the radius.
    alert = _street_alert(OPEN_ENDED, (72.8440, 19.0140))
    assert alert["locality"] == "near Hindmata junction"
    assert alert["headline"].startswith("Test Marg, near Hindmata junction: depth above 45 cm")
    assert alert["name"] == "Test Marg"
    # The identity is the street, not the sentence: an acknowledgement made before the locality
    # existed still finds the same situation.
    assert alert["area_desc"] == "Test Marg"
    assert alert_identity(alert) == "segment|Test Marg|severe"


def test_a_street_far_from_every_register_point_carries_no_locality() -> None:
    # About 5 km north: outside the radius.
    alert = _street_alert(OPEN_ENDED, (72.841, 19.057))
    assert alert["locality"] is None
    assert alert["headline"].startswith("Test Marg: depth above 45 cm")


def test_nearest_locality_is_the_closest_within_the_radius_and_ties_sort_by_name() -> None:
    spots = [
        {"name": "Zeta junction", "lon": 72.84, "lat": 19.01},
        {"name": "Alpha junction", "lon": 72.84, "lat": 19.01},
        {"name": "Far junction", "lon": 72.90, "lat": 19.10},
        {"name": None, "lon": 72.84, "lat": 19.01},
    ]
    assert nearest_locality(72.84, 19.01, spots) == "near Alpha junction"
    assert nearest_locality(72.84, 19.01, list(reversed(spots))) == "near Alpha junction"
    assert nearest_locality(None, 19.01, spots) is None
    # One degree of latitude is about 111 km, so 0.02 degrees is well past 1.5 km.
    assert nearest_locality(72.84, 19.03, spots[:2]) is None
    assert LOCALITY_RADIUS_M == 1500.0


@pytest.mark.parametrize(
    ("register_name", "short"),
    [
        # Names as the Mumbai register (city/mumbai/hotspots.geojson) spells them.
        ("Hindmata junction (Hindmata Cinema, Dr B. Ambedkar Marg)", "Hindmata junction"),
        ("King's Circle / Maheshwari Udyan junction", "King's Circle"),
        ("Postal Colony, Chembur", "Postal Colony"),
        ("Kamani junction, LBS Marg (Kurla)", "Kamani junction"),
        ("Sakinaka (Tele Exchange Lane)", "Sakinaka"),
        ("Sion Circle", "Sion Circle"),
        ("(odd)", "(odd)"),
    ],
)
def test_a_register_name_is_cut_to_its_landmark(register_name: str, short: str) -> None:
    assert landmark(register_name) == short


def test_the_locality_uses_the_landmark_not_the_register_name() -> None:
    spots = [{"name": "Gandhi Market (Matunga)", "lon": 72.858, "lat": 19.032}]
    assert nearest_locality(72.859, 19.033, spots) == "near Gandhi Market"


def test_the_new_copy_is_valid_cap_1_2() -> None:
    for series, point in ((OPEN_ENDED, (72.8440, 19.0140)), (CLOSED, None)):
        alert = _street_alert(series, point)
        errors = validate_cap(cap_xml(alert))
        assert not errors, errors[:2]


def test_the_copy_is_the_same_on_a_second_build() -> None:
    # Rule 8: the locality search must not depend on anything but its inputs.
    first = _street_alert(OPEN_ENDED, (72.8440, 19.0140))
    second = _street_alert(OPEN_ENDED, (72.8440, 19.0140))
    assert first == second


DEMO_RUNS = Path(__file__).resolve().parents[3] / "demo" / "runs"


def test_the_longest_headline_on_the_demo_cycles_still_carries_the_sms_instruction() -> None:
    """Every listed alert of the seven committed cycles, re-headlined as this module now writes it.

    The SMS is one segment and ``sms_text`` cuts the tail to fit, so the instruction is what goes
    first. "still above at 11:40, the end of the forecast" lost it on 29 of these 152 once the
    locality joined the headline; the short form must lose it on none.
    """
    runs = sorted(DEMO_RUNS.glob("MUM-*"))
    if not runs:
        pytest.skip("demo/runs is not in this checkout")
    lengths: list[int] = []
    for run in runs:
        body = json.loads((run / "alerts.json").read_text(encoding="utf-8"))
        register = json.loads((run / "hotspots.json").read_text(encoding="utf-8"))
        spots = register.get("hotspots", []) if isinstance(register, dict) else register
        for listed in body["alerts"]:
            open_ended = listed.get("window_open_ended")
            if open_ended is None:
                # Written before the product said so: every demo window that ends at the horizon.
                open_ended = listed["window_to"][11:16] == (
                    datetime.fromisoformat(body["hysteresis"]["cycle_ts"]) + timedelta(hours=3)
                ).strftime("%H:%M")
            locality = (
                None
                if listed["scope"] == "hotspot"
                else nearest_locality(listed.get("lon"), listed.get("lat"), spots)
            )
            place = f"{listed['area_desc']}, {locality}" if locality else listed["area_desc"]
            start, end = listed["window_from"][11:16], listed["window_to"][11:16]
            when = f"from {start} until at least {end}" if open_ended else f"from {start} to {end}"
            headline = f"{place}: depth above {listed['threshold_cm']} cm {when}"
            text = sms_text({**listed, "headline": headline})
            lengths.append(len(headline))
            assert len(text) <= SMS_LIMIT
            assert text.endswith("Avoid the street."), text
    assert len(lengths) == 152
    assert max(lengths) == 116

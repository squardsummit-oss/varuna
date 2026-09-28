from __future__ import annotations

from datetime import datetime, timedelta

from varuna_schemas import constants as c


def test_profile_thresholds_follow_spec_11_8() -> None:
    assert c.PROFILE_THRESHOLDS_CM["two_wheeler"] == 15
    assert c.PROFILE_THRESHOLDS_CM["car"] == 30
    assert c.PROFILE_THRESHOLDS_CM["ambulance"] == 30
    assert c.PROFILE_THRESHOLDS_CM["bus"] == 45
    assert c.PROFILE_THRESHOLDS_CM["fire_tender"] == 45
    assert c.PROFILE_THRESHOLDS_CM["pedestrian"] == 30
    assert set(c.PROFILE_THRESHOLDS_CM) == set(c.VEHICLE_PROFILES)
    assert set(c.PROFILE_RISK_TOLERANCE) == set(c.VEHICLE_PROFILES)
    assert c.PROFILE_RISK_TOLERANCE["ambulance"] == 0.2
    assert all(v == 0.5 for k, v in c.PROFILE_RISK_TOLERANCE.items() if k != "ambulance")
    assert c.RESCUE_THRESHOLD_CM == 60 and c.PEDESTRIAN_HV_LIMIT_M2S == 0.5


def test_depth_thresholds_and_bands() -> None:
    assert c.DEPTH_THRESHOLDS_CM == (15, 30, 45, 60)
    assert c.DEPTH_BAND_EDGES_CM == (5, 15, 30, 45, 60)
    assert set(c.PROFILE_THRESHOLDS_CM.values()) <= set(c.DEPTH_THRESHOLDS_CM)
    assert set(c.ALERT_LEVEL_THRESHOLDS_CM.values()) == {15, 30, 45}
    assert c.DEPTH_HINT_CM == {"ankle": (10, 8), "knee": (45, 12), "waist": (90, 15)}


def test_cycle_budgets_and_stages() -> None:
    assert set(c.STAGE_BUDGET_MS) == set(c.CYCLE_STAGES)
    assert c.STAGE_BUDGET_MS["sky"] == 5000 and c.STAGE_BUDGET_MS["twin"] == 8000
    assert c.STAGE_BUDGET_MS["publish"] == c.BAKED_PUBLISH_BUDGET_MS == 200
    # SPEC.md 11.11: Sky 5 s + Twin 8 s + the rest 2 s = 15 s; Flash runs beside Twin.
    assert c.STAGE_BUDGET_MS["flash"] <= c.STAGE_BUDGET_MS["twin"]
    assert c.STAGE_BUDGET_MS["sky"] + c.STAGE_BUDGET_MS["twin"] + 2000 == c.TOTAL_CYCLE_BUDGET_MS
    assert all(ms > 0 for ms in c.STAGE_BUDGET_MS.values())
    assert "idle" not in c.CYCLE_STAGES
    assert c.N_STEPS * c.STEP_MIN == c.LEAD_MAX_MIN == 180


def test_topics_and_time() -> None:
    assert c.BUS_TOPICS == (
        "radar.frames",
        "gauges.obs",
        "traffic.speeds",
        "reports.raw",
        "tide.stage",
        "runs.published",
        "cycle.stage",
        "alerts",
    )
    assert {
        "runs.published",
        "cycle.stage",
        "obs.assimilated",
        "replay.clock",
        "onboard.progress",
    } <= set(c.WS_TOPICS)
    assert all(topic.startswith("alert.") for topic in c.WS_TOPICS if "alert" in topic)
    assert datetime(2019, 7, 2, 17, 40, tzinfo=c.IST).utcoffset() == timedelta(hours=5, minutes=30)
    assert c.CITY_CODES == {"mumbai": "MUM", "chennai": "CHN"} and c.CITY_SLUGS["CHN"] == "chennai"


def test_honesty_labels_are_sentence_case_ui_copy() -> None:
    for label in (
        c.EMULATOR_LABEL,
        c.INFERRED_DRAINS_LABEL,
        c.RECONSTRUCTED_REPLAY_LABEL,
        c.SYNTHETIC_PUMPS_LABEL,
    ):
        assert label[0].isupper() and not label.isupper() and not label.endswith(".")

"""Every registered model round-trips through JSON, rejects unknown keys and normalises
timestamps to IST; plus the domain validators that other services rely on."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from varuna_schemas.constants import IST, PROFILE_THRESHOLDS_CM
from varuna_schemas.models import (
    MODEL_REGISTRY,
    Alert,
    Asset,
    BBox,
    BundleManifest,
    CityConfig,
    ContingencyTable,
    ErrorEnvelope,
    GroundTruthPin,
    LiveEvent,
    ObservationEffect,
    PhysicsCheckRequest,
    PhysicsCheckResponse,
    Polygon,
    ReachabilityResponse,
    ReplayClock,
    ReportIn,
    RoadSegment,
    RouteRequest,
    RouteResponse,
    SegmentDelta,
    SegmentForecastRow,
    VarunaModel,
)
from varuna_schemas.samples import RUN_ID, T0, all_samples, sample, sample_json, t

MODEL_NAMES = list(MODEL_REGISTRY)


def _walk(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _walk(item)
    else:
        yield value


# --------------------------------------------------------------------- registry and samples
def test_every_model_has_a_sample_and_every_sample_is_registered() -> None:
    samples = all_samples()
    assert set(samples) == set(MODEL_REGISTRY)
    for name, instance in samples.items():
        assert type(instance) is MODEL_REGISTRY[name], name
        assert issubclass(MODEL_REGISTRY[name], VarunaModel)


def test_registry_order_is_stable_and_unique() -> None:
    assert len(MODEL_NAMES) == len(set(MODEL_NAMES))
    assert MODEL_NAMES[0] == "BBox" and "RunMeta" in MODEL_NAMES and "LiveEvent" in MODEL_NAMES


# --------------------------------------------------------------------- round trips
@pytest.mark.parametrize("name", MODEL_NAMES)
def test_round_trip_via_json_dict(name: str) -> None:
    original = sample(name)
    dumped = original.model_dump(mode="json")
    text = json.dumps(dumped, sort_keys=True)
    again = MODEL_REGISTRY[name].model_validate(json.loads(text))
    assert again == original
    assert again.model_dump(mode="json") == dumped


@pytest.mark.parametrize("name", MODEL_NAMES)
def test_round_trip_via_model_dump_json(name: str) -> None:
    original = sample(name)
    again = MODEL_REGISTRY[name].model_validate_json(original.model_dump_json())
    assert again == original


@pytest.mark.parametrize("name", MODEL_NAMES)
def test_unknown_keys_are_rejected(name: str) -> None:
    model = MODEL_REGISTRY[name]
    if model.model_config.get("extra") == "allow":
        pytest.skip(f"{name} deliberately allows extra keys")
    payload = sample_json(name)
    payload["not_a_field"] = 1
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize("name", MODEL_NAMES)
def test_timestamps_are_ist_and_offset_is_serialised(name: str) -> None:
    instance = sample(name)
    for value in _walk(instance.model_dump(mode="python")):
        if isinstance(value, datetime):
            assert value.tzinfo is not None
            assert value.utcoffset() == timedelta(hours=5, minutes=30), name
    for value in _walk(instance.model_dump(mode="json")):
        if (
            isinstance(value, str)
            and value[:4].isdigit()
            and "T" in value
            and value.count(":") >= 2
        ):
            assert value.endswith("+05:30"), (name, value)


def test_utc_input_is_normalised_to_ist() -> None:
    row = sample("SegmentForecastRow")
    assert isinstance(row, SegmentForecastRow)
    payload = row.model_dump(mode="json")
    payload["valid_ts"] = "2019-07-02T12:20:00Z"
    again = SegmentForecastRow.model_validate(payload)
    assert again.valid_ts == datetime(2019, 7, 2, 17, 50, tzinfo=IST)
    assert again.valid_ts.utcoffset() == timedelta(hours=5, minutes=30)
    payload["valid_ts"] = "2019-07-02T17:50:00"
    with pytest.raises(ValidationError):
        SegmentForecastRow.model_validate(payload)


def test_json_schema_generation_for_every_model() -> None:
    for name, model in MODEL_REGISTRY.items():
        schema = model.model_json_schema(mode="validation")
        assert schema["title"] == name
        assert schema["type"] == "object"


# --------------------------------------------------------------------- common types
def test_bbox_accepts_list_and_checks_order() -> None:
    box = BBox.model_validate([72.815, 18.995, 72.905, 19.135])
    assert box.as_tuple() == (72.815, 18.995, 72.905, 19.135)
    assert box.contains(72.841, 19.012) and not box.contains(72.0, 19.0)
    assert box.center == pytest.approx((72.86, 19.065))
    with pytest.raises(ValidationError):
        BBox.model_validate([72.905, 18.995, 72.815, 19.135])
    with pytest.raises(ValidationError):
        BBox.model_validate([1, 2, 3])


def test_polygon_rings_must_close() -> None:
    open_ring = [(72.84, 19.01), (72.85, 19.01), (72.85, 19.02), (72.84, 19.02)]
    with pytest.raises(ValidationError):
        Polygon(coordinates=[open_ring])
    closed = Polygon(coordinates=[[*open_ring, open_ring[0]]])
    assert closed.type == "Polygon"


def test_ids_accept_integers_and_store_strings() -> None:
    row = sample("SegmentForecastRow")
    payload = row.model_dump(mode="json")
    payload["segment_id"] = 88213
    assert SegmentForecastRow.model_validate(payload).segment_id == "88213"
    payload["segment_id"] = ""
    with pytest.raises(ValidationError):
        SegmentForecastRow.model_validate(payload)


# --------------------------------------------------------------------- forecast
def test_segment_forecast_orderings_and_profiles() -> None:
    row = sample("SegmentForecastRow")
    assert isinstance(row, SegmentForecastRow)
    assert row.p_gt(45) == row.p_gt_45
    assert row.p_impassable("two_wheeler") == row.p_gt_15
    assert row.p_impassable("bus") == row.p_gt_45
    for profile, threshold in PROFILE_THRESHOLDS_CM.items():
        assert row.p_impassable(profile) == row.p_gt(threshold)
    with pytest.raises(ValueError, match="15, 30, 45, 60"):
        row.p_gt(20)
    payload = row.model_dump(mode="json")
    bad = {**payload, "depth_p10_cm": 60.0}
    with pytest.raises(ValidationError, match="p10 <= p50 <= p90"):
        SegmentForecastRow.model_validate(bad)
    bad = {**payload, "p_gt_60": 0.95}
    with pytest.raises(ValidationError, match="non-increasing"):
        SegmentForecastRow.model_validate(bad)
    bad = {**payload, "safe_until": {"tank": None}}
    with pytest.raises(ValidationError):
        SegmentForecastRow.model_validate(bad)


def test_segment_series_computed_peak() -> None:
    series = sample("SegmentSeries")
    dumped = series.model_dump(mode="json")
    assert dumped["peak_p50_cm"] == max(p["p50_cm"] for p in dumped["points"])
    assert dumped["time_to_peak"].endswith("+05:30")


# --------------------------------------------------------------------- observations and reports
def test_report_depth_hints_and_ack() -> None:
    report = sample("ReportIn")
    assert isinstance(report, ReportIn)
    assert (report.depth_cm, report.sd_cm) == (45, 12)
    assert ReportIn(lat=19.0, lon=72.8, depth_hint="ankle").depth_cm == 10
    assert ReportIn(lat=19.0, lon=72.8, depth_hint="waist").sd_cm == 15
    with pytest.raises(ValidationError):
        ReportIn(lat=19.0, lon=72.8, depth_hint="chest")  # type: ignore[arg-type]
    ack = sample("ReportAck")
    assert ack.model_dump(mode="json")["status"] == "queued"


def test_observation_effect_allows_extra_detail_and_source_url_must_be_http() -> None:
    effect = ObservationEffect.model_validate({"segments_changed": 2, "enkf_iterations": 1})
    assert effect.model_dump()["enkf_iterations"] == 1
    payload = sample_json("Observation")
    payload["source_url"] = "ftp://example.org/log"
    with pytest.raises(ValidationError, match="http"):
        MODEL_REGISTRY["Observation"].model_validate(payload)


# --------------------------------------------------------------------- alerts
def test_alert_level_threshold_and_state_consistency() -> None:
    alert = sample("Alert")
    assert isinstance(alert, Alert)
    assert alert.model_dump(mode="json")["active"] is True
    payload = alert.model_dump(mode="json")
    with pytest.raises(ValidationError, match="implies threshold"):
        Alert.model_validate({**payload, "level": "watch"})
    with pytest.raises(ValidationError, match="acknowledged_by"):
        Alert.model_validate({**payload, "state": "acknowledged", "acknowledged_by": None})
    with pytest.raises(ValidationError, match="cleared_ts"):
        Alert.model_validate({**payload, "state": "cleared"})
    cleared = Alert.model_validate(
        {**payload, "state": "cleared", "cleared_ts": t(150).isoformat()}
    )
    assert cleared.active is False
    with pytest.raises(ValidationError):
        Alert.model_validate({**payload, "cap_status": "Test"})


# --------------------------------------------------------------------- bundles and ground truth
def test_ground_truth_pins_need_sources_and_are_never_synthetic() -> None:
    payload = sample_json("GroundTruthPin")
    with pytest.raises(ValidationError, match="synthetic"):
        GroundTruthPin.model_validate({**payload, "synthetic": True})
    with pytest.raises(ValidationError):
        GroundTruthPin.model_validate({**payload, "source_url": "BMC said so"})
    with pytest.raises(ValidationError):
        GroundTruthPin.model_validate({k: v for k, v in payload.items() if k != "source_url"})


def test_bundle_manifest_window_sources_and_cycles() -> None:
    manifest = sample("BundleManifest")
    assert isinstance(manifest, BundleManifest)
    assert manifest.duration_min == 360 and manifest.n_cycles == 73
    assert manifest.is_reconstructed
    payload = manifest.model_dump(mode="json")
    with pytest.raises(ValidationError, match="at least one public source"):
        BundleManifest.model_validate({**payload, "sources": []})
    design = BundleManifest.model_validate({**payload, "label": "Design storm", "sources": []})
    assert not design.is_reconstructed
    with pytest.raises(ValidationError, match="t1 must be after t0"):
        BundleManifest.model_validate({**payload, "t1": payload["t0"]})
    with pytest.raises(ValidationError, match="positive minutes"):
        BundleManifest.model_validate({**payload, "cadences": {"radar": 0}})
    with pytest.raises(ValidationError):
        BundleManifest.model_validate({**payload, "id": "mum-2019"})


# --------------------------------------------------------------------- city and layers
def test_city_config_from_yaml_and_code_default(tmp_path: Path) -> None:
    cfg = sample("CityConfig")
    assert isinstance(cfg, CityConfig)
    assert cfg.code == "MUM" and cfg.crs_string == "EPSG:32643"
    assert cfg.radar_domain.n_px == 120
    text = json.dumps(cfg.model_dump(mode="json"))
    path = tmp_path / "mumbai.yaml"
    path.write_text(text, encoding="utf-8")  # JSON is valid YAML
    assert CityConfig.from_yaml(path) == cfg
    payload = cfg.model_dump(mode="json")
    with pytest.raises(ValidationError, match="not in CITY_CODES"):
        CityConfig.model_validate({**payload, "id": "pune", "code": ""})
    pune = CityConfig.model_validate({**payload, "id": "pune", "code": "PUN"})
    assert pune.code == "PUN"


def test_road_segment_and_asset_rules() -> None:
    seg = sample("RoadSegment")
    assert isinstance(seg, RoadSegment)
    payload = seg.model_dump(mode="json")
    with pytest.raises(ValidationError, match="z_min_m"):
        RoadSegment.model_validate({**payload, "z_min_m": 9.0})
    with pytest.raises(ValidationError):
        RoadSegment.model_validate({**payload, "road_class": "highway"})
    with pytest.raises(ValidationError, match="source_url"):
        Asset(id="p1", name="Parel depot", kind="depot", lon=72.83, lat=19.0)
    synthetic = Asset(
        id="p1", name="Parel depot", kind="depot", lon=72.83, lat=19.0, synthetic=True
    )
    assert synthetic.source_url is None
    with pytest.raises(ValidationError, match="http"):
        Asset(id="k", name="KEM", kind="hospital", lon=72.8, lat=19.0, source_url="osm")


# --------------------------------------------------------------------- replay and cycle
def test_replay_clock_window_and_progress() -> None:
    clock = sample("ReplayClock")
    assert isinstance(clock, ReplayClock)
    assert clock.model_dump(mode="json")["progress"] == pytest.approx(160 / 360)
    payload = clock.model_dump(mode="json")
    with pytest.raises(ValidationError, match="within"):
        ReplayClock.model_validate({**payload, "sim_time": t(400).isoformat()})


def test_cycle_status_budget_and_over_budget() -> None:
    status = sample("CycleStatus")
    dumped = status.model_dump(mode="json")
    assert dumped["busy"] is True and dumped["elapsed_ms"] == sum(status.stage_ms.values())
    assert dumped["budget_ms"]["sky"] == 5000 and dumped["total_budget_ms"] == 15000
    assert status.over_budget == []
    status.stage_ms = {**status.stage_ms, "twin": 9000}
    assert status.over_budget == ["twin"]


# --------------------------------------------------------------------- route and reachability
def test_route_request_defaults_and_response_delta() -> None:
    req = RouteRequest(
        origin=(72.8419, 19.0035), destination=(72.8628, 19.0176), profile="ambulance"
    )
    assert req.effective_risk_tolerance == 0.2 and req.threshold_cm == 30
    car = RouteRequest(origin=(72.8419, 19.0035), destination=(72.8628, 19.0176))
    assert car.profile == "car" and car.effective_risk_tolerance == 0.5
    with pytest.raises(ValidationError):
        RouteRequest(origin=(200.0, 19.0), destination=(72.86, 19.02))
    resp = sample("RouteResponse")
    assert isinstance(resp, RouteResponse)
    assert resp.model_dump(mode="json")["eta_delta_min"] == pytest.approx(0.0)
    assert resp.run_id == RUN_ID and resp.valid_ts == T0


def test_reachability_collapse_flag() -> None:
    reach = sample("ReachabilityResponse")
    assert isinstance(reach, ReachabilityResponse)
    assert reach.area_km2(15) == 3.1 and reach.area_km2(20) is None
    dumped = reach.model_dump(mode="json")
    assert dumped["catchment_ratio"] == pytest.approx(3.1 / 9.4)
    assert dumped["collapse"] is True
    healthy = ReachabilityResponse.model_validate({**dumped, "dry_area_15_km2": 4.0})
    assert healthy.collapse is False
    payload = reach.model_dump(mode="json")
    payload["isochrones"].append(payload["isochrones"][-1])
    with pytest.raises(ValidationError, match="one isochrone per"):
        ReachabilityResponse.model_validate(payload)


# --------------------------------------------------------------------- what-if
def test_segment_delta_classes_and_physics_check() -> None:
    assert SegmentDelta(segment_id="1", delta_p50_cm=-3.0).change == "improved"
    assert SegmentDelta(segment_id="1", delta_p50_cm=-2.9).change == "unchanged"
    assert SegmentDelta(segment_id="1", delta_p50_cm=2.9).change == "unchanged"
    assert SegmentDelta(segment_id="1", delta_p50_cm=3.0).change == "worse"
    check = sample("PhysicsCheckResponse")
    assert isinstance(check, PhysicsCheckResponse)
    dumped = check.model_dump(mode="json")
    assert dumped["max_diff_cm"] == 4 and dumped["max_diff_hotspot"] == "Sion Circle"
    assert dumped["agrees"] is True
    assert dumped["summary"] == "Emulator vs physics: max difference 4 cm at Sion Circle"
    with pytest.raises(ValidationError, match="exactly one"):
        PhysicsCheckRequest()
    with pytest.raises(ValidationError, match="exactly one"):
        PhysicsCheckRequest(whatif_id="wi-1", request=sample("WhatIfRequest"))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        MODEL_REGISTRY["WhatIfRequest"].model_validate({"rain_scale": 3.0})


# --------------------------------------------------------------------- verification
def test_contingency_scores() -> None:
    table = ContingencyTable(hits=8, misses=2, false_alarms=1, correct_negatives=25)
    assert table.n == 36
    assert table.csi == pytest.approx(8 / 11)
    assert table.pod == pytest.approx(0.8)
    assert table.far == pytest.approx(1 / 9)
    empty = ContingencyTable(hits=0, misses=0, false_alarms=0, correct_negatives=10)
    assert empty.csi is None and empty.pod is None and empty.far is None


# --------------------------------------------------------------------- api envelopes
def test_error_envelope_and_live_event() -> None:
    env = ErrorEnvelope.make(
        "run_not_found", "Run is not baked yet. Press Play on the replay.", RUN_ID
    )
    assert env.model_dump(mode="json") == {
        "error": {
            "code": "run_not_found",
            "message": "Run is not baked yet. Press Play on the replay.",
            "run_id": RUN_ID,
            "details": None,
        }
    }
    with pytest.raises(ValidationError):
        ErrorEnvelope.make("Run Not Found", "x")
    event = LiveEvent(topic="replay.clock", ts=datetime(2019, 7, 2, 12, 10, tzinfo=UTC))
    assert event.ts.utcoffset() == timedelta(hours=5, minutes=30)
    with pytest.raises(ValidationError):
        LiveEvent(topic="weather.update", ts=T0)  # type: ignore[arg-type]

"""The synthetic streams of a reconstructed replay (tasks P2.4-P2.6).

What these tests are really guarding is rule 7 rather than arithmetic: a stream must say what
it is, a gauge must sit where a source says it sits, a tide the sources do not carry must be
labelled illustrative, a pin must never grow a depth nobody wrote down. The numerical
assertions exist to keep the generators honest about the field they claim to sample.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from varuna_replay import streams
from varuna_replay.domain import StormDomain, step_times_min
from varuna_schemas.constants import IST
from varuna_schemas.models.bundle import GroundTruthPin
from varuna_schemas.models.city import RadarDomain

T0 = datetime(2019, 7, 2, 5, 40, tzinfo=IST)
WINDOW_MIN = 240.0
CRS = 32643

PIN_LON, PIN_LAT = 72.8421396, 19.010099


@pytest.fixture
def small_domain() -> StormDomain:
    """A 20 km, 500 m domain over the Mumbai area of interest."""
    return StormDomain.from_radar_domain(
        RadarDomain(center_lon=72.86, center_lat=19.065, size_km=20.0, res_m=500.0), CRS
    )


@pytest.fixture
def truth_times() -> np.ndarray:
    return step_times_min(0.0, WINDOW_MIN, 5.0)


def uniform_truth(domain: StormDomain, times: np.ndarray, rate_mm_h: float) -> np.ndarray:
    """A rain cube that holds one rate everywhere, so a sampled reading has a known answer."""
    return np.full((times.size, *domain.shape), rate_mm_h, dtype=np.float32)


def gauge_site(index: int = 0, **overrides: Any) -> streams.GaugeSite:
    defaults: dict[str, Any] = {
        "id": f"BMC-TEST-{index:02d}",
        "name": f"Test gauge {index}",
        "lon": 72.85 + index * 0.005,
        "lat": 19.05 + index * 0.005,
        "operator": "BMC",
        "source_url": "https://www.mumbairain.org/",
        "in_aoi": True,
    }
    defaults.update(overrides)
    return streams.GaugeSite(**defaults)


def chain(
    n: int = 40, *, first_x: float = 260_000.0, y: float = 2_103_400.0
) -> streams.SegmentTable:
    """A straight chain of segments 100 m apart, so hop distance is index distance.

    The default sits 13 km west of :data:`PIN_LON`/:data:`PIN_LAT`, out of snapping range, so a
    test that wants a pin matched has to place the chain under it deliberately."""
    return streams.SegmentTable(
        segment_id=np.array([f"S{index:03d}" for index in range(n)], dtype=object),
        road_class=np.array(["primary"] * n, dtype=object),
        free_flow_kmh=np.full(n, 40.0),
        u=np.arange(n, dtype=np.int64),
        v=np.arange(1, n + 1, dtype=np.int64),
        x=first_x + np.arange(n) * 100.0,
        y=np.full(n, y),
    )


def pin(**overrides: Any) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "id": "MUM19-07",
        "ts": "2019-07-02T08:07:00+05:30",
        "ts_uncertainty_min": 30,
        "name": "Kranti Nagar, Kurla East",
        "lon": PIN_LON,
        "lat": PIN_LAT,
        "depth_cm": None,
        "depth_phrase": None,
        "kind": "log",
        "text": "BMC log: waterlogging, traffic diverted",
        "source_url": "https://scroll.in/latest/929092",
        "inside_aoi": True,
        "synthetic": False,
    }
    properties.update(overrides)
    return properties


# ============================================================================ gauges
def test_only_stations_with_a_published_coordinate_become_gauges(tmp_path: Path) -> None:
    """A station with no coordinate is dropped, not placed by guesswork (rule 7)."""
    roster = tmp_path / "stations.json"
    roster.write_text(
        json.dumps(
            {
                "stations": [
                    {
                        "id": "IMD-SCZ-43003",
                        "name": "IMD Mumbai-Santacruz observatory",
                        "lon": 72.868,
                        "lat": 19.089,
                        "operator": "IMD",
                        "source_url": "https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv",
                        "in_aoi": True,
                    },
                    {
                        "id": "BMC-FS-DADAR",
                        "name": "Dadar fire station",
                        "lon": None,
                        "lat": None,
                        "operator": "BMC",
                        "source_url": "https://www.mumbairain.org/",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    sites = streams.load_gauge_sites(roster)
    assert [site.id for site in sites] == ["IMD-SCZ-43003"]
    assert sites[0].name == "IMD Mumbai-Santacruz observatory"


def test_gauge_readings_sample_the_truth_field_and_say_they_are_synthetic(
    small_domain: StormDomain, truth_times: np.ndarray
) -> None:
    """12 mm/h held everywhere is 1.0 mm in five minutes, before the stated 10 % noise."""
    sites = [gauge_site(0), gauge_site(1)]
    rows = streams.gauge_rows(
        sites,
        uniform_truth(small_domain, truth_times, 12.0),
        truth_times,
        small_domain,
        T0,
        seed=2019,
    )
    assert len(rows) == len(sites) * (int(WINDOW_MIN // streams.GAUGE_CADENCE_MIN) + 1)
    assert all(row["synthetic"] is True for row in rows)
    assert {row["station_id"] for row in rows} == {"BMC-TEST-00", "BMC-TEST-01"}
    assert rows[0]["ts"] == T0
    assert rows[-1]["ts"] == T0 + timedelta(minutes=WINDOW_MIN)

    readings = np.array([row["mm_5min"] for row in rows])
    assert readings.min() >= 0.0
    assert abs(readings.mean() - 1.0) < 0.1, "the mean reading must be the field, not the noise"
    assert readings.std() > 0.0, "10 % multiplicative noise must actually vary the readings"


def test_gauge_noise_is_seeded(small_domain: StormDomain, truth_times: np.ndarray) -> None:
    truth = uniform_truth(small_domain, truth_times, 12.0)
    sites = [gauge_site(0)]
    first = streams.gauge_rows(sites, truth, truth_times, small_domain, T0, seed=2019)
    again = streams.gauge_rows(sites, truth, truth_times, small_domain, T0, seed=2019)
    other = streams.gauge_rows(sites, truth, truth_times, small_domain, T0, seed=7)
    assert [row["mm_5min"] for row in first] == [row["mm_5min"] for row in again]
    assert [row["mm_5min"] for row in first] != [row["mm_5min"] for row in other]


def test_a_dry_field_gives_dry_gauges(small_domain: StormDomain, truth_times: np.ndarray) -> None:
    rows = streams.gauge_rows(
        [gauge_site(0)],
        uniform_truth(small_domain, truth_times, 0.0),
        truth_times,
        small_domain,
        T0,
        seed=2019,
    )
    assert {row["mm_5min"] for row in rows} == {0.0}


# ============================================================================ tide
def test_the_tide_rises_through_the_window_towards_the_documented_high_water() -> None:
    """The demo needs a rising stage so the tide-locked outfall can surcharge - and the rise is
    a consequence of the one sourced height, not an observation."""
    high_water = datetime(2019, 7, 2, 11, 30, tzinfo=IST)
    rows = streams.tide_rows(
        T0, WINDOW_MIN, high_water_m=4.92, high_water_at=high_water, period_min=745.2
    )
    stages = [row["stage_m"] for row in rows]
    assert stages == sorted(stages), "the stage must rise monotonically across the window"
    assert stages[0] < 0.2
    assert 3.5 < stages[-1] < 4.5
    assert all(row["source"] == "illustrative" for row in rows)
    assert rows[0]["ts"] == T0
    assert rows[-1]["ts"] == T0 + timedelta(minutes=WINDOW_MIN)


def test_the_tide_crest_is_the_sourced_height() -> None:
    high_water = datetime(2019, 7, 2, 11, 30, tzinfo=IST)
    rows = streams.tide_rows(
        high_water - timedelta(minutes=60),
        120.0,
        high_water_m=4.92,
        high_water_at=high_water,
        period_min=745.2,
    )
    crest = max(row["stage_m"] for row in rows)
    assert crest == pytest.approx(4.92, abs=1e-3)


# ============================================================================ traffic
def test_the_baseline_follows_the_weekday_hour_table() -> None:
    segments = chain(4)
    frame = streams.traffic_frame(segments, [], T0, 60.0, seed=2019)
    at_0540 = frame[frame["ts"].str.startswith("2019-07-02T05:40")]
    at_0640 = frame[frame["ts"].str.startswith("2019-07-02T06:40")]
    ratio = at_0640["baseline_kmh"].mean() / at_0540["baseline_kmh"].mean()
    expected = streams.WEEKDAY_HOUR_FACTOR[6] / streams.WEEKDAY_HOUR_FACTOR[5]
    assert ratio == pytest.approx(expected, rel=0.01)
    assert bool(frame["synthetic"].all())


def test_a_pin_collapses_its_segment_below_five_kmh_for_two_snapshots() -> None:
    """SPEC.md 11.6 calls an anomaly only after two consecutive snapshots, so the collapse
    has to survive the 5-minute grid wherever the pin's minute happens to fall."""
    segments = chain(20)
    for offset in (60.0, 62.0, 63.5, 65.0):
        anomalies = streams.pin_anomalies(segments, [streams.PinSnap("MUM19-07", 10, 12.0, offset)])
        frame = streams.traffic_frame(segments, anomalies, T0, 120.0, seed=2019)
        flooded = frame[frame["segment_id"] == "S010"].sort_values("ts")
        below = (flooded["kmh"] < 5.0).to_numpy()
        assert below.sum() >= 2, f"pin at +{offset} min never collapsed the segment"
        assert _longest_run(below) >= 2, "the collapse must be consecutive, not scattered"


def _longest_run(flags: np.ndarray) -> int:
    best = run = 0
    for flag in flags.tolist():
        run = run + 1 if flag else 0
        best = max(best, run)
    return best


def test_the_anomaly_spreads_two_hops_and_leaves_the_rest_of_the_network_alone() -> None:
    segments = chain(20)
    anomalies = streams.pin_anomalies(segments, [streams.PinSnap("MUM19-07", 10, 12.0, 60.0)])
    hops = {anomaly.segment_index: anomaly.hop for anomaly in anomalies}
    assert hops[10] == 0
    assert hops[9] == hops[11] == 1
    assert hops[8] == hops[12] == 2
    assert 7 not in hops and 13 not in hops

    frame = streams.traffic_frame(segments, anomalies, T0, 120.0, seed=2019)
    at_peak = frame[frame["ts"].str.startswith("2019-07-02T06:40")].set_index("segment_id")
    assert at_peak.loc["S010", "kmh"] < 5.0
    assert 5.0 <= at_peak.loc["S009", "kmh"] < 15.0, "a neighbour queues, it does not flood"
    assert at_peak.loc["S008", "kmh"] < at_peak.loc["S008", "baseline_kmh"]
    assert at_peak.loc["S000", "kmh"] > 15.0, "the far network must stay at its baseline"


def test_confounders_are_three_percent_and_never_sit_on_a_flooded_segment() -> None:
    """The feed carries no incident label, so Pulse has to reject these on the evidence."""
    segments = chain(200)
    flooded = streams.pin_anomalies(segments, [streams.PinSnap("MUM19-07", 100, 5.0, 60.0)])
    excluded = {anomaly.segment_index for anomaly in flooded}
    confounders = streams.confounder_anomalies(
        segments, WINDOW_MIN, seed=2019, exclude=sorted(excluded)
    )
    assert len(confounders) == round(streams.CONFOUNDER_FRACTION * len(segments))
    assert all(anomaly.is_confounder for anomaly in confounders)
    assert not {anomaly.segment_index for anomaly in confounders} & excluded
    assert all(0.0 <= anomaly.centre_min <= WINDOW_MIN for anomaly in confounders)

    again = streams.confounder_anomalies(segments, WINDOW_MIN, seed=2019, exclude=sorted(excluded))
    assert [anomaly.segment_index for anomaly in confounders] == [
        anomaly.segment_index for anomaly in again
    ]


def test_pins_outside_the_window_get_no_anomaly_and_are_reported() -> None:
    segments = chain(20)
    snaps, skipped = streams.snap_pins(
        segments,
        [pin(id="MUM19-01", ts="2019-07-01T10:44:00+05:30"), pin(id="MUM19-07")],
        CRS,
        T0,
        WINDOW_MIN,
    )
    assert [snap.pin_id for snap in snaps] == []
    assert len(skipped) == 2
    assert any("outside the window" in reason for reason in skipped)
    assert any("from the nearest covered segment" in reason for reason in skipped)


def test_a_pin_snaps_to_the_nearest_covered_segment() -> None:
    segments = chain(20)
    x, y = streams.project([PIN_LON], [PIN_LAT], CRS)
    near = chain(20, first_x=float(x[0]) - 500.0, y=float(y[0]))
    snaps, skipped = streams.snap_pins(near, [pin()], CRS, T0, WINDOW_MIN)
    assert skipped == []
    assert snaps[0].pin_id == "MUM19-07"
    assert snaps[0].distance_m <= streams.PIN_SNAP_MAX_M
    assert snaps[0].minutes == pytest.approx(147.0)
    assert len(segments) == 20


# ============================================================================ reports
def hotspot(index: int, **overrides: Any) -> dict[str, Any]:
    spot: dict[str, Any] = {
        "hotspot_id": f"MUM-HS-{index:02d}",
        "name": f"Test hotspot {index}",
        "lon": 72.85 + index * 0.004,
        "lat": 19.03 + index * 0.004,
    }
    spot.update(overrides)
    return spot


def test_report_chips_follow_the_rain_that_fell(
    small_domain: StormDomain, truth_times: np.ndarray
) -> None:
    """30 mm/h held for four hours accumulates 0.5 mm a minute, so a chip appears only once the
    stated threshold is crossed - the rule the manifest publishes, not a hydraulic depth."""
    truth = uniform_truth(small_domain, truth_times, 30.0)
    rows = streams.synthetic_reports(
        [hotspot(1), hotspot(2)], truth, truth_times, small_domain, T0, seed=2019, count=10
    )
    assert rows, "a wet hotspot must produce reports"
    assert all(row["synthetic"] is True for row in rows)
    assert {row["depth_hint"] for row in rows} <= {"ankle", "knee", "waist"}
    for row in rows:
        minutes = (row["ts"] - T0).total_seconds() / 60.0
        threshold = {"ankle": 25.0, "knee": 55.0, "waist": 85.0}[row["depth_hint"]]
        assert 0.5 * minutes >= threshold, "a chip must not appear before its rain has fallen"


def test_a_dry_window_produces_no_citizen_reports(
    small_domain: StormDomain, truth_times: np.ndarray
) -> None:
    truth = uniform_truth(small_domain, truth_times, 0.0)
    assert (
        streams.synthetic_reports(
            [hotspot(1)], truth, truth_times, small_domain, T0, seed=2019, count=10
        )
        == []
    )


def test_reports_are_capped_and_seeded(small_domain: StormDomain, truth_times: np.ndarray) -> None:
    truth = uniform_truth(small_domain, truth_times, 30.0)
    spots = [hotspot(index) for index in range(1, 15)]
    first = streams.synthetic_reports(
        spots, truth, truth_times, small_domain, T0, seed=2019, count=8
    )
    again = streams.synthetic_reports(
        spots, truth, truth_times, small_domain, T0, seed=2019, count=8
    )
    assert len(first) == 8
    assert [row["id"] for row in first] == [row["id"] for row in again]


def test_sourced_pins_enter_the_report_stream_with_a_url_and_no_depth() -> None:
    rows = streams.pin_reports(
        [
            pin(),
            pin(id="MUM19-01", ts="2019-07-01T10:44:00+05:30"),
            pin(id="MUM19-06", depth_phrase="vehicles submerged"),
        ],
        T0,
        WINDOW_MIN,
    )
    assert [row["id"] for row in rows] == ["MUM19-06", "MUM19-07"]
    assert all(row["synthetic"] is False for row in rows)
    assert all(row["depth_hint"] is None for row in rows), "no source states a depth in cm"
    assert all(row["source_url"] for row in rows)
    assert rows[0]["depth_phrase"] == "vehicles submerged"


# ============================================================================ ground truth
def test_ground_truth_pins_are_carried_through_untouched() -> None:
    features = streams.ground_truth_features([pin(depth_phrase="vehicles submerged")])
    properties = features[0]["properties"]
    assert features[0]["geometry"]["coordinates"] == [PIN_LON, PIN_LAT]
    assert properties["depth_cm"] is None
    assert properties["depth_phrase"] == "vehicles submerged"
    assert properties["ts_uncertainty_min"] == 30
    assert properties["synthetic"] is False
    assert properties["source_url"].startswith("https://")


def test_the_rail_kind_is_mapped_to_the_contract_and_the_mapping_is_stated() -> None:
    """``rail`` is not one of the contract's kinds; all three such pins came from news blogs,
    so they are carried as ``news`` and the feature says that is what happened."""
    features = streams.ground_truth_features([pin(id="MUM19-04", kind="rail")])
    properties = features[0]["properties"]
    assert properties["kind"] == "news"
    assert "rail" in properties["note"]


def test_every_pin_validates_against_the_bundle_contract() -> None:
    features = streams.ground_truth_features(
        [pin(), pin(id="MUM19-04", kind="rail"), pin(id="MUM19-06", kind="news")]
    )
    known = set(GroundTruthPin.model_fields)
    for feature in features:
        GroundTruthPin.model_validate(
            {key: value for key, value in feature["properties"].items() if key in known}
        )


def test_a_pin_may_never_be_marked_synthetic() -> None:
    features = streams.ground_truth_features([pin(synthetic=True)])
    assert features[0]["properties"]["synthetic"] is False


def test_the_curated_draft_loads_only_pins_inside_the_area_of_interest(tmp_path: Path) -> None:
    draft = tmp_path / "draft.geojson"
    draft.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [PIN_LON, PIN_LAT]},
                        "properties": pin(),
                    },
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [72.9, 19.18]},
                        "properties": pin(id="MUM19-90", inside_aoi=False),
                    },
                    {
                        "type": "Feature",
                        "geometry": None,
                        "properties": pin(id="MUM19-13", inside_aoi=False),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    pins = streams.load_ground_truth_pins(draft)
    assert [item["id"] for item in pins] == ["MUM19-07"]
    assert pins[0]["lon"] == PIN_LON

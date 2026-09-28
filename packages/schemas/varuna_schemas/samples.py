"""Sample instances of every model for tests, API contract tests and ``/design`` stories.

Every string is about the Mumbai demo (2 July 2019: Hindmata junction, King's Circle,
Sion Circle, KEM Hospital, Sion Hospital). The numbers are test fixtures only; they must
never be served as data (SPEC.md 0.6: every number on screen comes from run artifacts).

Use :func:`sample` by class name or :func:`all_samples` for one instance per registered model.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from varuna_schemas.constants import IST
from varuna_schemas.models import (
    MODEL_REGISTRY,
    Alert,
    AlertStateChange,
    Asset,
    AttributionItem,
    AvoidedSegment,
    BBox,
    Boundary,
    BundleManifest,
    BundleSource,
    CityConfig,
    CleanTopNEffect,
    ComputeRequest,
    ContingencyTable,
    Corridor,
    CycleLogEntry,
    CycleStageEvent,
    CycleStatus,
    DesignIntensity,
    DesignStorm,
    DrainEdge,
    DrainEdgeHealth,
    DrainHealthProduct,
    DrainNode,
    EngineVersions,
    ErrorDetail,
    ErrorEnvelope,
    Feature,
    FeatureCollection,
    FlashLiteScore,
    GridSpec,
    GroundTruthPin,
    HealthStatus,
    Hotspot,
    HotspotDelta,
    HotspotExposure,
    HotspotList,
    HotspotPumpBenefit,
    HotspotScore,
    Isochrone,
    LineString,
    LiveEvent,
    MissedPin,
    MultiLineString,
    MultiPolygon,
    NestSpec,
    NodeForecastRow,
    Observation,
    ObservationEffect,
    PhysicsCheckHotspot,
    PhysicsCheckRequest,
    PhysicsCheckResponse,
    Point,
    Polygon,
    Pump,
    PumpAssignment,
    PumpPlan,
    RadarDomain,
    ReachabilityResponse,
    ReliabilityBin,
    ReplayBundleSummary,
    ReplayClock,
    ReplaySeekRequest,
    ReplaySpeedRequest,
    ReportAck,
    ReportIn,
    RoadCondition,
    RoadSegment,
    RouteReason,
    RouteRequest,
    RouteResponse,
    RouteResult,
    RunList,
    RunMeta,
    RunSummary,
    SafeUntilEntry,
    SegmentDelta,
    SegmentForecastRow,
    SegmentSeries,
    SeriesPoint,
    SkillByLead,
    StormCellSpec,
    StormDesign,
    SurfaceUnit,
    TidalOutfall,
    VarunaModel,
    VerificationSummary,
    WhatIfRequest,
    WhatIfResponse,
    build_run_id,
)

T0 = datetime(2019, 7, 2, 17, 40, tzinfo=IST)
"""The demo cycle: 2 July 2019, 17:40 IST."""

RUN_ID = build_run_id("mumbai", T0, "1.0", "1.0", "0.3", "baked")
BUNDLE_ID = "MUM-2019-07-02"
AOI = BBox(min_lon=72.815, min_lat=18.995, max_lon=72.905, max_lat=19.135)
HINDMATA = (72.841, 19.012)
KINGS_CIRCLE = (72.857, 19.027)
SION_CIRCLE = (72.862, 19.039)
KEM = (72.8419, 19.0035)
SION_HOSPITAL = (72.8628, 19.0176)
BMC_URL = "https://portal.mcgm.gov.in/irj/portal/anonymous/qlddmgmt"


def t(minutes: int) -> datetime:
    """``T0`` plus ``minutes``."""
    return T0 + timedelta(minutes=minutes)


def _square(lon: float, lat: float, half: float = 0.002) -> list[tuple[float, float]]:
    return [
        (lon - half, lat - half),
        (lon + half, lat - half),
        (lon + half, lat + half),
        (lon - half, lat + half),
        (lon - half, lat - half),
    ]


def _series(peak_cm: float = 55.0) -> list[SeriesPoint]:
    points: list[SeriesPoint] = []
    for lead in range(-60, 181, 15):
        frac = max(0.0, 1.0 - abs(lead - 40) / 140.0)
        p50 = round(peak_cm * frac, 1)
        points.append(
            SeriesPoint(
                valid_ts=t(lead),
                lead_min=lead,
                p10_cm=round(p50 * 0.6, 1),
                p50_cm=p50,
                p90_cm=round(p50 * 1.4, 1),
                observed=lead <= 0,
            )
        )
    return points


# ----------------------------------------------------------------------------- builders
def _engine_versions() -> EngineVersions:
    return EngineVersions(sky="1.0", twin="1.0", flash="0.3", pulse="1.0", products="1.0")


def _grid() -> GridSpec:
    return GridSpec(dx_m=30, nx=316, ny=517, crs=32643, bounds=AOI)


def _run_meta() -> RunMeta:
    return RunMeta(
        run_id=RUN_ID,
        city="mumbai",
        cycle_ts=T0,
        radar_frame_ts=t(-10),
        versions=_engine_versions(),
        mode="baked",
        replay_mode="replay",
        ensemble_n=50,
        stage_ms={
            "decode": 120,
            "sky": 3900,
            "twin": 7100,
            "flash": 210,
            "pulse": 900,
            "products": 800,
        },
        mass_balance_err=0.0004,
        bundle=BUNDLE_ID,
        created_at=T0,
        grid=_grid(),
        notes=["Reconstructed replay", "Inferred drain graph"],
    )


def _segment_row() -> SegmentForecastRow:
    return SegmentForecastRow(
        run_id=RUN_ID,
        segment_id="88213",
        valid_ts=t(40),
        depth_p10_cm=28.0,
        depth_p50_cm=55.0,
        depth_p90_cm=78.0,
        p_gt_15=0.98,
        p_gt_30=0.9,
        p_gt_45=0.82,
        p_gt_60=0.41,
        safe_until={
            "two_wheeler": t(5),
            "car": t(15),
            "bus": t(30),
            "ambulance": t(10),
            "pedestrian": None,
        },
        velocity_p50_ms=0.3,
    )


def _node_row() -> NodeForecastRow:
    return NodeForecastRow(
        run_id=RUN_ID,
        node_id="mh-hindmata-03",
        valid_ts=t(40),
        head_p50_m=6.8,
        p_surcharge=0.86,
        q_surcharge_p50_m3s=0.42,
        responsible_edges=["e-1021", "e-1022", "e-0987"],
        reversed_flow=False,
    )


def _safe_until_entry() -> SafeUntilEntry:
    return SafeUntilEntry(
        profile="ambulance", threshold_cm=30, safe_until=t(10), risk_tolerance=0.2
    )


def _segment_series() -> SegmentSeries:
    return SegmentSeries(
        run_id=RUN_ID,
        valid_ts=T0,
        segment_id="88213",
        name="Hindmata junction",
        points=_series(),
        safe_until=[_safe_until_entry()],
    )


def _observation_effect() -> ObservationEffect:
    return ObservationEffect(
        beta_changes={"e-1021": 0.12, "e-1022": 0.08},
        segments_changed=3,
        hotspot_depth_deltas_cm={"hindmata": -7.0},
        assimilated_run_id=RUN_ID,
        innovation_cm=12.0,
    )


def _observation() -> Observation:
    return Observation(
        id="obs-000041",
        ts=t(-5),
        type="traffic",
        lon=HINDMATA[0],
        lat=HINDMATA[1],
        value_cm=25.0,
        sd_cm=8.0,
        quality="high",
        source="synthetic traffic feed",
        synthetic=True,
        segment_id="88213",
        text="Speed collapsed to 4 km/h on Dr Ambedkar Road during rain",
        effect=_observation_effect(),
    )


def _report_in() -> ReportIn:
    return ReportIn(
        lat=KINGS_CIRCLE[1],
        lon=KINGS_CIRCLE[0],
        depth_hint="knee",
        text="Water up to the knee outside Maheshwari Udyan",
        ts=t(20),
        reporter_id="device-7f3a",
    )


def _report_ack() -> ReportAck:
    return ReportAck(
        id="obs-000042",
        received_ts=t(20),
        status="queued",
        message="Thanks. Your report is queued for the 18:05 cycle.",
        queued_for_run=None,
    )


def _alert_state_change() -> AlertStateChange:
    return AlertStateChange(
        ts=t(7), state="acknowledged", user="ward-officer-fn", note="Pumps requested"
    )


def _alert() -> Alert:
    return Alert(
        id="VARUNA-MUM-20190702T1745-FN-0031",
        run_id=RUN_ID,
        scope="segment",
        scope_id="88213",
        hotspot_id="hindmata",
        level="severe",
        threshold_cm=45,
        headline="Hindmata junction: depth likely above 45 cm from 18:20 to 20:00",
        instruction="Avoid Hindmata and Parel TT; use Dr Ambedkar Road via Bharatmata. Pumps P-12, P-15 dispatched.",
        area_desc="Ward F/North, Hindmata",
        polygon=Polygon(coordinates=[_square(*HINDMATA)]),
        trigger_p=0.82,
        window_from=t(40),
        window_to=t(140),
        raised_ts=t(5),
        persists_cycles=2,
        state="acknowledged",
        channels=["dashboard", "cap", "whatsapp"],
        acknowledged_by="ward-officer-fn",
        acknowledged_ts=t(7),
        cap_status="Exercise",
        cap_path="alerts/VARUNA-MUM-20190702T1745-FN-0031.cap.xml",
        pumps=["P-12", "P-15"],
        history=[_alert_state_change()],
    )


def _pump() -> Pump:
    return Pump(
        id="P-12",
        name="Pump P-12",
        capacity_m3h=600,
        depot="Parel depot",
        lon=72.838,
        lat=19.007,
        status="available",
        synthetic=True,
        eta_min=25,
    )


def _pump_assignment() -> PumpAssignment:
    return PumpAssignment(
        run_id=RUN_ID,
        pump_id="P-12",
        hotspot_id="hindmata",
        hotspot_name="Hindmata junction",
        eta_min=25,
        arrive_ts=t(25),
        expected_benefit_min=40,
        order_text="Move P-12 from Parel depot to Hindmata now; ETA 25 min; prevents about 40 min above 45 cm",
    )


def _pump_benefit() -> HotspotPumpBenefit:
    return HotspotPumpBenefit(
        hotspot_id="hindmata",
        hotspot_name="Hindmata junction",
        minutes_above_45_before=95,
        minutes_above_45_after=55,
        excess_volume_m3=4200,
        assigned_pumps=["P-12", "P-15"],
    )


def _pump_plan() -> PumpPlan:
    return PumpPlan(
        run_id=RUN_ID,
        valid_ts=T0,
        solver="greedy",
        solve_ms=180,
        assignments=[_pump_assignment()],
        benefits=[_pump_benefit()],
    )


def _exposure() -> HotspotExposure:
    return HotspotExposure(
        weight=1.8,
        traffic_volume_proxy=0.9,
        nearest_hospital="KEM Hospital, Parel",
        nearest_hospital_m=950,
        nearest_station="Dadar station",
        nearest_station_m=700,
        transit_lines=["BEST 66", "Central line"],
        population_300m=18000,
        facilities=["hospital", "station", "transit"],
    )


def _attribution() -> AttributionItem:
    return AttributionItem(
        rank=1, edge_id="e-1021", street="Dr Ambedkar Road", beta=0.82, depth_explained_cm=9.0
    )


def _clean_top_n() -> CleanTopNEffect:
    return CleanTopNEffect(
        n=14,
        depth_before_cm=55,
        depth_after_cm=20,
        minutes_impassable_before=95,
        minutes_impassable_after=25,
        edge_ids=[f"e-10{i:02d}" for i in range(14)],
    )


def _hotspot() -> Hotspot:
    return Hotspot(
        rank=1,
        id="hindmata",
        name="Hindmata junction",
        lon=HINDMATA[0],
        lat=HINDMATA[1],
        segment_ids=["88213", "88214"],
        selected_ts=t(40),
        depth_p50_cm=55,
        peak_depth_cm=55,
        time_to_peak=t(40),
        p_impassable=0.9,
        minutes_impassable=95,
        expected_impact=1.62,
        exposure=_exposure(),
        attribution=[_attribution()],
        clean_top_n_effect=_clean_top_n(),
        sparkline=_series(),
        source_url=BMC_URL,
        surcharging_nodes=["mh-hindmata-03"],
    )


def _hotspot_list() -> HotspotList:
    return HotspotList(run_id=RUN_ID, valid_ts=T0, hotspots=[_hotspot()])


def _drain_node() -> DrainNode:
    return DrainNode(
        id="mh-hindmata-03",
        lon=HINDMATA[0],
        lat=HINDMATA[1],
        type="manhole",
        z_ground_m=6.2,
        z_invert_m=4.7,
        storage_area_m2=1.2,
        inlet_type="kerb",
        inlet_len_m=1.0,
        inlet_area_m2=0.12,
    )


def _drain_edge() -> DrainEdge:
    return DrainEdge(
        id="e-1021",
        from_node="mh-hindmata-03",
        to_node="mh-hindmata-04",
        street="Dr Ambedkar Road",
        length_m=42,
        shape="circular",
        diameter_mm=900,
        slope=0.004,
        q_full_m3s=0.95,
        beta_prior_mean=0.35,
        beta_prior_sd=0.15,
        last_desilted=date(2019, 5, 20),
    )


def _boundary() -> Boundary:
    return Boundary(id="b-worli", node_id="of-worli", type="tide", series_source="illustrative")


def _drain_health() -> DrainEdgeHealth:
    return DrainEdgeHealth(
        edge_id="e-1021",
        street="Dr Ambedkar Road",
        beta_mean=0.82,
        beta_sd=0.07,
        beta_prior_mean=0.35,
        kappa=0.3,
        capacity_reduction_pct=82,
        last_update=t(-5),
        explains=["hindmata"],
        observations_n=6,
        diameter_mm=900,
        q_full_m3s=0.95,
    )


def _drain_health_product() -> DrainHealthProduct:
    return DrainHealthProduct(run_id=RUN_ID, valid_ts=T0, edges=[_drain_health()])


def _road_segment() -> RoadSegment:
    return RoadSegment(
        id="88213",
        osm_way_id="4489120",
        name="Dr Ambedkar Road",
        road_class="primary",
        highway="primary",
        lanes=3,
        oneway=False,
        length_m=182.0,
        z_min_m=5.9,
        z_mean_m=6.4,
        ward_id="F/North",
        exposure_weight=1.8,
        speed_kmh=40,
        underpass=False,
        hotspot_id="hindmata",
        geometry=LineString(coordinates=[(72.8402, 19.0113), (72.8418, 19.0126)]),
    )


def _surface_unit() -> SurfaceUnit:
    return SurfaceUnit(
        id="su-01822",
        inlet_node_id="in-hindmata-11",
        segment_id="88213",
        area_m2=9800,
        imperviousness=0.86,
        cn=95,
        n_manning=0.018,
        depression_depth_m=0.42,
        depression_area_m2=1300,
        cells=[81204, 81205, 81520, 81521],
        method="watershed",
        geometry=Polygon(coordinates=[_square(*HINDMATA, half=0.0006)]),
    )


def _asset() -> Asset:
    return Asset(
        id="kem",
        name="KEM Hospital, Parel",
        kind="hospital",
        lon=KEM[0],
        lat=KEM[1],
        source_url="https://www.openstreetmap.org/",
        synthetic=False,
    )


def _radar_domain() -> RadarDomain:
    return RadarDomain(center_lon=72.86, center_lat=19.065, size_km=60, res_m=500)


def _nest() -> NestSpec:
    return NestSpec(id="hindmata", name="Hindmata junction", lon=HINDMATA[0], lat=HINDMATA[1])


def _tidal_outfall() -> TidalOutfall:
    return TidalOutfall(id="of-worli", name="Worli sea face outfall", lon=72.815, lat=19.02)


def _city_config() -> CityConfig:
    return CityConfig(
        id="mumbai",
        name="Mumbai",
        aoi_id="MUM-CENTRAL",
        bbox=AOI,
        crs=32643,
        nests=[_nest()],
        tidal_outfalls=[_tidal_outfall()],
        design_intensity_mm_h=DesignIntensity(),
        infra_path="assets/mumbai_infra.json",
        radar_domain=_radar_domain(),
    )


def _bundle_source() -> BundleSource:
    return BundleSource(
        name="IMD Santacruz 24-hour rainfall, 2 July 2019",
        url="https://mausam.imd.gov.in/",
        note="Daily total used to calibrate the AOI 3-hour accumulation",
        used_for="AOI 3-hour accumulation target 15:00-21:00 IST",
    )


def _storm_cell_spec() -> StormCellSpec:
    return StormCellSpec(
        id="cell-03",
        birth_min=25.0,
        lifetime_min=55.0,
        start_x_m=274_500.0,
        start_y_m=2_098_400.0,
        u_ms=5.657,
        v_ms=5.657,
        sigma_m=3400.0,
        peak_mm_h=96.0,
    )


def _storm_design() -> StormDesign:
    return StormDesign(
        crs=32643,
        seed=2019,
        background_mm_h=3.4,
        wind_from_deg=225.0,
        wind_speed_ms=8.0,
        intensity_scale=1.18,
        cells=[_storm_cell_spec()],
        notes=["Cell lifetimes are a designer choice; no gauge trace resolves them."],
    )


def _design_storm() -> DesignStorm:
    return DesignStorm(
        intensity_mm_h=50.0,
        intensity_source="services/city/configs/mumbai.yaml design_intensity_mm_h.upgraded",
        duration_min=180,
        step_min=5,
        peak_position_r=0.4,
        shape_b_min=10.0,
        shape_c=0.8,
        total_depth_mm=150.0,
        hyetograph_mm_h=[8.0, 12.0, 240.0, 30.0],
        basis=(
            "Built from the drainage-norm design intensity, not from a fitted "
            "intensity-duration-frequency curve: no published curve for this city was found."
        ),
    )


def _bundle_manifest() -> BundleManifest:
    return BundleManifest(
        id=BUNDLE_ID,
        city="mumbai",
        label="Reconstructed replay",
        t0=datetime(2019, 7, 2, 15, 0, tzinfo=IST),
        t1=datetime(2019, 7, 2, 21, 0, tzinfo=IST),
        cadences={
            "radar": 10,
            "truth": 5,
            "gauges": 15,
            "tide": 15,
            "traffic": 5,
            "reports": 5,
            "cycle": 5,
        },
        radar_domain=_radar_domain(),
        aoi=AOI,
        sources=[_bundle_source()],
        seed=2019,
        synthetic_notes=[
            "Radar frames: storm-designer reconstruction",
            "Traffic speeds: synthetic",
        ],
        event_date=date(2019, 7, 2),
        tide_source="illustrative",
        ground_truth_n=12,
        calibration={"aoi_3h_accumulation_mm": 150.0},
        calibration_basis=(
            "Inferred, not measured: scaled from the 24-hour Santacruz total so that the "
            "window accumulation is consistent with it. No gauge trace covers this window."
        ),
        storm=_storm_design(),
    )


def _ground_truth_pin() -> GroundTruthPin:
    return GroundTruthPin(
        id="gt-hindmata-1852",
        ts=datetime(2019, 7, 2, 18, 52, tzinfo=IST),
        ts_uncertainty_min=15,
        name="Hindmata junction",
        lon=HINDMATA[0],
        lat=HINDMATA[1],
        kind="log",
        text="BMC log: waterlogging, traffic diverted",
        source_url=BMC_URL,
    )


def _replay_clock() -> ReplayClock:
    return ReplayClock(
        bundle_id=BUNDLE_ID,
        sim_time=T0,
        playing=True,
        speed=30,
        t0=datetime(2019, 7, 2, 15, 0, tzinfo=IST),
        t1=datetime(2019, 7, 2, 21, 0, tzinfo=IST),
        cycle_index=32,
        n_cycles=73,
        mode="baked",
        last_run_id=RUN_ID,
        next_cycle_ts=t(5),
    )


def _replay_bundle_summary() -> ReplayBundleSummary:
    return ReplayBundleSummary(
        id=BUNDLE_ID,
        city="mumbai",
        label="Reconstructed replay",
        t0=datetime(2019, 7, 2, 15, 0, tzinfo=IST),
        t1=datetime(2019, 7, 2, 21, 0, tzinfo=IST),
        seed=2019,
        baked=True,
        baked_cycles=73,
        total_cycles=73,
        sources_n=4,
        ground_truth_n=12,
        synthetic_notes=["Radar frames: storm-designer reconstruction"],
    )


def _cycle_status() -> CycleStatus:
    return CycleStatus(
        run_id=RUN_ID,
        stage="pulse",
        stage_ms={"decode": 120, "sky": 3900, "twin": 7100, "flash": 210},
        started_at=T0,
        cycle_ts=T0,
        mode="live",
        replay_mode="replay",
        bundle=BUNDLE_ID,
    )


def _cycle_stage_event() -> CycleStageEvent:
    return CycleStageEvent(
        run_id=RUN_ID, cycle_ts=T0, stage="sky", status="finished", ms=3900, budget_ms=5000
    )


def _cycle_log_entry() -> CycleLogEntry:
    return CycleLogEntry(
        cycle_ts=T0,
        run_id=RUN_ID,
        mode="baked",
        stage_ms={"publish": 90},
        mass_balance_err=0.0004,
        published_ts=T0,
    )


def _route_request() -> RouteRequest:
    return RouteRequest(
        origin=KEM,
        destination=SION_HOSPITAL,
        depart_at=T0,
        profile="ambulance",
        risk_tolerance=0.2,
        run_id=RUN_ID,
        origin_name="KEM Hospital",
        destination_name="Sion Hospital",
    )


def _avoided() -> AvoidedSegment:
    return AvoidedSegment(
        segment_id="88213",
        name="Hindmata junction",
        reached_ts=t(30),
        threshold_cm=30,
        p_exceed=0.82,
        depth_p50_cm=48,
    )


def _route_result(label: str = "VARUNA") -> RouteResult:
    return RouteResult(
        label=label,  # type: ignore[arg-type]
        geometry=LineString(coordinates=[KEM, (72.848, 19.009), (72.855, 19.014), SION_HOSPITAL]),
        eta_min=21,
        distance_km=6.8,
        max_expected_depth_cm=9,
        safe_until=t(45),
        arrival_ts=t(21),
        segment_ids=["77101", "77102", "77103"],
        max_p_exceed=0.08,
    )


def _corridor() -> Corridor:
    return Corridor(
        id="9f2c41ab",
        label="A",
        route=_route_result(),
        share=0.55,
        assigned=True,
        capacity_score=41.8,
        max_p_exceed=0.08,
    )


def _route_reason() -> RouteReason:
    return RouteReason(
        kind="avoided",
        segment_id="88213",
        name="Dr Ambedkar Road near Hindmata",
        depth_cm=47.0,
        threshold_cm=30.0,
        at=t(30),
        probability=0.82,
    )


def _route_response() -> RouteResponse:
    return RouteResponse(
        run_id=RUN_ID,
        valid_ts=T0,
        profile="ambulance",
        depart_at=T0,
        risk_tolerance=0.2,
        route=_route_result(),
        naive=_route_result("Naive (shortest)"),
        avoided=[_avoided()],
        alternates=[_route_result("Alternate")],
        corridors=[_corridor()],
        reasons=[_route_reason()],
        trip_id="t-9f2c41ab",
        notes=[
            "Probabilities are this run's own, across 20 members; the risk tolerance applied "
            "is 0.20.",
            "Traffic is spread across 3 safe roads so the safe road does not become the next "
            "jam. The share beside each is a policy, not a measured traffic count.",
        ],
        confidence="high",
        confidence_note="high (lead 30 min)",
        explanation="Avoids Hindmata (82 % above 45 cm at 18:10) via Bharatmata",
        compute_ms=140,
    )


def _isochrone() -> Isochrone:
    return Isochrone(
        minutes=15,
        polygon=Polygon(coordinates=[_square(*KEM, half=0.01)]),
        area_km2=3.1,
        reachable_nodes=420,
    )


def _reachability() -> ReachabilityResponse:
    return ReachabilityResponse(
        run_id=RUN_ID,
        valid_ts=T0,
        t=t(60),
        facility_id="kem",
        facility_name="KEM Hospital, Parel",
        facility_kind="hospital",
        lon=KEM[0],
        lat=KEM[1],
        profile="ambulance",
        isochrones=[
            Isochrone(
                minutes=5, polygon=Polygon(coordinates=[_square(*KEM, half=0.004)]), area_km2=0.6
            ),
            Isochrone(
                minutes=10, polygon=Polygon(coordinates=[_square(*KEM, half=0.007)]), area_km2=1.7
            ),
            _isochrone(),
        ],
        dry_area_15_km2=9.4,
    )


def _road_condition() -> RoadCondition:
    return RoadCondition(
        segment_id="88213",
        name="Hindmata junction",
        status="impassable",
        profile="car",
        valid_from=t(40),
        valid_to=t(140),
        depth_p50_cm=55,
        p_exceed=0.9,
        run_id=RUN_ID,
    )


def _whatif_request() -> WhatIfRequest:
    return WhatIfRequest(
        run_id=RUN_ID, rain_scale=1.3, tide_offset_m=0.0, clean_top_n=14, pump_plan=False
    )


def _hotspot_delta() -> HotspotDelta:
    return HotspotDelta(
        hotspot_id="hindmata",
        name="Hindmata junction",
        depth_before_cm=55,
        depth_after_cm=20,
        minutes_impassable_before=95,
        minutes_impassable_after=25,
        peak_ts_before=t(40),
        peak_ts_after=t(35),
    )


def _segment_delta() -> SegmentDelta:
    return SegmentDelta(segment_id="88213", delta_p50_cm=-35.0)


def _whatif_response() -> WhatIfResponse:
    return WhatIfResponse(
        run_id=RUN_ID,
        whatif_id="wi-0007",
        valid_ts=T0,
        request=_whatif_request(),
        cleaned_edges=[f"e-10{i:02d}" for i in range(14)],
        hotspots=[_hotspot_delta()],
        segments_improved=41,
        segments_worse=2,
        segments_unchanged=1210,
        segment_deltas=[_segment_delta()],
        diff_raster_path="whatif/wi-0007/diff.png",
        emulator_ms=280,
    )


def _physics_check_request() -> PhysicsCheckRequest:
    return PhysicsCheckRequest(whatif_id="wi-0007")


def _physics_check_hotspot() -> PhysicsCheckHotspot:
    return PhysicsCheckHotspot(
        hotspot_id="sion-circle", name="Sion Circle", emulator_cm=32, twin_cm=36
    )


def _physics_check_response() -> PhysicsCheckResponse:
    return PhysicsCheckResponse(
        run_id=RUN_ID,
        whatif_id="wi-0007",
        valid_ts=T0,
        hotspots=[
            PhysicsCheckHotspot(
                hotspot_id="hindmata", name="Hindmata junction", emulator_cm=20, twin_cm=22
            ),
            _physics_check_hotspot(),
        ],
        twin_ms=6400,
        mass_balance_err=0.0006,
    )


def _contingency() -> ContingencyTable:
    return ContingencyTable(threshold_cm=30, hits=8, misses=2, false_alarms=1, correct_negatives=25)


def _hotspot_score() -> HotspotScore:
    return HotspotScore(
        hotspot_id="hindmata",
        name="Hindmata junction",
        contingency=_contingency(),
        timing_err_min=-12,
        mae_cm=9,
        lead_time_gained_min=67,
        n_pins=3,
    )


def _skill_by_lead() -> SkillByLead:
    return SkillByLead(lead_min=60, threshold_mm_h=20, csi=0.61, pod=0.75, far=0.22, n=14400)


def _reliability_bin() -> ReliabilityBin:
    return ReliabilityBin(p_lo=0.6, p_hi=0.8, forecast_p_mean=0.71, observed_freq=0.66, n=38)


def _missed_pin() -> MissedPin:
    return MissedPin(
        pin_id="gt-milan-1910",
        name="Milan subway",
        ts=datetime(2019, 7, 2, 19, 10, tzinfo=IST),
        observed_depth_cm=None,
        forecast_p50_cm=12,
        forecast_p_gt_30=0.2,
        reason="Subway is outside the inferred drain catchment; DEM shows no sink",
        source_url=BMC_URL,
    )


def _flash_lite_score() -> FlashLiteScore:
    return FlashLiteScore(
        rmse_cm=4.2, csi_30=0.87, n_train_runs=200, n_heldout_runs=40, fitted_at=T0
    )


def _verification_summary() -> VerificationSummary:
    return VerificationSummary(
        event=BUNDLE_ID,
        computed_at=T0,
        run_ids=[RUN_ID],
        csi=0.71,
        pod=0.8,
        far=0.11,
        mae_cm=9.0,
        bias_cm=-2.0,
        timing_err_min=-12,
        brier=0.14,
        roc_auc=0.86,
        latency_s=52,
        routing_value=0.9,
        lead_time_gained_min=67,
        n_ground_truth=12,
        n_pins_with_depth=5,
        contingency=_contingency(),
        per_hotspot=[_hotspot_score()],
        skill_by_lead=[_skill_by_lead()],
        reliability=[_reliability_bin()],
        missed=[_missed_pin()],
        flash_lite=_flash_lite_score(),
        limitations=["Open 30 m DEM: pattern and timing, not absolute depth"],
    )


def _error_detail() -> ErrorDetail:
    return ErrorDetail(
        code="run_not_found",
        message="Run is not baked yet. Press Play on the replay, or Compute live.",
        run_id=RUN_ID,
    )


def _error_envelope() -> ErrorEnvelope:
    return ErrorEnvelope(error=_error_detail())


def _live_event() -> LiveEvent:
    return LiveEvent(
        topic="runs.published",
        ts=T0,
        payload={"run_id": RUN_ID, "mode": "baked"},
        run_id=RUN_ID,
        seq=412,
    )


def _health() -> HealthStatus:
    return HealthStatus(
        status="ok",
        version="0.1.0",
        mode="replay",
        city="mumbai",
        bundle=BUNDLE_ID,
        last_run_id=RUN_ID,
        last_run_ts=T0,
        last_run_mode="baked",
        replay_mode="replay",
        replay=_replay_clock(),
        uptime_s=812.5,
    )


def _run_summary() -> RunSummary:
    return RunSummary(
        run_id=RUN_ID,
        city="mumbai",
        cycle_ts=T0,
        mode="baked",
        replay_mode="replay",
        bundle=BUNDLE_ID,
        created_at=T0,
        total_ms=13030,
        mass_balance_err=0.0004,
        ensemble_n=50,
    )


def _run_list() -> RunList:
    return RunList(runs=[_run_summary()], count=1, latest_run_id=RUN_ID)


def _feature() -> Feature:
    return Feature(
        id="88213", geometry=Point(coordinates=HINDMATA), properties={"name": "Hindmata junction"}
    )


_BUILDERS: dict[str, Callable[[], VarunaModel]] = {
    "BBox": lambda: AOI,
    "Point": lambda: Point(coordinates=HINDMATA),
    "LineString": lambda: LineString(coordinates=[KEM, SION_HOSPITAL]),
    "MultiLineString": lambda: MultiLineString(
        coordinates=[[KEM, HINDMATA], [HINDMATA, SION_HOSPITAL]]
    ),
    "Polygon": lambda: Polygon(coordinates=[_square(*HINDMATA)]),
    "MultiPolygon": lambda: MultiPolygon(
        coordinates=[[_square(*HINDMATA)], [_square(*SION_CIRCLE)]]
    ),
    "Feature": _feature,
    "FeatureCollection": lambda: FeatureCollection(features=[_feature()], bbox=AOI.as_tuple()),
    "EngineVersions": _engine_versions,
    "GridSpec": _grid,
    "RunMeta": _run_meta,
    "SegmentForecastRow": _segment_row,
    "NodeForecastRow": _node_row,
    "SeriesPoint": lambda: _series()[6],
    "SafeUntilEntry": _safe_until_entry,
    "SegmentSeries": _segment_series,
    "ObservationEffect": _observation_effect,
    "Observation": _observation,
    "ReportIn": _report_in,
    "ReportAck": _report_ack,
    "AlertStateChange": _alert_state_change,
    "Alert": _alert,
    "Pump": _pump,
    "PumpAssignment": _pump_assignment,
    "HotspotPumpBenefit": _pump_benefit,
    "PumpPlan": _pump_plan,
    "HotspotExposure": _exposure,
    "AttributionItem": _attribution,
    "CleanTopNEffect": _clean_top_n,
    "Hotspot": _hotspot,
    "HotspotList": _hotspot_list,
    "DrainNode": _drain_node,
    "DrainEdge": _drain_edge,
    "Boundary": _boundary,
    "DrainEdgeHealth": _drain_health,
    "DrainHealthProduct": _drain_health_product,
    "RoadSegment": _road_segment,
    "SurfaceUnit": _surface_unit,
    "Asset": _asset,
    "RadarDomain": _radar_domain,
    "NestSpec": _nest,
    "DesignIntensity": DesignIntensity,
    "TidalOutfall": _tidal_outfall,
    "CityConfig": _city_config,
    "BundleSource": _bundle_source,
    "StormCellSpec": _storm_cell_spec,
    "StormDesign": _storm_design,
    "DesignStorm": _design_storm,
    "BundleManifest": _bundle_manifest,
    "GroundTruthPin": _ground_truth_pin,
    "ReplayClock": _replay_clock,
    "ReplaySeekRequest": lambda: ReplaySeekRequest(sim_time=t(60)),
    "ReplaySpeedRequest": lambda: ReplaySpeedRequest(speed=30),
    "ReplayBundleSummary": _replay_bundle_summary,
    "CycleStatus": _cycle_status,
    "CycleStageEvent": _cycle_stage_event,
    "CycleLogEntry": _cycle_log_entry,
    "ComputeRequest": lambda: ComputeRequest(bundle=BUNDLE_ID, cycle_ts=T0),
    "RouteRequest": _route_request,
    "AvoidedSegment": _avoided,
    "RouteResult": _route_result,
    "Corridor": _corridor,
    "RouteReason": _route_reason,
    "RouteResponse": _route_response,
    "Isochrone": _isochrone,
    "ReachabilityResponse": _reachability,
    "RoadCondition": _road_condition,
    "WhatIfRequest": _whatif_request,
    "HotspotDelta": _hotspot_delta,
    "SegmentDelta": _segment_delta,
    "WhatIfResponse": _whatif_response,
    "PhysicsCheckRequest": _physics_check_request,
    "PhysicsCheckHotspot": _physics_check_hotspot,
    "PhysicsCheckResponse": _physics_check_response,
    "ContingencyTable": _contingency,
    "HotspotScore": _hotspot_score,
    "SkillByLead": _skill_by_lead,
    "ReliabilityBin": _reliability_bin,
    "MissedPin": _missed_pin,
    "FlashLiteScore": _flash_lite_score,
    "VerificationSummary": _verification_summary,
    "ErrorDetail": _error_detail,
    "ErrorEnvelope": _error_envelope,
    "LiveEvent": _live_event,
    "HealthStatus": _health,
    "RunSummary": _run_summary,
    "RunList": _run_list,
}


def sample(name: str) -> VarunaModel:
    """A fresh sample instance of the model called ``name`` (a key of ``MODEL_REGISTRY``)."""
    try:
        builder = _BUILDERS[name]
    except KeyError as exc:
        msg = f"No sample for model {name!r}; add one to varuna_schemas.samples"
        raise KeyError(msg) from exc
    instance = builder()
    expected = MODEL_REGISTRY[name]
    if not isinstance(instance, expected):
        msg = f"sample for {name!r} is a {type(instance).__name__}"
        raise TypeError(msg)
    return instance


def all_samples() -> dict[str, VarunaModel]:
    """One sample per registered model, in registry order."""
    return {name: sample(name) for name in MODEL_REGISTRY}


def sample_json(name: str) -> dict[str, Any]:
    """The sample as a JSON-compatible dict (what the API would return)."""
    return sample(name).model_dump(mode="json")


__all__ = ["AOI", "BUNDLE_ID", "RUN_ID", "T0", "all_samples", "sample", "sample_json", "t"]

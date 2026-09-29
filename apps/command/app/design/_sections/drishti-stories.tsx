"use client";

import { lazy, Suspense, useState } from "react";

import { Button } from "@/components/ui/button";
import { PlaceGauge } from "@/components/pumps/place-gauges";
import { BundleCard, type BundleSummary } from "@/components/varuna/bundle-card";
import { CapViewer } from "@/components/varuna/cap-viewer";
import { CitizenReportsCard } from "@/components/varuna/citizen-reports-card";
import { CycleLog, CycleLogState, type CycleLogRow } from "@/components/varuna/cycle-log";
import { CyclePicker } from "@/components/varuna/cycle-picker";
import { DeliveryLog, type DeliveryLogRow } from "@/components/varuna/delivery-log";
import { DispatchOrder, type DispatchOrderPlan } from "@/components/varuna/dispatch-order";
import { EscalationMatrix } from "@/components/varuna/escalation-matrix";
import { LayerPanel, type LayerKey, type LayerToggles } from "@/components/varuna/layer-panel";
import { LimitationsList } from "@/components/varuna/limitations-list";
import { LiveNowCard } from "@/components/varuna/live-now-card";
import { LiveOutlookCard, LiveOutlookView } from "@/components/varuna/live-outlook-card";
import { MapAttribution } from "@/components/varuna/map-attribution";
import { MapSlot } from "@/components/varuna/map-slot";
import { MinutesFlow } from "@/components/varuna/minutes-flow";
import { OnboardFinishCard, type FinishFacts } from "@/components/varuna/onboard-finish-card";
import { Panel } from "@/components/varuna/panel";
import { PhysicsCheckPanel, TwinRunProgress } from "@/components/varuna/physics-check-result";
import { ProbabilityLegend } from "@/components/varuna/probability-legend";
import { PumpBoard, type PumpColumn } from "@/components/varuna/pump-board";
import { PumpCard, type Pump } from "@/components/varuna/pump-card";
import { RailAlerts, RailPumps } from "@/components/varuna/rail-mirrors";
import { ReachabilityPanel } from "@/components/varuna/reachability-panel";
import { RightRail } from "@/components/varuna/right-rail";
import { RouteCompare, type RouteSummary } from "@/components/varuna/route-compare";
import { RouteForm, type RoutePlace, type RouteRequest } from "@/components/varuna/route-form";
import { SegmentPopover } from "@/components/varuna/segment-popover";
import { Skeleton } from "@/components/varuna/skeleton";
import { Sparkline } from "@/components/varuna/sparkline";
import { TopBar } from "@/components/varuna/top-bar";
import { WhatIfDetails } from "@/components/varuna/whatif-details";
import { ServedVerificationChip } from "@/components/varuna/verification-chip";
import {
  VerificationThresholdChart,
  type ThresholdPoint,
} from "@/components/varuna/verification-threshold-chart";
import type { SegmentPick } from "@/components/map/city-map";
import type { Hotspot, HotspotSet } from "@/lib/api/hotspots";
import type { ReportPin } from "@/lib/api/reports";
import { OutlookSchema, type Outlook, type OutlookState } from "@/lib/api/outlook";
import { outlookBody } from "@/lib/api/outlook.fixture";
import type { PhysicsCheckResult } from "@/lib/api/whatif";
import { parsePumpMap, type RawPumpMap } from "@/lib/api/pumps";
import { navItem } from "@/lib/nav";

import { Demo } from "./section";
import pumpMapBody from "./samples/pump-map-0840.json";

/*
 * Drishti (the console) and the screens it hands over to: Marga, Kalpana, Smriti, Pravesh,
 * Pramana, Sanket and Jalayantra's board. Every sample is about the 2 July 2019 replay and is read
 * off a baked run, a committed manifest or the component tests that pin these components.
 */

/** The 08:40 IST cycle the stories below were read from. */
const RUN_ID = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";
const CYCLE_TS = "2019-07-02T08:40:00+05:30";

/** The 36 valid times of that cycle, 5 min apart from +5 min. */
const VALID_TS: string[] = Array.from({ length: 36 }, (_, i) => {
  const minutes = 8 * 60 + 45 + i * 5;
  const hh = String(Math.floor(minutes / 60)).padStart(2, "0");
  const mm = String(minutes % 60).padStart(2, "0");
  return `2019-07-02T${hh}:${mm}:00+05:30`;
});

/**
 * Hindmata's street depth in cm over the 36 steps of the 08:40 cycle, as
 * `GET /v1/nowcast/hotspots?run_id=MUM-20190702T0310Z-...` answered on 2026-09-28: it peaks at
 * 7.7 cm at 11:40, so on this cycle the junction never closes to a car.
 */
const HINDMATA_DEPTH = [
  0, 0.2, 0.5, 0.8, 1.1, 1.4, 1.8, 2.2, 2.5, 2.8, 3.2, 3.5, 3.7, 4.1, 4.3, 4.5, 4.7, 5, 5.2, 5.4,
  5.6, 5.8, 6, 6.2, 6.4, 6.5, 6.6, 6.7, 6.9, 7, 7.1, 7.2, 7.4, 7.5, 7.6, 7.7,
];

/** One of Hindmata's segments, trimmed from the component test's pick. */
const PICK: SegmentPick = {
  segment: {
    id: "S100841069-000",
    name: "Dr Babasaheb Ambedkar Road",
    path: [
      [72.8421, 19.0101],
      [72.8425, 19.0106],
    ],
    depthCm: HINDMATA_DEPTH,
    width: 4,
  },
  x: 16,
  y: 16,
};

const HINDMATA: Hotspot = {
  rank: 3,
  id: "MUM-HS-01",
  name: "Hindmata junction (Hindmata Cinema, Dr B. Ambedkar Marg)",
  slug: "hindmata-junction-hindmata-cinema-dr-b-ambedkar-",
  lon: 72.8421396,
  lat: 19.010099,
  ward: "F/S",
  isSink: false,
  sourceUrl: null,
  sourced: true,
  peakDepthCm: 7.7,
  peakTs: "2019-07-02T11:40:00+05:30",
  timeToPeakMin: 175,
  depthCm: HINDMATA_DEPTH,
  pImpassableAtPeak: 0,
  impassableFromTs: null,
  minutesImpassable: 0,
  expectedImpact: 0,
  exposure: { weight: 0.783, facilities: ["shelter"] },
  segmentIds: ["S100841069-000", "S100841079-000", "S102172139-001"],
  attribution: [],
  attributionLabel:
    "Refused: the best single pipe moves Hindmata by 0.015 to 0.027 cm; its inlets, not its pipes, limit it (ADR-0071).",
};

const HOTSPOT_SET: HotspotSet = {
  runId: RUN_ID,
  ranking: "peak depth",
  impassableThresholdCm: 30,
  hotspots: [HINDMATA],
};

const LAYERS: LayerToggles = {
  satellite: true,
  probability: false,
  raster: true,
  segments: true,
  surcharge: true,
  drains: false,
  buildings: false,
  hotspots: true,
  isochrones: false,
  routes: false,
  threeD: false,
  xray: false,
  reports: true,
};

/** Two reports as the console's card draws them: one a citizen sent, one from the demo seed. */
const REPORT_PINS: ReportPin[] = [
  {
    id: "R-demo-1",
    lon: 72.8412,
    lat: 19.0125,
    depthHint: "knee",
    depthCm: 45,
    status: "received",
    statusLabel: "Received",
    statusSeeded: false,
    ts: "2019-07-02T08:47:00+05:30",
    text: "Water above the knee under the flyover",
    place: "Hindmata junction",
    thumbUrl: null,
    photoUrl: null,
    credit: null,
    synthetic: true,
    origin: "citizen",
  },
  {
    id: "R-demo-2",
    lon: 72.8575,
    lat: 19.027,
    depthHint: "ankle",
    depthCm: 10,
    status: "crew_sent",
    statusLabel: "Crew sent (demo status)",
    statusSeeded: true,
    ts: "2019-07-02T08:20:00+05:30",
    text: null,
    place: "King's Circle",
    thumbUrl: null,
    photoUrl: null,
    credit: null,
    synthetic: true,
    origin: "seed",
  },
];

/** Two bakes of the 08:10 cycle, from the cycle-log test: same time, a factor of three apart. */
const CYCLE_ROWS: CycleLogRow[] = [
  {
    id: "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.0-baked",
    time: "2019-07-02T08:10:00+05:30",
    stages: "decode, sky, twin, pulse, products",
    ms: 64_800,
    massBalance: 0.00091,
  },
  {
    id: "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.1-baked",
    time: "2019-07-02T08:10:00+05:30",
    stages: "decode, sky, twin, pulse, products",
    ms: 147_200,
    massBalance: 0.0026,
  },
];

const BUNDLES: BundleSummary[] = [
  {
    id: "MUM-2019-07-02",
    kind: "Reconstructed replay",
    city: "Mumbai",
    window: "05:40 to 09:40 IST, 2 Jul 2019",
    note: "Storm designer calibrated to public gauge totals; seed 2019.",
    available: true,
    t0: "2019-07-02T05:40:00+05:30",
    t1: "2019-07-02T09:40:00+05:30",
    simTime: "2019-07-02T06:40:00+05:30",
  },
  {
    id: "CHN-IDF-25yr",
    kind: "Design storm",
    city: "Chennai",
    window: "3 h, 25-year return period",
    note: "Chicago hyetograph; the first, uncalibrated forecast for Chennai.",
    available: false,
    t0: "2026-07-01T06:00:00+05:30",
    t1: "2026-07-01T09:00:00+05:30",
    simTime: "2026-07-01T06:00:00+05:30",
  },
];

/** Chennai's first forecast, as the onboarding finish card test pins it (R8). */
const FINISH: FinishFacts = {
  runId: "CHN-20260701T0040Z-sky1.0-twin1.0-flash0.0-baked",
  wetStreets: 15_472,
  streetsTotal: 18_622,
  wetThresholdCm: 5,
  medianPeakCm: 26,
  forecastMs: 46_512.3,
  stagesMs: 46_437,
  stages: ["Sky", "Twin", "Pulse", "products"],
  storm: { id: "CHN-IDF-25yr", totalMm: 150, durationMin: 180, peakMmH: 448.8, source: "manifest" },
};

/** KEM to Sion on the 2 July cycles, from the route-compare test. */
const NAIVE: RouteSummary = { etaMin: 5.6, distanceM: 3100 };
const VARUNA_ROUTE: RouteSummary = {
  etaMin: 6.1,
  distanceM: 3480,
  maxDepthCm: 12,
  avoided: [
    { segmentId: "S618477973-001", name: "Dr Babasaheb Ambedkar Marg", probability: 0.82 },
    { segmentId: "S100841079-000", name: "off Dr Ambedkar Road", probability: 0.64 },
  ],
};

const PLACES: RoutePlace[] = [
  { id: "kem", name: "KEM Hospital", group: "Hospitals" },
  { id: "sion", name: "LTMG Sion Hospital", group: "Hospitals" },
  { id: "hindmata", name: "Hindmata junction", group: "Hotspots" },
];

const PHYSICS: PhysicsCheckResult = {
  runId: RUN_ID,
  runsOnTwin: false,
  twinJob: null,
  summary: "Emulator vs physics: max difference 5.07 cm at Sion Circle",
  toleranceCm: 5,
  agrees: false,
  maxDiffCm: 5.07,
  maxDiffHotspot: "Sion Circle",
  hotspots: [
    {
      hotspotId: "MUM-HS-06",
      name: "Sion Circle",
      emulatorDeltaCm: 1.2,
      twinDeltaCm: 6.27,
      diffCm: 5.07,
    },
  ],
  outside: ["Sion Circle"],
  window: { sizeM: 990, nodes: 631, edges: 620, centre: "Sion Circle", cleanedEdgesInside: 0 },
  leversNotChecked: ["pump_plan"],
  cleanedEdges: 0,
  massBalance: { baseline: 0, scenario: 0, budget: 1e-3 },
  ms: 3200,
  budgetMs: 10_000,
  notes: [],
};

const CAP_XML = `<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>VARUNA-MUM-20190702T0310Z-SKY1.0-TWIN1.0-FLASH0.1-BAKED-STREET-0977-SEVERE</identifier>
  <sender>varuna@sih2026.example</sender>
  <sent>2019-07-02T08:40:00+05:30</sent>
  <status>Exercise</status>
  <msgType>Alert</msgType>
  <scope>Public</scope>
  <info>
    <category>Met</category>
    <event>Street flooding</event>
    <urgency>Expected</urgency>
    <severity>Severe</severity>
    <certainty>Likely</certainty>
    <onset>2019-07-02T09:30:00+05:30</onset>
    <expires>2019-07-02T11:40:00+05:30</expires>
    <headline>Sant Shitolebaba Maharaj Marg, near Sakinaka: depth above 45 cm from 09:30 until at least 11:40</headline>
    <description>VARUNA nowcast run MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked.</description>
    <instruction>Avoid Sant Shitolebaba Maharaj Marg. Peak forecast 76 cm. Route emergency vehicles around it; see the reachability tab for the affected catchment.</instruction>
    <area>
      <areaDesc>Sant Shitolebaba Maharaj Marg</areaDesc>
      <circle>19.0957649,72.89574214999999 0.5</circle>
    </area>
  </info>
</alert>`;

const DELIVERY: DeliveryLogRow[] = [
  {
    id: "severe-dashboard",
    label: "Dashboard",
    kind: "mock",
    status: "Shown on the alert queue",
    ts: CYCLE_TS,
  },
  {
    id: "severe-whatsapp_mock",
    label: "WhatsApp mock",
    kind: "mock",
    status: "Shown on the on-screen phone",
    ts: CYCLE_TS,
  },
  {
    id: "severe-sms_mock",
    label: "SMS mock",
    kind: "mock",
    status: "Rendered, not sent",
    ts: CYCLE_TS,
  },
];

/** The 08:40 dispatch map, whose legs name each pump's depot, place and minutes. */
const PUMP_MAP = parsePumpMap(pumpMapBody as unknown as RawPumpMap);
const FIRST_LEGS = PUMP_MAP.legs.slice(0, 2);

const ORDER: DispatchOrderPlan = {
  runId: PUMP_MAP.runId,
  moves: FIRST_LEGS.map((l) => ({
    id: l.pumpId,
    pumpId: l.pumpId,
    from: l.depot.name,
    to: l.target.name,
    etaMinutes: Math.round(l.etaMin),
    minutesAvoided: l.minutesSaved,
  })),
};

const POOL: Pump[] = PUMP_MAP.legs.slice(2, 4).map((l) => ({
  id: l.pumpId,
  capacityM3PerHour: l.capacityM3PerHour,
  depot: l.depot.name,
  status: "available",
}));

const COLUMNS: PumpColumn[] = FIRST_LEGS.map((l) => ({
  id: l.target.id,
  title: l.target.name,
  pumps: [
    {
      id: l.pumpId,
      capacityM3PerHour: l.capacityM3PerHour,
      depot: l.depot.name,
      status: "moving",
      etaMinutes: Math.round(l.etaMin),
      assignedTo: l.target.id,
    },
  ],
  minutesAbove45: { before: l.minutesBefore, after: l.minutesAfter },
}));

/**
 * CSI, POD and FAR at 5, 15 and 30 cm over the event's sourced pins, from the committed
 * `public/verification.json` (17 of 29 pins inside the replay window).
 */
const THRESHOLD_POINTS: ThresholdPoint[] = [
  { thresholdCm: 5, csi: 0.177, pod: 1, far: 0.823 },
  { thresholdCm: 15, csi: 0.103, pod: 0.176, far: 0.8 },
  { thresholdCm: 30, csi: 0.053, pod: 0.059, far: 0.667 },
];

const OUTLOOK: OutlookState = {
  kind: "ready",
  outlook: OutlookSchema.parse(outlookBody()) as Outlook,
};
const OUTLOOK_FETCHED_MS = Date.parse("2026-09-26T14:12:00+05:30");

const IGNORE = () => undefined;

/** The explorer carries the 214 kB OpenAPI snapshot, so it loads only when opened. */
const ApiExplorerStory = lazy(() => import("./api-explorer-story"));

export default function DrishtiStories() {
  const [layers, setLayers] = useState<LayerToggles>(LAYERS);
  const [threshold, setThreshold] = useState(30);
  const [route, setRoute] = useState<RouteRequest>({
    originId: "kem",
    destinationId: "sion",
    departAt: CYCLE_TS,
    profile: "ambulance",
    riskTolerance: 0.2,
  });
  const [popoverOpen, setPopoverOpen] = useState(true);
  const [picked, setPicked] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [explorer, setExplorer] = useState(false);
  const console_ = navItem("console");
  const route_ = navItem("route");

  return (
    <div className="flex flex-col gap-6">
      <Panel
        title={`${console_.label}: map chrome`}
        description="The top bar, the layer panel, the probability legend, the map frame and its attribution, and the segment popover on one of Hindmata's streets at 08:40."
      >
        <div className="flex flex-col gap-6">
          <Demo
            label="Top bar"
            note="Reads the run registry and the verification score from the API, so it shows what the API can answer now."
            bare
          >
            <div className="rounded-panel border-line overflow-hidden border">
              <TopBar />
            </div>
          </Demo>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
            <Demo label="Layer panel" bare>
              <LayerPanel
                value={layers}
                onChange={(key: LayerKey, next: boolean) =>
                  setLayers((current) => ({ ...current, [key]: next }))
                }
              />
            </Demo>
            <Demo
              label="Citizen reports"
              note="Newest first; a report sent from the public map lands here within one 30 s poll."
              bare
            >
              <div className="flex flex-col gap-3">
                <CitizenReportsCard reports={REPORT_PINS} loaded />
                <CitizenReportsCard reports={[]} loaded />
                <CitizenReportsCard reports={[]} loaded={false} />
              </div>
            </Demo>
            <Demo
              label="Right now"
              note="Live rain from GET /v1/weather and sea level from Open-Meteo's marine model; neither changes the replayed forecast."
              bare
            >
              <div className="flex flex-col gap-3">
                <LiveNowCard
                  city="mumbai"
                  cityLabel="Mumbai"
                  weather={{
                    kind: "unavailable",
                    reason:
                      "The live weather source could not be reached. The flood forecast does not depend on it.",
                  }}
                  tide={{
                    kind: "ready",
                    tide: {
                      point: { lon: 72.8, lat: 18.95, label: "off Colaba" },
                      now: { ts: "2026-09-30T02:00:00+05:30", m: 0.84 },
                      nextHigh: { ts: "2026-09-30T04:00:00+05:30", m: 1.31 },
                      nextLow: { ts: "2026-09-30T10:00:00+05:30", m: -1.22 },
                      rising: true,
                      series: [],
                    },
                  }}
                />
                <LiveNowCard
                  city="mumbai"
                  cityLabel="Mumbai"
                  weather={{ kind: "loading" }}
                  tide={{ kind: "loading" }}
                />
              </div>
            </Demo>
            <Demo label="Probability legend" note="Opacity is P(above the threshold).">
              <ProbabilityLegend thresholdCm={threshold} onThresholdChange={setThreshold} />
            </Demo>
            <Demo
              label="Probability legend, one member"
              note="Every street is 0 % or 100 %, and the legend says so."
            >
              <ProbabilityLegend
                thresholdCm={threshold}
                onThresholdChange={setThreshold}
                deterministic
              />
            </Demo>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Demo
              label="Map frame, empty"
              note="The frame every map sits in, with the empty state a screen passes when no run is baked. Static: no WebGL is mounted."
              bare
            >
              <div className="rounded-panel border-line relative h-64 overflow-hidden border">
                <MapSlot
                  emptyState={{
                    title: "No runs yet",
                    description: "Press Play on the replay, or Compute live.",
                  }}
                />
              </div>
            </Demo>
            <Demo
              label="Segment popover"
              note="Depth now, the peak and safe-until per vehicle."
              bare
            >
              <div className="rounded-panel border-line bg-ink relative h-[26rem] overflow-hidden border">
                {popoverOpen ? (
                  <SegmentPopover
                    pick={PICK}
                    step={20}
                    validTs={VALID_TS}
                    hotspot={HINDMATA}
                    onClose={() => setPopoverOpen(false)}
                  />
                ) : (
                  <div className="flex h-full items-center justify-center">
                    <Button size="sm" variant="outline" onClick={() => setPopoverOpen(true)}>
                      Open the popover again
                    </Button>
                  </div>
                )}
              </div>
            </Demo>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Demo label="Map attribution">
              <MapAttribution />
            </Demo>
            <Demo label="Sparkline" note="Hindmata's p50 over three hours, coloured by depth.">
              <Sparkline
                values={HINDMATA_DEPTH}
                markerIndex={20}
                colorByDepth
                label="Hindmata junction, depth over three hours"
              />
            </Demo>
            <Demo label="Minutes rolling" note="Motion M4; reduced motion prints the number.">
              <span className="type-h3 num text-text">
                <MinutesFlow value={95} /> <span className="type-small text-text-3">min</span>
              </span>
            </Demo>
          </div>
        </div>
      </Panel>

      <Panel
        title={`${console_.label}: right rail`}
        description="The rail's tabs and the mirrors of Sanket and Jalayantra. The mirrors and reachability read the API for the 08:40 run, so they show what it answers now."
      >
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Demo label="Right rail, Hindmata ranked" bare>
            <div className="rounded-panel border-line h-[30rem] overflow-hidden border">
              <RightRail
                hotspots={HOTSPOT_SET}
                step={20}
                selectedHotspotId={picked}
                onSelectHotspot={(h) => setPicked(h.id)}
                simTime={CYCLE_TS}
              />
            </div>
          </Demo>
          <div className="flex flex-col gap-4">
            <Demo label="Alerts mirror" bare>
              <RailAlerts runId={RUN_ID} />
            </Demo>
            <Demo label="Pumps mirror" bare>
              <RailPumps runId={RUN_ID} />
            </Demo>
          </div>
          <Demo
            label="Reachability"
            note="The 15-minute catchment of each facility against its dry baseline at 08:40."
            bare
          >
            <ReachabilityPanel at={CYCLE_TS} />
          </Demo>
          <Demo label="Cycle picker" note="The baked cycles in the run registry." bare>
            <CyclePicker currentRunId={RUN_ID} />
          </Demo>
        </div>
      </Panel>

      <Panel
        title={`${route_.label}: KEM Hospital to Sion Hospital`}
        description="The route form and the naive-against-VARUNA comparison. On the 2 July runs the corridor stays under an ambulance's 60 cm, so the comparison is shown as it is."
      >
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Demo
            label="Route form"
            note="Find route has no handler here, so it says what is missing."
          >
            <RouteForm value={route} onChange={setRoute} places={PLACES} />
          </Demo>
          <div className="flex flex-col gap-4">
            <Demo label="Comparison">
              <RouteCompare naive={NAIVE} varuna={VARUNA_ROUTE} />
            </Demo>
            <Demo label="Comparison, before a route is asked for">
              <RouteCompare />
            </Demo>
          </div>
        </div>
      </Panel>

      <Panel
        title="Kalpana: the physics check"
        description="Rain +30 % on the 08:40 cycle read 5.07 cm against a 5 cm tolerance, outside, and the panel says so rather than hiding it (ADR-0077)."
      >
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Demo
            label="Outside tolerance"
            note="5.07 cm against 5 cm is the measured figure of ADR-0077; the junction it is drawn at and the two changes beside it are illustrative."
          >
            <PhysicsCheckPanel result={PHYSICS} running={false} error={null} />
          </Demo>
          <div className="flex flex-col gap-4">
            <Demo label="Running">
              <PhysicsCheckPanel result={null} running error={null} />
            </Demo>
            <Demo label="Error">
              <PhysicsCheckPanel
                result={null}
                running={false}
                error="The physics check did not answer: the API is unreachable."
              />
            </Demo>
            <Demo
              label="Twin run progress"
              note="Motion M31: the bar fills as each output step arrives."
            >
              <TwinRunProgress
                line="Twin step 12 of 36 written"
                fraction={12 / 36}
                running
                error={null}
                cancelled={false}
                onCancel={IGNORE}
              />
            </Demo>
          </div>
        </div>
      </Panel>

      <Panel
        title="Smriti and Pravesh"
        description="Bundle cards and the cycle log on the replay screen; Chennai's finish card on the onboarding screen (R8), uncalibrated and saying so."
      >
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {BUNDLES.map((bundle, i) => (
              <Demo
                key={bundle.id}
                label={bundle.available ? "Bundle, built and selected" : "Bundle, not built"}
                bare
              >
                <BundleCard bundle={bundle} selected={i === 0} onSelect={IGNORE} />
              </Demo>
            ))}
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Demo label="Cycle log" note="Two bakes of the 08:10 cycle, a factor of three apart.">
              <CycleLog rows={CYCLE_ROWS} />
            </Demo>
            <Demo label="Cycle log, empty">
              <CycleLog rows={[]} />
            </Demo>
            <Demo label="Cycle log, loading">
              <CycleLogState
                bundleId="MUM-2019-07-02"
                log={{ rows: [], isPending: true, isError: false, error: null, refetch: IGNORE }}
              />
            </Demo>
            <Demo label="Cycle log, failed">
              <CycleLogState
                bundleId="MUM-2019-07-02"
                log={{
                  rows: [],
                  isPending: false,
                  isError: true,
                  error: {
                    message: "The run registry did not answer. Start the API with make dev.",
                  },
                  refetch: IGNORE,
                }}
              />
            </Demo>
            <Demo label="What-if details" note="Kalpana keeps the method one click away.">
              <WhatIfDetails>
                <p>Flash-lite, a reduced-order emulator calibrated to VARUNA-Twin.</p>
                <p>Rain scale multiplies every member of the 08:40 cycle&apos;s rain.</p>
              </WhatIfDetails>
            </Demo>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Demo label="Finish card" bare>
              <OnboardFinishCard cityName="Chennai" facts={FINISH} href="/console?city=chennai" />
            </Demo>
            <Demo label="Finish card, before the first forecast" bare>
              <OnboardFinishCard
                cityName="Chennai"
                facts={null}
                href={null}
                note="The first forecast is still running."
              />
            </Demo>
          </div>
        </div>
      </Panel>

      <Panel
        title="Pramana: the flood scores"
        description="CSI, POD and FAR across thresholds at the sourced pins, the served verification chip, and the limitations list the landing page links to."
      >
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Demo label="Scores by threshold">
              <VerificationThresholdChart
                points={THRESHOLD_POINTS}
                groundTruthCount={17}
                height={200}
              />
            </Demo>
            <Demo label="Scores, loading">
              <VerificationThresholdChart
                points={[]}
                groundTruthCount={null}
                loading
                height={200}
              />
            </Demo>
            <Demo label="Scores, error">
              <VerificationThresholdChart
                points={[]}
                groundTruthCount={null}
                error="The API is unreachable."
                height={200}
              />
            </Demo>
          </div>
          <Demo label="Served verification chip" note="Reads the event's score from the API.">
            <ServedVerificationChip event="MUM-2019-07-02" />
          </Demo>
          <Demo label="Limitations list">
            <LimitationsList id="design-limitations" />
          </Demo>
        </div>
      </Panel>

      <Panel
        title="Sanket and Jalayantra: documents and orders"
        description="The CAP 1.2 document of the 08:40 severe alert, its delivery log, the escalation matrix, and the pump board with the dispatch order. Synthetic pump inventory; replay alerts are Exercise."
      >
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Demo label="CAP viewer" bare>
              <CapViewer xml={CAP_XML} filename="VARUNA-STREET-0977-SEVERE.cap.xml" />
            </Demo>
            <div className="flex flex-col gap-4">
              <Demo label="CAP viewer, loading" bare>
                <CapViewer xml={null} loading compact />
              </Demo>
              <Demo label="Delivery log" note="Mocks only: no sender is configured.">
                <DeliveryLog rows={DELIVERY} />
              </Demo>
              <Demo label="Delivery log, error">
                <DeliveryLog
                  rows={null}
                  error="The delivery log did not load: the API is unreachable."
                />
              </Demo>
            </div>
          </div>
          <Demo
            label="Escalation matrix"
            note="Ward officer to control room to police, transit and public."
          >
            <EscalationMatrix />
          </Demo>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            {FIRST_LEGS.map((l) => (
              <Demo key={l.pumpId} label={`Place gauge, ${l.target.name}`} bare>
                <PlaceGauge leg={l} scaleCm={120} thresholdCm={PUMP_MAP.thresholdCm} arrived />
              </Demo>
            ))}
            {POOL[0] ? (
              <Demo label="Pump card, available" bare>
                <PumpCard pump={POOL[0]} />
              </Demo>
            ) : null}
          </div>
          <Demo label="Dispatch order">
            <DispatchOrder order={ORDER} />
          </Demo>
          <Demo label="Dispatch order, no plan">
            <DispatchOrder order={null} />
          </Demo>
          <Demo
            label="Pump board"
            note="Two pumps sent, two in the pool; drag a card to a column or use its menu."
            bare
          >
            <PumpBoard pumps={POOL} columns={COLUMNS} planApplied onOptimise={IGNORE} />
          </Demo>
        </div>
      </Panel>

      <Panel
        title="Live outlook"
        description="Today's three hours from live weather, beside the replay and never mixed into it. The view is shown from the outlook fixture; the card reads the API when asked."
      >
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Demo label="Outlook" bare>
              <LiveOutlookView state={OUTLOOK} nowMs={OUTLOOK_FETCHED_MS} />
            </Demo>
            <Demo label="Outlook, loading" bare>
              <LiveOutlookView state={{ kind: "loading" }} nowMs={OUTLOOK_FETCHED_MS} />
            </Demo>
            <Demo label="Outlook, unavailable" bare>
              <LiveOutlookView
                state={{
                  kind: "unavailable",
                  reason: "Live weather is unavailable: the API could not reach Open-Meteo.",
                }}
                nowMs={OUTLOOK_FETCHED_MS}
                onRetry={IGNORE}
              />
            </Demo>
          </div>
          <Demo label="Outlook card" note="Reads /v1/outlook when drawn." bare>
            {live ? (
              <LiveOutlookCard city="mumbai" />
            ) : (
              <div>
                <Button size="sm" variant="outline" onClick={() => setLive(true)}>
                  Read the live outlook
                </Button>
              </div>
            )}
          </Demo>
        </div>
      </Panel>

      <Panel
        title="API explorer"
        description="Three paths of the committed OpenAPI snapshot, sent to the local API when pressed. The explorer never sends the desk passphrase, and anything that changes state is shown and copied, never sent."
      >
        {explorer ? (
          <Suspense fallback={<Skeleton className="h-64 w-full" />}>
            <ApiExplorerStory />
          </Suspense>
        ) : (
          <div>
            <Button size="sm" variant="outline" onClick={() => setExplorer(true)}>
              Open the explorer
            </Button>
          </div>
        )}
      </Panel>
    </div>
  );
}

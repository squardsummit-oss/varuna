"use client";

import { Waves } from "lucide-react";
import { lazy, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { AgreementBar } from "@/components/varuna/agreement-bar";
import { AlertCard, type AlertSummary } from "@/components/varuna/alert-card";
import { ALERT_LEVELS, AlertLevelChip } from "@/components/varuna/alert-level-chip";
import { CitySwitcher } from "@/components/varuna/city-switcher";
import { CycleBudgetBar, type StageTiming } from "@/components/varuna/cycle-budget-bar";
import { DeltaTable, type DeltaRow } from "@/components/varuna/delta-table";
import { DepthChip } from "@/components/varuna/depth-chip";
import { EmptyState } from "@/components/varuna/empty-state";
import { FanChart, type FanChartPoint } from "@/components/varuna/fan-chart";
import { HotspotDrawer } from "@/components/varuna/hotspot-drawer";
import { HotspotRail } from "@/components/varuna/hotspot-rail";
import type { Hotspot } from "@/lib/api/hotspots";
import { IconRail } from "@/components/varuna/icon-rail";
import { Kbd } from "@/components/varuna/kbd";
import { LogStream, type LogLine } from "@/components/varuna/log-stream";
import { ModeBanner } from "@/components/varuna/mode-banner";
import {
  IDLE_ONBOARDING_STEPS,
  OnboardingStepDetails,
  OnboardingSteps,
  type OnboardingStepState,
} from "@/components/varuna/onboarding-steps";
import { PageHeader } from "@/components/varuna/page-header";
import { Panel } from "@/components/varuna/panel";
import { PanelErrorBoundary } from "@/components/varuna/panel-error-boundary";
import { PhoneMock, type PhoneMessage } from "@/components/varuna/phone-mock";
import { RADAR_FRAMES_MEMBER, RadarPreview } from "@/components/varuna/radar-preview";
import { ReplayPanel } from "@/components/varuna/replay-panel";
import { RunStamp } from "@/components/varuna/run-stamp";
import { Skeleton, SkeletonRows } from "@/components/varuna/skeleton";
import { SkyPanel } from "@/components/varuna/sky-panel";
import { StormDesigner, type StormCell } from "@/components/varuna/storm-designer";
import { TimeBar } from "@/components/varuna/time-bar";
import { HEADLINE_SCORE_TILES, VerificationGrid } from "@/components/varuna/verification-grid";
import { VerificationChip } from "@/components/varuna/verification-chip";
import {
  formatRainScale,
  formatTideOffset,
  WhatIfControls,
  type WhatIfValues,
} from "@/components/varuna/whatif-controls";
import { addMinutesIso } from "@/lib/format";
import { useMotionPref } from "@/lib/motion";
import { navItem } from "@/lib/nav";
import { useUiStore } from "@/lib/stores/ui";
import { useRunStore, type RunMeta } from "@/lib/stores/run";

import { BrandStories } from "./brand-stories";
import { DeferredStory } from "./deferred-story";
import { Demo, DesignSection } from "./section";

/*
 * The screen-level groups load when they near the viewport (DeferredStory): they carry charts,
 * maps and API reads, so /design pays for them only when someone scrolls to them. The coverage
 * test (`__tests__/story-coverage.test.ts`) reads these files, so a component exported from
 * components/varuna, pumps, citizen or authority without a story here fails the suite.
 */
const DrishtiStories = lazy(() => import("./drishti-stories"));
const NadiStories = lazy(() => import("./nadi-stories"));
const SanketStories = lazy(() => import("./sanket-stories"));
const JalayantraStories = lazy(() => import("./jalayantra-stories"));
const PramanaStories = lazy(() => import("./pramana-stories"));
const PublicStories = lazy(() => import("./public-stories"));
const AuthorityStories = lazy(() => import("./authority-stories"));

/* Sample data. Every string is about the 2 July 2019 Mumbai replay, per SPEC.md section 6.8. */

/** What `/v1/nowcast/hotspots` puts on every row: no run computes attribution (ADR-0042). */
const NO_ATTRIBUTION =
  "Not computed on this run: Flash-lite is element-wise per segment — ADR-0042.";

/**
 * The 06:40 IST baked cycle as `GET /v1/runs/MUM-20190702T0110Z-...` answered on 2026-09-28: a real
 * run, so a component that reads the run store and asks the API about it gets an answer, and the
 * stage times are the ones that run recorded (the Twin at 60 s is the budget it misses, P4.6).
 */
const SAMPLE_RUN: RunMeta = {
  run_id: "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked",
  city: "mumbai",
  cycle_ts: "2019-07-02T06:40:00+05:30",
  mode: "replay",
  replay_mode: "baked",
  ensemble_n: 50,
  bundle: "MUM-2019-07-02",
  stage_ms: { sky: 3151, twin: 59979, flash: 2370, pulse: 1941, products: 3106 },
  mass_balance_err: 7.690743785692783e-15,
  versions: { sky: "1.0", twin: "1.0", flash: "0.1" },
};

/** A plausible rise-and-recede shape, so the story panel's sparklines are not flat lines. */
function sampleSeries(peakCm: number, peakStep: number): number[] {
  return Array.from(
    { length: 36 },
    (_, i) => Math.round(peakCm * Math.exp(-(((i - peakStep) / 7) ** 2)) * 10) / 10,
  );
}

const SAMPLE_HOTSPOTS: Hotspot[] = [
  {
    rank: 1,
    id: "hindmata",
    name: "Hindmata junction",
    slug: "hindmata",
    lon: 72.841,
    lat: 19.012,
    ward: "F/S",
    isSink: true,
    sourceUrl: "https://www.openstreetmap.org/node/1646774128",
    sourced: true,
    peakDepthCm: 55,
    peakTs: "2019-07-02T08:20:00+05:30",
    timeToPeakMin: 100,
    depthCm: sampleSeries(55, 20),
    pImpassableAtPeak: 1,
    impassableFromTs: "2019-07-02T07:45:00+05:30",
    minutesImpassable: 95,
    expectedImpact: 0.86,
    exposure: { weight: 0.86, facilities: ["hospital", "station"] },
    segmentIds: [],
    attribution: [],
    attributionLabel: NO_ATTRIBUTION,
  },
  {
    rank: 2,
    id: "kings-circle",
    name: "King's Circle",
    slug: "kings-circle",
    lon: 72.857,
    lat: 19.027,
    ward: "F/N",
    isSink: true,
    sourceUrl: null,
    sourced: true,
    peakDepthCm: 38,
    peakTs: "2019-07-02T08:35:00+05:30",
    timeToPeakMin: 115,
    depthCm: sampleSeries(38, 23),
    pImpassableAtPeak: 1,
    impassableFromTs: "2019-07-02T08:05:00+05:30",
    minutesImpassable: 55,
    expectedImpact: 0.71,
    exposure: { weight: 0.71, facilities: ["station"] },
    segmentIds: [],
    attribution: [],
    attributionLabel: NO_ATTRIBUTION,
  },
  {
    rank: 3,
    id: "sion-circle",
    name: "Sion Circle",
    slug: "sion-circle",
    lon: 72.862,
    lat: 19.039,
    ward: "F/N",
    isSink: false,
    sourceUrl: null,
    sourced: true,
    peakDepthCm: 27,
    peakTs: "2019-07-02T08:50:00+05:30",
    timeToPeakMin: 130,
    depthCm: sampleSeries(27, 26),
    pImpassableAtPeak: 0,
    impassableFromTs: null,
    minutesImpassable: 0,
    expectedImpact: 0,
    exposure: { weight: 0.64, facilities: ["hospital"] },
    segmentIds: [],
    attribution: [],
    attributionLabel: NO_ATTRIBUTION,
  },
];

const SAMPLE_ALERT: AlertSummary = {
  id: "MUM-2019-07-02-hindmata-severe",
  level: "severe",
  headline: "Hindmata junction: depth likely above 45 cm from 08:20 to 10:00",
  area: "Ward F/South · Dr Ambedkar Road",
  triggerProbability: 0.82,
  raisedAt: "2019-07-02T17:45:00+05:30",
  persistsCycles: 2,
  channels: ["Dashboard", "WhatsApp mock"],
};

const SAMPLE_PHONE_MESSAGES: PhoneMessage[] = [
  {
    id: "hindmata-severe",
    time: "17:45",
    text: "Hindmata junction: depth likely above 45 cm from 08:20 to 10:00. Divert traffic at Tilak Bridge. Pumps P-12 and P-15 dispatched.",
  },
];

/** The same run's stage times: no decode stage (the replay radar is already a cube). */
const SAMPLE_STAGES: StageTiming[] = [
  { id: "sky", ms: 3151 },
  { id: "twin", ms: 59979 },
  { id: "flash", ms: 2370 },
  { id: "pulse", ms: 1941 },
  { id: "products", ms: 3106 },
];

const SAMPLE_DELTAS: DeltaRow[] = [
  {
    id: "hindmata",
    hotspot: "Hindmata junction",
    beforeCm: 55,
    afterCm: 20,
    minutesImpassableBefore: 95,
    minutesImpassableAfter: 20,
  },
  {
    id: "kings-circle",
    hotspot: "King's Circle",
    beforeCm: 38,
    afterCm: 31,
    minutesImpassableBefore: 60,
    minutesImpassableAfter: 45,
  },
];

/** Valid time of each of the 36 steps from the sample run's cycle, for the drawer's clock times. */
const SAMPLE_VALID_TS: string[] = Array.from(
  { length: 36 },
  (_, i) => addMinutesIso(SAMPLE_RUN.cycle_ts, i * 5) ?? SAMPLE_RUN.cycle_ts,
);

/** Step 20 is +100 min, 08:20 IST: Hindmata's sample peak, so the drawer opens on it. */
const SAMPLE_DRAWER_STEP = 20;

/**
 * The first three of Hindmata's road segments on the 08:40 baked cycle, as the register lists
 * them. Only three are named because only three were read off the run; the drawer's own copy
 * counts what it is given and never implies the junction's whole set.
 */
const HINDMATA_SEGMENT_IDS = ["S100841069-000", "S100841079-000", "S102172139-001"];

/**
 * The reasons the what-if lab passes for its two inert switches, repeated here so the disabled
 * state can be reviewed. The source of truth is `app/whatif/whatif-screen.tsx`.
 */
const STORY_CLEAN_DISABLED_REASON =
  "Pipes are ranked per junction, in the hotspot's drawer (ADR-0071). Press “Clean in what-if” " +
  "there to bring its pipes here";
const STORY_PUMP_DISABLED_REASON = "The pump plan is not a what-if lever yet (P7.7)";

const SAMPLE_ONBOARD_STEPS: OnboardingStepState[] = IDLE_ONBOARDING_STEPS.map((step, i) => {
  if (i < 2) return { ...step, progress: 1, elapsedS: 34 + i * 12, status: "done" as const };
  if (i === 2) return { ...step, progress: 0.42, elapsedS: 18, status: "running" as const };
  return step;
});

const SAMPLE_LOG_LINES: LogLine[] = [
  { ts: "2026-09-06T10:02:11+05:30", text: "Mosaicked 4 Copernicus GLO-30 tiles for CHN-SOUTH" },
  {
    ts: "2026-09-06T10:02:48+05:30",
    text: "Burned 41,206 building footprints, carved 3,180 road centrelines",
  },
  {
    ts: "2026-09-06T10:03:05+05:30",
    level: "warn",
    text: "12 depressions smaller than 900 m² breached as spurious pits",
  },
];

const SAMPLE_DEPTHS = [2, 8, 20, 35, 50, 80];

/** The bundle the console opens on, and the one whose radar cube the previews below animate. */
const SAMPLE_BUNDLE_ID = "MUM-2019-07-02";

/** A bundle `make bundle` has written in full; nothing is missing from it. */
const NO_MISSING_MEMBERS: readonly string[] = [];

/**
 * The convective cells of `bundles/MUM-2019-07-02/manifest.json` (seed 2019), read off the
 * committed manifest rather than invented: metres reprojected from EPSG:32643 to WGS84, birth
 * minutes added to the bundle's 05:40 IST start, and sigma converted to kilometres.
 */
const SAMPLE_STORM_CELLS: StormCell[] = [
  {
    id: "cell-01",
    birth: "2019-07-02T08:05:46+05:30",
    lifetimeMin: 80.9,
    startLat: 18.901,
    startLon: 72.74,
    velocityMs: 8,
    sigmaKm: 2.03,
    peakMmH: 70.5,
  },
  {
    id: "cell-02",
    birth: "2019-07-02T06:08:40+05:30",
    lifetimeMin: 57.1,
    startLat: 18.939,
    startLon: 72.751,
    velocityMs: 8,
    sigmaKm: 2.63,
    peakMmH: 77.3,
  },
  {
    id: "cell-03",
    birth: "2019-07-02T07:54:39+05:30",
    lifetimeMin: 69.6,
    startLat: 18.897,
    startLon: 72.739,
    velocityMs: 8,
    sigmaKm: 5.48,
    peakMmH: 71.9,
  },
  {
    id: "cell-04",
    birth: "2019-07-02T08:20:26+05:30",
    lifetimeMin: 76.8,
    startLat: 18.89,
    startLon: 72.683,
    velocityMs: 8,
    sigmaKm: 5.67,
    peakMmH: 59,
  },
  {
    id: "cell-05",
    birth: "2019-07-02T07:55:13+05:30",
    lifetimeMin: 78.4,
    startLat: 18.921,
    startLon: 72.793,
    velocityMs: 8,
    sigmaKm: 5.41,
    peakMmH: 88.2,
  },
  {
    id: "cell-06",
    birth: "2019-07-02T06:23:34+05:30",
    lifetimeMin: 31,
    startLat: 19.003,
    startLon: 72.852,
    velocityMs: 8,
    sigmaKm: 3.84,
    peakMmH: 90.2,
  },
  {
    id: "cell-07",
    birth: "2019-07-02T07:19:02+05:30",
    lifetimeMin: 59.8,
    startLat: 19.068,
    startLon: 72.759,
    velocityMs: 8,
    sigmaKm: 4.86,
    peakMmH: 79.8,
  },
  {
    id: "cell-08",
    birth: "2019-07-02T08:04:05+05:30",
    lifetimeMin: 50.4,
    startLat: 19.024,
    startLon: 72.703,
    velocityMs: 8,
    sigmaKm: 3.55,
    peakMmH: 111.4,
  },
];

/** The cycle the fan-chart sample was computed for. */
const HINDMATA_CYCLE_TS = "2019-07-02T07:40:00+05:30";

/**
 * Rain at Hindmata for that cycle, as
 * `GET /v1/nowcast/rain/series?hotspot=hindmata&compute=true&t=2019-07-02T07:40:00+05:30` answered
 * on bundle MUM-2019-07-02 (seed 2019): lead minutes, then p10, p50 and p90 in mm/h. Read off a
 * real cycle rather than invented, so the fan on this page opens the way the ensemble opens - the
 * median falls away after about an hour while p90 holds near 4 mm/h (SPEC.md rule 6).
 */
const HINDMATA_RAIN: readonly (readonly [number, number, number, number])[] = [
  [5, 3.636, 4.009, 4.299],
  [10, 2.965, 3.955, 4.364],
  [15, 2.47, 3.396, 4.134],
  [20, 2.169, 3.381, 4.564],
  [25, 2.391, 3.845, 5.191],
  [30, 2.146, 3.742, 4.238],
  [35, 2.132, 3.766, 4.25],
  [40, 2.043, 3.442, 4.105],
  [45, 1.344, 3.761, 4.418],
  [50, 0.44, 3.55, 4.642],
  [55, 0, 2.553, 5.348],
  [60, 0, 3.814, 4.238],
  [65, 0, 3.556, 4.311],
  [70, 0, 2.442, 4.127],
  [75, 0, 2.677, 4.736],
  [80, 0, 1.252, 4.108],
  [85, 0, 1.037, 4.105],
  [90, 0, 1.137, 4.101],
  [95, 0, 0.956, 4.078],
  [100, 0, 0.103, 4.065],
  [105, 0, 0, 4.022],
  [110, 0, 0, 4.082],
  [115, 0, 0, 4.042],
  [120, 0, 0, 4.121],
  [125, 0, 0, 4.156],
  [130, 0, 0, 4.273],
  [135, 0, 0, 4.144],
  [140, 0, 0, 4.042],
  [145, 0, 0, 3.298],
  [150, 0, 0, 3.892],
  [155, 0, 0, 3.591],
  [160, 0, 0, 3.937],
  [165, 0, 0, 0.563],
  [170, 0, 0, 3.514],
  [175, 0, 0, 1.853],
  [180, 0, 0, 3.238],
];

const SAMPLE_FAN: FanChartPoint[] = HINDMATA_RAIN.map(([leadMin, p10, p50, p90]) => ({
  leadMin,
  p10,
  p50,
  p90,
  validTs: addMinutesIso(HINDMATA_CYCLE_TS, leadMin) ?? HINDMATA_CYCLE_TS,
}));

/** The API's own answer when nothing is baked; the error state prints it verbatim. */
const NO_RAIN_RUNS_MESSAGE =
  "No run under data/runs carries rain products yet. Run make bake BUNDLE=MUM-2019-07-02, press Play on the replay, or add compute=true to compute this cycle from MUM-2019-07-02 now.";

/** The lead the fan charts mark: confidence decays after about 90 minutes (SPEC.md 7.10). */
const DIVERGENCE_MARKER_MIN = 90;

/** The state before `make bundle` has run: the manifest is on disk, the storm is not. */
const NO_STORM_CELLS: StormCell[] = [];

/**
 * What the reduced-motion demo is actually showing. The preference is read from the browser, so
 * the fallback is checked the way section 10.3 asks for it: turn reduce motion on and reload.
 */
function reducedMotionNote(reduced: boolean): string {
  return reduced
    ? "Reduced motion is on in this browser, so the preview holds its middle frame and the loop is off."
    : "Reduced motion holds the middle frame and never starts the loop. Turn on reduce motion in the operating system and reload to see it here.";
}

const BUTTON_VARIANTS = [
  "default",
  "outline",
  "secondary",
  "ghost",
  "destructive",
  "link",
] as const;
const BUTTON_SIZES = ["xs", "sm", "default", "lg"] as const;

/**
 * Puts a sample run in the run store while this section is mounted so the chrome components have
 * something real to read, and clears it on unmount so no other screen inherits a fake run.
 */
function WithSampleRun({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    useRunStore.getState().setRun(SAMPLE_RUN);
    return () => useRunStore.getState().clear();
  }, []);
  return <>{children}</>;
}

/** A child that throws on demand, to prove the boundary keeps the rest of the screen alive. */
function Exploder() {
  const [boom, setBoom] = useState(false);
  if (boom) throw new Error("Segment forecast failed to parse");
  return (
    <Button size="sm" variant="outline" onClick={() => setBoom(true)}>
      Throw inside this panel
    </Button>
  );
}

export function ComponentsSection() {
  const setCommandPaletteOpen = useUiStore((s) => s.setCommandPaletteOpen);
  const setShortcutsOpen = useUiStore((s) => s.setShortcutsOpen);
  const setSettingsOpen = useUiStore((s) => s.setSettingsOpen);
  const [selectedHotspot, setSelectedHotspot] = useState<string | null>("hindmata");
  // What the wired what-if story was last pressed with. The story calls no endpoint, so it
  // echoes the scenario rather than showing a result it did not compute (rule 6).
  const [storyPress, setStoryPress] = useState<string | null>(null);
  // The drawer's close button must do something here too, or the story ships a dead control.
  const [drawersOpen, setDrawersOpen] = useState(true);
  const echoPress = (action: string) => (values: WhatIfValues) =>
    setStoryPress(
      `${action} pressed with rain ${formatRainScale(values.rainScale)}, tide ${formatTideOffset(values.tideOffsetM)} and ${values.cleanedSegments.length} segment${values.cleanedSegments.length === 1 ? "" : "s"} to clean. The story calls no endpoint.`,
    );
  const { reduced } = useMotionPref();

  return (
    <DesignSection
      id="components"
      title="Components"
      description="Every component of components/varuna in its states, with the sample data the console would show on 2 July 2019. Screens compose these; they never copy them."
    >
      <div className="flex flex-col gap-6">
        <BrandStories />

        <Panel
          title="Page header"
          description="Title, description, honesty label and actions. A screen with a Sanskrit name also carries its English gloss, the Devanagari and what the word means (ADR-0085)."
        >
          <div className="flex flex-col gap-6">
            <Demo label="Sanskrit-named screen" bare>
              <PageHeader
                title={navItem("drains").label}
                screen={navItem("drains")}
                description="The learned blockage map, the observations that taught it, and the desilting priority list."
                honesty="Inferred drain graph"
                actions={
                  <Button size="sm" variant="outline">
                    Export desilting priority
                  </Button>
                }
              />
            </Demo>
            <Demo label="Alert centre" note="Sanket, the screen that raises the alerts." bare>
              <PageHeader
                title={navItem("alerts").label}
                screen={navItem("alerts")}
                description="Street flooding alerts for Mumbai, as CAP 1.2 and as the ward officer's WhatsApp card."
              />
            </Demo>
          </div>
        </Panel>

        <Panel
          title="Screen nav"
          description="Rides in the top bar. From 1280 px each screen is named beside its icon; below that the name, gloss, Devanagari and key appear on hover and focus."
        >
          <div className="rounded-panel border-line bg-deep h-top-bar flex items-center overflow-x-auto border px-3">
            <IconRail />
          </div>
        </Panel>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel title="Panel" description="Default padding, with a title and a description.">
            <p className="type-small text-text-2">
              Panels are 12 px radius, one pixel of line, deep on ink. There are no drop shadows.
            </p>
          </Panel>
          <Panel
            dense
            title="Panel, dense"
            description="Dense panels carry tables and log rows."
            actions={<Kbd>D</Kbd>}
          >
            <p className="type-small text-text-2">
              Dense drops the padding so a 32 px table row still breathes.
            </p>
          </Panel>
        </div>

        <Panel
          title="Empty state"
          description="Empty states say what to do next, never what broke."
        >
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Demo label="Default" bare>
              <EmptyState
                icon={Waves}
                title="No runs yet"
                description="Press Play on the replay, or Compute live."
                action={<Button size="sm">Open the replay panel</Button>}
              />
            </Demo>
            <Demo label="Small" note="Used inside a rail or a drawer." bare>
              <EmptyState
                size="sm"
                title="No observations this cycle"
                description="Pulse assimilates traffic anomalies and citizen reports as they arrive."
              />
            </Demo>
          </div>
        </Panel>

        <Panel
          title="Depth chip"
          description="Colour is fixed to the depth ramp; the number is always printed."
        >
          <div className="flex flex-wrap items-center gap-2">
            {SAMPLE_DEPTHS.map((cm) => (
              <DepthChip key={cm} cm={cm} showBand />
            ))}
            <DepthChip cm={null} />
          </div>
        </Panel>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel title="Keyboard keys" description="Shortcut hints in rows and in the overlay.">
            <div className="flex flex-wrap items-center gap-2">
              <Kbd>Space</Kbd>
              <Kbd>←</Kbd>
              <Kbd>→</Kbd>
              <Kbd>P</Kbd>
              <Kbd>D</Kbd>
              <Kbd>⌘K</Kbd>
              <Kbd>?</Kbd>
            </div>
          </Panel>
          <Panel title="Skeletons" description="Shimmer, never a spinner (build spec section 6.9).">
            <div className="flex flex-col gap-3">
              <Demo label="Single block" bare>
                <Skeleton className="h-6 w-48" />
              </Demo>
              <Demo label="Rail rows" bare>
                <SkeletonRows rows={3} />
              </Demo>
            </div>
          </Panel>
        </div>

        <Panel
          title="Panel error boundary"
          description="A broken panel never blanks the map: it fails in place with a retry."
        >
          <PanelErrorBoundary title="Segment forecast">
            <Exploder />
          </PanelErrorBoundary>
        </Panel>

        <Panel
          title="Mode banner"
          description="One banner per mode; degraded names the missing feed."
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Demo label="Replay" bare>
              <ModeBanner mode="replay" label="Replay 30× · 2 Jul 2019 · 06:40 IST" />
            </Demo>
            <Demo label="Live" bare>
              <ModeBanner mode="live" label="Live · 06:40 IST" />
            </Demo>
            <Demo label="Degraded" bare>
              <ModeBanner
                mode="degraded"
                label="Degraded: radar offline, using gauges and satellite"
              />
            </Demo>
            <Demo label="No run" note="The state a cold console opens in." bare>
              <ModeBanner mode="none" />
            </Demo>
          </div>
        </Panel>

        <Panel
          title="Run stamp, verification chip and city switcher"
          description="The provenance chrome of the top bar, with and without a run."
        >
          <div className="flex flex-col gap-4">
            <Demo label="No run" note="Nothing is baked yet." bare>
              <div className="flex flex-wrap items-center gap-3">
                <RunStamp />
                <VerificationChip />
                <CitySwitcher />
              </div>
            </Demo>
            <Demo label="With the 06:40 baked run" bare>
              <WithSampleRun>
                <div className="flex flex-wrap items-center gap-3">
                  <RunStamp />
                  <VerificationChip csi={0.71} eventLabel="2 Jul 2019" />
                  <CitySwitcher />
                </div>
              </WithSampleRun>
            </Demo>
          </div>
        </Panel>

        {/* Half the width, as the console's right rail is beside the map. */}
        <div className="grid grid-cols-1 gap-4 lg:w-1/2">
          <Panel title="Hotspot rail" description="Ranked rows with a depth chip and time to peak.">
            <div className="flex flex-col gap-4">
              <Demo label="Three hotspots" bare>
                <HotspotRail
                  hotspots={SAMPLE_HOTSPOTS}
                  step={20}
                  selectedId={selectedHotspot}
                  onSelect={(hotspot) => setSelectedHotspot(hotspot.id)}
                />
              </Demo>
              <Demo label="Empty" note="Before the first run is published." bare>
                <HotspotRail hotspots={[]} step={0} />
              </Demo>
            </div>
          </Panel>
        </div>

        <Panel
          title="Time bar"
          description="96 px tall; scrub, play, speed and the ensemble spread band."
        >
          <div className="rounded-panel border-line overflow-hidden border">
            <TimeBar />
          </div>
        </Panel>

        <Panel title="Replay panel" description="Bundle, clock, speed and the cycle log.">
          <ReplayPanel />
        </Panel>

        <Panel
          title="Radar preview"
          description="Motion M25: the bundle's radar frames loop at 4 fps and pause on hover, on focus and while the tab is hidden. The frames are served from the bundle, so a preview only animates once make bundle has written its radar cube."
        >
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <Demo
                label="Animating"
                note="Reconstructed replay, 25 frames every 10 min, with the rain ramp legend."
                bare
              >
                <RadarPreview
                  bundleId={SAMPLE_BUNDLE_ID}
                  built
                  missingMembers={NO_MISSING_MEMBERS}
                />
              </Demo>
              <Demo label="Reduced motion, static" note={reducedMotionNote(reduced)} bare>
                <RadarPreview
                  bundleId={SAMPLE_BUNDLE_ID}
                  built
                  missingMembers={NO_MISSING_MEMBERS}
                />
              </Demo>
              <Demo
                label="Frames missing"
                note="The bundle has no radar cube, so nothing is fetched and the empty state names the make target."
                bare
              >
                <RadarPreview
                  bundleId={SAMPLE_BUNDLE_ID}
                  built
                  missingMembers={[RADAR_FRAMES_MEMBER]}
                />
              </Demo>
            </div>
            <Demo
              label="Compact"
              note="The console replay panel drops the heading, the legend and the pause hint."
              bare
            >
              <div className="max-w-56">
                <RadarPreview
                  compact
                  bundleId={SAMPLE_BUNDLE_ID}
                  built
                  missingMembers={NO_MISSING_MEMBERS}
                />
              </div>
            </Demo>
          </div>
        </Panel>

        <Panel
          title="Storm designer"
          description="The cells a bundle was generated from, beside the preview they produce. Editing is a pilot feature, so Generate bundle is off and says why."
        >
          <div className="flex flex-col gap-6">
            <Demo
              label="The 2 July 2019 storm"
              note="Eight convective cells from the bundle manifest, seed 2019."
              bare
            >
              <StormDesigner
                cells={SAMPLE_STORM_CELLS}
                bundleId={SAMPLE_BUNDLE_ID}
                built
                missingMembers={NO_MISSING_MEMBERS}
              />
            </Demo>
            <Demo label="Reduced motion, static" note={reducedMotionNote(reduced)} bare>
              <StormDesigner
                cells={SAMPLE_STORM_CELLS}
                bundleId={SAMPLE_BUNDLE_ID}
                built
                missingMembers={NO_MISSING_MEMBERS}
              />
            </Demo>
            <Demo
              label="Frames missing"
              note="Before make bundle: the manifest is on disk, the cells and the radar cube are not."
              bare
            >
              <StormDesigner
                cells={NO_STORM_CELLS}
                bundleId={SAMPLE_BUNDLE_ID}
                built={false}
                missingMembers={[RADAR_FRAMES_MEMBER]}
              />
            </Demo>
          </div>
        </Panel>

        <Panel
          title="Fan chart"
          description="The ensemble band at 20 % opacity of the line colour with the median over it, per the uncertainty rule of section 6.2. Nothing animates: the motion catalogue has no row for a chart drawing itself in, so the series are drawn, not played."
        >
          <div className="flex flex-col gap-6">
            <Demo
              label="Rain at Hindmata, the 07:40 cycle"
              note="36 steps to +180 min from one real Sky cycle of MUM-2019-07-02. The band widens as the median falls away, which is the divergence Phase 3 has to show."
              bare
            >
              <FanChart
                points={SAMPLE_FAN}
                quantity="Rain rate"
                unit="mm/h"
                height={240}
                markerLeadMin={DIVERGENCE_MARKER_MIN}
                markerLabel="+90 min"
              />
            </Demo>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <Demo label="Loading" note="Shimmer at the plot's own height, never a spinner." bare>
                <FanChart points={[]} quantity="Rain rate" unit="mm/h" height={140} loading />
              </Demo>
              <Demo
                label="Empty"
                note="Before the first run: no chart, and what to do about it."
                bare
              >
                <FanChart points={[]} quantity="Rain rate" unit="mm/h" height={140} />
              </Demo>
              <Demo label="Error" note="The API's message, verbatim." bare>
                <FanChart
                  points={[]}
                  quantity="Rain rate"
                  unit="mm/h"
                  height={140}
                  error={NO_RAIN_RUNS_MESSAGE}
                />
              </Demo>
            </div>
          </div>
        </Panel>

        <Panel
          title="Rain nowcast panel"
          description="Phase 3 scaffolding for the console (task P3.8): the fan chart at Hindmata, every member's city-mean hyetograph, the run stamp and the honesty labels the run earned. Phase 6 deletes it and keeps the fan chart. It reads the API live, so what it shows here is whatever the API can answer now - with nothing baked, that is its empty state."
        >
          <SkyPanel chartHeight={240} />
        </Panel>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel
            title="Alerts"
            description="Level chips carry the threshold; the card carries hysteresis."
          >
            <div className="flex flex-col gap-4">
              <Demo label="Level chips" bare>
                <div className="flex flex-wrap items-center gap-2">
                  {ALERT_LEVELS.map((level) => (
                    <AlertLevelChip key={level} level={level} showThreshold />
                  ))}
                </div>
              </Demo>
              <Demo label="Severe alert, raised" bare>
                <AlertCard alert={SAMPLE_ALERT} />
              </Demo>
              <Demo label="Acknowledged" bare>
                <AlertCard alert={{ ...SAMPLE_ALERT, acknowledged: true }} />
              </Demo>
            </div>
          </Panel>
          <Panel
            title="Phone mock"
            description="The card the ward officer receives. The real sender arrives with a configured account."
          >
            <PhoneMock messages={SAMPLE_PHONE_MESSAGES} simTime="17:45" />
          </Panel>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel
            title="Cycle budget bar"
            description="Stage timings against the 15 s live-cycle budget."
          >
            <div className="flex flex-col gap-4">
              <Demo label="Complete cycle" bare>
                <CycleBudgetBar stages={SAMPLE_STAGES} totalMs={70547} />
              </Demo>
              <Demo label="No timings yet" bare>
                <CycleBudgetBar />
              </Demo>
            </div>
          </Panel>
          <Panel
            title="Delta table"
            description="What-if results: before and after depth, and minutes impassable."
          >
            <DeltaTable rows={SAMPLE_DELTAS} />
          </Panel>
        </div>

        <Panel
          title="What-if controls"
          description="The lab's control column. A lever the request does not carry is disabled and says why; a button with no handler is disabled and says what is missing (section 17, never a dead control)."
        >
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Demo
              label="Wired, as the lab mounts it"
              note="Run has a handler and the deep link carried three Hindmata segments. Both switches are inert with the lab's own reasons; physics check has no handler."
            >
              <WhatIfControls
                initial={{ rainScale: 1.3, cleanedSegments: HINDMATA_SEGMENT_IDS }}
                cleanedSource="Hindmata junction"
                onRun={echoPress("Run what-if")}
                cleanDisabled
                cleanDisabledReason={STORY_CLEAN_DISABLED_REASON}
                pumpDisabled
                pumpDisabledReason={STORY_PUMP_DISABLED_REASON}
              />
              <p aria-live="polite" className="type-micro text-text-3 mt-3">
                {storyPress ?? "Press Run what-if to see the scenario the button would send."}
              </p>
            </Demo>
            <Demo
              label="No handlers"
              note="The component's defaults: nothing picked, both actions disabled with the default reasons."
            >
              <WhatIfControls />
            </Demo>
          </div>
        </Panel>

        <Panel
          title="Agreement bar"
          description="Emulator against physics on the same scenario. The bar fills against twice the tolerance, so agreement sits left of the tick; the difference is always printed. No physics check is wired yet (P7.8), so the two results below are illustrations of the states, not measurements."
        >
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <Demo label="Not run" note="Before a physics check: what to do, not an empty bar.">
              <AgreementBar result={null} />
            </Demo>
            <Demo
              label="Within tolerance, illustrative"
              note="Section 7.7's own example: 4 cm at Sion Circle against a 5 cm tolerance."
            >
              <AgreementBar result={{ maxDiffCm: 4, atHotspot: "Sion Circle" }} />
            </Demo>
            <Demo
              label="Outside tolerance, illustrative"
              note="Past the tick the fill turns to the degraded colour and the label says so."
            >
              <AgreementBar result={{ maxDiffCm: 7, atHotspot: "Hindmata junction" }} />
            </Demo>
          </div>
        </Panel>

        <Panel
          title="Hotspot drawer"
          description="Opened at 08:20 (+100 min) on the sample Hindmata row: the big depth number, the flat fan chart of a one-member run, safe-until per vehicle, exposure, and the attribution empty state (ADR-0042)."
        >
          {drawersOpen ? (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <Demo
                label="No road segments recorded"
                note="The sample row carries none, so there is no deep link to offer and the footer says so."
                bare
              >
                <div className="rounded-panel border-line h-[720px] overflow-hidden border">
                  <HotspotDrawer
                    hotspot={SAMPLE_HOTSPOTS[0] ?? null}
                    step={SAMPLE_DRAWER_STEP}
                    validTs={SAMPLE_VALID_TS}
                    onClose={() => setDrawersOpen(false)}
                  />
                </div>
              </Demo>
              <Demo
                label="With road segments"
                note="Three segment ids, so the footer offers Clean in what-if and counts what the link carries."
                bare
              >
                <div className="rounded-panel border-line h-[720px] overflow-hidden border">
                  {SAMPLE_HOTSPOTS[0] ? (
                    <HotspotDrawer
                      hotspot={{ ...SAMPLE_HOTSPOTS[0], segmentIds: HINDMATA_SEGMENT_IDS }}
                      step={SAMPLE_DRAWER_STEP}
                      validTs={SAMPLE_VALID_TS}
                      onClose={() => setDrawersOpen(false)}
                    />
                  ) : null}
                </div>
              </Demo>
            </div>
          ) : (
            <EmptyState
              title="Drawers closed"
              description="Close hides both sample drawers, as it hides the drawer on the console. Open them again to review the states."
              action={
                <Button size="sm" variant="outline" onClick={() => setDrawersOpen(true)}>
                  Open the drawers
                </Button>
              }
            />
          )}
        </Panel>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel
            title="Onboarding steps"
            description="Chennai from cache: six steps with elapsed time."
          >
            <div className="flex flex-col gap-4">
              <OnboardingSteps steps={SAMPLE_ONBOARD_STEPS} />
              <OnboardingStepDetails steps={SAMPLE_ONBOARD_STEPS} />
            </div>
          </Panel>
          <Panel title="Log stream" description="Real pipeline lines only; mono is allowed here.">
            <div className="flex flex-col gap-4">
              <Demo label="Streaming" bare>
                <LogStream lines={SAMPLE_LOG_LINES} />
              </Demo>
              <Demo label="Empty" bare>
                <LogStream lines={[]} />
              </Demo>
            </div>
          </Panel>
        </div>

        <Panel
          title="Verification grid"
          description="Headline scores; a tile with no value says so rather than inventing one."
        >
          <VerificationGrid tiles={HEADLINE_SCORE_TILES} groundTruthCount={null} />
        </Panel>

        <Panel
          title="Buttons"
          description="The vendor primitive in every variant and size the console uses."
        >
          <div className="flex flex-col gap-4">
            {BUTTON_VARIANTS.map((variant) => (
              <Demo key={variant} label={variant} bare>
                <div className="flex flex-wrap items-center gap-2">
                  {BUTTON_SIZES.map((size) => (
                    <Button key={size} variant={variant} size={size}>
                      Dispatch pumps
                    </Button>
                  ))}
                  <Button variant={variant} disabled>
                    Compute live
                  </Button>
                </div>
              </Demo>
            ))}
            <p className="type-micro text-text-3">
              The disabled button is the phase 5 control: it runs a real cycle once the orchestrator
              publishes runs.
            </p>
          </div>
        </Panel>

        <DeferredStory title="Drishti" minHeight={2400}>
          <DrishtiStories />
        </DeferredStory>
        <DeferredStory title="Nadi" minHeight={1600}>
          <NadiStories />
        </DeferredStory>
        <DeferredStory title="Sanket" minHeight={1200}>
          <SanketStories />
        </DeferredStory>
        <DeferredStory title="Jalayantra" minHeight={1800}>
          <JalayantraStories />
        </DeferredStory>
        <DeferredStory title="Pramana" minHeight={1400}>
          <PramanaStories />
        </DeferredStory>
        <DeferredStory title="Public map and citizen dashboard" minHeight={2400}>
          <PublicStories />
        </DeferredStory>
        <Panel
          title="Ward officer's desk"
          description="The authority desk's panels. This page holds no passphrase and the local API is read-only, so every write is refused by the API rather than faked here."
        >
          <DeferredStory title="Ward officer's desk" minHeight={1600}>
            <AuthorityStories />
          </DeferredStory>
        </Panel>

        <Panel
          title="Overlays"
          description="The command palette, the shortcuts overlay and the settings drawer are mounted by the app shell; these buttons open them."
        >
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => setCommandPaletteOpen(true)}>
              Open the command palette
            </Button>
            <Button size="sm" variant="outline" onClick={() => setShortcutsOpen(true)}>
              Open the shortcuts overlay
            </Button>
            <Button size="sm" variant="outline" onClick={() => setSettingsOpen(true)}>
              Open settings
            </Button>
          </div>
        </Panel>
      </div>
    </DesignSection>
  );
}

"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { CitizenMap } from "@/components/citizen/citizen-map";
import { CorridorPicker } from "@/components/citizen/corridor-picker";
import { DashboardIntro } from "@/components/citizen/dashboard-intro";
import { LeadTimeControl, type LeadMinutes } from "@/components/citizen/lead-time";
import { PumpsNearRoute } from "@/components/citizen/pumps-near-route";
import { ReportCard, ReportStatusChip } from "@/components/citizen/report-card";
import {
  ComplaintsNearYou,
  MyReportsPanel,
  ReportWaterLink,
  SelectedReportCard,
} from "@/components/citizen/report-panels";
import { RouteAnswer } from "@/components/citizen/route-answer";
import { TimeBaseSwitch, type TimeBase } from "@/components/citizen/time-base-switch";
import { WeatherChip } from "@/components/citizen/weather-chip";
import { WeatherDialog, WeatherIcon } from "@/components/citizen/weather-dialog";
import { BottomSheet } from "@/components/varuna/bottom-sheet";
import { DepthChips, type DepthHint } from "@/components/varuna/depth-chips";
import { FaintStreets } from "@/components/varuna/faint-streets";
import { GlobeEntry } from "@/components/varuna/globe-entry";
import { LanguageToggle } from "@/components/varuna/language-toggle";
import { Panel } from "@/components/varuna/panel";
import { PublicLegend } from "@/components/varuna/public-legend";
import { ReportWizard } from "@/components/varuna/report-wizard";
import { VehicleSelector, type PublicProfile } from "@/components/varuna/vehicle-selector";
import type { PumpPlan } from "@/lib/api/pumps";
import type { PublicReport, DismissedReport } from "@/lib/api/reports";
import type { RouteCorridor, RouteLeg, RoutePlan, RouteReason } from "@/lib/api/route";
import { weatherFromBody, type WeatherState } from "@/lib/api/weather";
import type { PublicLocale } from "@/lib/i18n/locales";
import { REPORT_STATUSES } from "@/lib/api/reports";
import type { MyReportState } from "@/components/citizen/use-report-feeds";

import { Demo } from "./section";

/* The public map, the report flow and the citizen dashboard. Data is shaped as the API answers it,
 * from the component tests' fixtures (components/citizen/__tests__), about 2 July 2019. */

const HINDMATA: [number, number] = [72.841, 19.012];
const UP_THE_ROAD: [number, number] = [72.8437, 19.0132];

function leg(overrides: Partial<RouteLeg> = {}): RouteLeg {
  return {
    minutes: 27,
    distanceM: 6100,
    maxDepthCm: 24,
    depart: "2019-07-02T08:40:00+05:30",
    arrive: "2019-07-02T09:07:00+05:30",
    safeUntil: "2019-07-02T08:05:00+05:30",
    path: [HINDMATA, UP_THE_ROAD],
    streets: ["Dr Ambedkar Road"],
    ...overrides,
  };
}

const REASONS: RouteReason[] = [
  {
    kind: "avoided",
    segmentId: "S100841069-000",
    name: "Dr Ambedkar Road",
    depthCm: 47,
    thresholdCm: 30,
    at: "2019-07-02T08:20:00+05:30",
    probability: 0.82,
  },
  {
    kind: "design",
    segmentId: "S100841069-000",
    name: "Dr Ambedkar Road",
    designIntensityMmH: 25,
    forecastPeakMmH: 61,
  },
  {
    kind: "closure",
    segmentId: "S102172139-001",
    name: "Sion Road",
    reason: "water main work",
    user: "the ward officer",
    at: "2019-07-02T08:12:00+05:30",
    until: null,
  },
];

function corridor(id: string, label: string, share: number, assigned = false): RouteCorridor {
  return {
    id,
    label,
    route: leg({ minutes: share > 0.4 ? 27 : 31 }),
    share,
    assigned,
    capacityScore: share * 100,
    maxProbability: 0.18,
  };
}

const CORRIDORS: RouteCorridor[] = [
  corridor("c-a", "A", 0.6, true),
  corridor("c-b", "B", 0.3),
  corridor("c-c", "C", 0.1),
];

const PLAN: RoutePlan = {
  runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
  profile: "car",
  departAt: "2019-07-02T08:40:00+05:30",
  naive: leg({ minutes: 21, safeUntil: null }),
  varuna: leg(),
  alternates: [],
  avoided: [],
  corridors: CORRIDORS,
  reasons: REASONS,
  tripId: "trip-1",
  notes: [],
  ms: 85,
};

/** One pump sent up Dr Ambedkar Road, as `GET /v1/pumps` shapes an assignment. */
const PUMP_PLAN: PumpPlan = {
  runId: PLAN.runId,
  thresholdCm: 45,
  benefitLabel: "Flash-lite emulator re-run with the pump's outflow",
  inventory: "synthetic",
  pumps: [],
  assignments: [
    {
      pumpId: "P-05",
      capacityM3PerHour: 1000,
      depot: "F/South ward office",
      targetId: "MUM-HS-01",
      targetName: "Hindmata junction",
      lon: HINDMATA[0],
      lat: HINDMATA[1],
      etaMin: 18,
      minutesBefore: 95,
      minutesAfter: 55,
      minutesSaved: 40,
    },
  ],
  unassigned: [],
  totalMinutesSaved: 40,
};

/** The API's demo seed at Hindmata, with its Commons photo and credit. */
const SEED_REPORT: PublicReport = {
  id: "seed-RPT-MUM-HS-01-ankle",
  origin: "seed",
  synthetic: true,
  ts: "2019-07-02T08:31:00+05:30",
  received_at: null,
  lat: 19.012,
  lon: 72.841,
  coordinates: "rounded to 3 decimals",
  city: "mumbai",
  outside_aoi: false,
  place: "Hindmata junction",
  depth_hint: "ankle",
  depth_cm: 10,
  text: "Water over the kerb at Hindmata junction.",
  source: "seed",
  photo_attached: true,
  has_photo: true,
  photo_url:
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a8/Bombay_flooded_street.jpg/960px-Bombay_flooded_street.jpg",
  thumb_url:
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a8/Bombay_flooded_street.jpg/330px-Bombay_flooded_street.jpg",
  photo_note: null,
  credit: {
    title: "File:Bombay flooded street.jpg",
    author: "Hitesh Ashar",
    license: "CC BY 2.0",
    license_url: "https://creativecommons.org/licenses/by/2.0",
    source_url: "https://commons.wikimedia.org/wiki/File:Bombay_flooded_street.jpg",
    original_source: null,
    taken: "2005-08-01",
    caption: "Heavy monsoon in Mumbai, August 2005",
    note: "Illustrative photo from Wikimedia Commons; not taken at this spot or on 2 July 2019.",
  },
  status: "received",
  status_ts: null,
  history: [{ status: "received", ts: "2019-07-02T08:31:00+05:30", role: "citizen" }],
};

/** A citizen's own report at King's Circle, with no photo. */
const CITIZEN_REPORT: PublicReport = {
  ...SEED_REPORT,
  id: "rpt-1789225538684",
  origin: "citizen",
  synthetic: false,
  ts: "2026-09-27T18:10:00+05:30",
  received_at: "2026-09-27T18:10:02+05:30",
  lat: 19.027,
  lon: 72.857,
  place: "King's Circle",
  depth_hint: "knee",
  depth_cm: 45,
  text: "Knee deep outside the station",
  source: "public-map",
  photo_attached: false,
  has_photo: false,
  photo_url: null,
  thumb_url: null,
  credit: null,
  status: "crew_sent",
  status_ts: "2026-09-27T18:22:00+05:30",
  history: [
    { status: "received", ts: "2026-09-27T18:10:02+05:30", role: "citizen" },
    { status: "crew_sent", ts: "2026-09-27T18:22:00+05:30", role: "ward_officer" },
  ],
};

const DISMISSED: DismissedReport = {
  id: "rpt-1789225539911",
  origin: "citizen",
  status: "dismissed",
  status_ts: "2026-09-27T18:40:00+05:30",
  history: [
    { status: "received", ts: "2026-09-27T18:31:00+05:30", role: "citizen" },
    { status: "dismissed", ts: "2026-09-27T18:40:00+05:30", role: "ward_officer" },
  ],
  note: "The ward officer closed this report: it duplicated one at the same junction.",
};

const MY_REPORTS: MyReportState[] = [
  {
    kind: "ready",
    entry: { id: CITIZEN_REPORT.id, sentAt: "2026-09-27T18:10:00+05:30" },
    report: CITIZEN_REPORT,
  },
  {
    kind: "ready",
    entry: { id: DISMISSED.id, sentAt: "2026-09-27T18:31:00+05:30" },
    report: DISMISSED,
  },
  { kind: "loading", entry: { id: "rpt-1789225540123", sentAt: "2026-09-27T18:45:00+05:30" } },
];

/** Open-Meteo's reading for Mumbai as `/v1/weather` relayed it in the component test. */
const WEATHER: WeatherState = {
  kind: "ready",
  weather: weatherFromBody({
    city: "mumbai",
    point: { lon: 72.86, lat: 19.065 },
    grid_point: { lon: 72.875, lat: 19.0 },
    grid_offset_km: 7.29,
    current: {
      ts: "2026-09-19T14:15:00+05:30",
      temperature_c: 28.4,
      humidity_pct: 79,
      precipitation_mm: 0.3,
      wind_kmh: 17.6,
      weather_code: 63,
      weather: "Moderate rain",
    },
    hourly: [
      { ts: "2026-09-19T15:00:00+05:30", precipitation_mm: 1.2, precipitation_probability_pct: 62 },
      { ts: "2026-09-19T16:00:00+05:30", precipitation_mm: 4.4, precipitation_probability_pct: 71 },
      { ts: "2026-09-19T17:00:00+05:30", precipitation_mm: 0, precipitation_probability_pct: 18 },
    ],
    fetched_at: "2026-09-19T14:13:00+05:30",
    age_s: 124,
    stale: false,
    ttl_s: 900,
    source: "open-meteo",
    source_url: "https://open-meteo.com/",
    licence: "CC BY 4.0",
    licence_url: "https://creativecommons.org/licenses/by/4.0/",
    attribution: "Weather data by Open-Meteo.com (CC BY 4.0)",
    notes: [],
  }),
};

const WEATHER_LOADING: WeatherState = { kind: "loading" };
const WEATHER_UNAVAILABLE: WeatherState = {
  kind: "unavailable",
  reason: "Live weather is unavailable: the API could not reach Open-Meteo.",
};

/** Six steps of the 08:40 cycle, for the lead-time control's clock labels. */
const VALID_TS = Array.from({ length: 36 }, (_, i) => {
  const minutes = 8 * 60 + 45 + i * 5;
  const hh = String(Math.floor(minutes / 60)).padStart(2, "0");
  const mm = String(minutes % 60).padStart(2, "0");
  return `2019-07-02T${hh}:${mm}:00+05:30`;
});

const IGNORE = () => undefined;

/** The public map, the citizen report flow and the citizen dashboard's own parts. */
export default function PublicStories() {
  const [profile, setProfile] = useState<PublicProfile>("car");
  const [locale, setLocale] = useState<PublicLocale>("en");
  const [hint, setHint] = useState<DepthHint | null>("knee");
  const [lead, setLead] = useState<LeadMinutes>(60);
  const [base, setBase] = useState<TimeBase>("replay");
  const [weatherOpen, setWeatherOpen] = useState(false);
  const [corridorId, setCorridorId] = useState<string | null>("c-a");
  const [selectedReport, setSelectedReport] = useState<string | null>(SEED_REPORT.id);
  const [mapOpen, setMapOpen] = useState(false);
  const [intro, setIntro] = useState<"none" | "dashboard" | "globe">("none");

  return (
    <div className="flex flex-col gap-6">
      <Panel
        title="Public map controls"
        description="The public map's own controls at 44 px targets: the vehicle the three colours are judged for, the language, the legend and the bottom sheet."
      >
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Demo label="Vehicle selector" note="The profile decides which depth is impassable.">
            <VehicleSelector value={profile} onValueChange={setProfile} />
          </Demo>
          <Demo
            label="Language toggle"
            note="English, Hindi and Marathi. The Hindi and Marathi are drafts awaiting a native speaker (ADR-0067)."
          >
            <LanguageToggle value={locale} onValueChange={setLocale} />
          </Demo>
          <Demo label="Public legend" note={`Passable, caution and impassable for a ${profile}.`}>
            <PublicLegend profile={profile} />
          </Demo>
          <Demo label="Report water" note="The public map's link into the report flow.">
            <ReportWaterLink />
          </Demo>
          <Demo
            label="Bottom sheet"
            note="Drag, or tap the handle, between its snap points (M24); reduced motion snaps without the rubber band."
            bare
          >
            <div className="rounded-panel border-line bg-ink relative h-80 overflow-hidden border">
              <BottomSheet containerHeight={320} title="Nearby streets" defaultSnap="half">
                <p className="type-small text-text-2 px-4">
                  Dr Ambedkar Road passable until 09:25. Lady Jamshedji Road passable until 09:40.
                </p>
              </BottomSheet>
            </div>
          </Demo>
          <Demo
            label="Faint streets"
            note="Mumbai's streets drawn faintly behind the 404 and the public map's empty state; it reads the city's segment layer from the API."
            bare
          >
            <div className="rounded-panel border-line bg-ink relative h-48 overflow-hidden border">
              <FaintStreets city="mumbai" className="absolute inset-0" />
            </div>
          </Demo>
        </div>
      </Panel>

      <Panel
        title="Citizen report"
        description="The three-step report flow and its depth chips. The wizard posts to /v1/reports only when submitted, so the story is safe to walk through up to the last step."
      >
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Demo label="Depth chips" note="Ankle about 10 cm, knee about 45 cm, waist about 90 cm.">
            <DepthChips value={hint} onValueChange={setHint} />
          </Demo>
          <Demo label="Report wizard" note="Step one: where the water is." bare>
            <div className="rounded-panel border-line bg-deep border p-4">
              <ReportWizard />
            </div>
          </Demo>
        </div>
      </Panel>

      <Panel
        title="Reports on the dashboard"
        description="A report as the public reads it, its status chip, the list near a place and the reporter's own list. The seeded reports are synthetic and say so."
      >
        <div className="flex flex-col gap-6">
          <Demo label="Status chips" note="Every status a report can carry, and a seeded one.">
            <div className="flex flex-wrap items-center gap-2">
              {REPORT_STATUSES.map((status) => (
                <ReportStatusChip key={status} status={status} />
              ))}
              <ReportStatusChip status="received" seeded />
            </div>
          </Demo>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Demo label="Seeded report, with its credited photo" bare>
              <ReportCard report={SEED_REPORT} />
            </Demo>
            <Demo label="A citizen's report, crew sent" bare>
              <ReportCard report={CITIZEN_REPORT} selected />
            </Demo>
            <Demo label="Dismissed" note="The reporter reads why." bare>
              <ReportCard report={DISMISSED} />
            </Demo>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Demo label="Complaints near you" bare>
              <ComplaintsNearYou
                reports={[SEED_REPORT, CITIZEN_REPORT]}
                centre={{ lon: HINDMATA[0], lat: HINDMATA[1] }}
                selectedId={selectedReport}
                onSelect={setSelectedReport}
                loaded
                error={null}
                headingId="design-complaints"
              />
            </Demo>
            <Demo label="Complaints, loading and error" bare>
              <div className="flex flex-col gap-4">
                <ComplaintsNearYou
                  reports={[]}
                  centre={null}
                  selectedId={null}
                  onSelect={IGNORE}
                  loaded={false}
                  error={null}
                  headingId="design-complaints-loading"
                />
                <ComplaintsNearYou
                  reports={[]}
                  centre={null}
                  selectedId={null}
                  onSelect={IGNORE}
                  loaded
                  error="Reports are unavailable: the API did not answer. Your own reports are kept on this phone."
                  headingId="design-complaints-error"
                />
              </div>
            </Demo>
            <Demo label="My reports" note="Ready, dismissed and still loading." bare>
              <MyReportsPanel items={MY_REPORTS} ready headingId="design-my-reports" />
            </Demo>
            <Demo label="Selected report" bare>
              <SelectedReportCard report={CITIZEN_REPORT} onClose={IGNORE} focusOnOpen={false} />
            </Demo>
          </div>
        </div>
      </Panel>

      <Panel
        title="Dashboard route answer"
        description="The citizen's route in plain language: the reasons it turns, the corridors it spreads over and the pumps along it. The plan is the component test's KEM-to-Sion shape at 08:40."
      >
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Demo label="Route answer" bare>
              <RouteAnswer
                plan={PLAN}
                reasons={REASONS}
                corridors={CORRIDORS}
                selectedCorridorId={corridorId}
                onPickCorridor={setCorridorId}
                profile="car"
                pumpPlan={PUMP_PLAN}
              />
            </Demo>
            <div className="flex flex-col gap-4">
              <Demo
                label="Corridor picker"
                note="How the traffic is spread; the assigned one first."
              >
                <CorridorPicker
                  corridors={CORRIDORS}
                  selectedId={corridorId}
                  onPick={setCorridorId}
                />
              </Demo>
              <Demo label="Pumps near the route">
                <PumpsNearRoute route={PLAN.varuna} plan={PUMP_PLAN} runId={PLAN.runId} />
              </Demo>
              <Demo label="Pumps near the route, no route yet">
                <PumpsNearRoute route={null} plan={PUMP_PLAN} runId={PLAN.runId} />
              </Demo>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Demo label="Lead time" note="How far ahead the map is drawn.">
              <LeadTimeControl
                value={lead}
                onValueChange={setLead}
                validTs={VALID_TS}
                cycleTs="2019-07-02T08:40:00+05:30"
              />
            </Demo>
            <Demo label="Time base" note="Replay time or the clock on the wall.">
              <TimeBaseSwitch value={base} onValueChange={setBase} />
            </Demo>
          </div>
        </div>
      </Panel>

      <Panel
        title="Live weather"
        description="Open-Meteo's reading beside the replay, never mixed into it. The chip opens the dialog; loading and unavailable say so."
      >
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Demo label="Reading">
              <div className="flex flex-col gap-3">
                <WeatherChip state={WEATHER} cityLabel="Mumbai" replayLabel="Replay 2 Jul 2019" />
                <Button size="sm" variant="outline" onClick={() => setWeatherOpen(true)}>
                  Open the weather dialog
                </Button>
              </div>
            </Demo>
            <Demo label="Loading">
              <WeatherChip state={WEATHER_LOADING} cityLabel="Mumbai" />
            </Demo>
            <Demo label="Unavailable">
              <WeatherChip state={WEATHER_UNAVAILABLE} cityLabel="Mumbai" />
            </Demo>
          </div>
          <Demo
            label="Weather icons"
            note="By WMO code: clear, partly cloudy, rain, thunder, unknown."
          >
            <div className="text-text-2 flex items-center gap-3">
              {[0, 2, 63, 95, null].map((code) => (
                <WeatherIcon key={String(code)} code={code} className="size-5" />
              ))}
            </div>
          </Demo>
          <WeatherDialog
            open={weatherOpen}
            onOpenChange={setWeatherOpen}
            state={WEATHER}
            cityLabel="Mumbai"
            replayLabel="Replay 2 Jul 2019"
          />
        </div>
      </Panel>

      <Panel
        title="Citizen map and entry"
        description="The dashboard's map is WebGL over Esri imagery and the entry (M27) takes the whole screen, so this page draws each only when asked."
      >
        <div className="flex flex-col gap-4">
          <Demo
            label="Citizen map"
            note="Mumbai's latest run for a car, with the reports pinned."
            bare
          >
            {mapOpen ? (
              <div className="rounded-panel border-line relative h-[26rem] overflow-hidden border">
                <CitizenMap city="mumbai" profile="car" className="absolute inset-0" />
              </div>
            ) : (
              <div className="rounded-panel border-line bg-deep flex min-h-[10rem] flex-col items-start justify-center gap-2 border p-4">
                <p className="type-small text-text-2 max-w-[60ch]">
                  The map mounts MapLibre, deck.gl and the imagery basemap and reads the latest run
                  from the API. It is drawn here on request.
                </p>
                <Button size="sm" variant="outline" onClick={() => setMapOpen(true)}>
                  Draw the citizen map
                </Button>
              </div>
            )}
          </Demo>
          <Demo
            label="Entry, full screen"
            note="The globe turns to India and narrows to Mumbai (M27). Skip ends it; under reduced motion it is a static Mumbai frame and a cut."
          >
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => setIntro("dashboard")}>
                Play the dashboard entry
              </Button>
              <Button size="sm" variant="outline" onClick={() => setIntro("globe")}>
                Play the globe entry alone
              </Button>
            </div>
          </Demo>
          {intro === "dashboard" ? <DashboardIntro force onDone={() => setIntro("none")} /> : null}
          {intro === "globe" ? (
            <GlobeEntry
              sessionKey="varuna-design-globe"
              slot="design-globe"
              force
              onDone={() => setIntro("none")}
            />
          ) : null}
        </div>
      </Panel>
    </div>
  );
}

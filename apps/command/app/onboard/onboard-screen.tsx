"use client";

import { MapPinned } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { CityMap } from "@/components/map/city-map";
import { AppShell } from "@/components/varuna/app-shell";
import { LogStream, type LogLine } from "@/components/varuna/log-stream";
import { MapSlot } from "@/components/varuna/map-slot";
import { OnboardFinishCard, type FinishFacts } from "@/components/varuna/onboard-finish-card";
import {
  IDLE_ONBOARDING_STEPS,
  ONBOARDING_STEP_IDS,
  OnboardingSteps,
  type OnboardingStage,
  type OnboardingStepId,
  type OnboardingStepState,
  type OnboardingStepStatus,
} from "@/components/varuna/onboarding-steps";
import { PageHeader } from "@/components/varuna/page-header";
import { navItem } from "@/lib/nav";
import { PanelErrorBoundary } from "@/components/varuna/panel-error-boundary";
import { loadBuildings, loadDrains, type DrainPath } from "@/lib/api/city-layers";
import {
  FORECAST_STAGES,
  latestOnboard,
  loadRunRain,
  pollOnboard,
  startOnboard,
  type OnboardBuild,
  type OnboardForecast,
  type OnboardJob,
  type OnboardLogEntry,
  type OnboardStepId,
  type OnboardSteps,
  type RunRain,
} from "@/lib/api/onboard";
import {
  allSegments,
  joinSegments,
  loadRunDepth,
  type GeoSegment,
  type RunDepth,
} from "@/lib/api/run-depth";
import { apiUrl } from "@/lib/api/client";
import { useLive, type LiveTopicFilter } from "@/lib/api/live";
import type { LiveEvent } from "@/lib/api/schemas";
import { DEFAULT_CITY, cityFromSearch } from "@/lib/city";
import { useLayerFade } from "@/lib/hooks/use-layer-fade";
import { affectedFrame, useSettledBounds } from "@/lib/map/affected-bounds";
import {
  formatCount,
  formatDateTime,
  formatSeconds,
  formatTimeWithLead,
  minutesBetween,
} from "@/lib/format";
import { OnboardLayers, type WizardLayerId, type WizardLayerState } from "./onboard-layers";

/** The shape `/v1/city/{city}/layers/segments` returns, as the two readers below want it. */
type SegmentCollection = Parameters<typeof allSegments>[0];

/** The city the wizard builds when the URL names none: SPEC.md 3.3's `CHN-SOUTH` on stage. */
export const ONBOARD_DEFAULT_CITY = "chennai";

/**
 * The replay city (SPEC.md 3.3): the city the 2 July 2019 demo replays, and the one the API
 * serves when a URL names none. The wizard never onboards it - every step after `fetch` rebuilds
 * in place, so a click would rewrite `city/mumbai/` under the demo and make its design-storm run
 * the newest Mumbai run every screen defaults to - and the API answers 409 `city_is_replay_city`
 * if asked anyway.
 */
export const REPLAY_CITY = DEFAULT_CITY;

/** Each city's first-forecast storm. A city with none here gets the API's own default. */
const DESIGN_STORMS: Record<string, string> = {
  chennai: "CHN-IDF-25yr",
};

/** Where each city's AOI is (SPEC.md 3.3), so the map frames it before any layer arrives. */
const CITY_BOUNDS: Record<string, [[number, number], [number, number]]> = {
  chennai: [
    [80.2, 12.96],
    [80.28, 13.05],
  ],
  mumbai: [
    [72.815, 18.995],
    [72.905, 19.135],
  ],
};

/** How often the job is polled. A build is minutes long; a second is smooth and costs nothing. */
const POLL_MS = 1000;

/** The one socket topic the wizard listens to. Module-level so `useLive` never re-subscribes. */
const ONBOARD_TOPICS: readonly LiveTopicFilter[] = ["onboard.progress"];

/** Pipes drawn at once. Chennai's graph is smaller than Mumbai's, but the cap keeps 60 fps. */
const MAP_EDGE_LIMIT = 4000;

/** The lead the map opens the first forecast at: an hour ahead, a moment every run contains. */
export const DEFAULT_LEAD_MIN = 60;

/** The API's step names to the component's. Two vocabularies for one list, joined in one place. */
const STEP_OF: Record<OnboardStepId, OnboardingStepId> = {
  choose_area: "area",
  fetch_open_data: "fetch",
  condition_terrain: "condition",
  infer_drains: "drains",
  build_graph: "graph",
  first_forecast: "forecast",
};

const API_STEP_OF = Object.fromEntries(
  Object.entries(STEP_OF).map(([api, ui]) => [ui, api as OnboardStepId]),
) as Record<OnboardingStepId, OnboardStepId>;

/** The first forecast's stages as the row names them (SPEC.md 11.11). */
const STAGE_LABELS: Record<(typeof FORECAST_STAGES)[number], string> = {
  sky: "Sky",
  twin: "Twin",
  pulse: "Pulse",
  flash: "Flash",
  products: "products",
};

/**
 * Which step writes each map layer, as the index of that step in `ONBOARDING_STEP_IDS`.
 *
 * The wizard tries a layer as soon as its step has completed, and again on every later step, so a
 * layer appears the moment the pipeline has actually written it rather than at a time this screen
 * has decided looks good.
 */
const LAYER_AFTER: Record<Exclude<WizardLayerId, "depth">, number> = {
  streets: ONBOARDING_STEP_IDS.indexOf("fetch"),
  buildings: ONBOARDING_STEP_IDS.indexOf("fetch"),
  drains: ONBOARDING_STEP_IDS.indexOf("drains"),
};

/** "Chennai" from "chennai". The switcher's own name comes from the API; this one is the slug's. */
export function cityName(city: string): string {
  return city
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/**
 * The six rows from the API's own per-step records: "Done 1.4 s" with the pipeline's milliseconds,
 * "Loaded from disk" where every step behind a row only read the city folder, and the first
 * forecast's stages under its row.
 */
export function stepsFromRecords(steps: OnboardSteps): OnboardingStepState[] {
  return ONBOARDING_STEP_IDS.map((id) => {
    const record = steps[API_STEP_OF[id]];
    if (!record) return { id, progress: 0, elapsedS: 0, status: "waiting" as const };
    let status: OnboardingStepStatus = record.status;
    if (record.status === "done" && record.loadedFromDisk) status = "loaded";
    const finished = status === "done" || status === "loaded" || status === "skipped";
    const stages: OnboardingStage[] | undefined = record.stages
      ? FORECAST_STAGES.flatMap((stage) => {
          const entry = record.stages?.[stage];
          return entry ? [{ label: STAGE_LABELS[stage], status: entry.status, ms: entry.ms }] : [];
        })
      : undefined;
    return {
      id,
      status,
      progress: finished
        ? 100
        : status === "running"
          ? Math.round(record.progress * 100)
          : status === "failed"
            ? Math.round(record.progress * 100)
            : 0,
      elapsedS: (record.ms ?? 0) / 1000,
      elapsedMs: status === "waiting" ? null : record.ms,
      detail: record.detail,
      stages,
    };
  });
}

/**
 * Turn one job into the six step rows the wizard shows.
 *
 * An API that reports each step's own record (`steps`) is read as it is. An older one reports only
 * the step it is on and the overall progress: the rows before it are done - with no time, since
 * none was reported, rather than a "0 s" nobody measured (SPEC.md 6) - the running row carries
 * the job's elapsed time, and the rest wait.
 *
 * With no job in the API's memory, a city whose last build was recorded shows that build's rows
 * (`previous`). One with no record reads "Already built" (task D-21) - but `built` is only the API
 * finding `city/<city>/segments.parquet`: it says nothing about a first forecast, so the forecast
 * row reads "Already built" only when `hasRun`, a run for the city actually read, and "Waiting"
 * otherwise (SPEC.md rules 6, 7).
 */
export function toSteps(job: OnboardJob | null, hasRun = false): OnboardingStepState[] {
  if (!job) return IDLE_ONBOARDING_STEPS;
  if (job.status === "none") {
    if (job.previous?.steps) return stepsFromRecords(job.previous.steps);
    if (!job.built) return IDLE_ONBOARDING_STEPS;
    return ONBOARDING_STEP_IDS.map((id) =>
      id === "forecast" && !hasRun
        ? { id, progress: 0, elapsedS: 0, status: "waiting" as const }
        : { id, progress: 100, elapsedS: 0, status: "cached" as const },
    );
  }
  if (job.steps) return stepsFromRecords(job.steps);

  const current = ONBOARDING_STEP_IDS.indexOf(STEP_OF[job.step] ?? "area");
  const failed = job.status === "failed";
  const finished = job.status === "finished";
  return ONBOARDING_STEP_IDS.map((id, index) => {
    if (finished || index < current) {
      return { id, progress: 100, elapsedS: 0, status: "done" as const };
    }
    if (index === current) {
      return {
        id,
        progress: Math.round(job.progress * 100),
        elapsedS: job.elapsedS,
        status: failed ? ("failed" as const) : ("running" as const),
      };
    }
    return { id, progress: 0, elapsedS: 0, status: "waiting" as const };
  });
}

/**
 * The pipeline's lines, as the log component wants them. The text is never rewritten here.
 *
 * Each line carries the instant the API's tap captured it. An API too old to send those sends the
 * last forty lines as bare text, and they get no time at all rather than the job's start time on
 * every line, which read as forty things happening in the same second.
 */
export function toLines(job: OnboardJob | null): LogLine[] {
  if (!job) return [];
  if (job.status === "none") return job.previous ? entriesToLines(job.previous.log) : [];
  if (job.log) return entriesToLines(job.log);
  return job.logTail.map((text) => ({
    ts: "",
    level: /fail|error/i.test(text) ? ("error" as const) : ("info" as const),
    text,
  }));
}

function entriesToLines(entries: readonly OnboardLogEntry[]): LogLine[] {
  return entries.map((entry) => ({ ts: entry.ts, level: entry.level, text: entry.text }));
}

/**
 * How far the build has got, as an index into `ONBOARDING_STEP_IDS`, or -1 before it starts.
 *
 * A finished build counts as past every step, including one that a reopened tab never watched:
 * the layers are on disk and the map must draw them.
 */
export function reachedStepIndex(job: OnboardJob | null): number {
  if (!job) return -1;
  // A city built in an earlier session has no job in the API's memory - jobs do not outlive the
  // process - but its layers are on disk, and a wizard that drew nothing for them would be
  // telling the operator the city had not been built when it had.
  if (job.built || job.status === "finished") return ONBOARDING_STEP_IDS.length;
  if (job.status === "none") return -1;
  const current = ONBOARDING_STEP_IDS.indexOf(STEP_OF[job.step] ?? "area");
  // The step it is on has not finished, so only the ones before it have written anything.
  return current;
}

/**
 * Minutes from the run's cycle to each of its steps: the lead the scrub prints. A run with no
 * cycle time counts from one step before its first valid time, which is how the cycle writes them.
 */
export function stepLeads(
  validTs: readonly string[],
  cycleTs: string | null,
  stepMin = 5,
): number[] {
  const origin = cycleTs ?? null;
  return validTs.map((ts, index) => {
    const lead = origin ? minutesBetween(origin, ts) : null;
    return lead ?? (index + 1) * stepMin;
  });
}

/** The step whose lead is nearest `targetMin` (the earlier one on a tie); 0 for an empty run. */
export function leadStepIndex(leads: readonly number[], targetMin = DEFAULT_LEAD_MIN): number {
  let best = 0;
  for (let i = 1; i < leads.length; i += 1) {
    if (Math.abs(leads[i] - targetMin) < Math.abs(leads[best] - targetMin)) best = i;
  }
  return best;
}

/**
 * Where the finish card's button goes (task D-21).
 *
 * The city alone is not enough. `/console` opens on the cycle the demo script starts from, and
 * that rule is about Mumbai's 2 July replay; a Chennai console with no run named would fall back
 * to whatever the API calls newest for Chennai. Naming the build's own first run is what makes the
 * card land on the water it just made.
 */
export function consoleHref(city: string, runId: string | null): string {
  const params = new URLSearchParams({ city });
  if (runId) params.set("run", runId);
  return `/console?${params.toString()}`;
}

/** Where the wizard for `city` lives: a plain link, so the top bar's switcher reads the city. */
export function onboardHref(city: string): string {
  return `/onboard?${new URLSearchParams({ city }).toString()}`;
}

/** "Previous build, 26 Sep 2026 03:13 IST, 51 s". */
export function previousBuildLabel(build: OnboardBuild): string {
  const when = build.startedAt ? `, ${formatDateTime(build.startedAt)}` : "";
  const took = build.elapsedS !== null ? `, ${formatSeconds(build.elapsedS)}` : "";
  return `Previous build${when}${took}`;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * The forecast stages' milliseconds added up over the same allowlist the run stamp uses, with the
 * stages that sum covers. A stage at 0 ms did not run (Flash, on a design storm with no ensemble)
 * and is left out of both, so the label never names a stage that took no time.
 */
export function stageSum(stageMs: Record<string, number>): { ms: number | null; stages: string[] } {
  const ran = FORECAST_STAGES.filter((stage) => {
    const value = stageMs[stage];
    return typeof value === "number" && Number.isFinite(value) && value > 0;
  });
  return {
    ms: ran.length > 0 ? ran.reduce((total, stage) => total + stageMs[stage], 0) : null,
    stages: ran.map((stage) => STAGE_LABELS[stage]),
  };
}

/**
 * The finish card's numbers. The API's summary of the run when it made one (a build it recorded);
 * otherwise the same numbers computed from the run the map loaded: wet streets are the segments
 * the run served as wet, the peak is each street's highest step, the stage time is the run's own
 * `stage_ms`, and the rain is the AOI-mean hyetograph `run.json` recorded.
 *
 * `wallMs` is the first forecast's own step time from the build that made this run, so the card's
 * "Forecast computed in" is the number the step row beside it prints. The stage sum is printed as
 * well, labelled with the stages it adds up: it is smaller, and both are real.
 */
export function finishFacts(
  runId: string,
  summary: OnboardForecast | null,
  depth: RunDepth | null,
  rain: RunRain | null,
  wallMs: number | null = null,
): FinishFacts | null {
  if (!summary && !depth) return null;
  const peaks = depth
    ? [...depth.depthCm.values()].filter((s) => s.length > 0).map((s) => Math.max(...s))
    : [];
  const summaryStages = summary && Object.keys(summary.stageMs).length > 0 ? summary.stageMs : null;
  const stages = stageSum(summaryStages ?? depth?.provenance.stageMs ?? {});
  return {
    runId,
    wetStreets: summary?.wetStreets ?? (depth ? depth.depthCm.size : null),
    streetsTotal: summary?.streetsTotal ?? (depth?.nSegmentsTotal || null),
    wetThresholdCm: summary?.wetThresholdCm ?? null,
    medianPeakCm: summary?.medianPeakCm ?? median(peaks),
    forecastMs: wallMs !== null && Number.isFinite(wallMs) && wallMs > 0 ? wallMs : null,
    stagesMs: stages.ms,
    stages: stages.stages,
    storm:
      summary?.storm ??
      (rain
        ? {
            id: depth?.provenance.bundle ?? null,
            totalMm: rain.totalMm,
            durationMin: rain.durationMin,
            peakMmH: rain.peakMmH,
            source: "run" as const,
          }
        : null),
  };
}

/**
 * City-in-a-box wizard (SPEC.md 7.9, tasks P9.5, P9.6 and D-21), behind the Suspense boundary
 * `useSearchParams` needs.
 *
 * The city is the URL's `?city=`, `chennai` when it names none - and the URL is made to say so,
 * because the top bar's city switcher reads the address bar, and a wizard building Chennai under a
 * switcher reading "Mumbai" was the state this screen was in.
 */
export function OnboardScreen() {
  return (
    <Suspense
      fallback={
        <AppShell>
          <div className="relative h-full min-h-0 w-full">
            {/* Not the operator slot: its "Reconstructed replay" chip is not true of a city
                being onboarded from a design storm. */}
            <MapSlot audience="public" emptyState={null} />
          </div>
        </AppShell>
      }
    >
      <OnboardRoute />
    </Suspense>
  );
}

function OnboardRoute() {
  const searchParams = useSearchParams();
  const named = searchParams.get("city");
  const city = cityFromSearch(searchParams.toString(), ONBOARD_DEFAULT_CITY);

  useEffect(() => {
    if (named) return;
    const params = new URLSearchParams(window.location.search);
    params.set("city", city);
    // The native call rather than `router.replace`, and on purpose. The switcher reads
    // `window.location` while it renders; `router.replace` re-renders this screen first and moves
    // the address bar after, so the switcher kept reading "Mumbai" until something else rendered.
    // Next's router integrates `history.replaceState`: the URL changes at once, `useSearchParams`
    // follows, and the re-render it causes finds the new city - with no server round trip.
    window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
  }, [named, city]);

  if (city === REPLAY_CITY) return <ReplayCityRefusal key={city} city={city} />;
  // A different city is a different wizard: its job, layers and forecast start from nothing.
  return <OnboardView key={city} city={city} />;
}

/**
 * `/onboard?city=mumbai`: the wizard for the replay city, refused in one line with the way on.
 *
 * Nothing is fetched for it. A Mumbai job record, its layers and its newest run would otherwise
 * fill the map and the finish card, and the card would present the 2 July replay as a "first
 * forecast, uncalibrated", which is not what it is.
 */
function ReplayCityRefusal({ city }: { city: string }) {
  const name = cityName(city);
  const other = cityName(ONBOARD_DEFAULT_CITY);
  return (
    <AppShell>
      <div className="flex h-full min-h-0 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        <div className="border-line flex shrink-0 flex-col gap-4 border-b p-5 lg:min-h-0 lg:w-[420px] lg:border-r lg:border-b-0">
          <div className="space-y-2">
            <PageHeader
              title={navItem("onboard").label}
              screen={navItem("onboard")}
              description={`City in a box: open data in, digital twin out. ${other} in minutes.`}
              honesty="Inferred drain graph"
            />
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <Button disabled aria-disabled="true">
                <MapPinned aria-hidden="true" />
                Start onboarding {name}
              </Button>
              <p className="type-micro text-text-2 min-w-0 flex-1">
                {name} is the replay city and is not onboarded here.{" "}
                <a
                  href={onboardHref(ONBOARD_DEFAULT_CITY)}
                  className="text-tide underline underline-offset-2"
                >
                  Onboard {other}
                </a>
              </p>
            </div>
          </div>
        </div>
        <div className="relative min-h-[44rem] min-w-0 flex-1 lg:min-h-0">
          <MapSlot
            emptyState={{
              title: `${name} is the replay city`,
              description: `Onboard ${other} to watch a city build from open data.`,
            }}
          />
        </div>
      </div>
    </AppShell>
  );
}

/**
 * The wizard itself.
 *
 * Every line in the log comes from `services/city` and every step's status and time from the job,
 * so a judge watching this is watching the pipeline rather than an animation. The map stacks the
 * layers as the build writes them - roads, the inferred drain graph, and finally the first
 * forecast's depth - each fading in over 400 ms (motion M19); buildings wait until asked for.
 */
function OnboardView({ city }: { city: string }) {
  const name = cityName(city);
  const designStorm = DESIGN_STORMS[city];
  const [job, setJob] = useState<OnboardJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  // The city's own segment layer, kept as it arrived: `allSegments` draws the whole network and
  // `joinSegments` colours the subset the first forecast wetted, and both want this shape.
  const [roads, setRoads] = useState<SegmentCollection | null>(null);
  // The street layer was asked for and the API refused it: the map says so instead of "loading".
  const [streetsFailed, setStreetsFailed] = useState(false);
  const [buildings, setBuildings] = useState<[number, number][][] | null>(null);
  const [drains, setDrains] = useState<DrainPath[] | null>(null);
  // Every pipe the drain layer served, of which the map draws the first `MAP_EDGE_LIMIT`.
  const [drainsTotal, setDrainsTotal] = useState<number | null>(null);
  const [depth, setDepth] = useState<RunDepth | null>(null);
  const [rain, setRain] = useState<{ runId: string; rain: RunRain | null } | null>(null);
  // The API answered the run lookup and served no run for this city. Kept apart from `depth ===
  // null`, which is also true while the lookup is still in flight: the screen says "no forecast"
  // only once it has asked and been told so.
  const [noRun, setNoRun] = useState(false);
  // Buildings are off by default and fetched only when switched on: 72,522 polygons for Chennai,
  // 2.4 MB gzipped, and the console already opens with them off.
  const [show, setShow] = useState<Record<WizardLayerId, boolean>>({
    streets: true,
    buildings: false,
    drains: true,
    depth: true,
  });
  // The lead-time scrub's step, per run: null means "the default +60 min" for whichever run is up.
  const [picked, setPicked] = useState<{ runId: string; step: number } | null>(null);
  /** Which (job, layer) pairs have already been asked for, so a poll does not refetch. */
  const asked = useRef(new Set<string>());

  // Rejoin a build already in flight, so reopening the tab does not look like nothing happened.
  useEffect(() => {
    const controller = new AbortController();
    latestOnboard(city, controller.signal)
      .then(setJob)
      .catch(() => undefined);
    return () => controller.abort();
  }, [city]);

  // The pipeline's own `onboard.progress` events over the live socket (SPEC.md 7.9: "streams
  // progress over the WebSocket"). Each step start and finish for this city asks for the job at
  // once, so a step lands on screen when the pipeline reports it rather than on the next poll
  // tick; the job endpoint carries the log, so the event is the trigger and the job the state.
  const liveJobId = job?.status === "running" || job?.status === "queued" ? job.jobId : null;
  const [streamed, setStreamed] = useState(0);
  const onProgress = useCallback(
    (event: LiveEvent) => {
      const payload = (event.payload ?? {}) as { city?: unknown };
      if (!liveJobId || payload.city !== city) return;
      setStreamed((n) => n + 1);
      pollOnboard(liveJobId)
        .then(setJob)
        .catch(() => undefined);
    },
    [liveJobId, city],
  );
  useLive({ topics: ONBOARD_TOPICS, onEvent: onProgress, enabled: liveJobId !== null });

  // Poll while the job is live. Stops the moment it finishes or fails.
  useEffect(() => {
    const id = job?.jobId;
    if (!id || (job.status !== "running" && job.status !== "queued")) return;
    const controller = new AbortController();
    const timer = setInterval(() => {
      pollOnboard(id, controller.signal)
        .then(setJob)
        .catch(() => undefined);
    }, POLL_MS);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [job?.jobId, job?.status]);

  const live = job && job.status !== "none" ? job : null;
  const previous = job?.status === "none" ? job.previous : null;
  const built = job?.built ?? false;
  const finished = live?.status === "finished";
  // Built, no job in this API process, and no record of the build that wrote it: the case task
  // D-21 was about. Whether it also has a forecast is `hasRun`, not this.
  const cached = job?.status === "none" && built && !previous;

  // The run the map draws and the card names: this build's own first run; the recorded build's,
  // when that run is still on this API; or, with no record at all, the newest the API serves for
  // the city. A running or failed rebuild draws none - the city on disk is mid-rewrite or suspect.
  const recordedRun =
    previous && previous.firstRunExists !== false ? (previous.firstRunId ?? null) : null;
  const wantRun: string | "newest" | null = live
    ? finished
      ? (live.firstRunId ?? null)
      : null
    : recordedRun
      ? recordedRun
      : cached
        ? "newest"
        : null;

  const reached = reachedStepIndex(job);
  const jobKey = job?.jobId ?? (built ? "existing" : "none");

  // Ask for each layer once its step has completed. A layer the pipeline has not written yet
  // answers 404; that is not an error here, it is "not yet", and the next step tries again.
  useEffect(() => {
    if (reached < 0) return;
    const controller = new AbortController();
    const once = (layer: string, load: () => Promise<void>) => {
      const key = `${jobKey}:${layer}`;
      if (asked.current.has(key)) return;
      asked.current.add(key);
      void load().catch(() => {
        // Not written yet (or the request was aborted): forget the attempt so a later step retries.
        asked.current.delete(key);
      });
    };

    if (reached > LAYER_AFTER.streets) {
      once("streets", async () => {
        try {
          const response = await fetch(apiUrl(`/v1/city/${city}/layers/segments`), {
            signal: controller.signal,
          });
          if (!response.ok) throw new Error(`segments ${response.status}`);
          setRoads((await response.json()) as SegmentCollection);
          setStreetsFailed(false);
        } catch (failure) {
          if (!controller.signal.aborted) setStreetsFailed(true);
          throw failure;
        }
      });
    }
    if (show.buildings && reached > LAYER_AFTER.buildings) {
      once("buildings", async () => {
        setBuildings(await loadBuildings(city, controller.signal));
      });
    }
    if (reached > LAYER_AFTER.drains) {
      once("drains", async () => {
        const pipes = await loadDrains(city, controller.signal);
        if (pipes.length === 0) throw new Error("drains not written yet");
        setDrainsTotal(pipes.length);
        setDrains(pipes.slice(0, MAP_EDGE_LIMIT));
      });
    }
    if (wantRun) {
      once(`depth:${wantRun}`, async () => {
        try {
          const runId = wantRun === "newest" ? undefined : wantRun;
          setDepth(await loadRunDepth(runId, controller.signal, undefined, city));
          setNoRun(false);
        } catch (failure) {
          // An abort is this effect being replaced, not an answer about the city.
          if (!controller.signal.aborted) setNoRun(true);
          throw failure;
        }
      });
    }
    return () => controller.abort();
  }, [reached, jobKey, wantRun, city, show.buildings]);

  // The run the finish card names and opens: the one the map has actually loaded, or the build's
  // own first run while its layers are still on their way.
  const openRunId =
    wantRun === "newest" ? (depth?.provenance.runId ?? null) : wantRun ? wantRun : null;
  const shownDepth = depth && openRunId && depth.provenance.runId === openRunId ? depth : null;
  // The build that made the run on screen, when one did: its summary, and its first-forecast row's
  // own time for the card, so the card and the row beside it print the same number.
  const forecastBuild =
    [live, previous].find((build) => build?.forecast && build.forecast.runId === openRunId) ?? null;
  const summary: OnboardForecast | null = forecastBuild?.forecast ?? null;
  const forecastRow = forecastBuild?.steps?.first_forecast;
  const forecastWallMs = forecastRow?.status === "done" ? (forecastRow.ms ?? null) : null;

  // A first forecast the API did not summarise - one built before the API recorded builds - gets
  // its rain from the run's own `run.json`, never from a constant.
  const needRain = shownDepth !== null && !summary?.storm;
  const rainRunId = needRain ? shownDepth.provenance.runId : null;
  useEffect(() => {
    if (!rainRunId) return;
    const controller = new AbortController();
    loadRunRain(rainRunId, controller.signal)
      .then((value) => setRain({ runId: rainRunId, rain: value }))
      .catch(() => undefined);
    return () => controller.abort();
  }, [rainRunId]);
  const runRain = rain && rain.runId === rainRunId ? rain.rain : null;

  const facts = useMemo(
    () => (openRunId ? finishFacts(openRunId, summary, shownDepth, runRain, forecastWallMs) : null),
    [openRunId, summary, shownDepth, runRain, forecastWallMs],
  );

  const segments = useMemo<GeoSegment[] | null>(() => (roads ? allSegments(roads) : null), [roads]);
  // The first forecast's own wet streets, coloured by its depth.
  const wet = useMemo(
    () => (shownDepth && roads ? joinSegments(roads, shownDepth.depthCm) : []),
    [shownDepth, roads],
  );

  // Motion M19: each layer fades in over 400 ms when it arrives, and instantly under reduced
  // motion. A reopened tab whose layers are already loaded reads 1 at once and does not replay.
  const fadeStreets = useLayerFade("streets", (segments?.length ?? 0) > 0 && show.streets);
  const fadeBuildings = useLayerFade("buildings", (buildings?.length ?? 0) > 0 && show.buildings);
  const fadeDrains = useLayerFade("drains", (drains?.length ?? 0) > 0 && show.drains);
  const fadeDepth = useLayerFade("depth", shownDepth !== null && show.depth);
  // The card's numbers arrive with the forecast (M19's "final depth fade-in"), whether or not the
  // depth layer is switched on at that moment.
  const fadeFacts = useLayerFade("finish", shownDepth !== null || summary !== null);

  // The lead-time scrub: every step of the run, opening at +60 min.
  const leads = useMemo(
    () =>
      shownDepth
        ? stepLeads(
            shownDepth.validTs,
            shownDepth.provenance.cycleTs,
            shownDepth.provenance.stepMin,
          )
        : [],
    [shownDepth],
  );
  const defaultStep = useMemo(() => leadStepIndex(leads), [leads]);
  const depthStep =
    shownDepth && picked && picked.runId === shownDepth.provenance.runId
      ? Math.min(picked.step, Math.max(leads.length - 1, 0))
      : defaultStep;
  const depthAt = shownDepth?.validTs[depthStep];
  const atLabel = depthAt ? formatTimeWithLead(depthAt, leads[depthStep]) : null;

  // Where the map opens once the first forecast is drawn: on the city's wet streets at the step
  // the lead-time scrub shows (15 cm or more, else 5 cm), by the console's densest-7-km rule - not
  // on the whole AOI. Settled, so the camera re-frames when the water has moved somewhere else and
  // not at every step; and like every map, only while nobody has moved it. Before the forecast,
  // and with the depth layer off, the map frames the layers it has, as it always did.
  const cityBox = CITY_BOUNDS[city] ?? CITY_BOUNDS[ONBOARD_DEFAULT_CITY];
  const wetFrameNext = useMemo(() => {
    if (!shownDepth || !show.depth || wet.length === 0 || !cityBox) return null;
    const frame = affectedFrame({ streets: wet, step: depthStep, fallback: cityBox });
    return frame.basis === "aoi" ? null : frame.bounds;
  }, [shownDepth, show.depth, wet, depthStep, cityBox]);
  const wetFrame = useSettledBounds(wetFrameNext);

  const start = useCallback(async () => {
    setStarting(true);
    setError(null);
    try {
      asked.current.clear();
      setRoads(null);
      setStreetsFailed(false);
      setBuildings(null);
      setDrains(null);
      setDrainsTotal(null);
      setDepth(null);
      setNoRun(false);
      setPicked(null);
      setJob(await startOnboard(city, designStorm));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setStarting(false);
    }
  }, [city, designStorm]);

  const running = live?.status === "running" || live?.status === "queued" || starting;
  // A run for the city was actually read. Nothing else proves the first forecast exists.
  const hasRun = shownDepth !== null;
  const href = openRunId && (finished || previous || hasRun) ? consoleHref(city, openRunId) : null;

  const steps = toSteps(job, hasRun);
  const lines = toLines(job);
  const lastAttempt = job?.status === "none" ? job.lastAttempt : null;

  const stepsCaption = live
    ? live.fromRecord
      ? "Recorded by the API when this build ended. Each time is the pipeline's own."
      : "Each step's time is the pipeline's own."
    : previous
      ? `${previousBuildLabel(previous)}.${
          previous.seeded ? " Recorded where this deployment was packed, not on this server." : ""
        }${
          lastAttempt && lastAttempt.status === "failed"
            ? ` A later build failed${lastAttempt.failedStep ? ` at ${lastAttempt.failedStep}` : ""}.`
            : ""
        }`
      : cached
        ? hasRun
          ? `${name} is already built. These six steps ran before this session, and no record of them was kept.`
          : noRun
            ? `${name}'s layers are already built, and the API serves no forecast for it yet. Start onboarding ${name} to run its first one.`
            : `${name}'s layers are already built.`
        : "Every step reports its own progress and elapsed time.";

  // One line each: the steps caption above already names which build this is.
  const logCaption = previous
    ? "Lines from the previous build, each with the time it was captured."
    : live?.log
      ? live.logTotal !== null && live.logTotal > live.log.length
        ? `The last ${live.log.length} of ${live.logTotal} lines, each with its capture time.`
        : "The pipeline's own lines, each with the time it was captured."
      : "Real lines from services/city, never scripted copy.";

  const cardNote = live
    ? finished && live.firstRunId
      ? `Built in ${formatSeconds(live.elapsedS)}${live.fromRecord ? ", before the API restarted" : " in this session"}.`
      : finished
        ? `The build finished without a first forecast${live.designStorm ? `: ${live.designStorm} is not on this API` : ""}.`
        : null
    : previous
      ? previous.firstRunExists === false && previous.firstRunId
        ? `The previous build's first run, ${previous.firstRunId}, is not on this API.`
        : `From the previous build, ${previous.startedAt ? formatDateTime(previous.startedAt) : "recorded by the API"}.`
      : cached && hasRun
        ? `Newest ${name} run, built before this session.`
        : null;

  const layerState: Record<WizardLayerId, WizardLayerState> = {
    streets: { on: show.streets, count: segments?.length },
    buildings: {
      on: show.buildings,
      count: buildings?.length,
      // Written with the streets; not fetched until switched on.
      lazy: reached > LAYER_AFTER.buildings,
      loading: show.buildings && buildings === null && reached > LAYER_AFTER.buildings,
    },
    // The count is every pipe the pipeline wrote; the note says when the map draws fewer.
    drains: {
      on: show.drains,
      count: drains ? (drainsTotal ?? drains.length) : undefined,
      detail:
        drains && drainsTotal !== null && drainsTotal > drains.length
          ? `${formatCount(drains.length)} drawn`
          : undefined,
    },
    depth: {
      on: show.depth,
      count: shownDepth ? shownDepth.depthCm.size : undefined,
      detail: atLabel ?? undefined,
    },
  };
  const hasLayers = (segments?.length ?? 0) > 0 || (drains?.length ?? 0) > 0;

  const scrub =
    shownDepth && leads.length > 1 ? (
      <div className="space-y-1">
        <div className="flex items-baseline justify-between gap-3">
          <label htmlFor="onboard-lead" className="type-micro text-text-3">
            Lead time on the map
          </label>
          <span className="num type-small text-text" aria-hidden="true">
            {atLabel}
          </span>
        </div>
        <input
          id="onboard-lead"
          type="range"
          min={0}
          max={leads.length - 1}
          step={1}
          value={depthStep}
          aria-valuetext={atLabel ?? undefined}
          onChange={(event) =>
            setPicked({
              runId: shownDepth.provenance.runId,
              step: Number(event.currentTarget.value),
            })
          }
          className="accent-tide focus-visible:ring-tide/50 h-5 w-full cursor-pointer rounded-full focus-visible:ring-3 focus-visible:outline-none"
        />
      </div>
    ) : null;

  return (
    <AppShell>
      <div className="flex h-full min-h-0 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        <div className="border-line flex shrink-0 flex-col gap-4 border-b p-5 lg:min-h-0 lg:w-[420px] lg:overflow-hidden lg:border-r lg:border-b-0">
          <div className="space-y-2">
            <PageHeader
              title={navItem("onboard").label}
              screen={navItem("onboard")}
              description={`City in a box: open data in, digital twin out. ${name} in minutes.`}
              honesty="Inferred drain graph"
            />
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <Button onClick={() => void start()} disabled={running} aria-busy={running}>
                <MapPinned aria-hidden="true" />
                {running ? `Building ${name}` : `Start onboarding ${name}`}
              </Button>
              <p className="type-micro text-text-3 min-w-0 flex-1">
                {running
                  ? `${Math.round((live?.progress ?? 0) * 100)} % built, ${formatSeconds(live?.elapsedS ?? 0)} elapsed.${
                      streamed > 0
                        ? ` ${streamed} progress ${streamed === 1 ? "event" : "events"} over the live socket.`
                        : ""
                    }`
                  : `Runs from city/cache/${city}: open data already downloaded.`}
              </p>
            </div>
          </div>

          {error ? (
            <p className="rounded-control border-line bg-well type-small text-text-2 border p-3">
              {error}
            </p>
          ) : null}

          <PanelErrorBoundary title="Onboarding steps">
            <section aria-labelledby="onboard-steps-title" className="shrink-0 space-y-1.5">
              <div className="space-y-0.5">
                <h2 id="onboard-steps-title" className="type-small text-text font-medium">
                  Steps
                </h2>
                <p className="type-micro text-text-2">{stepsCaption}</p>
              </div>
              {/* Each row's detail line is shown where the column has the height for it: at
                  1366 x 768 six of them pushed the log below the fold. Below lg the page scrolls,
                  so they always show. */}
              <OnboardingSteps
                steps={steps}
                detailClassName="lg:hidden lg:[@media(min-height:860px)]:block"
              />
            </section>
          </PanelErrorBoundary>

          <PanelErrorBoundary title="Pipeline log">
            <section
              aria-labelledby="onboard-log-title"
              className="flex flex-col gap-1.5 lg:min-h-[184px] lg:flex-1"
            >
              <div className="space-y-0.5">
                <h2 id="onboard-log-title" className="type-small text-text font-medium">
                  Pipeline log
                </h2>
                <p className="type-micro text-text-2">{logCaption}</p>
              </div>
              <LogStream
                lines={lines}
                // A recorded build's lines are dimmed: they are its own, but they are not
                // happening now.
                className={
                  previous
                    ? "[&>div]:text-text-3 lg:h-auto lg:min-h-[160px] lg:flex-1"
                    : "lg:h-auto lg:min-h-[160px] lg:flex-1"
                }
                emptyDescription={
                  cached
                    ? `Nothing has run in this session and no build was recorded. Start onboarding ${name} to rebuild it and stream the pipeline's own lines.`
                    : "Logs stream here when the wizard runs."
                }
              />
            </section>
          </PanelErrorBoundary>
        </div>

        <PanelErrorBoundary title="Onboarding map">
          <div className="relative min-h-[44rem] min-w-0 flex-1 lg:min-h-0">
            {hasLayers ? (
              <>
                {/* Behind the map, as on the console: it hosts the depth legend section 6.7 keeps
                    on screen whenever streets are coloured by depth, and the credit line. No
                    replay chip - this city's first forecast is a design storm. */}
                <MapSlot replayChip={false} emptyState={null} />
                <CityMap
                  attribution={false}
                  frames={shownDepth && show.depth ? shownDepth.frames : []}
                  rasterBounds={shownDepth && show.depth ? shownDepth.bounds : null}
                  baseSegments={show.streets ? (segments ?? []) : []}
                  segments={show.depth ? wet : []}
                  surcharge={[]}
                  hotspots={[]}
                  buildings={show.buildings ? (buildings ?? []) : []}
                  drains={show.drains ? (drains ?? []) : []}
                  bounds={cityBox}
                  fitBounds={wetFrame}
                  showDrains={(drains?.length ?? 0) > 0 && show.drains}
                  showRaster={shownDepth !== null && show.depth}
                  showSurcharge={false}
                  showBuildings={(buildings?.length ?? 0) > 0 && show.buildings}
                  step={depthStep}
                  layerFade={{
                    streets: fadeStreets,
                    buildings: fadeBuildings,
                    drains: fadeDrains,
                    raster: fadeDepth,
                  }}
                />
                <div className="absolute top-4 left-4 z-10">
                  <OnboardLayers
                    value={layerState}
                    onChange={(key, next) => setShow((s) => ({ ...s, [key]: next }))}
                  />
                </div>
              </>
            ) : (
              // No "Reconstructed replay" chip: this city's first forecast is a design storm. And a
              // built city whose streets are still on their way is not "not built": saying "Press
              // Start" over a finished record sent the operator to rebuild what was on disk.
              <MapSlot
                replayChip={false}
                emptyState={
                  reached <= LAYER_AFTER.streets
                    ? {
                        title: `No ${name} layers yet`,
                        description: `Press Start onboarding ${name}.`,
                      }
                    : streetsFailed
                      ? {
                          title: `${name}'s streets did not load`,
                          description: `The API did not serve /v1/city/${city}/layers/segments. Reload once it answers.`,
                        }
                      : {
                          title: `Loading ${name}'s streets`,
                          description: `The layers this build wrote are on their way from the API.`,
                        }
                }
              />
            )}
            {/* Bottom-left, clear of the Layers panel above it and the credit line below it; with
                no map yet it sits above the empty slot's own chip. */}
            <div
              className={
                hasLayers
                  ? "absolute bottom-10 left-4 z-10 max-w-[calc(100%-2rem)]"
                  : "absolute bottom-20 left-4 z-10 max-w-[calc(100%-2rem)]"
              }
            >
              <OnboardFinishCard
                cityName={name}
                facts={facts}
                href={href}
                note={cardNote}
                factsOpacity={fadeFacts}
                scrub={scrub}
              />
            </div>
          </div>
        </PanelErrorBoundary>
      </div>
    </AppShell>
  );
}

/**
 * City-in-a-box jobs (`POST /v1/onboard`, `GET /v1/onboard/{id}`; SPEC.md 7.9, 12).
 *
 * The wizard polls rather than relying only on the WebSocket. A build is minutes long and the
 * one thing that must not happen on stage is a progress bar that stops moving because a socket
 * dropped; polling every second costs one small request and is the state of record.
 *
 * **What the API reports, and what it used to.** A job now carries every log line with the instant
 * the tap captured it (`log`), each of the six steps with its own milliseconds and whether it was
 * only loaded from the city folder (`steps`), the first forecast's stages and the numbers read from
 * its run (`forecast`), and - with no job in the API's memory - the city's last recorded build
 * (`previous`, from `city/<city>/onboard_last.json`). An API older than that sends none of these,
 * so every one parses to null and the wizard falls back to what it could say before: the step the
 * job is on, its elapsed time, and the last forty lines without their own times.
 *
 * The schemas are hand-written and lenient (the contract is regenerated from the API): a field that
 * does not parse is dropped on its own rather than failing the whole job, because a wizard that
 * stops polling over one malformed detail line would stop moving on stage.
 */

import { z } from "zod";

import { apiUrl } from "@/lib/api/client";

export type OnboardStatus = "none" | "queued" | "running" | "finished" | "failed";

/** The six steps the wizard shows, in order (SPEC.md 7.9). */
export type OnboardStepId =
  | "choose_area"
  | "fetch_open_data"
  | "condition_terrain"
  | "infer_drains"
  | "build_graph"
  | "first_forecast";

export const ONBOARD_STEP_IDS: readonly OnboardStepId[] = [
  "choose_area",
  "fetch_open_data",
  "condition_terrain",
  "infer_drains",
  "build_graph",
  "first_forecast",
];

/** The first forecast's stages, in the order `run_cycle` runs them (SPEC.md 11.11). */
export const FORECAST_STAGES = ["sky", "twin", "pulse", "flash", "products"] as const;
export type ForecastStage = (typeof FORECAST_STAGES)[number];

/** A step's or a stage's state as the API reports it. `skipped` is Flash on a design storm. */
export type OnboardStepStatus = "waiting" | "running" | "done" | "failed" | "skipped";

const StepStatus = z.enum(["waiting", "running", "done", "failed", "skipped"]).catch("waiting");
const finite = z.number().refine(Number.isFinite);

/** One captured pipeline line, stamped with the instant the API's tap captured it. */
const LogEntrySchema = z.object({
  ts: z.string(),
  text: z.string(),
  level: z.string().nullish(),
});

export interface OnboardLogEntry {
  ts: string;
  text: string;
  level: "info" | "warn" | "error";
}

const StageSchema = z.object({ status: StepStatus, ms: finite.nullish() });

const StepSchema = z.object({
  status: StepStatus,
  ms: finite.nullish(),
  detail: z.string().nullish(),
  loaded_from_disk: z.boolean().nullish(),
  progress: finite.nullish(),
  stages: z.record(z.string(), StageSchema).nullish().catch(null),
  stage_ms: z.record(z.string(), finite).nullish().catch(null),
});

export interface OnboardStage {
  status: OnboardStepStatus;
  ms: number | null;
}

export interface OnboardStepRecord {
  status: OnboardStepStatus;
  /** The pipeline's own milliseconds for this step; for a running step, its time so far. */
  ms: number | null;
  /** One line from the pipeline's own stats, e.g. "34,410 roads, 72,573 buildings from OSM". */
  detail: string | null;
  /** Every pipeline step behind this row was read from the city folder rather than computed. */
  loadedFromDisk: boolean;
  /** 0 to 1 within the step. */
  progress: number;
  /** First forecast only: Sky, Twin, Pulse, Flash and products as they ran. */
  stages: Partial<Record<ForecastStage, OnboardStage>> | null;
}

export type OnboardSteps = Partial<Record<OnboardStepId, OnboardStepRecord>>;

const StormSchema = z.object({
  id: z.string().nullish(),
  total_mm: finite.nullish(),
  duration_min: finite.nullish(),
  peak_mm_h: finite.nullish(),
  source: z.string().nullish(),
});

const ForecastSchema = z.object({
  run_id: z.string(),
  cycle_ts: z.string().nullish(),
  n_steps: finite.nullish(),
  step_min: finite.nullish(),
  streets_total: finite.nullish(),
  wet_streets: finite.nullish(),
  wet_threshold_cm: finite.nullish(),
  median_peak_cm: finite.nullish(),
  max_peak_cm: finite.nullish(),
  stage_ms: z.record(z.string(), finite).nullish().catch(null),
  forecast_ms: finite.nullish(),
  mass_balance_err: finite.nullish(),
  storm: StormSchema.nullish().catch(null),
});

/** The design storm as the finish card states it. `source` says where the numbers were read. */
export interface OnboardStorm {
  id: string | null;
  totalMm: number | null;
  durationMin: number | null;
  peakMmH: number | null;
  /** "manifest": the bundle's own design storm. "run": the AOI-mean rain the run recorded. */
  source: "manifest" | "run" | null;
}

/** What the first forecast produced, every number read from the run directory it wrote. */
export interface OnboardForecast {
  runId: string;
  cycleTs: string | null;
  streetsTotal: number | null;
  wetStreets: number | null;
  wetThresholdCm: number | null;
  medianPeakCm: number | null;
  maxPeakCm: number | null;
  stageMs: Record<string, number>;
  forecastMs: number | null;
  storm: OnboardStorm | null;
}

/** A build that is not running in this API process: the last one `onboard_last.json` recorded. */
export interface OnboardBuild {
  jobId: string | null;
  status: OnboardStatus;
  designStorm: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  elapsedS: number | null;
  firstRunId: string | null;
  /** False when the record names a run this API no longer holds; null when the API did not say. */
  firstRunExists: boolean | null;
  /** Shipped with the deployment rather than built on the machine serving it. */
  seeded: boolean;
  error: string | null;
  failedStep: string | null;
  steps: OnboardSteps | null;
  forecast: OnboardForecast | null;
  log: OnboardLogEntry[];
}

/** A later build that did not finish, recorded beside the good one it did not replace. */
export interface OnboardAttempt {
  jobId: string | null;
  status: OnboardStatus;
  startedAt: string | null;
  elapsedS: number | null;
  error: string | null;
  failedStep: string | null;
}

export interface OnboardJob {
  jobId: string | null;
  city: string;
  status: OnboardStatus;
  step: OnboardStepId;
  /** 0 to 1 across the whole build. */
  progress: number;
  startedAt: string | null;
  finishedAt: string | null;
  elapsedS: number;
  /** The pipeline's own log lines, newest last, as text alone. Never composed here. */
  logTail: string[];
  /** Every kept line with its capture time; null from an API that does not send them. */
  log: OnboardLogEntry[] | null;
  /** Lines captured in all, which exceeds `log.length` once the API's 400-line cap is reached. */
  logTotal: number | null;
  /** Per wizard step, from the pipeline's own timings; null from an older API. */
  steps: OnboardSteps | null;
  forecast: OnboardForecast | null;
  firstRunId: string | null;
  error: string | null;
  failedStep: string | null;
  designStorm: string | null;
  /** Answered from the persisted record because the API restarted since the job ran. */
  fromRecord: boolean;
  /** Whether `city/<city>/segments.parquet` already exists, so a built city reads as built. */
  built: boolean;
  /** With no job in the API's memory: the city's last recorded build, or null when none was. */
  previous: OnboardBuild | null;
  lastAttempt: OnboardAttempt | null;
}

const STATUSES: readonly OnboardStatus[] = ["none", "queued", "running", "finished", "failed"];

function status(value: unknown): OnboardStatus {
  return STATUSES.includes(value as OnboardStatus) ? (value as OnboardStatus) : "none";
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function level(value: string | null | undefined): OnboardLogEntry["level"] {
  if (value === "error" || value === "critical") return "error";
  if (value === "warning" || value === "warn") return "warn";
  return "info";
}

export function parseLog(value: unknown): OnboardLogEntry[] | null {
  if (!Array.isArray(value)) return null;
  const out: OnboardLogEntry[] = [];
  for (const item of value) {
    const parsed = LogEntrySchema.safeParse(item);
    if (parsed.success) {
      out.push({ ts: parsed.data.ts, text: parsed.data.text, level: level(parsed.data.level) });
    }
  }
  return out;
}

export function parseSteps(value: unknown): OnboardSteps | null {
  if (!value || typeof value !== "object") return null;
  const out: OnboardSteps = {};
  for (const id of ONBOARD_STEP_IDS) {
    const parsed = StepSchema.safeParse((value as Record<string, unknown>)[id]);
    if (!parsed.success) continue;
    const row = parsed.data;
    let stages: OnboardStepRecord["stages"] = null;
    if (row.stages) {
      stages = {};
      for (const stage of FORECAST_STAGES) {
        const entry = row.stages[stage];
        if (entry) stages[stage] = { status: entry.status, ms: entry.ms ?? null };
      }
    }
    out[id] = {
      status: row.status,
      ms: row.ms ?? null,
      detail: row.detail ?? null,
      loadedFromDisk: row.loaded_from_disk ?? false,
      progress: row.progress ?? 0,
      stages,
    };
  }
  return Object.keys(out).length > 0 ? out : null;
}

export function parseForecast(value: unknown): OnboardForecast | null {
  const parsed = ForecastSchema.safeParse(value);
  if (!parsed.success) return null;
  const f = parsed.data;
  const storm = f.storm
    ? {
        id: f.storm.id ?? null,
        totalMm: f.storm.total_mm ?? null,
        durationMin: f.storm.duration_min ?? null,
        peakMmH: f.storm.peak_mm_h ?? null,
        source:
          f.storm.source === "manifest" || f.storm.source === "run"
            ? (f.storm.source as "manifest" | "run")
            : null,
      }
    : null;
  return {
    runId: f.run_id,
    cycleTs: f.cycle_ts ?? null,
    streetsTotal: f.streets_total ?? null,
    wetStreets: f.wet_streets ?? null,
    wetThresholdCm: f.wet_threshold_cm ?? null,
    medianPeakCm: f.median_peak_cm ?? null,
    maxPeakCm: f.max_peak_cm ?? null,
    stageMs: f.stage_ms ?? {},
    forecastMs: f.forecast_ms ?? null,
    storm,
  };
}

function parseBuild(value: unknown): OnboardBuild | null {
  if (!value || typeof value !== "object") return null;
  const body = value as Record<string, unknown>;
  return {
    jobId: text(body.job_id),
    status: status(body.status),
    designStorm: text(body.design_storm),
    startedAt: text(body.started_at),
    finishedAt: text(body.finished_at),
    elapsedS: num(body.elapsed_s),
    firstRunId: text(body.first_run_id),
    firstRunExists: typeof body.first_run_exists === "boolean" ? body.first_run_exists : null,
    seeded: body.seeded === true,
    error: text(body.error),
    failedStep: text(body.failed_step),
    steps: parseSteps(body.steps),
    forecast: parseForecast(body.forecast),
    // A record stores its lines as `lines`; a served one as `log`. Either is the build's own.
    log: parseLog(body.lines ?? body.log) ?? [],
  };
}

function parseAttempt(value: unknown): OnboardAttempt | null {
  if (!value || typeof value !== "object") return null;
  const body = value as Record<string, unknown>;
  return {
    jobId: text(body.job_id),
    status: status(body.status),
    startedAt: text(body.started_at),
    elapsedS: num(body.elapsed_s),
    error: text(body.error),
    failedStep: text(body.failed_step),
  };
}

export function parseOnboardJob(body: Record<string, unknown>): OnboardJob {
  const step = body.step as OnboardStepId;
  return {
    jobId: text(body.job_id),
    city: String(body.city ?? ""),
    status: status(body.status),
    step: ONBOARD_STEP_IDS.includes(step) ? step : "choose_area",
    progress: num(body.progress) ?? 0,
    startedAt: text(body.started_at),
    finishedAt: text(body.finished_at),
    elapsedS: num(body.elapsed_s) ?? 0,
    logTail: Array.isArray(body.log_tail) ? body.log_tail.map(String) : [],
    log: parseLog(body.log),
    logTotal: num(body.log_total),
    steps: parseSteps(body.steps),
    forecast: parseForecast(body.forecast),
    firstRunId: text(body.first_run_id),
    error: text(body.error),
    failedStep: text(body.failed_step),
    designStorm: text(body.design_storm),
    fromRecord: body.from_record === true,
    built: Boolean(body.built),
    previous: parseBuild(body.previous),
    lastAttempt: parseAttempt(body.last_attempt),
  };
}

async function call(path: string, init?: RequestInit): Promise<OnboardJob> {
  const response = await fetch(apiUrl(path), init);
  const body = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    const envelope = body.error as { message?: string } | undefined;
    throw new Error(envelope?.message ?? `Onboarding failed: HTTP ${response.status}`);
  }
  return parseOnboardJob(body);
}

/**
 * Start a build, or rejoin the one already running for this city.
 *
 * `from_cache_only: false` lets the API fetch the city's open data when it is not already
 * cached. That is not a retreat from SPEC.md 7.9's offline requirement: on the demo laptop
 * `tools/prefetch_city_cache.py` has already run, so the API finds every tile present and
 * downloads nothing, and `VARUNA_OFFLINE=1` refuses the network whatever this flag says. It
 * only changes what happens on a cold cache - a hosted deployment, or a fresh clone - where
 * the alternative is not "offline", it is "fails".
 */
export function startOnboard(city: string, designStorm = "CHN-IDF-25yr"): Promise<OnboardJob> {
  return call("/v1/onboard", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ city, design_storm: designStorm, from_cache_only: false }),
  });
}

export function pollOnboard(jobId: string, signal?: AbortSignal): Promise<OnboardJob> {
  return call(`/v1/onboard/${encodeURIComponent(jobId)}`, { signal });
}

/** What the wizard asks on load, so a reopened tab rejoins a build already in flight. */
export function latestOnboard(city: string, signal?: AbortSignal): Promise<OnboardJob> {
  return call(`/v1/onboard/city/${encodeURIComponent(city)}`, { signal });
}

/** The rain a run was forced with, as `run.json` recorded it (AOI mean per step, mm/h). */
export interface RunRain {
  totalMm: number;
  durationMin: number;
  peakMmH: number;
}

/** Sum a run's AOI-mean hyetograph into the three numbers the finish card prints. */
export function summariseRain(rainMmH: readonly number[], stepMin: number): RunRain | null {
  const rain = rainMmH.filter((v) => Number.isFinite(v));
  if (rain.length === 0 || !(stepMin > 0)) return null;
  return {
    totalMm: (rain.reduce((a, b) => a + b, 0) * stepMin) / 60,
    durationMin: rain.length * stepMin,
    peakMmH: Math.max(...rain),
  };
}

/**
 * `GET /v1/runs/{id}`'s `rain_aoi_mm_h`, for a first forecast the API did not summarise - one
 * built before the API recorded builds. Null when the run carries no hyetograph.
 */
export async function loadRunRain(runId: string, signal?: AbortSignal): Promise<RunRain | null> {
  const response = await fetch(apiUrl(`/v1/runs/${encodeURIComponent(runId)}`), { signal });
  if (!response.ok) return null;
  const body = (await response.json()) as { rain_aoi_mm_h?: unknown; step_min?: unknown };
  const rain = Array.isArray(body.rain_aoi_mm_h)
    ? body.rain_aoi_mm_h.filter((v): v is number => typeof v === "number")
    : [];
  return summariseRain(rain, num(body.step_min) ?? 5);
}

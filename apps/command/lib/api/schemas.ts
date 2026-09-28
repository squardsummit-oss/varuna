/**
 * Zod schemas for the VARUNA API (SPEC.md section 10.3 run.json, section 11.11 bus
 * topics, section 12 API contract). These are deliberately lenient (`looseObject`) so the
 * console keeps rendering while the API grows; the generated `types.ts` (pnpm typegen) is
 * the strict contract and these schemas validate what the console actually relies on.
 */
import { z } from "zod";

/** Error envelope: every API error is `{ "error": { code, message, run_id } }`. */
export const ApiErrorEnvelope = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    run_id: z.string().nullish(),
  }),
});
export type ApiErrorEnvelope = z.infer<typeof ApiErrorEnvelope>;

/** `run.json` mode: a run is either computed live or served from a bake. */
export const RUN_MODES = ["live", "baked"] as const;
export type RunMode = (typeof RUN_MODES)[number];

/** System mode shown in the mode banner. */
export const SYSTEM_MODES = ["replay", "live", "degraded"] as const;
export type SystemMode = (typeof SYSTEM_MODES)[number];

export const RunVersions = z.looseObject({
  sky: z.string().optional(),
  twin: z.string().optional(),
  flash: z.string().optional(),
});

/** Mirrors `data/runs/<run_id>/run.json` (SPEC.md section 10.3). */
export const RunMeta = z.looseObject({
  run_id: z.string(),
  city: z.string(),
  cycle_ts: z.string(),
  radar_frame_ts: z.string().nullish(),
  versions: RunVersions.optional(),
  mode: z.string(),
  ensemble_n: z.number().int().nullish(),
  stage_ms: z.record(z.string(), z.number()).default({}),
  mass_balance_err: z.number().nullish(),
  bundle: z.string().nullish(),
  valid_ts: z.string().nullish(),
  step_min: z.number().nullish(),
  /** p10/p50/p90 of mean street depth per step, across members: the time bar's band (7.2). */
  aoi_depth_band: z
    .object({ p10: z.array(z.number()), p50: z.array(z.number()), p90: z.array(z.number()) })
    .nullish(),
});
export type RunMeta = z.infer<typeof RunMeta>;

/** `GET /v1/runs` may answer with a bare array or `{ runs: [...] }`; both normalise to an array. */
export const RunList = z
  .union([z.array(RunMeta), z.looseObject({ runs: z.array(RunMeta) })])
  .transform((value) => (Array.isArray(value) ? value : value.runs));
export type RunList = z.infer<typeof RunList>;

/** `GET /healthz`: liveness plus mode, bundle and the last run. */
export const Health = z.looseObject({
  status: z.string(),
  mode: z.string().nullish(),
  city: z.string().nullish(),
  bundle: z.string().nullish(),
  last_run: z.union([RunMeta, z.string()]).nullish(),
  version: z.string().nullish(),
  time: z.string().nullish(),
});
export type Health = z.infer<typeof Health>;

/** Stage names in cycle order (SPEC.md section 11.11). */
export const CYCLE_STAGES = [
  "decode",
  "sky",
  "twin",
  "flash",
  "pulse",
  "products",
  "route",
  "alerts",
  "publish",
] as const;
export type CycleStageName = (typeof CYCLE_STAGES)[number];

export const CycleStage = z.looseObject({
  name: z.string(),
  ms: z.number().nullish(),
  status: z.string().nullish(),
});
export type CycleStage = z.infer<typeof CycleStage>;

/** `GET /v1/cycle/status`: the live cycle and its per-stage timings. */
export const CycleStatus = z.looseObject({
  status: z.string(),
  run_id: z.string().nullish(),
  stage_ms: z.record(z.string(), z.number()).default({}),
  stages: z.array(CycleStage).optional(),
  current_stage: z.string().nullish(),
  started_at: z.string().nullish(),
  total_ms: z.number().nullish(),
  message: z.string().nullish(),
});
export type CycleStatus = z.infer<typeof CycleStatus>;

/** Topics relayed on `WS /v1/live` (SPEC.md section 11.11) plus the heartbeat pair. */
export const LIVE_TOPICS = [
  "runs.published",
  "cycle.stage",
  "alert.raised",
  "alert.updated",
  "alert.cleared",
  "obs.assimilated",
  "replay.clock",
  "onboard.progress",
  "ping",
  "pong",
] as const;
export type LiveTopic = (typeof LIVE_TOPICS)[number];

/** Every live event is `{ type, ts?, payload? }`. */
export const LiveEvent = z.looseObject({
  type: z.string(),
  ts: z.string().nullish(),
  payload: z.unknown().optional(),
});
export type LiveEvent = z.infer<typeof LiveEvent>;

/**
 * `GET /v1/replay/clock` and the payload of every `replay.clock` socket event: the one
 * simulation clock the console, the replay page and every other tab share.
 */
export const ReplayClock = z.looseObject({
  bundle_id: z.string(),
  sim_time: z.string(),
  playing: z.boolean(),
  speed: z.number(),
  t0: z.string(),
  t1: z.string(),
  cycle_index: z.number().int(),
  n_cycles: z.number().int().nullish(),
  mode: z.enum(RUN_MODES),
  last_run_id: z.string().nullish(),
  next_cycle_ts: z.string().nullish(),
  /** What the clock wants the operator to know, already written as UI copy. */
  note: z.string().nullish(),
  progress: z.number().nullish(),
});
export type ReplayClock = z.infer<typeof ReplayClock>;

/** One row of `GET /v1/replay/bundles`: a card on the replay page. */
export const ReplayBundle = z.looseObject({
  id: z.string(),
  city: z.string(),
  label: z.string(),
  t0: z.string(),
  t1: z.string(),
  seed: z.number().int(),
  /** False when the folder is still a stub: the card says "Generated by make bundle". */
  built: z.boolean().default(true),
  missing_members: z.array(z.string()).default([]),
  baked: z.boolean().default(false),
  baked_cycles: z.number().int().default(0),
  total_cycles: z.number().int(),
  sources_n: z.number().int().default(0),
  ground_truth_n: z.number().int().default(0),
  synthetic_notes: z.array(z.string()).default([]),
  description: z.string().nullish(),
});
export type ReplayBundle = z.infer<typeof ReplayBundle>;

export const ReplayBundleList = z.array(ReplayBundle);
export type ReplayBundleList = z.infer<typeof ReplayBundleList>;

/** One frame of a bundle's radar cube; `url` is an API-root-relative path to its PNG. */
export const RadarFrameRef = z.looseObject({
  index: z.number().int(),
  ts: z.string(),
  url: z.string(),
});
export type RadarFrameRef = z.infer<typeof RadarFrameRef>;

/**
 * The AOI as a rectangle in radar-cube pixels, top-left origin, `right`/`bottom` exclusive:
 * the storm domain is 60 km wide and the city takes a small window of it.
 */
export const RadarAoiPixels = z.looseObject({
  left: z.number(),
  top: z.number(),
  right: z.number(),
  bottom: z.number(),
  width: z.number(),
  height: z.number(),
});
export type RadarAoiPixels = z.infer<typeof RadarAoiPixels>;

/** One band of the rain ramp, so the legend is drawn from the tokens the PNG was coloured with. */
export const RainRampBand = z.looseObject({
  min_mm_h: z.number(),
  hex: z.string(),
  label: z.string(),
});
export type RainRampBand = z.infer<typeof RainRampBand>;

/** The 3-hour accumulation image and the honesty copy that belongs beside it. */
export const RadarAccumulation = z.looseObject({
  url: z.string(),
  label: z.string(),
  note: z.string(),
});
export type RadarAccumulation = z.infer<typeof RadarAccumulation>;

/**
 * `GET /v1/replay/bundles/{bundle_id}/radar`: everything the storm designer needs to animate a
 * bundle's radar frames. Bundle-scoped, so there is no `run_id` or `valid_ts` here.
 */
export const RadarPreview = z.looseObject({
  bundle_id: z.string(),
  label: z.string(),
  variable: z.string(),
  n_frames: z.number().int(),
  step_min: z.number(),
  t0: z.string(),
  width: z.number().int(),
  height: z.number().int(),
  frames: z.array(RadarFrameRef).default([]),
  aoi_px: RadarAoiPixels,
  ramp: z.array(RainRampBand).default([]),
  accumulation: RadarAccumulation,
});
export type RadarPreview = z.infer<typeof RadarPreview>;

/* ---------------------------------------------------------------------- rain nowcast (Sky)
 * `GET /v1/nowcast/rain` and `/v1/nowcast/rain/series` (SPEC.md sections 11.1 and 12): the
 * quantiles.zarr of one cycle in JSON. Both shapes carry the same provenance block, because the
 * run stamp above a chart has to say where its numbers came from.
 */

/** The nowcaster that produced the ensemble; the SPEC.md section 17 fallback is named, not hidden. */
export const NOWCASTERS = ["pysteps_steps", "fallback_steps"] as const;
export type Nowcaster = (typeof NOWCASTERS)[number];

export const NOWCASTER_LABELS: Record<Nowcaster, string> = {
  pysteps_steps: "pySTEPS STEPS",
  fallback_steps: "Fallback nowcaster (own advection, AR(2) and noise)",
};

/** The `Z = a R^b` relation the cycle came through (SPEC.md Appendix A). */
export const ZRRelation = z.looseObject({
  a: z.number(),
  b: z.number(),
  /** "adaptive" = fitted this cycle from gauge-radar pairs; "marshall_palmer" = the fallback. */
  source: z.string(),
  n_pairs: z.number().int().nullish(),
});
export type ZRRelation = z.infer<typeof ZRRelation>;

/** One forecast step of a rain series, in mm/h. */
export const RainStep = z.looseObject({
  valid_ts: z.string(),
  lead_min: z.number(),
  p10_mm_h: z.number(),
  p50_mm_h: z.number(),
  p90_mm_h: z.number(),
});
export type RainStep = z.infer<typeof RainStep>;

/** A step at one Sky pixel, with the two exceedance probabilities the pixel carries. */
export const RainPointStep = RainStep.extend({
  p_gt_20: z.number(),
  p_gt_40: z.number(),
});
export type RainPointStep = z.infer<typeof RainPointStep>;

/** One ensemble member's area-of-interest-mean hyetograph, one value per step. */
export const RainMemberSeries = z.looseObject({
  member: z.number().int(),
  mm_h: z.array(z.number()).default([]),
});
export type RainMemberSeries = z.infer<typeof RainMemberSeries>;

/**
 * Provenance on every rain response. `run_id` is null in exactly one case: the cycle was computed
 * on demand and never published as a run, and `mode` then reads "live".
 */
const rainProduct = {
  run_id: z.string().nullable(),
  valid_ts: z.string(),
  mode: z.string(),
  bundle: z.string().nullish(),
  city: z.string(),
  n_members: z.number().int(),
  n_steps: z.number().int(),
  step_min: z.number(),
  nowcaster: z.string().nullish(),
  seed: z.number().int().nullish(),
  zr: ZRRelation.nullish(),
  stage_ms: z.record(z.string(), z.number()).default({}),
  /** The run's honesty labels, printed verbatim (SPEC.md rule 6). */
  notes: z.array(z.string()).default([]),
  peak_p50_mm_h: z.number().nullish(),
  peak_ts: z.string().nullish(),
};

/** `GET /v1/nowcast/rain`: every member's AOI-mean hyetograph and the band across them. */
export const RainNowcast = z.looseObject({
  ...rainProduct,
  steps: z.array(RainStep).default([]),
  members: z.array(RainMemberSeries).default([]),
});
export type RainNowcast = z.infer<typeof RainNowcast>;

/** Where a fan chart was sampled, and the 500 m Sky pixel the point fell in. */
export const RainPoint = z.looseObject({
  lon: z.number(),
  lat: z.number(),
  name: z.string(),
  hotspot_id: z.string().nullish(),
  /** Where the register's coordinate was verified against; the honesty label for the point. */
  source_url: z.string().nullish(),
  row: z.number().int(),
  col: z.number().int(),
  res_m: z.number(),
});
export type RainPoint = z.infer<typeof RainPoint>;

/** `GET /v1/nowcast/rain/series`: the fan chart at one junction. */
export const RainPointSeries = z.looseObject({
  ...rainProduct,
  point: RainPoint,
  steps: z.array(RainPointStep).default([]),
  exceedance_mm_h: z.array(z.number()).default([]),
});
export type RainPointSeries = z.infer<typeof RainPointSeries>;

export const CycleStagePayload = z.looseObject({
  stage: z.string(),
  ms: z.number().nullish(),
  status: z.string().nullish(),
  run_id: z.string().nullish(),
});
export type CycleStagePayload = z.infer<typeof CycleStagePayload>;

export const OnboardProgressPayload = z.looseObject({
  job: z.string(),
  step: z.string(),
  progress: z.number().min(0).max(1).optional(),
  elapsed_s: z.number().nullish(),
  log: z.string().nullish(),
  done: z.boolean().optional(),
});
export type OnboardProgressPayload = z.infer<typeof OnboardProgressPayload>;

/** Depth chips on the report flow map to centimetres with a stated uncertainty (section 11.6). */
export const DEPTH_HINTS = ["ankle", "knee", "waist"] as const;
export type DepthHint = (typeof DEPTH_HINTS)[number];
export const DEPTH_HINT_CM: Record<DepthHint, { cm: number; sd: number; label: string }> = {
  ankle: { cm: 10, sd: 8, label: "Ankle" },
  knee: { cm: 45, sd: 12, label: "Knee" },
  waist: { cm: 90, sd: 15, label: "Waist" },
};

/** `POST /v1/reports` body. */
export const ReportInput = z.object({
  ts: z.string(),
  lat: z.number().min(-90).max(90),
  lon: z.number().min(-180).max(180),
  depth_hint: z.enum(DEPTH_HINTS),
  text: z.string().max(280).optional(),
  photo_data_url: z.string().optional(),
  source: z.string().default("public-map"),
});
export type ReportInput = z.infer<typeof ReportInput>;

/** `POST /v1/reports` response; `feedback_streets` is Pulse's "improved the forecast for N streets". */
export const ReportResponse = z.looseObject({
  id: z.string().nullish(),
  accepted: z.boolean().optional(),
  feedback_streets: z.number().int().nullish(),
  message: z.string().nullish(),
});
export type ReportResponse = z.infer<typeof ReportResponse>;

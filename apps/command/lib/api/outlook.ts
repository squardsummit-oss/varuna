/**
 * Today's live outlook (`GET /v1/outlook`): Open-Meteo rain for the next three hours through the
 * reduced-order emulator, street by street.
 *
 * **What it is, in the API's own words.** Every other run the console draws is the reconstructed
 * replay of 2 July 2019. This is the one forecast whose clock is *today*: a weather model's rain
 * for one grid cell, applied evenly to every street, run through Flash-lite at 50 members. It is
 * not a radar nowcast, and `method` says so; a screen that shows it prints `method`, the source
 * line and the skill rather than paraphrasing them.
 *
 * **Depth is above the emulator's base state.** The API removes the base state (a training
 * window's tide and upstream runoff) because with no rain at all it would put about a thousand
 * streets above 5 cm. `baselineRemoved` carries what was taken out so a screen can say it.
 *
 * **Four outcomes and no fifth**, as with the weather client: a served outlook (fresh or stale,
 * with its age and whether its window has passed); `refused` when the city has no emulator
 * (Chennai, 422), with the API's sentence; `unavailable` with the reason for anything else. None
 * of them produces a depth the API did not send.
 *
 * The schema is hand-written against `services/api/varuna_api/routers/outlook.py` until the team
 * regenerates `types.ts`; it is loose so an added field does not break the console.
 */
import { z } from "zod";

import { apiFetch, errorMessage, isApiError } from "@/lib/api/client";
import { formatIstDate, formatIstTime, parseIso } from "@/lib/stores/time";

const Point = z.looseObject({ lon: z.number(), lat: z.number() });

export const OutlookSourceSchema = z.looseObject({
  name: z.string(),
  url: z.string(),
  licence: z.string(),
  licence_url: z.string().optional(),
  attribution: z.string().optional(),
  fetched_at: z.string(),
  age_s: z.number(),
  stale: z.boolean(),
  grid_cell_km: z.number(),
  grid_point: Point.optional(),
  series: z.enum(["hourly", "minutely_15"]),
  series_note: z.string(),
});

export const OutlookSegmentSchema = z.looseObject({
  segment_id: z.string(),
  name: z.string().nullable(),
  /** OSM's name, else "off <street>" or "<class> near <place>"; absent from an older API. */
  display_name: z.string().optional(),
  p50_cm: z.array(z.number()),
  p90_cm: z.array(z.number()),
  p_gt_15: z.array(z.number()),
  p_gt_30: z.array(z.number()),
  p_gt_45: z.array(z.number()),
  peak_p50_cm: z.number(),
  peak_p90_cm: z.number(),
  peak_ts: z.string(),
});

const OutlookStreetSchema = z.looseObject({
  segment_id: z.string(),
  name: z.string().nullable(),
  display_name: z.string().optional(),
  peak_p50_cm: z.number(),
  peak_p90_cm: z.number(),
  peak_ts: z.string(),
});

export const OutlookSchema = z.looseObject({
  city: z.string(),
  mode: z.literal("outlook"),
  run_id: z.null().optional(),
  issued_at: z.string(),
  valid_from: z.string(),
  valid_to: z.string(),
  expired: z.boolean(),
  steps_min: z.number(),
  n_steps: z.number(),
  valid_ts: z.array(z.string()),
  rain_mm_h: z.array(z.number()),
  rain_total_mm: z.number(),
  members: z.number(),
  source: OutlookSourceSchema,
  segments: z.array(OutlookSegmentSchema),
  n_segments_over_1cm: z.number(),
  truncated: z.boolean(),
  summary: z.looseObject({
    max_cm: z.number(),
    max_p90_cm: z.number(),
    n_ge_5: z.number(),
    n_ge_15: z.number(),
    n_ge_30: z.number(),
    worst: z.array(OutlookStreetSchema),
    sentence: z.string(),
  }),
  baseline_removed: z.looseObject({
    n_ge_5: z.number(),
    n_ge_15: z.number(),
    n_ge_30: z.number(),
    max_cm: z.number(),
  }),
  blockage: z.looseObject({
    kind: z.enum(["pulse_posterior", "city_prior"]),
    run_id: z.string().nullable(),
    from_posterior: z.number(),
    from_prior: z.number(),
    flat: z.number(),
  }),
  skill: z.looseObject({
    rmse_cm: z.number(),
    csi_30cm: z.number(),
    n_training_runs: z.number(),
    fitted_segments: z.number(),
    n_segments: z.number(),
  }),
  no_rain_response: z.number(),
  unmatched_streets: z.number().optional(),
  notes: z.array(z.string()),
  method: z.string(),
  compute_ms: z.number(),
  cached: z.boolean(),
});

export type Outlook = z.infer<typeof OutlookSchema>;
export type OutlookSegment = z.infer<typeof OutlookSegmentSchema>;

/** What a screen has: a served outlook, a refusal for this city, or nothing and the reason. */
export type OutlookState =
  | { kind: "loading" }
  | { kind: "ready"; outlook: Outlook }
  | { kind: "refused"; reason: string }
  | { kind: "unavailable"; reason: string };

/** The console's city until the switcher threads one through. */
export const DEFAULT_OUTLOOK_CITY = "mumbai";

/**
 * How long an outlook is waited for. A cold call can reach Open-Meteo (6 s timeout, one retry)
 * and then run 50 emulator members (measured 1.2-2.7 s warm, about 4 s on a process's first
 * call), so a shorter ceiling would report "unavailable" over an API that was about to answer.
 */
export const OUTLOOK_TIMEOUT_MS = 25_000;

/**
 * Load the outlook for `city`, or say why there is none. Never throws.
 *
 * A 422 is the API saying this city has no emulator (Chennai); it is kept apart from a failure
 * because the screen should say "not available for this city", not "try again".
 */
export async function loadOutlook(
  city = DEFAULT_OUTLOOK_CITY,
  signalOrOptions: AbortSignal | { signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<OutlookState> {
  // `loadOutlook(city, signal)` and `loadOutlook(city, { signal, timeoutMs })` both work.
  const options =
    typeof AbortSignal !== "undefined" && signalOrOptions instanceof AbortSignal
      ? { signal: signalOrOptions }
      : (signalOrOptions as { signal?: AbortSignal; timeoutMs?: number });
  try {
    const outlook = await apiFetch<Outlook>("/v1/outlook", {
      query: { city },
      schema: OutlookSchema,
      timeoutMs: options.timeoutMs ?? OUTLOOK_TIMEOUT_MS,
      signal: options.signal,
    });
    return { kind: "ready", outlook };
  } catch (error) {
    if (isApiError(error) && error.code === "aborted") {
      return { kind: "unavailable", reason: "The outlook request was cancelled." };
    }
    if (isApiError(error) && error.status === 422) {
      return { kind: "refused", reason: error.message };
    }
    return {
      kind: "unavailable",
      reason: errorMessage(
        error,
        "The live outlook could not be loaded. The replay on this screen does not depend on it.",
      ),
    };
  }
}

/**
 * Median depth per street at one step, for a map layer. Only the listed streets are in the map;
 * every other street is below 1 cm at p90 throughout, which the caller draws as dry.
 */
export function outlookDepthAt(outlook: Outlook, step: number): Map<string, number> {
  const depths = new Map<string, number>();
  const index = Math.max(0, Math.min(step, outlook.n_steps - 1));
  for (const segment of outlook.segments) {
    const value = segment.p50_cm[index];
    if (typeof value === "number" && Number.isFinite(value)) depths.set(segment.segment_id, value);
  }
  return depths;
}

/**
 * How old the weather copy is, in seconds. With `nowMs` it is counted from `fetched_at`, so a card
 * left open keeps an honest age; without it, it is the API's `age_s` at the time it answered.
 */
export function outlookAgeSeconds(outlook: Outlook, nowMs?: number): number {
  const fetched = parseIso(outlook.source.fetched_at);
  if (nowMs !== undefined && fetched) return Math.max(0, (nowMs - fetched.getTime()) / 1000);
  return outlook.source.age_s;
}

/** "just now", "4 min ago", "2 h ago"; "age unknown" when the API sent nothing usable. */
export function formatOutlookAge(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "age unknown";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.floor(minutes / 60)} h ago`;
}

/**
 * "Open-Meteo hourly rain, fetched 4 min ago (CC BY 4.0)", built from what the API sent. A
 * sentence rather than a dotted meta string (SPEC.md 6.8).
 */
export function outlookSourceLine(outlook: Outlook, nowMs?: number): string {
  const { name, series, licence } = outlook.source;
  const kind = series === "minutely_15" ? "15-minute" : "hourly";
  const age = formatOutlookAge(outlookAgeSeconds(outlook, nowMs));
  const fetched = age === "age unknown" ? "fetched at an unknown time" : `fetched ${age}`;
  return `${name} ${kind} rain, ${fetched}${licence ? ` (${licence})` : ""}`;
}

/** The three hours every full outlook covers, as the API's `N_STEPS * STEP_MIN`. */
export const OUTLOOK_FULL_HORIZON_MIN = 180;

/**
 * Minutes the outlook actually covers. The API stops the series at the first five-minute step no
 * hourly rain covers and never pads it, so a null hour from Open-Meteo or a copy fetched well
 * before `current.time` serves fewer than 36 steps.
 */
export function outlookHorizonMin(outlook: Outlook): number {
  return Math.max(0, outlook.n_steps * outlook.steps_min);
}

/**
 * "the next 3 h" for a full outlook, "the next 100 min" for a short one: the API's own wording in
 * `summary.sentence`, so a card never says three hours beside a sentence that says fewer.
 */
export function outlookSpanLabel(outlook: Outlook): string {
  const minutes = outlookHorizonMin(outlook);
  return minutes === OUTLOOK_FULL_HORIZON_MIN ? "the next 3 h" : `the next ${minutes} min`;
}

/**
 * "14:10 to 17:10 IST, 26 Sep 2026": the outlook's own window, on today's clock. Printed wherever
 * the outlook sits beside the replay, whose clock is 2 July 2019.
 */
export function outlookWindowLabel(outlook: Outlook): string {
  const from = formatIstTime(outlook.valid_from);
  const to = formatIstTime(outlook.valid_to);
  const date = formatIstDate(outlook.valid_from);
  if (!parseIso(outlook.valid_from) || !parseIso(outlook.valid_to)) return "window unknown";
  return `${from} to ${to} IST${date ? `, ${date}` : ""}`;
}

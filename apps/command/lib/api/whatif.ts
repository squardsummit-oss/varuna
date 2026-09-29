/**
 * What-if against a baked run (`POST /v1/whatif`; SPEC.md 7.7).
 *
 * The level in every number here is the Twin's own forecast for the run; the emulator supplies
 * only the difference the scenario makes. The response carries the emulator's measured skill so
 * the screen can print it beside the answer rather than implying one it does not have.
 */

import { z } from "zod";

import { apiFetch, apiUrl } from "@/lib/api/client";

export interface WhatIfSegment {
  segmentId: string;
  /** The run's peak, or 0 when the run had the street below 5 cm (`dryBefore`). */
  beforeCm: number;
  afterCm: number;
  deltaCm: number;
  /**
   * The run lists no depth for this street because it stayed below 5 cm, so its change is read
   * from 0 cm. The screen says "below 5 cm" rather than printing the 0 as a measurement.
   */
  dryBefore: boolean;
  /** OSM street name, carried on `largestChanges` only; null for an unnamed way. */
  name?: string | null;
  /** The name to print on `largestChanges`: OSM's, else "off <street>" or "<class> near
   * <place>" (the API's `street_names`). Absent from an API that predates it. */
  displayName?: string;
}

/** One ranked hotspot from the run's `hotspots.json`, before and after the scenario. */
export interface WhatIfHotspot {
  hotspotId: string;
  name: string;
  beforeCm: number;
  afterCm: number;
  deltaCm: number;
  /** Minutes above 30 and 45 cm over the 3-hour window, keyed "30" and "45". */
  minutesAboveBefore: Record<string, number>;
  minutesAboveAfter: Record<string, number>;
  segments: number;
  segmentsMissing: number;
}

/** What the emulator did with the tide lever: nothing, and where the tide is answered instead. */
export interface WhatIfTide {
  requestedM: number;
  needsTwin: boolean;
  message: string | null;
}

/** The pump-plan and clean-top levers as the endpoint applied them. */
export interface WhatIfLevers {
  pumpPlan: { applied: boolean; label: string; reason?: string; segmentsDrained?: number } | null;
  cleanTop: {
    applied: boolean;
    label: string;
    reason?: string;
    pipes?: { edgeId: string; betaBefore: number }[];
    segmentsChanged?: number;
  } | null;
}

export interface WhatIfResult {
  runId: string;
  /** How the number was made, e.g. "twin_level_emulator_delta". */
  method: string;
  /**
   * The emulator's measured skill on held-out storms, or null when it did not run: a scenario
   * with no lever set is answered as "Nothing changed" without loading it, and a skill of 0 printed
   * beside that would be a number nothing measured (SPEC.md rule 6).
   */
  emulator: { rmseCm: number; csi30cm: number; nTrainingRuns: number } | null;
  nImproved: number;
  nWorse: number;
  /** Streets whose peak moved by 0.5 cm or more; always `segments.length`. */
  nChanged: number;
  nNewlyWet: number;
  /** Of the changed streets, how many the run had below 5 cm. */
  nDryBefore: number;
  /** "Nothing changed." or "N segments deeper, M shallower, each by 0.5 cm or more", from the endpoint. */
  summary: string;
  nothingChanged: boolean;
  tide: WhatIfTide;
  ms: number;
  /** Every street that changed by 0.5 cm or more, largest change first. Not truncated. */
  segments: WhatIfSegment[];
  /** Per hotspot of the run's register, largest change first. */
  hotspots: WhatIfHotspot[];
  /** The 25 largest changes, with street names. */
  largestChanges: WhatIfSegment[];
  worstAfter: WhatIfSegment[];
  /** The pump-plan and clean-top levers as applied; each null when not asked for. */
  levers: WhatIfLevers;
  /**
   * The ids the run actually cleaned, echoed by the handler. Not the ids asked for: an id this
   * city has no segment for is dropped, so the screen prints what was cleaned rather than what
   * was requested (SPEC.md rule 6).
   */
  cleanedSegments: string[];
  /** Ids asked for that are not road segments in this city, so the operator can see the gap. */
  cleanedUnmatched: string[];
  notes: string[];
}

/**
 * How many segments a deep link carries. Section 7.7's cleaning lever is "clean top 14", so
 * fourteen is the size the copy and the demo are written around; a hotspot with more segments
 * than that sends its first fourteen and the lab says how many it got.
 */
export const MAX_CLEANED_SEGMENTS = 14;

/**
 * What the request carries. The pump plan and "clean top 14" are levers of the endpoint now; a
 * tide offset is carried but not applied by the emulator, and the response's `tide` says so.
 */
export interface WhatIfScenario {
  rainScale: number;
  tideOffsetM: number;
  /**
   * The baked cycle the question is about. Omitted, the handler answers about the newest run,
   * and on this replay that is 09:10 IST - after the storm, where the whole AOI is nearly dry
   * and a rain scenario has almost nothing to move (SPEC.md 12: every response carries a
   * `run_id`, and the screen prints the one it got back).
   */
  runId?: string;
  /**
   * Road-segment ids to clean (blockage to 0.05 on the pipe under each). Road segments, not
   * drain edges: the two vocabularies are disjoint and the endpoint refuses a body of edge ids
   * with 422. A hotspot's `segmentIds` are the ids this takes.
   */
  cleanedSegments?: string[];
  /** Run the cycle's own pump plan from each pump's arrival (a lower bound, labelled). */
  pumpPlan?: boolean;
  /** Desilt the worst pipes of the run's learned blockage, city-wide: `true` is fourteen. */
  cleanTop?: boolean | number;
}

function toSegments(rows: Record<string, unknown>[] | undefined): WhatIfSegment[] {
  return (rows ?? []).map((r) => ({
    segmentId: String(r.segment_id ?? ""),
    beforeCm: Number(r.before_cm ?? 0),
    afterCm: Number(r.after_cm ?? 0),
    deltaCm: Number(r.delta_cm ?? 0),
    dryBefore: r.before_cm === null,
    ...("name" in r ? { name: r.name === null ? null : String(r.name) } : {}),
    ...(typeof r.display_name === "string" && r.display_name
      ? { displayName: r.display_name }
      : {}),
  }));
}

function toMinutes(value: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [key, minutes] of Object.entries((value ?? {}) as Record<string, unknown>)) {
    out[key] = Number(minutes ?? 0);
  }
  return out;
}

function toHotspots(rows: Record<string, unknown>[] | undefined): WhatIfHotspot[] {
  return (rows ?? []).map((r) => ({
    hotspotId: String(r.hotspot_id ?? ""),
    name: String(r.name ?? r.hotspot_id ?? ""),
    beforeCm: Number(r.before_cm ?? 0),
    afterCm: Number(r.after_cm ?? 0),
    deltaCm: Number(r.delta_cm ?? 0),
    minutesAboveBefore: toMinutes(r.minutes_above_before),
    minutesAboveAfter: toMinutes(r.minutes_above_after),
    segments: Number(r.segments ?? 0),
    segmentsMissing: Number(r.segments_missing ?? 0),
  }));
}

function toLevers(value: unknown): WhatIfLevers {
  const levers = (value ?? {}) as Record<string, Record<string, unknown> | null>;
  const pump = levers.pump_plan ?? null;
  const top = levers.clean_top ?? null;
  return {
    pumpPlan: pump
      ? {
          applied: Boolean(pump.applied),
          label: String(pump.label ?? ""),
          ...(pump.reason ? { reason: String(pump.reason) } : {}),
          ...(pump.segments_drained !== undefined
            ? { segmentsDrained: Number(pump.segments_drained) }
            : {}),
        }
      : null,
    cleanTop: top
      ? {
          applied: Boolean(top.applied),
          label: String(top.label ?? ""),
          ...(top.reason ? { reason: String(top.reason) } : {}),
          ...(Array.isArray(top.pipes)
            ? {
                pipes: (top.pipes as Record<string, unknown>[]).map((pipe) => ({
                  edgeId: String(pipe.edge_id ?? ""),
                  betaBefore: Number(pipe.beta_before ?? 0),
                })),
              }
            : {}),
          ...(top.segments_changed !== undefined
            ? { segmentsChanged: Number(top.segments_changed) }
            : {}),
        }
      : null,
  };
}

/**
 * One line per lever the endpoint applied or declined, in its own words: the tide it left out, the
 * "clean top 14" pipes and the pump plan with its lower-bound label. The lab and the console
 * drawer print the same lines, so the two cannot describe one answer differently.
 */
export function leverLines(result: WhatIfResult): string[] {
  const lines: string[] = [];
  if (result.tide.needsTwin && result.tide.message) lines.push(result.tide.message);
  const top = result.levers.cleanTop;
  if (top) {
    const pipes = (top.pipes ?? []).length;
    lines.push(
      top.applied
        ? `${top.label}: ${pipes} pipe${pipes === 1 ? "" : "s"} cleaned to 0.05, ${(top.segmentsChanged ?? 0).toLocaleString("en-IN")} segments run at a lower blockage.`
        : `${top.label}: not applied. ${top.reason ?? ""}`.trim(),
    );
  }
  const pump = result.levers.pumpPlan;
  if (pump) {
    lines.push(
      pump.applied
        ? `Pump plan: ${(pump.segmentsDrained ?? 0).toLocaleString("en-IN")} segments drained from each pump's arrival. ${pump.label} Synthetic pump inventory.`
        : `Pump plan not applied: ${pump.reason ?? ""}`.trim(),
    );
  }
  return lines;
}

/** Run a scenario. Throws with the API's own message when it refuses one. */
export async function runWhatIf(
  scenario: WhatIfScenario,
  signal?: AbortSignal,
): Promise<WhatIfResult> {
  const response = await fetch(apiUrl("/v1/whatif"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      // Undefined is dropped by `JSON.stringify`, which is the request the handler reads as
      // "the newest run" - the same default the cycle picker starts on.
      run_id: scenario.runId,
      rain_scale: scenario.rainScale,
      tide_offset_m: scenario.tideOffsetM,
      // Cleaning is expressed as road-segment ids in the API. Absent, an empty list is the
      // honest request: the response's notes then say the scenario was rain only.
      cleaned_segments: scenario.cleanedSegments ?? [],
      // Absent unless asked for, so a request from an older screen is the request it always was.
      ...(scenario.pumpPlan ? { pump_plan: true } : {}),
      ...(scenario.cleanTop ? { clean_top: scenario.cleanTop } : {}),
    }),
  });

  const body = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    const envelope = body.error as { message?: string } | undefined;
    throw new Error(envelope?.message ?? `What-if failed: HTTP ${response.status}`);
  }
  const tide = (body.tide ?? {}) as Record<string, unknown>;

  const emulator = (body.emulator ?? null) as Record<string, number> | null;
  return {
    runId: String(body.run_id ?? ""),
    method: String(body.method ?? ""),
    emulator:
      emulator && typeof emulator.rmse_cm === "number"
        ? {
            rmseCm: Number(emulator.rmse_cm),
            csi30cm: Number(emulator.csi_30cm ?? 0),
            nTrainingRuns: Number(emulator.n_training_runs ?? 0),
          }
        : null,
    nImproved: Number(body.n_improved ?? 0),
    nWorse: Number(body.n_worse ?? 0),
    nChanged: Number(body.n_changed ?? 0),
    nNewlyWet: Number(body.n_newly_wet ?? 0),
    nDryBefore: Number(body.n_dry_before ?? 0),
    summary: String(body.summary ?? ""),
    nothingChanged: Boolean(body.nothing_changed),
    tide: {
      requestedM: Number(tide.requested_m ?? scenario.tideOffsetM),
      needsTwin: Boolean(tide.needs_twin),
      message: tide.message ? String(tide.message) : null,
    },
    ms: Number(body.ms ?? 0),
    segments: toSegments(body.segments as Record<string, unknown>[]),
    hotspots: toHotspots(body.hotspots as Record<string, unknown>[]),
    largestChanges: toSegments(body.largest_changes as Record<string, unknown>[]),
    worstAfter: toSegments(body.worst_after as Record<string, unknown>[]),
    levers: toLevers(body.levers),
    cleanedSegments: (body.cleaned_segments as string[]) ?? [],
    cleanedUnmatched: (body.cleaned_unmatched as string[]) ?? [],
    notes: (body.notes as string[]) ?? [],
  };
}

/** One junction the physics check compared: the emulator's change against the Twin's. */
export interface PhysicsCheckHotspot {
  hotspotId: string;
  name: string;
  emulatorDeltaCm: number;
  twinDeltaCm: number;
  /** `|emulator - Twin|`, the number the check is about. */
  diffCm: number;
}

/**
 * `POST /v1/whatif/physics-check` (SPEC.md 7.7, P7.8): the same scenario re-run on the coupled
 * Twin over a window around the run's worst junction, compared as changes and not as levels.
 */
export interface PhysicsCheckResult {
  runId: string;
  /**
   * True for a tide scenario: it has no emulator answer to check, so nothing was compared and
   * `agrees` is false only because there is no second model. `twinJob` is the job to start.
   */
  runsOnTwin: boolean;
  twinJob: { endpoint: string; cached: string | null; expectedMs: number | null } | null;
  /** Section 7.7's sentence, from the endpoint: "Emulator vs physics: max difference ...". */
  summary: string;
  toleranceCm: number;
  agrees: boolean;
  maxDiffCm: number | null;
  maxDiffHotspot: string | null;
  hotspots: PhysicsCheckHotspot[];
  /** Junctions ranked high enough to check that fell outside the one window, named. */
  outside: string[];
  /**
   * `cleanedEdgesInside`: how many of the cleaned pipes the window holds. The top 14 are ranked
   * city-wide and the window is 990 m, so a check can agree about pipes it never saw.
   */
  window: {
    sizeM: number;
    nodes: number;
    edges: number;
    centre: string;
    cleanedEdgesInside: number;
  };
  /** What-if levers the Twin could not run, "pump_plan" or "clean_top"; each named in `notes`. */
  leversNotChecked: string[];
  /** Pipes cleaned in the scenario, by name or as the top 14; compare `window.cleanedEdgesInside`. */
  cleanedEdges: number;
  massBalance: { baseline: number; scenario: number; budget: number };
  ms: number;
  budgetMs: number;
  notes: string[];
}

/** Run the physics check on a scenario. Throws with the API's own message when it refuses one. */
export async function runPhysicsCheck(
  scenario: WhatIfScenario,
  signal?: AbortSignal,
): Promise<PhysicsCheckResult> {
  const response = await fetch(apiUrl("/v1/whatif/physics-check"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      run_id: scenario.runId,
      rain_scale: scenario.rainScale,
      tide_offset_m: scenario.tideOffsetM,
      cleaned_segments: scenario.cleanedSegments ?? [],
      // The same levers the what-if ran, so the check compares one scenario. The endpoint
      // cleans the top pipes in both models and names the pump plan in `levers_not_checked`.
      ...(scenario.pumpPlan ? { pump_plan: true } : {}),
      ...(scenario.cleanTop ? { clean_top: scenario.cleanTop } : {}),
    }),
  });

  const body = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    const envelope = body.error as { message?: string } | undefined;
    throw new Error(envelope?.message ?? `Physics check failed: HTTP ${response.status}`);
  }

  const window = (body.window ?? {}) as Record<string, unknown>;
  const balance = (body.mass_balance ?? {}) as Record<string, unknown>;
  const optionalNumber = (value: unknown) =>
    value === null || value === undefined ? null : Number(value);
  const twinJob = (body.twin_job ?? null) as Record<string, unknown> | null;
  return {
    runId: String(body.run_id ?? ""),
    runsOnTwin: Boolean(body.runs_on_twin),
    twinJob: twinJob
      ? {
          endpoint: String(twinJob.endpoint ?? ""),
          cached: twinJob.cached ? String(twinJob.cached) : null,
          expectedMs: optionalNumber(twinJob.expected_ms),
        }
      : null,
    summary: String(body.summary ?? ""),
    toleranceCm: Number(body.tolerance_cm ?? 5),
    agrees: Boolean(body.agrees),
    maxDiffCm: optionalNumber(body.max_diff_cm),
    maxDiffHotspot: body.max_diff_hotspot ? String(body.max_diff_hotspot) : null,
    hotspots: ((body.hotspots as Record<string, unknown>[]) ?? []).map((r) => ({
      hotspotId: String(r.hotspot_id ?? ""),
      name: String(r.name ?? r.hotspot_id ?? ""),
      emulatorDeltaCm: Number(r.emulator_delta_cm ?? 0),
      twinDeltaCm: Number(r.twin_delta_cm ?? 0),
      diffCm: Number(r.diff_cm ?? 0),
    })),
    outside: (body.hotspots_outside_window as string[]) ?? [],
    window: {
      sizeM: Number(window.size_m ?? 0),
      nodes: Number(window.nodes ?? 0),
      edges: Number(window.edges ?? 0),
      centre: String(window.centre_hotspot ?? ""),
      cleanedEdgesInside: Number(window.cleaned_edges_inside ?? 0),
    },
    leversNotChecked: ((body.levers_not_checked as unknown[]) ?? []).map(String),
    cleanedEdges: Array.isArray(body.cleaned_edges) ? body.cleaned_edges.length : 0,
    massBalance: {
      baseline: Number(balance.baseline ?? 0),
      scenario: Number(balance.scenario ?? 0),
      budget: Number(balance.budget ?? 1e-3),
    },
    ms: Number(body.ms ?? 0),
    budgetMs: Number(body.budget_ms ?? 10_000),
    notes: (body.notes as string[]) ?? [],
  };
}

// ================================================== the full-city Twin scenario (the tide lever)
//
// `POST /v1/whatif/twin` runs one full-city coupled Twin at the cycle's own inputs with the
// scenario applied - the only engine that has a sea level - as a job of about a minute. The
// schemas are hand-written against `services/api/varuna_api/routers/whatif.py` until the team
// regenerates `types.ts`; they are loose so an added field does not break the console.

const MinutesAbove = z.record(z.string(), z.number());

export const TwinScenarioSegmentSchema = z.looseObject({
  segment_id: z.string(),
  /** Null when the run lists the street as dry (below 5 cm): its before depth is not stored. */
  before_cm: z.number().nullable(),
  after_cm: z.number(),
  delta_cm: z.number().nullable(),
  newly_wet: z.boolean(),
  minutes_above_before: MinutesAbove,
  minutes_above_after: MinutesAbove,
});

export const TwinScenarioHotspotSchema = z.looseObject({
  hotspot_id: z.string().nullable(),
  name: z.string().nullable(),
  lon: z.number().nullable().optional(),
  lat: z.number().nullable().optional(),
  before_cm: z.number().nullable(),
  after_cm: z.number(),
  delta_cm: z.number().nullable(),
  minutes_above_before: MinutesAbove,
  minutes_above_after: MinutesAbove,
  segments: z.number(),
  segments_missing: z.number(),
  run_peak_cm: z.number().nullable().optional(),
});

export const TwinScenarioResultSchema = z.looseObject({
  run_id: z.string(),
  /** "twin_full_aoi". */
  method: z.string(),
  /** "prior": the blockage the run's own Twin level was computed at. */
  blockage: z.string(),
  scenario: z.looseObject({
    rain_scale: z.number(),
    tide_offset_m: z.number(),
    cleaned_segments: z.array(z.string()),
    cleaned_no_pipe: z.array(z.string()),
    cleaned_unmatched: z.array(z.string()),
    cleaned_edges: z.number(),
  }),
  segments: z.array(TwinScenarioSegmentSchema),
  n_segments_compared: z.number(),
  n_changed: z.number(),
  n_worse: z.number(),
  n_improved: z.number(),
  n_newly_wet: z.number(),
  newly_above: MinutesAbove,
  max_abs_delta_cm: z.number(),
  hotspots: z.array(TwinScenarioHotspotSchema),
  hotspots_moved: z.number(),
  sea: z.looseObject({
    offset_m: z.number(),
    source: z.string().nullable(),
    /**
     * Net face exchange between the sea cells and the land, positive inland. It is not the sea
     * that entered the city on its own: the pipes are the other path, `outfall_m3` (negative
     * when seawater is pushed up the drains).
     */
    sea_to_land_m3: z.number(),
    /** The same against the Twin with nothing changed, so the tide offset's own share. */
    sea_to_land_change_m3: z.number(),
    /**
     * The clamp's gross tallies on the sea cells: the sea's own rise and fall, never the sea
     * that entered the city. Carried for the ledger, not quoted.
     */
    tide_in_m3: z.number(),
    tide_out_m3: z.number(),
    outfall_m3: z.number(),
    volume_in_m3: z.number(),
    volume_in_change_m3: z.number().nullable(),
  }),
  /**
   * What the scenario is compared with: the same Twin with nothing changed, on this server's code
   * and city, and how far that run is from the bake's own map (`drift`).
   */
  baseline: z
    .looseObject({
      source: z.string(),
      twin_ms: z.number().nullable().optional(),
      drift: z.looseObject({
        n_changed: z.number(),
        max_abs_delta_cm: z.number(),
        hotspots_moved: z.number(),
        n_unsampled: z.number(),
      }),
    })
    .optional(),
  timings: z.looseObject({ baseline_ms: z.number().nullable().optional() }).optional(),
  mass_balance: z.looseObject({
    error_fraction: z.number(),
    budget: z.number(),
    within_budget: z.boolean(),
  }),
  rain_reproduces_run: z.boolean(),
  twin_ms: z.number(),
  ms: z.number(),
  notes: z.array(z.string()),
  computed_at: z.string().optional(),
});

export const TwinScenarioJobSchema = z.looseObject({
  job_id: z.string(),
  run_id: z.string(),
  state: z.enum(["running", "done", "failed", "cancelled"]),
  /** "queued", "sky", "baseline", "twin", "sampling" or "done". */
  stage: z.string(),
  step: z.number(),
  /** Output steps in the job, the comparison run's included when it has to be made first. */
  n_steps: z.number(),
  /** Of `n_steps`, the first ones belong to the Twin with nothing changed; 0 when it is cached. */
  baseline_steps: z.number().optional(),
  elapsed_ms: z.number(),
  /** The run's own Sky and Twin stage timings: what this job repeats. Null when unrecorded. */
  expected_ms: z.number().nullable(),
  expected_from: z.string(),
  scenario: z.looseObject({
    rain_scale: z.number(),
    tide_offset_m: z.number(),
    cleaned_segments: z.array(z.string()),
    notes: z.array(z.string()),
  }),
  error: z.looseObject({ code: z.string(), message: z.string() }).nullable(),
  cache: z
    .looseObject({
      source: z.enum(["computed", "memory", "disk", "shipped"]),
      label: z.string(),
      computed_at: z.string().nullable().optional(),
    })
    .nullable(),
  result: TwinScenarioResultSchema.nullable(),
});

export type TwinScenarioJob = z.infer<typeof TwinScenarioJobSchema>;
export type TwinScenarioResult = z.infer<typeof TwinScenarioResultSchema>;

/**
 * Start a scenario on the full-city Twin. A scenario already computed for this run comes back
 * with `state: "done"` and the result; otherwise the job is running and is polled with
 * {@link getTwinScenario}. Throws the API's own sentence when it refuses (gate, range, busy).
 */
export function startTwinScenario(
  scenario: WhatIfScenario,
  signal?: AbortSignal,
): Promise<TwinScenarioJob> {
  return apiFetch("/v1/whatif/twin", {
    method: "POST",
    signal,
    body: {
      run_id: scenario.runId,
      rain_scale: scenario.rainScale,
      tide_offset_m: scenario.tideOffsetM,
      cleaned_segments: scenario.cleanedSegments ?? [],
      // The Twin runs neither; sent so the job names them in `levers_left_out` and its notes
      // rather than answering as if they had never been asked for.
      ...(scenario.pumpPlan ? { pump_plan: true } : {}),
      ...(scenario.cleanTop ? { clean_top: scenario.cleanTop } : {}),
    },
    schema: TwinScenarioJobSchema,
  });
}

/**
 * `GET /v1/whatif/twin?run_id=`: whether this API runs the full-city Twin, and the stored answers
 * it can serve for a run. A cache-only API (`VARUNA_WHATIF_TWIN=0`, the deployed one) answers only
 * `answers`; `tide_offsets_m` are the tides it can give at rain 1.0x with nothing cleaned.
 */
export const TwinOfferSchema = z.looseObject({
  run_id: z.string(),
  enabled: z.boolean(),
  answers: z.array(
    z.looseObject({
      rain_scale: z.number(),
      tide_offset_m: z.number(),
      cleaned: z.boolean(),
      source: z.string(),
    }),
  ),
  tide_offsets_m: z.array(z.number()),
  elsewhere: z
    .array(
      z.looseObject({
        run_id: z.string(),
        cycle: z.string(),
        tide_offsets_m: z.array(z.number()),
      }),
    )
    .optional(),
  message: z.string(),
});

export type TwinOffer = z.infer<typeof TwinOfferSchema>;

/** What this API's Twin what-if can answer for a run. Throws when the API predates the route. */
export function getTwinOffer(runId: string | undefined, signal?: AbortSignal): Promise<TwinOffer> {
  return apiFetch("/v1/whatif/twin", {
    signal,
    query: runId ? { run_id: runId } : undefined,
    schema: TwinOfferSchema,
  });
}

/**
 * Whether a cache-only API holds the Twin's answer to this exact question: its rain and tide, and
 * nothing cleaned by name. Null means it does; otherwise the one line the lab prints instead of
 * starting a Twin run the API would refuse. An API that runs the Twin answers everything.
 */
export function twinCannotAnswer(
  offer: TwinOffer | null,
  scenario: Pick<WhatIfScenario, "rainScale" | "tideOffsetM" | "cleanedSegments">,
): string | null {
  if (!offer || offer.enabled || scenario.tideOffsetM === 0) return null;
  const same = (a: number, b: number) => Math.abs(a - b) < 1e-9;
  // Stored answers with streets cleaned are keyed on the pipes, which the lab cannot match from
  // here, so only a question with nothing cleaned is offered from the store.
  const cleanedNone = (scenario.cleanedSegments ?? []).length === 0;
  const held =
    cleanedNone &&
    offer.answers.some(
      (answer) =>
        !answer.cleaned &&
        same(answer.rain_scale, scenario.rainScale) &&
        same(answer.tide_offset_m, scenario.tideOffsetM),
    );
  if (held) return null;
  return offer.tide_offsets_m.length > 0
    ? "The tide is answered here only at rain 1.0x with no pipes cleaned."
    : offer.message;
}

/**
 * The tide offsets a lab offers on this API: every one when it runs the Twin (null: the slider),
 * else the run's own tide and the stored answers for this cycle.
 */
export function tideStopsFor(offer: TwinOffer | null): number[] | null {
  if (!offer || offer.enabled) return null;
  return [0, ...offer.tide_offsets_m.filter((metres) => metres !== 0)].sort((a, b) => a - b);
}

/** The tide lever's one line on a cache-only API, or undefined for the Twin's own note. */
export function tideOfferNote(offer: TwinOffer | null): string | undefined {
  if (!offer || offer.enabled) return undefined;
  if (offer.tide_offsets_m.length > 0) return "Stored Twin answers on this server, at rain 1.0x.";
  const elsewhere = (offer.elsewhere ?? []).map((row) => `${row.cycle} IST`);
  return elsewhere.length > 0
    ? `Tide answers are stored for ${elsewhere.join(", ")} only.`
    : "This server cannot run the Twin for the tide.";
}

/** One poll of a running scenario: its stage, step `k` of `n`, and the result once done. */
export function getTwinScenario(jobId: string, signal?: AbortSignal): Promise<TwinScenarioJob> {
  return apiFetch(`/v1/whatif/twin/${encodeURIComponent(jobId)}`, {
    signal,
    schema: TwinScenarioJobSchema,
  });
}

/** Stop a running scenario at its next output step; nothing it computed is stored. */
export function cancelTwinScenario(jobId: string): Promise<TwinScenarioJob> {
  return apiFetch(`/v1/whatif/twin/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    schema: TwinScenarioJobSchema,
  });
}

/** Where the Twin was when its step rate was first read: the elapsed ms and the step it was on. */
export interface TwinStepMark {
  elapsedMs: number;
  step: number;
}

/**
 * Seconds a running Twin job has left, or null when nothing measured says.
 *
 * From the job's own step rate once two polls have seen the Twin advance - this machine, now, with
 * whatever else is running on it. Before that, from the run's recorded Sky and Twin stage timings
 * (`expected_ms`), which were measured when the cycle was baked. With neither, no number: a guessed
 * countdown is a fake number (SPEC.md rule 6).
 */
export function twinSecondsLeft(job: TwinScenarioJob, mark: TwinStepMark | null): number | null {
  if (job.state !== "running") return null;
  // Both runs count on one series of steps, so a rate read in the comparison run holds through
  // the scenario's own.
  const stepping = job.stage === "twin" || job.stage === "baseline";
  if (stepping && mark && job.step > mark.step && job.n_steps > 0) {
    const perStep = (job.elapsed_ms - mark.elapsedMs) / (job.step - mark.step);
    return Math.max(0, (perStep * (job.n_steps - job.step)) / 1000);
  }
  if (job.expected_ms != null) return Math.max(0, (job.expected_ms - job.elapsed_ms) / 1000);
  return null;
}

/** "about 40 s left", "about 2 min left", "under 5 s left"; rounded so it does not flicker. */
export function formatSecondsLeft(seconds: number): string {
  if (seconds < 5) return "under 5 s left";
  if (seconds < 60) return `about ${Math.max(5, Math.round(seconds / 5) * 5)} s left`;
  const minutes = Math.round(seconds / 60);
  return `about ${minutes} min left`;
}

/**
 * One line for the M31 bar: what the job is doing, in its own terms. "Twin 17 of 36 steps, about
 * 40 s left" while the Twin runs; the Sky and sampling stages named as themselves.
 */
export function twinProgressLine(job: TwinScenarioJob, mark: TwinStepMark | null): string {
  const base = job.baseline_steps ?? 0;
  const own = job.n_steps - base;
  if (job.state === "done" && job.cache && job.cache.source !== "computed") {
    // Served from a store rather than run now: said as that, not as a run that just finished.
    return `Stored Twin answer: ${job.n_steps - base} steps, not rerun.`;
  }
  if (job.state === "done") {
    return base > 0
      ? `Twin finished: ${own} of ${own} steps, after the same run with nothing changed.`
      : `Twin finished: ${own} of ${own} steps.`;
  }
  if (job.state === "cancelled") return "Twin run cancelled. Nothing was stored.";
  if (job.state === "failed") return "Twin run failed.";
  const left = twinSecondsLeft(job, mark);
  const tail = left === null ? "" : `, ${formatSecondsLeft(left)}`;
  if (job.stage === "sky") return `Re-making this cycle's rain for the scenario${tail}`;
  if (job.stage === "baseline") {
    return `Twin with nothing changed, to compare with: ${job.step} of ${base} steps${tail}`;
  }
  if (job.stage === "twin") return `Twin ${Math.max(0, job.step - base)} of ${own} steps${tail}`;
  if (job.stage === "sampling") return "Twin done; comparing every street with the run";
  return `Twin job started${tail}`;
}

/** m3 as "1.26 Mm3" from a million up, and as whole cubic metres below it. */
export function formatVolumeM3(m3: number): string {
  if (Math.abs(m3) >= 1e6) return `${(m3 / 1e6).toFixed(2)} Mm3`;
  return `${Math.round(m3).toLocaleString("en-IN")} m3`;
}

/**
 * What a tide offset did on the full-city Twin, in one sentence: the sea that entered the city
 * against the same run with the tide as forecast, the streets newly above 30 cm (where cars
 * stop), and whether any hotspot moved. Every number is the job's own; a sea that moved nothing
 * says so.
 *
 * The sea is `sea_to_land_change_m3`, what the sea cells passed to land and pipes, net, less the
 * same with nothing changed. Never `tide_in_m3`, the clamp's gross tally on the sea cells, which
 * with a sea mask counts the whole sea's rise as if it had come ashore.
 */
export function twinTideOutcome(result: TwinScenarioResult): string {
  const change = result.sea.sea_to_land_change_m3;
  const streets = result.newly_above["30"] ?? 0;
  const moved = result.hotspots_moved;
  const sea =
    Math.abs(change) < 0.5
      ? "No more sea crossed onto the land than with the tide as forecast"
      : change > 0
        ? `${formatVolumeM3(change)} more sea crossed onto the land than with the tide as forecast`
        : `${formatVolumeM3(-change)} less sea crossed onto the land than with the tide as forecast`;
  const streetPart =
    streets === 0
      ? "no street newly impassable for cars (above 30 cm)"
      : `${streets.toLocaleString("en-IN")} street${streets === 1 ? "" : "s"} newly impassable for cars (above 30 cm)`;
  const hotspotPart =
    moved === 0
      ? "no hotspot moved by 0.5 cm or more"
      : `${moved} hotspot${moved === 1 ? "" : "s"} moved by 0.5 cm or more`;
  return `${sea}; ${streetPart}; ${hotspotPart}.`;
}

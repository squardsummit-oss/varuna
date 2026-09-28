/**
 * Rain skill by lead time (`GET /v1/verification/rain-skill`; SPEC.md 7.10, 11.12).
 *
 * Every number is computed by `services/verify/varuna_verify/rain_event.py` from the event's baked
 * rain products against the bundle's reconstructed truth field, pooled over cycles by lead time.
 * An event the scorer cannot score answers `available: false` with the reason, and that reason is
 * what the screen shows: nothing here is ever filled in by the client (rule 6).
 */

import { apiUrl } from "@/lib/api/client";

/** The three deterministic forecasts scored: ensemble mean, ensemble median, held analysis. */
export type RainForecastName = "mean" | "p50" | "persistence";

export const RAIN_FORECASTS: readonly RainForecastName[] = ["mean", "p50", "persistence"];

export interface RainContingency {
  hits: number;
  misses: number;
  falseAlarms: number;
  correctNegatives: number;
  csi: number | null;
  pod: number | null;
  far: number | null;
}

/** The p10-p90 of one score across cycles, each cycle scored on its own table. */
export interface ScoreSpread {
  /** Cycles with a defined score at this lead: the sample the band is taken over. */
  n: number;
  /** Null when fewer than three cycles have a defined score: no band is quoted. */
  p10: number | null;
  p90: number | null;
}

export type SkillMetric = "csi" | "pod" | "far";

export const SKILL_METRICS: readonly SkillMetric[] = ["csi", "pod", "far"];

/** The forecasts whose cycle-to-cycle spread the scorer reports. */
export type SpreadForecastName = "mean" | "p50";

export interface RainThresholdCell {
  /** Pixels whose truth rain rate is strictly above the threshold, pooled over cycles. */
  eventPixels: number;
  baseRate: number | null;
  forecasts: Record<RainForecastName, RainContingency>;
  brier: number | null;
  brierPersistence: number | null;
  brierClimatology: number | null;
  brierSkillVsPersistence: number | null;
  brierSkillVsClimatology: number | null;
  /** Spread across cycles for the ensemble mean and median; absent from an older scorer. */
  spread: Partial<Record<SpreadForecastName, Record<SkillMetric, ScoreSpread>>>;
}

export interface RainLeadRow {
  leadMin: number;
  /** Cycles whose forecast reaches this lead inside the truth window. */
  nCycles: number;
  /** Sky pixels scored at this lead, pooled over those cycles. */
  nPixels: number;
  maeMmH: Record<RainForecastName, number | null>;
  /** Keyed by the threshold in mm/h as the API writes it: "10", "20", "40". */
  thresholds: Record<string, RainThresholdCell>;
}

export interface ReliabilityBin {
  pFrom: number;
  pTo: number;
  n: number;
  meanP: number | null;
  observedFrequency: number | null;
}

export interface ReliabilityBand {
  leadFromMin: number;
  leadToMin: number;
  n: number;
  baseRate: number | null;
  reliability: number | null;
  resolution: number | null;
  uncertainty: number | null;
  bins: ReliabilityBin[];
}

export type HorizonStatus = "found" | "undetermined" | "beyond_scored_range" | "no_leads";

export interface SkillHorizon {
  thresholdMmH: number;
  forecast: RainForecastName;
  csiFloor: number;
  /** The last lead before the first failure; 0 when the first lead already fails. */
  leadMin: number | null;
  status: HorizonStatus;
  firstFailure: {
    leadMin: number;
    reasons: string[];
    csi: number | null;
    persistenceCsi: number | null;
    nCycles: number;
  } | null;
  /** Leads at which the forecast's CSI is strictly above persistence's. */
  beatsPersistenceLeadsMin: number[];
}

export interface RainCycleCurve {
  runId: string;
  cycleTs: string;
  leadsMin: number[];
  csiMean20: (number | null)[];
  csiPersistence20: (number | null)[];
}

export interface RainScope {
  label: string;
  nPixels: number;
  byLead: RainLeadRow[];
  reliability: Record<string, ReliabilityBand[]>;
  horizons: SkillHorizon[];
  perCycle: RainCycleCurve[];
}

export interface RainCycle {
  runId: string;
  cycleTs: string;
  nMembers: number;
  memberCube: boolean;
  productsSource: string;
  nLeads: number;
  nLeadsScored: number;
  maxLeadScoredMin: number | null;
  persistence: {
    frameTs: string | null;
    zrA: number | null;
    zrB: number | null;
    zrSource: string | null;
    /** Whether the recomputed Z-R equals the one the run published; null when it published none. */
    matchesCycle: boolean | null;
  };
}

export interface RainSkillScored {
  available: true;
  event: string;
  label: string;
  truth: { source: string; note: string; t0: string; t1: string; nFrames: number };
  units: Record<string, string>;
  definitions: Record<string, string>;
  thresholdsMmH: number[];
  headlineThresholdMmH: number;
  csiFloor: number;
  leadBandsMin: [number, number][];
  cycles: RainCycle[];
  nCycles: number;
  byScope: { aoi: RainScope; domain: RainScope };
  /** The headline: AOI, ensemble mean, 20 mm/h. */
  horizon: SkillHorizon | null;
  runsWithoutMemberCube: string[];
  skippedRuns: { runId: string; reason: string }[];
  unavailable: Record<string, string>;
  provenance: Record<string, unknown>;
  notes: string[];
}

/** What the scorer found missing: the truth field, any run with rain, or any run that loads. */
export type RainSkillMissing = "truth" | "runs" | "loadable_runs";

export interface RainSkillUnavailable {
  available: false;
  event: string;
  /** What happened, in the scorer's words. Never names a command. */
  reason: string;
  /** Null from an older scorer that did not say. */
  missing: RainSkillMissing | null;
  /** The one make target that supplies what is missing, as served; null when none was served. */
  command: string | null;
}

export type RainSkill = RainSkillScored | RainSkillUnavailable;

type Raw = Record<string, unknown>;

const num = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const count = (value: unknown): number => num(value) ?? 0;
const text = (value: unknown): string => (typeof value === "string" ? value : "");
const record = (value: unknown): Raw =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Raw) : {};
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

function contingency(raw: unknown): RainContingency {
  const c = record(raw);
  return {
    hits: count(c.hits),
    misses: count(c.misses),
    falseAlarms: count(c.false_alarms),
    correctNegatives: count(c.correct_negatives),
    csi: num(c.csi),
    pod: num(c.pod),
    far: num(c.far),
  };
}

function scoreSpread(raw: unknown): ScoreSpread {
  const s = record(raw);
  return { n: count(s.n), p10: num(s.p10), p90: num(s.p90) };
}

function spreads(raw: unknown): RainThresholdCell["spread"] {
  const out: RainThresholdCell["spread"] = {};
  const byForecast = record(raw);
  for (const name of ["mean", "p50"] as const) {
    if (!(name in byForecast)) continue;
    const metrics = record(byForecast[name]);
    out[name] = {
      csi: scoreSpread(metrics.csi),
      pod: scoreSpread(metrics.pod),
      far: scoreSpread(metrics.far),
    };
  }
  return out;
}

function thresholdCell(raw: unknown): RainThresholdCell {
  const c = record(raw);
  return {
    eventPixels: count(c.event_pixels),
    baseRate: num(c.base_rate),
    forecasts: {
      mean: contingency(c.mean),
      p50: contingency(c.p50),
      persistence: contingency(c.persistence),
    },
    brier: num(c.brier),
    brierPersistence: num(c.brier_persistence),
    brierClimatology: num(c.brier_climatology),
    brierSkillVsPersistence: num(c.brier_skill_vs_persistence),
    brierSkillVsClimatology: num(c.brier_skill_vs_climatology),
    spread: spreads(c.spread),
  };
}

function leadRow(raw: unknown): RainLeadRow {
  const r = record(raw);
  const mae = record(r.mae_mm_h);
  const thresholds: Record<string, RainThresholdCell> = {};
  for (const [key, cell] of Object.entries(record(r.thresholds))) {
    thresholds[key] = thresholdCell(cell);
  }
  return {
    leadMin: count(r.lead_min),
    nCycles: count(r.n_cycles),
    nPixels: count(r.n_pixels),
    maeMmH: { mean: num(mae.mean), p50: num(mae.p50), persistence: num(mae.persistence) },
    thresholds,
  };
}

function band(raw: unknown): ReliabilityBand {
  const b = record(raw);
  return {
    leadFromMin: count(b.lead_from_min),
    leadToMin: count(b.lead_to_min),
    n: count(b.n),
    baseRate: num(b.base_rate),
    reliability: num(b.reliability),
    resolution: num(b.resolution),
    uncertainty: num(b.uncertainty),
    bins: list(b.bins).map((item) => {
      const bin = record(item);
      return {
        pFrom: count(bin.p_from),
        pTo: count(bin.p_to),
        n: count(bin.n),
        meanP: num(bin.mean_p),
        observedFrequency: num(bin.observed_frequency),
      };
    }),
  };
}

const FORECAST_NAMES = new Set<string>(RAIN_FORECASTS);

function horizon(raw: unknown): SkillHorizon | null {
  if (raw === null || raw === undefined) return null;
  const h = record(raw);
  const failure = h.first_failure === null ? null : record(h.first_failure);
  const forecast = text(h.forecast);
  return {
    thresholdMmH: count(h.threshold_mm_h),
    forecast: (FORECAST_NAMES.has(forecast) ? forecast : "mean") as RainForecastName,
    csiFloor: count(h.csi_floor),
    leadMin: num(h.lead_min),
    status: (text(h.status) || "no_leads") as HorizonStatus,
    firstFailure: failure
      ? {
          leadMin: count(failure.lead_min),
          reasons: list(failure.reasons).map(text),
          csi: num(failure.csi),
          persistenceCsi: num(failure.persistence_csi),
          nCycles: count(failure.n_cycles),
        }
      : null,
    beatsPersistenceLeadsMin: list(h.beats_persistence_leads_min).map(count),
  };
}

function scope(raw: unknown): RainScope {
  const s = record(raw);
  const reliability: Record<string, ReliabilityBand[]> = {};
  for (const [key, bands] of Object.entries(record(s.reliability))) {
    reliability[key] = list(bands).map(band);
  }
  return {
    label: text(s.label),
    nPixels: count(s.n_pixels),
    byLead: list(s.by_lead)
      .map(leadRow)
      .sort((a, b) => a.leadMin - b.leadMin),
    reliability,
    horizons: list(s.horizons)
      .map(horizon)
      .filter((h): h is SkillHorizon => h !== null),
    perCycle: list(s.per_cycle).map((item) => {
      const c = record(item);
      return {
        runId: text(c.run_id),
        cycleTs: text(c.cycle_ts),
        leadsMin: list(c.leads_min).map(count),
        csiMean20: list(c.csi_mean_20).map(num),
        csiPersistence20: list(c.csi_persistence_20).map(num),
      };
    }),
  };
}

const MISSING = new Set<string>(["truth", "runs", "loadable_runs"]);

/** Parse a `/v1/verification/rain-skill` body. Exported for tests. */
export function parseRainSkill(body: Raw, event: string): RainSkill {
  if (body.available !== true) {
    const missing = text(body.missing);
    return {
      available: false,
      event: text(body.event) || event,
      reason: text(body.reason) || "The scorer returned no rain skill for this event.",
      missing: MISSING.has(missing) ? (missing as RainSkillMissing) : null,
      command: text(body.command) || null,
    };
  }
  const truth = record(body.truth);
  const byScope = record(body.by_scope);
  return {
    available: true,
    event: text(body.event) || event,
    label: text(body.label),
    truth: {
      source: text(truth.source),
      note: text(truth.note),
      t0: text(truth.t0),
      t1: text(truth.t1),
      nFrames: count(truth.n_frames),
    },
    units: record(body.units) as Record<string, string>,
    definitions: record(body.definitions) as Record<string, string>,
    thresholdsMmH: list(body.thresholds_mm_h).map(count),
    headlineThresholdMmH: count(body.headline_threshold_mm_h),
    csiFloor: count(body.csi_floor),
    leadBandsMin: list(body.lead_bands_min).map((pair) => {
      const [from, to] = list(pair).map(count);
      return [from ?? 0, to ?? 0] as [number, number];
    }),
    cycles: list(body.cycles).map((item) => {
      const c = record(item);
      const p = record(c.persistence);
      return {
        runId: text(c.run_id),
        cycleTs: text(c.cycle_ts),
        nMembers: count(c.n_members),
        memberCube: c.member_cube === true,
        productsSource: text(c.products_source),
        nLeads: count(c.n_leads),
        nLeadsScored: count(c.n_leads_scored),
        maxLeadScoredMin: num(c.max_lead_scored_min),
        persistence: {
          frameTs: text(p.frame_ts) || null,
          zrA: num(p.zr_a),
          zrB: num(p.zr_b),
          zrSource: text(p.zr_source) || null,
          matchesCycle: typeof p.matches_cycle === "boolean" ? p.matches_cycle : null,
        },
      };
    }),
    nCycles: count(body.n_cycles),
    byScope: { aoi: scope(byScope.aoi), domain: scope(byScope.domain) },
    horizon: horizon(body.horizon),
    runsWithoutMemberCube: list(body.runs_without_member_cube).map(text),
    skippedRuns: list(body.skipped_runs).map((item) => {
      const s = record(item);
      return { runId: text(s.run_id), reason: text(s.reason) };
    }),
    unavailable: record(body.unavailable) as Record<string, string>,
    provenance: record(body.provenance),
    notes: list(body.notes).map(text),
  };
}

/**
 * A rain-skill load that failed. `unreachable` is true when no API answered at all (the network
 * failed, or a proxy answered 502, 503 or 504), which is the only case where starting the API is
 * the fix; any other failure carries the API's own message, which names its own fix.
 */
export class RainSkillLoadError extends Error {
  readonly unreachable: boolean;

  constructor(message: string, unreachable: boolean) {
    super(message);
    this.name = "RainSkillLoadError";
    this.unreachable = unreachable;
  }
}

/** A proxy's answer when the API behind it is not there. */
const UNREACHABLE_STATUS = new Set([502, 503, 504]);

/**
 * Load the event's rain skill. The first request after a bake scores every cycle - measured at
 * 4.6 to 12.4 s over the eight demo cycles on the demo laptop (i5-1155G7) across three sessions
 * on 2026-09-27 and 2026-09-28, depending on what else was running - and the API keeps the
 * answer until a run or the bundle changes. An abort passes through as the
 * browser raised it; every other failure is a `RainSkillLoadError` whose message is a sentence.
 */
export async function loadRainSkill(
  event = "MUM-2019-07-02",
  signal?: AbortSignal,
): Promise<RainSkill> {
  let response: Response;
  try {
    response = await fetch(
      apiUrl(`/v1/verification/rain-skill?event=${encodeURIComponent(event)}`),
      { signal },
    );
  } catch (failure) {
    if (signal?.aborted) throw failure;
    throw new RainSkillLoadError("The API is unreachable.", true);
  }
  const body = (await response.json().catch(() => null)) as Raw | null;
  if (!response.ok || !body) {
    const message = text(record(body?.error).message);
    if (message) throw new RainSkillLoadError(message, false);
    if (UNREACHABLE_STATUS.has(response.status)) {
      throw new RainSkillLoadError(`The API answered ${response.status}.`, true);
    }
    throw new RainSkillLoadError(
      `The API answered ${response.status} without a rain skill. Its log names the failure.`,
      false,
    );
  }
  return parseRainSkill(body, event);
}

/** The pooled CSI curve of one forecast at one threshold, lead-ordered, for a chart. */
export function csiCurve(
  scope: RainScope,
  thresholdMmH: number,
  forecast: RainForecastName,
): { leadMin: number; csi: number | null; nCycles: number; eventPixels: number }[] {
  const key = String(thresholdMmH);
  return scope.byLead.map((row) => {
    const cell = row.thresholds[key];
    return {
      leadMin: row.leadMin,
      csi: cell ? cell.forecasts[forecast].csi : null,
      nCycles: row.nCycles,
      eventPixels: cell ? cell.eventPixels : 0,
    };
  });
}

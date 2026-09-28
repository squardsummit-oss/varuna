/**
 * The drain map Pulse learned (`GET /v1/drains/health`, `/v1/observations`; SPEC.md 11.6).
 *
 * Every pipe here is a **belief**, not a measurement: the geometry is inferred from roads and
 * terrain, and the blockage is a posterior with a standard deviation that an ensemble Kalman
 * filter moved using traffic anomalies and citizen reports. The UI has to carry all three facts
 * together, which is why `betaSd` and `confidence` come down the wire beside `betaMean`.
 */

import { apiUrl } from "@/lib/api/client";
import { STREET_NOT_RECORDED } from "@/lib/street-label";

export interface DrainEdge {
  id: string;
  /** The street OSM names for the road this pipe runs under; null for 83 % of them in Mumbai. */
  street: string | null;
  /**
   * What to call the pipe: its own street, else "off <nearest named street>" within 200 m, else
   * null. Null on a run baked before the product carried it; fall back to `street`.
   */
  displayName: string | null;
  /** "near <chronic hotspot>" within 300 m, or null. */
  locality: string | null;
  /**
   * Whether Pulse moved this pipe this cycle, up or down. Decided on unrounded values by the
   * bake; on an older run it is read from `betaDelta` at half the 1e-4 threshold.
   */
  moved: boolean;
  path: [number, number][];
  betaMean: number;
  betaSd: number;
  betaPrior: number;
  betaDelta: number;
  capacityReductionPct: number;
  diameterM: number;
  observations: number;
  explains: string[];
  /** Always "inferred" in the prototype; the map draws these dashed because of it. */
  confidence: string;
  lastUpdate: string | null;
}

/** One pipe named in the summary: the largest rise or fall this cycle. */
export interface SummaryPipe {
  edge: string;
  name: string | null;
  locality: string | null;
  prior: number;
  post: number;
}

/**
 * What this cycle learned, in the handful of numbers the drain map leads with. Computed by the
 * bake over every pipe of the network; a run baked before that gets one rebuilt by the API from
 * the pipes it wrote, with `source: "written_features"` and a `note` saying what that misses.
 */
export interface DrainSummary {
  source: "product" | "written_features";
  nPipes: number;
  nMoved: number;
  nMovedUp: number;
  nMovedDown: number;
  largestRise: SummaryPipe | null;
  largestFall: SummaryPipe | null;
  /** Full-flow capacity of the whole network, m3/s; null when the bake had none to weight by. */
  capacityFullM3s: number | null;
  /** Capacity lost at the land-use prior, weighted by full-flow capacity, %. */
  capacityLostPriorPct: number | null;
  /** Capacity lost after this cycle's learning, %. */
  capacityLostPostPct: number | null;
  /** What learning moved it by, m3/s; positive means more flow lost. */
  capacityLearnedM3s: number | null;
  nObs: number;
  nObsByKind: Record<string, number>;
  nObsSynthetic: number;
  nObsReal: number;
  /** Moved pipes an older run did not write; null on a product that wrote them all. */
  nMovedUnwritten: number | null;
  note: string | null;
}

export interface DrainHealth {
  runId: string;
  /** Which observation operator the filter used, e.g. "capacity_deficit". */
  operator: string;
  note: string;
  nEdges: number;
  nUpdated: number;
  edges: DrainEdge[];
  notes: string[];
  /** Null only when the API sent none, which it does not for any run it can read. */
  summary: DrainSummary | null;
}

export interface AssimilatedObservation {
  id: string;
  kind: "traffic" | "report";
  ts: string;
  /** Where it was, in words: "Dr Babasaheb Ambedkar Marg", "off Eastern Freeway", or
   * `UNNAMED_PLACE` when nothing was recorded. */
  place: string;
  /** "near <chronic hotspot>" within 300 m, or null. */
  locality: string | null;
  edgeId: string | null;
  depthCm: number;
  depthSdCm: number;
  speedKmh: number | null;
  baselineKmh: number | null;
  z: number | null;
  chip: string | null;
  synthetic: boolean;
  /**
   * The pipe's posterior blockage before and after this cycle's update, and `y - H(theta)` in
   * cm. Null on a run baked before Pulse recorded them, so the card says so rather than
   * printing a change it does not have.
   */
  betaBefore: number | null;
  betaAfter: number | null;
  innovationCm: number | null;
}

/** One row of the model-observation disagreement list (SPEC.md 11.6), worst residual first. */
export interface Disagreement {
  kind: "traffic" | "report";
  place: string;
  edgeId: string | null;
  ts: string;
  observedDepthCm: number;
  modelledDepthCm: number;
  residualCm: number;
}

export interface ObservationSet {
  runId: string;
  /** The cycle these observations were assimilated at; empty on a run that did not say. */
  cycleTs: string;
  nTraffic: number;
  nReports: number;
  nAssimilated: number;
  nEdgesUpdated: number;
  observations: AssimilatedObservation[];
  disagreements: Disagreement[];
  notes: string[];
}

/**
 * The fallback title for an observation the API could not place in words. The API names every
 * traffic anomaly on a segment it knows (its OSM name, "off <street>", "<class> near <place>"),
 * so this is a report with no place or a segment the city build does not carry. It says what is
 * true - no street was recorded - and never "Unnamed road".
 */
export const UNNAMED_PLACE = STREET_NOT_RECORDED;

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function summaryPipe(value: unknown): SummaryPipe | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  return {
    edge: String(v.edge ?? ""),
    name: stringOrNull(v.name),
    locality: stringOrNull(v.locality),
    prior: Number(v.prior ?? 0),
    post: Number(v.post ?? 0),
  };
}

/** Parse the product's `summary` block; exported for tests. */
export function parseDrainSummary(value: unknown): DrainSummary | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  const byKind: Record<string, number> = {};
  if (v.n_obs_by_kind && typeof v.n_obs_by_kind === "object") {
    for (const [kind, n] of Object.entries(v.n_obs_by_kind as Record<string, unknown>)) {
      byKind[kind] = Number(n ?? 0);
    }
  }
  return {
    source: v.source === "written_features" ? "written_features" : "product",
    nPipes: Number(v.n_pipes ?? 0),
    nMoved: Number(v.n_moved ?? 0),
    nMovedUp: Number(v.n_moved_up ?? 0),
    nMovedDown: Number(v.n_moved_down ?? 0),
    largestRise: summaryPipe(v.largest_rise),
    largestFall: summaryPipe(v.largest_fall),
    capacityFullM3s: numberOrNull(v.capacity_full_m3s),
    capacityLostPriorPct: numberOrNull(v.capacity_lost_prior_pct),
    capacityLostPostPct: numberOrNull(v.capacity_lost_post_pct),
    capacityLearnedM3s: numberOrNull(v.capacity_learned_m3s),
    nObs: Number(v.n_obs ?? 0),
    nObsByKind: byKind,
    nObsSynthetic: Number(v.n_obs_synthetic ?? 0),
    nObsReal: Number(v.n_obs_real ?? 0),
    nMovedUnwritten: numberOrNull(v.n_moved_unwritten),
    note: stringOrNull(v.note),
  };
}

/** Whether a pipe moved: the bake's own flag, else `beta_delta` at half the 1e-4 threshold. */
function movedOf(p: Record<string, unknown>): boolean {
  if (typeof p.moved === "boolean") return p.moved;
  return Math.abs(Number(p.beta_delta ?? 0)) >= 5e-5;
}

interface RawFeature {
  geometry?: { coordinates?: unknown } | null;
  properties?: Record<string, unknown> | null;
}

/**
 * How the API fills a capped response. `blockage` (the API's default) sends the worst pipes;
 * `learned` fills the cap with every pipe Pulse moved first, so a cleared pipe is never cut for a
 * high land-use prior. Both are sent worst first.
 */
export type DrainHealthOrder = "blockage" | "learned";

/** Fetch the learned drain map. Returns null when the run predates Pulse. */
export async function loadDrainHealth(
  runId?: string,
  signal?: AbortSignal,
  limit = 4000,
  city?: string,
  order?: DrainHealthOrder,
): Promise<DrainHealth | null> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (runId) query.set("run_id", runId);
  if (city) query.set("city", city);
  if (order) query.set("order", order);
  const response = await fetch(apiUrl(`/v1/drains/health?${query}`), { signal });
  if (!response.ok) {
    if (response.status === 404) return null;
    throw new Error(`Drain health failed: HTTP ${response.status}`);
  }
  const body = (await response.json()) as {
    run_id?: string;
    operator?: string;
    note?: string;
    n_edges?: number;
    n_updated?: number;
    notes?: string[];
    summary?: unknown;
    features?: RawFeature[];
  };

  return {
    runId: body.run_id ?? "",
    operator: body.operator ?? "",
    note: body.note ?? "",
    nEdges: body.n_edges ?? 0,
    nUpdated: body.n_updated ?? 0,
    notes: body.notes ?? [],
    summary: parseDrainSummary(body.summary),
    edges: (body.features ?? []).map((f, i) => {
      const p = f.properties ?? {};
      const street = stringOrNull(p.street);
      return {
        id: String(p.edge_id ?? `edge-${i}`),
        street,
        displayName: stringOrNull(p.display_name) ?? street,
        locality: stringOrNull(p.locality),
        moved: movedOf(p),
        path: (f.geometry?.coordinates as [number, number][]) ?? [],
        betaMean: Number(p.beta_mean ?? 0),
        betaSd: Number(p.beta_sd ?? 0),
        betaPrior: Number(p.beta_prior ?? 0),
        betaDelta: Number(p.beta_delta ?? 0),
        capacityReductionPct: Number(p.capacity_reduction_pct ?? 0),
        diameterM: Number(p.diameter_m ?? 0),
        observations: Number(p.observations ?? 0),
        explains: (p.explains as string[]) ?? [],
        confidence: String(p.confidence ?? "inferred"),
        lastUpdate: (p.last_update as string | null) ?? null,
      };
    }),
  };
}

/** Fetch what Pulse assimilated. Returns null when the run predates Pulse. */
export async function loadObservations(
  runId?: string,
  signal?: AbortSignal,
  city?: string,
): Promise<ObservationSet | null> {
  const params = new URLSearchParams();
  if (runId) params.set("run_id", runId);
  if (city) params.set("city", city);
  const query = params.toString() ? `?${params.toString()}` : "";
  const response = await fetch(apiUrl(`/v1/observations${query}`), { signal });
  if (!response.ok) {
    if (response.status === 404) return null;
    throw new Error(`Observations failed: HTTP ${response.status}`);
  }
  const body = (await response.json()) as {
    run_id?: string;
    cycle_ts?: string;
    n_traffic?: number;
    n_reports?: number;
    n_assimilated?: number;
    n_edges_updated?: number;
    notes?: string[];
    observations?: Record<string, unknown>[];
    disagreements?: Record<string, unknown>[];
  };

  return {
    runId: body.run_id ?? "",
    cycleTs: body.cycle_ts ?? "",
    nTraffic: body.n_traffic ?? 0,
    nReports: body.n_reports ?? 0,
    nAssimilated: body.n_assimilated ?? 0,
    nEdgesUpdated: body.n_edges_updated ?? 0,
    notes: body.notes ?? [],
    disagreements: (body.disagreements ?? []).map((d) => ({
      kind: d.kind === "report" ? "report" : "traffic",
      place: String(d.place ?? "—"),
      edgeId: (d.edge_id as string | null) ?? null,
      ts: String(d.ts ?? ""),
      observedDepthCm: Number(d.observed_depth_cm ?? 0),
      modelledDepthCm: Number(d.modelled_depth_cm ?? 0),
      residualCm: Number(d.residual_cm ?? 0),
    })),
    observations: (body.observations ?? []).map((o, i) => ({
      id: String(o.report_id ?? o.segment_id ?? `obs-${i}`),
      kind: o.kind === "report" ? "report" : "traffic",
      ts: String(o.ts ?? ""),
      // The bake names every observation it can ("off Eastern Freeway"); a run baked before it
      // did left traffic anomalies unnamed, and a raw segment id is not a place.
      place: stringOrNull(o.place) ?? UNNAMED_PLACE,
      locality: stringOrNull(o.locality),
      edgeId: (o.edge_id as string | null) ?? null,
      depthCm: Number(o.depth_cm ?? 0),
      depthSdCm: Number(o.depth_sd_cm ?? 0),
      // `null` on the wire is "not recorded", never zero: `Number(null)` would print a report
      // as traffic at 0 km/h, or a pipe as moved from 0.00.
      speedKmh: numberOrNull(o.speed_kmh),
      baselineKmh: numberOrNull(o.baseline_kmh),
      z: numberOrNull(o.z),
      chip: stringOrNull(o.chip),
      synthetic: Boolean(o.synthetic),
      betaBefore: numberOrNull(o.beta_before),
      betaAfter: numberOrNull(o.beta_after),
      innovationCm: numberOrNull(o.innovation_cm),
    })),
  };
}

/** The desilting CSV's URL, for the export button. */
export function desiltingCsvUrl(runId?: string, city?: string): string {
  const params = new URLSearchParams();
  if (runId) params.set("run_id", runId);
  if (city) params.set("city", city);
  const query = params.toString() ? `?${params.toString()}` : "";
  return apiUrl(`/v1/drains/health.csv${query}`);
}

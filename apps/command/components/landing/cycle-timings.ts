/**
 * The landing page's five-minute cycle as data (SPEC.md 7.1 item 4, motion M3).
 *
 * Seven nodes in pipeline order, each mapped explicitly to the `CYCLE_STAGES` keys whose measured
 * wall-clock it shows. The mapping is the whole point of this file: run.json's `stage_ms` also
 * carries the Twin's sub-timings (`twin_total_ms`, `twin_surface_ms` and the rest), measured
 * inside the `twin` wall clock, and a node that summed every key starting with "twin" would print
 * a Twin nearly twice as slow as it is (ADR-0046). Only the exact stage keys count, each in one
 * node, and a node none of whose keys were timed says "Not timed" rather than a number.
 *
 * Where the timings come from: `GET /v1/cycle/status`, which serves the newest run's `stage_ms`
 * with the per-stage budgets beside it (services/api/varuna_api/routers/cycle.py). When the API is
 * unreachable the page reads `public/cycle-status.json`, which `tools/write_cycle_status.py`
 * writes from a baked run.json through the same model - never typed by hand (rule 6).
 */

import { CYCLE_STAGES, type CycleStageName } from "@/lib/api/schemas";
import { apiUrl, timeoutFor } from "@/lib/api/client";
import { formatMs } from "@/lib/format";

export type CycleNodeId = "ingest" | "sky" | "twin" | "flash" | "pulse" | "products" | "outputs";

export interface CycleNode {
  id: CycleNodeId;
  /** Position in the sequence; the section is a real pipeline, so 7.1 allows numbering. */
  n: number;
  name: string;
  job: string;
  /** The `CYCLE_STAGES` keys this node's time is the sum of. */
  stages: readonly CycleStageName[];
}

export const CYCLE_NODES: readonly CycleNode[] = [
  {
    id: "ingest",
    n: 1,
    name: "Ingest",
    job: "Radar frames decoded and checked, with gauges, tide, traffic and reports",
    stages: ["decode"],
  },
  {
    id: "sky",
    n: 2,
    name: "Sky",
    job: "Z–R, gauge merge, optical flow, 20-member STEPS ensemble",
    stages: ["sky"],
  },
  {
    id: "twin",
    n: 3,
    name: "Twin",
    job: "2D shallow water on 30 m terrain, coupled to the 1D drain graph",
    stages: ["twin"],
  },
  {
    id: "flash",
    n: 4,
    name: "Flash",
    job: "Reduced-order emulator, run beside the Twin for the street ensemble",
    stages: ["flash"],
  },
  {
    id: "pulse",
    n: 5,
    name: "Pulse",
    job: "EnKF over pipe blockage, from traffic anomalies and citizen reports",
    stages: ["pulse"],
  },
  {
    id: "products",
    n: 6,
    name: "Products",
    job: "Segment depths, rasters, hotspots and surcharge",
    stages: ["products"],
  },
  {
    id: "outputs",
    n: 7,
    name: "Command, Route, Public",
    job: "Routes and reachability, alerts and pumps, then published to every screen",
    stages: ["route", "alerts", "publish"],
  },
];

/**
 * The beams between nodes, with the hop each belongs to. Twin and Flash run in parallel, so the
 * two hops into them share a hop index and travel together, and so do the two out of them.
 */
export const CYCLE_BEAMS: readonly { from: CycleNodeId; to: CycleNodeId; hop: number }[] = [
  { from: "ingest", to: "sky", hop: 0 },
  { from: "sky", to: "twin", hop: 1 },
  { from: "sky", to: "flash", hop: 1 },
  { from: "twin", to: "pulse", hop: 2 },
  { from: "flash", to: "pulse", hop: 2 },
  { from: "pulse", to: "products", hop: 3 },
  { from: "products", to: "outputs", hop: 4 },
];

export const CYCLE_HOPS = 1 + Math.max(...CYCLE_BEAMS.map((beam) => beam.hop));

/** One run's timings as the landing page shows them. */
export interface CycleTimings {
  runId: string;
  cycleTs: string | null;
  bundle: string | null;
  stageMs: Readonly<Record<string, number>>;
  budgetMs: Readonly<Record<string, number>>;
  totalBudgetMs: number | null;
  /** "live" from the API, "committed" from public/cycle-status.json. */
  source: "live" | "committed";
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function numberRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const out: Record<string, number> = {};
  for (const [key, v] of Object.entries(value as Record<string, unknown>)) {
    if (finite(v)) out[key] = v;
  }
  return out;
}

/**
 * Reads a `/v1/cycle/status` body (or the committed copy of one). Null when there is nothing to
 * show: no run, or a run with no stage timings - an API with no baked runs answers 200 with
 * `run_id: null`, and that is a reason to try the committed copy, not to print zeros.
 */
export function readCycleStatus(
  body: unknown,
  source: CycleTimings["source"],
): CycleTimings | null {
  if (!body || typeof body !== "object") return null;
  const record = body as Record<string, unknown>;
  const runId = typeof record.run_id === "string" && record.run_id ? record.run_id : null;
  const stageMs = numberRecord(record.stage_ms);
  if (!runId || Object.keys(stageMs).length === 0) return null;
  return {
    runId,
    cycleTs: typeof record.cycle_ts === "string" ? record.cycle_ts : null,
    bundle: typeof record.bundle === "string" ? record.bundle : null,
    stageMs,
    budgetMs: numberRecord(record.budget_ms),
    totalBudgetMs: finite(record.total_budget_ms) ? record.total_budget_ms : null,
    source,
  };
}

export interface NodeTiming {
  /** Sum of the node's timed stages, or null when none of them was timed. */
  ms: number | null;
  /** Sum of the node's stage budgets, or null unless every stage has one. */
  budgetMs: number | null;
  timed: CycleStageName[];
  untimed: CycleStageName[];
}

export function nodeTiming(
  node: CycleNode,
  stageMs: Readonly<Record<string, number>>,
  budgetMs: Readonly<Record<string, number>> = {},
): NodeTiming {
  const timed = node.stages.filter((stage) => finite(stageMs[stage]));
  const untimed = node.stages.filter((stage) => !finite(stageMs[stage]));
  const budgets = node.stages.map((stage) => budgetMs[stage]);
  return {
    ms: timed.length ? timed.reduce((sum, stage) => sum + stageMs[stage], 0) : null,
    budgetMs: budgets.every(finite) ? budgets.reduce((sum, ms) => sum + ms, 0) : null,
    timed,
    untimed,
  };
}

/** "58.5 s", "226 ms", "Not timed", or "6.2 s, partly timed" when only some stages were. */
export function timingLabel(timing: NodeTiming): string {
  if (timing.ms === null) return "Not timed";
  const value = formatMs(timing.ms);
  return timing.untimed.length ? `${value}, partly timed` : value;
}

/**
 * Wall-clock of the cycle, each `CYCLE_STAGES` key counted once and the Twin's sub-timings not at
 * all; the same rule as `totalStageMs` in lib/stores/run.ts and `stage_total_ms` in the schemas.
 */
export function cycleTotalMs(stageMs: Readonly<Record<string, number>>): number | null {
  const values = CYCLE_STAGES.map((stage) => stageMs[stage]).filter(finite);
  return values.length ? values.reduce((sum, ms) => sum + ms, 0) : null;
}

/** Loads the newest run's timings: the API first, then the committed copy, else null. */
export async function loadCycleTimings(
  signal?: AbortSignal,
  fetchImpl: typeof fetch = fetch,
): Promise<CycleTimings | null> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort);
  const timer = setTimeout(abort, timeoutFor(4000));
  try {
    const live = await fetchImpl(apiUrl("/v1/cycle/status"), { signal: controller.signal });
    if (live.ok) {
      const timings = readCycleStatus(await live.json(), "live");
      if (timings) return timings;
    }
  } catch {
    // An unreachable API is a venue problem; the committed copy is the answer (SPEC.md 17).
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
  if (signal?.aborted) return null;
  try {
    const committed = await fetchImpl("/cycle-status.json", { signal });
    if (committed.ok) return readCycleStatus(await committed.json(), "committed");
  } catch {
    // Nothing to show; every node then says "Not timed".
  }
  return null;
}

/**
 * Manholes surcharging and pipes running backwards (`GET /v1/nowcast/surcharge`; SPEC.md 11.4).
 *
 * The demo's 1:40 moment: red markers where the drain is pushing water back up into the street,
 * and the trunk the sea is holding shut. Loaded once per run with every step's discharge, so the
 * markers follow the scrub without a fetch.
 */

import { apiUrl } from "@/lib/api/client";

export interface SurchargeNodeSeries {
  id: string;
  lon: number;
  lat: number;
  /** Peak discharge out of the manhole over the run, in m³/s. */
  peakQ: number;
  /** Discharge at each step, so a marker only appears while the manhole is actually surcharging. */
  q: number[];
}

export interface ReversedEdge {
  id: string;
  /** True where the downstream end is a tidal outfall — the sea holding the drain shut. */
  tidal: boolean;
  steps: number[];
  minQ: number;
  /**
   * The pipe's geometry as [lon, lat] pairs, `path[0]` at its from-node (reversed flow runs toward
   * it). Absent on runs baked before the product carried it; such an edge is never drawn.
   */
  path?: [number, number][];
}

export interface SurchargeSet {
  runId: string;
  nodesTotal: number;
  nodes: SurchargeNodeSeries[];
  /** The worst reversed edges the product stored (500 at most, tidal first). */
  reversedEdges: ReversedEdge[];
  /**
   * Every edge the run reversed at any step (`n_reversed_edges`), which is what the UI quotes. Null
   * when the product does not report it: the stored list is capped at 500, so its length is not the
   * run's number and is never substituted for it.
   */
  reversedTotal: number | null;
  reversedAtTidalOutfall: number;
}

interface RawNode {
  node_id?: string;
  lon?: number;
  lat?: number;
  peak_q_m3s?: number;
  q_m3s?: number[];
}

interface RawEdge {
  edge_id?: string;
  tidal?: boolean;
  steps?: number[];
  min_q_m3s?: number;
  path?: unknown;
}

/** A path only if it is a list of at least two finite [lon, lat] pairs; anything else is dropped. */
export function parseEdgePath(raw: unknown): [number, number][] | undefined {
  if (!Array.isArray(raw) || raw.length < 2) return undefined;
  const out: [number, number][] = [];
  for (const point of raw) {
    if (!Array.isArray(point) || point.length < 2) return undefined;
    const [lon, lat] = point as unknown[];
    if (typeof lon !== "number" || typeof lat !== "number") return undefined;
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) return undefined;
    out.push([lon, lat]);
  }
  return out;
}

/** Fetch a run's surcharge product. Returns null when the run predates it. */
export async function loadSurcharge(
  runId: string | undefined,
  signal?: AbortSignal,
): Promise<SurchargeSet | null> {
  const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
  const response = await fetch(apiUrl(`/v1/nowcast/surcharge${query}`), { signal });
  if (!response.ok) {
    if (response.status === 404) return null;
    throw new Error(`Surcharge failed: HTTP ${response.status}`);
  }

  const body = (await response.json()) as {
    run_id?: string;
    n_nodes_total?: number;
    n_reversed_edges?: number;
    n_reversed_at_tidal_outfall?: number;
    nodes?: RawNode[];
    reversed_edges?: RawEdge[];
  };

  return {
    runId: body.run_id ?? "",
    nodesTotal: body.n_nodes_total ?? 0,
    reversedTotal:
      typeof body.n_reversed_edges === "number" && Number.isFinite(body.n_reversed_edges)
        ? body.n_reversed_edges
        : null,
    reversedAtTidalOutfall: body.n_reversed_at_tidal_outfall ?? 0,
    nodes: (body.nodes ?? []).map((n, i) => ({
      id: n.node_id ?? `node-${i}`,
      lon: n.lon ?? 0,
      lat: n.lat ?? 0,
      peakQ: n.peak_q_m3s ?? 0,
      q: n.q_m3s ?? [],
    })),
    reversedEdges: (body.reversed_edges ?? []).map((e, i) => ({
      id: e.edge_id ?? `edge-${i}`,
      tidal: Boolean(e.tidal),
      steps: e.steps ?? [],
      minQ: e.min_q_m3s ?? 0,
      path: parseEdgePath(e.path),
    })),
  };
}

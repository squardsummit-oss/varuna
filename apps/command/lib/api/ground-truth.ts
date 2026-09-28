/**
 * The event's sourced ground-truth pins (`GET /v1/replay/ground-truth`; SPEC.md 10.2, task P6.12).
 *
 * These are the only observations in a replay bundle that are **not** generated - the gauges, the
 * traffic and the citizen reports all are - and every one carries the URL it was read from. That
 * is what makes the 2:40 moment of the demo worth anything: the claim is not "VARUNA says this
 * street flooded", it is "a civic log said so at 08:47, here is the link, and VARUNA had the
 * street red before it".
 */

import { apiUrl } from "@/lib/api/client";

export interface GroundTruthPin {
  id: string;
  /** ISO 8601 with +05:30. */
  ts: string;
  /** How uncertain that timestamp is, in minutes. A log entry is not a stopwatch. */
  tsUncertaintyMin: number | null;
  name: string;
  lon: number;
  lat: number;
  /** Only where the source states or shows one; most say "waterlogging" and no depth (rule 7). */
  depthCm: number | null;
  depthPhrase: string | null;
  kind: string;
  text: string | null;
  sourceUrl: string;
  sourceTitle: string | null;
  /** False for a pin outside the modelled AOI: shown in the ticker, not on the map. */
  insideAoi: boolean;
}

export interface GroundTruthSet {
  bundle: string;
  count: number;
  pins: GroundTruthPin[];
  notes: string[];
}

export async function loadGroundTruth(
  bundle?: string,
  signal?: AbortSignal,
): Promise<GroundTruthSet | null> {
  const query = bundle ? `?bundle=${encodeURIComponent(bundle)}` : "";
  const response = await fetch(apiUrl(`/v1/replay/ground-truth${query}`), { signal });
  // A bundle without curated pins is an ordinary state, not an error: the console simply has no
  // ticker for it.
  if (!response.ok) return null;
  const body = (await response.json()) as Record<string, unknown>;
  return {
    bundle: String(body.bundle ?? ""),
    count: Number(body.count ?? 0),
    pins: ((body.pins as Record<string, unknown>[]) ?? []).map((p) => ({
      id: String(p.id ?? ""),
      ts: String(p.ts ?? ""),
      tsUncertaintyMin:
        p.ts_uncertainty_min === null || p.ts_uncertainty_min === undefined
          ? null
          : Number(p.ts_uncertainty_min),
      name: String(p.name ?? "Reported place"),
      lon: Number(p.lon),
      lat: Number(p.lat),
      depthCm: p.depth_cm === null || p.depth_cm === undefined ? null : Number(p.depth_cm),
      depthPhrase: (p.depth_phrase as string | null) ?? null,
      kind: String(p.kind ?? "log"),
      text: (p.text as string | null) ?? null,
      sourceUrl: String(p.source_url ?? ""),
      sourceTitle: (p.source_title as string | null) ?? null,
      insideAoi: p.inside_aoi !== false,
    })),
    notes: (body.notes as string[]) ?? [],
  };
}

/** The pins the replay clock has already passed, newest first — what the ticker shows. */
export function pinsSoFar(pins: readonly GroundTruthPin[], simTime: string): GroundTruthPin[] {
  const now = Date.parse(simTime);
  if (!Number.isFinite(now)) return [];
  return pins
    .filter((pin) => Date.parse(pin.ts) <= now)
    .sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts));
}

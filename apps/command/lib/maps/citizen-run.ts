"use client";

/**
 * The run load the citizen map makes: the wet streets and their provenance, and nothing else.
 *
 * `lib/api/run-depth.ts`'s `loadRunDepth` is the operator's load - it decodes thirty-six depth
 * PNGs so the console can scrub. The citizen screen draws no raster (TECH_SPEC 2.2: thirty-six
 * decoded frames are an operator's tool, and the street colours carry the same information), so
 * fetching them would cost a reader on a phone about a megabyte for pixels nothing draws.
 *
 * What is left is the two requests that matter - the run's segment forecast and the city's road
 * geometry - joined by the same `joinSegments` the console uses, so a street is the same colour on
 * both screens or the bug is in one place.
 */

import { useEffect, useState } from "react";

import { apiUrl } from "@/lib/api/client";
import { allSegments, joinSegments, pivotExceedance, type GeoSegment } from "@/lib/api/run-depth";

/** The run the citizen map is drawing, for the header's stamp and honesty line. */
export interface CitizenRunProvenance {
  runId: string;
  cycleTs: string | null;
  mode: string | null;
  bundle: string | null;
  nSteps: number;
  ensembleN: number;
}

export interface CitizenRun {
  provenance: CitizenRunProvenance;
  /** The city AOI in lon/lat as the raster reports it: west, south, east, north. */
  bounds: [number, number, number, number];
  /** Wet streets with their depth series, ready for `wetStreetsLayers`. */
  segments: GeoSegment[];
  /**
   * Every road in the city, with no depth attached: the geography the water sits on.
   *
   * Drawn in the dry colour under the wet streets, which is what stops a citizen map being three
   * orange lines floating in a black rectangle.
   */
  baseSegments: GeoSegment[];
  /** segment_id -> depth in cm per step, for the "streets near you" list. */
  depthCm: Map<string, number[]>;
  /** ISO valid time of each step. */
  validTs: string[];
}

/** Loading, then either a run or a reason there is none. */
export type CitizenRunState =
  | { kind: "loading" }
  | { kind: "ready"; run: CitizenRun }
  | { kind: "empty"; message: string }
  | { kind: "error"; message: string };

interface BoundsBody {
  run_id?: string;
  cycle_ts?: string | null;
  mode?: string | null;
  bundle?: string | null;
  n_steps?: number;
  ensemble_n?: number;
  bounds?: { wgs84?: [number, number, number, number] };
}

interface SegmentsBody {
  valid_ts?: string[];
  depth_cm?: Record<string, number[]>;
  p_gt?: Record<string, Record<string, number[]>>;
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(apiUrl(path), { signal });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { message?: string };
    } | null;
    // The API's own 404 carries the command that produces a run; repeating "HTTP 404" would
    // throw that away.
    throw new Error(body?.error?.message ?? `${path} returned ${response.status}`);
  }
  return (await response.json()) as T;
}

/** Load one run's wet streets. `runId` omitted means the newest baked run with depth products. */
export async function loadCitizenRun(
  city = "mumbai",
  runId?: string,
  signal?: AbortSignal,
): Promise<CitizenRun> {
  const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
  const bounds = await getJson<BoundsBody>(`/v1/nowcast/raster/bounds${query}`, signal);

  const [forecast, geojson] = await Promise.all([
    getJson<SegmentsBody>(`/v1/nowcast/segments${query}`, signal),
    // A city with no exported layers leaves the map without street geometry, which is a map with
    // no water on it - honest, and better than an error over a run that loaded perfectly well.
    fetch(apiUrl(`/v1/city/${city}/layers/segments`), { signal })
      .then((r) => (r.ok ? r.json() : { features: [] }))
      .catch(() => ({ features: [] })),
  ]);

  const depthCm = new Map(Object.entries(forecast.depth_cm ?? {}));
  const segments = joinSegments(geojson, depthCm, pivotExceedance(forecast.p_gt));
  const baseSegments = allSegments(geojson);

  return {
    provenance: {
      runId: String(bounds.run_id ?? ""),
      cycleTs: bounds.cycle_ts ?? null,
      mode: bounds.mode ?? null,
      bundle: bounds.bundle ?? null,
      nSteps: Number(bounds.n_steps ?? 0),
      ensembleN: Number(bounds.ensemble_n ?? 0),
    },
    bounds: bounds.bounds?.wgs84 ?? [0, 0, 0, 0],
    segments,
    baseSegments,
    depthCm,
    validTs: forecast.valid_ts ?? [],
  };
}

/** True when the API's refusal is "nothing is baked", which is an empty state and not a failure. */
export function isEmptyRunMessage(message: string): boolean {
  return /No baked run|make bake|Compute live/i.test(message);
}

/** Load the run once per `city`/`runId`, and report every state the screen has to word. */
export function useCitizenRun(city: string, runId?: string): CitizenRunState {
  const [state, setState] = useState<CitizenRunState>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    // The same shape `FloodMap` uses: the load is announced as it begins rather than during the
    // effect body, so a changed city or run shows its own loading state instead of the last
    // city's streets.
    void (async () => {
      setState({ kind: "loading" });
      try {
        const run = await loadCitizenRun(city, runId, controller.signal);
        if (!controller.signal.aborted) setState({ kind: "ready", run });
      } catch (error: unknown) {
        if (controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : String(error);
        setState(
          isEmptyRunMessage(message) ? { kind: "empty", message } : { kind: "error", message },
        );
      }
    })();
    return () => controller.abort();
  }, [city, runId]);

  return state;
}

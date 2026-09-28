/**
 * Loading one baked run so the map can scrub it without touching the network
 * (SPEC.md task P6.3, and the 7.2 acceptance criterion "no network during scrub").
 *
 * Three fetches, once, in parallel:
 *   1. `/v1/nowcast/raster/bounds` — where the rasters go, how many there are, and the run's
 *      provenance (the run stamp and its honesty notes come from here, not from a constant).
 *   2. `/v1/nowcast/segments` — the depth series per wet segment.
 *   3. `/v1/city/{city}/layers/segments` — the road geometry, which never changes with the run
 *      and so is fetched separately and cached hard by the browser.
 *
 * Then every depth PNG is decoded to an `ImageBitmap` **before** the time bar is enabled.
 * Decoding is the expensive part and doing it lazily is what makes a scrub stutter; doing it up
 * front costs a visible second on load and buys a scrub that is a texture swap.
 */

import { apiUrl } from "./client";

/** Provenance the run stamp prints, straight from `run.json`. */
export interface RunProvenance {
  runId: string;
  cycleTs: string | null;
  mode: string | null;
  bundle: string | null;
  nSteps: number;
  stepMin: number;
  ensembleN: number;
  massBalanceErr: number | null;
  stageMs: Record<string, number>;
  /** p10/p50/p90 of mean street depth per step across members: the time bar's band (7.2). */
  aoiDepthBand: AoiDepthBand | null;
  /** Printed verbatim under the run stamp. Never summarised (SPEC.md rule 6). */
  notes: string[];
}

export interface AoiDepthBand {
  p10: number[];
  p50: number[];
  p90: number[];
}

export interface RunDepth {
  provenance: RunProvenance;
  /** Lon/lat corners for the BitmapLayer: [west, south, east, north]. */
  bounds: [number, number, number, number];
  /** One decoded frame per step; a null means that step's PNG failed to decode. */
  frames: (ImageBitmap | null)[];
  /** segment_id -> depth in cm at each step. Only segments the run wetted appear. */
  depthCm: Map<string, number[]>;
  /**
   * segment_id -> threshold in cm ("15", "30", "45", "60") -> P(depth > threshold) at each step,
   * measured across the run's members (SPEC.md 11.7, 11.8).
   *
   * **Null means the run has no spread to report**, not that it failed to load: the cycle only
   * writes the key when some probability lies strictly between 0 and 1. A one-member run's
   * exceedance is just its depth compared with the threshold, so a reader that finds null should
   * make that comparison and say the run is deterministic - which is the one signal the
   * probability legend needs, and a more honest one than the member count (a 20-member run whose
   * members all agree has nothing to draw either).
   */
  pGt: Map<string, Record<string, number[]>> | null;
  /** ISO valid time of each step, for the time bar's labels. */
  validTs: string[];
  nSegmentsTotal: number;
}

interface BoundsResponse {
  run_id: string;
  cycle_ts: string | null;
  mode: string | null;
  bundle: string | null;
  n_steps: number;
  step_min: number;
  ensemble_n: number;
  mass_balance_err: number | null;
  stage_ms: Record<string, number>;
  notes: string[];
  bounds: { wgs84: [number, number, number, number] };
  aoi_depth_band?: AoiDepthBand | null;
}

interface SegmentsResponse {
  valid_ts: string[];
  n_segments_total: number;
  depth_cm: Record<string, number[]>;
  /** threshold cm -> segment_id -> P(depth > threshold) per step. Absent on a run with no spread. */
  p_gt?: Record<string, Record<string, number[]>>;
}

/**
 * Turn the wire's threshold-first `p_gt` into one record per segment.
 *
 * The file is keyed threshold-first because that is how it compresses and how the cycle computes
 * it; the map is keyed segment-first because a PathLayer accessor holds one segment and asks for
 * one threshold at one step. Pivoting once at load keeps that accessor a pair of lookups, which a
 * scrub re-runs for every wet street (SPEC.md 14: restyle within 16 ms).
 */
export function pivotExceedance(
  pGt: Record<string, Record<string, number[]>> | undefined,
): Map<string, Record<string, number[]>> | null {
  if (!pGt || Object.keys(pGt).length === 0) return null;
  const bySegment = new Map<string, Record<string, number[]>>();
  for (const [threshold, series] of Object.entries(pGt)) {
    for (const [segmentId, values] of Object.entries(series)) {
      let record = bySegment.get(segmentId);
      if (!record) {
        record = {};
        bySegment.set(segmentId, record);
      }
      record[threshold] = values;
    }
  }
  return bySegment;
}

/**
 * The exceedance that arrived with each `depthCm` map, so `joinSegments(geojson, run.depthCm)`
 * carries it without every caller learning a third argument. Weak, so a run the console has moved
 * past is collected with its depth map rather than pinned here.
 */
const EXCEEDANCE_BY_DEPTH = new WeakMap<
  Map<string, number[]>,
  Map<string, Record<string, number[]>>
>();

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(apiUrl(path), { signal });
  if (!response.ok) {
    // The API's errors carry the command that fixes them; surfacing that beats "HTTP 404".
    const body = (await response.json().catch(() => null)) as {
      error?: { message?: string };
    } | null;
    throw new Error(body?.error?.message ?? `${path} returned ${response.status}`);
  }
  return (await response.json()) as T;
}

/**
 * How many depth frames are fetched and decoded at once (task P10.4).
 *
 * This was 1 - a strict `for` loop, one `await fetch` after another - and the comment that
 * justified it was half right and expensive. The half that is right: 36 *simultaneous* decodes of
 * a 522 x 323 RGBA image spike memory and, on an integrated GPU, stall the first paint of the map
 * itself. The half that was wrong: it is the decode that has to be bounded, not the request, and
 * the loop bounded both, so the console paid 36 serialised round trips before it could draw
 * anything at all.
 *
 * Measured on 2026-09-24 against a local API at 12 python processes: the 36 raster fetches cost
 * **2,907 ms end to end, p50 42.7 ms each** - which was the largest single term in a console
 * first render of 6,100 ms against section 14's 2,000 ms budget. Six in flight keeps at most six
 * decodes overlapping, about 4 MB of pixels, and turns those 36 round trips into six.
 *
 * Re-measured after the change, same day and same machine: the 36 fetches now run from 536 ms to
 * 811 ms after navigation start, **275 ms end to end**, and are no longer the largest term in
 * anything. The console's first render still misses its budget - 3.6 to 4.5 s observable - but
 * the reason moved: the run stamp is in the DOM at about 1.4 s and the main thread is then
 * blocked for 3.6 to 5.7 s, evaluating ~3.9 MB of parsed JavaScript and letting deck.gl build
 * its first ~28,000 paths. Raising this number further would buy nothing; see
 * `tests/e2e/performance.spec.ts` and the notes filed for the console's static imports.
 */
const FRAME_CONCURRENCY = 6;

/**
 * Decode every step's PNG into a GPU-ready bitmap, six at a time.
 *
 * Order is by step, not by arrival: `frames[step]` is that step's bitmap however the responses
 * interleave, because the scrub indexes it directly. `onProgress` counts completions, so it still
 * rises monotonically to `nSteps` even though the steps finish out of order - it drives a
 * progress bar, and which particular frame landed is not something a progress bar can say.
 *
 * A frame that fails is `null` and costs the other 35 nothing, which is what it was before: the
 * map draws nothing for that step and the scrub passes over it, visibly and honestly.
 */
async function decodeFrames(
  runId: string,
  nSteps: number,
  signal?: AbortSignal,
  onProgress?: (done: number, total: number) => void,
): Promise<(ImageBitmap | null)[]> {
  const frames: (ImageBitmap | null)[] = new Array<ImageBitmap | null>(nSteps).fill(null);
  let next = 0;
  let done = 0;

  const worker = async (): Promise<void> => {
    for (;;) {
      const step = next;
      next += 1;
      if (step >= nSteps || signal?.aborted) return;
      try {
        const response = await fetch(
          apiUrl(`/v1/nowcast/raster?run_id=${encodeURIComponent(runId)}&step=${step}`),
          { signal },
        );
        frames[step] = response.ok ? await createImageBitmap(await response.blob()) : null;
      } catch {
        frames[step] = null;
      }
      done += 1;
      onProgress?.(done, nSteps);
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(FRAME_CONCURRENCY, Math.max(nSteps, 1)) }, worker),
  );
  return frames;
}

/** Load one run end to end. `runId` omitted means the newest baked run with depth products.
 *
 * `city` decides *whose* newest that is, and only then: a named `runId` is served as asked,
 * because a run directory already knows which city it is for and a second opinion from the caller
 * could only disagree with it. Omitted, the API's configured city answers - which is what every
 * Mumbai screen relies on, and what quietly handed a Mumbai console Chennai's water once Chennai
 * had been onboarded (task D-09). */
export async function loadRunDepth(
  runId?: string,
  signal?: AbortSignal,
  onProgress?: (done: number, total: number) => void,
  city?: string,
): Promise<RunDepth> {
  const params = new URLSearchParams();
  if (runId) params.set("run_id", runId);
  else if (city) params.set("city", city);
  const query = params.toString() ? `?${params.toString()}` : "";
  const bounds = await getJson<BoundsResponse>(`/v1/nowcast/raster/bounds${query}`, signal);

  const [segments, frames] = await Promise.all([
    getJson<SegmentsResponse>(`/v1/nowcast/segments${query}`, signal),
    decodeFrames(bounds.run_id, bounds.n_steps, signal, onProgress),
  ]);

  const depthCm = new Map(Object.entries(segments.depth_cm ?? {}));
  const pGt = pivotExceedance(segments.p_gt);
  if (pGt) EXCEEDANCE_BY_DEPTH.set(depthCm, pGt);

  return {
    provenance: {
      runId: bounds.run_id,
      cycleTs: bounds.cycle_ts,
      mode: bounds.mode,
      bundle: bounds.bundle,
      nSteps: bounds.n_steps,
      stepMin: bounds.step_min,
      ensembleN: bounds.ensemble_n,
      massBalanceErr: bounds.mass_balance_err,
      stageMs: bounds.stage_ms ?? {},
      aoiDepthBand: bounds.aoi_depth_band ?? null,
      notes: bounds.notes ?? [],
    },
    bounds: bounds.bounds.wgs84,
    frames,
    depthCm,
    pGt,
    validTs: segments.valid_ts ?? [],
    nSegmentsTotal: segments.n_segments_total ?? 0,
  };
}

/** Road-class widths in pixels (SPEC.md 6.7: 2-6 px by class, dry segments 1 px). */
const CLASS_WIDTH: Record<string, number> = {
  motorway: 6,
  trunk: 5,
  primary: 4.5,
  secondary: 3.5,
  tertiary: 3,
  residential: 2,
  service: 1.5,
  unclassified: 2,
};

export interface GeoSegment {
  id: string;
  path: [number, number][];
  depthCm: number[];
  /** threshold cm -> P(depth > threshold) per step; absent when the run has no spread. */
  pGt?: Record<string, number[]>;
  width: number;
  /** OSM street name; 10,096 of Mumbai's 21,296 segments have one. Map labels draw only this. */
  name?: string;
  /**
   * The name a list or a popover prints: OSM's where it has one, else the API's `display_name`
   * ("off Dr Ambedkar Road", "Service road near Wadala Depot"). Undefined only on a layer served
   * before the API named every segment, where the screen prints its own "Road" rather than a
   * class word that would read as the street's name. No screen prints "Unnamed road".
   */
  displayName?: string;
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (!trimmed || /^unnamed (road|way|street)$/i.test(trimmed)) return undefined;
  return ["none", "nan", "null", "[]"].includes(trimmed.toLowerCase()) ? undefined : trimmed;
}

/**
 * The name a screen prints for one segment feature of `/v1/city/{city}/layers/segments`.
 *
 * `display_name` is served on every feature (`varuna_api.street_names`), and OSM's `name` covers a
 * layer served before it was. Nothing else: a bare class word ("Residential street") would read
 * as the street's own name, so a feature with neither is undefined and the screen says "Road".
 */
export function segmentDisplayName(
  properties: Record<string, unknown> | null | undefined,
): string | undefined {
  const props = properties ?? {};
  return text(props.display_name) ?? text(props.name);
}

/**
 * Join the city's road geometry to the run's depth series.
 *
 * Only segments the run actually wetted are returned. The dry remainder is 19,000-odd more
 * paths that would never change colour, and drawing them costs frame rate the scrub needs
 * (SPEC.md 14: 55 fps at 1440 x 900). They are already on screen as the city's own street
 * layer, in the dry colour, which is exactly what they should look like.
 *
 * `pGt` defaults to the exceedance `loadRunDepth` loaded with this same `depthCm`, so a segment
 * carries its measured probabilities wherever the run had them; pass one explicitly to override.
 */
export function joinSegments(
  geojson: {
    features: {
      properties: Record<string, unknown>;
      geometry: { type: string; coordinates: number[][] };
    }[];
  },
  depthCm: Map<string, number[]>,
  pGt: Map<string, Record<string, number[]>> | null = EXCEEDANCE_BY_DEPTH.get(depthCm) ?? null,
): GeoSegment[] {
  const out: GeoSegment[] = [];
  for (const feature of geojson.features ?? []) {
    const id = String(feature.properties?.segment_id ?? "");
    const series = depthCm.get(id);
    if (!series || feature.geometry?.type !== "LineString") continue;
    out.push({
      id,
      path: feature.geometry.coordinates as [number, number][],
      depthCm: series,
      pGt: pGt?.get(id),
      width: CLASS_WIDTH[String(feature.properties?.class ?? "residential")] ?? 2,
      name: typeof feature.properties?.name === "string" ? feature.properties.name : undefined,
      displayName: segmentDisplayName(feature.properties),
    });
  }
  return out;
}

/**
 * Every road in the city as a drawable path, with no depth attached.
 *
 * This is the geography layer: drawn once, in the dry colour, so the operator can see Mumbai
 * rather than a black rectangle with some orange on it. It replaces the basemap that SPEC.md 5
 * would have supplied - see the note at the top of `CityMap` - and unlike a tile service it comes
 * from the city VARUNA built and works with the network off (SPEC.md 17).
 */
export function allSegments(geojson: {
  features: {
    properties: Record<string, unknown>;
    geometry: { type: string; coordinates: number[][] };
  }[];
}): GeoSegment[] {
  const out: GeoSegment[] = [];
  for (const feature of geojson.features ?? []) {
    if (feature.geometry?.type !== "LineString") continue;
    out.push({
      id: String(feature.properties?.segment_id ?? ""),
      path: feature.geometry.coordinates as [number, number][],
      depthCm: [],
      width: CLASS_WIDTH[String(feature.properties?.class ?? "residential")] ?? 2,
      name: typeof feature.properties?.name === "string" ? feature.properties.name : undefined,
      displayName: segmentDisplayName(feature.properties),
    });
  }
  return out;
}

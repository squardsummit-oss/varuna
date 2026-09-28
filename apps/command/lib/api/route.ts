/**
 * Routing and reachability (`POST /v1/route`, `GET /v1/reachability`; SPEC.md 7.4, 12).
 *
 * Places are resolved from the city's own layers rather than from coordinates typed into this
 * file: hospitals and fire stations come from the asset register, junctions from the chronic
 * hotspot register, and both carry a `source_url` there (SPEC.md 7). A hard-coded lon/lat for
 * "Hindmata" would be a number nobody can check.
 */

import { apiUrl } from "@/lib/api/client";

export interface Place {
  id: string;
  name: string;
  kind: "hospital" | "fire_station" | "hotspot";
  lon: number;
  lat: number;
}

export interface RouteLeg {
  minutes: number;
  distanceM: number;
  maxDepthCm: number;
  depart: string;
  arrive: string;
  safeUntil: string | null;
  path: [number, number][];
  streets: string[];
}

export interface RouteAvoided {
  segmentId: string;
  name: string;
  depthCm: number;
  probability: number;
  at: string;
  /** The street's own geometry, so the map can draw what the detour went around. */
  path: [number, number][];
}

/**
 * One of up to three safe roads the policy spreads traffic across (TECH_SPEC 3.3).
 *
 * `share` is a policy, not a measured traffic count, and the screen must say so. An API that
 * predates route spreading returns none of these, and the screen then shows one answer.
 */
export interface RouteCorridor {
  id: string;
  label: string;
  route: RouteLeg | null;
  share: number;
  assigned: boolean;
  capacityScore: number;
  maxProbability: number;
}

export type RouteReasonKind = "avoided" | "design" | "timing" | "closure";

/**
 * Why the route went this way, as a record rather than a sentence: `lib/explain.ts` words it.
 *
 * Every field beyond `kind` is optional on the wire, because a reason whose number the run could
 * not supply is dropped rather than softened (UI_SPEC 4) - and because an older API sends none.
 */
export interface RouteReason {
  kind: RouteReasonKind;
  segmentId: string;
  name: string;
  depthCm?: number;
  thresholdCm?: number;
  at?: string;
  probability?: number;
  designIntensityMmH?: number;
  forecastPeakMmH?: number;
  dryUntil?: string;
  dryBelowCm?: number;
  reason?: string;
  user?: string;
  until?: string | null;
}

export interface RoutePlan {
  runId: string;
  profile: string;
  departAt: string;
  naive: RouteLeg | null;
  varuna: RouteLeg | null;
  alternates: RouteLeg[];
  avoided: RouteAvoided[];
  /** Empty when the API does not spread, or when only one safe road exists. */
  corridors: RouteCorridor[];
  /** Empty when the API does not explain, or when no reason kept all of its numbers. */
  reasons: RouteReason[];
  tripId: string | null;
  notes: string[];
  ms: number;
}

export interface ReachabilityBand {
  minutes: number;
  areaKm2: number;
  dryAreaKm2: number;
  nJunctions: number;
  nJunctionsDry: number;
  rings: [number, number][][];
}

export interface ReachabilityResult {
  runId: string;
  facility: Place;
  validTs: string;
  profile: string;
  collapsed: boolean;
  shareOfDry: number;
  bands: ReachabilityBand[];
  ms: number;
}

function leg(raw: Record<string, unknown> | null | undefined): RouteLeg | null {
  if (!raw) return null;
  return {
    minutes: Number(raw.minutes ?? 0),
    distanceM: Number(raw.distance_m ?? 0),
    maxDepthCm: Number(raw.max_depth_cm ?? 0),
    depart: String(raw.depart ?? ""),
    arrive: String(raw.arrive ?? ""),
    safeUntil: (raw.safe_until as string | null) ?? null,
    path: (raw.path as [number, number][]) ?? [],
    streets: (raw.streets as string[]) ?? [],
  };
}

/** Every place the trip pickers offer, hospitals first then chronic junctions. */
export async function loadPlaces(city = "mumbai", signal?: AbortSignal): Promise<Place[]> {
  const [facilities, hotspots] = await Promise.all([
    fetch(apiUrl(`/v1/route/facilities?city=${city}`), { signal })
      .then((r) => (r.ok ? r.json() : { facilities: [] }))
      .catch(() => ({ facilities: [] })),
    fetch(apiUrl(`/v1/city/${city}/layers/hotspots`), { signal })
      .then((r) => (r.ok ? r.json() : { features: [] }))
      .catch(() => ({ features: [] })),
  ]);

  const out: Place[] = [];
  for (const f of (facilities.facilities ?? []) as Record<string, unknown>[]) {
    out.push({
      id: String(f.asset_id ?? ""),
      name: String(f.name ?? ""),
      kind: f.kind === "fire_station" ? "fire_station" : "hospital",
      lon: Number(f.lon),
      lat: Number(f.lat),
    });
  }
  for (const feature of (hotspots.features ?? []) as Record<string, never>[]) {
    const props = (feature.properties ?? {}) as Record<string, unknown>;
    const geometry = feature.geometry as { type?: string; coordinates?: number[] } | undefined;
    if (geometry?.type !== "Point" || !geometry.coordinates) continue;
    out.push({
      id: String(props.hotspot_id ?? props.id ?? props.name ?? ""),
      name: String(props.name ?? "Junction"),
      kind: "hotspot",
      lon: Number(geometry.coordinates[0]),
      lat: Number(geometry.coordinates[1]),
    });
  }
  return out;
}

/**
 * The API's key for a vehicle profile.
 *
 * The console's picker spells two profiles with a hyphen ("two-wheeler", "fire-tender") and
 * `varuna_route.profiles.PROFILES` keys them with an underscore, so sending the picker's value
 * as it stands got a 422 "No vehicle profile 'two-wheeler'" for both. Every other key is
 * already the same on both sides and passes through unchanged.
 */
export function apiProfile(profile: string): string {
  return profile.replace(/-/g, "_");
}

export interface RouteQuery {
  origin: Place;
  destination: Place;
  departAt: string;
  profile: string;
  riskTolerance: number;
  runId?: string;
  /** Ask for up to three corridors. Default on the API side is true. */
  spread?: boolean;
  /** The caller's own id, so repeated requests for one trip keep the same corridor. */
  tripId?: string;
  /** Ask for structured reasons. Default on the API side is true. */
  explain?: boolean;
}

/** Plan a trip. Throws with the API's own message when it refuses one. */
export async function planRoute(query: RouteQuery, signal?: AbortSignal): Promise<RoutePlan> {
  const response = await fetch(apiUrl("/v1/route"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      origin: [query.origin.lon, query.origin.lat],
      destination: [query.destination.lon, query.destination.lat],
      depart_at: query.departAt,
      profile: query.profile,
      risk_tolerance: query.riskTolerance,
      run_id: query.runId,
      spread: query.spread,
      trip_id: query.tripId,
      explain: query.explain,
    }),
  });
  const body = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    const envelope = body.error as { message?: string } | undefined;
    throw new Error(envelope?.message ?? `Route failed: HTTP ${response.status}`);
  }
  return {
    runId: String(body.run_id ?? ""),
    profile: String(body.profile ?? ""),
    departAt: String(body.depart_at ?? ""),
    naive: leg(body.naive as Record<string, unknown> | null),
    varuna: leg(body.varuna as Record<string, unknown> | null),
    alternates: ((body.alternates as Record<string, unknown>[]) ?? [])
      .map(leg)
      .filter((r): r is RouteLeg => r !== null),
    avoided: ((body.avoided as Record<string, unknown>[]) ?? []).map((a) => ({
      segmentId: String(a.segment_id ?? ""),
      name: String(a.name ?? ""),
      depthCm: Number(a.depth_cm ?? 0),
      probability: Number(a.probability ?? 0),
      at: String(a.at ?? ""),
      path: (a.path as [number, number][]) ?? [],
    })),
    corridors: ((body.corridors as Record<string, unknown>[]) ?? []).map((c) => ({
      id: String(c.id ?? ""),
      label: String(c.label ?? ""),
      route: leg(c.route as Record<string, unknown> | null),
      share: Number(c.share ?? 0),
      assigned: Boolean(c.assigned),
      capacityScore: Number(c.capacity_score ?? 0),
      maxProbability: Number(c.max_probability ?? 0),
    })),
    reasons: ((body.reasons as Record<string, unknown>[]) ?? [])
      .map(reason)
      .filter((r): r is RouteReason => r !== null),
    tripId: body.trip_id == null ? null : String(body.trip_id),
    notes: (body.notes as string[]) ?? [],
    ms: Number(body.ms ?? 0),
  };
}

const REASON_KINDS: ReadonlySet<string> = new Set(["avoided", "design", "timing", "closure"]);

/**
 * One reason record, or `null` when it is not one of the four kinds `lib/explain.ts` can word.
 *
 * Numbers are carried through only when the wire actually holds them: `undefined` here is what
 * makes the sentence builder drop a reason rather than print "undefined cm".
 */
function reason(raw: Record<string, unknown>): RouteReason | null {
  const kind = String(raw.kind ?? "");
  if (!REASON_KINDS.has(kind)) return null;
  const number = (key: string): number | undefined => {
    const value = raw[key];
    if (value == null) return undefined;
    const n = Number(value);
    return Number.isFinite(n) ? n : undefined;
  };
  const text = (key: string): string | undefined => {
    const value = raw[key];
    return value == null || value === "" ? undefined : String(value);
  };
  return {
    kind: kind as RouteReasonKind,
    segmentId: String(raw.segment_id ?? ""),
    name: String(raw.name ?? ""),
    depthCm: number("depth_cm"),
    thresholdCm: number("threshold_cm"),
    at: text("at"),
    probability: number("probability"),
    designIntensityMmH: number("design_intensity_mm_h"),
    forecastPeakMmH: number("forecast_peak_mm_h"),
    dryUntil: text("dry_until"),
    dryBelowCm: number("dry_below_cm"),
    reason: text("reason"),
    user: text("user"),
    until: raw.until == null ? null : String(raw.until),
  };
}

/** One facility's catchment at one instant. */
export async function loadReachability(
  facility: string,
  at: string,
  profile: string,
  signal?: AbortSignal,
  runId?: string,
): Promise<ReachabilityResult> {
  const params = new URLSearchParams({ facility, t: at, profile });
  if (runId) params.set("run_id", runId);
  const response = await fetch(apiUrl(`/v1/reachability?${params}`), { signal });
  const body = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    const envelope = body.error as { message?: string } | undefined;
    throw new Error(envelope?.message ?? `Reachability failed: HTTP ${response.status}`);
  }
  const f = (body.facility ?? {}) as Record<string, unknown>;
  return {
    runId: String(body.run_id ?? ""),
    facility: {
      id: String(f.asset_id ?? ""),
      name: String(f.name ?? ""),
      kind: f.kind === "fire_station" ? "fire_station" : "hospital",
      lon: Number(f.lon),
      lat: Number(f.lat),
    },
    validTs: String(body.valid_ts ?? ""),
    profile: String(body.profile ?? ""),
    collapsed: Boolean(body.collapsed),
    shareOfDry: Number(body.share_of_dry ?? 1),
    bands: ((body.bands as Record<string, unknown>[]) ?? []).map((b) => ({
      minutes: Number(b.minutes ?? 0),
      areaKm2: Number(b.area_km2 ?? 0),
      dryAreaKm2: Number(b.dry_area_km2 ?? 0),
      nJunctions: Number(b.n_junctions ?? 0),
      nJunctionsDry: Number(b.n_junctions_dry ?? 0),
      rings: (b.rings as [number, number][][]) ?? [],
    })),
    ms: Number(body.ms ?? 0),
  };
}

/**
 * The run's pump inventory and dispatch plan (`GET /v1/pumps`; SPEC.md 11.10, 7.6).
 *
 * The inventory is synthetic — twelve lorries at real OSM ward-office depots with invented
 * capacities — and the benefit is a documented reduced model, not a physics run. Both labels
 * come down the wire so the board can print them rather than the client asserting them.
 */

import { api, apiUrl, errorFromResponse } from "@/lib/api/client";

export interface PumpUnit {
  id: string;
  capacityM3PerHour: number;
  depot: string;
  status: string;
  /** The depot on the map; absent or null for a plan written before the fleet carried it. */
  lon?: number | null;
  lat?: number | null;
}

export interface PumpAssignment {
  pumpId: string;
  capacityM3PerHour: number;
  depot: string;
  targetId: string;
  targetName: string;
  lon: number | null;
  lat: number | null;
  etaMin: number;
  minutesBefore: number;
  minutesAfter: number;
  minutesSaved: number;
}

export interface PumpPlan {
  runId: string;
  thresholdCm: number;
  /** "Bathtub estimate, not a physics run" — printed beside every benefit number. */
  benefitLabel: string;
  inventory: string;
  pumps: PumpUnit[];
  assignments: PumpAssignment[];
  unassigned: { name: string; minutesAbove: number }[];
  totalMinutesSaved: number;
}

interface RawPump {
  pump_id?: string;
  capacity_m3_per_h?: number;
  depot?: string | null;
  status?: string;
  lon?: number | null;
  lat?: number | null;
}

interface RawAssignment {
  pump_id?: string;
  capacity_m3_per_h?: number;
  depot?: string | null;
  hotspot_id?: string;
  hotspot_name?: string;
  lon?: number | null;
  lat?: number | null;
  eta_min?: number;
  minutes_before?: number;
  minutes_after?: number;
  minutes_saved?: number;
}

/**
 * `fetch` for the pump routes, with the browser's "Failed to fetch" replaced by what happened and
 * what to do (SPEC.md 6.8): the screen prints this sentence where the plan would be.
 */
export async function fetchPumps(
  path: string,
  signal?: AbortSignal,
  /** What the request was for, as the sentence names it: "pump plan", "dispatch map". */
  what = "pump plan",
): Promise<Response> {
  try {
    return await fetch(apiUrl(path), { signal });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new Error(
      `The VARUNA API did not answer at ${apiUrl()}, so there is no ${what} to show. Reload once it is back; on this laptop, make dev starts it.`,
    );
  }
}

/** Fetch the plan. Returns null when the run predates the pump product. */
export async function loadPumpPlan(runId?: string, signal?: AbortSignal): Promise<PumpPlan | null> {
  const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
  const path = `/v1/pumps${query}`;
  const response = await fetchPumps(path, signal);
  if (!response.ok) {
    if (response.status === 404) return null;
    const body: unknown = await response.json().catch(() => null);
    throw errorFromResponse(path, response.status, response.statusText, body);
  }
  const body = (await response.json()) as {
    run_id?: string;
    threshold_cm?: number;
    benefit_label?: string;
    inventory?: string;
    pumps?: RawPump[];
    assignments?: RawAssignment[];
    unassigned?: { name?: string; minutes_above?: number }[];
    total_minutes_saved?: number;
  };

  return {
    runId: body.run_id ?? "",
    thresholdCm: body.threshold_cm ?? 45,
    benefitLabel: body.benefit_label ?? "",
    inventory: body.inventory ?? "synthetic",
    pumps: (body.pumps ?? []).map((p, i) => ({
      id: p.pump_id ?? `P-${i}`,
      capacityM3PerHour: p.capacity_m3_per_h ?? 0,
      depot: p.depot ?? "depot",
      status: p.status ?? "available",
      lon: p.lon ?? null,
      lat: p.lat ?? null,
    })),
    assignments: (body.assignments ?? []).map((a, i) => ({
      pumpId: a.pump_id ?? `P-${i}`,
      capacityM3PerHour: a.capacity_m3_per_h ?? 0,
      depot: a.depot ?? "depot",
      targetId: a.hotspot_id ?? `target-${i}`,
      targetName: a.hotspot_name ?? "",
      lon: a.lon ?? null,
      lat: a.lat ?? null,
      etaMin: a.eta_min ?? 0,
      minutesBefore: a.minutes_before ?? 0,
      minutesAfter: a.minutes_after ?? 0,
      minutesSaved: a.minutes_saved ?? 0,
    })),
    unassigned: (body.unassigned ?? []).map((u) => ({
      name: u.name ?? "",
      minutesAbove: u.minutes_above ?? 0,
    })),
    totalMinutesSaved: body.total_minutes_saved ?? 0,
  };
}

/** One pump where the operator put it, priced by the API (`POST /v1/pumps/price`). */
export interface PricedPlacement {
  pumpId: string;
  targetId: string;
  targetName: string | null;
  depot: string;
  /** Minutes from its depot; null on a place the forecast keeps below 45 cm. */
  etaMin: number | null;
  /** The minutes this pump adds, in arrival order: a second lorry buys only its marginal share. */
  minutesSaved: number;
  /** Why it saves nothing, when it saves nothing for a reason the forecast can state. */
  note: string | null;
}

/** One place on the board, with and without the pumps the operator put there. */
export interface PricedTarget {
  targetId: string;
  targetName: string | null;
  minutesBefore: number;
  minutesAfter: number;
  minutesSaved: number;
}

export interface PricedPlan {
  runId: string;
  placements: PricedPlacement[];
  targets: PricedTarget[];
  refused: { pumpId: string; targetId: string; reason: string }[];
  totalMinutesSaved: number;
  benefitModel: string;
  /** "Flash-lite emulator re-run with the pump's outflow" or the bathtub label. */
  benefitLabel: string;
  priceMs: number;
}

/**
 * Price the board as the operator arranged it (SPEC.md 7.6 AC2). Ungated: it records nothing.
 * The same model and arithmetic as the optimiser, so an unmoved plan prices to its own figures.
 */
export async function pricePumpPlacements(
  input: { runId?: string; placements: { pumpId: string; targetId: string }[] },
  signal?: AbortSignal,
): Promise<PricedPlan> {
  const body = await api.post<{
    run_id?: string;
    placements?: {
      pump_id?: string;
      hotspot_id?: string;
      hotspot_name?: string | null;
      depot?: string | null;
      eta_min?: number | null;
      minutes_saved?: number;
      note?: string | null;
    }[];
    targets?: {
      hotspot_id?: string;
      hotspot_name?: string | null;
      minutes_before?: number;
      minutes_after?: number;
      minutes_saved?: number;
    }[];
    refused?: { pump_id?: string; target_id?: string; reason?: string }[];
    total_minutes_saved?: number;
    benefit_model?: string;
    benefit_label?: string;
    price_ms?: number;
  }>(
    "/v1/pumps/price",
    {
      run_id: input.runId ?? null,
      placements: input.placements.map((p) => ({ pump_id: p.pumpId, hotspot_id: p.targetId })),
    },
    { signal },
  );
  return {
    runId: body.run_id ?? "",
    placements: (body.placements ?? []).map((p) => ({
      pumpId: p.pump_id ?? "",
      targetId: p.hotspot_id ?? "",
      targetName: p.hotspot_name ?? null,
      depot: p.depot ?? "",
      etaMin: p.eta_min ?? null,
      minutesSaved: p.minutes_saved ?? 0,
      note: p.note ?? null,
    })),
    targets: (body.targets ?? []).map((t) => ({
      targetId: t.hotspot_id ?? "",
      targetName: t.hotspot_name ?? null,
      minutesBefore: t.minutes_before ?? 0,
      minutesAfter: t.minutes_after ?? 0,
      minutesSaved: t.minutes_saved ?? 0,
    })),
    refused: (body.refused ?? []).map((r) => ({
      pumpId: r.pump_id ?? "",
      targetId: r.target_id ?? "",
      reason: r.reason ?? "",
    })),
    totalMinutesSaved: body.total_minutes_saved ?? 0,
    benefitModel: body.benefit_model ?? "reduced_model",
    benefitLabel: body.benefit_label ?? "",
    priceMs: body.price_ms ?? 0,
  };
}

// ---- the dispatch map (`GET /v1/pumps/map`) --------------------------------------------------

/** A window of forecast minutes above 45 cm, counted from the cycle time. */
export interface MinuteWindow {
  fromMin: number;
  toMin: number;
}

/** The deepest water in a series and when, counted from the cycle time. */
export interface SeriesPeak {
  depthCm: number;
  atMin: number;
}

/** The road a truck would take from depot to place at the cycle time (the router's answer). */
export interface PumpRoad {
  profile: string;
  minutes: number;
  distanceM: number | null;
  maxDepthCm: number | null;
  /** [lon, lat] vertices, depot first. */
  path: [number, number][];
  streets: string[];
  /** Minutes above 45 cm if the pump arrives when this road says, rather than the plan's ETA. */
  minutesAfter: number;
  minutesSaved: number;
}

/** One pump's journey and what it buys, drawn on the dispatch map. */
export interface PumpLeg {
  pumpId: string;
  capacityM3PerHour: number;
  depot: { name: string; lon: number | null; lat: number | null };
  target: {
    id: string;
    name: string;
    kind: "register" | "street";
    lon: number | null;
    lat: number | null;
  };
  /** The plan's ETA: straight line at the plan's travel speed. The benefit is priced at it. */
  etaMin: number;
  minutesBefore: number;
  minutesAfter: number;
  minutesSaved: number;
  benefitModel: string;
  /** Depth at the place per forecast step, without and with the pump; null when not drawable. */
  depthBeforeCm: number[] | null;
  depthAfterCm: number[] | null;
  windowBefore: MinuteWindow | null;
  windowAfter: MinuteWindow | null;
  peakBefore: SeriesPeak | null;
  peakAfter: SeriesPeak | null;
  /** The first forecast minute the pump's drawdown counts in. */
  effectiveFromMin: number | null;
  /** Whether recounting the series reproduces the plan's minutes; the screen says when not. */
  agrees: boolean;
  road: PumpRoad | null;
  /** Why there is no road. */
  roadNote: string | null;
  /** Why there is no series. */
  seriesNote: string | null;
}

export interface PumpMapSummary {
  pumpsDispatched: number;
  pumpsInFleet: number;
  minutesBefore: number;
  minutesAfter: number;
  minutesSaved: number;
  helpedMost: {
    pumpId: string;
    name: string;
    minutesBefore: number;
    minutesAfter: number;
    minutesSaved: number;
  } | null;
  routed: number;
  /** Minutes saved if every routed lorry arrives when its road says; null with no roads. */
  routedMinutesSaved: number | null;
  /** Lorries whose road takes longer than the plan's straight-line ETA. */
  lateOnRoad: number;
}

export interface PumpMap {
  runId: string;
  cycleTs: string | null;
  stepMin: number;
  nSteps: number;
  thresholdCm: number;
  travelSpeedKmh: number;
  benefitModel: string;
  benefitLabel: string;
  inventory: string;
  /** Flash-lite's measured skill, printed beside every number it produced (rule 6). */
  emulator: { rmseCm: number; csi30cm: number; nTrainingRuns: number } | null;
  routeProfile: string | null;
  summary: PumpMapSummary;
  legs: PumpLeg[];
  depots: { name: string; lon: number | null; lat: number | null; pumpIds: string[] }[];
  unassigned: {
    id: string;
    name: string;
    minutesAbove: number;
    lon: number | null;
    lat: number | null;
  }[];
  nUnassigned: number;
  notes: string[];
  ms: number;
  cached: boolean;
}

interface RawWindow {
  from_min?: number;
  to_min?: number;
}

interface RawPeak {
  depth_cm?: number;
  at_min?: number;
}

interface RawLeg {
  pump_id?: string;
  capacity_m3_per_h?: number;
  depot?: { name?: string | null; lon?: number | null; lat?: number | null };
  target?: {
    id?: string;
    name?: string | null;
    kind?: string;
    lon?: number | null;
    lat?: number | null;
  };
  eta_min?: number;
  minutes_before?: number;
  minutes_after?: number;
  minutes_saved?: number;
  benefit_model?: string;
  depth_before_cm?: number[] | null;
  depth_after_cm?: number[] | null;
  window_before?: RawWindow | null;
  window_after?: RawWindow | null;
  peak_before?: RawPeak | null;
  peak_after?: RawPeak | null;
  effective_from_min?: number | null;
  agrees?: boolean;
  route?: {
    profile?: string;
    minutes?: number;
    distance_m?: number | null;
    max_depth_cm?: number | null;
    path?: [number, number][];
    streets?: string[];
    minutes_after?: number;
    minutes_saved?: number;
  } | null;
  route_note?: string | null;
  series_note?: string | null;
}

/** The body of `GET /v1/pumps/map` as it comes off the wire. */
export interface RawPumpMap {
  run_id?: string;
  cycle_ts?: string | null;
  step_min?: number;
  n_steps?: number;
  threshold_cm?: number;
  travel_speed_kmh?: number;
  benefit_model?: string;
  benefit_label?: string;
  inventory?: string;
  emulator?: { rmse_cm?: number; csi_30cm?: number; n_training_runs?: number } | null;
  route_profile?: string | null;
  summary?: {
    pumps_dispatched?: number;
    pumps_in_fleet?: number;
    minutes_before?: number;
    minutes_after?: number;
    minutes_saved?: number;
    helped_most?: {
      pump_id?: string;
      name?: string;
      minutes_before?: number;
      minutes_after?: number;
      minutes_saved?: number;
    } | null;
    routed?: number;
    routed_minutes_saved?: number | null;
    late_on_road?: number;
  };
  legs?: RawLeg[];
  depots?: { name?: string; lon?: number | null; lat?: number | null; pump_ids?: string[] }[];
  unassigned?: {
    id?: string;
    name?: string;
    minutes_above?: number;
    lon?: number | null;
    lat?: number | null;
  }[];
  n_unassigned?: number;
  notes?: string[];
  ms?: number;
  cached?: boolean;
}

function minuteWindow(raw: RawWindow | null | undefined): MinuteWindow | null {
  if (!raw || raw.from_min == null || raw.to_min == null) return null;
  return { fromMin: raw.from_min, toMin: raw.to_min };
}

function seriesPeak(raw: RawPeak | null | undefined): SeriesPeak | null {
  if (!raw || raw.depth_cm == null || raw.at_min == null) return null;
  return { depthCm: raw.depth_cm, atMin: raw.at_min };
}

/** Shape `GET /v1/pumps/map` for the screen. Exported for the tests, which feed it raw bodies. */
export function parsePumpMap(body: RawPumpMap): PumpMap {
  const s = body.summary ?? {};
  const helped = s.helped_most;
  return {
    runId: body.run_id ?? "",
    cycleTs: body.cycle_ts ?? null,
    stepMin: body.step_min ?? 5,
    nSteps: body.n_steps ?? 0,
    thresholdCm: body.threshold_cm ?? 45,
    travelSpeedKmh: body.travel_speed_kmh ?? 0,
    benefitModel: body.benefit_model ?? "reduced_model",
    benefitLabel: body.benefit_label ?? "",
    inventory: body.inventory ?? "synthetic",
    emulator: body.emulator
      ? {
          rmseCm: body.emulator.rmse_cm ?? 0,
          csi30cm: body.emulator.csi_30cm ?? 0,
          nTrainingRuns: body.emulator.n_training_runs ?? 0,
        }
      : null,
    routeProfile: body.route_profile ?? null,
    summary: {
      pumpsDispatched: s.pumps_dispatched ?? 0,
      pumpsInFleet: s.pumps_in_fleet ?? 0,
      minutesBefore: s.minutes_before ?? 0,
      minutesAfter: s.minutes_after ?? 0,
      minutesSaved: s.minutes_saved ?? 0,
      helpedMost: helped
        ? {
            pumpId: helped.pump_id ?? "",
            name: helped.name ?? "",
            minutesBefore: helped.minutes_before ?? 0,
            minutesAfter: helped.minutes_after ?? 0,
            minutesSaved: helped.minutes_saved ?? 0,
          }
        : null,
      routed: s.routed ?? 0,
      routedMinutesSaved: s.routed_minutes_saved ?? null,
      lateOnRoad: s.late_on_road ?? 0,
    },
    legs: (body.legs ?? []).map((l, i) => ({
      pumpId: l.pump_id ?? `P-${i}`,
      capacityM3PerHour: l.capacity_m3_per_h ?? 0,
      depot: {
        name: l.depot?.name ?? "",
        lon: l.depot?.lon ?? null,
        lat: l.depot?.lat ?? null,
      },
      target: {
        id: l.target?.id ?? `target-${i}`,
        name: l.target?.name ?? "",
        kind: l.target?.kind === "register" ? "register" : "street",
        lon: l.target?.lon ?? null,
        lat: l.target?.lat ?? null,
      },
      etaMin: l.eta_min ?? 0,
      minutesBefore: l.minutes_before ?? 0,
      minutesAfter: l.minutes_after ?? 0,
      minutesSaved: l.minutes_saved ?? 0,
      benefitModel: l.benefit_model ?? "reduced_model",
      depthBeforeCm: l.depth_before_cm ?? null,
      depthAfterCm: l.depth_after_cm ?? null,
      windowBefore: minuteWindow(l.window_before),
      windowAfter: minuteWindow(l.window_after),
      peakBefore: seriesPeak(l.peak_before),
      peakAfter: seriesPeak(l.peak_after),
      effectiveFromMin: l.effective_from_min ?? null,
      agrees: l.agrees ?? false,
      road: l.route
        ? {
            profile: l.route.profile ?? "truck",
            minutes: l.route.minutes ?? 0,
            distanceM: l.route.distance_m ?? null,
            maxDepthCm: l.route.max_depth_cm ?? null,
            path: l.route.path ?? [],
            streets: l.route.streets ?? [],
            minutesAfter: l.route.minutes_after ?? 0,
            minutesSaved: l.route.minutes_saved ?? 0,
          }
        : null,
      roadNote: l.route_note ?? null,
      seriesNote: l.series_note ?? null,
    })),
    depots: (body.depots ?? []).map((d) => ({
      name: d.name ?? "",
      lon: d.lon ?? null,
      lat: d.lat ?? null,
      pumpIds: d.pump_ids ?? [],
    })),
    unassigned: (body.unassigned ?? []).map((u, i) => ({
      id: u.id ?? `unassigned-${i}`,
      name: u.name ?? "",
      minutesAbove: u.minutes_above ?? 0,
      lon: u.lon ?? null,
      lat: u.lat ?? null,
    })),
    nUnassigned: body.n_unassigned ?? 0,
    notes: body.notes ?? [],
    ms: body.ms ?? 0,
    cached: body.cached ?? false,
  };
}

/**
 * The run's pump plan on the city (`GET /v1/pumps/map`): depots, roads, and the depth at each
 * place with and without its pump. The first answer for a run is slow on the API (the city's
 * segment table and twelve truck routes) and remembered there, so the screen draws the plan from
 * {@link loadPumpPlan} first and this when it lands.
 *
 * **A 404 is an error here, never "no map".** The screen asks only once the plan has loaded, so a
 * 404 means either the API refused this run (its own sentence, e.g. a run with no hotspot ranking)
 * or the API is older than the route - FastAPI's bare "Not Found", which would read as nothing at
 * all. Returning null for both left the screen saying "Drawing the fleet" for ever.
 */
export async function loadPumpMap(runId?: string, signal?: AbortSignal): Promise<PumpMap> {
  const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
  const path = `/v1/pumps/map${query}`;
  const response = await fetchPumps(path, signal, "dispatch map");
  if (!response.ok) {
    // The API's own sentence, from its error envelope, is what the screen prints.
    const body: unknown = await response.json().catch(() => null);
    const error = errorFromResponse(path, response.status, response.statusText, body);
    // A route the API does not have: FastAPI's bare "Not Found", or this API's own envelope for
    // an unknown path ("No endpoint at ..."). A refusal of this run carries its own sentence.
    const noRoute =
      error.code === "http_error" ||
      (error.code === "not_found" && error.message.startsWith("No endpoint at"));
    if (response.status === 404 && noRoute) {
      throw new Error(MAP_ROUTE_MISSING(apiUrl()));
    }
    throw error;
  }
  return parsePumpMap((await response.json()) as RawPumpMap);
}

/** An API that answers the plan but not its map is older than this screen. */
export const MAP_ROUTE_MISSING = (base: string): string =>
  `The API at ${base} answers the pump plan but not its dispatch map, so it is older than this screen. Restart it, or redeploy it, to draw the roads.`;

/** One cycle that carries a pump plan, with its plan's own counts (`GET /v1/pumps/cycles`). */
export interface PumpCycle {
  runId: string;
  cycleTs: string | null;
  nAssigned: number;
  nUnassigned: number;
  minutesSaved: number;
}

export interface PumpCycles {
  cycles: PumpCycle[];
  /** The cycle whose plan avoids the most minutes above 45 cm; null when no cycle sends a pump. */
  busiestRunId: string | null;
}

interface RawPumpCycles {
  cycles?: {
    run_id?: string;
    cycle_ts?: string | null;
    n_assigned?: number;
    n_unassigned?: number;
    minutes_saved?: number;
  }[];
  busiest_run_id?: string | null;
}

export function parsePumpCycles(body: RawPumpCycles): PumpCycles {
  return {
    cycles: (body.cycles ?? []).flatMap((c) =>
      c.run_id
        ? [
            {
              runId: c.run_id,
              cycleTs: c.cycle_ts ?? null,
              nAssigned: c.n_assigned ?? 0,
              nUnassigned: c.n_unassigned ?? 0,
              minutesSaved: c.minutes_saved ?? 0,
            },
          ]
        : [],
    ),
    busiestRunId: body.busiest_run_id ?? null,
  };
}

/** Which cycles send pumps: for the cycle on screen that sends none. */
export async function loadPumpCycles(signal?: AbortSignal): Promise<PumpCycles> {
  const path = "/v1/pumps/cycles";
  const response = await fetchPumps(path, signal, "list of cycles");
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw errorFromResponse(path, response.status, response.statusText, body);
  }
  return parsePumpCycles((await response.json()) as RawPumpCycles);
}

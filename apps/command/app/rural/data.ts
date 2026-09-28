/**
 * Everything `/rural` asks the API for, in one place (task D-16).
 *
 * The advisory is rendered on the server, so every call here is made by the API's own host rather
 * than by the reader's phone: a 2G connection downloads one small document and nothing else. That
 * is the whole point of the screen, and it is why the six requests below are not a problem - they
 * are between two processes, and the page reports how long they took.
 *
 * The run is chosen by the same rule `/console` and `/map` use (`lib/opening-run.ts` against the
 * 06:40 opening instant), so a reader comparing this page with the dashboard is looking at one
 * cycle. `?run=` overrides it, and a shared link carries whatever was used.
 */

import { apiUrl } from "@/lib/api/client";
import { loadPlaces, planRoute, type Place, type RoutePlan } from "@/lib/api/route";
import { formatDate, formatIst } from "@/lib/format";
import { openingRunId, type RunSummary } from "@/lib/opening-run";
import {
  OPENING_SIM_TIME,
  buildAdvisory,
  forecastHorizon,
  parseVehicle,
  resolvePlace,
  shareLink,
  DEFAULT_VEHICLE,
  type Bbox,
  type RuralVehicle,
} from "@/lib/rural";

import type { RuralBody, RuralCity, RuralPage, RuralRunStamp } from "./html";

/** How long one API call may take before the page gives up and says so. */
const TIMEOUT_MS = 20_000;

/** The city a reader gets when the query names none. */
const DEFAULT_CITY = "mumbai";

interface CityRow {
  id: string;
  name: string;
  code: string | null;
  bbox: number[] | null;
  built: boolean;
}

interface RunMetaRow {
  run_id: string;
  cycle_ts: string;
  mode: string;
  bundle: string | null;
  n_steps: number;
  step_min: number;
}

interface BundleRow {
  id: string;
  label: string;
}

async function getJson<T>(path: string, signal: AbortSignal): Promise<T | null> {
  try {
    const response = await fetch(apiUrl(path), { signal, cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

/** What the reader typed, before anything is resolved. */
export interface RuralParams {
  city: string;
  from: string;
  to: string;
  vehicleRaw: string;
  vehicle: RuralVehicle;
  run: string | null;
  at: string | null;
}

/**
 * The query, read from the URL.
 *
 * `v` is the short spelling UI_SPEC 7's share link uses; `vehicle` is accepted too, because a
 * person retyping a forwarded link will write the longer word. An unreadable vehicle falls back
 * to the default and the page prints which vehicle it answered for, so the fallback is visible.
 */
export function readParams(url: URL): RuralParams {
  const get = (key: string) => (url.searchParams.get(key) ?? "").trim();
  const vehicleRaw = get("v") || get("vehicle");
  return {
    city: get("city") || DEFAULT_CITY,
    from: get("from"),
    to: get("to"),
    vehicleRaw,
    vehicle: parseVehicle(vehicleRaw) ?? DEFAULT_VEHICLE,
    run: get("run") || null,
    at: get("at") || null,
  };
}

/** The page, and the HTTP status it should be served with. */
export interface RuralResult {
  page: RuralPage;
  status: number;
  /** Wall-clock milliseconds spent talking to the API, printed nowhere but logged. */
  ms: number;
}

/**
 * Build the whole page for one request.
 *
 * Statuses. A trip this page answers, refuses as outside the built area, or cannot name a place
 * for is a 200: the advisory *is* the answer in all three cases, and UI_SPEC 7 requires the "we
 * do not know" block to be the reply rather than an error. Only an API that cannot be reached is
 * a 502, because then there is nothing to read.
 */
export async function buildPage(url: URL, origin: string): Promise<RuralResult> {
  const started = Date.now();
  const params = readParams(url);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    return await assemble(params, origin, controller.signal, started);
  } finally {
    clearTimeout(timer);
  }
}

async function assemble(
  params: RuralParams,
  origin: string,
  signal: AbortSignal,
  started: number,
): Promise<RuralResult> {
  const [cities, places, runs, bundles] = await Promise.all([
    getJson<{ cities: CityRow[] }>("/v1/cities", signal),
    loadPlaces(params.city, signal).catch(() => [] as Place[]),
    getJson<{ runs: RunSummary[] }>(`/v1/runs?city=${encodeURIComponent(params.city)}`, signal),
    getJson<BundleRow[]>("/v1/replay/bundles", signal),
  ]);

  const done = (body: RuralBody, run: RuralRunStamp | null, city: RuralCity, status = 200) => ({
    page: {
      city,
      run,
      query: {
        from: params.from,
        to: params.to,
        vehicle: params.vehicle,
        run: params.run,
        at: params.at,
      },
      body,
      shareUrl:
        body.kind === "answer"
          ? shareLink(origin, {
              from: params.from,
              to: params.to,
              vehicle: params.vehicle.word,
              run: run?.runId ?? null,
              at: params.at,
            })
          : null,
    },
    status,
    ms: Date.now() - started,
  });

  const row = cities?.cities.find((entry) => entry.id === params.city);
  const city: RuralCity = {
    id: params.city,
    name: row?.name ?? params.city,
    code: row?.code ?? null,
  };

  if (!cities) {
    return done(
      {
        kind: "upstream",
        message: `The VARUNA API did not answer at ${apiUrl()}. Try again in a moment.`,
      },
      null,
      city,
      502,
    );
  }
  if (!row || !row.built || !row.bbox || row.bbox.length !== 4) {
    return done(
      {
        kind: "ask",
        message: `VARUNA has not built ${city.name} yet, so there is no drain map and no forecast to read here.`,
      },
      null,
      city,
    );
  }
  const bbox = row.bbox as Bbox;

  const runId =
    params.run ?? openingRunId(runs?.runs ?? [], OPENING_SIM_TIME) ?? runs?.runs?.[0]?.run_id;
  const meta = runId
    ? await getJson<RunMetaRow>(`/v1/runs/${encodeURIComponent(runId)}`, signal)
    : null;
  const stamp = meta ? runStamp(meta, bundles ?? []) : null;

  if (!params.from || !params.to) {
    return done({ kind: "ask", message: null }, stamp, city);
  }

  const from = resolvePlace(params.from, places, bbox);
  const to = resolvePlace(params.to, places, bbox);
  for (const [field, resolved] of [
    ["from", from],
    ["to", to],
  ] as const) {
    if (resolved.status === "outside") {
      return done({ kind: "outside", field, typed: resolved.typed }, stamp, city);
    }
    if (resolved.status === "unknown") {
      return done(
        { kind: "unknown", field, typed: resolved.typed, suggestions: resolved.suggestions },
        stamp,
        city,
      );
    }
  }
  if (from.status !== "ok" || to.status !== "ok") {
    return done({ kind: "ask", message: null }, stamp, city);
  }

  const departAt = params.at || meta?.cycle_ts || OPENING_SIM_TIME;
  let plan: RoutePlan;
  try {
    plan = await planRoute(
      {
        origin: asPlace(from.point.name, from.point.lon, from.point.lat),
        destination: asPlace(to.point.name, to.point.lon, to.point.lat),
        departAt,
        profile: params.vehicle.profile,
        // Not a number, on purpose: `JSON.stringify` writes `null`, the API reads no tolerance
        // and applies the profile's own (`services/route/varuna_route/profiles.py`). Writing a
        // number here would copy those thresholds into the browser, where they would go stale.
        riskTolerance: Number.NaN,
        runId,
        spread: true,
        explain: true,
        // The trip id is the trip itself, so a forwarded link lands on the corridor the sender
        // saw. A random id per request would spread the population correctly and make the page
        // unreproducible, which is the one thing a shared advisory must not be.
        tripId: `${from.point.name}|${to.point.name}|${params.vehicle.word}|${departAt}`,
      },
      signal,
    );
  } catch (error) {
    return done(
      { kind: "upstream", message: message(error) },
      stamp,
      city,
      error instanceof Error && /unreachable|did not answer/i.test(error.message) ? 502 : 200,
    );
  }

  const horizon = meta ? forecastHorizon(meta.cycle_ts, meta.n_steps, meta.step_min) : null;
  const advisory = buildAdvisory(plan, params.vehicle, from.point, to.point, horizon);
  return done({ kind: "answer", advisory }, stamp ?? runStampFromPlan(plan), city);
}

function asPlace(name: string, lon: number, lat: number): Place {
  return { id: "", name, kind: "hotspot", lon, lat };
}

function message(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "The VARUNA API could not plan this trip.";
}

function runStamp(meta: RunMetaRow, bundles: BundleRow[]): RuralRunStamp {
  const bundle = bundles.find((entry) => entry.id === meta.bundle);
  return {
    runId: meta.run_id,
    cycleTime: formatIst(meta.cycle_ts),
    cycleDate: formatDate(meta.cycle_ts),
    mode: meta.mode,
    // The label is the bundle's own, from its manifest, so the honesty chip is quoted and not
    // composed here. A run with no bundle is a live cycle and says so.
    label: bundle?.label ?? (meta.bundle ? "Replay" : "Live cycle"),
  };
}

/** A stamp from the route answer alone, for when the run registry could not be read. */
function runStampFromPlan(plan: RoutePlan): RuralRunStamp | null {
  if (!plan.runId) return null;
  return {
    runId: plan.runId,
    cycleTime: formatIst(plan.departAt),
    cycleDate: formatDate(plan.departAt),
    mode: "unknown",
    label: "run",
  };
}

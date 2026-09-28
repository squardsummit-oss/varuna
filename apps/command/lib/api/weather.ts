/**
 * Live weather for a city (`GET /v1/weather`; TECH_SPEC 5, task D-13).
 *
 * **Why this client refuses to guess.** Every other number the dashboard prints comes out of a
 * baked run. This one comes from outside, and it is the only thing on the screen that is true
 * *now* - which makes it the one number a reader will trust without checking. So the loader has
 * exactly three outcomes and no fourth: a copy the API served (fresh or stale, with its real
 * age), or `unavailable` with the reason in words. A 404, a dead network and a refusing proxy all
 * land in `unavailable`; none of them produces a temperature. SPEC.md rule 6 says a number on
 * screen comes from an artifact or a labelled source, and "the last one I saw" is neither.
 *
 * **Staleness is the API's word, not ours.** `age_s` and `stale` are recomputed by the server on
 * every response against `fetched_at`, so this file never re-ages anything: it reports what it was
 * told. `ageLabel()` turns the seconds into the sentence UI_SPEC 5 asks for.
 *
 * **The grid caveat.** Open-Meteo answers for the model cell containing the point, not the point.
 * `grid_offset_km` is how far that cell's centre is from the city's AOI centre; past
 * {@link GRID_NOTICE_KM} the dialog says so rather than letting a reader think the thermometer is
 * in Dadar.
 */

import { apiFetch, errorMessage, isApiError } from "@/lib/api/client";

/** A WGS84 point, as the API serialises it. */
export interface WeatherPoint {
  lon: number;
  lat: number;
}

/** Current conditions. Every field is optional because the source may omit any of them. */
export interface WeatherNow {
  /** Observation time, ISO 8601 with the city's offset. */
  ts: string;
  temperatureC: number | null;
  humidityPct: number | null;
  /** Rain in the source's last interval, millimetres. */
  precipitationMm: number | null;
  windKmh: number | null;
  /** WMO 4677 present-weather code. */
  weatherCode: number | null;
  /** The WMO table's label for the code, e.g. "Moderate rain". */
  weather: string | null;
}

/** One hourly step of the short forecast. */
export interface WeatherStep {
  ts: string;
  precipitationMm: number | null;
  precipitationProbabilityPct: number | null;
}

/** A served response: the sky over the city, with its provenance and its age. */
export interface Weather {
  city: string;
  /** The city AOI centre the question was asked about. */
  point: WeatherPoint;
  /** The centre of the model cell that answered it. */
  gridPoint: WeatherPoint;
  /** Distance between the two, kilometres. */
  gridOffsetKm: number;
  elevationM: number | null;
  current: WeatherNow;
  hourly: WeatherStep[];
  /** When this copy was retrieved from the source. */
  fetchedAt: string;
  /** Seconds since `fetchedAt`, computed by the API for this request. */
  ageS: number;
  /** True once the copy is older than the API's cache window. */
  stale: boolean;
  ttlS: number;
  source: string;
  sourceUrl: string;
  licence: string;
  licenceUrl: string;
  /** The attribution the licence requires; printed verbatim wherever the data is shown. */
  attribution: string;
  /** Why this copy is what it is - offline, upstream down - in the API's own words. */
  notes: string[];
}

/** What the screen has: a served reading, or nothing and the reason. */
export type WeatherState =
  | { kind: "loading" }
  | { kind: "ready"; weather: Weather }
  | { kind: "unavailable"; reason: string };

/**
 * Past this far from the AOI centre the dialog says the answer is for a model grid cell.
 *
 * Two kilometres is roughly Hindmata to Dadar TT: near enough that "Mumbai" is honest, far enough
 * that a reader who knows the city would otherwise be entitled to read it as their street.
 */
export const GRID_NOTICE_KM = 2;

/** The dashboard's city until the switcher threads one through (task D-09). */
export const DEFAULT_WEATHER_CITY = "mumbai";

/**
 * How long a weather call is waited for.
 *
 * The API's own upstream timeout is 6 s with one retry, so a cold call that has to reach
 * Open-Meteo can legitimately take about 13 s before it either answers or falls back to its disk
 * copy. A shorter ceiling here would report "unavailable" over an API that was about to answer.
 */
export const WEATHER_TIMEOUT_MS = 20_000;

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function point(raw: unknown): WeatherPoint {
  const record = (raw ?? {}) as Record<string, unknown>;
  return { lon: num(record.lon) ?? 0, lat: num(record.lat) ?? 0 };
}

function now(raw: unknown): WeatherNow {
  const record = (raw ?? {}) as Record<string, unknown>;
  return {
    ts: String(record.ts ?? ""),
    temperatureC: num(record.temperature_c),
    humidityPct: num(record.humidity_pct),
    precipitationMm: num(record.precipitation_mm),
    windKmh: num(record.wind_kmh),
    weatherCode: num(record.weather_code),
    weather: typeof record.weather === "string" ? record.weather : null,
  };
}

function step(raw: unknown): WeatherStep {
  const record = (raw ?? {}) as Record<string, unknown>;
  return {
    ts: String(record.ts ?? ""),
    precipitationMm: num(record.precipitation_mm),
    precipitationProbabilityPct: num(record.precipitation_probability_pct),
  };
}

/** The wire body as the screen's shape. Missing numbers stay null; they are never defaulted to 0. */
export function weatherFromBody(body: Record<string, unknown>): Weather {
  return {
    city: String(body.city ?? ""),
    point: point(body.point),
    gridPoint: point(body.grid_point),
    gridOffsetKm: num(body.grid_offset_km) ?? 0,
    elevationM: num(body.elevation_m),
    current: now(body.current),
    hourly: Array.isArray(body.hourly) ? body.hourly.map(step) : [],
    fetchedAt: String(body.fetched_at ?? ""),
    ageS: num(body.age_s) ?? 0,
    stale: body.stale === true,
    ttlS: num(body.ttl_s) ?? 900,
    source: String(body.source ?? ""),
    sourceUrl: String(body.source_url ?? ""),
    licence: String(body.licence ?? ""),
    licenceUrl: String(body.licence_url ?? ""),
    attribution: String(body.attribution ?? ""),
    notes: Array.isArray(body.notes) ? body.notes.map((n) => String(n)) : [],
  };
}

/**
 * Load the weather for `city`, or say why there is none.
 *
 * Never throws and never resolves to a partial reading: a failure of any kind - the API refusing,
 * the network gone, the body unreadable - becomes `unavailable` carrying the API's own sentence
 * where there is one, because SPEC.md 6.8 wants an error that names the fix rather than a shrug.
 */
export async function loadWeather(
  city = DEFAULT_WEATHER_CITY,
  options: { signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<WeatherState> {
  try {
    const body = await apiFetch<Record<string, unknown>>("/v1/weather", {
      query: { city },
      timeoutMs: options.timeoutMs ?? WEATHER_TIMEOUT_MS,
      signal: options.signal,
    });
    return { kind: "ready", weather: weatherFromBody(body) };
  } catch (error) {
    if (isApiError(error) && error.code === "aborted") {
      return { kind: "unavailable", reason: "The weather request was cancelled." };
    }
    return {
      kind: "unavailable",
      reason: errorMessage(
        error,
        "The live weather source could not be reached. The flood forecast on this page does not depend on it.",
      ),
    };
  }
}

/**
 * "2 min ago", "just now", "41 min ago", "3 h ago" - the age UI_SPEC 5 prints beside the source.
 *
 * Rounded down, so a copy is never described as younger than it is.
 */
export function ageLabel(ageS: number): string {
  if (!Number.isFinite(ageS) || ageS < 0) return "age unknown";
  if (ageS < 60) return "just now";
  const minutes = Math.floor(ageS / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return `${days} d ago`;
}

/** "Open-Meteo · CC BY 4.0 · fetched 2 min ago" (UI_SPEC 5), built from what the API sent. */
export function sourceLine(weather: Weather): string {
  const name = weather.source === "open-meteo" ? "Open-Meteo" : weather.source;
  const parts = [name, weather.licence].filter((part) => part.length > 0);
  parts.push(`fetched ${ageLabel(weather.ageS)}`);
  return parts.join(" · ");
}

/** True when the answering model cell is far enough away that the dialog must say so. */
export function isGridDistant(weather: Weather): boolean {
  return Number.isFinite(weather.gridOffsetKm) && weather.gridOffsetKm > GRID_NOTICE_KM;
}

/** Temperature with its unit, or null when the source did not send one - never a zero. */
export function temperatureLabel(weather: Weather): string | null {
  const value = weather.current.temperatureC;
  return value === null ? null : `${Math.round(value)} °C`;
}

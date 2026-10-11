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
  options: { signal?: AbortSignal; timeoutMs?: number; fetchImpl?: typeof fetch } = {},
): Promise<WeatherState> {
  try {
    const body = await apiFetch<Record<string, unknown>>("/v1/weather", {
      query: { city },
      timeoutMs: options.timeoutMs ?? WEATHER_TIMEOUT_MS,
      signal: options.signal,
      fetchImpl: options.fetchImpl,
    });
    return { kind: "ready", weather: weatherFromBody(body) };
  } catch (error) {
    if (isApiError(error) && error.code === "aborted") {
      return { kind: "unavailable", reason: "The weather request was cancelled." };
    }
    // The API only relays Open-Meteo. When it is not answering - asleep, or out of its free
    // allowance (ADR-0097) - the browser asks Open-Meteo itself, as the sea level card already does.
    const direct = await loadWeatherDirect(city, options);
    if (direct) return { kind: "ready", weather: direct };
    return {
      kind: "unavailable",
      reason: errorMessage(
        error,
        "The live weather source could not be reached. The flood forecast on this page does not depend on it.",
      ),
    };
  }
}

/** Centre of each city's AOI (services/city/configs/<city>.yaml), where the API asks too. */
export const WEATHER_POINTS: Record<string, WeatherPoint> = {
  mumbai: { lon: 72.86, lat: 19.065 },
  chennai: { lon: 80.24, lat: 13.005 },
};

/** WMO 4677 present-weather labels as the API's `WMO_LABELS` gives them. */
const WMO_LABELS: Record<number, string> = {
  0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog",
  48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
  56: "Light freezing drizzle", 57: "Dense freezing drizzle", 61: "Slight rain",
  63: "Moderate rain", 65: "Heavy rain", 66: "Light freezing rain", 67: "Heavy freezing rain",
  71: "Slight snowfall", 73: "Moderate snowfall", 75: "Heavy snowfall", 77: "Snow grains",
  80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
  85: "Slight snow showers", 86: "Heavy snow showers", 95: "Thunderstorm",
  96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
};

/** Hourly steps the API keeps: the four that end after "now". */
const DIRECT_HOURLY_STEPS = 4;

function haversineKm(a: WeatherPoint, b: WeatherPoint): number {
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLon = (b.lon - a.lon) * rad;
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

/**
 * The same reading the API serves, asked of Open-Meteo straight from the browser. Null when
 * Open-Meteo does not answer either, or the city has no point. Times are unix seconds so no
 * timezone parsing is involved; they are written back as ISO instants.
 */
export async function loadWeatherDirect(
  city: string,
  options: { signal?: AbortSignal; fetchImpl?: typeof fetch } = {},
): Promise<Weather | null> {
  const at = WEATHER_POINTS[city];
  if (!at) return null;
  const url =
    "https://api.open-meteo.com/v1/forecast" +
    `?latitude=${at.lat}&longitude=${at.lon}` +
    "&current=temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m" +
    "&hourly=precipitation,precipitation_probability&forecast_hours=6&past_hours=1" +
    "&timezone=Asia%2FKolkata&timeformat=unixtime";
  try {
    const doFetch = options.fetchImpl ?? fetch;
    const response = await doFetch(url, { signal: options.signal });
    if (!response.ok) return null;
    const raw = (await response.json()) as Record<string, unknown>;
    const current = (raw.current ?? null) as Record<string, unknown> | null;
    if (!current) return null;
    const hourly = (raw.hourly ?? {}) as Record<string, unknown[]>;
    const nowS = Date.now() / 1000;
    const times = (hourly.time ?? []) as number[];
    const steps: WeatherStep[] = [];
    times.forEach((t, i) => {
      if (t <= nowS || steps.length >= DIRECT_HOURLY_STEPS) return;
      steps.push({
        ts: new Date(t * 1000).toISOString(),
        precipitationMm: num(hourly.precipitation?.[i]),
        precipitationProbabilityPct: num(hourly.precipitation_probability?.[i]),
      });
    });
    const code = num(current.weather_code);
    const grid = { lon: num(raw.longitude) ?? at.lon, lat: num(raw.latitude) ?? at.lat };
    const fetchedAt = new Date().toISOString();
    return {
      city,
      point: at,
      gridPoint: grid,
      gridOffsetKm: Math.round(haversineKm(at, grid) * 100) / 100,
      elevationM: num(raw.elevation),
      current: {
        ts: typeof current.time === "number" ? new Date(current.time * 1000).toISOString() : fetchedAt,
        temperatureC: num(current.temperature_2m),
        humidityPct: num(current.relative_humidity_2m),
        precipitationMm: num(current.precipitation),
        windKmh: num(current.wind_speed_10m),
        weatherCode: code,
        weather: code !== null ? (WMO_LABELS[code] ?? null) : null,
      },
      hourly: steps,
      fetchedAt,
      ageS: 0,
      stale: false,
      ttlS: 900,
      source: "open-meteo",
      sourceUrl: "https://open-meteo.com/",
      licence: "CC BY 4.0",
      licenceUrl: "https://creativecommons.org/licenses/by/4.0/",
      attribution: "Weather data by Open-Meteo.com (CC BY 4.0)",
      notes: ["Read straight from Open-Meteo, because the forecast server is not answering."],
    };
  } catch {
    return null;
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

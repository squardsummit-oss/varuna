/**
 * Live sea level off the city's coast, from Open-Meteo's marine API (free, no key, CC BY 4.0;
 * listed in public-apis' Weather section).
 *
 * The tide is half of whether a coastal city drains: a tide-locked outfall runs backwards
 * (SPEC.md 11.4), so an officer reading the console wants the sea level now and when the next
 * high water comes. The series is the model's sea-surface height above mean sea level, hourly, at
 * the marine grid cell nearest the point below; it is a forecast, not a gauge, and the card says
 * so. Asked from the browser: Open-Meteo answers cross-origin, and the replayed flood never
 * depends on it.
 */

export const TIDE_SOURCE_URL = "https://open-meteo.com/en/docs/marine-weather-api";
export const TIDE_ATTRIBUTION = "Sea level by Open-Meteo.com (CC BY 4.0)";

/** A sea point just off each city's shore, where the marine model has a wet cell. */
export const TIDE_POINTS: Record<string, { lon: number; lat: number; label: string }> = {
  mumbai: { lon: 72.8, lat: 18.95, label: "off Colaba" },
  chennai: { lon: 80.3, lat: 13.0, label: "off Besant Nagar" },
};

export interface TideStep {
  ts: string;
  m: number;
}

export interface Tide {
  point: { lon: number; lat: number; label: string };
  /** Sea level at the hour nearest now, metres above mean sea level. */
  now: TideStep;
  /** The next local maximum after now; null when the series ends first. */
  nextHigh: TideStep | null;
  nextLow: TideStep | null;
  /** Rising or falling at now. */
  rising: boolean;
  series: TideStep[];
}

export type TideState =
  { kind: "loading" } | { kind: "ready"; tide: Tide } | { kind: "unavailable"; reason: string };

/**
 * The tide as the card reads it, from Open-Meteo's hourly series.
 *
 * `nowMs` picks the step nearest the present; the next high and low are the first local extrema
 * after it. Pure, so the arithmetic is tested without a network.
 */
export function tideFromSeries(
  times: readonly string[],
  levels: readonly (number | null)[],
  nowMs: number,
  point: Tide["point"],
): Tide | null {
  const series: TideStep[] = [];
  for (let i = 0; i < times.length; i += 1) {
    const m = levels[i];
    if (typeof m === "number" && Number.isFinite(m)) series.push({ ts: times[i]!, m });
  }
  if (series.length < 3) return null;
  const at = (step: TideStep) => Date.parse(step.ts);
  let nowIdx = 0;
  for (let i = 1; i < series.length; i += 1) {
    if (Math.abs(at(series[i]!) - nowMs) < Math.abs(at(series[nowIdx]!) - nowMs)) nowIdx = i;
  }
  let nextHigh: TideStep | null = null;
  let nextLow: TideStep | null = null;
  for (let i = Math.max(nowIdx, 1); i < series.length - 1; i += 1) {
    const [a, b, c] = [series[i - 1]!.m, series[i]!.m, series[i + 1]!.m];
    if (!nextHigh && b >= a && b > c && at(series[i]!) > nowMs) nextHigh = series[i]!;
    if (!nextLow && b <= a && b < c && at(series[i]!) > nowMs) nextLow = series[i]!;
    if (nextHigh && nextLow) break;
  }
  const next = series[Math.min(nowIdx + 1, series.length - 1)]!;
  const prev = series[Math.max(nowIdx - 1, 0)]!;
  return {
    point,
    now: series[nowIdx]!,
    nextHigh,
    nextLow,
    rising: next.m >= prev.m,
    series,
  };
}

/** Load the tide for `city`, or say why there is none. Never throws. */
export async function loadTide(
  city: string,
  options: { signal?: AbortSignal } = {},
): Promise<TideState> {
  const point = TIDE_POINTS[city];
  if (!point) return { kind: "unavailable", reason: "No sea level point is set for this city." };
  const url =
    "https://marine-api.open-meteo.com/v1/marine" +
    `?latitude=${point.lat}&longitude=${point.lon}` +
    "&hourly=sea_level_height_msl&timezone=Asia%2FKolkata&past_hours=3&forecast_hours=30" +
    "&timeformat=unixtime";
  try {
    const response = await fetch(url, { signal: options.signal });
    if (!response.ok) {
      return { kind: "unavailable", reason: `The sea level source answered ${response.status}.` };
    }
    const body = (await response.json()) as {
      hourly?: { time?: number[]; sea_level_height_msl?: (number | null)[] };
    };
    const times = (body.hourly?.time ?? []).map((s) => new Date(s * 1000).toISOString());
    const tide = tideFromSeries(times, body.hourly?.sea_level_height_msl ?? [], Date.now(), point);
    return tide
      ? { kind: "ready", tide }
      : { kind: "unavailable", reason: "The sea level source sent no usable hours." };
  } catch {
    if (options.signal?.aborted) return { kind: "unavailable", reason: "Cancelled." };
    return {
      kind: "unavailable",
      reason:
        "The sea level source could not be reached. The flood forecast does not depend on it.",
    };
  }
}

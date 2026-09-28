/**
 * VARUNA-Sky's rain products as the console reads them (SPEC.md sections 11.1 and 12).
 *
 * Two endpoints, one cycle: `GET /v1/nowcast/rain` is every member's area-of-interest-mean
 * hyetograph plus the band across them, and `GET /v1/nowcast/rain/series` is the quantiles and
 * exceedance probabilities of the 500 m Sky pixel a named junction falls in.
 *
 * Both take the same source: a baked run by default, or `compute=true` to run one Sky cycle from
 * the replay bundle now. A Mumbai cycle takes about eight seconds, so computing is a button, never
 * something a scrub sets off; the two hooks share one source object so a single click fetches both
 * (the API caches the cycle, so the second request costs a read rather than a second forecast).
 *
 * With nothing baked and no compute, the API answers 404 with the make target to run. That message
 * is the empty state, shown verbatim: an empty chart would be indistinguishable from a forecast of
 * no rain (SPEC.md rule 6).
 */
"use client";

import { useQuery, type UseQueryOptions } from "@tanstack/react-query";

import { ApiError, api, type QueryValue } from "./client";
import { queryKeys, shouldRetry } from "./queries";
import { RainNowcast, RainPointSeries } from "./schemas";

type QueryOverrides<T> = Omit<UseQueryOptions<T, ApiError>, "queryKey" | "queryFn">;

/** Which cycle to read, and whether the API may compute it. */
export interface RainSource {
  /** Compute this cycle from the replay bundle instead of reading a baked run. */
  compute: boolean;
  /**
   * Cycle time to compute (ISO 8601 with the +05:30 offset). Floored onto the bundle's five-minute
   * ladder and clamped to the first instant with three radar frames behind it, so always read
   * `valid_ts` back rather than assuming the instant asked for. Null uses the replay clock.
   */
  t?: string | null;
  /** Bundle to compute from; null uses the open replay, else `VARUNA_BUNDLE`. */
  bundle?: string | null;
  /** A specific run under `data/runs`; null takes the newest run that carries rain. */
  runId?: string | null;
}

/** The baked source: whatever run is newest, no computing. */
export const BAKED_RAIN: RainSource = { compute: false };

/** Milliseconds a compute is allowed: one Mumbai cycle is about 8 s on the demo laptop. */
export const COMPUTE_TIMEOUT_MS = 60_000;

/** Query string for a source; a baked read sends nothing it does not need. */
export function rainQuery(source: RainSource): Record<string, QueryValue> {
  const query: Record<string, QueryValue> = {};
  if (source.runId) query.run_id = source.runId;
  if (source.compute) {
    query.compute = true;
    if (source.bundle) query.bundle = source.bundle;
    if (source.t) query.t = source.t;
  }
  return query;
}

function timeoutFor(source: RainSource): number {
  return source.compute ? COMPUTE_TIMEOUT_MS : 10_000;
}

/**
 * `GET /v1/nowcast/rain`: the ensemble's disagreement about how much rain the whole city is about
 * to get. `steps` is the band, `members` the individual hyetographs. The band is the spread of the
 * city mean, which is narrower than the spread over any one street.
 */
export function useRainNowcast(
  source: RainSource,
  overrides: QueryOverrides<RainNowcast> = {},
) {
  return useQuery<RainNowcast, ApiError>({
    queryKey: queryKeys.rain(source),
    queryFn: () =>
      api.get("/v1/nowcast/rain", {
        schema: RainNowcast,
        query: rainQuery(source),
        timeoutMs: timeoutFor(source),
      }),
    // A cycle's products are deterministic for a given run or instant, so once fetched they never
    // go stale; asking again would recompute an identical forecast.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: shouldRetry,
    ...overrides,
  });
}

/**
 * `GET /v1/nowcast/rain/series?hotspot=...`: the fan chart at one junction. The coordinate comes
 * from the city's own hotspot register, which is why the response carries the `source_url` it was
 * verified against - that link is the honesty label for the point.
 */
export function useRainPointSeries(
  hotspot: string,
  source: RainSource,
  overrides: QueryOverrides<RainPointSeries> = {},
) {
  return useQuery<RainPointSeries, ApiError>({
    queryKey: queryKeys.rainSeries(hotspot, source),
    queryFn: () =>
      api.get("/v1/nowcast/rain/series", {
        schema: RainPointSeries,
        query: { hotspot, ...rainQuery(source) },
        timeoutMs: timeoutFor(source),
      }),
    enabled: Boolean(hotspot),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: shouldRetry,
    ...overrides,
  });
}

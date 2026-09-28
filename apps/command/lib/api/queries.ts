/**
 * TanStack Query hooks for the read endpoints the shells need (SPEC.md section 12).
 * Window-focus refetching is off everywhere: the console must never re-fetch while an operator
 * scrubs, and live updates arrive over the socket instead.
 */
"use client";

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { ApiError, api, timeoutFor } from "./client";
import {
  CycleStatus,
  Health,
  ReportInput,
  ReportResponse,
  RunList,
  RunMeta,
} from "./schemas";

/** Query keys in one place so socket events can invalidate precisely. */
export const queryKeys = {
  health: ["health"] as const,
  runs: ["runs"] as const,
  run: (runId: string) => ["runs", runId] as const,
  cycleStatus: ["cycle", "status"] as const,
  verification: (event: string) => ["verification", event] as const,
  bundles: ["replay", "bundles"] as const,
  replayClock: ["replay", "clock"] as const,
  /** Rain products of one cycle; the source object distinguishes a baked read from a compute. */
  rain: (source: unknown) => ["nowcast", "rain", source] as const,
  rainSeries: (hotspot: string, source: unknown) =>
    ["nowcast", "rain", "series", hotspot, source] as const,
};

/** Do not hammer an API that answers 4xx (wrong request) or 501 (phase not landed). */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && (error.isClientFault || error.isNotImplemented)) return false;
  return failureCount < 2;
}

type QueryOverrides<T> = Omit<UseQueryOptions<T, ApiError>, "queryKey" | "queryFn">;

/** `GET /healthz`: liveness, mode, bundle and the last run. Polls every 30 s. */
export function useHealth(overrides: QueryOverrides<Health> = {}) {
  return useQuery<Health, ApiError>({
    queryKey: queryKeys.health,
    queryFn: () => api.get("/healthz", { schema: Health, timeoutMs: timeoutFor(4_000) }),
    staleTime: 15_000,
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
    retry: shouldRetry,
    ...overrides,
  });
}

/** `GET /v1/runs`: the run registry, newest first when the API sorts it. */
export function useRuns(overrides: QueryOverrides<RunList> = {}) {
  return useQuery<RunList, ApiError>({
    queryKey: queryKeys.runs,
    queryFn: () => api.get("/v1/runs", { schema: RunList }),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    retry: shouldRetry,
    ...overrides,
  });
}

/** `GET /v1/runs/{run_id}`: one run's provenance (versions, stage_ms, mass balance). */
export function useRun(runId: string | null | undefined, overrides: QueryOverrides<RunMeta> = {}) {
  return useQuery<RunMeta, ApiError>({
    queryKey: queryKeys.run(runId ?? ""),
    queryFn: () => api.get(`/v1/runs/${encodeURIComponent(runId ?? "")}`, { schema: RunMeta }),
    enabled: Boolean(runId),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    retry: shouldRetry,
    ...overrides,
  });
}

/**
 * `GET /v1/cycle/status`: live cycle and stage timings. Pass `{ live: true }` while a compute
 * is in flight to poll every second; otherwise the socket's `cycle.stage` events drive it.
 */
export function useCycleStatus(options: { live?: boolean } & QueryOverrides<CycleStatus> = {}) {
  const { live = false, ...overrides } = options;
  return useQuery<CycleStatus, ApiError>({
    queryKey: queryKeys.cycleStatus,
    queryFn: () => api.get("/v1/cycle/status", { schema: CycleStatus }),
    staleTime: live ? 0 : 10_000,
    refetchInterval: live ? 1_000 : false,
    refetchOnWindowFocus: false,
    retry: shouldRetry,
    ...overrides,
  });
}

/** `POST /v1/reports`: citizen observation. The API answers 501 until Phase 7 lands. */
export function useSubmitReport() {
  const queryClient = useQueryClient();
  return useMutation<ReportResponse, ApiError, ReportInput>({
    mutationFn: (input) =>
      api.post("/v1/reports", ReportInput.parse(input), { schema: ReportResponse, timeoutMs: 15_000 }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs });
    },
  });
}

/** Invalidate the queries a `runs.published` socket event makes stale. */
export function invalidateOnRunPublished(queryClient: ReturnType<typeof useQueryClient>): void {
  void queryClient.invalidateQueries({ queryKey: queryKeys.runs });
  void queryClient.invalidateQueries({ queryKey: queryKeys.health });
  void queryClient.invalidateQueries({ queryKey: queryKeys.cycleStatus });
}

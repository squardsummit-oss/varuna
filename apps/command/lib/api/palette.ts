/**
 * What the command palette lists (SPEC.md 7.13): hotspots, facilities, the worst pipes and the
 * run registry, each read from the API rather than passed down by whichever screen happens to be
 * mounted. The palette lives in the root providers, above every screen, so it has to load its own.
 *
 * Every query is disabled until the palette opens, so a screen pays nothing for a palette nobody
 * pressed Ctrl K on. Once loaded they stay in the cache, and the next open is instant. None of
 * them retries: a failure shows at once with its reason, and because a failed query is stale,
 * opening the palette again is the retry.
 *
 * The deep links are built here, as pure functions, so the URLs the palette emits are one list a
 * test can read and a screen can honour - not strings scattered through JSX.
 */
"use client";

import { useQuery } from "@tanstack/react-query";
import type { Route } from "next";

import { apiUrl } from "@/lib/api/client";
import { loadDrainHealth } from "@/lib/api/drains";
import { type Hotspot, loadHotspots } from "@/lib/api/hotspots";
import { useRuns } from "@/lib/api/queries";

/** How many pipes the palette offers: the drain X-ray's table shows the same 25 (SPEC.md 7.3). */
export const PALETTE_PIPE_LIMIT = 25;

export interface PaletteFacility {
  id: string;
  name: string;
  kind: "hospital" | "fire_station";
}

export interface PalettePipe {
  id: string;
  street: string | null;
  betaMean: number;
  betaSd: number;
}

export interface PaletteHotspots {
  /** The run the ranking was read from; the API answers for the newest run when none is asked. */
  runId: string;
  hotspots: Hotspot[];
}

export const paletteKeys = {
  hotspots: (runId: string | undefined) => ["palette", "hotspots", runId ?? "newest"] as const,
  facilities: (city: string | undefined) => ["palette", "facilities", city ?? "default"] as const,
  pipes: (runId: string | undefined) => ["palette", "pipes", runId ?? "newest"] as const,
};

/** `GET /v1/route/facilities`: the hospitals and fire stations reachability can start from. */
export async function loadFacilities(
  city: string | undefined,
  signal?: AbortSignal,
): Promise<PaletteFacility[]> {
  const query = city ? `?city=${encodeURIComponent(city)}` : "";
  const response = await fetch(apiUrl(`/v1/route/facilities${query}`), { signal });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { message?: string };
    } | null;
    throw new Error(body?.error?.message ?? `Facilities failed: HTTP ${response.status}.`);
  }
  const body = (await response.json()) as { facilities?: Record<string, unknown>[] };
  return (body.facilities ?? [])
    .map((f) => ({
      id: String(f.asset_id ?? ""),
      name: String(f.name ?? ""),
      kind: f.kind === "fire_station" ? ("fire_station" as const) : ("hospital" as const),
    }))
    .filter((f) => f.id !== "" && f.name !== "");
}

/** The worst pipes by posterior blockage; the API sorts worst-first. Null when the run has no Pulse. */
export async function loadTopPipes(
  runId: string | undefined,
  signal?: AbortSignal,
): Promise<PalettePipe[] | null> {
  const health = await loadDrainHealth(runId, signal, PALETTE_PIPE_LIMIT);
  if (!health) return null;
  return health.edges.map((edge) => ({
    id: edge.id,
    street: edge.street,
    betaMean: edge.betaMean,
    betaSd: edge.betaSd,
  }));
}

/** Query string from ordered pairs, skipping empty values. */
function withQuery(path: string, params: [string, string | undefined][]): Route {
  const query = params
    .filter((pair): pair is [string, string] => Boolean(pair[1]))
    .map(([key, value]) => `${key}=${encodeURIComponent(value)}`)
    .join("&");
  return (query ? `${path}?${query}` : path) as Route;
}

/**
 * Every URL the palette can open. `run` rides along wherever the palette knows the cycle, because
 * a screen without it defaults to the newest run - 09:10 IST on the baked bundle - and a hotspot
 * picked from the 08:40 ranking would open on a different cycle from the one it was ranked in.
 */
export const paletteHref = {
  hotspot: (hotspotId: string, runId?: string) =>
    withQuery("/console", [
      ["run", runId],
      ["hotspot", hotspotId],
    ]),
  facility: (facilityId: string, runId?: string) =>
    withQuery("/console", [
      ["run", runId],
      ["tab", "reachability"],
      ["facility", facilityId],
    ]),
  pipe: (edgeId: string, runId?: string) =>
    withQuery("/drains", [
      ["run", runId],
      ["pipe", edgeId],
    ]),
  run: (runId: string) => withQuery("/console", [["run", runId]]),
  dispatch: (hotspotId: string | undefined, runId?: string) =>
    withQuery("/pumps", [
      ["run", runId],
      ["hotspot", hotspotId],
    ]),
  whatif: (runId?: string) => withQuery("/whatif", [["run", runId]]),
};

export interface PaletteDataOptions {
  /** Load only while the palette is open. */
  enabled: boolean;
  /** The run the operator is looking at; the newest run when undefined. */
  runId: string | undefined;
  /** That run's city, for the facilities; the API's configured city when undefined. */
  city: string | undefined;
}

/** The four lists the palette draws, each with its own loading and error state. */
export function usePaletteData({ enabled, runId, city }: PaletteDataOptions) {
  const runs = useRuns({ enabled, retry: false });
  const hotspots = useQuery<PaletteHotspots | null, Error>({
    queryKey: paletteKeys.hotspots(runId),
    queryFn: async ({ signal }) => {
      const set = await loadHotspots(runId, signal);
      return set ? { runId: set.runId, hotspots: set.hotspots } : null;
    },
    enabled,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const facilities = useQuery<PaletteFacility[], Error>({
    queryKey: paletteKeys.facilities(city),
    queryFn: ({ signal }) => loadFacilities(city, signal),
    enabled,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const pipes = useQuery<PalettePipe[] | null, Error>({
    queryKey: paletteKeys.pipes(runId),
    queryFn: ({ signal }) => loadTopPipes(runId, signal),
    enabled,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
  return { runs, hotspots, facilities, pipes };
}

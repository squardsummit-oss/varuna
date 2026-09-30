/**
 * The cycle the console opens on when no `?run=` names one (SPEC.md 15: "The replay is
 * pre-seeked to 06:40 and paused on load").
 *
 * Without it the API hands back the newest run, which on the replay is the calm cycle after the
 * storm has passed. The opening instant is the replay store's default simulated time, and a run
 * is matched by instant rather than by string, so a cycle time written with `Z` or with `+05:30`
 * matches the same run. When no baked run sits at the opening, the console keeps the API's
 * default rather than inventing a nearby one.
 */
export interface RunSummary {
  run_id: string;
  cycle_ts: string;
  /** The folder the run was forced from; `MUM-LIVE` for a live cycle (`varuna_cycle.live`). */
  bundle?: string | null;
}

/**
 * RainViewer's terms ask for a link to rainviewer.com wherever its radar is shown, so every screen
 * that draws a radar-driven live run carries this credit beside the run's time.
 */
export const RAINVIEWER_CREDIT = {
  href: "https://www.rainviewer.com",
  label: "Radar: RainViewer",
} as const;

/** Whether a live run nowcast from radar (its notes open with "Live radar:"), not NWP alone. */
export function usesLiveRadar(notes: readonly string[] | null | undefined): boolean {
  return Boolean(notes?.some((note) => note.startsWith("Live radar:")));
}

/** A run forced by today's weather rather than a replay bundle. */
export function isLiveRun(run: Pick<RunSummary, "bundle"> | null | undefined): boolean {
  return Boolean(run?.bundle && run.bundle.endsWith("-LIVE"));
}

/** Past this age a live run is yesterday's news, and a screen opens on the replay instead. */
export const LIVE_MAX_AGE_MIN = 180;

/**
 * The newest live run no older than {@link LIVE_MAX_AGE_MIN}, or undefined.
 *
 * The API runs a live cycle every 30 minutes (`varuna_cycle.live`), so a fresh one is normally
 * under half an hour old; one older than three hours means the live loop has stopped, and a
 * screen that opened on it would present a stale forecast as today's.
 */
export function newestLiveRunId(
  runs: readonly RunSummary[],
  nowMs: number,
  maxAgeMin = LIVE_MAX_AGE_MIN,
): string | undefined {
  let best: RunSummary | undefined;
  for (const run of runs) {
    if (!isLiveRun(run)) continue;
    const at = Date.parse(run.cycle_ts);
    if (!Number.isFinite(at) || nowMs - at > maxAgeMin * 60_000) continue;
    if (!best || at > Date.parse(best.cycle_ts)) best = run;
  }
  return best?.run_id;
}

export function openingRunId(runs: readonly RunSummary[], openingTs: string): string | undefined {
  const target = Date.parse(openingTs);
  if (!Number.isFinite(target)) return undefined;
  return runs.find((run) => Date.parse(run.cycle_ts) === target)?.run_id;
}

/** How long a screen waits for the run registry before opening on the API's default run. */
export const OPENING_LOOKUP_MS = 4_000;

/**
 * Ask the registry which run a city opens on, or undefined to let the API pick its own newest.
 *
 * **The city is not optional in spirit.** Every city's runs live in one directory and `CHN-` sorts
 * after `MUM-`, so "the cycle at 06:40" is only a well-posed question once a city is attached to
 * it: asking without one returns the Mumbai cycle whoever is asking, which is exactly what
 * `/console?city=chennai` was doing - pinning a Mumbai run to a Chennai map.
 *
 * Never throws. A registry that is slow, unreachable, or has no run at the opening instant gives
 * undefined and the caller keeps the API's own newest-for-this-city: a late map beats a wrong one.
 *
 * `apiUrl` is injected rather than imported so this stays a pure function of its arguments and a
 * test can drive it without a base URL in the environment.
 */
export async function fetchOpeningRunId(
  city: string,
  openingTs: string,
  apiUrl: (path: string) => string,
  signal?: AbortSignal,
  options: { preferLive?: boolean; nowMs?: number } = {},
): Promise<string | undefined> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) return undefined;
  signal?.addEventListener("abort", abort);
  const timer = setTimeout(abort, OPENING_LOOKUP_MS);
  try {
    const response = await fetch(apiUrl(`/v1/runs?city=${encodeURIComponent(city)}`), {
      signal: controller.signal,
    });
    if (!response.ok) return undefined;
    const body = (await response.json()) as { runs?: RunSummary[] };
    const runs = body.runs ?? [];
    const live = options.preferLive
      ? newestLiveRunId(runs, options.nowMs ?? Date.now())
      : undefined;
    return live ?? openingRunId(runs, openingTs);
  } catch {
    return undefined;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

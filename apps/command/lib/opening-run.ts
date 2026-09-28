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
    return openingRunId(body.runs ?? [], openingTs);
  } catch {
    return undefined;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

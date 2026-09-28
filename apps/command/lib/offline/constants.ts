/**
 * What the page and `public/sw.js` agree on (task P9.10). The worker is plain JavaScript served as
 * a static file, so it cannot import this module; each constant here names its twin in the worker,
 * and `constants.test.ts` reads the worker's source to prove the two still match.
 */

/** The worker script, registered as `/sw.js?v=<version>`. */
export const SW_PATH = "/sw.js";

/**
 * The two scopes the worker is registered for. Never `/`: the console and every operator screen
 * stay uncontrolled, so nothing the worker does can reach them.
 */
export const OFFLINE_SCOPES = ["/map", "/report"] as const;
export type OfflineScope = (typeof OFFLINE_SCOPES)[number];

/** Where the worker keeps the saved forecast's provenance (`META_PATH` in the worker). */
export const FORECAST_META_PATH = "/__varuna/forecast-meta";

/** The queue of reports posted with no connection (`DB_NAME`, `DB_STORE` in the worker). */
export const OFFLINE_DB = "varuna-offline";
export const OFFLINE_STORE = "reports";

/** Every cache the worker opens starts with this (`PREFIX` in the worker). */
export const CACHE_PREFIX = "varuna-";

/** Messages the worker sends to the pages it controls. */
export type WorkerMessage =
  | { type: "varuna:from-cache"; url: string }
  | { type: "varuna:report-queued"; pending: number }
  | { type: "varuna:reports-sent"; sent: number; refused: number; pending: number }
  | { type: "varuna:warmed" };

/** Messages a page sends the worker. */
export type PageMessage = { type: "varuna:warm"; urls: string[] } | { type: "varuna:flush" };

/**
 * The cache version, which names every cache the worker opens. A new version deletes the old
 * caches on activation, so a deploy never serves the last release's shell.
 *
 * Vercel exposes the commit as `NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA`; any other deploy sets
 * `NEXT_PUBLIC_VARUNA_BUILD_ID`. With neither, the version is `dev`, and the shell is still never
 * stale online: the worker serves the page network first and only falls back to its copy offline.
 */
export function offlineVersion(
  // Spelled out: Next inlines `process.env.NEXT_PUBLIC_*` only where each is written in full.
  env: Record<string, string | undefined> = {
    NEXT_PUBLIC_VARUNA_BUILD_ID: process.env.NEXT_PUBLIC_VARUNA_BUILD_ID,
    NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA: process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA,
  },
): string {
  const raw = env.NEXT_PUBLIC_VARUNA_BUILD_ID || env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA || "dev";
  return raw.replace(/[^A-Za-z0-9._-]/g, "").slice(0, 40) || "dev";
}

/** `/sw.js?v=<version>`. */
export function swUrl(version: string): string {
  return `${SW_PATH}?v=${encodeURIComponent(version)}`;
}

/** The scope a page belongs to, or null for every page the worker must leave alone. */
export function offlineScopeOf(pathname: string): OfflineScope | null {
  for (const scope of OFFLINE_SCOPES) {
    if (pathname === scope || pathname.startsWith(`${scope}/`)) return scope;
  }
  return null;
}

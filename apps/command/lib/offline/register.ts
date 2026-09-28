/**
 * Registering the offline worker (task P9.10), from the two pages it serves and nowhere else.
 *
 * Registered for both scopes from either page, because an offline tap on "Report water" from
 * `/map` is a navigation to `/report`, and only a worker already registered for `/report` can
 * answer it. Then the page tells its own worker what it loaded before the worker controlled it -
 * the static files, the forecast reads, the basemap - so the very first online visit is enough
 * to open the map offline afterwards.
 */

import { apiUrl } from "@/lib/api/client";

import { OFFLINE_SCOPES, offlineScopeOf, swUrl, type PageMessage } from "./constants";

/** How long after registering the page lists what it loaded a second time. */
const WARM_AGAIN_MS = 15_000;

export interface RegisterOptions {
  version: string;
  pathname: string;
  city: string;
  container?: ServiceWorkerContainer;
  /** Resource URLs the page has loaded so far; defaults to the Resource Timing entries. */
  loaded?: () => string[];
}

/** The URLs a page asks its worker to keep after the first load. */
export function warmList(city: string, loaded: readonly string[]): string[] {
  const urls = new Set<string>();
  for (const scope of OFFLINE_SCOPES) urls.add(scope);
  urls.add(apiUrl(`/v1/city/${encodeURIComponent(city)}/basemap.pmtiles`));
  for (const url of loaded) {
    // The worker decides what it keeps; the page only filters out what it certainly will not.
    if (/^(https?:)?\/\//.test(url) || url.startsWith("/")) urls.add(url);
  }
  return [...urls];
}

function resourceUrls(): string[] {
  if (typeof performance === "undefined" || !performance.getEntriesByType) return [];
  return performance.getEntriesByType("resource").map((entry) => entry.name);
}

/** Resolves once the registration has a worker that is running. */
export function activeWorker(registration: ServiceWorkerRegistration): Promise<ServiceWorker> {
  if (registration.active) return Promise.resolve(registration.active);
  const pending = registration.installing ?? registration.waiting;
  return new Promise((resolve) => {
    if (!pending) {
      registration.addEventListener("updatefound", () => {
        const next = registration.installing;
        next?.addEventListener("statechange", () => {
          if (next.state === "activated") resolve(next);
        });
      });
      return;
    }
    pending.addEventListener("statechange", () => {
      if (pending.state === "activated") resolve(pending);
    });
  });
}

/**
 * Register the worker for `/map` and `/report` when the page is one of them. Returns false, and
 * touches nothing, anywhere else.
 */
export async function registerOfflineWorker({
  version,
  pathname,
  city,
  container = typeof navigator !== "undefined" ? navigator.serviceWorker : undefined,
  loaded = resourceUrls,
}: RegisterOptions): Promise<boolean> {
  const scope = offlineScopeOf(pathname);
  if (!scope || !container) return false;
  const url = swUrl(version);
  const registrations = await Promise.all(
    OFFLINE_SCOPES.map((s) => container.register(url, { scope: s, updateViaCache: "none" })),
  );
  const own = registrations[OFFLINE_SCOPES.indexOf(scope)];
  if (!own) return true;
  const worker = await activeWorker(own);
  const send = () => {
    const message: PageMessage = { type: "varuna:warm", urls: warmList(city, loaded()) };
    worker.postMessage(message);
  };
  send();
  // Again once the map has had time to load the rest of its run: a read already in flight when
  // the worker took control was never seen by it, and the worker skips what it already holds.
  setTimeout(send, WARM_AGAIN_MS);
  return true;
}

/** Ask the controlling worker to send any queued reports now. */
export function flushQueuedReports(container?: ServiceWorkerContainer): void {
  const c = container ?? (typeof navigator !== "undefined" ? navigator.serviceWorker : undefined);
  const message: PageMessage = { type: "varuna:flush" };
  c?.controller?.postMessage(message);
}

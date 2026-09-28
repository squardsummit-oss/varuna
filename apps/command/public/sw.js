/*
 * VARUNA's service worker: the public map as an installable app that works offline (task P9.10,
 * SPEC.md 3.2 and 7.11 AC3).
 *
 * **Scoped so it cannot break the console.** It is registered twice, with scope `/map` and scope
 * `/report`, and never with `/`, so `/console`, `/dashboard` and every operator screen are not
 * controlled by it at all: their requests never reach this file. Inside those two scopes it only
 * answers what it recognises - the two pages, Next's content-hashed static files, a short list of
 * API reads, the basemap and `POST /v1/reports` - and lets everything else go to the network
 * untouched.
 *
 * What it keeps:
 * - the app shell for `/map` and `/report` (network first, so an online visit always gets the
 *   release that is deployed; the cached copy is only what an offline visit sees);
 * - the last forecast the public map loaded: the run registry, that run's bounds, wet segments,
 *   depth frames and hotspots, and the city's street, asset and hotspot layers. When a new run's
 *   bounds arrive, entries for every other run are deleted, so this is one forecast, not a pile;
 * - the PMTiles basemap VARUNA builds from OpenStreetMap and WorldCover.
 *
 * What it never keeps: Esri's aerial imagery. Its licence does not allow an offline copy (P10.6),
 * so those requests are not even looked at.
 *
 * A report posted with no connection is stored in IndexedDB, answered `202 {"queued": true}`, and
 * sent when the connection returns (Background Sync where the browser has it, and on every sign of
 * life otherwise: the page coming back online, a successful API read, the worker starting).
 *
 * **Versioned.** The page registers `/sw.js?v=<build>`; the version names every cache, and on
 * activation every cache from another version is deleted, so a release never serves the previous
 * release's shell.
 */

const VERSION = new URL(self.location.href).searchParams.get("v") || "dev";
const PREFIX = "varuna-";
const SHELL_CACHE = `${PREFIX}shell-${VERSION}`;
const STATIC_CACHE = `${PREFIX}static-${VERSION}`;
const DATA_CACHE = `${PREFIX}data-${VERSION}`;

/** The pages this worker keeps. Their query strings are ignored when matching. */
const SHELL_PATHS = ["/map", "/report"];

/** Same-origin files worth keeping besides `/_next/static`: the manifest, the favicon and the
 * three install icons the manifest lists, so an installed app offline still has its icon. */
const STATIC_FILES = [
  "/manifest.webmanifest",
  "/icon.svg",
  "/favicon.ico",
  "/brand/varuna-app-icon-192.png",
  "/brand/varuna-app-icon-512.png",
  "/brand/varuna-app-icon-maskable-512.png",
];

/** Where the saved forecast's provenance lives, inside the data cache. */
const META_PATH = "/__varuna/forecast-meta";

/** The API reads the public map makes, by path; matched on any origin, because the API's origin
 * is configured on the page and not known here. */
const FORECAST_PATTERNS = [
  /\/v1\/runs$/,
  /\/v1\/nowcast\/raster\/bounds$/,
  /\/v1\/nowcast\/raster$/,
  /\/v1\/nowcast\/segments$/,
  /\/v1\/nowcast\/hotspots$/,
  /\/v1\/city\/[^/]+\/layers\/(segments|assets|hotspots)$/,
];
const BASEMAP_PATTERN = /\/v1\/city\/[^/]+\/basemap\.pmtiles$/;
const BOUNDS_PATTERN = /\/v1\/nowcast\/raster\/bounds$/;
const REPORTS_PATTERN = /\/v1\/reports$/;
/** Never cached, never intercepted: the imagery whose licence forbids an offline copy. */
const NEVER = /(^|\.)arcgisonline\.com$|(^|\.)arcgis\.com$/;

const DB_NAME = "varuna-offline";
const DB_STORE = "reports";
const SYNC_TAG = "varuna-reports";

// ---------------------------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------------------------

self.addEventListener("install", (event) => {
  self.skipWaiting();
  // Best effort: an install with no network still installs, and fills on the next visit.
  event.waitUntil(Promise.allSettled(SHELL_PATHS.map((path) => refreshShell(path))));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names
          .filter((name) => name.startsWith(PREFIX) && !name.endsWith(`-${VERSION}`))
          .map((name) => caches.delete(name)),
      );
      await self.clients.claim();
      await flushReports();
    })(),
  );
});

self.addEventListener("sync", (event) => {
  if (event.tag === SYNC_TAG) event.waitUntil(flushReports({ rethrow: true }));
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type === "varuna:warm" && Array.isArray(data.urls)) {
    event.waitUntil(warm(data.urls));
  } else if (data.type === "varuna:flush") {
    event.waitUntil(flushReports());
  }
});

// ---------------------------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------------------------

self.addEventListener("fetch", (event) => {
  const request = event.request;
  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  if (NEVER.test(url.hostname)) return;

  if (request.method === "POST" && REPORTS_PATTERN.test(url.pathname)) {
    event.respondWith(postReport(event));
    return;
  }
  if (request.method !== "GET") return;

  const sameOrigin = url.origin === self.location.origin;
  if (request.mode === "navigate") {
    if (sameOrigin && isShell(url.pathname)) event.respondWith(navigate(event, url));
    return;
  }
  if (sameOrigin) {
    if (url.pathname.startsWith("/_next/static/") || STATIC_FILES.includes(url.pathname)) {
      event.respondWith(cacheFirst(request, STATIC_CACHE));
    }
    return;
  }
  if (BASEMAP_PATTERN.test(url.pathname)) {
    event.respondWith(basemap(event));
    return;
  }
  if (FORECAST_PATTERNS.some((pattern) => pattern.test(url.pathname))) {
    event.respondWith(networkFirst(event));
  }
});

function isShell(pathname) {
  return SHELL_PATHS.some((path) => pathname === path || pathname === `${path}/`);
}

// ---------------------------------------------------------------------------------------------
// Shell
// ---------------------------------------------------------------------------------------------

/** Network first: online always gets the deployed release; offline gets the last one seen. */
async function navigate(event, url) {
  const key = shellKey(url.pathname);
  try {
    const response = await fetch(event.request);
    if (response.ok) event.waitUntil(storeShell(key, response.clone()));
    return response;
  } catch (error) {
    const cache = await caches.open(SHELL_CACHE);
    const cached = await cache.match(key);
    if (cached) return cached;
    throw error;
  }
}

function shellKey(pathname) {
  return new URL(pathname.replace(/\/$/, ""), self.location.origin).href;
}

async function refreshShell(path) {
  const response = await fetch(
    new Request(path, { cache: "no-store", credentials: "same-origin" }),
  );
  if (!response.ok) return;
  await storeShell(shellKey(path), response);
}

/** Keep the page and every `/_next/static` file it names, so the shell boots with no network. */
async function storeShell(key, response) {
  const html = await response.clone().text();
  const shell = await caches.open(SHELL_CACHE);
  await shell.put(key, response);
  const assets = new Set();
  for (const match of html.matchAll(/["'(](\/_next\/static\/[^"'()\s\\]+)/g)) {
    assets.add(new URL(match[1], self.location.origin).href);
  }
  const statics = await caches.open(STATIC_CACHE);
  const fonts = new Set();
  await Promise.allSettled(
    [...assets].map(async (asset) => {
      let cached = await statics.match(asset);
      if (!cached) {
        const res = await fetch(asset);
        if (!res.ok) return;
        await statics.put(asset, res.clone());
        cached = res;
      }
      // Stylesheets name the self-hosted fonts; without them the shell boots in a fallback face.
      if (asset.endsWith(".css")) {
        const css = await cached.text();
        for (const m of css.matchAll(/url\(["']?(\/_next\/static\/[^"')]+)["']?\)/g)) {
          fonts.add(new URL(m[1], self.location.origin).href);
        }
      }
    }),
  );
  await Promise.allSettled(
    [...fonts].map(async (font) => {
      if (await statics.match(font)) return;
      const res = await fetch(font);
      if (res.ok) await statics.put(font, res);
    }),
  );
}

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request, { ignoreVary: true });
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok && response.status === 200) await cache.put(request, response.clone());
  return response;
}

// ---------------------------------------------------------------------------------------------
// Forecast and basemap
// ---------------------------------------------------------------------------------------------

/** Network first, falling back to the last copy; the page is told when it is reading a copy. */
async function networkFirst(event) {
  const request = event.request;
  try {
    const response = await fetch(request);
    if (response.ok && response.status === 200) {
      event.waitUntil(storeData(request.url, response.clone()));
      // The API answered, so this is a good moment to send anything waiting.
      event.waitUntil(flushReports());
    }
    return response;
  } catch (error) {
    const cache = await caches.open(DATA_CACHE);
    const cached = await cache.match(request.url, { ignoreVary: true });
    if (!cached) throw error;
    event.waitUntil(tell(event.clientId, { type: "varuna:from-cache", url: request.url }));
    return cached;
  }
}

/** Store one forecast read; a new run's bounds replace the previous run's saved entries. */
async function storeData(url, response) {
  const cache = await caches.open(DATA_CACHE);
  if (BOUNDS_PATTERN.test(new URL(url).pathname)) {
    const body = await response
      .clone()
      .json()
      .catch(() => null);
    if (body && body.run_id) {
      await pruneOtherRuns(cache, body.run_id);
      const meta = {
        runId: body.run_id,
        cycleTs: body.cycle_ts || null,
        savedAt: new Date().toISOString(),
      };
      await cache.put(
        new URL(META_PATH, self.location.origin).href,
        new Response(JSON.stringify(meta), { headers: { "content-type": "application/json" } }),
      );
    }
  }
  await cache.put(url, response);
}

async function pruneOtherRuns(cache, runId) {
  const keys = await cache.keys();
  await Promise.all(
    keys.map((request) => {
      const other = new URL(request.url).searchParams.get("run_id");
      return other && other !== runId ? cache.delete(request) : Promise.resolve(false);
    }),
  );
}

/** The basemap: the saved copy at once, refreshed behind it. Byte ranges are cut from the copy. */
async function basemap(event) {
  const request = event.request;
  const key = new URL(request.url);
  key.search = "";
  const cache = await caches.open(DATA_CACHE);
  const cached = await cache.match(key.href);
  const refresh = fetch(new Request(key.href, { mode: "cors", credentials: "omit" }))
    .then(async (response) => {
      if (response.ok && response.status === 200) await cache.put(key.href, response.clone());
      return response;
    })
    .catch(() => null);
  if (cached) {
    event.waitUntil(refresh);
    return withRange(request, cached);
  }
  const fresh = await refresh;
  if (fresh) return withRange(request, fresh.clone());
  return fetch(request);
}

async function withRange(request, response) {
  const range = request.headers.get("range");
  const match = range && /^bytes=(\d+)-(\d*)$/.exec(range.trim());
  if (!match) return response;
  const buffer = await response.arrayBuffer();
  const start = Number(match[1]);
  const end = match[2] ? Math.min(Number(match[2]), buffer.byteLength - 1) : buffer.byteLength - 1;
  if (start >= buffer.byteLength || end < start) {
    return new Response(null, {
      status: 416,
      headers: { "content-range": `bytes */${buffer.byteLength}` },
    });
  }
  return new Response(buffer.slice(start, end + 1), {
    status: 206,
    headers: {
      "content-type": response.headers.get("content-type") || "application/octet-stream",
      "content-range": `bytes ${start}-${end}/${buffer.byteLength}`,
      "content-length": String(end - start + 1),
    },
  });
}

/** Fetch and store what the page loaded before this worker controlled it. */
async function warm(urls) {
  const tasks = [];
  for (const raw of urls) {
    let url;
    try {
      url = new URL(raw, self.location.origin);
    } catch {
      continue;
    }
    if (NEVER.test(url.hostname)) continue;
    const sameOrigin = url.origin === self.location.origin;
    if (sameOrigin && isShell(url.pathname)) {
      tasks.push(refreshShell(url.pathname));
    } else if (
      sameOrigin &&
      (url.pathname.startsWith("/_next/static/") || STATIC_FILES.includes(url.pathname))
    ) {
      tasks.push(
        (async () => {
          const cache = await caches.open(STATIC_CACHE);
          if (await cache.match(url.href)) return;
          const response = await fetch(url.href);
          if (response.ok) await cache.put(url.href, response);
        })(),
      );
    } else if (BASEMAP_PATTERN.test(url.pathname)) {
      url.search = "";
      tasks.push(
        (async () => {
          const cache = await caches.open(DATA_CACHE);
          const response = await fetch(url.href, { mode: "cors", credentials: "omit" });
          if (response.ok && response.status === 200) await cache.put(url.href, response);
        })(),
      );
    } else if (!sameOrigin && FORECAST_PATTERNS.some((pattern) => pattern.test(url.pathname))) {
      tasks.push(
        (async () => {
          const cache = await caches.open(DATA_CACHE);
          if (await cache.match(url.href)) return;
          const response = await fetch(url.href, { mode: "cors" });
          if (response.ok && response.status === 200) await storeData(url.href, response);
        })(),
      );
    }
  }
  await Promise.allSettled(tasks);
  await broadcast({ type: "varuna:warmed" });
}

// ---------------------------------------------------------------------------------------------
// Reports queued while offline
// ---------------------------------------------------------------------------------------------

async function postReport(event) {
  const request = event.request;
  const body = await request.clone().text();
  try {
    const response = await fetch(request);
    event.waitUntil(flushReports());
    return response;
  } catch (error) {
    // The page gave up on its own request (a timeout): that is not "offline", and queueing it
    // could send a report the API already has.
    if (error && error.name === "AbortError") throw error;
    await enqueue({
      url: request.url,
      body,
      contentType: request.headers.get("content-type") || "application/json",
      queuedAt: new Date().toISOString(),
    });
    try {
      await self.registration.sync.register(SYNC_TAG);
    } catch {
      // No Background Sync here; the page and the next API read will flush the queue instead.
    }
    const pending = await countQueued();
    event.waitUntil(broadcast({ type: "varuna:report-queued", pending }));
    return new Response(
      JSON.stringify({
        queued: true,
        accepted: false,
        feedback_streets: null,
        pending,
        message:
          "No connection. Your report is saved on this phone and will be sent when the connection returns.",
      }),
      { status: 202, headers: { "content-type": "application/json" } },
    );
  }
}

let flushing = null;

function flushReports(options) {
  if (!flushing) {
    flushing = sendQueued(options).finally(() => {
      flushing = null;
    });
  }
  return flushing;
}

async function sendQueued({ rethrow = false } = {}) {
  // Claimed, not just read: the `/map` and `/report` registrations are two workers sharing one
  // queue, and both wake when the connection returns. Reading the queue from both sent each report
  // twice; a claim taken inside one IndexedDB transaction lets exactly one of them send it.
  const items = await claimQueued();
  if (items.length === 0) return;
  let sent = 0;
  let refused = 0;
  let failure = null;
  for (const [index, item] of items.entries()) {
    try {
      const response = await fetch(item.url, {
        method: "POST",
        headers: { "content-type": item.contentType },
        body: item.body,
        mode: "cors",
      });
      if (response.ok) {
        sent += 1;
        await dequeue(item.id);
      } else if (
        response.status >= 400 &&
        response.status < 500 &&
        ![408, 429].includes(response.status)
      ) {
        // The API read it and said no; sending it again cannot change the answer.
        refused += 1;
        await dequeue(item.id);
      } else {
        // The API answered and failed (5xx, 408, 429). It may have stored the report before it
        // failed - `POST /v1/reports` writes the inbox before it counts nearby streets - so this
        // one keeps its claim and waits out `CLAIM_MS` rather than being sent again at once.
        failure = new Error(`Report replay answered ${response.status}`);
        await Promise.allSettled(items.slice(index + 1).map((rest) => release(rest)));
        break;
      }
    } catch (error) {
      failure = error;
      // No answer at all: hand back this report and every one after it for the next attempt.
      await Promise.allSettled(items.slice(index).map((rest) => release(rest)));
      break;
    }
  }
  const pending = await countQueued();
  if (sent || refused) await broadcast({ type: "varuna:reports-sent", sent, refused, pending });
  if (failure && rethrow) throw failure;
}

function openDb() {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open(DB_NAME, 1);
    open.onupgradeneeded = () => {
      if (!open.result.objectStoreNames.contains(DB_STORE)) {
        open.result.createObjectStore(DB_STORE, { keyPath: "id", autoIncrement: true });
      }
    };
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
}

async function withStore(mode, run) {
  const db = await openDb();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(DB_STORE, mode);
      const result = run(tx.objectStore(DB_STORE));
      tx.oncomplete = () => resolve(result && "result" in result ? result.result : undefined);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

function enqueue(item) {
  return withStore("readwrite", (store) => store.add(item));
}

function dequeue(id) {
  return withStore("readwrite", (store) => store.delete(id));
}

/** How long a claim holds before another worker may take the report over. */
const CLAIM_MS = 60_000;
const WORKER_ID = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

/** Every report no other worker holds, marked as this worker's in the same transaction. */
async function claimQueued() {
  const db = await openDb();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(DB_STORE, "readwrite");
      const store = tx.objectStore(DB_STORE);
      const claimed = [];
      const now = Date.now();
      const all = store.getAll();
      all.onsuccess = () => {
        for (const item of all.result) {
          if (item.claimedBy && now - (item.claimedAt || 0) < CLAIM_MS) continue;
          item.claimedBy = WORKER_ID;
          item.claimedAt = now;
          store.put(item);
          claimed.push(item);
        }
      };
      tx.oncomplete = () => resolve(claimed);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

function release(item) {
  return withStore("readwrite", (store) => store.put({ ...item, claimedBy: null, claimedAt: 0 }));
}

async function countQueued() {
  return (await withStore("readonly", (store) => store.count())) || 0;
}

// ---------------------------------------------------------------------------------------------
// Telling the pages
// ---------------------------------------------------------------------------------------------

async function tell(clientId, message) {
  if (!clientId) return broadcast(message);
  const client = await self.clients.get(clientId);
  if (client) client.postMessage(message);
}

async function broadcast(message) {
  const clients = await self.clients.matchAll({ includeUncontrolled: true, type: "window" });
  for (const client of clients) client.postMessage(message);
}

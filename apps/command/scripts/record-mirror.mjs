// Records the stored replay the deployed site falls back on when its server is not answering
// (ADR-0097, lib/api/resilience.ts).
//
//   node scripts/record-mirror.mjs --api http://127.0.0.1:8765 --app http://127.0.0.1:3100
//
// Run it against an API whose run directory holds only the shipped demo cycles (start one with
// VARUNA_DATA_DIR pointing at an empty folder: it seeds itself from demo/runs) and a console
// built with NEXT_PUBLIC_API_URL set to that API. It:
//   1. opens every screen in a headless browser and notes each GET the page sends to the API,
//      exactly as the page builds it;
//   2. repeats every request that names one replay cycle for each of the other cycles, so the
//      cycle picker works from the mirror too;
//   3. asks the API for each one and stores the answers under public/mirror, keyed the way
//      `mirrorKey` keys a request in the browser.
//
// Requests whose answer changes with time are never stored: citizen reports, today's weather,
// the live socket. The mirror only ever repeats what the server says about the reconstructed
// 2 July 2019 replay.
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, readdirSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

import { chromium } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, value, i, all) => {
    if (value.startsWith("--")) pairs.push([value.slice(2), all[i + 1]]);
    return pairs;
  }, []),
);
const API = (args.api ?? "http://127.0.0.1:8765").replace(/\/+$/, "");
const APP = (args.app ?? "http://127.0.0.1:3100").replace(/\/+$/, "");
const OUT = args.out ?? join(here, "..", "public", "mirror");
const SETTLE_MS = Number(args.settle ?? 15000);

/** Same as `mirrorKey` in lib/api/resilience.ts. */
function mirrorKey(url) {
  const params = new URLSearchParams(url.search);
  params.sort();
  const query = params.toString();
  return query ? `${url.pathname}?${query}` : url.pathname;
}

/**
 * Requests a healthy page may never send but a page whose server is gone does: the wake logic's
 * health probe, and the city-wide bounds a map asks for before it knows a run.
 */
const ALWAYS = ["/healthz", "/v1/nowcast/raster/bounds?city=mumbai"];

/** Paths whose answer depends on the moment it is asked; never stored. */
const LIVE_ONLY = [/^\/v1\/reports/, /^\/v1\/weather/, /^\/v1\/live/, /^\/v1\/ops\//];

const EXTENSIONS = {
  "application/json": "json",
  "application/geo+json": "json",
  "image/png": "png",
  "application/xml": "xml",
  "text/xml": "xml",
  "application/cap+xml": "xml",
  "text/csv": "csv",
  "text/plain": "txt",
  "application/vnd.pmtiles": "pmtiles",
  "application/octet-stream": "bin",
};

/** Screens and what to do on each so its optional layers ask for their data too. */
const SCREENS = [
  { path: "/", wait: 8000 },
  { path: "/console", keys: ["p", "s", "d", "g"] },
  { path: "/console?bundle=MUM-2019-07-02", keys: ["p", "s", "d", "g"] },
  { path: "/map" },
  { path: "/dashboard" },
  { path: "/authority" },
  { path: "/drains" },
  { path: "/route" },
  { path: "/alerts" },
  { path: "/pumps" },
  { path: "/whatif" },
  { path: "/replay" },
  { path: "/onboard" },
  { path: "/verify" },
  { path: "/report" },
];

async function crawl(runIds) {
  const seen = new Set();
  const browser = await chromium.launch({
    args: ["--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"],
  });
  const screens = [
    ...SCREENS,
    ...runIds.map((runId) => ({ path: `/console?run=${encodeURIComponent(runId)}`, keys: ["s", "d"] })),
  ];
  for (const screen of screens) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    page.on("request", (request) => {
      if (request.method() !== "GET") return;
      const url = request.url();
      if (url.startsWith(`${API}/`)) seen.add(url);
    });
    const started = Date.now();
    try {
      await page.goto(APP + screen.path, { waitUntil: "domcontentloaded", timeout: 180_000 });
      await page.waitForTimeout(screen.wait ?? SETTLE_MS);
      for (const key of screen.keys ?? []) {
        await page.keyboard.press(key);
        await page.waitForTimeout(2500);
      }
      // Open the first hotspot, if the screen lists any, so its series is asked for.
      const row = page.locator("[data-hotspot-row], [data-testid='hotspot-row']").first();
      if (await row.count()) {
        await row.click({ timeout: 5000 }).catch(() => {});
        await page.waitForTimeout(3000);
      }
    } catch (error) {
      console.warn(`  ${screen.path}: ${String(error).slice(0, 160)}`);
    }
    console.log(`  ${screen.path.padEnd(70)} ${Date.now() - started} ms, ${seen.size} requests so far`);
    await context.close();
  }
  await browser.close();
  return [...seen];
}

/** Repeats every request naming one replay cycle for each of the others. */
function expand(urls, runIds) {
  const out = new Set(urls);
  for (const raw of urls) {
    const url = new URL(raw);
    const named = runIds.find((id) => raw.includes(encodeURIComponent(id)) || raw.includes(id));
    if (!named) continue;
    for (const other of runIds) {
      if (other === named) continue;
      const copy = new URL(url.href.split(named).join(other).split(encodeURIComponent(named)).join(encodeURIComponent(other)));
      out.add(copy.href);
    }
  }
  return [...out];
}

/**
 * Fill pass: browse a console pointed at a server that does not answer, with the mirror in place,
 * and record whatever it asked for that the mirror could not answer. A page whose server is gone
 * takes paths a healthy page never does - a city-wide bounds request instead of a run's, the
 * health probe the wake logic sends - so recording only the healthy pages leaves holes. Repeats
 * until a pass finds nothing new.
 *
 *   node scripts/record-mirror.mjs --api http://127.0.0.1:8765 \
 *     --dead-app http://localhost:3100 --dead-api https://<the stopped API>
 */
async function fill(deadApp, deadApi) {
  const indexPath = join(OUT, "index.json");
  const index = JSON.parse(readFileSync(indexPath, "utf8"));
  const runs = await (await fetch(`${API}/v1/runs`)).json();
  const runIds = (runs.runs ?? runs).map((run) => run.run_id).filter((id) => id.endsWith("-baked"));
  for (let pass = 1; pass <= 4; pass += 1) {
    const missing = new Set();
    const browser = await chromium.launch({
      args: ["--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"],
    });
    const screens = [
      ...SCREENS,
      ...runIds.map((runId) => ({ path: `/console?run=${encodeURIComponent(runId)}`, keys: ["s", "d"] })),
    ];
    for (const screen of screens) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const page = await context.newPage();
      page.on("request", (request) => {
        if (request.method() !== "GET" || !request.url().startsWith(`${deadApi}/`)) return;
        const url = new URL(request.url());
        if (LIVE_ONLY.some((pattern) => pattern.test(url.pathname))) return;
        if (!index.entries[mirrorKey(url)]) missing.add(mirrorKey(url));
      });
      try {
        await page.goto(deadApp + screen.path, { waitUntil: "domcontentloaded", timeout: 180_000 });
        await page.waitForTimeout(screen.wait ?? SETTLE_MS);
        for (const key of screen.keys ?? []) {
          await page.keyboard.press(key);
          await page.waitForTimeout(2500);
        }
      } catch (error) {
        console.warn(`  ${screen.path}: ${String(error).slice(0, 160)}`);
      }
      await context.close();
    }
    await browser.close();
    console.log(`fill pass ${pass}: ${missing.size} requests the mirror could not answer`);
    let added = 0;
    for (const key of [...missing].sort()) {
      if (await store(key, index.entries)) added += 1;
    }
    writeFileSync(indexPath, `${JSON.stringify(index, null, 1)}\n`);
    console.log(`  recorded ${added}: ${[...missing].slice(0, 12).join(", ")}${missing.size > 12 ? ", ..." : ""}`);
    if (added === 0) break;
  }
}

/** Asks the recording API for `key` and stores the answer; false when it is not a 200. */
async function store(key, entries) {
  const response = await fetch(`${API}${key}`, { headers: { "accept-encoding": "identity" } });
  if (response.status !== 200) return false;
  const type = (response.headers.get("content-type") ?? "application/octet-stream").split(";")[0].trim();
  const raw = Buffer.from(await response.arrayBuffer());
  const zipped = !type.startsWith("image/");
  const body = zipped ? gzipSync(raw, { level: 9 }) : raw;
  const hash = createHash("sha1").update(key).digest("hex").slice(0, 20);
  const file = zipped ? `${hash}.gz` : `${hash}.${EXTENSIONS[type] ?? "bin"}`;
  writeFileSync(join(OUT, file), body);
  entries[key] = zipped ? { file, type, encoding: "gzip" } : { file, type };
  return true;
}

async function main() {
  if (args["dead-app"]) {
    await fill(args["dead-app"].replace(/\/+$/, ""), String(args["dead-api"] ?? "").replace(/\/+$/, ""));
    return;
  }
  const runs = await (await fetch(`${API}/v1/runs`)).json();
  const runIds = (runs.runs ?? runs).map((run) => run.run_id).filter((id) => id.endsWith("-baked"));
  console.log(`API ${API}: ${runIds.length} baked cycles`);
  if (runIds.some((id) => id.endsWith("-live"))) throw new Error("The recording API holds a live run; use a demo-only data dir.");

  console.log(`Crawling ${APP}`);
  const crawled = await crawl(runIds);
  const wanted = expand(crawled, runIds).filter((raw) => {
    const path = new URL(raw).pathname;
    return !LIVE_ONLY.some((pattern) => pattern.test(path));
  });
  console.log(`${crawled.length} requests seen, ${wanted.length} to store after expansion`);

  mkdirSync(OUT, { recursive: true });
  for (const name of readdirSync(OUT)) unlinkSync(join(OUT, name));

  const entries = {};
  let skipped = 0;
  for (const raw of [...wanted, ...ALWAYS.map((path) => `${API}${path}`)].sort()) {
    const key = mirrorKey(new URL(raw));
    if (entries[key]) continue;
    if (!(await store(key, entries))) skipped += 1;
  }
  const bytes = Object.values(entries).reduce((sum, entry) => sum + statSync(join(OUT, entry.file)).size, 0);
  const index = {
    version: 1,
    generated_at: new Date().toISOString(),
    note: "Recorded from the VARUNA API for the reconstructed 2 July 2019 replay; see scripts/record-mirror.mjs.",
    entries,
  };
  writeFileSync(join(OUT, "index.json"), `${JSON.stringify(index, null, 1)}\n`);
  console.log(`Stored ${Object.keys(entries).length} answers, ${(bytes / 1e6).toFixed(1)} MB; ${skipped} non-200 skipped.`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

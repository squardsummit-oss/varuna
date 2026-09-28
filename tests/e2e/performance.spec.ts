import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";

import { chromium, type BrowserContext } from "@playwright/test";

import { expect, test, type Page, type Request } from "./requirements";

/**
 * Section 14's performance budgets, measured rather than asserted (task P10.4).
 *
 * P10.4 has been marked partial since it was written: the segment table and the depth writers had
 * been made fast (ADR-0050, ADR-0051), products had been timed, and everything else in section
 * 14's table - the console's first render, the scrub, the map's frame rate, the API's percentiles
 * and, above all, the browser tab's memory - had either been measured once by hand in a session
 * nobody could repeat or, in the case of memory, never measured at all. This file is the
 * repeatable half. What it cannot measure honestly it refuses to measure, and names the
 * instrument that would.
 *
 * **It will not report a number from a dev build.** Every performance figure in this repository is
 * a production-build figure, because `next dev` compiles a route on first visit and invents stalls
 * that are not in the product: the same landing page measured LCP 3.5 s on a dev build and 1.6 s
 * on a production one during P9.2. `assertProductionBuild` fails the run rather than let a dev
 * number be written down as if it meant something. Bring the server up with:
 *
 *     cd apps/command
 *     VARUNA_DIST_DIR=.next-perf NEXT_PUBLIC_API_URL=http://127.0.0.1:8001 npx next build
 *     VARUNA_DIST_DIR=.next-perf npx next start -p 3100
 *
 * and point the run at it with `PLAYWRIGHT_PERF_URL`. `VARUNA_DIST_DIR` exists (see
 * `apps/command/next.config.ts`) so that build does not land on top of the `.next` a running
 * `make demo` is serving, and `NEXT_PUBLIC_API_URL` has to be passed because `.env.production`
 * points the built console at the deployed Railway API - measuring that would be measuring a
 * network, not this laptop.
 *
 * The API has to be told about the new origin, because `Settings.cors_origins` defaults to
 * `http://localhost:3000` alone and nothing else. Without it the console loads, paints its
 * shell and shows "No runs yet" - every fetch blocked before it left the browser - which reads
 * exactly like an empty run directory and is how the first attempt at this file measured a
 * console that had no run in it:
 *
 *     VARUNA_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3100 \
 *       uv run uvicorn varuna_api.main:app --host 127.0.0.1 --port 8001
 *
 * **Frame rate is the one budget a headless run cannot settle.** Playwright's Chromium falls back
 * to SwiftShader, a software rasteriser, and a WebGL map measured on it says nothing about the
 * laptop's Iris Xe. `readRenderer` reads `WEBGL_debug_renderer_info` and the frame-rate test
 * prints the renderer beside the figure; on a software renderer it records the number and states
 * that it does not decide the budget. The deciding measurement is this same rAF counter run in a
 * browser with a GPU, which is how the 44.9 and 57.8 fps figures in `docs/QA.md` were taken.
 *
 * **Verdicts, not failures.** Most of these budgets are missed today and have been missed in
 * writing since the phase rows were written. A suite that went red on each of them would be a
 * suite nobody runs, so each test records `MET` or `MISSED` against the budget, prints the
 * measurement with the machine's python-process count beside it as every timing in this
 * repository does, and asserts only that a measurement was taken and that it has not drifted past
 * a stated multiple of the budget. Those multiples are in `GUARD`, each with the regression it is
 * there to catch. It is the shape `services/sky`'s regression guard settled on, for the same
 * reason (ADR-0040): a green test named after a budget it does not check is worse than a red one.
 */

/** Where the production console is served. Never the dev server on 3000. */
const PERF_URL = process.env.PLAYWRIGHT_PERF_URL ?? "http://127.0.0.1:3100";
/** Where the API is. Defaults to a second instance so a measurement never waits on the demo's. */
const PERF_API = process.env.PLAYWRIGHT_PERF_API ?? "http://127.0.0.1:8001";

const NAV = 120_000;
const SETTLE = 60_000;

/** Section 14's table, in the units each budget is stated in. */
const BUDGET = {
  landingLcpMs: 2_500,
  landingCls: 0.1,
  consoleFirstRenderMs: 2_000,
  scrubRestyleMs: 16,
  mapFps: 55,
  apiSegmentsMs: 200,
  apiRouteMs: 300,
  apiWhatIfMs: 1_000,
  apiPhysicsCheckMs: 10_000,
  runSwapMs: 200,
  tabMemoryBytes: 1024 * 1024 * 1024,
} as const;

/**
 * How far past its budget a measurement may drift before the test goes red.
 *
 * Each multiple is the smallest round one that leaves today's measurement passing, so a guard is
 * a ratchet on the number as it stands rather than a second, softer budget. Tightening one when
 * the real number improves is the point of having them.
 */
const GUARD = {
  // Landing LCP measured 976 ms median on a production build in P9.1 and 1.6 s in P9.2. 2x the
  // budget catches the return of the eagerly bundled console map, which cost 3.5 s.
  landingLcpMs: 5_000,
  // CLS has measured 0 on every production build since P9.2. Anything over the budget is a
  // layout that moved under the reader, which is never acceptable, so this guard is the budget.
  landingCls: BUDGET.landingCls,
  // This was written as "the console load is dominated by decoding 36 depth PNGs", which was a
  // reasonable reading of P6.3 - the decode is paid up front so a scrub is a texture swap - and
  // is not what the load does. Measured on 2026-09-24: all 36 rasters are fetched and decoded by
  // 811 ms after navigation start, the 8.08 MB street layer parses in 24 ms, and the run stamp is
  // in the DOM at about 1.4 s; the rest of the four to six seconds is the main thread blocked,
  // 3.6-5.7 s of it over eight long tasks. 8x the budget still catches a regression to lazy
  // decoding, which is what a stuttering scrub used to be, but it is guarding a number whose
  // largest term is script evaluation and deck.gl's first build of ~28,000 paths.
  consoleUsableMs: 16_000,
  // The share of frames during a scrub that are dropped frames. 1.0 would mean every step drops
  // one; half is the point at which the scrub stops reading as continuous.
  scrubLongFrameShare: 0.5,
  apiRouteMs: 2 * BUDGET.apiRouteMs,
  apiWhatIfMs: 2 * BUDGET.apiWhatIfMs,
  apiPhysicsCheckMs: 2 * BUDGET.apiPhysicsCheckMs,
} as const;

interface Verdict {
  name: string;
  budget: string;
  measured: string;
  /** null where the instrument cannot decide the budget, which is not the same as a miss. */
  met: boolean | null;
  note?: string;
}

const VERDICTS: Verdict[] = [];

/** Record a budget verdict, print it, and hang it on the test as an annotation. */
function verdict(v: Verdict): void {
  VERDICTS.push(v);
  const mark = v.met === null ? "NOT DECIDED" : v.met ? "MET" : "MISSED";
  const line =
    `BUDGET ${v.name}: budget ${v.budget}, measured ${v.measured} - ${mark}` +
    (v.note ? ` (${v.note})` : "");
  // eslint-disable-next-line no-console -- the printed table is this file's product.
  console.log(line);
  test.info().annotations.push({ type: "budget", description: line });
}

test.afterAll(() => {
  if (!VERDICTS.length) return;
  const lines = VERDICTS.map((v) => {
    const mark = v.met === null ? "NOT DECIDED" : v.met ? "MET       " : "MISSED    ";
    return `  ${mark}  ${v.name}: ${v.measured} against ${v.budget}`;
  });
  // eslint-disable-next-line no-console -- ditto.
  console.log(
    `\n== SPEC.md section 14, ${VERDICTS.length} budgets, ` +
      `${processCount()} python processes ==\n${lines.join("\n")}`,
  );
});

/**
 * How many python processes are on the machine.
 *
 * Every timing in this repository carries this count beside it, because the load term is larger
 * than several of the budgets: the same Sky cycle measured 7.92 s at ten processes and 14.22 s
 * with eight build jobs and a test suite running (docs/QA.md). Returns -1 where it cannot be
 * read, which is honest, rather than a 0 that would read as "the machine was idle".
 */
function processCount(): number {
  if (process.platform !== "win32") return -1;
  try {
    const out = execFileSync(
      "powershell",
      ["-NoProfile", "-Command", "(Get-Process python -ErrorAction SilentlyContinue).Count"],
      { encoding: "utf8", timeout: 20_000 },
    );
    return Number.parseInt(out.trim(), 10);
  } catch {
    return -1;
  }
}

/**
 * The private working set of every browser process Playwright launched, in bytes.
 *
 * Section 14's budget is "browser tab memory during a full replay < 1 GB" and nothing inside the
 * page reports that. `performance.memory` is the JS heap only and misses the decoded textures,
 * which on this screen are the run's 36 depth rasters and the single largest thing the console
 * holds; `measureUserAgentSpecificMemory` needs the cross-origin isolation the app does not set.
 * Chrome's own task-manager figure is the operating system's private working set, so this reads
 * that, selecting processes by executable path so a Chrome the user has open of their own is
 * never counted.
 *
 * It is a **ceiling on a tab, not a tab**: it includes the browser process, the GPU process and
 * any utility process as well as the one renderer. With `workers: 1` and a single page open the
 * renderer dominates it, and over-reporting against a budget is the safe direction to be wrong in.
 */
function browserPrivateBytes(exeName?: string): { total: number; processes: number } | null {
  if (process.platform !== "win32") return null;
  try {
    // Selected by path, not by name. Playwright's headless runs are `headless_shell.exe` and its
    // headed ones `chrome.exe`; filtering on the name alone found neither in the first run of
    // this file and reported "could not be read" while a browser was plainly sitting there.
    // `exeName` narrows it to one of the two, which the GPU block needs, because when it runs
    // the runner's own headless browser is alive beside the headed one being measured.
    const name = exeName ? ` -and $_.Name -eq '${exeName}'` : "";
    const script =
      "Get-CimInstance Win32_Process | " +
      `Where-Object { $_.ExecutablePath -like '*ms-playwright*'${name} } | ` +
      "Measure-Object -Property WorkingSetSize -Sum | " +
      'ForEach-Object { "$($_.Count) $($_.Sum)" }';
    const out = execFileSync("powershell", ["-NoProfile", "-Command", script], {
      encoding: "utf8",
      timeout: 30_000,
    });
    const [count, sum] = out.trim().split(/\s+/);
    const processes = Number.parseInt(count ?? "0", 10);
    const total = Number.parseInt(sum ?? "0", 10);
    if (!Number.isFinite(total) || !processes) return null;
    return { total, processes };
  } catch {
    return null;
  }
}

/**
 * Refuse to measure a dev build.
 *
 * Next's dev server ships its own client - the hot-reload socket and the dev overlay - and names
 * its chunks after module paths where a production build emits content hashes. Either signal
 * alone would do; both are checked so that a future Next release dropping one does not silently
 * turn this guard off and leave the numbers looking merely disappointing.
 */
async function assertProductionBuild(page: Page): Promise<void> {
  const html = await page.content();
  const devClient = /_next\/static\/chunks\/[^"']*(_dev_|react-refresh|hot-reloader)/i.test(html);
  expect(
    devClient,
    `${PERF_URL} is serving a development build. Every number in section 14 is a production-build ` +
      "number: a dev build compiles a route on first visit and invents stalls that are not in the " +
      "product. Build into .next-perf and `next start` it - see the note at the top of this file.",
  ).toBe(false);
}

/** The GPU string Chrome reports, so a software-rendered frame rate is never mistaken for one. */
async function readRenderer(page: Page): Promise<string> {
  return page.evaluate(() => {
    const gl = document.createElement("canvas").getContext("webgl2");
    if (!gl) return "no webgl2";
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    return ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : "renderer info withheld";
  });
}

function isSoftware(renderer: string): boolean {
  return /swiftshader|llvmpipe|software|microsoft basic/i.test(renderer);
}

/** Install the web-vitals observers, which have to exist before the page paints anything. */
async function installWebVitals(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __vitals: { lcp: number; cls: number } };
    w.__vitals = { lcp: 0, cls: 0 };
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) w.__vitals.lcp = entry.startTime;
    }).observe({ type: "largest-contentful-paint", buffered: true });
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const shift = entry as PerformanceEntry & { value: number; hadRecentInput: boolean };
        if (!shift.hadRecentInput) w.__vitals.cls += shift.value;
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
}

/** Wait until the console has a run: the run stamp is the first thing only a loaded run can draw. */
async function waitForRun(page: Page): Promise<void> {
  await expect(page.getByText(/MUM-\d{8}T\d{4}Z/).first()).toBeVisible({ timeout: SETTLE });
}

/** What the page itself saw of its own boot, as `installBootProbes` recorded it. */
interface BootProbe {
  /** ms from navigation start to the run stamp entering the DOM, or null if it never did. */
  stampInDomMs: number | null;
  /** Total main-thread blocking, in ms, and the longest single block. */
  longTaskMs: number;
  longestTaskMs: number;
  longTasks: number;
}

/**
 * Record, inside the page, when the run stamp arrives and how long the main thread is blocked.
 *
 * `waitForRun` measures when Playwright could *see* the stamp, and that is the number a reader
 * waits for. On one run of the GPU block on 2026-09-24 it was 4,277 ms while the same page had
 * the stamp in its DOM at 1,525 ms and its main thread blocked for 3,597 ms over eight long
 * tasks, the longest 1,824 ms: the console had the run on screen in about a second and a half
 * and then froze. A single figure hides which of those two things is wrong, and they have
 * different fixes - the first is the load's critical path (fetches, decode), the second is the
 * main thread (script evaluation, deck.gl building its layers and linking their shaders).
 *
 * The MutationObserver is armed in an init script so it is running before React hydrates, and
 * watches `characterData` as well as `childList` because the stamp's text is swapped into an
 * element that is already there rather than appended. The long-task observer is `buffered`, so
 * tasks that ran before the observer was constructed are still counted.
 *
 * Both are read-only observers: nothing here changes what the page does, so a boot measured with
 * them is the boot that ships. What it cannot see is a main thread blocked so hard that the
 * observer callback itself is starved - the totals are therefore a floor, not a ceiling.
 */
async function installBootProbes(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __boot: BootProbeState };
    w.__boot = { stamp: null, long: [] };
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) w.__boot.long.push(entry.duration);
    }).observe({ type: "longtask", buffered: true });

    const look = () => {
      if (w.__boot.stamp !== null) return;
      if (/MUM-\d{8}T\d{4}Z/.test(document.body?.innerText ?? "")) {
        w.__boot.stamp = performance.now();
      }
    };
    const arm = () => {
      look();
      new MutationObserver(look).observe(document.body, {
        childList: true,
        subtree: true,
        characterData: true,
      });
    };
    if (document.body) arm();
    else document.addEventListener("DOMContentLoaded", arm);
  });
}

interface BootProbeState {
  stamp: number | null;
  long: number[];
}

async function readBootProbes(page: Page): Promise<BootProbe> {
  return page.evaluate(() => {
    const state = (window as unknown as { __boot?: BootProbeState }).__boot;
    const long = state?.long ?? [];
    return {
      stampInDomMs:
        state?.stamp === null || state?.stamp === undefined ? null : Math.round(state.stamp),
      longTaskMs: Math.round(long.reduce((a, b) => a + b, 0)),
      longestTaskMs: Math.round(long.length ? Math.max(...long) : 0),
      longTasks: long.length,
    };
  });
}

/**
 * Which library each built chunk is, by looking for its own name inside it.
 *
 * A production profile names `3mqius987sx6w.js:30`, which tells a reader nothing. Turbopack's
 * chunk hashes change on every build, so this cannot be a table; it is a fingerprint taken from
 * the build that is actually being measured. The markers are each library's own identifiers, and
 * a chunk that matches none is the app's own code, which is the answer that matters most - "the
 * scrub is our render, not theirs" is a different finding from "the scrub is deck.gl".
 *
 * Coarse on purpose, and stated as coarse wherever it is quoted: a chunk that holds zod *and* the
 * api layer is counted once, under both names joined, because a sampling profiler cannot split a
 * chunk it only knows by URL. Read the rows as "which bundle the work was in", not as a per-
 * package attribution.
 */
function fingerprintChunks(distDir: string): Map<string, string> {
  const marks: [string, RegExp][] = [
    ["react-dom", /react-dom|__reactFiber/],
    ["deck.gl+luma", /deck\.gl|@luma\.gl/],
    ["loaders.gl", /loaders\.gl/],
    ["3d-tiles+draco", /3d-tiles|draco/],
    ["recharts", /recharts/],
    ["maplibre", /maplibre/],
    ["zod", /zod/],
    ["motion", /framer|motion-dom/],
  ];
  const out = new Map<string, string>();
  const dir = `${distDir}/static/chunks`;
  let names: string[] = [];
  try {
    names = readdirSync(dir).filter((f) => f.endsWith(".js"));
  } catch {
    return out;
  }
  for (const file of names) {
    let src = "";
    try {
      src = readFileSync(`${dir}/${file}`, "utf8");
    } catch {
      continue;
    }
    const hits = marks.filter(([, re]) => re.test(src)).map(([name]) => name);
    out.set(file, hits.length ? hits.join("+") : "app");
  }
  return out;
}

/**
 * Sample the main thread across `steps` arrow presses and split the busy time by chunk.
 *
 * The scrub budget is the one number in section 14 that names a *cause* in its own wording -
 * 7.2 calls a step "a texture swap and one colour accessor" - so a verdict that only says
 * "19.0 ms against 16 ms" cannot tell anyone whether that sentence is true. This says which
 * bundle the milliseconds were in.
 *
 * **Read it as a spread, not a number.** Two correct runs on 2026-09-24, twenty moving steps
 * each on the same build and the same GPU, split it differently: one gave react-dom 563 ms and
 * deck.gl with loaders.gl 92 ms of 1,359 ms busy, the other react-dom 251 ms and deck.gl with
 * loaders.gl 627 ms of 2,740 ms. What both agree on is the shape - the app's own code and React
 * are a large share of a step, and the map is not the whole of it - and that is as far as this
 * instrument can be pushed. Part of the spread is deck.gl's animation clock, which draws the
 * surcharge pulse (M8) and the reversed-flow dash (M9) every frame whether or not anything
 * scrubbed, and which no window that contains a scrub can exclude.
 *
 * A 100 us sampling interval over a ~2 s window is ~1,500 samples, enough to separate the top
 * few buckets and not enough to trust a bucket worth a handful of samples; anything under about
 * 2 % is noise and is printed only so the total adds up.
 */
async function profileScrub(
  page: Page,
  context: BrowserContext,
  steps: number,
  chunks: Map<string, string>,
  key: "ArrowLeft" | "ArrowRight" = "ArrowLeft",
): Promise<string> {
  const cdp = (await context.newCDPSession(page)) as unknown as CdpLike;
  await cdp.send("Profiler.enable");
  await cdp.send("Profiler.setSamplingInterval", { interval: 100 });
  const before = await scrubHandle(page).inputValue();
  await cdp.send("Profiler.start");
  for (let i = 0; i < steps; i++) await page.keyboard.press(key);
  await page.waitForTimeout(300);
  const { profile } = (await cdp.send("Profiler.stop")) as { profile: CpuProfile };
  const after = await scrubHandle(page).inputValue();

  // Refuse rather than mislead. The console clamps the step at both ends, so a caller that
  // profiles arrows in the direction the scrub has already run to measures a *still* map and
  // gets a table dominated by deck.gl's own animation clock - the surcharge pulse and the
  // reversed-flow dash, which run every frame whether or not anything scrubbed. The first
  // version of this helper did exactly that and reported deck.gl 1,479 ms and react-dom 8 ms
  // for twenty presses that moved nothing.
  if (before === after) {
    return (
      `the scrub did not move (step stayed at ${before}), so nothing was measured: ` +
      `${steps} ${key} presses were clamped at the end of the run`
    );
  }

  const byId = new Map(profile.nodes.map((n) => [n.id, n]));
  const windowMs = (profile.endTime - profile.startTime) / 1000;
  const total = profile.samples.length;
  if (!total) return "the profiler returned no samples";
  const bucket = new Map<string, number>();
  for (const id of profile.samples) {
    const frame = byId.get(id)?.callFrame;
    const url = String(frame?.url ?? "");
    const file = url.split("/").pop() ?? "";
    const key = url
      ? (chunks.get(file) ?? `other(${file})`)
      : frame?.functionName === "(idle)"
        ? "(idle)"
        : "(browser)";
    bucket.set(key, (bucket.get(key) ?? 0) + 1);
  }
  const idle = bucket.get("(idle)") ?? 0;
  const busy = ((total - idle) / total) * windowMs;
  const rows = [...bucket.entries()]
    .filter(([k]) => k !== "(idle)")
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([k, n]) => `${k} ${((n / total) * windowMs).toFixed(0)} ms`);
  return (
    `${busy.toFixed(0)} ms of main thread over ${steps} steps ` +
    `(step ${before} to ${after}), in: ${rows.join(", ")}`
  );
}

/** Only the two CDP calls this file makes; Playwright's own session type is not exported. */
interface CdpLike {
  send: (method: string, params?: Record<string, unknown>) => Promise<unknown>;
}
interface CpuProfile {
  nodes: { id: number; callFrame: { functionName: string; url: string } }[];
  samples: number[];
  startTime: number;
  endTime: number;
}

/** The boot probe's two figures as one clause, for a verdict note. */
function bootNote(boot: BootProbe): string {
  const stamp = boot.stampInDomMs === null ? "never" : `${boot.stampInDomMs} ms`;
  return (
    `the run stamp entered the DOM at ${stamp}, and the main thread was blocked for ` +
    `${boot.longTaskMs} ms over ${boot.longTasks} long tasks, the longest ${boot.longestTaskMs} ms`
  );
}

/**
 * The console's own step slider, which exists only once the run is drawable.
 *
 * `console-screen.tsx` renders it inside the block gated on the decoded `RunDepth`, so its
 * presence is the signal that P6.3's up-front decode of all 36 frames has finished and the map
 * can be scrubbed. Matched exactly, because the time bar above it carries a slider whose label
 * starts with the same words - and which Base UI renders as *two* thumbs, "handle 1 of 2" and
 * "handle 2 of 2", for a single-valued scrub.
 */
function scrubHandle(page: Page) {
  return page.getByRole("slider", { name: "Scrub the forecast", exact: true });
}

/**
 * Wait until the page has started no new request for `quietMs`, or `capMs` has passed.
 *
 * Not `waitForLoadState("networkidle")`, which wants *two* connections or fewer and never
 * settles on a page holding a WebSocket - `/console` holds `WS /v1/live` for its whole life.
 * This watches only when a request last *started*, which is the question being asked: has the
 * load stopped fetching, so that anything fetched from here is the thing under test?
 *
 * Returning at the cap rather than throwing is deliberate. A page that never goes quiet is worth
 * measuring anyway - the caller's own count will show what it was still doing - and a timeout
 * here would turn a slow machine into a red test about nothing.
 */
async function waitForQuiet(page: Page, quietMs: number, capMs: number): Promise<void> {
  let last = Date.now();
  const bump = () => {
    last = Date.now();
  };
  page.on("request", bump);
  try {
    const deadline = Date.now() + capMs;
    while (Date.now() < deadline && Date.now() - last < quietMs) {
      await page.waitForTimeout(250);
    }
  } finally {
    page.off("request", bump);
  }
}

/** The distinct hosts in a list of "METHOD url" lines, named, so a reader can see whose they are. */
function hostList(requests: readonly string[]): string {
  const hosts = new Set<string>();
  for (const line of requests) {
    try {
      hosts.add(new URL(line.slice(line.indexOf(" ") + 1)).host);
    } catch {
      hosts.add("(unparsed)");
    }
  }
  return [...hosts].join(", ");
}

function percentile(sorted: readonly number[], p: number): number {
  if (!sorted.length) return Number.NaN;
  return sorted[Math.min(sorted.length - 1, Math.round(p * (sorted.length - 1)))];
}

const mb = (bytes: number): string => `${(bytes / 1048576).toFixed(0)} MB`;

// Serial: these tests compete for the machine, and a frame rate measured beside another page
// loading is a measurement of the other page.
test.describe.configure({ mode: "serial" });

test.describe("section 14, the browser budgets", () => {
  test("landing: LCP and CLS", async ({ page }) => {
    await installWebVitals(page);
    await page.goto(`${PERF_URL}/`, { waitUntil: "load", timeout: NAV });
    await assertProductionBuild(page);
    // The hero's globe intro (M26) runs for about 4 s and nothing may move under it, so CLS is
    // read after it has finished - which is when a reader would have noticed a shift.
    await page.waitForTimeout(6_000);

    const vitals = await page.evaluate(
      () => (window as unknown as { __vitals: { lcp: number; cls: number } }).__vitals,
    );

    verdict({
      name: "landing LCP",
      budget: `${BUDGET.landingLcpMs} ms`,
      measured: `${Math.round(vitals.lcp)} ms`,
      met: vitals.lcp > 0 && vitals.lcp < BUDGET.landingLcpMs,
      note: `${processCount()} python processes`,
    });
    verdict({
      name: "landing CLS",
      budget: `${BUDGET.landingCls}`,
      measured: vitals.cls.toFixed(4),
      met: vitals.cls < BUDGET.landingCls,
    });

    expect(vitals.lcp, "no largest-contentful-paint entry was recorded at all").toBeGreaterThan(0);
    expect(vitals.lcp, "landing LCP drifted past twice its budget").toBeLessThan(
      GUARD.landingLcpMs,
    );
    expect(vitals.cls, "the landing layout shifted under the reader").toBeLessThan(
      GUARD.landingCls,
    );
  });

  test("console: first render after the run loads", async ({ page }) => {
    await installBootProbes(page);
    const started = Date.now();
    await page.goto(`${PERF_URL}/console`, { waitUntil: "domcontentloaded", timeout: NAV });
    await assertProductionBuild(page);
    await waitForRun(page);
    const runVisible = Date.now() - started;

    // Two moments, because they are two different products. The run stamp appears when the run's
    // metadata lands and is what "first meaningful render" means to a reader; the step slider
    // appears only once `loadRunDepth` has resolved, which is after all 36 depth frames have been
    // decoded to `ImageBitmap`s, and is when the screen can actually be used.
    await expect(scrubHandle(page)).toBeEnabled({ timeout: SETTLE });
    const usable = Date.now() - started;
    const boot = await readBootProbes(page);

    // The verdict stays on the *observable* figure, which is the conservative one and the one a
    // reader lives with: a page whose main thread is blocked has not rendered to anybody, however
    // complete its DOM is. The boot probe's two numbers go in the note because they say which of
    // the two halves to fix, and on 2026-09-24 they said something the single figure hid - the
    // stamp was in the DOM at 978 ms and the thread was then blocked for 2,116 ms in one task.
    verdict({
      name: "console first render (run on screen)",
      budget: `${BUDGET.consoleFirstRenderMs} ms`,
      measured: `${runVisible} ms`,
      met: runVisible < BUDGET.consoleFirstRenderMs,
      note: `scrub usable at ${usable} ms; ${bootNote(boot)}; ${processCount()} python processes`,
    });

    expect(runVisible, "the console never rendered a run").toBeGreaterThan(0);
    expect(usable, "the console load drifted past eight times its budget").toBeLessThan(
      GUARD.consoleUsableMs,
    );
  });

  test("console: the scrub restyles inside a frame and touches no network", async ({ page }) => {
    // 35 steps, each waiting for a frame, is minutes rather than seconds on a software
    // rasteriser, where a frame is about a second.
    test.setTimeout(600_000);
    await page.goto(`${PERF_URL}/console`, { waitUntil: "domcontentloaded", timeout: NAV });
    await assertProductionBuild(page);
    await waitForRun(page);
    await expect(scrubHandle(page)).toBeEnabled({ timeout: SETTLE });
    const renderer = await readRenderer(page);
    const software = isSoftware(renderer);
    // Deliberately *not* focused. The console's key handler ignores a keydown whose target is an
    // INPUT, SELECT or TEXTAREA - so that typing in a field never scrubs the map - and the step
    // slider is an INPUT. Focusing it would make the arrow keys move the slider's own value and
    // leave the map where it was, which would measure a native range input rather than a restyle.
    await page.locator("body").click({ position: { x: 5, y: 5 } });

    // Let the load finish before measuring the scrub, and say what it was still fetching.
    //
    // Without this the test is a race it sometimes loses: on 2026-09-24 it recorded 0 requests on
    // one run and, on a slower one the same evening, twelve - all of them Esri label tiles from
    // `server.arcgisonline.com`, the basemap's own streaming (ADR-0034), still arriving when the
    // first arrow was pressed. Those are a one-time basemap load and have nothing to do with
    // stepping, but they are indistinguishable from a real regression once they are in the list.
    // Waiting for quiet keeps the assertion below strict - zero requests of any kind, which is
    // what 7.2 says - and makes it mean the scrub rather than the tail of the load.
    const settling: string[] = [];
    const noteSettling = (r: Request) => settling.push(`${r.method()} ${r.url()}`);
    page.on("request", noteSettling);
    await waitForQuiet(page, 2_000, 30_000);
    page.off("request", noteSettling);

    // Every request from here to the end of the scrub. 7.2's acceptance criterion is that there
    // are none: the run was preloaded, so a step is a texture swap and one colour accessor.
    const during: string[] = [];
    const record = (r: Request) => during.push(`${r.method()} ${r.url()}`);
    page.on("request", record);

    // Event Timing is the instrument the budget is written in: `duration` is the interval from
    // the key arriving to the frame that shows its effect, which is what "restyle" means to the
    // hand on the arrow key. Chrome clamps `durationThreshold` to a 16 ms floor - exactly section
    // 14's number - so an entry that appears is a step that missed the budget and a step that
    // produces no entry made it. `processingEnd - processingStart` separates the handler's own
    // work from the frame it then waited for, which is how a slow accessor is told from a busy
    // compositor.
    await page.evaluate(() => {
      const w = window as unknown as { __events: { duration: number; handler: number }[] };
      w.__events = [];
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          const e = entry as PerformanceEventTiming;
          if (e.name !== "keydown") continue;
          w.__events.push({ duration: e.duration, handler: e.processingEnd - e.processingStart });
        }
      }).observe({ type: "event", durationThreshold: 16 });

      const f = window as unknown as { __frames: number[] };
      f.__frames = [];
      let last = performance.now();
      const tick = () => {
        const now = performance.now();
        f.__frames.push(now - last);
        last = now;
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });

    // 35 plain arrows walk every one of the run's 36 frames. A plain ArrowRight is what the
    // console's own handler reads as one step of the depth raster; Ctrl and Shift change the
    // *time bar's* lead by 5 and 60 minutes through `lib/shortcuts.ts`, which is a different
    // piece of state and does not move the map.
    const STEPS = 35;
    for (let i = 0; i < STEPS; i++) await page.keyboard.press("ArrowRight");
    await page.waitForTimeout(500);
    page.off("request", record);

    const { events, frames } = await page.evaluate(() => {
      const w = window as unknown as {
        __events: { duration: number; handler: number }[];
        __frames: number[];
      };
      return { events: w.__events, frames: w.__frames };
    });

    const over = events.length;
    const worst = over ? Math.max(...events.map((e) => e.duration)) : 0;
    const worstHandler = over ? Math.max(...events.map((e) => e.handler)) : 0;
    // A frame interval over 32 ms during a scrub is a dropped frame: that step's work did not fit.
    const longFrames = frames.filter((d) => d > 32).length;
    const share = frames.length ? longFrames / frames.length : 0;
    const sorted = [...frames].sort((a, b) => a - b);

    // On a software rasteriser the paint leg is the rasteriser, not the restyle: the run below
    // measured a p50 frame interval of 1,038 ms on SwiftShader, which is the whole 1440 x 900
    // map being drawn in software at one frame a second, and says nothing about a laptop with a
    // GPU. The handler leg - the store write, React's commit and deck.gl's `setProps` - is
    // reported either way, because it is the half this repository's own 26.1 ms figure measured,
    // but even it partly blocks on GL calls under software rendering, so it is a ceiling.
    verdict({
      name: "scrub restyle, key to paint",
      budget: `${BUDGET.scrubRestyleMs} ms per step`,
      measured:
        `${over} of ${STEPS} steps over 16 ms` + (over ? `, worst ${worst.toFixed(0)} ms` : ""),
      met: software ? null : over === 0,
      note: software
        ? `the renderer is "${renderer}", a software rasteriser, so the paint leg is the ` +
          "rasteriser rather than the restyle - run the same scrub in a browser with a GPU"
        : `frame intervals during the scrub: p50 ${percentile(sorted, 0.5).toFixed(1)} ms, ` +
          `p95 ${percentile(sorted, 0.95).toFixed(1)} ms, ` +
          `${longFrames} of ${frames.length} over 32 ms`,
    });
    verdict({
      name: "scrub restyle, the handler alone",
      budget: `${BUDGET.scrubRestyleMs} ms per step`,
      measured: over
        ? `worst ${worstHandler.toFixed(1)} ms over ${over} steps that missed the frame`
        : "every step made its frame, so no handler time was recorded",
      met: over === 0 ? true : worstHandler < BUDGET.scrubRestyleMs,
      note: "Event Timing reports the handler only for the steps whose paint missed 16 ms",
    });
    verdict({
      name: "no network during a scrub (7.2)",
      budget: "0 requests",
      measured: `${during.length} requests`,
      met: during.length === 0,
      note:
        during.slice(0, 3).join(" | ") ||
        (settling.length
          ? `the load was still fetching ${settling.length} things from ${hostList(settling)} ` +
            "when the console became usable; they finished before the scrub started"
          : "nothing was still in flight when the console became usable"),
    });

    expect(frames.length, "the frame counter never ran").toBeGreaterThan(10);
    expect(
      during,
      `the scrub fetched something, so the run was not fully preloaded:\n${during.join("\n")}`,
    ).toEqual([]);
    if (!software) {
      expect(share, "more than half the frames of a scrub are dropped frames").toBeLessThan(
        GUARD.scrubLongFrameShare,
      );
    }
  });

  test("console: map frame rate at 1440 x 900", async ({ page }) => {
    test.setTimeout(300_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${PERF_URL}/console`, { waitUntil: "domcontentloaded", timeout: NAV });
    await assertProductionBuild(page);
    await waitForRun(page);
    await expect(page.locator("canvas").first()).toBeVisible({ timeout: SETTLE });
    await expect(scrubHandle(page)).toBeEnabled({ timeout: SETTLE });
    await page.waitForTimeout(3_000);

    const renderer = await readRenderer(page);

    // Ten seconds of rAF deltas with the map idle: streets, the depth raster, the surcharge
    // markers and the ground-truth pins, which is the layer set section 14 names.
    const fps = await page.evaluate(async () => {
      const deltas: number[] = [];
      let last = performance.now();
      const until = last + 10_000;
      await new Promise<void>((resolve) => {
        const tick = () => {
          const now = performance.now();
          deltas.push(now - last);
          last = now;
          if (now < until) requestAnimationFrame(tick);
          else resolve();
        };
        requestAnimationFrame(tick);
      });
      const mean = deltas.reduce((a, b) => a + b, 0) / deltas.length;
      const sorted = [...deltas].sort((a, b) => a - b);
      return {
        fps: 1000 / mean,
        p95FrameMs: sorted[Math.round(0.95 * (sorted.length - 1))],
        n: deltas.length,
      };
    });

    const software = isSoftware(renderer);
    verdict({
      name: "map frame rate, 1440 x 900",
      budget: `${BUDGET.mapFps} fps`,
      measured: `${fps.fps.toFixed(1)} fps (p95 frame ${fps.p95FrameMs.toFixed(1)} ms over ${fps.n} frames)`,
      met: software ? null : fps.fps >= BUDGET.mapFps,
      note: software
        ? `the renderer is "${renderer}", a software rasteriser, so this number does not decide ` +
          "the budget - run the same counter in a browser with a GPU"
        : `renderer "${renderer}"`,
    });

    expect(fps.n, "the frame counter produced no frames").toBeGreaterThan(30);
  });

  test("console: browser memory across a full replay", async ({ page }) => {
    // 140 key presses and a minute of playback, each press waiting on a frame.
    test.setTimeout(900_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${PERF_URL}/console`, { waitUntil: "domcontentloaded", timeout: NAV });
    await assertProductionBuild(page);
    await waitForRun(page);
    await expect(scrubHandle(page)).toBeEnabled({ timeout: SETTLE });
    await page.locator("body").click({ position: { x: 5, y: 5 } });

    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Performance.enable");
    const heap = async (): Promise<number> => {
      const { metrics } = await cdp.send("Performance.getMetrics");
      return metrics.find((m) => m.name === "JSHeapUsedSize")?.value ?? Number.NaN;
    };

    const beforeHeap = await heap();
    const beforeRss = browserPrivateBytes();

    // The whole window scrubbed forward and back twice, then a minute of Play at the console's
    // own step interval. A leak in the frame decoder or in a layer accessor shows up as a heap
    // that does not come back down; a texture the map never releases shows up in the operating
    // system's figure and not in the heap, which is why both are read.
    for (let pass = 0; pass < 2; pass++) {
      for (let i = 0; i < 35; i++) await page.keyboard.press("ArrowRight");
      for (let i = 0; i < 35; i++) await page.keyboard.press("ArrowLeft");
    }
    // Sampled through the playback rather than read once at the end: the budget is memory
    // *during* a replay, and a tab that peaks at 1.2 GB and is collected back to 300 MB has
    // missed it. Six samples over the minute.
    await page.keyboard.press("Space");
    let peak = browserPrivateBytes();
    let peakHeap = await heap();
    for (let i = 0; i < 6; i++) {
      await page.waitForTimeout(10_000);
      const sample = browserPrivateBytes();
      if (sample && (!peak || sample.total > peak.total)) peak = sample;
      const sampleHeap = await heap();
      if (sampleHeap > peakHeap) peakHeap = sampleHeap;
    }
    await page.keyboard.press("Space");

    const afterHeap = Math.max(peakHeap, await heap());
    const afterRss = browserPrivateBytes();
    if (afterRss && (!peak || afterRss.total > peak.total)) peak = afterRss;

    verdict({
      name: "browser tab memory during a full replay",
      budget: mb(BUDGET.tabMemoryBytes),
      measured: peak
        ? `peak ${mb(peak.total)} across ${peak.processes} browser processes ` +
          `(peak JS heap ${mb(afterHeap)})`
        : `peak JS heap ${mb(afterHeap)}; the operating system's figure could not be read`,
      met: peak ? peak.total < BUDGET.tabMemoryBytes : null,
      note:
        `started at ${beforeRss ? mb(beforeRss.total) : "unknown"} with a ${mb(beforeHeap)} heap. ` +
        "The operating-system figure covers the whole Playwright browser - renderer, browser and " +
        "GPU process - so it is a ceiling on one tab rather than the tab itself.",
    });

    expect(Number.isFinite(afterHeap), "no heap metric came back from CDP").toBe(true);
  });

  test("console: switching cycles swaps the map", async ({ page }) => {
    await page.goto(`${PERF_URL}/console`, { waitUntil: "domcontentloaded", timeout: NAV });
    await assertProductionBuild(page);
    await waitForRun(page);
    const stamp = page.getByText(/MUM-\d{8}T\d{4}Z/).first();
    const first = await stamp.innerText();

    const cycles = page.getByRole("button", { name: /^Forecast from \d\d:\d\d IST/ });
    const options = await cycles.count();
    test.skip(options < 2, "the console is showing fewer than two cycles to switch between");

    const started = Date.now();
    await cycles.nth(options - 1).click();
    await expect(stamp).not.toHaveText(first, { timeout: SETTLE });
    const swapped = Date.now() - started;

    verdict({
      name: "run published -> map swap",
      budget: `${BUDGET.runSwapMs} ms`,
      measured: `${swapped} ms from the click to the new run stamp`,
      met: swapped < BUDGET.runSwapMs,
      note:
        "the swap leg only: clicking a cycle fetches that run, where a baked publish over the " +
        "WebSocket hands the client a run the server has already built. The published-to-swap " +
        "leg needs a bake running beside the browser and is not measured here",
    });

    expect(swapped).toBeGreaterThan(0);
  });
});

/**
 * What each route makes a visitor download and parse, and which libraries it is.
 *
 * P10.4 asks for a bundle analysis by name. Next's own build output prints per-route totals but
 * not what is in them, and `@next/bundle-analyzer` needs a plugin in `next.config.ts` and a
 * second build; this reads the browser's own resource timings from the build that is being
 * measured anyway, and names each chunk by `fingerprintChunks`. It is not a budget - section 14
 * sets none for bytes - so it records rather than judges, and the routes are the three that
 * matter: the landing page a judge opens first, the console, and the phone-sized public map.
 */
test.describe("the bundle, measured rather than guessed", () => {
  for (const route of ["/", "/console", "/map"]) {
    test(`JS weight of ${route}`, async ({ page }) => {
      test.setTimeout(180_000);
      const chunks = fingerprintChunks(
        `apps/command/${process.env.VARUNA_DIST_DIR ?? ".next-perf"}`,
      );
      await page.goto(`${PERF_URL}${route}`, { waitUntil: "load", timeout: NAV });
      await assertProductionBuild(page);
      // Long enough for a route that loads its map lazily to have loaded it: on the landing page
      // the console map is `next/dynamic` (P9.2 took LCP from 3.5 s to 1.6 s that way), so a
      // reading taken at `load` would miss the largest chunks it eventually pulls.
      await page.waitForTimeout(6_000);
      const js = await page.evaluate(() =>
        performance
          .getEntriesByType("resource")
          .filter((r) => r.name.endsWith(".js"))
          .map((r) => {
            const t = r as PerformanceResourceTiming;
            return {
              file: t.name.split("/").pop() ?? "",
              wire: t.transferSize,
              parsed: t.decodedBodySize,
            };
          }),
      );
      const wire = js.reduce((a, r) => a + r.wire, 0);
      const parsed = js.reduce((a, r) => a + r.parsed, 0);
      const top = [...js]
        .sort((a, b) => b.parsed - a.parsed)
        .slice(0, 3)
        .map((r) => `${chunks.get(r.file) ?? "?"} ${(r.parsed / 1024).toFixed(0)} KB`);

      verdict({
        name: `JS on ${route}`,
        budget: "no byte budget in section 14; recorded so a regression is visible",
        measured:
          `${js.length} files, ${(wire / 1024).toFixed(0)} KB over the wire, ` +
          `${(parsed / 1024).toFixed(0)} KB parsed; largest: ${top.join(", ")}`,
        met: null,
      });

      expect(js.length, `${route} loaded no JavaScript at all`).toBeGreaterThan(0);
    });
  }
});

test.describe("section 14, the API percentiles", () => {
  const RUN = process.env.PERF_RUN_ID ?? "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";
  const DEPART = "2019-07-02T08:40:00+05:30";

  /** Time `n` calls and return the percentiles, plus the first call, which is the cold one. */
  async function timed(call: () => Promise<number>, n: number) {
    const ms: number[] = [];
    for (let i = 0; i < n; i++) ms.push(await call());
    const sorted = [...ms].sort((a, b) => a - b);
    return {
      p50: percentile(sorted, 0.5),
      p95: percentile(sorted, 0.95),
      min: sorted[0],
      max: sorted[sorted.length - 1],
      cold: ms[0],
    };
  }

  test("segments, route, what-if and the physics check", async ({ request }) => {
    // 55 calls, five of which run the coupled Twin twice on a crop of the city.
    test.setTimeout(600_000);
    const health = await request.get(`${PERF_API}/healthz`, { timeout: 30_000 }).catch(() => null);
    test.skip(!health?.ok(), `no API answering at ${PERF_API}`);

    const get = (url: string, headers: Record<string, string>) => async () => {
      const t0 = Date.now();
      const r = await request.get(url, { headers, timeout: 120_000 });
      await r.body();
      expect(r.status(), `${url} did not answer 200`).toBe(200);
      return Date.now() - t0;
    };
    const post =
      (url: string, data: unknown, timeout = 300_000) =>
      async () => {
        const t0 = Date.now();
        const r = await request.post(url, { data, timeout });
        await r.body();
        expect(r.status(), `${url} did not answer 200`).toBe(200);
        return Date.now() - t0;
      };

    // A browser sends `Accept-Encoding: gzip` and the server honours it, so the figure a visitor
    // waits for is the compressed one. Both are measured because on a 3.7 MB body they are very
    // different numbers, and the difference is the budget.
    const segmentsUrl =
      `${PERF_API}/v1/nowcast/segments?run_id=${RUN}` +
      `&bbox=72.815,18.995,72.905,19.135&t=${encodeURIComponent(DEPART)}&profile=car`;
    const plain = await timed(get(segmentsUrl, { "Accept-Encoding": "identity" }), 20);
    const gzip = await timed(get(segmentsUrl, { "Accept-Encoding": "gzip" }), 20);
    verdict({
      name: "GET /v1/nowcast/segments, as a browser asks for it",
      budget: `${BUDGET.apiSegmentsMs} ms p95`,
      measured: `${gzip.p95.toFixed(0)} ms (p50 ${gzip.p50.toFixed(0)} ms)`,
      met: gzip.p95 < BUDGET.apiSegmentsMs,
      note:
        `uncompressed the same call is p95 ${plain.p95.toFixed(0)} ms, ` +
        `p50 ${plain.p50.toFixed(0)} ms, so the gzip of the body is most of it`,
    });

    const route = await timed(
      post(`${PERF_API}/v1/route`, {
        origin: [72.841, 19.003],
        destination: [72.862, 19.041],
        depart_at: DEPART,
        profile: "ambulance",
        risk_tolerance: 0.2,
        run_id: RUN,
      }),
      20,
    );
    verdict({
      name: "POST /v1/route, KEM to Sion, ambulance",
      budget: `${BUDGET.apiRouteMs} ms p95`,
      measured: `${route.p95.toFixed(0)} ms (p50 ${route.p50.toFixed(0)} ms)`,
      met: route.p95 < BUDGET.apiRouteMs,
      note: `the first call in the process took ${route.cold.toFixed(0)} ms - that one loads the road graph`,
    });

    const whatif = await timed(
      post(`${PERF_API}/v1/whatif`, {
        run_id: RUN,
        rain_scale: 1.3,
        tide_offset_m: 0,
        cleaned_segments: [],
      }),
      10,
    );
    verdict({
      name: "POST /v1/whatif, rain +30 %",
      budget: `${BUDGET.apiWhatIfMs} ms p95`,
      measured: `${whatif.p95.toFixed(0)} ms (p50 ${whatif.p50.toFixed(0)} ms)`,
      met: whatif.p95 < BUDGET.apiWhatIfMs,
      note: `the first call in the process took ${whatif.cold.toFixed(0)} ms`,
    });

    const physics = await timed(
      post(`${PERF_API}/v1/whatif/physics-check`, {
        run_id: RUN,
        rain_scale: 1.3,
        tide_offset_m: 0,
        cleaned_segments: [],
      }),
      5,
    );
    verdict({
      name: "POST /v1/whatif/physics-check, rain +30 %",
      budget: `${(BUDGET.apiPhysicsCheckMs / 1000).toFixed(0)} s p95`,
      measured: `${(physics.p95 / 1000).toFixed(2)} s (p50 ${(physics.p50 / 1000).toFixed(2)} s)`,
      met: physics.p95 < BUDGET.apiPhysicsCheckMs,
      note:
        `the first call in the process took ${(physics.cold / 1000).toFixed(2)} s; ` +
        `${processCount()} python processes`,
    });

    expect(route.p95, "the router drifted past twice its budget").toBeLessThan(GUARD.apiRouteMs);
    expect(whatif.p95, "what-if drifted past twice its budget").toBeLessThan(GUARD.apiWhatIfMs);
    expect(physics.p95, "the physics check drifted past twice its budget").toBeLessThan(
      GUARD.apiPhysicsCheckMs,
    );
  });
});

/**
 * The budgets that need a GPU, measured in a browser that has one.
 *
 * Off by default, because it opens a real Chrome window on the machine running it. Turn it on
 * with `VARUNA_PERF_GPU=1`. Everything above runs in the headless Chromium the config launches,
 * which falls back to SwiftShader: on it the same console measured 53.8 fps idle and a p50 frame
 * interval of 1,038 ms during a scrub, and neither figure is about VARUNA - the first is
 * SwiftShader keeping up with an idle map, the second is SwiftShader redrawing 1440 x 900 of
 * street geometry in software. Section 14's budget says "on an integrated GPU laptop", so it is
 * decided here or it is not decided.
 *
 * The browser is launched inside the test rather than added as a Playwright project, because
 * `playwright.config.ts` belongs to the whole suite and a headed project in it would open a
 * window during every `make e2e`.
 */
test.describe("section 14, the budgets that need a GPU", () => {
  const ENABLED = process.env.VARUNA_PERF_GPU === "1";

  test("frame rate, scrub paint and tab memory on a real GPU", async () => {
    test.skip(
      !ENABLED,
      "needs a browser with a GPU, which opens a window on this machine: set VARUNA_PERF_GPU=1",
    );
    test.setTimeout(900_000);

    const browser = await chromium.launch({ headless: false });
    try {
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const page = await context.newPage();
      await installBootProbes(page);
      const loadStarted = Date.now();
      await page.goto(`${PERF_URL}/console`, { waitUntil: "domcontentloaded", timeout: NAV });
      await assertProductionBuild(page);
      await waitForRun(page);
      const runVisible = Date.now() - loadStarted;
      await expect(scrubHandle(page)).toBeEnabled({ timeout: SETTLE });
      const usable = Date.now() - loadStarted;
      const boot = await readBootProbes(page);

      const renderer = await readRenderer(page);
      expect(
        isSoftware(renderer),
        `the headed browser fell back to a software renderer ("${renderer}") too, so this block ` +
          "cannot decide the budget either",
      ).toBe(false);

      verdict({
        name: "console first render, GPU browser, cold cache",
        budget: `${BUDGET.consoleFirstRenderMs} ms`,
        measured: `${runVisible} ms`,
        met: runVisible < BUDGET.consoleFirstRenderMs,
        note:
          `scrub usable at ${usable} ms; ${bootNote(boot)}; renderer "${renderer}"; ` +
          `${processCount()} python processes`,
      });

      await page.waitForTimeout(3_000);

      // Idle frame rate, twice, because the first ten seconds after a load still carry the
      // hotspot rail's own fetches and the map's first draw of 39,259 building footprints.
      const idle = async () =>
        page.evaluate(async () => {
          const deltas: number[] = [];
          let last = performance.now();
          const until = last + 10_000;
          await new Promise<void>((resolve) => {
            const tick = () => {
              const now = performance.now();
              deltas.push(now - last);
              last = now;
              if (now < until) requestAnimationFrame(tick);
              else resolve();
            };
            requestAnimationFrame(tick);
          });
          const sorted = [...deltas].sort((a, b) => a - b);
          return {
            fps: 1000 / (deltas.reduce((a, b) => a + b, 0) / deltas.length),
            p95FrameMs: sorted[Math.round(0.95 * (sorted.length - 1))],
            n: deltas.length,
          };
        });
      const first = await idle();
      const second = await idle();

      verdict({
        name: "map frame rate, 1440 x 900, GPU browser",
        budget: `${BUDGET.mapFps} fps`,
        measured: `${first.fps.toFixed(1)} and ${second.fps.toFixed(1)} fps over two 10 s samples`,
        met: Math.max(first.fps, second.fps) >= BUDGET.mapFps,
        note:
          `p95 frame ${second.p95FrameMs.toFixed(1)} ms; renderer "${renderer}"; ` +
          `${processCount()} python processes`,
      });

      // The scrub's paint leg, which is what section 14's 16 ms is actually about.
      await page.locator("body").click({ position: { x: 5, y: 5 } });
      await page.evaluate(() => {
        const w = window as unknown as { __events: { duration: number; handler: number }[] };
        w.__events = [];
        new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            const e = entry as PerformanceEventTiming;
            if (e.name !== "keydown") continue;
            w.__events.push({ duration: e.duration, handler: e.processingEnd - e.processingStart });
          }
        }).observe({ type: "event", durationThreshold: 16 });
      });
      // Twelve steps out and back first: the first arrow after a load still creates a layer and
      // links its shaders, and a synchronous shader link on this Intel driver is about 90 ms.
      // Profiling the scrub cold measured 40 % of its CPU inside `getProgramParameter` under
      // `_getLinkStatus`, which is that link and not the restyle.
      for (let i = 0; i < 12; i++) await page.keyboard.press("ArrowRight");
      for (let i = 0; i < 12; i++) await page.keyboard.press("ArrowLeft");
      await page.waitForTimeout(1_000);
      await page.evaluate(
        () => ((window as unknown as { __events: unknown[] }).__events.length = 0),
      );

      for (let i = 0; i < 35; i++) await page.keyboard.press("ArrowRight");
      await page.waitForTimeout(500);
      const events = await page.evaluate(
        () => (window as unknown as { __events: { duration: number; handler: number }[] }).__events,
      );
      const durations = events.map((e) => e.duration).sort((a, b) => a - b);
      const handlers = events.map((e) => e.handler).sort((a, b) => a - b);
      verdict({
        name: "scrub restyle, key to paint, GPU browser",
        budget: `${BUDGET.scrubRestyleMs} ms per step`,
        measured:
          `${events.length} of 35 steps over 16 ms` +
          (events.length
            ? `; key to paint p50 ${percentile(durations, 0.5).toFixed(0)} ms, ` +
              `p95 ${percentile(durations, 0.95).toFixed(0)} ms, worst ` +
              `${durations[durations.length - 1].toFixed(0)} ms; the handler alone p50 ` +
              `${percentile(handlers, 0.5).toFixed(1)} ms, worst ` +
              `${handlers[handlers.length - 1].toFixed(1)} ms`
            : ""),
        met: events.length === 0,
        note: `renderer "${renderer}"`,
      });

      // Having measured that the scrub misses, say what it spends the time on. Twenty more
      // steps, sampled: the budget's own wording claims the step is the map's work, and this is
      // the only thing in the file that can check that claim. Leftwards, because the 35 steps
      // above ended at the last frame of the run and another ArrowRight would be clamped - the
      // helper checks the step moved and refuses to report a table if it did not.
      const split = await profileScrub(
        page,
        context,
        20,
        fingerprintChunks(`apps/command/${process.env.VARUNA_DIST_DIR ?? ".next-perf"}`),
      );
      verdict({
        name: "scrub restyle, where the time goes",
        budget: "7.2 calls a step a texture swap and one colour accessor",
        measured: split,
        met: null,
        note:
          "a sampling profile attributed by bundle, not by package: a chunk holding two " +
          "libraries is counted under both. It answers whose code ran, not which function, and " +
          "the split moves run to run - two runs on the same build put react-dom at 563 and " +
          "251 ms. Read the shape, not the milliseconds",
      });

      // Memory across a full replay, scoped to the headed browser's own processes: the runner's
      // own headless shell is alive beside it and is not what this budget is about.
      const cdp = await context.newCDPSession(page);
      await cdp.send("Performance.enable");
      const heap = async (): Promise<number> => {
        const { metrics } = await cdp.send("Performance.getMetrics");
        return metrics.find((m) => m.name === "JSHeapUsedSize")?.value ?? Number.NaN;
      };

      const beforeRss = browserPrivateBytes("chrome.exe");
      let peak = beforeRss;
      let peakHeap = await heap();
      // A sample that survives the browser going away. The headed Chrome was seen closing
      // mid-replay on one run; losing the rest of the samples is a smaller loss than losing the
      // four verdicts already recorded, so this records how far it got and stops.
      let alive = true;
      const sample = async () => {
        if (!alive) return;
        const rss = browserPrivateBytes("chrome.exe");
        if (rss && (!peak || rss.total > peak.total)) peak = rss;
        try {
          peakHeap = Math.max(peakHeap, await heap());
        } catch {
          alive = false;
        }
      };

      try {
        for (let pass = 0; pass < 2 && alive; pass++) {
          for (let i = 0; i < 35; i++) await page.keyboard.press("ArrowLeft");
          for (let i = 0; i < 35; i++) await page.keyboard.press("ArrowRight");
          await sample();
        }
        if (alive) await page.keyboard.press("Space");
        for (let i = 0; i < 6 && alive; i++) {
          await page.waitForTimeout(10_000);
          await sample();
        }
        if (alive) await page.keyboard.press("Space");
      } catch {
        alive = false;
      }

      verdict({
        name: "browser tab memory during a full replay, GPU browser",
        budget: mb(BUDGET.tabMemoryBytes),
        measured: peak
          ? `peak ${mb(peak.total)} across ${peak.processes} browser processes ` +
            `(peak JS heap ${mb(peakHeap)})`
          : `peak JS heap ${mb(peakHeap)}; the operating system's figure could not be read`,
        met: peak ? peak.total < BUDGET.tabMemoryBytes : null,
        note:
          `started at ${beforeRss ? mb(beforeRss.total) : "unknown"}. The figure is every ` +
          "chrome.exe under ms-playwright - renderer, browser and GPU process - so it is a " +
          "ceiling on one tab rather than the tab itself" +
          (alive ? "" : ". The browser closed part way through, so this is a partial replay"),
      });
    } finally {
      await browser.close();
    }
  });
});

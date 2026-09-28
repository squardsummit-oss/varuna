// Screen capture for the design QA checklist (SPEC.md 6.11): every screen is photographed at the
// reference sizes into docs/screens/ and compared with the previous one before a screen is marked done.
// Console errors are collected per page and reported; the script exits non-zero if any page logged one.
//
//   pnpm screens                     # needs `make dev` (API :8000, UI :3000) already running
//   pnpm screens -- --url http://localhost:3001
//
// @playwright/test lives in the apps/command workspace (it owns the e2e stack), so it is resolved
// from there rather than installed twice.

import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(new URL("../apps/command/package.json", import.meta.url));
const { chromium } = require("@playwright/test");

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const outDir = path.join(root, "docs", "screens");

const argv = process.argv.slice(2);
const urlFlag = argv.indexOf("--url");
const baseUrl = (urlFlag >= 0 ? argv[urlFlag + 1] : process.env.VARUNA_UI_URL) ?? "http://localhost:3000";

const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };

/** Every route in SPEC.md 3.4, with the file name the design QA checklist expects. */
const SCREENS = [
  { route: "/console", file: "console.png", viewport: DESKTOP },
  { route: "/design", file: "design.png", viewport: DESKTOP },
  { route: "/", file: "landing.png", viewport: DESKTOP },
  { route: "/drains", file: "drains.png", viewport: DESKTOP },
  { route: "/route", file: "route.png", viewport: DESKTOP },
  { route: "/alerts", file: "alerts.png", viewport: DESKTOP },
  { route: "/pumps", file: "pumps.png", viewport: DESKTOP },
  { route: "/whatif", file: "whatif.png", viewport: DESKTOP },
  { route: "/replay", file: "replay.png", viewport: DESKTOP },
  { route: "/onboard", file: "onboard.png", viewport: DESKTOP },
  { route: "/verify", file: "verify.png", viewport: DESKTOP },
  { route: "/api", file: "api.png", viewport: DESKTOP },
  { route: "/map", file: "map-390.png", viewport: MOBILE },
  { route: "/report", file: "report-390.png", viewport: MOBILE },
];

async function capture(browser, screen) {
  const context = await browser.newContext({
    viewport: screen.viewport,
    deviceScaleFactor: 1,
    colorScheme: "dark",
    locale: "en-IN",
    timezoneId: "Asia/Kolkata",
    isMobile: screen.viewport === MOBILE,
    hasTouch: screen.viewport === MOBILE,
  });
  const page = await context.newPage();
  const problems = [];
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(`console.error  ${message.text()}`);
  });
  page.on("pageerror", (error) => problems.push(`pageerror      ${error.message}`));
  page.on("requestfailed", (request) => {
    const failure = request.failure()?.errorText ?? "failed";
    // Aborted map tiles and cancelled prefetches during teardown are not our errors.
    if (failure.includes("ERR_ABORTED")) return;
    problems.push(`requestfailed  ${request.url()} (${failure})`);
  });

  const response = await page.goto(`${baseUrl}${screen.route}`, {
    waitUntil: "domcontentloaded",
    timeout: 90_000,
  });
  const status = response ? response.status() : 0;
  // Let the first Turbopack compile, fonts and any lazy panel settle before the shutter.
  await page.waitForLoadState("networkidle", { timeout: 30_000 }).catch(() => {});
  await page.waitForTimeout(1_200);
  // The dev-server route indicator is not part of the design; keep it out of the baseline.
  await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" }).catch(() => {});

  const file = path.join(outDir, screen.file);
  await page.screenshot({ path: file, fullPage: true, animations: "disabled" });
  await context.close();
  return { ...screen, status, problems, file };
}

const browser = await chromium.launch();
await mkdir(outDir, { recursive: true });

const results = [];
for (const screen of SCREENS) {
  const result = await capture(browser, screen);
  results.push(result);
  const size = `${result.viewport.width}x${result.viewport.height}`;
  const verdict = result.problems.length === 0 ? "clean" : `${result.problems.length} problem(s)`;
  console.log(`${result.route.padEnd(10)} ${size.padEnd(9)} ${result.status}  ${result.file.replace(root + path.sep, "")}  ${verdict}`);
  for (const problem of result.problems) console.log(`             ${problem}`);
}
await browser.close();

await writeFile(
  path.join(outDir, "console-report.json"),
  JSON.stringify(
    {
      capturedAt: new Date().toISOString(),
      baseUrl,
      screens: results.map(({ route, file, status, problems }) => ({ route, file, status, problems })),
    },
    null,
    2,
  ) + "\n",
  "utf8",
);

const failed = results.filter((r) => r.problems.length > 0 || r.status >= 400);
console.log(
  failed.length === 0
    ? `\nscreens  ${results.length} captured into docs/screens, every page clean.`
    : `\nscreens  ${results.length} captured; ${failed.length} page(s) need attention: ${failed.map((r) => r.route).join(", ")}`,
);
process.exit(failed.length === 0 ? 0 : 1);

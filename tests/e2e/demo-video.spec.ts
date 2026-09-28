import { copyFileSync, mkdirSync } from "node:fs";
import path from "node:path";

import { chromium, type Locator } from "@playwright/test";

import { expect, test } from "./requirements";

/**
 * The fallback recording of the demo (SPEC.md 15, task P10.7): `make demo-video`.
 *
 * One continuous 1920 x 1080 take of the eight-minute script, paced to section 15's own clock so
 * the video can be played on stage in place of the live demo and the presenter can speak over it
 * at the times the rehearsal uses. It is a recording, not a test: every beat is attempted, a beat
 * that fails is noted and the take carries on, and the run fails at the end only after the video
 * has been written, naming the beats that did not happen - a fallback with a hole in it must say
 * where the hole is.
 *
 * **Recorded in a headed browser, on the GPU.** Headless Chromium draws WebGL on SwiftShader, and
 * at 1920 x 1080 with a video encoder running the map starves the main thread: measured on
 * 2026-09-26, a click on the drawer's Close button reached "performing click action" and was still
 * waiting ten seconds later. A presenter records on the laptop's GPU, so the take does too - it
 * opens a window on this machine for the length of the take. `VARUNA_DEMO_VIDEO_HEADLESS=1` forces
 * headless for a machine with no display.
 *
 * Every route is fetched once before the camera starts, because `next dev` compiles a route on
 * its first visit and a recording of a spinner is not a fallback. The replay clock is reset to the
 * opening, paused, first: it is the API's and shared by every open console, and one left playing
 * advances the cycle under the camera.
 *
 * Skipped unless `VARUNA_DEMO_VIDEO=1`, so `make e2e` never spends eight minutes filming.
 * `VARUNA_DEMO_VIDEO_OUT` names the file; `varuna demo-video` sets it outside the repository,
 * because a 1080p take does not belong in git (P10.7: "stored outside the repo").
 */

const RECORD = process.env.VARUNA_DEMO_VIDEO === "1";
const OUT = process.env.VARUNA_DEMO_VIDEO_OUT;
const SIZE = { width: 1920, height: 1080 };
const SETTLE = 45_000;
const API = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";
/** Where section 15 opens: 2 July 2019, 06:40 IST, paused. */
const OPENING = "2019-07-02T06:40:00+05:30";

/** Section 15's beats, in seconds from the start of the take. */
const BEAT = {
  console: 0,
  hotspot: 40,
  scrub: 100,
  pins: 160,
  drains: 210,
  whatif: 270,
  route: 320,
  alerts: 345,
  pumps: 365,
  onboard: 390,
  verify: 440,
  end: 480,
} as const;

const ROUTES = [
  "/console",
  "/drains",
  "/whatif",
  "/route",
  "/alerts",
  "/pumps",
  "/onboard",
  "/verify",
];

/**
 * Click as a presenter would, and if the pointer cannot get through in time - a busy frame, a
 * transient overlay - deliver the click to the element itself rather than drop the beat.
 */
async function press(target: Locator): Promise<void> {
  await target
    .first()
    .click({ timeout: 20_000 })
    .catch(() => target.first().dispatchEvent("click"));
}

test.skip(!RECORD, "Records the eight-minute demo; run it with `make demo-video`.");

test("the demo, recorded end to end at 1080p", async ({ request, baseURL }) => {
  test.setTimeout(15 * 60_000);

  await request.post(`${API}/v1/replay/pause`).catch(() => undefined);
  await request
    .post(`${API}/v1/replay/seek`, { data: { sim_time: OPENING } })
    .catch(() => undefined);

  // Warm every route off camera: Turbopack compiles on first visit.
  for (const route of ROUTES) {
    await request.get(`${baseURL}${route}`, { timeout: 180_000 }).catch(() => undefined);
  }

  const browser = await chromium.launch({
    headless: process.env.VARUNA_DEMO_VIDEO_HEADLESS === "1",
  });
  const context = await browser.newContext({
    baseURL,
    viewport: SIZE,
    recordVideo: { dir: path.resolve("test-results", "demo-video"), size: SIZE },
    colorScheme: "dark",
    locale: "en-IN",
    timezoneId: "Asia/Kolkata",
  });
  const page = await context.newPage();
  const started = Date.now();
  const missed: string[] = [];

  /** Hold until the script clock reaches `seconds`, so every beat lands on its own time. */
  const until = async (seconds: number) => {
    const wait = started + seconds * 1000 - Date.now();
    if (wait > 0) await page.waitForTimeout(wait);
  };
  const beat = async (name: string, body: () => Promise<void>) => {
    try {
      await body();
    } catch (error) {
      missed.push(`${name}: ${error instanceof Error ? error.message.split("\n")[0] : error}`);
    }
  };
  const open = async (route: string) => {
    await page.goto(route, { waitUntil: "domcontentloaded", timeout: 120_000 });
  };
  /** The storm's peak cycle, as a presenter would pick it: the screens open on the newest run,
   * 09:10, after the storm has passed. */
  const peakCycle = async () => {
    await press(page.getByRole("button", { name: /Forecast from 08:40 IST/ }));
  };

  // 0:00 - the console on a real run, the banner and the run stamp.
  await beat("0:00 console", async () => {
    await open("/console");
    await expect(page.getByText(/MUM-\d{8}T\d{4}Z/).first()).toBeVisible({ timeout: SETTLE });
  });

  // 0:40 - the worst junction's drawer: its fan chart with the ensemble's band, safe-until, and
  // why it floods.
  await until(BEAT.hotspot);
  await beat("0:40 hotspot drawer", async () => {
    await press(page.getByRole("list", { name: "Ranked hotspots" }).getByRole("button"));
    await expect(page.getByText("Why this junction floods").first()).toBeVisible({
      timeout: SETTLE,
    });
    await page.waitForTimeout(12_000);
    await page.getByText("Why this junction floods").first().scrollIntoViewIfNeeded();
  });

  // 1:40 - scrub two hours ahead, fifteen minutes a key, so the streets turn as the judge watches.
  await until(BEAT.scrub);
  await beat("1:40 scrub to +2 h", async () => {
    // Closed with its own button: Escape does not close the drawer, and clicking the map to move
    // focus opens a street's popover over the time bar. The drawer is an <aside> nested in the
    // rail's <aside>, which is not exposed as a landmark, so it is found by its label. The arrow
    // keys are handled on the window, so nothing needs focus for them.
    await press(
      page
        .locator('aside[aria-label$=" forecast"]')
        .getByRole("button", { name: "Close", exact: true }),
    );
    for (let step = 0; step < 8; step += 1) {
      await page.keyboard.press("ArrowRight");
      await page.waitForTimeout(2_500);
    }
  });

  // 2:40 - play the replay so the ground-truth pins drop on the clock.
  await until(BEAT.pins);
  await beat("2:40 play the replay", async () => {
    // One button that is "Play" while paused and "Pause" while playing: press it only if paused.
    const toggle = page
      .getByRole("toolbar", { name: "Replay time bar" })
      .getByRole("button", { name: /^(Play|Pause) the replay$/ });
    const label = await toggle.getAttribute("aria-label", { timeout: 20_000 });
    if (label !== "Play the replay") return;
    await press(toggle);
    await expect(toggle).toHaveAttribute("aria-label", "Pause the replay", { timeout: 20_000 });
  });

  // 3:30 - the drain X-ray: the prior, then what Pulse learned.
  await until(BEAT.drains);
  await beat("3:30 drain X-ray", async () => {
    await open("/drains");
    await expect(page.getByText(/Inferred drain graph/i).first()).toBeVisible({ timeout: SETTLE });
    await press(page.getByRole("button", { name: "Before", exact: true }));
    await page.waitForTimeout(6_000);
    await press(page.getByRole("button", { name: "After", exact: true }));
  });

  // 4:30 - rain plus 30 % in well under a second, then the physics check and its disagreement.
  await until(BEAT.whatif);
  await beat("4:30 what-if and physics check", async () => {
    await open("/whatif");
    await peakCycle();
    const rain = page.getByRole("region", { name: "Rain scale" });
    const slider = rain.getByRole("slider", { name: "Rain scale" });
    await expect(async () => {
      await slider.fill("1.3");
      await expect(rain.getByRole("status")).toHaveText("1.3x", { timeout: 1_000 });
    }).toPass({ timeout: SETTLE });
    await press(page.getByRole("button", { name: "Run what-if" }));
    await expect(page.getByText(/segments deeper/).first()).toBeVisible({ timeout: SETTLE });
    await page.waitForTimeout(6_000);
    await press(page.getByRole("button", { name: "Physics check" }));
    await expect(page.getByText(/Emulator vs physics: max difference/).first()).toBeVisible({
      timeout: 90_000,
    });
  });

  // 5:20 - the ambulance, the alert on the phone, the pump order.
  await until(BEAT.route);
  await beat("5:20 route", async () => {
    await open("/route");
    const find = page.getByRole("button", { name: "Find route" });
    await expect(find).toBeEnabled({ timeout: SETTLE });
    await press(find);
    await expect(page.getByText(/Routed on run/i).first()).toBeVisible({ timeout: SETTLE });
  });
  await until(BEAT.alerts);
  await beat("5:45 alerts", async () => {
    await open("/alerts");
    await peakCycle();
    await expect(page.getByText(/Exercise/i).first()).toBeVisible({ timeout: SETTLE });
  });
  await until(BEAT.pumps);
  await beat("6:05 pumps", async () => {
    await open("/pumps");
    await peakCycle();
    await press(page.getByRole("button", { name: "Optimise" }));
  });

  // 6:30 - Chennai, built from its cache while the judges watch.
  await until(BEAT.onboard);
  await beat("6:30 onboarding", async () => {
    await open("/onboard");
    await press(page.getByRole("button", { name: /Start onboarding Chennai/i }));
  });

  // 7:20 - how VARUNA scores itself, and where it is wrong.
  await until(BEAT.verify);
  await beat("7:20 verification", async () => {
    await open("/verify");
    await expect(page.getByText(/CSI/).first()).toBeVisible({ timeout: SETTLE });
    await page.waitForTimeout(15_000);
    await page.mouse.wheel(0, 900);
    await page.waitForTimeout(8_000);
    await page.mouse.wheel(0, 900);
  });
  await until(BEAT.end);

  const video = page.video();
  await context.close();
  await browser.close();
  // Leave the shared clock paused, as the take found it.
  await request.post(`${API}/v1/replay/pause`).catch(() => undefined);
  const recorded = video ? await video.path() : null;
  expect(recorded, "Playwright wrote no video for the take").toBeTruthy();
  if (recorded && OUT) {
    mkdirSync(path.dirname(OUT), { recursive: true });
    copyFileSync(recorded, OUT);
  }
  console.log(`demo video: ${OUT ?? recorded} (${Math.round((Date.now() - started) / 1000)} s)`);
  expect(missed, `beats that did not happen in the take:\n${missed.join("\n")}`).toEqual([]);
});

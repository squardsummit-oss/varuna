import { mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

/**
 * Renders `apps/command/public/hero/`: the 36 frames the landing hero plays when the API is
 * unreachable (SPEC.md 7.1, "States"), and the frame the Open Graph image is built on (7.1 AC5).
 *
 * **Not a test, and skipped unless asked for.** It is a generator that needs a browser and a
 * running API with the demo runs, which is exactly what this directory's config provides, so it
 * lives here rather than as a script that would re-implement the server wiring:
 *
 *     HERO_FRAMES=1 pnpm exec playwright test landing-hero-frames
 *
 * Each frame is a screenshot of the real hero map - `FloodMap` in hero mode, on the run the hero
 * chose - with the page's own copy, wash and readout hidden, one per five-minute step. The step is
 * read from the hero's readout while the loop is paused by its own hover handler, so a frame is
 * never labelled with a step it does not show. Esri's imagery is blocked while rendering: its
 * tiles are not ours to redistribute (P10.6), so the frames carry only VARUNA's own layers.
 */

const OUT = path.resolve(__dirname, "../../apps/command/public/hero");
const STEPS = 36;
const WIDTH = 1280;
const HEIGHT = 800;

test.skip(!process.env.HERO_FRAMES, "Generator for public/hero/; run with HERO_FRAMES=1");

test("render the hero's frames from the live map", async ({ page }) => {
  test.setTimeout(300_000);
  await page.setViewportSize({ width: WIDTH, height: HEIGHT });
  await page.route(/arcgisonline\.com/, (route) => route.abort());
  await page.goto("/", { waitUntil: "domcontentloaded" });

  // The globe hands over once the run's frames are decoded.
  await expect(page.locator('[data-slot="globe-intro"]')).toHaveCount(0, { timeout: 120_000 });
  const map = page.locator("[data-hero-map]");
  await expect(map).toHaveAttribute("data-run-id", /.+/);
  const runId = (await map.getAttribute("data-run-id"))!;
  const cycleTs = (await map.getAttribute("data-cycle-ts"))!;

  await page.addStyleTag({
    content:
      "[data-hero-copy],[data-hero-wash],[data-hero-readout],[data-hero-map] p.type-micro" +
      "{visibility:hidden !important}",
  });

  const hover = (inside: boolean) =>
    page.evaluate((over) => {
      const section = document.querySelector("section");
      section?.dispatchEvent(
        new MouseEvent(over ? "mouseover" : "mouseout", { bubbles: true, relatedTarget: null }),
      );
    }, inside);
  const currentStep = async () => {
    const text = (await page.locator("[data-hero-readout] p.num").textContent()) ?? "";
    const match = /\+(\d+) min/.exec(text);
    return match ? Number(match[1]) / 5 : null;
  };

  const frames = new Map<number, Buffer>();
  const deadline = Date.now() + 240_000;
  while (frames.size < STEPS && Date.now() < deadline) {
    await hover(true);
    // Let deck draw the paused step before it is photographed.
    await page.waitForTimeout(250);
    const step = await currentStep();
    if (step !== null && !frames.has(step)) {
      frames.set(step, await page.screenshot({ type: "jpeg", quality: 72 }));
    }
    await hover(false);
    await page.waitForTimeout(90 + Math.floor(Math.random() * 200));
  }
  expect(frames.size, `captured ${frames.size} of ${STEPS} steps`).toBe(STEPS);

  mkdirSync(OUT, { recursive: true });
  for (const name of readdirSync(OUT)) rmSync(path.join(OUT, name));
  const names: string[] = [];
  for (let step = 0; step < STEPS; step += 1) {
    const name = `frame-${String(step).padStart(2, "0")}.jpg`;
    writeFileSync(path.join(OUT, name), frames.get(step)!);
    names.push(`/hero/${name}`);
  }
  writeFileSync(
    path.join(OUT, "manifest.json"),
    `${JSON.stringify(
      {
        run_id: runId,
        cycle_ts: cycleTs,
        step_min: 5,
        frames: names,
        width: WIDTH,
        height: HEIGHT,
        rendered_at: new Date().toISOString(),
        rendered_by: "HERO_FRAMES=1 pnpm exec playwright test landing-hero-frames",
      },
      null,
      2,
    )}\n`,
  );
});

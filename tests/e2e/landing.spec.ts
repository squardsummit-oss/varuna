import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "./requirements";

/**
 * The landing page's acceptance criteria (SPEC.md 7.1) that a browser can check, and the
 * phase 9 exit's "offline numbers fallback verified".
 *
 * The API is taken away by aborting every request to it, which is what a venue's dead network
 * looks like to the page: the fetch fails rather than returning an error body.
 */

const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";
const PUBLIC = path.resolve(__dirname, "../../apps/command/public");
const NAV_TIMEOUT = 60_000;

interface Verification {
  scores: { pod: number | null; median_lead_min: number | null };
  ground_truth: { n_in_window: number };
  headline_threshold_cm?: number;
}

function committed(): Verification {
  return JSON.parse(readFileSync(path.join(PUBLIC, "verification.json"), "utf8")) as Verification;
}

async function cutTheApi(page: Page) {
  await page.route(`${API_URL}/**`, (route) => route.abort());
}

/** `text` as a pattern that tolerates whitespace between its characters. */
function loosely(text: string): RegExp {
  const escaped = [...text].map((c) => c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`^\\s*${escaped.join("\\s*")}\\s*$`);
}

/**
 * Asserts the three proof figures read `expected`. Playwright's text matching pierces the shadow
 * root NumberFlow draws its digits into, which `textContent` does not, so this holds whichever
 * branch of the figure is on screen when it is read.
 */
async function expectProofFigures(page: Page, expected: string[]) {
  await page.getByRole("heading", { name: "How we score ourselves" }).scrollIntoViewIfNeeded();
  const figures = page.locator("[data-figure]");
  await expect(figures).toHaveCount(3);
  for (const [index, text] of expected.entries()) {
    await expect(figures.nth(index)).toHaveText(loosely(text), { timeout: 20_000 });
  }
}

function expectedFigures(v: Verification): string[] {
  const pod = v.scores.pod === null ? "—" : v.scores.pod.toFixed(2);
  const lead =
    v.scores.median_lead_min === null
      ? "—"
      : `${Number(v.scores.median_lead_min.toFixed(0)).toFixed(0)}min`;
  return [pod, lead, String(v.ground_truth.n_in_window)];
}

test.describe("landing page (7.1)", () => {
  // Per page rather than `test.use`: the config's own `use` block wins over the context option
  // (see motion.spec.ts), and the proof figures must be read as plain text.
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
  });

  test("the proof numbers come from the committed verification.json when the API is unreachable", async ({
    page,
  }) => {
    await cutTheApi(page);
    await page.goto("/", { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });
    await expectProofFigures(page, expectedFigures(committed()));
  });

  test(
    "the proof numbers are fetched from the API when it is reachable",
    { tag: "@needs-city" },
    async ({ page, request }) => {
      // The positive control for the test above it. Both figures are read from the same page, so
      // a page that always falls back to the committed copy passes the offline test on its own -
      // and a harness whose browser cannot reach the API at all (a CORS allowlist that does not
      // name the UI's port will do it) looks exactly like a venue with no network. This test
      // fails in that case, because the figure it demands is the one only a live fetch produces.
      const served = (await (
        await request.get(`${API_URL}/v1/verification?event=MUM-2019-07-02`, { timeout: 120_000 })
      ).json()) as Verification;
      let asked = false;
      page.on("request", (r) => {
        if (r.url().startsWith(`${API_URL}/v1/verification`)) asked = true;
      });
      await page.goto("/", { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });
      await expectProofFigures(page, expectedFigures(served));
      expect(asked, "the page asked /v1/verification for its proof numbers").toBe(true);
    },
  );

  test(
    "the committed copy is what /v1/verification serves today",
    { tag: "@needs-city" },
    async ({ request }) => {
      // The sweep reads every run's wet streets and the city's road graph.
      const response = await request.get(`${API_URL}/v1/verification?event=MUM-2019-07-02`, {
        timeout: 120_000,
      });
      expect(response.status()).toBe(200);
      expect(await response.json()).toEqual(committed());
    },
  );

  test("table, list and roadmap fit a 390 px phone with no horizontal scroll", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/", { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });
    for (const name of ["Where VARUNA sits", "What the data actually is", "V1, V10, V100"]) {
      await page.getByRole("heading", { name }).scrollIntoViewIfNeeded();
    }
    const overflow = await page.evaluate(() => {
      const wide = [...document.querySelectorAll("main *")].filter((element) => {
        const style = getComputedStyle(element);
        if (style.position === "absolute" || style.position === "fixed") return false;
        return element.getBoundingClientRect().right > window.innerWidth + 0.5;
      });
      const scrollers = [...document.querySelectorAll("main *")].filter(
        (element) =>
          element.scrollWidth > element.clientWidth + 1 &&
          ["auto", "scroll"].includes(getComputedStyle(element).overflowX),
      );
      return {
        page: document.documentElement.scrollWidth - window.innerWidth,
        wide: wide.map((element) => element.tagName),
        scrollers: scrollers.map((element) => element.tagName),
      };
    });
    expect(overflow).toEqual({ page: 0, wide: [], scrollers: [] });
  });

  test("the Open Graph image is a 1200 x 630 PNG built on a committed hero frame", async ({
    request,
  }) => {
    const manifest = JSON.parse(
      readFileSync(path.join(PUBLIC, "hero", "manifest.json"), "utf8"),
    ) as { frames: string[] };
    expect(manifest.frames).toHaveLength(36);
    const image = await request.get("/opengraph-image");
    expect(image.status()).toBe(200);
    expect(image.headers()["content-type"]).toContain("image/png");
    const png = await image.body();
    // IHDR: width and height are the big-endian words at bytes 16 and 20.
    expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([1200, 630]);
    // A text-only card is a few tens of kilobytes; one carrying the map frame is hundreds.
    expect(png.length).toBeGreaterThan(200_000);
    for (const frame of [manifest.frames[0]!, manifest.frames[24]!, manifest.frames[35]!]) {
      expect((await request.get(frame)).status()).toBe(200);
    }
  });

  test("the proof footnote links to the limitations on /verify", async ({ page }) => {
    await cutTheApi(page);
    await page.goto("/", { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });
    const link = page.getByRole("link", { name: /where we fall short/i });
    await expect(link).toHaveAttribute("href", "/verify#limitations");
    await page.goto("/verify#limitations", { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });
    await expect(page.locator("#limitations")).toBeVisible({ timeout: 30_000 });
  });
});

test.describe("landing hero without the API (7.1 states)", () => {
  test("the hero plays the pre-rendered frames, and says so, when the API is unreachable", async ({
    page,
  }) => {
    await cutTheApi(page);
    await page.goto("/", { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });
    const manifest = JSON.parse(
      readFileSync(path.join(PUBLIC, "hero", "manifest.json"), "utf8"),
    ) as { run_id: string; frames: string[] };
    await expect(page.locator('[data-slot="hero-frames"]')).toBeVisible({ timeout: 30_000 });
    await expect(page.locator("[data-hero-readout]")).toContainText(
      "Pre-rendered frames of a baked run",
    );
    // Which run the frames are is provenance, kept on the map layer rather than in the copy.
    await expect(page.locator("[data-hero-map]")).toHaveAttribute("data-run-id", manifest.run_id);
  });
});

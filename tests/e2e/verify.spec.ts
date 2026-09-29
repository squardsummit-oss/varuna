import { expect, test } from "./requirements";

/**
 * The verification dashboard's acceptance criteria (SPEC.md 7.10).
 *
 * The first AC - "all scores computed by `services/verify` from artifacts, not typed in" - is not
 * something a screen can assert about itself, so the test asks the API for the same event and
 * compares every headline figure on the page against what was served. A number typed into the
 * page would survive any test that only checked it was present; it cannot survive being compared
 * with the sweep the scorer just computed.
 *
 * Tagged `@needs-city` because `/v1/verification` reads the road graph and every baked run of the
 * event: on a clean clone there is no city to score against (ADR-0043).
 */

const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";
const EVENT = "MUM-2019-07-02";
const NAV_TIMEOUT = 60_000;

interface ThresholdScores {
  csi: number | null;
  pod: number | null;
  far: number | null;
  median_lead_min: number | null;
}

interface Served {
  headline_threshold_cm: number;
  ground_truth: { n_in_window: number };
  scores: ThresholdScores;
  by_threshold: Record<
    string,
    {
      threshold_cm: number;
      contingency: { hits: number; misses: number; false_alarms: number };
      scores: ThresholdScores;
    }
  >;
}

/** The page prints two decimals for a score, "N min" for a lead, and a plain count for pins. */
const asScore = (value: number | null) => (value === null ? "Not scored yet" : value.toFixed(2));

test.describe("verification (7.10)", () => {
  test(
    "every headline figure on the page is the one services/verify served",
    { tag: "@needs-city" },
    async ({ page, request }) => {
      // The sweep reads each run's wet streets, so it is slow the first time and cached after.
      const response = await request.get(`${API_URL}/v1/verification?event=${EVENT}`, {
        timeout: 120_000,
      });
      expect(response.status()).toBe(200);
      const served = (await response.json()) as Served;

      await page.goto("/verify", { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });
      const scores = page.getByRole("list", { name: "Verification scores" });
      await expect(scores).toBeVisible({ timeout: 30_000 });

      const cm = served.headline_threshold_cm;
      const tile = (label: string) =>
        scores.locator("li").filter({ has: page.getByText(label, { exact: true }) });

      await expect(tile(`CSI at ${cm} cm`)).toContainText(asScore(served.scores.csi));
      await expect(tile(`POD at ${cm} cm`)).toContainText(asScore(served.scores.pod));
      await expect(tile(`FAR at ${cm} cm`)).toContainText(asScore(served.scores.far));
      await expect(tile("Median lead time")).toContainText(
        `${(served.scores.median_lead_min ?? 0).toFixed(0)} min`,
      );
      await expect(tile("Ground-truth pins")).toContainText(
        String(served.ground_truth.n_in_window),
      );
    },
  );

  test(
    "the poured contingency carries every served count, its thresholds and the pin count",
    { tag: "@needs-city" },
    async ({ page, request }) => {
      const served = (await (
        await request.get(`${API_URL}/v1/verification?event=${EVENT}`, { timeout: 120_000 })
      ).json()) as Served;
      const rows = Object.values(served.by_threshold).sort(
        (a, b) => a.threshold_cm - b.threshold_cm,
      );
      expect(rows.length).toBeGreaterThan(0);

      await page.goto("/verify", { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });
      const chart = page.locator('[data-slot="contingency-pour"]');
      await expect(chart).toBeVisible({ timeout: 30_000 });
      const plot = chart.getByRole("img");

      // The shared count axis is named, every threshold the sweep holds is a choice, and the
      // denominator every count is over is printed.
      await expect(chart).toContainText("Count");
      const thresholds = chart.getByRole("radiogroup", { name: "Threshold" });
      for (const row of rows) {
        await expect(
          thresholds.getByRole("radio", { name: `${row.threshold_cm} cm`, exact: true }),
        ).toBeVisible();
      }
      await expect(chart).toContainText(`n = ${served.ground_truth.n_in_window} sourced pins`);
      // Each band carries its word, so colour is never the only carrier.
      for (const word of ["Hits", "Misses", "False alarms"]) {
        await expect(chart.locator("figcaption")).toContainText(word);
      }

      // The plot's own description carries every served count and score, so a reader who cannot
      // see the cylinders is given the same figures rather than "chart".
      const label = (await plot.getAttribute("aria-label")) ?? "";
      for (const row of rows) {
        const c = row.contingency;
        expect(label).toContain(`${row.threshold_cm} cm ${c.hits} hit`);
        expect(label).toContain(`${c.misses} miss`);
        expect(label).toContain(`${c.false_alarms} false alarm`);
        if (row.scores.csi === null) continue;
        expect(label).toContain(`CSI ${row.scores.csi.toFixed(2)}`);
      }

      // Once poured, the readout for the headline threshold states the served scores.
      const readout = chart.locator('[data-slot="contingency-readout"]');
      const headline =
        rows.find((r) => r.threshold_cm === served.headline_threshold_cm) ?? rows[0]!;
      const c = headline.contingency;
      await expect(readout).toContainText(`${c.hits} of ${c.hits + c.misses}`, { timeout: 10_000 });
      if (headline.scores.csi !== null) {
        await expect(readout).toContainText(headline.scores.csi.toFixed(2), { timeout: 10_000 });
      }
    },
  );

  test("the limitations are on the page and are what the landing footnote points at", async ({
    page,
  }) => {
    await page.goto("/verify#limitations", { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });
    const limitations = page.locator("#limitations");
    await expect(limitations).toBeVisible({ timeout: 30_000 });
    // Prose, not an empty anchor: the list has entries.
    expect((await limitations.innerText()).trim().length).toBeGreaterThan(200);
  });
});

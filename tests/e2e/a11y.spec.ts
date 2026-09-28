import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "./requirements";

/**
 * The accessibility floor, checked rather than asserted (SPEC.md 6.10, task P10.3).
 *
 * SPEC.md 6.10 sets a floor: 4.5:1 contrast on text, ARIA labels on icon buttons, a visible
 * focus ring, colour never the only carrier of meaning. Most of that is machine-checkable, and
 * "we were careful" is not evidence - so axe runs over every screen the demo visits.
 *
 * **Scoped to WCAG 2.1 AA**, which is the floor the spec names. Rules outside it are opinions
 * about best practice and would turn this into a test of taste.
 *
 * The map canvas is excluded from the scan and tested separately: a WebGL canvas has no DOM to
 * inspect, and its keyboard story is `CityMap`'s own - deck.gl's controller handles arrows and
 * the time bar owns the scrub (SPEC.md 6.10).
 */

const NAV = 90_000;
const SETTLE = 45_000;

/** Every screen in the demo script, plus the internal design page. */
const SCREENS = [
  "/",
  "/console",
  "/drains",
  "/route",
  "/alerts",
  "/pumps",
  "/whatif",
  "/replay",
  "/onboard",
  "/verify",
  "/map",
  "/report",
  "/design",
] as const;

async function scan(page: Page, path: string) {
  await page.goto(path, { waitUntil: "domcontentloaded", timeout: NAV });
  await expect(page.getByText("VARUNA", { exact: false }).first()).toBeVisible({
    timeout: SETTLE,
  });
  // Panels arrive after their fetches; scanning before they do tests the skeletons.
  await page.waitForTimeout(2_500);

  return (
    new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      // deck.gl's canvas and its own tooltip node: no inspectable DOM, and both are decorative to
      // a screen reader by construction.
      .exclude("canvas")
      .exclude("#deckgl-wrapper")
      .analyze()
  );
}

test.describe("accessibility floor", () => {
  test.slow();

  for (const path of SCREENS) {
    test(`${path} has no WCAG 2.1 AA violations`, async ({ page }) => {
      const results = await scan(page, path);

      const summary = results.violations
        .map(
          (v) =>
            `${v.id} (${v.impact}): ${v.help}\n  ${v.nodes
              .slice(0, 3)
              .map((n) => n.target.join(" "))
              .join("\n  ")}`,
        )
        .join("\n\n");

      expect(results.violations, `axe violations on ${path}:\n\n${summary}`).toEqual([]);
    });
  }
});

test.describe("keyboard", () => {
  test.slow();

  test("the console is reachable by tab, and focus is visible", async ({ page }) => {
    await page.goto("/console", { waitUntil: "domcontentloaded", timeout: NAV });
    await expect(page.getByText("VARUNA", { exact: false }).first()).toBeVisible({
      timeout: SETTLE,
    });

    // Walk far enough into the page to have passed the rail and the layer panel.
    for (let i = 0; i < 25; i += 1) await page.keyboard.press("Tab");

    const focused = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const style = getComputedStyle(el);
      return {
        tag: el.tagName,
        // A focus ring is either an outline or a box-shadow; the token set uses a ring.
        outline: style.outlineStyle !== "none" && style.outlineWidth !== "0px",
        shadow: style.boxShadow !== "none",
      };
    });

    expect(focused, "nothing was focused after 25 tabs").not.toBeNull();
    expect(
      focused?.outline || focused?.shadow,
      `focused <${focused?.tag}> has no visible focus ring`,
    ).toBe(true);
  });

  test("the time bar scrubs with the arrow keys", async ({ page }) => {
    await page.goto("/console", { waitUntil: "domcontentloaded", timeout: NAV });
    // Scoped to the time bar. The hotspot rail prints each junction's time to peak in the same
    // "08:20 (+40 min)" form and sits earlier in the DOM, so an unscoped `.first()` read a row that
    // no scrub moves once the rail had loaded - and, being lazy, could read the time bar before
    // the press and the rail after it.
    const readout = page
      .getByRole("toolbar", { name: "Replay time bar" })
      .getByText(/\d{2}:\d{2} \(\+\d+ min\)/);
    await expect(readout).toBeVisible({ timeout: SETTLE });
    // The run has loaded and been decoded once this scrub control exists (P6.3); pressing before
    // that would compare two readouts the load itself can move.
    await expect(page.getByRole("slider", { name: "Scrub the forecast", exact: true })).toBeEnabled(
      {
        timeout: SETTLE,
      },
    );

    const before = await readout.textContent();
    await page.keyboard.press("ArrowRight");
    await page.waitForTimeout(400);
    const after = await readout.textContent();

    expect(after, "ArrowRight did not move the scrub").not.toBe(before);
  });
});

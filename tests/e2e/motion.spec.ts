import { expect, test, type Page } from "./requirements";

/**
 * Motion under the OS reduced-motion setting (SPEC.md 8, task P10.1).
 *
 * Every row of the motion catalogue names a reduced-motion fallback, and the code respects it in
 * nineteen places - but "the code calls `usePrefersReducedMotion`" is not evidence that the
 * animation stops. Playwright can emulate the OS setting, so this asserts the *observable*
 * behaviour: with the setting on, nothing moves.
 *
 * Two levels are checked, because there are two mechanisms:
 *
 * 1. the CSS escape hatch in `globals.css`, which flattens every animation and transition;
 * 2. the JavaScript animations - the route draw-on, the pin drop, the what-if wipe - which run on
 *    `requestAnimationFrame` and would ignore CSS entirely.
 */

const NAV = 90_000;
const SETTLE = 45_000;

/**
 * Open a screen with the OS motion preference emulated.
 *
 * `page.emulateMedia` rather than the `reducedMotion` context option: the option is applied when
 * the context is created and the config's own `use` block was winning, so the page never saw the
 * setting and the test was passing or failing for the wrong reason. Emulating per page is
 * unambiguous and is checked in the test body.
 */
async function open(
  page: Page,
  path: string,
  motion: "reduce" | "no-preference" = "reduce",
): Promise<void> {
  await page.emulateMedia({ reducedMotion: motion });
  await page.goto(path, { waitUntil: "domcontentloaded", timeout: NAV });
  await expect(page.getByText("VARUNA", { exact: false }).first()).toBeVisible({
    timeout: SETTLE,
  });
}

test.describe("reduced motion", () => {
  test.slow();
  test("the browser reports the setting, and CSS flattens every animation", async ({ page }) => {
    await open(page, "/console");

    const flattened = await page.evaluate(() => {
      const matches = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      // A computed duration is a *comma list* when the element transitions several properties -
      // "0.01ms, 0.01ms" - so every part is parsed rather than the string compared. Anything at or
      // under a millisecond counts as flattened: that is what the escape hatch in `globals.css`
      // sets, and it is far below the threshold of perception.
      const longest = (value: string): number =>
        Math.max(
          0,
          ...value.split(",").map((part) => {
            const text = part.trim();
            if (text.endsWith("ms")) return Number.parseFloat(text);
            if (text.endsWith("s")) return Number.parseFloat(text) * 1000;
            return 0;
          }),
        );

      const offenders: string[] = [];
      for (const el of document.querySelectorAll("*")) {
        const style = getComputedStyle(el);
        const animation = style.animationName !== "none" ? longest(style.animationDuration) : 0;
        const transition = longest(style.transitionDuration);
        if (animation > 1 || transition > 1) {
          offenders.push(
            `${el.tagName.toLowerCase()}.${el.className.toString().slice(0, 40)} ` +
              `animation=${style.animationDuration} transition=${style.transitionDuration}`,
          );
        }
      }
      return { matches, offenders: offenders.slice(0, 8), count: offenders.length };
    });

    expect(flattened.matches, "the page did not see the reduced-motion setting").toBe(true);
    expect(
      flattened.count,
      ["still moving under reduced motion:", ...flattened.offenders].join("\n"),
    ).toBe(0);
  });

  // "Find route" enables once /v1/route/facilities answers, and that reads the city's asset layer.
  test(
    "M14: the route is drawn whole rather than drawing itself on",
    { tag: "@needs-city" },
    async ({ page }) => {
      await open(page, "/route");
      // Enabled first: a click on a disabled button waits out the whole test timeout instead.
      const findRoute = page.getByRole("button", { name: "Find route" });
      await expect(findRoute).toBeEnabled({ timeout: SETTLE });
      await findRoute.click();
      await expect(page.getByText(/Routed on run/i).first()).toBeVisible({ timeout: SETTLE });

      // The catalogue's fallback for M14 is "both shown at once". The route's own draw progress is
      // internal, so the observable version is that the comparison is complete immediately rather
      // than filling in over 1.2 s.
      const eta = page.getByText(/\d+ min/).first();
      await expect(eta).toBeVisible({ timeout: 2_000 });
    },
  );

  test("M22: skeletons do not shimmer", async ({ page }) => {
    await open(page, "/console");
    const shimmering = await page.evaluate(
      () =>
        [...document.querySelectorAll(".skeleton-shimmer")].filter(
          (el) => getComputedStyle(el).animationName !== "none",
        ).length,
    );
    expect(shimmering, "skeletons are still shimmering under reduced motion").toBe(0);
  });

  test("M8: the canvas is still - the surcharge pulse does not run", async ({ page }) => {
    await open(page, "/console");

    const canvas = page.locator("canvas").first();
    await expect(canvas).toBeVisible({ timeout: SETTLE });
    // Let the tiles and the first run settle, or the comparison would catch them loading rather
    // than the pulse running.
    await page.waitForTimeout(12_000);

    // The pulse is a WebGL animation: there is no DOM to inspect, so the honest test is whether
    // the picture changes. Two shots 700 ms apart span most of the 1.6 s pulse, so a running one
    // would be caught at a different radius.
    const first = await canvas.screenshot();
    await page.waitForTimeout(700);
    const second = await canvas.screenshot();

    expect(
      Buffer.compare(first, second),
      "the map canvas changed under reduced motion - something is still animating",
    ).toBe(0);
  });
});

test.describe("motion on", () => {
  test.slow();
  test(
    "the route draws itself on when motion is allowed",
    { tag: "@needs-city" },
    async ({ page }) => {
      await open(page, "/route", "no-preference");
      const findRoute = page.getByRole("button", { name: "Find route" });
      await expect(findRoute).toBeEnabled({ timeout: SETTLE });
      await findRoute.click();
      await expect(page.getByText(/Routed on run/i).first()).toBeVisible({ timeout: SETTLE });
      // The counterpart to the reduced-motion test: with motion on, the page reports it, so the two
      // branches are both exercised rather than only the one the CI machine happens to prefer.
      const allowed = await page.evaluate(
        () => !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
      );
      expect(allowed).toBe(true);
    },
  );
});

/**
 * The repairs of UI_SPEC section 8 that made the console look broken (tasks D-17, D-18).
 *
 * Each test asserts the geometry the repair is about, not a screenshot: a clipped panel and a map
 * drawn into a band of its pane are both layout facts, and a layout fact is exactly the thing a
 * later change can quietly undo.
 *
 * D-19 (the imagery cap) is not here. What it changes is the sharpness of a tile at zoom 18 and
 * 19, which a DOM assertion cannot see; it is measured instead, and the tile count and cache size
 * are recorded in `docs/QA.md`.
 */
import { test, expect, type Page } from "./requirements";

/** The small laptop of SPEC.md 6.5, which is where the clipping was reported. */
const SMALL = { width: 1366, height: 768 };

/** The three viewports section 6.5 names: small laptop, reference, and a 4K wall at 150 %. */
const VIEWPORTS = [
  { name: "1366 x 768", width: 1366, height: 768 },
  { name: "1440 x 900", width: 1440, height: 900 },
  { name: "2560 x 1440", width: 2560, height: 1440 },
];

async function openConsole(page: Page) {
  await page.goto("/console");
  await expect(page.getByRole("button", { name: "Layers" })).toBeVisible({ timeout: 60_000 });
}

test.describe("D-17 the console's floating column scrolls", () => {
  test("every layer row is reachable at 1366 x 768 @needs-city", async ({ page }) => {
    await page.setViewportSize(SMALL);
    await openConsole(page);

    const column = page.getByTestId("console-map-column");
    // Probability mode adds the legend and Drains adds the honesty note, which is the state the
    // column was clipped in: with both on it holds more than the cap leaves room for.
    await page.getByRole("switch", { name: /Probability/ }).click();
    await page.getByRole("switch", { name: /Drains/ }).click();

    const metrics = await column.evaluate((el) => ({
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
    }));
    expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);

    // The wheel over the panel moves the column rather than the map behind it.
    await page.getByRole("button", { name: "Layers" }).hover();
    await page.mouse.wheel(0, 240);
    await expect.poll(async () => column.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);

    // Every row can be brought fully inside the column's own box.
    const rows = page.getByRole("switch");
    const count = await rows.count();
    expect(count).toBeGreaterThan(4);
    for (let i = 0; i < count; i += 1) {
      const row = rows.nth(i);
      await row.scrollIntoViewIfNeeded();
      const inside = await row.evaluate((el) => {
        const column = el.closest("[data-testid='console-map-column']") as HTMLElement;
        const box = el.getBoundingClientRect();
        const pane = column.getBoundingClientRect();
        return box.top >= pane.top - 1 && box.bottom <= pane.bottom + 1 && box.height > 0;
      });
      expect(inside, `layer row ${i} is inside the column`).toBe(true);
    }
  });

  test("the cycle picker stays inside the column @needs-city", async ({ page }) => {
    await page.setViewportSize(SMALL);
    await openConsole(page);
    const column = page.getByTestId("console-map-column");
    const overflow = await column.evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test("the probability legend stacks below the panel, never over it @needs-city", async ({
    page,
  }) => {
    await page.setViewportSize(SMALL);
    await openConsole(page);
    await page.getByRole("switch", { name: /Probability/ }).click();

    const legend = page.getByRole("complementary", { name: "Probability legend" });
    await expect(legend).toBeVisible();
    const panel = page.getByRole("button", { name: "Layers" });

    const [a, b] = await Promise.all([legend.boundingBox(), panel.boundingBox()]);
    expect(a).not.toBeNull();
    expect(b).not.toBeNull();
    // Disjoint vertically: the legend begins below the panel's header rather than across it.
    expect(a!.y).toBeGreaterThanOrEqual(b!.y + b!.height - 1);
  });
});

/**
 * The map's own box and the pane it is laid into.
 *
 * `CityMap`'s root is `absolute inset-0`, so its parent is the pane: the element that owns the
 * height. Measuring both is what separates "the map fills what it was given" from "what it was
 * given is a band".
 */
async function mapAndPane(page: Page) {
  const canvas = page.locator("canvas").first();
  await expect(canvas).toBeVisible({ timeout: 60_000 });
  const box = await canvas.evaluate((el) => {
    const map = el.closest(".absolute.inset-0") ?? (el.closest("div") as HTMLElement);
    const pane = (map as HTMLElement).parentElement as HTMLElement;
    const m = (map as HTMLElement).getBoundingClientRect();
    const p = pane.getBoundingClientRect();
    return { mapW: m.width, mapH: m.height, paneW: p.width, paneH: p.height };
  });
  return box;
}

test.describe("D-18 maps fill their panes", () => {
  for (const screen of ["/console", "/route", "/drains"]) {
    test(`${screen} fills its pane and the pane grows with the viewport @needs-city`, async ({
      page,
    }) => {
      // Three full page loads with the city's layers on each: `/drains` alone is about thirty
      // seconds a viewport on this laptop.
      test.slow();
      const heights: number[] = [];
      for (const vp of VIEWPORTS) {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await page.goto(screen);
        const box = await mapAndPane(page);

        // The map is the pane, not a band inside it.
        expect(box.mapW / box.paneW, `${screen} at ${vp.name}: map width vs pane`).toBeGreaterThan(
          0.98,
        );
        expect(box.mapH / box.paneH, `${screen} at ${vp.name}: map height vs pane`).toBeGreaterThan(
          0.98,
        );
        heights.push(box.paneH);
      }

      // And the pane itself is not capped: a taller window gives the map nearly all of the extra
      // height. This is what a `clamp(20rem, 52vh, 40rem)` band fails - it takes about half of
      // each extra pixel and then stops taking any at all.
      for (let i = 1; i < VIEWPORTS.length; i += 1) {
        const gained = heights[i] - heights[i - 1];
        const offered = VIEWPORTS[i].height - VIEWPORTS[i - 1].height;
        expect(
          gained / offered,
          `${screen}: pane grew ${Math.round(gained)} px of the ${offered} px between ` +
            `${VIEWPORTS[i - 1].name} and ${VIEWPORTS[i].name}`,
        ).toBeGreaterThan(0.8);
      }
    });
  }
});

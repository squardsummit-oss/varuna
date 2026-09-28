import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "./requirements";

/**
 * Citizen reports on the dashboard and the ward desk, driven through real browser events.
 *
 * These exist because a unit test once passed while the behaviour it named did not happen: a tap
 * on a report pin opened the card *and* became the trip's destination on every pin, and the vitest
 * case passed only because it fired Google's click and deck's in one synchronous act, which a
 * browser does not do. So the pin is found the way a reader finds it - "Show on the map" puts it at
 * the centre of the map - and tapped with the mouse.
 *
 * Nothing here writes: every POST to the report and ops endpoints is aborted, so no test report
 * lands in the demo inbox and no status is set.
 *
 * The dashboard draws Google's basemap when its key loads and VARUNA's own map when it does not.
 * The assertions hold on both; the one that needs a destination to be pickable at all runs only on
 * Google's, because VARUNA's own map takes no destination taps.
 */

const INTRO_KEYS = ["varuna.dashboard-intro.played", "varuna.authority-intro.played"];

async function prepare(page: Page): Promise<void> {
  await page.addInitScript((keys) => {
    for (const key of keys) window.sessionStorage.setItem(key, "1");
  }, INTRO_KEYS);
  await page.route("**/v1/reports", (route) =>
    route.request().method() === "POST" ? route.abort() : route.continue(),
  );
  await page.route("**/v1/ops/**", (route) =>
    route.request().method() === "POST" ? route.abort() : route.continue(),
  );
}

/** "google" once Google has painted tiles, "varuna" once the fallback notice is up. */
async function basemap(page: Page): Promise<"google" | "varuna"> {
  const map = page.locator('[data-slot="citizen-map"]');
  await expect(map).toBeVisible({ timeout: 60_000 });
  await expect
    .poll(
      () =>
        page.evaluate(() => {
          if (document.querySelector('[data-slot="basemap-notice"]')) return "varuna";
          const root = document.querySelector('[data-slot="citizen-map"]');
          const painted =
            root?.querySelector(".gm-style") && !root.querySelector(":scope > div[aria-hidden]");
          return painted ? "google" : "pending";
        }),
      { timeout: 30_000 },
    )
    .not.toBe("pending");
  return (await page.locator('[data-slot="basemap-notice"]').count()) ? "varuna" : "google";
}

test.describe("citizen reports on the dashboard", () => {
  test.slow();

  test("a tap on a report pin opens its card and is not taken as a destination @needs-city", async ({
    page,
  }) => {
    await prepare(page);
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    const path = await basemap(page);
    const complaint = page.locator('[data-slot="complaints"] li').first();
    await expect(complaint).toBeVisible({ timeout: 60_000 });

    await complaint.getByRole("button", { name: /Show on the map/ }).click();
    await expect(page.locator('[data-slot="selected-report"]')).toBeVisible();
    // The camera glides to the pin (M10); let it land before tapping where it landed.
    await page.waitForTimeout(3_000);

    // Open with the card open, and the page's heading order kept (an h2 after the h1).
    const scan = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .exclude("canvas")
      .exclude("#deckgl-wrapper")
      .analyze();
    expect(scan.violations.filter((v) => v.id === "heading-order")).toEqual([]);

    await page.getByRole("button", { name: "Close this report" }).click();
    await expect(page.locator('[data-slot="selected-report"]')).toHaveCount(0);

    const box = await page.locator('[data-slot="citizen-map"]').boundingBox();
    if (!box) throw new Error("the map has no box");
    const centre = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    // A mouse arrives before it clicks.
    await page.mouse.move(centre.x, centre.y);
    await page.waitForTimeout(300);
    await page.mouse.click(centre.x, centre.y, { delay: 80 });

    await expect(page.locator('[data-slot="selected-report"]')).toBeVisible();
    await expect(page.locator("#dashboard-to")).toHaveValue("");
    await expect(page.locator('#dashboard-to option[value="__picked__"]')).toHaveCount(0);

    if (path === "google") {
      // The control: a tap on empty map is still a destination, so the check above is not
      // passing because taps do nothing at all.
      await page.getByRole("button", { name: "Close this report" }).click();
      await page.mouse.click(box.x + box.width * 0.85, box.y + box.height * 0.2, { delay: 80 });
      await expect(page.locator("#dashboard-to")).toHaveValue("__picked__");
      await expect(page.locator('[data-slot="selected-report"]')).toHaveCount(0);
    }
  });

  test("on a phone, showing a complaint puts the sheet down so the card and its pin are seen @needs-city", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await prepare(page);
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    await basemap(page);

    const handle = page.getByRole("button", { name: /Your way there/ });
    await handle.click();
    await handle.click();
    await expect(handle).toHaveAttribute("aria-label", /fully open/);

    const show = page
      .locator('[data-slot="complaints"] li')
      .first()
      .getByRole("button", { name: /Show on the map/ });
    await show.scrollIntoViewIfNeeded();
    await show.click();

    await expect(page.getByRole("button", { name: /Your way there/ })).toHaveAttribute(
      "aria-label",
      /collapsed/,
    );
    const card = await page.locator('[data-slot="selected-report"]').boundingBox();
    const map = await page.locator('[data-slot="citizen-map"]').boundingBox();
    const sheet = await page.getByRole("button", { name: /Your way there/ }).boundingBox();
    if (!card || !map || !sheet) throw new Error("the card, the map or the sheet has no box");
    // The pin is centred on the map: it is below the card and above the sheet.
    const pinY = map.y + map.height / 2;
    expect(pinY).toBeGreaterThan(card.y + card.height);
    expect(pinY).toBeLessThan(sheet.y);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      390,
    );
  });
});

test.describe("citizen reports on the ward desk", () => {
  test.slow();

  test("at 1366 x 768 the inbox shows a whole report beside the map @needs-city", async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await prepare(page);
    await page.goto("/authority", { waitUntil: "domcontentloaded" });
    const list = page.getByRole("list", { name: "Citizen reports" });
    await expect(list.locator("li").first()).toBeVisible({ timeout: 60_000 });

    const listBox = await list.boundingBox();
    const cardBox = await list.locator("li").first().boundingBox();
    if (!listBox || !cardBox) throw new Error("the inbox has no box");
    expect(cardBox.height).toBeLessThanOrEqual(listBox.height);

    // On an API that takes no writes, the row says why in the API's own words - the read-only
    // note, not the last note in the list.
    const api = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";
    const log = await (await request.get(`${api}/v1/ops/log?city=mumbai`)).json();
    if (log.writes_enabled === false) {
      const reason = (log.notes as string[]).find((note) => /read-only/i.test(note));
      await list.locator("li").first().getByRole("button", { name: "Open on the map" }).click();
      const closed = page.locator('[data-slot="report-status-closed"]');
      await expect(closed).toBeVisible();
      if (reason) await expect(closed).toHaveText(reason);
      await expect(closed).not.toContainText("X-Varuna-Ops");
    }
  });
});

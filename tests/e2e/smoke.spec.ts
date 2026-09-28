import { expect, test, type ConsoleMessage, type Page } from "./requirements";

// Phase 0 smoke: the shell renders, the empty state is honest, no console errors.
// Waits are generous because the first Turbopack compile of a route can take a while.

const NAV_TIMEOUT = 60_000;

function collectConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message: ConsoleMessage) => {
    if (message.type() === "error") {
      errors.push(message.text());
    }
  });
  page.on("pageerror", (error) => {
    errors.push(`pageerror: ${error.message}`);
  });
  return errors;
}

test.describe("Phase 0 shell", () => {
  // Counts console errors, and the map's road and asset layers 404 without a built city.
  test(
    "/console shows the empty-state mode banner and the wordmark",
    { tag: "@needs-city" },
    async ({ page }) => {
      const errors = collectConsoleErrors(page);
      await page.goto("/console", { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });

      await expect(page.getByText("No runs yet", { exact: false }).first()).toBeVisible({
        timeout: 30_000,
      });
      await expect(page.getByText("VARUNA", { exact: false }).first()).toBeVisible({
        timeout: 30_000,
      });

      // Give lazy panels (replay panel, tokens, map placeholder) time to settle before judging.
      await page.waitForTimeout(2_000);
      expect(errors, `console.error entries on /console:\n${errors.join("\n")}`).toEqual([]);
    },
  );

  test("/design responds", async ({ page }) => {
    const response = await page.goto("/design", {
      waitUntil: "domcontentloaded",
      timeout: NAV_TIMEOUT,
    });
    expect(response, "no response from /design").not.toBeNull();
    expect(response!.status(), "/design should not error").toBeLessThan(400);
    await expect(page.locator("body")).toBeVisible();
  });

  test("/ shows the headline", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });
    await expect(
      page.getByText("Every street. Three hours early.", { exact: false }).first(),
    ).toBeVisible({ timeout: 30_000 });
  });
});

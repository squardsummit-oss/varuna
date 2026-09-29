import { expect, test, type ConsoleMessage, type Page } from "./requirements";

/**
 * The demo script, walked end to end (SPEC.md 15, task P10.5).
 *
 * Every step of the eight-minute run, asserted on what a judge would actually look at rather than
 * on markup: a run stamp with a real id, streets that are wet, a route with two columns of numbers,
 * pins with source links, a drain map with a learned posterior. If a number is missing the test
 * fails, which is the point - `make demo` being green has to mean the demo works, not that the
 * pages render.
 *
 * **It asserts numbers exist, never their values.** The forecast is recomputed whenever the bundle
 * is re-baked, and a test that pinned "55 cm at Hindmata" would either go stale or quietly become
 * a test of nothing. The values are `/verify`'s job.
 *
 * Generous timeouts throughout: the first Turbopack compile of a route takes seconds, and the API
 * may be waking from sleep.
 */

const NAV = 90_000;
const SETTLE = 45_000;

/** Console noise that is not VARUNA's and cannot be fixed from here. */
const IGNORED = [
  // Esri's tile CDN rate-limits under a test run; the map falls back to the derived GIS by design.
  /arcgisonline/i,
  // Next's dev-only hot-reload socket, which the test harness closes as it navigates.
  /_next\/hmr|HMR|Fast Refresh/i,
  /WebSocket .*(closed|failed)/i,
];

function collectConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message: ConsoleMessage) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (IGNORED.some((pattern) => pattern.test(text))) return;
    errors.push(text);
  });
  page.on("pageerror", (error) => {
    if (IGNORED.some((pattern) => pattern.test(error.message))) return;
    errors.push(`pageerror: ${error.message}`);
  });
  return errors;
}

/** Open a screen and wait for it to stop being a skeleton. */
async function open(page: Page, path: string): Promise<void> {
  await page.goto(path, { waitUntil: "domcontentloaded", timeout: NAV });
  await expect(page.getByText("VARUNA", { exact: false }).first()).toBeVisible({
    timeout: SETTLE,
  });
}

test.describe("the demo script", () => {
  test.slow();

  // Counts console errors, and the map's road and asset layers 404 without a built city.
  test(
    "0:00 the console opens on a real run, with the mode banner and the run stamp",
    { tag: "@needs-city" },
    async ({ page }) => {
      const errors = collectConsoleErrors(page);
      await open(page, "/console");

      // A run stamp that names an actual run, not the empty state.
      await expect(page.getByText(/MUM-\d{8}T\d{4}Z/).first()).toBeVisible({ timeout: SETTLE });
      // The mode banner says what kind of run this is (SPEC.md 7.2).
      await expect(page.getByText(/Replay|Live|baked/i).first()).toBeVisible();

      await page.waitForTimeout(3_000);
      expect(errors, `console errors on /console:\n${errors.join("\n")}`).toEqual([]);
    },
  );

  test("0:40 the hotspot rail ranks real places with depths", async ({ page }) => {
    await open(page, "/console");
    const rail = page.getByRole("tabpanel").first();
    // Ranked rows, each with a depth chip in centimetres.
    await expect(rail.getByText(/\d+ cm/).first()).toBeVisible({ timeout: SETTLE });
    await expect(rail.getByText(/peak .* at \d{2}:\d{2}/).first()).toBeVisible({
      timeout: SETTLE,
    });
  });

  test("1:40 the cycle row lets the operator reach the storm's peak", async ({ page }) => {
    await open(page, "/console");
    const cycles = page.getByRole("button", { name: /Forecast from \d{2}:\d{2} IST/ });
    await expect(cycles.first()).toBeVisible({ timeout: SETTLE });
    expect(await cycles.count()).toBeGreaterThan(1);
  });

  test("2:40 the ground-truth pins carry the source they were read from", async ({ page }) => {
    // The console's "As it happened" ticker was removed from the hotspot rail at the team's
    // request. The pins still drop on the map as the clock passes them, but that is WebGL and has
    // no markup to assert on, so the sourcing - which is what rule 7 is about and what this beat
    // is for - is asserted where it is now read: /verify lists every scored pin with its URL.
    await open(page, "/verify");
    await expect(page.getByText(/Ground-truth pins:/)).toBeVisible({ timeout: SETTLE });
    // Either pins have been scored and carry their sources, or the event has none to show - both
    // are honest, but a pin without a link never is (rule 7).
    const sources = page.getByRole("link", { name: "Source" });
    if ((await sources.count()) > 0) {
      await expect(sources.first()).toHaveAttribute("href", /^https?:\/\//);
    }
  });

  // Counts console errors, and the drain and road layers 404 without a built city.
  test(
    "3:30 the drain X-ray shows a learned posterior over the inferred graph",
    { tag: "@needs-city" },
    async ({ page }) => {
      const errors = collectConsoleErrors(page);
      await open(page, "/drains");

      await expect(page.getByText(/Inferred drain graph/i).first()).toBeVisible({
        timeout: SETTLE,
      });
      // The table ranks pipes by blockage, with a capacity reduction for each.
      await expect(page.getByText(/\d+ %/).first()).toBeVisible({ timeout: SETTLE });
      await expect(page.getByText(/pipes moved this cycle/i).first()).toBeVisible({
        timeout: SETTLE,
      });

      await page.waitForTimeout(2_000);
      expect(errors, `console errors on /drains:\n${errors.join("\n")}`).toEqual([]);
    },
  );

  test("4:30 rain plus 30 % deepens streets and prints the emulator's measured skill", async ({
    page,
  }) => {
    await open(page, "/whatif");

    // The scenario has to be a scenario. At the default 1.0x the endpoint moves nothing - 0
    // segments deeper, 0 shallower - so clicking Run straight away certifies a no-op. 1.3x is
    // the demo's "rain plus 30 %".
    //
    // Written to the range input Base UI renders behind the thumb, and retried until the readout
    // agrees: a value set before the controls hydrate is silently dropped, which is how this
    // test first passed with the slider still at 1.0x.
    const rain = page.getByRole("region", { name: "Rain scale" });
    const slider = rain.getByRole("slider", { name: "Rain scale" });
    // The readout, not the help text under the slider - that sentence names 1.3x itself, so a
    // plain text match here would pass with the slider untouched.
    const readout = rain.getByRole("status");
    await expect(async () => {
      await slider.fill("1.3");
      await expect(readout).toHaveText("1.3x", { timeout: 1_000 });
    }).toPass({ timeout: SETTLE });

    await page.getByRole("button", { name: "Run what-if" }).click();

    // A count that is not zero: en-IN grouping, so "1,687 segments deeper". Anchored, because
    // the panel's ancestor text runs the attribution line into this one - "GLO-30" followed by
    // "0 segments deeper" reads as "GLO-300 segments deeper" and matches an unanchored pattern.
    await expect(page.getByText(/^[1-9][\d,]* segments deeper/)).toBeVisible({
      timeout: SETTLE,
    });
    // The honesty label, and the skill the answer was measured at - printed beside it rather
    // than implied (SPEC.md 7.7, ADR-0025).
    await expect(page.getByText(/Reduced-order emulator/i).first()).toBeVisible();
    await expect(
      page.getByText(/RMSE \d+(\.\d+)? cm, CSI \d+(\.\d+)? at 30 cm/).first(),
    ).toBeVisible();
  });

  // Section 15's 3:30 beat: "why this junction floods", ranked on drain1d (ADR-0071). It asserts
  // the drawer carries either a ranking or the measured refusal - both name the pipes re-run -
  // and never the old "cleaning these 14 pipes: 55 -> 20 cm", which no cycle computes.
  test("3:30 the hotspot drawer says which pipes explain a junction, or why none do", async ({
    page,
  }) => {
    await open(page, "/console");
    const rows = page.getByRole("list", { name: "Ranked hotspots" }).getByRole("button");
    await expect(rows.first()).toBeVisible({ timeout: SETTLE });
    await rows.first().click();
    await expect(page.getByText("Why this junction floods").first()).toBeVisible({
      timeout: SETTLE,
    });
    await expect(page.getByText(/upstream hops|drain1d|Not attributed/i).first()).toBeVisible({
      timeout: SETTLE,
    });
  });

  test("4:30 the physics check prints the emulator against the Twin, agreeing or not", async ({
    page,
  }) => {
    await open(page, "/whatif");
    const rain = page.getByRole("region", { name: "Rain scale" });
    const slider = rain.getByRole("slider", { name: "Rain scale" });
    const readout = rain.getByRole("status");
    await expect(async () => {
      await slider.fill("1.3");
      await expect(readout).toHaveText("1.3x", { timeout: 1_000 });
    }).toPass({ timeout: SETTLE });

    await page.getByRole("button", { name: "Physics check" }).click();
    // Two coupled Twin runs on a 990 m window: 2.4-4.7 s warm, about 20 s on a process's first
    // call (ADR-0077). The disagreement is printed either way - section 7.7 forbids hiding it.
    await expect(page.getByText(/Emulator vs physics: max difference/).first()).toBeVisible({
      timeout: 90_000,
    });
    await expect(page.getByText(/Within tolerance|Outside tolerance/).first()).toBeVisible();
    await expect(page.getByText(/The Twin ran twice on a \d+ m window/).first()).toBeVisible();
  });

  // "Find route" enables once /v1/route/facilities answers, and that reads the city's asset layer.
  test(
    "5:20 the route planner compares the naive route with VARUNA's",
    { tag: "@needs-city" },
    async ({ page }) => {
      await open(page, "/route");
      // Enabled first, then clicked: a click waits for a disabled button until the test times out,
      // which is how this test spent twelve minutes in CI. This fails in 45 s and names the cause.
      const findRoute = page.getByRole("button", { name: "Find route" });
      await expect(findRoute).toBeEnabled({ timeout: SETTLE });
      await findRoute.click();

      await expect(page.getByText(/Routed on run/i).first()).toBeVisible({ timeout: SETTLE });
      // Two columns of real numbers: an ETA and a distance for each route.
      await expect(page.getByText(/\d+ min/).first()).toBeVisible({ timeout: SETTLE });
      await expect(page.getByText(/\d+(\.\d+)? km/).first()).toBeVisible();
    },
  );

  test("5:20 the alert centre raises alerts with a CAP document at Exercise", async ({ page }) => {
    await open(page, "/alerts");
    await expect(page.getByText(/Severe|Moderate|Watch/).first()).toBeVisible({ timeout: SETTLE });
    // Replay alerts must say on their face that they are a drill (SPEC.md 11.10).
    await expect(page.getByText(/Exercise/i).first()).toBeVisible({ timeout: SETTLE });
  });

  test("5:20 the pump board assigns pumps and writes the order in plain language", async ({
    page,
  }) => {
    await open(page, "/pumps");
    await expect(page.getByText(/Synthetic pump inventory/i).first()).toBeVisible({
      timeout: SETTLE,
    });
    await expect(page.getByText(/of \d+ pumps assigned/i).first()).toBeVisible({ timeout: SETTLE });
  });

  test("6:30 the onboarding wizard is ready to build Chennai", async ({ page }) => {
    await open(page, "/onboard");
    await expect(page.getByRole("button", { name: /onboarding Chennai/i })).toBeEnabled({
      timeout: SETTLE,
    });
    await expect(page.getByText(/Choose area/i).first()).toBeVisible();
  });

  // Counts console errors, and /v1/verification scores against the city's road graph.
  test(
    "7:20 verification reports scores computed from artifacts, with its limits",
    { tag: "@needs-city" },
    async ({ page }) => {
      const errors = collectConsoleErrors(page);
      await open(page, "/verify");

      // A CSI, and the count of ground truth it was scored against.
      await expect(page.getByText(/CSI/i).first()).toBeVisible({ timeout: SETTLE });
      await expect(page.getByText(/\d+(\.\d+)?/).first()).toBeVisible();

      await page.waitForTimeout(2_000);
      expect(errors, `console errors on /verify:\n${errors.join("\n")}`).toEqual([]);
    },
  );

  test("the public map draws the run in three colours for a chosen vehicle", async ({ page }) => {
    await open(page, "/map");
    await expect(page.getByText(/passable|impassable|caution/i).first()).toBeVisible({
      timeout: SETTLE,
    });
    // The honesty line: which run this is and how often it updates (SPEC.md 7.11).
    await expect(page.getByText(/a new run every 30 minutes|last VARUNA run/i).first()).toBeVisible();
  });
});

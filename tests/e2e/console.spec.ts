/**
 * The console's keyboard path, its states and its two hard viewports (P6.13, P6.14).
 *
 * SPEC.md 7.2 lists twelve shortcuts and a `?` overlay that lists them, and its AC asks for
 * "zero console errors during a full replay". 6.11 asks every screen to work at 1366 x 768 and
 * on a 4K wall at 150 % zoom. All of it existed in `lib/shortcuts.ts` and
 * `components/varuna/shortcuts-overlay.tsx`; none of it was checked, which is the difference
 * between a shortcut that works and a shortcut nobody has pressed since it was written.
 *
 * The layer toggles are asserted through the layer panel's own checked state rather than by
 * looking at the map: deck.gl draws into a canvas, so the only readable evidence that `D`
 * reached the drains layer is the control that reports it.
 */
import { expect, test, type ConsoleMessage, type Page } from "./requirements";

/**
 * Wait until a run is actually loaded, not merely until the shell has painted.
 *
 * The layer shortcuts are gated on `useRunStore().currentRun` - a key that changed a layer
 * before the run arrived would be changing what the map shows about nothing - so a test that
 * presses one the moment the panel attaches is racing the fetch, not testing the shortcut.
 * The run stamp is the first thing on screen that only exists once the run is in the store.
 */
async function waitForRun(page: Page): Promise<void> {
  await expect(page.getByText(/MUM-\d{8}T\d{4}Z/).first()).toBeVisible({ timeout: 30_000 });
}

/** Section 7.2's shortcut table: the key, and the layer control it must toggle. */
const LAYER_KEYS = [
  { key: "p", label: /probability/i },
  { key: "d", label: /drain/i },
  { key: "s", label: /surcharge/i },
  { key: "g", label: /ground truth/i },
  { key: "r", label: /^routes/i },
  { key: "i", label: /^isochrones/i },
  { key: "3", label: /3d city/i },
  { key: "x", label: /drain x-ray/i },
] as const;

/**
 * Layer keys the overlay may still call "coming in pilot". None: R, I, 3 and W were wired in
 * the CONSOLE chunk (P6.15, P6.13), so a pilot note on any of them is the overlay describing a
 * console that no longer exists.
 */
const PILOT_KEYS = [] as const;

/** Browser noise that is not the app's fault and would make the gate lie. */
const IGNORED = [
  /favicon/i,
  /Download the React DevTools/i,
  /\[Fast Refresh\]/i,
  /WebGL.*deprecated/i,
  /Failed to load resource.*esri/i,
  // Emitted by the GPU driver, not by the app: headless Chromium's software GL reports a
  // performance stall whenever deck.gl reads pixels back for picking. Section 14 asks for zero
  // console errors *from the demo*, and no change to VARUNA can stop a driver talking about
  // its own scheduling. Scoped to this exact message so a real WebGL error still fails.
  /GL Driver Message/i,
];

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message: ConsoleMessage) => {
    if (message.type() !== "error" && message.type() !== "warning") return;
    const text = message.text();
    if (IGNORED.some((pattern) => pattern.test(text))) return;
    errors.push(`${message.type()}: ${text}`);
  });
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  return errors;
}

test.describe("P6.13 the keyboard path", () => {
  test("the ? overlay opens and lists every shortcut section 7.2 names", async ({ page }) => {
    await page.goto("/console");
    await waitForRun(page);
    await page.keyboard.press("?");

    const overlay = page.getByRole("dialog").filter({ hasText: /shortcut/i });
    await expect(overlay).toBeVisible();

    // Section 17: never a dead control, and never a live control described as dead. Every layer
    // key has a console handler now, so no row may still say it is coming in pilot.
    const pilotNotes = overlay.getByText(/coming in pilot/i);
    await expect(pilotNotes).toHaveCount(PILOT_KEYS.length);

    // Every key in the spec's table has a row. A shortcut that works but is undocumented is
    // invisible to the operator; one that is listed but does nothing is worse. Asserted on the
    // <kbd> elements rather than the dialog's text, because `textContent` runs a keycap straight
    // into the next label ("...loadedPDrains...") where a word-boundary match is meaningless.
    const printed = (await overlay.locator("kbd").allTextContents()).map((cap) =>
      cap.trim().toLowerCase(),
    );
    for (const { key } of LAYER_KEYS) {
      expect(printed, `the overlay must print the ${key.toUpperCase()} shortcut`).toContain(key);
    }
    expect(printed).toContain("space");

    await page.keyboard.press("Escape");
    await expect(overlay).not.toBeVisible();
  });

  // X is offered under the 3D city and switches 3D on with it, so its switch is not in the panel
  // until pressed; it has its own test below.
  for (const { key, label } of LAYER_KEYS.filter((k) => k.key !== "x")) {
    test(`"${key}" toggles its layer and the panel reports it`, async ({ page }) => {
      await page.goto("/console");
      await waitForRun(page);
      const control = page
        .getByRole("checkbox", { name: label })
        .or(page.getByRole("switch", { name: label }))
        .first();
      await expect(control).toBeAttached({ timeout: 20_000 });

      const before = await control.getAttribute("aria-checked");
      await page.keyboard.press(key);
      await expect
        .poll(async () => control.getAttribute("aria-checked"), { timeout: 10_000 })
        .not.toBe(before);

      // Pressing it again puts the map back, so the shortcut is a toggle rather than a latch.
      await page.keyboard.press(key);
      await expect.poll(async () => control.getAttribute("aria-checked")).toBe(before);
    });
  }

  test('"w" opens the what-if drawer over the rail and closes it again', async ({ page }) => {
    await page.goto("/console");
    await waitForRun(page);
    const drawer = page.getByRole("complementary", { name: "What-if" });
    await page.keyboard.press("w");
    await expect(drawer).toBeVisible({ timeout: 10_000 });
    await expect(
      drawer.getByText("Reduced-order emulator calibrated to VARUNA-Twin"),
    ).toBeVisible();
    await page.keyboard.press("w");
    await expect(drawer).not.toBeVisible();
  });

  /*
   * This test used to assert "Conditioned 30 m DEM, N x N cells" - the Terrarium heightmap 3D
   * mode was built on (ADR-0065). That ground is gone: 3D is now Google's photorealistic tileset
   * and the DEM sentence describes a feature that no longer exists, so the assertion is replaced
   * rather than relaxed.
   *
   * What it asserts instead is the thing that has to hold in *every* state: pressing 3 says
   * something about the photorealistic city, pressing it again stops saying it, and neither logs.
   * The tiles did draw here on 2026-09-23 - the row read "Google's photorealistic Mumbai, with
   * the water, the routes and the markers draped on it." and the merged Google credits appeared
   * under the map - but they come from Google over the network, on a key that is not in the
   * repository. A gate that required them would fail on any checkout without that key and on any
   * machine with the network off, which SPEC.md 17 says is the one the finale runs on.
   */
  test(
    '"3" says what the photorealistic city is doing, and leaving 3D logs nothing',
    { tag: "@needs-city" },
    async ({ page }) => {
      const errors = collectErrors(page);
      await page.goto("/console");
      await waitForRun(page);
      await page.keyboard.press("3");
      const row = page.getByRole("listitem").filter({ hasText: "3D city" });
      await expect(row).toContainText(/photorealistic/i, { timeout: 30_000 });
      await page.keyboard.press("3");
      await expect(row).not.toContainText(/Asking Google|Map Tiles API|draped on it/);
      // Leaving 3D once re-created the flat map under a dying ground effect: 286 errors.
      await page.waitForTimeout(2_000);
      expect(errors).toEqual([]);
    },
  );

  test(
    '"x" opens the drain X-ray and the row counts what it drew',
    { tag: "@needs-city" },
    async ({ page }) => {
      const errors = collectErrors(page);
      await page.goto("/console");
      await waitForRun(page);
      await page.keyboard.press("x");
      const row = page.getByRole("listitem").filter({ hasText: "Drain X-ray" });
      // Whichever state the served drain layer is in, the row says which: pipes drawn at their
      // invert depth, or the re-export that would give them one. Never silence.
      await expect(row).toContainText(/invert depth|make city CITY=|Loading the inferred/, {
        timeout: 60_000,
      });
      await page.keyboard.press("x");
      await page.waitForTimeout(1_000);
      expect(errors).toEqual([]);
    },
  );

  test("space plays and pauses the same clock the time bar shows", async ({ page }) => {
    await page.goto("/console");
    await waitForRun(page);
    const play = page.getByRole("button", { name: /^(play|pause) the replay$/i }).first();
    await expect(play).toBeAttached({ timeout: 20_000 });

    const before = await play.getAttribute("aria-label");
    await page.keyboard.press("Space");
    await expect
      .poll(async () => play.getAttribute("aria-label"), { timeout: 10_000 })
      .not.toBe(before);

    await page.keyboard.press("Space");
    await expect.poll(async () => play.getAttribute("aria-label")).toBe(before);
  });
});

test.describe("P6.13 states and console cleanliness", () => {
  // Counts console errors, and the map's road and asset layers 404 without a built city.
  test(
    "a full scrub across the window raises no console errors",
    { tag: "@needs-city" },
    async ({ page }) => {
      const errors = collectErrors(page);
      await page.goto("/console");
      await waitForRun(page);

      // Walk the whole -60 -> +180 window in 15-minute steps, which is what an operator does
      // and what the AC means by "a full replay": every step restyles the map from the run.
      await page.keyboard.press("Home");
      for (let step = 0; step < 16; step += 1) {
        await page.keyboard.press("ArrowRight");
        await page.waitForTimeout(120);
      }

      expect(errors, `console errors during a full scrub: ${JSON.stringify(errors)}`).toEqual([]);
    },
  );

  test("an unknown run shows an empty state that says what to do, not a blank map", async ({
    page,
  }) => {
    await page.goto("/console?run_id=MUM-19000101T0000Z-sky0.0-twin0.0-flash0.0-baked");

    // 6.8: "Empty states tell the user what to do." A blank panel is the failure mode.
    await expect(
      page.getByText(/no runs yet|press play|compute live|not found|no run/i).first(),
    ).toBeVisible({ timeout: 20_000 });
  });

  test("the mode banner names the mode and the run stamp names the run", async ({ page }) => {
    await page.goto("/console");
    await expect(page.getByText(/replay|live|baked|degraded/i).first()).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByText(/MUM-\d{8}T\d{4}Z/).first()).toBeVisible({ timeout: 20_000 });
  });
});

test.describe("the console's address bar", () => {
  /** The run id the stamp is showing, straight off the screen. */
  async function stampedRun(page: Page): Promise<string> {
    const stamp = page.getByText(/MUM-\d{8}T\d{4}Z/).first();
    await expect(stamp).toBeVisible({ timeout: 30_000 });
    return (await stamp.textContent()) ?? "";
  }

  /**
   * Chunk INTEGRATE, defect 2: the query string used to be read once, in a `useState`
   * initialiser, so a client-side navigation between two console URLs changed nothing at all.
   * Picking a cycle is that navigation - it is a `router.replace` now - and this asserts the
   * console follows it rather than only reading the URL it was mounted with.
   */
  test(
    "picking a cycle navigates, and the console follows the navigation",
    { tag: "@needs-city" },
    async ({ page }) => {
      await page.goto("/console");
      const opening = await stampedRun(page);
      expect(page.url()).not.toContain("run=");

      // Any cycle chip other than the one already showing.
      const chips = page.getByRole("button", { name: /^Forecast from / });
      await expect(chips.first()).toBeVisible({ timeout: 30_000 });
      const other = chips.filter({ hasNot: page.locator('[aria-current="true"]') });
      await other.last().click();

      await expect.poll(() => page.url(), { timeout: 30_000 }).toContain("run=");
      await expect.poll(() => stampedRun(page), { timeout: 60_000 }).not.toBe(opening);

      // And the run the URL names is the run on screen, not a second lookup's answer.
      const named = decodeURIComponent(new URL(page.url()).searchParams.get("run") ?? "");
      expect(named).not.toBe("");
      expect((await stampedRun(page)).replace(/\s/g, "")).toContain(named.slice(0, 20));

      // The discriminating half: a real `next/link` navigation back to `/console`, with no run.
      // The old initialiser read `window.location` once, so the console stayed pinned to the
      // cycle it had; read from the router, it goes back to the opening cycle the demo starts
      // from (SPEC.md 15).
      await page.getByRole("link", { name: "Drishti, command console", exact: true }).click();
      await expect.poll(() => page.url(), { timeout: 30_000 }).not.toContain("run=");
      await expect.poll(() => stampedRun(page), { timeout: 60_000 }).toBe(opening);
    },
  );
});

test.describe("P6.14 the viewports 6.11 fixes", () => {
  const VIEWPORTS = [
    { name: "1366 x 768 laptop", width: 1366, height: 768 },
    { name: "1440 x 900 reference", width: 1440, height: 900 },
    // A 4K wall at 150 % zoom presents as 2560 x 1440 CSS pixels.
    { name: "4K wall at 150 %", width: 2560, height: 1440 },
  ];

  for (const viewport of VIEWPORTS) {
    test(`/console fits ${viewport.name} without a horizontal scrollbar`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto("/console");
      await expect(page.getByText(/varuna/i).first()).toBeVisible({ timeout: 20_000 });

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, "the console must never scroll horizontally (6.5)").toBeLessThanOrEqual(1);

      // The three fixed regions of the 6.5 shell have to survive the resize, not just the body.
      await expect(page.locator("canvas").first()).toBeVisible();
    });
  }

  test("the public map fits a 390 x 844 phone", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/map");
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, "7.11 fixes a 390 px layout with no horizontal scroll").toBeLessThanOrEqual(1);
  });
});

import { mkdirSync } from "node:fs";
import path from "node:path";

import { expect, test, type ConsoleMessage, type Page } from "./requirements";

/**
 * The per-screen design QA of SPEC.md 6.11, run rather than asserted (task P10.2).
 *
 * 6.11 is a checklist that ends every screen's build, and until this file it had never been run
 * screen by screen: the phase 10 row said so in words ("the per-screen design QA and screenshots"
 * outstanding) while thirteen screens had shipped. This turns the checkable half of that list into
 * a test and writes `docs/screens/<route>.png` for the half a person has to look at.
 *
 * **What is checked here, and why only this much.** 6.11 has eight lines. Four of them are
 * machine-checkable and are the assertions below:
 *
 *   - *Uses only tokens* - `node tools/lint-design.mjs` already owns this over the source, and it
 *     is a separate command rather than a browser assertion. Not duplicated here; the task's own
 *     verify step runs it.
 *   - *Works at 1366 x 768, 1440 x 900, 4K at 150 %, and (public map) 390 x 844* - measured as the
 *     absence of a horizontal scrollbar, which is how 6.5 words it, at all three desktop sizes.
 *     P6.14 did this for `/console` and `/map` only; all seventeen routes of 3.4 are checked here.
 *   - *Keyboard path works; focus visible* - one Tab from a fresh load has to land somewhere that
 *     is not `<body>` and has to draw the 2 px ring `globals.css` gives `:focus-visible`. Reduced
 *     motion has its own five tests in `motion.spec.ts` and is not repeated.
 *   - *No placeholder text* (6.8: "No lorem ipsum, ever") - scanned in the rendered text, not the
 *     source, because a placeholder that arrives from an API is still on the screen.
 *
 * Plus section 14's "zero console errors", which the demo test asserts on four screens and nothing
 * asserted on the other thirteen.
 *
 * The other four lines of 6.11 are judgements a browser cannot make - "one memorable element and
 * nothing competes with it", "all states implemented", "copy follows 6.8", and the comparison of
 * this screenshot with the previous one. `HUMAN_EYE` below names them, the run prints them per
 * screen, and they stay a person's job. Faking them as assertions would make the checklist read
 * green while nothing had been looked at, which is the failure mode this file exists to end.
 *
 * **Screenshots are the artifact, not the assertion.** A byte comparison against the committed PNG
 * would fail on a font hinting difference and on every legitimate redesign, so nothing compares
 * them; they are written for the human half of the list and for the diff a reviewer reads.
 */

const NAV = 90_000;
const SETTLE = 45_000;
const SCREENS_DIR = path.resolve(__dirname, "../../docs/screens");

/** Strings that must never reach a rendered screen (6.8). */
const PLACEHOLDERS = [/lorem ipsum/i, /\bTODO\b/, /\bFIXME\b/, /\bXXX\b/, /placeholder text/i];

/**
 * The four lines of 6.11 that need a person. Printed per screen by the run so the checklist is
 * visibly half-done rather than invisibly so.
 */
const HUMAN_EYE = [
  "one memorable element; nothing competes with it",
  "all states implemented: loading skeleton, empty, error, degraded",
  "copy follows 6.8; no placeholder text beyond the strings scanned here",
  "screenshot compared with the previous one",
] as const;

interface Screen {
  /** The route, exactly as SPEC.md 3.4 lists it. */
  path: string;
  /** `docs/screens/<slug>.png`. */
  slug: string;
  /** 7.11 fixes the public map and the report flow at a phone size; everything else is 6.5's. */
  viewport?: { width: number; height: number };
  /** Data a clean clone does not have, per `requirements.ts`. */
  tag?: "@needs-city";
  /** Text that proves the screen's own content arrived, not just its shell. */
  ready?: RegExp;
  /**
   * A third-party script whose errors this screen cannot be held to (section 14), with why.
   *
   * Errors are attributed by source where the browser gives one - the console message's URL or the
   * exception's stack - and otherwise by a message prefix only that vendor emits. Errors from
   * VARUNA's own code still fail the screen, and how many third-party ones arrived is annotated on
   * the run, so the exclusion is visible rather than silent.
   *
   * An earlier version asserted the known error was *still happening*, `xfail(strict=True)` style,
   * so that fixing the cause would fail the test and say to delete the row. Measured over three
   * consecutive runs on 2026-09-24: Google's loader errored on two and not on the third, so that
   * assertion was flaky on its own and was dropped. What keeps this from rotting is the annotation
   * and `.wf/DESIGNQA-requests.md`, not a test.
   */
  thirdParty?: {
    /** Matched against the error's source: a console message's URL, or an exception's stack. */
    origin: RegExp;
    /**
     * Matched against the message itself, for what the source cannot reach.
     *
     * Next re-logs a third-party `console.error` from its own chunk, so `location().url` reads
     * `/_next/static/chunks/...` and the source rule never fires - measured 2026-09-24. This must
     * therefore be a prefix only that vendor emits, never a generic message shape: a rule like
     * `/Cannot read properties of undefined/` would excuse the next real defect of that shape.
     */
    message: RegExp;
    why: string;
  };
}

/**
 * Every route in SPEC.md 3.4, in the order it lists them.
 *
 * `/console?tab=reachability` is listed there as a screen but it is the console with a tab
 * selected, so it is not a separate page to photograph; the console entry covers the shell and
 * `console.spec.ts` covers the tab.
 */
const SCREENS: readonly Screen[] = [
  { path: "/", slug: "landing", ready: /Every street/i },
  { path: "/console", slug: "console", tag: "@needs-city", ready: /Hotspots/i },
  { path: "/drains", slug: "drains", tag: "@needs-city", ready: /blockage|drain/i },
  { path: "/route", slug: "route", tag: "@needs-city", ready: /Find route/i },
  { path: "/alerts", slug: "alerts", ready: /alert/i },
  { path: "/pumps", slug: "pumps", ready: /pump/i },
  { path: "/whatif", slug: "whatif", ready: /what-if/i },
  { path: "/replay", slug: "replay", ready: /replay|bundle/i },
  { path: "/onboard", slug: "onboard", ready: /Chennai|Choose area/i },
  { path: "/verify", slug: "verify", tag: "@needs-city", ready: /CSI|verification/i },
  {
    path: "/map",
    slug: "map-390",
    viewport: { width: 390, height: 844 },
    tag: "@needs-city",
    ready: /passable|vehicle|street/i,
  },
  {
    path: "/report",
    slug: "report-390",
    viewport: { width: 390, height: 844 },
    ready: /location|report/i,
  },
  { path: "/api", slug: "api", ready: /v1\// },
  { path: "/design", slug: "design", ready: /token/i },
  {
    path: "/dashboard",
    slug: "dashboard",
    tag: "@needs-city",
    ready: /Mumbai|street/i,
    // Measured 2026-09-24 on this tree, with a 39-character key present in
    // `apps/command/.env.local`: Google's loader answers `InvalidKeyMapError`, so the Maps
    // JavaScript API is not enabled for that key's project or the key is restricted away from it.
    // The status board's D-25/D-26 row reads as though the key question is closed; it is closed
    // for Map Tiles, Static Maps, Geocoding and Directions, and not for Maps JS. Two different
    // errors were seen over four runs - the `InvalidKeyMapError` message, and an uncaught
    // "Cannot read properties of undefined (reading 'firstChild')" thrown inside Google's own
    // bundle as the screen takes its surface away - and neither is ours to raise or to swallow.
    // The screen itself behaves: ADR-0059's fallback draws VARUNA's own Esri-and-deck map with
    // identical water and routes, and says so on screen. Delete this row once a key that works
    // for Maps JS is configured, or once the Google basemap is dropped.
    thirdParty: {
      origin: /maps\.googleapis\.com|maps\.gstatic\.com/,
      message: /^Google Maps JavaScript API (error|warning):/,
      why:
        "Google Maps JS rejects the configured key, so its loader logs and throws; the screen " +
        "falls back to VARUNA's own map per ADR-0059 and cannot suppress a third party's errors",
    },
  },
  { path: "/authority", slug: "authority", tag: "@needs-city", ready: /closure|pump|ward/i },
  { path: "/rural", slug: "rural", ready: /advisory|water|rain/i },
];

/** The three desktop sizes 6.11 names. A phone screen is checked at its own size instead. */
const DESKTOP_VIEWPORTS = [
  { name: "1366 x 768", width: 1366, height: 768 },
  { name: "1440 x 900", width: 1440, height: 900 },
  // A 4K wall at 150 % zoom presents to CSS as 2560 x 1440.
  { name: "2560 x 1440", width: 2560, height: 1440 },
] as const;

/** A console error or uncaught exception, with whatever the browser says about where it came from. */
interface PageError {
  text: string;
  /** The script it came from: the console message's own URL, or the exception's stack. */
  source: string;
}

function collectConsoleErrors(page: Page): PageError[] {
  const errors: PageError[] = [];
  page.on("console", (message: ConsoleMessage) => {
    if (message.type() !== "error") return;
    errors.push({ text: message.text(), source: message.location().url ?? "" });
  });
  page.on("pageerror", (error) => {
    errors.push({ text: `pageerror: ${error.message}`, source: error.stack ?? "" });
  });
  return errors;
}

/**
 * Lets the two globe entries (M27) know they have already played.
 *
 * `/dashboard` and `/authority` open on four seconds of vector Earth once per session. It is the
 * right thing for a reader and the wrong thing for a screenshot of the screen, so the session flag
 * the component reads is set before the page's own scripts run. Nothing here disables the entry:
 * `motion.spec.ts` is where the sequence itself is tested.
 */
async function skipGlobeEntries(page: Page) {
  await page.addInitScript(() => {
    try {
      window.sessionStorage.setItem("varuna.dashboard-intro.played", "1");
      window.sessionStorage.setItem("varuna.authority-intro.played", "1");
    } catch {
      // A browser with storage blocked plays the entry; the screenshot then shows the globe,
      // which is a worse picture but not a failure.
    }
  });
}

/**
 * Waits until nothing on the screen still says it is loading, and reports how long that took.
 *
 * The first run of this file photographed `/dashboard` and `/authority` mid-load - an empty map
 * under "Loading the last VARUNA run", with the trip panel saying the city's places had not
 * arrived - because their `ready` text is in the chrome and matches before any data does. A
 * screenshot of a skeleton is not the artifact 6.11 asks for.
 *
 * A screen that is *still* loading when the budget runs out is a finding, not a test failure to
 * be waited away, so this returns the wait and the caller records it rather than throwing.
 */
async function waitUntilLoaded(page: Page): Promise<{ ms: number; settled: boolean }> {
  const loading = page.getByText(/loading/i);
  const started = Date.now();
  try {
    await expect(loading).toHaveCount(0, { timeout: SETTLE });
    return { ms: Date.now() - started, settled: true };
  } catch {
    return { ms: Date.now() - started, settled: false };
  }
}

async function open(page: Page, screen: Screen): Promise<{ ms: number; settled: boolean }> {
  if (screen.viewport) await page.setViewportSize(screen.viewport);
  await skipGlobeEntries(page);
  await page.goto(screen.path, { waitUntil: "domcontentloaded", timeout: NAV });
  if (screen.ready) {
    await expect(page.getByText(screen.ready).first()).toBeVisible({ timeout: SETTLE });
  }
  const load = await waitUntilLoaded(page);
  // Basemap tiles are requests like any other, and `/onboard` was photographed with imagery over
  // half its canvas and bare vectors over the rest. Bounded and swallowed: the console holds a
  // WebSocket open and never goes idle, and that is not a reason to fail its QA.
  await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => undefined);
  // Panels arrive after their fetches, and a screenshot taken before they do photographs the
  // skeletons - which are a state worth checking but not the one this file is for.
  await page.waitForTimeout(3_000);
  return load;
}

test.describe("P10.2 design QA per screen (SPEC.md 6.11)", () => {
  test.slow();

  test.beforeAll(() => {
    mkdirSync(SCREENS_DIR, { recursive: true });
  });

  for (const screen of SCREENS) {
    test(
      `${screen.path} photographs clean, focuses visibly and says nothing placeholder`,
      { tag: screen.tag ? [screen.tag] : [] },
      async ({ page }, testInfo) => {
        const errors = collectConsoleErrors(page);
        const load = await open(page, screen);

        // 1. The artifact 6.11 asks for. From the top of the page: `/design` photographed itself
        // scrolled to its components section, which is not the screen a reviewer is comparing.
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.waitForTimeout(300);
        await page.screenshot({
          path: path.join(SCREENS_DIR, `${screen.slug}.png`),
          fullPage: false,
        });

        // 2. No placeholder text anywhere in the rendered body (6.8).
        const text = (await page.locator("body").innerText()).replace(/\s+/g, " ");
        const found = PLACEHOLDERS.filter((pattern) => pattern.test(text)).map(String);
        expect(found, `placeholder copy on ${screen.path}: ${found.join(", ")}`).toEqual([]);

        // 3. One Tab has to reach something, and that something has to draw the ring (6.10).
        await page.keyboard.press("Tab");
        const focus = await page.evaluate(() => {
          const el = document.activeElement as HTMLElement | null;
          if (!el || el === document.body) return { tag: null as string | null, ring: false };
          const style = getComputedStyle(el);
          const width = Number.parseFloat(style.outlineWidth || "0");
          const ring =
            (width > 0 && style.outlineStyle !== "none") || style.boxShadow.includes("rgb");
          return {
            tag: `${el.tagName.toLowerCase()}${el.getAttribute("aria-label") ? `[${el.getAttribute("aria-label")}]` : ""}`,
            ring,
          };
        });
        expect(focus.tag, `Tab reached nothing focusable on ${screen.path}`).not.toBeNull();
        expect(
          focus.ring,
          `no visible focus ring on ${screen.path}'s first focusable element (${focus.tag})`,
        ).toBe(true);

        // 4. Section 14: zero console errors from VARUNA's own code. A screen that declares a
        // third-party script has that script's errors attributed to it instead of asserted away.
        const vendor = screen.thirdParty;
        const fromVendor = vendor
          ? errors.filter((e) => vendor.origin.test(e.source) || vendor.message.test(e.text))
          : [];
        const ours = errors.filter((e) => !fromVendor.includes(e));
        expect(
          ours.map((e) => e.text),
          `console errors on ${screen.path}:\n${ours.map((e) => `${e.text}  <- ${e.source}`).join("\n")}`,
        ).toEqual([]);
        if (vendor) {
          testInfo.annotations.push({
            type: "third-party console errors (section 14 missed here)",
            description: `${screen.path}: ${fromVendor.length} this run. ${vendor.why}`,
          });
        }

        testInfo.annotations.push({
          type: "needs a human eye (6.11)",
          description: `${screen.path}: ${HUMAN_EYE.join(" | ")}`,
        });

        // Soft, so the screenshot and every assertion above still count: a screen that says
        // "loading" for longer than the settle budget is a measurement to report, not a reason
        // to lose the rest of this screen's QA.
        expect
          .soft(
            load.settled,
            `${screen.path} still said "loading" after ${(load.ms / 1000).toFixed(1)} s, so its ` +
              `screenshot is of a skeleton`,
          )
          .toBe(true);
      },
    );
  }
});

test.describe("P10.2 the viewports 6.11 names", () => {
  test.slow();

  for (const screen of SCREENS) {
    const sizes = screen.viewport ? [{ name: "390 x 844", ...screen.viewport }] : DESKTOP_VIEWPORTS;

    test(
      `${screen.path} never scrolls horizontally`,
      { tag: screen.tag ? [screen.tag] : [] },
      async ({ page }) => {
        await skipGlobeEntries(page);
        const overflow: Record<string, number> = {};
        for (const size of sizes) {
          await page.setViewportSize({ width: size.width, height: size.height });
          await page.goto(screen.path, { waitUntil: "domcontentloaded", timeout: NAV });
          if (screen.ready) {
            await expect(page.getByText(screen.ready).first()).toBeVisible({ timeout: SETTLE });
          }
          await page.waitForTimeout(1_500);
          overflow[size.name] = await page.evaluate(
            () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
          );
        }
        const over = Object.entries(overflow).filter(([, px]) => px > 1);
        expect(
          over,
          `${screen.path} scrolls horizontally: ${over.map(([n, px]) => `${n} by ${px} px`).join(", ")}`,
        ).toEqual([]);
      },
    );
  }
});

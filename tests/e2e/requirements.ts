import { existsSync } from "node:fs";
import path from "node:path";

import { test as base } from "@playwright/test";

/**
 * What an end-to-end test needs that a clean clone does not have (ADR-0043).
 *
 * `make e2e` on the demo laptop runs against a built city. A clean clone - which is what the CI
 * job is - has none: `city/mumbai/` is gitignored and written by `make city`, and the only runs
 * are the committed `demo/runs` the API seeds at start-up. There, a test that needs the city
 * does not fail on anything VARUNA did. It fails on a 404 whose message names the command to run,
 * or it waits out the whole test timeout on a "Find route" button that cannot enable, which is how
 * the CI job sat for forty minutes the first time it ran.
 *
 * So a test declares the data it needs with a tag, and this fixture skips it - giving the command
 * that provides the data as the reason - when that data is not on disk.
 *
 * **The check reads the files the API reads, never the API.** A city that is on disk but that the
 * API fails to serve is a regression and has to fail. Asking the server whether it can serve the
 * city would turn exactly that failure into a skip.
 *
 * Paths mirror `varuna_schemas.paths`, environment overrides included, resolved from the clone the
 * config lives in. A server reused from another clone (the config's `reuseExistingServer`) is
 * outside what this can see.
 */

interface Requirement {
  /** The tag a test carries to declare the need. */
  tag: `@${string}`;
  /** True when the data is on disk, given the repository root. */
  present: (repo: string) => boolean;
  /** Why the test is not run: what is missing, and the command that provides it. */
  reason: string;
}

function envPath(name: string): string | null {
  const value = process.env[name]?.trim();
  return value ? path.resolve(value) : null;
}

export const REQUIREMENTS: readonly Requirement[] = [
  {
    tag: "@needs-city",
    // The road and asset layers the console and the route planner draw, and the road graph that
    // /v1/route/facilities and /v1/verification read. `make city` writes all three together.
    present: (repo) => {
      const city = path.join(envPath("VARUNA_CITY_DIR") ?? path.join(repo, "city"), "mumbai");
      return [
        path.join(city, "map", "segments.geojson"),
        path.join(city, "map", "assets.geojson"),
        path.join(city, "segments.parquet"),
      ].every((file) => existsSync(file));
    },
    reason:
      "needs the built Mumbai city, which a clean clone does not have (city/ is gitignored): " +
      "run `make city CITY=mumbai`",
  },
];

/** `@playwright/test`'s `test`, with every tagged requirement checked before the test body runs. */
export const test = base.extend<{ requirements: void }>({
  requirements: [
    // Playwright reads a fixture's dependencies from its first parameter, so it has to be an
    // object pattern even when the fixture depends on nothing.
    // eslint-disable-next-line no-empty-pattern
    async ({}, use, testInfo) => {
      const repo =
        envPath("VARUNA_REPO_ROOT") ??
        path.dirname(
          testInfo.config.configFile ?? path.join(process.cwd(), "playwright.config.ts"),
        );
      for (const need of REQUIREMENTS) {
        testInfo.skip(testInfo.tags.includes(need.tag) && !need.present(repo), need.reason);
      }
      await use();
    },
    { auto: true },
  ],
});

export { expect } from "@playwright/test";
export type { ConsoleMessage, Page } from "@playwright/test";

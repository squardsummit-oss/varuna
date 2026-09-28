/**
 * The offline public map (task P9.10): the scopes that keep the worker off the console, the page
 * and worker agreeing on names, the copy that says how old a saved forecast is, and the basemap
 * drawing in tokens.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import {
  BUILDING_FILL,
  ROAD_LINE,
  WATERWAY_LINE,
  WATER_FILL,
  basemapFillColor,
  basemapLineColor,
  basemapLineWidth,
  basemapPath,
  offlineBasemapLayers,
} from "./basemap";
import {
  CACHE_PREFIX,
  FORECAST_META_PATH,
  OFFLINE_DB,
  OFFLINE_SCOPES,
  OFFLINE_STORE,
  offlineScopeOf,
  offlineVersion,
  swUrl,
} from "./constants";
import { formatAge, offlineLine, queueLine } from "./forecast-status";
import { registerOfflineWorker, warmList } from "./register";

const WORKER = readFileSync(path.resolve(__dirname, "../../public/sw.js"), "utf8");
const TOKENS = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../../packages/tokens/tokens.json"), "utf8"),
) as { color: { base: Record<string, { value: string }> } };

function rgb(token: string): number[] {
  const hex = TOKENS.color.base[token]!.value.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
}

describe("offline scopes", () => {
  it("controls the public map and the report flow and nothing else", () => {
    expect(offlineScopeOf("/map")).toBe("/map");
    expect(offlineScopeOf("/report")).toBe("/report");
    for (const page of ["/", "/console", "/dashboard", "/authority", "/rural", "/drains"]) {
      expect(offlineScopeOf(page)).toBeNull();
    }
  });

  it("never registers from a page it does not serve", async () => {
    const register = vi.fn();
    const container = { register } as unknown as ServiceWorkerContainer;
    const done = await registerOfflineWorker({
      version: "v1",
      pathname: "/console",
      city: "mumbai",
      container,
    });
    expect(done).toBe(false);
    expect(register).not.toHaveBeenCalled();
  });

  it("registers both scopes from the map, never the root, and warms its own worker", async () => {
    const postMessage = vi.fn();
    const active = { postMessage } as unknown as ServiceWorker;
    const register = vi.fn(async () => ({ active }) as unknown as ServiceWorkerRegistration);
    const container = { register } as unknown as ServiceWorkerContainer;
    vi.useFakeTimers();
    try {
      await registerOfflineWorker({
        version: "abc123",
        pathname: "/map",
        city: "mumbai",
        container,
        loaded: () => ["http://localhost:3000/_next/static/chunks/app.js"],
      });
      const scopes = register.mock.calls.map((call) => (call as unknown[])[1]);
      expect(scopes).toEqual([
        { scope: "/map", updateViaCache: "none" },
        { scope: "/report", updateViaCache: "none" },
      ]);
      expect(
        register.mock.calls.every((call) => (call as unknown[])[0] === "/sw.js?v=abc123"),
      ).toBe(true);
      expect(postMessage).toHaveBeenCalledTimes(1);
      vi.advanceTimersByTime(15_000);
      expect(postMessage).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("asks the worker to keep the basemap, both pages and what the page loaded", () => {
    const urls = warmList("mumbai", ["/_next/static/css/a.css", "blob:nothing"]);
    expect(urls).toContain("/map");
    expect(urls).toContain("/report");
    expect(urls.some((u) => u.endsWith(basemapPath("mumbai")))).toBe(true);
    expect(urls).toContain("/_next/static/css/a.css");
    expect(urls).not.toContain("blob:nothing");
  });
});

describe("page and worker agree", () => {
  it("on the scopes, the cache prefix, the queue and the meta path", () => {
    for (const scope of OFFLINE_SCOPES) expect(WORKER).toContain(`"${scope}"`);
    expect(WORKER).toContain(`const PREFIX = "${CACHE_PREFIX}"`);
    expect(WORKER).toContain(`const DB_NAME = "${OFFLINE_DB}"`);
    expect(WORKER).toContain(`const DB_STORE = "${OFFLINE_STORE}"`);
    expect(WORKER).toContain(`const META_PATH = "${FORECAST_META_PATH}"`);
  });

  it("never lets the worker near Esri's imagery, whose licence forbids an offline copy", () => {
    expect(WORKER).toMatch(/const NEVER = .*arcgisonline/);
    expect(WORKER).not.toMatch(/scope:\s*"\/"/);
  });

  it("answers an offline report with 202 and queued", () => {
    expect(WORKER).toContain("status: 202");
    expect(WORKER).toContain("queued: true");
  });
});

describe("cache version", () => {
  it("prefers the build id, then the Vercel commit, then dev", () => {
    expect(offlineVersion({ NEXT_PUBLIC_VARUNA_BUILD_ID: "b-7" })).toBe("b-7");
    expect(offlineVersion({ NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA: "0123abc" })).toBe("0123abc");
    expect(offlineVersion({})).toBe("dev");
    expect(offlineVersion({ NEXT_PUBLIC_VARUNA_BUILD_ID: "a/b?c" })).toBe("abc");
  });

  it("puts the version in the worker's address, so a deploy installs a new worker", () => {
    expect(swUrl("0123abc")).toBe("/sw.js?v=0123abc");
  });
});

describe("how old the saved forecast is", () => {
  const now = new Date("2026-09-22T10:42:00Z");

  it("says ages the way a person would", () => {
    expect(formatAge("2026-09-22T10:41:40Z", now)).toBe("under a minute");
    expect(formatAge("2026-09-22T10:30:00Z", now)).toBe("12 min");
    expect(formatAge("2026-09-22T07:37:00Z", now)).toBe("3 h 5 min");
    expect(formatAge("2026-09-22T08:42:00Z", now)).toBe("2 h");
    expect(formatAge("2026-09-19T10:42:00Z", now)).toBe("3 days");
  });

  it("says why, when it was saved, how long ago and which run", () => {
    const saved = {
      runId: "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked",
      cycleTs: "2019-07-02T06:40:00+05:30",
      savedAt: "2026-09-22T10:30:00Z",
    };
    expect(offlineLine("online", saved, now)).toBeNull();
    expect(offlineLine("offline", saved, now)).toBe(
      "You are offline. Showing the forecast saved at 16:00 (12 min ago), from the run at 06:40.",
    );
    expect(offlineLine("unreachable", saved, now)).toMatch(
      /^The forecast service is not answering\. Showing the forecast saved at 16:00/,
    );
    expect(offlineLine("offline", null, now)).toBe(
      "You are offline. This phone has no saved forecast yet.",
    );
  });

  it("says what is waiting to send, then what went", () => {
    expect(queueLine(0, 0)).toBeNull();
    expect(queueLine(1, 0)).toBe(
      "1 report is saved on this phone and will be sent when the connection returns.",
    );
    expect(queueLine(2, 0)).toMatch(/^2 reports are saved/);
    expect(queueLine(0, 1)).toBe("Your saved report was sent.");
  });
});

describe("offline basemap", () => {
  it("draws in tokens and never in the depth ramp", () => {
    expect(WATER_FILL.slice(0, 3)).toEqual(rgb("well"));
    expect(ROAD_LINE.slice(0, 3)).toEqual(rgb("line"));
    expect(WATERWAY_LINE.slice(0, 3)).toEqual(rgb("line-strong"));
    expect(BUILDING_FILL.slice(0, 3)).toEqual(rgb("deep"));
  });

  it("styles each archive layer by its name", () => {
    expect(basemapFillColor({ properties: { layerName: "water" } })).toEqual(WATER_FILL);
    expect(basemapFillColor({ properties: { layerName: "buildings" } })).toEqual(BUILDING_FILL);
    expect(basemapLineColor({ properties: { layerName: "roads" } })).toEqual(ROAD_LINE);
    expect(basemapLineColor({ properties: { layerName: "water" } })[3]).toBe(0);
    expect(basemapLineWidth({ properties: { layerName: "roads", class: "primary" } })).toBe(2.5);
    expect(basemapLineWidth({ properties: { layerName: "roads", class: "service" } })).toBe(0.8);
  });

  it("draws nothing until an archive is open", () => {
    expect(offlineBasemapLayers(null)).toEqual([]);
  });
});

/**
 * Contract tests for the live outlook client.
 *
 * The fixture (`outlook.fixture.ts`) is a body `services/api/varuna_api/routers/outlook.py`
 * produced on the shipped emulator, so a rename on the API side fails here rather than on the
 * console.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  loadOutlook,
  OutlookSchema,
  outlookDepthAt,
  outlookSourceLine,
  outlookWindowLabel,
  type Outlook,
} from "@/lib/api/outlook";
import { outlookBody } from "@/lib/api/outlook.fixture";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("OutlookSchema", () => {
  it("accepts the body the API writes", () => {
    const parsed = OutlookSchema.safeParse(outlookBody());
    expect(parsed.success).toBe(true);
  });

  it("refuses a body that is not an outlook, so a replay run can never be read as one", () => {
    expect(OutlookSchema.safeParse(outlookBody({ mode: "baked" })).success).toBe(false);
  });
});

describe("loadOutlook", () => {
  it("returns the outlook and asks for the city it was given", async () => {
    const asked: string[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      asked.push(String(input));
      return jsonResponse(outlookBody());
    });

    const state = await loadOutlook("mumbai");

    expect(state.kind).toBe("ready");
    if (state.kind !== "ready") throw new Error("expected an outlook");
    expect(state.outlook.summary.n_ge_5).toBe(393);
    expect(asked[0]).toContain("/v1/outlook?city=mumbai");
  });

  it("keeps Chennai's 422 apart from a failure, with the API's sentence", async () => {
    const message =
      "There is no live outlook for Chennai: Flash-lite, the emulator it runs on, is fitted to Mumbai's streets only.";
    vi.stubGlobal("fetch", async () =>
      jsonResponse({ error: { code: "no_emulator_for_city", message } }, 422),
    );

    expect(await loadOutlook("chennai")).toEqual({ kind: "refused", reason: message });
  });

  it("turns a 503 into unavailable carrying the reason, and no depth", async () => {
    const message = "The live outlook runs on Open-Meteo's rain and there is none to run.";
    vi.stubGlobal("fetch", async () =>
      jsonResponse({ error: { code: "weather_unavailable", message } }, 503),
    );

    expect(await loadOutlook()).toEqual({ kind: "unavailable", reason: message });
  });

  it("takes a bare AbortSignal as well as an options object, and says a cancel is a cancel", async () => {
    vi.stubGlobal("fetch", async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.signal?.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
      return jsonResponse(outlookBody());
    });

    const controller = new AbortController();
    expect((await loadOutlook("mumbai", controller.signal)).kind).toBe("ready");
    controller.abort();
    expect(await loadOutlook("mumbai", controller.signal)).toEqual({
      kind: "unavailable",
      reason: "The outlook request was cancelled.",
    });
  });

  it("turns a dead network into unavailable rather than throwing", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("fetch failed");
    });

    const state = await loadOutlook();
    expect(state.kind).toBe("unavailable");
  });
});

describe("helpers", () => {
  it("reads the median depth per listed street at a step, clamped to the horizon", () => {
    const outlook = OutlookSchema.parse(outlookBody()) as Outlook;
    const last = outlookDepthAt(outlook, 99);
    expect(last.get("S1267122772-000")).toBe(32.3);
    expect(last.get("S1509385069-002")).toBe(23.1);
    expect(last.size).toBe(2);
    expect(outlookDepthAt(outlook, 0).get("S1267122772-000")).toBe(0);
  });

  it("prints the source line from what the API sent, as a sentence", () => {
    const outlook = OutlookSchema.parse(outlookBody()) as Outlook;
    expect(outlookSourceLine(outlook)).toBe(
      "Open-Meteo hourly rain, fetched 4 min ago (CC BY 4.0)",
    );
  });

  it("counts the age from fetched_at when given a clock, so an open card stays honest", () => {
    const outlook = OutlookSchema.parse(outlookBody()) as Outlook;
    const later = Date.parse("2026-09-26T16:20:00+05:30");
    expect(outlookSourceLine(outlook, later)).toBe(
      "Open-Meteo hourly rain, fetched 2 h ago (CC BY 4.0)",
    );
  });

  it("names the outlook's own window on today's clock", () => {
    const outlook = OutlookSchema.parse(outlookBody()) as Outlook;
    // ICU spells September "Sep" or "Sept" depending on its version.
    expect(outlookWindowLabel(outlook)).toMatch(/^14:10 to 17:10 IST, 26 Sept? 2026$/);
  });
});

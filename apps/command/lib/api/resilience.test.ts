import { gzipSync } from "node:zlib";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createResilientFetch, mirrorKey, type ServerState } from "./resilience";

const API = "https://api.varuna.example";
const RUN = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";
const HOTSPOTS = `${API}/v1/nowcast/hotspots?run_id=${RUN}&limit=10`;
const LAYER = `${API}/v1/city/mumbai/layers/segments`;
const LAYER_BODY = JSON.stringify({ type: "FeatureCollection", features: [] });

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

interface Harness {
  fetch: typeof fetch;
  calls: string[];
  states: ServerState[];
  stored: { count: number };
}

/**
 * A fake world: the site serves the mirror; `api` decides what the API does with each request.
 * `sleep` resolves at once and `now` moves ten seconds a call, so a wake runs to its end in a
 * few microtasks.
 */
function harness(api: (url: string, init?: RequestInit) => Promise<Response>): Harness {
  const calls: string[] = [];
  const states: ServerState[] = [];
  const stored = { count: 0 };
  let clock = 0;
  const original = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    calls.push(url);
    if (url === "/mirror/index.json") {
      return json({
        version: 1,
        entries: {
          [mirrorKey(new URL(HOTSPOTS))]: { file: "h1.json", type: "application/json" },
          [mirrorKey(new URL(LAYER))]: { file: "l1.gz", type: "application/geo+json", encoding: "gzip" },
        },
      });
    }
    if (url === "/mirror/h1.json") return json({ stored: true, hotspots: [] });
    if (url === "/mirror/l1.gz") return new Response(gzipSync(Buffer.from(LAYER_BODY)));
    return api(url, init);
  }) as unknown as typeof fetch;
  const wrapped = createResilientFetch({
    apiBase: API,
    original,
    now: () => (clock += 10_000),
    sleep: async (_ms, signal) => {
      if (signal?.aborted) throw new DOMException("aborted", "AbortError");
      await Promise.resolve();
    },
    store: {
      setState: (state) => states.push(state),
      markMirrorServed: () => {
        stored.count += 1;
      },
    },
  });
  return { fetch: wrapped, calls, states, stored };
}

const refused = () => Promise.reject(new TypeError("Failed to fetch"));

beforeEach(() => {
  // The reprobe and probe timeouts use real timers; fake ones keep them from outliving a test.
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("mirrorKey", () => {
  it("sorts the query so a request matches its recording in any order", () => {
    const a = mirrorKey(new URL(`${API}/v1/nowcast/raster?t=3&stat=p50&run_id=${RUN}`));
    const b = mirrorKey(new URL(`${API}/v1/nowcast/raster?run_id=${RUN}&stat=p50&t=3`));
    expect(a).toBe(b);
    expect(a).toBe(`/v1/nowcast/raster?run_id=${RUN}&stat=p50&t=3`);
  });

  it("keeps a bare path bare and encodes values as URLSearchParams does", () => {
    expect(mirrorKey(new URL(`${API}/v1/runs`))).toBe("/v1/runs");
    expect(mirrorKey(new URL(`${API}/v1/reachability?t=2019-07-02T08:40:00%2B05:30`))).toBe(
      "/v1/reachability?t=2019-07-02T08%3A40%3A00%2B05%3A30",
    );
  });
});

describe("createResilientFetch", () => {
  it("leaves requests to anywhere but the API alone", async () => {
    const h = harness(async () => json({ never: true }));
    const response = await h.fetch("https://tiles.example/0/0/0.png");
    await expect(response.json()).resolves.toEqual({ never: true });
    expect(h.states).toEqual([]);
  });

  it("returns whatever the API answered, errors included", async () => {
    const h = harness(async () => json({ error: { code: "run_not_found", message: "No run" } }, 404));
    const response = await h.fetch(HOTSPOTS);
    expect(response.status).toBe(404);
    expect(response.headers.get("x-varuna-mirror")).toBeNull();
    expect(h.stored.count).toBe(0);
  });

  it("answers a GET from the mirror when the server refuses, and starts waking it", async () => {
    const h = harness(refused);
    const response = await h.fetch(HOTSPOTS);
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/json");
    expect(response.headers.get("x-varuna-mirror")).toBe("1");
    await expect(response.json()).resolves.toEqual({ stored: true, hotspots: [] });
    expect(h.stored.count).toBe(1);
    expect(h.states[0]).toBe("waking");
  });

  it("decompresses a stored file and hands it over with the API's own content type", async () => {
    const h = harness(refused);
    const response = await h.fetch(LAYER);
    expect(response.headers.get("content-type")).toBe("application/geo+json");
    await expect(response.text()).resolves.toBe(LAYER_BODY);
  });

  it("treats the platform's 502 as the server being away", async () => {
    const h = harness(async () => new Response("Bad gateway", { status: 502 }));
    const response = await h.fetch(HOTSPOTS);
    expect(response.headers.get("x-varuna-mirror")).toBe("1");
  });

  it("gives up on a server that never wakes and says so", async () => {
    const h = harness(refused);
    await h.fetch(HOTSPOTS);
    // Let the wake loop run out its (fake) budget.
    for (let i = 0; i < 50; i += 1) await Promise.resolve();
    expect(h.states).toContain("offline");
  });

  it("serves the mirror at once when the server is known to be down", async () => {
    const h = harness(refused);
    await h.fetch(HOTSPOTS);
    for (let i = 0; i < 50; i += 1) await Promise.resolve();
    const apiCallsBefore = h.calls.filter((c) => c.startsWith(`${API}/v1/`)).length;
    const again = await h.fetch(HOTSPOTS);
    expect(again.headers.get("x-varuna-mirror")).toBe("1");
    const apiCallsAfter = h.calls.filter((c) => c.startsWith(`${API}/v1/`)).length;
    expect(apiCallsAfter).toBe(apiCallsBefore);
  });

  it("waits for a sleeping server to wake before sending a POST again", async () => {
    let healthChecks = 0;
    let posts = 0;
    const h = harness(async (url) => {
      if (url.endsWith("/healthz")) {
        healthChecks += 1;
        return healthChecks >= 3 ? json({ status: "ok" }) : refused();
      }
      posts += 1;
      return posts === 1 ? refused() : json({ id: "r1" }, 201);
    });
    const response = await h.fetch(`${API}/v1/reports`, { method: "POST", body: "{}" });
    expect(response.status).toBe(201);
    expect(posts).toBe(2);
    expect(h.states).toEqual(["waking", "ok"]);
  });

  it("does not resend a POST to a server that never woke", async () => {
    const h = harness(refused);
    await expect(h.fetch(`${API}/v1/reports`, { method: "POST", body: "{}" })).rejects.toBeInstanceOf(TypeError);
  });

  it("stops waiting when the caller gives up", async () => {
    const h = harness(refused);
    const controller = new AbortController();
    controller.abort();
    await expect(
      h.fetch(`${API}/v1/route`, { method: "POST", body: "{}", signal: controller.signal }),
    ).rejects.toMatchObject({ name: "AbortError" });
  });

  it("reports the server back once it answers after the page showed stored data", async () => {
    let up = false;
    const h = harness(async (url) => {
      if (!up) return refused();
      return url.endsWith("/healthz") ? json({ status: "ok" }) : json({ live: true });
    });
    await h.fetch(HOTSPOTS);
    up = true;
    for (let i = 0; i < 50; i += 1) await Promise.resolve();
    expect(h.states).toEqual(["waking", "back"]);
  });
});

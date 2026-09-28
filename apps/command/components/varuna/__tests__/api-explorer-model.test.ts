import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it, vi } from "vitest";

import {
  DEMO_RUN_ID,
  PASSPHRASE_HEADER,
  PRESETS,
  assertNoPassphrase,
  buildRequest,
  demoPlacesFrom,
  filterOperations,
  groupByTag,
  initialValues,
  operationsFromOpenApi,
  readEnvelope,
  sendRequest,
  toCurl,
  type ApiOperation,
  type OpenApiLike,
} from "@/components/varuna/api-explorer-model";

/** The committed snapshot the page reads: the tests run against the real contract. */
const snapshot = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../openapi.json"), "utf-8"),
) as OpenApiLike;
const operations = operationsFromOpenApi(snapshot);
const byKey = new Map(operations.map((op) => [op.key, op]));

function op(key: string): ApiOperation {
  const found = byKey.get(key);
  if (!found) throw new Error(`${key} is not in the snapshot`);
  return found;
}

describe("operationsFromOpenApi", () => {
  it("reads every path of the committed snapshot", () => {
    const paths = new Set(operations.map((o) => o.path));
    // Against the snapshot itself, never against a number written down on the day. The count was
    // pinned at 52 and the merge that added the terrain, basemap, escalation, sender, delivery,
    // send, price and live-compute routes took it to 60, failing a test that had found no defect.
    expect(paths.size).toBe(Object.keys(snapshot.paths).length);
    // The explorer is only worth having if it lists the whole API, so a snapshot that has lost
    // most of its paths should still fail here rather than pass an empty comparison.
    expect(paths.size).toBeGreaterThan(40);
  });

  it("strips the passphrase header from every form and marks the operation", () => {
    for (const o of operations) {
      expect(o.params.map((p) => p.name.toLowerCase())).not.toContain(PASSPHRASE_HEADER);
    }
    const gated = operations.filter((o) => o.needsPassphrase).map((o) => o.key);
    expect(gated).toContain("POST /v1/ops/closures");
    expect(gated).toContain("POST /v1/alerts/{alert_id}/ack");
    for (const key of gated) expect(op(key).runnable).toBe(false);
  });

  it("sends reads and the two compute-only POSTs, and nothing that changes state", () => {
    const runnablePosts = operations
      .filter((o) => o.method !== "GET" && o.runnable)
      .map((o) => o.key);
    expect(runnablePosts.sort()).toEqual(["POST /v1/route", "POST /v1/whatif"]);
    expect(operations.filter((o) => o.method === "GET").every((o) => o.runnable)).toBe(true);
    expect(op("POST /v1/replay/play").notRunnableReason).toMatch(/replay clock/);
    expect(op("POST /v1/reports").runnable).toBe(false);
  });

  it("resolves a referenced request body into its fields", () => {
    const body = op("POST /v1/ops/closures").body;
    expect(body?.kind).toBe("schema");
    if (body?.kind === "schema") {
      expect(body.schema).toBe("ClosureRequest");
      expect(body.fields.length).toBeGreaterThan(0);
    }
    expect(op("POST /v1/route").body).toEqual({ kind: "free" });
    expect(op("GET /healthz").body).toBeNull();
  });

  it("groups by tag and filters on path, method and summary", () => {
    const groups = groupByTag(operations);
    expect(groups.reduce((n, g) => n + g.operations.length, 0)).toBe(operations.length);
    expect(filterOperations(operations, "weather").map((o) => o.key)).toEqual(["GET /v1/weather"]);
    expect(filterOperations(operations, "  ").length).toBe(operations.length);
  });
});

describe("buildRequest", () => {
  it("fills path parameters, drops empty optional ones and pre-fills the demo run", () => {
    const series = op("GET /v1/nowcast/segments/{segment_id}/series");
    const values: Record<string, string> = { ...initialValues(series), segment_id: "S1/2" };
    expect(values.run_id).toBe(DEMO_RUN_ID);
    const built = buildRequest(series, values, "", "http://localhost:8154/");
    expect(built.ok).toBe(true);
    if (built.ok) {
      expect(built.request.url).toBe(
        `http://localhost:8154/v1/nowcast/segments/S1%2F2/series?run_id=${DEMO_RUN_ID}`,
      );
      expect(built.request.body).toBeNull();
    }
  });

  it("refuses a missing required parameter by name, before anything is sent", () => {
    const built = buildRequest(op("GET /v1/reachability"), { facility: " " }, "", "http://x");
    expect(built).toEqual({ ok: false, message: "facility is required." });
  });

  it("refuses a body that is not JSON", () => {
    const built = buildRequest(op("POST /v1/route"), {}, "{origin:", "http://x");
    expect(built.ok).toBe(false);
    if (!built.ok) expect(built.message).toMatch(/not JSON/);
  });

  it("never carries the passphrase header, and the guard refuses one however it arrives", () => {
    const closures = op("POST /v1/ops/closures");
    const built = buildRequest(closures, {}, "{}", "http://x");
    expect(built.ok).toBe(true);
    if (built.ok) {
      expect(Object.keys(built.request.headers).map((h) => h.toLowerCase())).not.toContain(
        PASSPHRASE_HEADER,
      );
      const curl = toCurl(built.request, closures.needsPassphrase);
      expect(curl).toContain("$VARUNA_OPS_PASSPHRASE");
    }
    expect(() => assertNoPassphrase({ "X-Varuna-Ops": "hunter2" })).toThrow(/never sends/);
  });
});

describe("sendRequest", () => {
  const request = {
    method: "POST" as const,
    url: "http://localhost:8154/v1/route",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: "{}",
  };

  it("reports status, time and the pretty body", async () => {
    let t = 100;
    const fetchImpl = vi.fn(async () => {
      t = 342;
      return new Response(JSON.stringify({ run_id: "R", ms: 80 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    const result = await sendRequest(request, {
      fetchImpl: fetchImpl as unknown as typeof fetch,
      now: () => t,
    });
    expect(result.status).toBe(200);
    expect(result.ms).toBe(242);
    expect(result.kind).toBe("json");
    expect(result.shown).toContain('"run_id": "R"');
    expect(result.envelope).toBeNull();
  });

  it("shows a refusal in the API's own envelope", async () => {
    const envelope = {
      error: {
        code: "bad_point",
        message: "origin must be [lon, lat] or {lon, lat}.",
        run_id: null,
      },
    };
    const fetchImpl = async () =>
      new Response(JSON.stringify(envelope), {
        status: 422,
        headers: { "content-type": "application/json" },
      });
    const result = await sendRequest(request, { fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(result.status).toBe(422);
    expect(result.envelope).toEqual({
      code: "bad_point",
      message: envelope.error.message,
      runId: null,
    });
  });

  it("turns an unreachable API into a result that says so", async () => {
    const fetchImpl = async () => {
      throw new TypeError("Failed to fetch");
    };
    const result = await sendRequest(request, { fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(result.status).toBe(0);
    expect(result.kind).toBe("unreachable");
    expect(result.shown).toMatch(/never reached http:\/\/localhost:8154/);
  });

  it("cuts a long body and says how long it was", async () => {
    const long = JSON.stringify({
      rows: Array.from({ length: 5000 }, (_, i) => ({ segment_id: `S${i}`, delta_cm: i })),
    });
    const fetchImpl = async () =>
      new Response(long, { status: 200, headers: { "content-type": "application/json" } });
    const result = await sendRequest(request, { fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(result.truncatedFrom).toBeGreaterThan(result.shown.length);
  });

  it("refuses to send a request carrying the passphrase", async () => {
    const fetchImpl = vi.fn();
    await expect(
      sendRequest(
        { ...request, headers: { [PASSPHRASE_HEADER]: "x" } },
        { fetchImpl: fetchImpl as unknown as typeof fetch },
      ),
    ).rejects.toThrow();
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("presets", () => {
  it("each names an operation in the snapshot the explorer is allowed to send", () => {
    expect(PRESETS.map((p) => p.id)).toEqual(["segments", "route", "whatif"]);
    for (const preset of PRESETS) expect(op(preset.operationKey).runnable).toBe(true);
  });

  it("sends the what-if body the endpoint reads, pinned to the demo run", () => {
    const whatif = PRESETS.find((p) => p.id === "whatif");
    const body = JSON.parse(whatif?.body(null) ?? "{}") as Record<string, unknown>;
    expect(body).toEqual({ run_id: DEMO_RUN_ID, rain_scale: 1.3, tide_offset_m: 0 });
  });

  it("reads KEM and Sion from the facility register", () => {
    const places = demoPlacesFrom({
      facilities: [
        { name: "King Edward Memorial (KEM) Hospital, Parel", lon: 72.8414, lat: 19.0028 },
        {
          name: "Lokmanya Tilak Municipal General (LTMG) Hospital, Sion",
          lon: 72.8608,
          lat: 19.0374,
        },
      ],
    });
    expect(places).toEqual({ origin: [72.8414, 19.0028], destination: [72.8608, 19.0374] });
    expect(demoPlacesFrom({ facilities: [] })).toBeNull();
    const route = PRESETS.find((p) => p.id === "route");
    const body = JSON.parse(route?.body(places) ?? "{}") as Record<string, unknown>;
    expect(body.origin).toEqual([72.8414, 19.0028]);
    expect(body.spread).toBe(true);
    expect(body.explain).toBe(true);
    expect(body.trip_id).toBe("api-explorer-demo");
  });
});

describe("readEnvelope", () => {
  it("accepts only the documented shape", () => {
    expect(readEnvelope({ detail: "x" })).toBeNull();
    expect(readEnvelope({ error: { code: "c", message: "m", run_id: "R" } })).toEqual({
      code: "c",
      message: "m",
      runId: "R",
    });
  });
});

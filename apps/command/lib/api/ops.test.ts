/**
 * Contract tests for the authority client (task D-15).
 *
 * Fixtures are the shapes `services/api/varuna_api/routers/ops.py` serialises, field for field,
 * so a rename on the API side fails here rather than on a ward officer's screen. The passphrase
 * tests are the ones that matter most: they assert where it goes (one header) and where it does
 * not (no query string, no `localStorage`, no message the screen prints back).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  changesForecast,
  checkPassphrase,
  clearPassphrase,
  describeEntry,
  describeRefusal,
  dispatchPumps,
  isGateRefusal,
  loadClosures,
  loadOpsLog,
  loadReports,
  loadStreetOptions,
  opsRefusal,
  OPS_HEADER,
  OPS_SESSION_KEY,
  optimisePumps,
  postAlertAction,
  postClosure,
  postPumpStatus,
  readPassphrase,
  writePassphrase,
  WRONG_PASSPHRASE_MESSAGE,
} from "@/lib/api/ops";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function envelope(code: string, message: string, status: number): Response {
  return jsonResponse({ error: { code, message } }, status);
}

/** Records every request the client makes so the tests can read its headers and body. */
interface Sent {
  url: string;
  init: RequestInit | undefined;
}

function capture(responder: (url: string) => Response): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    sent.push({ url: String(input), init });
    return responder(String(input));
  });
  return sent;
}

function headerOf(sent: Sent, name: string): string | null {
  return new Headers(sent.init?.headers).get(name);
}

function bodyOf(sent: Sent): Record<string, unknown> {
  return JSON.parse(String(sent.init?.body ?? "{}")) as Record<string, unknown>;
}

beforeEach(() => {
  clearPassphrase();
  window.sessionStorage.clear();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  clearPassphrase();
});

describe("the passphrase", () => {
  it("is held for the tab and never written to localStorage", () => {
    writePassphrase("  monsoon-desk  ");

    expect(readPassphrase()).toBe("monsoon-desk");
    expect(window.sessionStorage.getItem(OPS_SESSION_KEY)).toBe("monsoon-desk");
    expect(window.localStorage.length).toBe(0);
  });

  it("is forgotten on sign out, in memory and in the tab's storage", () => {
    writePassphrase("monsoon-desk");
    clearPassphrase();

    expect(readPassphrase()).toBeNull();
    expect(window.sessionStorage.getItem(OPS_SESSION_KEY)).toBeNull();
  });

  it("travels in one header and never in the URL", async () => {
    writePassphrase("monsoon-desk");
    const sent = capture(() =>
      jsonResponse({ entry: { id: "a1", kind: "closure", ts: "t", user: "u" }, closures: [] }),
    );

    await postClosure({
      segmentId: "seg-1",
      reason: "Water over the kerb",
      user: "ward officer",
    });

    expect(headerOf(sent[0]!, OPS_HEADER)).toBe("monsoon-desk");
    expect(sent[0]!.url).not.toContain("monsoon-desk");
  });

  it("is not sent at all when the tab holds none", async () => {
    const sent = capture(() => jsonResponse({ city: "mumbai", closures: [] }));

    await postClosure({ segmentId: "seg-1", reason: "x", user: "u" }).catch(() => undefined);

    expect(headerOf(sent[0]!, OPS_HEADER)).toBeNull();
  });
});

describe("opsRefusal", () => {
  const cases: [string, number, string][] = [
    ["ops_writes_disabled", 503, "disabled"],
    ["ops_passphrase_required", 401, "passphrase_required"],
    ["ops_passphrase_rejected", 403, "rejected"],
    ["rate_limited", 429, "rate_limited"],
    ["no_run", 404, "refused"],
  ];

  for (const [code, status, kind] of cases) {
    it(`reads ${code} as ${kind}`, async () => {
      capture(() => envelope(code, "The API's own sentence.", status));

      const refusal = await optimisePumps().then(
        () => null,
        (error: unknown) => opsRefusal(error),
      );

      expect(refusal?.kind).toBe(kind);
      expect(refusal?.code).toBe(code);
    });
  }

  it("keeps the API's sentence for everything except a wrong passphrase", async () => {
    capture(() => envelope("rate_limited", "30 authority edits a minute is the limit.", 429));

    const refusal = await optimisePumps().then(
      () => null,
      (error: unknown) => opsRefusal(error),
    );

    expect(describeRefusal(refusal!)).toBe("30 authority edits a minute is the limit.");
  });

  it("gives every wrong passphrase the same sentence, whatever the API said", async () => {
    capture(() => envelope("ops_passphrase_rejected", "Nothing was written.", 403));
    const rejected = await optimisePumps().then(
      () => null,
      (error: unknown) => opsRefusal(error),
    );
    vi.unstubAllGlobals();

    capture(() => envelope("ops_passphrase_required", "This edit carries no passphrase.", 401));
    const missing = await optimisePumps().then(
      () => null,
      (error: unknown) => opsRefusal(error),
    );

    expect(describeRefusal(rejected!)).toBe(WRONG_PASSPHRASE_MESSAGE);
    expect(describeRefusal(missing!)).toBe(WRONG_PASSPHRASE_MESSAGE);
    expect(isGateRefusal(rejected!)).toBe(true);
    expect(isGateRefusal(missing!)).toBe(true);
  });
});

describe("checkPassphrase", () => {
  it("accepts a passphrase the gate let through to the 501 it asked for", async () => {
    const sent = capture(() =>
      envelope("not_implemented", "The MILP solver is P1 (task P8.9).", 501),
    );

    const refusal = await checkPassphrase("monsoon-desk");

    expect(refusal).toBeNull();
    expect(bodyOf(sent[0]!).solver).toBe("milp");
    expect(headerOf(sent[0]!, OPS_HEADER)).toBe("monsoon-desk");
  });

  it("rejects one the gate refused", async () => {
    capture(() => envelope("ops_passphrase_rejected", "Nothing was written.", 403));

    const refusal = await checkPassphrase("wrong");

    expect(refusal?.kind).toBe("rejected");
  });

  it("reports an API that holds no passphrase as disabled rather than as a wrong one", async () => {
    capture(() => envelope("ops_writes_disabled", "VARUNA_OPS_PASSPHRASE is not set.", 503));

    const refusal = await checkPassphrase("anything");

    expect(refusal?.kind).toBe("disabled");
    expect(refusal?.message).toContain("VARUNA_OPS_PASSPHRASE");
  });

  it("refuses an empty field without asking the API", async () => {
    const sent = capture(() => jsonResponse({}));

    const refusal = await checkPassphrase("   ");

    expect(refusal?.kind).toBe("passphrase_required");
    expect(sent).toHaveLength(0);
  });
});

describe("reads", () => {
  it("reads the log, its entries and whether this API accepts writes at all", async () => {
    capture(() =>
      jsonResponse({
        city: "mumbai",
        n_entries: 2,
        entries: [
          {
            id: "e2",
            kind: "closure",
            ts: "2019-07-02T08:12:00+05:30",
            user: "ward officer",
            segment_id: "seg-42",
            reason: "Water over the kerb",
          },
          {
            id: "e1",
            kind: "pump_status",
            ts: "2019-07-02T08:05:00+05:30",
            user: "control room",
            pump_id: "P-12",
            status: "unavailable",
          },
        ],
        writes_enabled: false,
        passphrase_env: "VARUNA_OPS_PASSPHRASE",
        notes: ["This API is read-only."],
      }),
    );

    const log = await loadOpsLog();

    expect(log.writesEnabled).toBe(false);
    expect(log.entries[0]?.kind).toBe("closure");
    expect(log.entries[0]?.detail.segment_id).toBe("seg-42");
    expect(log.notes[0]).toBe("This API is read-only.");
  });

  it("reads the live closure set with its expiries", async () => {
    capture(() =>
      jsonResponse({
        city: "mumbai",
        at: "2019-07-02T08:40:00+05:30",
        n_closed: 1,
        closures: [
          {
            segment_id: "seg-42",
            reason: "Water over the kerb",
            user: "ward officer",
            ts: "2019-07-02T08:12:00+05:30",
            until: null,
            id: "e2",
          },
        ],
        n_entries: 3,
        writes_enabled: true,
      }),
    );

    const set = await loadClosures();

    expect(set.closures).toHaveLength(1);
    expect(set.closures[0]?.reason).toBe("Water over the kerb");
    expect(set.closures[0]?.until).toBeNull();
  });

  it("reads street options from the road-conditions feed, keeping an unnamed street null", async () => {
    capture(() =>
      jsonResponse({
        type: "FeatureCollection",
        features: [
          {
            properties: {
              segment_id: "seg-42",
              name: "Dr Ambedkar Road",
              cause: "forecast",
              peak_depth_cm: 54.2,
              from: "2019-07-02T08:20:00+05:30",
              to: "2019-07-02T10:05:00+05:30",
            },
          },
          { properties: { segment_id: "seg-99", name: null, cause: "closure" } },
        ],
      }),
    );

    const streets = await loadStreetOptions({ profile: "car" });

    expect(streets[0]?.name).toBe("Dr Ambedkar Road");
    expect(streets[0]?.peakDepthCm).toBe(54.2);
    expect(streets[1]?.name).toBeNull();
    expect(streets[1]?.cause).toBe("closure");
  });

  it("reads the citizen inbox", async () => {
    capture(() =>
      jsonResponse({
        count: 1,
        reports: [
          {
            id: "rpt-1",
            ts: "2019-07-02T08:47:00+05:30",
            received_at: "2019-07-02T08:47:10+05:30",
            lat: 19.012,
            lon: 72.841,
            depth_hint: "knee",
            depth_cm: 45,
            text: "Water at the junction",
            has_photo: true,
            source: "public-map",
            synthetic: false,
          },
        ],
      }),
    );

    const reports = await loadReports();

    expect(reports).toHaveLength(1);
    expect(reports[0]?.depthCm).toBe(45);
    expect(reports[0]?.hasPhoto).toBe(true);
  });
});

describe("writes", () => {
  it("sends a closure in the API's own field names and reads back what it changed", async () => {
    writePassphrase("monsoon-desk");
    const sent = capture(() =>
      jsonResponse({
        entry: {
          id: "e7",
          kind: "closure",
          ts: "2019-07-02T08:12:00+05:30",
          user: "ward officer",
          segment_id: "seg-42",
        },
        city: "mumbai",
        at: "2019-07-02T08:12:00+05:30",
        n_closed: 1,
        closures: [
          {
            segment_id: "seg-42",
            reason: "Water over the kerb",
            user: "ward officer",
            ts: "2019-07-02T08:12:00+05:30",
            until: null,
            id: "e7",
          },
        ],
        n_entries: 1,
        writes_enabled: true,
        notes: ["This changed no forecast."],
      }),
    );

    const result = await postClosure({
      segmentId: "seg-42",
      reason: "Water over the kerb",
      until: null,
      user: "ward officer",
      city: "mumbai",
    });

    expect(bodyOf(sent[0]!)).toMatchObject({
      segment_id: "seg-42",
      reason: "Water over the kerb",
      reopen: false,
    });
    expect(result.closures).toHaveLength(1);
    expect(result.notes[0]).toBe("This changed no forecast.");
  });

  it("sends a reopen as an append, not a delete", async () => {
    writePassphrase("monsoon-desk");
    const sent = capture(() => jsonResponse({ entry: {}, closures: [], notes: [] }));

    await postClosure({ segmentId: "seg-42", reason: "", user: "u", reopen: true });

    expect(sent[0]!.init?.method).toBe("POST");
    expect(bodyOf(sent[0]!).reopen).toBe(true);
  });

  it("sends a pump status and reads the desk's whole pump state back", async () => {
    writePassphrase("monsoon-desk");
    const sent = capture(() =>
      jsonResponse({
        entry: { id: "e8", kind: "pump_status", ts: "t", user: "u" },
        city: "mumbai",
        pumps: {
          "P-12": { status: "unavailable", user: "ward officer", ts: "t", lon: null, lat: null },
        },
        notes: ["The next optimise honours this."],
      }),
    );

    const result = await postPumpStatus({
      pumpId: "P-12",
      status: "unavailable",
      user: "ward officer",
    });

    expect(sent[0]!.url).toContain("/v1/ops/pumps/P-12/status");
    expect(bodyOf(sent[0]!).status).toBe("unavailable");
    expect(result.pumps["P-12"]?.status).toBe("unavailable");
  });

  it("acknowledges an alert against the run that raised it", async () => {
    writePassphrase("monsoon-desk");
    const sent = capture(() =>
      jsonResponse({
        run_id: "MUM-20190702T0310Z",
        entry: { id: "e9", kind: "alert_ack", ts: "t", user: "ward officer" },
        alert: {
          id: "alert-1",
          level: "severe",
          headline: "Hindmata junction",
          state: "acknowledged",
          acknowledged_by: "ward officer",
          acknowledged_ts: "2019-07-02T08:14:00+05:30",
        },
        notes: [],
      }),
    );

    const result = await postAlertAction({
      alertId: "alert-1",
      action: "ack",
      user: "ward officer",
      runId: "MUM-20190702T0310Z",
    });

    expect(sent[0]!.url).toContain("/v1/alerts/alert-1/ack?run_id=MUM-20190702T0310Z");
    expect(result.alert.state).toBe("acknowledged");
    expect(result.alert.acknowledgedBy).toBe("ward officer");
  });

  it("reads an optimised plan, its withheld pumps and the model that priced it", async () => {
    writePassphrase("monsoon-desk");
    capture(() =>
      jsonResponse({
        run_id: "MUM-20190702T0310Z",
        city: "mumbai",
        threshold_cm: 45,
        assignments: [
          {
            pump_id: "P-03",
            depot: "Parel depot",
            hotspot_id: "hindmata",
            hotspot_name: "Hindmata junction",
            eta_min: 25,
            minutes_saved: 40,
            benefit_model: "emulator",
          },
        ],
        withheld: [{ pump_id: "P-12", status: "unavailable" }],
        total_minutes_saved: 40,
        benefit_model: "emulator",
        benefit_label: "Emulator estimate",
        solve_ms: 812,
        notes: ["Withheld by the desk and not assigned: P-12 (unavailable)."],
      }),
    );

    const plan = await optimisePumps();

    expect(plan.assignments[0]?.pumpId).toBe("P-03");
    expect(plan.withheld[0]?.pumpId).toBe("P-12");
    expect(plan.benefitLabel).toBe("Emulator estimate");
    expect(plan.solveMs).toBe(812);
  });

  it("dispatches and keeps the API's order text rather than composing one", async () => {
    writePassphrase("monsoon-desk");
    capture(() =>
      jsonResponse(
        {
          run_id: "MUM-20190702T0310Z",
          city: "mumbai",
          dispatched: true,
          dispatched_by: "control room",
          dispatched_ts: "2019-07-02T08:41:00+05:30",
          n_dispatched: 1,
          orders: [
            {
              pump_id: "P-03",
              depot: "Parel depot",
              hotspot_id: "hindmata",
              hotspot_name: "Hindmata junction",
              eta_min: 25,
              minutes_saved: 40,
              benefit_model: "emulator",
              order_text: "Move P-03 from Parel depot to Hindmata junction now; ETA 25 min.",
            },
          ],
          benefit_label: "Emulator estimate",
          synthetic_inventory: true,
          notes: ["The pump inventory is synthetic."],
        },
        202,
      ),
    );

    const result = await dispatchPumps({ user: "control room" });

    expect(result.orders[0]?.orderText).toContain("Move P-03 from Parel depot");
    expect(result.syntheticInventory).toBe(true);
  });
});

describe("wording the log", () => {
  it("words each kind from its own fields", () => {
    const row = (kind: string, detail: Record<string, unknown>) => ({
      id: "e1",
      kind,
      ts: "t",
      user: "u",
      detail,
    });

    expect(describeEntry(row("closure", { segment_id: "seg-42", reason: "Water" }))).toBe(
      "Closed seg-42 — Water",
    );
    expect(describeEntry(row("reopen", { segment_id: "seg-42" }))).toBe("Reopened seg-42");
    expect(describeEntry(row("pump_status", { pump_id: "P-12", status: "moved" }))).toBe(
      "Pump P-12 marked moved",
    );
    expect(describeEntry(row("alert_escalate", { alert_id: "a1", to: "police" }))).toBe(
      "Escalated alert a1 to police",
    );
    expect(describeEntry(row("dispatch", { order_text: "Move P-03 now." }))).toBe("Move P-03 now.");
  });

  it("prints an unknown kind rather than hiding the row", () => {
    expect(describeEntry({ id: "e1", kind: "invented", ts: "t", user: "u", detail: {} })).toBe(
      "Recorded an entry of kind invented",
    );
  });

  it("knows which kinds a route reads and which are only ever a record", () => {
    expect(changesForecast("closure")).toBe(true);
    expect(changesForecast("reopen")).toBe(true);
    expect(changesForecast("pump_status")).toBe(true);
    expect(changesForecast("alert_ack")).toBe(false);
    expect(changesForecast("dispatch")).toBe(false);
  });
});

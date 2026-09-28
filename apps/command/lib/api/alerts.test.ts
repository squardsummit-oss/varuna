import { describe, expect, it, vi } from "vitest";

import {
  alertMessages,
  alertStatus,
  alertWindowLine,
  capFilename,
  isOpenEnded,
  loadAlertCount,
  loadAlerts,
  loadDelivery,
  sortByOnset,
  splitPending,
  summariseQueue,
  type EscalationStep,
  type PendingAlert,
  type RunAlert,
} from "./alerts";

/**
 * The desk's state, as the console reads it.
 *
 * `GET /v1/alerts` serves the cycle's own queue with the ops log folded onto it at read time, so
 * an acknowledgement reaches this screen without the screen remembering anything. It used to
 * remember: a local boolean that vanished on reload and never existed for anyone else. These pin
 * that the state comes off the wire, and that an answer without one is read as `raised` rather
 * than as "somebody has seen this".
 */

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

const BASE = {
  id: "VARUNA-MUM-TEST-STREET-1105-SEVERE",
  run_id: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
  level: "severe",
  area_desc: "V B Worlikar Marg",
  scope: "segment",
  headline: "V B Worlikar Marg: depth likely above 45 cm",
};

async function load(alert: Record<string, unknown>) {
  vi.stubGlobal("fetch", async () => json({ run_id: "r", alerts: [{ ...BASE, ...alert }] }));
  try {
    const set = await loadAlerts();
    return set!.alerts[0]!;
  } finally {
    vi.unstubAllGlobals();
  }
}

describe("loadAlerts: the desk's state", () => {
  it("reads an untouched alert as raised, with nothing claimed about who has seen it", async () => {
    const alert = await load({ state: "raised" });

    expect(alert.state).toBe("raised");
    expect(alert.acknowledgedBy).toBeNull();
    expect(alert.acknowledgedTs).toBeNull();
    expect(alert.history).toEqual([]);
  });

  it("carries who acknowledged it and when", async () => {
    const alert = await load({
      state: "acknowledged",
      acknowledged_by: "ward officer",
      acknowledged_ts: "2019-07-02T08:12:00+05:30",
      history: [
        {
          ts: "2019-07-02T08:12:00+05:30",
          state: "acknowledged",
          user: "ward officer",
          note: "Traffic police informed",
        },
      ],
    });

    expect(alert.state).toBe("acknowledged");
    expect(alert.acknowledgedBy).toBe("ward officer");
    expect(alert.acknowledgedTs).toBe("2019-07-02T08:12:00+05:30");
    expect(alert.history).toEqual([
      {
        ts: "2019-07-02T08:12:00+05:30",
        state: "acknowledged",
        user: "ward officer",
        note: "Traffic police informed",
      },
    ]);
  });

  it("keeps the acknowledgement after an escalation, because the trail is the point", async () => {
    const alert = await load({
      state: "escalated",
      acknowledged_by: "ward officer",
      escalated_to: "police_traffic",
      history: [
        { ts: "1", state: "acknowledged", user: "ward officer" },
        { ts: "2", state: "escalated", user: "ward officer" },
      ],
    });

    expect(alert.state).toBe("escalated");
    expect(alert.escalatedTo).toBe("police_traffic");
    expect(alert.acknowledgedBy).toBe("ward officer");
    expect(alert.history.map((h) => h.state)).toEqual(["acknowledged", "escalated"]);
  });

  it("reads a state it does not know as raised", async () => {
    // Never the other way round: inventing "seen" from a word we do not recognise is the one
    // mistake an alert queue must not make.
    expect((await load({ state: "dismissed-by-someone" })).state).toBe("raised");
    expect((await load({})).state).toBe("raised");
  });

  it("fills a history row that arrives without its user", async () => {
    const alert = await load({
      state: "acknowledged",
      history: [{ ts: "1", state: "acknowledged" }],
    });

    expect(alert.history[0]!.user).toBe("unknown");
    expect(alert.history[0]!.note).toBeNull();
  });
});

describe("loadAlerts: the place, the window and the ensemble", () => {
  it("carries the street, its locality, an open-ended window and the member count", async () => {
    const alert = await load({
      name: "Pipeline Road",
      locality: "near Sion Circle",
      window_open_ended: true,
      members_above: 32,
      members_total: 50,
    });

    expect(alert.name).toBe("Pipeline Road");
    expect(alert.locality).toBe("near Sion Circle");
    expect(alert.windowOpenEnded).toBe(true);
    expect(alert.membersAbove).toBe(32);
    expect(alert.membersTotal).toBe(50);
  });

  it("reads a run baked before those fields as unknown, not as false or zero", async () => {
    // An old queue never said whether its window ran to the horizon or how many members agreed;
    // reading that silence as "no" would print a claim the product never made.
    const alert = await load({});

    expect(alert.name).toBeNull();
    expect(alert.locality).toBeNull();
    expect(alert.windowOpenEnded).toBeNull();
    expect(alert.membersAbove).toBeNull();
    expect(alert.membersTotal).toBeNull();
  });

  it("carries the locality and member count on a pending place too", async () => {
    vi.stubGlobal("fetch", async () =>
      json({
        run_id: "r",
        alerts: [],
        pending: [
          {
            id: "p1",
            level: "severe",
            headline: "Pipeline Road, near Sion Circle: depth above 45 cm",
            area_desc: "Pipeline Road",
            name: "Pipeline Road",
            locality: "near Sion Circle",
            members_above: 12,
            members_total: 50,
          },
        ],
      }),
    );
    try {
      const set = await loadAlerts();
      expect(set!.pending[0]).toMatchObject({
        name: "Pipeline Road",
        locality: "near Sion Circle",
        membersAbove: 12,
        membersTotal: 50,
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

// ---------------------------------------------------------------------------------------------
// How the alert centre reads a queue
// ---------------------------------------------------------------------------------------------

const CYCLE = "2019-07-02T08:40:00+05:30";

function runAlert(over: Partial<RunAlert>): RunAlert {
  return {
    id: "A",
    runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
    level: "severe",
    thresholdCm: 45,
    headline: "",
    instruction: null,
    areaDesc: "Sant Shitolebaba Maharaj Marg",
    name: null,
    locality: null,
    scope: "segment",
    hotspotId: null,
    lon: null,
    lat: null,
    peakCm: 99.8,
    windowFrom: "2019-07-02T09:20:00+05:30",
    windowTo: "2019-07-02T11:40:00+05:30",
    windowOpenEnded: null,
    membersAbove: null,
    membersTotal: null,
    raisedTs: "2019-07-02T08:10:00+05:30",
    persistsCycles: 3,
    persistsUnit: "cycles",
    firstSeenTs: null,
    sentTs: null,
    notify: [],
    pumps: [],
    dispatchNote: null,
    triggerP: 1,
    capStatus: "Exercise",
    sourceUrl: null,
    state: "raised",
    acknowledgedBy: null,
    acknowledgedTs: null,
    escalatedTo: null,
    history: [],
    ...over,
  };
}

const STEPS: EscalationStep[] = [
  { id: "ward_officer", recipient: "Ward officer", trigger: "", channel: "", levels: [] },
  { id: "control_room", recipient: "Control room", trigger: "", channel: "", levels: [] },
];

describe("the row's window", () => {
  it("says the forecast ends, not the water, when the window runs to the horizon", () => {
    // A run baked before `window_open_ended`: read from the horizon, 180 min after the cycle.
    expect(isOpenEnded(runAlert({}), CYCLE)).toBe(true);
    expect(alertWindowLine(runAlert({}), CYCLE)).toBe(
      "09:20 (+40 min) until the end of the forecast",
    );
  });

  it("gives the end time when the water goes below the threshold inside the forecast", () => {
    const closed = runAlert({ windowTo: "2019-07-02T10:10:00+05:30" });
    expect(alertWindowLine(closed, CYCLE)).toBe("09:20 (+40 min) to 10:10");
  });

  it("believes the product when it says so", () => {
    expect(isOpenEnded(runAlert({ windowOpenEnded: false }), CYCLE)).toBe(false);
  });
});

describe("the row's status pill", () => {
  it("reads new on the cycle that raised it and held after", () => {
    expect(alertStatus(runAlert({ raisedTs: CYCLE }), CYCLE, STEPS).label).toBe("New");
    expect(alertStatus(runAlert({}), CYCLE, STEPS).label).toBe("Held 3 cycles");
  });

  it("puts the desk's state first, with when and to whom", () => {
    expect(
      alertStatus(
        runAlert({
          state: "acknowledged",
          acknowledgedBy: "ward officer",
          acknowledgedTs: "2019-07-02T08:52:00+05:30",
        }),
        CYCLE,
        STEPS,
      ).label,
    ).toBe("Acknowledged 08:52");
    expect(
      alertStatus(runAlert({ state: "escalated", escalatedTo: "control_room" }), CYCLE, STEPS)
        .label,
    ).toBe("Escalated to control room");
  });
});

describe("pending places beside the queue", () => {
  const pending = (over: Partial<PendingAlert>): PendingAlert => ({
    id: "P",
    level: "severe",
    headline: "",
    areaDesc: "Mahatma Gandhi Road",
    name: null,
    locality: null,
    scope: "segment",
    peakCm: 88.6,
    membersAbove: null,
    membersTotal: null,
    sinceTs: CYCLE,
    raisedLevel: null,
    raisedLevelKnown: true,
    ...over,
  });

  it("puts a level up on the row the place already has, and lists only new places apart", () => {
    const moderate = runAlert({ id: "M", level: "moderate", areaDesc: "Mahatma Gandhi Road" });
    const { upgrades, pendingOnly } = splitPending(
      [moderate],
      [pending({ id: "P1" }), pending({ id: "P2", areaDesc: "Pipeline Road", level: "watch" })],
    );
    expect(upgrades.get("M")?.level).toBe("severe");
    expect(pendingOnly.map((p) => p.id)).toEqual(["P2"]);
  });

  it("never calls a place raised beyond a capped list 'not raised yet'", () => {
    // 08:40 in miniature: the listed queue is full, and a watch street off the list goes moderate.
    const listed = runAlert({ id: "M", level: "moderate", areaDesc: "Mahatma Gandhi Road" });
    const { upgrades, stepUps, pendingOnly, unplaced } = splitPending(
      [listed],
      [
        pending({ id: "UP", level: "moderate", areaDesc: "Pipeline Road", raisedLevel: "watch" }),
        pending({ id: "NEW", level: "watch", areaDesc: "Dharavi Main Road" }),
      ],
      true,
    );
    expect(upgrades.size).toBe(0);
    expect(stepUps.map((p) => p.id)).toEqual(["UP"]);
    expect(pendingOnly.map((p) => p.id)).toEqual(["NEW"]);
    expect(unplaced).toEqual([]);
  });

  it("says it cannot tell when a capped queue's API does not name the raised level", () => {
    const entry = pending({ id: "X", areaDesc: "Pipeline Road", raisedLevelKnown: false });
    expect(splitPending([], [entry], true).unplaced.map((p) => p.id)).toEqual(["X"]);
    // Uncapped, no listed row is not raised: the list is everything raised.
    expect(splitPending([], [entry], false).pendingOnly.map((p) => p.id)).toEqual(["X"]);
  });
});

describe("loadAlerts: a capped queue", () => {
  function served(extra: Record<string, unknown>, listed = 60) {
    return {
      run_id: "r",
      cycle_ts: CYCLE,
      alerts: Array.from({ length: listed }, (_, i) => ({ id: `a${i}`, level: "moderate" })),
      pending: [{ id: "p", level: "moderate", area_desc: "Pipeline Road", raised_level: "watch" }],
      ...extra,
    };
  }

  it("reads every raised alert by level, the first onset and the pending split", async () => {
    vi.stubGlobal("fetch", async () =>
      json(
        served({
          n_raised: 213,
          n_raised_by_level: { severe: 13, moderate: 35, watch: 165 },
          first_onset: { ts: "2019-07-02T08:45:00+05:30", name: "Step Marg", level: "watch" },
          n_pending: 175,
          n_pending_new: 109,
          n_pending_step_up: 66,
        }),
      ),
    );
    try {
      const set = (await loadAlerts())!;
      expect(set.nRaised).toBe(213);
      expect(set.nRaisedByLevel).toEqual({ severe: 13, moderate: 35, watch: 165 });
      expect(set.capped).toBe(true);
      expect(set.firstOnset).toEqual({
        ts: "2019-07-02T08:45:00+05:30",
        place: "Step Marg",
        level: "watch",
      });
      expect([set.nPendingNew, set.nPendingStepUp]).toEqual([109, 66]);
      expect(set.pending[0]).toMatchObject({ raisedLevel: "watch", raisedLevelKnown: true });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("treats a full list as capped when the API does not count what it left out", async () => {
    vi.stubGlobal("fetch", async () => json(served({ pending: [{ id: "p" }] })));
    try {
      const set = (await loadAlerts())!;
      expect(set.nRaised).toBeNull();
      expect(set.nRaisedByLevel).toBeNull();
      expect(set.capped).toBe(true);
      expect(set.firstOnset).toBeNull();
      expect(set.pending[0]).toMatchObject({ raisedLevel: null, raisedLevelKnown: false });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("ranks a cycle for the storm's peak on every alert raised, not the 60 listed", async () => {
    vi.stubGlobal("fetch", async () =>
      json({
        n_total: 13,
        n_raised: 213,
        n_raised_by_level: { severe: 13, moderate: 35, watch: 165 },
        alerts: [],
      }),
    );
    try {
      expect(await loadAlertCount("r")).toEqual({ total: 213, severe: 13, listedOnly: false });
    } finally {
      vi.unstubAllGlobals();
    }
    vi.stubGlobal("fetch", async () => json({ n_total: 60, alerts: [{ level: "severe" }] }));
    try {
      expect(await loadAlertCount("r")).toEqual({ total: 60, severe: 1, listedOnly: true });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("the queue's order and summary", () => {
  it("orders by when the water arrives, then by depth", () => {
    const late = runAlert({ id: "late", windowFrom: "2019-07-02T09:40:00+05:30", peakCm: 120 });
    const early = runAlert({ id: "early", windowFrom: "2019-07-02T09:05:00+05:30", peakCm: 50 });
    const earlyDeep = runAlert({ id: "deep", windowFrom: "2019-07-02T09:05:00+05:30", peakCm: 80 });
    expect(sortByOnset([late, early, earlyDeep]).map((a) => a.id)).toEqual([
      "deep",
      "early",
      "late",
    ]);
  });

  it("counts each level, what nobody has acknowledged, the deepest and the first onset", () => {
    const summary = summariseQueue(
      [
        runAlert({ id: "a", peakCm: 100 }),
        runAlert({ id: "b", level: "watch", peakCm: 20, windowFrom: "2019-07-02T09:05:00+05:30" }),
        runAlert({ id: "c", level: "watch", acknowledgedBy: "desk", state: "acknowledged" }),
      ],
      CYCLE,
    );
    expect(summary.counts).toEqual({ severe: 1, moderate: 0, watch: 2 });
    expect(summary.unacknowledged).toBe(2);
    expect(summary.worst?.id).toBe("a");
    expect(summary.firstOnset).toEqual({ ts: "2019-07-02T09:05:00+05:30", leadMin: 25 });
  });
});

describe("the CAP filename and the rendered messages", () => {
  it("names the file by cycle, place and level rather than the 80-character alert id", () => {
    expect(capFilename(CYCLE, runAlert({}))).toBe(
      "20190702-0840-sant-shitolebaba-maharaj-marg-severe.cap.xml",
    );
  });

  it("reads the API's WhatsApp and SMS text for one alert", async () => {
    vi.stubGlobal("fetch", async () =>
      json({
        run_id: "R",
        rows: [
          { id: "1", alert_id: "A", label: "WhatsApp mock", kind: "mock", text: "card" },
          { id: "2", alert_id: "A", label: "SMS mock", kind: "mock", text: "sms" },
          { id: "3", alert_id: "B", label: "SMS mock", kind: "mock", text: "other" },
          { id: "4", alert_id: "A", label: "Dashboard", kind: "mock" },
        ],
      }),
    );
    const log = await loadDelivery("R", undefined, 60);
    vi.unstubAllGlobals();
    expect(log.rows[3]!.text).toBeNull();
    expect(alertMessages(log.rows, "A")).toEqual({ whatsapp: "card", sms: "sms" });
  });
});

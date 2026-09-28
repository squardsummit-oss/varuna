import { afterEach, describe, expect, it, vi } from "vitest";

import { RainSkillLoadError, csiCurve, loadRainSkill, parseRainSkill } from "./verification-rain";

const cell = (csi: number | null, persistence: number | null) => ({
  event_pixels: 1819,
  base_rate: 0.386,
  mean: {
    hits: 1200,
    misses: 400,
    false_alarms: 459,
    correct_negatives: 2653,
    csi,
    pod: 0.75,
    far: 0.28,
  },
  p50: {
    hits: 1100,
    misses: 500,
    false_alarms: 400,
    correct_negatives: 2712,
    csi: 0.55,
    pod: 0.69,
    far: 0.27,
  },
  persistence: {
    hits: 1250,
    misses: 350,
    false_alarms: 386,
    correct_negatives: 2726,
    csi: persistence,
    pod: 0.78,
    far: 0.24,
  },
  brier: 0.08,
  brier_persistence: 0.1,
  brier_climatology: 0.19,
  brier_skill_vs_persistence: 0.16,
  brier_skill_vs_climatology: 0.45,
});

/** The shape `services/verify/varuna_verify/rain_event.py` serves, trimmed to two leads. */
const body = {
  event: "MUM-2019-07-02",
  available: true,
  label: "Reconstructed replay",
  truth: {
    source: "truth/rain.zarr",
    note: "Scored against the bundle's reconstructed truth field, not a measurement.",
    t0: "2019-07-02T05:40:00+05:30",
    t1: "2019-07-02T09:40:00+05:30",
    n_frames: 49,
  },
  units: { rain_rate: "mm/h", lead: "minutes after the cycle" },
  definitions: { csi: "Critical success index" },
  thresholds_mm_h: [10, 20, 40],
  headline_threshold_mm_h: 20,
  csi_floor: 0.5,
  lead_bands_min: [
    [5, 60],
    [65, 120],
    [125, 180],
  ],
  cycles: [
    {
      run_id: "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked",
      cycle_ts: "2019-07-02T06:40:00+05:30",
      n_members: 20,
      member_cube: true,
      products_source: "rain/quantiles.zarr",
      n_leads: 36,
      n_leads_scored: 36,
      max_lead_scored_min: 180,
      persistence: {
        frame_ts: "2019-07-02T06:40:00+05:30",
        zr_a: 137.8154,
        zr_b: 1.4106,
        zr_source: "adaptive",
        merge_method: "mfb+idw",
        matches_cycle: true,
      },
    },
  ],
  n_cycles: 1,
  by_scope: {
    aoi: {
      label: "Sky pixels (500 m) whose centres fall inside the city grid",
      n_pixels: 589,
      by_lead: [
        {
          lead_min: 10,
          n_cycles: 1,
          n_pixels: 589,
          mae_mm_h: { mean: 4.1, p50: 4.3, persistence: 5.2 },
          thresholds: { "20": cell(0.46, 0.49) },
        },
        {
          lead_min: 5,
          n_cycles: 1,
          n_pixels: 589,
          mae_mm_h: { mean: 3.1, p50: 3.3, persistence: 3.0 },
          thresholds: { "20": cell(0.58, 0.63) },
        },
      ],
      reliability: {
        "20": [
          {
            lead_from_min: 5,
            lead_to_min: 60,
            n: 1178,
            base_rate: 0.3,
            reliability: 0.01,
            resolution: 0.08,
            uncertainty: 0.21,
            bins: [{ p_from: 0, p_to: 0.1, n: 700, mean_p: 0.01, observed_frequency: 0.05 }],
          },
        ],
      },
      horizons: [
        {
          threshold_mm_h: 20,
          forecast: "mean",
          csi_floor: 0.5,
          lead_min: 0,
          status: "found",
          first_failure: {
            lead_min: 5,
            reasons: ["below_persistence"],
            csi: 0.58,
            persistence_csi: 0.63,
            n_cycles: 1,
          },
          beats_persistence_leads_min: [15, 20],
        },
      ],
      per_cycle: [
        {
          run_id: "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked",
          cycle_ts: "2019-07-02T06:40:00+05:30",
          leads_min: [5, 10],
          csi_mean_20: [0.58, null],
          csi_persistence_20: [0.63, 0.49],
        },
      ],
    },
    domain: {
      label: "Every pixel the radar covers on the 60 km Sky domain",
      n_pixels: 11304,
      by_lead: [],
      reliability: {},
      horizons: [],
    },
  },
  horizon: {
    threshold_mm_h: 20,
    forecast: "mean",
    csi_floor: 0.5,
    lead_min: 0,
    status: "found",
    first_failure: {
      lead_min: 5,
      reasons: ["below_persistence"],
      csi: 0.58,
      persistence_csi: 0.63,
      n_cycles: 1,
    },
    beats_persistence_leads_min: [15, 20],
  },
  runs_without_member_cube: [],
  skipped_runs: [],
  unavailable: {},
  provenance: { generator: "varuna_verify.rain_event" },
  notes: ["Pooled over 1 baked cycle."],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseRainSkill", () => {
  it("keeps every served number and orders the leads", () => {
    const skill = parseRainSkill(body, "MUM-2019-07-02");
    if (!skill.available) throw new Error("expected a scored body");
    const aoi = skill.byScope.aoi;
    expect(aoi.byLead.map((row) => row.leadMin)).toEqual([5, 10]);
    expect(aoi.byLead[0]?.thresholds["20"]?.forecasts.mean.csi).toBe(0.58);
    expect(aoi.byLead[0]?.thresholds["20"]?.forecasts.persistence.falseAlarms).toBe(386);
    expect(aoi.byLead[0]?.thresholds["20"]?.eventPixels).toBe(1819);
    expect(aoi.reliability["20"]?.[0]?.bins[0]?.observedFrequency).toBe(0.05);
    expect(aoi.perCycle[0]?.csiMean20).toEqual([0.58, null]);
    expect(skill.cycles[0]?.persistence.matchesCycle).toBe(true);
    expect(skill.leadBandsMin[1]).toEqual([65, 120]);
    expect(skill.byScope.domain.perCycle).toEqual([]);
  });

  it("carries the computed horizon, including a first-lead failure", () => {
    const skill = parseRainSkill(body, "MUM-2019-07-02");
    if (!skill.available) throw new Error("expected a scored body");
    expect(skill.horizon?.leadMin).toBe(0);
    expect(skill.horizon?.status).toBe("found");
    expect(skill.horizon?.firstFailure?.reasons).toEqual(["below_persistence"]);
    expect(skill.horizon?.beatsPersistenceLeadsMin).toEqual([15, 20]);
  });

  it("reads the cycle-to-cycle spread, and none from a scorer that served none", () => {
    const withSpread = {
      ...body,
      by_scope: {
        ...body.by_scope,
        aoi: {
          ...body.by_scope.aoi,
          by_lead: [
            {
              ...body.by_scope.aoi.by_lead[1],
              thresholds: {
                "20": {
                  ...cell(0.58, 0.63),
                  spread: {
                    mean: {
                      csi: { n: 5, p10: 0.3484, p90: 0.7486 },
                      pod: { n: 5, p10: 0.5, p90: 0.9 },
                      far: { n: 2, p10: null, p90: null },
                    },
                  },
                },
              },
            },
          ],
        },
      },
    };
    const skill = parseRainSkill(withSpread, "MUM-2019-07-02");
    if (!skill.available) throw new Error("expected a scored body");
    const spread = skill.byScope.aoi.byLead[0]?.thresholds["20"]?.spread;
    expect(spread?.mean?.csi).toEqual({ n: 5, p10: 0.3484, p90: 0.7486 });
    expect(spread?.mean?.far).toEqual({ n: 2, p10: null, p90: null });
    expect(spread?.p50).toBeUndefined();
    const old = parseRainSkill(body, "MUM-2019-07-02");
    if (!old.available) throw new Error("expected a scored body");
    expect(old.byScope.aoi.byLead[0]?.thresholds["20"]?.spread).toEqual({});
  });

  it("passes an unavailable answer through with its reason", () => {
    const skill = parseRainSkill(
      { event: "CHN-IDF-25yr", available: false, reason: "No run keeps rain products." },
      "CHN-IDF-25yr",
    );
    expect(skill).toEqual({
      available: false,
      event: "CHN-IDF-25yr",
      reason: "No run keeps rain products.",
      missing: null,
      command: null,
    });
  });

  it("draws a curve with nulls where a lead has no score", () => {
    const skill = parseRainSkill(body, "MUM-2019-07-02");
    if (!skill.available) throw new Error("expected a scored body");
    expect(csiCurve(skill.byScope.aoi, 20, "persistence")).toEqual([
      { leadMin: 5, csi: 0.63, nCycles: 1, eventPixels: 1819 },
      { leadMin: 10, csi: 0.49, nCycles: 1, eventPixels: 1819 },
    ]);
    expect(csiCurve(skill.byScope.aoi, 40, "mean")[0]?.csi).toBeNull();
  });
});

describe("loadRainSkill", () => {
  it("throws the API's own message on an error envelope, not as unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { error: { code: "no_bundle", message: "No bundle X. Run make bundle BUNDLE=X." } },
          { status: 404 },
        ),
      ),
    );
    const failure = await loadRainSkill("X").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(RainSkillLoadError);
    expect((failure as RainSkillLoadError).message).toBe("No bundle X. Run make bundle BUNDLE=X.");
    expect((failure as RainSkillLoadError).unreachable).toBe(false);
  });

  it("turns the browser's network failure into a sentence that says the API is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const failure = await loadRainSkill("MUM-2019-07-02").catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(RainSkillLoadError);
    expect((failure as RainSkillLoadError).message).toBe("The API is unreachable.");
    expect((failure as RainSkillLoadError).unreachable).toBe(true);
  });

  it("counts a proxy's 502 as unreachable and a bare 500 as a failure of the API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("Bad gateway", { status: 502 })),
    );
    const gateway = (await loadRainSkill("X").catch((e: unknown) => e)) as RainSkillLoadError;
    expect(gateway.unreachable).toBe(true);
    expect(gateway.message).toBe("The API answered 502.");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 500 })),
    );
    const broken = (await loadRainSkill("X").catch((e: unknown) => e)) as RainSkillLoadError;
    expect(broken.unreachable).toBe(false);
    expect(broken.message.startsWith("The API answered 500 without a rain skill.")).toBe(true);
  });

  it("lets an abort through as the browser raised it", async () => {
    const controller = new AbortController();
    const abort = new DOMException("The operation was aborted.", "AbortError");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        controller.abort();
        throw abort;
      }),
    );
    await expect(loadRainSkill("X", controller.signal)).rejects.toBe(abort);
  });

  it("parses the served command and what is missing for an unscored event", () => {
    const answer = parseRainSkill(
      {
        event: "X",
        available: false,
        reason: "No run of X keeps rain products (rain/quantiles.zarr or rain/cube.zarr).",
        missing: "runs",
        command: "make bake BUNDLE=X",
      },
      "X",
    );
    expect(answer).toEqual({
      available: false,
      event: "X",
      reason: "No run of X keeps rain products (rain/quantiles.zarr or rain/cube.zarr).",
      missing: "runs",
      command: "make bake BUNDLE=X",
    });
    const older = parseRainSkill({ event: "X", available: false, reason: "Old." }, "X");
    expect(older).toMatchObject({ missing: null, command: null });
  });

  it("asks the rain-skill path for the event", async () => {
    const fetchMock = vi.fn<(input: string) => Promise<Response>>(async () => Response.json(body));
    vi.stubGlobal("fetch", fetchMock);
    const skill = await loadRainSkill("MUM-2019-07-02");
    expect(skill.available).toBe(true);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "/v1/verification/rain-skill?event=MUM-2019-07-02",
    );
  });
});

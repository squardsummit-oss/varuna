/**
 * The decisions the wizard makes that are worth pinning: where the finish card lands, how far the
 * build has got (which decides when each map layer is asked for), how the API's per-step records
 * become rows, which step the lead-time scrub opens on, and where the finish card's numbers come
 * from when the API did not summarise the run.
 */

import { describe, expect, it } from "vitest";

import type { OnboardJob, OnboardSteps } from "@/lib/api/onboard";
import type { RunDepth } from "@/lib/api/run-depth";
import { formatDateTime } from "@/lib/format";
import {
  cityName,
  consoleHref,
  finishFacts,
  leadStepIndex,
  previousBuildLabel,
  reachedStepIndex,
  stageSum,
  stepLeads,
  stepsFromRecords,
  toLines,
} from "./onboard-screen";

const RUN = "CHN-20260701T0040Z-sky1.0-twin1.0-flash0.0-baked";

function job(over: Partial<OnboardJob>): OnboardJob {
  return {
    jobId: "job-1",
    city: "chennai",
    status: "running",
    step: "choose_area",
    progress: 0,
    startedAt: null,
    finishedAt: null,
    elapsedS: 0,
    logTail: [],
    log: null,
    logTotal: null,
    steps: null,
    forecast: null,
    firstRunId: null,
    error: null,
    failedStep: null,
    designStorm: null,
    fromRecord: false,
    built: false,
    previous: null,
    lastAttempt: null,
    ...over,
  };
}

describe("consoleHref", () => {
  it("lands on a Chennai console showing this build's own first run", () => {
    expect(consoleHref("chennai", RUN)).toBe(`/console?city=chennai&run=${RUN}`);
  });

  it("still names the city when the build produced no run to name", () => {
    expect(consoleHref("chennai", null)).toBe("/console?city=chennai");
  });
});

describe("cityName", () => {
  it("names the city from its slug", () => {
    expect(cityName("chennai")).toBe("Chennai");
    expect(cityName("navi-mumbai")).toBe("Navi Mumbai");
  });
});

describe("reachedStepIndex", () => {
  it("is -1 before a build has started, so no layer is asked for", () => {
    expect(reachedStepIndex(null)).toBe(-1);
    expect(reachedStepIndex(job({ status: "none" }))).toBe(-1);
  });

  it("counts only the steps that have finished, not the one running", () => {
    // Running "Fetch open data" (index 1) means only "Choose area" has written anything.
    expect(reachedStepIndex(job({ step: "fetch_open_data" }))).toBe(1);
    expect(reachedStepIndex(job({ step: "infer_drains" }))).toBe(3);
  });

  it("counts a finished or already-built city as past every step", () => {
    expect(reachedStepIndex(job({ status: "finished", step: "first_forecast" }))).toBe(6);
    // A city built in an earlier session reports no job at all - jobs do not outlive the API
    // process - and its layers are still on disk, so the map must draw them.
    expect(reachedStepIndex(job({ status: "none", built: true, jobId: null }))).toBe(6);
  });
});

describe("stepsFromRecords", () => {
  const steps: OnboardSteps = {
    choose_area: {
      status: "done",
      ms: 18.2,
      detail: "CHN-SOUTH, 291 x 334 cells at 30 m, EPSG:32644",
      loadedFromDisk: false,
      progress: 1,
      stages: null,
    },
    fetch_open_data: {
      status: "done",
      ms: 1412.6,
      detail: null,
      loadedFromDisk: true,
      progress: 1,
      stages: null,
    },
    infer_drains: {
      status: "running",
      ms: 2100,
      detail: null,
      loadedFromDisk: false,
      progress: 0.5,
      stages: null,
    },
    first_forecast: {
      status: "waiting",
      ms: 0,
      detail: null,
      loadedFromDisk: false,
      progress: 0,
      stages: {
        sky: { status: "waiting", ms: null },
        flash: { status: "skipped", ms: 0 },
      },
    },
  };

  it("keeps each step's own milliseconds and says which only loaded from disk", () => {
    const rows = stepsFromRecords(steps);
    expect(rows.map((row) => row.status)).toEqual([
      "done",
      "loaded",
      "waiting",
      "running",
      "waiting",
      "waiting",
    ]);
    expect(rows[0].elapsedMs).toBe(18.2);
    expect(rows[0].detail).toMatch(/CHN-SOUTH/);
    expect(rows[1].progress).toBe(100);
    expect(rows[3]).toMatchObject({ progress: 50, elapsedMs: 2100 });
    // A waiting row carries no time, even when the record holds a zero.
    expect(rows[5].elapsedMs).toBeNull();
    // The forecast's stages, in the cycle's order, named as the row prints them.
    expect(rows[5].stages?.map((stage) => stage.label)).toEqual(["Sky", "Flash"]);
  });
});

describe("toLines", () => {
  it("prints each line with the time the API captured it", () => {
    const lines = toLines(
      job({
        log: [
          { ts: "2026-09-26T03:13:44.120+05:30", text: "city.step step=osm", level: "info" },
          { ts: "2026-09-26T03:13:46.500+05:30", text: "Failed: disk full", level: "error" },
        ],
        logTail: ["city.step step=osm", "Failed: disk full"],
      }),
    );
    expect(lines).toEqual([
      { ts: "2026-09-26T03:13:44.120+05:30", text: "city.step step=osm", level: "info" },
      { ts: "2026-09-26T03:13:46.500+05:30", text: "Failed: disk full", level: "error" },
    ]);
  });

  it("gives an older API's bare lines no time rather than the job's start on every line", () => {
    const lines = toLines(job({ startedAt: "2026-09-26T03:13:40+05:30", logTail: ["a", "b"] }));
    expect(lines.map((line) => line.ts)).toEqual(["", ""]);
    expect(lines.map((line) => line.text)).toEqual(["a", "b"]);
  });

  it("shows a recorded build's own lines when no job is running", () => {
    const lines = toLines(
      job({
        status: "none",
        jobId: null,
        previous: {
          jobId: "onboard-chennai-1a2b3c4d",
          status: "finished",
          designStorm: "CHN-IDF-25yr",
          startedAt: "2026-09-26T03:13:43+05:30",
          finishedAt: null,
          elapsedS: 51.2,
          firstRunId: RUN,
          firstRunExists: true,
          seeded: false,
          error: null,
          failedStep: null,
          steps: null,
          forecast: null,
          log: [{ ts: "2026-09-26T03:13:44+05:30", text: "city.start", level: "info" }],
        },
      }),
    );
    expect(lines).toHaveLength(1);
    expect(lines[0].text).toBe("city.start");
  });
});

describe("previousBuildLabel", () => {
  it("says when the recorded build ran and how long it took", () => {
    expect(
      previousBuildLabel({
        jobId: "onboard-chennai-1a2b3c4d",
        status: "finished",
        designStorm: null,
        startedAt: "2026-09-26T03:13:43+05:30",
        finishedAt: null,
        elapsedS: 51.2,
        firstRunId: null,
        firstRunExists: null,
        seeded: false,
        error: null,
        failedStep: null,
        steps: null,
        forecast: null,
        log: [],
      }),
    ).toBe(`Previous build, ${formatDateTime("2026-09-26T03:13:43+05:30")}, 51 s`);
  });
});

describe("the lead-time scrub", () => {
  // T0040Z's own steps: cycle 06:10 IST, the first valid time 06:15, every five minutes.
  const validTs = Array.from({ length: 31 }, (_, i) => {
    const minutes = 6 * 60 + 15 + i * 5;
    const hh = String(Math.floor(minutes / 60)).padStart(2, "0");
    const mm = String(minutes % 60).padStart(2, "0");
    return `2026-07-01T${hh}:${mm}:00+05:30`;
  });

  it("counts each step's lead from the run's cycle", () => {
    const leads = stepLeads(validTs, "2026-07-01T06:10:00+05:30");
    expect(leads[0]).toBe(5);
    expect(leads[30]).toBe(155);
  });

  it("opens on the step an hour ahead", () => {
    const leads = stepLeads(validTs, "2026-07-01T06:10:00+05:30");
    const step = leadStepIndex(leads);
    expect(leads[step]).toBe(60);
    expect(validTs[step]).toBe("2026-07-01T07:10:00+05:30");
  });

  it("counts from a step before the first when the run has no cycle time", () => {
    expect(stepLeads(validTs.slice(0, 3), null, 5)).toEqual([5, 10, 15]);
  });

  it("opens a run shorter than an hour on its last step, and an empty one on 0", () => {
    expect(leadStepIndex([5, 10, 15])).toBe(2);
    expect(leadStepIndex([])).toBe(0);
  });
});

describe("finishFacts", () => {
  function depth(depthCm: Record<string, number[]>, stageMs: Record<string, number>): RunDepth {
    return {
      provenance: {
        runId: RUN,
        cycleTs: "2026-07-01T06:10:00+05:30",
        mode: "baked",
        bundle: "CHN-IDF-25yr",
        nSteps: 3,
        stepMin: 5,
        ensembleN: 1,
        massBalanceErr: null,
        stageMs,
        aoiDepthBand: null,
        notes: [],
      },
      bounds: [80.2, 12.96, 80.28, 13.05],
      frames: [],
      depthCm: new Map(Object.entries(depthCm)),
      pGt: null,
      validTs: [],
      nSegmentsTotal: 18_622,
    };
  }

  it("prefers the API's summary of the run", () => {
    const facts = finishFacts(
      RUN,
      {
        runId: RUN,
        cycleTs: null,
        streetsTotal: 18_622,
        wetStreets: 15_472,
        wetThresholdCm: 5,
        medianPeakCm: 26,
        maxPeakCm: 240,
        stageMs: { sky: 135, twin: 42_859, pulse: 981, flash: 0, products: 2462 },
        forecastMs: 46_437,
        storm: {
          id: "CHN-IDF-25yr",
          totalMm: 150,
          durationMin: 180,
          peakMmH: 448.8,
          source: "manifest",
        },
      },
      null,
      null,
      46_512.3,
    );
    expect(facts).toMatchObject({
      wetStreets: 15_472,
      streetsTotal: 18_622,
      medianPeakCm: 26,
      // The first forecast row's own wall time, so the card prints what the row beside it does.
      forecastMs: 46_512.3,
      // And `run.json`'s stage sum, named by what it adds up. Flash at 0 ms did not run.
      stagesMs: 46_437,
      stages: ["Sky", "Twin", "Pulse", "products"],
      storm: { totalMm: 150, source: "manifest" },
    });
  });

  it("never passes a stage sum off as the forecast's wall time", () => {
    // Chennai's recorded build (onboard-chennai-0541eeaa): the row read 6 min 06 s and the card,
    // which printed `run.json`'s total_ms, read 5 min 30 s beside it.
    const stageMs = { sky: 8101, twin: 315_416, pulse: 2863, flash: 0, products: 3138 };
    expect(stageSum(stageMs)).toEqual({
      ms: 329_518,
      stages: ["Sky", "Twin", "Pulse", "products"],
    });
    const facts = finishFacts(
      RUN,
      {
        runId: RUN,
        cycleTs: null,
        streetsTotal: null,
        wetStreets: null,
        wetThresholdCm: null,
        medianPeakCm: null,
        maxPeakCm: null,
        stageMs,
        forecastMs: 329_518,
        storm: null,
      },
      null,
      null,
      365_738.9,
    );
    expect(facts?.forecastMs).toBe(365_738.9);
    expect(facts?.stagesMs).toBe(329_518);
  });

  it("computes the same numbers from the run the map loaded when there is no summary", () => {
    const facts = finishFacts(
      RUN,
      null,
      depth(
        { a: [0, 12, 30], b: [5, 20, 10], c: [40, 50, 60] },
        {
          sky: 135,
          twin: 42_859,
          twin_surface_ms: 7599,
          pulse: 981,
          flash: 0,
          products: 2462,
        },
      ),
      { totalMm: 142.6, durationMin: 155, peakMmH: 448.8 },
    );
    expect(facts).toMatchObject({
      wetStreets: 3,
      streetsTotal: 18_622,
      // Peaks 30, 20 and 60: the median is 30.
      medianPeakCm: 30,
      // No build recorded this run's first-forecast step, so there is no wall time to print.
      forecastMs: null,
      // The five stages only: the Twin's sub-timings are inside its own number.
      stagesMs: 46_437,
      stages: ["Sky", "Twin", "Pulse", "products"],
      storm: { id: "CHN-IDF-25yr", totalMm: 142.6, source: "run" },
    });
  });

  it("has nothing to say before either has arrived", () => {
    expect(finishFacts(RUN, null, null, null)).toBeNull();
  });
});

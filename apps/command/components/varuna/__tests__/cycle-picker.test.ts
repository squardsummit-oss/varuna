import { describe, expect, it } from "vitest";

import { newestPerCycle, type BakedCycle } from "../cycle-picker";

/**
 * The picker's one-chip-per-cycle rule, pinned after the P10.2 design QA found it broken
 * (2026-09-24).
 *
 * The row was drawing a chip per *run*, so `/pumps`, `/alerts` and the console showed
 * "06:00 06:00 06:10 06:10 06:40 06:40 07:10 07:10 07:40 07:40 08:10 08:10 08:10 08:40 09:10
 * 09:10 09:10" - seventeen chips for eight cycles, three of them identical. The keys were unique
 * so React never complained; the reader was the only one who could see the problem.
 *
 * These rows are the registry's own, trimmed: the 2026-09-12 flash0.0 bake, the 2026-09-23
 * flash0.1 re-bake and a live run, all at 08:10 IST.
 */
const RUNS: BakedCycle[] = [
  {
    runId: "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.0-baked",
    cycleTs: "2019-07-02T08:10:00+05:30",
    massBalanceErr: 0.00091,
    createdAt: "2026-09-12T22:40:16.530057+05:30",
  },
  {
    runId: "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.1-live",
    cycleTs: "2019-07-02T08:10:00+05:30",
    massBalanceErr: 0.00091,
    createdAt: "2026-09-13T19:07:36.185514+05:30",
  },
  {
    runId: "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.1-baked",
    cycleTs: "2019-07-02T08:10:00+05:30",
    massBalanceErr: 0.00091,
    createdAt: "2026-09-23T10:30:30.983724+05:30",
  },
  {
    runId: "MUM-20190702T0030Z-sky1.0-twin1.0-flash0.1-baked",
    cycleTs: "2019-07-02T06:00:00+05:30",
    massBalanceErr: 0.00097,
    createdAt: "2026-09-23T10:30:30.983724+05:30",
  },
];

describe("newestPerCycle", () => {
  it("keeps one run per cycle time, the most recently written", () => {
    const kept = newestPerCycle(RUNS);
    expect(kept.map((c) => c.cycleTs)).toEqual([
      "2019-07-02T06:00:00+05:30",
      "2019-07-02T08:10:00+05:30",
    ]);
    expect(kept[1].runId).toBe("MUM-20190702T0240Z-sky1.0-twin1.0-flash0.1-baked");
  });

  it("orders cycles oldest first, so the row reads as the storm's timeline", () => {
    const times = newestPerCycle([...RUNS].reverse()).map((c) => c.cycleTs);
    expect(times).toEqual([...times].sort());
  });

  it("is deterministic when two runs of a cycle were written at the same instant", () => {
    const tie: BakedCycle[] = [
      { ...RUNS[0], runId: "a-run", createdAt: "2026-09-23T10:30:30+05:30" },
      { ...RUNS[0], runId: "b-run", createdAt: "2026-09-23T10:30:30+05:30" },
    ];
    expect(newestPerCycle(tie)[0].runId).toBe("b-run");
    expect(newestPerCycle([...tie].reverse())[0].runId).toBe("b-run");
  });

  it("never lets a run with no created_at displace one that has it", () => {
    const undated: BakedCycle = { ...RUNS[0], runId: "undated", createdAt: "" };
    expect(newestPerCycle([RUNS[2], undated])[0].runId).toBe(RUNS[2].runId);
    expect(newestPerCycle([undated, RUNS[2]])[0].runId).toBe(RUNS[2].runId);
  });

  it("keeps the run on screen for its own cycle, so a chip reads as selected", () => {
    // `/pumps` opens on the 09:10 live run while the registry's newest at 09:10 is the re-bake.
    // Dropping the live run leaves the row with no selected chip, which says nothing about where
    // the reader is - worse than showing a run written ten days earlier.
    const live = "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.1-live";
    const kept = newestPerCycle(RUNS, live);
    expect(kept.find((c) => c.cycleTs === "2019-07-02T08:10:00+05:30")?.runId).toBe(live);
    // And the cycle the current run is not in is unaffected.
    expect(kept.find((c) => c.cycleTs === "2019-07-02T06:00:00+05:30")?.runId).toBe(RUNS[3].runId);
  });

  it("does not depend on where the current run sits in the registry's order", () => {
    const live = "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.1-live";
    const forwards = newestPerCycle(RUNS, live).map((c) => c.runId);
    const backwards = newestPerCycle([...RUNS].reverse(), live).map((c) => c.runId);
    expect(backwards).toEqual(forwards);
  });
});

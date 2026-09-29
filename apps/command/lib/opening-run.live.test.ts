import { describe, expect, it } from "vitest";

import { splitLive } from "@/components/varuna/cycle-picker";
import { isLiveRun, newestLiveRunId, openingRunId } from "@/lib/opening-run";

const NOW = Date.parse("2026-09-30T15:10:00+05:30");
const RUNS = [
  {
    run_id: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
    cycle_ts: "2019-07-02T08:40:00+05:30",
    bundle: "MUM-2019-07-02",
  },
  {
    run_id: "MUM-20260930T0905Z-sky1.0-twin1.0-flash0.1-live",
    cycle_ts: "2026-09-30T14:35:00+05:30",
    bundle: "MUM-LIVE",
  },
  {
    run_id: "MUM-20260930T0835Z-sky1.0-twin1.0-flash0.1-live",
    cycle_ts: "2026-09-30T14:05:00+05:30",
    bundle: "MUM-LIVE",
  },
];

describe("live runs", () => {
  it("knows a live cycle by the folder it was forced from", () => {
    expect(isLiveRun({ bundle: "MUM-LIVE" })).toBe(true);
    expect(isLiveRun({ bundle: "MUM-2019-07-02" })).toBe(false);
    expect(isLiveRun({ bundle: null })).toBe(false);
  });

  it("opens on the newest live cycle while it is fresh, and on the replay once it is not", () => {
    expect(newestLiveRunId(RUNS, NOW)).toBe("MUM-20260930T0905Z-sky1.0-twin1.0-flash0.1-live");
    expect(newestLiveRunId(RUNS, NOW + 4 * 3_600_000)).toBeUndefined();
    expect(openingRunId(RUNS, "2019-07-02T08:40:00+05:30")).toBe(RUNS[0]!.run_id);
  });

  it("keeps live cycles out of the replay's row of clock times", () => {
    const rows = RUNS.map((r) => ({
      runId: r.run_id,
      cycleTs: r.cycle_ts,
      massBalanceErr: null,
      createdAt: "",
      bundle: r.bundle,
    }));
    const split = splitLive(rows);
    expect(split.replay.map((r) => r.runId)).toEqual([RUNS[0]!.run_id]);
    expect(split.live?.runId).toBe(RUNS[1]!.run_id);
  });
});

import { describe, expect, it } from "vitest";

import { openingRunId } from "@/lib/opening-run";
import { DEFAULT_SIM_TIME } from "@/lib/stores/replay";

const RUNS = [
  {
    run_id: "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked",
    cycle_ts: "2019-07-02T09:10:00+05:30",
  },
  {
    run_id: "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked",
    cycle_ts: "2019-07-02T06:40:00+05:30",
  },
  {
    run_id: "MUM-20190702T0040Z-sky1.0-twin1.0-flash0.1-baked",
    cycle_ts: "2019-07-02T06:10:00+05:30",
  },
];

describe("openingRunId", () => {
  it("picks the 06:40 cycle the demo script opens on, not the newest", () => {
    expect(openingRunId(RUNS, DEFAULT_SIM_TIME)).toBe(
      "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked",
    );
  });

  it("matches the instant whatever offset the cycle time is written in", () => {
    const utc = [{ run_id: "utc", cycle_ts: "2019-07-02T01:10:00Z" }];
    expect(openingRunId(utc, "2019-07-02T06:40:00+05:30")).toBe("utc");
  });

  it("keeps the API's default when no baked run sits at the opening", () => {
    expect(openingRunId(RUNS.slice(0, 1), DEFAULT_SIM_TIME)).toBeUndefined();
    expect(openingRunId([], DEFAULT_SIM_TIME)).toBeUndefined();
    expect(openingRunId(RUNS, "not a time")).toBeUndefined();
  });
});

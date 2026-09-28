import { describe, expect, it } from "vitest";

import { CYCLE_STAGES } from "@/lib/api/schemas";
import { totalStageMs, type RunMeta } from "@/lib/stores/run";

function runWith(stage_ms: Record<string, number> | undefined): RunMeta {
  return {
    run_id: "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked",
    city: "mumbai",
    cycle_ts: "2019-07-02T09:10:00+05:30",
    mode: "replay",
    replay_mode: "baked",
    stage_ms,
  };
}

describe("totalStageMs", () => {
  it("counts each cycle stage once and ignores the Twin's sub-timings (ADR-0046)", () => {
    // run.json carries the Twin's wall clock as `twin` and five timings measured inside it.
    // Summing every key showed 189.7 s on the run stamp for a 76.9 s cycle; the API already
    // counts only CYCLE_STAGES, and the stamp has to say the same number.
    const run = runWith({
      sky: 5592,
      twin: 58282,
      twin_total_ms: 58282,
      twin_surface_ms: 30000,
      twin_drain_ms: 20000,
      twin_coupling_ms: 5000,
      twin_hydrology_ms: 700,
      flash: 226,
      products: 9000,
      pulse: 3751,
    });
    expect(totalStageMs(run)).toBe(5592 + 58282 + 226 + 9000 + 3751);
  });

  it("does not let an unknown key or a cycle-level total inflate the sum", () => {
    expect(totalStageMs(runWith({ sky: 1000, twin: 2000, total: 3000, twin_new_ms: 900 }))).toBe(
      3000,
    );
  });

  it("is null when the run has no stage timings to add", () => {
    expect(totalStageMs(null)).toBeNull();
    expect(totalStageMs(runWith(undefined))).toBeNull();
    expect(totalStageMs(runWith({ twin_total_ms: 58282 }))).toBeNull();
  });

  it("uses the same stage list as the API schema", () => {
    // The Python rule is varuna_schemas.constants.CYCLE_STAGES; lib/api/schemas.ts mirrors it.
    expect([...CYCLE_STAGES]).toEqual([
      "decode",
      "sky",
      "twin",
      "flash",
      "pulse",
      "products",
      "route",
      "alerts",
      "publish",
    ]);
  });
});

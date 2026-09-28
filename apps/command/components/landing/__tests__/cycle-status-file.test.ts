import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { readCycleStatus } from "@/components/landing/cycle-timings";

/**
 * The landing page's offline timings must be a real run's, and the run it names must still be the
 * newest one shipped. `tools/write_cycle_status.py` writes the file; this fails when `demo/runs`
 * is re-baked and nobody re-ran it, which would otherwise leave the landing quoting a replaced run.
 */
const file = path.resolve(__dirname, "../../../public/cycle-status.json");
const runs = `${path.resolve(__dirname, "../../../../../demo/runs")}/`;

const committed = JSON.parse(readFileSync(file, "utf8")) as {
  run_id: string;
  stage_ms: Record<string, number>;
  cycle_ts: string;
  bundle: string;
  fallback: { written_by: string; from_run_json: string };
};

describe("public/cycle-status.json", () => {
  it("reads as a status the landing can show", () => {
    expect(readCycleStatus(committed, "committed")?.runId).toBe(committed.run_id);
    expect(committed.fallback.written_by).toBe("tools/write_cycle_status.py");
  });

  it.skipIf(!existsSync(runs))("is the newest demo run, copied exactly", () => {
    const newest = readdirSync(runs)
      .filter((name) => name.startsWith("MUM-") && existsSync(`${runs}${name}/run.json`))
      .sort()
      .at(-1);
    expect(committed.run_id, "re-run: uv run python tools/write_cycle_status.py").toBe(newest);
    const run = JSON.parse(readFileSync(`${runs}${newest}/run.json`, "utf8")) as {
      stage_ms: Record<string, number>;
      cycle_ts: string;
      bundle: string;
    };
    expect(committed.stage_ms).toEqual(run.stage_ms);
    expect(committed.cycle_ts).toBe(run.cycle_ts);
    expect(committed.bundle).toBe(run.bundle);
    expect(committed.fallback.from_run_json).toBe(`demo/runs/${newest}/run.json`);
  });
});

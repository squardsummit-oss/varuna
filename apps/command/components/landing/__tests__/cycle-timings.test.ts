import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  CYCLE_BEAMS,
  CYCLE_HOPS,
  CYCLE_NODES,
  cycleTotalMs,
  loadCycleTimings,
  nodeTiming,
  readCycleStatus,
  timingLabel,
} from "@/components/landing/cycle-timings";
import { beamSegment } from "@/components/ui/animated-beam";
import { CYCLE_STAGES } from "@/lib/api/schemas";
import { formatMs } from "@/lib/format";
import { totalStageMs, type RunMeta } from "@/lib/stores/run";

/** The committed fallback, which carries a real baked run's stage_ms, Twin sub-timings included. */
const committed = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../public/cycle-status.json"), "utf8"),
) as { run_id: string; stage_ms: Record<string, number>; budget_ms: Record<string, number> };

const node = (id: string) => {
  const found = CYCLE_NODES.find((n) => n.id === id);
  if (!found) throw new Error(`no node ${id}`);
  return found;
};

describe("the node-to-stage mapping", () => {
  it("puts every cycle stage in exactly one node, so no stage is counted twice or dropped", () => {
    const mapped = CYCLE_NODES.flatMap((n) => [...n.stages]);
    expect([...mapped].sort()).toEqual([...CYCLE_STAGES].sort());
    expect(new Set(mapped).size).toBe(mapped.length);
  });

  it("numbers the nodes as a sequence and connects every node", () => {
    expect(CYCLE_NODES.map((n) => n.n)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    const touched = new Set(CYCLE_BEAMS.flatMap((b) => [b.from, b.to]));
    expect(touched.size).toBe(CYCLE_NODES.length);
    expect(CYCLE_HOPS).toBe(5);
  });

  it("gives the Twin its own wall clock and never its sub-timings", () => {
    const twin = nodeTiming(node("twin"), committed.stage_ms, committed.budget_ms);
    expect(committed.stage_ms.twin_total_ms).toBeGreaterThan(0);
    expect(twin.ms).toBe(committed.stage_ms.twin);
    expect(twin.budgetMs).toBe(committed.budget_ms.twin);
    expect(timingLabel(twin)).toBe(formatMs(committed.stage_ms.twin));
  });

  it("says not timed, never a number, when a node's stages are absent", () => {
    // The baked runs time neither decode nor route, alerts and publish.
    for (const id of ["ingest", "outputs"]) {
      const timing = nodeTiming(node(id), committed.stage_ms, committed.budget_ms);
      expect(timing.ms).toBeNull();
      expect(timingLabel(timing)).toBe("Not timed");
    }
    expect(timingLabel(nodeTiming(node("sky"), {}))).toBe("Not timed");
  });

  it("marks a node whose stages were only partly timed", () => {
    const timing = nodeTiming(node("outputs"), { route: 1200 }, { route: 2000, alerts: 500 });
    expect(timing.ms).toBe(1200);
    expect(timing.untimed).toEqual(["alerts", "publish"]);
    // Two of three budgets is not the node's budget.
    expect(timing.budgetMs).toBeNull();
    expect(timingLabel(timing)).toBe(`${formatMs(1200)}, partly timed`);
  });

  it("totals the cycle the way the run stamp and the API do", () => {
    const everyKey = Object.values(committed.stage_ms).reduce((a, b) => a + b, 0);
    const total = cycleTotalMs(committed.stage_ms);
    const stamp = totalStageMs({ stage_ms: committed.stage_ms } as unknown as RunMeta);
    expect(total).toBe(stamp);
    expect(total).toBeLessThan(everyKey);
    expect(cycleTotalMs({ twin_total_ms: 5 })).toBeNull();
  });
});

describe("readCycleStatus", () => {
  it("reads the endpoint's shape and drops values that are not numbers", () => {
    const timings = readCycleStatus(
      {
        run_id: "r",
        stage_ms: { sky: 5, twin: "slow" },
        budget_ms: { sky: 9 },
        total_budget_ms: 15,
      },
      "live",
    );
    expect(timings).toMatchObject({ runId: "r", stageMs: { sky: 5 }, totalBudgetMs: 15 });
  });

  it("is null when there is no run or no timing to show", () => {
    expect(readCycleStatus({ run_id: null, stage_ms: {} }, "live")).toBeNull();
    expect(readCycleStatus({ run_id: "r", stage_ms: {} }, "live")).toBeNull();
    expect(readCycleStatus("<html>", "live")).toBeNull();
    expect(readCycleStatus(null, "committed")).toBeNull();
  });
});

describe("loadCycleTimings", () => {
  const json = (body: unknown, ok = true) =>
    Promise.resolve({ ok, json: () => Promise.resolve(body) } as Response);

  it("prefers the API", async () => {
    const calls: string[] = [];
    const fetchImpl = ((url: string) => {
      calls.push(url);
      return json({ run_id: "live-run", stage_ms: { sky: 1 } });
    }) as unknown as typeof fetch;
    const timings = await loadCycleTimings(undefined, fetchImpl);
    expect(timings?.source).toBe("live");
    expect(calls).toHaveLength(1);
  });

  it("falls back to the committed copy when the API is down or has no run", async () => {
    for (const live of [Promise.reject(new TypeError("offline")), json({ run_id: null })]) {
      const fetchImpl = ((url: string) =>
        url.includes("/v1/cycle/status") ? live : json(committed)) as unknown as typeof fetch;
      const timings = await loadCycleTimings(undefined, fetchImpl);
      expect(timings?.source).toBe("committed");
      expect(timings?.runId).toBe(committed.run_id);
    }
  });

  it("is null when neither answers", async () => {
    const fetchImpl = (() => Promise.reject(new TypeError("offline"))) as unknown as typeof fetch;
    expect(await loadCycleTimings(undefined, fetchImpl)).toBeNull();
  });
});

describe("beamSegment", () => {
  it("runs from edge to facing edge with the gap at both ends", () => {
    const seg = beamSegment(
      { x: 0, y: 0, width: 100, height: 50 },
      { x: 200, y: 0, width: 100, height: 50 },
      6,
    );
    expect(seg).toEqual({ x1: 106, y1: 25, x2: 194, y2: 25 });
  });

  it("leaves a vertical stack from the bottom edge", () => {
    const seg = beamSegment(
      { x: 0, y: 0, width: 200, height: 40 },
      { x: 0, y: 100, width: 200, height: 40 },
      0,
    );
    expect(seg).toEqual({ x1: 100, y1: 40, x2: 100, y2: 100 });
  });

  it("draws nothing for unlaid-out or overlapping boxes", () => {
    expect(
      beamSegment({ x: 0, y: 0, width: 0, height: 0 }, { x: 9, y: 9, width: 9, height: 9 }),
    ).toBeNull();
    expect(
      beamSegment(
        { x: 0, y: 0, width: 100, height: 100 },
        { x: 50, y: 0, width: 100, height: 100 },
      ),
    ).toBeNull();
  });
});

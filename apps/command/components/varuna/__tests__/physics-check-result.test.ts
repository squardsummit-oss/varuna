/**
 * What the physics check says it could not compare (SPEC.md 7.7, rule 6).
 *
 * On a crop check both models run, so a lever neither ran is "not in this check" and both ran
 * without it. On a tide scenario the check runs nothing - the full-city Twin job is the answer -
 * so no line may say a model ran without anything.
 */
import { describe, expect, it } from "vitest";

import { uncheckedLines } from "@/components/varuna/physics-check-result";
import type { PhysicsCheckResult } from "@/lib/api/whatif";

function result(overrides: Partial<PhysicsCheckResult>): PhysicsCheckResult {
  return {
    runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
    runsOnTwin: false,
    twinJob: null,
    summary: "Emulator vs physics: max difference 4 cm at Sion Circle",
    toleranceCm: 5,
    agrees: true,
    maxDiffCm: 4,
    maxDiffHotspot: "Sion Circle",
    hotspots: [],
    outside: [],
    window: { sizeM: 990, nodes: 631, edges: 620, centre: "Sion Circle", cleanedEdgesInside: 0 },
    leversNotChecked: [],
    cleanedEdges: 0,
    massBalance: { baseline: 0, scenario: 0, budget: 1e-3 },
    ms: 3200,
    budgetMs: 10_000,
    notes: [],
    ...overrides,
  };
}

describe("uncheckedLines", () => {
  it("says both models ran without the pump plan on a crop check", () => {
    expect(uncheckedLines(result({ leversNotChecked: ["pump_plan"] }))).toEqual([
      "The pump plan is not in this check: the Twin has no pump sink on the street, so both models ran without it.",
    ]);
  });

  it("never says a model ran on a tide scenario, where the check runs nothing", () => {
    const lines = uncheckedLines(
      result({
        runsOnTwin: true,
        agrees: false,
        maxDiffCm: null,
        leversNotChecked: ["pump_plan", "clean_top"],
        cleanedEdges: 14,
      }),
    );
    expect(lines).toEqual([
      "The pump plan is not in the Twin run: the Twin has no pump sink on the street, so the pumps are priced by the emulator only, as a lower bound.",
      "The top 14 pipes by blockage are not in the full-city Twin run.",
    ]);
    expect(lines.join(" ")).not.toMatch(/ran without/);
    // No window on the tide path, so nothing is said about cleaned pipes inside one.
    expect(lines.join(" ")).not.toMatch(/inside the window/);
  });

  it("names cleaned pipes the crop's window does not hold", () => {
    expect(uncheckedLines(result({ cleanedEdges: 14, window: { ...result({}).window } }))).toEqual([
      "None of the 14 cleaned pipes is inside the window, so the cleaning changes nothing the check can see.",
    ]);
    const partly = result({
      cleanedEdges: 14,
      window: { ...result({}).window, cleanedEdgesInside: 3 },
    });
    expect(uncheckedLines(partly)).toEqual([
      "3 of the 14 cleaned pipes are inside the window; the rest change nothing the check can see.",
    ]);
  });
});

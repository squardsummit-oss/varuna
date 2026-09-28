/**
 * Contract tests for the emulator what-if client (`POST /v1/whatif`, `/v1/whatif/physics-check`).
 *
 * `whatif-emulator.fixture.json` holds bodies `services/api/varuna_api/routers/whatif.py`
 * produced on the five-street run of `services/api/tests/test_whatif_emulator.py` (rain 1.5x,
 * tide +0.5 m, the pump plan and "clean top 1"), so a rename on the API side fails here rather
 * than on the console.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { uncheckedLines } from "@/components/varuna/physics-check-result";
import { runPhysicsCheck, runWhatIf } from "@/lib/api/whatif";

import fixture from "./whatif-emulator.fixture.json";

function respond(body: unknown) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("runWhatIf", () => {
  it("sends the pump-plan and clean-top levers only when they are asked for", async () => {
    const fetchMock = respond(fixture.emulator);
    await runWhatIf({ rainScale: 1.5, tideOffsetM: 0.5, pumpPlan: true, cleanTop: 1 });
    await runWhatIf({ rainScale: 1.5, tideOffsetM: 0 });

    const bodies = fetchMock.mock.calls.map((call) =>
      JSON.parse(String((call as unknown as [string, RequestInit])[1].body)),
    );
    expect(bodies[0]).toMatchObject({ pump_plan: true, clean_top: 1, tide_offset_m: 0.5 });
    expect(bodies[1]).not.toHaveProperty("pump_plan");
    expect(bodies[1]).not.toHaveProperty("clean_top");
  });

  it("reads every changed street, the per-hotspot table and the tide it left out", async () => {
    respond(fixture.emulator);
    const result = await runWhatIf({ rainScale: 1.5, tideOffsetM: 0.5 });

    // The counts are the length of the list the map draws.
    expect(result.nChanged).toBe(result.segments.length);
    expect(result.nWorse + result.nImproved).toBe(result.nChanged);
    expect(result.summary).toContain("Tide not included");
    expect(result.tide).toEqual({
      requestedM: 0.5,
      needsTwin: true,
      message: expect.stringContaining("no sea level"),
    });

    // A street the run had below 5 cm comes back flagged, not as a measured 0 cm.
    const dry = result.segments.find((s) => s.segmentId === "S-D");
    expect(dry?.dryBefore).toBe(true);
    expect(result.nDryBefore).toBe(result.segments.filter((s) => s.dryBefore).length);

    const [hotspot] = result.hotspots;
    expect(hotspot?.name).toBe("Test junction");
    expect(Object.keys(hotspot?.minutesAboveAfter ?? {}).sort()).toEqual(["30", "45"]);
    expect(result.largestChanges[0]).toHaveProperty("name");

    expect(result.levers.pumpPlan?.applied).toBe(true);
    expect(result.levers.pumpPlan?.label).toMatch(/^Lower bound/);
    expect(result.levers.cleanTop?.label).toBe("Top 1 pipe by learned blockage, city-wide");
    expect(result.levers.cleanTop?.pipes?.[0]?.edgeId).toBe("E1");
  });
});

describe("runPhysicsCheck", () => {
  it("says a tide scenario runs on the Twin instead of reporting a disagreement", async () => {
    respond(fixture.physics_tide);
    const result = await runPhysicsCheck({ rainScale: 1.3, tideOffsetM: 0.5 });

    expect(result.runsOnTwin).toBe(true);
    expect(result.maxDiffCm).toBeNull();
    expect(result.hotspots).toEqual([]);
    expect(result.twinJob?.endpoint).toBe("/v1/whatif/twin");
    expect(result.summary).toMatch(/^This scenario runs on the Twin/);
  });

  it("checks the levers the what-if ran and reads the ones the Twin left out", async () => {
    const fetchMock = respond(fixture.physics_tide);
    const result = await runPhysicsCheck({
      rainScale: 1.3,
      tideOffsetM: 0.5,
      pumpPlan: true,
      cleanTop: true,
    });

    const sent = JSON.parse(
      String((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body),
    );
    expect(sent).toMatchObject({ pump_plan: true, clean_top: true, tide_offset_m: 0.5 });
    expect(result.leversNotChecked).toEqual(["pump_plan", "clean_top"]);
    // A tide scenario runs nothing in the check, so no line may say a model "ran without" the
    // pumps: the pump line speaks of the full-city Twin run instead.
    const lines = uncheckedLines(result);
    expect(lines).toEqual([
      expect.stringContaining("pump plan is not in the Twin run"),
      expect.stringContaining("top 14 pipes"),
    ]);
    expect(lines.join(" ")).not.toMatch(/ran without/);
  });

  it("says when the cleaned pipes lie outside the check's window", () => {
    const crop = {
      runId: "R",
      runsOnTwin: false,
      twinJob: null,
      summary: "Emulator vs physics: max difference 0.0 cm at Bandra Talao",
      toleranceCm: 5,
      agrees: true,
      maxDiffCm: 0,
      maxDiffHotspot: "Bandra Talao",
      hotspots: [],
      outside: [],
      window: { sizeM: 990, nodes: 631, edges: 620, centre: "Bandra Talao", cleanedEdgesInside: 0 },
      leversNotChecked: [],
      cleanedEdges: 14,
      massBalance: { baseline: 0, scenario: 0, budget: 1e-3 },
      ms: 4100,
      budgetMs: 10_000,
      notes: [],
    };
    expect(uncheckedLines(crop)).toEqual([
      "None of the 14 cleaned pipes is inside the window, so the cleaning changes nothing the check can see.",
    ]);
    expect(uncheckedLines({ ...crop, window: { ...crop.window, cleanedEdgesInside: 3 } })).toEqual([
      "3 of the 14 cleaned pipes are inside the window; the rest change nothing the check can see.",
    ]);
    expect(uncheckedLines({ ...crop, cleanedEdges: 0 })).toEqual([]);
  });
});

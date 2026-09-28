/**
 * The emulator's answer reduced to what the lab and the console drawer draw.
 *
 * The body is the one `POST /v1/whatif` produced on the five-street test run of
 * `services/api/tests/test_whatif_emulator.py` (rain 1.5x, tide +0.5 m, the pump plan and "clean
 * top 1"), so the counts the screen prints are checked against the list the map draws.
 */
import { describe, expect, it, vi } from "vitest";

import { runWhatIf } from "@/lib/api/whatif";

import fixture from "@/lib/api/__tests__/whatif-emulator.fixture.json";

import { BEFORE_FLOOR_CM, changeSummary, emulatorAnswer } from "../whatif-answer";

async function emulatorResult() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(fixture.emulator), { status: 200 })),
  );
  try {
    return await runWhatIf({ rainScale: 1.5, tideOffsetM: 0.5, pumpPlan: true, cleanTop: 1 });
  } finally {
    vi.unstubAllGlobals();
  }
}

describe("changeSummary", () => {
  it("leaves out a side with nothing on it rather than printing a zero", () => {
    expect(changeSummary(0, 0)).toBe("Nothing changed.");
    expect(changeSummary(1687, 0)).toBe("1,687 segments deeper, each by 0.5 cm or more.");
    expect(changeSummary(0, 3)).toBe("3 segments shallower, each by 0.5 cm or more.");
    expect(changeSummary(1, 1)).toBe("1 segment deeper, 1 shallower, each by 0.5 cm or more.");
    expect(changeSummary(12, 0, 2)).toBe(
      "12 segments deeper (2 of them newly wet), each by 0.5 cm or more.",
    );
  });

  it("reads as the endpoint's own sentence", () => {
    const { n_worse: worse, n_improved: improved, summary } = fixture.emulator;
    expect(summary.startsWith(changeSummary(worse, improved))).toBe(true);
  });
});

describe("emulatorAnswer", () => {
  it("draws exactly the streets it counts", async () => {
    const answer = emulatorAnswer(await emulatorResult());
    expect(answer.engine).toBe("emulator");
    // The difference layer's input is the counted list, so the count and the map cannot differ.
    expect(answer.deltaCm.size).toBe(answer.nChanged);
    expect(answer.nChanged).toBe(fixture.emulator.segments.length);
    expect(answer.nothingChanged).toBe(false);
  });

  it("tables each hotspot with its minutes above 30 cm, and names the largest changes", async () => {
    const answer = emulatorAnswer(await emulatorResult());
    const junction = answer.hotspots.find((row) => row.hotspot === "Test junction");
    expect(junction).toBeDefined();
    expect(junction?.afterCm).toBeGreaterThan(junction?.beforeCm ?? Infinity);
    expect(junction?.minutesImpassableBefore).toBeTypeOf("number");
    // A street the run had below 5 cm says so rather than showing a 0 cm "before".
    const dry = answer.streets.find((row) => row.id === "S-D");
    expect(dry?.hotspot).toBe("Segment S-D");
    expect(dry?.beforeBelowCm).toBe(BEFORE_FLOOR_CM);
    // Each lever the endpoint applied or left out is a line, in its own words.
    expect(answer.lines.some((line) => line.includes("the emulator has no sea level"))).toBe(true);
    expect(answer.lines.some((line) => line.startsWith("Pump plan:"))).toBe(true);
  });
});

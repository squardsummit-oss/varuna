import { describe, expect, it } from "vitest";

import { mostFloodedStep } from "../console-screen";

const TS = ["06:45", "06:50", "06:55"].map((t) => `2019-07-02T${t}:00+05:30`);

describe("mostFloodedStep", () => {
  it("picks the step with the most streets at or above 15 cm", () => {
    const depthCm = new Map([
      ["a", [0, 20, 16]],
      ["b", [0, 14, 30]],
      ["c", [0, 15, 2]],
    ]);
    expect(mostFloodedStep({ depthCm, validTs: TS })).toBe(1);
  });

  it("returns null for a run where no street reaches 15 cm", () => {
    expect(mostFloodedStep({ depthCm: new Map([["a", [1, 2, 3]]]), validTs: TS })).toBeNull();
  });
});

/**
 * The landing's warning-time note says why its figure is a dash.
 *
 * On the shipped 2 July scores every hit came after the civic log (`n_hits_early` 0,
 * `n_hits_after` 3), so the median over "before the log" hits is null and the figure is "—". A
 * bare dash under "Median warning time" read as a number that failed to load.
 */

import { describe, expect, it } from "vitest";

import { leadNote } from "../proof";

const base = { csi: 0.103, pod: 0.176, pins: 17, thresholdCm: 15 };

describe("leadNote", () => {
  it("explains a null median when every hit came after the log", () => {
    const note = leadNote({ ...base, leadMin: null, hitsEarly: 0, hitsAfter: 3 });
    expect(note).toContain("None on this event");
    expect(note).toContain("all 3 pins");
  });

  it("keeps the definition when there is a median to show", () => {
    const note = leadNote({ ...base, leadMin: 31, hitsEarly: 4, hitsAfter: 1 });
    expect(note).toBe("How long before the civic log VARUNA first called that street impassable.");
  });

  it("keeps the definition before the scores arrive", () => {
    expect(leadNote(null)).toBe(
      "How long before the civic log VARUNA first called that street impassable.",
    );
  });
});

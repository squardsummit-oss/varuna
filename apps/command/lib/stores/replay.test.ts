import { describe, expect, it } from "vitest";

import { minutesFromStart, stepCycle } from "./replay";

const T0 = "2019-07-02T05:40:00+05:30";

describe("stepCycle", () => {
  it("steps from a cycle boundary to the next and the previous one", () => {
    expect(stepCycle("2019-07-02T06:40:00+05:30", T0, 1)).toBe("2019-07-02T06:45:00+05:30");
    expect(stepCycle("2019-07-02T06:40:00+05:30", T0, -1)).toBe("2019-07-02T06:35:00+05:30");
  });

  it("lands a clock paused between cycles back on the grid", () => {
    // The deployed clock was found paused at 06:43:09; plus five minutes never met a cycle again.
    expect(stepCycle("2019-07-02T06:43:09.057+05:30", T0, 1)).toBe("2019-07-02T06:45:00+05:30");
    expect(stepCycle("2019-07-02T06:43:09.057+05:30", T0, -1)).toBe("2019-07-02T06:40:00+05:30");
  });

  it("treats a hair past a boundary as the boundary", () => {
    expect(stepCycle("2019-07-02T06:40:00.001+05:30", T0, 1)).toBe("2019-07-02T06:45:00+05:30");
  });
});

describe("minutesFromStart", () => {
  it("counts minutes from the window start, and 0 for a time it cannot read", () => {
    expect(minutesFromStart("2019-07-02T06:40:00+05:30", T0)).toBe(60);
    expect(minutesFromStart("not a time", T0)).toBe(0);
  });
});

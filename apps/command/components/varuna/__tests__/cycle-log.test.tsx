import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CycleLog, runVersionTail, type CycleLogRow } from "../cycle-log";

/**
 * The cycle log's identity rule, pinned after the P10.2 design QA found it broken (2026-09-24).
 *
 * The rows were keyed on the cycle *time*. The demo registry holds seventeen Mumbai runs across
 * eight cycle times - the 2026-09-13 bake, the 2026-09-23 re-bake and a live run, three of them
 * at 08:10 IST - so a single load of `/replay` produced twenty-nine React key warnings, and under
 * React's own documented behaviour for duplicate keys rows may be "duplicated and/or omitted".
 * A cycle log that silently drops a run is worse than one that is empty, because the numbers it
 * does show look complete.
 *
 * These two runs are the shape that collided: same cycle, different bakes, different mass balance.
 * The 0.00091 and 0.00260 are the 2026-09-13 and 2026-09-23 figures for that cycle, so the test
 * also stands as a record that the two bakes disagree by a factor of three.
 */
const SAME_CYCLE: CycleLogRow[] = [
  {
    id: "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.0-baked",
    time: "2019-07-02T08:10:00+05:30",
    stages: "decode, sky, twin, pulse, products",
    ms: 64_800,
    massBalance: 0.00091,
  },
  {
    id: "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.1-baked",
    time: "2019-07-02T08:10:00+05:30",
    stages: "decode, sky, twin, pulse, products",
    ms: 147_200,
    massBalance: 0.0026,
  },
];

describe("runVersionTail", () => {
  it("drops the city and cycle stamp, which the row's own Time cell already says", () => {
    expect(runVersionTail("MUM-20190702T0240Z-sky1.0-twin1.0-flash0.1-baked")).toBe(
      "sky1.0-twin1.0-flash0.1-baked",
    );
  });

  it("shows an id it does not recognise whole rather than guessing at it", () => {
    expect(runVersionTail("something-else-entirely")).toBe("something-else-entirely");
  });
});

describe("CycleLog", () => {
  it("draws one row per run when two runs share a cycle time", () => {
    render(<CycleLog rows={SAME_CYCLE} />);

    // Two rows, not one: the defect showed as a row going missing, never as an error.
    const rows = screen.getAllByRole("row").slice(1); // drop the header
    expect(rows).toHaveLength(2);

    // And they are tellable apart, which is the half a unique key alone would not fix.
    expect(within(rows[0]).getByText("sky1.0-twin1.0-flash0.0-baked")).toBeInTheDocument();
    expect(within(rows[1]).getByText("sky1.0-twin1.0-flash0.1-baked")).toBeInTheDocument();
    expect(screen.getAllByText("08:10")).toHaveLength(2);
  });

  it("states the stage list once instead of repeating it on every row", () => {
    render(<CycleLog rows={SAME_CYCLE} />);
    // The registry gives a run's total, not its per-stage split, so a Stages column printed the
    // same string on every row and pushed the table past the console panel's width.
    expect(
      screen.getByText(/Stages each run: decode, sky, twin, pulse, products\./),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Time",
      "Run",
      "Time taken",
      "Mass balance",
    ]);
  });

  it("lists every distinct stage list, so it never speaks for a run it does not describe", () => {
    render(<CycleLog rows={[SAME_CYCLE[0], { ...SAME_CYCLE[1], stages: "decode, sky, twin" }]} />);
    expect(
      screen.getByText("Stages each run: decode, sky, twin, pulse, products / decode, sky, twin."),
    ).toBeInTheDocument();
  });

  it("says so plainly when there are no runs, rather than drawing an empty table", () => {
    render(<CycleLog rows={[]} />);
    expect(screen.getByText("No cycles yet")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

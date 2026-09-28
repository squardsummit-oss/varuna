/**
 * The desilting table (SPEC.md 7.3 AC5): sortable by blockage, spread and capacity reduction,
 * and on `/drains` by learned change, the column that says what Pulse moved rather than what land
 * use assumed.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  DrainHealthTable,
  formatBetaDelta,
  sortDrainRows,
  type DrainHealthRow,
} from "../drain-health-table";

function row(id: string, betaMean: number, betaDelta: number | undefined): DrainHealthRow {
  return {
    id,
    street: `Street ${id}`,
    betaMean,
    betaSd: 0.1,
    capacityReduction: betaMean,
    hotspotsExplained: [],
    observations: 0,
    lastUpdated: "2019-07-02T08:40:00+05:30",
    betaDelta,
  };
}

// A 0.35 market prior that nothing moved, a pipe raised by 0.34 and one cleared by 0.19.
const ROWS = [row("MUM-E1", 0.35, 0), row("MUM-E2", 0.49, 0.34), row("MUM-E3", 0.01, -0.19)];

function pipeOrder(): string[] {
  return within(screen.getByRole("table"))
    .getAllByRole("row")
    .slice(1)
    .map((r) => within(r).getByRole("rowheader").textContent ?? "");
}

describe("DrainHealthTable", () => {
  it("opens on blockage by default, the desilting order", () => {
    render(<DrainHealthTable rows={ROWS} />);
    expect(pipeOrder().map((t) => t.slice(-6))).toEqual(["MUM-E2", "MUM-E1", "MUM-E3"]);
  });

  it("opens on learned change when asked, a cleared pipe ranked by its size", () => {
    render(<DrainHealthTable rows={ROWS} defaultSort="change" />);
    expect(pipeOrder().map((t) => t.slice(-6))).toEqual(["MUM-E2", "MUM-E3", "MUM-E1"]);
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    // Learned change sits beside the pipe's name, where a narrow column still shows it.
    expect(headers[0]).toBe("Pipe");
    expect(headers[1]).toContain("Learned change");
    expect(screen.getByText("-0.19")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Blockage/ }));
    expect(pipeOrder().map((t) => t.slice(-6))).toEqual(["MUM-E2", "MUM-E1", "MUM-E3"]);
  });

  it("breaks ties on the id, so the order never depends on the file's", () => {
    const tied = [row("MUM-E9", 0.2, 0), row("MUM-E4", 0.2, 0)];
    expect(sortDrainRows(tied, "change", "desc").map((r) => r.id)).toEqual(["MUM-E4", "MUM-E9"]);
  });

  it("prints a dash for a change it was not given, never a zero", () => {
    expect(formatBetaDelta(undefined)).toBe("—");
    expect(formatBetaDelta(0.001)).toBe("0.00");
    expect(formatBetaDelta(0.3426)).toBe("+0.34");
  });
});

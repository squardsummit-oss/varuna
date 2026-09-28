import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// NumberFlow draws into a custom element jsdom cannot update; the value it is handed is the point.
vi.mock("@number-flow/react", () => ({
  default: ({ value, suffix = "" }: { value: number; suffix?: string }) => (
    <span data-number-flow="">{`${value}${suffix}`}</span>
  ),
}));

import {
  DEFAULT_PUMP_COLUMNS,
  PUMP_ACTIONS_HELPER,
  PumpBoard,
} from "@/components/varuna/pump-board";
import type { Pump } from "@/components/varuna/pump-card";

const PUMP: Pump = {
  id: "P-12",
  capacityM3PerHour: 500,
  depot: "Parel depot",
  status: "available",
  etaMinutes: 25,
};

describe("PumpBoard", () => {
  it("renders the available column and the three hotspot columns", () => {
    render(<PumpBoard pumps={[]} columns={DEFAULT_PUMP_COLUMNS} />);

    expect(screen.getByRole("heading", { name: "Available pumps" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Hindmata junction" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "King's Circle" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sion Circle" })).toBeInTheDocument();
    // No run carries a depth series for these columns: said as a settled answer, with no line.
    expect(screen.getAllByText("No depth series for this place in this run")).toHaveLength(3);
    // The sparkline is the board's only 254 px wide drawing; icons are 24 px.
    expect(document.querySelector('svg[width="254"]')).toBeNull();
    expect(screen.getAllByText("Minutes above 45 cm: no data")).toHaveLength(3);
  });

  it("says the series is loading while it is, never that there is none", () => {
    render(
      <PumpBoard
        pumps={[]}
        columns={DEFAULT_PUMP_COLUMNS}
        depthNote="Loading this place's depth series"
      />,
    );

    expect(screen.getAllByText("Loading this place's depth series")).toHaveLength(3);
    expect(screen.queryByText("No depth series for this place in this run")).toBeNull();
  });

  it("draws each column's depth line and captions its peak with and without the plan", () => {
    const columns = DEFAULT_PUMP_COLUMNS.map((column, i) =>
      i === 0
        ? {
            ...column,
            minutesAbove45: { before: 70, after: 20 },
            depthCm: { before: [12, 38.2, 58.4, 51, 40], after: [12, 30, 46.2, 44, 35] },
          }
        : column,
    );
    const { container, rerender } = render(<PumpBoard pumps={[]} columns={columns} />);

    expect(screen.getByText("Peak 46 cm with the plan, 58 cm without")).toBeInTheDocument();
    expect(container.querySelectorAll('svg[width="254"]')).toHaveLength(1);
    expect(screen.getAllByText("No depth series for this place in this run")).toHaveLength(2);

    // Before Optimise the plan's line is not the board's: the no-pump peak only.
    rerender(<PumpBoard pumps={[]} columns={columns} planApplied={false} />);
    expect(screen.getByText("Peak 58 cm with no pump, next 3 h")).toBeInTheDocument();
  });

  it("shows the empty inventory and empty hotspot columns", () => {
    render(<PumpBoard pumps={[]} columns={DEFAULT_PUMP_COLUMNS} />);

    expect(screen.getByText("No pumps loaded yet")).toBeInTheDocument();
    expect(screen.getByText("The inventory arrives with the city layers.")).toBeInTheDocument();
    expect(screen.getAllByText("No pump assigned")).toHaveLength(3);
  });

  it("labels the inventory synthetic and disables the Phase 8 actions with a reason", () => {
    render(<PumpBoard pumps={[]} columns={DEFAULT_PUMP_COLUMNS} />);

    expect(screen.getByText("Synthetic pump inventory")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Optimise" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Dispatch pumps" })).toBeDisabled();
    expect(screen.getByText(PUMP_ACTIONS_HELPER)).toBeInTheDocument();
  });

  it("says every pump is assigned, not that none loaded, when the pool is empty after a plan", () => {
    const columns = DEFAULT_PUMP_COLUMNS.map((column, i) =>
      i === 0 ? { ...column, pumps: [{ ...PUMP, status: "moving" as const }] } : column,
    );
    render(<PumpBoard pumps={[]} columns={columns} onAssign={() => {}} />);

    expect(screen.getByText("Every pump is assigned")).toBeInTheDocument();
    expect(
      screen.getByText("Drag a pump back here to take it off its hotspot."),
    ).toBeInTheDocument();
    expect(screen.queryByText("No pumps loaded yet")).not.toBeInTheDocument();
  });

  it("renders a pump card in the available column when the inventory has pumps", () => {
    render(<PumpBoard pumps={[PUMP]} columns={DEFAULT_PUMP_COLUMNS} />);

    expect(screen.getByRole("article", { name: "Pump P-12" })).toBeInTheDocument();
    expect(screen.getByText("Parel depot")).toBeInTheDocument();
    expect(screen.getByText("ETA 25 min")).toBeInTheDocument();
    expect(screen.queryByText("No pumps loaded yet")).not.toBeInTheDocument();
  });
});

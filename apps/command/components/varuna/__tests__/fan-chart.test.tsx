import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FanChart, type FanChartPoint } from "@/components/varuna/fan-chart";

/**
 * Three steps of the Hindmata rain series of the 07:40 cycle of MUM-2019-07-02, so the assertions
 * quote numbers a real Sky cycle produced rather than round ones invented for a test.
 */
const POINTS: FanChartPoint[] = [
  { validTs: "2019-07-02T07:45:00+05:30", leadMin: 5, p10: 3.636, p50: 4.009, p90: 4.299 },
  { validTs: "2019-07-02T08:25:00+05:30", leadMin: 45, p10: 1.344, p50: 3.761, p90: 4.418 },
  { validTs: "2019-07-02T09:10:00+05:30", leadMin: 90, p10: 0, p50: 1.137, p90: 4.101 },
];

describe("FanChart", () => {
  it("summarises the series with its peak, its unit and its band for a screen reader", () => {
    render(<FanChart points={POINTS} quantity="Rain rate" unit="mm/h" />);
    const aria = screen.getByRole("img").getAttribute("aria-label") ?? "";
    expect(aria).toContain("Rain rate");
    expect(aria).toContain("4.0 mm/h");
    expect(aria).toContain("07:45");
    expect(aria).toContain("+5 min");
  });

  it("names both series under the plot, so colour is never the only carrier of meaning", () => {
    render(<FanChart points={POINTS} quantity="Rain rate" unit="mm/h" />);
    expect(screen.getByText("Median (p50)")).toBeInTheDocument();
    expect(screen.getByText("p10 to p90")).toBeInTheDocument();
  });

  it("counts the members it was given", () => {
    const { container } = render(
      <FanChart
        points={POINTS}
        quantity="Rain rate"
        unit="mm/h"
        members={[
          { id: 0, values: [3.9, 3.4, 0.4] },
          { id: 1, values: [4.2, 3.9, 2.1] },
        ]}
      />,
    );
    expect(container.textContent).toContain("2 members");
  });

  it("says what to do instead of drawing a flat line when there is no run", () => {
    render(<FanChart points={[]} quantity="Rain rate" unit="mm/h" />);
    expect(screen.getByText("No forecast yet")).toBeInTheDocument();
    expect(
      screen.getByText("Press Play on the replay, or Compute live."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("prints the API's message verbatim when the forecast could not be read", () => {
    const message = "No run under data/runs carries rain products yet. Run make bake.";
    render(<FanChart points={[]} quantity="Rain rate" unit="mm/h" error={message} />);
    expect(screen.getByText("Forecast unavailable")).toBeInTheDocument();
    expect(screen.getByText(message)).toBeInTheDocument();
  });

  it("shows shimmer while loading, never a chart and never a spinner", () => {
    const { container } = render(
      <FanChart points={POINTS} quantity="Rain rate" unit="mm/h" loading />,
    );
    expect(container.querySelectorAll(".skeleton-shimmer").length).toBeGreaterThan(0);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("formats values with the caller's own formatter when it has one", () => {
    render(
      <FanChart
        points={POINTS}
        quantity="Depth"
        unit="cm"
        formatValue={(v) => `${Math.round(v)} cm`}
      />,
    );
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("4 cm");
  });
});

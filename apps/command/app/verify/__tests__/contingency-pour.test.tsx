import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DUR_MS } from "@/lib/motion";

// NumberFlow is a custom element jsdom cannot upgrade; the tests read the values it is given.
vi.mock("@number-flow/react", () => ({
  default: ({ value, format }: { value: number; format?: Intl.NumberFormatOptions }) => (
    <span data-number-flow="">{new Intl.NumberFormat("en-IN", format).format(value)}</span>
  ),
}));

import {
  ContingencyPour,
  niceScale,
  planColumn,
  pourAt,
  type PourColumn,
} from "../contingency-pour";

/** The three rows `/v1/verification` served for MUM-2019-07-02 on 2026-09-29. */
const COLUMNS: PourColumn[] = [
  {
    thresholdCm: 5,
    hits: 17,
    misses: 0,
    falseAlarms: 79,
    csi: 0.177,
    pod: 1,
    far: 0.823,
    medianLeadMin: 73,
  },
  {
    thresholdCm: 15,
    hits: 3,
    misses: 14,
    falseAlarms: 12,
    csi: 0.103,
    pod: 0.176,
    far: 0.8,
    medianLeadMin: null,
  },
  {
    thresholdCm: 30,
    hits: 1,
    misses: 16,
    falseAlarms: 2,
    csi: 0.053,
    pod: 0.059,
    far: 0.667,
    medianLeadMin: null,
  },
];

function mockReducedMotion(reduced: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: reduced && query.includes("reduce"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

describe("niceScale", () => {
  it("tops the axis at a round number at or above the largest column", () => {
    expect(niceScale(96)).toEqual({ top: 100, ticks: [0, 25, 50, 75, 100] });
    expect(niceScale(29).top).toBeGreaterThanOrEqual(29);
    expect(niceScale(0)).toEqual({ top: 1, ticks: [0, 1] });
  });
});

describe("the pour plan (M36)", () => {
  it("pours hits, then misses, then false alarms, 600 ms a band, staggered 150 ms per threshold", () => {
    const plan = planColumn(COLUMNS[1]!, 1);
    expect(plan.bands.map((b) => [b.key, b.from, b.to, b.start])).toEqual([
      ["hits", 0, 3, DUR_MS.staggerThresholds],
      ["misses", 3, 17, DUR_MS.staggerThresholds + DUR_MS.pourBand],
      ["falseAlarms", 17, 29, DUR_MS.staggerThresholds + 2 * DUR_MS.pourBand],
    ]);
    // The crest is flat 1.2 s after the last band starts, and the water stands at the served total.
    expect(plan.end).toBe(DUR_MS.staggerThresholds + 2 * DUR_MS.pourBand + DUR_MS.crestSettle);
    expect(pourAt(plan, plan.end)).toEqual({ level: 29, amp: 0 });
  });

  it("gives a zero count no time, so an empty band is not a pause", () => {
    const plan = planColumn(COLUMNS[0]!, 0);
    expect(plan.bands.map((b) => b.key)).toEqual(["hits", "falseAlarms"]);
    expect(plan.bands[1]!.start).toBe(DUR_MS.pourBand);
  });

  it("is empty before it starts and never overfills", () => {
    const plan = planColumn(COLUMNS[2]!, 2);
    expect(pourAt(plan, 0)).toEqual({ level: 0, amp: 0 });
    for (let t = 0; t <= plan.end; t += 50) {
      expect(pourAt(plan, t).level).toBeLessThanOrEqual(19 + 1e-9);
    }
  });
});

describe("ContingencyPour", () => {
  beforeEach(() => mockReducedMotion(true));
  afterEach(() => vi.unstubAllGlobals());

  it("names every served count and score for a reader who cannot see the cylinders", () => {
    render(<ContingencyPour columns={COLUMNS} groundTruthCount={17} headlineCm={15} />);
    const plot = screen.getByRole("img");
    const label = plot.getAttribute("aria-label") ?? "";
    expect(label).toContain("17 sourced pins");
    expect(label).toContain(
      "5 cm 17 hits, 0 misses, 79 false alarms, CSI 0.18, POD 1.00, FAR 0.82",
    );
    expect(label).toContain("15 cm 3 hits, 14 misses, 12 false alarms, CSI 0.10");
    expect(label).toContain("30 cm 1 hit, 16 misses, 2 false alarms, CSI 0.05");
    expect(screen.getByText("n = 17 sourced pins in the window")).toBeInTheDocument();
  });

  it("opens on the headline threshold and moves with the arrow keys", () => {
    render(<ContingencyPour columns={COLUMNS} groundTruthCount={17} headlineCm={15} />);
    const group = screen.getByRole("radiogroup", { name: "Threshold" });
    const fifteen = within(group).getByRole("radio", { name: "15 cm" });
    expect(fifteen).toHaveAttribute("aria-checked", "true");
    const readout = document.querySelector('[data-slot="contingency-readout"]')!;
    expect(readout).toHaveTextContent("3 of 17 pins found, 12 false alarms");
    expect(readout).toHaveTextContent("No pin flagged before it was logged");

    fireEvent.keyDown(fifteen, { key: "ArrowLeft" });
    expect(within(group).getByRole("radio", { name: "5 cm" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(readout).toHaveTextContent("17 of 17 pins found, 79 false alarms");
    expect(readout).toHaveTextContent("Median lead 73 min before the log");
  });

  it("stands full under reduced motion: every band shows its served count at once", async () => {
    render(<ContingencyPour columns={COLUMNS} groundTruthCount={17} headlineCm={15} />);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    const words = screen.getAllByText("false alarms");
    expect(words.length).toBeGreaterThanOrEqual(3);
    // The water paths close at the served level: a flat crest at the full height of each column.
    const crests = document.querySelectorAll('svg path[fill="none"]');
    expect(crests.length).toBe(COLUMNS.length);
  });
});

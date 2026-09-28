/**
 * The dashboard's lead-time control: which step each lead lands on, what it prints, and why the
 * screen does not open at "now" (every baked cycle's first step is dry).
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  DEFAULT_LEAD_MIN,
  LEAD_DEFAULT_REASON,
  LEAD_MINUTES,
  LeadTimeControl,
  stepForLead,
  stepLabel,
} from "@/components/citizen/lead-time";

const CYCLE = "2019-07-02T08:40:00+05:30";

/** 36 five-minute steps from 08:45 to 11:40 IST, as every baked cycle writes `valid_ts`. */
const VALID_TS = Array.from({ length: 36 }, (_, i) => {
  const at = new Date(Date.parse(CYCLE) + (i + 1) * 5 * 60_000);
  return at.toISOString();
});

describe("stepForLead", () => {
  it("lands each lead on the step whose valid time is nearest the cycle plus the lead", () => {
    expect(LEAD_MINUTES.map((lead) => stepForLead(VALID_TS, CYCLE, lead))).toEqual([
      0, 5, 11, 17, 23, 35,
    ]);
  });

  it("opens at +60 min, not at the first step every cycle leaves dry", () => {
    expect(DEFAULT_LEAD_MIN).toBe(60);
    expect(stepForLead(VALID_TS, CYCLE, DEFAULT_LEAD_MIN)).toBe(11);
    expect(LEAD_DEFAULT_REASON).toMatch(/\+60 min/);
    expect(LEAD_DEFAULT_REASON).toMatch(/no street is 5 cm deep at the first step/);
  });

  it("reads the run's own steps, so a ten-minute run lands on its own nearest step", () => {
    const tenMinute = Array.from({ length: 18 }, (_, i) =>
      new Date(Date.parse(CYCLE) + (i + 1) * 10 * 60_000).toISOString(),
    );
    expect(stepForLead(tenMinute, CYCLE, 60)).toBe(5);
    expect(stepForLead(tenMinute, CYCLE, 180)).toBe(17);
  });

  it("falls back to five-minute steps when the run names no cycle, and never leaves the run", () => {
    expect(stepForLead(VALID_TS, null, 60)).toBe(11);
    expect(stepForLead(VALID_TS, null, 0)).toBe(0);
    expect(stepForLead(VALID_TS.slice(0, 6), CYCLE, 180)).toBe(5);
    expect(stepForLead([], CYCLE, 60)).toBe(0);
  });
});

describe("stepLabel", () => {
  it("prints the step's own time and its lead, so 'Now' says it is five minutes on", () => {
    expect(stepLabel(VALID_TS, CYCLE, 11)).toBe("09:40 (+60 min)");
    expect(stepLabel(VALID_TS, CYCLE, 0)).toBe("08:45 (+5 min)");
    expect(stepLabel(VALID_TS, CYCLE, 36)).toBeNull();
  });
});

describe("LeadTimeControl", () => {
  it("names every option with its unit and time, and reports the lead chosen", () => {
    const onValueChange = vi.fn();
    render(
      <LeadTimeControl
        value={60}
        onValueChange={onValueChange}
        validTs={VALID_TS}
        cycleTs={CYCLE}
      />,
    );
    expect(screen.getByRole("group", { name: "Minutes ahead of the forecast cycle" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Now, 08:45 (+5 min)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "+180 min, 11:40 (+180 min)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^\+60 min/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    fireEvent.click(screen.getByRole("button", { name: /^\+120 min/ }));
    expect(onValueChange).toHaveBeenCalledWith(120);
  });

  it("stays usable before a run has loaded, with the unit still in every name", () => {
    render(<LeadTimeControl value={60} onValueChange={() => undefined} />);
    expect(screen.getByRole("button", { name: "+90 min" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Now" })).toBeTruthy();
  });
});

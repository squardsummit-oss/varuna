import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { brierAxis, brierByLeadRows } from "@/components/varuna/brier-by-lead-chart";
import {
  LEAD_TICKS_MIN,
  SkillByLeadChart,
  describeHorizon,
  horizonIsDrawn,
  skillByLeadRows,
} from "@/components/varuna/skill-by-lead-chart";
import type { RainScope, SkillHorizon } from "@/lib/api/verification-rain";

/** Contingency as the parser returns it; only the scores matter to the chart. */
const c = (csi: number | null, pod: number | null = null, far: number | null = null) => ({
  hits: 0,
  misses: 0,
  falseAlarms: 0,
  correctNegatives: 0,
  csi,
  pod,
  far,
});

const spread = (n: number, p10: number | null, p90: number | null) => ({ n, p10, p90 });

/** Two leads of the AOI at 20 mm/h, shaped as `parseRainSkill` returns them (08 cycles served). */
const SCOPE: RainScope = {
  label: "Sky pixels (500 m) whose centres fall inside the city grid",
  nPixels: 589,
  reliability: {},
  horizons: [],
  perCycle: [],
  byLead: [
    {
      leadMin: 5,
      nCycles: 8,
      nPixels: 4712,
      maeMmH: { mean: 7.25, p50: 7.56, persistence: 9.39 },
      thresholds: {
        "20": {
          eventPixels: 1819,
          baseRate: 0.386,
          forecasts: { mean: c(0.5829, 0.7, 0.2), p50: c(0.55), persistence: c(0.6291, 0.8, 0.3) },
          brier: 0.1294,
          brierPersistence: 0.1543,
          brierClimatology: 0.237,
          brierSkillVsPersistence: 0.16,
          brierSkillVsClimatology: 0.45,
          spread: {
            mean: {
              csi: spread(5, 0.3484, 0.7486),
              pod: spread(5, 0.5, 0.9),
              far: spread(5, 0.1, 0.3),
            },
          },
        },
      },
    },
    {
      leadMin: 170,
      nCycles: 3,
      nPixels: 1767,
      maeMmH: { mean: null, p50: null, persistence: null },
      thresholds: {
        "20": {
          eventPixels: 863,
          baseRate: 0.49,
          forecasts: { mean: c(0), p50: c(0), persistence: c(0) },
          brier: null,
          brierPersistence: null,
          brierClimatology: null,
          brierSkillVsPersistence: null,
          brierSkillVsClimatology: null,
          spread: {
            mean: { csi: spread(2, null, null), pod: spread(3, 0, 0), far: spread(0, null, null) },
          },
        },
      },
    },
  ],
};

const horizon = (over: Partial<SkillHorizon>): SkillHorizon => ({
  thresholdMmH: 20,
  forecast: "mean",
  csiFloor: 0.5,
  leadMin: 0,
  status: "found",
  firstFailure: {
    leadMin: 5,
    reasons: ["below_persistence"],
    csi: 0.5829,
    persistenceCsi: 0.6291,
    nCycles: 8,
  },
  beatsPersistenceLeadsMin: [15, 20],
  ...over,
});

describe("skillByLeadRows (7.10 data mapping)", () => {
  it("maps each lead's served score, persistence, band and counts", () => {
    const rows = skillByLeadRows(SCOPE, 20, "csi", "mean");
    expect(rows[0]).toEqual({
      leadMin: 5,
      forecast: 0.5829,
      persistence: 0.6291,
      band: [0.3484, 0.7486],
      bandN: 5,
      nCycles: 8,
      nPixels: 4712,
      eventPixels: 1819,
    });
  });

  it("draws no band where fewer than three cycles have a score", () => {
    const rows = skillByLeadRows(SCOPE, 20, "csi", "mean");
    expect(rows[1]?.band).toBeNull();
    expect(rows[1]?.bandN).toBe(2);
    // POD at the same lead has three cycles, so it keeps a band, even a flat one.
    expect(skillByLeadRows(SCOPE, 20, "pod", "mean")[1]?.band).toEqual([0, 0]);
  });

  it("switches metric and forecast without inventing a band the scorer did not serve", () => {
    const far = skillByLeadRows(SCOPE, 20, "far", "mean")[0];
    expect(far?.forecast).toBe(0.2);
    expect(far?.persistence).toBe(0.3);
    const median = skillByLeadRows(SCOPE, 20, "csi", "p50")[0];
    expect(median?.forecast).toBe(0.55);
    expect(median?.band).toBeNull();
  });

  it("keeps a lead with no row for the threshold as a gap, not a zero", () => {
    const rows = skillByLeadRows(SCOPE, 40, "csi", "mean");
    expect(rows.map((r) => r.forecast)).toEqual([null, null]);
    expect(rows.map((r) => r.eventPixels)).toEqual([0, 0]);
  });

  it("covers 0 to 180 minutes in 15-minute ticks", () => {
    expect(LEAD_TICKS_MIN[0]).toBe(0);
    expect(LEAD_TICKS_MIN[LEAD_TICKS_MIN.length - 1]).toBe(180);
    expect(LEAD_TICKS_MIN[1]).toBe(15);
  });

  it("maps the Brier rows from the same served cells", () => {
    expect(brierByLeadRows(SCOPE, 20)[0]).toEqual({
      leadMin: 5,
      brier: 0.1294,
      persistence: 0.1543,
      climatology: 0.237,
      nCycles: 8,
    });
    expect(brierByLeadRows(SCOPE, 20)[1]?.brier).toBeNull();
  });

  it("scales the Brier axis to the served scores, never past the score's own 0 to 1", () => {
    // The AOI's +5 min row: the base rate, 0.237, is the largest of the three series.
    expect(brierAxis(brierByLeadRows(SCOPE, 20))).toEqual({
      top: 0.25,
      ticks: [0, 0.0625, 0.125, 0.1875, 0.25],
    });
    const row = (brier: number | null, persistence: number | null, climatology: number | null) => ({
      leadMin: 5,
      brier,
      persistence,
      climatology,
      nCycles: 8,
    });
    // The radar domain at 40 mm/h sits under 0.07 everywhere.
    expect(brierAxis([row(0.0138, 0.019, 0.0274), row(0.0634, 0.0646, 0.0594)]).top).toBe(0.1);
    expect(brierAxis([row(0.6721, 0.7538, 0.2)]).top).toBe(1);
    expect(brierAxis([row(null, null, null)]).top).toBe(0.05);
  });
});

describe("describeHorizon", () => {
  it("does not say confidence decays when the first lead already fails", () => {
    const said = describeHorizon(horizon({}), "mean");
    expect(said.headline).toBe("No useful lead at 20 mm/h.");
    expect(said.headline + said.detail).not.toMatch(/decays/);
    expect(said.detail).toContain("0.58, already below persistence's 0.63, over 8 cycles");
    expect(said.detail).toContain("It beats persistence at +15 and +20 min.");
  });

  it("says confidence decays after N minutes only when the data shows a useful lead first", () => {
    const said = describeHorizon(
      horizon({
        leadMin: 10,
        firstFailure: {
          leadMin: 15,
          reasons: ["below_persistence", "below_floor"],
          csi: 0.47,
          persistenceCsi: 0.49,
          nCycles: 8,
        },
        beatsPersistenceLeadsMin: [],
      }),
      "mean",
    );
    expect(said.headline).toBe("Confidence decays after 10 minutes.");
    expect(said.detail).toContain("below persistence's 0.49 and below the 0.5 floor");
    expect(said.detail).toContain("It never beats persistence.");
  });

  it("states an undetermined walk and a horizon beyond the scored range as what they are", () => {
    const undetermined = describeHorizon(
      horizon({
        leadMin: 5,
        status: "undetermined",
        firstFailure: {
          leadMin: 10,
          reasons: ["no_event"],
          csi: null,
          persistenceCsi: 0.2,
          nCycles: 3,
        },
      }),
      "p50",
    );
    expect(undetermined.headline).toBe("Useful to +5 min, then undetermined.");
    expect(undetermined.detail).toContain("no denominator");
    const beyond = describeHorizon(
      horizon({ leadMin: 180, status: "beyond_scored_range", firstFailure: null }),
      "mean",
    );
    expect(beyond.headline).toBe("Useful skill holds to the last scored lead, +180 min.");
    expect(describeHorizon(null, "mean").headline).toBe("No horizon to state.");
  });
});

describe("SkillByLeadChart", () => {
  it("summarises the served numbers for a screen reader and names every series", () => {
    render(
      <SkillByLeadChart
        rows={skillByLeadRows(SCOPE, 20, "csi", "mean")}
        metric="csi"
        thresholdMmH={20}
        forecast="mean"
        horizon={horizon({})}
        csiFloor={0.5}
      />,
    );
    const label = screen.getByRole("img").getAttribute("aria-label") ?? "";
    expect(label).toContain("CSI at 20 mm/h by lead time, ensemble mean against persistence");
    expect(label).toContain("+5 min 0.58 against 0.63 over 8 cycles");
    expect(label).toContain("Useful-skill horizon 0 min, no useful lead.");
    expect(screen.getByText("Persistence, the analysis held for three hours")).toBeInTheDocument();
    expect(
      screen.getByText("Bars: observed event pixels above 20 mm/h, pooled over cycles"),
    ).toBeInTheDocument();
  });

  it("neither marks nor quotes an undetermined horizon at lead 0, which the headline calls undetermined", () => {
    const undetermined = horizon({
      leadMin: 0,
      status: "undetermined",
      firstFailure: {
        leadMin: 5,
        reasons: ["no_event"],
        csi: null,
        persistenceCsi: null,
        nCycles: 8,
      },
      beatsPersistenceLeadsMin: [],
    });
    expect(describeHorizon(undetermined, "mean").headline).toBe("Horizon undetermined.");
    expect(horizonIsDrawn(undetermined)).toBe(false);
    const { container } = render(
      <SkillByLeadChart
        rows={skillByLeadRows(SCOPE, 20, "csi", "mean")}
        metric="csi"
        thresholdMmH={20}
        forecast="mean"
        horizon={undetermined}
        csiFloor={0.5}
      />,
    );
    const label = screen.getByRole("img").getAttribute("aria-label") ?? "";
    expect(label).not.toMatch(/horizon/i);
    expect(container.textContent).not.toMatch(/Horizon 0 min/);
    // A useful lead before the walk stopped is a lead worth marking; the other statuses as served.
    expect(horizonIsDrawn({ ...undetermined, leadMin: 5 })).toBe(true);
    expect(horizonIsDrawn(horizon({}))).toBe(true);
    expect(horizonIsDrawn(horizon({ status: "beyond_scored_range", leadMin: 180 }))).toBe(true);
    expect(horizonIsDrawn(horizon({ status: "no_leads", leadMin: null }))).toBe(false);
    expect(horizonIsDrawn(null)).toBe(false);
  });

  it("renders its loading, error and empty states", () => {
    const { rerender, container } = render(
      <SkillByLeadChart rows={[]} metric="csi" thresholdMmH={20} forecast="mean" loading />,
    );
    expect(container.querySelector('[aria-hidden="true"]')).not.toBeNull();
    rerender(
      <SkillByLeadChart
        rows={[]}
        metric="csi"
        thresholdMmH={20}
        forecast="mean"
        error="Rain skill failed: HTTP 500."
      />,
    );
    expect(screen.getByText("Rain skill did not load")).toBeInTheDocument();
    rerender(<SkillByLeadChart rows={[]} metric="csi" thresholdMmH={20} forecast="mean" />);
    expect(screen.getByText("No leads scored")).toBeInTheDocument();
  });
});

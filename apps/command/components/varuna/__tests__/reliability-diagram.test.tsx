import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ReliabilityDiagram,
  describeReliability,
  reliabilitySeries,
} from "@/components/varuna/reliability-diagram";
import type { ReliabilityBand } from "@/lib/api/verification-rain";

const bin = (pFrom: number, n: number, meanP: number | null, observedFrequency: number | null) => ({
  pFrom,
  pTo: pFrom + 0.1,
  n,
  meanP,
  observedFrequency,
});

/** The first AOI band at 20 mm/h as `/v1/verification/rain-skill` served it on 2026-09-28. */
const BANDS: ReliabilityBand[] = [
  {
    leadFromMin: 5,
    leadToMin: 60,
    n: 53010,
    baseRate: 0.3574,
    reliability: 0.0556,
    resolution: 0.031,
    uncertainty: 0.2297,
    bins: [
      bin(0.1, 7373, 0.1231, 0.3795),
      bin(0, 31454, 0.0077, 0.24),
      bin(0.4, 1833, 0.4214, 0.6601),
      bin(0.9, 0, null, null),
    ],
  },
  {
    leadFromMin: 65,
    leadToMin: 120,
    n: 0,
    baseRate: null,
    reliability: null,
    resolution: null,
    uncertainty: null,
    bins: [bin(0, 0, null, null)],
  },
];

describe("reliabilitySeries", () => {
  it("keeps served bins in probability order and drops empty ones rather than drawing zeros", () => {
    const [first, second] = reliabilitySeries(BANDS);
    expect(first?.label).toBe("+5 to +60 min");
    expect(first?.points.map((p) => p.x)).toEqual([0.0077, 0.1231, 0.4214]);
    expect(first?.points[0]).toEqual({ x: 0.0077, y: 0.24, n: 31454, pFrom: 0, pTo: 0.1 });
    expect(second?.points).toEqual([]);
  });

  it("describes each drawn band from its lowest and highest bin", () => {
    const said = describeReliability(reliabilitySeries(BANDS), 20);
    expect(said).toContain("above 20 mm/h");
    expect(said).toContain(
      "+5 to +60 min, 53,010 pixel-leads: where the members gave 1 % the rain came 24 %",
    );
    expect(said).toContain("where they gave 42 % it came 66 %");
    expect(said).toContain("reliability 0.056, resolution 0.031");
    expect(said).not.toContain("+65 to +120");
  });
});

describe("ReliabilityDiagram", () => {
  it("names every band, the diagonal, and prints the decomposition per band", () => {
    render(<ReliabilityDiagram bands={BANDS} thresholdMmH={20} />);
    expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/^Reliability of the/);
    expect(screen.getByText("Lead +5 to +60 min")).toBeInTheDocument();
    expect(screen.getByText("Perfect reliability")).toBeInTheDocument();
    const table = screen.getByRole("region", { name: "Reliability by lead band" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(within(rows[1]!).getByText("0.056")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("36 %")).toBeInTheDocument();
    // A band with nothing scored says so rather than printing zeros.
    expect(within(rows[2]!).getAllByText("not scored")).toHaveLength(4);
  });

  it("says why when no band has a drawable bin", () => {
    render(
      <ReliabilityDiagram
        bands={[BANDS[1]!]}
        thresholdMmH={10}
        emptyReason="8 of 8 runs keep no member cube (rain/cube.zarr)."
      />,
    );
    expect(screen.getByText("No reliability at 10 mm/h")).toBeInTheDocument();
    expect(
      screen.getByText("8 of 8 runs keep no member cube (rain/cube.zarr)."),
    ).toBeInTheDocument();
  });
});

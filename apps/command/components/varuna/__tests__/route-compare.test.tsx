import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RouteCompare, type RouteSummary } from "@/components/varuna/route-compare";

/** KEM to Sion as a 2 July cycle might answer it; the names are the API's own labels. */
const VARUNA: RouteSummary = {
  etaMin: 6.1,
  distanceM: 3480,
  maxDepthCm: 12,
  avoided: [
    { segmentId: "S618477973-001", name: "Dr Babasaheb Ambedkar Marg", probability: 0.82 },
    { segmentId: "S100841079-000", name: "off Dr Ambedkar Road", probability: 0.64 },
    // What the deployed API still writes until it is redeployed.
    { segmentId: "S102172139-001", name: "Unnamed road", probability: 0.41 },
  ],
};

describe("RouteCompare avoided list", () => {
  it("prints each avoided street by the API's name or label, and never 'Unnamed road'", () => {
    render(<RouteCompare naive={{ etaMin: 5.6, distanceM: 3100 }} varuna={VARUNA} />);
    const list = within(screen.getByRole("region", { name: "Avoided segments" }));

    expect(list.getByText("Dr Babasaheb Ambedkar Marg")).toBeInTheDocument();
    expect(list.getByText("off Dr Ambedkar Road")).toBeInTheDocument();
    expect(list.getByText("Road")).toBeInTheDocument();
    expect(list.queryByText(/unnamed/i)).not.toBeInTheDocument();
  });
});

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CitizenReportsCard } from "@/components/varuna/citizen-reports-card";
import type { ReportPin } from "@/lib/api/reports";

function pin(over: Partial<ReportPin>): ReportPin {
  return {
    id: "R-1",
    lon: 72.84,
    lat: 19.01,
    depthHint: "ankle",
    depthCm: 10,
    status: "received",
    statusLabel: "Received",
    statusSeeded: false,
    ts: new Date().toISOString(),
    text: null,
    place: "Milan Subway",
    thumbUrl: null,
    photoUrl: null,
    credit: null,
    synthetic: false,
    origin: "citizen",
    ...over,
  };
}

describe("CitizenReportsCard", () => {
  it("labels a seeded demo report synthetic and dates it, so it never reads as today's", () => {
    render(
      <CitizenReportsCard
        loaded
        reports={[pin({ id: "S-1", synthetic: true, origin: "seed", ts: "2019-07-02T08:47:00+05:30" })]}
      />,
    );
    const row = screen.getByRole("button", { name: /Milan Subway/ });
    expect(within(row).getByText("Synthetic")).toBeInTheDocument();
    expect(row).toHaveTextContent("2 Jul 2019, 08:47");
  });

  it("prints a report a person sent today with its time alone and no synthetic label", () => {
    render(<CitizenReportsCard loaded reports={[pin({})]} />);
    const row = screen.getByRole("button", { name: /Milan Subway/ });
    expect(within(row).queryByText("Synthetic")).toBeNull();
    expect(row.textContent).not.toMatch(/\d{4},/);
  });
});

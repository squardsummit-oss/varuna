/**
 * The console's ranked hotspot rail (task P6.7): the header states what ordered the list and how
 * many spots go impassable, each row carries its rank, name and the depth *now*, and the chip
 * follows the time bar rather than the peak.
 *
 * The file used to test the "As it happened" ticker (task P6.12, motion M18), which the rail
 * carried underneath. The ticker was removed at the team's request; the pins still drop on the map
 * and `/verify` lists each one with its source, so a test that the rail no longer draws it is the
 * only thing left to say about it here.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HotspotRail } from "@/components/varuna/hotspot-rail";
import type { Hotspot } from "@/lib/api/hotspots";

function hotspot(rank: number, name: string, depthCm: number[], peakDepthCm: number): Hotspot {
  return {
    rank,
    id: `H-${rank}`,
    name,
    slug: null,
    lon: 72.841,
    lat: 19.012,
    ward: null,
    isSink: false,
    sourceUrl: "https://example.org/register",
    sourced: true,
    peakDepthCm,
    peakTs: "2019-07-02T08:20:00+05:30",
    timeToPeakMin: 40,
    depthCm,
    pImpassableAtPeak: 1,
    impassableFromTs: null,
    minutesImpassable: 0,
    expectedImpact: 1,
    exposure: { weight: 1, facilities: [] },
    segmentIds: [],
    attribution: [],
    attributionLabel: null,
  };
}

const hindmata = hotspot(1, "Hindmata junction", [5, 20, 55], 55);
const sion = hotspot(2, "Sion Circle", [2, 8, 24], 24);

/** Eight spots, so the five-row preview has something to hide. */
const many = Array.from({ length: 8 }, (_, i) =>
  hotspot(i + 1, `Spot ${i + 1}`, [1, 2, 3], 40 - i),
);

function rows() {
  return within(screen.getByRole("list", { name: "Ranked hotspots" })).getAllByRole("listitem");
}

describe("the hotspot rail", () => {
  it("states what ordered the list and how many go impassable", () => {
    render(<HotspotRail hotspots={[hindmata, sion]} step={0} impassableThresholdCm={30} />);
    expect(screen.getByText(/2 chronic spots, ranked by peak depth/)).toBeInTheDocument();
    // Only Hindmata's 55 cm peak clears a car's 30 cm.
    expect(screen.getByText(/1 go above 30 cm/)).toBeInTheDocument();
  });

  it("says so plainly when nothing goes impassable", () => {
    render(<HotspotRail hotspots={[sion]} step={0} impassableThresholdCm={30} />);
    expect(screen.getByText(/None go above 30 cm/)).toBeInTheDocument();
  });

  it("shows the depth at the scrubbed step, not the peak", () => {
    const { rerender } = render(<HotspotRail hotspots={[hindmata]} step={0} />);
    expect(within(rows()[0]!).getByText("5 cm")).toBeInTheDocument();
    rerender(<HotspotRail hotspots={[hindmata]} step={2} />);
    expect(within(rows()[0]!).getByText("55 cm")).toBeInTheDocument();
    // The peak stays on the row either way, with the time it is reached.
    expect(within(rows()[0]!).getByText(/peak 55 cm at 08:20 \(\+40 min\)/)).toBeInTheDocument();
  });

  it("ranks the rows in the order it is given", () => {
    render(<HotspotRail hotspots={[hindmata, sion]} step={0} />);
    const listed = rows();
    expect(listed).toHaveLength(2);
    expect(listed[0]).toHaveTextContent("Hindmata junction");
    expect(listed[1]).toHaveTextContent("Sion Circle");
  });

  it("tells the operator what to do when there is no run, and what is coming while one loads", () => {
    const { rerender } = render(<HotspotRail hotspots={[]} step={0} />);
    expect(screen.getByText("No hotspots yet")).toBeInTheDocument();
    expect(screen.getByText("Press Play on the replay, or Compute live.")).toBeInTheDocument();
    rerender(<HotspotRail hotspots={[]} step={0} loading />);
    expect(screen.getByText("Loading hotspots")).toBeInTheDocument();
  });

  it("no longer draws the As it happened ticker", () => {
    render(<HotspotRail hotspots={[hindmata]} step={0} />);
    expect(screen.queryByRole("region", { name: "As it happened" })).not.toBeInTheDocument();
  });
});

describe("the hotspot rail's preview", () => {
  it("opens on five and says how many more there are", () => {
    render(<HotspotRail hotspots={many} step={0} />);
    expect(rows()).toHaveLength(5);
    expect(screen.getByText("Spot 5")).toBeInTheDocument();
    expect(screen.queryByText("Spot 6")).not.toBeInTheDocument();
    // The header still counts every spot: the preview hides rows, it does not change the ranking.
    expect(screen.getByText(/8 chronic spots/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Show all 8 hotspots (3 more)" }),
    ).toBeInTheDocument();
  });

  it("reveals the rest and folds them back", () => {
    render(<HotspotRail hotspots={many} step={0} />);
    fireEvent.click(screen.getByRole("button", { name: "Show all 8 hotspots (3 more)" }));
    expect(rows()).toHaveLength(8);
    const fewer = screen.getByRole("button", { name: "Show fewer" });
    expect(fewer).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(fewer);
    expect(rows()).toHaveLength(5);
  });

  it("draws no button when every spot already fits", () => {
    render(<HotspotRail hotspots={[hindmata, sion]} step={0} />);
    expect(screen.queryByRole("button", { name: /Show all/ })).not.toBeInTheDocument();
  });

  it("keeps a spot picked on the map, even when it ranks below the preview", () => {
    // Clicking a ring on the map selects by id. Marking a row that is not drawn would look like
    // the rail ignored the click, so the selected spot is kept beside the opening five.
    render(<HotspotRail hotspots={many} step={0} selectedId="H-7" />);
    expect(rows()).toHaveLength(6);
    expect(screen.getByText("Spot 7")).toBeInTheDocument();
    expect(screen.queryByText("Spot 6")).not.toBeInTheDocument();
    // The count stays honest about what is still hidden.
    expect(
      screen.getByRole("button", { name: "Show all 8 hotspots (2 more)" }),
    ).toBeInTheDocument();
  });

  it("keeps the arrows inside the rows it has drawn", () => {
    const onSelect = vi.fn();
    render(<HotspotRail hotspots={many} step={0} onSelect={onSelect} />);
    const last = within(rows()[4]!).getByRole("button");
    fireEvent.keyDown(last, { key: "ArrowDown" });
    // Clamped to the fifth row rather than reaching for a sixth that is not in the DOM.
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ name: "Spot 5" }));
  });
});

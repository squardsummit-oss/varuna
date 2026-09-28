import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SegmentPopover } from "@/components/varuna/segment-popover";
import type { SegmentPick } from "@/components/map/city-map";
import type { Hotspot, HotspotAttribution } from "@/lib/api/hotspots";

/**
 * One of Hindmata's own segments as the 08:40 cycle of MUM-2019-07-02 forecast it, trimmed to six
 * steps, so the popover under test renders numbers a baked run produced.
 */
const PICK: SegmentPick = {
  segment: {
    id: "S100841069-000",
    name: "Dr Babasaheb Ambedkar Road",
    path: [
      [72.8421, 19.0101],
      [72.8425, 19.0106],
    ],
    depthCm: [0.0, 0.1, 0.3, 0.6, 0.7, 0.9],
    width: 4,
  },
  x: 420,
  y: 260,
};

const VALID_TS = [
  "2019-07-02T08:40:00+05:30",
  "2019-07-02T08:45:00+05:30",
  "2019-07-02T08:50:00+05:30",
  "2019-07-02T08:55:00+05:30",
  "2019-07-02T09:00:00+05:30",
  "2019-07-02T09:05:00+05:30",
];

/** Hindmata as `/v1/nowcast/hotspots` serves it: segments, and no attribution to rank. */
const HINDMATA: Hotspot = {
  rank: 2,
  id: "MUM-HS-01",
  name: "Hindmata junction (Hindmata Cinema, Dr B. Ambedkar Marg)",
  slug: "hindmata-junction-hindmata-cinema-dr-b-ambedkar-",
  lon: 72.8421396,
  lat: 19.010099,
  ward: "F/S",
  isSink: false,
  sourceUrl: null,
  sourced: true,
  peakDepthCm: 10.0,
  peakTs: "2019-07-02T11:40:00+05:30",
  timeToPeakMin: 175,
  depthCm: [0.0, 0.1, 0.3, 0.6, 0.7, 0.9],
  pImpassableAtPeak: 0.0,
  impassableFromTs: null,
  minutesImpassable: 0,
  expectedImpact: 0.0,
  exposure: { weight: 0.783, facilities: ["shelter"] },
  segmentIds: ["S100841069-000", "S100841079-000", "S102172139-001"],
  attribution: [],
  attributionLabel: "Not computed on this run: Flash-lite is element-wise per segment — ADR-0042.",
};

/** The shape `varuna_flash.whatif.attribute` would write, for the day a run writes one. */
const RANKED: HotspotAttribution[] = [
  { rank: 1, segmentId: "S100841069-000", beta: 0.68, depthExplainedCm: 12.4 },
  { rank: 2, segmentId: "S100841079-000", beta: 0.51, depthExplainedCm: 6.1 },
];

describe("SegmentPopover", () => {
  it("offers the why link only when there are pipes on the other side of it", async () => {
    const onWhy = vi.fn();
    render(
      <SegmentPopover
        pick={PICK}
        step={0}
        validTs={VALID_TS}
        hotspot={{ ...HINDMATA, attribution: RANKED, attributionLabel: null }}
        onWhy={onWhy}
        onClose={() => {}}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Show the 2 responsible pipes" }));
    expect(onWhy).toHaveBeenCalledWith("MUM-HS-01");
  });

  it("states the run's own reason instead of linking to an empty attribution section", () => {
    render(
      <SegmentPopover
        pick={PICK}
        step={0}
        validTs={VALID_TS}
        hotspot={HINDMATA}
        onWhy={() => {}}
        onClose={() => {}}
      />,
    );

    expect(screen.getByText("Why it floods")).toBeInTheDocument();
    expect(screen.getByText(HINDMATA.attributionLabel as string)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /responsible pipes/ })).not.toBeInTheDocument();
  });

  it("says nothing about why on a street that is not on the register", () => {
    render(<SegmentPopover pick={PICK} step={0} validTs={VALID_TS} onClose={() => {}} />);

    expect(screen.queryByText("Why it floods")).not.toBeInTheDocument();
    // The three things the popover always answers are still there.
    expect(screen.getByText("Safe until")).toBeInTheDocument();
    expect(screen.getByText(/Peak 1 cm/)).toBeInTheDocument();
  });
  it("titles a street OSM does not name by its display name, and says it is not the street's own", () => {
    const lane: SegmentPick = {
      ...PICK,
      segment: { ...PICK.segment, name: undefined, displayName: "off Dr Ambedkar Road" },
    };
    render(<SegmentPopover pick={lane} step={0} validTs={VALID_TS} onClose={() => {}} />);

    expect(screen.getByRole("heading", { name: "off Dr Ambedkar Road" })).toBeInTheDocument();
    expect(screen.getByText("OpenStreetMap does not name this street.")).toBeInTheDocument();
    expect(screen.queryByText(/unnamed/i)).not.toBeInTheDocument();
  });

  it("adds no note under a street's own OSM name", () => {
    render(<SegmentPopover pick={PICK} step={0} validTs={VALID_TS} onClose={() => {}} />);

    expect(screen.getByRole("heading", { name: "Dr Babasaheb Ambedkar Road" })).toBeInTheDocument();
    expect(screen.queryByText("OpenStreetMap does not name this street.")).not.toBeInTheDocument();
  });
});

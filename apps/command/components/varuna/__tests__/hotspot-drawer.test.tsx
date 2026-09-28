import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HotspotDrawer, PIPE_NOT_RECORDED } from "@/components/varuna/hotspot-drawer";
import type { Hotspot } from "@/lib/api/hotspots";

/**
 * Hindmata as the 08:40 cycle of MUM-2019-07-02 ranked it, trimmed to its first six steps, so the
 * drawer under test is rendering numbers a baked run produced rather than round ones invented here.
 *
 * Its attribution is empty on purpose, and that is also measured: with `drain1d` in the loop
 * (P7.7) the 51 pipes within five upstream hops of Hindmata were re-run and the best of them
 * explains 0.003 cm of a 10.1 cm peak, which is under the 0.1 cm floor. So the drawer's refusal
 * state is not hypothetical - it is what this junction does.
 */
const HINDMATA: Hotspot = {
  rank: 2,
  id: "MUM-HS-01",
  name: "Hindmata junction (Hindmata Cinema, Dr B. Ambedkar Marg)",
  slug: "hindmata-junction-hindmata-cinema-dr-b-ambedkar-",
  lon: 72.8421396,
  lat: 19.010099,
  ward: "F/S",
  isSink: false,
  sourceUrl:
    "https://www.freepressjournal.in/mumbai/mumbai-rains-knee-deep-water-accumulates-in-dadar-hindmata-due-to-heavy-downpour-video-surfaces",
  sourced: true,
  peakDepthCm: 10.1,
  peakTs: "2019-07-02T11:40:00+05:30",
  timeToPeakMin: 175,
  depthCm: [0.0, 0.1, 0.3, 0.6, 0.7, 0.9],
  pImpassableAtPeak: 0.0,
  impassableFromTs: null,
  minutesImpassable: 0,
  expectedImpact: 0.0,
  exposure: { weight: 0.783, facilities: ["shelter"] },
  // The junction's own segments, trimmed to three like the series above.
  segmentIds: ["S100841069-000", "S100841079-000", "S102172139-001"],
  attribution: [],
  attributionLabel:
    "51 pipes within 5 upstream hops were re-run on drain1d with the street frozen; the best of " +
    "them explains 0.003 cm, under the 0.10 cm this junction needs to name one. At this depth " +
    "the junction's inlets, not its pipes, are what limit the drain.",
  attributionCombined: null,
  attributionMethod: "drain1d, frozen surface",
  attributionCandidates: 51,
};

/**
 * Sion Subway 1 on the same cycle: the junction that *does* get a ranking.
 *
 * Every figure here was produced by `varuna_flash.whatif.attribute_pipes` on the 08:40 Twin depth
 * field - 14 of 55 candidate pipes above the floor, the top one explaining 1.62 cm of a 9.2 cm
 * peak, and cleaning all fourteen together taking it to 7.2 cm. Four rows are kept so the fixture
 * stays readable; the counts below are the real ones.
 */
const SION_SUBWAY: Hotspot = {
  ...HINDMATA,
  rank: 5,
  id: "MUM-HS-22",
  name: "Sion Subway 1",
  slug: "sion-subway-1",
  peakDepthCm: 9.2,
  segmentIds: ["S0-227", "S0-287"],
  attribution: [
    {
      rank: 1,
      pipeId: "MUM-E002730",
      segmentId: "S0-227",
      beta: 0.15,
      depthExplainedCm: 1.62,
      depthBeforeCm: 9.2,
    },
    {
      rank: 2,
      pipeId: "MUM-E002732",
      segmentId: "S0-227",
      beta: 0.15,
      depthExplainedCm: 1.56,
      depthBeforeCm: 9.2,
    },
    {
      rank: 3,
      pipeId: "MUM-E003304",
      segmentId: "S0-287",
      beta: 0.15,
      depthExplainedCm: 1.32,
      depthBeforeCm: 9.2,
    },
    {
      rank: 4,
      pipeId: "MUM-E003297",
      segmentId: "S0-287",
      beta: 0.15,
      depthExplainedCm: 1.31,
      depthBeforeCm: 9.2,
    },
  ],
  attributionLabel: null,
  attributionCombined: {
    nCleaned: 14,
    depthBeforeCm: 9.2,
    depthAfterCm: 7.2,
    depthExplainedCm: 1.98,
  },
  attributionMethod: "drain1d, frozen surface",
  attributionCandidates: 55,
};

const VALID_TS = [
  "2019-07-02T08:40:00+05:30",
  "2019-07-02T08:45:00+05:30",
  "2019-07-02T08:50:00+05:30",
  "2019-07-02T08:55:00+05:30",
  "2019-07-02T09:00:00+05:30",
  "2019-07-02T09:05:00+05:30",
];

describe("HotspotDrawer", () => {
  it("keeps 7.2's panel structure", () => {
    render(<HotspotDrawer hotspot={HINDMATA} step={0} validTs={VALID_TS} />);
    expect(screen.getByText("Why this junction floods")).toBeInTheDocument();
    expect(screen.getByText("Safe until")).toBeInTheDocument();
    expect(screen.getByText("Exposure")).toBeInTheDocument();
  });

  it("ranks the responsible pipes with their blockage and the depth each explains", () => {
    render(<HotspotDrawer hotspot={SION_SUBWAY} step={0} validTs={VALID_TS} />);
    const table = screen.getByRole("table", {
      name: /Inferred pipes ranked by the depth each explains/,
    });
    // Header plus one row per pipe. Nothing is drawn for a pipe that scored under the floor:
    // the products service never writes one (rule 6, and ADR-0042's own objection to a column
    // of 0.00 cm rows).
    expect(table.querySelectorAll("tbody tr")).toHaveLength(4);
    const first = table.querySelectorAll("tbody tr")[0];
    expect(first).toHaveTextContent("MUM-E002730");
    expect(first).toHaveTextContent("S0-227");
    expect(first).toHaveTextContent("0.15");
    expect(first).toHaveTextContent("1.62 cm");
  });

  it("names a row with no pipe by its segment, and one with neither as not recorded, never 'unnamed'", () => {
    // The parser writes an absent `pipe_id` as "", so the fallback must not stop at an empty id.
    const [a, b, c] = SION_SUBWAY.attribution;
    const hotspot: Hotspot = {
      ...SION_SUBWAY,
      attribution: [
        a!,
        { ...b!, pipeId: "", segmentId: "S0-227" },
        { ...c!, pipeId: "", segmentId: null },
      ],
    };
    render(<HotspotDrawer hotspot={hotspot} step={0} validTs={VALID_TS} />);
    const rows = screen
      .getByRole("table", { name: /Inferred pipes ranked/ })
      .querySelectorAll("tbody tr");
    // Every row is kept: its depth is a measurement, and the ranks stay 1, 2, 3.
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent(/^2S0-227/);
    expect(rows[2]).toHaveTextContent(PIPE_NOT_RECORDED);
    expect(rows[2]).toHaveTextContent("1.32 cm");
    expect(screen.queryByText(/unnamed/i)).not.toBeInTheDocument();
  });

  it("prints the combined effect of cleaning them together, before and after", () => {
    render(<HotspotDrawer hotspot={SION_SUBWAY} step={0} validTs={VALID_TS} />);
    expect(screen.getByText(/Cleaning these/)).toHaveTextContent(
      "Cleaning these 14 pipes together: 9.2 to 7.2 cm at the peak.",
    );
  });

  it("says when cleaning makes a junction deeper rather than clamping it at zero", () => {
    // Sion Circle on the same cycle: seven pipes each help alone, and together they cost it
    // 0.06 cm, because cleaning upstream delivers water faster than the junction can shed it.
    const sionCircle: Hotspot = {
      ...SION_SUBWAY,
      name: "Sion Circle",
      peakDepthCm: 7.5,
      attributionCombined: {
        nCleaned: 7,
        depthBeforeCm: 7.5,
        depthAfterCm: 7.6,
        depthExplainedCm: -0.06,
      },
    };
    render(<HotspotDrawer hotspot={sionCircle} step={0} validTs={VALID_TS} />);
    expect(screen.getByText(/Cleaning these/)).toHaveTextContent(
      "Cleaning these 7 pipes together: 7.5 to 7.6 cm at the peak, deeper than before: " +
        "cleaning upstream delivers more water than it removes.",
    );
  });

  it("names how many of the candidates were worth naming, and how it was measured", () => {
    render(<HotspotDrawer hotspot={SION_SUBWAY} step={0} validTs={VALID_TS} />);
    expect(screen.getByText(/pipes within 5 upstream hops/)).toHaveTextContent(
      "4 of 55 pipes within 5 upstream hops explain enough to be named. Measured on drain1d, " +
        "frozen surface: the street depth is held at this run’s forecast, so each figure is the " +
        "water the drain takes off the junction and an upper bound on what a coupled re-run " +
        "would remove. The drain graph is inferred.",
    );
  });

  it("carries the run's measured refusal when no pipe clears the floor", () => {
    render(<HotspotDrawer hotspot={HINDMATA} step={0} validTs={VALID_TS} />);
    expect(screen.getByText("No pipe is named for this junction")).toBeInTheDocument();
    expect(screen.getByText(/51 pipes within 5 upstream hops were re-run/)).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /Inferred pipes ranked/ })).not.toBeInTheDocument();
  });

  it("deep-links the what-if lab to the streets the ranked pipes run under (P7.11)", () => {
    render(<HotspotDrawer hotspot={SION_SUBWAY} step={0} validTs={VALID_TS} />);
    const link = screen.getByRole("button", { name: "Clean in what-if" });
    // Two streets, not four: the four ranked pipes run in series under two segments, and the
    // link de-duplicates them in rank order.
    expect(link).toHaveAttribute("href", "/whatif?segments=S0-227%2CS0-287&from=Sion+Subway+1");
    expect(screen.getByText(/Opens the lab on/)).toHaveTextContent(
      "Opens the lab on the 2 streets the ranked pipes run under.",
    );
  });

  it("falls back to the junction's own segments when nothing was attributed", () => {
    render(<HotspotDrawer hotspot={HINDMATA} step={0} validTs={VALID_TS} />);
    // An anchor carrying `role="button"`: the house pattern for a link styled as a button
    // (`render={<Link/>} nativeButton={false}`), as on the landing hero and the 404.
    const link = screen.getByRole("button", { name: "Clean in what-if" });
    expect(link).toHaveAttribute(
      "href",
      "/whatif?segments=S100841069-000%2CS100841079-000%2CS102172139-001&from=Hindmata+junction+%28Hindmata+Cinema%2C+Dr+B.+Ambedkar+Marg%29",
    );
    expect(screen.getByText(/Opens the lab with/)).toHaveTextContent(
      "Opens the lab with this junction’s 3 road segments",
    );
  });

  it("says the link carries the first fourteen when the junction has more", () => {
    // Hindmata has 25 in the baked run. The ids are in the register's own order, so a bare "14"
    // would read as the junction's whole set.
    const ids = Array.from({ length: 25 }, (_, i) => `S1008410${String(i).padStart(2, "0")}-000`);
    render(
      <HotspotDrawer hotspot={{ ...HINDMATA, segmentIds: ids }} step={0} validTs={VALID_TS} />,
    );
    expect(screen.getByText(/Opens the lab with/)).toHaveTextContent(
      "Opens the lab with the first 14 of this junction’s 25 road segments",
    );
    expect(screen.getByRole("button", { name: "Clean in what-if" }).getAttribute("href")).toContain(
      `segments=${ids.slice(0, 14).join("%2C")}`,
    );
  });

  it("offers nothing to clean when the junction has no segments recorded", () => {
    render(<HotspotDrawer hotspot={{ ...HINDMATA, segmentIds: [] }} step={0} validTs={VALID_TS} />);
    expect(screen.queryByRole("button", { name: "Clean in what-if" })).not.toBeInTheDocument();
    // The lab is named as the rail names it (ADR-0085), with the Sanskrit kept from translation.
    const note = screen.getByText(/nothing to send to/);
    expect(note).toHaveTextContent(
      "No road segments are recorded for this junction, so there is nothing to send to Kalpana, the what-if lab.",
    );
    expect(within(note).getByText("Kalpana")).toHaveAttribute("translate", "no");
  });

  it("leaves nothing in the drawer marked busy, since nothing in it is loading", () => {
    const { container } = render(<HotspotDrawer hotspot={HINDMATA} step={0} validTs={VALID_TS} />);
    expect(container.querySelectorAll("[aria-busy]")).toHaveLength(0);
    expect(container.querySelectorAll(".skeleton-shimmer")).toHaveLength(0);
  });

  it("draws the junction's ensemble band and says where it came from (ADR-0076)", () => {
    const n = HINDMATA.depthCm.length;
    const banded: Hotspot = {
      ...HINDMATA,
      depthP10Cm: HINDMATA.depthCm.map((cm) => Math.max(cm - 2, 0)),
      depthP90Cm: HINDMATA.depthCm.map((cm) => cm + 4),
      bandSegments: 25,
    };
    render(<HotspotDrawer hotspot={banded} step={0} validTs={VALID_TS} />);
    expect(n).toBeGreaterThan(0);
    expect(
      screen.getByText(/the band is the ensemble's p10 to p90 over the 25 streets registered/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/arrives with Flash-lite/)).not.toBeInTheDocument();
  });

  it("says why the band is flat on a run baked before junctions carried one", () => {
    render(<HotspotDrawer hotspot={HINDMATA} step={0} validTs={VALID_TS} />);
    expect(
      screen.getByText(/baked before junctions carried the ensemble's band/),
    ).toBeInTheDocument();
  });

  it("renders nothing without a hotspot", () => {
    const { container } = render(<HotspotDrawer hotspot={null} step={0} />);
    expect(container).toBeEmptyDOMElement();
  });
});

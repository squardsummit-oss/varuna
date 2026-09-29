import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { formatDate } from "@/lib/format";
import {
  OnboardFinishCard,
  detailLines,
  headlineFacts,
  stageListText,
  stormText,
  type FinishFacts,
} from "@/components/varuna/onboard-finish-card";

const RUN = "CHN-20260701T0040Z-sky1.0-twin1.0-flash0.0-baked";

/** The numbers the API read from that run on 2026-09-27 (services/api part 1, measured). */
const FACTS: FinishFacts = {
  runId: RUN,
  wetStreets: 15_472,
  streetsTotal: 18_622,
  wetThresholdCm: 5,
  medianPeakCm: 26,
  forecastMs: 46_512.3,
  stagesMs: 46_437,
  stages: ["Sky", "Twin", "Pulse", "products"],
  storm: { id: "CHN-IDF-25yr", totalMm: 150, durationMin: 180, peakMmH: 448.8, source: "manifest" },
};

describe("stormText", () => {
  it("states a design storm as its total, span and peak", () => {
    expect(stormText(FACTS.storm!)).toBe("150 mm in 3 h, peak 449 mm/h");
  });

  it("says 'over' for rain summed from the run rather than read from the manifest", () => {
    expect(
      stormText({ id: null, totalMm: 143.2, durationMin: 155, peakMmH: 448.8, source: "run" }),
    ).toBe("143 mm over 2 h 35 min, peak 449 mm/h");
  });

  it("has nothing to say without a total", () => {
    expect(
      stormText({ id: "CHN-IDF-25yr", totalMm: null, durationMin: 180, peakMmH: 50, source: null }),
    ).toBeNull();
  });
});

describe("stageListText", () => {
  it("joins the last two stages with 'and'", () => {
    expect(stageListText(["Sky", "Twin", "Pulse", "products"])).toBe(
      "Sky, Twin, Pulse and products",
    );
    expect(stageListText(["Twin"])).toBe("Twin");
    expect(stageListText([])).toBe("");
  });
});

/** What the screen adds once the map has loaded the run's own depths. */
const DEPTH_FACTS: FinishFacts = {
  ...FACTS,
  flooded: { count: 9_214, total: 18_622, thresholdCm: 15 },
  deepest: {
    cm: 366.6,
    at: "2026-07-01T07:25:00+05:30",
    leadMin: 75,
    name: "Anna Salai",
  },
  issuedAt: "2026-07-01T06:10:00+05:30",
  horizonMin: 155,
};

describe("headlineFacts", () => {
  it("leads with streets above 15 cm, the deepest street and when the forecast was issued", () => {
    const [flooded, deepest, issued] = headlineFacts(DEPTH_FACTS);
    expect(flooded).toEqual({
      label: "Flooded streets",
      value: "9,214",
      subs: ["of 18,622", "above 15 cm"],
    });
    expect(deepest).toEqual({
      label: "Deepest street",
      value: "367 cm",
      subs: ["07:25 (+75 min)", "Anna Salai"],
    });
    expect(issued.label).toBe("Forecast issued");
    expect(issued.value).toBe("06:10");
    expect(issued.subs).toEqual([formatDate("2026-07-01T06:10:00+05:30"), "2 h 35 min ahead"]);
  });

  it("falls back to the record's own numbers before the depths have loaded", () => {
    const [flooded, deepest, issued] = headlineFacts(FACTS);
    expect(flooded).toEqual({
      label: "Wet streets",
      value: "15,472",
      subs: ["of 18,622", "above 5 cm"],
    });
    expect(deepest).toEqual({ label: "Median street peak", value: "26 cm" });
    expect(issued).toEqual({ label: "Forecast computed in", value: "46.5 s" });
  });

  it("leaves the street unnamed rather than guessing one", () => {
    const [, deepest] = headlineFacts({
      ...DEPTH_FACTS,
      deepest: { ...DEPTH_FACTS.deepest!, name: null },
    });
    expect(deepest.subs).toEqual(["07:25 (+75 min)"]);
  });
});

describe("detailLines", () => {
  it("keeps the storm, the 5 cm count, the median and the timings behind Details", () => {
    expect(detailLines(DEPTH_FACTS, "Chennai")).toEqual([
      "Design storm CHN-IDF-25yr: 150 mm in 3 h, peak 449 mm/h",
      "Streets above 5 cm: 15,472 of 18,622",
      "Median street peak: 26 cm",
      "Forecast computed in 46.5 s; Sky, Twin, Pulse and products took 46.4 s of it",
    ]);
  });

  it("does not repeat what the headline already shows", () => {
    const lines = detailLines(FACTS, "Chennai");
    expect(lines.some((line) => line.startsWith("Streets above"))).toBe(false);
    expect(lines.some((line) => line.startsWith("Median"))).toBe(false);
    expect(lines).toContain("Sky, Twin, Pulse and products took 46.4 s of it");
  });
});

describe("OnboardFinishCard", () => {
  it("holds the honesty chips and a disabled button before a forecast has been read", () => {
    render(<OnboardFinishCard cityName="Chennai" facts={null} href={null} />);
    const card = within(screen.getByRole("region", { name: "First forecast" }));
    expect(card.getByText("Design storm")).toBeInTheDocument();
    expect(card.getByText("Uncalibrated")).toBeInTheDocument();
    expect(
      card.getByText("Press Start onboarding to generate Chennai's flood forecast."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Chennai console" })).toBeDisabled();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("First forecast numbers")).not.toBeInTheDocument();
  });

  it("shows the note in view while there are no numbers, because then it is the explanation", () => {
    render(
      <OnboardFinishCard
        cityName="Chennai"
        facts={null}
        href={null}
        note="The recorded forecast is not on this API."
      />,
    );
    expect(screen.getByText("The recorded forecast is not on this API.")).toBeVisible();
  });

  it("prints three headline numbers and links the run's console", () => {
    render(
      <OnboardFinishCard
        cityName="Chennai"
        facts={DEPTH_FACTS}
        href={`/console?city=chennai&run=${RUN}`}
        note="From the previous build."
      />,
    );
    const numbers = within(screen.getByLabelText("First forecast numbers"));
    expect(numbers.getByText("Flooded streets")).toBeInTheDocument();
    expect(numbers.getByText("9,214")).toBeInTheDocument();
    expect(numbers.getByText("of 18,622")).toBeInTheDocument();
    expect(numbers.getByText("above 15 cm")).toBeInTheDocument();
    expect(numbers.getByText("367 cm")).toBeInTheDocument();
    expect(numbers.getByText("07:25 (+75 min)")).toBeInTheDocument();
    expect(numbers.getByText("Anna Salai")).toBeInTheDocument();
    expect(numbers.getByText("06:10")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Chennai console" })).toHaveAttribute(
      "href",
      `/console?city=chennai&run=${RUN}`,
    );
    // The run id, the storm and the note are one click away, never deleted.
    const card = screen.getByRole("region", { name: "First forecast" });
    expect(card).toHaveTextContent(RUN);
    expect(card).toHaveTextContent("Design storm CHN-IDF-25yr: 150 mm in 3 h, peak 449 mm/h");
    expect(card).toHaveTextContent("From the previous build.");
    expect(screen.getByText("Details").closest("details")).not.toHaveAttribute("open");
  });

  it("says a number was not recorded rather than inventing one", () => {
    render(
      <OnboardFinishCard
        cityName="Chennai"
        facts={{
          ...FACTS,
          wetStreets: null,
          medianPeakCm: null,
          forecastMs: null,
          stagesMs: null,
          stages: [],
          storm: null,
        }}
        href={null}
      />,
    );
    expect(screen.getAllByText("Not recorded")).toHaveLength(3);
    expect(screen.queryByText(/Design storm CHN/)).not.toBeInTheDocument();
  });

  it("fades the numbers in with the forecast's depth layer (M19)", () => {
    render(<OnboardFinishCard cityName="Chennai" facts={FACTS} href={null} factsOpacity={0.25} />);
    expect(screen.getByLabelText("First forecast numbers")).toHaveStyle({ opacity: "0.25" });
  });
});

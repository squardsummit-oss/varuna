import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  OnboardFinishCard,
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

describe("OnboardFinishCard", () => {
  it("holds the sentence and a disabled button before a forecast has been read", () => {
    render(<OnboardFinishCard cityName="Chennai" facts={null} href={null} />);
    expect(
      screen.getByText(
        "First forecast, uncalibrated. VARUNA learns Chennai's drains from the next monsoon.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Chennai console" })).toBeDisabled();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("First forecast numbers")).not.toBeInTheDocument();
  });

  it("prints the run's own numbers and links its console", () => {
    render(
      <OnboardFinishCard
        cityName="Chennai"
        facts={FACTS}
        href={`/console?city=chennai&run=${RUN}`}
        note="Built in 51 s in this session."
      />,
    );
    expect(screen.getByText(RUN)).toBeInTheDocument();
    const numbers = within(screen.getByLabelText("First forecast numbers"));
    expect(numbers.getByText("Wet streets, 5 cm or more")).toBeInTheDocument();
    expect(numbers.getByText("15,472 of 18,622")).toBeInTheDocument();
    expect(numbers.getByText("26 cm")).toBeInTheDocument();
    expect(numbers.getByText("Design storm CHN-IDF-25yr")).toBeInTheDocument();
    expect(numbers.getByText("150 mm in 3 h, peak 449 mm/h")).toBeInTheDocument();
    // The wall time the first forecast's row prints, then the stage sum under its own label.
    expect(numbers.getByText("Forecast computed in")).toBeInTheDocument();
    expect(numbers.getByText("46.5 s")).toBeInTheDocument();
    expect(
      numbers.getByText("Sky, Twin, Pulse and products took 46.4 s of it"),
    ).toBeInTheDocument();
    expect(screen.getByText("Built in 51 s in this session.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Chennai console" })).toHaveAttribute(
      "href",
      `/console?city=chennai&run=${RUN}`,
    );
  });

  it("labels a stage sum as a stage sum when no build recorded the wall time", () => {
    render(
      <OnboardFinishCard cityName="Chennai" facts={{ ...FACTS, forecastMs: null }} href={null} />,
    );
    const numbers = within(screen.getByLabelText("First forecast numbers"));
    expect(numbers.queryByText("Forecast computed in")).not.toBeInTheDocument();
    expect(numbers.getByText("Sky, Twin, Pulse and products")).toBeInTheDocument();
    expect(numbers.getByText("46.4 s")).toBeInTheDocument();
    expect(numbers.queryByText(/of it/)).not.toBeInTheDocument();
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
    expect(screen.queryByText(/Design storm/)).not.toBeInTheDocument();
  });

  it("fades the numbers in with the forecast's depth layer (M19)", () => {
    render(<OnboardFinishCard cityName="Chennai" facts={FACTS} href={null} factsOpacity={0.25} />);
    expect(screen.getByLabelText("First forecast numbers")).toHaveStyle({ opacity: "0.25" });
  });
});

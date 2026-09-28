import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { VerificationThresholdChart } from "@/components/varuna/verification-threshold-chart";

/** The sweep `/v1/verification` served for MUM-2019-07-02 on 2026-09-22. */
const SERVED = [
  { thresholdCm: 15, csi: 0.219, pod: 0.412, far: 0.682 },
  { thresholdCm: 5, csi: 0.143, pod: 1.0, far: 0.857 },
  { thresholdCm: 30, csi: 0.091, pod: 0.118, far: 0.714 },
];

describe("VerificationThresholdChart (7.10)", () => {
  it("states the ground-truth count and every point it draws", () => {
    render(<VerificationThresholdChart points={SERVED} groundTruthCount={17} />);
    expect(screen.getByText("n = 17 sourced pins in the window")).toBeInTheDocument();
    const figure = screen.getByRole("img");
    const label = figure.getAttribute("aria-label") ?? "";
    // Sorted by threshold, each with its three scores.
    expect(label).toContain("on 17 sourced pins");
    expect(label.indexOf("5 cm")).toBeLessThan(label.indexOf("15 cm"));
    expect(label).toContain("15 cm CSI 0.22, POD 0.41, FAR 0.68");
    expect(label).toContain("30 cm CSI 0.09, POD 0.12, FAR 0.71");
  });

  it("names which way each score is good", () => {
    render(<VerificationThresholdChart points={SERVED} groundTruthCount={17} />);
    expect(screen.getByText("CSI, higher is better")).toBeInTheDocument();
    expect(screen.getByText("FAR, lower is better")).toBeInTheDocument();
  });

  it("calls a missing denominator what it is rather than zero", () => {
    render(
      <VerificationThresholdChart
        points={[{ thresholdCm: 60, csi: null, pod: null, far: null }]}
        groundTruthCount={17}
      />,
    );
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain(
      "60 cm CSI no denominator",
    );
  });

  it("tells the reader what to do when nothing is scored", () => {
    render(<VerificationThresholdChart points={[]} groundTruthCount={null} />);
    expect(screen.getByText("Not scored yet")).toBeInTheDocument();
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DepthChip, depthBandLabel } from "@/components/varuna/depth-chip";

describe("DepthChip", () => {
  it("shows the number with its unit in tabular figures", () => {
    render(<DepthChip cm={45} />);
    const value = screen.getByText("45 cm");
    expect(value).toHaveClass("num");
  });

  it("carries the band label as the tooltip so colour is never the only carrier", () => {
    render(<DepthChip cm={45} />);
    const chip = screen.getByTitle(depthBandLabel(45));
    expect(chip).toHaveAttribute("title", expect.stringContaining("45-60 cm"));
    expect(chip.getAttribute("aria-label")).toContain("45 cm");
  });

  it("colours the dot from the depth token, not a literal", () => {
    const { container } = render(<DepthChip cm={52} />);
    const dot = container.querySelector("span[aria-hidden='true']") as HTMLElement;
    expect(dot.style.backgroundColor).toBe("var(--depth-4)");
  });

  it("prints the band next to the number when asked", () => {
    render(<DepthChip cm={20} showBand />);
    expect(screen.getByText("20 cm")).toBeInTheDocument();
    expect(screen.getByText("15-30 cm")).toBeInTheDocument();
  });

  it("says no data for a missing depth", () => {
    render(<DepthChip cm={null} />);
    expect(screen.getByText("no data")).toHaveClass("text-text-3");
  });

  it("rounds fractional centimetres", () => {
    render(<DepthChip cm={12.6} size="sm" />);
    expect(screen.getByText("13 cm")).toBeInTheDocument();
  });
});

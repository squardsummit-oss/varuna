/**
 * The wizard's layer panel offers two plain switches, Streets and Flooded streets, and nothing
 * under them: an officer reading the map should not have to parse counts or pipeline step names.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OnboardLayers, type WizardLayerId, type WizardLayerState } from "./onboard-layers";

function state(over: Partial<Record<WizardLayerId, WizardLayerState>> = {}) {
  return {
    streets: { on: true, count: 18_626 },
    buildings: { on: false, lazy: true },
    drains: { on: true, count: 40_731, detail: "4,000 drawn" },
    depth: { on: true },
    ...over,
  } satisfies Record<WizardLayerId, WizardLayerState>;
}

/** Base UI marks a disabled switch with `data-disabled` and `aria-disabled`, not `disabled`. */
function disabled(toggle: HTMLElement): boolean {
  return toggle.hasAttribute("data-disabled") || toggle.getAttribute("aria-disabled") === "true";
}

describe("OnboardLayers", () => {
  it("offers only streets and flooded streets", () => {
    render(<OnboardLayers value={state()} onChange={vi.fn()} />);
    expect(screen.getAllByRole("switch")).toHaveLength(2);
    expect(screen.getByRole("switch", { name: "Streets" })).toBeTruthy();
    expect(screen.getByRole("switch", { name: "Flooded streets" })).toBeTruthy();
    expect(screen.queryByText("Buildings")).toBeNull();
    expect(screen.queryByText("Drains")).toBeNull();
  });

  it("prints no counts or helper text under the switches", () => {
    render(<OnboardLayers value={state()} onChange={vi.fn()} />);
    expect(screen.queryByText("18,626")).toBeNull();
    expect(screen.queryByText(/Loads when switched on/)).toBeNull();
    expect(screen.queryByText(/Not yet/)).toBeNull();
  });

  it("keeps flooded streets switched off until the forecast has arrived", () => {
    const onChange = vi.fn();
    render(<OnboardLayers value={state()} onChange={onChange} />);
    expect(disabled(screen.getByRole("switch", { name: "Flooded streets" }))).toBe(true);
    fireEvent.click(screen.getByRole("switch", { name: "Streets" }));
    expect(onChange).toHaveBeenCalledWith("streets", false);
  });
});

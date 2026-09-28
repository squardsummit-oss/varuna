/**
 * The wizard's layer panel says what the pipeline wrote, not what the map happens to draw: a
 * capped drain layer reads its full count with the drawn share beside it, and buildings - off by
 * default and fetched only when asked for - can be switched on before they have arrived.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
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

function row(label: string): HTMLElement {
  // The label element itself: Base UI's switch also carries a hidden input the label names.
  const item = screen.getByText(label, { selector: "label" }).closest("li");
  if (!item) throw new Error(`no row for ${label}`);
  return item;
}

/** Base UI marks a disabled switch with `data-disabled` and `aria-disabled`, not `disabled`. */
function disabled(toggle: HTMLElement): boolean {
  return toggle.hasAttribute("data-disabled") || toggle.getAttribute("aria-disabled") === "true";
}

describe("OnboardLayers", () => {
  it("prints every pipe the pipeline wrote, and how many of them the map draws", () => {
    render(<OnboardLayers value={state()} onChange={vi.fn()} />);
    const drains = within(row("Drains"));
    expect(drains.getByText("40,731")).toBeTruthy();
    expect(drains.getByText("4,000 drawn")).toBeTruthy();
  });

  it("lets buildings be switched on before they are fetched, and says they load then", () => {
    const onChange = vi.fn();
    render(<OnboardLayers value={state()} onChange={onChange} />);
    const buildings = within(row("Buildings"));
    expect(buildings.getByText("Loads when switched on")).toBeTruthy();
    const toggle = screen.getByRole("switch", { name: "Buildings" });
    expect(disabled(toggle)).toBe(false);
    fireEvent.click(toggle);
    expect(onChange).toHaveBeenCalledWith("buildings", true);
  });

  it("says which step writes a layer that has not arrived, and keeps it switched off", () => {
    render(<OnboardLayers value={state()} onChange={vi.fn()} />);
    expect(
      within(row("First forecast")).getByText("Not yet, written by First forecast"),
    ).toBeTruthy();
    expect(disabled(screen.getByRole("switch", { name: "First forecast" }))).toBe(true);
  });
});

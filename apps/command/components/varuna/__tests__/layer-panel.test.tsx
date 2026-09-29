/**
 * The console's layer panel, and in particular that no switch on it is silent.
 *
 * SPEC.md 17: "every P1 button reads 'coming in pilot' with one sentence of plan - never a dead
 * control", and the panel's own docstring makes that a rule about layers: where a layer needs
 * something first, the row's detail line says what. Two of those rows are new - the
 * photorealistic city and the drain X-ray - and this file asserts every state each row can be in,
 * including the ones it is **not** in today. Since 2026-09-23 the key's project is billed with the
 * Map Tiles API on, so the photorealistic row renders its ready sentence and the tiles draw; the
 * unavailable sentences are fixtures for a project that has not been through those switches. The
 * drain row's empty state is likewise dormant now that `varuna_city.export` keeps the invert
 * elevations, and still reachable by any city exported before that.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LayerPanel, type LayerToggles } from "../layer-panel";

const ALL_OFF: LayerToggles = {
  probability: false,
  raster: false,
  segments: false,
  surcharge: false,
  drains: false,
  hotspots: false,
  isochrones: false,
  routes: false,
  threeD: false,
  xray: false,
};

describe("LayerPanel", () => {
  it("offers section 7.2's layers and no others", () => {
    render(<LayerPanel value={ALL_OFF} onChange={() => undefined} />);
    const names = screen.getAllByRole("switch").map((el) => el.textContent?.replace(/\s+/g, " "));
    expect(names.map((n) => n?.replace(/\s*[A-Z0-9]$/, "").trim())).toEqual([
      "Streets (depth)",
      "Depth raster",
      "Probability",
      "Surcharge",
      "Drains",
      "Ground truth",
      "Isochrones",
      "Routes",
      "3D city",
    ]);
    // Removed: the imagery is the basemap and always on, and the footprints only greyed the roofs.
    expect(screen.queryByRole("switch", { name: /Satellite/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: /Buildings/ })).not.toBeInTheDocument();
  });

  it("offers the drain X-ray under the 3D city, and only while it is on", () => {
    const { rerender } = render(<LayerPanel value={ALL_OFF} onChange={() => undefined} />);
    expect(screen.queryByRole("switch", { name: /Drain X-ray/ })).not.toBeInTheDocument();
    rerender(<LayerPanel value={{ ...ALL_OFF, threeD: true }} onChange={() => undefined} />);
    expect(screen.getByRole("switch", { name: /Drain X-ray/ })).toBeInTheDocument();
  });

  it("no longer offers the 3D terrain the photorealistic city replaced", () => {
    render(<LayerPanel value={ALL_OFF} onChange={() => undefined} />);
    expect(screen.queryByText("3D terrain")).not.toBeInTheDocument();
  });

  it("reports which layer was switched, and to what", async () => {
    const onChange = vi.fn();
    render(<LayerPanel value={{ ...ALL_OFF, threeD: true }} onChange={onChange} />);
    await userEvent.click(screen.getByRole("switch", { name: /Drain X-ray/ }));
    expect(onChange).toHaveBeenCalledWith("xray", true);
  });

  it("prints a row's detail whether or not the layer is on", () => {
    // The point of the rule: switching 3D on when the API is disabled must not look like a map
    // that failed. The sentence is on screen with the switch still off.
    render(
      <LayerPanel
        value={ALL_OFF}
        onChange={() => undefined}
        details={{ threeD: "Google's Map Tiles API is not enabled on this key's project." }}
      />,
    );
    expect(screen.getByText(/Map Tiles API is not enabled/)).toBeInTheDocument();
  });

  it("marks each switch's state for a reader who cannot see the toggle", () => {
    render(<LayerPanel value={{ ...ALL_OFF, threeD: true }} onChange={() => undefined} />);
    expect(screen.getByRole("switch", { name: /3D city/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: /Drain X-ray/ })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });
});

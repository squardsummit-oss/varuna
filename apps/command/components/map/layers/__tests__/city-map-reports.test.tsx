/**
 * `CityMap`'s two changes for citizen reports: building footprints are off unless asked for, and
 * report pins are drawn only when a screen passes reports - with nothing else about the map
 * changing when it does not.
 */

import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CityMapProps } from "../../city-map";
import type { ReportPin } from "@/lib/api/reports";
import * as fx from "./fixture";

interface Recorded {
  layers: { id: string }[];
  getTooltip?: (info: { object?: unknown; layer?: unknown }) => unknown;
}

const renders: Recorded[] = [];

vi.mock("@deck.gl/react", () => ({
  default: (props: Record<string, unknown>) => {
    renders.push({
      layers: props.layers as { id: string }[],
      getTooltip: props.getTooltip as Recorded["getTooltip"],
    });
    return null;
  },
}));

// Imported after the mock so `CityMap` binds to the recorder.
const { CityMap } = await import("../../city-map");

beforeEach(() => {
  renders.length = 0;
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function ids(): string[] {
  const recorded = renders.at(-1);
  if (!recorded) throw new Error("CityMap never rendered DeckGL");
  return recorded.layers.map((layer) => layer.id);
}

/** The console fixture without the explicit `showBuildings`, i.e. a caller that says nothing. */
function withoutBuildingsFlag(overrides: Partial<CityMapProps> = {}): CityMapProps {
  const { showBuildings: _omit, ...props } = fx.consoleProps(overrides);
  void _omit;
  return props;
}

const reports: ReportPin[] = [
  {
    id: "rpt-1",
    lon: 72.841,
    lat: 19.012,
    depthHint: "knee",
    depthCm: 45,
    status: "crew_sent",
    statusLabel: "Crew sent (demo status)",
    statusSeeded: true,
    ts: "2019-07-02T08:47:00+05:30",
    text: null,
    place: "Hindmata junction",
    thumbUrl:
      "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a8/Bombay_flooded_street.jpg/330px-Bombay_flooded_street.jpg",
    photoUrl: null,
    credit: "Photo: Hitesh Ashar, CC BY 2.0, via Wikimedia Commons",
    synthetic: true,
    origin: "seed",
  },
];

describe("CityMap building footprints", () => {
  it("are off when the caller does not ask for them, even with footprints loaded", () => {
    render(<CityMap {...withoutBuildingsFlag()} />);
    expect(ids()).not.toContain("buildings");
  });

  it("are drawn when asked for", () => {
    render(<CityMap {...fx.consoleProps({ showBuildings: true })} />);
    expect(ids()).toContain("buildings");
  });
});

describe("CityMap report pins", () => {
  it("draws none by default", () => {
    render(<CityMap {...fx.consoleProps()} />);
    expect(ids().some((id) => id.startsWith("report-"))).toBe(false);
  });

  it("draws them over the overlays and under the labels", () => {
    render(<CityMap {...fx.consoleProps({ reports, onPickReport: () => {} })} />);
    const list = ids();
    const pins = list.indexOf("report-pins");
    expect(pins).toBeGreaterThan(-1);
    expect(list).toContain("report-photo-rings");
    // Every label layer comes after the pins, so a street name is never hidden under one.
    const firstLabel = list.findIndex((id) => id.includes("label"));
    if (firstLabel > -1) expect(firstLabel).toBeGreaterThan(pins);
  });

  it("names a hovered pin and leaves the street tooltip to the streets", () => {
    render(<CityMap {...fx.consoleProps({ reports, onPickReport: () => {} })} />);
    const recorded = renders.at(-1);
    const tooltip = recorded?.getTooltip;
    expect(tooltip).toBeTypeOf("function");
    const onPin = tooltip?.({ object: reports[0], layer: { id: "report-pins" } }) as {
      text: string;
    };
    expect(onPin.text).toContain("Hindmata junction");
    expect(onPin.text).toContain("Demo report (synthetic)");
    // Nothing under the cursor is no tooltip at all.
    expect(tooltip?.({ layer: undefined })).toBeNull();
  });

  it("hands deck the plain street tooltip when there are no pickable pins", () => {
    render(<CityMap {...fx.consoleProps()} />);
    const plain = renders.at(-1)?.getTooltip;
    render(<CityMap {...fx.consoleProps({ reports })} />);
    // Reports without a handler are not pickable, so the tooltip stays the street one.
    expect(String(renders.at(-1)?.getTooltip)).toBe(String(plain));
  });
});

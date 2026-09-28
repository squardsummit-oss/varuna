import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useGoogleDeckOverlay, type OverlayFactory } from "./overlay";

interface Fake {
  interleaved: boolean;
  attached: unknown[];
  layers: unknown[][];
  finalized: number;
}

function fakeFactory() {
  const made: Fake[] = [];
  const factory: OverlayFactory = (options) => {
    const fake: Fake = {
      interleaved: options.interleaved,
      attached: [],
      layers: [],
      finalized: 0,
    };
    made.push(fake);
    return {
      setMap: (map) => fake.attached.push(map),
      setProps: ({ layers }) => fake.layers.push(layers),
      finalize: () => {
        fake.finalized += 1;
      },
    };
  };
  return { made, factory };
}

function Host({
  map,
  layers,
  factory,
}: {
  map: object | null;
  layers: readonly unknown[];
  factory: OverlayFactory;
}) {
  useGoogleDeckOverlay(map, layers, factory);
  return null;
}

describe("useGoogleDeckOverlay", () => {
  it("creates one overlay in overlaid mode and disposes it on unmount", () => {
    const { made, factory } = fakeFactory();
    const map = { id: "map" };
    const layers = [{ id: "streets" }];

    const view = render(<Host map={map} layers={layers} factory={factory} />);
    view.rerender(<Host map={map} layers={layers} factory={factory} />);
    view.rerender(<Host map={map} layers={layers} factory={factory} />);

    expect(made).toHaveLength(1);
    // Interleaved rendering aliases every path on Google's context (TECH_SPEC 2.2).
    expect(made[0].interleaved).toBe(false);
    expect(made[0].attached).toEqual([map]);
    expect(made[0].finalized).toBe(0);

    view.unmount();

    // Detached before finalize, so Google never redraws through a disposed renderer.
    expect(made[0].attached).toEqual([map, null]);
    expect(made[0].finalized).toBe(1);
  });

  it("draws the mounting render's layers on the first frame", () => {
    const { made, factory } = fakeFactory();
    const layers = [{ id: "streets" }];
    render(<Host map={{}} layers={layers} factory={factory} />);
    expect(made[0].layers[0]).toEqual(layers);
  });

  it("pushes new layers through the same overlay rather than making another", () => {
    const { made, factory } = fakeFactory();
    const map = {};
    const view = render(<Host map={map} layers={[{ id: "a" }]} factory={factory} />);
    view.rerender(<Host map={map} layers={[{ id: "b" }]} factory={factory} />);

    expect(made).toHaveLength(1);
    expect(made[0].layers.at(-1)).toEqual([{ id: "b" }]);
  });

  it("builds nothing while there is no map - the fallback map's ordinary state", () => {
    const { made, factory } = fakeFactory();
    const view = render(<Host map={null} layers={[]} factory={factory} />);
    expect(made).toHaveLength(0);

    const map = {};
    view.rerender(<Host map={map} layers={[{ id: "a" }]} factory={factory} />);
    expect(made).toHaveLength(1);
    expect(made[0].layers.at(-1)).toEqual([{ id: "a" }]);
  });

  it("replaces the overlay when the map instance itself is replaced", () => {
    const { made, factory } = fakeFactory();
    const view = render(<Host map={{ id: 1 }} layers={[]} factory={factory} />);
    view.rerender(<Host map={{ id: 2 }} layers={[]} factory={factory} />);

    expect(made).toHaveLength(2);
    expect(made[0].finalized).toBe(1);
    expect(made[1].finalized).toBe(0);
    vi.clearAllMocks();
  });
});

/**
 * The learned overlay `/drains` draws (`withLearned`): the whole network quiet, and over it only
 * the pipes Pulse moved, each with a glow; cleared pipes in `--naive`; the before/after split as a
 * clip (motion M30), and M12's cross-fade and the pipe hover on the learned pipes only.
 *
 * The plain path every other screen uses is pinned by the MO1 equivalence fixture and by
 * `seams.test.tsx`; nothing here may change it.
 */

import { describe, expect, it, vi } from "vitest";

import {
  CLEARED_DRAIN,
  HALO_ALPHA,
  QUIET_DRAIN,
  SPLIT_ALL_AFTER,
  SPLIT_ALL_BEFORE,
  drainsLayers,
  haloColour,
  learnedColour,
  priorColour,
  withLearned,
  type LearnedDrain,
} from "../drains";
import { drainColour } from "../palette";
import * as fx from "./fixture";
import { serializeLayers } from "./serialize";

function learned(
  id: string,
  prior: number,
  post: number,
  over: Partial<LearnedDrain> = {},
): LearnedDrain {
  return {
    id,
    path: [
      [72.84, 19.01],
      [72.842, 19.012],
    ],
    beta: post,
    diameter: 0.6,
    prior,
    post,
    sd: 0.1,
    direction: post < prior ? "down" : "up",
    name: null,
    locality: null,
    capacityLostPct: 40,
    lastUpdate: "2019-07-02T08:40:00+05:30",
    ...over,
  };
}

const RISE = learned("E-rise", 0.15, 0.49, { diameter: 3 });
const FALL = learned("E-fall", 0.2, 0.0047);

type Drawn = { id: string; props: Record<string, unknown> };

function ids(layers: unknown[]): string[] {
  return (layers as Drawn[]).map((l) => l.id);
}

function byId(layers: unknown[], id: string): Drawn {
  const found = (layers as Drawn[]).find((l) => l.id === id);
  if (!found) throw new Error(`no layer ${id}`);
  return found;
}

describe("drainsLayers with a learned overlay", () => {
  it("draws the network quiet, then the glow, the cleared pipes and the raised pipes", () => {
    const layers = drainsLayers({
      drains: withLearned(fx.drains, { learned: [RISE, FALL], splitLon: SPLIT_ALL_AFTER }),
      show: true,
    });
    expect(ids(layers)).toEqual([
      "drains",
      "drains-learned-halo",
      "drains-learned-cleared",
      "drains-learned",
    ]);
    const quiet = byId(layers, "drains");
    // The base network is the array handed in, by reference, drawn in one colour and never picked.
    expect(quiet.props.data).toBe(fx.drains);
    expect(quiet.props.getColor).toEqual(QUIET_DRAIN);
    expect(quiet.props.pickable).toBe(false);
    expect((byId(layers, "drains-learned").props.data as LearnedDrain[]).map((d) => d.id)).toEqual([
      "E-rise",
    ]);
    expect(
      (byId(layers, "drains-learned-cleared").props.data as LearnedDrain[]).map((d) => d.id),
    ).toEqual(["E-fall"]);
  });

  it("glows by learned change: the raised pipe in its band, the cleared one in --naive", () => {
    const [r, g, b] = drainColour(0.49);
    expect(learnedColour(RISE)).toEqual([r, g, b, 255]);
    // The glow is one neutral light for every learned pipe: it says "learned", the stroke says
    // the band. A band-tinted glow on a pipe that stayed under 0.25 would be the quiet colour.
    expect(haloColour()).toEqual([227, 234, 246, HALO_ALPHA]);
    expect(learnedColour(FALL)).toEqual(CLEARED_DRAIN);
    const [pr, pg, pb] = drainColour(0.2);
    expect(priorColour(FALL)).toEqual([pr, pg, pb, 235]);
  });

  it("with nothing learned, still draws the quiet network and nothing over it", () => {
    const layers = drainsLayers({
      drains: withLearned(fx.drains, { learned: [], splitLon: SPLIT_ALL_AFTER }),
      show: true,
    });
    expect(ids(layers)).toEqual(["drains"]);
  });

  it("splits at a longitude: the prior clipped west of it, what Pulse learned east (M30)", () => {
    const split = 72.85;
    const layers = drainsLayers({
      drains: withLearned(fx.drains, { learned: [RISE, FALL], splitLon: split }),
      show: true,
    });
    expect(byId(layers, "drains-learned-prior").props.clipBounds).toEqual([-180, -90, split, 90]);
    for (const id of ["drains-learned-halo", "drains-learned-cleared", "drains-learned"]) {
      expect(byId(layers, id).props.clipBounds).toEqual([split, -90, 180, 90]);
    }
  });

  it("draws only the prior at Before, and only the learning at After", () => {
    const before = drainsLayers({
      drains: withLearned(fx.drains, { learned: [RISE, FALL], splitLon: SPLIT_ALL_BEFORE }),
      show: true,
    });
    expect(ids(before)).toEqual(["drains", "drains-learned-prior"]);
    const after = drainsLayers({
      drains: withLearned(fx.drains, { learned: [RISE, FALL], splitLon: SPLIT_ALL_AFTER }),
      show: true,
    });
    expect(ids(after)).not.toContain("drains-learned-prior");
  });

  it("cross-fades the learned pipes' colour (M12) and leaves the quiet network instant", () => {
    const layers = drainsLayers({
      drains: withLearned(fx.drains, { learned: [RISE, FALL], splitLon: 72.85 }),
      show: true,
      crossFadeMs: 300,
    });
    expect(byId(layers, "drains").props.transitions).toBeFalsy();
    for (const id of ["drains-learned-prior", "drains-learned-halo", "drains-learned"]) {
      expect(byId(layers, id).props.transitions).toEqual({ getColor: 300 });
    }
    // Reduced motion passes 0: no transition at all.
    const still = drainsLayers({
      drains: withLearned(fx.drains, { learned: [RISE], splitLon: SPLIT_ALL_AFTER }),
      show: true,
      crossFadeMs: 0,
    });
    expect(byId(still, "drains-learned").props.transitions).toBeFalsy();
  });

  it("hovers the learned pipes only, and hands back the pipe with where it is", () => {
    const onHover = vi.fn();
    const layers = drainsLayers({
      drains: withLearned(fx.drains, { learned: [RISE, FALL], splitLon: SPLIT_ALL_AFTER }),
      show: true,
      onHover,
    });
    expect(byId(layers, "drains").props.pickable).toBe(false);
    expect(byId(layers, "drains-learned-halo").props.pickable).toBe(false);
    const top = byId(layers, "drains-learned");
    expect(top.props.pickable).toBe(true);
    (top.props.onHover as (info: unknown) => void)({ object: RISE, x: 10, y: 20 });
    expect(onHover).toHaveBeenLastCalledWith({ drain: RISE, x: 10, y: 20 });
    (top.props.onHover as (info: unknown) => void)({ object: null, x: 0, y: 0 });
    expect(onHover).toHaveBeenLastCalledWith(null);
  });

  it("reports deck's viewport through a probe that draws nothing", () => {
    const onViewport = vi.fn();
    const layers = drainsLayers({
      drains: withLearned(fx.drains, { learned: [RISE], splitLon: SPLIT_ALL_AFTER, onViewport }),
      show: true,
    });
    expect(ids(layers).at(-1)).toBe("drains-split-probe");
  });

  it("changes nothing on the plain path the console and the wizard draw", () => {
    expect(serializeLayers(drainsLayers({ drains: fx.drains, show: true }))).toEqual(
      serializeLayers(
        drainsLayers({
          drains: fx.drains.slice(),
          show: true,
          crossFadeMs: 300,
          onHover: () => {},
        }),
      ),
    );
    expect(drainsLayers({ drains: fx.drains, show: false })).toEqual([]);
  });
});

/**
 * The inferred drain graph, coloured by blockage (SPEC.md 6.7, task P7.9). Off by default.
 *
 * Two ways to draw it:
 *
 * - **Plain** (the console, the onboarding wizard): one dashed layer, every pipe at its own
 *   blockage. This is what every screen but `/drains` asks for, and it is byte-for-byte what it
 *   always drew - the MO1 equivalence fixture pins it.
 * - **Learned** (`/drains`, via `withLearned`): the whole network drawn quiet, and over it only
 *   the pipes Pulse moved this cycle, each with a wide low-alpha halo so the learning glows.
 *   Section 6.1 names "the pipes glowing with learned blockage" as this screen's one memorable
 *   element, and the glow means *learned change*, never a high prior: it is one neutral `--text`
 *   light under every moved pipe, and the stroke over it carries the band. At 08:40 on 2 July 2019
 *   about 4,912 of the 4,926 pipes above 0.25 sit at the 0.35 market prior, so colouring by
 *   posterior alone would glow land use. Pipes an observation cleared draw in `--naive`, dashed.
 *
 * The before/after split (motion M30) clips the learned pipes at a longitude: the prior west of
 * it, the posterior east. Clipping is a uniform, so dragging the handle re-uploads nothing.
 */

import { CompositeLayer, type UpdateParameters, type Viewport } from "@deck.gl/core";
import {
  ClipExtension,
  PathStyleExtension,
  type ClipExtensionProps,
  type PathStyleExtensionProps,
} from "@deck.gl/extensions";
import { PathLayer } from "@deck.gl/layers";

import { DRAIN_RAMP, NAIVE_ROUTE, STREET_HIGHLIGHT, drainColour, type Rgba } from "./palette";
import type { DrainPath, DrainPick } from "./types";

/**
 * The dash that carries "inferred" (section 6.7, task P7.9).
 *
 * Not decoration and not a style choice: every pipe on this map was synthesised from roads and
 * terrain by the city pipeline, none of it from a surveyed drain GIS, and `/drains` says so in
 * words beside the map. Drawing the pipes solid made that sentence false - a solid line reads as
 * a surveyed asset. One instance at module scope: deck.gl's `LayerExtension.equals` compares the
 * constructor and the options rather than the reference, so a fresh instance per render would not
 * rebuild the shaders - it would just be an allocation on every scrub step for nothing.
 */
export const DASHED = new PathStyleExtension({ dash: true });

/** The before/after split: per-pixel clipping, so a long trunk is cut where the handle is. */
const CLIPPED = new ClipExtension();

/** `[dash, gap]` **as multiples of the drawn width**, which is how deck.gl's dash shader reads the
 * array ("solid stroke length, relative to width"). The narrowest pipe the city pipeline emits is
 * 450 mm, drawn 1.9 px wide, so it dashes 7.6 px on and 5.7 px off; a 1500 mm trunk is 4 px and
 * dashes proportionally. The dash stays legible across the whole diameter range. */
export const DRAIN_DASH: [number, number] = [4, 3];

/**
 * A pipe Pulse moved this cycle, as `/drains` draws it. It is a `DrainPath` (so `CityMap`,
 * `DrainPick` and the plain builder all accept it) with `beta` set to the posterior.
 */
export interface LearnedDrain extends DrainPath {
  id: string;
  /** Blockage the city pipeline gave the pipe from land use. */
  prior: number;
  /** Blockage after this cycle's update; equal to `beta`. */
  post: number;
  /** Posterior standard deviation. */
  sd: number;
  /** "up": the filter raised it. "down": an observation cleared it. */
  direction: "up" | "down";
  /** "Dr Babasaheb Ambedkar Marg", "off Eastern Freeway", or null. */
  name: string | null;
  /** "near Hindmata junction", or null. */
  locality: string | null;
  capacityLostPct: number;
  /** ISO 8601 with +05:30. */
  lastUpdate: string | null;
}

/** What `/drains` adds to the drains it hands `CityMap`. */
export interface DrainsLearned {
  /** The pipes Pulse moved this cycle. */
  learned: readonly LearnedDrain[];
  /**
   * Longitude of the before/after split: the prior is drawn west of it, the posterior east.
   * `SPLIT_ALL_AFTER` draws only the posterior, `SPLIT_ALL_BEFORE` only the prior.
   */
  splitLon: number;
  /** Called with deck's viewport whenever the camera moves, so a DOM handle can follow the split
   * across the map. Called inside deck's update: it must not set React state. */
  onViewport?: (viewport: Viewport) => void;
}

/** Every learned pipe at its posterior. */
export const SPLIT_ALL_AFTER = -180;
/** Every learned pipe at its prior. */
export const SPLIT_ALL_BEFORE = 180;

/**
 * A drains array carrying the learned overlay.
 *
 * **Why the overlay rides on the array.** `CityMap` hands its `drains` prop to this builder
 * untouched and has no prop for a split or a learned set, and `/drains` is the only screen that
 * wants one. Tagging the array keeps the host unchanged; a `drainsLearned` prop on `CityMap` would
 * replace the tag without changing anything drawn. `base` is kept by reference so the quiet
 * network's 49,770 paths are uploaded once, however often the split moves.
 */
export type LearnedDrains = readonly DrainPath[] & {
  readonly learnedOverlay?: DrainsLearned & { base: readonly DrainPath[] };
};

export function withLearned(base: readonly DrainPath[], overlay: DrainsLearned): LearnedDrains {
  return Object.assign(base.slice(), { learnedOverlay: { ...overlay, base } });
}

export interface DrainsLayerOptions {
  drains: readonly DrainPath[];
  show: boolean;
  /**
   * Motion M12: the colour cross-fade in ms when the learned pipes change (a new cycle picked).
   * Applies to the learned overlay only; plain drains restyle instantly, as they always have.
   */
  crossFadeMs?: number;
  /** Hovering a learned pipe on `/drains`. The quiet network is never pickable: 49,770 paths
   * at citywide zoom are two pixels long each, and a tooltip on one of them says nothing. */
  onHover?: (pick: DrainPick | null) => void;
  /** 0 to 1 opacity, for the onboarding wizard's layer stack (motion M19). 1 everywhere else. */
  fade?: number;
}

/**
 * The quiet network under the learned pipes: `--drain-0`, 1 px, at 30 % alpha. 49,770 pipes at the
 * citywide fit are a wash rather than lines; any stronger and the wash is what the eye reads.
 */
export const QUIET_DRAIN: Rgba = [...DRAIN_RAMP[0], 78] as Rgba;
/** A cleared pipe: `--naive`, the product's "before" and "pre-update" colour. */
export const CLEARED_DRAIN: Rgba = [NAIVE_ROUTE[0], NAIVE_ROUTE[1], NAIVE_ROUTE[2], 235];
/** Alpha of the glow beneath a learned pipe. */
export const HALO_ALPHA = 80;
/**
 * The glow's colour: `--text`, the same neutral light as the street hover highlight. The glow says
 * one thing - *this pipe was learned this cycle* - and the stroke over it says the band. Tinting
 * the glow by band would leave every pipe that stayed under 0.25 glowing in `--drain-0`, the very
 * colour of the quiet network it is meant to stand out from, and most raised pipes do (0.15 to 0.22
 * at 08:40).
 */
const GLOW_RGB: [number, number, number] = [
  STREET_HIGHLIGHT[0],
  STREET_HIGHLIGHT[1],
  STREET_HIGHLIGHT[2],
];

function learnedWidth(d: DrainPath): number {
  return Math.max(2.5, Math.min(1 + d.diameter * 2, 5));
}

/** The posterior side: the band colour at full strength, or `--naive` for a cleared pipe. */
export function learnedColour(d: LearnedDrain): Rgba {
  if (d.direction === "down") return CLEARED_DRAIN;
  const [r, g, b] = drainColour(d.post);
  return [r, g, b, 255];
}

/** The glow: `--text` at `HALO_ALPHA`, for a raised and a cleared pipe alike. */
export function haloColour(): Rgba {
  return [...GLOW_RGB, HALO_ALPHA];
}

/** The prior side: the band colour of the land-use prior. */
export function priorColour(d: LearnedDrain): Rgba {
  const [r, g, b] = drainColour(d.prior);
  return [r, g, b, 235];
}

interface ProbeProps {
  onViewport: (viewport: Viewport) => void;
}

/**
 * Reports deck's viewport and draws nothing. The split handle is a DOM element over the map, and
 * it has to sit on the split's longitude wherever the operator pans; only deck knows where that
 * is on screen.
 */
export class ViewportProbeLayer extends CompositeLayer<ProbeProps> {
  static layerName = "ViewportProbeLayer";

  shouldUpdateState({ changeFlags }: UpdateParameters<this>): boolean {
    return Boolean(changeFlags.viewportChanged || changeFlags.propsChanged);
  }

  updateState(): void {
    const viewport = this.context.viewport;
    if (viewport) this.props.onViewport(viewport);
  }

  renderLayers(): null {
    return null;
  }
}

function plainLayer(drains: readonly DrainPath[], fade: number): unknown {
  return new PathLayer<DrainPath, PathStyleExtensionProps<DrainPath>>({
    id: "drains",
    ...(fade < 1 ? { opacity: fade } : {}),
    data: drains as DrainPath[],
    getPath: (d) => d.path,
    getColor: (d) => drainColour(d.beta),
    // Section 6.7: width by diameter, 1-4 px.
    getWidth: (d) => Math.min(1 + d.diameter * 2, 4),
    widthUnits: "pixels",
    // The floor never binds on this data and is kept only as a guard: diameters snap to
    // 450-1500 mm, so the narrowest pipe already draws at 1 + 2 x 0.45 = 1.9 px, wide
    // enough for the dash to separate. What the dash cannot survive is the citywide fit
    // both this page and the console open at - a 40 m pipe is about two pixels long there,
    // so the network reads as a wash until the operator zooms in. That is scale, not a
    // style: the dash is drawn at every zoom, and separates once a junction fills the view.
    widthMinPixels: 0.8,
    extensions: [DASHED],
    getDashArray: DRAIN_DASH,
    dashJustified: true,
    pickable: false,
  });
}

function learnedLayers(
  overlay: DrainsLearned & { base: readonly DrainPath[] },
  {
    crossFadeMs,
    onHover,
    fade,
  }: { crossFadeMs?: number; onHover?: DrainsLayerOptions["onHover"]; fade: number },
): unknown[] {
  const opacity = fade < 1 ? { opacity: fade } : {};
  const split = overlay.splitLon;
  const west = Math.max(-180, Math.min(180, split));
  const transitions = crossFadeMs && crossFadeMs > 0 ? { getColor: crossFadeMs } : undefined;
  const hover = onHover
    ? {
        pickable: true,
        autoHighlight: true,
        highlightColor: [227, 234, 246, 90] as Rgba,
        onHover: (info: { object?: LearnedDrain | null; x: number; y: number }) =>
          onHover(info.object ? { drain: info.object, x: info.x, y: info.y } : null),
      }
    : { pickable: false };
  const learned = overlay.learned as LearnedDrain[];
  const up = learned.filter((d) => d.direction === "up");
  const down = learned.filter((d) => d.direction === "down");

  const layers: unknown[] = [
    // The whole network, quiet: the paper the learning is drawn on.
    new PathLayer<DrainPath, PathStyleExtensionProps<DrainPath>>({
      id: "drains",
      ...opacity,
      data: overlay.base as DrainPath[],
      getPath: (d) => d.path,
      getColor: QUIET_DRAIN,
      getWidth: 1,
      widthUnits: "pixels",
      widthMinPixels: 0.6,
      extensions: [DASHED],
      getDashArray: DRAIN_DASH,
      dashJustified: true,
      pickable: false,
    }),
  ];

  if (learned.length === 0) return layers;

  // West of the split: every learned pipe at its prior, as the city pipeline left it.
  if (west > SPLIT_ALL_AFTER) {
    layers.push(
      new PathLayer<LearnedDrain, PathStyleExtensionProps<LearnedDrain> & ClipExtensionProps>({
        id: "drains-learned-prior",
        ...opacity,
        data: learned,
        getPath: (d) => d.path,
        getColor: priorColour,
        getWidth: learnedWidth,
        widthUnits: "pixels",
        extensions: [DASHED, CLIPPED],
        getDashArray: DRAIN_DASH,
        dashJustified: true,
        clipBounds: [-180, -90, west, 90],
        clipByInstance: false,
        ...(transitions ? { transitions } : {}),
        ...hover,
      }),
    );
  }

  // East of the split: what Pulse learned. The glow first, then the pipe over it.
  if (west < SPLIT_ALL_BEFORE) {
    const clip = { clipBounds: [west, -90, 180, 90] as [number, number, number, number] };
    layers.push(
      new PathLayer<LearnedDrain, ClipExtensionProps>({
        id: "drains-learned-halo",
        ...opacity,
        data: learned,
        getPath: (d) => d.path,
        getColor: haloColour(),
        getWidth: (d) => learnedWidth(d) * 3 + 4,
        widthUnits: "pixels",
        capRounded: true,
        extensions: [CLIPPED],
        ...clip,
        clipByInstance: false,
        ...(transitions ? { transitions } : {}),
        pickable: false,
      }),
    );
    if (down.length > 0) {
      layers.push(
        new PathLayer<LearnedDrain, PathStyleExtensionProps<LearnedDrain> & ClipExtensionProps>({
          id: "drains-learned-cleared",
          ...opacity,
          data: down,
          getPath: (d) => d.path,
          getColor: learnedColour,
          getWidth: learnedWidth,
          widthUnits: "pixels",
          extensions: [DASHED, CLIPPED],
          getDashArray: DRAIN_DASH,
          dashJustified: true,
          ...clip,
          clipByInstance: false,
          ...(transitions ? { transitions } : {}),
          ...hover,
        }),
      );
    }
    if (up.length > 0) {
      layers.push(
        new PathLayer<LearnedDrain, PathStyleExtensionProps<LearnedDrain> & ClipExtensionProps>({
          id: "drains-learned",
          ...opacity,
          data: up,
          getPath: (d) => d.path,
          getColor: learnedColour,
          getWidth: learnedWidth,
          widthUnits: "pixels",
          extensions: [DASHED, CLIPPED],
          getDashArray: DRAIN_DASH,
          dashJustified: true,
          ...clip,
          clipByInstance: false,
          ...(transitions ? { transitions } : {}),
          ...hover,
        }),
      );
    }
  }
  return layers;
}

export function drainsLayers({
  drains,
  show,
  crossFadeMs,
  onHover,
  fade = 1,
}: DrainsLayerOptions): unknown[] {
  if (!show || fade <= 0) return [];
  const overlay = (drains as LearnedDrains).learnedOverlay;
  if (!overlay) {
    if (drains.length === 0) return [];
    return [plainLayer(drains, fade)];
  }
  if (overlay.base.length === 0 && overlay.learned.length === 0) return [];
  const layers = learnedLayers(overlay, { crossFadeMs, onHover, fade });
  if (overlay.onViewport) {
    layers.push(
      new ViewportProbeLayer({ id: "drains-split-probe", onViewport: overlay.onViewport }),
    );
  }
  return layers;
}

/**
 * Sourced ground-truth pins and their drop ripple (SPEC.md 7.2's 2:40 moment, motion M18).
 *
 * **The drop runs on deck's clock, not React's.** A pin the replay clock has just passed arrives
 * with the `performance.now()` its drop began at. Pins that began together share a pair of layers -
 * the ripple under everything and the pin on top - and a small shader module scales each marker
 * and fades the ripple by two uniforms. The layer's `draw` reads the clock, sets the uniforms and,
 * while the drop is running, asks deck to draw again next frame, so a drop frame runs no accessor,
 * uploads no attribute and renders no React component. When the drop is over the layer stops
 * asking, and the next time the pins change they merge into the one static layer.
 *
 * The frame math is plain TypeScript ({@link pinDropFrame}, {@link rippleFrame}) so the tests read
 * the same numbers the uniforms carry:
 *
 * - the pin scales 0 to 1 on the catalogue spring (stiffness 400, damping 32), overshooting by
 *   about 1.5 % once;
 * - the ripple expands from the pin's 70 m to 320 m and fades out over 600 ms on the catalogue
 *   easing, cubic-bezier(0.2, 0.8, 0.2, 1).
 *
 * Under reduced motion no drop layer is built, so what is left is section 8's fallback - the pin
 * appears, with no ripple - and the canvas does not change between frames.
 */

import { LayerExtension, type Layer } from "@deck.gl/core";
import { ScatterplotLayer } from "@deck.gl/layers";

import { DROP_MS } from "@/lib/hooks/use-truth-pins";
import { clamp01, DUR_MS, easeUi, lerp, springValue } from "@/lib/motion";
import { TRUTH_FILL, TRUTH_RING } from "./palette";
import type { TruthPin } from "./types";

/** Pin radius in metres at full drop, and the ripple it expands to (motion M18). */
export const TRUTH_RADIUS_M = 70;
export const RIPPLE_RADIUS_M = 320;

/**
 * The pin's size in pixels, floor and ceiling.
 *
 * A pin is a **marker, not an area**: it says "this was reported here", and 70 m is a reading of
 * how precisely, not a claim about how much street was under water. Without the ceiling the radius
 * is geographic all the way in, so at street zoom - 0.28 m per pixel at z19 over Hindmata - the
 * pin became a 248 px opaque white disc that covered the junction, the imagery and the very
 * streets whose depth it is there to corroborate. Capped, it stops growing at a marker's size and
 * keeps marking something the operator can still see.
 *
 * The ripple (M18) scales from this same base in the shader, so its 4.6x expansion is unchanged;
 * it is given the same floor and ceiling so it always starts at the pin's own edge.
 */
export const TRUTH_MIN_PX = 4;
export const TRUTH_MAX_PX = 12;

/** Stroke alpha of the ripple as it starts; the fade takes it to 0. */
export const RIPPLE_ALPHA = 220;

/** What the drop shader is given for one frame: a size multiplier and an alpha multiplier. */
// A type alias rather than an interface: deck's shader-module props want an index signature.
export type DropFrame = {
  scale: number;
  alpha: number;
};

/**
 * The pin, `elapsedMs` after its drop began: scale 0 to 1 on the catalogue spring, never faded.
 * Before the start (or with no start yet) it is not drawn; from {@link DROP_MS} on it is exactly 1.
 */
export function pinDropFrame(elapsedMs: number): DropFrame {
  if (!(elapsedMs > 0)) return { scale: 0, alpha: 1 };
  if (elapsedMs >= DROP_MS) return { scale: 1, alpha: 1 };
  return { scale: springValue(elapsedMs), alpha: 1 };
}

/**
 * The ripple, `elapsedMs` after the drop began: from the pin's radius out to
 * {@link RIPPLE_RADIUS_M} while it fades out, both on the catalogue easing over section 8's 600 ms.
 * Invisible before it starts and after it ends.
 */
export function rippleFrame(elapsedMs: number): DropFrame {
  if (!(elapsedMs >= 0)) return { scale: 1, alpha: 0 };
  const eased = easeUi(clamp01(elapsedMs / DUR_MS.pinRipple));
  return {
    scale: lerp(TRUTH_RADIUS_M, RIPPLE_RADIUS_M, eased) / TRUTH_RADIUS_M,
    alpha: 1 - eased,
  };
}

/** The uniform block the drop shader module declares, identical in both stages. */
const DROP_BLOCK = /* glsl */ `\
layout(std140) uniform truthDropUniforms {
  float scale;
  float alpha;
} truthDrop;
`;

/**
 * Appended to the scatterplot vertex shader's `main`. `DECKGL_FILTER_SIZE` has already scaled the
 * marker's quad; this scales the radius the fragment shader measures against by the same factor
 * and keeps the stroke its own width in pixels, so a growing ring stays a 2 px ring rather than
 * thickening with the quad.
 */
export const DROP_MAIN_END = /* glsl */ `\
  outerRadiusPixels *= truthDrop.scale;
  innerUnitRadius = 1.0 - (1.0 - innerUnitRadius) / max(truthDrop.scale, 0.0001);
`;

/** The shader module, kept as data so the tests can read what the GPU is given. */
export const DROP_SHADER = {
  name: "truthDrop",
  vs: DROP_BLOCK,
  fs: DROP_BLOCK,
  uniformTypes: { scale: "f32", alpha: "f32" },
  inject: {
    "vs:DECKGL_FILTER_SIZE": "size *= truthDrop.scale;",
    "vs:#main-end": DROP_MAIN_END,
    "fs:DECKGL_FILTER_COLOR": "color.a *= truthDrop.alpha;",
  },
} as const;

interface DropProps {
  /** `performance.now()` at which these pins began to drop. */
  dropStartMs?: number;
  /** Which half of M18 this layer draws. */
  dropPart?: "pin" | "ripple";
}

export class TruthDropExtension extends LayerExtension {
  static extensionName = "TruthDropExtension";
  static defaultProps = { dropStartMs: Number.POSITIVE_INFINITY, dropPart: "pin" };

  getShaders() {
    return { modules: [DROP_SHADER] };
  }

  draw(this: Layer<DropProps>) {
    const elapsed = performance.now() - (this.props.dropStartMs ?? Number.POSITIVE_INFINITY);
    const frame = this.props.dropPart === "ripple" ? rippleFrame(elapsed) : pinDropFrame(elapsed);
    this.setShaderModuleProps({ truthDrop: frame });
    // Deck calls `redraw` every animation frame and draws only when something asks. Asking from
    // here, and only while the drop runs, is what lets it end by itself with nothing looping.
    if (elapsed >= 0 && elapsed < DROP_MS) this.setNeedsRedraw();
  }
}

/** One instance for every layer, so a rebuilt layer keeps its compiled program. */
const DROP = new TruthDropExtension();

export interface TruthPinLayerOptions {
  truthPins: readonly TruthPin[];
  /** Every pin drawn landed, with no ripple: section 8's fallback. */
  reducedMotion?: boolean;
}

const pinProps = {
  getPosition: (d: TruthPin) => [d.lon, d.lat] as [number, number],
  getRadius: TRUTH_RADIUS_M,
  radiusUnits: "meters",
  radiusMinPixels: TRUTH_MIN_PX,
  radiusMaxPixels: TRUTH_MAX_PX,
  stroked: true,
  filled: true,
  getFillColor: TRUTH_FILL,
  getLineColor: TRUTH_RING,
  getLineWidth: 2,
  lineWidthUnits: "pixels",
  pickable: false,
} as const;

const rippleProps = {
  getPosition: (d: TruthPin) => [d.lon, d.lat] as [number, number],
  getRadius: TRUTH_RADIUS_M,
  radiusUnits: "meters",
  radiusMinPixels: TRUTH_MIN_PX,
  radiusMaxPixels: TRUTH_MAX_PX,
  stroked: true,
  filled: false,
  getLineColor: [TRUTH_RING[0], TRUTH_RING[1], TRUTH_RING[2], RIPPLE_ALPHA] as [
    number,
    number,
    number,
    number,
  ],
  getLineWidth: 2,
  lineWidthUnits: "pixels",
  pickable: false,
} as const;

type DropLayerProps = ConstructorParameters<typeof ScatterplotLayer<TruthPin>>[0] & DropProps;

/**
 * Ripples first, then the landed pins, then the pins still dropping: drawn above everything,
 * because a pin under a street is a pin nobody sees, and these are the point of the whole replay.
 */
export function truthPinLayers({
  truthPins,
  reducedMotion = false,
}: TruthPinLayerOptions): unknown[] {
  if (truthPins.length === 0) return [];

  const landed: TruthPin[] = [];
  const drops = new Map<number, TruthPin[]>();
  for (const pin of truthPins) {
    const start = pin.dropStartMs;
    if (reducedMotion || start === undefined) {
      landed.push(pin);
      continue;
    }
    const group = drops.get(start);
    if (group) group.push(pin);
    else drops.set(start, [pin]);
  }

  const ripples: unknown[] = [];
  const dropping: unknown[] = [];
  for (const [start, pins] of drops) {
    // Named for the group's first pin, so the layer keeps its identity from the frame the clock
    // passes it to the frame the drop begins, when its start time goes from pending to a number.
    const key = pins[0]?.id ?? String(start);
    ripples.push(
      new ScatterplotLayer<TruthPin>({
        id: `truth-ripple-${key}`,
        data: pins,
        ...rippleProps,
        extensions: [DROP],
        dropStartMs: start,
        dropPart: "ripple",
      } as DropLayerProps),
    );
    dropping.push(
      new ScatterplotLayer<TruthPin>({
        id: `truth-drop-${key}`,
        data: pins,
        ...pinProps,
        extensions: [DROP],
        dropStartMs: start,
        dropPart: "pin",
      } as DropLayerProps),
    );
  }

  const built: unknown[] = [...ripples];
  if (landed.length > 0) {
    built.push(new ScatterplotLayer<TruthPin>({ id: "truth-pins", data: landed, ...pinProps }));
  }
  built.push(...dropping);
  return built;
}

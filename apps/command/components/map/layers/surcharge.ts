/**
 * Surcharging manholes and their pulse (SPEC.md 6.7, motion M8).
 *
 * **The pulse runs on deck's clock, not React's.** Each marker is two layers: a static core (the
 * filled disc with its stroked ring, sized by discharge) and, while motion is allowed, a second
 * stroked ring that a small shader module expands 1 to 2.4 times and fades out over the section 8
 * period. The phase is a uniform set in the layer's `draw`, read from `performance.now()`, so a
 * pulse frame re-runs no accessor, uploads no attribute and renders no React component. `CityMap`
 * turns deck's own redraw loop (`_animate`) on only while something that moves is on screen.
 *
 * Under reduced motion the pulse ring is not built at all and the uniform is never read, so what is
 * left is the section 8 fallback, a static ring, and the canvas does not change between frames.
 *
 * The surcharge markers and the reversed-flow dash are the only looping things on the console, and
 * section 8 allows both because they are data: a pulsing manhole is the drain failing.
 */

import { LayerExtension, type Layer } from "@deck.gl/core";
import { ScatterplotLayer } from "@deck.gl/layers";

import { DUR_MS } from "@/lib/motion";
import { SURCHARGE_LINE, SURCHARGE_RGB } from "./palette";
import type { SurchargeNode, SurchargeStyle } from "./types";

/** How far the pulse ring grows over one period: 1 at the start, `1 + PULSE_GROWTH` at the end. */
export const PULSE_GROWTH = 1.4;

/** Fill alpha of the static core; the value the React pulse drew at rest, so the fallback is unchanged. */
const CORE_FILL_ALPHA = 200;

/**
 * Where a loop is in its period, 0 to 1. The single clock both map loops read: the pulse (M8) and
 * the reversed-flow dash (M9) take their phase from the same `performance.now()` in the same
 * frame, so they stay in step with each other.
 */
export function loopPhase(nowMs: number, periodMs: number): number {
  if (!(periodMs > 0) || !Number.isFinite(nowMs)) return 0;
  const phase = (nowMs % periodMs) / periodMs;
  return phase < 0 ? phase + 1 : phase;
}

/** The uniform block the pulse shader module declares, identical in both stages. */
const PULSE_BLOCK = /* glsl */ `\
layout(std140) uniform surchargePulseUniforms {
  float phase;
} surchargePulse;
`;

/**
 * Expands and fades a `ScatterplotLayer`'s markers by a phase uniform (motion M8).
 *
 * `vs:DECKGL_FILTER_SIZE` scales the marker's quad in pixels, so the stroked ring grows past the
 * layer's `radiusMaxPixels` as a pulse should; `fs:DECKGL_FILTER_COLOR` fades it by the same phase.
 * The GLSL is kept as data (`PULSE_SHADER`) so the tests can read what the GPU is given.
 */
export const PULSE_SHADER = {
  name: "surchargePulse",
  vs: PULSE_BLOCK,
  fs: PULSE_BLOCK,
  uniformTypes: { phase: "f32" },
  inject: {
    "vs:DECKGL_FILTER_SIZE": `size *= 1.0 + ${PULSE_GROWTH.toFixed(1)} * surchargePulse.phase;`,
    "fs:DECKGL_FILTER_COLOR": "color.a *= 1.0 - surchargePulse.phase;",
  },
} as const;

interface PulseProps {
  /** False freezes the phase at 0: the ring is drawn but does not move. */
  pulseAnimated?: boolean;
}

export class SurchargePulseExtension extends LayerExtension {
  static extensionName = "SurchargePulseExtension";
  static defaultProps = { pulseAnimated: false };

  getShaders() {
    return { modules: [PULSE_SHADER] };
  }

  draw(this: Layer<PulseProps>) {
    const phase = this.props.pulseAnimated
      ? loopPhase(performance.now(), DUR_MS.surchargePulse)
      : 0;
    this.setShaderModuleProps({ surchargePulse: { phase } });
  }
}

/** One instance for every layer, so a rebuilt layer keeps its compiled program. */
const PULSE = new SurchargePulseExtension();

export interface SurchargeLayerOptions {
  surcharge: readonly SurchargeNode[];
  show: boolean;
  /** No pulse ring under reduced motion: the section 8 fallback is the static ring. */
  reducedMotion: boolean;
  /** Seam for PU8: `/drains` wants static rings. **Not applied yet** - every map pulses. */
  style?: SurchargeStyle;
}

/** Radius by discharge, so a manhole shifting 0.4 m³/s reads bigger than one at 0.01. Square root
 * because the eye compares areas, and the marker's area is what it is. */
function markerRadius(d: SurchargeNode): number {
  return 40 + 110 * Math.sqrt(Math.min(d.q ?? 0, 1));
}

/**
 * Rebuilt only when the markers or the motion preference change; the pulse itself never rebuilds a
 * layer.
 */
export function surchargeLayers({
  surcharge,
  show,
  reducedMotion,
}: SurchargeLayerOptions): unknown[] {
  if (!show || surcharge.length === 0) return [];
  const data = surcharge as SurchargeNode[];
  const core = new ScatterplotLayer<SurchargeNode>({
    id: "surcharge",
    data,
    getPosition: (d) => [d.lon, d.lat],
    getRadius: markerRadius,
    radiusUnits: "meters",
    radiusMinPixels: 3,
    radiusMaxPixels: 16,
    filled: true,
    getFillColor: [SURCHARGE_RGB[0], SURCHARGE_RGB[1], SURCHARGE_RGB[2], CORE_FILL_ALPHA],
    stroked: true,
    getLineColor: SURCHARGE_LINE,
    lineWidthMinPixels: 1,
    pickable: false,
    updateTriggers: { getRadius: surcharge },
  });
  if (reducedMotion) return [core];
  return [
    core,
    new ScatterplotLayer<SurchargeNode>({
      id: "surcharge-pulse",
      data,
      getPosition: (d) => [d.lon, d.lat],
      getRadius: markerRadius,
      radiusUnits: "meters",
      radiusMinPixels: 3,
      radiusMaxPixels: 16,
      filled: false,
      stroked: true,
      getLineColor: SURCHARGE_LINE,
      lineWidthMinPixels: 2,
      pickable: false,
      extensions: [PULSE],
      pulseAnimated: true,
      updateTriggers: { getRadius: surcharge },
    } as ConstructorParameters<typeof ScatterplotLayer<SurchargeNode>>[0] & PulseProps),
  ];
}

export interface DeckAnimationOptions {
  reducedMotion: boolean;
  /** Surcharge markers drawn and in view. */
  surchargeVisible: number;
  /** Reversed-flow edges drawn and in view. */
  reversedVisible: number;
}

/**
 * Whether deck must redraw every frame on its own (`DeckGL._animate`). Only while a pulse or a dash
 * is actually on screen: deck's loop redraws the whole scene - 39,259 buildings, the imagery and
 * 21k streets - so a loop with nothing moving in view is a laptop fan for nothing. Never under
 * reduced motion, which is what makes that canvas provably still.
 */
export function deckAnimates({
  reducedMotion,
  surchargeVisible,
  reversedVisible,
}: DeckAnimationOptions): boolean {
  if (reducedMotion) return false;
  return surchargeVisible > 0 || reversedVisible > 0;
}

/**
 * Colour ramps for the UI, thin over `@varuna/tokens` so map pixels, chips and legends read the
 * same `tokens.json` (SPEC.md section 6.2). Adds legend stops for the Legend component, the
 * public map's three-colour passability scheme, and deck.gl-ready rgba helpers.
 */
import {
  BAND_OPACITY,
  DEPTH_RASTER_OPACITY,
  DEPTH_THRESHOLDS_CM,
  MIN_PROBABILITY_OPACITY,
  PROBABILITY_THRESHOLDS_CM,
  RAIN_THRESHOLDS_MM_H,
  chartColor,
  colors,
  cssVar,
  depthBand,
  depthColor,
  depthColorRgba,
  depthRampStops,
  drainBand,
  drainColor,
  drainColorRgba,
  drainRampStops,
  hexToRgb,
  hexToRgba,
  obsColor,
  opacityToAlpha,
  probabilityOpacity,
  rainBand,
  rainColor,
  rainColorRgba,
  rainRampStops,
  reachColorRgba,
  statusColor,
  type DepthBand,
  type DepthKey,
  type DrainBand,
  type DrainKey,
  type Hex,
  type ObservationKind,
  type RainBand,
  type RainKey,
  type ReachMinutes,
  type Rgb,
  type Rgba,
  type StatusMode,
} from "@varuna/tokens";

export {
  BAND_OPACITY,
  DEPTH_RASTER_OPACITY,
  DEPTH_THRESHOLDS_CM,
  MIN_PROBABILITY_OPACITY,
  PROBABILITY_THRESHOLDS_CM,
  RAIN_THRESHOLDS_MM_H,
  chartColor,
  colors,
  cssVar,
  depthBand,
  depthColor,
  depthColorRgba,
  depthRampStops,
  drainBand,
  drainColor,
  drainColorRgba,
  drainRampStops,
  hexToRgb,
  hexToRgba,
  obsColor,
  opacityToAlpha,
  probabilityOpacity,
  rainBand,
  rainColor,
  rainColorRgba,
  rainRampStops,
  reachColorRgba,
  statusColor,
};
export type {
  DepthBand,
  DepthKey,
  DrainBand,
  DrainKey,
  Hex,
  ObservationKind,
  RainBand,
  RainKey,
  ReachMinutes,
  Rgb,
  Rgba,
  StatusMode,
};

/** One entry of a legend: a swatch, a label and what it means. */
export interface LegendStop {
  key: string;
  /** Hex from tokens.json (for canvas and deck.gl). */
  hex: Hex;
  /** `var(--depth-3)` for DOM styles. */
  cssVar: string;
  /** "30-45 cm". */
  label: string;
  /** "cars impassable". */
  meaning: string;
  /** Opacity for probability legends; 1 otherwise. */
  opacity: number;
}

/** Depth legend: dry, 5-15, 15-30, 30-45, 45-60, > 60 cm, in ramp order. */
export function depthLegendStops(): LegendStop[] {
  return depthRampStops().map((band) => ({
    key: `depth-${band.key}`,
    hex: band.hex,
    cssVar: cssVar(`--depth-${band.key}`),
    label: band.label,
    meaning: band.meaning,
    opacity: 1,
  }));
}

const DRAIN_MEANING: Record<DrainKey, string> = {
  "0": "clear",
  "1": "partly blocked",
  "2": "mostly blocked",
  "3": "blocked",
};

/** Drain-health legend by posterior blockage beta: 0-0.25 clear to > 0.75 blocked (magenta). */
export function drainLegendStops(): LegendStop[] {
  return drainRampStops().map((band) => ({
    key: `drain-${band.key}`,
    hex: band.hex,
    cssVar: cssVar(`--drain-${band.key}`),
    label:
      band.key === "3"
        ? `> ${band.min_beta.toFixed(2)}`
        : `${band.min_beta.toFixed(2)}-${band.max_beta.toFixed(2)}`,
    meaning: DRAIN_MEANING[band.key],
    opacity: 1,
  }));
}

/**
 * Rain-rate legend for the radar and nowcast layers: 0.5-2 mm/h drizzle up to > 80 mm/h cloudburst,
 * in ramp order. Its own indigo ramp, never the depth ramp, so an echo cannot be read as a flooded
 * street (SPEC.md section 6.2).
 */
export function rainLegendStops(): LegendStop[] {
  return rainRampStops().map((band) => ({
    key: `rain-${band.key}`,
    hex: band.hex,
    cssVar: cssVar(`--rain-${band.key}`),
    label: band.label,
    meaning: band.meaning,
    opacity: 1,
  }));
}

/**
 * Probability legend: the depth colour at the chosen threshold, at opacities for P(> threshold) of
 * 15 % (the floor), 40 %, 60 %, 80 % and 100 %. Colour stays the depth ramp; opacity carries P.
 */
export function probabilityLegendStops(thresholdCm: number): LegendStop[] {
  const band = depthBand(thresholdCm);
  return [0.15, 0.4, 0.6, 0.8, 1].map((p) => ({
    key: `prob-${Math.round(p * 100)}`,
    hex: band.hex,
    cssVar: cssVar(`--depth-${band.key}`),
    label: `${Math.round(p * 100)} %`,
    meaning: `P(depth > ${thresholdCm} cm)`,
    opacity: probabilityOpacity(p),
  }));
}

/** Vehicle profiles the public map and the route planner share (SPEC.md section 11.8). */
export type PassabilityProfile =
  | "two-wheeler"
  | "car"
  | "bus"
  | "ambulance"
  | "fire-tender"
  | "pedestrian";

/** Depth at which a profile is impassable (rescue vehicles at 60 cm; pedestrians at 30 cm). */
export const PROFILE_THRESHOLD_CM: Record<PassabilityProfile, number> = {
  "two-wheeler": 15,
  car: 30,
  bus: 45,
  ambulance: 60,
  "fire-tender": 60,
  pedestrian: 30,
};

export type PassabilityState = "passable" | "caution" | "impassable";

export interface PassabilityStop {
  state: PassabilityState;
  label: string;
  /** "below 15 cm", "15-30 cm", "30 cm and above". */
  range: string;
  hex: Hex;
  cssVar: string;
}

/** Caution begins at half the profile threshold, never below the 5 cm visibility floor. */
export function cautionThresholdCm(profile: PassabilityProfile): number {
  return Math.max(5, PROFILE_THRESHOLD_CM[profile] / 2);
}

/**
 * Three-colour scheme for the public map. Passable is the safe-route accent (tide); caution and
 * impassable take the depth-ramp colour of the band each threshold sits in, so amber still means
 * 15-30 cm and red still means 45-60 cm, per the rule that the ramp only ever encodes depth.
 */
export function passabilityStops(profile: PassabilityProfile): PassabilityStop[] {
  const impassableAt = PROFILE_THRESHOLD_CM[profile];
  const cautionAt = cautionThresholdCm(profile);
  const cautionBand = depthBand(cautionAt);
  const impassableBand = depthBand(impassableAt);
  return [
    {
      state: "passable",
      label: "Passable",
      range: `below ${cautionAt} cm`,
      hex: colors.tide,
      cssVar: cssVar("--tide"),
    },
    {
      state: "caution",
      label: "Caution",
      range: `${cautionAt}-${impassableAt} cm`,
      hex: cautionBand.hex,
      cssVar: cssVar(`--depth-${cautionBand.key}`),
    },
    {
      state: "impassable",
      label: "Impassable",
      range: `${impassableAt} cm and above`,
      hex: impassableBand.hex,
      cssVar: cssVar(`--depth-${impassableBand.key}`),
    },
  ];
}

/** Passability of a depth for a profile. */
export function passability(cm: number, profile: PassabilityProfile): PassabilityStop {
  const stops = passabilityStops(profile);
  const depth = Number.isFinite(cm) ? Math.max(0, cm) : 0;
  if (depth >= PROFILE_THRESHOLD_CM[profile]) return stops[2]!;
  if (depth >= cautionThresholdCm(profile)) return stops[1]!;
  return stops[0]!;
}

/** `rgb(r g b / a)` for inline styles when a token needs an alpha (band fills, glass). */
export function rgbaCss(hex: string, opacity = 1): string {
  const [r, g, b] = hexToRgb(hex);
  const a = Math.min(1, Math.max(0, opacity));
  return `rgb(${r} ${g} ${b} / ${a})`;
}

/** deck.gl colour for a segment in depth mode at the raster or a custom opacity. */
export function depthRgba(cm: number, opacity = 1): Rgba {
  return depthColorRgba(cm, opacityToAlpha(opacity));
}

/**
 * deck.gl colour in probability mode: the depth-ramp colour at the p50 depth, with alpha equal to
 * P(> threshold) clamped to the 15 % floor so a segment never vanishes (SPEC.md section 6.2).
 */
export function probabilityRgba(p50Cm: number, probability: number): Rgba {
  return depthColorRgba(p50Cm, opacityToAlpha(probabilityOpacity(probability)));
}

/** deck.gl colour for a drain edge by posterior beta. */
export function drainRgba(beta: number, opacity = 1): Rgba {
  return drainColorRgba(beta, opacityToAlpha(opacity));
}

/** `rgb(...)` fill for the p10-p90 band on charts, from a chart or depth colour. */
export function bandFill(hex: string): string {
  return rgbaCss(hex, BAND_OPACITY);
}

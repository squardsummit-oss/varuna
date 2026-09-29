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
  THEME_ATTRIBUTE,
  colors,
  colorsFor,
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

/**
 * The depth ramp for deck.gl, with the one band that moves between themes resolved per theme.
 *
 * The water is the same pixels in both themes (UI_UX.md 2); only the dry band is not. `< 5 cm` is
 * `#2B3A55` on the night map and `#C5CEDC` on the day one, because a dry street is a thin line
 * that has to recede. The generated `depthColorRgba` knows one table, the dark one, so on a light
 * map every street a run carried at 0-5 cm was drawn as a thick navy line, the heaviest thing on
 * the page (seen on /console at 06:45, 2026-09-29). Read at call time, so a layer rebuilt after a
 * switch draws the new theme.
 */
function depthRampRgba(cm: number, alpha: number): Rgba {
  if (depthBand(cm).key === "dry" && pageIsLight()) {
    return hexToRgba(colorsFor("light")["depth-dry"], alpha);
  }
  return depthColorRgba(cm, alpha);
}

/**
 * Whether the page is in the light theme, read from `<html data-theme>`.
 *
 * Not `getTheme()` from `lib/theme.ts`: that module imports React's `useSyncExternalStore`, and
 * this one is imported by server components, which may not import it (every page answered 500
 * for the few minutes this read that way, 2026-09-29). The attribute is the same answer: the head
 * script sets it before first paint, `setTheme` moves it before it notifies anyone, and the guard
 * in `lib/theme.ts` puts it back if a hydration failure resets `<html>`.
 */
function pageIsLight(): boolean {
  if (typeof document === "undefined") return false;
  return document.documentElement.getAttribute(THEME_ATTRIBUTE) === "light";
}

/** deck.gl colour for a segment in depth mode at the raster or a custom opacity. */
export function depthRgba(cm: number, opacity = 1): Rgba {
  return depthRampRgba(cm, opacityToAlpha(opacity));
}

/**
 * deck.gl colour in probability mode: the depth-ramp colour at the p50 depth, with alpha equal to
 * P(> threshold) clamped to the 15 % floor so a segment never vanishes (SPEC.md section 6.2).
 */
export function probabilityRgba(p50Cm: number, probability: number): Rgba {
  return depthRampRgba(p50Cm, opacityToAlpha(probabilityOpacity(probability)));
}

/** deck.gl colour for a drain edge by posterior beta. The clear band (`--drain-0`) follows the
 * theme for the same reason the dry street does; the magenta blockage bands are fixed. */
export function drainRgba(beta: number, opacity = 1): Rgba {
  const alpha = opacityToAlpha(opacity);
  if (drainBand(beta).key === "0" && pageIsLight()) {
    return hexToRgba(colorsFor("light")["drain-0"], alpha);
  }
  return drainColorRgba(beta, alpha);
}

/** How many chart colours the tokens define (`--chart-1` to `--chart-5`). */
const CHART_COLOUR_COUNT = Object.keys(colors).filter((name) => /^chart-\d+$/.test(name)).length;

/**
 * Chart series colour `index` (cycling, as the token helper does), as `var(--chart-N)`.
 *
 * A CSS variable rather than a hex because the chart palette is one of the things the light theme
 * redefines: `#2DD4BF` is the right first series on night and a 1.9:1 line on paper. SVG strokes
 * and fills, inline `background`s and Recharts' `stroke`/`fill` props all resolve `var()` in the
 * browser, so every chart follows `<html data-theme>` without re-rendering. Code that needs the
 * dark theme's hex itself (canvas, deck.gl) reads `colors["chart-1"]` or `themeColor()` instead.
 */
export function chartColor(index: number): string {
  const n = CHART_COLOUR_COUNT;
  const whole = Number.isFinite(index) ? Math.trunc(index) : 0;
  const i = ((whole % n) + n) % n;
  return `var(--chart-${i + 1})`;
}

/**
 * The p10-p90 band fill on charts, from a chart or depth colour at the band opacity (6.2).
 *
 * A token hex gives `rgb(r g b / 0.2)`; a `var(--...)` colour (what `chartColor` returns) gives a
 * `color-mix` toward transparent, which follows the theme the same way the line does.
 */
export function bandFill(color: string): string {
  if (color.startsWith("var(")) {
    return `color-mix(in srgb, ${color} ${Math.round(BAND_OPACITY * 100)}%, transparent)`;
  }
  return rgbaCss(color, BAND_OPACITY);
}

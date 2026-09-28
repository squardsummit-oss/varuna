/**
 * The motion catalogue (SPEC.md section 8) as code. Nothing on a screen may move unless it is
 * a row in `M`; every row has a reduced-motion branch. Durations are seconds (framer units);
 * `DUR_MS` carries the same numbers in milliseconds for CSS, deck.gl and timers.
 *
 * Constants are safe to import anywhere. `useMotionPref()` is a client hook.
 */
import {
  useReducedMotion,
  type Target,
  type TargetAndTransition,
  type Transition,
} from "motion/react";
import { tokens } from "@varuna/tokens";

/** Global UI easing: cubic-bezier(0.2, 0.8, 0.2, 1), as framer's control-point array. */
export const EASE_UI: readonly [number, number, number, number] = [0.2, 0.8, 0.2, 1];

/**
 * The same curve as a CSS timing function, straight from tokens.json, for inline `transition`
 * styles and keyframes. `motion.test.ts` checks it names the same four points as {@link EASE_UI}.
 */
export const EASE_UI_CSS: string = tokens.motion.easing;

/** x or y of a one-dimensional cubic Bezier from 0 to 1 with inner control points p1 and p2. */
function bezierAxis(s: number, p1: number, p2: number): number {
  const inv = 1 - s;
  return 3 * inv * inv * s * p1 + 3 * inv * s * s * p2 + s * s * s;
}

/** d/ds of {@link bezierAxis}. */
function bezierAxisSlope(s: number, p1: number, p2: number): number {
  const inv = 1 - s;
  return 3 * inv * inv * p1 + 6 * inv * s * (p2 - p1) + 3 * s * s * (1 - p2);
}

/**
 * A cubic-bezier timing function as a JS function of progress, solved the way browsers do:
 * Newton-Raphson on x(s) = t, falling back to bisection where the slope is too flat to trust.
 * Used by rAF-driven motion (deck.gl layers, SVG) that cannot hand the curve to CSS or framer.
 */
export function cubicBezier(x1: number, y1: number, x2: number, y2: number): (t: number) => number {
  return (t: number) => {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    let s = t;
    for (let i = 0; i < 8; i += 1) {
      const error = bezierAxis(s, x1, x2) - t;
      if (Math.abs(error) < 1e-7) return bezierAxis(s, y1, y2);
      const slope = bezierAxisSlope(s, x1, x2);
      if (Math.abs(slope) < 1e-6) break;
      s -= error / slope;
    }
    let lo = 0;
    let hi = 1;
    s = t;
    for (let i = 0; i < 60; i += 1) {
      const x = bezierAxis(s, x1, x2);
      if (Math.abs(x - t) < 1e-7) break;
      if (x < t) lo = s;
      else hi = s;
      s = (lo + hi) / 2;
    }
    return bezierAxis(s, y1, y2);
  };
}

/** The catalogue easing as a JS function: `easeUi(0.5)` is where a CSS `cubic-bezier(...)` is at half time. */
export const easeUi: (t: number) => number = cubicBezier(...EASE_UI);

/** Linear interpolation; `t` is not clamped. */
export function lerp(from: number, to: number, t: number): number {
  return from + (to - from) * t;
}

/** Clamps progress to [0, 1]. */
export function clamp01(t: number): number {
  return t < 0 ? 0 : t > 1 ? 1 : t;
}

/** The spring token as numbers (stiffness 400, damping 32, mass 1, framer's default mass). */
export const SPRING_PARAMS = {
  stiffness: tokens.motion.spring.stiffness,
  damping: tokens.motion.spring.damping,
  mass: 1,
} as const;

/** Spring for handles and pins (stiffness 400, damping 32). */
export const SPRING: Transition = {
  type: "spring",
  stiffness: SPRING_PARAMS.stiffness,
  damping: SPRING_PARAMS.damping,
};

const SPRING_OMEGA = Math.sqrt(SPRING_PARAMS.stiffness / SPRING_PARAMS.mass);
const SPRING_ZETA =
  SPRING_PARAMS.damping / (2 * Math.sqrt(SPRING_PARAMS.stiffness * SPRING_PARAMS.mass));

/**
 * Position of the catalogue spring released from 0 at rest towards 1, `ms` after release: the
 * closed-form solution of m x'' + c x' + k (x - 1) = 0. For rAF-driven motion (deck.gl pins) that
 * wants framer's spring without framer. With 400/32/1 the spring is underdamped (zeta 0.8), so it
 * overshoots by about 1.5 % once and settles.
 */
export function springValue(ms: number): number {
  if (ms <= 0) return 0;
  const t = ms / 1000;
  const decay = SPRING_ZETA * SPRING_OMEGA;
  if (SPRING_ZETA < 1) {
    const damped = SPRING_OMEGA * Math.sqrt(1 - SPRING_ZETA * SPRING_ZETA);
    return (
      1 - Math.exp(-decay * t) * (Math.cos(damped * t) + (decay / damped) * Math.sin(damped * t))
    );
  }
  // Critically damped or overdamped would need other forms; the token is neither, and the test
  // pins zeta below 1, so this branch exists only to keep the function total.
  return 1 - Math.exp(-SPRING_OMEGA * t) * (1 + SPRING_OMEGA * t);
}

/**
 * Milliseconds after which {@link springValue} stays within `tolerance` of 1, from the decay
 * envelope (an upper bound, so the spring is at rest by then rather than merely passing through).
 */
export function springSettleMs(tolerance = 0.02): number {
  const decay = SPRING_ZETA * SPRING_OMEGA;
  const amplitude = SPRING_ZETA < 1 ? 1 / Math.sqrt(1 - SPRING_ZETA * SPRING_ZETA) : 1;
  return Math.ceil((Math.log(amplitude / tolerance) / decay) * 1000);
}

/** Durations in milliseconds, from tokens.json plus the per-row values in section 8. */
export const DUR_MS = {
  micro: tokens.motion.duration_ms.micro,
  microMin: tokens.motion.duration_ms.micro_min,
  microMax: tokens.motion.duration_ms.micro_max,
  panel: tokens.motion.duration_ms.panel,
  flight: tokens.motion.duration_ms.flight,
  drawOnMax: tokens.motion.duration_ms.draw_on_max,
  surchargePulse: tokens.motion.surcharge_pulse_ms,
  heroLoop: 8000,
  colourTween: 120,
  drawerSlide: 220,
  crossFade: 300,
  diffWipe: 500,
  rowHighlight: 600,
  routeDrawOn: 1200,
  alertSlide: 180,
  phoneShake: 300,
  pinRipple: 600,
  layerFade: 400,
  budgetFill: 200,
  staggerCopy: 60,
  staggerPipes: 40,
  isochroneMorph: 300,
  radarLoop: 6250,
  globeTurn: 1400,
  globeApproach: 1600,
  globeArrive: 1000,
  globeUnroll: 2600,
  heroHandover: 900,
  staggerPumps: 150,
  gaugeDrain: 900,
} as const;

export type DurationKey = keyof typeof DUR_MS;

/** The same durations in seconds for framer transitions. */
export const DUR = Object.fromEntries(
  Object.entries(DUR_MS).map(([key, ms]) => [key, ms / 1000]),
) as { readonly [K in keyof typeof DUR_MS]: number };

/** MapLibre flyTo curve. */
export const FLY_TO_CURVE = tokens.motion.fly_to_curve;

/** Tween on the global easing; the default for micro and panel motions. */
export function tween(seconds: number, extra: Transition = {}): Transition {
  return { duration: seconds, ease: EASE_UI, ...extra };
}

export type MotionId =
  | "M1"
  | "M2"
  | "M3"
  | "M4"
  | "M5"
  | "M6"
  | "M7"
  | "M8"
  | "M9"
  | "M10"
  | "M11"
  | "M12"
  | "M13"
  | "M14"
  | "M15"
  | "M16"
  | "M17"
  | "M18"
  | "M19"
  | "M20"
  | "M21"
  | "M22"
  | "M23"
  | "M24"
  | "M25"
  | "M26"
  | "M27"
  | "M28"
  | "M29"
  | "M30"
  | "M31"
  | "M32"
  | "M33"
  | "M34"
  | "M35";

/** Framer props for one motion; spread onto a `motion.*` element. */
export interface MotionPreset {
  initial?: Target | false;
  animate?: TargetAndTransition;
  exit?: TargetAndTransition;
  transition?: Transition;
}

export interface MotionSpec {
  id: MotionId;
  where: string;
  motion: string;
  trigger: string;
  /** What the reduced-motion user sees instead. */
  reduced: string;
  /**
   * The `DUR_MS` entries this row's Motion and Implementation columns state, in the order they
   * appear. `motion.test.ts` checks this list against SPEC.md section 8 in both directions.
   */
  durations: readonly DurationKey[];
  /** Framer preset for the full motion; absent when the motion lives in CSS, deck.gl or MapLibre. */
  full?: MotionPreset;
  /** Framer preset under reduced motion; absent means render the final state with no animation. */
  fallback?: MotionPreset;
}

const instant: MotionPreset = {
  initial: false,
  animate: { opacity: 1 },
  transition: { duration: 0 },
};

/** Every row of the catalogue. Add a row here before adding a motion anywhere. */
export const M: Readonly<Record<MotionId, MotionSpec>> = {
  M1: {
    id: "M1",
    where: "Landing hero",
    motion: "Map auto-scrubs -60 to +180 min in 8 s, loops, pauses on hover",
    trigger: "page load",
    reduced: "static +120 min frame",
    durations: ["heroLoop"],
  },
  M2: {
    id: "M2",
    where: "Landing hero copy",
    motion: "Blur-fade entrance, stagger 60 ms, once",
    trigger: "page load",
    reduced: "instant",
    durations: ["staggerCopy"],
    full: {
      initial: { opacity: 0, filter: "blur(6px)", y: 6 },
      animate: { opacity: 1, filter: "blur(0px)", y: 0 },
      transition: tween(DUR.panel),
    },
    fallback: instant,
  },
  M3: {
    id: "M3",
    where: "Landing cycle diagram",
    motion: "Beams travelling between pipeline nodes",
    trigger: "in view",
    reduced: "static arrows",
    durations: [],
  },
  M4: {
    id: "M4",
    where: "Landing proof, hotspot drawer, delta tables",
    motion: "Numbers roll to new values (NumberFlow)",
    trigger: "value change or in view",
    reduced: "instant",
    durations: [],
  },
  M5: {
    id: "M5",
    where: "Landing roadmap",
    motion: "Beam traces the timeline on scroll",
    trigger: "scroll",
    reduced: "static line",
    durations: [],
  },
  M6: {
    id: "M6",
    where: "Console time bar",
    motion: "Scrub handle springs; layers restyle instantly",
    trigger: "drag or keys",
    reduced: "same, no spring",
    durations: [],
    full: { transition: SPRING },
    fallback: { transition: { duration: 0 } },
  },
  M7: {
    id: "M7",
    where: "Console play mode",
    motion: "Depth colours tween between 5-min steps (120 ms)",
    trigger: "play",
    reduced: "no tween",
    durations: ["colourTween"],
  },
  M8: {
    id: "M8",
    where: "Surcharge markers",
    motion: "Expanding ring pulse, 1.6 s",
    trigger: "data",
    reduced: "static ring",
    durations: ["surchargePulse"],
  },
  M9: {
    id: "M9",
    where: "Reversed-flow edges",
    motion: "Dash offset animates in the flow direction",
    trigger: "data",
    reduced: "static dashed red",
    durations: [],
  },
  M10: {
    id: "M10",
    where: "Hotspot select",
    motion: "900 ms fly-to plus ring highlight fades in",
    trigger: "click",
    reduced: "jump cut",
    durations: ["flight"],
    full: { initial: { opacity: 0 }, animate: { opacity: 1 }, transition: tween(DUR.panel) },
    fallback: instant,
  },
  M11: {
    id: "M11",
    where: "Hotspot and attribution drawer",
    motion: "Slides in 220 ms; responsible pipes glow in sequence (40 ms stagger)",
    trigger: "open",
    reduced: "instant",
    durations: ["drawerSlide", "staggerPipes"],
    full: {
      initial: { x: 24, opacity: 0 },
      animate: { x: 0, opacity: 1 },
      exit: { x: 24, opacity: 0 },
      transition: tween(DUR.drawerSlide),
    },
    fallback: instant,
  },
  M12: {
    id: "M12",
    where: "Drain X-ray before and after",
    motion: "Pipe colours cross-fade 300 ms; hotspot depth rolls",
    trigger: "toggle",
    reduced: "instant",
    durations: ["crossFade"],
    full: { initial: { opacity: 0 }, animate: { opacity: 1 }, transition: tween(DUR.crossFade) },
    fallback: instant,
  },
  M13: {
    id: "M13",
    where: "What-if result",
    motion: "Diff layer wipes left to right 500 ms; delta rows highlight 600 ms",
    trigger: "result",
    reduced: "instant",
    durations: ["diffWipe", "rowHighlight"],
    full: {
      initial: { clipPath: "inset(0 100% 0 0)" },
      animate: { clipPath: "inset(0 0% 0 0)" },
      transition: tween(DUR.diffWipe),
    },
    fallback: instant,
  },
  M14: {
    id: "M14",
    where: "Route planner",
    motion:
      "Naive route draws dashed grey, VARUNA route draws on over 1.2 s; avoided segments flash once",
    trigger: "result",
    reduced: "both shown at once",
    durations: ["routeDrawOn"],
    full: {
      initial: { pathLength: 0 },
      animate: { pathLength: 1 },
      transition: tween(DUR.routeDrawOn),
    },
    fallback: { initial: false, animate: { pathLength: 1 }, transition: { duration: 0 } },
  },
  M15: {
    id: "M15",
    where: "Reachability",
    motion: "Isochrone polygons morph on scrub (300 ms)",
    trigger: "scrub",
    reduced: "instant",
    durations: ["isochroneMorph"],
  },
  M16: {
    id: "M16",
    where: "Alerts",
    motion:
      "Card slides into the queue 180 ms; phone mock message pops with a 300 ms shake and optional sound",
    trigger: "new alert",
    reduced: "fade only, no sound",
    durations: ["alertSlide", "phoneShake"],
    full: {
      initial: { y: -12, opacity: 0 },
      animate: { y: 0, opacity: 1 },
      exit: { opacity: 0 },
      transition: tween(DUR.alertSlide),
    },
    fallback: { initial: { opacity: 0 }, animate: { opacity: 1 }, transition: tween(DUR.micro) },
  },
  M17: {
    id: "M17",
    where: "Pump board",
    motion: "Card flies to the hotspot column; benefit numbers roll",
    trigger: "drag or optimise",
    reduced: "instant move",
    durations: [],
    full: { transition: tween(DUR.panel) },
    fallback: { transition: { duration: 0 } },
  },
  M18: {
    id: "M18",
    where: "Ground-truth pins",
    motion: "Pin drops (scale 0 to 1 spring) with a 600 ms ripple; ticker row slides in",
    trigger: "replay clock passes timestamp",
    reduced: "pin appears, no ripple",
    durations: ["pinRipple"],
    full: {
      initial: { scale: 0, opacity: 0 },
      animate: { scale: 1, opacity: 1 },
      transition: SPRING,
    },
    fallback: instant,
  },
  M19: {
    id: "M19",
    where: "Onboarding",
    motion: "Each completed step stacks a map layer with a 400 ms fade; final depth fade-in",
    trigger: "step complete",
    reduced: "instant",
    durations: ["layerFade"],
    full: { initial: { opacity: 0 }, animate: { opacity: 1 }, transition: tween(DUR.layerFade) },
    fallback: instant,
  },
  M20: {
    id: "M20",
    where: "Mode banner",
    motion: "Colour cross-fade 300 ms; degraded pulses once",
    trigger: "mode change",
    reduced: "colour change only",
    durations: ["crossFade"],
  },
  M21: {
    id: "M21",
    where: "Cycle budget bar",
    motion: "Stage segments fill as timings arrive (200 ms width tween)",
    trigger: "WS cycle.stage",
    reduced: "instant",
    durations: ["budgetFill"],
    full: { transition: tween(DUR.budgetFill) },
    fallback: { transition: { duration: 0 } },
  },
  M22: {
    id: "M22",
    where: "Skeletons",
    motion: "Shimmer only",
    trigger: "loading",
    reduced: "static blocks",
    durations: [],
  },
  M23: {
    id: "M23",
    where: "Page navigation",
    motion: "None, instant",
    trigger: "none",
    reduced: "none",
    durations: [],
  },
  M24: {
    id: "M24",
    where: "Public map bottom sheet",
    motion: "Drag with rubber-band, snap points",
    trigger: "drag",
    reduced: "tap to expand",
    durations: [],
    full: { transition: SPRING },
    fallback: { transition: { duration: 0 } },
  },
  M25: {
    id: "M25",
    where: "Replay radar preview",
    motion:
      "Radar frames loop at 4 fps (25 frames, 6.25 s), pauses on hover, focus and when the tab is hidden",
    trigger: "bundle selected",
    reduced: "static middle frame",
    durations: ["radarLoop"],
    fallback: instant,
  },
  M26: {
    id: "M26",
    where: "Landing hero intro",
    motion:
      "Globe turns 1.4 s to face Mumbai and unrolls into a flat world map over 2.6 s, then cross-fades 900 ms into the M1 city map once its frames are decoded; once per load",
    trigger: "page load",
    reduced: "finished flat map, then a cut to M1's static +120 min frame",
    durations: ["globeTurn", "globeUnroll", "heroHandover"],
  },
  M27: {
    id: "M27",
    where: "Citizen dashboard entry and ward officer's desk",
    motion:
      "The vector Earth turns 1.4 s to bring India to the meridian, the frame approaches India over 1.6 s as the sphere flattens, then narrows to the Mumbai AOI over 1.0 s and cross-fades 900 ms into the screen's own map, which is mounted and framed on the same bounds behind it; once per session, and skippable",
    trigger: "opening the dashboard or the desk",
    reduced: "static Mumbai frame, then a cut to the framed map",
    durations: ["globeTurn", "globeApproach", "globeArrive", "heroHandover"],
  },
  M28: {
    id: "M28",
    where: "Console 3D, drain X-ray",
    motion:
      "Google's photorealistic surface fades to 20 % opacity over 300 ms while the inferred pipes beneath it fade up to full",
    trigger: "X-ray toggle",
    reduced: "both layers at their final opacity, no fade",
    durations: ["crossFade"],
  },
  M29: {
    id: "M29",
    where: "Alert centre",
    motion: "An alert's details open under its row over 220 ms; one alert is open at a time",
    trigger: "See more",
    reduced: "details shown at once, no expand",
    durations: ["drawerSlide"],
    full: {
      initial: { opacity: 0, height: 0 },
      animate: { opacity: 1, height: "auto" },
      exit: { opacity: 0, height: 0 },
      transition: tween(0.22),
    },
    fallback: instant,
  },
  M30: {
    id: "M30",
    where: "Drain health before and after",
    motion:
      "A drag handle splits the prior on the left from the posterior on the right; pressing Before or After slides the split across in 300 ms",
    trigger: "drag or Before or After",
    reduced: "split jumps, no slide",
    durations: ["crossFade"],
  },
  M31: {
    id: "M31",
    where: "What-if physics run",
    motion:
      "A bar fills as each of the Twin's output steps arrives, and the physics answer replaces the emulator's with a 300 ms cross-fade",
    trigger: "Twin progress",
    reduced: "bar jumps to each step, result swaps with no fade",
    durations: ["budgetFill", "crossFade"],
  },
  M32: {
    id: "M32",
    where: "Citizen report pins on the dashboard and the ward map",
    motion:
      "A new report's pin drops in, scale 0 to 1 on the spring, with a 600 ms ripple, as M18 does",
    trigger: "a report appears",
    reduced: "pin appears, no ripple",
    durations: ["pinRipple"],
  },
  M33: {
    id: "M33",
    where: "Jalayantra dispatch map",
    motion:
      "Each dispatched pump travels its road route from depot to hotspot over 1.2 s, the route drawing behind it, staggered 150 ms per pump",
    trigger: "Optimise or dispatch",
    reduced: "pumps at their hotspots and routes drawn, no travel",
    durations: ["routeDrawOn", "staggerPumps"],
  },
  M34: {
    id: "M34",
    where: "Jalayantra hotspot gauges",
    motion:
      "A hotspot's water gauge drains from the no-pump level to the planned level over 900 ms as its pump arrives, and its minutes above 45 cm roll down",
    trigger: "pump arrival",
    reduced: "gauge and number at the final value, no drain",
    durations: ["gaugeDrain"],
  },
  M35: {
    id: "M35",
    where: "Jalayantra arrival timeline",
    motion:
      "Each pump's arrival marker slides onto the flood-window timeline in 300 ms when the plan lands",
    trigger: "Optimise",
    reduced: "markers at their times, no slide",
    durations: ["crossFade"],
  },
};

/** Ordered list for the design page and tests. */
export const MOTION_IDS = Object.keys(M) as MotionId[];

/** The preset a component should spread, given the user's motion preference. */
export function presetFor(id: MotionId, reduced: boolean): MotionPreset {
  const spec = M[id];
  if (reduced) return spec.fallback ?? instant;
  return spec.full ?? instant;
}

/** Stagger transition for a list (copy lines, responsible pipes). */
export function stagger(childSeconds: number, base: Transition = tween(DUR.panel)): Transition {
  return { ...base, staggerChildren: childSeconds };
}

export interface MotionPref {
  /** True when the OS asks for reduced motion (null from the hook is treated as false). */
  reduced: boolean;
  /** Picks the full or fallback value. */
  pick: <T>(full: T, fallback: T) => T;
  /** Framer props for a catalogue row under the current preference. */
  preset: (id: MotionId) => MotionPreset;
  /** Duration in seconds, zero under reduced motion. */
  seconds: (seconds: number) => number;
}

/**
 * Client hook: wraps `useReducedMotion` so every component asks the same question the same way.
 * Returns stable helpers; call inside client components only.
 */
export function useMotionPref(): MotionPref {
  const reduced = useReducedMotion() === true;
  return {
    reduced,
    pick: (full, fallback) => (reduced ? fallback : full),
    preset: (id) => presetFor(id, reduced),
    seconds: (seconds) => (reduced ? 0 : seconds),
  };
}

#!/usr/bin/env node
/**
 * VARUNA design-token build.
 *
 * tokens.json is the single source of truth (SPEC.md section 6). This script derives everything
 * the UI consumes from it so map pixels, chips, charts and Python ramps can never disagree:
 *
 *   dist/tokens.css   CSS custom properties on :root (--ink, --depth-1, --radius-panel ...),
 *                     a Tailwind v4 @theme block (bg-ink, text-text-2, border-line, rounded-panel,
 *                     text-h1, font-display ...) and @utility classes (num, type-micro ... type-hero).
 *   dist/tokens.js    ESM export of the whole tokens object plus colour helpers for the UI and deck.gl.
 *   dist/tokens.d.ts  Declarations for tokens.js with literal types derived from tokens.json.
 *
 * Usage:
 *   node build.mjs            write dist/
 *   node build.mjs --check    exit 1 when dist/ is missing or stale relative to tokens.json or this file
 *
 * No dependencies. Deterministic: the same tokens.json always produces byte-identical outputs.
 */

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const TOKENS_PATH = path.join(HERE, "tokens.json");
export const DIST_DIR = path.join(HERE, "dist");
export const OUTPUT_FILES = ["tokens.css", "tokens.js", "tokens.d.ts"];

const HEX6 = /^#[0-9A-F]{6}$/;
const DEPTH_ORDER = ["dry", "1", "2", "3", "4", "5"];
const RAIN_ORDER = ["1", "2", "3", "4", "5", "6"];
const DRAIN_ORDER = ["0", "1", "2", "3"];
const CHART_ORDER = ["1", "2", "3", "4", "5"];
const REACH_ORDER = ["5", "10", "15"];
const TYPE_ORDER = ["micro", "small", "body", "h3", "h2", "h1", "display", "hero"];
/** The themes, dark first: dark is the default and the one :root carries. */
export const THEMES = ["dark", "light"];
/** Colour groups the light theme may override, and which keys (null = any key of the group). */
const LIGHT_OVERRIDABLE = {
  base: null,
  depth: ["dry"],
  drain: ["0"],
  semantic: null,
  reach: null,
  obs: null,
  status: null,
  chart: null,
};
/** Fills that carry text, in order: the accent and every depth band. */
const ON_FILL_ORDER = ["tide", ...DEPTH_ORDER.map((k) => `depth-${k}`), "obs"];
/** Type sizes set in the display family (Bricolage Grotesque), weight 600, tracking -0.02em. */
const DISPLAY_SIZES = new Set(["h1", "display", "hero"]);
/** Weight per size for the sans sizes. */
const SANS_WEIGHTS = { micro: 400, small: 400, body: 400, h3: 600, h2: 600 };

const normaliseNewlines = (text) => text.replace(/\r\n/g, "\n");
const sha256 = (text) => createHash("sha256").update(text).digest("hex");
const px = (n) => `${n}px`;
const ms = (n) => `${n}ms`;

/* ------------------------------------------------------------------------------------------------
 * Loading and validation
 * ---------------------------------------------------------------------------------------------- */

export function loadTokens(file = TOKENS_PATH) {
  const raw = normaliseNewlines(readFileSync(file, "utf8"));
  const tokens = JSON.parse(raw);
  validateTokens(tokens);
  return { tokens, hash: sha256(raw) };
}

/** Throws with a precise message when tokens.json does not have the shape this build relies on. */
export function validateTokens(t) {
  const fail = (msg) => {
    throw new Error(`tokens.json: ${msg}`);
  };
  const needHex = (value, where) => {
    if (typeof value !== "string" || !HEX6.test(value)) fail(`${where} must be an upper-case #RRGGBB hex, got ${JSON.stringify(value)}`);
  };
  const needGroup = (group, keys, where) => {
    if (!group || typeof group !== "object") fail(`${where} is missing`);
    for (const k of keys) {
      if (!group[k]) fail(`${where}.${k} is missing`);
      needHex(group[k].value, `${where}.${k}.value`);
    }
  };
  for (const section of ["color", "probability", "raster", "font", "type_scale", "radius", "space", "layout", "glass", "icon", "motion", "focus"]) {
    if (!t[section]) fail(`section "${section}" is missing`);
  }
  needGroup(t.color.base, ["ink", "deep", "well", "line", "line-strong", "text", "text-2", "text-3", "tide", "tide-soft"], "color.base");
  needGroup(t.color.depth, DEPTH_ORDER, "color.depth");
  needGroup(t.color.rain, RAIN_ORDER, "color.rain");
  needGroup(t.color.drain, DRAIN_ORDER, "color.drain");
  needGroup(t.color.semantic, ["surcharge", "naive", "truth", "danger"], "color.semantic");
  needGroup(t.color.reach, REACH_ORDER, "color.reach");
  needGroup(t.color.obs, ["traffic", "report", "sensor", "cctv", "sar"], "color.obs");
  needGroup(t.color.status, ["live", "replay", "baked", "degraded"], "color.status");
  needGroup(t.color.chart, CHART_ORDER, "color.chart");
  let previousMax = 0;
  for (const key of DEPTH_ORDER) {
    const band = t.color.depth[key];
    if (typeof band.min_cm !== "number" || band.min_cm !== previousMax) fail(`color.depth.${key}.min_cm must equal the previous band's max_cm (${previousMax})`);
    if (band.max_cm !== null && typeof band.max_cm !== "number") fail(`color.depth.${key}.max_cm must be a number or null`);
    if (typeof band.label !== "string") fail(`color.depth.${key}.label is missing`);
    previousMax = band.max_cm;
  }
  if (previousMax !== null) fail("the last depth band must be open-ended (max_cm: null)");
  let previousRate = t.color.rain[RAIN_ORDER[0]].min_mm_h;
  if (typeof previousRate !== "number" || previousRate <= 0) fail(`color.rain.${RAIN_ORDER[0]}.min_mm_h must be a rain rate above 0`);
  for (const key of RAIN_ORDER) {
    const band = t.color.rain[key];
    if (typeof band.min_mm_h !== "number" || band.min_mm_h !== previousRate) fail(`color.rain.${key}.min_mm_h must equal the previous band's max_mm_h (${previousRate})`);
    if (band.max_mm_h !== null && (typeof band.max_mm_h !== "number" || band.max_mm_h <= band.min_mm_h)) fail(`color.rain.${key}.max_mm_h must be a number above min_mm_h, or null`);
    if (typeof band.label !== "string") fail(`color.rain.${key}.label is missing`);
    previousRate = band.max_mm_h;
  }
  if (previousRate !== null) fail("the last rain band must be open-ended (max_mm_h: null)");
  let previousBeta = 0;
  for (const key of DRAIN_ORDER) {
    const band = t.color.drain[key];
    if (band.min_beta !== previousBeta) fail(`color.drain.${key}.min_beta must equal the previous band's max_beta (${previousBeta})`);
    previousBeta = band.max_beta;
  }
  if (previousBeta !== 1) fail("the last drain band must end at beta = 1");
  for (const key of REACH_ORDER) {
    const r = t.color.reach[key];
    if (typeof r.opacity !== "number" || r.opacity <= 0 || r.opacity > 1) fail(`color.reach.${key}.opacity must be in (0, 1]`);
  }
  for (const key of TYPE_ORDER) {
    const s = t.type_scale[key];
    if (!s || typeof s.size !== "number" || typeof s.line_height !== "number") fail(`type_scale.${key} needs size and line_height`);
  }
  for (const key of ["display", "sans", "mono"]) {
    if (!t.font[key] || typeof t.font[key].family !== "string") fail(`font.${key}.family is missing`);
  }
  if (typeof t.probability.min_opacity !== "number") fail("probability.min_opacity is missing");
  if (!Array.isArray(t.probability.thresholds_cm)) fail("probability.thresholds_cm must be an array");
  needHex(t.focus.ring_color, "focus.ring_color");
  needHex(t.glass.background, "glass.background");
  if (typeof t.motion.easing !== "string") fail("motion.easing is missing");
  validateTheme(t, fail, needHex);
}

/**
 * The theme section: a light set that overrides the dark tokens by name, and the text colours that
 * sit on a fill. Light may only rename nothing, add nothing and leave the water alone: depth 1-5
 * and the rain ramp are the same pixels in Python rasters and in both themes, so they cannot move.
 */
function validateTheme(t, fail, needHex) {
  const theme = t.theme;
  if (!theme || typeof theme !== "object") fail('section "theme" is missing');
  if (!THEMES.includes(theme.default)) fail(`theme.default must be one of ${THEMES.join(", ")}`);
  if (typeof theme.storage_key !== "string" || !theme.storage_key) fail("theme.storage_key is missing");
  if (theme.attribute !== "data-theme") fail('theme.attribute must be "data-theme"');
  const light = theme.light;
  if (!light || !light.color) fail("theme.light.color is missing");
  for (const [group, entries] of Object.entries(light.color)) {
    if (!(group in LIGHT_OVERRIDABLE)) fail(`theme.light.color.${group} cannot change with the theme`);
    const allowed = LIGHT_OVERRIDABLE[group] ?? Object.keys(t.color[group]);
    for (const [key, entry] of Object.entries(entries)) {
      if (!allowed.includes(key)) fail(`theme.light.color.${group}.${key} is not a token that may change with the theme`);
      needHex(entry?.value, `theme.light.color.${group}.${key}.value`);
    }
  }
  for (const key of Object.keys(t.color.base)) {
    if (!light.color.base?.[key]) fail(`theme.light.color.base.${key} is missing: every surface and text colour needs a light value`);
  }
  needHex(light.glass?.background, "theme.light.glass.background");
  if (typeof light.glass?.opacity !== "number" || light.glass.opacity <= 0 || light.glass.opacity > 1) fail("theme.light.glass.opacity must be in (0, 1]");
  needHex(light.focus?.ring_color, "theme.light.focus.ring_color");
  const onDark = theme.on_fill?.dark;
  if (!onDark) fail("theme.on_fill.dark is missing");
  for (const key of ON_FILL_ORDER) {
    if (!onDark[key]) fail(`theme.on_fill.dark.${key} is missing`);
    needHex(onDark[key].value, `theme.on_fill.dark.${key}.value`);
  }
  for (const [key, entry] of Object.entries(theme.on_fill.light ?? {})) {
    if (!ON_FILL_ORDER.includes(key)) fail(`theme.on_fill.light.${key} has no dark counterpart`);
    needHex(entry?.value, `theme.on_fill.light.${key}.value`);
  }
}

/* ------------------------------------------------------------------------------------------------
 * Derived views of the tokens
 * ---------------------------------------------------------------------------------------------- */

function hexToRgbTriplet(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** `rgb(r g b / a)` from a hex and an opacity in [0, 1]. */
function rgbWithAlpha(hex, opacity) {
  const [r, g, b] = hexToRgbTriplet(hex);
  return `rgb(${r} ${g} ${b} / ${opacity})`;
}

/** A CSS font-family stack: the next/font (or geist) variable first, then the token fallbacks. */
function fontStack(fontToken, cssVariable) {
  const fallback = (fontToken.fallback || "sans-serif")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((f) => (/\s/.test(f) ? `"${f}"` : f))
    .join(", ");
  return `var(${cssVariable}), ${fallback}`;
}

/**
 * Ordered list of solid colour custom properties: [name without "--", hex]. `theme` "light" lays
 * the light overrides over the dark values by name; the default is the dark set :root carries.
 */
export function solidColorEntries(t, theme = "dark") {
  const light = theme === "light" ? t.theme.light.color : {};
  const pick = (group, key) => light[group]?.[key]?.value ?? t.color[group][key].value;
  const out = [];
  for (const k of Object.keys(t.color.base)) out.push([k, pick("base", k)]);
  for (const k of DEPTH_ORDER) out.push([`depth-${k}`, pick("depth", k)]);
  for (const k of RAIN_ORDER) out.push([`rain-${k}`, pick("rain", k)]);
  for (const k of DRAIN_ORDER) out.push([`drain-${k}`, pick("drain", k)]);
  for (const k of Object.keys(t.color.semantic)) out.push([k, pick("semantic", k)]);
  for (const k of Object.keys(t.color.obs)) out.push([`obs-${k}`, pick("obs", k)]);
  for (const k of Object.keys(t.color.status)) out.push([`status-${k}`, pick("status", k)]);
  for (const k of CHART_ORDER) out.push([`chart-${k}`, pick("chart", k)]);
  return out;
}

/** Text colours that sit on a fill, per theme: [name without "--", hex], e.g. ["on-depth-5", "#FFFFFF"]. */
export function onFillEntries(t, theme = "dark") {
  const light = theme === "light" ? (t.theme.on_fill.light ?? {}) : {};
  return ON_FILL_ORDER.map((k) => [`on-${k}`, light[k]?.value ?? t.theme.on_fill.dark[k].value]);
}

/** Reachability ring colours per theme: [minutes, `rgb(r g b / a)`]. The opacities never change. */
function reachEntries(t, theme = "dark") {
  const light = theme === "light" ? (t.theme.light.color.reach ?? {}) : {};
  return REACH_ORDER.map((k) => [k, rgbWithAlpha(light[k]?.value ?? t.color.reach[k].value, t.color.reach[k].opacity)]);
}

/** Every colour a theme resolves, by name: solids, then the text-on-fill colours. */
export function themeColorMap(t, theme = "dark") {
  return Object.fromEntries([...solidColorEntries(t, theme), ...onFillEntries(t, theme)]);
}

/**
 * The light theme's overrides as custom properties, grouped like :root. Only the names whose value
 * differs from dark are written, so the block reads as the list of what daylight changes.
 */
export function lightVarGroups(t) {
  const dark = themeColorMap(t, "dark");
  const light = themeColorMap(t, "light");
  const changed = (names) => names.filter((n) => light[n] !== dark[n]).map((n) => [`--${n}`, light[n]]);
  const names = Object.keys(light);
  const groups = [];
  const group = (title, vars) => {
    if (vars.length) groups.push({ title, vars });
  };
  group("Base surface and text", changed(Object.keys(t.color.base)));
  group("Depth ramp: only the dry band moves; 5 cm and up keep their hex", changed(names.filter((n) => n.startsWith("depth-"))));
  group("Drain blockage: a clear pipe stays quiet on a light ground", changed(names.filter((n) => n.startsWith("drain-"))));
  group("Semantic", changed(Object.keys(t.color.semantic)));
  const darkReach = Object.fromEntries(reachEntries(t, "dark"));
  group(
    "Reachability isochrones",
    reachEntries(t, "light")
      .filter(([k, v]) => darkReach[k] !== v)
      .map(([k, v]) => [`--reach-${k}`, v]),
  );
  group("Observation types (Pulse)", changed(names.filter((n) => n.startsWith("obs-"))));
  group("Mode banner status", changed(names.filter((n) => n.startsWith("status-"))));
  group("Charts, in order", changed(names.filter((n) => n.startsWith("chart-"))));
  group("Text on fills", changed(names.filter((n) => n.startsWith("on-"))));
  group("Glass", [["--glass-bg", rgbWithAlpha(t.theme.light.glass.background, t.theme.light.glass.opacity)]]);
  group("Focus ring", [["--focus-ring-color", t.theme.light.focus.ring_color]]);
  return groups;
}

/**
 * Every custom property dist/tokens.css defines on :root, grouped with a title so the CSS reads
 * like the spec. Order is stable, which keeps the build byte-reproducible.
 */
export function cssVarGroups(t) {
  const groups = [];
  const group = (title, vars) => groups.push({ title, vars });

  group("Base surface and text", Object.entries(t.color.base).map(([k, v]) => [`--${k}`, v.value]));
  group(
    "Depth ramp: water depth only, fixed meaning everywhere",
    DEPTH_ORDER.map((k) => [`--depth-${k}`, t.color.depth[k].value, `${t.color.depth[k].label}, ${t.color.depth[k].meaning}`]),
  );
  group(
    "Rain rate ramp: radar and nowcast rain only, never water depth",
    RAIN_ORDER.map((k) => [`--rain-${k}`, t.color.rain[k].value, `${t.color.rain[k].label}, ${t.color.rain[k].meaning}`]),
  );
  group(
    "Drain blockage (posterior beta)",
    DRAIN_ORDER.map((k) => [`--drain-${k}`, t.color.drain[k].value, `beta ${t.color.drain[k].min_beta} to ${t.color.drain[k].max_beta}`]),
  );
  group("Semantic", Object.entries(t.color.semantic).map(([k, v]) => [`--${k}`, v.value, v.use]));
  group(
    "Reachability isochrones (tide at the token opacity)",
    REACH_ORDER.map((k) => [`--reach-${k}`, rgbWithAlpha(t.color.reach[k].value, t.color.reach[k].opacity), `${t.color.reach[k].minutes} min`]),
  );
  group("Observation types (Pulse)", Object.entries(t.color.obs).map(([k, v]) => [`--obs-${k}`, v.value]));
  group("Mode banner status", Object.entries(t.color.status).map(([k, v]) => [`--status-${k}`, v.value]));
  group("Charts, in order", CHART_ORDER.map((k) => [`--chart-${k}`, t.color.chart[k].value]));
  group("Probability mode and rasters", [
    ["--prob-min-opacity", String(t.probability.min_opacity), "opacity floor so a segment never vanishes"],
    ["--prob-band-opacity", String(t.probability.band_opacity), "p10 to p90 band on charts"],
    ["--raster-depth-opacity", String(t.raster.depth_layer_opacity), "depth raster BitmapLayer"],
  ]);
  group("Radius", Object.entries(t.radius).map(([k, v]) => [`--radius-${k}`, px(v)]));
  group("Spacing and density (4 pt grid)", [
    ["--space-grid", px(t.space.grid)],
    ["--row", px(t.space.row), "list rows"],
    ["--row-dense", px(t.space.row_dense), "dense table rows"],
    ["--panel-padding", px(t.space.panel_padding)],
    ["--section-gap", px(t.space.section_gap)],
  ]);
  group("Console layout", [
    ["--top-bar", px(t.layout.top_bar)],
    ["--time-bar", px(t.layout.time_bar)],
    ["--icon-rail", px(t.layout.icon_rail)],
    ["--right-rail", px(t.layout.right_rail)],
  ]);
  group(`Glass (allowed on the ${t.glass.allowed_on})`, [
    ["--glass-bg", rgbWithAlpha(t.glass.background, t.glass.opacity)],
    ["--glass-blur", px(t.glass.blur_px)],
  ]);
  group("Icons (Lucide)", [
    ["--icon-row", px(t.icon.row)],
    ["--icon-nav", px(t.icon.nav)],
    ["--icon-stroke", String(t.icon.stroke)],
  ]);
  group("Motion (SPEC.md section 8)", [
    ["--ease-ui", t.motion.easing],
    ["--dur-micro", ms(t.motion.duration_ms.micro)],
    ["--dur-micro-min", ms(t.motion.duration_ms.micro_min)],
    ["--dur-micro-max", ms(t.motion.duration_ms.micro_max)],
    ["--dur-panel", ms(t.motion.duration_ms.panel)],
    ["--dur-flight", ms(t.motion.duration_ms.flight)],
    ["--dur-draw-on-max", ms(t.motion.duration_ms.draw_on_max)],
    ["--dur-surcharge-pulse", ms(t.motion.surcharge_pulse_ms)],
    ["--spring-stiffness", String(t.motion.spring.stiffness)],
    ["--spring-damping", String(t.motion.spring.damping)],
    ["--fly-to-curve", String(t.motion.fly_to_curve)],
  ]);
  group("Focus ring (the only glow besides the active hotspot ring)", [
    ["--focus-ring-color", t.focus.ring_color],
    ["--focus-ring-width", px(t.focus.ring_px)],
    ["--focus-ring", `0 0 0 var(--focus-ring-width) var(--focus-ring-color)`],
  ]);
  group("Type", [["--tracking-display", t.font.display.tracking]]);
  return groups;
}

/** Flat `{ "--name": value }` of everything in cssVarGroups. */
export function cssVarMap(t) {
  const out = {};
  for (const g of cssVarGroups(t)) for (const [name, value] of g.vars) out[name] = value;
  return out;
}

/* ------------------------------------------------------------------------------------------------
 * CSS
 * ---------------------------------------------------------------------------------------------- */

function header(hash, commentOpen, commentClose) {
  return [
    `${commentOpen} VARUNA design tokens. Generated by packages/tokens/build.mjs from tokens.json; do not edit.`,
    `   Source of truth: SPEC.md section 6 (design system).`,
    `   tokens.json sha256: ${hash} ${commentClose}`,
  ].join("\n");
}

export function generateCss(t, hash) {
  const lines = [];
  const push = (s = "") => lines.push(s);
  push(header(hash, "/*", "*/"));
  push("");
  push("/* Custom properties. Every colour, radius, spacing, layout and motion value the UI may use. */");
  push(":root {");
  for (const g of cssVarGroups(t)) {
    push(`  /* ${g.title} */`);
    for (const [name, value, note] of g.vars) push(`  ${name}: ${value};${note ? ` /* ${note} */` : ""}`);
  }
  push("}");
  push("");
  push("/* Text on fills: the colour a label takes on --tide or on a depth band, chosen per fill for");
  push("   contrast. A separate block so the dark :root above stays exactly what it was. */");
  push(":root {");
  for (const [name, value] of onFillEntries(t)) push(`  --${name}: ${value};`);
  push("}");
  push("");
  push(`/* Light theme: the same control room in daylight, switched by <html ${t.theme.attribute}="light">.`);
  push("   Only what changes is listed. Tailwind utilities read var(--color-*), so those follow too. */");
  push(`:root[${t.theme.attribute}="light"] {`);
  const lightGroups = lightVarGroups(t);
  for (const g of lightGroups) {
    push(`  /* ${g.title} */`);
    for (const [name, value] of g.vars) push(`  ${name}: ${value};`);
  }
  push("  /* Tailwind theme colours */");
  for (const g of lightGroups) {
    for (const [name, value] of g.vars) {
      if (name.startsWith("--glass") || name.startsWith("--focus")) continue;
      push(`  --color-${name.slice(2)}: ${value};`);
    }
  }
  push("}");
  push("");

  const display = fontStack(t.font.display, "--font-bricolage");
  const sans = fontStack(t.font.sans, "--font-geist-sans");
  const mono = fontStack(t.font.mono, "--font-geist-mono");

  push("/* Tailwind v4 theme. Generates bg-ink, text-text-2, border-line, bg-depth-3, rounded-panel,");
  push("   h-top-bar, w-right-rail, text-h1, tracking-display, ease-ui and so on. */");
  push("@theme {");
  push("  /* Colours */");
  for (const [name, value] of solidColorEntries(t)) push(`  --color-${name}: ${value};`);
  for (const k of REACH_ORDER) push(`  --color-reach-${k}: ${rgbWithAlpha(t.color.reach[k].value, t.color.reach[k].opacity)};`);
  push("  /* Text on fills: text-on-tide, text-on-depth-5 ... */");
  for (const [name, value] of onFillEntries(t)) push(`  --color-${name}: ${value};`);
  push("  /* Radius: rounded-panel, rounded-control, rounded-chip, rounded-popover, rounded-phone */");
  for (const [k, v] of Object.entries(t.radius)) push(`  --radius-${k}: ${px(v)};`);
  push("  /* Spacing: h-top-bar, h-time-bar, w-icon-rail, w-right-rail, h-row, h-row-dense, p-panel, gap-section */");
  push(`  --spacing-top-bar: ${px(t.layout.top_bar)};`);
  push(`  --spacing-time-bar: ${px(t.layout.time_bar)};`);
  push(`  --spacing-icon-rail: ${px(t.layout.icon_rail)};`);
  push(`  --spacing-right-rail: ${px(t.layout.right_rail)};`);
  push(`  --spacing-row: ${px(t.space.row)};`);
  push(`  --spacing-row-dense: ${px(t.space.row_dense)};`);
  push(`  --spacing-panel: ${px(t.space.panel_padding)};`);
  push(`  --spacing-section: ${px(t.space.section_gap)};`);
  push("  /* Motion: ease-ui */");
  push(`  --ease-ui: ${t.motion.easing};`);
  push("  /* Type scale: text-micro ... text-hero (size and line-height) */");
  for (const k of TYPE_ORDER) {
    push(`  --text-${k}: ${px(t.type_scale[k].size)};`);
    push(`  --text-${k}--line-height: ${t.type_scale[k].line_height};`);
  }
  push("  /* Tracking: tracking-display */");
  push(`  --tracking-display: ${t.font.display.tracking};`);
  push("}");
  push("");
  push("/* Font families are inlined so the next/font variables resolve on the element, not on :root. */");
  push("@theme inline {");
  push(`  --font-display: ${display};`);
  push(`  --font-sans: ${sans};`);
  push(`  --font-mono: ${mono};`);
  push("}");
  push("");
  push("/* Every element that shows a number carries .num (SPEC.md section 6.3). */");
  push("@utility num {");
  push("  font-variant-numeric: tabular-nums;");
  push("}");
  push("");
  push("/* Type presets: size, line-height, family and weight in one class. h1, display and hero use the");
  push("   display family with tracking -0.02em; everything else is Geist Sans. */");
  for (const k of TYPE_ORDER) {
    const s = t.type_scale[k];
    const isDisplay = DISPLAY_SIZES.has(k);
    push(`@utility type-${k} {`);
    push(`  font-family: ${isDisplay ? display : sans};`);
    push(`  font-size: ${px(s.size)};`);
    push(`  line-height: ${s.line_height};`);
    push(`  font-weight: ${isDisplay ? 600 : SANS_WEIGHTS[k]};`);
    if (isDisplay) push(`  letter-spacing: ${t.font.display.tracking};`);
    push("}");
  }
  push("");
  push("/* Run IDs, CAP XML, API explorer and log streams only; never for labels or small data. */");
  push("@utility type-mono {");
  push(`  font-family: ${mono};`);
  push(`  font-size: ${px(t.type_scale.small.size)};`);
  push(`  line-height: ${t.type_scale.small.line_height};`);
  push("}");
  return `${lines.join("\n")}\n`;
}

/* ------------------------------------------------------------------------------------------------
 * JavaScript
 * ---------------------------------------------------------------------------------------------- */

function depthBands(t) {
  return DEPTH_ORDER.map((key) => {
    const b = t.color.depth[key];
    return { key, hex: b.value, label: b.label, meaning: b.meaning, min_cm: b.min_cm, max_cm: b.max_cm };
  });
}

function rainBands(t) {
  return RAIN_ORDER.map((key) => {
    const b = t.color.rain[key];
    return { key, hex: b.value, label: b.label, meaning: b.meaning, min_mm_h: b.min_mm_h, max_mm_h: b.max_mm_h };
  });
}

function drainBands(t) {
  return DRAIN_ORDER.map((key) => {
    const b = t.color.drain[key];
    return { key, hex: b.value, min_beta: b.min_beta, max_beta: b.max_beta };
  });
}

function depthThresholds(t) {
  return DEPTH_ORDER.slice(1).map((k) => t.color.depth[k].min_cm);
}

function rainThresholds(t) {
  return RAIN_ORDER.map((k) => t.color.rain[k].min_mm_h);
}

/** Private helpers emitted at the top of dist/tokens.js. */
const PRELUDE = String.raw`const deepFreeze = (value) => {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const key of Object.keys(value)) deepFreeze(value[key]);
  }
  return value;
};
const finite = (n, fallback) => (typeof n === "number" && Number.isFinite(n) ? n : fallback);
const real = (n, fallback) => (typeof n === "number" && !Number.isNaN(n) ? n : fallback);
const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));
const clampAlpha = (alpha) => Math.round(clamp(finite(alpha, 255), 0, 255));
const bandAtOrAbove = (bands, value, field) => {
  for (let i = bands.length - 1; i >= 0; i -= 1) if (value >= bands[i][field]) return bands[i];
  return bands[0];
};
`;

/** Public helper functions emitted after the data in dist/tokens.js. */
const HELPERS = String.raw`
/** Depth bands in ramp order: dry, 1, 2, 3, 4, 5. A fresh array; the band objects are frozen. */
export function depthRampStops() {
  return DEPTH_BANDS.slice();
}

/** Rain-rate bands in ramp order: 1 (drizzle) to 6 (cloudburst). */
export function rainRampStops() {
  return RAIN_BANDS.slice();
}

/** Drain bands in ramp order: 0 (clear) to 3 (blocked). */
export function drainRampStops() {
  return DRAIN_BANDS.slice();
}

/**
 * The depth band for a depth in centimetres. Lower bounds are inclusive, upper bounds exclusive:
 * 4.9 -> dry, 5 -> 1, 15 -> 2, 30 -> 3, 45 -> 4, 60 and above -> 5. NaN, non-numbers, negatives
 * and -Infinity -> dry; +Infinity -> 5.
 */
export function depthBand(cm) {
  return bandAtOrAbove(DEPTH_BANDS, real(cm, 0), "min_cm");
}

/** Hex colour of the depth band for a depth in centimetres. */
export function depthColor(cm) {
  return depthBand(cm).hex;
}

/** [r, g, b, a] for deck.gl, alpha 0-255 (default 255). */
export function depthColorRgba(cm, alpha = 255) {
  return hexToRgba(depthColor(cm), alpha);
}

/**
 * The rain band for a rate in mm/h, lower bounds inclusive: 2 -> 2, 20 -> 4, 80 and above -> 6.
 * Below the first band (0.5 mm/h) there is no echo to draw, so the result is null; so is NaN.
 */
export function rainBand(mmH) {
  const rate = real(mmH, 0);
  for (let i = RAIN_BANDS.length - 1; i >= 0; i -= 1) if (rate >= RAIN_BANDS[i].min_mm_h) return RAIN_BANDS[i];
  return null;
}

/** Hex colour of the rain band for a rate in mm/h; null below the first band. */
export function rainColor(mmH) {
  const band = rainBand(mmH);
  return band === null ? null : band.hex;
}

/** [r, g, b, a] for deck.gl rain rasters, alpha 0-255 (default 255); fully transparent below the first band. */
export function rainColorRgba(mmH, alpha = 255) {
  const hex = rainColor(mmH);
  return hex === null ? [0, 0, 0, 0] : hexToRgba(hex, alpha);
}

/** The drain band for a posterior blockage beta clamped to [0, 1]: 0.25 -> 1, 0.5 -> 2, 0.75 -> 3; NaN -> 0. */
export function drainBand(beta) {
  return bandAtOrAbove(DRAIN_BANDS, clamp(real(beta, 0), 0, 1), "min_beta");
}

/** Hex colour of the drain band for a posterior blockage beta. */
export function drainColor(beta) {
  return drainBand(beta).hex;
}

/** [r, g, b, a] for deck.gl drain paths, alpha 0-255 (default 255). */
export function drainColorRgba(beta, alpha = 255) {
  return hexToRgba(drainColor(beta), alpha);
}

/**
 * Opacity for probability mode: P(depth > threshold) clamped to the token floor so a segment never
 * vanishes. probabilityOpacity(0) === MIN_PROBABILITY_OPACITY; non-finite input returns the floor.
 */
export function probabilityOpacity(p) {
  return clamp(finite(p, 0), MIN_PROBABILITY_OPACITY, 1);
}

/** Opacity in [0, 1] to a deck.gl alpha in 0-255. */
export function opacityToAlpha(opacity) {
  return Math.round(clamp(finite(opacity, 1), 0, 1) * 255);
}

/** [r, g, b] from #RGB, #RGBA, #RRGGBB or #RRGGBBAA. Throws RangeError on anything else. */
export function hexToRgb(hex) {
  return hexToRgba(hex).slice(0, 3);
}

/**
 * [r, g, b, a] from a hex colour. alpha (0-255) overrides an embedded alpha channel; without it an
 * 8-digit hex keeps its own alpha and shorter forms are opaque.
 */
export function hexToRgba(hex, alpha) {
  const match = /^#?([0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(String(hex).trim());
  if (!match) throw new RangeError("hexToRgba: not a hex colour: " + String(hex));
  let digits = match[1];
  if (digits.length <= 4) digits = digits.split("").map((c) => c + c).join("");
  const n = parseInt(digits.slice(0, 6), 16);
  const embedded = digits.length === 8 ? parseInt(digits.slice(6, 8), 16) : 255;
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255, alpha === undefined ? embedded : clampAlpha(alpha)];
}

/** [r, g, b, a] for a reachability isochrone ring (5, 10 or 15 minutes) at its token opacity. */
export function reachColorRgba(minutes) {
  const reach = tokens.color.reach[String(minutes)];
  if (!reach) throw new RangeError("reachColorRgba: minutes must be one of " + Object.keys(tokens.color.reach).join(", "));
  return hexToRgba(reach.value, opacityToAlpha(reach.opacity));
}

/** Chart series colour by 0-based index, cycling through chart-1 ... chart-5. */
export function chartColor(index) {
  const keys = Object.keys(tokens.color.chart);
  const i = ((Math.trunc(finite(index, 0)) % keys.length) + keys.length) % keys.length;
  return tokens.color.chart[keys[i]].value;
}

/** Mode banner colour: live, replay, baked or degraded. */
export function statusColor(mode) {
  const entry = tokens.color.status[mode];
  if (!entry) throw new RangeError("statusColor: unknown mode " + String(mode));
  return entry.value;
}

/** Observation type colour: traffic, report, sensor, cctv or sar. */
export function obsColor(kind) {
  const entry = tokens.color.obs[kind];
  if (!entry) throw new RangeError("obsColor: unknown observation kind " + String(kind));
  return entry.value;
}

/** True for "dark" or "light". */
export function isTheme(value) {
  return THEMES.includes(value);
}

/** The colour table of a theme; anything that is not a theme name reads as the default theme. */
export function colorsFor(theme) {
  return themeColors[isTheme(theme) ? theme : DEFAULT_THEME];
}

/** "var(--name)" for a token name, e.g. cssVar("tide") -> "var(--tide)". */
export function cssVar(name) {
  return "var(--" + String(name).replace(/^--/, "") + ")";
}
`;

export function generateJs(t, hash) {
  const json = (value) => JSON.stringify(value, null, 2);
  const colors = Object.fromEntries(solidColorEntries(t));
  const lines = [
    header(hash, "/*", "*/"),
    "",
    PRELUDE.trim(),
    "",
    "/** The whole tokens.json, deep-frozen. */",
    `export const tokens = deepFreeze(${json(t)});`,
    "",
    "export default tokens;",
    "",
    "/** Every custom property dist/tokens.css defines on :root, by name. */",
    `export const cssVars = deepFreeze(${json(cssVarMap(t))});`,
    "",
    "/** Solid colour tokens by name, e.g. colors.tide, colors[\"depth-3\"], colors[\"obs-traffic\"]. */",
    `export const colors = deepFreeze(${json(colors)});`,
    "",
    "/** The themes, dark first. <html data-theme> carries the choice; dark is the default. */",
    `export const THEMES = deepFreeze(${json(THEMES)});`,
    "",
    "/** The theme a first visit gets. */",
    `export const DEFAULT_THEME = ${JSON.stringify(t.theme.default)};`,
    "",
    "/** localStorage key the chosen theme is kept under. */",
    `export const THEME_STORAGE_KEY = ${JSON.stringify(t.theme.storage_key)};`,
    "",
    "/** The attribute on <html> that switches the theme. */",
    `export const THEME_ATTRIBUTE = ${JSON.stringify(t.theme.attribute)};`,
    "",
    "/** Every colour each theme resolves, by name: the solids plus the on-* text colours for fills. */",
    `export const themeColors = deepFreeze(${json(Object.fromEntries(THEMES.map((name) => [name, themeColorMap(t, name)])))});`,
    "",
    "/** Depth band lower bounds in centimetres: the fixed thresholds of the ramp. */",
    `export const DEPTH_THRESHOLDS_CM = deepFreeze(${json(depthThresholds(t))});`,
    "",
    "/** Rain band lower bounds in mm/h; 20 and 40 are the exceedance thresholds Sky publishes. */",
    `export const RAIN_THRESHOLDS_MM_H = deepFreeze(${json(rainThresholds(t))});`,
    "",
    "/** Thresholds offered by the probability-mode selector. */",
    `export const PROBABILITY_THRESHOLDS_CM = deepFreeze(${json(t.probability.thresholds_cm)});`,
    "",
    "/** Opacity floor in probability mode so a segment never vanishes. */",
    `export const MIN_PROBABILITY_OPACITY = ${t.probability.min_opacity};`,
    "",
    "/** Opacity of the p10 to p90 band on charts. */",
    `export const BAND_OPACITY = ${t.probability.band_opacity};`,
    "",
    "/** Opacity of the depth raster BitmapLayer. */",
    `export const DEPTH_RASTER_OPACITY = ${t.raster.depth_layer_opacity};`,
    "",
    `const DEPTH_BANDS = deepFreeze(${json(depthBands(t))});`,
    "",
    `const RAIN_BANDS = deepFreeze(${json(rainBands(t))});`,
    "",
    `const DRAIN_BANDS = deepFreeze(${json(drainBands(t))});`,
    "",
    HELPERS.trim(),
  ];
  return `${lines.join("\n")}\n`;
}

/* ------------------------------------------------------------------------------------------------
 * TypeScript declarations
 * ---------------------------------------------------------------------------------------------- */

const IDENTIFIER = /^[A-Za-z_$][A-Za-z0-9_$]*$/;

/** A readonly literal type for a JSON value, so consumers get exact keys and values. */
export function typeLiteral(value, indent = "") {
  if (value === null) return "null";
  if (Array.isArray(value)) {
    if (value.length === 0) return "readonly []";
    return `readonly [${value.map((v) => typeLiteral(v, indent)).join(", ")}]`;
  }
  switch (typeof value) {
    case "string":
      return JSON.stringify(value);
    case "number":
    case "boolean":
      return String(value);
    case "object": {
      const inner = `${indent}  `;
      const entries = Object.entries(value).map(([k, v]) => `${inner}readonly ${IDENTIFIER.test(k) ? k : JSON.stringify(k)}: ${typeLiteral(v, inner)};`);
      return `{\n${entries.join("\n")}\n${indent}}`;
    }
    default:
      throw new TypeError(`typeLiteral: unsupported value ${String(value)}`);
  }
}

export function generateDts(t, hash) {
  const colors = Object.fromEntries(solidColorEntries(t));
  const lines = [
    header(hash, "/*", "*/"),
    "",
    "/** A hex colour such as \"#2DD4BF\". */",
    "export type Hex = `#${string}`;",
    "/** [r, g, b], each 0-255. */",
    "export type Rgb = readonly [number, number, number];",
    "/** [r, g, b, a], each 0-255, as deck.gl accessors expect. */",
    "export type Rgba = readonly [number, number, number, number];",
    "",
    `export type DepthKey = ${DEPTH_ORDER.map((k) => JSON.stringify(k)).join(" | ")};`,
    `export type RainKey = ${RAIN_ORDER.map((k) => JSON.stringify(k)).join(" | ")};`,
    `export type DrainKey = ${DRAIN_ORDER.map((k) => JSON.stringify(k)).join(" | ")};`,
    `export type ReachMinutes = ${REACH_ORDER.map((k) => Number(k)).join(" | ")};`,
    `export type StatusMode = ${Object.keys(t.color.status).map((k) => JSON.stringify(k)).join(" | ")};`,
    `export type ObservationKind = ${Object.keys(t.color.obs).map((k) => JSON.stringify(k)).join(" | ")};`,
    `export type ColorName = ${Object.keys(colors).map((k) => JSON.stringify(k)).join(" | ")};`,
    `export type CssVarName = ${Object.keys(cssVarMap(t)).map((k) => JSON.stringify(k)).join(" | ")};`,
    `export type ThemeName = ${THEMES.map((k) => JSON.stringify(k)).join(" | ")};`,
    `export type ThemeColorName = ${Object.keys(themeColorMap(t)).map((k) => JSON.stringify(k)).join(" | ")};`,
    "/** Every colour one theme resolves, by name. */",
    "export type ThemeColorTable = { readonly [K in ThemeColorName]: Hex };",
    "",
    "export interface DepthBand {",
    "  readonly key: DepthKey;",
    "  readonly hex: Hex;",
    "  /** Human label, e.g. \"15-30 cm\". */",
    "  readonly label: string;",
    "  /** What the band means for traffic, e.g. \"cars impassable\". */",
    "  readonly meaning: string;",
    "  /** Inclusive lower bound in centimetres. */",
    "  readonly min_cm: number;",
    "  /** Exclusive upper bound in centimetres; null for the open-ended top band. */",
    "  readonly max_cm: number | null;",
    "}",
    "",
    "export interface RainBand {",
    "  readonly key: RainKey;",
    "  readonly hex: Hex;",
    "  /** Human label, e.g. \"20-40 mm/h\". */",
    "  readonly label: string;",
    "  /** What the rate means, e.g. \"heavy rain\". */",
    "  readonly meaning: string;",
    "  /** Inclusive lower bound in mm/h. */",
    "  readonly min_mm_h: number;",
    "  /** Exclusive upper bound in mm/h; null for the open-ended top band. */",
    "  readonly max_mm_h: number | null;",
    "}",
    "",
    "export interface DrainBand {",
    "  readonly key: DrainKey;",
    "  readonly hex: Hex;",
    "  /** Inclusive lower bound of posterior blockage beta. */",
    "  readonly min_beta: number;",
    "  /** Upper bound of beta (inclusive only for the top band). */",
    "  readonly max_beta: number;",
    "}",
    "",
    "/** The whole tokens.json, deep-frozen, with literal types. */",
    `export declare const tokens: ${typeLiteral(t)};`,
    "export default tokens;",
    "",
    "/** Every custom property dist/tokens.css defines on :root, by name. */",
    `export declare const cssVars: ${typeLiteral(cssVarMap(t))};`,
    "",
    "/** Solid colour tokens by name, e.g. colors.tide, colors[\"depth-3\"]. */",
    `export declare const colors: ${typeLiteral(colors)};`,
    "",
    "/** The themes, dark first. <html data-theme> carries the choice; dark is the default. */",
    `export declare const THEMES: ${typeLiteral(THEMES)};`,
    "/** The theme a first visit gets. */",
    `export declare const DEFAULT_THEME: ${JSON.stringify(t.theme.default)};`,
    "/** localStorage key the chosen theme is kept under. */",
    `export declare const THEME_STORAGE_KEY: ${JSON.stringify(t.theme.storage_key)};`,
    "/** The attribute on <html> that switches the theme. */",
    `export declare const THEME_ATTRIBUTE: ${JSON.stringify(t.theme.attribute)};`,
    "/** Every colour each theme resolves, by name: the solids plus the on-* text colours for fills. */",
    "export declare const themeColors: { readonly [K in ThemeName]: ThemeColorTable };",
    "",
    "/** Depth band lower bounds in centimetres: the fixed thresholds of the ramp. */",
    `export declare const DEPTH_THRESHOLDS_CM: ${typeLiteral(depthThresholds(t))};`,
    "/** Rain band lower bounds in mm/h; 20 and 40 are the exceedance thresholds Sky publishes. */",
    `export declare const RAIN_THRESHOLDS_MM_H: ${typeLiteral(rainThresholds(t))};`,
    "/** Thresholds offered by the probability-mode selector. */",
    `export declare const PROBABILITY_THRESHOLDS_CM: ${typeLiteral(t.probability.thresholds_cm)};`,
    "/** Opacity floor in probability mode so a segment never vanishes. */",
    `export declare const MIN_PROBABILITY_OPACITY: ${t.probability.min_opacity};`,
    "/** Opacity of the p10 to p90 band on charts. */",
    `export declare const BAND_OPACITY: ${t.probability.band_opacity};`,
    "/** Opacity of the depth raster BitmapLayer. */",
    `export declare const DEPTH_RASTER_OPACITY: ${t.raster.depth_layer_opacity};`,
    "",
    "/** Depth bands in ramp order (dry, 1 ... 5). Returns a fresh array of frozen bands. */",
    "export declare function depthRampStops(): DepthBand[];",
    "/** Rain-rate bands in ramp order (1 drizzle ... 6 cloudburst). */",
    "export declare function rainRampStops(): RainBand[];",
    "/** Drain bands in ramp order (0 clear ... 3 blocked). */",
    "export declare function drainRampStops(): DrainBand[];",
    "/** Band for a depth in cm; lower bounds inclusive (5 -> band 1); NaN, negatives and -Infinity -> dry; +Infinity -> the top band. */",
    "export declare function depthBand(cm: number): DepthBand;",
    "/** Hex colour for a depth in cm. */",
    "export declare function depthColor(cm: number): Hex;",
    "/** [r, g, b, a] for a depth in cm; alpha 0-255, default 255. */",
    "export declare function depthColorRgba(cm: number, alpha?: number): Rgba;",
    "/** Band for a rain rate in mm/h; lower bounds inclusive; null below 0.5 mm/h (no echo). */",
    "export declare function rainBand(mmH: number): RainBand | null;",
    "/** Hex colour for a rain rate in mm/h; null below the first band. */",
    "export declare function rainColor(mmH: number): Hex | null;",
    "/** [r, g, b, a] for a rain rate in mm/h; alpha 0-255, default 255; transparent below the first band. */",
    "export declare function rainColorRgba(mmH: number, alpha?: number): Rgba;",
    "/** Band for a posterior blockage beta in [0, 1]; 0.25 -> 1, 0.5 -> 2, 0.75 -> 3. */",
    "export declare function drainBand(beta: number): DrainBand;",
    "/** Hex colour for a posterior blockage beta. */",
    "export declare function drainColor(beta: number): Hex;",
    "/** [r, g, b, a] for a posterior blockage beta; alpha 0-255, default 255. */",
    "export declare function drainColorRgba(beta: number, alpha?: number): Rgba;",
    "/** P(depth > threshold) clamped to [MIN_PROBABILITY_OPACITY, 1]. */",
    "export declare function probabilityOpacity(p: number): number;",
    "/** Opacity in [0, 1] to a deck.gl alpha 0-255. */",
    "export declare function opacityToAlpha(opacity: number): number;",
    "/** [r, g, b] from #RGB, #RGBA, #RRGGBB or #RRGGBBAA; throws RangeError otherwise. */",
    "export declare function hexToRgb(hex: string): Rgb;",
    "/** [r, g, b, a] from a hex colour; alpha (0-255) overrides an embedded alpha channel. */",
    "export declare function hexToRgba(hex: string, alpha?: number): Rgba;",
    "/** [r, g, b, a] for a 5, 10 or 15 minute reachability ring at its token opacity. */",
    "export declare function reachColorRgba(minutes: ReachMinutes): Rgba;",
    "/** Chart series colour by 0-based index, cycling through chart-1 ... chart-5. */",
    "export declare function chartColor(index: number): Hex;",
    "/** Mode banner colour. */",
    "export declare function statusColor(mode: StatusMode): Hex;",
    "/** Observation type colour. */",
    "export declare function obsColor(kind: ObservationKind): Hex;",
    "/** True for \"dark\" or \"light\". */",
    "export declare function isTheme(value: unknown): value is ThemeName;",
    "/** The colour table of a theme; anything that is not a theme name reads as the default theme. */",
    "export declare function colorsFor(theme: ThemeName | (string & {}) | null | undefined): ThemeColorTable;",
    "/** \"var(--name)\" for a token name. */",
    "export declare function cssVar(name: CssVarName | (string & {})): string;",
  ];
  return `${lines.join("\n")}\n`;
}

/* ------------------------------------------------------------------------------------------------
 * Build, check, CLI
 * ---------------------------------------------------------------------------------------------- */

/** All three outputs as strings, keyed by file name. */
export function generate({ tokens, hash } = loadTokens()) {
  return {
    "tokens.css": generateCss(tokens, hash),
    "tokens.js": generateJs(tokens, hash),
    "tokens.d.ts": generateDts(tokens, hash),
  };
}

/** Writes dist/. Returns the file names written. */
export function build({ log = console.log } = {}) {
  const loaded = loadTokens();
  const outputs = generate(loaded);
  mkdirSync(DIST_DIR, { recursive: true });
  for (const [name, content] of Object.entries(outputs)) writeFileSync(path.join(DIST_DIR, name), content, "utf8");
  log(`tokens: wrote ${OUTPUT_FILES.map((f) => `dist/${f}`).join(", ")} (tokens.json ${loaded.hash.slice(0, 12)})`);
  return OUTPUT_FILES.slice();
}

/**
 * Compares dist/ with what tokens.json would generate now. Stale means: a file is missing, its
 * header hash differs from the current tokens.json hash, or its content differs from a fresh build
 * (which also catches changes to this script). Returns { ok, stale: [{ file, reason }] }.
 */
export function check() {
  const loaded = loadTokens();
  const outputs = generate(loaded);
  const stale = [];
  for (const [name, expected] of Object.entries(outputs)) {
    const file = path.join(DIST_DIR, name);
    if (!existsSync(file)) {
      stale.push({ file: name, reason: "missing" });
      continue;
    }
    const actual = normaliseNewlines(readFileSync(file, "utf8"));
    const headerHash = /tokens\.json sha256: ([0-9a-f]{64})/.exec(actual)?.[1];
    if (headerHash !== loaded.hash) stale.push({ file: name, reason: `built from tokens.json ${headerHash ? headerHash.slice(0, 12) : "unknown"}, current is ${loaded.hash.slice(0, 12)}` });
    else if (actual !== expected) stale.push({ file: name, reason: "content differs from a fresh build (build.mjs changed?)" });
  }
  return { ok: stale.length === 0, stale, hash: loaded.hash };
}

function main(argv) {
  const wantsCheck = argv.includes("--check");
  try {
    if (wantsCheck) {
      const result = check();
      if (result.ok) {
        console.log(`tokens: dist/ is up to date with tokens.json (${result.hash.slice(0, 12)})`);
        return 0;
      }
      console.error("tokens: dist/ is stale. Run `pnpm --filter @varuna/tokens build`.");
      for (const s of result.stale) console.error(`  dist/${s.file}: ${s.reason}`);
      return 1;
    }
    build();
    return 0;
  } catch (error) {
    console.error(`tokens: ${error instanceof Error ? error.message : String(error)}`);
    return 2;
  }
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  process.exitCode = main(process.argv.slice(2));
}

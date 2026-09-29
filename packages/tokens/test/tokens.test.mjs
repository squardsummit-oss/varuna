/**
 * Tests for the VARUNA design-token build (packages/tokens).
 *
 * Run: pnpm --filter @varuna/tokens test   (node --test)
 *
 * What is covered:
 *   - dist/ is byte-identical to a fresh build (so `node build.mjs --check` is green)
 *   - every hex in tokens.json appears in dist/tokens.css as the custom property SPEC.md 6.2 names
 *   - the Tailwind @theme block, type scale and @utility classes exist
 *   - colour helpers return the right band at every boundary
 *   - the rain-rate ramp is contiguous, open-ended at the top, and shares no hex with depth or drains
 *   - validateTokens rejects a broken ramp, so a bad tokens.json cannot reach dist/
 *   - `node build.mjs --check` exits non-zero when dist/ is stale
 */

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { before, describe, it } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  DIST_DIR,
  OUTPUT_FILES,
  TOKENS_PATH,
  build,
  check,
  generate,
  generateCss,
  loadTokens,
  validateTokens,
} from "../build.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BUILD_SCRIPT = path.join(HERE, "..", "build.mjs");
const CSS_PATH = path.join(DIST_DIR, "tokens.css");
const JS_PATH = path.join(DIST_DIR, "tokens.js");
const DTS_PATH = path.join(DIST_DIR, "tokens.d.ts");

const raw = JSON.parse(readFileSync(TOKENS_PATH, "utf8"));

/** `{ "--name": "value" }` from the first `:root { ... }` block of a CSS string. */
function parseRootVars(css) {
  const start = css.indexOf(":root {");
  assert.notEqual(start, -1, "tokens.css has a :root block");
  const end = css.indexOf("\n}", start);
  const block = css.slice(start, end);
  const vars = {};
  for (const m of block.matchAll(/^\s*(--[\w-]+):\s*([^;]+);/gm)) vars[m[1]] = m[2].trim();
  return vars;
}

/** `{ "--name": "value" }` from every `@theme ... { ... }` block (plain and inline). */
function parseThemeVars(css) {
  const vars = {};
  for (const block of css.matchAll(/@theme[^{]*\{([\s\S]*?)\n\}/g)) {
    for (const m of block[1].matchAll(/^\s*(--[\w-]+):\s*([^;]+);/gm)) vars[m[1]] = m[2].trim();
  }
  return vars;
}

/** Every [customPropertyName, hex] pair SPEC.md 6.2 promises, derived from tokens.json. */
function expectedSolidColors() {
  const out = [];
  for (const [k, v] of Object.entries(raw.color.base)) out.push([`--${k}`, v.value]);
  for (const [k, v] of Object.entries(raw.color.depth)) out.push([`--depth-${k}`, v.value]);
  for (const [k, v] of Object.entries(raw.color.rain)) out.push([`--rain-${k}`, v.value]);
  for (const [k, v] of Object.entries(raw.color.drain)) out.push([`--drain-${k}`, v.value]);
  for (const [k, v] of Object.entries(raw.color.semantic)) out.push([`--${k}`, v.value]);
  for (const [k, v] of Object.entries(raw.color.obs)) out.push([`--obs-${k}`, v.value]);
  for (const [k, v] of Object.entries(raw.color.status)) out.push([`--status-${k}`, v.value]);
  for (const [k, v] of Object.entries(raw.color.chart)) out.push([`--chart-${k}`, v.value]);
  return out;
}

let css;
let tokensModule;

before(async () => {
  if (!OUTPUT_FILES.every((f) => existsSync(path.join(DIST_DIR, f)))) build({ log: () => {} });
  css = readFileSync(CSS_PATH, "utf8").replace(/\r\n/g, "\n");
  tokensModule = await import(pathToFileURL(JS_PATH).href);
});

describe("build outputs", () => {
  it("dist/ matches a fresh build so --check is green", () => {
    const result = check();
    assert.deepEqual(result.stale, []);
    assert.equal(result.ok, true);
  });

  it("is deterministic: two generations are byte-identical", () => {
    const a = generate(loadTokens());
    const b = generate(loadTokens());
    for (const f of OUTPUT_FILES) assert.equal(a[f], b[f], `${f} differs between two builds`);
  });

  it("every output carries the tokens.json hash in its header", () => {
    const { hash } = loadTokens();
    for (const f of OUTPUT_FILES) {
      const head = readFileSync(path.join(DIST_DIR, f), "utf8").slice(0, 400);
      assert.ok(head.includes(`tokens.json sha256: ${hash}`), `${f} header names the current tokens.json hash`);
    }
  });
});

describe("tokens.css custom properties", () => {
  it("defines every hex from tokens.json under the SPEC.md 6.2 name", () => {
    const vars = parseRootVars(css);
    const expected = expectedSolidColors();
    // 10 base + 6 depth + 6 rain + 4 drain + 4 semantic + 5 obs + 4 status + 5 chart. A new colour must update this.
    assert.equal(expected.length, 44, "tokens.json has the full SPEC.md 6.2 colour set");
    for (const [name, hex] of expected) assert.equal(vars[name], hex, `${name} is ${hex}`);
  });

  it("defines the reachability rings as rgb() with the token opacity", () => {
    const vars = parseRootVars(css);
    for (const [k, v] of Object.entries(raw.color.reach)) {
      const n = parseInt(v.value.slice(1), 16);
      const rgb = `rgb(${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255} / ${v.opacity})`;
      assert.equal(vars[`--reach-${k}`], rgb, `--reach-${k}`);
    }
  });

  it("defines radius, spacing, layout, motion and focus tokens", () => {
    const vars = parseRootVars(css);
    const expected = {
      "--radius-panel": "12px",
      "--radius-control": "8px",
      "--radius-chip": "999px",
      "--radius-popover": "10px",
      "--radius-phone": "40px",
      "--space-grid": "4px",
      "--row": "40px",
      "--row-dense": "32px",
      "--panel-padding": "16px",
      "--section-gap": "24px",
      "--top-bar": "52px",
      "--time-bar": "96px",
      "--icon-rail": "56px",
      "--right-rail": "360px",
      "--ease-ui": raw.motion.easing,
      "--dur-micro": `${raw.motion.duration_ms.micro}ms`,
      "--dur-panel": `${raw.motion.duration_ms.panel}ms`,
      "--dur-flight": `${raw.motion.duration_ms.flight}ms`,
      "--focus-ring-color": raw.focus.ring_color,
      "--focus-ring-width": `${raw.focus.ring_px}px`,
      "--glass-bg": "rgb(10 16 32 / 0.72)",
      "--glass-blur": "12px",
    };
    for (const [name, value] of Object.entries(expected)) assert.equal(vars[name], value, name);
    assert.ok(vars["--focus-ring"].includes("var(--focus-ring-color)"), "--focus-ring composes the colour and width");
  });

  it("defines the six rain bands, contiguous in mm/h and distinct from depth and drain", () => {
    const vars = parseRootVars(css);
    const bands = Object.entries(raw.color.rain);
    assert.equal(bands.length, 6, "the rain ramp has six bands");
    let previous = bands[0][1].min_mm_h;
    for (const [k, band] of bands) {
      assert.equal(vars[`--rain-${k}`], band.value, `--rain-${k}`);
      assert.equal(band.min_mm_h, previous, `rain band ${k} starts where the previous one ends`);
      previous = band.max_mm_h;
    }
    assert.equal(previous, null, "the top rain band is open-ended");
    assert.deepEqual(
      bands.map(([, b]) => b.min_mm_h),
      [0.5, 2, 8, 20, 40, 80],
      "the ramp carries the 20 and 40 mm/h exceedance thresholds Sky publishes",
    );
    // SPEC.md 6.2: the depth ramp means water depth and nothing else, so a radar echo must never
    // wear a depth or a blockage colour.
    const taken = new Set([...Object.values(raw.color.depth), ...Object.values(raw.color.drain)].map((b) => b.value));
    for (const [k, band] of bands) assert.ok(!taken.has(band.value), `--rain-${k} is not a depth or drain colour`);
  });

  it("does not leak the depth ramp or --tide into unrelated names", () => {
    const vars = parseRootVars(css);
    assert.equal(vars["--danger"], raw.color.semantic.danger.value);
    assert.notEqual(vars["--danger"], vars["--depth-4"], "danger is never a depth colour");
  });
});

describe("tokens.css Tailwind theme", () => {
  it("maps every colour to a --color-* theme entry", () => {
    const theme = parseThemeVars(css);
    for (const [name, hex] of expectedSolidColors()) assert.equal(theme[`--color-${name.slice(2)}`], hex, `--color-${name.slice(2)}`);
    for (const k of Object.keys(raw.color.reach)) assert.match(theme[`--color-reach-${k}`], /^rgb\(45 212 191 \/ 0\.\d+\)$/);
  });

  it("exposes the rain ramp as bg-rain-1 ... bg-rain-6", () => {
    const theme = parseThemeVars(css);
    for (const [k, band] of Object.entries(raw.color.rain)) assert.equal(theme[`--color-rain-${k}`], band.value, `--color-rain-${k}`);
  });

  it("exposes radius, fonts and the type scale in Tailwind v4 form", () => {
    const theme = parseThemeVars(css);
    for (const [k, v] of Object.entries(raw.radius)) assert.equal(theme[`--radius-${k}`], `${v}px`);
    assert.match(theme["--font-display"], /^var\(--font-bricolage\)/);
    assert.match(theme["--font-sans"], /^var\(--font-geist-sans\)/);
    assert.match(theme["--font-mono"], /^var\(--font-geist-mono\)/);
    for (const [k, v] of Object.entries(raw.type_scale)) {
      assert.equal(theme[`--text-${k}`], `${v.size}px`, `--text-${k}`);
      assert.equal(Number(theme[`--text-${k}--line-height`]), v.line_height, `--text-${k}--line-height`);
    }
    assert.equal(theme["--text-micro"], "12px");
    assert.equal(theme["--text-hero"], "72px");
    assert.equal(theme["--tracking-display"], "-0.02em");
  });

  it("emits the .num utility and the type presets", () => {
    assert.match(css, /@utility num \{\s*font-variant-numeric: tabular-nums;\s*\}/);
    for (const k of Object.keys(raw.type_scale)) assert.ok(css.includes(`@utility type-${k} {`), `type-${k}`);
    const hero = css.slice(css.indexOf("@utility type-hero {"), css.indexOf("}", css.indexOf("@utility type-hero {")));
    assert.match(hero, /font-family: var\(--font-bricolage\)/);
    assert.match(hero, /font-size: 72px/);
    assert.match(hero, /line-height: 1;/);
    assert.match(hero, /font-weight: 600/);
    assert.match(hero, /letter-spacing: -0\.02em/);
    const body = css.slice(css.indexOf("@utility type-body {"), css.indexOf("}", css.indexOf("@utility type-body {")));
    assert.match(body, /font-family: var\(--font-geist-sans\)/);
    assert.match(body, /font-size: 15px/);
    assert.match(body, /line-height: 1\.5/);
  });

  it("contains no hex outside the token set", () => {
    const light = raw.theme.light;
    const allowed = new Set([
      ...expectedSolidColors().map(([, hex]) => hex.toUpperCase()),
      raw.focus.ring_color.toUpperCase(),
      raw.glass.background.toUpperCase(),
      raw.color.reach["5"].value.toUpperCase(),
      ...Object.values(light.color).flatMap((group) => Object.values(group).map((e) => e.value.toUpperCase())),
      ...Object.values(raw.theme.on_fill).flatMap((group) => Object.values(group).map((e) => e.value.toUpperCase())),
      light.focus.ring_color.toUpperCase(),
    ]);
    for (const m of css.matchAll(/#[0-9A-Fa-f]{6}\b/g)) assert.ok(allowed.has(m[0].toUpperCase()), `${m[0]} is a token colour`);
  });
});

describe("tokens.js helpers", () => {
  it("exports the whole tokens object, frozen, plus the threshold constants", () => {
    const { tokens, DEPTH_THRESHOLDS_CM, PROBABILITY_THRESHOLDS_CM, MIN_PROBABILITY_OPACITY, colors } = tokensModule;
    assert.deepEqual(tokens, raw);
    assert.ok(Object.isFrozen(tokens.color.depth["3"]));
    assert.deepEqual([...DEPTH_THRESHOLDS_CM], [5, 15, 30, 45, 60]);
    assert.deepEqual([...PROBABILITY_THRESHOLDS_CM], [15, 30, 45, 60]);
    assert.equal(MIN_PROBABILITY_OPACITY, 0.15);
    assert.equal(colors.tide, "#2DD4BF");
    assert.equal(colors["depth-3"], "#F97316");
    assert.equal(colors["obs-traffic"], "#FB7185");
  });

  it("depthBand picks the right band at every boundary", () => {
    const { depthBand } = tokensModule;
    const cases = [
      [-1, "dry"],
      [0, "dry"],
      [4.9, "dry"],
      [5, "1"],
      [14.99, "1"],
      [15, "2"],
      [30, "3"],
      [44.5, "3"],
      [45, "4"],
      [60, "5"],
      [200, "5"],
      [Number.NaN, "dry"],
      [Number.NEGATIVE_INFINITY, "dry"],
      [Number.POSITIVE_INFINITY, "5"],
      [undefined, "dry"],
    ];
    for (const [cm, key] of cases) assert.equal(depthBand(cm).key, key, `${cm} cm -> band ${key}`);
    assert.deepEqual(depthBand(20), { key: "2", hex: "#F59E0B", label: "15-30 cm", meaning: "two-wheelers impassable", min_cm: 15, max_cm: 30 });
    assert.equal(depthBand(200).max_cm, null);
  });

  it("depthColor and depthColorRgba follow the ramp", () => {
    const { depthColor, depthColorRgba } = tokensModule;
    assert.equal(depthColor(4.9), "#2B3A55");
    assert.equal(depthColor(5), "#3B82F6");
    assert.equal(depthColor(15), "#F59E0B");
    assert.equal(depthColor(30), "#F97316");
    assert.equal(depthColor(45), "#EF4444");
    assert.equal(depthColor(60), "#B91C1C");
    assert.equal(depthColor(200), "#B91C1C");
    assert.deepEqual(depthColorRgba(50), [239, 68, 68, 255]);
    assert.deepEqual(depthColorRgba(50, 128), [239, 68, 68, 128]);
    assert.deepEqual(depthColorRgba(50, 999), [239, 68, 68, 255], "alpha is clamped to 255");
  });

  it("depthRampStops returns the six bands in order, thresholds contiguous", () => {
    const stops = tokensModule.depthRampStops();
    assert.deepEqual(
      stops.map((s) => s.key),
      ["dry", "1", "2", "3", "4", "5"],
    );
    for (let i = 1; i < stops.length; i += 1) assert.equal(stops[i].min_cm, stops[i - 1].max_cm);
    stops.push("mutating the copy must not touch the module");
    assert.equal(tokensModule.depthRampStops().length, 6);
  });

  it("rainBand picks the right band at every boundary and is empty below the first", () => {
    const { rainBand, rainColor, rainColorRgba, RAIN_THRESHOLDS_MM_H } = tokensModule;
    assert.deepEqual([...RAIN_THRESHOLDS_MM_H], [0.5, 2, 8, 20, 40, 80]);
    const cases = [
      [0, null],
      [0.49, null],
      [0.5, "1"],
      [1.99, "1"],
      [2, "2"],
      [8, "3"],
      [19.9, "3"],
      [20, "4"],
      [40, "5"],
      [80, "6"],
      [300, "6"],
      [Number.NaN, null],
      [Number.POSITIVE_INFINITY, "6"],
      [undefined, null],
    ];
    for (const [mmH, key] of cases) assert.equal(rainBand(mmH)?.key ?? null, key, `${mmH} mm/h -> band ${key}`);
    assert.equal(rainBand(80).max_mm_h, null, "the cloudburst band is open-ended");
    assert.equal(rainColor(25), raw.color.rain["4"].value);
    assert.equal(rainColor(0.1), null, "no echo below 0.5 mm/h");
    assert.deepEqual(rainColorRgba(45, 200), [...tokensModule.hexToRgb(raw.color.rain["5"].value), 200]);
    assert.deepEqual(rainColorRgba(0.1), [0, 0, 0, 0], "no echo renders transparent");
  });

  it("rainRampStops returns the six bands in order, thresholds contiguous", () => {
    const stops = tokensModule.rainRampStops();
    assert.deepEqual(
      stops.map((s) => s.key),
      ["1", "2", "3", "4", "5", "6"],
    );
    for (let i = 1; i < stops.length; i += 1) assert.equal(stops[i].min_mm_h, stops[i - 1].max_mm_h);
    stops.push("mutating the copy must not touch the module");
    assert.equal(tokensModule.rainRampStops().length, 6);
  });

  it("drainColor picks the magenta ramp by beta with inclusive lower bounds", () => {
    const { drainColor, drainBand } = tokensModule;
    assert.equal(drainColor(0), "#3E4C6E");
    assert.equal(drainColor(0.249), "#3E4C6E");
    assert.equal(drainColor(0.25), "#7C3AED");
    assert.equal(drainColor(0.5), "#C026D3");
    assert.equal(drainColor(0.75), "#E879F9");
    assert.equal(drainColor(1), "#E879F9");
    assert.equal(drainColor(1.5), "#E879F9", "beta above 1 clamps to the top band");
    assert.equal(drainColor(-0.2), "#3E4C6E", "negative beta clamps to the clear band");
    assert.equal(drainBand(0.6).key, "2");
    assert.deepEqual(tokensModule.drainColorRgba(0.9, 200), [232, 121, 249, 200]);
  });

  it("probabilityOpacity never drops below the floor", () => {
    const { probabilityOpacity } = tokensModule;
    assert.equal(probabilityOpacity(0), 0.15);
    assert.equal(probabilityOpacity(0.1), 0.15);
    assert.equal(probabilityOpacity(0.15), 0.15);
    assert.equal(probabilityOpacity(0.82), 0.82);
    assert.equal(probabilityOpacity(1), 1);
    assert.equal(probabilityOpacity(3), 1);
    assert.equal(probabilityOpacity(Number.NaN), 0.15);
  });

  it("hexToRgb and hexToRgba parse every hex form and reject the rest", () => {
    const { hexToRgb, hexToRgba, opacityToAlpha } = tokensModule;
    assert.deepEqual(hexToRgb("#2DD4BF"), [45, 212, 191]);
    assert.deepEqual(hexToRgb("2dd4bf"), [45, 212, 191]);
    assert.deepEqual(hexToRgb("#fff"), [255, 255, 255]);
    assert.deepEqual(hexToRgba("#2DD4BF80"), [45, 212, 191, 128]);
    assert.deepEqual(hexToRgba("#2DD4BF80", 255), [45, 212, 191, 255], "explicit alpha wins");
    assert.throws(() => hexToRgb("#12345"), RangeError);
    assert.throws(() => hexToRgb("tide"), RangeError);
    assert.equal(opacityToAlpha(0.45), 115);
    assert.equal(opacityToAlpha(2), 255);
  });

  it("reach, chart, status and obs helpers read the token tables", () => {
    const { reachColorRgba, chartColor, statusColor, obsColor, cssVar } = tokensModule;
    assert.deepEqual(reachColorRgba(5), [45, 212, 191, 115]);
    assert.deepEqual(reachColorRgba(15), [45, 212, 191, 36]);
    assert.throws(() => reachColorRgba(20), RangeError);
    assert.equal(chartColor(0), "#2DD4BF");
    assert.equal(chartColor(4), "#A3E635");
    assert.equal(chartColor(5), "#2DD4BF", "cycles");
    assert.equal(chartColor(-1), "#A3E635", "negative indices cycle backwards");
    assert.equal(statusColor("replay"), "#38BDF8");
    assert.throws(() => statusColor("paused"), RangeError);
    assert.equal(obsColor("report"), "#FDE68A");
    assert.equal(cssVar("tide"), "var(--tide)");
    assert.equal(cssVar("--depth-2"), "var(--depth-2)");
  });
});

describe("tokens.d.ts", () => {
  it("declares the helpers and the literal tokens type", () => {
    const dts = readFileSync(DTS_PATH, "utf8");
    for (const sig of [
      "export declare function depthColor(cm: number): Hex;",
      "export declare function depthBand(cm: number): DepthBand;",
      "export declare function drainColor(beta: number): Hex;",
      "export declare function rainColor(mmH: number): Hex | null;",
      "export declare function rainBand(mmH: number): RainBand | null;",
      "export declare function rainRampStops(): RainBand[];",
      "export declare const RAIN_THRESHOLDS_MM_H: readonly [0.5, 2, 8, 20, 40, 80];",
      "export declare function depthRampStops(): DepthBand[];",
      "export declare function probabilityOpacity(p: number): number;",
      "export declare function hexToRgb(hex: string): Rgb;",
      "export declare function depthColorRgba(cm: number, alpha?: number): Rgba;",
      "export declare const DEPTH_THRESHOLDS_CM: readonly [5, 15, 30, 45, 60];",
    ]) {
      assert.ok(dts.includes(sig), `d.ts contains ${JSON.stringify(sig)}`);
    }
    assert.match(dts, /readonly tide: \{\s+readonly value: "#2DD4BF";/, "tokens literal type carries the exact hex values");
  });
});

describe("validateTokens", () => {
  /** A deep copy of tokens.json so a test may break one field without touching the file. */
  const clone = () => JSON.parse(JSON.stringify(raw));

  it("accepts tokens.json as committed", () => {
    assert.doesNotThrow(() => validateTokens(clone()));
  });

  it("rejects a rain band that leaves a gap in the ramp", () => {
    const broken = clone();
    broken.color.rain["4"].min_mm_h = 25;
    assert.throws(() => validateTokens(broken), /color\.rain\.4\.min_mm_h/);
  });

  it("rejects a rain ramp whose top band is closed", () => {
    const broken = clone();
    broken.color.rain["6"].max_mm_h = 120;
    assert.throws(() => validateTokens(broken), /last rain band must be open-ended/);
  });

  it("rejects a rain colour that is not an upper-case hex", () => {
    const broken = clone();
    broken.color.rain["2"].value = "rgb(47 58 158)";
    assert.throws(() => validateTokens(broken), /color\.rain\.2\.value/);
  });
});

describe("tokens.json shape the Python ramps read", () => {
  it("gives every rain band the fields varuna_schemas.tokens reads straight from the file", () => {
    // Python does not consume dist/; packages/schemas/varuna_schemas/tokens.py parses tokens.json,
    // so the ramp reaches services/sky only if every band carries these fields (SPEC.md 6.7).
    for (const [k, band] of Object.entries(raw.color.rain)) {
      assert.match(band.value, /^#[0-9A-F]{6}$/, `color.rain.${k}.value`);
      assert.equal(typeof band.min_mm_h, "number", `color.rain.${k}.min_mm_h`);
      assert.ok(band.max_mm_h === null || typeof band.max_mm_h === "number", `color.rain.${k}.max_mm_h`);
      assert.match(band.label, /mm\/h$/, `color.rain.${k}.label carries the unit`);
      assert.equal(typeof band.meaning, "string", `color.rain.${k}.meaning`);
    }
  });
});

/* ------------------------------------------------------------------------------------------------
 * Light theme
 * ---------------------------------------------------------------------------------------------- */

/** sha256 of the dark :root block as it stood before the light theme existed (commit 46007d8). */
const DARK_ROOT_SHA256 = "15bf1ce03f0e7f96231e9da1ebefc48b1515634636bde2c3cc20f825d7c976dd";

/** The `{ "--name": value }` of a `selector { ... }` block. */
function parseBlock(cssText, selector) {
  const start = cssText.indexOf(`${selector} {`);
  assert.notEqual(start, -1, `tokens.css has a ${selector} block`);
  const block = cssText.slice(start, cssText.indexOf("\n}", start));
  const vars = {};
  for (const m of block.matchAll(/^\s*(--[\w-]+):\s*([^;]+);/gm)) vars[m[1]] = m[2].trim();
  return vars;
}

/** The first `:root {` block, verbatim: the dark theme. */
function darkRootBlock(cssText) {
  const start = cssText.indexOf(":root {");
  return cssText.slice(start, cssText.indexOf("\n}", start) + 2);
}

/** The `@theme {` block, verbatim. */
function themeBlock(cssText) {
  const start = cssText.indexOf("@theme {");
  return cssText.slice(start, cssText.indexOf("\n}", start) + 2);
}

/** WCAG 2.x contrast ratio of two #RRGGBB colours. */
function contrast(a, b) {
  const lum = (hex) => {
    const n = parseInt(hex.slice(1), 16);
    const [r, g, bl] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
      const c = v / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("light theme", () => {
  const LIGHT = ':root[data-theme="light"]';

  it("leaves the dark :root block byte for byte as it was", () => {
    // Changing a dark token on purpose changes this hash; the light theme never may.
    const hash = createHash("sha256").update(darkRootBlock(css)).digest("hex");
    assert.equal(hash, DARK_ROOT_SHA256);
  });

  it("cannot leak into the dark :root or the Tailwind theme", () => {
    const poisoned = JSON.parse(JSON.stringify(raw));
    for (const group of Object.values(poisoned.theme.light.color)) for (const entry of Object.values(group)) entry.value = "#000000";
    poisoned.theme.light.focus.ring_color = "#000000";
    const out = generateCss(poisoned, "x");
    assert.equal(darkRootBlock(out), darkRootBlock(css));
    assert.equal(themeBlock(out), themeBlock(css));
  });

  it("overrides every surface and text colour, each as a CSS variable and a Tailwind colour", () => {
    const vars = parseBlock(css, LIGHT);
    for (const [k, v] of Object.entries(raw.theme.light.color.base)) {
      assert.equal(vars[`--${k}`], v.value, `--${k}`);
      assert.equal(vars[`--color-${k}`], v.value, `--color-${k}`);
    }
    assert.equal(vars["--focus-ring-color"], raw.theme.light.focus.ring_color);
    assert.equal(vars["--glass-bg"], "rgb(245 247 251 / 0.82)");
  });

  it("never moves the water: depth 1-5 and the rain ramp keep their hex", () => {
    const vars = parseBlock(css, LIGHT);
    for (const k of ["1", "2", "3", "4", "5"]) assert.equal(vars[`--depth-${k}`], undefined, `--depth-${k} is fixed`);
    for (const k of Object.keys(raw.color.rain)) assert.equal(vars[`--rain-${k}`], undefined, `--rain-${k} is fixed`);
    assert.equal(tokensModule.colorsFor("light")["depth-3"], raw.color.depth["3"].value);
  });

  it("keeps text readable: 4.5:1 on ink, deep and well in both themes", () => {
    for (const theme of ["dark", "light"]) {
      const c = tokensModule.colorsFor(theme);
      for (const fg of ["text", "text-2", "text-3", "tide"]) {
        for (const bg of ["ink", "deep", "well"]) {
          const ratio = contrast(c[fg], c[bg]);
          assert.ok(ratio >= 4.5, `${theme}: --${fg} on --${bg} is ${ratio.toFixed(2)}:1`);
        }
      }
      assert.ok(contrast(c.danger, c.deep) >= 4.5, `${theme}: --danger on --deep`);
      assert.ok(contrast(c["on-tide"], c.tide) >= 4.5, `${theme}: --on-tide on --tide`);
      // Count badges on an observation-type fill (Nadi's timeline): axe found `text-ink` there,
      // which is near-white paper in light.
      for (const kind of ["traffic", "report", "sensor", "cctv", "sar"]) {
        const ratio = contrast(c["on-obs"], c[`obs-${kind}`]);
        assert.ok(ratio >= 4.5, `${theme}: --on-obs on --obs-${kind} is ${ratio.toFixed(2)}:1`);
      }
    }
  });

  it("keeps --tide readable as text on its own 15-20 % tint, the selected-chip pattern", () => {
    // `border-tide bg-tide/15 text-tide` (cycle picker, alert filters) is the light theme's first
    // axe failure: teal-700 read 4.16:1 on its own tint over paper.
    const tint = (fg, bg, alpha) => {
      const [f, b] = [fg, bg].map((hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)));
      return `#${f.map((v, i) => Math.round(alpha * v + (1 - alpha) * b[i]).toString(16).padStart(2, "0")).join("")}`;
    };
    for (const theme of ["dark", "light"]) {
      const c = tokensModule.colorsFor(theme);
      for (const bg of ["ink", "deep", "well"]) {
        for (const alpha of [0.15, 0.2]) {
          const ratio = contrast(c.tide, tint(c.tide, c[bg], alpha));
          assert.ok(ratio >= 4.5, `${theme}: --tide on --tide/${alpha * 100} over --${bg} is ${ratio.toFixed(2)}:1`);
        }
      }
    }
  });

  it("chooses the text colour per depth fill for contrast", () => {
    for (const theme of ["dark", "light"]) {
      const c = tokensModule.colorsFor(theme);
      for (const k of ["dry", "1", "2", "3", "4", "5"]) {
        const ratio = contrast(c[`on-depth-${k}`], c[`depth-${k}`]);
        assert.ok(ratio >= 4.5, `${theme}: --on-depth-${k} on --depth-${k} is ${ratio.toFixed(2)}:1`);
      }
    }
  });

  it("keeps status, chart and observation marks at 3:1 on a light panel", () => {
    const c = tokensModule.colorsFor("light");
    for (const name of Object.keys(c).filter((n) => /^(status|chart|obs)-/.test(n))) {
      const ratio = contrast(c[name], c.deep);
      assert.ok(ratio >= 3, `light --${name} on --deep is ${ratio.toFixed(2)}:1`);
    }
  });

  it("exports the theme tables and helpers", () => {
    const { THEMES, DEFAULT_THEME, THEME_STORAGE_KEY, THEME_ATTRIBUTE, themeColors, colorsFor, isTheme, colors } = tokensModule;
    assert.deepEqual([...THEMES], ["dark", "light"]);
    assert.equal(DEFAULT_THEME, "dark");
    assert.equal(THEME_STORAGE_KEY, "varuna-theme");
    assert.equal(THEME_ATTRIBUTE, "data-theme");
    for (const [name, hex] of Object.entries(colors)) assert.equal(themeColors.dark[name], hex, `dark ${name} is the :root value`);
    assert.equal(colorsFor("light").ink, "#F5F7FB");
    assert.equal(colorsFor("sepia").ink, colors.ink, "an unknown theme reads as dark");
    assert.equal(isTheme("light"), true);
    assert.equal(isTheme("Light"), false);
    assert.ok(Object.isFrozen(themeColors.light));
  });

  it("rejects a light theme that moves a depth band, drops a base colour or adds a group", () => {
    const clone = () => JSON.parse(JSON.stringify(raw));
    const moved = clone();
    moved.theme.light.color.depth["3"] = { value: "#123456" };
    assert.throws(() => validateTokens(moved), /theme\.light\.color\.depth\.3/);
    const missing = clone();
    delete missing.theme.light.color.base["text-3"];
    assert.throws(() => validateTokens(missing), /theme\.light\.color\.base\.text-3 is missing/);
    const rain = clone();
    rain.theme.light.color.rain = { 1: { value: "#FFFFFF" } };
    assert.throws(() => validateTokens(rain), /theme\.light\.color\.rain cannot change/);
  });
});

describe("build.mjs --check", () => {
  const run = (...args) => {
    try {
      const stdout = execFileSync(process.execPath, [BUILD_SCRIPT, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
      return { status: 0, stdout, stderr: "" };
    } catch (error) {
      return { status: error.status, stdout: String(error.stdout ?? ""), stderr: String(error.stderr ?? "") };
    }
  };

  it("exits 0 when dist/ is fresh", () => {
    const result = run("--check");
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /up to date/);
  });

  it("exits non-zero when dist/ is stale, naming the file", () => {
    const original = readFileSync(CSS_PATH, "utf8");
    try {
      writeFileSync(CSS_PATH, `${original}\n/* stale edit */\n`, "utf8");
      const result = run("--check");
      assert.notEqual(result.status, 0, "stale dist must fail the check");
      assert.match(result.stderr, /dist\/tokens\.css/);
    } finally {
      writeFileSync(CSS_PATH, original, "utf8");
    }
    assert.equal(check().ok, true, "dist/ restored");
  });
});

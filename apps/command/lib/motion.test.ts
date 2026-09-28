/**
 * Pins `lib/motion.ts` to SPEC.md section 8, the motion catalogue. Rule 9 says no motion ships
 * without a row; this test makes the row and the code move together. It fails when a row is added
 * or removed on either side, when a row's trigger or reduced-motion fallback drifts, when a
 * duration the row states is missing from `DUR_MS` (or `DUR_MS` claims one the row does not
 * state), and when the easing, spring or flight curve in the preamble stops matching tokens.json.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { tokens } from "@varuna/tokens";
import { describe, expect, it } from "vitest";

import {
  cubicBezier,
  DUR_MS,
  EASE_UI,
  EASE_UI_CSS,
  easeUi,
  FLY_TO_CURVE,
  M,
  MOTION_IDS,
  SPRING_PARAMS,
  springSettleMs,
  springValue,
  type MotionId,
} from "./motion";

/**
 * apps/command/lib -> repo root. Resolved from this file rather than the working directory, which
 * is apps/command under `pnpm test` but the repo root under turbo or an editor runner. Under jsdom
 * `import.meta.url` is not a file URL, so `__dirname` (which vitest provides) is the stable anchor.
 */
const SPEC_PATH = path.resolve(__dirname, "../../../SPEC.md");

interface CatalogueRow {
  id: string;
  where: string;
  motion: string;
  implementation: string;
  trigger: string;
  reduced: string;
}

function readSection8(): { preamble: string; rows: CatalogueRow[] } {
  const spec = readFileSync(SPEC_PATH, "utf8");
  const start = spec.indexOf("## 8. Motion catalogue");
  if (start < 0)
    throw new Error(`SPEC.md at ${SPEC_PATH} has no "## 8. Motion catalogue" heading`);
  const end = spec.indexOf("\n---", start);
  const section = spec.slice(start, end < 0 ? undefined : end);
  const lines = section.split(/\r?\n/);
  const preamble = lines.find((line) => line.startsWith("Global easing:")) ?? "";
  const rows = lines
    .filter((line) => /^\|\s*M\d+\s*\|/.test(line))
    .map((line) => {
      const cells = line
        .split("|")
        .slice(1, -1)
        .map((cell) => cell.trim());
      if (cells.length !== 6) throw new Error(`section 8 row has ${cells.length} cells: ${line}`);
      const [id, where, motion, implementation, trigger, reduced] = cells as [
        string,
        string,
        string,
        string,
        string,
        string,
      ];
      return { id, where, motion, implementation, trigger, reduced };
    });
  return { preamble, rows };
}

/**
 * The table and the code say the same thing in slightly different typography: the code avoids
 * slashes, parentheses and backticks because /design renders these strings as copy (section 6.8).
 * These are the only rewrites allowed; anything else is drift.
 */
function normalise(cell: string): string {
  return cell
    .replace(/`/g, "")
    .replace(/\s*\/\s*/g, " or ")
    .replace(/\s*\(([^)]*)\)/g, ", $1")
    .replace(/^—$/, "none")
    .replace(/\s+/g, " ")
    .trim();
}

/** Every "N ms" or "N s" in a cell, in milliseconds. "4 fps", "25 frames" and "+120 min" are not durations. */
function statedDurationsMs(text: string): number[] {
  return [...text.matchAll(/(\d+(?:\.\d+)?)\s*(ms|s)\b/g)].map(([, value, unit]) =>
    Math.round(Number(value) * (unit === "s" ? 1000 : 1)),
  );
}

/** Unitless integers in the Implementation column, where the catalogue writes deck.gl props such as `getColor: 120`. */
function bareIntegers(text: string): number[] {
  return [...text.matchAll(/(?<![\d.])(\d+)(?![\d.]|\s*(?:ms|s)\b)/g)].map(([, value]) =>
    Number(value),
  );
}

const { preamble, rows } = readSection8();

describe("SPEC.md section 8 parity", () => {
  it("finds the catalogue", () => {
    expect(preamble).not.toBe("");
    expect(rows.length).toBeGreaterThanOrEqual(26);
  });

  it("has the same rows, in the same order, as lib/motion.ts", () => {
    expect(MOTION_IDS).toEqual(rows.map((row) => row.id));
    for (const id of MOTION_IDS) expect(M[id].id).toBe(id);
  });

  it.each(rows.map((row) => [row.id, row] as const))("%s trigger matches the table", (id, row) => {
    const spec = M[id as MotionId];
    expect(spec, `lib/motion.ts has no entry for ${id}`).toBeDefined();
    expect(normalise(spec.trigger)).toBe(normalise(row.trigger));
  });

  it.each(rows.map((row) => [row.id, row] as const))(
    "%s reduced-motion fallback matches the table",
    (id, row) => {
      expect(normalise(M[id as MotionId].reduced)).toBe(normalise(row.reduced));
    },
  );

  it.each(rows.map((row) => [row.id, row] as const))(
    "%s durations match the table in both directions",
    (id, row) => {
      const spec = M[id as MotionId];
      const stated = statedDurationsMs(`${row.motion} ${row.implementation}`);
      const bare = bareIntegers(row.implementation);
      const unclaimed = [...stated];
      for (const key of spec.durations) {
        const ms = DUR_MS[key];
        expect(ms, `DUR_MS.${key} is missing`).toBeTypeOf("number");
        const at = unclaimed.indexOf(ms);
        if (at >= 0) unclaimed.splice(at, 1);
        else
          expect(
            bare,
            `${id} claims DUR_MS.${key} = ${ms} ms, which the row does not state`,
          ).toContain(ms);
      }
      expect(unclaimed, `${id} states durations lib/motion.ts does not carry`).toEqual([]);
    },
  );

  it("keeps the motion text's own numbers in step with its durations", () => {
    for (const id of MOTION_IDS) {
      const spec = M[id];
      const values = spec.durations.map((key) => DUR_MS[key]);
      for (const ms of statedDurationsMs(spec.motion))
        expect(values, `${id}: "${spec.motion}"`).toContain(ms);
    }
  });

  it("carries no DUR_MS entry that neither a row nor the preamble states", () => {
    // The preamble's tokens are checked against its prose in the next describe block; every other
    // entry must be named by some row, so a timing cannot enter the code without entering the table.
    const preambleKeys = new Set<string>([
      "micro",
      "microMin",
      "microMax",
      "panel",
      "flight",
      "drawOnMax",
    ]);
    const claimed = new Set<string>(MOTION_IDS.flatMap((id) => M[id].durations));
    const orphans = Object.keys(DUR_MS).filter(
      (key) => !preambleKeys.has(key) && !claimed.has(key),
    );
    expect(orphans, "DUR_MS entries no section 8 row states").toEqual([]);
  });

  it("carries only draw-on durations within the preamble's ceiling", () => {
    expect(DUR_MS.routeDrawOn).toBeLessThanOrEqual(DUR_MS.drawOnMax);
  });
});

describe("section 8 preamble against tokens.json", () => {
  it("names the catalogue easing", () => {
    const easing = preamble.match(/`(cubic-bezier\([^)]*\))`/)?.[1];
    expect(easing).toBe(tokens.motion.easing);
    expect(EASE_UI_CSS).toBe(tokens.motion.easing);
    expect(EASE_UI_CSS).toBe(`cubic-bezier(${EASE_UI.join(", ")})`);
  });

  it("names the spring", () => {
    const match = preamble.match(/stiffness (\d+), damping (\d+)/);
    expect(match).not.toBeNull();
    expect(SPRING_PARAMS.stiffness).toBe(Number(match?.[1]));
    expect(SPRING_PARAMS.damping).toBe(Number(match?.[2]));
  });

  it("names the flyTo curve", () => {
    expect(FLY_TO_CURVE).toBe(Number(preamble.match(/curve (\d+(?:\.\d+)?)/)?.[1]));
  });

  it("names the duration tokens", () => {
    const micro = preamble.match(/(\d+)–(\d+) ms micro/);
    expect(DUR_MS.microMin).toBe(Number(micro?.[1]));
    expect(DUR_MS.microMax).toBe(Number(micro?.[2]));
    expect(DUR_MS.micro).toBeGreaterThanOrEqual(DUR_MS.microMin);
    expect(DUR_MS.micro).toBeLessThanOrEqual(DUR_MS.microMax);
    expect(DUR_MS.panel).toBe(Number(preamble.match(/(\d+) ms panel/)?.[1]));
    expect(DUR_MS.flight).toBe(Number(preamble.match(/(\d+) ms map flight/)?.[1]));
    expect(DUR_MS.drawOnMax).toBe(
      Number(preamble.match(/≤ (\d+(?:\.\d+)?) s draw-on/)?.[1]) * 1000,
    );
  });
});

/** Reference solver with nothing clever in it: bisection on x(s) = t to machine precision. */
function referenceBezier(x1: number, y1: number, x2: number, y2: number, t: number): number {
  const axis = (s: number, p1: number, p2: number) =>
    3 * (1 - s) * (1 - s) * s * p1 + 3 * (1 - s) * s * s * p2 + s * s * s;
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 100; i += 1) {
    const mid = (lo + hi) / 2;
    if (axis(mid, x1, x2) < t) lo = mid;
    else hi = mid;
  }
  return axis((lo + hi) / 2, y1, y2);
}

describe("easeUi", () => {
  it("starts at 0 and ends at 1", () => {
    expect(easeUi(0)).toBe(0);
    expect(easeUi(1)).toBe(1);
    expect(easeUi(-0.5)).toBe(0);
    expect(easeUi(1.5)).toBe(1);
  });

  it("is monotone over 1,000 samples", () => {
    let previous = 0;
    for (let i = 1; i <= 1000; i += 1) {
      const value = easeUi(i / 1000);
      expect(value).toBeGreaterThanOrEqual(previous);
      previous = value;
    }
  });

  it("agrees with a bisection reference of the CSS curve", () => {
    for (const t of [0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99]) {
      expect(easeUi(t)).toBeCloseTo(referenceBezier(0.2, 0.8, 0.2, 1, t), 5);
    }
    // Fast out of the origin: most of the travel is done by half time.
    expect(easeUi(0.5)).toBeGreaterThan(0.85);
  });

  it("solves curves where Newton's method stalls", () => {
    const linear = cubicBezier(0, 0, 1, 1);
    const steep = cubicBezier(1, 0, 0, 1);
    for (const t of [0.01, 0.3, 0.5, 0.7, 0.99]) {
      expect(linear(t)).toBeCloseTo(t, 5);
      expect(steep(t)).toBeCloseTo(referenceBezier(1, 0, 0, 1, t), 5);
      expect(steep(t) + steep(1 - t)).toBeCloseTo(1, 5);
    }
  });
});

describe("springValue", () => {
  it("starts at rest at 0", () => {
    expect(springValue(0)).toBe(0);
    expect(springValue(-10)).toBe(0);
  });

  it("matches a numerical integration of the same spring", () => {
    const { stiffness, damping, mass } = SPRING_PARAMS;
    const dt = 0.00001;
    let x = 0;
    let v = 0;
    let t = 0;
    for (const checkMs of [20, 50, 100, 150, 200, 300, 500]) {
      while (t < checkMs / 1000 - dt / 2) {
        v += ((-stiffness * (x - 1) - damping * v) / mass) * dt;
        x += v * dt;
        t += dt;
      }
      expect(springValue(checkMs)).toBeCloseTo(x, 3);
    }
  });

  it("overshoots once, by under 3 %", () => {
    let peak = 0;
    for (let ms = 0; ms <= 1000; ms += 1) peak = Math.max(peak, springValue(ms));
    expect(peak).toBeGreaterThan(1);
    expect(peak).toBeLessThan(1.03);
  });

  it("stays within 2 % of 1 after springSettleMs, which is under 600 ms", () => {
    const settle = springSettleMs();
    expect(settle).toBeLessThan(600);
    for (let ms = settle; ms <= 2000; ms += 1)
      expect(Math.abs(springValue(ms) - 1)).toBeLessThanOrEqual(0.02);
  });
});

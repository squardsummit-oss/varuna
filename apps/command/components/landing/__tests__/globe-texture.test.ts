/**
 * The photographic Earth's maths and its refusals.
 *
 * Two things are worth testing here and one is not.
 *
 * Worth testing: the **inversion**, because it is the whole trick - a screen pixel goes back
 * through a projection that d3 cannot invert (the mutated raw has no `.invert`, so there is no
 * reference implementation to compare against except d3's own *forward* map, which is exactly what
 * these round-trip cases use). And the **refusals**, because every one of them is a path a real
 * browser will take: no WebGL2, a texture that never decodes, a token colour that cannot be read.
 * jsdom has no WebGL at all, so the degraded path is the only one these tests can execute, which
 * makes it the one that must be proved not to throw.
 *
 * Not worth pretending to test: the GLSL. It is a transcription of `invertMorphedProjection` and
 * nothing in this suite compiles it. What is checked is only that the shader declares every
 * uniform the painter sets - a rename on one side and not the other is otherwise silent, because
 * `getUniformLocation` returns null for an unknown name and `uniform3f(null, ...)` is a no-op that
 * would leave the planet black - and that no colour literal has crept into it (SPEC.md 6.2).
 */
import { describe, expect, it, vi } from "vitest";

import {
  morphProjection,
  photoOf,
  vectorOpacity,
  VIEW_H,
  VIEW_W,
} from "@/components/landing/globe-paint";
import {
  createEarthPainter,
  cssColorToRgb,
  EARTH_FRAGMENT_SOURCE,
  EARTH_TEXTURE_URL,
  ensureEarthImage,
  invertMorphedProjection,
  MUMBAI,
  photoAmount,
  resetEarthImageForTests,
  subsolarPoint,
  SUN_INSTANT,
  unitVector,
} from "@/components/landing/globe-texture";

const DEG = Math.PI / 180;

/** A `GlobeFrame` with only the fields a given case cares about. */
function frame(fields: Partial<Parameters<typeof photoAmount>[0]>) {
  return {
    alpha: 0,
    scale: 190,
    centre: [0, 0] as [number, number],
    highlight: 0,
    aoi: 0,
    finished: false,
    ...fields,
  };
}

/** Angle between two points on the sphere, in degrees. */
function separation(a: { lon: number; lat: number }, b: { lon: number; lat: number }): number {
  const u = unitVector(a.lon, a.lat);
  const v = unitVector(b.lon, b.lat);
  return Math.acos(Math.min(1, Math.max(-1, u[0] * v[0] + u[1] * v[1] + u[2] * v[2]))) / DEG;
}

describe("the inverse of the morphed projection", () => {
  /**
   * The proof: project a point forward with the very projection `globe-intro.tsx` builds, then
   * push the result back through the inverse and see whether the point comes home.
   *
   * Targets are kept within 70° of the frame centre because the morph is only single-valued out
   * to Λ(t, φ) - at t = 0 that is the near hemisphere, and a far-side point projects onto its own
   * near-side twin, which the inverse correctly returns and the test would wrongly call an error.
   */
  it("returns the point d3's forward projection started from, at every stage of the morph", () => {
    const centres: [number, number][] = [
      [0, 0],
      [72.86, 19.06],
      [-28, 0],
      [79, 22],
      [-140, -35],
    ];
    const offsets: [number, number][] = [
      [0, 0],
      [12, 7],
      [-31, 24],
      [45, -18],
      [-58, -9],
      [8, 60],
      [-5, -62],
    ];
    let checked = 0;
    let worst = 0;

    for (const alpha of [0, 0.08, 0.35, 0.62, 0.9, 1]) {
      for (const centre of centres) {
        const scale = 190 + alpha * 900;
        const projection = morphProjection(alpha)
          .scale(scale)
          .translate([VIEW_W / 2, VIEW_H / 2])
          .rotate([-centre[0], -centre[1], 0]);

        for (const [dLon, dLat] of offsets) {
          const target = {
            lon: centre[0] + dLon,
            lat: Math.max(-88, Math.min(88, centre[1] + dLat)),
          };
          if (separation(target, { lon: centre[0], lat: centre[1] }) > 70) continue;

          const projected = projection([target.lon, target.lat]);
          expect(projected).not.toBeNull();
          const [x, y] = projected as [number, number];
          const back = invertMorphedProjection(
            alpha,
            centre,
            (x - VIEW_W / 2) / scale,
            (VIEW_H / 2 - y) / scale,
          );
          expect(back).not.toBeNull();
          const error = separation(back as { lon: number; lat: number }, target);
          worst = Math.max(worst, error);
          checked += 1;
        }
      }
    }

    // A 4096-pixel-wide texture has 0.088° per pixel, so anything under a hundredth of a degree is
    // far below what a sampled pixel could show.
    expect(checked).toBeGreaterThan(80);
    expect(worst).toBeLessThan(0.01);
  });

  it("refuses the far side of a globe and accepts the whole of a flat map", () => {
    // At t = 0 the visible planet is the unit disc in raw units.
    expect(invertMorphedProjection(0, [0, 0], 0.4, 0.3)).not.toBeNull();
    expect(invertMorphedProjection(0, [0, 0], 1.2, 0)).toBeNull();
    expect(invertMorphedProjection(0, [0, 0], 0, 1.2)).toBeNull();
    // At t = 1 it is the whole [-pi, pi] x [-pi/2, pi/2] rectangle.
    expect(invertMorphedProjection(1, [0, 0], 3.0, 1.5)).not.toBeNull();
    expect(invertMorphedProjection(1, [0, 0], 3.3, 0)).toBeNull();
    expect(invertMorphedProjection(1, [0, 0], 0, 1.7)).toBeNull();
  });

  it("puts the frame centre at the origin, whichever way the camera is pointing", () => {
    for (const centre of [
      [0, 0],
      [72.86, 19.06],
      [-140, -35],
    ] as [number, number][]) {
      for (const alpha of [0, 0.5, 1]) {
        const back = invertMorphedProjection(alpha, centre, 0, 0);
        expect(back).not.toBeNull();
        expect(
          separation(back as { lon: number; lat: number }, { lon: centre[0], lat: centre[1] }),
        ).toBeLessThan(0.01);
      }
    }
  });

  /**
   * The limb is where a wrong branch would show: a pixel just inside the edge that comes back with
   * the *far* side's coordinates paints a smear of the wrong continent around the rim. So the edge
   * is found by bisection in every direction, and the last accepted point is pushed back through
   * d3's forward projection to see whether it really is the pixel it came from.
   */
  it("stays on the right branch all the way out to the limb", () => {
    const centre: [number, number] = [40, -12];
    const scale = 240;
    for (const alpha of [0, 0.25, 0.7]) {
      const projection = morphProjection(alpha)
        .scale(scale)
        .translate([VIEW_W / 2, VIEW_H / 2])
        .rotate([-centre[0], -centre[1], 0]);

      let edgesChecked = 0;
      for (let i = 0; i < 24; i += 1) {
        const theta = (i / 24) * 2 * Math.PI;
        const at = (r: number) =>
          invertMorphedProjection(alpha, centre, r * Math.cos(theta), r * Math.sin(theta));
        // Bisect for the largest radius still on the planet.
        let inside = 0;
        let outside = 4;
        for (let step = 0; step < 40; step += 1) {
          const mid = 0.5 * (inside + outside);
          if (at(mid)) inside = mid;
          else outside = mid;
        }
        expect(inside).toBeGreaterThan(0.2);
        const r = inside * 0.999;
        const point = at(r);
        expect(point).not.toBeNull();
        const { lon, lat } = point as { lon: number; lat: number };
        const forward = projection([lon, lat]);
        expect(forward).not.toBeNull();
        const [x, y] = forward as [number, number];
        expect((x - VIEW_W / 2) / scale).toBeCloseTo(r * Math.cos(theta), 4);
        expect((VIEW_H / 2 - y) / scale).toBeCloseTo(r * Math.sin(theta), 4);
        edgesChecked += 1;

        // At t = 0 the planet is exactly the unit disc, whichever way the camera points.
        if (alpha === 0) expect(inside).toBeCloseTo(1, 6);
      }
      expect(edgesChecked).toBe(24);
    }
  });
});

describe("the sun the planet is lit by", () => {
  it("is the subsolar point of the replay's own morning, not a chosen direction", () => {
    const sun = subsolarPoint(SUN_INSTANT);
    // 2 July is ten days past the solstice, so the sun is still close to the tropic of Cancer.
    expect(sun.lat).toBeGreaterThan(22.5);
    expect(sun.lat).toBeLessThan(23.5);
    // 03:10 UTC puts apparent noon about nine hours of longitude east of Greenwich.
    expect(sun.lon).toBeGreaterThan(125);
    expect(sun.lon).toBeLessThan(140);
  });

  it("leaves Mumbai in morning light and the approach's opening frame in night", () => {
    const sun = subsolarPoint(SUN_INSTANT);
    // The subject of both sequences has to be lit, or there is nothing to look at.
    expect(separation(sun, MUMBAI)).toBeLessThan(80);
    // M27 opens at 28° west, mid-Atlantic, which at that instant is the far side of the terminator
    // - so the approach turns out of the dark into the sunrise over India.
    expect(separation(sun, { lon: -28, lat: 0 })).toBeGreaterThan(95);
  });

  it("tracks the seasons and the clock rather than returning a constant", () => {
    const january = subsolarPoint(new Date(Date.UTC(2019, 0, 2, 3, 10, 0)));
    expect(january.lat).toBeLessThan(-22);
    const sixHoursLater = subsolarPoint(new Date(Date.UTC(2019, 6, 2, 9, 10, 0)));
    // Six hours of rotation is ninety degrees of longitude, westward.
    expect(subsolarPoint(SUN_INSTANT).lon - sixHoursLater.lon).toBeCloseTo(90, 0);
  });
});

describe("reading the token colours", () => {
  it("parses the hex the tokens are written in, in both lengths", () => {
    expect(cssColorToRgb("#000000")).toEqual([0, 0, 0]);
    expect(cssColorToRgb("#ffffff")).toEqual([1, 1, 1]);
    const tide = cssColorToRgb("#2DD4BF");
    expect(tide).not.toBeNull();
    expect((tide as number[])[1]).toBeGreaterThan((tide as number[])[0]);
    expect(cssColorToRgb("#fff")).toEqual([1, 1, 1]);
  });

  it("answers null rather than guessing when the property is not set", () => {
    // What `getPropertyValue("--ink")` returns when no stylesheet has been applied, which is every
    // test run and every server render.
    expect(cssColorToRgb("")).toBeNull();
    expect(cssColorToRgb("   ")).toBeNull();
  });
});

describe("how much of the picture the photograph carries", () => {
  it("is nothing at all until the texture has decoded", () => {
    expect(photoAmount(frame({ alpha: 0.5 }), false)).toBe(0);
    expect(photoAmount(frame({ alpha: 0.5 }), true)).toBe(1);
  });

  it("hands the frame back to the vectors before the arrival act outruns the texture", () => {
    // 4096 pixels of longitude over 360° is 0.088° a pixel; the arrival act ends on a frame about
    // 1.6° wide, which is eighteen pixels. The photograph must be gone well before that.
    expect(photoAmount(frame({ aoi: 0 }), true)).toBe(1);
    expect(photoAmount(frame({ aoi: 0.25 }), true)).toBeCloseTo(0.5, 6);
    expect(photoAmount(frame({ aoi: 0.5 }), true)).toBe(0);
    expect(photoAmount(frame({ aoi: 1 }), true)).toBe(0);
  });

  it("fades the fills out and keeps the strokes, so the outlines stay instrumentation", () => {
    const off = vectorOpacity(0);
    expect(off.sphereFill).toBe(1);
    expect(off.landFill).toBe(1);
    const on = vectorOpacity(1);
    expect(on.sphereFill).toBe(0);
    expect(on.landFill).toBe(0);
    // Every vector goes to nothing: the photograph already has coastlines, and a traced second
    // set over them is what made the hero read as a diagram (2026-09-23).
    expect(on.landStroke).toBe(0);
    expect(on.sphereStroke).toBe(0);
    // Half way through the fade they are still half drawn, so the hand-over is a fade and not a
    // cut - a cut would be a motion, and section 8 lists none for it.
    const half = vectorOpacity(0.5);
    expect(half.landStroke).toBeCloseTo(0.5, 10);
    expect(half.sphereStroke).toBeCloseTo(0.5, 10);
  });

  it("treats a frame with no `photo` at all as the picture that shipped before", () => {
    expect(photoOf(frame({}))).toBe(0);
    expect(photoOf({ ...frame({}), photo: 0.4 })).toBe(0.4);
    expect(photoOf({ ...frame({}), photo: 5 })).toBe(1);
  });
});

describe("the shader and the painter agree about their uniforms", () => {
  it("declares every uniform the painter sets", () => {
    for (const name of [
      "uEarth",
      "uRawMap",
      "uMorph",
      "uCamera",
      "uSun",
      "uInk",
      "uDeep",
      "uTide",
      "uPhoto",
    ]) {
      expect(EARTH_FRAGMENT_SOURCE).toContain(`uniform `);
      expect(new RegExp(`uniform\\s+\\w+\\s+${name}\\s*;`).test(EARTH_FRAGMENT_SOURCE)).toBe(true);
    }
  });

  it("writes no colour of its own", () => {
    // SPEC.md 6.2: the three colours arrive as uniforms read from the stylesheet. `#version` is
    // the only `#` in the file and its letters are not hex digits.
    expect(/#[0-9a-fA-F]{3,8}\b/.test(EARTH_FRAGMENT_SOURCE)).toBe(false);
    expect(EARTH_FRAGMENT_SOURCE).toContain("#version 300 es");
  });
});

describe("what happens where there is no WebGL", () => {
  const palette = {
    ink: [0, 0, 0] as [number, number, number],
    deep: [0.1, 0.1, 0.2] as [number, number, number],
    tide: [0.2, 0.8, 0.7] as [number, number, number],
  };

  it("returns no painter instead of throwing when the context is null", () => {
    const canvas = document.createElement("canvas");
    const getContext = vi
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue(null as never);
    try {
      const image = document.createElement("canvas");
      expect(createEarthPainter(canvas, image, palette, 800, 600, 1)).toBeNull();
    } finally {
      getContext.mockRestore();
    }
  });

  it("returns no painter instead of throwing when the context request itself throws", () => {
    const canvas = document.createElement("canvas");
    const getContext = vi
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockImplementation(() => {
        throw new Error("no GPU process");
      });
    try {
      const image = document.createElement("canvas");
      expect(createEarthPainter(canvas, image, palette, 800, 600, 1)).toBeNull();
    } finally {
      getContext.mockRestore();
    }
  });
});

describe("loading the committed texture", () => {
  it("asks for the file that is in the repository, and resolves null when it will not load", async () => {
    resetEarthImageForTests();
    const asked: string[] = [];
    // jsdom does not fetch images, so the element never fires either handler on its own; the
    // descriptor below lets the test decide, which is the only way to reach the failure branch.
    const descriptor = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "src");
    Object.defineProperty(HTMLImageElement.prototype, "src", {
      configurable: true,
      set(this: HTMLImageElement, value: string) {
        asked.push(value);
        queueMicrotask(() => this.onerror?.(new Event("error")));
      },
      get() {
        return asked[asked.length - 1] ?? "";
      },
    });
    try {
      const image = await ensureEarthImage();
      expect(asked).toEqual([EARTH_TEXTURE_URL]);
      expect(image).toBeNull();
      // The decode is cached, so a second caller does not ask again.
      expect(await ensureEarthImage()).toBeNull();
      expect(asked).toHaveLength(1);
    } finally {
      if (descriptor) Object.defineProperty(HTMLImageElement.prototype, "src", descriptor);
      resetEarthImageForTests();
    }
  });
});

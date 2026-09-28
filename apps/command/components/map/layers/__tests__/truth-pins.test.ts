/**
 * Motion M18 on the map: the pin's spring, the ripple's eased expansion and fade, the layers the
 * builder hands deck, and the extension's draw. jsdom has no WebGL, so the GPU half is checked on
 * the shader source the extension injects and on the uniforms its draw sets.
 */

import { describe, expect, it, vi } from "vitest";

import { DROP_MS } from "@/lib/hooks/use-truth-pins";
import { DUR_MS, easeUi, springSettleMs, springValue } from "@/lib/motion";
import {
  DROP_MAIN_END,
  DROP_SHADER,
  RIPPLE_ALPHA,
  RIPPLE_RADIUS_M,
  TRUTH_MAX_PX,
  TRUTH_MIN_PX,
  TRUTH_RADIUS_M,
  TruthDropExtension,
  pinDropFrame,
  rippleFrame,
  truthPinLayers,
} from "../truth-pins";
import type { TruthPin } from "../types";
import { serializeLayers } from "./serialize";

const pin = (id: string, dropStartMs?: number): TruthPin => ({
  id,
  lon: 72.858,
  lat: 19.032,
  name: id,
  dropStartMs,
});

const ids = (layers: unknown[]) => layers.map((layer) => (layer as { id: string }).id);

describe("the pin's spring (scale 0 to 1)", () => {
  it("is section 8's 600 ms ripple, which outlasts the spring", () => {
    expect(DUR_MS.pinRipple).toBe(600);
    expect(springSettleMs()).toBeLessThan(600);
    expect(DROP_MS).toBe(600);
  });

  it("starts from nothing, and is nothing before it starts", () => {
    expect(pinDropFrame(0).scale).toBe(0);
    expect(pinDropFrame(-50).scale).toBe(0);
    expect(pinDropFrame(Number.NEGATIVE_INFINITY).scale).toBe(0);
    expect(pinDropFrame(Number.NaN).scale).toBe(0);
  });

  it("rises, overshoots its full size by at most 3 % once, and settles at exactly 1", () => {
    let peak = 0;
    let peakAt = 0;
    for (let ms = 1; ms < DROP_MS; ms += 1) {
      const { scale } = pinDropFrame(ms);
      if (scale > peak) {
        peak = scale;
        peakAt = ms;
      }
    }
    // A spring, not a clamp: it goes past full size. The old linear grow never did.
    expect(peak).toBeGreaterThan(1.001);
    expect(peak * TRUTH_RADIUS_M).toBeLessThanOrEqual(1.03 * TRUTH_RADIUS_M);
    expect(peakAt).toBeGreaterThan(50);
    expect(peakAt).toBeLessThan(400);
    // It is the catalogue spring itself, rising all the way to its peak.
    let rising = 0;
    for (let ms = 1; ms < DROP_MS; ms += 1) {
      expect(pinDropFrame(ms).scale).toBe(springValue(ms));
      if (ms <= peakAt) {
        expect(pinDropFrame(ms).scale).toBeGreaterThan(rising);
        rising = pinDropFrame(ms).scale;
      }
    }
    expect(Math.abs(pinDropFrame(springSettleMs()).scale - 1)).toBeLessThanOrEqual(0.02);
    expect(pinDropFrame(DROP_MS).scale).toBe(1);
    expect(pinDropFrame(10_000)).toEqual({ scale: 1, alpha: 1 });
  });

  it("never fades the pin", () => {
    for (const ms of [0, 100, 300, 599, 600]) expect(pinDropFrame(ms).alpha).toBe(1);
  });
});

describe("the ripple (600 ms, catalogue easing)", () => {
  const radius = (ms: number) => TRUTH_RADIUS_M * rippleFrame(ms).scale;

  it("starts at the pin's radius at full strength and ends at 320 m, faded out", () => {
    expect(radius(0)).toBeCloseTo(TRUTH_RADIUS_M, 9);
    expect(rippleFrame(0).alpha).toBe(1);
    expect(radius(600)).toBeCloseTo(RIPPLE_RADIUS_M, 9);
    expect(rippleFrame(600).alpha).toBe(0);
    expect(radius(5_000)).toBeCloseTo(RIPPLE_RADIUS_M, 9);
    expect(rippleFrame(5_000).alpha).toBe(0);
  });

  it("follows cubic-bezier(0.2, 0.8, 0.2, 1), not a straight line", () => {
    for (const ms of [60, 150, 300, 450]) {
      const eased = easeUi(ms / 600);
      expect(radius(ms)).toBeCloseTo(70 + 250 * eased, 6);
      expect(rippleFrame(ms).alpha).toBeCloseTo(1 - eased, 6);
    }
    // Ease-out: three quarters of the way out by a quarter of the time. Linear would be 132.5 m.
    expect(radius(150)).toBeGreaterThan(250);
    // And the stroke it starts from is RIPPLE_ALPHA, so 220 * alpha is what the eye sees.
    expect(Math.round(RIPPLE_ALPHA * rippleFrame(300).alpha)).toBe(
      Math.round(220 * (1 - easeUi(0.5))),
    );
  });

  it("grows and fades monotonically", () => {
    let previous = rippleFrame(0);
    for (let ms = 10; ms <= 600; ms += 10) {
      const frame = rippleFrame(ms);
      expect(frame.scale).toBeGreaterThanOrEqual(previous.scale);
      expect(frame.alpha).toBeLessThanOrEqual(previous.alpha);
      previous = frame;
    }
  });

  it("is invisible before it starts", () => {
    expect(rippleFrame(-1).alpha).toBe(0);
    expect(rippleFrame(Number.NEGATIVE_INFINITY).alpha).toBe(0);
  });
});

describe("the layers", () => {
  it("draws a ripple under and a dropping pin over the landed pins, one pair per start time", () => {
    const layers = truthPinLayers({
      truthPins: [pin("P-1", 500), pin("P-2"), pin("P-3", 500), pin("P-4", 900)],
    });
    expect(ids(layers)).toEqual([
      "truth-ripple-P-1",
      "truth-ripple-P-4",
      "truth-pins",
      "truth-drop-P-1",
      "truth-drop-P-4",
    ]);
    const drawn = serializeLayers(layers) as Record<string, unknown>[];
    const byId = new Map(drawn.map((layer) => [layer.id, layer]));
    expect(byId.get("truth-drop-P-1")).toMatchObject({
      "async:data": { length: 2 },
      dropStartMs: 500,
      dropPart: "pin",
      radiusMinPixels: 4,
      extensions: [{ class: "TruthDropExtension" }],
    });
    expect(byId.get("truth-ripple-P-1")).toMatchObject({
      dropStartMs: 500,
      dropPart: "ripple",
      filled: false,
      getLineColor: [45, 212, 191, RIPPLE_ALPHA],
    });
    expect(byId.get("truth-pins")).toMatchObject({ "async:data": { length: 1 } });
    // The landed layer has no drop extension: it never reads the clock.
    expect(byId.get("truth-pins")?.extensions).toBeUndefined();
  });

  it("keeps a group's layer ids from pending to started, so deck keeps the layer", () => {
    const pending = ids(truthPinLayers({ truthPins: [pin("P-9", Number.POSITIVE_INFINITY)] }));
    const started = ids(truthPinLayers({ truthPins: [pin("P-9", 1234.5)] }));
    expect(started).toEqual(pending);
  });

  it("under reduced motion every pin has landed and no ripple is built", () => {
    const layers = truthPinLayers({
      truthPins: [pin("P-1", 500), pin("P-2"), pin("P-3", Number.POSITIVE_INFINITY)],
      reducedMotion: true,
    });
    expect(ids(layers)).toEqual(["truth-pins"]);
    const [only] = serializeLayers(layers) as Record<string, unknown>[];
    expect(only).toMatchObject({ "async:data": { length: 3 }, getRadius: 70 });
    expect(only?.extensions).toBeUndefined();
  });

  it("draws nothing without pins", () => {
    expect(truthPinLayers({ truthPins: [] })).toEqual([]);
  });

  // Chunk INTEGRATE, defect 3: at z19 over Hindmata - 0.28 m per pixel - an uncapped 70 m radius
  // put a 248 px opaque white disc over the junction, the imagery and the streets the pin is
  // there to corroborate. A pin is a marker, not an area, so it stops growing at a marker's size.
  it("caps every pin and ripple in pixels, so a pin never covers what it marks", () => {
    const layers = truthPinLayers({ truthPins: [pin("P-1", 500), pin("P-2")] });
    const drawn = serializeLayers(layers) as Record<string, unknown>[];
    expect(drawn).toHaveLength(3);
    for (const layer of drawn) {
      expect(layer).toMatchObject({
        radiusUnits: "meters",
        radiusMinPixels: TRUTH_MIN_PX,
        radiusMaxPixels: TRUTH_MAX_PX,
      });
    }
    // A ceiling that is not a marker's size would let the disc come back by another route.
    expect(TRUTH_MAX_PX).toBeLessThanOrEqual(16);
    expect(TRUTH_MAX_PX).toBeGreaterThan(TRUTH_MIN_PX);
  });
});

describe("the drop extension", () => {
  function draw(props: Record<string, unknown>, nowMs: number) {
    const now = vi.spyOn(performance, "now").mockReturnValue(nowMs);
    const setShaderModuleProps = vi.fn();
    const setNeedsRedraw = vi.fn();
    new TruthDropExtension().draw.call({
      props,
      setShaderModuleProps,
      setNeedsRedraw,
    } as never);
    now.mockRestore();
    return {
      uniforms: setShaderModuleProps.mock.lastCall?.[0],
      redraws: setNeedsRedraw.mock.calls.length,
    };
  }

  it("sets the pin's spring and the ripple's ease from the clock, and asks deck for the next frame", () => {
    const pinDraw = draw({ dropStartMs: 1000, dropPart: "pin" }, 1150);
    expect(pinDraw.uniforms).toEqual({ truthDrop: pinDropFrame(150) });
    expect(pinDraw.redraws).toBe(1);
    const rippleDraw = draw({ dropStartMs: 1000, dropPart: "ripple" }, 1300);
    expect(rippleDraw.uniforms).toEqual({ truthDrop: rippleFrame(300) });
    expect(rippleDraw.redraws).toBe(1);
  });

  it("stops asking once the drop is over, and does not ask for a pin that has not started", () => {
    expect(draw({ dropStartMs: 1000, dropPart: "ripple" }, 1600)).toEqual({
      uniforms: { truthDrop: { scale: RIPPLE_RADIUS_M / TRUTH_RADIUS_M, alpha: 0 } },
      redraws: 0,
    });
    expect(draw({ dropStartMs: 1000, dropPart: "pin" }, 5000)).toEqual({
      uniforms: { truthDrop: { scale: 1, alpha: 1 } },
      redraws: 0,
    });
    expect(draw({ dropStartMs: Number.POSITIVE_INFINITY, dropPart: "pin" }, 5000)).toEqual({
      uniforms: { truthDrop: { scale: 0, alpha: 1 } },
      redraws: 0,
    });
  });

  it("scales the marker and its measured radius together, keeping the stroke its own width", () => {
    expect(DROP_SHADER.inject["vs:DECKGL_FILTER_SIZE"]).toBe("size *= truthDrop.scale;");
    expect(DROP_SHADER.inject["fs:DECKGL_FILTER_COLOR"]).toBe("color.a *= truthDrop.alpha;");
    expect(DROP_MAIN_END).toContain("outerRadiusPixels *= truthDrop.scale;");
    // Mirror of the inner-radius line: a 2 px stroke on a 10 px marker stays 2 px at 3x.
    const outer = 10;
    const inner = 1 - 2 / outer;
    const scale = 3;
    const scaledInner = 1 - (1 - inner) / Math.max(scale, 0.0001);
    expect(outer * scale * (1 - scaledInner)).toBeCloseTo(2, 9);
    expect(DROP_MAIN_END).toContain(
      "innerUnitRadius = 1.0 - (1.0 - innerUnitRadius) / max(truthDrop.scale, 0.0001);",
    );
  });
});

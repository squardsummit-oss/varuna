/**
 * Motion M9 (reversed-flow dash) and M8 (surcharge pulse) on deck's clock: the pure selection,
 * phase and copy functions, and the layers the builders hand deck. The GPU itself is not here -
 * jsdom has no WebGL - so what is checked is the shader source the extensions inject and the
 * TypeScript mirror of the fragment's dash test.
 */

import { describe, expect, it, vi } from "vitest";

import type { ReversedEdge } from "@/lib/api/surcharge";
import { DUR_MS } from "@/lib/motion";
import type { Bbox } from "../../basemap";
import {
  DASH_ARRAY,
  DASH_PERIOD_MS,
  FLOW_DASH_MAIN_END,
  FlowDashExtension,
  INLAND_MIN_ZOOM,
  edgesInView,
  flowDashModules,
  pointsInView,
  reversedEdgeWidth,
  reversedEdgesAtStep,
  reversedFlowLayers,
  reversedFlowSummary,
  selectReversedEdges,
} from "../reversed-flow";
import {
  PULSE_SHADER,
  SurchargePulseExtension,
  deckAnimates,
  loopPhase,
  surchargeLayers,
} from "../surcharge";
import type { ReversedEdgePath } from "../types";

// ---- Mirrors of the GLSL, derived from the strings the GPU is actually given --------------------
//
// jsdom has no WebGL, so the direction of travel is checked on a TypeScript copy of the shader
// arithmetic. The copy is not written by hand: its sign and constants are parsed out of the injected
// source, so editing `+=` to `-=` in FLOW_DASH_MAIN_END, or the pulse growth in PULSE_SHADER, changes
// what these tests compute and fails them.

const FLOW_DASH_PATTERN =
  /^vDashOffset ([+-])= flowDash\.phase \* \(instanceDashArrays\.x \+ instanceDashArrays\.y\);$/;

/** +1 or -1: how the injected vertex line moves `vDashOffset` as the phase grows. */
function injectedDashSign(): number {
  const match = FLOW_DASH_MAIN_END.match(FLOW_DASH_PATTERN);
  if (!match)
    throw new Error(`FLOW_DASH_MAIN_END no longer has the tested shape: ${FLOW_DASH_MAIN_END}`);
  return match[1] === "+" ? 1 : -1;
}

/** `vDashOffset` at `phase`, as the injected vertex line computes it. */
function dashOffset(phase: number, dash: readonly [number, number] = DASH_ARRAY): number {
  return injectedDashSign() * phase * (dash[0] + dash[1]);
}

/** deck's fragment test, `mod(vPathPosition.y + offset, unitLength) <= solidLength` with
 * `offset = vDashOffset` (dashAlignMode 0); the test below asserts deck's source still says so. */
function insideDash(along: number, phase: number, dash = DASH_ARRAY): boolean {
  const unit = dash[0] + dash[1];
  const offset = (((along + dashOffset(phase, dash)) % unit) + unit) % unit;
  return offset <= dash[0];
}

/** The pulse ring's radius and alpha multipliers, parsed from the injected shader lines. */
function pulseRing(phase: number): { scale: number; alpha: number } {
  const size = PULSE_SHADER.inject["vs:DECKGL_FILTER_SIZE"].match(
    /^size \*= 1\.0 \+ ([\d.]+) \* surchargePulse\.phase;$/,
  );
  const alpha = PULSE_SHADER.inject["fs:DECKGL_FILTER_COLOR"].match(
    /^color\.a \*= 1\.0 - surchargePulse\.phase;$/,
  );
  if (!size || !alpha) throw new Error("PULSE_SHADER no longer has the tested shape");
  return { scale: 1 + Number(size[1]) * phase, alpha: 1 - phase };
}

// The tidal edge's real geometry from city/mumbai/map/drains.geojson (MUM-E026920), and two
// inland pipes: one near Hindmata, one far north at Andheri.
const tidal: ReversedEdgePath = {
  edgeId: "MUM-E026920",
  path: [
    [72.816018, 19.024918],
    [72.815843, 19.025201],
  ],
  minQ: -1.0412,
  tidal: true,
};
const hindmata: ReversedEdgePath = {
  edgeId: "E-hindmata",
  path: [
    [72.8412, 19.0121],
    [72.8415, 19.0124],
  ],
  minQ: -0.43,
  tidal: false,
};
const andheri: ReversedEdgePath = {
  edgeId: "E-andheri",
  path: [
    [72.8441, 19.1191],
    [72.8444, 19.1194],
  ],
  minQ: -0.05,
  tidal: false,
};
const edges = [tidal, hindmata, andheri];

/** A view around Dadar: Hindmata in it, Andheri and the tidal outfall out of it. */
const dadar: Bbox = [
  [72.83, 19.0],
  [72.86, 19.02],
];

function stepsEdge(overrides: Partial<ReversedEdge>): ReversedEdge {
  return { id: "E", tidal: false, steps: [3, 4], minQ: -0.2, ...overrides };
}

describe("which reversed edges are drawn", () => {
  it("keeps only edges reversed at the step that carry a path of non-zero length", () => {
    const raw: ReversedEdge[] = [
      stepsEdge({ id: "on-step", path: tidal.path }),
      stepsEdge({ id: "off-step", steps: [9], path: tidal.path }),
      stepsEdge({ id: "no-path" }),
      stepsEdge({ id: "one-point", path: [[72.8, 19.0]] }),
      stepsEdge({
        id: "zero-length",
        path: [
          [72.8, 19.0],
          [72.8, 19.0],
        ],
      }),
    ];
    expect(reversedEdgesAtStep(raw, 3).map((e) => e.edgeId)).toEqual(["on-step"]);
    expect(reversedEdgesAtStep(raw, 9).map((e) => e.edgeId)).toEqual(["off-step"]);
    expect(reversedEdgesAtStep(raw, 7)).toEqual([]);
  });

  it("a run baked before the product carried paths draws nothing, and does not throw", () => {
    const stale: ReversedEdge[] = [
      stepsEdge({ id: "MUM-E026920", tidal: true, steps: [0, 1, 2] }),
      stepsEdge({ id: "MUM-E1" }),
    ];
    const drawn = reversedEdgesAtStep(stale, 1);
    expect(drawn).toEqual([]);
    expect(
      reversedFlowLayers({
        edges: drawn,
        show: true,
        reducedMotion: false,
        zoom: 16,
        bounds: null,
      }),
    ).toEqual([]);
  });

  it("always includes tidal edges, at the citywide zoom and out of view", () => {
    for (const zoom of [9, 12, INLAND_MIN_ZOOM - 0.01, 17]) {
      const ids = selectReversedEdges(edges, { zoom, bounds: dadar }).map((e) => e.edgeId);
      expect(ids).toContain("MUM-E026920");
    }
  });

  it("gates inland edges by zoom", () => {
    const below = selectReversedEdges(edges, { zoom: INLAND_MIN_ZOOM - 0.01, bounds: null });
    expect(below.map((e) => e.edgeId)).toEqual(["MUM-E026920"]);
    const at = selectReversedEdges(edges, { zoom: INLAND_MIN_ZOOM, bounds: null });
    expect(at.map((e) => e.edgeId)).toEqual(["MUM-E026920", "E-hindmata", "E-andheri"]);
  });

  it("gates inland edges by view", () => {
    const ids = selectReversedEdges(edges, { zoom: 15, bounds: dadar }).map((e) => e.edgeId);
    expect(ids).toEqual(["MUM-E026920", "E-hindmata"]);
  });

  it("counts what is in view for the redraw gate", () => {
    expect(edgesInView(edges, dadar)).toBe(1);
    expect(edgesInView(edges, null)).toBe(3);
    expect(
      pointsInView(
        [
          { lon: 72.84, lat: 19.01 },
          { lon: 72.9, lat: 19.1 },
        ],
        dadar,
      ),
    ).toBe(1);
  });
});

describe("the dash moves the way the water does", () => {
  it("loops once per section 8 period, seamlessly", () => {
    expect(DASH_PERIOD_MS).toBe(DUR_MS.surchargePulse);
    expect(loopPhase(0, 1600)).toBe(0);
    expect(loopPhase(400, 1600)).toBeCloseTo(0.25);
    expect(loopPhase(1600 + 400, 1600)).toBeCloseTo(0.25);
    expect(loopPhase(-400, 1600)).toBeCloseTo(0.75);
    expect(loopPhase(123, 0)).toBe(0);
    // One period moves the pattern by exactly one dash plus gap, so phase 1 draws phase 0.
    const unit = DASH_ARRAY[0] + DASH_ARRAY[1];
    expect(dashOffset(1)).toBeCloseTo(unit);
    // Points away from a dash's ends, where float rounding at phase 0.999999 could flip the test.
    for (const along of [0.4, 1.2, 2.3, 7.9]) {
      expect(insideDash(along, 0.999999)).toBe(insideDash(along, 0));
    }
  });

  it("advances with time toward path[0], the from-node reversed flow runs to", () => {
    // Track the leading edge of the dash that starts at `along = unit`. A point just inside it at
    // phase 0 must be outside it once time has moved on, and the dash must now cover a point
    // nearer path[0] - so the pattern has slid toward the start of the path.
    // The sign is read from the GLSL line itself, so this also pins what the GPU runs.
    expect(FLOW_DASH_MAIN_END).toMatch(/vDashOffset \+= flowDash\.phase/);
    const unit = DASH_ARRAY[0] + DASH_ARRAY[1];
    const phaseLater = 0.2; // 320 ms into a 1.6 s loop
    const shift = dashOffset(phaseLater);
    expect(shift).toBeGreaterThan(0);
    // The start of the second dash, measured from path[0]:
    expect(insideDash(unit + 0.01, 0)).toBe(true);
    expect(insideDash(unit - 0.01, 0)).toBe(false);
    // Later, that dash starts `shift` widths closer to path[0].
    expect(insideDash(unit - shift + 0.01, phaseLater)).toBe(true);
    expect(insideDash(unit - shift - 0.01, phaseLater)).toBe(false);
  });

  it("appends the phase to the stock vertex dash offset, where the fragment reads it", () => {
    const stock = [
      {
        name: "pathStyle",
        inject: {
          "vs:#main-end": "vDashArray = instanceDashArrays;\nvDashOffset = 0.0;",
          "fs:#main-start": "float unitOffset = mod(vPathPosition.y + offset, unitLength);",
        },
      },
    ];
    const modules = flowDashModules(stock);
    expect(modules[0].name).toBe("flowDash");
    const pathStyle = modules.find((m) => m.name === "pathStyle");
    const mainEnd = pathStyle?.inject?.["vs:#main-end"] ?? "";
    expect(mainEnd.indexOf("vDashOffset = 0.0;")).toBeLessThan(mainEnd.indexOf(FLOW_DASH_MAIN_END));
    // The fragment injection is left exactly as deck wrote it.
    expect(pathStyle?.inject?.["fs:#main-start"]).toBe(stock[0].inject["fs:#main-start"]);
    expect(String(modules[0].vs)).toContain("uniform flowDashUniforms");
  });

  it("builds against deck's own PathStyleExtension shaders", () => {
    const extension = new FlowDashExtension();
    const shaders = extension.getShaders.call(
      { state: { pathTesselator: {} }, constructor: { layerName: "PathLayer" } } as never,
      extension,
    ) as { modules: { name: string; inject?: Record<string, string> }[] };
    const pathStyle = shaders.modules.find((m) => m.name === "pathStyle");
    const mainEnd = pathStyle?.inject?.["vs:#main-end"] ?? "";
    expect(mainEnd).toContain("vDashOffset = 0.0;");
    // Appended after deck's own write, so the phase is added to the offset rather than overwritten.
    expect(mainEnd.lastIndexOf("vDashOffset = 0.0;")).toBeLessThan(
      mainEnd.indexOf(FLOW_DASH_MAIN_END),
    );
    // The fragment test the insideDash mirror copies, in deck's installed source: at dashAlignMode 0
    // the offset is vDashOffset and a larger offset slides the pattern toward path[0].
    const fragment = pathStyle?.inject?.["fs:#main-start"] ?? "";
    expect(fragment).toContain("offset = vDashOffset;");
    expect(fragment).toContain("mod(vPathPosition.y + offset, unitLength)");
    expect(shaders.modules.map((m) => m.name)).toContain("flowDash");
  });

  it("reads the clock in draw only when animated; under reduced motion the phase stays 0", () => {
    const extension = new FlowDashExtension();
    const now = vi.spyOn(performance, "now").mockReturnValue(DUR_MS.surchargePulse * 1.25);
    const setShaderModuleProps = vi.fn();
    extension.draw.call({ props: { flowAnimated: true }, setShaderModuleProps } as never);
    expect(setShaderModuleProps).toHaveBeenLastCalledWith({ flowDash: { phase: 0.25 } });
    extension.draw.call({ props: { flowAnimated: false }, setShaderModuleProps } as never);
    expect(setShaderModuleProps).toHaveBeenLastCalledWith({ flowDash: { phase: 0 } });
    now.mockRestore();
  });

  it("draws a static red dash under reduced motion, an animated one otherwise", () => {
    const moving = reversedFlowLayers({
      edges,
      show: true,
      reducedMotion: false,
      zoom: 15,
      bounds: null,
    }) as { id: string; props: Record<string, unknown> }[];
    const still = reversedFlowLayers({
      edges,
      show: true,
      reducedMotion: true,
      zoom: 15,
      bounds: null,
    }) as { id: string; props: Record<string, unknown> }[];
    expect(moving).toHaveLength(1);
    expect(still).toHaveLength(1);
    expect(moving[0].id).toBe("reversed-flow");
    expect(moving[0].props.flowAnimated).toBe(true);
    expect(still[0].props.flowAnimated).toBe(false);
    // Same dash, same colour, same data either way: reduced motion only stops the clock.
    expect(still[0].props.getDashArray).toEqual(DASH_ARRAY);
    expect(still[0].props.getColor).toEqual(moving[0].props.getColor);
    expect(
      reversedFlowLayers({ edges, show: false, reducedMotion: false, zoom: 15, bounds: null }),
    ).toEqual([]);
  });

  it("widths scale with the square root of the reverse discharge, capped at 4 px", () => {
    expect(reversedEdgeWidth(0)).toBe(2);
    expect(reversedEdgeWidth(-0.25)).toBeCloseTo(3);
    expect(reversedEdgeWidth(-1.0412)).toBe(4);
    expect(reversedEdgeWidth(-9)).toBe(4);
  });
});

describe("the surcharge pulse", () => {
  const nodes = [{ id: "N-1", lon: 72.8412, lat: 19.0121, q: 0.4 }];

  it("expands the ring 1 to 2.4 times and fades it over the period", () => {
    expect(pulseRing(0)).toEqual({ scale: 1, alpha: 1 });
    expect(pulseRing(0.5).scale).toBeCloseTo(1.7);
    expect(pulseRing(0.5).alpha).toBeCloseTo(0.5);
    expect(pulseRing(1).scale).toBeCloseTo(2.4);
    expect(PULSE_SHADER.inject["vs:DECKGL_FILTER_SIZE"]).toContain("1.4 * surchargePulse.phase");
    expect(PULSE_SHADER.inject["fs:DECKGL_FILTER_COLOR"]).toContain("1.0 - surchargePulse.phase");
  });

  it("is a static ring under reduced motion and a core plus a pulse ring otherwise", () => {
    const still = surchargeLayers({ surcharge: nodes, show: true, reducedMotion: true }) as {
      id: string;
      props: Record<string, unknown>;
    }[];
    expect(still.map((l) => l.id)).toEqual(["surcharge"]);
    expect(still[0].props.stroked).toBe(true);
    const moving = surchargeLayers({ surcharge: nodes, show: true, reducedMotion: false }) as {
      id: string;
      props: Record<string, unknown>;
    }[];
    expect(moving.map((l) => l.id)).toEqual(["surcharge", "surcharge-pulse"]);
    expect(moving[1].props.pulseAnimated).toBe(true);
    expect(moving[1].props.filled).toBe(false);
    // The core is identical either way, so switching the preference never moves the marker.
    expect(moving[0].props.getFillColor).toEqual(still[0].props.getFillColor);
  });

  it("reads the clock only through its draw", () => {
    const extension = new SurchargePulseExtension();
    const now = vi.spyOn(performance, "now").mockReturnValue(DUR_MS.surchargePulse * 2.5);
    const setShaderModuleProps = vi.fn();
    extension.draw.call({ props: { pulseAnimated: true }, setShaderModuleProps } as never);
    expect(setShaderModuleProps).toHaveBeenLastCalledWith({ surchargePulse: { phase: 0.5 } });
    now.mockRestore();
  });
});

describe("deck's redraw loop", () => {
  it("runs only while a pulse or a dash is in view, and never under reduced motion", () => {
    const cases: [boolean, number, number, boolean][] = [
      [false, 0, 0, false],
      [false, 2, 0, true],
      [false, 0, 1, true],
      [false, 3, 1, true],
      [true, 0, 0, false],
      [true, 2, 0, false],
      [true, 0, 1, false],
      [true, 3, 1, false],
    ];
    for (const [reducedMotion, surchargeVisible, reversedVisible, expected] of cases) {
      expect(deckAnimates({ reducedMotion, surchargeVisible, reversedVisible })).toBe(expected);
    }
  });
});

describe("the reversed-pipe sentence", () => {
  const withPath = (id: string, tidalEdge = false): ReversedEdge => ({
    id,
    tidal: tidalEdge,
    steps: [1],
    minQ: -0.5,
    path: tidal.path,
  });

  it("quotes the run's total, not what is stored or drawn", () => {
    const text = reversedFlowSummary({
      reversedTotal: 24014,
      reversedEdges: [
        withPath("t", true),
        withPath("a"),
        { id: "b", tidal: false, steps: [], minQ: -1 },
      ],
    });
    expect(text).toBe(
      "24,014 pipes run backwards in this run. 2 of the 3 it stored can be drawn: tide-locked " +
        `outfalls at every zoom, inland pipes from zoom ${INLAND_MIN_ZOOM} and in view.`,
    );
  });

  it("makes no causal claim about why a pipe runs backwards", () => {
    const text = reversedFlowSummary({ reversedTotal: 5, reversedEdges: [withPath("a")] });
    expect(text).not.toMatch(/uphill|because|follow/i);
  });

  it("never claims edges are shown when the run carries no geometry", () => {
    const text = reversedFlowSummary({
      reversedTotal: 18380,
      reversedEdges: [{ id: "t", tidal: true, steps: [1], minQ: -0.77 }],
    });
    expect(text).toBe(
      "18,380 pipes run backwards in this run. This run stores no pipe geometry, so they are counted here but not drawn.",
    );
    expect(text).not.toMatch(/can be drawn|are drawn:/);
    // Nothing the stage operator cannot do from the console.
    expect(text).not.toMatch(/bake|make |terminal/i);
  });

  it("never quotes the stored count as the run's total when the product omits it", () => {
    const stored = Array.from({ length: 500 }, (_, i) => withPath(`e${i}`));
    const withGeometry = reversedFlowSummary({ reversedTotal: null, reversedEdges: stored });
    expect(withGeometry).toBe(
      "This run does not report how many pipes run backwards. 500 of the 500 it stored can be " +
        `drawn: tide-locked outfalls at every zoom, inland pipes from zoom ${INLAND_MIN_ZOOM} and in view.`,
    );
    expect(withGeometry).not.toMatch(/500 pipes run backwards/);
    expect(
      reversedFlowSummary({
        reversedTotal: null,
        reversedEdges: [{ id: "t", tidal: true, steps: [1], minQ: -0.77 }],
      }),
    ).toBe(
      "This run reports no reversed-pipe total and stores no pipe geometry, so none are drawn.",
    );
  });

  it("says so when nothing runs backwards", () => {
    expect(reversedFlowSummary({ reversedTotal: 0, reversedEdges: [] })).toBe(
      "No pipe runs backwards in this run.",
    );
  });
});

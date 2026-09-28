/**
 * Reversed-flow drain edges (SPEC.md 6.7, motion M9): pipes the model runs backwards, above all
 * the tide-locked outfall pushing the sea back up the trunk.
 *
 * **What is drawn.** Every cycle reverses 18,380 to 24,014 of Mumbai's 49,770 inferred pipes, and
 * the surcharge product stores the 500 worst with the tidal ones first. All of them are model
 * output, so hiding them would be a physics choice; but 500 animated dashes at the citywide fit read
 * as noise, and an inland pipe of 40 m is a pixel or two there. So:
 *
 * - tide-locked edges are drawn and animated at every zoom;
 * - inland edges only at zoom 14 or closer, and only those in view;
 * - an edge with no `path` is never drawn. Runs baked before the product carried geometry have
 *   none, and the layer then renders nothing rather than inventing a line.
 *
 * The run's total count is quoted beside the layer toggle by {@link reversedFlowSummary}, so the
 * zoom gate never hides how much of the network is running backwards.
 *
 * **How it moves.** `PathStyleExtension` draws the dash; {@link FlowDashExtension} adds one uniform,
 * the loop phase, to the dash offset in the vertex shader, so a frame of motion costs one uniform
 * write and no attribute upload or React render. Reversed flow runs from the edge's to-node back to
 * its from-node, which is `path[0]`, and a growing offset carries the dash toward `path[0]`: the
 * dash travels the way the water does. Under reduced motion the phase is pinned at 0 and the layer
 * is the section 8 fallback, a static red dash.
 */

import { WebMercatorViewport, type Layer } from "@deck.gl/core";
import { PathStyleExtension } from "@deck.gl/extensions";
import { PathLayer } from "@deck.gl/layers";
import { useMemo } from "react";

import type { ReversedEdge, SurchargeSet } from "@/lib/api/surcharge";
import { DUR_MS } from "@/lib/motion";
import type { Bbox } from "../basemap";
import { SURCHARGE_LINE } from "./palette";
import { loopPhase } from "./surcharge";
import type { ReversedEdgePath } from "./types";

/** Inland reversed edges are drawn from this zoom in. Below it a 40 m pipe is a pixel or two. */
export const INLAND_MIN_ZOOM = 14;

/**
 * Solid and gap length of the dash in deck's dash units, which measured on SwiftShader are half the
 * drawn width: at the 4 px tidal edge this is a 6 px dash and a 4 px gap, and one loop period
 * carries the pattern 10 px toward `path[0]`.
 */
export const DASH_ARRAY: [number, number] = [3, 2];

/** Widest a reversed edge is drawn, in pixels. */
export const MAX_WIDTH_PX = 4;

/**
 * One dash period takes the section 8 loop period. Section 8 gives M9 no duration of its own, and
 * reusing the surcharge pulse's 1.6 s keeps the two data loops on one clock rather than adding a
 * number the catalogue does not state.
 */
export const DASH_PERIOD_MS = DUR_MS.surchargePulse;

// ---- Selection -------------------------------------------------------------------------------

function hasLength(path: readonly (readonly [number, number])[]): boolean {
  if (path.length < 2) return false;
  const [x0, y0] = path[0];
  return path.some(([x, y]) => x !== x0 || y !== y0);
}

/**
 * The edges running backwards at `step` that can be drawn: those whose `steps` include it and that
 * carry a path of non-zero length. Everything else is dropped here, which is how a run baked before
 * the product carried geometry draws nothing without an error.
 */
export function reversedEdgesAtStep(
  edges: readonly ReversedEdge[],
  step: number,
): ReversedEdgePath[] {
  const out: ReversedEdgePath[] = [];
  for (const edge of edges) {
    if (!edge.path || !hasLength(edge.path) || !edge.steps.includes(step)) continue;
    out.push({ edgeId: edge.id, path: edge.path, minQ: edge.minQ, tidal: edge.tidal });
  }
  return out;
}

function pathIntersects(path: readonly (readonly [number, number])[], bounds: Bbox): boolean {
  const [[west, south], [east, north]] = bounds;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const [x, y] of path) {
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }
  return maxX >= west && minX <= east && maxY >= south && minY <= north;
}

export interface EdgeSelection {
  zoom: number;
  /** The viewport in lon/lat; null when the canvas has no size yet, which counts as all in view. */
  bounds: Bbox | null;
}

/** The decision on which reversed edges M9 draws: tidal always, inland from zoom 14 and in view. */
export function selectReversedEdges(
  edges: readonly ReversedEdgePath[],
  { zoom, bounds }: EdgeSelection,
): ReversedEdgePath[] {
  const inlandShown = zoom >= INLAND_MIN_ZOOM;
  return edges.filter(
    (edge) => edge.tidal || (inlandShown && (bounds === null || pathIntersects(edge.path, bounds))),
  );
}

/** How many of `edges` are in view, for the redraw gate (a tidal edge off screen moves nothing). */
export function edgesInView(edges: readonly ReversedEdgePath[], bounds: Bbox | null): number {
  if (bounds === null) return edges.length;
  let n = 0;
  for (const edge of edges) if (pathIntersects(edge.path, bounds)) n += 1;
  return n;
}

/** How many points are in view; the surcharge markers' half of the redraw gate. */
export function pointsInView(
  points: readonly { lon: number; lat: number }[],
  bounds: Bbox | null,
): number {
  if (bounds === null) return points.length;
  const [[west, south], [east, north]] = bounds;
  let n = 0;
  for (const p of points) {
    if (p.lon >= west && p.lon <= east && p.lat >= south && p.lat <= north) n += 1;
  }
  return n;
}

/** The camera's footprint in lon/lat, or null before the canvas has been measured. */
export function viewBounds(
  view: { longitude: number; latitude: number; zoom: number },
  size: { width: number; height: number } | null,
): Bbox | null {
  if (!size || size.width <= 0 || size.height <= 0) return null;
  try {
    const viewport = new WebMercatorViewport({
      width: size.width,
      height: size.height,
      longitude: view.longitude,
      latitude: view.latitude,
      zoom: view.zoom,
    });
    const [[west, south], [east, north]] = viewport.getBounds() as unknown as [
      [number, number],
      [number, number],
    ];
    return [
      [west, south],
      [east, north],
    ];
  } catch {
    return null;
  }
}

// ---- Motion ----------------------------------------------------------------------------------

/** The uniform block the flow-dash module declares for the vertex stage. */
const FLOW_DASH_BLOCK = /* glsl */ `\
layout(std140) uniform flowDashUniforms {
  float phase;
} flowDash;
`;

/** Added after the stock `vs:#main-end`, which has just set `vDashOffset` (to 0 without
 * `highPrecisionDash`). `dashJustified` stays false, so the fragment reads `vDashOffset`. */
export const FLOW_DASH_MAIN_END =
  "vDashOffset += flowDash.phase * (instanceDashArrays.x + instanceDashArrays.y);";

export const FLOW_DASH_MODULE = {
  name: "flowDash",
  vs: FLOW_DASH_BLOCK,
  uniformTypes: { phase: "f32" },
} as const;

interface ShaderModuleLike {
  name: string;
  inject?: Record<string, string>;
  [key: string]: unknown;
}

interface FlowDashProps {
  /** False pins the phase at 0: the dash is drawn and does not move. */
  flowAnimated?: boolean;
}

/**
 * `PathStyleExtension({dash: true})` with the dash offset driven by a phase uniform (motion M9).
 *
 * The stock fragment injection declares its `offset` inside an `if` and discards in the same block,
 * so nothing appended after it can move the dash. The vertex stage can: the stock
 * `vs:#main-end` writes `vDashOffset`, and this appends the phase to it.
 */
export class FlowDashExtension extends PathStyleExtension {
  static extensionName = "FlowDashExtension";
  static defaultProps = { ...PathStyleExtension.defaultProps, flowAnimated: false };

  constructor() {
    super({ dash: true });
  }

  getShaders(this: Layer, extension: FlowDashExtension) {
    const stock = PathStyleExtension.prototype.getShaders.call(this, extension) as {
      modules: ShaderModuleLike[];
      defines?: Record<string, unknown>;
    } | null;
    if (!stock) return stock;
    return { ...stock, modules: flowDashModules(stock.modules) };
  }

  draw(this: Layer<FlowDashProps>) {
    const phase = this.props.flowAnimated ? loopPhase(performance.now(), DASH_PERIOD_MS) : 0;
    this.setShaderModuleProps({ flowDash: { phase } });
  }
}

/** The stock path-style modules with the phase appended to `vs:#main-end`, plus the phase module. */
export function flowDashModules(stock: readonly ShaderModuleLike[]): ShaderModuleLike[] {
  const modules = stock.map((module) => {
    if (module.name !== "pathStyle" || !module.inject) return module;
    const mainEnd = module.inject["vs:#main-end"] ?? "";
    return {
      ...module,
      inject: { ...module.inject, "vs:#main-end": `${mainEnd}\n${FLOW_DASH_MAIN_END}\n` },
    };
  });
  return [FLOW_DASH_MODULE as unknown as ShaderModuleLike, ...modules];
}

/** One instance for every layer, so a rebuilt layer keeps its compiled program. */
const FLOW_DASH = new FlowDashExtension();

// ---- Layer -----------------------------------------------------------------------------------

/** Width by the square root of the strongest reverse discharge, 2 to 4 px. */
export function reversedEdgeWidth(minQ: number): number {
  return Math.min(MAX_WIDTH_PX, 2 + 2 * Math.sqrt(Math.min(Math.abs(minQ), 1)));
}

export interface ReversedFlowLayerOptions {
  /** Edges running backwards at the current step, with geometry (`reversedEdgesAtStep`). */
  edges: readonly ReversedEdgePath[];
  show: boolean;
  reducedMotion: boolean;
  zoom: number;
  bounds: Bbox | null;
}

export function reversedFlowLayers({
  edges,
  show,
  reducedMotion,
  zoom,
  bounds,
}: ReversedFlowLayerOptions): unknown[] {
  if (!show) return [];
  return buildLayers(selectReversedEdges(edges, { zoom, bounds }), reducedMotion);
}

function buildLayers(drawn: ReversedEdgePath[], reducedMotion: boolean): unknown[] {
  if (drawn.length === 0) return [];
  return [
    new PathLayer<ReversedEdgePath>({
      id: "reversed-flow",
      data: drawn,
      getPath: (d) => d.path,
      getColor: SURCHARGE_LINE,
      getWidth: (d) => reversedEdgeWidth(d.minQ),
      widthUnits: "pixels",
      widthMinPixels: 2,
      getDashArray: DASH_ARRAY,
      dashJustified: false,
      pickable: false,
      extensions: [FLOW_DASH],
      flowAnimated: !reducedMotion,
    } as ConstructorParameters<typeof PathLayer<ReversedEdgePath>>[0] & FlowDashProps),
  ];
}

/**
 * {@link reversedFlowLayers} for `CityMap`, memoised on the set of edges drawn rather than on the
 * camera: a pan that keeps the same edges on screen rebuilds nothing. Also returns how many drawn
 * edges are in view, for the redraw gate.
 */
export function useReversedFlowLayers(options: ReversedFlowLayerOptions): {
  layers: unknown[];
  inView: number;
} {
  const { edges, show, reducedMotion, zoom, bounds } = options;
  const drawn = show ? selectReversedEdges(edges, { zoom, bounds }) : [];
  // `drawn` is a fresh array on every render and on every scrub step; what it draws - which edges,
  // how wide - is the identity that matters, so the same edges on the next step rebuild nothing.
  const key = drawn.map((edge) => `${edge.edgeId}:${edge.minQ}`).join("|");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const layers = useMemo(() => buildLayers(drawn, reducedMotion), [key, reducedMotion]);
  return { layers, inView: edgesInView(drawn, bounds) };
}

// ---- Copy ------------------------------------------------------------------------------------

/**
 * The sentence under the surcharge toggle (section 6.8: sentence case, numbers with context). It
 * quotes the run's total (`n_reversed_edges`), never the number drawn, and never claims an edge is
 * on the map when the run carries no geometry for it.
 */
export function reversedFlowSummary(
  set: Pick<SurchargeSet, "reversedTotal" | "reversedEdges">,
): string {
  const total = set.reversedTotal;
  const stored = set.reversedEdges.length;
  const withPath = set.reversedEdges.filter((edge) => edge.path && hasLength(edge.path)).length;
  const n = (value: number) => value.toLocaleString("en-IN");
  if (total !== null && total <= 0) return "No pipe runs backwards in this run.";
  if (total === null && withPath <= 0) {
    return "This run reports no reversed-pipe total and stores no pipe geometry, so none are drawn.";
  }
  const lead =
    total === null
      ? "This run does not report how many pipes run backwards."
      : `${n(total)} ${total === 1 ? "pipe runs" : "pipes run"} backwards in this run.`;
  if (withPath <= 0) {
    return `${lead} This run stores no pipe geometry, so they are counted here but not drawn.`;
  }
  return (
    `${lead} ${n(withPath)} of the ${n(stored)} it stored can be drawn: tide-locked outfalls at ` +
    `every zoom, inland pipes from zoom ${INLAND_MIN_ZOOM} and in view.`
  );
}

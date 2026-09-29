/**
 * The globe's projection and its canvas painter, with nothing in it that needs a DOM or React.
 *
 * `globe-intro.tsx` draws both sequences (M26 on the landing hero, M27 on the dashboard) and owns
 * the camera: *where* the globe is at a given instant, from the motion catalogue's durations. This
 * module is only *how* one frame is drawn, which is what lets the landing hero paint its moving
 * frames in a worker (`globe-worker.ts`) while the main thread computes nothing but the camera.
 * It imports `d3-geo` and `topojson-client` and nothing else, so a worker can load it.
 */

import {
  geoBounds,
  geoEquirectangularRaw,
  geoOrthographicRaw,
  geoPath,
  geoProjectionMutator,
  type GeoProjection,
} from "d3-geo";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import { feature } from "topojson-client";

/** The SVG's own coordinate space. Everything scales from it, so the hero is resolution-free. */
export const VIEW_W = 900;
export const VIEW_H = 560;

export const MUMBAI_LON = 72.86;
export const MUMBAI_LAT = 19.06;

/** One country with its lon/lat bounds, computed once so the zoomed acts can cull cheaply. */
export interface Land {
  feature: Feature<Geometry>;
  /** [west, south, east, north] in degrees. */
  bounds: [number, number, number, number];
  name: string;
}

export interface WorldShape {
  land: Land[];
}

/** Where the camera is at `elapsed`: everything the globe draws is a pure function of this. */
export interface GlobeFrame {
  /** 0 a globe, 1 a flat equirectangular map. */
  alpha: number;
  /** Projection scale in view units. */
  scale: number;
  /** Longitude and latitude at the centre of the frame. */
  centre: [number, number];
  /** 0 to 1 as the highlighted country's outline strengthens (acts 2 and 3). */
  highlight: number;
  /** 0 to 1 as the AOI box appears in the final act. */
  aoi: number;
  /** True once the sequence has run its length. */
  finished: boolean;
  /**
   * How much of the picture the photographic Earth behind this geometry is carrying, 0 to 1.
   *
   * `frameAt` always reports 0, because the camera does not know whether the Blue Marble texture
   * has decoded; the component sets it from `photoAmount()` before it paints or posts the frame.
   * Everything that would *hide* the photograph - the ocean disc, the country fills - is faded out
   * by it, and everything the photograph cannot say - the coastlines as instrumentation, the
   * India highlight, the AOI box, the Mumbai mark - is kept, dimmed where it would
   * otherwise fight the imagery. It rides on the frame rather than on a second message so that
   * `globe-worker.ts`, which forwards frames unread, needs no change at all.
   */
  photo?: number;
}

/** {@link GlobeFrame.photo}, defaulted, so every reader treats an old frame as vector-only. */
export function photoOf(frame: GlobeFrame): number {
  return frame.photo === undefined ? 0 : Math.min(Math.max(frame.photo, 0), 1);
}

/**
 * How strongly each vector layer is drawn once the photograph is behind it.
 *
 * One place for the numbers so the SVG in `globe-intro.tsx` and the canvas below cannot drift
 * apart. **Everything goes to nothing.** The strokes used to keep about half their weight, on the
 * argument that a thin coastline over imagery reads as an instrument rather than as wallpaper;
 * looked at on the built page that is not what it does. Blue Marble already has coastlines, drawn
 * by the light on the water, and a second set in `--line-strong` a pixel off the first reads as a
 * traced outline over a photograph - the thing that made the hero look like a diagram of a planet
 * rather than a planet (2026-09-23).
 *
 * It is also the whole cost of the loop. At zero opacity the painter was still projecting and
 * stroking every country outline sixty times a second; `paintUnroll` and the SVG memo both skip
 * the land entirely once this returns zero, which is why the numbers live here and not inline.
 */
export function vectorOpacity(photo: number): {
  sphereFill: number;
  sphereStroke: number;
  landFill: number;
  landStroke: number;
} {
  return {
    sphereFill: 1 - photo,
    sphereStroke: 1 - photo,
    landFill: 1 - photo,
    landStroke: 1 - photo,
  };
}

/** The interpolated projection: `alpha` 0 is a globe, 1 is a flat equirectangular map. */
export function morphProjection(alpha: number) {
  // `geoProjectionMutator` takes a factory of raw projections and returns a function of the
  // mutable parameter; the typings describe the zero-argument shape, so the call is narrowed here.
  const mutate = geoProjectionMutator((t: number) => (lambda: number, phi: number) => {
    const [x0, y0] = geoOrthographicRaw(lambda, phi);
    const [x1, y1] = geoEquirectangularRaw(lambda, phi);
    return [x0 + t * (x1 - x0), y0 + t * (y1 - y0)];
  }) as unknown as (t: number) => GeoProjection;
  return mutate(alpha);
}

/** Reads one committed TopoJSON file into features with their bounds. */
export async function loadTopology(url: string, signal: AbortSignal): Promise<WorldShape | null> {
  const response = await fetch(url, { signal });
  if (!response.ok) return null;
  const topology = (await response.json()) as unknown;
  if (!topology) return null;
  // The world-atlas topologies carry a `countries` object; typing them precisely would pull in
  // `topojson-specification` for one field, so they are narrowed here instead.
  const topo = topology as { objects: { countries: unknown } };
  const collection = feature(
    topo as never,
    topo.objects.countries as never,
  ) as unknown as FeatureCollection<Geometry>;
  const land = collection.features.map((f) => {
    const [[west, south], [east, north]] = geoBounds(f as never);
    const name = String((f.properties as { name?: unknown } | null)?.name ?? "");
    return {
      feature: f,
      bounds: [west, south, east, north] as [number, number, number, number],
      name,
    };
  });
  return { land };
}

/**
 * The token colours a canvas paints with. Read from the stylesheet by the page and handed over,
 * so no colour is written here (SPEC.md 6.2) and a worker, which has no stylesheet, gets them.
 */
export interface CanvasPalette {
  /** `--ink`: the halo behind the Mumbai mark, so it reads on the photograph in either theme. */
  ink: string;
  deep: string;
  well: string;
  lineStrong: string;
  tide: string;
  text2: string;
  font: string;
}

/** The 2D context calls the painter makes; both a DOM canvas and an offscreen one provide them. */
export type Paintable = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;

/**
 * One frame of M26: the same projection, scale and centre, and the same strokes in the same view
 * units as the SVG, painted through `geoPath(projection, context)`.
 *
 * `width` and `height` are the canvas's CSS size; `dpr` is the device pixel ratio it is backed at.
 */
export function paintUnroll(
  context: Paintable,
  palette: CanvasPalette,
  land: readonly Land[],
  frame: GlobeFrame,
  width: number,
  height: number,
  dpr: number,
): void {
  // `xMidYMid slice`, as the SVG does it: the view box scaled to **cover** the canvas and
  // centred, so the offsets go negative and the overflow is cropped rather than letterboxed.
  const fit = Math.max(width / VIEW_W, height / VIEW_H);
  const offsetX = (width - VIEW_W * fit) / 2;
  const offsetY = (height - VIEW_H * fit) / 2;
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.clearRect(0, 0, width * dpr, height * dpr);
  context.setTransform(dpr * fit, 0, 0, dpr * fit, dpr * offsetX, dpr * offsetY);

  const projection = morphProjection(frame.alpha)
    .scale(frame.scale)
    .translate([VIEW_W / 2, VIEW_H / 2])
    .rotate([-frame.centre[0], -frame.centre[1], 0])
    .precision(0.4);
  // d3's context typing names the DOM context; the offscreen one has the same path methods.
  const path = geoPath(projection, context as CanvasRenderingContext2D);

  // What the photographic Earth behind this canvas is carrying, if anything. At 0 - no WebGL, no
  // texture yet, or the approach's final act - every line below is exactly what it always was.
  const dim = vectorOpacity(photoOf(frame));

  context.beginPath();
  path({ type: "Sphere" });
  if (dim.sphereFill > 0.01) {
    context.globalAlpha = dim.sphereFill;
    context.fillStyle = palette.deep;
    context.fill();
  }
  if (dim.sphereStroke > 0.01) {
    context.globalAlpha = dim.sphereStroke;
    context.lineWidth = 1;
    context.strokeStyle = palette.lineStrong;
    context.stroke();
  }

  // Resampling 240 country outlines through the projection is the most expensive thing this
  // function does, and once the photograph carries the picture none of it reaches a pixel. The
  // guard is `> 0.01` on both, so the land is still drawn all the way through the fade and stops
  // exactly when it stops being visible.
  if (dim.landFill > 0.01 || dim.landStroke > 0.01) {
    context.beginPath();
    for (const shape of land) path(shape.feature as never);
    if (dim.landFill > 0.01) {
      context.globalAlpha = dim.landFill;
      context.fillStyle = palette.well;
      context.fill();
    }
    context.globalAlpha = dim.landStroke;
    context.lineWidth = 0.6;
    context.strokeStyle = palette.lineStrong;
    context.stroke();
    context.globalAlpha = 1;
  }

  // The Mumbai mark, held back until the globe faces the city and grown with the flattening.
  const point = projection([MUMBAI_LON, MUMBAI_LAT]);
  const opacity = Math.min(Math.max(frame.alpha * 2, 0), 1);
  if (!point || !Number.isFinite(point[0]) || !Number.isFinite(point[1]) || opacity <= 0.01) {
    return;
  }
  const [x, y] = point;
  const grow = frame.alpha;
  const labelX = x + 12 + 14 * grow;
  // Every part of the mark sits on an `--ink` halo, as a map label does. In the dark theme the halo
  // is the page's own colour and changes nothing; in the light theme the teal and the label are
  // dark, and without it they vanished into the photograph's dark night-side ocean.
  context.globalAlpha = opacity;
  context.beginPath();
  context.arc(x, y, 3, 0, 2 * Math.PI);
  context.lineWidth = 3;
  context.strokeStyle = palette.ink;
  context.stroke();
  context.fillStyle = palette.tide;
  context.fill();
  context.globalAlpha = opacity * 0.7;
  context.beginPath();
  context.arc(x, y, 6 + 14 * grow, 0, 2 * Math.PI);
  context.lineWidth = 3.2;
  context.strokeStyle = palette.ink;
  context.stroke();
  context.lineWidth = 1.2;
  context.strokeStyle = palette.tide;
  context.stroke();
  context.globalAlpha = opacity;
  context.font = `11px ${palette.font}`;
  context.lineWidth = 3;
  context.lineJoin = "round";
  context.strokeStyle = palette.ink;
  context.strokeText("Mumbai", labelX, y + 4);
  context.fillStyle = palette.text2;
  context.fillText("Mumbai", labelX, y + 4);
  context.globalAlpha = 1;
}

/** Messages the landing hero sends its globe worker. */
export type GlobeWorkerMessage =
  | {
      type: "init";
      canvas: OffscreenCanvas;
      palette: CanvasPalette;
      topologyUrl: string;
      width: number;
      height: number;
      dpr: number;
    }
  | { type: "resize"; width: number; height: number }
  | { type: "frame"; frame: GlobeFrame };

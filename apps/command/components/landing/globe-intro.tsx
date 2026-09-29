"use client";

/**
 * The Earth two screens open on: the landing hero's unrolling world map (M26) and the citizen
 * dashboard's approach to Mumbai (M27).
 *
 * **Why it belongs on these pages.** VARUNA's claim is a scale change - global forecasting stops
 * at 12 km, and the water arrives at 30 m. The morph *is* that claim: the camera starts where
 * every weather product starts and ends on one city's streets with the run's own water on them.
 * It is the only decorative motion on the site, it plays once, and it earns its place by being
 * the argument rather than illustrating it (SPEC.md 6.1: spend the boldness in one place).
 *
 * **How it is drawn.** `d3-geo`'s projection mutator interpolates between the orthographic and
 * equirectangular *raw* projections, so this is a genuine continuous family of projections rather
 * than a cross-fade between two pictures - the coastlines deform correctly the whole way through.
 * The paths are rendered as React elements instead of by `d3-selection`, which keeps one rendering
 * model in the app and lets the whole thing be a pure function of one number.
 *
 * **The planet is a photograph** (2026-09-23). Behind the geometry sits a WebGL2 canvas that paints
 * NASA's Blue Marble imagery through the *inverse* of that same morphed projection, per pixel, with
 * the terminator of the replay's own instant across it and an atmospheric rim on the limb
 * (`globe-texture.ts`). The vectors above it then stop being the picture and become the
 * instrumentation on it: the ocean disc, the country fills *and the coastlines* all fade out -
 * Blue Marble draws its own coastlines in light on water, and a second set traced a pixel off
 * them is what made this read as a diagram of a planet rather than a planet - and only what the
 * photograph cannot say stays: the India highlight, the AOI box and the Mumbai mark. One number,
 * `GlobeFrame.photo`, carries that hand-over, and it is 0 - which is to say the picture is exactly
 * what it was before - whenever the browser has no WebGL2, the texture has not decoded, a token
 * colour could not be read, or the frame is tighter than the texture can honestly fill. There is no
 * fade when the texture arrives: a fade would be a motion, and section 8 lists none for this.
 *
 * **Two sequences, one geometry.** `sequence="unroll"` is M26 unchanged: turn 1.4 s, unroll 2.6 s,
 * hand over. `sequence="approach"` is M27: turn 1.4 s to bring India to the meridian, approach
 * over 1.6 s while the sphere flattens, narrow to the Mumbai AOI over 1.0 s. Every act's duration
 * is a `DUR_MS` entry from the catalogue (`lib/motion.ts`), never a local literal, so section 8
 * and this file cannot drift apart.
 *
 * **Offline.** The 110 m topology is committed to `public/world-110m.json` (108 KB) rather than
 * fetched from a CDN: SPEC.md 17 requires the finale to run with the venue's network off, and a
 * hero that needs jsdelivr is the one thing on the page that cannot. The approach also wants
 * finer coastlines once the frame is over India, so `public/world-50m.json` (739 KB) is committed
 * beside it and fetched **only when the approach asks for it** - the landing page never pays for
 * it, and if that second fetch fails the 110 m outline carries all three acts and nothing about
 * the sequence looks broken.
 *
 * Both files are TopoJSON from the `world-atlas` package (Mike Bostock, ISC), derived from
 * Natural Earth, which is public domain. `world-50m.json` is `world-atlas@2.0.2/countries-50m.json`
 * byte for byte, sha256 04342cdc1e3016bcd7db1630de95684d67b79fe3c8c460321e87aef469502394,
 * retrieved 2026-09-19.
 *
 * **What the zoomed acts cost, and what was done about it.** At the arrival scale a country
 * outline drawn whole resamples into tens of thousands of points that are nowhere near the
 * viewport. Four things hold the frame time down: the projection is clipped to the SVG's own
 * extent; features whose lon/lat bounds do not meet the visible window are not projected at all
 * once the frame is narrow enough for that to matter; every country that is drawn the same way is
 * one `<path>` rather than 241 of them; and adaptive resampling is turned off once the map is
 * flat (see `approachPrecision`). Measured on this laptop 2026-09-19, over the whole sequence at
 * 1440 x 900: 24.6-31.8 fps mean before those last two, **41.2-42.1 fps mean and 59.9 fps median
 * after**, against section 14's 55 fps. **The budget is missed.** The remaining cost is the 50 m
 * topology itself: the same sequence on the committed 110 m outline measures 49.1-54.0 fps mean,
 * which still misses it. A spherical pre-clip (`geoClipCircle` on `preclip`) was tried and made
 * it worse - the clip's own arc interpolation costs more than the points it removes. For scale:
 * M26, which has shipped on the landing page since P9.1, measures 23.4-29.9 fps on the same
 * harness, so the approach is not a regression on what is already there.
 *
 * **What the photograph costs (2026-09-23).** Re-measured on the same laptop with 8 python, 12
 * node and 22 chrome processes running, in a **`next dev` build**, driving a headed Chromium at
 * 1440 x 900 and sampling `requestAnimationFrame` over the first 4,000 ms from the instant the
 * sequence's element enters the document. The harness the 2026-09-19 numbers above came from is
 * not in the repository, so these are **not like-for-like** with them; what is like-for-like is the
 * pair below, which is the same page, the same build and the same four seconds with the texture
 * allowed to load and with it refused.
 *
 * - Landing hero (M26), photograph **on**: 32.8, 46.2 and 46.8 fps mean over three runs; median
 *   **59.9 fps** in all three; 95th-percentile frame 33.7-66.6 ms.
 * - Landing hero (M26), texture **blocked**, so the vector globe alone: 53.2, 53.8 and 55.3 fps
 *   mean; median 59.9; 95th-percentile frame 17.4-32.8 ms.
 *
 * So the photograph costs roughly **7 to 20 fps of the mean and nothing of the median**: most
 * frames still land on the refresh, and the mean is dragged down by a handful of 250-1,000 ms
 * frames that are the dev build compiling and the 110 m topology parsing, not the shader.
 * **Section 14's 55 fps is missed either way**, as it already was.
 *
 * The approach (M27) could not be measured against the 41.2-42.1 above at all. On `/dashboard`,
 * which is the only screen that plays it, the same harness measures 15.0-17.0 fps mean with the
 * photograph and 14.3-20.1 fps mean without it - the page around the globe (its deck.gl map, its
 * basemap and its failing data fetches on a machine with no API running) costs more than the globe
 * does, and the two arms are within each other's spread. A number for the approach's own cost
 * needs the isolated harness that produced the earlier figures, and that harness does not exist
 * here.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { geoPath } from "d3-geo";

import {
  loadTopology,
  MUMBAI_LAT,
  MUMBAI_LON,
  morphProjection,
  paintUnroll,
  vectorOpacity,
  VIEW_H,
  VIEW_W,
  type CanvasPalette,
  type GlobeFrame,
  type GlobeWorkerMessage,
  type Land,
  type WorldShape,
} from "@/components/landing/globe-paint";
import {
  createEarthPainter,
  cssColorToRgb,
  ensureEarthImage,
  photoAmount,
  type EarthPainter,
  type EarthPalette,
} from "@/components/landing/globe-texture";
import { DUR_MS } from "@/lib/motion";

export type { GlobeFrame } from "@/components/landing/globe-paint";

/** Globe radius at the start, in view units. */
const SCALE_GLOBE = 190;

/**
 * The equirectangular scale the unroll ends on: the one at which the flat map **covers** the view
 * box rather than sitting inside it.
 *
 * An equirectangular projection at scale `s` is `2 pi s` wide and `pi s` tall, so the map fills
 * the box's height at `VIEW_H / pi` and its width at `VIEW_W / (2 pi)`; covering both means the
 * larger. It was a flat 138 until 2026-09-23, which is `pi * 138 = 433` view units against a
 * 560-unit box - 127 units of `--ink` banded across the top and bottom of a full-bleed hero,
 * which is what the map unrolled into. Covering crops the Pacific instead, and the sequence ends
 * centred on Mumbai, so the longitude it gives up is the half of the world furthest from the one
 * city this page is about.
 */
const SCALE_FLAT = Math.max(VIEW_H / Math.PI, VIEW_W / (2 * Math.PI));

/**
 * The approach's two further scales, in view units of an equirectangular projection (radians to
 * pixels). `SCALE_INDIA` puts about 40° of longitude across the frame - the subcontinent and the
 * Arabian Sea it drains into. `SCALE_AOI` puts about 1.6° across it, so the Mumbai AOI's
 * 0.09° x 0.14° box is a readable rectangle rather than a dot, and the coast is the coast.
 */
const SCALE_INDIA = 1290;
const SCALE_AOI = 32000;

/**
 * Act durations, from the motion catalogue. M26 is turn plus unroll; M27 is turn, approach,
 * arrive. Section 8 lists exactly these `DUR_MS` keys against those two rows.
 */
const SPIN_MS = DUR_MS.globeTurn;
const UNROLL_MS = DUR_MS.globeUnroll;
const APPROACH_MS = DUR_MS.globeApproach;
const ARRIVE_MS = DUR_MS.globeArrive;

/** Degrees per second the globe turns, and the longitude it starts at - Mumbai's, so the city is
 * facing the viewer when the unrolling begins. */
const SPIN_DEG_PER_S = 22;

/**
 * Where the approach's first act ends: the middle of the subcontinent, so India faces the reader
 * square-on before the frame starts closing on one city of it.
 */
const INDIA_LON = 79;
const INDIA_LAT = 22;

/** Where the approach's first act begins: mid-Atlantic, so the turn east is the whole Old World. */
const START_LON = -28;

/** The country whose outline strengthens as the frame approaches it (UI_SPEC 2, act 2). */
const HIGHLIGHT_NAME = "India";

/** The Mumbai AOI (SPEC.md 3.3), drawn as a box in the final act. */
const AOI = { west: 72.815, east: 72.905, south: 18.995, north: 19.135 };

/** Committed topologies, coarse first. The fine one is fetched only by the approach. */
const TOPOLOGY_110M = "/world-110m.json";
const TOPOLOGY_50M = "/world-50m.json";

/** Which opening is playing: the landing hero's (M26) or the dashboard's (M27). */
export type GlobeSequence = "unroll" | "approach";

/** Total length of a sequence, in milliseconds. */
export function sequenceMs(sequence: GlobeSequence): number {
  return sequence === "approach" ? SPIN_MS + APPROACH_MS + ARRIVE_MS : SPIN_MS + UNROLL_MS;
}

/** Ease-out cubic: fast at the start, settling into the flat map rather than stopping dead. */
function easeOut(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

function clamp01(t: number): number {
  return t < 0 ? 0 : t > 1 ? 1 : t;
}

function lerp(from: number, to: number, t: number): number {
  return from + (to - from) * t;
}

/** Zoom is multiplicative, so scales interpolate in the log, or the approach lurches at the end. */
function zoomLerp(from: number, to: number, t: number): number {
  return from * Math.pow(to / from, t);
}

/**
 * The approach (M27), act by act.
 *
 * Act 1 turns the globe from the Atlantic to India at a fixed scale. Act 2 flattens the sphere
 * and closes on the subcontinent. Act 3 narrows to the Mumbai AOI, which is where the map behind
 * is already framed, so the cross-fade that follows lands on the same picture.
 */
function approachFrame(elapsed: number): GlobeFrame {
  const turn = clamp01(elapsed / SPIN_MS);
  const approach = clamp01((elapsed - SPIN_MS) / APPROACH_MS);
  const arrive = clamp01((elapsed - SPIN_MS - APPROACH_MS) / ARRIVE_MS);

  const turnEase = easeOut(turn);
  const approachEase = easeOut(approach);
  const arriveEase = easeOut(arrive);

  const lon =
    arrive > 0 ? lerp(INDIA_LON, MUMBAI_LON, arriveEase) : lerp(START_LON, INDIA_LON, turnEase);
  const lat = arrive > 0 ? lerp(INDIA_LAT, MUMBAI_LAT, arriveEase) : lerp(0, INDIA_LAT, turnEase);

  const scale =
    arrive > 0
      ? zoomLerp(SCALE_INDIA, SCALE_AOI, arriveEase)
      : zoomLerp(SCALE_GLOBE, SCALE_INDIA, approachEase);

  return {
    alpha: approachEase,
    scale,
    centre: [lon, lat],
    highlight: Math.max(approach, arrive > 0 ? 1 : 0),
    aoi: arriveEase,
    finished: elapsed >= sequenceMs("approach"),
  };
}

/** The landing hero's unrolling world map (M26), unchanged. */
function unrollFrame(elapsed: number): GlobeFrame {
  const unrollT = clamp01((elapsed - SPIN_MS) / UNROLL_MS);
  const alpha = easeOut(unrollT);
  const spun = (elapsed / 1000) * SPIN_DEG_PER_S;
  return {
    alpha,
    scale: SCALE_GLOBE + (SCALE_FLAT - SCALE_GLOBE) * alpha,
    // The globe turns while it is still a globe, and settles on Mumbai as it flattens: the
    // rotation eases back to the city's longitude so the hand-over is over the right place.
    centre: [MUMBAI_LON + spun * (1 - alpha), MUMBAI_LAT * (1 - alpha)],
    highlight: 0,
    aoi: 0,
    finished: elapsed >= sequenceMs("unroll"),
  };
}

/** The finished frame each sequence holds under reduced motion. */
function stillFrame(sequence: GlobeSequence): GlobeFrame {
  return sequence === "approach"
    ? approachFrame(sequenceMs("approach"))
    : unrollFrame(sequenceMs("unroll"));
}

/** Where the camera is, for either sequence. Exported so tests can read the acts without a DOM. */
export function frameAt(sequence: GlobeSequence, elapsed: number): GlobeFrame {
  return sequence === "approach" ? approachFrame(elapsed) : unrollFrame(elapsed);
}

/**
 * Half-width and half-height of the visible window in degrees, with a generous margin.
 *
 * Only used to decide what *not* to project, so an over-estimate costs a little work and an
 * under-estimate would drop a coastline that should be on screen. The margin is 3x.
 */
function visibleHalfDegrees(scale: number): [number, number] {
  const toDeg = 180 / Math.PI;
  const margin = 3;
  return [(VIEW_W / 2 / scale) * toDeg * margin, (VIEW_H / 2 / scale) * toDeg * margin];
}

/** Below this the whole world is in frame and culling would only cost time. */
const CULL_ABOVE_SCALE = 600;

/**
 * Adaptive-resampling threshold, in projected pixels, for the approach.
 *
 * d3 subdivides every segment until it is straighter than this, which is what makes a globe's
 * coastlines curve correctly - and what made the arrival act cost 100 to 300 ms a frame, because
 * at a scale of 32,000 a one-degree segment is 560 px long and gets subdivided the whole way.
 * The flatter the projection, the less resampling buys: past 0.9 the map is equirectangular and a
 * chord is the arc, so resampling is turned off outright. Measured 2026-09-19: mean over the
 * sequence 31 fps at a flat 2, 41-42 fps with this. M26 keeps 0.4 and is untouched.
 */
function approachPrecision(alpha: number): number {
  return alpha > 0.9 ? 0 : 2;
}

function inWindow(land: Land, centre: [number, number], scale: number): boolean {
  if (scale < CULL_ABOVE_SCALE) return true;
  const [halfLon, halfLat] = visibleHalfDegrees(scale);
  const [west, south, east, north] = land.bounds;
  if (north < centre[1] - halfLat || south > centre[1] + halfLat) return false;
  // Longitudes wrap; comparing the delta to 180 keeps a window straddling the antimeridian honest.
  const delta = Math.abs(((east + west) / 2 - centre[0] + 540) % 360) - 180;
  const span = (east - west) / 2;
  return Math.abs(delta) - span <= halfLon;
}

export interface GlobeIntroProps {
  /** Called once the sequence has finished, so the page can hand over to its live map. */
  onDone?: () => void;
  /** Skip the animation and render the finished frame (reduced motion). */
  still?: boolean;
  /** Which opening to play; "unroll" (M26) by default, so the landing hero is unchanged. */
  sequence?: GlobeSequence;
}

export function GlobeIntro(props: GlobeIntroProps) {
  // The landing hero's moving globe is drawn on a canvas; everything else - its finished frame
  // under reduced motion, and the whole of the dashboard's approach - stays the SVG below.
  if ((props.sequence ?? "unroll") === "unroll" && !props.still) {
    return <UnrollCanvas onDone={props.onDone} />;
  }
  return <GlobeSvg {...props} />;
}

/** The token colours the canvas paints with, read from the stylesheet (no literals). */
function readPalette(element: Element): CanvasPalette {
  const style = getComputedStyle(element);
  const token = (name: string) => style.getPropertyValue(name).trim();
  return {
    ink: token("--ink"),
    deep: token("--deep"),
    well: token("--well"),
    lineStrong: token("--line-strong"),
    tide: token("--tide"),
    text2: token("--text-2"),
    font: style.fontFamily,
  };
}

/**
 * The three token colours the Earth shader needs, or null when they cannot be read.
 *
 * Null is the ordinary case in a test environment, where `getPropertyValue` on a custom property
 * returns an empty string because no stylesheet has been applied; the caller then never creates a
 * WebGL context and the picture is the vector one. It is also the honest answer if the tokens ever
 * become a colour space the browser refuses to parse, and dropping the imagery beats painting the
 * planet in whatever `fillStyle` fell back to.
 */
function readEarthPalette(element: Element): EarthPalette | null {
  const style = getComputedStyle(element);
  const ink = cssColorToRgb(style.getPropertyValue("--ink"));
  const deep = cssColorToRgb(style.getPropertyValue("--deep"));
  const tide = cssColorToRgb(style.getPropertyValue("--tide"));
  if (!ink || !deep || !tide) return null;
  return { ink, deep, tide };
}

/**
 * Owns the photographic Earth behind one sequence: the texture's single decode, the WebGL2 context
 * on `canvasRef`, and the resize observer.
 *
 * `ready` is state rather than a ref because the SVG above has to re-render when the imagery
 * arrives - that is the frame on which the ocean disc and the country fills stop being drawn.
 * `paint` is a stable callback that does nothing at all until there is a painter, so the animation
 * loop can call it unconditionally.
 */
function useEarthLayer(
  hostRef: RefObject<HTMLElement | null>,
  canvasRef: RefObject<HTMLCanvasElement | null>,
): { ready: boolean; paint: (frame: GlobeFrame, photo: number) => void } {
  const painterRef = useRef<EarthPainter | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const host = hostRef.current;
    const canvas = canvasRef.current;
    if (!host || !canvas) return;
    let disposed = false;

    const palette = readEarthPalette(host);
    if (palette) {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      void ensureEarthImage()
        .then((image) => {
          if (disposed || !image) return;
          const painter = createEarthPainter(
            canvas,
            image,
            palette,
            canvas.clientWidth,
            canvas.clientHeight,
            dpr,
          );
          if (!painter) return;
          painterRef.current = painter;
          setReady(true);
        })
        .catch(() => undefined);
    }

    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() =>
            painterRef.current?.resize(canvas.clientWidth, canvas.clientHeight),
          );
    observer?.observe(canvas);

    return () => {
      disposed = true;
      observer?.disconnect();
      painterRef.current?.dispose();
      painterRef.current = null;
      setReady(false);
    };
  }, [canvasRef, hostRef]);

  const paint = useCallback((frame: GlobeFrame, photo: number) => {
    painterRef.current?.frame(frame, photo);
  }, []);

  return { ready, paint };
}

/** A painter for one canvas: in a worker where the browser allows it, on this thread where not. */
interface Painter {
  frame(frame: GlobeFrame): void;
  resize(width: number, height: number): void;
  dispose(): void;
}

function workerPainter(
  canvas: HTMLCanvasElement,
  palette: CanvasPalette,
  width: number,
  height: number,
  dpr: number,
): Painter | null {
  if (typeof Worker === "undefined" || !("transferControlToOffscreen" in canvas)) return null;
  let worker: Worker;
  try {
    worker = new Worker(new URL("./globe-worker.ts", import.meta.url), { type: "module" });
    const offscreen = canvas.transferControlToOffscreen();
    const init: GlobeWorkerMessage = {
      type: "init",
      canvas: offscreen,
      palette,
      topologyUrl: new URL(TOPOLOGY_110M, window.location.href).href,
      width,
      height,
      dpr,
    };
    worker.postMessage(init, [offscreen]);
  } catch {
    return null;
  }
  const post = (message: GlobeWorkerMessage) => worker.postMessage(message);
  return {
    frame: (frame) => post({ type: "frame", frame }),
    resize: (w, h) => post({ type: "resize", width: w, height: h }),
    dispose: () => worker.terminate(),
  };
}

function mainThreadPainter(
  canvas: HTMLCanvasElement,
  palette: CanvasPalette,
  width: number,
  height: number,
  dpr: number,
): Painter {
  const context = canvas.getContext("2d");
  const controller = new AbortController();
  let land: Land[] = [];
  let latest: GlobeFrame | null = null;
  let w = width;
  let h = height;
  const paint = () => {
    if (context && latest && w > 0 && h > 0) paintUnroll(context, palette, land, latest, w, h, dpr);
  };
  const size = () => {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  };
  size();
  void loadTopology(TOPOLOGY_110M, controller.signal)
    .then((shape) => {
      if (shape) {
        land = shape.land;
        paint();
      }
    })
    .catch(() => undefined);
  return {
    frame: (frame) => {
      latest = frame;
      paint();
    },
    resize: (nextW, nextH) => {
      w = nextW;
      h = nextH;
      size();
      paint();
    },
    dispose: () => controller.abort(),
  };
}

/**
 * M26 while it moves.
 *
 * **Why a canvas, and why a worker.** The SVG version rebuilt about 214,000 characters of path
 * data every frame - one `d` string per country, joined - and handed them to React to diff and to
 * the browser to parse. Measured in Node on this laptop, building those strings cost 23-38 ms a
 * frame against 7.5-10.5 ms for the projection itself, and on a phone at Lighthouse's 4x CPU
 * slowdown every frame became a 150-400 ms long task: the four-second intro alone put 3.9-5.2 s of
 * blocking time on the landing page. A canvas removes the strings, the diff and the parse; the
 * projection that is left still cost about 100 ms a frame at 4x on the main thread, so the canvas
 * is handed to `globe-worker.ts`, which projects and paints there. This thread computes only the
 * camera - `frameAt("unroll", t)`, a few multiplications - and posts it.
 *
 * Where a browser cannot transfer a canvas to a worker, the same painter runs here instead. The
 * geometry, the timing and the strokes are unchanged either way, and the finished frame reduced
 * motion shows is still the SVG.
 *
 * The canvas is created by the effect rather than rendered by React, because a canvas whose
 * control has been transferred can never be transferred again, and React re-runs effects on the
 * same element in development.
 */
function UnrollCanvas({ onDone }: { onDone?: () => void }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const earthRef = useRef<HTMLCanvasElement>(null);
  const onDoneRef = useRef(onDone);
  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  // The photograph, on its own canvas behind the vectors. Its `ready` is read through a ref by the
  // animation loop below, which is started once and must not be torn down when the texture lands.
  const earth = useEarthLayer(hostRef, earthRef);
  const earthRefState = useRef(earth);
  useEffect(() => {
    earthRefState.current = earth;
  });

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const canvas = document.createElement("canvas");
    canvas.className = "absolute inset-0 block h-full w-full";
    canvas.setAttribute("aria-hidden", "true");
    host.appendChild(canvas);

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const palette = readPalette(host);
    const painter =
      workerPainter(canvas, palette, canvas.clientWidth, canvas.clientHeight, dpr) ??
      mainThreadPainter(canvas, palette, canvas.clientWidth, canvas.clientHeight, dpr);

    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => painter.resize(canvas.clientWidth, canvas.clientHeight));
    observer?.observe(canvas);

    let raf = 0;
    let finished = false;
    const total = sequenceMs("unroll");
    const start = performance.now();
    const tick = (now: number) => {
      const elapsed = Math.min(now - start, total);
      const camera = frameAt("unroll", elapsed);
      const layer = earthRefState.current;
      const photo = photoAmount(camera, layer.ready);
      layer.paint(camera, photo);
      // The vector painter may be in a worker, so the hand-over rides on the frame itself.
      painter.frame(photo > 0 ? { ...camera, photo } : camera);
      if (elapsed < total) {
        raf = requestAnimationFrame(tick);
      } else if (!finished) {
        finished = true;
        onDoneRef.current?.();
      }
    };
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      observer?.disconnect();
      painter.dispose();
      canvas.remove();
    };
  }, []);

  return (
    <div
      ref={hostRef}
      className="relative h-full w-full"
      data-slot="globe-intro"
      data-sequence="unroll"
      role="img"
      aria-label="A globe unrolling into a world map, before the view settles on Mumbai"
    >
      <canvas
        ref={earthRef}
        aria-hidden="true"
        data-slot="globe-earth"
        className="absolute inset-0 block h-full w-full"
      />
    </div>
  );
}

function GlobeSvg({ onDone, still = false, sequence = "unroll" }: GlobeIntroProps) {
  const [world, setWorld] = useState<WorldShape | null>(null);
  /** The finer topology, once it has arrived; null means the approach runs on 110 m throughout. */
  const [fine, setFine] = useState<WorldShape | null>(null);
  const [elapsed, setElapsed] = useState(still ? sequenceMs(sequence) : 0);
  const done = useRef(false);
  const hostRef = useRef<HTMLDivElement>(null);
  const earthRef = useRef<HTMLCanvasElement>(null);
  const earth = useEarthLayer(hostRef, earthRef);

  useEffect(() => {
    const controller = new AbortController();
    void loadTopology(TOPOLOGY_110M, controller.signal)
      .then((shape) => {
        if (shape) setWorld(shape);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  // The 50 m coastline, for acts 2 and 3 only. The landing hero never asks for it, and a failure
  // here is not an error state: the sequence simply keeps the outline it already has.
  useEffect(() => {
    if (sequence !== "approach") return;
    const controller = new AbortController();
    void loadTopology(TOPOLOGY_50M, controller.signal)
      .then((shape) => {
        if (shape) setFine(shape);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [sequence]);

  // One animation frame loop drives the whole thing: `elapsed` is the only state it writes, and
  // every geometry below is a pure function of it.
  useEffect(() => {
    if (still) return;
    let raf = 0;
    const start = performance.now();
    const total = sequenceMs(sequence);
    const tick = (now: number) => {
      const since = now - start;
      setElapsed(Math.min(since, total));
      if (since < total) {
        raf = requestAnimationFrame(tick);
      } else if (!done.current) {
        done.current = true;
        onDone?.();
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [still, onDone, sequence]);

  const frame = useMemo(
    () => (still ? stillFrame(sequence) : frameAt(sequence, elapsed)),
    [elapsed, sequence, still],
  );

  const photo = photoAmount(frame, earth.ready);
  const dim = vectorOpacity(photo);

  // The photograph is painted from the same frame the geometry below is built from, in a layout
  // effect ordering that does not matter because both canvases and the SVG are composited by the
  // browser after this render commits. Under reduced motion `elapsed` never changes and this runs
  // once, for the one still frame, which is what section 8 asks for.
  const paintEarth = earth.paint;
  useEffect(() => {
    paintEarth(frame, photo);
  }, [frame, paintEarth, photo]);

  const { landPath, highlightPath, spherePath, aoiPath, point } = useMemo(() => {
    // Acts 2 and 3 draw the finer coastline once it is here; act 1 keeps the coarse one, so the
    // detail rises at an act boundary rather than popping mid-turn.
    const source = sequence === "approach" && frame.alpha > 0 && fine ? fine : world;

    const projection = morphProjection(frame.alpha)
      .scale(frame.scale)
      .translate([VIEW_W / 2, VIEW_H / 2])
      .rotate([-frame.centre[0], -frame.centre[1], 0])
      .precision(sequence === "approach" ? approachPrecision(frame.alpha) : 0.4);
    // Clipping in projected space is what keeps the arrival act affordable: a country outline is
    // cut to the SVG's own box before it is resampled into a path string. Only the approach needs
    // it - the unroll never leaves the viewBox - and M26 is left exactly as it was.
    if (sequence === "approach") {
      projection.clipExtent([
        [0, 0],
        [VIEW_W, VIEW_H],
      ]);
    }

    const path = geoPath(projection);
    const clean = (d: string | null) =>
      d && !d.includes("NaN") && !d.includes("Infinity") ? d : null;

    // Once the photograph carries the picture the plain countries reach no pixel, and projecting
    // them is the most expensive thing in this memo. The highlighted one is not decoration - it
    // is act 2 of M27 naming where the frame is going - so it is still built when it is asked for.
    const wantPlain = dim.landFill > 0.01 || dim.landStroke > 0.01;
    const lands = (source?.land ?? []).filter(
      (land) =>
        (wantPlain || (frame.highlight > 0 && land.name === HIGHLIGHT_NAME)) &&
        inWindow(land, frame.centre, frame.scale),
    );

    // Every country that is drawn the same way becomes one `d` string and therefore one element.
    // Two hundred and forty `<path>` nodes rebuilt sixty times a second is reconciliation work
    // the picture does not need: the countries do not overlap, so one path with many subpaths is
    // the same image, and the highlighted one is kept separate only because it is stroked
    // differently.
    const plain: string[] = [];
    const highlighted: string[] = [];
    for (const land of lands) {
      const d = clean(path(land.feature as never));
      if (!d) continue;
      (land.name === HIGHLIGHT_NAME ? highlighted : plain).push(d);
    }

    return {
      landPath: plain.length > 0 ? plain.join("") : null,
      highlightPath: highlighted.length > 0 ? highlighted.join("") : null,
      spherePath: clean(path({ type: "Sphere" })),
      aoiPath:
        frame.aoi > 0
          ? clean(
              path({
                type: "Polygon",
                coordinates: [
                  [
                    [AOI.west, AOI.south],
                    [AOI.east, AOI.south],
                    [AOI.east, AOI.north],
                    [AOI.west, AOI.north],
                    [AOI.west, AOI.south],
                  ],
                ],
              } as never),
            )
          : null,
      point: projection([MUMBAI_LON, MUMBAI_LAT]),
    };
  }, [dim.landFill, dim.landStroke, fine, frame, sequence, world]);

  // The dashboard's globe is decoration in front of a map the reader is waiting for, and its
  // skip button is the real control (UI_SPEC 10), so the canvas is hidden from assistive
  // technology there. The landing hero's globe is the page's only picture, so it keeps its label.
  const labelling =
    sequence === "approach"
      ? ({ "aria-hidden": true } as const)
      : ({
          role: "img",
          "aria-label": "A globe unrolling into a world map, before the view settles on Mumbai",
        } as const);

  return (
    <div ref={hostRef} className="relative h-full w-full">
      {/* The photograph. It is behind the SVG rather than inside it because a fragment shader
          cannot run in an SVG, and because the two are composited by the browser at exactly the
          same `xMidYMid slice` fit - the painter reproduces that fit from the same view box. */}
      <canvas
        ref={earthRef}
        aria-hidden="true"
        data-slot="globe-earth"
        className="absolute inset-0 block h-full w-full"
      />
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        className="absolute inset-0 h-full w-full"
        // `slice`, not `meet`: this is a full-bleed hero, and `meet` fits the 900 x 560 box
        // *inside* the frame, so any viewport that is not 1.607:1 - which is every 16:9 screen -
        // got `--ink` down the sides. Cover crops the box instead. The globe is centred and its
        // 380-unit diameter still clears the visible height at every desktop aspect; a portrait
        // phone crops its limb, which is the trade for a hero that fills the page.
        preserveAspectRatio="xMidYMid slice"
        data-slot="globe-intro"
        data-sequence={sequence}
        data-photo={photo > 0 ? "true" : "false"}
        {...labelling}
      >
        {/* The ocean inside the sphere: `--deep`, the same panel colour the console uses, so the
            globe reads as part of the product rather than as an illustration bolted on. With the
            photograph behind, the fill would hide an ocean that is already there, so it goes and
            only the limb stroke stays. */}
        {spherePath ? (
          <path
            d={spherePath}
            fill={dim.sphereFill > 0.01 ? "var(--deep)" : "none"}
            fillOpacity={dim.sphereFill}
            stroke="var(--line-strong)"
            strokeOpacity={dim.sphereStroke}
            strokeWidth={1}
          />
        ) : null}
        {landPath ? (
          <path
            d={landPath}
            fill={dim.landFill > 0.01 ? "var(--well)" : "none"}
            fillOpacity={dim.landFill}
            stroke="var(--line-strong)"
            strokeOpacity={dim.landStroke}
            strokeWidth={0.6}
          />
        ) : null}
        {highlightPath ? (
          <path
            data-slot="globe-highlight"
            d={highlightPath}
            fill={dim.landFill > 0.01 ? "var(--well)" : "none"}
            fillOpacity={dim.landFill}
            // The highlight is the one stroke the photograph cannot make: it says *this* country,
            // which is a claim about the demo and not about the Earth. It keeps its full weight.
            stroke={frame.highlight > 0 ? "var(--text-2)" : "var(--line-strong)"}
            strokeWidth={0.6 + 1.2 * frame.highlight}
          />
        ) : null}
        {aoiPath ? (
          <path
            d={aoiPath}
            fill="none"
            stroke="var(--tide)"
            strokeWidth={1.2}
            opacity={frame.aoi}
            data-slot="globe-aoi"
          />
        ) : null}
        <MumbaiMark point={point} frame={frame} />
      </svg>
    </div>
  );
}

function MumbaiMark({ point, frame }: { point: [number, number] | null; frame: GlobeFrame }) {
  if (!point || !Number.isFinite(point[0]) || !Number.isFinite(point[1])) return null;
  const [x, y] = point;
  // Held back until the globe has turned to face the city, then grown with the flattening. In the
  // approach the ring shrinks away again once the AOI box takes over as the subject.
  const opacity = Math.min(Math.max(frame.alpha * 2, 0), 1) * (1 - frame.aoi);
  if (opacity <= 0.01) return null;
  const grow = frame.alpha;

  // The same `--ink` halo the canvas mark carries (globe-paint.ts), so the mark reads on the
  // photograph in the light theme as well as the dark.
  return (
    <g opacity={opacity}>
      <circle cx={x} cy={y} r={3} fill="var(--tide)" stroke="var(--ink)" strokeWidth={3} paintOrder="stroke" />
      <g opacity={0.7} fill="none">
        <circle cx={x} cy={y} r={6 + 14 * grow} stroke="var(--ink)" strokeWidth={3.2} />
        <circle cx={x} cy={y} r={6 + 14 * grow} stroke="var(--tide)" strokeWidth={1.2} />
      </g>
      <text
        x={x + 12 + 14 * grow}
        y={y + 4}
        className="num fill-[var(--text-2)]"
        fontSize={11}
        stroke="var(--ink)"
        strokeWidth={3}
        strokeLinejoin="round"
        paintOrder="stroke"
      >
        Mumbai
      </text>
    </g>
  );
}

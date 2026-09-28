/// <reference lib="webworker" />
/**
 * Paints the landing hero's moving globe (M26) off the main thread.
 *
 * The page keeps the camera - it computes `frameAt("unroll", t)` each animation frame, which is a
 * few multiplications - and posts the result here. This worker owns the canvas (transferred with
 * `transferControlToOffscreen`), the topology and the projection, and paints the newest frame it
 * has been sent. Frames that arrive while one is being painted are coalesced to the latest, so a
 * slow device draws fewer frames of the same four seconds rather than falling behind.
 *
 * Why it exists: projecting and stroking the 110 m world costs 7-25 ms a frame on this laptop,
 * which Lighthouse multiplies by four for its phone and counts as blocking time on every frame.
 * In a worker none of it is on the main thread.
 */

import {
  loadTopology,
  paintUnroll,
  type CanvasPalette,
  type GlobeFrame,
  type GlobeWorkerMessage,
  type Land,
} from "@/components/landing/globe-paint";

const scope = self as unknown as DedicatedWorkerGlobalScope;

let canvas: OffscreenCanvas | null = null;
let context: OffscreenCanvasRenderingContext2D | null = null;
let palette: CanvasPalette | null = null;
let land: Land[] = [];
let width = 0;
let height = 0;
let dpr = 1;
let latest: GlobeFrame | null = null;
let scheduled = false;

function paint(): void {
  scheduled = false;
  if (!context || !palette || !latest || width === 0 || height === 0) return;
  paintUnroll(context, palette, land, latest, width, height, dpr);
}

/** Paint on the worker's own animation frame where it has one, and at once where it does not. */
function schedule(): void {
  if (scheduled) return;
  scheduled = true;
  if (typeof scope.requestAnimationFrame === "function") {
    scope.requestAnimationFrame(paint);
  } else {
    setTimeout(paint, 0);
  }
}

function size(nextWidth: number, nextHeight: number): void {
  width = nextWidth;
  height = nextHeight;
  if (canvas) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
}

scope.onmessage = (event: MessageEvent<GlobeWorkerMessage>) => {
  const message = event.data;
  if (message.type === "init") {
    canvas = message.canvas;
    context = canvas.getContext("2d");
    palette = message.palette;
    dpr = message.dpr;
    size(message.width, message.height);
    void loadTopology(message.topologyUrl, new AbortController().signal)
      .then((shape) => {
        if (shape) {
          land = shape.land;
          schedule();
        }
      })
      .catch(() => undefined);
  } else if (message.type === "resize") {
    size(message.width, message.height);
    schedule();
  } else {
    latest = message.frame;
    schedule();
  }
};

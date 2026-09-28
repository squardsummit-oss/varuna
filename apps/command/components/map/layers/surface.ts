/**
 * The light half of the photorealistic 3D mode: everything the console needs on every load.
 *
 * `photoreal.ts` builds Google's tileset and imports loaders.gl's 3D Tiles parser and Draco with
 * it - 530 KB of JavaScript parsed on `/`, `/console` and `/map` for a mode that is off by
 * default and that two of those routes cannot switch on (P10.4). What is here is what the map
 * needs whether or not 3D is ever pressed: the camera pitch, the extension that drapes a layer
 * onto the ground, the hide-don't-drop rule for the flat layers, and the probe that decides where
 * tiles will be decoded, which has to start before the tileset exists. `city-map.tsx` imports
 * this statically and `photoreal.ts` only when 3D is asked for.
 */

import { _TerrainExtension as TerrainExtension } from "@deck.gl/extensions";

/**
 * SPEC.md 6.7's 3D camera: "pitch 55 degrees".
 *
 * There is no exaggeration constant here, unlike `terrain.ts`'s 2x. That existed because a 30 m
 * DEM of a coastal plain is nearly flat and needed help to read as terrain; Google's tiles carry
 * buildings at their real heights, and stretching those would make the water's depth a lie about
 * a city the reader can recognise.
 */
export const PHOTOREAL_PITCH = 55;

/** Where the app serves the loaders.gl worker bundles from. */
export const LOCAL_WORKER_BASE = "/workers";

/**
 * The bundles `scripts/copy-loader-workers.mjs` publishes, keyed by the loaders.gl worker id that
 * names each one's `workerUrl` option.
 *
 * Exactly two, and the list came from the parser rather than the documentation: the glTF loader
 * reaches `DracoLoader` from `KHR_draco_mesh_compression.js` and parses every image with
 * `[ImageLoader, BasisLoader]`, and nothing else on the 3D Tiles path declares `worker: true`.
 */
export const LOCAL_WORKER_FILES = {
  draco: "draco-worker.js",
  basis: "basis-worker.js",
} as const;

/** Whether tile geometry and textures are decoded on a worker this app serves, or in place. */
export type PhotorealWorkers = "off" | "local";

/**
 * One probe per page, and a synchronous answer for the render path to read.
 *
 * `verified` only ever goes from false to true: a refusal means the copy step did not run, which
 * a reload cannot change, and re-probing on every layer build would put a fetch inside deck's
 * render loop.
 */
let workerProbe: Promise<boolean> | null = null;
let workersVerified = false;

/** Whether this app is actually serving both worker bundles. False until the probe says so. */
export function localWorkersVerified(): boolean {
  return workersVerified;
}

/** Forget the probe. For tests; the application has no reason to call it. */
export function resetLocalWorkerProbe(): void {
  workerProbe = null;
  workersVerified = false;
}

/**
 * Ask this origin, once, whether both worker bundles are really being served.
 *
 * A HEAD rather than a GET, because the question is whether the path resolves and not what is at
 * it - Next answers HEAD on a `public/` file with the same status and content type it answers GET
 * with, and 404s a missing one (measured against the dev server on 2026-09-23). A thrown fetch is
 * treated exactly like a 404: both mean the worker would fail to load, and the honest response to
 * either is to decode in place.
 *
 * `fetchImpl` is the seam the tests drive; production passes nothing and gets the platform's.
 */
export async function verifyLocalWorkers(fetchImpl: typeof fetch = fetch): Promise<boolean> {
  workerProbe ??= (async () => {
    try {
      const answers = await Promise.all(
        Object.values(LOCAL_WORKER_FILES).map((file) =>
          fetchImpl(`${LOCAL_WORKER_BASE}/${file}`, { method: "HEAD" }),
        ),
      );
      return answers.every((answer) => answer.ok);
    } catch {
      // No network stack to this origin at all. Decoding in place still works.
      return false;
    }
  })();
  workersVerified = await workerProbe;
  return workersVerified;
}

/** The one extension every other layer gets in 3D, so it lies on the ground instead of under it. */
export const SURFACE_EXTENSION = new TerrainExtension();

/**
 * Put a layer on the photorealistic surface.
 *
 * Carried over from `terrain.ts`'s `onTerrain`, including the id-suffix fix, which is a real bug
 * and not a style choice: **a new id, so deck builds the layer fresh with the terrain shader
 * module in it. A clone under the flat layer's id keeps the flat layer's compiled shaders, which
 * have no `terrain_map` binding: luma warns once per layer and the layer draws under the ground.**
 *
 * Unlike `onTerrain` this keeps `depth-raster` and drapes it. There, the depth frame *was* the
 * ground's texture; here the ground is a photograph of Mumbai that has never heard of the storm,
 * so dropping the raster would drape a dry city over a flooded one.
 */
export function onSurface(layers: readonly unknown[]): unknown[] {
  return layers.map((layer) => {
    const l = layer as {
      props: { extensions?: unknown[]; id?: string };
      clone: (p: object) => unknown;
    };
    return l.clone({
      id: `${l.props.id ?? "layer"}-3d`,
      extensions: [...(l.props.extensions ?? []), SURFACE_EXTENSION],
    });
  });
}

/**
 * The flat map kept alive but not drawn while 3D is on, so leaving 3D never re-creates it.
 *
 * Carried over unchanged from `terrain.ts`, where the reason was measured: dropped layers are
 * finalised, and re-creating them on the way out of 3D tore the terrain effect down in the same
 * frame - an assertion per layer and a wave of WebGL errors. Hidden, they are never
 * re-initialised, and the satellite basemap keeps its tile cache.
 */
export function hiddenLayers(layers: readonly unknown[]): unknown[] {
  return layers.map((layer) =>
    (layer as { clone: (p: object) => unknown }).clone({ visible: false }),
  );
}

import { beforeEach, describe, expect, it, vi } from "vitest";

import { PHOTOREAL_TILESET_URL, type PhotorealState } from "@/lib/maps/photoreal";
import {
  collectTileCredits,
  hiddenLayers,
  LOCAL_WORKER_BASE,
  LOCAL_WORKER_FILES,
  localWorkersVerified,
  onSurface,
  PHOTOREAL_LAYER_ID,
  photorealLayers,
  photorealLoadOptions,
  resetLocalWorkerProbe,
  SURFACE_EXTENSION,
  verifyLocalWorkers,
} from "../photoreal";

const READY: PhotorealState = { kind: "ready" };
const DISABLED: PhotorealState = {
  kind: "unavailable",
  reason: "api-disabled",
  message: "anything",
};

/** A stand-in for a deck layer: the two things `onSurface` and `hiddenLayers` touch. */
function fakeLayer(id: string, extensions: unknown[] = []) {
  const layer = {
    props: { id, extensions } as Record<string, unknown>,
    clone(patch: object) {
      return { ...layer, props: { ...layer.props, ...patch } };
    },
  };
  return layer;
}

type BuiltLayer = { props: Record<string, unknown> };

/** A fetch that answers HEAD for the named files and 404s everything else. */
function serving(...files: string[]) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    void init;
    return files.some((file) => String(input).endsWith(file))
      ? ({ ok: true, status: 200 } as Response)
      : ({ ok: false, status: 404 } as Response);
  });
}

const BOTH_WORKERS = Object.values(LOCAL_WORKER_FILES);

beforeEach(() => {
  // The probe is one-per-page module state on purpose, so each test has to start from nothing.
  resetLocalWorkerProbe();
});

describe("photorealLoadOptions", () => {
  it("carries the key in a header and never in a URL", () => {
    const options = photorealLoadOptions("test-key");
    expect(options.fetch.headers).toEqual({ "X-GOOG-API-KEY": "test-key" });
    expect(JSON.stringify(options)).not.toContain("?key=");
  });

  it("decodes in place until the local workers have been seen, never guessing they are there", async () => {
    // The dangerous version of this module names a `workerUrl` it has not checked. loaders.gl's
    // `parseWithLoader` has no try/catch around `parseWithWorker`, so a 404 there rejects the
    // parse and the tile never appears - strictly worse than a slow decode.
    expect(localWorkersVerified()).toBe(false);
    expect(photorealLoadOptions("test-key", "local")).toMatchObject({ core: { worker: false } });

    await verifyLocalWorkers(serving(...BOTH_WORKERS));
    expect(photorealLoadOptions("test-key", "local")).not.toHaveProperty("core");
  });

  it("decodes in place when asked to, which keeps unpkg out of the loop by construction", () => {
    // `core.worker: false` means loaders.gl never reaches `getWorkerURL`, so the CDN branch that
    // would produce `https://unpkg.com/@loaders.gl/...` is never evaluated at all.
    expect(photorealLoadOptions("test-key", "off")).toMatchObject({ core: { worker: false } });
    expect(JSON.stringify(photorealLoadOptions("test-key", "off"))).not.toContain("unpkg");
  });

  it("points Draco and Basis at this app's own workers once they answer, still never at a CDN", async () => {
    await verifyLocalWorkers(serving(...BOTH_WORKERS));
    const options = photorealLoadOptions("test-key", "local");
    const serialised = JSON.stringify(options);
    expect(serialised).toContain(`${LOCAL_WORKER_BASE}/${LOCAL_WORKER_FILES.draco}`);
    expect(serialised).toContain(`${LOCAL_WORKER_BASE}/${LOCAL_WORKER_FILES.basis}`);
    expect(serialised).not.toContain("unpkg");
    expect(serialised).not.toContain("gstatic");
    // Every worker URL is origin-relative, so none of them can name another host.
    expect(serialised).not.toContain("http");
    // Naming a worker URL is itself what suppresses the generated one, so `worker: false` would
    // be redundant here - and would throw the workers away again.
    expect(options).not.toHaveProperty("core");
  });

  it("still decodes in place when asked for the local workers after they answered", async () => {
    await verifyLocalWorkers(serving(...BOTH_WORKERS));
    expect(photorealLoadOptions("test-key", "off")).toMatchObject({ core: { worker: false } });
  });
});

describe("verifyLocalWorkers", () => {
  it("HEADs every bundle at this origin and nothing else", async () => {
    const fetchImpl = serving(...BOTH_WORKERS);
    await expect(verifyLocalWorkers(fetchImpl)).resolves.toBe(true);

    const asked = fetchImpl.mock.calls.map(([url]) => String(url));
    expect(asked.sort()).toEqual(BOTH_WORKERS.map((f) => `${LOCAL_WORKER_BASE}/${f}`).sort());
    for (const [, init] of fetchImpl.mock.calls) expect(init).toEqual({ method: "HEAD" });
    // Relative paths: this origin, whatever it is, and never a CDN.
    for (const url of asked) expect(url.startsWith("/")).toBe(true);
  });

  it("falls back to decoding in place when a bundle 404s, rather than losing the tile", async () => {
    // The copy step was never run, or only half of it landed. This is the case the whole probe
    // exists for: `scripts/copy-loader-workers.mjs` not having run must cost a slower decode and
    // nothing else.
    const fetchImpl = serving(LOCAL_WORKER_FILES.draco);
    await expect(verifyLocalWorkers(fetchImpl)).resolves.toBe(false);
    expect(localWorkersVerified()).toBe(false);
    expect(photorealLoadOptions("k", "local")).toMatchObject({ core: { worker: false } });
  });

  it("falls back to decoding in place when the fetch throws", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(verifyLocalWorkers(fetchImpl as unknown as typeof fetch)).resolves.toBe(false);
    expect(photorealLoadOptions("k", "local")).toMatchObject({ core: { worker: false } });
  });

  it("probes once per page, not once per layer build", async () => {
    const fetchImpl = serving(...BOTH_WORKERS);
    await Promise.all([
      verifyLocalWorkers(fetchImpl),
      verifyLocalWorkers(fetchImpl),
      verifyLocalWorkers(fetchImpl),
    ]);
    // Two calls: one per bundle, from the single cached probe.
    expect(fetchImpl).toHaveBeenCalledTimes(BOTH_WORKERS.length);
  });
});

describe("photorealLayers", () => {
  it("builds nothing without a key", () => {
    expect(photorealLayers({ key: null, state: READY })).toEqual([]);
  });

  it("builds nothing while the Map Tiles API is disabled, rather than requesting refused tiles", () => {
    expect(photorealLayers({ key: "test-key", state: DISABLED })).toEqual([]);
    expect(photorealLayers({ key: "test-key", state: { kind: "loading" } })).toEqual([]);
    expect(photorealLayers({ key: "test-key", state: { kind: "off" } })).toEqual([]);
  });

  it("starts the worker probe on the first build, before there is a tileset to decode", async () => {
    // Deliberately on a state that builds nothing: this is the render that happens while
    // `usePhotorealTileset` is still waiting on tile.googleapis.com, and a same-origin HEAD
    // started here has settled long before that round trip does.
    const fetchImpl = serving(...BOTH_WORKERS);
    vi.stubGlobal("fetch", fetchImpl);
    try {
      expect(photorealLayers({ key: "k", state: { kind: "loading" } })).toEqual([]);
      expect(fetchImpl).toHaveBeenCalledTimes(BOTH_WORKERS.length);
      await vi.waitFor(() => expect(localWorkersVerified()).toBe(true));

      // And it is not re-probed on every subsequent build.
      photorealLayers({ key: "k", state: READY });
      photorealLayers({ key: "k", state: READY });
      expect(fetchImpl).toHaveBeenCalledTimes(BOTH_WORKERS.length);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("does not probe at all when the caller asked for in-place decoding", () => {
    const fetchImpl = serving(...BOTH_WORKERS);
    vi.stubGlobal("fetch", fetchImpl);
    try {
      photorealLayers({ key: "k", state: READY, workers: "off" });
      expect(fetchImpl).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("builds a tileset whose decode never names a CDN, verified or not", async () => {
    const unverified = photorealLayers({ key: "k", state: READY, workers: "off" }) as BuiltLayer[];
    expect(JSON.stringify(unverified[0].props.loadOptions)).not.toContain("unpkg");

    await verifyLocalWorkers(serving(...BOTH_WORKERS));
    const verified = photorealLayers({ key: "k", state: READY }) as BuiltLayer[];
    const serialised = JSON.stringify(verified[0].props.loadOptions);
    expect(serialised).toContain(`${LOCAL_WORKER_BASE}/${LOCAL_WORKER_FILES.draco}`);
    expect(serialised).not.toContain("unpkg");
  });

  it("builds one tileset that declares itself the ground", () => {
    const layers = photorealLayers({ key: "test-key", state: READY }) as BuiltLayer[];
    expect(layers).toHaveLength(1);
    expect(layers[0].props.id).toBe(PHOTOREAL_LAYER_ID);
    expect(layers[0].props.data).toBe(PHOTOREAL_TILESET_URL);
    // Without this, deck's terrain extension has nothing to drape the streets onto.
    expect(layers[0].props.operation).toBe("terrain+draw");
    expect(layers[0].props.pickable).toBe(false);
  });

  it("transitions opacity only when a duration is given (motion row M28)", () => {
    const cut = photorealLayers({ key: "k", state: READY, opacity: 0.2 }) as BuiltLayer[];
    expect(cut[0].props.opacity).toBe(0.2);
    expect(cut[0].props.transitions).toEqual({});

    const faded = photorealLayers({
      key: "k",
      state: READY,
      opacity: 0.2,
      fadeMs: 300,
    }) as BuiltLayer[];
    expect(faded[0].props.transitions).toEqual({ opacity: 300 });
  });

  it("harvests the on-screen tiles' credits every traversal and hands the list back unchanged", () => {
    const onCredits = vi.fn();
    const layers = photorealLayers({ key: "k", state: READY, onCredits }) as BuiltLayer[];

    const tileset = { options: {} as Record<string, unknown> };
    (layers[0].props.onTilesetLoad as (t: unknown) => void)(tileset);

    const traversal = tileset.options.onTraversalComplete as (
      t: readonly unknown[],
    ) => readonly unknown[];
    expect(typeof traversal).toBe("function");

    const selected = [
      { content: { gltf: { asset: { copyright: "Airbus; Maxar Technologies" } } } },
      { content: { gltf: { asset: { copyright: "Airbus" } } } },
    ];
    // The tileset draws what this returns, so returning anything but the same list would change
    // the picture.
    expect(traversal(selected)).toBe(selected);
    expect(onCredits).toHaveBeenCalledWith(["Airbus; Maxar Technologies", "Airbus"]);
  });

  it("does not reach into a tileset that has no options object", () => {
    const layers = photorealLayers({
      key: "k",
      state: READY,
      onCredits: vi.fn(),
    }) as BuiltLayer[];
    expect(() => (layers[0].props.onTilesetLoad as (t: unknown) => void)(null)).not.toThrow();
  });
});

describe("collectTileCredits", () => {
  it("skips tiles whose content has not arrived, which are not on screen yet", () => {
    expect(
      collectTileCredits([
        { content: null },
        {},
        { content: { gltf: {} } },
        { content: { gltf: { asset: {} } } },
        { content: { gltf: { asset: { copyright: "   " } } } },
        { content: { gltf: { asset: { copyright: "Airbus" } } } },
      ]),
    ).toEqual(["Airbus"]);
  });

  it("survives a null in the selected set", () => {
    expect(collectTileCredits([null, undefined])).toEqual([]);
  });
});

describe("onSurface", () => {
  it("drapes the depth raster rather than dropping it", () => {
    // The change from `terrain.ts`'s `onTerrain`. There the depth frame was the ground's own
    // texture, so draping it again drew the water twice; here the ground is a photograph that has
    // never heard of the storm, and dropping the raster would show a dry city in a cloudburst.
    const draped = onSurface([fakeLayer("depth-raster")]) as BuiltLayer[];
    expect(draped).toHaveLength(1);
    expect(draped[0].props.id).toBe("depth-raster-3d");
    expect(draped[0].props.extensions).toContain(SURFACE_EXTENSION);
  });

  it("keeps every layer it is given, one for one", () => {
    const ids = ["streets", "depth-raster", "surcharge", "routes"];
    const draped = onSurface(ids.map((id) => fakeLayer(id))) as BuiltLayer[];
    expect(draped.map((l) => l.props.id)).toEqual(ids.map((id) => `${id}-3d`));
  });

  it("gives each clone a new id, so deck compiles the terrain shader into it", () => {
    // Not cosmetic: a clone under the flat layer's id keeps the flat layer's compiled shaders,
    // which have no `terrain_map` binding, and the layer then draws under the ground.
    const [draped] = onSurface([fakeLayer("streets")]) as BuiltLayer[];
    expect(draped.props.id).not.toBe("streets");
  });

  it("adds the extension to whatever a layer already carries", () => {
    const existing = { name: "dash" };
    const [draped] = onSurface([fakeLayer("reversed-flow", [existing])]) as BuiltLayer[];
    expect(draped.props.extensions).toEqual([existing, SURFACE_EXTENSION]);
  });
});

describe("hiddenLayers", () => {
  it("hides the flat map without finalising it, so leaving 3D costs nothing", () => {
    const hidden = hiddenLayers([fakeLayer("streets"), fakeLayer("satellite")]) as BuiltLayer[];
    expect(hidden.map((l) => l.props.visible)).toEqual([false, false]);
    // Same ids: these are the flat layers, still alive, not new ones.
    expect(hidden.map((l) => l.props.id)).toEqual(["streets", "satellite"]);
  });
});

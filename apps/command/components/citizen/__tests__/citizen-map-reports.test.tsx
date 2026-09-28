/**
 * The citizen map hands the forecast step and the citizens' reports to every one of its three
 * render paths: Google's basemap with deck laid over it, VARUNA's own map when Google is not
 * there, and the photographed city in 3D. A report that shows on one path and not another would
 * be a report the ward officer sees and the citizen does not.
 */

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ReportPin } from "@/lib/api/reports";
import type { CitizenRunState } from "@/lib/maps/citizen-run";

// ---- What each path is handed -------------------------------------------------------------

/** Props of every `CityMap` render: the fallback and 3D paths. */
const cityMapProps: Record<string, unknown>[] = [];
/** Layers of every `useGoogleDeckOverlay` call: the Google path. */
const overlayLayers: unknown[][] = [];
/** The Google map's own click handler, so a test can tap the basemap. */
let googleClick: ((event: unknown) => void) | undefined;
/** Every `pickObject` call the map's click made on the deck overlay. */
const picks: { x: number; y: number; radius?: number; layerIds?: string[] }[] = [];
/** Where each report's pin is drawn on screen, in container pixels, for the fake overlay. */
const PIN_PIXELS: Record<string, { x: number; y: number }> = {
  "seed-hindmata-1": { x: 477, y: 628 },
  "rpt-1789225538684-a1b2c3": { x: 612, y: 402 },
};
/** The overlay the map's factory built, one per test. */
let builtOverlay: unknown;

vi.mock("@/components/map/city-map", () => ({
  CityMap: (props: Record<string, unknown>) => {
    cityMapProps.push(props);
    return <div data-testid="varuna-map" />;
  },
}));

/** Whether the fake `APIProvider` reports Google as loaded. */
const apiLoads = vi.hoisted(() => ({ value: false }));

/** The live Google map the hooks are handed, with the camera calls a focus makes. */
const googleMap = vi.hoisted(() => ({
  panTo: vi.fn(),
  setCenter: vi.fn(),
  setZoom: vi.fn(),
  getZoom: vi.fn((): number | undefined => 12),
}));

vi.mock("@vis.gl/react-google-maps", () => {
  const map = googleMap;
  return {
    APIProvider: ({ children, onLoad }: { children: ReactNode; onLoad?: () => void }) => {
      // Google "loads" only in the tests that ask for it, so the others keep the bootstrap timer.
      if (apiLoads.value && onLoad) queueMicrotask(onLoad);
      return <>{children}</>;
    },
    Map: ({
      children,
      onClick,
      zoomControlOptions,
    }: {
      children: ReactNode;
      onClick?: (e: unknown) => void;
      zoomControlOptions?: { position?: number };
    }) => {
      googleClick = onClick;
      return (
        <div
          data-testid="google-map"
          data-zoom-position={String(zoomControlOptions?.position ?? "")}
        >
          {children}
        </div>
      );
    },
    useMap: () => map,
  };
});

vi.mock("@/lib/maps/overlay", () => ({
  useGoogleDeckOverlay: (
    _map: unknown,
    layers: readonly unknown[],
    factory?: (options: { interleaved: boolean }) => unknown,
  ) => {
    overlayLayers.push([...layers]);
    // The map must hand its own factory over, or its click has nothing to ask.
    if (factory && !builtOverlay) builtOverlay = factory({ interleaved: false });
  },
}));

/**
 * Deck's picking, faked the way deck does it: the top pickable object of a layer whose id starts
 * with one of `layerIds`, within the radius of the pixel it is drawn at.
 */
vi.mock("@deck.gl/google-maps", () => ({
  GoogleMapsOverlay: class {
    pickObject(options: { x: number; y: number; radius?: number; layerIds?: string[] }) {
      picks.push(options);
      const layers = (overlayLayers.at(-1) ?? []) as {
        id: string;
        props: { pickable?: boolean; data?: { id: string }[] };
      }[];
      for (const layer of [...layers].reverse()) {
        if (!layer.props.pickable) continue;
        if (options.layerIds && !options.layerIds.some((id) => layer.id.startsWith(id))) continue;
        for (const object of layer.props.data ?? []) {
          const at = PIN_PIXELS[object.id];
          if (at && Math.hypot(at.x - options.x, at.y - options.y) <= 6 + (options.radius ?? 0)) {
            return { object, layer };
          }
        }
      }
      return null;
    }
  },
}));

vi.mock("@/lib/maps/fit", () => ({
  toLatLngBounds: () => ({ north: 19.135, south: 18.995, east: 72.905, west: 72.815 }),
  useGoogleFit: () => {},
}));

vi.mock("@/lib/maps/photoreal", () => ({
  // Ready whenever it is asked for: the probe's own answers are tested in `citizen-map.test.tsx`.
  usePhotorealTileset: (enabled: boolean) => (enabled ? { kind: "ready" } : { kind: "off" }),
}));

vi.mock("@/lib/api/city-layers", () => ({
  loadFacilityLabels: () => Promise.resolve([]),
}));

/** One wet street at Hindmata, 10 cm now and 40 cm an hour on. */
const READY_RUN: CitizenRunState = {
  kind: "ready",
  run: {
    provenance: {
      runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.3-baked",
      cycleTs: "2019-07-02T08:40:00+05:30",
      mode: "baked",
      bundle: "MUM-2019-07-02",
      nSteps: 36,
      ensembleN: 50,
    },
    bounds: [72.815, 18.995, 72.905, 19.135],
    segments: [
      {
        id: "S-hindmata",
        path: [
          [72.8405, 19.0115],
          [72.8415, 19.0125],
        ],
        width: 3,
        depthCm: Array.from({ length: 36 }, (_, i) => (i < 12 ? 10 : 40)),
      },
    ] as never,
    baseSegments: [],
    depthCm: new Map(),
    validTs: [],
  },
};

vi.mock("@/lib/maps/citizen-run", () => ({
  useCitizenRun: () => READY_RUN,
}));

// Imported after the mocks so the map binds to them.
const { CitizenMap } = await import("../citizen-map");

const KEY = "NEXT_PUBLIC_GOOGLE_MAPS_API_KEY";

const reports: ReportPin[] = [
  {
    id: "seed-hindmata-1",
    lon: 72.841,
    lat: 19.012,
    depthHint: "knee",
    depthCm: 45,
    status: "crew_sent",
    statusLabel: "Crew sent (demo status)",
    statusSeeded: true,
    ts: "2019-07-02T08:47:00+05:30",
    text: null,
    place: "Hindmata junction",
    thumbUrl:
      "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a8/Bombay_flooded_street.jpg/330px-Bombay_flooded_street.jpg",
    photoUrl: null,
    credit: "Photo: Hitesh Ashar, CC BY 2.0, via Wikimedia Commons",
    synthetic: true,
    origin: "seed",
  },
  {
    id: "rpt-1789225538684-a1b2c3",
    lon: 72.857,
    lat: 19.027,
    depthHint: "ankle",
    depthCm: 10,
    status: "received",
    statusLabel: "Received",
    statusSeeded: false,
    ts: "2019-07-02T08:52:00+05:30",
    text: "Water at the bus stop outside Maheshwari Udyan",
    place: "King's Circle",
    thumbUrl: null,
    photoUrl: null,
    credit: null,
    synthetic: false,
    origin: "citizen",
  },
];

interface LayerLike {
  id: string;
  props: {
    data?: { id: string }[];
    updateTriggers?: { getColor?: unknown[] };
    onClick?: (info: { object?: unknown }) => boolean;
  };
}

function layerById(layers: unknown[], id: string): LayerLike {
  const found = (layers as LayerLike[]).find((l) => l.id === id);
  if (!found) throw new Error(`no layer ${id} in ${(layers as LayerLike[]).map((l) => l.id)}`);
  return found;
}

beforeEach(() => {
  cityMapProps.length = 0;
  overlayLayers.length = 0;
  picks.length = 0;
  builtOverlay = undefined;
  googleClick = undefined;
  googleMap.panTo.mockClear();
  googleMap.setCenter.mockClear();
  googleMap.setZoom.mockClear();
  googleMap.getZoom.mockClear();
  googleMap.getZoom.mockImplementation(() => 12);
  apiLoads.value = false;
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("CitizenMap on Google's basemap", () => {
  it("colours the streets at the step it is given and lays the report pins over them", () => {
    vi.stubEnv(KEY, "test-key");
    render(
      <CitizenMap
        profile="car"
        step={12}
        reports={reports}
        selectedReportId="seed-hindmata-1"
        onPickReport={() => {}}
      />,
    );

    expect(screen.getByTestId("google-map")).toBeInTheDocument();
    const layers = overlayLayers.at(-1) ?? [];
    expect(layerById(layers, "streets-wet").props.updateTriggers?.getColor?.[0]).toBe(12);
    expect(layerById(layers, "report-pins").props.data?.map((d) => d.id)).toEqual([
      "seed-hindmata-1",
      "rpt-1789225538684-a1b2c3",
    ]);
    expect(layerById(layers, "report-photo-rings").props.data?.map((d) => d.id)).toEqual([
      "seed-hindmata-1",
    ]);
    expect(layerById(layers, "report-selected").props.data?.map((d) => d.id)).toEqual([
      "seed-hindmata-1",
    ]);
    // Water first, the reports over it.
    const ids = (layers as LayerLike[]).map((l) => l.id);
    expect(ids.indexOf("streets-wet")).toBeLessThan(ids.indexOf("report-pins"));
  });

  it("colours now and draws no pins when the screen passes neither", () => {
    vi.stubEnv(KEY, "test-key");
    render(<CitizenMap profile="car" />);

    const layers = overlayLayers.at(-1) ?? [];
    expect(layerById(layers, "streets-wet").props.updateTriggers?.getColor?.[0]).toBe(0);
    expect((layers as LayerLike[]).some((l) => l.id.startsWith("report-"))).toBe(false);
  });

  /** A Google map click at a screen pixel, as vis.gl delivers it: the lat/lng and the DOM event. */
  function tap(x: number, y: number, lat: number, lng: number) {
    act(() => {
      googleClick?.({ detail: { latLng: { lat, lng } }, domEvent: { clientX: x, clientY: y } });
    });
  }

  it("does not take a tap on a report pin as a destination, whichever listener hears it first", () => {
    vi.stubEnv(KEY, "test-key");
    const onPickReport = vi.fn();
    const onPickPoint = vi.fn();
    render(
      <CitizenMap
        profile="car"
        reports={reports}
        onPickReport={onPickReport}
        onPickPoint={onPickPoint}
      />,
    );

    // The map's own click first, deck's pin click after: the order a real browser was measured in
    // when the old timestamp guard let the tap through as a destination.
    tap(477, 628, 19.012, 72.841);
    const pins = layerById(overlayLayers.at(-1) ?? [], "report-pins");
    act(() => {
      pins.props.onClick?.({ object: reports[0] });
    });
    expect(onPickReport).toHaveBeenCalledWith("seed-hindmata-1");
    expect(onPickPoint).not.toHaveBeenCalled();
    // It asked deck about the report layers only, at the tap's pixel, with deck's own radius.
    expect(picks.at(-1)).toEqual({ x: 477, y: 628, radius: 0, layerIds: ["report-"] });

    // Deck first, then the map: still one report and no destination.
    act(() => {
      pins.props.onClick?.({ object: reports[1] });
    });
    tap(612, 402, 19.027, 72.857);
    expect(onPickReport).toHaveBeenLastCalledWith("rpt-1789225538684-a1b2c3");
    expect(onPickPoint).not.toHaveBeenCalled();
    // No white "picked point" was drawn on the pin either.
    const ids = (overlayLayers.at(-1) as LayerLike[]).map((l) => l.id);
    expect(ids).not.toContain("citizen-picked-point");
  });

  it("takes a tap beside the pins, or on a touch screen, as a destination", () => {
    vi.stubEnv(KEY, "test-key");
    const onPickPoint = vi.fn();
    render(
      <CitizenMap
        profile="car"
        reports={reports}
        onPickReport={() => {}}
        onPickPoint={onPickPoint}
      />,
    );

    tap(300, 300, 19.039, 72.862);
    expect(onPickPoint).toHaveBeenCalledWith({ lon: 72.862, lat: 19.039 });

    // A touch reports its position in `changedTouches`; a touch on a pin is still the pin's.
    act(() => {
      googleClick?.({
        detail: { latLng: { lat: 19.012, lng: 72.841 } },
        domEvent: { changedTouches: [{ clientX: 478, clientY: 627 }] },
      });
    });
    expect(onPickPoint).toHaveBeenCalledTimes(1);
  });

  it("brings a focus into view once per key, and never zooms out from a closer view", () => {
    vi.stubEnv(KEY, "test-key");
    const focus = { lon: 72.841, lat: 19.012, key: "report-seed-hindmata-1-1", zoom: 15 };
    const { rerender } = render(<CitizenMap profile="car" reports={reports} focus={focus} />);

    expect(googleMap.setZoom).toHaveBeenCalledWith(15);
    expect(googleMap.panTo).toHaveBeenCalledWith({ lat: 19.012, lng: 72.841 });
    expect(googleMap.setCenter).not.toHaveBeenCalled();

    // A re-render with the same focus leaves the camera to the reader.
    rerender(<CitizenMap profile="bus" reports={reports} focus={{ ...focus }} />);
    expect(googleMap.panTo).toHaveBeenCalledTimes(1);

    // A second press is a new key; the reader has zoomed closer, so the zoom is kept.
    googleMap.getZoom.mockImplementation(() => 17);
    rerender(
      <CitizenMap
        profile="bus"
        reports={reports}
        focus={{ lon: 72.857, lat: 19.027, key: "report-rpt-2", zoom: 15 }}
      />,
    );
    expect(googleMap.panTo).toHaveBeenLastCalledWith({ lat: 19.027, lng: 72.857 });
    expect(googleMap.setZoom).toHaveBeenCalledTimes(1);
  });

  it("cuts to a focus rather than gliding under reduced motion", () => {
    vi.stubEnv(KEY, "test-key");
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: (query: string) => ({
        matches: query.includes("prefers-reduced-motion"),
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
    render(
      <CitizenMap profile="car" focus={{ lon: 72.841, lat: 19.012, key: "report-a", zoom: 15 }} />,
    );

    expect(googleMap.setCenter).toHaveBeenCalledWith({ lat: 19.012, lng: 72.841 });
    expect(googleMap.panTo).not.toHaveBeenCalled();
  });

  it("moves Google's zoom buttons halfway up the right edge on a phone, once Google has loaded", async () => {
    vi.stubEnv(KEY, "test-key");
    vi.stubGlobal("google", { maps: { ControlPosition: { RIGHT_CENTER: 8 } } });
    apiLoads.value = true;
    render(<CitizenMap profile="car" />);

    // jsdom's matchMedia matches nothing here, so this is the phone's width.
    await vi.waitFor(() => expect(screen.getByTestId("google-map").dataset.zoomPosition).toBe("8"));
    vi.unstubAllGlobals();
  });

  it("leaves Google's zoom buttons where Google puts them on a wide screen", async () => {
    vi.stubEnv(KEY, "test-key");
    vi.stubGlobal("google", { maps: { ControlPosition: { RIGHT_CENTER: 8 } } });
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: (query: string) => ({
        matches: query.includes("min-width"),
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
    apiLoads.value = true;
    render(<CitizenMap profile="car" />);
    await act(async () => {});

    expect(screen.getByTestId("google-map").dataset.zoomPosition).toBe("");
    vi.unstubAllGlobals();
  });

  it("takes a tap on a pin as a destination when the screen cannot open a report", () => {
    vi.stubEnv(KEY, "test-key");
    const onPickPoint = vi.fn();
    render(<CitizenMap profile="car" reports={reports} onPickPoint={onPickPoint} />);

    tap(477, 628, 19.012, 72.841);
    expect(onPickPoint).toHaveBeenCalledWith({ lon: 72.841, lat: 19.012 });
  });
});

describe("CitizenMap on VARUNA's own map", () => {
  it("hands CityMap the step, the reports, the selection and the handler, with buildings off", async () => {
    vi.stubEnv(KEY, "");
    const onPickReport = vi.fn();
    render(
      <CitizenMap
        profile="bus"
        step={24}
        reports={reports}
        selectedReportId="rpt-1789225538684-a1b2c3"
        onPickReport={onPickReport}
      />,
    );

    expect(await screen.findByTestId("varuna-map")).toBeInTheDocument();
    const props = cityMapProps.at(-1);
    expect(props).toMatchObject({
      step: 24,
      reports,
      selectedReportId: "rpt-1789225538684-a1b2c3",
      onPickReport,
      showBuildings: false,
    });
  });

  it("hands CityMap the focus, so a report chosen from a list is flown to on this path too", async () => {
    vi.stubEnv(KEY, "");
    const focus = { lon: 72.841, lat: 19.012, key: "report-seed-hindmata-1-1", zoom: 15 };
    render(<CitizenMap profile="car" reports={reports} focus={focus} />);

    await screen.findByTestId("varuna-map");
    expect(cityMapProps.at(-1)?.focus).toEqual(focus);
  });

  it("passes step 0 and no reports when the screen says nothing", async () => {
    vi.stubEnv(KEY, "");
    render(<CitizenMap profile="car" />);

    await screen.findByTestId("varuna-map");
    const props = cityMapProps.at(-1);
    expect(props?.step).toBe(0);
    expect(props?.reports).toEqual([]);
  });
});

describe("CitizenMap on the photographed city", () => {
  it("hands CityMap the same step and reports once 3D is switched on", async () => {
    vi.stubEnv(KEY, "");
    const onPickReport = vi.fn();
    render(
      <CitizenMap
        profile="pedestrian"
        step={6}
        reports={reports}
        selectedReportId="seed-hindmata-1"
        onPickReport={onPickReport}
      />,
    );

    await userEvent.click(await screen.findByRole("switch", { name: /Photorealistic city/ }));

    const props = cityMapProps.at(-1);
    // The 3D path is the one without Esri's flat imagery.
    expect(props?.showSatellite).toBe(false);
    expect(props).toMatchObject({
      step: 6,
      reports,
      selectedReportId: "seed-hindmata-1",
      onPickReport,
      showBuildings: false,
    });
  });
});

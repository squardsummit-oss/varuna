"use client";

/**
 * `CityMap` — the console's one memorable element (SPEC.md sections 6.1, 6.7, task P6.1).
 *
 * deck.gl renders standalone here, with **no basemap tile service behind it**. That is a
 * deliberate departure from section 5's "MapLibre + deck.gl" and it is worth stating plainly:
 *
 * 1. MapLibre decodes vector tiles in a web worker, and under this app's bundler the worker does
 *    not load - the style, the sprite and tiles.json all fetch, and then not one `.pbf` is ever
 *    requested, so the basemap is permanently blank. Rendering a map that is reliably empty is
 *    worse than rendering no map.
 * 2. SPEC.md 17 requires the finale to run with the venue's network switched off, and a CDN
 *    basemap is the one thing on this screen that cannot. Everything drawn below comes from the
 *    city VARUNA built and the run it computed, so the console works on an aeroplane.
 *
 * What replaces it is not a placeholder, it is the city's own GIS. 39,259 building footprints
 * give Mumbai its texture, 21,296 road segments give it its shape, and the drain graph beneath
 * them is the thing no basemap has. A judge reads the city from the data VARUNA derived, which
 * is the honest version of this map anyway.
 *
 * **3D is the one exception, and it is opt-in.** With `threeD` asked for and Google's Map Tiles
 * API answering, the ground becomes Google's photorealistic mesh and every VARUNA layer is draped
 * on it (`layers/photoreal.ts`, loaded only when 3D is asked for), so the water sits on a photographed Mumbai. That ground is
 * online-only by construction, which is exactly why it is a toggle and never the default: with
 * the venue's network off, or with Google declining the key, 3D simply does not turn on and the
 * flat map above stays exactly as it was. It replaced a Terrarium heightmap built from the city's
 * own DEM (`layers/terrain.ts`, ADR-0065), which is deleted. Seen drawing on 2026-09-23 at
 * `/console`, `/dashboard` and over Dadar at street level, and the tileset answers `curl` on the
 * same machine too - 200 with no Referer, with a localhost Referer and with an arbitrary one -
 * because the key carries no HTTP-referrer restriction. It is still only a browser that proves
 * the *mesh* drew; 200 on `root.json` proves only that the service answered.
 *
 * **It costs the frame rate, and the number is bad.** Measured 2026-09-23 at 1440 x 900:
 * **25.2-26.1 fps at street level and 11.4-11.9 fps at the AOI fit**, against 44.9 fps with 3D
 * off and section 14's budget of 55. So 3D stays off by default and the flat map is what the
 * demo runs on - the same conclusion ADR-0065 reached about the heightmap it replaced, for a
 * worse number.
 *
 * **The drain X-ray** (motion M28) is the view the 3D ground exists for: the surface fades to
 * 20 % while `layers/drains-3d.ts` draws the inferred pipes at their invert elevations beneath
 * it, with a shaft up to the street at every manhole. It is the only way to look *along* a sewer
 * under the road it follows rather than at a line on a plan.
 *
 * **This file is the host.** It owns the memoised composition, the props and the DOM around the
 * canvas. Every layer is built by one module under `layers/` (task MO1), and the camera (the fit,
 * the fly-to, who owns the view) by `layers/camera.ts`, so a motion or a new layer edits that
 * module rather than this file.
 *
 * Layer order follows section 6.7 bottom to top: basemap, buildings, dry streets, drains, depth
 * raster, wet streets, hotspot rings, reversed-flow edges, inlets, surcharging manholes,
 * isochrones, routes, ground-truth pins, labels.
 *
 * **Scrubbing costs nothing.** The run's 36 frames are decoded to ImageBitmaps before the scrub
 * is usable; a step change swaps a texture and re-runs one colour accessor. No fetch, no decode
 * (SPEC.md 7.2: "no network during scrub").
 */

import DeckGL from "@deck.gl/react";
import { useEffect, useMemo, useState } from "react";

import { useTheme } from "@/lib/theme";
import { cityBounds, type Bbox } from "./basemap";
import { buildingsLayers, dryStreetsLayers } from "./layers/base";
import { useCityCamera, type FitPadding } from "./layers/camera";
import { wipeLongitude } from "./layers/diff";
import { drainsLayers } from "./layers/drains";
import { drawnExtent } from "./layers/frame";
import { hotspotRingsLayers } from "./layers/hotspots";
import { inletLayers } from "./layers/inlets";
import { isochroneLayers, useDisplayedIsochrones } from "./layers/isochrones";
import { useLabelLayers } from "./layers/map-labels";
import { depthRasterLayers } from "./layers/raster";
import { pointsInView, useReversedFlowLayers, viewBounds } from "./layers/reversed-flow";
import { routeLayers, useRouteProgress } from "./layers/routes";
import { wetStreetsLayers } from "./layers/streets";
import { deckAnimates, surchargeLayers } from "./layers/surcharge";
import { drains3dLayers, viewKey } from "./layers/drains-3d";
import {
  PHOTOREAL_PITCH,
  hiddenLayers,
  localWorkersVerified,
  onSurface,
  verifyLocalWorkers,
} from "./layers/surface";
import {
  isReportLayer,
  reportPinLayers,
  reportTooltipText,
  useReportDrops,
} from "./layers/reports";
import { mapTooltip } from "./layers/tooltip";
import { truthPinLayers } from "./layers/truth-pins";
import { useMapOverlay } from "./layers/overlay-context";
import type {
  BuildingPolygon,
  DrainNode,
  DrainPath,
  DrainPick,
  HotspotRing,
  InletPoint,
  Isochrone,
  MapFocus,
  ReversedEdgePath,
  RouteLine,
  SegmentPath,
  SegmentPick,
  SurchargeNode,
  SurchargeStyle,
  TruthPin,
} from "./layers/types";
import type { MapLabel } from "./labels";
import { MAP_ATTRIBUTION, satelliteLayers } from "./satellite";
import type { CityMapMode } from "./types";

/** The loader-heavy half of 3D, fetched on demand (`layers/surface.ts` is the light half). */
type PhotorealModule = typeof import("./layers/photoreal");
import { MapAttribution } from "@/components/varuna/map-attribution";
import type { ReportPin } from "@/lib/api/reports";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { createCreditStore, useMapCredits, usePhotorealTileset } from "@/lib/maps/photoreal";
import { googleMapsKey } from "@/lib/maps/google";
import { DUR_MS } from "@/lib/motion";

// The data types lived here before the split; importers still find them here.
export type * from "./layers/types";

/** Stable empty default, so a screen that passes no routes never changes the routes memo. */
const NO_ROUTES: readonly RouteLine[] = [];
/** Stable empty default, so a caller that passes nothing does not rebuild the basemap memo. */
const NO_LAYERS: readonly unknown[] = [];
/** Stable empty default for the X-ray's nodes: a fresh `[]` would rebuild its memo every render. */
const NO_NODES: readonly DrainNode[] = [];
/** Stable empty default for citizen reports: every screen that shows none draws exactly as before. */
const NO_REPORTS: readonly ReportPin[] = [];
/** How far the photographed surface fades under the X-ray (motion row M28: "to 20 % opacity"). */
const XRAY_SURFACE_OPACITY = 0.2;

export interface CityMapProps {
  mode?: CityMapMode;
  frames: readonly (ImageBitmap | null)[];
  rasterBounds: [number, number, number, number] | null;
  /** Every road in the city, for context. Drawn once, in the dry colour. */
  baseSegments: readonly SegmentPath[];
  /** The segments this run wetted, coloured by depth at the current step. */
  segments: readonly SegmentPath[];
  surcharge: readonly SurchargeNode[];
  hotspots: readonly HotspotRing[];
  /** Building footprints; the city's texture. Loaded after first paint. */
  buildings?: readonly BuildingPolygon[];
  /** The inferred drain graph, off by default (section 6.7). */
  drains?: readonly DrainPath[];
  /**
   * The drain graph's nodes, for the X-ray's manhole shafts and outfall markers.
   *
   * Loaded only when the X-ray is asked for (about 9 MB for Mumbai) and optional even then: with
   * none, `drains3dLayers` draws the pipes and says in its own summary that the shafts are
   * missing rather than inventing a depth for them.
   */
  drainNodes?: readonly DrainNode[];
  /** Routes to draw over everything else (section 6.7's layer order). */
  routes?: readonly RouteLine[];
  /** Reachability bands, under the routes and over the streets. */
  isochrones?: readonly Isochrone[];
  /** Sourced ground-truth pins the replay clock has reached (SPEC.md 7.2's 2:40 moment). */
  truthPins?: readonly TruthPin[];
  /**
   * Citizen reports, drawn as `--obs-report` pins with their status in the outline and a ring when
   * a photo is attached (`layers/reports.ts`). A report that arrives after the first list drops in
   * (motion M32). Empty by default, and an empty list draws nothing and changes nothing else.
   */
  reports?: readonly ReportPin[];
  /** The report whose card is open, drawn with a ring outside its pin. */
  selectedReportId?: string | null;
  /** A report pin was tapped. Absent leaves the pins unpickable. */
  onPickReport?: (id: string) => void;
  /** Called when a wet street is clicked, with the segment and where on screen it was (P6.9).
   * Absent leaves the streets unpickable, which is what the hero and the public map want. */
  onSegmentPick?: (pick: SegmentPick | null) => void;
  /** Depth in cm at which the audience's vehicle stops. Set it and the wet streets are drawn in
   * the public map's three colours instead of the operator's depth ramp (SPEC.md 7.11). */
  passableBelowCm?: number;
  /** The hotspot the rail has selected; drawn as a second, brighter ring (motion M10). */
  selectedHotspotId?: string | null;
  /** Fly the camera here when `key` changes. */
  focus?: MapFocus | null;
  step: number;
  /**
   * The area the camera frames when nothing is drawn yet. Defaults to the city's AOI. Once the
   * streets load the camera frames what is drawn, unless `fitBounds` says otherwise.
   */
  bounds?: Bbox;
  /**
   * The box the camera opens on, instead of everything drawn: a screen's main affected area
   * (`lib/map/affected-bounds.ts`). Absent or null, the camera frames what is drawn, as it always
   * has. A camera the operator has moved is theirs: a changed `fitBounds` re-frames only a camera
   * nobody has touched, so a new run can re-frame the map and a scrub never yanks it.
   */
  fitBounds?: Bbox | null;
  /**
   * Change it to re-arm the fit - drop the operator's pan and zoom and frame `fitBounds` (or what
   * is drawn) again, as a cut. For an explicit ask such as the console's full view.
   */
  fitKey?: string | number | null;
  /**
   * The `fitKey` whose camera is kept on leaving it and given back on returning: the console
   * passes its normal layout's key, so leaving full view restores the view the operator had.
   */
  keepViewOf?: string | number | null;
  /**
   * The fit's margin in pixels, per side where a screen floats panels over its map, so the frame
   * lands in the part of the map nobody has covered. Defaults to 12 px all round.
   */
  fitPadding?: FitPadding;
  showRaster?: boolean;
  showSegments?: boolean;
  showSurcharge?: boolean;
  showHotspots?: boolean;
  /**
   * Building footprints. Off unless asked for: every screen either passes it or wants it off, and
   * 11 MB of polygons is an operator's load, not a default.
   */
  showBuildings?: boolean;
  showDrains?: boolean;
  /** Draw Esri's aerial imagery under everything (section 6.7's basemap slot). */
  showSatellite?: boolean;
  /** Probability mode (SPEC.md 6.2, task P6.5): the depth-ramp colour at the p50 depth, with
   * opacity set by P(depth > this threshold in cm). Unset draws ordinary depth. */
  probabilityThresholdCm?: number;
  /** Draw `segments` by their `deltaCm` rather than their depth: the what-if diff layer
   * (SPEC.md 7.7). Blue is improved, red is worse, grey is unchanged. */
  diffMode?: boolean;
  /** 0 to 1 left-to-right reveal of the diff layer (motion M13). 1 shows all of it. */
  diffProgress?: number;
  /** Place names, street names and facility markers (section 6.7's label layer). */
  showLabels?: boolean;
  /** Facilities and chronic junctions to name on the map, beside the street names. */
  labels?: readonly MapLabel[];
  /** Draw the map credit. Off where `MapSlot` sits behind this map and draws it already. */
  attribution?: boolean;
  /** Layers drawn under everything else, after the imagery: the public map's offline vector
   * basemap (task P9.10). Built by the caller so this host stays a composer. */
  basemapLayers?: readonly unknown[];

  // ---- Seams (task MO1) ---------------------------------------------------------------------
  // Accepted and handed to their layer module, and **not drawn or applied yet**. Each names the
  // chunk that makes it do something, so that chunk edits `layers/*` and not this file.

  /** The replay is playing, which lets street colours tween between steps (M7, MO5). */
  playing?: boolean;
  /** Drain edges flowing backwards at `step`, drawn with an animated dash (M9): tidal edges at
   * every zoom, inland edges from zoom 14 and in view. */
  reversedEdges?: readonly ReversedEdgePath[];
  /** The drain before/after cross-fade in ms, set only for a toggle (M12, MO10). */
  drainCrossFadeMs?: number;
  /** Hovering a pipe on `/drains` (PU8). */
  onDrainHover?: (pick: DrainPick | null) => void;
  /** Drain inlets as squares coloured by κ (SPEC.md 7.3, PU8). */
  inlets?: readonly InletPoint[];
  /** The console's pulsing manholes or `/drains`' static rings (PU8). */
  surchargeStyle?: SurchargeStyle;
  /**
   * Per-layer opacity, 0 to 1, for the onboarding wizard's layer stack (motion M19, task D-21).
   *
   * The wizard is the one screen where layers arrive one at a time, as the pipeline writes them,
   * and M19 asks each to fade in over 400 ms as its step completes. Every other screen leaves
   * this unset and every layer draws at 1, exactly as before. Drive it with `useLayerFade`, which
   * holds the catalogue's duration and its reduced-motion branch.
   */
  layerFade?: { streets?: number; buildings?: number; drains?: number; raster?: number };
}

export function CityMap({
  mode = "console",
  frames,
  rasterBounds,
  baseSegments,
  segments: segmentsProp,
  surcharge,
  hotspots,
  buildings = [],
  drains = [],
  drainNodes = NO_NODES,
  routes: routesProp = NO_ROUTES,
  isochrones = [],
  truthPins = [],
  reports = NO_REPORTS,
  selectedReportId = null,
  onPickReport,
  onSegmentPick,
  passableBelowCm,
  selectedHotspotId = null,
  focus = null,
  step,
  bounds,
  fitBounds = null,
  fitKey = null,
  keepViewOf = null,
  fitPadding,
  showRaster = true,
  showSegments = true,
  showSurcharge = true,
  showHotspots = true,
  showBuildings = false,
  showDrains = false,
  probabilityThresholdCm,
  diffMode: diffModeProp = false,
  diffProgress: diffProgressProp = 1,
  showSatellite = true,
  showLabels = true,
  labels = [],
  attribution = true,
  basemapLayers: extraBasemap = NO_LAYERS,
  playing = false,
  reversedEdges = [],
  drainCrossFadeMs,
  onDrainHover,
  inlets = [],
  surchargeStyle = "pulse",
  layerFade,
}: CityMapProps) {
  const interactive = mode !== "hero";
  const reducedMotion = usePrefersReducedMotion();
  // The palette's theme-dependent colours (the dry street, the buildings, the route casing, the
  // accent) are read when a layer is built, so every memo that builds one lists the theme: a
  // switch rebuilds them in the new colours instead of waiting for the next scrub (palette.ts).
  const { theme } = useTheme();

  // What the console asks for beyond these props: 3D, its routes layer and the what-if
  // difference layer (`layers/overlay-context.ts`). Every other screen provides nothing.
  const overlay = useMapOverlay();

  // Google's photorealistic ground, probed the first time 3D is asked for. `usePhotorealTileset`
  // returns `off` until then, so a screen nobody has switched to 3D never touches Google.
  const wantsThreeD = Boolean(overlay.threeD) && interactive;
  const photoreal = usePhotorealTileset(wantsThreeD);
  // The tileset builder, and the 3D Tiles parser and Draco decoder it imports, load only when 3D
  // is asked for (P10.4): 530 KB of JavaScript every screen used to parse at boot for a mode
  // that is off by default. Fetched alongside the Google probe, so it is normally in by the time
  // the probe says `ready`; until both are, the flat map is what draws.
  const [photorealModule, setPhotorealModule] = useState<PhotorealModule | null>(null);
  useEffect(() => {
    if (!wantsThreeD || photorealModule) return;
    let live = true;
    void import("./layers/photoreal").then((module) => {
      if (live) setPhotorealModule(module);
    });
    return () => {
      live = false;
    };
  }, [wantsThreeD, photorealModule]);
  // Where tiles will be decoded is decided before any tileset exists, as it was when the builder
  // was imported eagerly: the probe is one same-origin HEAD per worker, cached for the page.
  useEffect(() => {
    if (interactive && !localWorkersVerified()) void verifyLocalWorkers();
  }, [interactive]);
  // 3D is drawn only once the ground exists; until then the flat map stays exactly as it was.
  // Anything but `ready` - no key, the Map Tiles API disabled, a refused referrer, no network -
  // leaves the flat map alone, and the screen prints the state's own sentence beside the toggle.
  const threeD = photoreal.kind === "ready" && photorealModule !== null;
  // Read once, like the probe: Next inlines the key at build time and it cannot change in-page.
  const [googleKey] = useState(() => googleMapsKey());
  // The credits of the tiles currently on screen. A store rather than state: the tileset hands
  // them over on every traversal, and only the merged line reaching React is affordable.
  const creditStore = useMemo(() => createCreditStore(), []);
  const credits = useMapCredits(creditStore);

  const overlayRoutes = overlay.routes;
  const routes = useMemo(
    () =>
      overlayRoutes && overlayRoutes.length > 0 ? [...routesProp, ...overlayRoutes] : routesProp,
    [routesProp, overlayRoutes],
  );

  // The what-if answer joined onto the city's own geometry: only the segments it moved, as the
  // lab does, so 21,296 unchanged paths never go through the diff accessor.
  const overlayDiff = overlay.diff ?? null;
  const diffDelta = overlayDiff?.deltaCm ?? null;
  const diffSegments = useMemo(() => {
    if (!diffDelta) return null;
    return baseSegments
      .filter((s) => diffDelta.has(s.id))
      .map((s) => ({ ...s, deltaCm: diffDelta.get(s.id) ?? 0 }));
  }, [baseSegments, diffDelta]);
  const segments = diffSegments ?? segmentsProp;
  const diffMode = diffSegments ? true : diffModeProp;
  const diffProgress = diffSegments && overlayDiff ? overlayDiff.progress : diffProgressProp;

  const routeProgress = useRouteProgress(routes, reducedMotion);
  const shownIsochrones = useDisplayedIsochrones(isochrones, reducedMotion);

  // ---- Framing --------------------------------------------------------------------------
  // The camera (fit, fly-to, ownership) lives in `layers/camera.ts`; it frames what is drawn.
  const aoi = bounds ?? cityBounds("mumbai");

  const drawn = useMemo<Bbox>(
    () => drawnExtent({ routes, baseSegments, segments, drains, hotspots, fallback: aoi }),
    [routes, baseSegments, segments, drains, hotspots, aoi],
  );
  // A screen's own frame wins over the drawn extent; the what-if wipe then sweeps across what is
  // on screen rather than across the whole city.
  const frame = fitBounds ?? drawn;

  const wipeLon = useMemo(
    () => wipeLongitude(diffMode, diffProgress, frame),
    [diffMode, diffProgress, frame],
  );

  const { containerRef, size, viewState, onViewStateChange } = useCityCamera({
    frame,
    focus,
    reducedMotion,
    interactive,
    threeD,
    pitch3d: PHOTOREAL_PITCH,
    fitKey,
    keepViewOf,
    fitPadding,
  });

  // ---- Keyboard -------------------------------------------------------------------------

  /**
   * deck.gl's events root, made honest about the tab order (SPEC.md 6.10, found by P10.2 on
   * 2026-09-24).
   *
   * deck creates `div.deck-events-root` with `tabIndex="0"` and an inline `outline: none`, on
   * every map, whatever `controller` is set to. Two things follow, and both were measured on this
   * tree rather than reasoned about:
   *
   * 1. On the landing page the hero map is read-only decoration (`mode="hero"`, motion M1), and
   *    that div was the **first tab stop on the whole page** - unnamed, invisible, and offering a
   *    keyboard user nothing. A stop like that is worse than no stop: the page looks unresponsive
   *    to the first Tab. With `controller={false}` there is no keyboard behaviour to preserve, so
   *    it leaves the tab order.
   * 2. Where the map *is* interactive the stop belongs, but deck's inline `outline: none` beats
   *    the `:focus-visible` rule in `globals.css`, so it was reachable and invisible. Clearing the
   *    inline value lets the 2 px `--tide` ring 6.10 asks for paint like everything else.
   *
   * It is done from the DOM because deck.gl exposes neither prop. The lookup is scoped to this
   * map's own container, so a screen with two maps does not reach into the other's.
   */
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const wanted = interactive ? 0 : -1;
    /** Writes only when the value is wrong, so the observer below cannot loop on its own edit. */
    const apply = () => {
      const root = container.querySelector<HTMLElement>(".deck-events-root");
      if (!root) return;
      if (root.tabIndex !== wanted) root.tabIndex = wanted;
      if (interactive && root.style.outline === "none") {
        // Cleared rather than removed: an empty inline value still beats deck's own when deck
        // re-applies it, and it lets the stylesheet's `:focus-visible` outline through.
        root.style.outline = "";
      }
    };

    apply();
    // deck writes `tabIndex` and `outline` onto the events root whenever its event manager
    // attaches, which is after this effect on the first render and again whenever the renderer is
    // re-initialised - a lost WebGL context, which this map recovers from. Measured on 2026-09-24:
    // a one-shot effect was silently overwritten and `/`'s first tab stop stayed the unnamed div.
    const observer = new MutationObserver(apply);
    observer.observe(container, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["tabindex", "style"],
    });
    return () => observer.disconnect();
  }, [containerRef, interactive]);

  // ---- Layers ---------------------------------------------------------------------------
  // Memoised in groups by what changes them, so moving the time bar rebuilds only the run's
  // layers and never 39,259 building polygons, the drain graph or the routes.

  // The basemap, under everything. Rebuilt only when it is toggled or the raster comes and goes:
  // `TileLayer` keeps its own tile cache, and handing deck a new instance every render would
  // throw that cache away on every scrub.
  //
  // Not in 3D: the imagery is flat and the ground stands above it, so every tile would be drawn
  // and then hidden. Google's photographed mesh carries the city's surface there instead.
  const imagery = showSatellite && !threeD;
  const basemapLayers = useMemo(
    () => [...satelliteLayers({ enabled: showSatellite, dimmed: showRaster }), ...extraBasemap],
    [showSatellite, showRaster, extraBasemap],
  );

  // The ground: Google's photorealistic mesh (task P6.15, ADR-0065 superseded). Unlike the
  // Terrarium heightmap it replaced, this ground carries no water of its own - the depth raster
  // is draped onto it like every other layer, which is what `onSurface` does and `onTerrain`
  // deliberately did not.
  //
  // Motion M28: the X-ray fades this to 20 % over `DUR_MS.crossFade` while the pipes beneath it
  // come up. Under `prefers-reduced-motion` the duration is 0, which deck reads as a cut.
  const surfaceOpacity = overlay.xray ? XRAY_SURFACE_OPACITY : 1;
  const groundLayers = useMemo(
    () =>
      photorealModule
        ? photorealModule.photorealLayers({
            key: googleKey,
            state: photoreal,
            opacity: surfaceOpacity,
            fadeMs: reducedMotion ? 0 : DUR_MS.crossFade,
            onCredits: creditStore.setCredits,
          })
        : [],
    [photorealModule, googleKey, photoreal, surfaceOpacity, reducedMotion, creditStore],
  );

  // Each layer's M19 opacity, read out here so the memos below depend on a number rather than on
  // an object identity a parent would recreate every render.
  const fadeStreets = layerFade?.streets ?? 1;
  const fadeBuildings = layerFade?.buildings ?? 1;
  const fadeDrains = layerFade?.drains ?? 1;
  const fadeRaster = layerFade?.raster ?? 1;

  const cityLayers = useMemo(
    () => (void theme, buildingsLayers({ buildings, show: showBuildings, fade: fadeBuildings })),
    [buildings, showBuildings, fadeBuildings, theme],
  );

  const streetLayers = useMemo(
    () => [
      ...(void theme, dryStreetsLayers({ baseSegments, fade: fadeStreets })),
      ...drainsLayers({
        drains,
        show: showDrains,
        crossFadeMs: drainCrossFadeMs,
        onHover: onDrainHover,
        fade: fadeDrains,
      }),
    ],
    [
      baseSegments,
      drains,
      showDrains,
      drainCrossFadeMs,
      onDrainHover,
      fadeStreets,
      fadeDrains,
      theme,
    ],
  );

  const runLayers = useMemo(
    () => [
      ...(void theme,
      depthRasterLayers({
        frame: frames[step] ?? null,
        bounds: rasterBounds,
        // In 3D the frame is the terrain's texture, and this flat copy is hidden with the rest
        // of the flat map (see `hiddenLayers`) rather than removed.
        show: showRaster,
        fade: fadeRaster,
      })),
      ...wetStreetsLayers({
        segments,
        step,
        show: showSegments,
        diffMode,
        wipeLon,
        probabilityThresholdCm,
        passableBelowCm,
        pickable: Boolean(onSegmentPick),
        playing,
        reducedMotion,
      }),
      ...hotspotRingsLayers({
        hotspots,
        selectedHotspotId,
        show: showHotspots,
        reducedMotion,
      }),
    ],
    [
      frames,
      step,
      rasterBounds,
      segments,
      hotspots,
      selectedHotspotId,
      showRaster,
      showSegments,
      showHotspots,
      passableBelowCm,
      diffMode,
      wipeLon,
      probabilityThresholdCm,
      onSegmentPick,
      playing,
      reducedMotion,
      fadeRaster,
      theme,
    ],
  );

  // What the camera can see, for the reversed-flow zoom gate and the redraw gate (M8, M9).
  const visible = viewBounds(viewState, size);
  const reversedFlow = useReversedFlowLayers({
    edges: reversedEdges,
    show: showSurcharge,
    reducedMotion,
    zoom: viewState.zoom,
    bounds: visible,
  });

  const drainFlowLayers = useMemo(
    () => [...reversedFlow.layers, ...inletLayers({ inlets, show: showDrains })],
    [reversedFlow.layers, inlets, showDrains],
  );

  // The drain X-ray (motion M28). Keyed on `viewKey` rather than on the raw camera: a pan that
  // moves nothing more than 110 m, or a zoom inside a half step, rebuilds nothing.
  const xrayOn = Boolean(overlay.xray);
  const xrayExaggeration = overlay.xrayExaggeration ?? 1;
  const xrayCamera = viewKey(viewState.zoom, visible);
  const xray = useMemo(
    () =>
      drains3dLayers({
        city: overlay.city ?? "mumbai",
        drains,
        nodes: drainNodes,
        show: xrayOn,
        zoom: viewState.zoom,
        bounds: visible,
        depthExaggeration: xrayExaggeration,
      }),
    // `visible` and `viewState.zoom` are read above and deliberately not listed: `xrayCamera` is
    // their identity, and listing them raw would rebuild 49,770 pipes on every mouse move.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [overlay.city, drains, drainNodes, xrayOn, xrayCamera, xrayExaggeration],
  );
  /**
   * The X-ray's pipes, and the one thing that makes them an X-ray rather than a buried secret.
   *
   * Drawn straight, they lose the depth test to the ground above them: Google's mesh is opaque
   * geometry, it writes depth first, and a pipe 1.5 m under a photographed street is rejected
   * before it is shaded. Driven in a browser over Mumbai central on 2026-09-23, the network was
   * there while the camera was wide and the mesh coarse, and gone once the tiles refined over it.
   * That reads as a bug and is physics.
   *
   * So in 3D the pipes are drawn with the depth test off. That is what an X-ray is: the whole
   * network is visible through the surface rather than only the parts nothing covers. It changes
   * nothing about where they are - the positions are still the true inverts - only what occludes
   * them, and it is paired with M28 fading the surface to 20 % so the photograph still reads as
   * the thing they run under. On the flat map the depth test is left alone; there is no ground.
   */
  const xrayLayers = useMemo(() => {
    if (xray.kind !== "ready") return NO_LAYERS;
    if (!threeD) return xray.layers;
    return xray.layers.map((layer) =>
      (layer as { clone: (p: object) => unknown }).clone({ parameters: { depthTest: false } }),
    );
  }, [xray, threeD]);

  // What the X-ray managed to draw, back to the screen that switched it on, so the layer panel
  // prints counts rather than a claim. An effect, not a render-time call: the counts depend on
  // the camera, and a parent setState during this component's render is a React warning.
  const onXray = overlay.onXray;
  useEffect(() => {
    onXray?.(xray);
  }, [onXray, xray]);

  // The pulse runs on deck's clock (a shader uniform), so the markers rebuild only with their data.
  const markerLayers = useMemo(
    () => surchargeLayers({ surcharge, show: showSurcharge, reducedMotion, style: surchargeStyle }),
    [showSurcharge, surcharge, reducedMotion, surchargeStyle],
  );

  // Reachability under the routes, routes over everything (section 6.7's order), pins above both.
  // All are small - three polygons and four paths - so they share one memo.
  const overlayLayers = useMemo(
    () => [
      ...(void theme, isochroneLayers({ isochrones: shownIsochrones })),
      ...routeLayers({ routes, progress: routeProgress }),
      ...truthPinLayers({ truthPins, reducedMotion }),
    ],
    [routes, shownIsochrones, routeProgress, truthPins, reducedMotion, theme],
  );

  // Citizen reports over the overlays and under the labels, so a pin is never hidden by a route
  // and a street name is never hidden by a pin. Their own memo: a report arriving rebuilds these
  // few pins and nothing else, and a scrub never touches them.
  const shownReports = useReportDrops(reports, reducedMotion);
  const reportLayers = useMemo(
    () =>
      reportPinLayers({
        reports: shownReports,
        selectedReportId,
        onPick: onPickReport,
        reducedMotion,
      }),
    [shownReports, selectedReportId, onPickReport, reducedMotion],
  );

  const labelDrawLayers = useLabelLayers({
    baseSegments,
    segments,
    labels,
    showLabels,
    showSatellite,
    view: viewState,
    size,
  });

  const layers = useMemo(() => {
    const above = [
      ...cityLayers,
      ...streetLayers,
      ...runLayers,
      ...drainFlowLayers,
      ...markerLayers,
      ...overlayLayers,
      ...reportLayers,
      // Labels last: a street name the depth ramp paints over is a name nobody can read.
      ...labelDrawLayers,
    ];
    // In 3D everything above the ground is laid on it: streets, rings and markers are drawn at
    // z = 0, which would put them under a ground that averages 20 m up once exaggerated.
    //
    // The flat layers stay in the list, hidden, rather than being dropped: dropped, deck
    // finalises them, and re-creating them on the way out of 3D ran into the terrain effect being
    // torn down in the same frame - an assertion per layer and a wave of WebGL errors. Hidden,
    // they are never re-initialised, and the satellite keeps its tile cache.
    // The X-ray's pipes are the one thing that is *not* draped: they carry their own invert
    // elevation and belong under the ground, which is the whole point of the view. Everything
    // else is lifted onto the photographed surface.
    return threeD
      ? [
          ...hiddenLayers([...basemapLayers, ...above]),
          ...groundLayers,
          ...xrayLayers,
          ...onSurface(above),
        ]
      : [...basemapLayers, ...above, ...xrayLayers];
  }, [
    threeD,
    groundLayers,
    xrayLayers,
    basemapLayers,
    cityLayers,
    streetLayers,
    runLayers,
    drainFlowLayers,
    markerLayers,
    overlayLayers,
    reportLayers,
    labelDrawLayers,
  ]);

  // A hovered report pin names itself; every other object keeps the street tooltip it had. Only
  // wrapped when there are pickable pins, so a screen without reports hands deck the same function.
  const streetTooltip = mapTooltip({ step, streetsPickable: Boolean(onSegmentPick) });
  const getTooltip =
    onPickReport && reports.length > 0
      ? (info: { object?: unknown; layer?: unknown }) =>
          isReportLayer(info.layer) && info.object
            ? {
                text: reportTooltipText(info.object as ReportPin),
                style: {
                  backgroundColor: "var(--deep)",
                  color: "var(--text)",
                  border: "1px solid var(--line)",
                  borderRadius: "8px",
                  fontSize: "12px",
                  padding: "6px 8px",
                  whiteSpace: "pre-line",
                },
              }
            : (streetTooltip?.(info as { object?: SegmentPath }) ?? null)
      : streetTooltip;

  const animate = deckAnimates({
    reducedMotion,
    surchargeVisible: showSurcharge ? pointsInView(surcharge, visible) : 0,
    reversedVisible: reversedFlow.inView,
  });

  return (
    <div ref={containerRef} className="absolute inset-0 bg-[var(--ink)]">
      <DeckGL
        viewState={viewState as never}
        onViewStateChange={onViewStateChange as never}
        controller={interactive}
        layers={layers as never}
        pickingRadius={6}
        _animate={animate}
        getTooltip={getTooltip as never}
        onClick={
          onSegmentPick
            ? ((({ object, x, y }: { object?: SegmentPath; x: number; y: number }) => {
                onSegmentPick(object ? { segment: object, x, y } : null);
              }) as never)
            : undefined
        }
      />

      {imagery ? (
        // The scrim. The imagery is already drawn dim; this takes the last of its contrast out of
        // the midtones so the depth ramp has the only saturated colour on the screen. It is
        // `pointer-events-none` because the map underneath still has to be draggable.
        //
        // Lighter in hero mode: the landing page lays its own wash across the left of this map
        // for the headline to sit on, and the full scrim on top of that left the imagery
        // invisible. The hero wants the city to look like somewhere; the console wants it to
        // stay out of the water's way.
        <div
          aria-hidden="true"
          className={
            interactive
              ? "pointer-events-none absolute inset-0 bg-[var(--ink)]/30"
              : "pointer-events-none absolute inset-0 bg-[var(--ink)]/15"
          }
        />
      ) : null}

      {/* Google's Map Tiles policy requires the Google attribution and the providers of the tiles
          *currently on screen* whenever they are drawn, so this is mounted on `threeD` and not on
          the `attribution` prop: a host that draws its own credit line elsewhere does not get to
          switch Google's off.

          It sits in the bottom-right corner, where nothing else does. The two neighbours were
          checked rather than assumed: `MapSlot`'s depth legend (section 6.7) is `bottom-10` and
          starts 40 px up, and the full-width credit line below it is a `<p>` whose text - 75
          characters of Esri, OpenStreetMap and Copernicus - runs out well before the right edge
          at any width this mode is used at. On a phone it would collide, and 3D is not offered
          on the public map. */}
      {threeD ? <MapAttribution credits={credits} /> : null}

      {/* Esri's imagery is free to use and requires the credit while it is on screen. The console
          mounts `MapSlot` behind this map and that draws the same line; `attribution={false}`
          there keeps it from appearing twice. */}
      {attribution ? (
        <p className="type-micro text-text-3 pointer-events-none absolute inset-x-0 bottom-0 z-10 px-4 py-2">
          {MAP_ATTRIBUTION}
        </p>
      ) : null}
    </div>
  );
}

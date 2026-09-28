"use client";

/**
 * The citizen map: a Google basemap with VARUNA's water drawn over it (TECH_SPEC 2, task D-10).
 *
 * The console's map is deck.gl standing alone over aerial imagery, because an operator reads the
 * city from the GIS VARUNA built. A citizen reads it from the city they already know - the shop on
 * the corner, the flyover they take every morning - and that is the one thing Google's basemap is
 * genuinely better at. So this screen, and only this screen, loads Google.
 *
 * Google is a **basemap and nothing else** (TECH_SPEC 0). The streets, their depths, the route and
 * every marker come from VARUNA's own run. That is a rule this code keeps, not one the key
 * enforces - see `lib/maps/google.ts`, which records that the key now in use answers Geocoding and
 * Directions perfectly well.
 *
 * **The fallback is no longer the path most likely to run, and that sentence stood here until
 * 2026-09-23.** It was written when this build carried MCGM's browser key rather than one of ours,
 * whose referrer list named no origin of ours and which was measured throwing
 * `RefererNotAllowedMapError` at a dev origin (ADR-0059). On the team's own key Google loads. The
 * fallback is still the path for a missing key, a slow bootstrap, a refused referrer once the key
 * is restricted, and the venue with its network off (SPEC.md 17) - it draws the same map the
 * console draws, over aerial imagery, with one sentence saying which map this is, and it carries
 * identical water, identical routes and identical numbers.
 *
 * Nothing here logs to the console in either path. SPEC.md 14 makes a console error a failing
 * gate, and a key that is absent by design is not an error.
 *
 * **The photorealistic city is a third path, and it is off until asked for.** The same key can
 * unlock Google's Photorealistic 3D Tiles, which put this screen's water on a photographed Mumbai
 * rather than on a street diagram of it. That needs a *different* Google product with its own
 * switch in the Cloud console, so it gets its own probe (`lib/maps/photoreal.ts`) and its own
 * sentence. Driven in a browser on 2026-09-23 it works: switching it on at
 * `http://localhost:3000/dashboard` replaced the basemap with Google's photographed Mumbai and
 * VARUNA's streets kept their depths on top of it. When it does not work - no key, a referrer
 * Google will not serve, no network - the switch prints that instead and leaves the basemap
 * exactly as it was. The existing fallback chain is untouched by it: no key, a timeout,
 * `gm_authFailure` and a refused referrer all still land on VARUNA's own map with the sentence
 * that says which map this is.
 */

import { GoogleMapsOverlay } from "@deck.gl/google-maps";
import { ScatterplotLayer } from "@deck.gl/layers";
import { APIProvider, Map as GoogleMap, useMap } from "@vis.gl/react-google-maps";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Box } from "lucide-react";

import { CityMap } from "@/components/map/city-map";
import { MapOverlayContext, type MapOverlay } from "@/components/map/layers/overlay-context";
import { cityBounds } from "@/components/map/basemap";
import { EmptyState } from "@/components/varuna/empty-state";
import { Skeleton } from "@/components/varuna/skeleton";
import { loadFacilityLabels, type FacilityLabel } from "@/lib/api/city-layers";
import {
  REPORT_LAYER_PREFIX,
  reportPinLayers,
  useReportDrops,
} from "@/components/map/layers/reports";
import { routeLayers, useRouteProgress } from "@/components/map/layers/routes";
import { wetStreetsLayers } from "@/components/map/layers/streets";
import type { MapFocus, RouteLine } from "@/components/map/layers/types";
import { TRUTH_FILL, TRUTH_RING } from "@/components/map/layers/palette";
import type { PublicProfile } from "@/components/varuna/vehicle-selector";
import { useCitizenRun, type CitizenRun, type CitizenRunState } from "@/lib/maps/citizen-run";
import { toLatLngBounds, useGoogleFit, type FittableMap } from "@/lib/maps/fit";
import {
  GOOGLE_BOOTSTRAP_TIMEOUT_MS,
  googleFallbackNotice,
  googleMapsKey,
  onGoogleAuthFailure,
  type GoogleFallbackReason,
} from "@/lib/maps/google";
import { darkMapStyle } from "@/lib/maps/google-style";
import { usePhotorealTileset, type PhotorealState } from "@/lib/maps/photoreal";
import { useGoogleDeckOverlay, type OverlayFactory } from "@/lib/maps/overlay";
import { useMediaQuery, usePrefersReducedMotion } from "@/lib/hooks";
import type { ReportPin } from "@/lib/api/reports";
import type { RouteCorridor, RoutePlan } from "@/lib/api/route";
import { cn } from "@/lib/utils";

/**
 * Depth at which each vehicle stops, in cm.
 *
 * The same numbers `varuna_route.profiles` routes on and `/map` colours by, so the citizen
 * dashboard and the public map cannot disagree about who is stopped.
 */
export const STOPS_AT_CM: Record<PublicProfile, number> = {
  "two-wheeler": 15,
  car: 30,
  bus: 45,
  pedestrian: 30,
};

/** A point the reader tapped on the map, as a destination for the rail's route form. */
export interface MapPoint {
  lon: number;
  lat: number;
}

export interface CitizenMapProps {
  city?: string;
  runId?: string;
  /** The reader's vehicle; sets the depth at which a street turns red. */
  profile: PublicProfile;
  /** The planned trip, drawn as the shortest way, the safe way and what it went around. */
  route?: RoutePlan | null;
  /** The other safe roads the policy spreads across, drawn dimmed behind the chosen one. */
  corridors?: readonly RouteCorridor[] | null;
  selectedCorridorId?: string | null;
  /** Set to let the reader pick a destination by tapping the map. */
  onPickPoint?: (point: MapPoint) => void;
  /** The run the map drew, so the header can stamp it. */
  onRunLoaded?: (run: CitizenRun) => void;
  /**
   * The forecast step the streets are coloured at, 0 to 35 in five-minute steps from the run's
   * cycle. 0, now, unless the screen offers a slider: the "passable until" times carry the forecast
   * otherwise.
   */
  step?: number;
  /**
   * Citizen reports, drawn as `--obs-report` pins with their status in the outline and a ring when
   * a photo is attached, on every path (`layers/reports.ts`, motion M32). Empty by default.
   */
  reports?: readonly ReportPin[];
  /** The report whose card is open. */
  selectedReportId?: string | null;
  /** A report pin was tapped. Absent leaves the pins unpickable. */
  onPickReport?: (id: string) => void;
  /**
   * A point to bring into view, such as a report chosen from a list. Applied once per `key`, so
   * the reader's own pan afterwards is not taken back. The desk's `MapFocus`, on every path.
   */
  focus?: MapFocus | null;
  className?: string;
}

/** The step a citizen sees unless the screen says otherwise: now. */
const NOW_STEP = 0;

/**
 * The slice of `google.maps.Map` a focus uses. Structural, as `FittableMap` is: the ambient
 * `google` namespace is not in this app's type program, and a test can drive it.
 */
export interface FocusableMap {
  panTo?(latLng: { lat: number; lng: number }): void;
  setCenter?(latLng: { lat: number; lng: number }): void;
  getZoom?(): number | undefined;
  setZoom?(zoom: number): void;
}

/**
 * Bring a focus into view on Google's map, once per `key` (motion M10's camera move in Google's
 * vocabulary). It zooms in to the focus's zoom and never out from a closer view the reader chose,
 * as the console's flight does; `panTo` is Google's own glide, and under reduced motion the camera
 * cuts with `setCenter`, which is M10's fallback.
 */
export function useGoogleFocus(
  map: FocusableMap | null,
  focus: MapFocus | null,
  reducedMotion: boolean,
): void {
  const applied = useRef<string | null>(null);
  useEffect(() => {
    if (!map || !focus || applied.current === focus.key) return;
    applied.current = focus.key;
    const at = { lat: focus.lat, lng: focus.lon };
    const zoom = map.getZoom?.();
    if (focus.zoom !== undefined && (zoom === undefined || zoom < focus.zoom)) {
      map.setZoom?.(focus.zoom);
    }
    if (reducedMotion) map.setCenter?.(at);
    else map.panTo?.(at);
  }, [map, focus, reducedMotion]);
}

/**
 * What a map tap asks deck before it becomes a destination: is a report pin under this pixel?
 *
 * **Not a timestamp.** Google hands one tap to deck's overlay and to the map's own `onClick` as two
 * listeners, and this used to decide "same tap" by how close together the two fired. Driven in a
 * browser on 2026-09-27 that guard failed on every pin - the card opened *and* the tap became the
 * trip's destination - because the order and spacing of those listeners is Google's, not ours. So
 * the map's click asks deck directly, at the tap's own pixel, with the same picking radius deck
 * dispatches its own click with: the answer is the one deck is about to act on, whichever listener
 * runs first.
 */
export interface ReportPicker {
  pickObject?: (options: { x: number; y: number; radius?: number; layerIds?: string[] }) => unknown;
}

/** The part of a Google map click this file reads. `domEvent` and `map` are vis.gl's. */
export interface MapTap {
  detail?: { latLng?: { lat: number; lng: number } | null };
  domEvent?: unknown;
  map?: { getDiv?: () => Element | null } | null;
}

/** Where on the map the tap landed, in the container pixels deck picks in; null if unknown. */
export function tapPixel(
  domEvent: unknown,
  container: Element | null | undefined,
): { x: number; y: number } | null {
  if (!container || !domEvent || typeof domEvent !== "object") return null;
  const source = domEvent as {
    clientX?: unknown;
    clientY?: unknown;
    changedTouches?: ArrayLike<{ clientX: number; clientY: number }>;
  };
  const touch = source.changedTouches?.[0];
  const clientX = typeof source.clientX === "number" ? source.clientX : touch?.clientX;
  const clientY = typeof source.clientY === "number" ? source.clientY : touch?.clientY;
  if (typeof clientX !== "number" || typeof clientY !== "number") return null;
  const rect = container.getBoundingClientRect();
  return { x: clientX - rect.left, y: clientY - rect.top };
}

/**
 * True when a report pin is under the tap, by deck's own picking. False when deck cannot say - no
 * overlay yet, no pixel, a picking error - which is also when deck itself opens no card, so a tap
 * is never both a report and a destination.
 */
export function tapHitsReport(
  picker: ReportPicker | null | undefined,
  tap: MapTap,
  fallbackContainer: Element | null | undefined,
): boolean {
  if (!picker?.pickObject) return false;
  const pixel = tapPixel(tap.domEvent, tap.map?.getDiv?.() ?? fallbackContainer);
  if (!pixel) return false;
  try {
    // `layerIds` matches by prefix in deck, so this is every report layer and nothing else. Radius
    // 0 is the overlay's own `pickingRadius`, the one its click is dispatched with.
    return Boolean(picker.pickObject({ ...pixel, radius: 0, layerIds: [REPORT_LAYER_PREFIX] }));
  } catch {
    return false;
  }
}

/** The width from which the dashboard puts its rail beside the map, as `dashboard-screen.tsx` does. */
const WIDE_QUERY = "(min-width: 1024px)";

/**
 * Where Google's zoom buttons go on a phone: `ControlPosition.RIGHT_CENTER`, read from the loaded
 * API because the ambient `google` namespace is not in this app's type program. Undefined until the
 * API has loaded, which leaves Google's own default.
 *
 * On a phone the bottom of the map is the time strip and the sheet, and Google's default,
 * bottom-right, put the zoom buttons partly under both. Halfway up the right edge they are clear of
 * the strip, the sheet, the report card along the top and the 3D switch in the corner.
 */
export function phoneZoomPosition(): number | undefined {
  const maps = (globalThis as { google?: { maps?: { ControlPosition?: Record<string, number> } } })
    .google?.maps;
  return maps?.ControlPosition?.RIGHT_CENTER;
}

/** Stable empties, so `CityMap`'s memos are not rebuilt by a fresh `[]` on every render. */
const NO_FRAMES: readonly (ImageBitmap | null)[] = [];
const NO_SURCHARGE: readonly [] = [];
const NO_HOTSPOTS: readonly [] = [];
const NO_REPORTS: readonly ReportPin[] = [];

/**
 * Which lines to draw, and in what character.
 *
 * The chosen corridor is the VARUNA route; the other corridors are `alternate`, which the palette
 * already draws at half strength, so "this one, and there are others" reads without a legend. The
 * avoided streets are drawn in the depth ramp's deepest red so the detour visibly goes around
 * something (SPEC.md 7.4).
 */
export function routeLines(
  plan: RoutePlan | null | undefined,
  corridors: readonly RouteCorridor[] | null | undefined,
  selectedCorridorId: string | null | undefined,
): RouteLine[] {
  if (!plan) return [];
  const lines: RouteLine[] = [];

  if (plan.naive?.path?.length) {
    lines.push({ id: "naive", path: plan.naive.path, kind: "naive" });
  }
  for (const avoided of plan.avoided) {
    if (avoided.path?.length) {
      lines.push({ id: `avoided-${avoided.segmentId}`, path: avoided.path, kind: "avoided" });
    }
  }

  const list = corridors ?? [];
  const chosen =
    list.find((c) => c.id === selectedCorridorId) ?? list.find((c) => c.assigned) ?? null;
  for (const corridor of list) {
    if (!corridor.route?.path?.length) continue;
    if (corridor === chosen) continue;
    lines.push({ id: `corridor-${corridor.id}`, path: corridor.route.path, kind: "alternate" });
  }

  const safe = chosen?.route ?? plan.varuna;
  if (safe?.path?.length) {
    lines.push({ id: "varuna", path: safe.path, kind: "varuna" });
  }
  return lines;
}

interface CitizenLayerInputs {
  run: CitizenRun | null;
  profile: PublicProfile;
  step: number;
  routes: readonly RouteLine[];
  picked: MapPoint | null;
  reports: readonly ReportPin[];
  selectedReportId: string | null;
  onPickReport?: (id: string) => void;
}

/**
 * Everything deck draws on the Google path, in section 6.7's order: water, routes, the citizens'
 * reports, then the reader's own pin. The fallback and 3D paths hand the same inputs to `CityMap`,
 * which builds the same layers from the same modules.
 */
export function useCitizenLayers({
  run,
  profile,
  step,
  routes,
  picked,
  reports,
  selectedReportId,
  onPickReport,
}: CitizenLayerInputs): unknown[] {
  const reducedMotion = usePrefersReducedMotion();
  const progress = useRouteProgress(routes, reducedMotion);
  const shownReports = useReportDrops(reports, reducedMotion);

  return useMemo(
    () => [
      ...wetStreetsLayers({
        segments: run?.segments ?? [],
        step,
        show: true,
        diffMode: false,
        wipeLon: 0,
        // Three colours, not six bands: a reader needs to know whether to turn around.
        passableBelowCm: STOPS_AT_CM[profile],
        pickable: false,
      }),
      ...routeLayers({ routes, progress }),
      ...reportPinLayers({
        reports: shownReports,
        selectedReportId,
        onPick: onPickReport,
        reducedMotion,
      }),
      ...(picked
        ? [
            new ScatterplotLayer<MapPoint>({
              id: "citizen-picked-point",
              data: [picked],
              getPosition: (d) => [d.lon, d.lat],
              getRadius: 9,
              radiusUnits: "pixels",
              getFillColor: TRUTH_FILL,
              getLineColor: TRUTH_RING,
              lineWidthUnits: "pixels",
              getLineWidth: 2,
              stroked: true,
              pickable: false,
            }),
          ]
        : []),
    ],
    [
      run,
      profile,
      step,
      routes,
      progress,
      picked,
      shownReports,
      selectedReportId,
      onPickReport,
      reducedMotion,
    ],
  );
}

/** Inside `<Map>`: owns the deck overlay and the fit, both keyed on the live map instance. */
function GoogleLayers({
  layers,
  bounds,
  factory,
  focus,
}: {
  layers: unknown[];
  bounds: ReturnType<typeof cityBounds>;
  factory: OverlayFactory;
  focus: MapFocus | null;
}) {
  const map = useMap() as unknown as (FittableMap & FocusableMap & object) | null;
  const reducedMotion = usePrefersReducedMotion();
  useGoogleDeckOverlay(map, layers, factory);
  useGoogleFit(map, bounds);
  useGoogleFocus(map, focus, reducedMotion);
  return null;
}

/**
 * What the map shows when it has no run to draw.
 *
 * Named states, not a blank rectangle (SPEC.md 6.11): the load, an empty state carrying the
 * API's own sentence about which command produces a run, and an error that says what failed. A
 * skeleton, never a spinner (6.9).
 */
function RunState({ state }: { state: CitizenRunState }) {
  if (state.kind === "ready") return null;
  return (
    <div className="bg-ink/90 absolute inset-0 z-20 flex items-center justify-center p-6">
      {state.kind === "loading" ? (
        <div className="w-[260px]">
          <Skeleton className="h-2 w-full rounded-full" />
          <p className="type-small text-text-2 mt-3">Loading the streets around you</p>
        </div>
      ) : (
        <EmptyState
          size="sm"
          title={state.kind === "empty" ? "No forecast yet" : "The forecast did not load"}
          description={state.message}
        />
      )}
    </div>
  );
}

/**
 * The switch between the map the reader knows and the city they recognise.
 *
 * It is offered whether or not the photorealistic tiles turn out to be available, because finding
 * out costs a request to Google and SPEC.md 17's "never a dead control" is satisfied by a switch
 * that answers rather than by one that is hidden: pressing it either draws the photographed city
 * or prints the sentence saying which switch in the Cloud console is off.
 */
function ThreeDToggle({ on, onChange }: { on: boolean; onChange: (next: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      data-slot="citizen-3d-toggle"
      onClick={() => onChange(!on)}
      className={cn(
        "rounded-control border-line type-small absolute top-3 right-3 z-10 flex h-11 items-center gap-2 border px-3",
        on ? "border-tide bg-tide/20 text-text" : "bg-ink/80 text-text-2",
      )}
    >
      <Box aria-hidden="true" size={16} strokeWidth={1.75} />
      Photorealistic city
    </button>
  );
}

/** Why the photorealistic city is not drawn, in Google's own terms and VARUNA's own words. */
function PhotorealNotice({ state }: { state: PhotorealState }) {
  if (state.kind === "ready" || state.kind === "off") return null;
  return (
    <p
      data-slot="photoreal-notice"
      className="rounded-control border-line bg-ink/80 text-text-2 type-micro absolute top-16 right-3 z-10 max-w-[min(90%,40ch)] border px-2.5 py-1.5"
    >
      {state.kind === "loading" ? "Asking Google for the photorealistic city." : state.message}
    </p>
  );
}

/** One sentence, always present, saying which basemap the reader is looking at. */
function FallbackNotice({ reason }: { reason: GoogleFallbackReason }) {
  return (
    <p
      data-slot="basemap-notice"
      className="rounded-control border-line bg-ink/80 text-text-2 type-micro pointer-events-none absolute top-3 left-3 z-10 max-w-[min(90%,34ch)] border px-2.5 py-1.5"
    >
      {googleFallbackNotice(reason)}
    </p>
  );
}

export function CitizenMap({
  city = "mumbai",
  runId,
  profile,
  route = null,
  corridors = null,
  selectedCorridorId = null,
  onPickPoint,
  onRunLoaded,
  step = NOW_STEP,
  reports = NO_REPORTS,
  selectedReportId = null,
  onPickReport,
  focus = null,
  className,
}: CitizenMapProps) {
  // Read once: the key is inlined at build time and cannot change while the page is open.
  const [apiKey] = useState(() => googleMapsKey());
  const [fallback, setFallback] = useState<GoogleFallbackReason | null>(apiKey ? null : "no-key");
  const [loaded, setLoaded] = useState(false);
  /** Google has actually painted tiles. Until then its map is covered: see the return below. */
  const [tilesDrawn, setTilesDrawn] = useState(false);
  const [picked, setPicked] = useState<MapPoint | null>(null);
  /** The reader has asked for the photographed city. Off on load: it is a second Google product,
   * it needs a network, and the flat map is the one that answers "can I get through?" fastest. */
  const [wantThreeD, setWantThreeD] = useState(false);
  const photoreal = usePhotorealTileset(wantThreeD);
  const showThreeD = wantThreeD && photoreal.kind === "ready";

  const state = useCitizenRun(city, runId);
  const run = state.kind === "ready" ? state.run : null;

  useEffect(() => {
    if (run) onRunLoaded?.(run);
  }, [run, onRunLoaded]);

  // TECH_SPEC 2.5: a bootstrap that has not finished in four seconds is a bootstrap the reader
  // should stop waiting for. Google's own script has no timeout of its own.
  useEffect(() => {
    if (!apiKey || loaded || fallback) return;
    const timer = window.setTimeout(
      () => setFallback((current) => current ?? "timeout"),
      GOOGLE_BOOTSTRAP_TIMEOUT_MS,
    );
    return () => window.clearTimeout(timer);
  }, [apiKey, loaded, fallback]);

  const onError = useCallback(() => setFallback((current) => current ?? "error"), []);
  const onLoad = useCallback(() => setLoaded(true), []);
  const onTiles = useCallback(() => setTilesDrawn(true), []);

  // An auth failure is the one Google error that arrives through neither `onError` nor the
  // timeout: the script loads and the map mounts, then Google paints its own grey surface over
  // our screen. `gm_authFailure` is how it tells us, and it is how the reader gets VARUNA's map
  // instead of somebody else's error message.
  useEffect(() => {
    if (!apiKey) return;
    return onGoogleAuthFailure(() => setFallback((current) => current ?? "refused"));
  }, [apiKey]);

  const routes = useMemo(
    () => routeLines(route, corridors, selectedCorridorId),
    [route, corridors, selectedCorridorId],
  );
  const layers = useCitizenLayers({
    run,
    profile,
    step,
    routes,
    picked,
    reports,
    selectedReportId,
    onPickReport,
  });

  // The Google path's deck overlay, kept so a map tap can ask it what is under the finger
  // (`tapHitsReport`). One factory for the component's life: `useGoogleDeckOverlay` rebuilds the
  // overlay when its factory changes, and it must not.
  const overlayRef = useRef<ReportPicker | null>(null);
  const overlayFactory = useCallback<OverlayFactory>((options) => {
    const overlay = new GoogleMapsOverlay(options);
    overlayRef.current = overlay;
    return overlay as unknown as ReturnType<OverlayFactory>;
  }, []);
  const containerRef = useRef<HTMLDivElement>(null);

  // Named places, so the map says "KEM Hospital" rather than showing an unlabelled junction. A
  // few hundred points, unlike the building and drain layers, which this screen never loads.
  const [facilities, setFacilities] = useState<readonly FacilityLabel[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    loadFacilityLabels(city, controller.signal)
      .then(setFacilities)
      // A map without names is still a map; nothing here is worth an error state.
      .catch(() => undefined);
    return () => controller.abort();
  }, [city]);
  const labels = useMemo(
    () => facilities.map((f) => ({ id: f.id, text: f.text, lon: f.lon, lat: f.lat, kind: f.kind })),
    [facilities],
  );

  const bounds = useMemo(() => cityBounds(city), [city]);
  const wide = useMediaQuery(WIDE_QUERY);
  // Recomputed once Google has loaded (`loaded` re-renders this), which is when the enum exists.
  const zoomPosition = !wide && loaded ? phoneZoomPosition() : undefined;
  const zoomControlOptions = useMemo(
    () => (zoomPosition === undefined ? undefined : { position: zoomPosition }),
    [zoomPosition],
  );
  /** The one thing the 3D path asks `CityMap` for, memoised so it is one identity per city. */
  const threeDOverlay = useMemo<MapOverlay>(() => ({ city, threeD: true }), [city]);
  // Resolved inside the component, not at module scope, so a theme override reaches the tiles.
  const styles = useMemo(() => darkMapStyle(), []);

  const pick = useCallback(
    (event: MapTap) => {
      const at = event.detail?.latLng;
      if (!at) return;
      // A tap on a report pin opens its card (deck's own click does that) and is not also a new
      // destination. Pins are pickable only when the screen can open a card.
      if (onPickReport && tapHitsReport(overlayRef.current, event, containerRef.current)) return;
      const point = { lon: at.lng, lat: at.lat };
      setPicked(point);
      onPickPoint?.(point);
    },
    [onPickPoint, onPickReport],
  );

  // The photographed city. Deck stands alone here, as it does on the console: Google's tiles are
  // the ground and every VARUNA layer is draped on it, so there is no Google *basemap* to mount
  // underneath and the JS API is not loaded on this path at all. The water, the route and the
  // pin are the same layer modules the other two paths use.
  if (showThreeD) {
    return (
      <div className={cn("bg-ink absolute inset-0", className)} data-slot="citizen-map">
        <MapOverlayContext.Provider value={threeDOverlay}>
          <CityMap
            frames={NO_FRAMES}
            rasterBounds={null}
            baseSegments={run?.baseSegments ?? []}
            segments={run?.segments ?? []}
            surcharge={NO_SURCHARGE}
            hotspots={NO_HOTSPOTS}
            routes={routes}
            labels={labels}
            passableBelowCm={STOPS_AT_CM[profile]}
            step={step}
            reports={reports}
            selectedReportId={selectedReportId}
            onPickReport={onPickReport}
            focus={focus}
            bounds={bounds}
            showRaster={false}
            showBuildings={false}
            showSurcharge={false}
            showHotspots={false}
            // Esri's flat imagery would be drawn and then hidden under the mesh; `CityMap` drops
            // it in 3D anyway, and saying so here keeps the intent readable.
            showSatellite={false}
          />
        </MapOverlayContext.Provider>
        <RunState state={state} />
        <ThreeDToggle on={wantThreeD} onChange={setWantThreeD} />
      </div>
    );
  }

  if (fallback || !apiKey) {
    const reason: GoogleFallbackReason = fallback ?? "no-key";
    return (
      <div className={cn("bg-ink absolute inset-0", className)} data-slot="citizen-map">
        {/* VARUNA's own map, over Esri's aerial imagery, built from the same layer modules the
            Google path uses - so the water, the chosen route and the dimmed corridors are
            identical either way, and the fallback is a different basemap rather than a lesser
            answer. The depth raster and the building footprints stay off: 36 decoded frames and
            11 MB of footprints are an operator's load, not a phone's. */}
        <CityMap
          frames={NO_FRAMES}
          rasterBounds={null}
          baseSegments={run?.baseSegments ?? []}
          segments={run?.segments ?? []}
          surcharge={NO_SURCHARGE}
          hotspots={NO_HOTSPOTS}
          routes={routes}
          labels={labels}
          passableBelowCm={STOPS_AT_CM[profile]}
          step={step}
          reports={reports}
          selectedReportId={selectedReportId}
          onPickReport={onPickReport}
          focus={focus}
          bounds={bounds}
          showRaster={false}
          showBuildings={false}
          showSurcharge={false}
          showHotspots={false}
          showSatellite
        />
        <RunState state={state} />
        <FallbackNotice reason={reason} />
        <ThreeDToggle on={wantThreeD} onChange={setWantThreeD} />
        <PhotorealNotice state={photoreal} />
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={cn("bg-ink absolute inset-0", className)}
      data-slot="citizen-map"
    >
      <APIProvider apiKey={apiKey} onLoad={onLoad} onError={onError}>
        <GoogleMap
          className="size-full"
          // A raster map: Google refuses an inline `styles` array on a vector map, and no cloud
          // Map ID exists for this project (TECH_SPEC 2.3).
          renderingType="RASTER"
          styles={styles}
          defaultBounds={{ ...toLatLngBounds(bounds), padding: 16 }}
          gestureHandling="greedy"
          disableDefaultUI
          zoomControl
          zoomControlOptions={zoomControlOptions}
          clickableIcons={false}
          onClick={onPickPoint ? pick : undefined}
          onTilesLoaded={onTiles}
        >
          <GoogleLayers layers={layers} bounds={bounds} factory={overlayFactory} focus={focus} />
        </GoogleMap>
      </APIProvider>
      {/* Google's own failure surface is white, centred and says "Oops! Something went wrong" -
          somebody else's error message, in somebody else's palette, on the one screen a citizen
          opens during a storm. It appears before `gm_authFailure` reaches us, so the swap to
          VARUNA's map cannot be fast enough to hide it. This covers Google's map until it has
          actually drawn tiles: the reader sees the app's own background and a skeleton, then
          either Google's tiles or VARUNA's map, and never an error that is not ours. */}
      {tilesDrawn ? null : (
        <div className="bg-ink absolute inset-0" aria-hidden>
          <Skeleton className="size-full rounded-none" />
        </div>
      )}
      <ThreeDToggle on={wantThreeD} onChange={setWantThreeD} />
      <PhotorealNotice state={photoreal} />
    </div>
  );
}

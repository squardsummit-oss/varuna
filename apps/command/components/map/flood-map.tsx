"use client";

/**
 * The console's live flood map: loads one baked run and scrubs it (SPEC.md 7.2, tasks P6.2-P6.4).
 *
 * This is the piece that turns everything behind it into something a judge can read from across a
 * room — the conditioned 30 m terrain, the 50,110-node inferred drain graph, the coupled solver's
 * depth field — and it is deliberately thin. All it does is load a run, hold the current step, and
 * hand `CityMap` the frame and the segment colours for that step.
 *
 * Every state it can be in is real and named (SPEC.md 6.11): loading with its progress, an empty
 * state that says which command produces a run, an error that says what failed, and the map.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CloudOff } from "lucide-react";

import {
  CityMap,
  type Isochrone,
  type MapFocus,
  type SegmentPath,
  type SegmentPick,
  type TruthPin,
} from "./city-map";
import type { ReportPin } from "@/lib/api/reports";
import type { CityMapMode } from "./types";
import { cityBounds, type Bbox } from "./basemap";
import { affectedFrameAt, WHOLE, type AffectedFrame } from "@/lib/map/affected-bounds";
import type { FitPadding } from "./layers/camera";
import {
  loadBuildings,
  loadDrainNodes,
  loadDrains,
  loadFacilityLabels,
  type BuildingPolygon,
  type DrainPath,
  type FacilityLabel,
} from "@/lib/api/city-layers";
import { useMapOverlay } from "./layers/overlay-context";
import type { DrainNode } from "./layers/types";
import { apiUrl } from "@/lib/api/client";
import { allSegments, joinSegments, loadRunDepth, type RunDepth } from "@/lib/api/run-depth";
import type { Hotspot } from "@/lib/api/hotspots";
import type { SurchargeSet } from "@/lib/api/surcharge";
import { reversedEdgesAtStep } from "./layers/reversed-flow";
import { EmptyState } from "@/components/varuna/empty-state";
import { Button } from "@/components/ui/button";
import { currentCity } from "@/lib/city";
import {
  loadOfflineBasemap,
  OFFLINE_BASEMAP_ATTRIBUTION,
  offlineBasemapLayers,
  type OfflineBasemap,
} from "@/lib/offline/basemap";
import { offlineLine, queueLine, useOfflineForecast } from "@/lib/offline/forecast-status";

/** The terrain credit, which holds offline too: the depths were solved on it. */
const TERRAIN_CREDIT = "Terrain: Copernicus GLO-30";

/** Stable empty default for `drains`: a fresh `[]` in the parameter list would change identity on
 * every render and re-run the memo below (and `CityMap`'s layer build) for the screens - the
 * public map, the landing hero - that never pass a posterior. */
const NO_LEARNED_DRAINS: readonly DrainPath[] = [];

type Status =
  | { kind: "loading"; done: number; total: number }
  | { kind: "ready"; run: RunDepth; segments: SegmentPath[]; baseSegments: SegmentPath[] }
  | { kind: "empty"; message: string }
  | { kind: "error"; message: string };

/** The load states a screen can react to: loading, ready, empty (nothing baked) or error. */
export type FloodMapStatusKind = Status["kind"];

export interface FloodMapProps {
  /**
   * Which city's layers and - when no `runId` pins one - whose newest run to draw.
   *
   * Defaults to the city in the address bar rather than to Mumbai, so `?city=chennai` opens a
   * Chennai map on any screen that hosts this map without having to thread the slug through
   * itself (task D-09). With no `?city=` that is Mumbai, as it has always been.
   */
  city?: string;
  runId?: string;
  /** Current step, owned by the time bar so keyboard and play share one clock. */
  step: number;
  onLoaded?: (run: RunDepth) => void;
  /** The load's state whenever it changes, for a screen that words its own honesty line. */
  onStatus?: (kind: FloodMapStatusKind) => void;
  /** Hold the run load until the screen knows which run to ask for; the loading state shows. */
  deferLoad?: boolean;
  /** The run's ranked hotspots, drawn as 120 m rings (section 6.7). */
  hotspots?: readonly Hotspot[];
  selectedHotspotId?: string | null;
  /** The run's surcharging manholes; only those active at the current step are drawn. */
  surcharge?: SurchargeSet | null;
  showSurcharge?: boolean;
  /** Building footprints, and their 11 MB fetch. Off unless asked for, as on `CityMap`. */
  showBuildings?: boolean;
  showDrains?: boolean;
  /** Camera target from the rail; a new `key` starts a new flight (motion M10). */
  focus?: MapFocus | null;
  /** Reachability bands from the right rail, drawn over the streets (section 6.7). */
  isochrones?: readonly Isochrone[];
  /**
   * The run's learned pipes, joined onto the city's inferred network by edge id.
   *
   * The city layer carries all 49,770 edges at the prior the pipeline gave them; a run's
   * drain-health product carries the worst 6,000 at the posterior Pulse learned. Passing the
   * latter here re-colours the pipes the filter actually moved and leaves the rest at their
   * prior, which is the honest picture: most of Mumbai's drains have never been observed.
   * Omitted, the map draws the whole network at its prior.
   */
  drains?: readonly DrainPath[];
  /**
   * The box the camera frames on its *first* paint. Defaults to the city's AOI.
   *
   * Passed through to `CityMap`'s camera untouched, so a screen that must open on a known frame -
   * the ward officer's desk, where motion M27's globe cross-fades into this map and the two have
   * to be looking at the same place - gets that frame without a fly-to.
   */
  bounds?: Bbox;
  /**
   * What the camera opens on once the run has loaded. `drawn` (the default) is every street, as
   * it always was; `affected` is the run's main affected area at its peak step
   * (`lib/map/affected-bounds.ts`), which is computed once per run and never follows the scrub.
   */
  frameOn?: "drawn" | "affected";
  /**
   * With `frameOn="affected"`, frame every qualifying street with no window (`WHOLE`): the
   * console's full view, which shows the whole affected picture rather than its densest part.
   * Read at the peak or at `frameStep`, never at the live scrub, so scrubbing never moves the
   * camera.
   */
  frameWhole?: boolean;
  /**
   * With `frameOn="affected"`, read the streets at this step rather than at the run's peak, and
   * fall back to the peak when the step has too little water to frame (`affectedFrameAt`). The
   * console passes the step on screen when full view opens and holds it there, so the scrub still
   * never moves the camera. Absent or null, the peak.
   */
  frameStep?: number | null;
  /**
   * With `frameOn="affected"`, how many of the run's top-ranked chronic spots the frame must hold
   * (`include`): the console passes the rows its rail lists first, so the map opens on what the
   * rail names. 0, the default, frames the water alone.
   */
  frameHolds?: number;
  /** The fit's margin, per side where the screen floats panels over the map (`CityMap`). */
  fitPadding?: FitPadding;
  /** Change it to re-arm the fit after the operator has moved the camera (`CityMap.fitKey`). */
  fitKey?: string | number | null;
  /** The `fitKey` whose camera is kept and given back on return (`CityMap.keepViewOf`). */
  keepViewOf?: string | number | null;
  /** The affected frame whenever it changes, for a screen that says what it is framed on. */
  onFrame?: (frame: AffectedFrame | null) => void;
  /** `hero` makes the map read-only for the landing page's scrub loop (motion M1). */
  mode?: CityMapMode;
  /** Set to draw wet streets in the public map's three colours against this stopping depth. */
  passableBelowCm?: number;
  /** Aerial imagery under everything. On by default, as it is on `CityMap`. */
  showSatellite?: boolean;
  /** Probability mode's threshold in cm; unset draws ordinary depth (task P6.5). */
  probabilityThresholdCm?: number;
  /** Sourced ground-truth pins to drop on the map (task P6.12, motion M18). */
  truthPins?: readonly TruthPin[];
  /** Citizen reports, passed straight to `CityMap` (`layers/reports.ts`, motion M32). */
  reports?: readonly ReportPin[];
  selectedReportId?: string | null;
  /** A report pin was tapped. Absent leaves the pins unpickable. */
  onPickReport?: (id: string) => void;
  /** A wet street was clicked (task P6.9); absent leaves the streets unpickable. */
  onSegmentPick?: (pick: SegmentPick | null) => void;
  /** Off where `MapSlot` sits behind this map and draws the credit already. */
  attribution?: boolean;
  showRaster?: boolean;
  showSegments?: boolean;
  showHotspots?: boolean;
}

export function FloodMap({
  city = currentCity(),
  runId,
  step,
  onLoaded,
  onStatus,
  deferLoad = false,
  hotspots: ranked = [],
  selectedHotspotId = null,
  surcharge: surchargeSet = null,
  showSurcharge = true,
  showBuildings = false,
  showDrains = false,
  drains: learned = NO_LEARNED_DRAINS,
  bounds,
  frameOn = "drawn",
  frameWhole = false,
  frameStep = null,
  frameHolds = 0,
  fitPadding,
  fitKey = null,
  keepViewOf = null,
  onFrame,
  focus = null,
  isochrones = [],
  mode = "console",
  passableBelowCm,
  probabilityThresholdCm,
  truthPins,
  reports,
  selectedReportId,
  onPickReport,
  onSegmentPick,
  showSatellite = true,
  attribution = true,
  showRaster = true,
  showSegments = true,
  showHotspots = true,
}: FloodMapProps) {
  const [status, setStatus] = useState<Status>({ kind: "loading", done: 0, total: 36 });
  const [attempt, setAttempt] = useState(0);
  const loadedRef = useRef<string | null>(null);

  useEffect(() => {
    if (deferLoad) return;
    const controller = new AbortController();
    let cancelled = false;

    (async () => {
      try {
        setStatus({ kind: "loading", done: 0, total: 36 });
        const [run, geojson] = await Promise.all([
          loadRunDepth(
            runId,
            controller.signal,
            (done, total) => {
              if (!cancelled) setStatus({ kind: "loading", done, total });
            },
            // Only read when no `runId` pins the load: it picks whose newest run answers (D-09).
            city,
          ),
          fetch(apiUrl(`/v1/city/${city}/layers/segments`), { signal: controller.signal })
            .then((r) => (r.ok ? r.json() : { features: [] }))
            .catch(() => ({ features: [] })),
        ]);
        if (cancelled) return;
        const segments = joinSegments(geojson, run.depthCm);
        const baseSegments = allSegments(geojson);
        setStatus({ kind: "ready", run, segments, baseSegments });
        if (loadedRef.current !== run.provenance.runId) {
          loadedRef.current = run.provenance.runId;
          onLoaded?.(run);
        }
      } catch (error) {
        if (cancelled || controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : String(error);
        // The API's 404 for "nothing baked yet" is not a failure of the console; it is the
        // honest empty state, and it already carries the command that fixes it.
        const isEmpty = /No baked run|make bake|Compute live/i.test(message);
        setStatus(isEmpty ? { kind: "empty", message } : { kind: "error", message });
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [city, runId, attempt, onLoaded, deferLoad]);

  const retry = useCallback(() => setAttempt((a) => a + 1), []);

  // Offline (task P9.10). Only a page the offline worker controls - the public map and the report
  // flow - can ever read a saved copy; every other screen stays `online` and draws what it drew.
  const offline = useOfflineForecast();
  const usingSaved = offline.controlled && offline.mode !== "online";
  const [basemap, setBasemap] = useState<OfflineBasemap | null>(null);
  useEffect(() => {
    if (!usingSaved || basemap) return;
    const controller = new AbortController();
    loadOfflineBasemap(city, controller.signal)
      .then(setBasemap)
      // No saved basemap: the city's own streets still draw, from the saved street layer.
      .catch(() => undefined);
    return () => controller.abort();
  }, [usingSaved, basemap, city]);
  const vectorBasemap = useMemo(
    () => (usingSaved ? offlineBasemapLayers(basemap) : []),
    [usingSaved, basemap],
  );
  const offlineNote = usingSaved ? offlineLine(offline.mode, offline.saved) : null;
  const queueNote = queueLine(offline.pending, offline.sent);

  useEffect(() => {
    onStatus?.(status.kind);
  }, [status.kind, onStatus]);

  // The city's context layers, fetched *after* the run so they never delay the flood. Buildings
  // are 11 MB and the drain graph is 18 MB; putting either on the critical path would mean
  // staring at a progress bar before seeing a single street.
  const [buildings, setBuildings] = useState<readonly BuildingPolygon[]>([]);
  useEffect(() => {
    if (!showBuildings || buildings.length > 0) return;
    const controller = new AbortController();
    loadBuildings(city, controller.signal)
      .then(setBuildings)
      // A map without footprints is still a map. Nothing here is worth an error state.
      .catch(() => undefined);
    return () => controller.abort();
  }, [city, showBuildings, buildings.length]);

  // The drain X-ray asks for the same network as the Drains layer, so the two share one fetch:
  // switching the X-ray on after the Drains layer costs nothing, and either way round the 18 MB
  // is paid once.
  const overlay = useMapOverlay();
  const xrayOn = Boolean(overlay.xray);
  const wantNetwork = showDrains || xrayOn;

  // Drains only when asked for: section 6.7 has them off by default, and they are the biggest
  // layer VARUNA serves.
  const [network, setNetwork] = useState<readonly DrainPath[]>([]);
  useEffect(() => {
    if (!wantNetwork || network.length > 0) return;
    const controller = new AbortController();
    loadDrains(city, controller.signal)
      .then(setNetwork)
      .catch(() => undefined);
    return () => controller.abort();
  }, [city, wantNetwork, network.length]);

  // The graph's 49,897 nodes, about 9 MB, and only the X-ray wants them: they are what a manhole
  // shaft and an outfall marker are drawn from. Without them the X-ray still draws its pipes and
  // says in its own summary that the shafts are absent, so a slow or failed load degrades to a
  // smaller true picture rather than to an invented one.
  const [nodes, setNodes] = useState<readonly DrainNode[]>([]);
  useEffect(() => {
    if (!xrayOn || nodes.length > 0) return;
    const controller = new AbortController();
    loadDrainNodes(city, controller.signal)
      .then(setNodes)
      .catch(() => undefined);
    return () => controller.abort();
  }, [city, xrayOn, nodes.length]);

  // The whole network at its prior, with the run's learned pipes drawn over it at their
  // posterior - the same join `/drains` makes, so the console's Drains mode and the X-ray
  // colour the same pipe the same way. Before the 18 MB network arrives, the learned pipes are
  // what there is to draw, so the layer is never empty once the operator has asked for it.
  const drains = useMemo(() => {
    if (learned.length === 0) return network;
    if (network.length === 0) return learned;
    const posterior = new Map(learned.map((edge) => [edge.id, edge.beta]));
    return network.map((edge) => {
      const beta = posterior.get(edge.id);
      return beta === undefined ? edge : { ...edge, beta };
    });
  }, [network, learned]);

  // Named facilities, for the label layer. Small (a few hundred points) and worth having early:
  // "KEM Hospital" on the map is what turns a route from two lines into a trip.
  const [facilities, setFacilities] = useState<readonly FacilityLabel[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    loadFacilityLabels(city, controller.signal)
      .then(setFacilities)
      .catch(() => undefined);
    return () => controller.abort();
  }, [city]);

  // Facilities plus the chronic register: the two sets of places this product is about. Street
  // names come from the segments themselves, inside `CityMap`.
  const labels = useMemo(
    () => [
      ...facilities.map((f) => ({
        id: f.id,
        text: f.text,
        lon: f.lon,
        lat: f.lat,
        kind: f.kind,
      })),
      ...ranked.map((h) => ({
        id: `hotspot-${h.id}`,
        text: h.name,
        lon: h.lon,
        lat: h.lat,
        kind: "hotspot" as const,
      })),
    ],
    [facilities, ranked],
  );

  // The rings only need a position and an identity; the rail owns everything else about a
  // hotspot, so the map is not re-created when the scrub moves its depth chips.
  const rings = useMemo(
    () => ranked.map((h) => ({ id: h.id, name: h.name, lon: h.lon, lat: h.lat })),
    [ranked],
  );
  // Only the manholes actually surcharging *now*: a marker that stayed put for the whole run
  // would say the drain is failing at 06:40, when it is not yet.
  const surcharge = useMemo(() => {
    if (!surchargeSet) return [];
    return surchargeSet.nodes
      .map((n) => ({ id: n.id, lon: n.lon, lat: n.lat, q: n.q[step] ?? 0 }))
      .filter((n) => n.q > 0);
  }, [surchargeSet, step]);
  // Pipes running backwards *now* that carry geometry (motion M9). A run baked before the product
  // carried paths yields none, so the layer draws nothing rather than failing.
  const reversedEdges = useMemo(
    () => reversedEdgesAtStep(surchargeSet?.reversedEdges ?? [], step),
    [surchargeSet, step],
  );

  // The main affected area, from the run's own depths at its peak step: once per run, so the
  // scrub never moves the camera. The chronic spots are the fallback for a run with no water deep
  // enough to frame, and the top `frameHolds` of them are held in the frame either way. Only their
  // positions and ranks are read, so the rail's depth chips changing never re-frames.
  const readyStreets = status.kind === "ready" ? status.segments : null;
  const spotKey = [...ranked]
    .sort((a, b) => a.rank - b.rank)
    .map((h) => `${h.lon},${h.lat}`)
    .join(";");
  // A screen that asks for the caption gets the frame whichever way it is framed.
  const wantFrame = frameOn === "affected" || onFrame !== undefined;
  const affected = useMemo<AffectedFrame | null>(() => {
    if (!wantFrame || !readyStreets) return null;
    const spots = spotKey
      ? spotKey.split(";").map((pair) => {
          const [lon, lat] = pair.split(",").map(Number);
          return { lon: lon ?? Number.NaN, lat: lat ?? Number.NaN };
        })
      : [];
    return affectedFrameAt(
      {
        streets: readyStreets,
        hotspots: spots,
        include: spots.slice(0, Math.max(0, frameHolds)),
        fallback: bounds ?? cityBounds(city),
        ...(frameWhole ? WHOLE : {}),
      },
      frameStep,
    );
  }, [wantFrame, readyStreets, spotKey, bounds, city, frameWhole, frameStep, frameHolds]);
  useEffect(() => {
    onFrame?.(affected);
  }, [affected, onFrame]);

  if (status.kind === "loading") {
    const pct = status.total > 0 ? Math.round((status.done / status.total) * 100) : 0;
    return (
      <div className="absolute inset-0 flex items-center justify-center bg-[var(--ink)]">
        <div className="w-[280px]">
          {/* Skeleton shimmer, never a spinner (SPEC.md 6.9). */}
          <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--well)]">
            <div
              className="h-full rounded-full bg-[var(--tide)] transition-[width] duration-200"
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="num mt-3 text-[13px] text-[var(--text-2)]">
            Loading the run: {status.done} of {status.total} depth frames
          </p>
        </div>
      </div>
    );
  }

  if (status.kind === "empty") {
    return (
      <div className="absolute inset-0 flex items-center justify-center bg-[var(--ink)] p-6">
        <EmptyState
          title="No runs yet"
          description="Press Play on the replay, or Compute live. A baked run brings the map to life."
        />
      </div>
    );
  }

  if (status.kind === "error") {
    // Offline with nothing saved says what to do about it, not what the fetch threw.
    const noSavedCopy = usingSaved && !offline.saved;
    return (
      <div className="absolute inset-0 flex items-center justify-center bg-[var(--ink)] p-6">
        <div className="max-w-[420px] text-center">
          <p className="text-[15px] text-[var(--text)]">
            {noSavedCopy
              ? "This phone has no saved forecast yet."
              : "The map could not load this run."}
          </p>
          <p className="mt-2 text-[13px] text-[var(--text-2)]">
            {noSavedCopy
              ? "Open the map once with a connection and the forecast is kept for next time."
              : status.message}
          </p>
          <Button size="sm" variant="outline" className="mt-4" onClick={retry}>
            Try again
          </Button>
        </div>
      </div>
    );
  }

  return (
    <>
      <CityMap
        mode={mode}
        frames={status.run.frames}
        rasterBounds={status.run.bounds}
        baseSegments={status.baseSegments}
        segments={status.segments}
        surcharge={surcharge}
        reversedEdges={reversedEdges}
        showSurcharge={showSurcharge}
        buildings={buildings}
        drains={drains}
        drainNodes={nodes}
        bounds={bounds}
        fitBounds={frameOn === "affected" ? (affected?.bounds ?? null) : null}
        fitKey={fitKey}
        keepViewOf={keepViewOf}
        fitPadding={fitPadding}
        showBuildings={showBuildings}
        showDrains={showDrains}
        hotspots={rings}
        labels={labels}
        isochrones={isochrones}
        passableBelowCm={passableBelowCm}
        probabilityThresholdCm={probabilityThresholdCm}
        truthPins={truthPins}
        reports={reports}
        selectedReportId={selectedReportId}
        onPickReport={onPickReport}
        onSegmentPick={onSegmentPick}
        // Esri's imagery may not be kept offline (P10.6), so a saved forecast draws over VARUNA's own
        // basemap instead, with that basemap's credit.
        showSatellite={showSatellite && !usingSaved}
        basemapLayers={vectorBasemap}
        attribution={attribution && !usingSaved}
        selectedHotspotId={selectedHotspotId}
        focus={focus}
        step={Math.min(step, status.run.provenance.nSteps - 1)}
        showRaster={showRaster}
        showSegments={showSegments}
        showHotspots={showHotspots}
      />
      {offlineNote || queueNote ? (
        <div
          role="status"
          aria-live="polite"
          className="rounded-panel border-line bg-deep pointer-events-none absolute inset-x-3 top-3 z-10 border px-3 py-2"
        >
          {offlineNote ? (
            <p className="type-small text-text flex items-start gap-2">
              <CloudOff aria-hidden="true" className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} />
              <span className="num">{offlineNote}</span>
            </p>
          ) : null}
          {queueNote ? <p className="type-small text-text-2 mt-1">{queueNote}</p> : null}
        </div>
      ) : null}
      {usingSaved && attribution ? (
        <p className="type-micro text-text-3 pointer-events-none absolute inset-x-0 bottom-0 z-10 px-4 py-2">
          {basemap?.attribution ?? OFFLINE_BASEMAP_ATTRIBUTION}; {TERRAIN_CREDIT}
        </p>
      ) : null}
    </>
  );
}

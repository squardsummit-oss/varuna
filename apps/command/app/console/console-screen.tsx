"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Maximize2, Minimize2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { FloodMap } from "@/components/map/flood-map";
import type { Isochrone } from "@/components/map/city-map";
import { apiUrl } from "@/lib/api/client";
import { useLive } from "@/lib/api/live";
import type { LiveEvent } from "@/lib/api/schemas";
import type { RunDepth } from "@/lib/api/run-depth";
import { loadDrainHealth, type DrainHealth } from "@/lib/api/drains";
import { loadHotspots, type Hotspot, type HotspotSet } from "@/lib/api/hotspots";
import type { MapFocus } from "@/components/map/city-map";
import { loadSurcharge, type SurchargeSet } from "@/lib/api/surcharge";
import { reversedFlowSummary } from "@/components/map/layers/reversed-flow";
import { MapOverlayContext, type MapOverlay } from "@/components/map/layers/overlay-context";
import {
  drainXraySummary,
  exaggerationLabel,
  type Drains3dResult,
} from "@/components/map/layers/drains-3d";
import { usePhotorealTileset, type PhotorealState } from "@/lib/maps/photoreal";
import { AppShell } from "@/components/varuna/app-shell";
import { MapSlot } from "@/components/varuna/map-slot";
import { PanelErrorBoundary } from "@/components/varuna/panel-error-boundary";
import { ReplayPanel } from "@/components/varuna/replay-panel";
import { HotspotDrawer } from "@/components/varuna/hotspot-drawer";
import { CyclePicker } from "@/components/varuna/cycle-picker";
import { LayerPanel, type LayerToggles, type LayerKey } from "@/components/varuna/layer-panel";
import {
  isTextEntry,
  registerLayerShortcut,
  type LayerKey as ShortcutLayerKey,
} from "@/lib/shortcuts";
import { ProbabilityLegend } from "@/components/varuna/probability-legend";
import { SegmentPopover } from "@/components/varuna/segment-popover";
import { Skeleton } from "@/components/varuna/skeleton";
import type { SegmentPick } from "@/components/map/city-map";
import { useTruthPins } from "@/lib/hooks/use-truth-pins";
import { RightRail } from "@/components/varuna/right-rail";
import { SkyPanel } from "@/components/varuna/sky-panel";
import { LiveOutlookCard } from "@/components/varuna/live-outlook-card";
import { TimeBar } from "@/components/varuna/time-bar";
import { edgeFadeStyle, useScrollEdges } from "./use-scroll-edges";
import { useConsoleRoutes, type ConsoleRouteState } from "./use-console-routes";
import { WhatIfDrawer, type WhatIfDiff } from "./whatif-drawer";
import { fetchOpeningRunId } from "@/lib/opening-run";
import { formatMassBalance, minutesBetween } from "@/lib/format";
import { DEFAULT_CITY, cityFromSearch } from "@/lib/city";
import { MIN_AREA_M, type AffectedFrame } from "@/lib/map/affected-bounds";
import { DEFAULT_SIM_TIME } from "@/lib/stores/replay";
import { useRunStore } from "@/lib/stores/run";
import { useUiStore } from "@/lib/stores/ui";

/**
 * The command console (SPEC.md section 7.2). The map slot, the replay panel, the right rail and
 * the time bar are the Phase 0 shell; `CityMap`, the real time bar and the hotspot drawer land in
 * Phase 6.
 *
 * The rain panel on the left is Phase 3 scaffolding (task P3.8): it proves the Sky cube is real and
 * readable from the browser before there is a map to draw it on. Phase 6 deletes it - the panel, its
 * toggle and these three lines - and keeps `FanChart`, which the hotspot drawer needs anyway.
 */
/** IST clock time of a step, or a dash before the run has loaded. */
function formatStep(iso: string | undefined): string {
  if (!iso) return "--:--";
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime())
    ? "--:--"
    : parsed.toLocaleTimeString("en-IN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
        timeZone: "Asia/Kolkata",
      });
}

/** What the Routes row says: the trip it drew, with its numbers, or why there is none. */
function routeDetail(state: ConsoleRouteState): string | undefined {
  if (state.kind === "off") return undefined;
  if (state.kind === "loading") return "Planning KEM Hospital to Sion Hospital by ambulance.";
  if (state.kind === "error") return state.message;
  const { plan } = state;
  const varuna = plan.varuna;
  if (!varuna) return "No safe ambulance route between KEM and Sion at this time.";
  const avoided = plan.avoided.length;
  return (
    `KEM to Sion, departing ${formatStep(plan.departAt)}: ${varuna.minutes.toFixed(1)} min, ` +
    (avoided === 0
      ? "nothing on the way predicted impassable for an ambulance."
      : `around ${avoided} street${avoided === 1 ? "" : "s"} an ambulance cannot pass.`)
  );
}

/**
 * Minutes from the run's cycle to `step`'s valid time: the lead the scrub and the caption print.
 *
 * Step i is valid at the cycle plus i + 1 steps (the 06:40 cycle's first step is 06:45), so the
 * lead is read from the two timestamps rather than as `step * stepMin`, which printed every lead
 * five minutes short. A run with no cycle time counts from one step before its first valid time,
 * which is how the cycle writes them (`stepLeads` on `/onboard` does the same).
 */
export function leadMin(
  run: Pick<RunDepth, "validTs"> & {
    provenance: Pick<RunDepth["provenance"], "stepMin" | "cycleTs">;
  },
  step: number,
): number {
  const ts = run.validTs[step];
  const lead = run.provenance.cycleTs && ts ? minutesBetween(run.provenance.cycleTs, ts) : null;
  return lead ?? (step + 1) * run.provenance.stepMin;
}

/** How many of the rail's ranked chronic spots the console's frame must hold: its top five rows. */
export const RAIL_TOP_SPOTS = 5;

/**
 * What the camera's fit keeps clear, in pixels, so the frame lands where nothing floats over it.
 * The layer column is 16 px in and 380 px wide; the scrub card is 16 px up and about 130 px tall
 * with its caption; the replay panel is 16 px in and 360 px wide. Each carries a 16 px gap.
 * Measured before the change, 27-34 % of the framed deep street length sat under the column and
 * another 11-20 % under the scrub card.
 */
export const FIT_CLEARANCE = { column: 412, scrub: 160, replay: 392, edge: 16 } as const;

/** The console's fit margins: the column, the scrub and the replay panel, or in full view the
 *  scrub alone - full view has no column and no replay panel. */
export function consoleFitPadding(fullView: boolean, replayPanelOpen: boolean) {
  const { column, scrub, replay, edge } = FIT_CLEARANCE;
  return fullView
    ? { top: edge, right: edge, bottom: scrub, left: edge }
    : { top: edge, right: replayPanelOpen ? replay : edge, bottom: scrub, left: column };
}

/**
 * What the map is framed on, in one sentence under the scrub (the "fit by default" request).
 * Every number comes from the run: the length, the share and the time are `affectedFrame`'s own,
 * read at the run's peak step, so the time it names is the peak's and the scrub never changes it.
 * The normal view holds the densest 7 km of water and the rail's top five chronic spots; full view
 * holds every qualifying street (`WHOLE`) at `askedStep`, the step on screen when it opened, and
 * says so - and says so too when that step was too dry to frame and it framed the peak instead.
 */
export function frameCaption(
  frame: AffectedFrame | null,
  run: Pick<RunDepth, "validTs"> & {
    provenance: Pick<RunDepth["provenance"], "stepMin" | "cycleTs">;
  },
  fullView = false,
  askedStep: number | null = null,
): string | null {
  if (!frame) return null;
  const back = " F or Esc goes back.";
  if (frame.basis === "aoi") {
    return fullView
      ? `Full view: nothing in this run is wet, so it shows the whole city.${back}`
      : "Nothing in this run is wet, so the map shows the whole city.";
  }
  if (frame.basis === "hotspots") {
    return fullView
      ? `Full view: no street reaches 5 cm in this run, so it frames the chronic spots.${back}`
      : "No street reaches 5 cm in this run, so the map opened on its chronic spots.";
  }
  const km = (frame.lengthM / 1000).toLocaleString("en-IN", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
  const step = frame.step ?? 0;
  const what =
    `streets at ${frame.thresholdCm} cm or more at ` +
    `${formatStep(run.validTs[step])} (+${leadMin(run, step)} min)`;
  const pct = Math.round(frame.share * 100);
  if (fullView) {
    const held = pct >= 100 ? `all ${km} km of ${what}` : `${pct} % of the ${km} km of ${what}`;
    if (askedStep !== null && askedStep !== step && run.validTs[askedStep]) {
      const asked = `${formatStep(run.validTs[askedStep])} (+${leadMin(run, askedStep)} min)`;
      return (
        `Full view: less than ${MIN_AREA_M} m of street is 5 cm deep at ${asked}, ` +
        `so it frames the peak: ${held}.${back}`
      );
    }
    return `Full view: ${held}.${back}`;
  }
  return pct >= 100
    ? `Opened on all ${km} km of ${what}.`
    : `Opened on the densest water and the rail's top ${RAIL_TOP_SPOTS} spots: ${pct} % of the ${km} km of ${what}. Full view (F) shows all of it.`;
}

/**
 * How the console's full view is being shown. `screen` is the Fullscreen API on the map region;
 * `layout` is the fallback when the browser refuses it (or has none), where the region is pinned
 * over the whole viewport instead. Both show the same thing: the map, its scrub and its legend.
 */
export type FullViewMode = "off" | "screen" | "layout";

/**
 * What the photorealistic-city row says: what it is waiting for, what it drew, or which switch is
 * off and where to throw it.
 *
 * The line a judge reads today is the last one: driven at `http://localhost:3000/console` on
 * 2026-09-23, with the key's Cloud project billed and the Map Tiles API enabled, the probe
 * returned `ready` and the row read "Google's photorealistic Mumbai, with the water, the routes
 * and the markers draped on it."
 *
 * The `unavailable` sentence comes from `lib/maps/photoreal.ts`, which words one per reason. It
 * is printed whole rather than summarised: a reason that does not name the page it is fixed on
 * is not a fix. None of those reasons is reachable on this key - they are kept for the project
 * that has not yet been through the billing and Map Tiles switches, which is where this one was
 * earlier the same day.
 */
export function photorealDetail(state: PhotorealState): string | undefined {
  if (state.kind === "off") return undefined;
  if (state.kind === "loading") return "Asking Google for the photorealistic city.";
  if (state.kind === "unavailable") return state.message;
  return "Google's photorealistic Mumbai, with the water, the routes and the markers draped on it.";
}

/**
 * What the X-ray row says, which is the X-ray's own summary plus the two things only this screen
 * knows: whether the ground it is meant to be read under is actually drawn, and that the pipes
 * are in the DEM's vertical frame rather than the tiles'.
 *
 * The datum clause is not hedging. The inverts are orthometric heights on Copernicus GLO-30 and
 * Google's photorealistic mesh is at WGS84 ellipsoidal height; over western India the geoid
 * separation is tens of metres and nobody here has measured it, so the offset is left at 0 and
 * the screen says the two frames have not been reconciled (`.wf/DRAINS-requests.md` section 5).
 * Quietly shipping a guessed offset would look right and be wrong.
 */
export function xrayDetail(
  result: Drains3dResult,
  threeD: boolean,
  exaggeration: number,
): string | undefined {
  if (result.kind === "off") return undefined;
  const parts = [drainXraySummary(result)];
  if (result.kind === "ready") {
    if (exaggeration !== 1) parts.push(`${exaggerationLabel(exaggeration)}.`);
    parts.push(
      threeD
        ? "Depths are in the DEM's vertical frame; its offset from Google's ellipsoidal ground has not been measured, so the network may sit high or low as a whole."
        : "Switch the photorealistic city on to look along them under the street.",
    );
  }
  return parts.join(" ");
}

/** The one socket topic the console itself listens to: a published live run. */
const LIVE_RUN_TOPICS = ["runs.published"] as const;

/** Stable empty bands, for when I has hidden the isochrones. */
const NO_ISOCHRONES: Isochrone[] = [];

/**
 * The stretches the X-ray offers, as whole multiples so `exaggerationLabel` reads as a sentence.
 *
 * 1 is the truth, and the truth is thin: the pipeline lays every node at a fixed cover, measured
 * over `city/mumbai/drain_nodes.parquet` on 2026-09-23 as exactly 1.50 m on all 49,897 nodes bar
 * the trunks, which are 3.00 m (median 1.50, p10 1.50, p90 1.50, max 3.00). At 30 m ground
 * resolution and a 55 degree camera that is a couple of pixels of separation. 4 puts the ordinary
 * cover at 6 m, about a storey, which reads; 8 puts it at 12 m, which is for following one pipe
 * rather than for reading the network. Both are labelled on screen as stretches.
 */
const XRAY_EXAGGERATIONS = [1, 4, 8] as const;

/** Motion M7: 5-minute steps advance about three a second while playing. */
const PLAY_INTERVAL_MS = 320;

/** How many learned pipes to ask for. The cycle writes the 6,000 worst by blockage, which is what
 * `/drains` asks for too, so the console's Drains mode and the X-ray colour the same set. */
const LEARNED_EDGE_LIMIT = 6000;

/**
 * The console, behind the Suspense boundary `useSearchParams` needs.
 *
 * Without it `next build` refuses the route: a client component reading the query string cannot be
 * prerendered, and Next asks for the boundary rather than opting the whole page into client
 * rendering. The fallback is the empty map slot the console shows before its run has loaded anyway.
 */
export function ConsoleScreen() {
  return (
    <Suspense
      fallback={
        <AppShell>
          <div className="relative h-full min-h-0 w-full">
            <MapSlot />
          </div>
        </AppShell>
      }
    >
      <ConsoleView />
    </Suspense>
  );
}

function ConsoleView() {
  const router = useRouter();
  // The query string, read through `useSearchParams` rather than `window.location`.
  //
  // It used to be read once, in a `useState` initialiser. Under the App Router that runs while the
  // router is still mid-navigation, so a `next/link` to `/console?run=<id>` landed with no run and
  // fell back to the newest - and a second link, from one console URL to another, never changed
  // anything at all, because an initialiser runs once per mount. This hook is the router's own
  // value and re-renders when it changes, which is what makes a navigation land where it points.
  const searchParams = useSearchParams();
  const search = searchParams.toString();
  const pinnedRun = searchParams.get("run") ?? undefined;
  // Which city this console is of. Only read by the fetches, never rendered, so the server's
  // Mumbai and a client's `?city=` can never disagree on screen.
  const city = cityFromSearch(search);
  const replayPanelOpen = useUiStore((s) => s.replayPanelOpen);
  const [skyPanelOpen, setSkyPanelOpen] = useState(false);
  const [run, setRun] = useState<RunDepth | null>(null);
  // Stable identity: `FloodMap` keys its load effect on this, so an inline arrow here re-ran
  // the whole run + city-layer fetch on every render of the console.
  const setReplayPanelOpen = useUiStore((s) => s.setReplayPanelOpen);
  const setStoreRun = useRunStore((s) => s.setRun);
  const handleLoaded = useCallback(
    (loaded: RunDepth) => {
      setRun(loaded);
      // The chrome - mode banner, run stamp, verification chip - reads the run store, so a run
      // the map has loaded has to land there too or the top bar goes on saying "No runs yet"
      // over a console that is plainly showing one.
      const p = loaded.provenance;
      setStoreRun({
        run_id: p.runId,
        // The city the console is of, not a literal: a Chennai run in the store as "mumbai" is
        // how the top bar ends up naming the wrong city over the right water.
        city,
        cycle_ts: p.cycleTs ?? "",
        mode: p.mode === "live" ? "live" : "replay",
        replay_mode: p.mode === "live" ? "live" : "baked",
        ensemble_n: p.ensembleN,
        mass_balance_err: p.massBalanceErr,
        // Section 7.2's stamp reads "... · baked · 3.9 s". Without the timings the stamp had no
        // time to print, and its total rule (top-level stages only, ADR-0046) never ran.
        stage_ms: p.stageMs,
        bundle: p.bundle,
        step_min: p.stepMin,
        aoi_depth_band: p.aoiDepthBand,
      });
      // The replay panel is open on an empty console because it holds the command that fixes
      // that (P0.12). Once a run has landed the map is the screen, so the panel gets out of its
      // way; the icon rail brings it back.
      setReplayPanelOpen(false);
    },
    [city, setReplayPanelOpen, setStoreRun],
  );
  // With no `?run=`, open on the cycle the demo script starts from (SPEC.md 15, 06:40) rather
  // than the newest run the API would pick, which on the replay is the calm one after the storm.
  // The map holds its load for the moment it takes to read the registry, so nobody watches 09:10
  // load and then swap. If the registry is slow or has no run at the opening, the API's default
  // stands - and that default is now per city too, so a city with nothing baked gets its own
  // empty state rather than another city's water.
  //
  // The lookup is stamped with the city it asked about, which is what lets "still looking" be
  // derived rather than tracked: a result for a different city is, by definition, stale.
  const [opening, setOpening] = useState<{ city: string; runId?: string } | null>(null);
  useEffect(() => {
    if (pinnedRun !== undefined || opening?.city === city) return;
    let cancelled = false;
    const controller = new AbortController();
    fetchOpeningRunId(city, DEFAULT_SIM_TIME, apiUrl, controller.signal).then((runId) => {
      if (!cancelled) setOpening({ city, runId });
    });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [city, pinnedRun, opening?.city]);

  // The URL wins over the lookup: `?run=` is the operator saying which cycle, and a stale answer
  // from a lookup that ran before they said it must not outrank them.
  const runParam = pinnedRun ?? (opening?.city === city ? opening.runId : undefined);
  const mapReady = pinnedRun !== undefined || opening?.city === city;
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  // The loaded set is stamped with the run it belongs to, which is what lets "loading" be
  // derived rather than tracked: a rail whose stamp does not match the map's run is, by
  // definition, still catching up. One state, no flag to fall out of step with it.
  const [loadedHotspots, setLoadedHotspots] = useState<{
    runId: string;
    set: HotspotSet | null;
  } | null>(null);
  const [surcharge, setSurcharge] = useState<{ runId: string; set: SurchargeSet | null } | null>(
    null,
  );
  const [selectedHotspotId, setSelectedHotspotId] = useState<string | null>(null);
  // `?focus=<lon>,<lat>` flies the map to a point (M10), which is how an alert's "Show on the
  // map" lands on its street. Seeded from the address at mount and adjusted during render when
  // the parameter changes (React's pattern for state derived from a prop); a malformed pair is
  // ignored rather than flying to (0, 0).
  const focusParam = searchParams.get("focus");
  const [focus, setFocus] = useState<MapFocus | null>(() => focusFromParam(focusParam));
  const [seenFocusParam, setSeenFocusParam] = useState(focusParam);
  if (focusParam !== seenFocusParam) {
    setSeenFocusParam(focusParam);
    const next = focusFromParam(focusParam);
    if (next) setFocus(next);
  }
  // Reachability bands live here rather than in the rail, because two things need them: the rail
  // draws the clocks and the map draws the polygons, and the rail is unmounted whenever the
  // hotspot drawer is open.
  const [isochrones, setIsochrones] = useState<Isochrone[]>([]);
  const [layers, setLayers] = useState<LayerToggles>({
    satellite: true,
    // Off by default: the depth ramp is what an operator reads first, and probability is the
    // question they ask second (SPEC.md 7.2 puts it behind a toggle, not in front of one).
    probability: false,
    raster: true,
    segments: true,
    surcharge: true,
    // Off by default (SPEC.md 6.7); it is also the largest layer VARUNA serves.
    drains: false,
    // Off when the imagery is on: the footprints are the *same buildings* the photograph already
    // shows, so drawing both puts a grey polygon over every roof and loses the texture that makes
    // the basemap worth having. The toggle brings them back for anyone who wants the derived GIS.
    buildings: false,
    hotspots: true,
    // On: the bands appear only once a facility is picked under Reachability, which is itself
    // the operator asking for them. I hides them without losing the pick.
    isochrones: true,
    // Off: a route is a question about one trip, and it costs a request the scrub would not.
    routes: false,
    // Off: 3D is the P1 view (SPEC.md 3.2); the flat map is the one the demo reads from, and
    // Google's photorealistic ground is the one layer on this screen that needs a network.
    threeD: false,
    // Off: the X-ray is a second 18 MB network plus 9 MB of nodes, asked for rather than assumed.
    xray: false,
  });
  /**
   * How far the X-ray stretches each pipe's cover, so a 1.5 m sewer under a photographed street
   * is visible at all from a 55 degree camera. 1 is the truth and the default; the control says
   * plainly what any other value is doing (`exaggerationLabel`), because a stretched depth that
   * is not labelled is a fake number on screen (SPEC.md rule 6).
   */
  const [xrayExaggeration, setXrayExaggeration] = useState(1);
  /** What the map's X-ray actually drew, reported back through the overlay context. */
  const [xrayState, setXrayState] = useState<Drains3dResult>({ kind: "off" });
  const onXray = useCallback((result: Drains3dResult) => setXrayState(result), []);
  // The exceedance the probability layer asks about. SPEC.md 7.2's four: the depth at which
  // each class of vehicle stops, so the question is always "who is stopped here?".
  const [probabilityThresholdCm, setProbabilityThresholdCm] = useState(30);

  // The event's sourced pins, dropping as the clock reaches each one (task P6.12, motion M18).
  // The only observations on this screen that VARUNA did not compute.
  // Keyed on the run's own bundle and its current step, so the ticker follows the scrub the
  // map is showing rather than a clock somewhere else on the page.
  // The street the operator last clicked (task P6.9). Cleared by clicking empty map, by Escape,
  // and by a new run - a popover about a segment of a run that is no longer on screen is a lie.
  const [pick, setPick] = useState<SegmentPick | null>(null);
  // Full view (F, or the control at the map's top-right): the map region - the map, its scrub and
  // its legend - takes the whole screen through the Fullscreen API, or the whole viewport when the
  // browser refuses. Entering frames every street under water at the step on screen (`frameWhole`,
  // `frameStep`), or at the run's peak when that step is still dry. The step is read once, on
  // entering, so scrubbing inside full view never moves the camera; F twice re-frames at the new
  // step. Leaving hands back the camera the operator had (`keepViewOf`), panned or not. Every
  // change is a cut: section 8 has no row for it (M23).
  const regionRef = useRef<HTMLDivElement>(null);
  const fullViewControlRef = useRef<HTMLButtonElement>(null);
  const [fullView, setFullViewState] = useState<FullViewMode>("off");
  const inFullView = fullView !== "off";
  // Read by the callbacks below, which the key registry holds on to: a ref, so the Fullscreen
  // API's late answers see the current mode.
  const fullViewRef = useRef<FullViewMode>("off");
  // The step on screen, for `enterFullView`, which the key registry holds on to between renders.
  const stepRef = useRef(step);
  useEffect(() => {
    stepRef.current = step;
  }, [step]);
  const [fullViewStep, setFullViewStep] = useState<number | null>(null);
  // Set when the console leaves the browser's full screen itself, to show an overlay the full
  // screen would hide; the exit that follows then drops to the layout rather than closing.
  const demotingRef = useRef(false);
  const setFullView = useCallback((next: FullViewMode) => {
    fullViewRef.current = next;
    setFullViewState(next);
  }, []);
  const enterFullView = useCallback(() => {
    setFullViewStep(stepRef.current);
    const region = regionRef.current;
    if (!region || !document.fullscreenEnabled || typeof region.requestFullscreen !== "function") {
      setFullView("layout");
      return;
    }
    setFullView("screen");
    region.requestFullscreen({ navigationUI: "hide" }).catch(() => {
      // Refused - an iframe without `allow="fullscreen"`, a browser setting, a key press the
      // browser did not count as a gesture. The layout shows the same map instead.
      if (fullViewRef.current === "screen") setFullView("layout");
    });
  }, [setFullView]);
  const exitFullView = useCallback(() => {
    const wasScreen = fullViewRef.current === "screen";
    setFullView("off");
    if (wasScreen && document.fullscreenElement) {
      document.exitFullscreen().catch(() => undefined);
    }
  }, [setFullView]);
  const toggleFullView = useCallback(() => {
    if (fullViewRef.current === "off") enterFullView();
    else exitFullView();
  }, [enterFullView, exitFullView]);
  const [frame, setFrame] = useState<AffectedFrame | null>(null);
  // The what-if drawer (W) and the answer it has drawn on the map, if any.
  const [whatIfOpen, setWhatIfOpen] = useState(false);
  const [whatIfDiff, setWhatIfDiff] = useState<WhatIfDiff | null>(null);
  // Bumped by the popover's "why" link so the drawer opens at its attribution section.
  const [whyFocus, setWhyFocus] = useState<number | null>(null);

  const truth = useTruthPins(run?.provenance.bundle ?? undefined, run?.validTs[step] ?? null);

  // The layer column scrolls, and nothing said so: over the aerial basemap the thin `--line`
  // scrollbar thumb is invisible, so at 1366 x 768 with probability and drains on the column read
  // as though it ended at the cut. Two affordances, neither of them motion: a scrollbar in
  // `--line-strong` on a `--well` track with its gutter reserved, and a 20 px fade applied as a
  // mask on whichever edge has something hidden - a mask makes the clipped row translucent rather
  // than laying anything over it, so no row is covered and no click is intercepted.
  // Destructured rather than kept as one object: the React compiler's lint infers that whatever
  // reaches a `ref` prop is a ref, and then reads of its siblings during render are ref reads.
  const { attach: attachColumn, above: columnAbove, below: columnBelow } = useScrollEdges();

  const toggleLayer = useCallback(
    (key: LayerKey, next: boolean) => setLayers((current) => ({ ...current, [key]: next })),
    [],
  );

  // The rail loads once the map has told us which run it settled on, so the two can never be
  // describing different cycles. `?run=` may be absent, in which case the API picks the newest
  // run and the map reports back which one that was.
  const loadedRunId = run?.provenance.runId;
  useEffect(() => {
    if (!loadedRunId) return;
    const controller = new AbortController();
    loadHotspots(loadedRunId, controller.signal)
      .then((set) => setLoadedHotspots({ runId: loadedRunId, set }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        // A rail that cannot load is an empty rail, never a broken console: the map, the scrub
        // and the run stamp are all still telling the truth about this run.
        console.error("Hotspots failed to load", error);
        setLoadedHotspots({ runId: loadedRunId, set: null });
      });
    return () => controller.abort();
  }, [loadedRunId]);

  // Same shape for the surcharge product: stamped with its run, loaded once, scrubbed for free.
  useEffect(() => {
    if (!loadedRunId) return;
    const controller = new AbortController();
    loadSurcharge(loadedRunId, controller.signal)
      .then((set) => setSurcharge({ runId: loadedRunId, set }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        console.error("Surcharge failed to load", error);
        setSurcharge({ runId: loadedRunId, set: null });
      });
    return () => controller.abort();
  }, [loadedRunId]);

  // The drain map Pulse learned, fetched the first time the operator asks for the layer (task
  // P7.9). Never on mount: `drain_health.geojson` is 2.3 MB a run and section 6.7 has the layer
  // off by default. Stamped with its run, like the rail and the surcharge set, so switching cycle
  // re-asks rather than colouring the new run's pipes with the old run's posterior.
  //
  // Without this the layer drew the whole inferred network at the *prior* the city pipeline gave
  // it - pipes, but not the learning. The posterior is what "Drains (health)" means.
  const [drainHealth, setDrainHealth] = useState<{ runId: string; set: DrainHealth | null } | null>(
    null,
  );
  useEffect(() => {
    if (!layers.drains || !loadedRunId || drainHealth?.runId === loadedRunId) return;
    const controller = new AbortController();
    loadDrainHealth(loadedRunId, controller.signal, LEARNED_EDGE_LIMIT)
      .then((set) => setDrainHealth({ runId: loadedRunId, set }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        // A run without a learned map still has pipes to draw, at their prior; the panel says so.
        console.error("Drain health failed to load", error);
        setDrainHealth({ runId: loadedRunId, set: null });
      });
    return () => controller.abort();
  }, [layers.drains, loadedRunId, drainHealth?.runId]);

  // Derived, not tracked: a posterior stamped with a different run than the map's is, by
  // definition, still in flight (the same rule the hotspot rail uses).
  const learned = drainHealth && drainHealth.runId === loadedRunId ? drainHealth.set : null;
  const learnedDrains = useMemo(
    () =>
      (learned?.edges ?? []).map((edge) => ({
        id: edge.id,
        path: edge.path,
        beta: edge.betaMean,
        diameter: edge.diameterM,
      })),
    [learned],
  );
  const drainsPending = layers.drains && Boolean(loadedRunId) && drainHealth?.runId !== loadedRunId;

  const current = loadedHotspots?.runId === loadedRunId ? loadedHotspots : null;
  const hotspots = current?.set ?? null;
  const hotspotsLoading = Boolean(loadedRunId) && current === null;
  const selected = hotspots?.hotspots.find((h) => h.id === selectedHotspotId) ?? null;

  // Selecting a hotspot flies the map to it and rings it (motion M10). The key carries the click
  // count so choosing the same row after panning away flies back rather than doing nothing.
  // Switching cycle resets the scrub: step 12 of the 06:40 forecast is not step 12 of the 08:40
  // one, and carrying the index across would silently change what the readout means.
  const pickCycle = useCallback(
    (runId: string) => {
      setSelectedHotspotId(null);
      setStep(0);
      setPlaying(false);
      // Through the router, not `window.history`: the run the map loads is now derived from
      // `useSearchParams`, so the address bar is the single place a cycle is chosen and the back
      // button means what it says.
      const next = new URLSearchParams(search);
      next.set("run", runId);
      router.replace(`/console?${next.toString()}`, { scroll: false });
    },
    [router, search],
  );

  // A live cycle the time bar started has published (task P6.11): the console moves to it. The
  // map keeps drawing the last run until this event, so nothing half-computed is ever shown.
  const onLiveEvent = useCallback(
    (event: LiveEvent) => {
      const payload = (event.payload ?? {}) as { run_id?: unknown; mode?: unknown };
      if (payload.mode === "live" && typeof payload.run_id === "string") pickCycle(payload.run_id);
    },
    [pickCycle],
  );
  useLive({ topics: LIVE_RUN_TOPICS, onEvent: onLiveEvent });

  const selectHotspot = useCallback((hotspot: Hotspot) => {
    setSelectedHotspotId(hotspot.id);
    setFocus({ lon: hotspot.lon, lat: hotspot.lat, key: `${hotspot.id}-${Date.now()}`, zoom: 14 });
  }, []);

  // Play advances the same `step` the slider and the keyboard write, so there is one clock and
  // no way for the readout to disagree with the map.
  useEffect(() => {
    if (!playing || !run) return;
    const id = window.setInterval(() => {
      setStep((current) => {
        if (current >= run.provenance.nSteps - 1) {
          setPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, PLAY_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [playing, run]);

  // Space plays, the arrows scrub (SPEC.md 6.10). Ignored while the operator is typing.
  useEffect(() => {
    if (!run) return;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (event.key === " ") {
        event.preventDefault();
        setPlaying((p) => !p);
      } else if (event.key === "ArrowLeft") {
        setPlaying(false);
        setStep((s) => Math.max(0, s - 1));
      } else if (event.key === "ArrowRight") {
        setPlaying(false);
        setStep((s) => Math.min(run.provenance.nSteps - 1, s + 1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [run]);

  // SPEC.md 7.2's layer shortcuts, registered rather than handled locally. The registry in
  // lib/shortcuts.ts exists so the `?` overlay can ask which keys actually do something: it
  // was built and never used, so the overlay listed ten layer shortcuts while the console
  // handled six, and R, I, 3 and W were dead keys advertised as working. Registering here
  // makes the overlay's answer true by construction, and section 17's "never a dead control"
  // applies to a key the same way it applies to a button.
  useEffect(() => {
    if (!run) return;
    const toggles: Partial<Record<ShortcutLayerKey, LayerKey>> = {
      p: "probability",
      d: "drains",
      s: "surcharge",
      g: "hotspots",
      r: "routes",
      i: "isochrones",
      "3": "threeD",
      x: "xray",
    };
    const unsubscribes = Object.entries(toggles).map(([key, layer]) =>
      registerLayerShortcut(key as ShortcutLayerKey, () =>
        setLayers((current) => ({ ...current, [layer]: !current[layer] })),
      ),
    );
    // W opens the what-if drawer over the rail, and closes it again (SPEC.md 7.2, 7.7).
    unsubscribes.push(registerLayerShortcut("w", () => setWhatIfOpen((open) => !open)));
    // F is the full view, and back.
    unsubscribes.push(registerLayerShortcut("f", toggleFullView));
    return () => unsubscribes.forEach((off) => off());
  }, [run, toggleFullView]);

  // Escape leaves full view. Only while it is on, so Escape keeps meaning "close the open panel"
  // everywhere else; a field or an open dialog keeps its own Escape. Listened for in the capture
  // phase, ahead of the global handler, which would otherwise close the `?` overlay first and make
  // one Escape close both. In the browser's full screen the browser takes Escape itself, and the
  // `fullscreenchange` below is what hears it.
  useEffect(() => {
    if (fullView === "off") return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      // A text field keeps its Escape. The scrub is a range input with none of its own, and it is
      // the control holding focus after a drag, so it must not swallow the way back.
      if (isTextEntry(event.target)) return;
      const ui = useUiStore.getState();
      if (ui.commandPaletteOpen || ui.shortcutsOpen || ui.settingsOpen) return;
      exitFullView();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [fullView, exitFullView]);

  // The browser left its full screen: Escape, F11, a tab switch on some platforms. The console
  // follows - out of full view, or down to the layout when it left on purpose to show an overlay.
  useEffect(() => {
    const onChange = () => {
      if (document.fullscreenElement || fullViewRef.current !== "screen") return;
      const demoted = demotingRef.current;
      demotingRef.current = false;
      if (demoted) setFullView("layout");
      else setFullView("off");
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, [setFullView]);

  // The palette, the `?` overlay and settings render outside the map region, where the browser's
  // full screen cannot show them. Opening one drops full view to the layout, which can.
  useEffect(() => {
    if (fullView !== "screen") return;
    return useUiStore.subscribe((ui) => {
      if (!(ui.commandPaletteOpen || ui.shortcutsOpen || ui.settingsOpen)) return;
      if (!document.fullscreenElement) return;
      demotingRef.current = true;
      document.exitFullscreen().catch(() => {
        demotingRef.current = false;
      });
    });
  }, [fullView]);

  // In full view the top bar, the rail and the time bar are covered but still in the page, so Tab
  // would walk into controls nobody can see. The shell makes them inert for as long as full view
  // lasts (`chromeInert` below), and focus goes to the full-view control, which is where it came
  // from on a click and is the one control that stays put on both sides of the toggle.
  const fullViewWasOn = useRef(false);
  useEffect(() => {
    // Only on a change between off and on; the drop from the screen to the layout keeps focus.
    const on = fullView !== "off";
    if (on === fullViewWasOn.current) return;
    fullViewWasOn.current = on;
    fullViewControlRef.current?.focus({ preventScroll: true });
  }, [fullView]);

  // 3D mode's ground (task P6.15). The map probes it too, and the verdict is cached per key for
  // the life of the tab, so this second call costs no second request; the console reads it only
  // to say in the layer panel what 3D is waiting on or which switch is off.
  const photoreal = usePhotorealTileset(layers.threeD);

  // The Routes layer: the demo ambulance trip at the scrub time, re-planned when the scrub rests.
  const routeState = useConsoleRoutes(
    layers.routes,
    city,
    loadedRunId,
    run?.validTs[step] ?? undefined,
  );

  // Which chronic spot a clicked street belongs to, for the popover's "why" (task P6.9).
  const hotspotBySegment = useMemo(() => {
    const index = new Map<string, Hotspot>();
    for (const hotspot of hotspots?.hotspots ?? []) {
      for (const id of hotspot.segmentIds) if (!index.has(id)) index.set(id, hotspot);
    }
    return index;
  }, [hotspots]);
  const pickedHotspot = pick ? (hotspotBySegment.get(pick.segment.id) ?? null) : null;
  const openWhy = useCallback((hotspotId: string) => {
    setWhatIfOpen(false);
    setSelectedHotspotId(hotspotId);
    setWhyFocus(Date.now());
  }, []);

  const routeLines = routeState.kind === "ready" ? routeState.lines : undefined;
  const overlay = useMemo<MapOverlay>(
    () => ({
      city,
      threeD: layers.threeD,
      xray: layers.xray,
      xrayExaggeration,
      onXray,
      routes: routeLines,
      diff: whatIfOpen ? whatIfDiff : null,
    }),
    [
      city,
      layers.threeD,
      layers.xray,
      xrayExaggeration,
      onXray,
      routeLines,
      whatIfOpen,
      whatIfDiff,
    ],
  );

  const layerDetails: Partial<Record<LayerKey, string>> = {
    surcharge: surcharge?.set ? reversedFlowSummary(surcharge.set) : undefined,
    isochrones:
      layers.isochrones && isochrones.length === 0
        ? "Pick a facility under Reachability to draw its 5, 10 and 15 minute reach."
        : undefined,
    routes: routeDetail(routeState),
    threeD: photorealDetail(photoreal),
    xray: xrayDetail(xrayState, photoreal.kind === "ready", xrayExaggeration),
  };
  const caption = run
    ? frameCaption(frame, run, inFullView, inFullView ? fullViewStep : null)
    : null;
  const fitPadding = useMemo(
    () => consoleFitPadding(inFullView, replayPanelOpen),
    [inFullView, replayPanelOpen],
  );
  // The street popover and the probability legend live in the layer column, which full view hides.
  // They are drawn in their own corner there instead: a click on a street has to answer, and a map
  // whose opacity means P(above threshold) has to say so (SPEC.md 7.2).
  const segmentPopover =
    pick && run ? (
      <SegmentPopover
        pick={pick}
        step={step}
        validTs={run.validTs}
        hotspot={pickedHotspot}
        onWhy={openWhy}
        onClose={() => setPick(null)}
      />
    ) : null;
  const probabilityLegend = layers.probability ? (
    <ProbabilityLegend
      thresholdCm={probabilityThresholdCm}
      onThresholdChange={setProbabilityThresholdCm}
      deterministic={(run?.provenance.ensembleN ?? 1) <= 1}
    />
  ) : null;

  return (
    <AppShell
      chromeInert={inFullView}
      rightRail={
        // The drawers slide in *over* the rail (SPEC.md 7.2), so they take the same slot. Full
        // view covers the rail rather than unmounting it, so it comes back exactly as it was.
        whatIfOpen ? (
          <WhatIfDrawer
            runId={loadedRunId ?? null}
            onDiff={setWhatIfDiff}
            onClose={() => setWhatIfOpen(false)}
          />
        ) : selected ? (
          <HotspotDrawer
            hotspot={selected}
            step={step}
            stepMin={run?.provenance.stepMin ?? 5}
            validTs={run?.validTs ?? []}
            onClose={() => setSelectedHotspotId(null)}
            focusWhyKey={whyFocus}
          />
        ) : (
          <RightRail
            hotspots={hotspots}
            step={step}
            selectedHotspotId={selectedHotspotId}
            onSelectHotspot={selectHotspot}
            hotspotsLoading={hotspotsLoading}
            simTime={run?.validTs[step] ?? null}
            onIsochrones={setIsochrones}
          />
        )
      }
      bottomBar={<TimeBar />}
    >
      {/* The map region, which full view gives the whole screen: through the Fullscreen API, or
          pinned over the viewport when the browser refuses (`layout`). The scrub and the legend
          live inside it, so both still work in full view. */}
      <div
        ref={regionRef}
        data-testid="console-map-region"
        data-full-view={fullView}
        className={
          fullView === "layout"
            ? "fixed inset-0 z-50 min-h-0 bg-[var(--ink)]"
            : "relative h-full min-h-0 w-full bg-[var(--ink)]"
        }
      >
        {/* The map is the one memorable element on this screen (SPEC.md 6.1); everything else
            floats over it. `MapSlot` stays behind it as the legend and attribution host. */}
        <MapSlot legendClearsRightPanel={replayPanelOpen && !inFullView} />
        {/* 3D, the routes layer and the what-if difference reach the map through context: they
            are console-only asks, and `FloodMap` is shared by every screen with a map. */}
        <MapOverlayContext.Provider value={overlay}>
          <FloodMap
            // Passed rather than left to the map's own `currentCity()`: that reads `window` during
            // render, which is the same staleness the run parameter had.
            city={city}
            runId={runParam}
            deferLoad={!mapReady}
            step={step}
            onLoaded={handleLoaded}
            // Opens on the run's main affected area at its peak step, not the whole 9.5 x 15.5 km
            // AOI: the densest 7 km of water, grown to hold the rail's top five chronic spots, in
            // the part of the map the layer column and the scrub card leave clear. Full view
            // frames every street under water at the step it opened on. The key re-arms the fit on
            // entering, and the camera kept for "affected" comes back on exit.
            frameOn="affected"
            frameWhole={inFullView}
            frameStep={inFullView ? fullViewStep : null}
            frameHolds={RAIL_TOP_SPOTS}
            fitPadding={fitPadding}
            fitKey={inFullView ? "full-view" : "affected"}
            keepViewOf="affected"
            onFrame={setFrame}
            hotspots={hotspots?.hotspots ?? []}
            selectedHotspotId={selectedHotspotId}
            surcharge={surcharge?.runId === loadedRunId ? surcharge?.set : null}
            focus={focus}
            isochrones={layers.isochrones ? isochrones : NO_ISOCHRONES}
            showRaster={layers.raster}
            showSegments={layers.segments}
            showSurcharge={layers.surcharge}
            showDrains={layers.drains}
            drains={learnedDrains}
            showBuildings={layers.buildings}
            showHotspots={layers.hotspots}
            showSatellite={layers.satellite}
            probabilityThresholdCm={layers.probability ? probabilityThresholdCm : undefined}
            truthPins={layers.hotspots ? truth.dropping : undefined}
            onSegmentPick={setPick}
            attribution={false}
          />
        </MapOverlayContext.Provider>

        {/* Full view's control, at the map's top-right: beside the replay panel when that is open,
            as the legend is, and in the corner otherwise. One name in both states with
            `aria-pressed` carrying on or off, so a screen reader hears one toggle, not two
            buttons; the icon and the fill say it to the eye. */}
        {run ? (
          <div
            className={`absolute top-4 z-30 ${replayPanelOpen && !inFullView ? "right-[24.5rem]" : "right-4"}`}
          >
            <Button
              ref={fullViewControlRef}
              size="sm"
              variant={inFullView ? "default" : "outline"}
              className={inFullView ? undefined : "bg-[var(--deep)] dark:bg-[var(--deep)]"}
              onClick={toggleFullView}
              aria-pressed={inFullView}
              aria-keyshortcuts="F"
              title={
                inFullView ? "Leave full view (F or Esc)" : "Give the map the whole screen (F)"
              }
              data-testid="console-full-view"
            >
              {inFullView ? (
                <Minimize2 aria-hidden="true" strokeWidth={1.75} />
              ) : (
                <Maximize2 aria-hidden="true" strokeWidth={1.75} />
              )}
              Full view
            </Button>
          </div>
        ) : null}

        {/* The scrub. Owned here so the map, the readout and the keyboard share one step.

            It sits between the "Reconstructed replay" chip (16 px in, 154 px wide) and the depth
            legend (16 px in, 288 px wide), both of which `MapSlot` draws at the map's foot. Centred
            on the map, as it was, it covered the legend's three deepest rows at 1440 x 900 - the
            thresholds a judge reads first - and the legend is always visible (SPEC.md 6.7). With
            the replay panel open the legend moves inboard past where the card could fit, so the
            card stays centred there, as before. */}
        {run ? (
          <div
            data-testid="console-scrub"
            className={`pointer-events-auto absolute bottom-4 z-30 rounded-xl border border-[var(--line)] bg-[var(--ink)]/80 p-3 backdrop-blur-[12px] ${
              replayPanelOpen && !inFullView
                ? "left-1/2 w-[min(680px,calc(100%-2rem))] -translate-x-1/2"
                : "right-[20rem] left-[12rem] mx-auto max-w-[680px]"
            }`}
          >
            <div className="flex items-center gap-3">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setPlaying((p) => !p)}
                aria-label={playing ? "Pause" : "Play"}
              >
                {playing ? "Pause" : "Play"}
              </Button>
              <input
                type="range"
                min={0}
                max={Math.max(run.provenance.nSteps - 1, 0)}
                value={step}
                onChange={(event) => {
                  setPlaying(false);
                  setStep(Number(event.target.value));
                }}
                className="h-1 flex-1 cursor-pointer accent-[var(--tide)]"
                aria-label="Scrub the forecast"
              />
              <span className="num min-w-[132px] text-right text-[13px] text-[var(--text)]">
                {formatStep(run.validTs[step])} · +{leadMin(run, step)} min
              </span>
            </div>
            {caption ? (
              <p className="num mt-2 text-[12px] text-[var(--text-2)]" data-testid="console-frame">
                {caption}
              </p>
            ) : null}
            <p className="num mt-2 text-[12px] text-[var(--text-3)]">
              run {run.provenance.runId} · {run.provenance.mode} ·{" "}
              {run.provenance.ensembleN === 1
                ? "1 member (deterministic)"
                : `${run.provenance.ensembleN} members`}
              {run.provenance.massBalanceErr != null
                ? ` · mass balance ${formatMassBalance(run.provenance.massBalanceErr)}`
                : ""}
            </p>
          </div>
        ) : null}

        {/* Capped well above the canvas floor: at 1366 x 768 the depth legend reaches inboard to
            clear the replay panel, and the legend is always visible (SPEC.md section 6.7), so the
            column stops short of it and scrolls instead.

            The column itself scrolls (UI_SPEC 8, task D-17). It used to be a plain flex column
            with a `max-h`, which at 1366 x 768 simply clipped: the panel needs about 324 px, the
            rows below the fold were unreachable, and turning the wheel over them did nothing
            because there was no scroll container to turn. `min-h-0` lets the flex column shrink
            to its cap and `overflow-y-auto` gives the wheel something to move; `overscroll-contain`
            stops the scroll chaining out of the column when it reaches the end, which is what
            would hand the gesture to the map behind it. */}
        <div
          data-testid="console-map-column"
          ref={attachColumn}
          data-scroll-above={columnAbove ? "yes" : "no"}
          data-scroll-below={columnBelow ? "yes" : "no"}
          style={edgeFadeStyle({ above: columnAbove, below: columnBelow })}
          // Hidden rather than unmounted in full view, so the outlook card and the popover keep
          // what they loaded and come back as they were.
          className={`absolute top-4 left-4 z-20 ${inFullView ? "hidden" : "flex"} max-h-[calc(100%-12rem)] min-h-0 w-[380px] max-w-[calc(100%-2rem)] [scrollbar-color:var(--line-strong)_var(--well)] [scrollbar-gutter:stable] flex-col items-start gap-2 overflow-x-hidden overflow-y-auto overscroll-contain`}
        >
          {/* The chips are 414 px of clock times in a 380 px column, so they wrap to a second row
              rather than spilling over the map (UI_SPEC 8). */}
          {/* Only on the default city. `CyclePicker` reads `/v1/runs` with no city, which answers
              with Mumbai's cycles whoever asks - so on `?city=chennai` every chip was a Mumbai run
              waiting to be pinned to a Chennai map. Better no picker than a wrong one
              (SPEC.md 17); the chips come back for every city once the component takes one. */}
          {city === DEFAULT_CITY ? (
            <CyclePicker
              currentRunId={run?.provenance.runId}
              onPick={pickCycle}
              className="w-full flex-wrap"
            />
          ) : null}
          {inFullView ? null : segmentPopover}
          <LayerPanel
            value={layers}
            onChange={toggleLayer}
            counts={{
              surcharge: surcharge?.set?.nodes.length,
              hotspots: hotspots?.hotspots.length,
            }}
            details={layerDetails}
          />
          {/* Below the panel, never over it (UI_SPEC 8): the legend used to be positioned
              absolutely at a fixed offset from the map's top-left, which put it on top of the
              layer rows as soon as probability mode was on. */}
          {inFullView ? null : probabilityLegend}
          {/* The X-ray's one control. A 1.5 m cover under a photographed street is about four
              pixels at the zoom this view is read at, so stretching it is what makes the network
              legible - and the label says what the stretch is doing, every time it is not 1
              (SPEC.md rule 6: a number on screen that is not the measurement has to say so). */}
          {layers.xray ? (
            <div className="rounded-panel border-line w-[248px] border bg-[var(--ink)]/85 p-3 backdrop-blur-[12px]">
              <p className="type-small text-text-2">{exaggerationLabel(xrayExaggeration)}</p>
              <div role="group" aria-label="Drain depth exaggeration" className="mt-2 flex gap-1">
                {XRAY_EXAGGERATIONS.map((factor) => (
                  <Button
                    key={factor}
                    size="sm"
                    variant={factor === xrayExaggeration ? "default" : "outline"}
                    aria-pressed={factor === xrayExaggeration}
                    onClick={() => setXrayExaggeration(factor)}
                  >
                    {factor === 1 ? "Real" : `${factor}x`}
                  </Button>
                ))}
              </div>
            </div>
          ) : null}
          {/* What the Drains layer is actually showing. An honesty label, not fine print
              (SPEC.md 6.8): most of this graph has never been observed, and the operator has to
              be able to tell the pipes the filter moved from the pipes it never saw. */}
          {layers.drains ? (
            <div className="rounded-panel border-line w-[248px] border bg-[var(--ink)]/85 p-3 backdrop-blur-[12px]">
              {drainsPending ? (
                <>
                  <p className="type-small text-text-2">Loading the drain map Pulse learned.</p>
                  <Skeleton className="mt-2" lines={2} />
                </>
              ) : learned ? (
                // Three numbers, all from the run's own product, and the last clause is load
                // bearing: the cycle writes the worst 6,000 pipes, so a pipe the filter moved
                // that ranks below them is on the map at its prior. Saying "275 pipes moved" and
                // stopping there would claim a re-colouring the map does not draw.
                <p className="type-small text-text-2">
                  Inferred graph. Pulse moved{" "}
                  <span className="num">{learned.nUpdated.toLocaleString("en-IN")}</span> of{" "}
                  <span className="num">{learned.nEdges.toLocaleString("en-IN")}</span> pipes; the{" "}
                  <span className="num">{learned.edges.length.toLocaleString("en-IN")}</span> worst
                  are drawn at their posterior and the rest at the pipeline&apos;s prior.
                </p>
              ) : (
                <p className="type-small text-text-2">
                  This run has no learned drain map, so every pipe is drawn at the pipeline&apos;s
                  prior. Bake a cycle with Pulse to give them a posterior.
                </p>
              )}
            </div>
          ) : null}
          {/* Today's outlook, on today's clock: the one forecast here that is not the replay. It
              loads when opened, reads nothing from the replay and writes nothing to it, so the
              scrub, the map and the run above never move because of it. */}
          {/* 248 px like the layer panel above it: at 1366 x 768 with the replay panel open the
              depth legend reaches to x = 327 over the column's lower edge, and a full-width card
              ran under it. */}
          <PanelErrorBoundary title="Today, next 3 h">
            <LiveOutlookCard city={city} className="w-[248px]" />
          </PanelErrorBoundary>
          <Button size="sm" variant="outline" onClick={() => setSkyPanelOpen((open) => !open)}>
            {skyPanelOpen ? "Hide the rain nowcast" : "Show the rain nowcast"}
          </Button>
          {skyPanelOpen ? (
            <div className="min-h-0 w-full overflow-y-auto">
              <PanelErrorBoundary title="Rain nowcast">
                <SkyPanel />
              </PanelErrorBoundary>
            </div>
          ) : null}
        </div>

        {inFullView && (segmentPopover || probabilityLegend) ? (
          <div
            data-testid="console-full-view-overlays"
            className="absolute top-4 left-4 z-20 flex max-h-[calc(100%-12rem)] w-[380px] max-w-[calc(100%-2rem)] flex-col items-start gap-2 overflow-y-auto overscroll-contain"
          >
            {segmentPopover}
            {probabilityLegend}
          </div>
        ) : null}

        {replayPanelOpen && !inFullView ? (
          <div className="absolute top-4 right-4 z-20 max-h-[calc(100%-2rem)] w-[360px] max-w-[calc(100%-2rem)] overflow-y-auto">
            <ReplayPanel />
          </div>
        ) : null}
      </div>
    </AppShell>
  );
}

/** A `?focus=<lon>,<lat>` value as a map focus, or null when it is absent or not a coordinate. */
function focusFromParam(value: string | null): MapFocus | null {
  if (!value) return null;
  const [lon, lat] = value.split(",").map(Number);
  if (lon === undefined || lat === undefined) return null;
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return null;
  if (Math.abs(lon) > 180 || Math.abs(lat) > 90) return null;
  return { lon, lat, key: `focus-${value}`, zoom: 15 };
}

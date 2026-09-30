"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Maximize2, Minimize2 } from "lucide-react";
import { toast } from "sonner";

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
import { MapOverlayContext, type MapOverlay } from "@/components/map/layers/overlay-context";
import {
  drainXraySummary,
  exaggerationLabel,
  type Drains3dResult,
} from "@/components/map/layers/drains-3d";
import { usePhotorealTileset, type PhotorealState } from "@/lib/maps/photoreal";
import { AppShell } from "@/components/varuna/app-shell";
import { MapSlot } from "@/components/varuna/map-slot";
import { ReplayPanel } from "@/components/varuna/replay-panel";
import { CitizenReportsCard } from "@/components/varuna/citizen-reports-card";
import { LiveNowCard } from "@/components/varuna/live-now-card";
import { usePublicReports } from "@/components/citizen/use-report-feeds";
import { reportToPin, type ReportPin } from "@/lib/api/reports";
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
import { TimeBar } from "@/components/varuna/time-bar";
import { edgeFadeStyle, useScrollEdges } from "./use-scroll-edges";
import { useConsoleRoutes } from "./use-console-routes";
import { WhatIfDrawer, type WhatIfDiff } from "./whatif-drawer";
import {
  RAINVIEWER_CREDIT,
  fetchOpeningRunId,
  isLiveRun,
  usesLiveRadar,
} from "@/lib/opening-run";
import { minutesBetween } from "@/lib/format";
import { DEFAULT_CITY, cityFromSearch } from "@/lib/city";
import { MIN_AREA_M, type AffectedFrame } from "@/lib/map/affected-bounds";
import { DEFAULT_SIM_TIME, REPLAY_PEAK_SIM_TIME, useReplayStore } from "@/lib/stores/replay";
import { useRunStore } from "@/lib/stores/run";
import { nearestStep, playIntervalMs, useScrubStore } from "@/lib/stores/scrub";
import { useUiStore } from "@/lib/stores/ui";

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

/**
 * The step at which the most streets stand above 15 cm, or null when none ever do.
 *
 * Where the replay opens "at its peak": the 08:40 cycle floods most at +180 min (1,263 streets
 * above 15 cm on the 2026-09-28 bake) and hardly at all at +5 min, so opening on its first step
 * showed a dry city under a storm that was about to arrive.
 */
export function mostFloodedStep(run: Pick<RunDepth, "depthCm" | "validTs">): number | null {
  const counts = new Array<number>(run.validTs.length).fill(0);
  for (const series of run.depthCm.values()) {
    for (let i = 0; i < counts.length; i += 1) if ((series[i] ?? 0) >= 15) counts[i] += 1;
  }
  let best = -1;
  let bestCount = 0;
  counts.forEach((count, i) => {
    if (count > bestCount) {
      best = i;
      bestCount = count;
    }
  });
  return best >= 0 ? best : null;
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
      ? `Full view: nothing is wet, so the whole city.${back}`
      : "Nothing in this run is wet, so the map shows the whole city.";
  }
  if (frame.basis === "hotspots") {
    return fullView
      ? `Full view: no street reaches 5 cm, so the chronic spots.${back}`
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
      return `Full view: under ${MIN_AREA_M} m wet at ${asked}, so the peak: ${held}.${back}`;
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
  if (state.kind === "loading") return "Asking Google for the 3D city.";
  if (state.kind === "unavailable") return state.message;
  return "Google's photorealistic Mumbai, with the water draped on it.";
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
  if (result.kind !== "ready") return drainXraySummary(result);
  const n = result.pipesDrawn.toLocaleString("en-IN");
  const parts = [`${n} inferred ${result.pipesDrawn === 1 ? "pipe" : "pipes"} at invert depth.`];
  if (exaggeration !== 1) parts.push(`${exaggerationLabel(exaggeration)}.`);
  parts.push(
    threeD
      ? "Offset from Google's ground has not been measured."
      : "Switch the 3D city on to look under the street.",
  );
  return parts.join(" ");
}

/** The one socket topic the console itself listens to: a published live run. */
const LIVE_RUN_TOPICS = ["runs.published"] as const;

/** Stable empty bands, for when I has hidden the isochrones. */
const NO_ISOCHRONES: Isochrone[] = [];

/** Stable empty pins, for when the citizen-reports layer is off. */
const NO_REPORT_PINS: ReportPin[] = [];

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
  const [run, setRun] = useState<RunDepth | null>(null);
  // Stable identity: `FloodMap` keys its load effect on this, so an inline arrow here re-ran
  // the whole run + city-layer fetch on every render of the console.
  const setReplayPanelOpen = useUiStore((s) => s.setReplayPanelOpen);
  const setStoreRun = useRunStore((s) => s.setRun);
  const handleLoaded = useCallback(
    (loaded: RunDepth) => {
      setRun(loaded);
      if (scrubToPeakRef.current) {
        scrubToPeakRef.current = false;
        const peak = mostFloodedStep(loaded);
        if (peak !== null) useReplayStore.getState().setLeadMin(leadMin(loaded, peak));
      }
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
        step_leads: loaded.validTs.map((_, i) => leadMin(loaded, i)),
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
  const pickCycleRef = useRef<((runId: string) => void) | null>(null);
  // Set when the console is asked for the replay "at its peak": the next run that loads is
  // scrubbed to its most flooded step instead of +5 min, where the storm has not arrived yet.
  const scrubToPeakRef = useRef(false);
  useEffect(() => {
    if (pinnedRun !== undefined || opening?.city === city) return;
    let cancelled = false;
    const controller = new AbortController();
    // Today's live cycle when the live loop has one (varuna_cycle.live). The demo script's
    // `?bundle=` asks for the replay, which opens at 06:40 so Play is the first click; without
    // it and without a live run, the replay opens at 08:40, the storm near its peak, so the first
    // view shows the water rather than the city before it.
    const bundleAsked = searchParams.get("bundle") !== null;
    fetchOpeningRunId(
      city,
      bundleAsked ? DEFAULT_SIM_TIME : REPLAY_PEAK_SIM_TIME,
      apiUrl,
      controller.signal,
      { preferLive: !bundleAsked },
    ).then((runId) => {
      if (!bundleAsked && runId && !runId.endsWith("-live")) scrubToPeakRef.current = true;
      if (!cancelled) setOpening({ city, runId });
    });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [city, pinnedRun, opening?.city, searchParams]);

  // The URL wins over the lookup: `?run=` is the operator saying which cycle, and a stale answer
  // from a lookup that ran before they said it must not outrank them.
  const runParam = pinnedRun ?? (opening?.city === city ? opening.runId : undefined);
  const mapReady = pinnedRun !== undefined || opening?.city === city;
  // One clock for the map: the scrub is the replay store's `leadMin`, which the time bar's slider
  // and the arrow keys write, and the step is the run's step nearest to it. Play is
  // `useScrubStore`. There used to be two: this screen kept its own step and Play inside the map,
  // while the time bar under it drove the API's shared clock, so the two readouts disagreed and
  // the time bar's Play moved nothing on the map.
  const scrubLead = useReplayStore((s) => s.leadMin);
  const playing = useScrubStore((s) => s.playing);
  const playRate = useScrubStore((s) => s.rate);
  const stepLeads = useMemo(() => (run ? run.validTs.map((_, i) => leadMin(run, i)) : []), [run]);
  const step = nearestStep(stepLeads, scrubLead);
  const setStep = useCallback(
    (next: number) => {
      const lead = stepLeads[Math.max(0, Math.min(next, stepLeads.length - 1))];
      if (lead !== undefined) useReplayStore.getState().setLeadMin(lead);
    },
    [stepLeads],
  );
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
  // Section 7.2's layers and no others. Satellite and Buildings were removed from the panel: the
  // imagery is the basemap, always on, and the footprints are the same buildings the photograph
  // already shows, so drawing them only greyed every roof.
  const [layers, setLayers] = useState<LayerToggles>({
    // Off by default: the depth ramp is what an operator reads first, and probability is the
    // question they ask second (SPEC.md 7.2 puts it behind a toggle, not in front of one).
    probability: false,
    // Off, and no longer offered in the panel: the 30 m depth squares read as noise to an officer
    // next to the streets coloured by the same depth.
    raster: false,
    segments: true,
    surcharge: true,
    // Off by default (SPEC.md 6.7); it is also the largest layer VARUNA serves.
    drains: false,
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
    // On: a complaint raised at /report lands on this map within one poll (30 s).
    reports: true,
  });
  // Citizen reports, polled every 30 s: a complaint raised at /report is pinned here within one
  // poll, and a report that arrives while the console is open is announced once.
  const reportFeed = usePublicReports(city);
  const reportPins = useMemo(() => reportFeed.reports.map(reportToPin), [reportFeed.reports]);
  const [pickedReportId, setPickedReportId] = useState<string | null>(null);
  const seenReports = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (!reportFeed.loaded) return;
    const ids = new Set(reportPins.map((pin) => pin.id));
    if (seenReports.current === null) {
      seenReports.current = ids;
      return;
    }
    const fresh = reportPins.filter(
      (pin) => pin.origin === "citizen" && !seenReports.current?.has(pin.id),
    );
    seenReports.current = ids;
    for (const pin of fresh.slice(0, 3)) {
      toast(`New citizen report${pin.place ? ` at ${pin.place}` : ""}`, {
        description: pin.text ?? undefined,
      });
    }
  }, [reportFeed.loaded, reportPins]);
  const pickReport = useCallback((report: ReportPin) => {
    setPickedReportId(report.id);
    setFocus({
      lon: report.lon,
      lat: report.lat,
      key: `report-${report.id}-${Date.now()}`,
      zoom: 15,
    });
  }, []);
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

  // A live cycle is today's weather: the 2019 ground truth has nothing to say about it (and the
  // API has none for a live folder), and its chip and notice say which forecast this is.
  const liveRun = isLiveRun({ bundle: run?.provenance.bundle ?? null });
  const liveRadar = liveRun && usesLiveRadar(run?.provenance.notes);
  const truth = useTruthPins(
    liveRun ? undefined : (run?.provenance.bundle ?? undefined),
    run?.validTs[step] ?? null,
  );
  // The deepest street anywhere in the live forecast, so a dry day says so in words instead of
  // leaving an officer to wonder whether the map failed to draw.
  const livePeakCm = useMemo(() => {
    if (!run || !liveRun) return null;
    let peak = 0;
    for (const series of run.depthCm.values()) {
      for (const cm of series) if (cm > peak) peak = cm;
    }
    return peak;
  }, [run, liveRun]);
  const openReplayPeak = useCallback(() => {
    const controller = new AbortController();
    void fetchOpeningRunId(city, REPLAY_PEAK_SIM_TIME, apiUrl, controller.signal).then((runId) => {
      if (!runId) return;
      scrubToPeakRef.current = true;
      pickCycleRef.current?.(runId);
    });
  }, [city]);

  // The layer column scrolls, and nothing said so: over the aerial basemap the thin `--line`
  // scrollbar thumb is invisible, so at 1366 x 768 with probability and drains on the column read
  // as though it ended at the cut. Two affordances, neither of them motion: a scrollbar in
  // `--line-strong` on a `--well` track with its gutter reserved, and a 20 px fade applied as a
  // mask on whichever edge has something hidden - a mask makes the clipped row translucent rather
  // than laying anything over it, so no row is covered and no click is intercepted.
  // Destructured rather than kept as one object: the React compiler's lint infers that whatever
  // reaches a `ref` prop is a ref, and then reads of its siblings during render are ref reads.
  const { attach: attachColumn, above: columnAbove, below: columnBelow } = useScrollEdges();

  // The X-ray is read under Google's 3D ground and is offered under the 3D row, so leaving 3D
  // takes the X-ray with it rather than leaving it on behind a hidden switch.
  const toggleLayer = useCallback(
    (key: LayerKey, next: boolean) =>
      setLayers((current) => ({
        ...current,
        [key]: next,
        ...(key === "threeD" && !next ? { xray: false } : {}),
      })),
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
      // Back to the first step: the new run's leads are the same five minutes apart on every
      // cycle, and the scrub snaps onto them once it loads.
      useReplayStore.getState().setLeadMin(0);
      useScrubStore.getState().pause();
      // Through the router, not `window.history`: the run the map loads is now derived from
      // `useSearchParams`, so the address bar is the single place a cycle is chosen and the back
      // button means what it says.
      const next = new URLSearchParams(search);
      next.set("run", runId);
      router.replace(`/console?${next.toString()}`, { scroll: false });
    },
    [router, search],
  );

  useEffect(() => {
    pickCycleRef.current = pickCycle;
  }, [pickCycle]);

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

  // The scrub rests on one of the run's steps. The arrow keys move it 15 or 60 minutes from
  // wherever it is (SPEC.md 6.10), and the store opens at +0, which no run has a step at; both
  // land here and are put on the nearest step, so the readout always names what the map shows.
  useEffect(() => {
    if (stepLeads.length === 0) return;
    const on = stepLeads[nearestStep(stepLeads, scrubLead)];
    if (on !== undefined && on !== scrubLead) useReplayStore.getState().setLeadMin(on);
  }, [stepLeads, scrubLead]);

  // Play steps the map through the run (motion M7), from where the scrub is, and stops on the
  // last step. Pressed on the last step, it starts again from the first.
  useEffect(() => {
    if (!playing || stepLeads.length === 0) return;
    const replay = useReplayStore.getState();
    if (nearestStep(stepLeads, replay.leadMin) >= stepLeads.length - 1) {
      replay.setLeadMin(stepLeads[0]);
    }
    const id = window.setInterval(() => {
      const store = useReplayStore.getState();
      const at = nearestStep(stepLeads, store.leadMin);
      const next = stepLeads[at + 1];
      if (next === undefined) {
        useScrubStore.getState().pause();
        return;
      }
      store.setLeadMin(next);
    }, playIntervalMs(playRate));
    return () => window.clearInterval(id);
  }, [playing, playRate, stepLeads]);

  // Nothing plays on a screen that is not showing the map.
  useEffect(() => () => useScrubStore.getState().pause(), []);

  // `?autoplay=1` (the "watch the replay" links) presses Play once, when the first run's steps
  // are known, and never again: a pause after that is the reader's.
  const autoplay = searchParams.get("autoplay") === "1";
  const autoplayed = useRef(false);
  useEffect(() => {
    if (!autoplay || autoplayed.current || stepLeads.length === 0) return;
    autoplayed.current = true;
    useScrubStore.getState().play();
  }, [autoplay, stepLeads]);

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
    };
    const unsubscribes = Object.entries(toggles).map(([key, layer]) =>
      registerLayerShortcut(key as ShortcutLayerKey, () =>
        setLayers((current) => ({
          ...current,
          [layer]: !current[layer],
          ...(layer === "threeD" && current.threeD ? { xray: false } : {}),
        })),
      ),
    );
    // X is the X-ray, which is read under the 3D city: switching it on switches 3D on with it, so
    // the key always does something and its row (offered under 3D) is always there to report it.
    unsubscribes.push(
      registerLayerShortcut("x", () =>
        setLayers((current) =>
          current.xray ? { ...current, xray: false } : { ...current, xray: true, threeD: true },
        ),
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

  // Only a layer that cannot draw what was asked for says so under its switch.
  const layerDetails: Partial<Record<LayerKey, string>> = {
    threeD: photoreal.kind === "unavailable" ? photorealDetail(photoreal) : undefined,
    xray:
      xrayState.kind !== "off" && xrayState.kind !== "ready"
        ? xrayDetail(xrayState, photoreal.kind === "ready", xrayExaggeration)
        : undefined,
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
        <MapSlot
          legendClearsRightPanel={replayPanelOpen && !inFullView}
          chipLabel={
            liveRun
              ? liveRadar
                ? "Live forecast, radar nowcast"
                : "Live forecast, NWP rain, no radar"
              : undefined
          }
          chipLink={liveRadar ? RAINVIEWER_CREDIT : undefined}
        />
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
            showBuildings={false}
            showHotspots={layers.hotspots}
            showSatellite
            probabilityThresholdCm={layers.probability ? probabilityThresholdCm : undefined}
            truthPins={layers.hotspots && !liveRun ? truth.dropping : undefined}
            reports={layers.reports ? reportPins : NO_REPORT_PINS}
            selectedReportId={pickedReportId}
            onPickReport={setPickedReportId}
            onSegmentPick={setPick}
            attribution={false}
          />
        </MapOverlayContext.Provider>

        {liveRun && livePeakCm !== null && livePeakCm < 15 && !inFullView ? (
          <div className="rounded-panel border-line bg-deep absolute bottom-10 left-1/2 z-30 w-[min(24rem,calc(100%-2rem))] -translate-x-1/2 border p-3 text-center">
            <p className="type-small text-text font-medium">
              No street is forecast above 15 cm in the next 3 hours.
            </p>
            <p className="type-micro text-text-2 mt-0.5">
              Live forecast from {run ? formatStep(run.provenance.cycleTs ?? undefined) : ""} IST,
              from {liveRadar ? "live radar and " : ""}today&apos;s rain forecast.
            </p>
            <Button size="sm" variant="outline" className="mt-2" onClick={openReplayPeak}>
              See 2 July 2019 at its peak
            </Button>
          </div>
        ) : null}

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

        {/* The scrub, in full view only. Full view covers the time bar, so the map carries its own
            Play and scrub there; both write the same scrub as the time bar, so there is still one
            clock. Outside full view the time bar is the map's clock and nothing duplicates it. */}
        {run && inFullView ? (
          <div
            data-testid="console-scrub"
            className="pointer-events-auto absolute right-[20rem] bottom-4 left-[12rem] z-30 mx-auto max-w-[680px] rounded-xl border border-[var(--line)] bg-[var(--ink)]/80 p-3 backdrop-blur-[12px]"
          >
            <div className="flex items-center gap-3">
              <Button
                size="sm"
                variant="outline"
                onClick={() => useScrubStore.getState().toggle()}
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
                  useScrubStore.getState().pause();
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
          <LayerPanel value={layers} onChange={toggleLayer} details={layerDetails} />
          {/* Below the panel, never over it (UI_SPEC 8): the legend used to be positioned
              absolutely at a fixed offset from the map's top-left, which put it on top of the
              layer rows as soon as probability mode was on. */}
          {inFullView ? null : probabilityLegend}
          {inFullView ? null : (
            <LiveNowCard city={city} cityLabel={city.charAt(0).toUpperCase() + city.slice(1)} />
          )}
          {inFullView || !layers.reports ? null : (
            <CitizenReportsCard
              reports={reportPins}
              loaded={reportFeed.loaded}
              error={reportFeed.error}
              selectedId={pickedReportId}
              onPick={pickReport}
            />
          )}
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
              {xrayState.kind === "ready" ? (
                <details className="type-micro text-text-3 mt-2">
                  <summary className="text-text-2 cursor-pointer">Details</summary>
                  <p className="num mt-1">{drainXraySummary(xrayState)}</p>
                </details>
              ) : null}
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
                <>
                  <span className="rounded-chip border-line text-text-2 type-micro inline-flex h-5 items-center border px-2">
                    Inferred drain graph
                  </span>
                  <p className="type-small text-text-2 mt-2">
                    Pulse moved{" "}
                    <span className="num">{learned.nUpdated.toLocaleString("en-IN")}</span> of{" "}
                    <span className="num">{learned.nEdges.toLocaleString("en-IN")}</span> pipes.
                  </p>
                  {/* The cycle writes the worst 6,000 pipes by blockage, so a pipe the filter
                      moved that ranks below them is drawn at its prior: said, not implied. */}
                  <details className="type-micro text-text-3 mt-1">
                    <summary className="text-text-2 cursor-pointer">Details</summary>
                    <p className="num mt-1">
                      The {learned.edges.length.toLocaleString("en-IN")} worst are drawn at their
                      posterior; the rest at the pipeline&apos;s prior.
                    </p>
                  </details>
                </>
              ) : (
                <p className="type-small text-text-2">
                  Inferred drain graph, drawn at its prior: this run has no learned drain map.
                </p>
              )}
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

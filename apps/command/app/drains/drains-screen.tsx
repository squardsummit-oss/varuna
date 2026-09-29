"use client";

import { ChevronsLeftRight, Download, Info, Layers3, Scan } from "lucide-react";
import { animate } from "motion/react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Suspense,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";

import type { Viewport } from "@deck.gl/core";

import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { CityMap, type SegmentPath } from "@/components/map/city-map";
import { cityBounds } from "@/components/map/basemap";
import {
  SPLIT_ALL_AFTER,
  SPLIT_ALL_BEFORE,
  withLearned,
  type LearnedDrain,
} from "@/components/map/layers/drains";
import type { DrainPick, MapFocus } from "@/components/map/layers/types";
import { AppShell } from "@/components/varuna/app-shell";
import { CyclePicker } from "@/components/varuna/cycle-picker";
import { DrainHealthTable, type DrainHealthRow } from "@/components/varuna/drain-health-table";
import { DrainStatsStrip } from "@/components/varuna/drain-stats";
import { EmptyState } from "@/components/varuna/empty-state";
import { MapSlot } from "@/components/varuna/map-slot";
import { ObservationCard, type Observation } from "@/components/varuna/observation-card";
import { ObservationStrip } from "@/components/varuna/observation-strip";
import { PageHeader } from "@/components/varuna/page-header";
import { Panel } from "@/components/varuna/panel";
import { PanelErrorBoundary } from "@/components/varuna/panel-error-boundary";
import { PipeCard } from "@/components/varuna/pipe-card";
import { Skeleton } from "@/components/varuna/skeleton";
import { apiUrl } from "@/lib/api/client";
import { loadDrains, type DrainPath as NetworkDrain } from "@/lib/api/city-layers";
import {
  desiltingCsvUrl,
  loadDrainHealth,
  loadObservations,
  UNNAMED_PLACE,
  type AssimilatedObservation,
  type DrainHealth,
  type ObservationSet,
} from "@/lib/api/drains";
import { allSegments } from "@/lib/api/run-depth";
import { loadSurcharge, type SurchargeSet } from "@/lib/api/surcharge";
import { cityFromSearch, DEFAULT_CITY } from "@/lib/city";
import { formatBeta, formatBetaWithSd, formatIst } from "@/lib/format";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { DUR_MS, EASE_UI } from "@/lib/motion";
import { useSettledBounds } from "@/lib/map/affected-bounds";
import { navItem } from "@/lib/nav";
import { fetchOpeningRunId } from "@/lib/opening-run";
import { useRunStore, type RunMeta } from "@/lib/stores/run";
import { cn } from "@/lib/utils";

import {
  DRAIN_FIT_PADDING,
  DRAIN_PANE_FALLBACK,
  deriveDrainStats,
  drainFrame,
  drainStory,
  learnedDrains,
  midpoint,
  nearbyStreetFinder,
  pipeTitle,
  rankPipeCards,
  type DrainStats,
  type PipeCardData,
  type PlaceAt,
} from "./drain-model";

/**
 * The cycle `/drains` opens on when the URL names none: 08:40 IST on 2 July 2019, the cycle the
 * demo quotes for the drain map (201 pipes moved), and the one the ambulance trip and the what-if
 * are pinned to. The newest run is 09:10, the calm cycle after the storm.
 */
export const DRAINS_OPENING_TS = "2019-07-02T08:40:00+05:30";

/** Pipes fetched from the run: the bake writes every moved pipe inside its 6,000, and the request
 * asks for them first (`order=learned`), so one request carries the whole learned set and the
 * worst of the rest. */
const HEALTH_LIMIT = 6000;
/** Pipe cards named beside the map. */
const CARD_COUNT = 6;
/** The worst pipes by blockage added to the full table beside the moved ones. */
const TABLE_WORST = 25;

/** How long the map waits for the surcharging manholes, once the pipes are in, before it opens. */
const MANHOLE_WAIT_MS = 2_500;

/**
 * What each observation operator is, in one line. The filter's `H(theta)` is injectable
 * (`services/pulse/varuna_pulse/enkf.py`) and the prototype ships the reduced one, so every
 * blockage on this map came through a stand-in for SPEC.md 11.6's "run drain1d per member".
 */
const OPERATOR_NOTES: Record<string, string> = {
  capacity_deficit: "a volume balance over the pipe's catchment, not a drain1d run.",
};

type Load<T> =
  | { state: "loading" }
  | { state: "ready"; value: T }
  | { state: "missing" }
  | { state: "error"; message: string };

/** A tiny store for the hovered pipe, so the tooltip re-renders on every pointer move and the
 * screen - and the map inside it - does not. */
function createHoverStore() {
  let current: DrainPick | null = null;
  const listeners = new Set<() => void>();
  return {
    set(pick: DrainPick | null) {
      current = pick;
      for (const listener of listeners) listener();
    },
    get: () => current,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
type HoverStore = ReturnType<typeof createHoverStore>;

function isLearned(drain: unknown): drain is LearnedDrain {
  return Boolean(drain && typeof drain === "object" && "post" in drain && "prior" in drain);
}

/**
 * Section 7.3's pipe hover: name, blockage with its spread, the prior, capacity lost, updated. A
 * pipe no street names is titled by its id and the nearest street (`pipeTitle`), never "Unnamed".
 */
function DrainTooltip({ store, placeAt }: { store: HoverStore; placeAt: PlaceAt }) {
  const pick = useSyncExternalStore(store.subscribe, store.get, () => null);
  if (!pick || !isLearned(pick.drain)) return null;
  const d = pick.drain;
  const middle = d.name ? null : midpoint(d.path);
  const title = pipeTitle(d.id, d.name, middle ? placeAt(middle) : null);
  return (
    <div
      role="tooltip"
      className="rounded-control border-line bg-deep type-micro text-text-2 pointer-events-none absolute z-10 max-w-[280px] border px-2.5 py-2"
      // Kept inside the pane: a pipe near the right or bottom edge flips nothing off-screen.
      style={{
        left: `min(${pick.x + 14}px, calc(100% - 290px))`,
        top: `min(${pick.y + 14}px, calc(100% - 120px))`,
      }}
    >
      <p className="type-small text-text font-medium">{title}</p>
      {d.locality ? <p className="text-text-3">{d.locality}</p> : null}
      <p className="num mt-1">
        Blockage {formatBetaWithSd(d.post, d.sd)}, prior {formatBeta(d.prior)}
      </p>
      <p className="num">Capacity lost {d.capacityLostPct.toFixed(1)} %</p>
      {d.lastUpdate ? <p className="num">Updated {formatIst(d.lastUpdate)}</p> : null}
    </div>
  );
}

/**
 * What the three kinds of line on the map mean. The glow is the screen's one memorable element
 * (section 6.1), so it is named first: it means *learned this cycle*, never a high blockage.
 */
function MapLegend() {
  return (
    <ul
      aria-label="Map legend"
      className="type-micro text-text-2 flex flex-wrap items-center gap-x-4 gap-y-1"
    >
      <li className="inline-flex items-center gap-2">
        <span
          aria-hidden="true"
          className="bg-text/30 relative inline-block h-2.5 w-6 rounded-full"
        >
          <span className="bg-drain-1 absolute inset-x-1 top-1/2 h-0.5 -translate-y-1/2" />
        </span>
        Learned this cycle
      </li>
      <li className="inline-flex items-center gap-2">
        <span
          aria-hidden="true"
          className="border-naive inline-block w-6 border-t-2 border-dashed"
        />
        Cleared
      </li>
      <li className="inline-flex items-center gap-2">
        <span
          aria-hidden="true"
          className="border-drain-0 inline-block w-6 border-t border-dashed"
        />
        Rest of network
      </li>
    </ul>
  );
}

/** "About this map": the explanations that used to be four paragraphs above the pipes. */
function AboutMap({
  health,
  stats,
  surchargeNote,
}: {
  health: DrainHealth | null;
  stats: DrainStats | null;
  surchargeNote: string | null;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onDown = (e: PointerEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  return (
    <div ref={wrapRef} className="relative">
      <Button
        variant="outline"
        size="sm"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
      >
        <Info size={16} strokeWidth={1.75} aria-hidden="true" />
        About this map
      </Button>
      <div
        id={panelId}
        hidden={!open}
        className="rounded-panel border-line bg-deep type-micro text-text-2 absolute top-full right-0 z-20 mt-2 w-[min(360px,80vw)] space-y-2 border p-4"
      >
        <p>Every pipe is inferred from roads and terrain, so every pipe is dashed.</p>
        <p>Glow: moved this cycle. Grey dashed: blockage lowered. Faint: at its land-use prior.</p>
        {health ? (
          <p>
            Observation operator: {health.operator.replaceAll("_", " ")}
            {OPERATOR_NOTES[health.operator] ? ` - ${OPERATOR_NOTES[health.operator]}` : "."}
          </p>
        ) : null}
        {stats?.capacity?.learnedM3s != null ? (
          <p className="num">
            Learning moved full-flow capacity lost by {stats.capacity.learnedM3s >= 0 ? "+" : "-"}
            {Math.abs(stats.capacity.learnedM3s).toFixed(1)} m³/s.
          </p>
        ) : null}
        {stats ? (
          <p className="num">
            Observations: {stats.nSynthetic.toLocaleString("en-IN")} synthetic,{" "}
            {stats.nReal.toLocaleString("en-IN")} real.
          </p>
        ) : null}
        <p>Each cycle re-learns from the land-use prior; nothing carries over yet.</p>
        <p>Inlet clogging (κ) is not learned yet, and inlets are not drawn.</p>
        {surchargeNote ? <p>{surchargeNote}</p> : null}
      </div>
    </div>
  );
}

/**
 * The before/after split (motion M30). A DOM handle over the map, kept on the split's longitude
 * as the map pans by the viewport the drains layer reports; dragging it moves the clip, and the
 * Before and After buttons slide it to either edge in 300 ms.
 */
function useSplit(reducedMotion: boolean) {
  const [splitLon, setSplitLon] = useState(SPLIT_ALL_AFTER);
  const splitRef = useRef(splitLon);
  const viewportRef = useRef<Viewport | null>(null);
  const paneRef = useRef<HTMLDivElement>(null);
  const handleRef = useRef<HTMLDivElement>(null);
  const tweenRef = useRef<{ stop: () => void } | null>(null);
  const xRef = useRef(0);

  const place = useCallback((x?: number) => {
    const handle = handleRef.current;
    const viewport = viewportRef.current;
    const pane = paneRef.current;
    if (!handle || !pane) return;
    const width = pane.clientWidth;
    let px = x;
    if (px === undefined) {
      const split = splitRef.current;
      if (split <= SPLIT_ALL_AFTER) px = 0;
      else if (split >= SPLIT_ALL_BEFORE) px = width;
      else if (viewport) {
        const midLat = viewport.unproject([viewport.width / 2, viewport.height / 2])[1];
        px = viewport.project([split, midLat])[0];
      } else px = 0;
    }
    const clamped = Math.min(width, Math.max(0, px));
    xRef.current = clamped;
    handle.style.transform = `translateX(${clamped}px)`;
    handle.style.opacity = viewport ? "1" : "0";
  }, []);

  const commit = useCallback((lon: number) => {
    splitRef.current = lon;
    setSplitLon(lon);
  }, []);

  const onViewport = useCallback(
    (viewport: Viewport) => {
      viewportRef.current = viewport;
      place();
    },
    [place],
  );

  useLayoutEffect(() => {
    splitRef.current = splitLon;
    place();
  }, [splitLon, place]);

  /** A screen x inside the map, as the split it stands for. */
  const lonAt = useCallback((x: number): number => {
    const pane = paneRef.current;
    const viewport = viewportRef.current;
    if (!pane || !viewport) return SPLIT_ALL_AFTER;
    const width = pane.clientWidth;
    if (x <= 2) return SPLIT_ALL_AFTER;
    if (x >= width - 2) return SPLIT_ALL_BEFORE;
    return viewport.unproject([x, pane.clientHeight / 2])[0];
  }, []);

  const slideTo = useCallback(
    (side: "before" | "after") => {
      tweenRef.current?.stop();
      const pane = paneRef.current;
      const target = side === "before" ? SPLIT_ALL_BEFORE : SPLIT_ALL_AFTER;
      if (!pane || !viewportRef.current || reducedMotion) {
        commit(target);
        return;
      }
      const from = xRef.current;
      const to = side === "before" ? pane.clientWidth : 0;
      tweenRef.current = animate(from, to, {
        duration: DUR_MS.crossFade / 1000,
        ease: EASE_UI as unknown as [number, number, number, number],
        onUpdate: (x) => {
          place(x);
          commit(lonAt(x));
        },
        onComplete: () => commit(target),
      });
    },
    [commit, lonAt, place, reducedMotion],
  );

  useEffect(() => () => tweenRef.current?.stop(), []);

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    tweenRef.current?.stop();
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  }, []);
  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
      const pane = paneRef.current;
      if (!pane) return;
      const x = event.clientX - pane.getBoundingClientRect().left;
      place(x);
      commit(lonAt(x));
    },
    [commit, lonAt, place],
  );

  return {
    splitLon,
    paneRef,
    handleRef,
    onViewport,
    slideTo,
    onPointerDown,
    onPointerMove,
    refresh: place,
  };
}

/**
 * The map pane's size, measured before the first paint and on every resize, rounded to 8 px so a
 * scrollbar appearing does not re-frame. The opening frame is shaped to it (`drainFrame`).
 */
function usePaneSize(ref: RefObject<HTMLDivElement | null>) {
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const read = () => {
      const box = element.getBoundingClientRect();
      if (box.width < 2 || box.height < 2) return;
      const width = Math.round(box.width / 8) * 8;
      const height = Math.round(box.height / 8) * 8;
      setSize((held) =>
        held && held.width === width && held.height === height ? held : { width, height },
      );
    };
    read();
    const observer = new ResizeObserver(read);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

/** The run `/drains` opens on: `?run=`, else the 08:40 cycle, else the API's own newest. */
function useDrainsRun(city: string, pinned: string | undefined) {
  const [opening, setOpening] = useState<{ city: string; runId?: string } | null>(null);
  useEffect(() => {
    if (pinned !== undefined || opening?.city === city) return;
    let cancelled = false;
    const controller = new AbortController();
    fetchOpeningRunId(city, DRAINS_OPENING_TS, apiUrl, controller.signal).then((runId) => {
      if (!cancelled) setOpening({ city, runId });
    });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [city, pinned, opening?.city]);
  if (pinned !== undefined) return { runId: pinned, ready: true };
  if (opening?.city === city) return { runId: opening.runId, ready: true };
  return { runId: undefined, ready: false };
}

/** `GET /v1/runs/{run_id}`: `run.json` as written, read loosely for the chrome. */
interface RegistryRun {
  run_id: string;
  city: string;
  cycle_ts: string;
  mode: string;
  replay_mode?: string;
  ensemble_n?: number | null;
  stage_ms?: Record<string, number>;
  mass_balance_err?: number | null;
  bundle?: string | null;
  degraded_feeds?: string[];
  versions?: RunMeta["versions"];
  step_min?: number;
  aoi_depth_band?: RunMeta["aoi_depth_band"];
}

/**
 * Put the run this screen draws into the run store, as the console does with its own.
 *
 * The top bar's run stamp and mode banner read the store, and the shell's fallback fills it with
 * the registry's newest run - 09:10, the calm cycle after the storm - so `/drains` drawing 08:40
 * under a stamp reading 09:10 was two runs on one screen. Whoever lands last wins, and the shell's
 * fallback never overwrites a run that is already there.
 */
function useChromeRun(runId: string | undefined) {
  const setRun = useRunStore((s) => s.setRun);
  useEffect(() => {
    if (!runId || useRunStore.getState().currentRun?.run_id === runId) return;
    const controller = new AbortController();
    fetch(apiUrl(`/v1/runs/${encodeURIComponent(runId)}`), { signal: controller.signal })
      .then((r) => (r.ok ? (r.json() as Promise<RegistryRun>) : null))
      .then((run) => {
        if (!run || controller.signal.aborted || run.run_id !== runId) return;
        setRun({
          run_id: run.run_id,
          city: run.city,
          cycle_ts: run.cycle_ts,
          // `run.json` names these the other way round from the store; see `use-latest-run.ts`.
          mode: run.replay_mode === "live" ? "live" : "replay",
          replay_mode: run.mode === "live" ? "live" : "baked",
          ensemble_n: run.ensemble_n,
          stage_ms: run.stage_ms,
          mass_balance_err: run.mass_balance_err,
          bundle: run.bundle,
          degraded_feeds: run.degraded_feeds,
          versions: run.versions,
          step_min: run.step_min,
          aoi_depth_band: run.aoi_depth_band,
        });
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [runId, setRun]);
}

function DrainsView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const search = searchParams.toString();
  const city = cityFromSearch(search);
  const cityQuery = city === DEFAULT_CITY ? undefined : city;
  const pinnedRun = searchParams.get("run") ?? undefined;
  const run = useDrainsRun(city, pinnedRun);
  const reducedMotion = usePrefersReducedMotion();

  // The load is stamped with the run and city it asked about, so "still loading" is derived: a
  // result for another key is by definition stale, and no effect has to reset it first.
  const loadKey = run.ready ? `${cityQuery ?? DEFAULT_CITY}|${run.runId ?? ""}` : null;
  const [loaded, setLoaded] = useState<{
    key: string;
    health: Load<DrainHealth>;
    observed: ObservationSet | null;
  } | null>(null);
  useEffect(() => {
    if (!run.ready || loadKey === null) return;
    const controller = new AbortController();
    Promise.all([
      loadDrainHealth(run.runId, controller.signal, HEALTH_LIMIT, cityQuery, "learned"),
      loadObservations(run.runId, controller.signal, cityQuery).catch(() => null),
    ])
      .then(([h, o]) => {
        setLoaded({
          key: loadKey,
          health: h ? { state: "ready", value: h } : { state: "missing" },
          observed: o,
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setLoaded({
          key: loadKey,
          health: {
            state: "error",
            message: error instanceof Error ? error.message : "the request failed",
          },
          observed: null,
        });
      });
    return () => controller.abort();
  }, [run.ready, run.runId, cityQuery, loadKey]);
  const fresh = loaded !== null && loaded.key === loadKey ? loaded : null;
  const health: Load<DrainHealth> = fresh ? fresh.health : { state: "loading" };
  const observed = fresh ? fresh.observed : null;
  const current = health.state === "ready" ? health.value : null;
  const runId = current?.runId;
  useChromeRun(runId);

  // The full inferred graph, from the city layer: the quiet network the learning is drawn over.
  const [network, setNetwork] = useState<NetworkDrain[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    loadDrains(city, controller.signal)
      .then(setNetwork)
      .catch(() => undefined);
    return () => controller.abort();
  }, [city]);

  // The streets in the dry colour, so the sewer reads as lying under a city.
  const [streets, setStreets] = useState<SegmentPath[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    fetch(apiUrl(`/v1/city/${encodeURIComponent(city)}/layers/segments`), {
      signal: controller.signal,
    })
      .then((r) => (r.ok ? r.json() : { features: [] }))
      .then((geojson) => setStreets(allSegments(geojson)))
      .catch(() => undefined);
    return () => controller.abort();
  }, [city]);

  // Surcharge rings: drawn only when switched on, because 500 red rings drown the pipes this screen
  // is about - but loaded with the run, because where manholes surcharge is half of where the map
  // opens, and a frame that moved when the rings came on would take the camera from the reader.
  const [surchargeOn, setSurchargeOn] = useState(false);
  const [surcharge, setSurcharge] = useState<{
    runId: string;
    set: SurchargeSet | null;
    failed: boolean;
  } | null>(null);
  useEffect(() => {
    if (!runId || surcharge?.runId === runId) return;
    const controller = new AbortController();
    loadSurcharge(runId, controller.signal)
      .then((set) => setSurcharge({ runId, set, failed: false }))
      .catch(() => {
        if (!controller.signal.aborted) setSurcharge({ runId, set: null, failed: true });
      });
    return () => controller.abort();
  }, [runId, surcharge?.runId]);
  const surchargeSet = surcharge?.runId === runId ? surcharge?.set : undefined;
  const surchargeSettled = surcharge?.runId === runId;
  const allManholes = useMemo(
    () =>
      (surchargeSet?.nodes ?? []).map((n) => ({ id: n.id, lon: n.lon, lat: n.lat, q: n.peakQ })),
    [surchargeSet],
  );
  const surchargeNodes = useMemo(
    () => (surchargeOn ? allManholes : []),
    [surchargeOn, allManholes],
  );
  const surchargeNote = !surchargeOn
    ? null
    : surchargeSet
      ? surchargeSet.nodes.length > 0
        ? `Red rings: the ${surchargeSet.nodes.length.toLocaleString("en-IN")} hardest-surcharging manholes, sized by peak discharge.`
        : "No manhole surcharges in this run."
      : surcharge?.runId === runId
        ? surcharge?.failed
          ? "Surcharge rings did not load. Reload the page to try again."
          : "This run has no surcharge product. Bake it again to draw the rings."
        : null;

  const {
    splitLon,
    paneRef,
    handleRef,
    onViewport,
    slideTo,
    onPointerDown,
    onPointerMove,
    refresh: refreshSplit,
  } = useSplit(reducedMotion);
  const learned = useMemo(() => learnedDrains(current), [current]);
  const drains = useMemo(
    () =>
      withLearned(network, {
        learned,
        splitLon,
        onViewport,
      }),
    [network, learned, splitLon, onViewport],
  );

  // Where the map opens: on the part of the city, shaped like the pane, that holds the most of
  // this cycle's learning and of its surcharging manholes, at a zoom where a pipe reads - not on
  // the whole 49,770-pipe network, which is the AOI again (`drainFrame`). Decided once the run's
  // pipes and its manholes are both in, so the camera does not open on one and cut to the other;
  // settled, and a camera somebody has moved is theirs. The manholes get `MANHOLE_WAIT_MS` after
  // the pipes: past that the map opens on the pipes alone and keeps that frame, so a slow
  // surcharge product (10-11 s from the deployed API on 2026-09-29) never holds the map back.
  const paneSize = usePaneSize(paneRef);
  const [manholesLate, setManholesLate] = useState<string | null>(null);
  useEffect(() => {
    if (health.state !== "ready" || !runId || surchargeSettled) return;
    const timer = setTimeout(() => setManholesLate(runId), MANHOLE_WAIT_MS);
    return () => clearTimeout(timer);
  }, [health.state, runId, surchargeSettled]);
  const late = runId !== undefined && manholesLate === runId;
  const frameReady =
    health.state !== "loading" && (health.state !== "ready" || surchargeSettled || late);
  const frameManholes = useMemo(() => (late ? [] : allManholes), [late, allManholes]);
  const story = useMemo(() => drainStory(learned, frameManholes), [learned, frameManholes]);
  const drainFrameNext = useMemo(
    () =>
      frameReady ? drainFrame(story, paneSize ?? DRAIN_PANE_FALLBACK, cityBounds(city)) : null,
    [frameReady, story, paneSize, city],
  );
  const openingFrame = useSettledBounds(drainFrameNext);
  // "Reset view" hands the camera back to the opening frame after a pipe was shown or the map
  // was panned (CityMap re-arms its fit on a new key).
  const [fitKey, setFitKey] = useState(0);

  // The handle mounts with the learned set; put it where the split is straight away rather than
  // at the next camera move.
  const hasLearned = learned.length > 0;
  useLayoutEffect(() => {
    if (hasLearned) refreshSplit();
  }, [hasLearned, refreshSplit]);

  const hoverStore = useMemo(() => createHoverStore(), []);
  const [focus, setFocus] = useState<MapFocus | null>(null);
  const [selectedPipe, setSelectedPipe] = useState<string | null>(null);
  /** The observations on show beside the map: one picked on the strip, or every member of a
   * group mark (a cycle's traffic anomalies share one minute and one mark). */
  const [selectedObs, setSelectedObs] = useState<string[] | null>(null);

  // Where a pipe no street names is: the nearest street the segments layer names within 200 m.
  const placeAt = useMemo(() => nearbyStreetFinder(streets), [streets]);
  const cards = useMemo(
    () => rankPipeCards(current, observed, CARD_COUNT, placeAt),
    [current, observed, placeAt],
  );
  const stats = useMemo(
    () => (current ? deriveDrainStats(current, observed, placeAt) : null),
    [current, observed, placeAt],
  );

  const pathOf = useMemo(() => {
    const byId = new Map<string, [number, number][]>();
    for (const edge of network) byId.set(edge.id, edge.path);
    for (const edge of current?.edges ?? []) byId.set(edge.id, edge.path);
    return byId;
  }, [network, current]);

  const flyToPipe = useCallback(
    (edgeId: string, lonLat?: [number, number] | null) => {
      const at = lonLat ?? midpoint(pathOf.get(edgeId) ?? []);
      if (!at) return;
      setSelectedPipe(edgeId);
      setFocus({ lon: at[0], lat: at[1], key: `${edgeId}-${Date.now()}`, zoom: 16 });
    },
    [pathOf],
  );
  const selectCard = useCallback(
    (pipe: PipeCardData) => flyToPipe(pipe.id, [pipe.lon, pipe.lat]),
    [flyToPipe],
  );
  const selectObservation = useCallback(
    (obs: AssimilatedObservation) => {
      setSelectedObs([obs.id]);
      if (obs.edgeId) flyToPipe(obs.edgeId);
    },
    [flyToPipe],
  );
  // A group is listed beside the map, and the map frames the middle of the pipes its members
  // landed on, at a zoom that keeps a few kilometres in view rather than diving into one of them.
  const selectGroup = useCallback(
    (members: readonly AssimilatedObservation[]) => {
      setSelectedObs(members.map((m) => m.id));
      setSelectedPipe(null);
      const points = members
        .map((m) => (m.edgeId ? midpoint(pathOf.get(m.edgeId) ?? []) : null))
        .filter((p): p is [number, number] => p !== null);
      if (points.length === 0) return;
      const lon = points.reduce((a, p) => a + p[0], 0) / points.length;
      const lat = points.reduce((a, p) => a + p[1], 0) / points.length;
      setFocus({ lon, lat, key: `group-${members[0].id}-${Date.now()}`, zoom: 13 });
    },
    [pathOf],
  );

  const toCard = (o: AssimilatedObservation): Observation => ({
    id: o.id,
    kind: o.kind,
    ts: o.ts,
    place: o.locality ? `${o.place}, ${o.locality}` : o.place,
    inferredDepthCm: o.depthCm,
    pipeId: o.edgeId ?? undefined,
    // Left undefined rather than defaulted, so a run baked before Pulse recorded the pair says
    // it has no change instead of printing "0.00 → 0.00" as though the filter had moved nothing.
    betaBefore: o.betaBefore ?? undefined,
    betaAfter: o.betaAfter ?? undefined,
    synthetic: o.synthetic,
  });
  const observations = observed?.observations ?? [];
  const selectedObservations = selectedObs
    ? observations.filter((o) => selectedObs.includes(o.id))
    : [];

  const rows: DrainHealthRow[] = useMemo(() => {
    const edges = current?.edges ?? [];
    const moved = edges.filter((e) => e.moved);
    const worst = edges.filter((e) => !e.moved).slice(0, TABLE_WORST);
    return [...moved, ...worst].map((edge) => ({
      id: edge.id,
      street: edge.displayName ?? UNNAMED_PLACE,
      betaMean: edge.betaMean,
      betaSd: edge.betaSd,
      capacityReduction: edge.capacityReductionPct / 100,
      hotspotsExplained: edge.explains,
      observations: edge.observations,
      lastUpdated: edge.lastUpdate ?? "",
      betaDelta: edge.betaDelta,
    }));
  }, [current]);

  const [showObservations, setShowObservations] = useState(false);
  const [showPipes, setShowPipes] = useState(false);
  const observationsId = useId();
  const pipesId = useId();

  const toggleValue =
    splitLon <= SPLIT_ALL_AFTER ? ["after"] : splitLon >= SPLIT_ALL_BEFORE ? ["before"] : [];

  const pickCycle = useCallback(
    (next: string) => {
      setSelectedPipe(null);
      setSelectedObs(null);
      const params = new URLSearchParams(search);
      params.set("run", next);
      router.replace(`/drains?${params.toString()}`, { scroll: false });
    },
    [router, search],
  );

  // The map mounts once it knows where to open, so it never paints the whole city first and cuts.
  const mapReady = frameReady && (network.length > 0 || streets.length > 0 || learned.length > 0);
  const cycleTs = observed?.cycleTs || current?.edges[0]?.lastUpdate || "";

  return (
    <AppShell>
      {/*
       * A two-pane screen with the viewport's height, not a scrolling page: the map takes the
       * height it is given and the column beside it scrolls on its own (repairs D-18).
       */}
      <div className="flex h-full min-h-0 flex-col overflow-hidden">
        <div className="mx-auto flex min-h-0 w-full max-w-[1600px] flex-1 flex-col gap-3 p-6">
          <PageHeader
            title={navItem("drains").label}
            screen={navItem("drains")}
            honesty="Inferred drain graph"
            actions={
              <>
                <CyclePicker currentRunId={runId ?? run.runId} onPick={pickCycle} />
                <AboutMap health={current} stats={stats} surchargeNote={surchargeNote} />
              </>
            }
          />

          {health.state === "missing" ? (
            <EmptyState
              size="sm"
              title="No drain health for this run yet"
              description="Baked before drain health existed. Pick a later cycle, or press Play on the replay."
            />
          ) : health.state === "error" ? (
            <EmptyState
              size="sm"
              title="Drain health did not load"
              description={`The API answered: ${health.message}. Reload the page to try again.`}
            />
          ) : (
            <DrainStatsStrip stats={stats} />
          )}

          <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(320px,380px)]">
            <div className="flex min-h-0 min-w-0 flex-col gap-3">
              <PanelErrorBoundary title="Drain map">
                <section
                  aria-label="Drain map"
                  className="rounded-panel border-line bg-deep flex min-h-[360px] flex-1 flex-col overflow-hidden border"
                >
                  <header className="border-line flex flex-wrap items-center justify-between gap-3 border-b px-4 py-2">
                    <MapLegend />
                    <div className="flex flex-wrap items-center gap-3">
                      <label className="type-micro text-text-2 inline-flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={surchargeOn}
                          onChange={(e) => setSurchargeOn(e.target.checked)}
                          disabled={!runId}
                          className="size-4 accent-[var(--tide)]"
                        />
                        Surcharge rings
                      </label>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={!openingFrame}
                        onClick={() => setFitKey((k) => k + 1)}
                      >
                        <Scan size={16} strokeWidth={1.75} aria-hidden="true" />
                        Reset view
                      </Button>
                      <ToggleGroup
                        aria-label="Drain state"
                        aria-describedby="drain-toggle-help"
                        value={toggleValue}
                        onValueChange={(value) => {
                          if (value.includes("before") && !toggleValue.includes("before"))
                            slideTo("before");
                          else if (value.includes("after") && !toggleValue.includes("after"))
                            slideTo("after");
                        }}
                      >
                        <ToggleGroupItem
                          value="before"
                          variant="outline"
                          size="sm"
                          disabled={!current}
                          title="The prior every pipe started with"
                        >
                          Before
                        </ToggleGroupItem>
                        <ToggleGroupItem
                          value="after"
                          variant="outline"
                          size="sm"
                          disabled={!current}
                          title="The posterior Pulse learned"
                        >
                          After
                        </ToggleGroupItem>
                      </ToggleGroup>
                      <span id="drain-toggle-help" className="sr-only">
                        Before draws every learned pipe at its land-use prior, After at what Pulse
                        learned. Drag the handle on the map to compare the two side by side.
                      </span>
                    </div>
                  </header>
                  <div
                    ref={paneRef}
                    className="relative min-h-0 flex-1"
                    // deck reports no hover when the pointer leaves the canvas, so the tooltip
                    // would stay on the last pipe; leaving the pane clears it.
                    onPointerLeave={() => hoverStore.set(null)}
                  >
                    {mapReady ? (
                      <CityMap
                        frames={[]}
                        rasterBounds={null}
                        baseSegments={streets}
                        segments={[]}
                        surcharge={surchargeNodes}
                        surchargeStyle="ring"
                        hotspots={[]}
                        drains={drains}
                        showDrains
                        showRaster={false}
                        showSegments={false}
                        showSurcharge={surchargeOn}
                        showBuildings={false}
                        step={0}
                        focus={focus}
                        fitBounds={openingFrame}
                        fitKey={fitKey}
                        fitPadding={DRAIN_FIT_PADDING}
                        onDrainHover={hoverStore.set}
                        drainCrossFadeMs={reducedMotion ? 0 : DUR_MS.crossFade}
                      />
                    ) : (
                      <MapSlot />
                    )}
                    {/* The split handle: prior to its left, posterior to its right. */}
                    {learned.length > 0 ? (
                      <div
                        ref={handleRef}
                        aria-hidden="true"
                        onPointerDown={onPointerDown}
                        onPointerMove={onPointerMove}
                        className="absolute inset-y-0 left-0 z-10 w-0 cursor-ew-resize touch-none opacity-0"
                      >
                        <div className="absolute inset-y-0 -left-2 w-4" />
                        <div className="bg-text-2 absolute inset-y-0 -left-px w-0.5" />
                        <div className="border-line-strong bg-deep text-text-2 absolute top-1/2 -left-3 flex size-6 -translate-y-1/2 items-center justify-center rounded-full border">
                          <ChevronsLeftRight size={14} strokeWidth={1.75} />
                        </div>
                        {/* Labels only: a drag that starts on one pans the map, not the split. */}
                        <span className="rounded-chip bg-deep type-micro text-text-2 pointer-events-none absolute top-2 right-2 px-2 py-0.5 whitespace-nowrap">
                          Prior
                        </span>
                        <span className="rounded-chip bg-deep type-micro text-text-2 pointer-events-none absolute top-2 left-2 px-2 py-0.5 whitespace-nowrap">
                          Learned
                        </span>
                      </div>
                    ) : null}
                    <DrainTooltip store={hoverStore} placeAt={placeAt} />
                  </div>
                  {/*
                   * The assimilation timeline, as the map's own footer: what taught the pipes
                   * above it, on one line of time from two hours before the cycle to the cycle.
                   */}
                  <PanelErrorBoundary title="Observation strip">
                    <footer
                      aria-label="Observations this cycle"
                      className="border-line border-t px-4 pt-2 pb-2"
                    >
                      {observed ? (
                        <ObservationStrip
                          observations={observations}
                          cycleTs={cycleTs}
                          selectedIds={selectedObs}
                          onSelect={selectObservation}
                          onSelectGroup={selectGroup}
                        />
                      ) : health.state === "loading" ? (
                        <Skeleton className="h-12" />
                      ) : (
                        <p className="type-micro text-text-3">
                          This run has no observation product.
                        </p>
                      )}
                    </footer>
                  </PanelErrorBoundary>
                </section>
              </PanelErrorBoundary>
            </div>

            <div
              role="region"
              aria-label="Learned pipes and exports"
              tabIndex={0}
              className="flex min-h-0 min-w-0 flex-col gap-3 overflow-y-auto pr-1"
            >
              {selectedObservations.length > 0 ? (
                <section aria-label="Selected on the strip" className="space-y-1">
                  <div className="flex items-center justify-between gap-2">
                    <p className="type-micro text-text-3">
                      {selectedObservations.length === 1
                        ? "Selected observation"
                        : `${selectedObservations.length} observations at ${formatIst(selectedObservations[0].ts)}`}
                    </p>
                    <Button variant="ghost" size="sm" onClick={() => setSelectedObs(null)}>
                      Clear
                    </Button>
                  </div>
                  <ol className="flex flex-col gap-2">
                    {selectedObservations.map((o) => (
                      <li key={o.id}>
                        <ObservationCard obs={toCard(o)} />
                      </li>
                    ))}
                  </ol>
                </section>
              ) : null}

              <PanelErrorBoundary title="Largest learned changes">
                <Panel title="Largest learned changes">
                  {health.state === "loading" ? (
                    <Skeleton lines={4} />
                  ) : health.state !== "ready" ? (
                    // The run has no drain health, so there is nothing to rank; the empty state
                    // above the map already says what to do, and "no pipe moved" would be a claim.
                    <p className="type-micro text-text-3">
                      No pipes to name: this run has no drain health.
                    </p>
                  ) : cards.length === 0 ? (
                    <EmptyState
                      size="sm"
                      title="No pipe moved this cycle"
                      description="Nothing to assimilate yet. Pick a later cycle, or press Play on the replay."
                    />
                  ) : (
                    <ol className="flex flex-col gap-2">
                      {cards.map((pipe) => (
                        <li key={pipe.id}>
                          <PipeCard
                            pipe={pipe}
                            selected={pipe.id === selectedPipe}
                            onSelect={selectCard}
                          />
                        </li>
                      ))}
                    </ol>
                  )}
                </Panel>
              </PanelErrorBoundary>

              <PanelErrorBoundary title="All observations">
                <section className="rounded-panel border-line bg-deep border p-4">
                  <Button
                    variant="outline"
                    className="w-full"
                    aria-expanded={showObservations}
                    aria-controls={observationsId}
                    disabled={observations.length === 0}
                    onClick={() => setShowObservations((v) => !v)}
                  >
                    {showObservations
                      ? "Hide observations"
                      : `See all observations (${observations.length})`}
                  </Button>
                  <div id={observationsId} hidden={!showObservations} className="mt-3">
                    {showObservations ? (
                      <ol className="flex flex-col gap-2">
                        {observations.map((o) => (
                          <li key={o.id}>
                            <ObservationCard obs={toCard(o)} />
                          </li>
                        ))}
                      </ol>
                    ) : null}
                  </div>
                </section>
              </PanelErrorBoundary>

              <PanelErrorBoundary title="All pipes">
                <section className="rounded-panel border-line bg-deep border p-4">
                  <Button
                    variant="outline"
                    className="w-full"
                    aria-expanded={showPipes}
                    aria-controls={pipesId}
                    disabled={rows.length === 0}
                    onClick={() => setShowPipes((v) => !v)}
                  >
                    {showPipes ? "Hide pipes" : `See all pipes (${rows.length})`}
                  </Button>
                  <div id={pipesId} hidden={!showPipes} className="mt-3">
                    {showPipes ? (
                      <>
                        <p className="type-micro text-text-3 mb-2">
                          Moved pipes, then the {TABLE_WORST} worst by blockage.
                        </p>
                        <DrainHealthTable rows={rows} defaultSort="change" />
                      </>
                    ) : null}
                  </div>
                  <Button
                    variant="outline"
                    className="mt-3 w-full"
                    disabled={!current}
                    onClick={() => {
                      if (current) window.open(desiltingCsvUrl(current.runId, cityQuery), "_blank");
                    }}
                  >
                    <Download size={16} strokeWidth={1.75} aria-hidden="true" />
                    Export desilting priority (CSV)
                  </Button>
                </section>
              </PanelErrorBoundary>

              <section className="rounded-panel border-line bg-deep border p-4">
                <p className="type-small text-text inline-flex items-center gap-2 font-medium">
                  <Layers3 size={16} strokeWidth={1.75} aria-hidden="true" />
                  Drains in 3D
                </p>
                <p className="type-micro text-text-2 mt-1">
                  In the console, press 3, then X, to see these pipes under the city.
                </p>
                <Link
                  href={runId ? `/console?run=${encodeURIComponent(runId)}` : "/console"}
                  className={cn(
                    "type-micro text-tide focus-visible:outline-tide mt-2 inline-flex underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2",
                  )}
                >
                  Open this cycle in the console
                </Link>
              </section>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

/** `/drains`, behind the Suspense boundary `useSearchParams` needs for `next build`. */
export function DrainsScreen() {
  return (
    <Suspense
      fallback={
        <AppShell>
          <div className="p-6">
            <Skeleton lines={3} />
          </div>
        </AppShell>
      }
    >
      <DrainsView />
    </Suspense>
  );
}

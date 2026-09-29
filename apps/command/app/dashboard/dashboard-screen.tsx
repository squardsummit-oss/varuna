"use client";

/**
 * The citizen dashboard (`UI_SPEC` 3, task D-11).
 *
 * The console answers "what is the city doing?". This screen answers the two questions a person
 * standing in the rain actually has: *which streets near me are passable*, and *what is the safe
 * way to where I am going*. Everything on it is one of those two answers or the provenance of one.
 *
 * Three rules shape the layout.
 *
 * 1. **The map owns its box.** The pane is `relative` and the map is `absolute inset-0`, so it
 *    fills whatever it is given at 390 x 844, at 1440 x 900 and on a wall - no `vh` band, no fixed
 *    pixel height, and `fitBounds` frames the AOI on first paint.
 * 2. **One rail, two shapes.** At 1024 px and up it is a column beside the map; below that it is
 *    the existing `BottomSheet` (motion M24) over it, which is the shape a thumb can reach.
 * 3. **No number floats free.** The header carries the run and its time, and the three honesty
 *    chips name what each kind of number is, so a depth, a minute and a temperature on this screen
 *    can each be traced to what produced it (SPEC.md rules 6 and 7).
 */

import Link from "next/link";
import type { Route } from "next";
import { LocateFixed, Umbrella } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CitizenMap, STOPS_AT_CM, type MapPoint } from "@/components/citizen/citizen-map";
import { DashboardIntro } from "@/components/citizen/dashboard-intro";
import {
  DEFAULT_LEAD_MIN,
  LEAD_DEFAULT_REASON,
  LeadTimeControl,
  stepForLead,
  stepLabel,
  type LeadMinutes,
} from "@/components/citizen/lead-time";
import {
  ComplaintsNearYou,
  MyReportsPanel,
  SelectedReportCard,
} from "@/components/citizen/report-panels";
import { RouteAnswer } from "@/components/citizen/route-answer";
import {
  TIME_BASE_LABEL,
  TimeBaseSwitch,
  type TimeBase,
} from "@/components/citizen/time-base-switch";
import { DASHBOARD_OPENING_TS, useDashboardRun } from "@/components/citizen/use-dashboard-run";
import { useMyReports, usePublicReports } from "@/components/citizen/use-report-feeds";
import { WeatherChip } from "@/components/citizen/weather-chip";
import { ThemeToggle } from "@/components/varuna/theme-toggle";
import { CAUTION_FRACTION } from "@/components/map/layers/palette";
import { REPORT_FOCUS_ZOOM } from "@/components/map/layers/reports";
import type { MapFocus } from "@/components/map/layers/types";
import { Button } from "@/components/ui/button";
import { BottomSheet } from "@/components/varuna/bottom-sheet";
import { EmptyState } from "@/components/varuna/empty-state";
import { LiveOutlookCard } from "@/components/varuna/live-outlook-card";
import { PublicLegend } from "@/components/varuna/public-legend";
import { Skeleton } from "@/components/varuna/skeleton";
import { VehicleSelector, type PublicProfile } from "@/components/varuna/vehicle-selector";
import { Wordmark } from "@/components/varuna/wordmark";
import { reportToPin } from "@/lib/api/reports";
import { loadPlaces, planRoute, type Place, type RoutePlan } from "@/lib/api/route";
import { formatDate, formatIst } from "@/lib/format";
import { useMediaQuery } from "@/lib/hooks";
import type { CitizenRun } from "@/lib/maps/citizen-run";
import { LIST_ROAD } from "@/lib/street-label";

const REPORT_ROUTE = "/report" as Route;

/** One trip keeps one corridor across reloads, so a reader is not re-spread on every request. */
const TRIP_ID_KEY = "varuna.dashboard.trip-id";

/** How far "near you" reaches. Beyond this a street is not on the way out of the door. */
export const NEARBY_RADIUS_M = 1_500;

/** Rows in the list. More than this on a phone and nobody reaches the bottom. */
const NEARBY_LIMIT = 8;

/**
 * A street's row name when neither the API's `display_name` nor OSM names it: only a segment the
 * city's street layer does not carry. OSM names none of 52.6 % of Mumbai's segments; the layer's
 * `display_name` ("off Dr Ambedkar Road", "Service road near Wadala Depot") names every one.
 */
export const UNLISTED_ROAD = LIST_ROAD;

/**
 * The tolerance a citizen route is planned at: **the profile's own, chosen by the API**.
 *
 * Not a number, on purpose. `JSON.stringify` writes `null` for `NaN`, the API reads no tolerance
 * and applies the profile's own from `services/route/varuna_route/profiles.py`. This screen used
 * to send a flat 0.5 for everyone, which copied a server-side constant into the browser and then
 * disagreed with it: a bus is refused a street at 0.4 and someone on foot at 0.3, so the same
 * trip answered differently here and on `/rural`, which sends nothing. One trip, one answer.
 */
const CITIZEN_RISK_TOLERANCE = Number.NaN;

/** UI_SPEC 3's split: a rail beside the map at 1024 px and up, the bottom sheet below it. */
const RAIL_BREAKPOINT = "(min-width: 1024px)";

/**
 * The phone's stack over the map, bottom up: the collapsed sheet (96 px), the time strip on top
 * of it, and the floating Report water button above the strip.
 */
const TIME_STRIP_BOTTOM_PX = 104;
const REPORT_BUTTON_BOTTOM_PX = 196;

/** Metres per degree of latitude; longitude is scaled by the cosine at the reader's latitude. */
const METRES_PER_DEGREE = 111_320;

/** Straight-line distance in metres, flat-earth over a 1.5 km radius, which is exact enough. */
export function distanceM(from: MapPoint, to: readonly [number, number]): number {
  const dy = (to[1] - from.lat) * METRES_PER_DEGREE;
  const dx = (to[0] - from.lon) * METRES_PER_DEGREE * Math.cos((from.lat * Math.PI) / 180);
  return Math.hypot(dx, dy);
}

export interface NearbyStreet {
  id: string;
  name: string;
  /** Depth at the step the map is coloured at, so the row and the street's colour agree. */
  cm: number;
  /** The deepest it gets from that step to the end of the forecast. */
  peakCm: number;
  /** Last step still passable for this vehicle, as IST; null when it is impassable at the step. */
  passableUntil: string | null;
  /** True when it never reaches the vehicle's stopping depth before the forecast ends. */
  throughEnd: boolean;
}

/**
 * The streets near the reader that matter for this vehicle **at the step the map shows**.
 *
 * A street is listed when the map paints it caution or impassable at that step - the same
 * {@link CAUTION_FRACTION} of the stopping depth the three-colour map uses - or when it is passable
 * there but closes later in the forecast, because "passable until 09:25" is the one fact a reader
 * needs about a street that is still blue. Impassable streets come first, then caution, deepest
 * first, then the ones that close, soonest first.
 *
 * With a position, only streets within {@link NEARBY_RADIUS_M} of it; without one - geolocation
 * refused, unavailable, or not yet answered - every street in the AOI, and the caller says so in
 * words rather than calling the city's worst street "near you".
 */
export function nearbyStreets(
  segments: readonly {
    id: string;
    path: [number, number][];
    depthCm: number[];
    name?: string;
    displayName?: string;
  }[],
  validTs: readonly string[],
  stopsAtCm: number,
  at: MapPoint | null,
  step = 0,
  limit = NEARBY_LIMIT,
): NearbyStreet[] {
  const rows: { row: NearbyStreet; rank: number; closesAt: number }[] = [];
  for (const segment of segments) {
    const series = segment.depthCm;
    const cm = series[step] ?? 0;
    let firstOver = -1;
    for (let i = step; i < series.length; i += 1) {
      if (series[i] >= stopsAtCm) {
        firstOver = i;
        break;
      }
    }
    const caution = cm >= stopsAtCm * CAUTION_FRACTION;
    if (!caution && firstOver < 0) continue;
    if (at && !segment.path.some((point) => distanceM(at, point) <= NEARBY_RADIUS_M)) continue;

    const ahead = series.slice(step);
    const last = validTs[validTs.length - 1] ?? "";
    rows.push({
      row: {
        id: segment.id,
        name: segment.displayName || segment.name || UNLISTED_ROAD,
        cm,
        peakCm: ahead.length ? Math.max(...ahead) : cm,
        passableUntil:
          firstOver === step
            ? null
            : formatIst(firstOver < 0 ? last : (validTs[firstOver - 1] ?? last)),
        throughEnd: firstOver < 0,
      },
      rank: firstOver === step ? 0 : caution ? 1 : 2,
      closesAt: firstOver < 0 ? Number.POSITIVE_INFINITY : firstOver,
    });
  }
  rows.sort((a, b) => {
    if (a.rank !== b.rank) return a.rank - b.rank;
    if (a.rank === 2 && a.closesAt !== b.closesAt) return a.closesAt - b.closesAt;
    return b.row.cm - a.row.cm || b.row.peakCm - a.row.peakCm;
  });
  return rows.slice(0, limit).map((entry) => entry.row);
}

/** The header's honesty line: which run drew this map, and for when. */
export function runLine(run: CitizenRun | null, failed: boolean): string {
  if (run?.provenance.cycleTs) {
    return `Forecast from ${formatIst(run.provenance.cycleTs)} IST, ${formatDate(run.provenance.cycleTs)}`;
  }
  if (failed) return "The forecast did not load; check the connection and reload";
  return "Loading the last VARUNA run";
}

/** The three labels UI_SPEC 3 fixes, each naming a kind of number this screen shows. */
export const HONESTY_CHIPS = [
  "Reconstructed replay",
  "Emulator estimate",
  "Live weather - Open-Meteo",
] as const;

/** The reader's own position, once they grant it. Never asked for without a click. */
function useMyLocation(): {
  position: MapPoint | null;
  state: "idle" | "asking" | "granted" | "refused";
  ask: () => void;
} {
  const [position, setPosition] = useState<MapPoint | null>(null);
  const [state, setState] = useState<"idle" | "asking" | "granted" | "refused">("idle");

  const ask = useCallback(() => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setState("refused");
      return;
    }
    setState("asking");
    navigator.geolocation.getCurrentPosition(
      (fix) => {
        setPosition({ lon: fix.coords.longitude, lat: fix.coords.latitude });
        setState("granted");
      },
      // A refusal is a choice, not an error: the screen falls back to the place the reader named.
      () => setState("refused"),
      { timeout: 8_000, maximumAge: 60_000 },
    );
  }, []);

  return { position, state, ask };
}

/** The id this browser uses for this trip, so the corridor it was given stays its corridor. */
function tripId(): string | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const stored = window.sessionStorage.getItem(TRIP_ID_KEY);
    if (stored) return stored;
    const made =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `trip-${Date.now()}-${Math.round(Math.random() * 1e9)}`;
    window.sessionStorage.setItem(TRIP_ID_KEY, made);
    return made;
  } catch {
    // A private window can refuse storage; the API then spreads this request on its own.
    return undefined;
  }
}

const PICKED_ON_MAP = "__picked__";
const MY_LOCATION = "__me__";

function ReportWaterButton({ variant }: { variant: "solid" | "outline" }) {
  return (
    <Button
      size="lg"
      variant={variant === "outline" ? "outline" : undefined}
      // 44 px, the citizen floor (SPEC.md 6.5, 7.11).
      className="h-11"
      render={<Link href={REPORT_ROUTE} />}
      nativeButton={false}
    >
      <Umbrella aria-hidden="true" />
      Report water
    </Button>
  );
}

/** A native select: on a phone it opens the OS picker, which beats any listbox we could draw. */
function PlaceField({
  id,
  label,
  value,
  onChange,
  places,
  extra,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  places: readonly Place[];
  extra?: { value: string; label: string };
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="type-micro text-text-2">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="border-line bg-well text-text type-small rounded-control focus-visible:border-line-strong h-11 w-full border px-3 outline-none focus-visible:ring-2 focus-visible:ring-[var(--tide)]"
      >
        <option value="">Choose a place</option>
        {extra ? <option value={extra.value}>{extra.label}</option> : null}
        {places.map((place) => (
          <option key={place.id} value={place.id}>
            {place.name}
          </option>
        ))}
      </select>
    </div>
  );
}

/** The cycle the map is drawn from, as a native select: a phone opens it as its own picker. */
function ForecastCycleSelect({
  value,
  cycles,
  onChange,
}: {
  value: string;
  cycles: readonly { runId: string; cycleTs: string }[];
  onChange: (runId: string) => void;
}) {
  if (cycles.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor="dashboard-cycle" className="type-micro text-text-2">
        Forecast cycle, {formatDate(cycles[0]?.cycleTs)}
      </label>
      <select
        id="dashboard-cycle"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="num border-line bg-well text-text type-small rounded-control focus-visible:border-line-strong h-11 w-full border px-3 outline-none focus-visible:ring-2 focus-visible:ring-[var(--tide)]"
      >
        {value && !cycles.some((c) => c.runId === value) ? (
          <option value={value}>The run on the map</option>
        ) : null}
        {cycles.map((c) => (
          <option key={c.runId} value={c.runId}>
            {`Forecast from ${formatIst(c.cycleTs)} IST`}
          </option>
        ))}
      </select>
    </div>
  );
}

export function DashboardScreen() {
  const city = "mumbai";
  const [profile, setProfile] = useState<PublicProfile>("car");
  const [run, setRun] = useState<CitizenRun | null>(null);
  const [runFailed, setRunFailed] = useState(false);
  const [introDone, setIntroDone] = useState(false);
  const [lead, setLead] = useState<LeadMinutes>(DEFAULT_LEAD_MIN);
  // The reader's own choice of clock, once they make one; until then the clock follows the run
  // on the map, which is today's live cycle whenever the live loop has a fresh one.
  const [chosenBase, setChosenBase] = useState<TimeBase | null>(null);
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);

  const [places, setPlaces] = useState<Place[]>([]);
  const [fromId, setFromId] = useState("");
  const [toId, setToId] = useState("");
  const [picked, setPicked] = useState<MapPoint | null>(null);
  const [plan, setPlan] = useState<RoutePlan | null>(null);
  const [corridorId, setCorridorId] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);

  const { position, state: locationState, ask } = useMyLocation();
  const stageRef = useRef<HTMLDivElement>(null);
  const [sheetHeight, setSheetHeight] = useState(600);

  const cycle = useDashboardRun(city);
  const publicReports = usePublicReports(city);
  const mine = useMyReports();

  // **The rail exists once, not twice.** Rendering both shapes and hiding one with `lg:hidden`
  // puts two "From" comboboxes, two "To" comboboxes and two copies of every id in the document,
  // which is a real accessibility defect and not a styling detail. The server assumes the phone
  // shape (UI_SPEC 3's default) and hydration corrects it.
  const wide = useMediaQuery(RAIL_BREAKPOINT);

  const onIntroDone = useCallback(() => setIntroDone(true), []);
  const onRunLoaded = useCallback((loaded: CitizenRun) => {
    setRun(loaded);
    setRunFailed(false);
  }, []);

  const { pick: pickRun, runId: pickedRunId } = cycle;
  const shownRunId = pickedRunId ?? run?.provenance.runId ?? "";
  const liveCycle = cycle.live;
  const shownLive = liveCycle !== null && shownRunId === liveCycle.runId;
  const timeBase: TimeBase = chosenBase ?? (shownLive ? "today" : "replay");
  const pickCycle = useCallback(
    (runId: string) => {
      if (!runId || runId === shownRunId) return;
      // The header stops naming the old run the moment another is asked for, and a trip priced
      // on the old run's streets is not left on screen over the new one's.
      setRun(null);
      setPlan(null);
      setPlanError(null);
      pickRun(runId);
    },
    [pickRun, shownRunId],
  );

  // "Today" draws today's live cycle when there is one; "2 July 2019" goes back to the storm's
  // peak cycle. Without a live cycle "Today" keeps its old meaning: the outlook card, with the
  // map left on the replay and saying so.
  const setTimeBase = useCallback(
    (next: TimeBase) => {
      setChosenBase(next);
      if (next === "today" && liveCycle && shownRunId !== liveCycle.runId) {
        pickCycle(liveCycle.runId);
      }
      if (next === "replay" && shownLive) {
        const peak =
          cycle.cycles.find((c) => Date.parse(c.cycleTs) === Date.parse(DASHBOARD_OPENING_TS)) ??
          cycle.cycles[cycle.cycles.length - 1];
        if (peak) pickCycle(peak.runId);
      }
    },
    [liveCycle, shownRunId, shownLive, cycle.cycles, pickCycle],
  );
  const liveFrom = shownLive && liveCycle ? formatIst(liveCycle.cycleTs) : null;
  const mapLabel = liveFrom ? `Today, live forecast from ${liveFrom}` : TIME_BASE_LABEL.replay;

  useEffect(() => {
    const controller = new AbortController();
    loadPlaces(city, controller.signal)
      .then(setPlaces)
      // Without the registers the trip form has nothing to offer; the map and the street list
      // still work, and the form says so below rather than throwing the screen away.
      .catch(() => undefined);
    return () => controller.abort();
  }, [city]);

  // The map pane's height, which the sheet's snap points are shares of.
  useEffect(() => {
    const node = stageRef.current;
    if (!node) return;
    const update = () => setSheetHeight(node.clientHeight);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // Nothing baked, or the API is down: the map says so itself, and the header stops claiming a
  // run is on the way once a reasonable wait has passed without one.
  useEffect(() => {
    if (run) return;
    const timer = window.setTimeout(() => setRunFailed(true), 15_000);
    return () => window.clearTimeout(timer);
  }, [run]);

  // **Showing a report from the list puts the phone's sheet down.** On a phone the list lives in
  // the sheet, and a sheet left fully open covers the map: measured at 390 x 844, "Show on the map"
  // turned into "Shown on the map" over a card of which one line showed above the sheet and a pin
  // nobody could see. `BottomSheet` keeps its snap to itself, so a new key mounts it again at its
  // default, collapsed - a cut, not a motion (section 8 has no row for a sheet that closes itself).
  // The button that had focus goes with the old sheet, so focus moves to the card it opened.
  const [sheetKey, setSheetKey] = useState(0);
  const [focusCard, setFocusCard] = useState(false);
  // "Show on the map" also brings the pin into view: the list is ordered by distance from the
  // reader, not by what the map is showing, so the report chosen may be off screen or a dot among
  // many at the city's zoom. The desk's focus, at the desk's zoom, applied once per press.
  const [reportFocus, setReportFocus] = useState<MapFocus | null>(null);
  const showReportOnMap = useCallback(
    (id: string) => {
      setSelectedReportId(id);
      const report = publicReports.reports.find((r) => r.id === id);
      if (report) {
        setReportFocus({
          lon: report.lon,
          lat: report.lat,
          key: `report-${id}-${Date.now()}`,
          zoom: REPORT_FOCUS_ZOOM,
        });
      }
      if (wide) return;
      setSheetKey((key) => key + 1);
      setFocusCard(true);
    },
    [wide, publicReports.reports],
  );
  const pickReportOnMap = useCallback((id: string) => {
    setSelectedReportId(id);
    // A pin tapped on the map keeps focus where the reader is: on the map.
    setFocusCard(false);
  }, []);

  // Escape puts away the card a pin opened, as it would any other popover.
  useEffect(() => {
    if (!selectedReportId) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelectedReportId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedReportId]);

  /** The step the map is coloured at: the chosen lead, read off this run's own valid times. */
  const step = run ? stepForLead(run.validTs, run.provenance.cycleTs, lead) : 0;
  const stepTime = run ? stepLabel(run.validTs, run.provenance.cycleTs, step) : null;
  /** The instant a trip planned now departs at: the step on the map, never this laptop's clock. */
  const departAt = run ? (run.validTs[step] ?? run.provenance.cycleTs ?? null) : null;

  const pins = useMemo(() => publicReports.reports.map(reportToPin), [publicReports.reports]);
  const selectedReport = useMemo(
    () => publicReports.reports.find((report) => report.id === selectedReportId) ?? null,
    [publicReports.reports, selectedReportId],
  );

  const byId = useMemo(() => new Map(places.map((p) => [p.id, p])), [places]);

  /** Where "near you" is centred: the reader's fix, else the place they named as their start. */
  const centre = useMemo<MapPoint | null>(() => {
    if (position) return position;
    const from = byId.get(fromId);
    return from ? { lon: from.lon, lat: from.lat } : null;
  }, [position, byId, fromId]);

  const nearby = useMemo(
    () => (run ? nearbyStreets(run.segments, run.validTs, STOPS_AT_CM[profile], centre, step) : []),
    [run, profile, centre, step],
  );

  const origin = fromId === MY_LOCATION ? position : (byId.get(fromId) ?? null);
  const destination = toId === PICKED_ON_MAP ? picked : (byId.get(toId) ?? null);
  const canPlan = Boolean(origin && destination && run && departAt && !planning);

  const pickPoint = useCallback((point: MapPoint) => {
    setPicked(point);
    setToId(PICKED_ON_MAP);
  }, []);

  /** Plan the trip, departing at the step on the map unless a new lead's step is passed in. */
  const planAt = useCallback(
    (at: string | null) => {
      if (!origin || !destination || !run || !at) return;
      setPlanning(true);
      setPlanError(null);
      // The map is a reconstruction of 2 July 2019, so the trip departs at the step the map is
      // drawing. Costing it against the clock on this laptop would price a 2019 storm in 2026.
      planRoute({
        origin: { id: "from", name: "From", kind: "hotspot", lon: origin.lon, lat: origin.lat },
        destination: {
          id: "to",
          name: "To",
          kind: "hotspot",
          lon: destination.lon,
          lat: destination.lat,
        },
        departAt: at,
        profile,
        riskTolerance: CITIZEN_RISK_TOLERANCE,
        runId: run.provenance.runId || undefined,
        spread: true,
        explain: true,
        tripId: tripId(),
      })
        .then((next) => {
          setPlan(next);
          setCorridorId(next.corridors.find((c) => c.assigned)?.id ?? null);
        })
        .catch((error: unknown) => {
          setPlan(null);
          setPlanError(error instanceof Error ? error.message : String(error));
        })
        .finally(() => setPlanning(false));
    },
    [origin, destination, run, profile],
  );
  const findRoute = useCallback(() => planAt(departAt), [planAt, departAt]);

  // A trip on screen departs at the step on the map, so moving the lead re-prices it rather than
  // leaving a route for 09:40 under a map of 11:40.
  const changeLead = useCallback(
    (next: LeadMinutes) => {
      setLead(next);
      if (!plan || !run) return;
      planAt(run.validTs[stepForLead(run.validTs, run.provenance.cycleTs, next)] ?? null);
    },
    [plan, run, planAt],
  );

  const forecastSection =
    timeBase === "replay" ? (
      <section aria-labelledby="forecast-when" className="flex flex-col gap-2">
        <h2 id="forecast-when" className="type-small text-text font-medium">
          The map: {mapLabel}
        </h2>
        <ForecastCycleSelect value={shownRunId} cycles={cycle.cycles} onChange={pickCycle} />
        <details className="type-micro text-text-3">
          <summary className="text-text-2 hover:text-text focus-visible:outline-tide cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2">
            Why +60 min
          </summary>
          <p className="mt-1">{LEAD_DEFAULT_REASON}</p>
        </details>
      </section>
    ) : (
      <section aria-label={TIME_BASE_LABEL.today} className="flex flex-col gap-2">
        <LiveOutlookCard city={city} collapsible={false} />
        <p className="type-micro text-text-2">
          {shownLive
            ? "The map shows today's live forecast, run every 30 minutes from today's rain."
            : "The map stays on the 2 July 2019 replay."}
        </p>
      </section>
    );

  const rail = (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-3">
        <TimeBaseSwitch value={timeBase} onValueChange={setTimeBase} />
        {forecastSection}
      </div>

      <section aria-labelledby="trip" className="flex flex-col gap-3">
        <h2 id="trip" className="font-display text-h3 text-text">
          Can I get there?
        </h2>
        {places.length === 0 ? (
          <p className="type-small text-text-2">
            Places did not load. Reload once the API is reachable.
          </p>
        ) : (
          <>
            <PlaceField
              id="dashboard-from"
              label="From"
              value={fromId}
              onChange={setFromId}
              places={places}
              extra={position ? { value: MY_LOCATION, label: "My location" } : undefined}
            />
            <PlaceField
              id="dashboard-to"
              label="To"
              value={toId}
              onChange={setToId}
              places={places}
              extra={
                picked
                  ? { value: PICKED_ON_MAP, label: "The point I picked on the map" }
                  : undefined
              }
            />
            <div className="flex flex-col gap-1">
              <span className="type-micro text-text-2">Vehicle</span>
              <VehicleSelector value={profile} onValueChange={setProfile} />
            </div>
            <Button className="h-11" onClick={findRoute} disabled={!canPlan}>
              {planning ? "Finding the safe way" : "Find the safe way"}
            </Button>
            {run && stepTime ? (
              <p className="num type-micro text-text-3">Departs {stepTime}, the step on the map.</p>
            ) : (
              <p className="type-micro text-text-3">Available once the forecast loads.</p>
            )}
            {locationState !== "granted" ? (
              <Button variant="outline" className="h-11" onClick={ask}>
                <LocateFixed aria-hidden="true" />
                {locationState === "refused"
                  ? "Location unavailable - choose a place instead"
                  : "Use my location"}
              </Button>
            ) : null}
          </>
        )}
      </section>

      {planError ? (
        <p className="border-line bg-deep rounded-panel text-small text-text-2 border p-3">
          {planError}
        </p>
      ) : plan ? (
        <RouteAnswer
          plan={plan}
          reasons={plan.reasons}
          corridors={plan.corridors}
          selectedCorridorId={corridorId}
          onPickCorridor={setCorridorId}
          profile={profile}
        />
      ) : (
        <EmptyState
          size="sm"
          title="No trip yet"
          description="Choose where you are and where you are going, then find the safe way."
        />
      )}
    </div>
  );

  const reportSections = (
    <>
      <ComplaintsNearYou
        reports={publicReports.reports}
        centre={centre}
        selectedId={selectedReportId}
        onSelect={showReportOnMap}
        loaded={publicReports.loaded}
        error={publicReports.error}
        notes={publicReports.notes}
      />
      <MyReportsPanel items={mine.items} ready={mine.ready} />
    </>
  );

  const at = stepTime ? ` at ${stepTime}` : "";
  const nearbyTitle = centre ? `Streets near you${at}` : `The worst streets in the city${at}`;
  const nearbyEmpty = centre
    ? "No street within 1.5 km of you nears this vehicle's stopping depth."
    : "No street nears this vehicle's stopping depth.";
  const untilLabel = (street: NearbyStreet) =>
    street.passableUntil === null
      ? `Impassable at ${formatIst(run?.validTs[step])}`
      : street.throughEnd
        ? `Passable through ${street.passableUntil}`
        : `Passable until ${street.passableUntil}`;

  /** The sheet's shape: a column, because a phone has height and no width. */
  const streetColumn = (
    <section aria-labelledby="near-you-sheet" className="flex min-w-0 flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="near-you-sheet" className="num type-small text-text font-medium">
          {nearbyTitle}
        </h2>
        <span className="type-micro text-text-3 shrink-0">depth then</span>
      </div>
      {!run ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-3.5 w-3/4" />
          <Skeleton className="h-3.5 w-2/3" />
        </div>
      ) : nearby.length === 0 ? (
        <p className="type-micro text-text-2">{nearbyEmpty}</p>
      ) : (
        <ul className="divide-line rounded-panel border-line divide-y border">
          {nearby.map((street) => (
            <li key={street.id} className="flex items-center justify-between gap-3 px-3 py-2">
              <span className="min-w-0 flex-1">
                <span className="type-small text-text block truncate">{street.name}</span>
                <span className="num type-micro text-text-2 block">{untilLabel(street)}</span>
              </span>
              <span className="num type-small text-text-2 shrink-0">{street.cm.toFixed(0)} cm</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );

  /**
   * The desktop band's shape: one 88 px strip, because the map is the screen and a list eight rows
   * tall underneath it would take half of what the map is for. The strip scrolls sideways and is
   * focusable, so a keyboard reaches the streets past the fold (the defect P10.3 found five times).
   */
  const streetStrip = (
    <section
      aria-labelledby="near-you-band"
      className="flex min-w-0 flex-1 items-center gap-4 overflow-hidden"
    >
      <div className="w-[200px] shrink-0">
        <h2 id="near-you-band" className="num type-small text-text font-medium">
          {nearbyTitle}
        </h2>
        <span className="type-micro text-text-3">depth then, and until when</span>
      </div>
      {!run ? (
        <div className="flex flex-1 gap-2">
          <Skeleton className="h-11 w-40" />
          <Skeleton className="h-11 w-40" />
        </div>
      ) : nearby.length === 0 ? (
        <p className="type-micro text-text-2">{nearbyEmpty}</p>
      ) : (
        <ul
          tabIndex={0}
          aria-labelledby="near-you-band"
          className="focus-visible:border-line-strong flex min-w-0 flex-1 gap-2 overflow-x-auto rounded-md outline-none focus-visible:ring-2 focus-visible:ring-[var(--tide)]"
        >
          {nearby.map((street) => (
            <li
              key={street.id}
              className="border-line rounded-control min-w-0 shrink-0 border px-3 py-1.5"
            >
              <span className="type-small text-text block max-w-[24ch] truncate">
                {street.name}
              </span>
              <span className="num type-micro text-text-2 block">
                {street.cm.toFixed(0)} cm · {untilLabel(street)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );

  /** Over the map: which clock it is on, the step it shows, and the six leads to choose from. */
  const timeStrip = (
    <div
      data-slot="dashboard-time-strip"
      className={
        wide
          ? "border-line bg-ink/90 rounded-panel absolute right-[60px] bottom-8 z-10 flex w-[400px] flex-col gap-1.5 border p-2"
          : "border-line bg-ink/90 rounded-panel absolute right-3 left-3 z-10 flex flex-col gap-1.5 border p-2"
      }
      style={wide ? undefined : { bottom: TIME_STRIP_BOTTOM_PX }}
    >
      <p className="num type-micro text-text-2 px-1" aria-live="polite">
        {shownLive ? "Today" : TIME_BASE_LABEL.replay}
        {stepTime ? `: streets at ${stepTime}` : ""}
      </p>
      <LeadTimeControl
        value={lead}
        onValueChange={changeLead}
        validTs={run?.validTs}
        cycleTs={run?.provenance.cycleTs}
      />
    </div>
  );

  return (
    <main className="flex h-full min-h-0 flex-col">
      <header className="border-line bg-deep shrink-0 border-b px-4 py-2 lg:px-6">
        <h1 className="sr-only">Citizen flood dashboard, Mumbai</h1>
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
          <div className="flex min-w-0 items-center gap-3">
            <Wordmark size="sm" withMark />
            <span className="type-small text-text-2">Mumbai</span>
          </div>
          <div className="flex items-center gap-2">
            <WeatherChip city={city} />
            <ThemeToggle size={wide ? "sm" : "md"} />
          </div>
        </div>
        <p
          className="num type-micro text-text-3 mt-1"
          data-slot="run-line"
          title={run?.provenance.runId ?? undefined}
        >
          {runLine(run, runFailed)}
        </p>
        <ul className="mt-1.5 flex flex-wrap gap-1.5">
          {HONESTY_CHIPS.map((chip) => (
            <li
              key={chip}
              className="border-line text-text-3 type-micro rounded-full border px-2 py-0.5"
            >
              {chip}
            </li>
          ))}
        </ul>
        {wide ? null : <PublicLegend profile={profile} className="mt-2" />}
      </header>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* The pane owns the height; the map is absolute inside it (UI_SPEC 3). */}
        <div
          ref={stageRef}
          data-slot="dashboard-stage"
          data-handover={introDone ? "done" : "playing"}
          className="relative min-h-0 flex-1"
        >
          {/* Held until the registry says which cycle to open on, so the map loads 08:40 once
              rather than loading 09:10 and then swapping. */}
          {cycle.resolved ? (
            <CitizenMap
              city={city}
              runId={cycle.runId}
              profile={profile}
              step={step}
              route={plan}
              corridors={plan?.corridors ?? null}
              selectedCorridorId={corridorId}
              onPickPoint={pickPoint}
              onRunLoaded={onRunLoaded}
              reports={pins}
              selectedReportId={selectedReportId}
              onPickReport={pickReportOnMap}
              focus={reportFocus}
            />
          ) : (
            <div className="bg-ink absolute inset-0" aria-hidden>
              <Skeleton className="size-full rounded-none" />
            </div>
          )}

          {/* Over the map on a desktop, above its attribution credit. On a phone the bottom of
              the map is the sheet, so the legend goes in the header instead - where `/map`
              already puts it - rather than under something the reader has to drag away. */}
          {wide ? (
            <PublicLegend
              profile={profile}
              className="border-line bg-ink/80 rounded-control pointer-events-none absolute bottom-8 left-3 z-10 border px-2.5 py-1.5"
            />
          ) : null}

          {timeStrip}

          {selectedReport ? (
            <SelectedReportCard
              report={selectedReport}
              focusOnOpen={focusCard}
              onClose={() => setSelectedReportId(null)}
              className={
                wide
                  ? "absolute top-3 left-3 z-20 max-h-[calc(100%-120px)] w-[360px] overflow-y-auto"
                  : "absolute top-3 right-3 left-3 z-20 max-h-[calc(100%-240px)] overflow-y-auto"
              }
            />
          ) : null}

          {wide ? null : (
            <>
              <Button
                size="lg"
                // Under the sheet (z-20) and above the time strip, so neither is read through it.
                className="absolute right-4 z-10 h-11"
                style={{ bottom: REPORT_BUTTON_BOTTOM_PX }}
                render={<Link href={REPORT_ROUTE} />}
                nativeButton={false}
              >
                <Umbrella aria-hidden="true" />
                Report water
              </Button>

              <BottomSheet key={sheetKey} containerHeight={sheetHeight} title="Your way there">
                <div className="flex flex-col gap-5">
                  {/* The floating button sits under an opened sheet, so it carries its own. */}
                  <div className="self-start">
                    <ReportWaterButton variant="outline" />
                  </div>
                  {rail}
                  {streetColumn}
                  {reportSections}
                </div>
              </BottomSheet>
            </>
          )}

          {/* The map above is mounted and framed behind the entry, so the cross-fade lands on a
              framed city rather than on an unframed world (UI_SPEC 2).

              **No wrapper.** This used to sit inside an opaque `bg-ink absolute inset-0 z-30` div
              carrying `hidden={introDone}`, and that div broke the motion it was meant to serve in
              two ways. It had to be `hidden`, because otherwise an opaque `--ink` rectangle stays
              over the map for ever once the entry returns null; and being `hidden` the moment
              `onDone` fired meant the 900 ms cross-fade of M27's third act was cut to nothing,
              since `reveal()` runs *before* the fade begins. Together with the hydration defect
              fixed in `globe-entry.tsx` - `onDone` firing at about 1.5 s, before the globe had
              drawn a frame - the entry was `display: none` for its entire four seconds. `GlobeEntry`
              is its own `fixed inset-0 z-50` overlay on `--ink` and removes itself when it is
              finished, so it needs nothing around it. */}
          <DashboardIntro onDone={onIntroDone} />
        </div>

        {wide ? (
          <aside className="border-line bg-deep flex min-h-0 w-[380px] shrink-0 flex-col gap-6 overflow-y-auto border-l p-4">
            {rail}
            {reportSections}
          </aside>
        ) : null}
      </div>

      {wide ? (
        <div className="border-line bg-deep flex h-[88px] shrink-0 items-center justify-between gap-6 border-t px-6">
          {streetStrip}
          <ReportWaterButton variant="solid" />
        </div>
      ) : null}
    </main>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  CityMap,
  type Isochrone,
  type RouteLine,
  type SegmentPath,
} from "@/components/map/city-map";
import { AppShell } from "@/components/varuna/app-shell";
import { EmptyState } from "@/components/varuna/empty-state";
import { CyclePicker } from "@/components/varuna/cycle-picker";
import { PageHeader } from "@/components/varuna/page-header";
import { Panel } from "@/components/varuna/panel";
import { PanelErrorBoundary } from "@/components/varuna/panel-error-boundary";
import { RouteCompare, type RouteSummary } from "@/components/varuna/route-compare";
import {
  RouteForm,
  defaultRouteRequest,
  type RoutePlace,
  type RouteRequest,
} from "@/components/varuna/route-form";
import { apiUrl } from "@/lib/api/client";
import { allSegments } from "@/lib/api/run-depth";
import { apiProfile, loadPlaces, planRoute, type Place, type RoutePlan } from "@/lib/api/route";
import { useReplayStore } from "@/lib/stores/replay";
import { navItem } from "@/lib/nav";
import { cityBounds } from "@/components/map/basemap";
import { extentFrame, pathsFrame } from "@/lib/map/affected-bounds";

/**
 * How long the form must hold still before a changed trip is routed again.
 *
 * Long enough that dragging the tolerance slider across its range asks once rather than twenty
 * times, short enough that picking a new profile or departure time reads as immediate.
 */
export const REROUTE_DEBOUNCE_MS = 200;

/**
 * The route planner (SPEC.md 7.4, task P8.5).
 *
 * The screen's one job is a comparison. A safe route on its own is unfalsifiable - of course the
 * system says its own route is fine. Drawn against the route a navigation app would give you
 * today, with the streets it walks into named and their predicted depth beside them, a dispatcher
 * can see for themselves what the detour bought.
 *
 * Places come from the city's own registers (`/v1/route/facilities` and the chronic hotspot
 * layer), never from coordinates typed into the front end: each one carries a `source_url` there.
 */
export function RouteScreen() {
  const simTime = useReplayStore((s) => s.simTime);
  const [request, setRequest] = useState<RouteRequest>(() => defaultRouteRequest(simTime));
  const [places, setPlaces] = useState<Place[]>([]);
  const [streets, setStreets] = useState<SegmentPath[]>([]);
  const [plan, setPlan] = useState<RoutePlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  // How many times "Find route" has been pressed. Zero means nobody has asked for a route yet,
  // so editing the form routes nothing; once somebody has, every change to the trip - departure
  // time, profile, tolerance, either end, the cycle - asks again (SPEC.md 7.4 AC3). The count
  // rather than a flag, so pressing the button on an unchanged trip still re-asks.
  const [asked, setAsked] = useState(0);
  // The run the trip is costed against; undefined means the newest for this city.
  const [runId, setRunId] = useState<string | undefined>(undefined);
  // An alert's "Plan a route around it" sends `?to=<lon>,<lat>&place=<street>`. The planner only
  // routes between registered places (each with its source), so it picks the nearest registered
  // place within ARRIVAL_SNAP_M and says which, rather than routing to a typed coordinate.
  const [arrivalNote, setArrivalNote] = useState<string | null>(null);

  // A different cycle is a different answer, so the last one stops being shown with it.
  const pickCycle = useCallback((next: string) => {
    setRunId(next);
    setPlan(null);
    setError(null);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    loadPlaces("mumbai", controller.signal)
      .then((loaded) => {
        setPlaces(loaded);
        // Preselect the demo trip: KEM Hospital to Sion Hospital (SPEC.md 3.3, 15).
        const kem = loaded.find((p) => p.name.includes("(KEM)"));
        const sion = loaded.find((p) => p.name.includes("(LTMG)"));
        const arrival = arrivalFromSearch(window.location.search, loaded);
        if (arrival) setArrivalNote(arrival.note);
        const destinationId = arrival?.place?.id ?? sion?.id;
        if (kem && destinationId) {
          setRequest((current) => ({ ...current, originId: kem.id, destinationId }));
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  // The street network, for context under the route. Without it a route is two lines in the dark.
  useEffect(() => {
    const controller = new AbortController();
    fetch(apiUrl("/v1/city/mumbai/layers/segments"), { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : { features: [] }))
      .then((geojson) => setStreets(allSegments(geojson)))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  // The demo's two hospitals first, then the chronic junctions, then the rest of the register.
  // Mumbai has 354 hospitals in the AOI and the ambulance trip is between two named ones; a picker
  // that makes you scroll past 300 clinics to find KEM is a picker nobody uses on stage.
  const options: RoutePlace[] = useMemo(() => {
    const demo = new Set(
      places.filter((p) => p.name.includes("(KEM)") || p.name.includes("(LTMG)")).map((p) => p.id),
    );
    const group = (p: Place): string => {
      if (demo.has(p.id)) return "Demo trip";
      if (p.kind === "hotspot") return "Chronic junctions";
      return p.kind === "fire_station" ? "Fire stations" : "Hospitals";
    };
    return places.map((p) => ({ id: p.id, name: p.name, group: group(p) }));
  }, [places]);

  const run = useCallback(
    async (next: RouteRequest, signal: AbortSignal) => {
      const origin = places.find((p) => p.id === next.originId);
      const destination = places.find((p) => p.id === next.destinationId);
      if (!origin || !destination) {
        setError("Pick an origin and a destination from the city's registers.");
        setRunning(false);
        return;
      }
      setRunning(true);
      setError(null);
      try {
        const answer = await planRoute(
          {
            origin,
            destination,
            departAt: next.departAt,
            profile: apiProfile(next.profile),
            riskTolerance: next.riskTolerance,
            runId,
          },
          signal,
        );
        // A newer question superseded this one while it was in flight: its answer is not shown.
        if (!signal.aborted) setPlan(answer);
      } catch (failure) {
        if (signal.aborted) return;
        setError(failure instanceof Error ? failure.message : String(failure));
        setPlan(null);
      } finally {
        if (!signal.aborted) setRunning(false);
      }
    },
    [places, runId],
  );

  // Route, and route again whenever the trip changes once somebody has asked for one. The
  // previous request is aborted rather than raced, so a slow answer for 08:40 can never land on
  // top of the answer for 10:40 the operator has since asked for.
  useEffect(() => {
    if (asked === 0) return;
    const controller = new AbortController();
    const timer = setTimeout(() => void run(request, controller.signal), REROUTE_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [asked, request, run]);

  // Motion M14: the naive route in dashed grey, the VARUNA route over it in `--tide`. Both are
  // handed to the map together; deck draws them in the order section 6.7 sets.
  const routes: RouteLine[] = useMemo(() => {
    if (!plan) return [];
    const lines: RouteLine[] = [];
    // Avoided streets first, so the route and its casing draw over them rather than under.
    plan.avoided.forEach((a, i) => {
      if (a.path.length >= 2) lines.push({ id: `avoided-${i}`, path: a.path, kind: "avoided" });
    });
    if (plan.naive) lines.push({ id: "naive", path: plan.naive.path, kind: "naive" });
    plan.alternates.forEach((alt, i) =>
      lines.push({ id: `alt-${i}`, path: alt.path, kind: "alternate" }),
    );
    if (plan.varuna) lines.push({ id: "varuna", path: plan.varuna.path, kind: "varuna" });
    return lines;
  }, [plan]);

  const noIsochrones: Isochrone[] = useMemo(() => [], []);

  // Where the map opens: on the trip, never on the whole city. With an answer, on every line it
  // drew - both routes, the alternates and the streets avoided - so the detour and what it went
  // around are in view together; before one, on the corridor between the trip's two ends (KEM
  // Hospital to Sion Hospital by default). Choosing a different end re-arms the fit, because that
  // is a different trip; a new departure time or profile re-frames only a camera nobody moved.
  const routeFrame = useMemo(() => {
    const within = cityBounds("mumbai");
    // A route is a thin thing in a wide panel, so it gets more air than a flood does.
    if (routes.length > 0) {
      return pathsFrame(
        routes.map((line) => line.path),
        { within, padFraction: 0.2 },
      );
    }
    const ends = places.filter((p) => p.id === request.originId || p.id === request.destinationId);
    return extentFrame(ends, { within });
  }, [routes, places, request.originId, request.destinationId]);
  const tripKey = `${request.originId}>${request.destinationId}`;

  const naive: RouteSummary | null = plan?.naive
    ? {
        etaMin: plan.naive.minutes,
        distanceM: plan.naive.distanceM,
        maxDepthCm: plan.naive.maxDepthCm,
        safeUntil: plan.naive.safeUntil ?? undefined,
        // The naive route's own hazard: the streets it walks into. Listing them under the naive
        // column rather than the VARUNA one is the point - this is what the shortest path costs.
        avoided: plan.avoided.map((a) => ({
          segmentId: a.segmentId,
          name: a.name,
          probability: a.probability,
          atTs: a.at,
        })),
      }
    : null;

  const varuna: RouteSummary | null = plan?.varuna
    ? {
        etaMin: plan.varuna.minutes,
        distanceM: plan.varuna.distanceM,
        maxDepthCm: plan.varuna.maxDepthCm,
        safeUntil: plan.varuna.safeUntil ?? undefined,
        avoided: plan.avoided.map((a) => ({
          segmentId: a.segmentId,
          name: a.name,
          probability: a.probability,
          atTs: a.at,
        })),
        alternates: plan.alternates.map((alt, i) => ({
          id: `alt-${i}`,
          label: `Alternate ${i + 1}`,
          etaMin: alt.minutes,
          maxDepthCm: alt.maxDepthCm,
        })),
      }
    : null;

  return (
    <AppShell>
      <div className="flex h-full min-h-0 flex-col overflow-hidden">
        <div className="mx-auto flex min-h-0 w-full max-w-[1600px] flex-1 flex-col gap-4 p-6">
          <PageHeader
            title={navItem("route").label}
            screen={navItem("route")}
            description="Prediction turned into an ambulance route."
          />

          {/* The cycle the route is costed against. Without it the planner always answered for the
              newest run - 09:10 IST, after the storm - where an ambulance's corridor is dry and
              the comparison is two identical columns. The cycle is the question. */}
          <CyclePicker currentRunId={plan?.runId ?? runId} onPick={pickCycle} />

          <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[30fr_70fr]">
            <PanelErrorBoundary>
              <div className="flex min-h-0 flex-col gap-3 overflow-y-auto pr-1">
                <Panel
                  title="Trip"
                  description="KEM Hospital to Sion Hospital at the replay clock."
                >
                  <RouteForm
                    value={request}
                    onChange={setRequest}
                    onSubmit={(next) => {
                      setRequest(next);
                      setAsked((count) => count + 1);
                    }}
                    places={options.length > 0 ? options : undefined}
                    disabled={places.length === 0}
                    submitDisabledReason="Loading the city's hospitals and chronic junctions"
                  />
                  {running ? (
                    <p className="type-small text-text-3 mt-3" role="status">
                      Routing...
                    </p>
                  ) : null}
                  {asked > 0 ? (
                    <p className="type-micro text-text-3 mt-2">
                      Changing the departure time, the profile or the tolerance routes again.
                    </p>
                  ) : null}
                  {arrivalNote ? (
                    <p className="type-micro text-text-2 mt-3" role="status">
                      {arrivalNote}
                    </p>
                  ) : null}
                  {error ? <p className="type-small text-text-2 mt-3">{error}</p> : null}
                  {plan ? (
                    <p className="type-micro text-text-3 mt-3">
                      Routed on run <span className="num">{plan.runId}</span> in{" "}
                      <span className="num">{Math.round(plan.ms)}</span> ms.
                    </p>
                  ) : null}
                  {plan?.notes.map((note) => (
                    <p key={note} className="type-micro text-text-3 mt-2">
                      {note}
                    </p>
                  ))}
                </Panel>
              </div>
            </PanelErrorBoundary>

            {/*
             * The right column scrolls. It used to be a plain flex column inside a page that is
             * `overflow-hidden`, so once a route came back with an avoided list and two alternates
             * the comparison grew past the viewport and there was no way to reach it - the reported
             * "I can't even scroll down".
             *
             * The map fills the pane rather than a band of it (UI_SPEC 8, task D-18). It used to
             * be capped at `clamp(20rem, 52vh, 40rem)`, which left the city drawn into about half
             * the column on a 1440 x 900 screen with the comparison below it and nothing in
             * between. `h-full` against the column's own height gives the map the pane; the
             * comparison then sits below the fold and the column scrolls to it, which is the
             * behaviour this column was given in the first place. `shrink-0` keeps the panel below
             * from squeezing it, and the `min-h` keeps it readable on a short screen.
             */}
            <div
              // Focusable, because until the city and a route have loaded it scrolls and holds
              // nothing to tab to - the map is an empty state and the comparison has no buttons
              // yet - so a keyboard user could not reach the bottom of it (WCAG 2.1.1). A clean
              // clone without city/mumbai is exactly that state, which is where axe caught it.
              tabIndex={0}
              role="region"
              aria-label="Route map and comparison"
              className="focus-visible:ring-tide/50 flex min-h-0 min-w-0 flex-col gap-4 overflow-y-auto pr-1 focus-visible:ring-3 focus-visible:outline-none"
            >
              <PanelErrorBoundary>
                <section
                  aria-label="Route map"
                  className="rounded-panel border-line bg-deep relative h-full min-h-[20rem] shrink-0 overflow-hidden border"
                >
                  {streets.length > 0 ? (
                    <CityMap
                      frames={[]}
                      rasterBounds={null}
                      baseSegments={streets}
                      segments={[]}
                      surcharge={[]}
                      hotspots={[]}
                      routes={routes}
                      isochrones={noIsochrones}
                      showRaster={false}
                      showSegments={false}
                      showSurcharge={false}
                      showBuildings={false}
                      showHotspots={false}
                      step={0}
                      fitBounds={routeFrame}
                      fitKey={tripKey}
                    />
                  ) : (
                    <EmptyState
                      title="Loading the city"
                      description="The street network arrives from the city VARUNA built; the route draws on it."
                    />
                  )}
                </section>
              </PanelErrorBoundary>

              <PanelErrorBoundary>
                <RouteCompare naive={naive} varuna={varuna} />
              </PanelErrorBoundary>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

/** How close a registered place must be to an alert's street to stand in for it. */
const ARRIVAL_SNAP_M = 300;

/**
 * The destination an alert's link asks for, as a registered place near it, with the sentence that
 * says what happened. Null when the link carries no destination.
 */
export function arrivalFromSearch(
  search: string,
  places: readonly Place[],
): { place: Place | null; note: string } | null {
  const params = new URLSearchParams(search);
  const to = params.get("to");
  if (!to) return null;
  const [lon, lat] = to.split(",").map(Number);
  const street = params.get("place") ?? "the alert's street";
  if (lon === undefined || lat === undefined || !Number.isFinite(lon) || !Number.isFinite(lat)) {
    return null;
  }
  let best: Place | null = null;
  let bestM = Infinity;
  for (const place of places) {
    const dx = (place.lon - lon) * 111_320 * Math.cos((lat * Math.PI) / 180);
    const dy = (place.lat - lat) * 110_540;
    const d = Math.hypot(dx, dy);
    if (d < bestM) {
      best = place;
      bestM = d;
    }
  }
  if (best && bestM <= ARRIVAL_SNAP_M) {
    return {
      place: best,
      note: `Destination set to ${best.name}, the registered place ${Math.round(bestM)} m from ${street}.`,
    };
  }
  return {
    place: null,
    note: `${street} is not near a registered place, so the trip stays KEM Hospital to Sion Hospital. Pick the destination the alert affects.`,
  };
}

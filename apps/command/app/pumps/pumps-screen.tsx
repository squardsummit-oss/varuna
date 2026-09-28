"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { ArrivalTimeline } from "@/components/pumps/arrival-timeline";
import { DispatchClock } from "@/components/pumps/dispatch-clock";
import { dispatchOrder } from "@/components/pumps/model";
import { PlaceGauges, type GaugeClock } from "@/components/pumps/place-gauges";
import { benefitCaveat, inferModel, PumpHeadline } from "@/components/pumps/pump-headline";
import { Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { AppShell } from "@/components/varuna/app-shell";
import { CyclePicker } from "@/components/varuna/cycle-picker";
import { DispatchOrder } from "@/components/varuna/dispatch-order";
import { PageHeader } from "@/components/varuna/page-header";
import { Panel } from "@/components/varuna/panel";
import { PhoneMock, type PhoneMessage } from "@/components/varuna/phone-mock";
import { SkeletonRows } from "@/components/varuna/skeleton";
import { PumpBoard } from "@/components/varuna/pump-board";
import { describeRefusal, dispatchPumps, opsRefusal, readPassphrase } from "@/lib/api/ops";
import { errorMessage } from "@/lib/api/client";
import {
  loadPumpCycles,
  loadPumpMap,
  loadPumpPlan,
  pricePumpPlacements,
  type PricedPlan,
  type PumpCycles,
  type PumpMap,
  type PumpPlan,
} from "@/lib/api/pumps";
import { useMotionPref } from "@/lib/motion";
import { navItem } from "@/lib/nav";
import { cn } from "@/lib/utils";

import { boardPlacements, buildPumpBoard, placementsKey } from "./pump-board-state";

/** WebGL and deck.gl load only when the map view is shown, never in the first bundle. */
const DispatchMap = dynamic(
  () => import("@/components/pumps/dispatch-map").then((m) => m.DispatchMap),
  { ssr: false },
);

/**
 * How long the screen waits for the map to start a dispatch before starting it itself. The map
 * starts the clock on its first frame so a slow WebGL start loses nothing; if the map never draws
 * (no WebGL), the gauges and the timeline must not wait at "no pump" for ever.
 */
const CLOCK_FALLBACK_MS = 1500;

/**
 * The map and its rail share one height, sized so the whole map is on screen without scrolling
 * at 1366 x 768 and 1440 x 900, and so it does not grow into a strip on a 4K wall. Above it sit
 * the 52 px top bar, the title block with its Sanskrit gloss, the cycle row and the line saying
 * which cycle the screen opened on: 305 px measured at 1440 x 900 on 2026-09-28, plus the page's
 * 24 px bottom padding. At 17rem the map ran 33 px past the fold.
 */
const MAP_HEIGHT = "h-[clamp(24rem,calc(100dvh-21rem),46rem)]";

/** What the gauges and the headline time from before Optimise: nothing has been sent. */
const IDLE_GAUGE: GaugeClock = { key: "idle", startMs: null, idle: true };

/** What a failed or missing dispatch map leaves standing, after the reason. */
const PLAN_STANDS = "The plan's own figures still stand.";

/** A settled answer with no map in it: said as what happened, never as "loading" for ever. */
const MAP_MISSING = `The API returned no dispatch map for this cycle, so there are no roads or depth series to draw. ${PLAN_STANDS}`;

/**
 * Which cycle the screen opened on, and why: the address named one, or the cycle whose plan
 * avoids the most minutes above 45 cm, or - when no cycle list could be read - the newest.
 */
interface Opening {
  runId?: string;
  why: "pinned" | "busiest" | "newest";
}

/** "08:40" from a cycle time with its offset, as the IST clock reads it. */
function cycleClock(cycleTs: string | null): string | null {
  const match = cycleTs ? /T(\d{2}):(\d{2})/.exec(cycleTs) : null;
  return match ? `${match[1]}:${match[2]}` : null;
}

/** The two ways to read the plan: the fleet on the city, or the kanban an operator rearranges. */
type View = "map" | "board";

const VIEWS: { id: View; label: string }[] = [
  { id: "map", label: "Dispatch map" },
  { id: "board", label: "Plan board" },
];

/** The tab holds no passphrase: nothing is recorded, and the screen says where it is entered. */
const NO_PASSPHRASE =
  "This tab holds no desk passphrase. Enter it on the authority desk, then come back.";

/** What a dispatch put on the ward officer's phone, and the sentence each alert now carries. */
interface Dispatched {
  messages: PhoneMessage[];
  instructions: string[];
  notes: string[];
}

/**
 * Pump dispatch board (SPEC.md section 7.6, tasks P8.9 and P8.10).
 *
 * **The board opens with every pump in the pool.** Optimise places the plan the optimiser
 * computed when the cycle ran: the cards fly to their hotspots and each column's minutes above
 * 45 cm roll from the no-pump figure to the plan's (motion M17).
 *
 * **A drag is priced, not left stale** (7.6 AC2). Moving a pump sends the board as it now stands
 * to `POST /v1/pumps/price`, which runs the same model and arithmetic as the optimiser - the
 * Flash-lite emulator with the pump's outflow when the run carries its storm - and the columns
 * roll to that answer. Until it arrives the figures stay where they were and the screen says the
 * board is being priced; a drag never moves a number by itself.
 *
 * **Dispatch** (7.6 AC4) goes through the desk's gated client: the order is recorded in the ops
 * log, every alert about a dispatched place carries "Pump P-05 dispatched." from then on, and the
 * ward officer's phone mock shows the message. No lorry moves - the inventory is synthetic, and
 * the header says so.
 */
export function PumpsScreen() {
  const [view, setView] = useState<View>("map");
  const [plan, setPlan] = useState<PumpPlan | null>(null);
  const [planLoaded, setPlanLoaded] = useState(false);
  // The API's own sentence when it refused the plan; a missing plan is not a refusal.
  const [planError, setPlanError] = useState<string | null>(null);
  // The dispatch map, keyed by the run it describes so a slow answer for an old cycle is dropped.
  const [pumpMap, setPumpMap] = useState<{ runId: string; map: PumpMap | null } | null>(null);
  const [mapError, setMapError] = useState<{ runId: string; message: string } | null>(null);
  // The pump a row was pressed for, and the one under the pointer or focus. The map lights the
  // hovered one while there is one, else the pressed one; only a press sets aria-pressed.
  const [selectedPumpId, setSelectedPumpId] = useState<string | null>(null);
  const [hoveredPumpId, setHoveredPumpId] = useState<string | null>(null);
  // The cycle the operator picked; until they pick, the one the screen opened on.
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const [opening, setOpening] = useState<Opening | null>(null);
  const [applied, setApplied] = useState(false);
  const [moved, setMoved] = useState<Record<string, string | null>>({});
  // The last price the API returned, with the board it priced; drawn only while that board is
  // still the one on screen.
  const [price, setPrice] = useState<{ key: string; plan: PricedPlan } | null>(null);
  const [priceError, setPriceError] = useState<{ key: string; message: string } | null>(null);
  const [dispatched, setDispatched] = useState<Dispatched | null>(null);
  const [dispatching, setDispatching] = useState(false);
  // Which cycles send pumps, asked for only when the one on screen sends none.
  const [cycles, setCycles] = useState<PumpCycles | null>(null);
  // Replays of the dispatch (M33-M35): Optimise, "Play dispatch" and a successful dispatch each
  // add one. It only ever grows - never reset per cycle - so a dispatch key is never reused and a
  // round trip A, B, A cannot hand the gauges an old start time (M34 in step with M33).
  const [replays, setReplays] = useState(0);
  const [startedAt, setStartedAt] = useState<{ key: string; ms: number } | null>(null);
  const { reduced } = useMotionPref();

  // Open on the cycle the plan is worth reading at. With no pick, `/v1/pumps` answers the newest
  // run - on the 2 July replay the 09:10 cycle, which sends one pump - so the screen asks which
  // cycle sends the most first, unless the address names one (`?run=`).
  useEffect(() => {
    const controller = new AbortController();
    const pinned = new URLSearchParams(window.location.search).get("run");
    Promise.resolve()
      .then(() => (pinned ? null : loadPumpCycles(controller.signal)))
      .then((found) => {
        if (controller.signal.aborted) return;
        if (pinned) {
          setOpening({ runId: pinned, why: "pinned" });
          return;
        }
        if (found) setCycles(found);
        setOpening(
          found?.busiestRunId ? { runId: found.busiestRunId, why: "busiest" } : { why: "newest" },
        );
      })
      .catch(() => {
        // No cycle list: open on the newest run, as the API picks it.
        if (!controller.signal.aborted) setOpening({ why: "newest" });
      });
    return () => controller.abort();
  }, []);

  const requestedRunId = runId ?? opening?.runId;
  const openingDecided = opening !== null || runId !== undefined;
  useEffect(() => {
    if (!openingDecided) return;
    const controller = new AbortController();
    loadPumpPlan(requestedRunId, controller.signal)
      .then((next) => {
        setPlan(next);
        setPlanError(null);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setPlan(null);
        setPlanError(errorMessage(error, "The pump plan could not be loaded."));
      })
      .finally(() => {
        if (!controller.signal.aborted) setPlanLoaded(true);
      });
    return () => controller.abort();
  }, [requestedRunId, openingDecided]);

  // The map for the plan on screen, asked for once the plan names its run. The API remembers the
  // answer, so only the first visit to a cycle waits for its roads.
  const planRunId = plan?.runId;
  useEffect(() => {
    if (!planRunId) return;
    const controller = new AbortController();
    loadPumpMap(planRunId, controller.signal)
      .then((map: PumpMap | null) => {
        setPumpMap({ runId: planRunId, map });
        setMapError((e) => (e?.runId === planRunId ? null : e));
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        // The API's own sentence when it refused, then what that leaves standing.
        setMapError({
          runId: planRunId,
          message: `${errorMessage(error, "The dispatch map could not be loaded.")} ${PLAN_STANDS}`,
        });
      });
    return () => controller.abort();
  }, [planRunId]);

  // Three settled answers and one pending: a map, the API's refusal, an answer with no map in it
  // (said as such), or still waiting. Only the last may read as loading.
  const mapSettled = pumpMap !== null && pumpMap.runId === planRunId;
  const shownMap = mapSettled ? pumpMap.map : null;
  const shownMapError =
    (mapError && mapError.runId === planRunId ? mapError.message : null) ??
    (mapSettled && pumpMap.map === null ? MAP_MISSING : null);
  const mapLoading = Boolean(planRunId) && !mapSettled && !shownMapError;

  // The dispatch on screen: one clock per map per Optimise or replay. Before Optimise the clock
  // is idle - the lorries wait at their depots and every place shows its no-pump depth. After it,
  // the map starts the clock on its first frame; the gauges (M34), the headline and the timeline
  // (M35) time from the instant it reports.
  const hasLegs = Boolean(shownMap && shownMap.legs.length > 0);
  const shownRunId = shownMap?.runId ?? null;
  const playKey = hasLegs && applied ? `${shownRunId}:${replays}` : null;
  const clock = useMemo(() => {
    if (!hasLegs) return null;
    if (!playKey) return new DispatchClock({ idle: true });
    return new DispatchClock({ reduced, onStart: (ms) => setStartedAt({ key: playKey, ms }) });
  }, [hasLegs, playKey, reduced]);
  useEffect(() => {
    if (!clock || clock.idle || view !== "map") return;
    const timer = window.setTimeout(() => clock.start(), CLOCK_FALLBACK_MS);
    return () => window.clearTimeout(timer);
  }, [clock, view]);
  const gaugeClock = useMemo<GaugeClock | null>(() => {
    if (!applied) return IDLE_GAUGE;
    return playKey
      ? { key: playKey, startMs: startedAt?.key === playKey ? startedAt.ms : null }
      : null;
  }, [applied, playKey, startedAt]);
  const lorryOrder = useMemo(() => dispatchOrder(shownMap), [shownMap]);
  const replay = useCallback(() => setReplays((n) => n + 1), []);
  // Optimise places the optimiser's plan - on the board, and on the map, where it sends the fleet
  // (section 8: M33 and M35 trigger on Optimise). A hand-moved board is put back first.
  const optimise = useCallback(() => {
    setApplied(true);
    setMoved({});
    setReplays((n) => n + 1);
  }, []);

  // A cycle whose plan sends nothing: find the cycles that do, so the screen can offer one.
  const sendsNothing = planLoaded && !planError && (plan === null || plan.assignments.length === 0);
  useEffect(() => {
    if (!sendsNothing || cycles) return;
    const controller = new AbortController();
    loadPumpCycles(controller.signal)
      .then(setCycles)
      .catch(() => {
        // The empty state still says this cycle sends nothing; it just cannot name another.
      });
    return () => controller.abort();
  }, [sendsNothing, cycles]);

  // Picking a cycle is picking a different plan, so the operator's overrides go with it.
  // `replays` is deliberately not reset: see its declaration.
  const pickCycle = useCallback((next: string) => {
    setRunId(next);
    setApplied(false);
    setMoved({});
    setDispatched(null);
    setSelectedPumpId(null);
    setHoveredPumpId(null);
    setStartedAt(null);
  }, []);

  const movedCount = Object.keys(moved).length;
  const placements = useMemo(
    () => boardPlacements(plan, { applied, moved }),
    [plan, applied, moved],
  );
  const key = placementsKey(placements);

  // Re-price whenever the operator has changed the board by hand. The answer is kept with the
  // placements it was asked about, so a slow answer for an older board is never drawn.
  useEffect(() => {
    if (!plan || movedCount === 0) return;
    const controller = new AbortController();
    pricePumpPlacements({ runId: plan.runId, placements }, controller.signal)
      .then((priced) => setPrice({ key, plan: priced }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setPriceError({
          key,
          message:
            error instanceof Error
              ? `The board could not be priced: ${error.message}`
              : "The board could not be priced.",
        });
      });
    return () => controller.abort();
  }, [plan, movedCount, placements, key]);

  const priced = movedCount > 0 && price?.key === key ? price.plan : null;
  const failed = movedCount > 0 && priceError?.key === key ? priceError.message : null;
  const pricing = movedCount > 0 && !priced && !failed;

  const board = buildPumpBoard(plan, { applied, moved, priced });
  const { available, order } = board;
  // Each column's forecast depth, from the dispatch map's legs. The with-plan series belongs to
  // the optimiser's plan, so a board moved by hand shows only the no-pump one.
  const columns = useMemo(() => {
    const series = new Map(
      (shownMap?.legs ?? []).map((leg) => [
        leg.target.id,
        { before: leg.depthBeforeCm ?? [], after: movedCount > 0 ? null : leg.depthAfterCm },
      ]),
    );
    return board.columns.map((c) => ({ ...c, depthCm: series.get(c.id) ?? null }));
  }, [board.columns, shownMap, movedCount]);
  const hasPlan = Boolean(plan?.assignments.length);
  const canOptimise = hasPlan && (!applied || movedCount > 0);
  // The API dispatches what the optimiser assigns, not a board arranged by hand: a hand-made
  // plan is priced here, and put back with Optimise before it is sent.
  const canDispatch = Boolean(order) && applied && movedCount === 0 && !dispatching;

  const dispatch = useCallback(async () => {
    if (!plan) return;
    if (!readPassphrase()) {
      toast("Dispatched nothing", { description: NO_PASSPHRASE });
      return;
    }
    setDispatching(true);
    try {
      const done = await dispatchPumps({ runId: plan.runId, user: "control room" });
      setDispatched({
        messages: done.phoneMessages.map((m, i) => ({
          // No time on the bubble: the order was given now, on the wall clock, and the phone's
          // status bar reads the replay's 2019 clock - two clocks on one card read as one.
          id: m.alertId ?? `${m.hotspotId}-${i}`,
          text: m.text,
        })),
        instructions: done.alertInstructions,
        notes: done.notes,
      });
      toast("Pumps dispatched", {
        description: `${done.orders.length} orders recorded in the ops log. The inventory is synthetic, so no lorry moves.`,
      });
      // The fleet leaves again on the map, as the order went out (M33, trigger "dispatch").
      setReplays((n) => n + 1);
    } catch (error) {
      toast("Not dispatched", { description: describeRefusal(opsRefusal(error)) });
    } finally {
      setDispatching(false);
    }
  }, [plan]);

  const nothingSent = describeNothingSent(plan, cycles);
  const offer = nothingSent.offer;
  const openedOn =
    plan && runId === undefined && opening?.why === "busiest" && plan.runId === opening.runId
      ? describeOpening(plan, cycles)
      : null;
  const highlightPumpId = hoveredPumpId ?? selectedPumpId;
  // What a board column says while its depth series is not there to draw.
  const depthNote = mapLoading
    ? "Loading this place's depth series"
    : shownMapError
      ? "Depth series not loaded; the dispatch map says why"
      : null;

  const total = priced ? priced.totalMinutesSaved : plan?.totalMinutesSaved;
  const label = priced ? priced.benefitLabel : plan?.benefitLabel;

  return (
    <AppShell>
      <div className="h-full min-h-0 overflow-y-auto">
        <div className="flex flex-col gap-4 p-6">
          <PageHeader
            title={navItem("pumps").label}
            screen={navItem("pumps")}
            description="Where each dewatering lorry goes before the water rises, by which road, and how much water it takes away."
            honesty="Synthetic pump inventory"
          />

          <div className="flex flex-wrap items-center justify-between gap-3">
            <CyclePicker currentRunId={plan?.runId ?? requestedRunId} onPick={pickCycle} />
            <div
              role="tablist"
              aria-label="How to read the plan"
              className="rounded-control border-line bg-deep flex w-fit gap-1 border p-1"
            >
              {VIEWS.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  role="tab"
                  id={`pumps-tab-${v.id}`}
                  aria-selected={view === v.id}
                  // Only the shown panel is in the DOM, so only its tab may point at it.
                  aria-controls={view === v.id ? `pumps-panel-${v.id}` : undefined}
                  onClick={() => setView(v.id)}
                  className={cn(
                    "rounded-control type-small min-h-8 px-3 transition-colors duration-150",
                    "focus-visible:ring-tide/60 focus-visible:ring-2 focus-visible:outline-none",
                    view === v.id ? "bg-well text-text" : "text-text-2 hover:text-text",
                  )}
                >
                  {v.label}
                </button>
              ))}
            </div>
          </div>
          {openedOn ? (
            <p className="type-micro text-text-3 -mt-2 max-w-[72ch]" role="note">
              {openedOn}
            </p>
          ) : null}

          {view === "map" ? (
            <div
              role="tabpanel"
              id="pumps-panel-map"
              aria-labelledby="pumps-tab-map"
              className="flex flex-col gap-4"
            >
              {planError ? (
                <DispatchMap
                  map={null}
                  error={planError}
                  errorTitle="The pump plan did not load"
                  className="h-[24rem]"
                />
              ) : sendsNothing ? (
                <DispatchMap
                  map={null}
                  emptyTitle={nothingSent.title}
                  emptyDescription={nothingSent.description}
                  emptyAction={
                    offer ? (
                      <Button onClick={() => pickCycle(offer.runId)}>
                        Open the {offer.clock} plan
                      </Button>
                    ) : undefined
                  }
                  className="h-[24rem]"
                />
              ) : (
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(19rem,23rem)]">
                  <div className="relative min-w-0">
                    <DispatchMap
                      map={shownMap}
                      loading={mapLoading}
                      error={shownMapError}
                      selectedPumpId={highlightPumpId}
                      clock={clock}
                      className={MAP_HEIGHT}
                    />
                    {hasPlan ? (
                      <MapActions
                        nPumps={plan?.assignments.length ?? 0}
                        canOptimise={canOptimise}
                        onOptimise={optimise}
                        onReplay={hasLegs ? replay : undefined}
                        onDispatch={() => void dispatch()}
                        dispatching={dispatching}
                        movedCount={movedCount}
                        reduced={reduced}
                      />
                    ) : null}
                  </div>
                  <aside
                    aria-label="What the plan buys and where each pump goes"
                    className={cn(
                      "rounded-panel border-line bg-deep flex min-h-0 flex-col overflow-hidden border",
                      MAP_HEIGHT,
                    )}
                  >
                    <PumpHeadline
                      plan={plan}
                      loading={!planLoaded}
                      order={lorryOrder}
                      clock={shownMap ? gaugeClock : applied ? null : IDLE_GAUGE}
                      reduced={reduced}
                      className="border-line border-b p-4"
                    />
                    <div className="flex flex-col gap-0.5 px-4 pt-3 pb-1">
                      <h2 className="type-small text-text font-medium">Where each pump goes</h2>
                      <p className="type-micro text-text-3">
                        Pale column: the peak with no pump. Solid: with it. The line is{" "}
                        {plan?.thresholdCm ?? 45} cm.
                      </p>
                    </div>
                    {shownMap ? (
                      <PlaceGauges
                        legs={shownMap.legs}
                        thresholdCm={shownMap.thresholdCm}
                        order={lorryOrder}
                        clock={gaugeClock}
                        reduced={reduced}
                        selectedPumpId={selectedPumpId}
                        onSelect={setSelectedPumpId}
                        onHover={setHoveredPumpId}
                        className="min-h-0 flex-1 overflow-y-auto px-2 pb-2"
                      />
                    ) : shownMapError ? (
                      <p className="type-small text-text-2 px-4">{shownMapError}</p>
                    ) : (
                      <SkeletonRows rows={5} className="px-4" />
                    )}
                  </aside>
                </div>
              )}

              {plan && !sendsNothing && !planError ? (
                <div className="type-micro text-text-3 flex max-w-[72ch] flex-col gap-1">
                  <p>
                    {benefitCaveat(
                      shownMap?.benefitModel ?? inferModel(plan),
                      shownMap?.emulator ?? null,
                    )}
                  </p>
                  <p>
                    Dispatch pumps sends the optimiser&rsquo;s plan shown here to the ops log, the
                    alerts and the ward officer&rsquo;s phone. The inventory is synthetic, so no
                    lorry moves. To rearrange it by hand, open the plan board.
                  </p>
                </div>
              ) : null}

              {sendsNothing || planError ? null : (
                <Panel
                  title="Does each pump get there in time"
                  description="Each cell is one five-minute forecast step, starting at the time the forecast gives for it. A pump that arrives before the pale cells begin meets the water; one that arrives after chases it."
                >
                  {shownMap ? (
                    <ArrivalTimeline
                      legs={shownMap.legs}
                      cycleTs={shownMap.cycleTs}
                      stepMin={shownMap.stepMin}
                      nSteps={shownMap.nSteps}
                      thresholdCm={shownMap.thresholdCm}
                      order={lorryOrder}
                      clock={gaugeClock}
                      reduced={reduced}
                      selectedPumpId={selectedPumpId}
                      onSelect={setSelectedPumpId}
                      onHover={setHoveredPumpId}
                    />
                  ) : shownMapError ? (
                    <p className="type-small text-text-2">{shownMapError}</p>
                  ) : (
                    <SkeletonRows rows={6} />
                  )}
                  {shownMap ? <MapNotes map={shownMap} /> : null}
                </Panel>
              )}
            </div>
          ) : (
            <div
              role="tabpanel"
              id="pumps-panel-board"
              aria-labelledby="pumps-tab-board"
              className="flex flex-col gap-6"
            >
              {label ? (
                <p className="type-micro text-text-3">
                  Benefit: {label}.{" "}
                  {priced
                    ? `The board as arranged saves about ${total} minutes above 45 cm, priced in ${priced.priceMs} ms.`
                    : "The optimiser's figures, computed when the cycle ran."}
                </p>
              ) : null}

              <PumpBoard
                pumps={available}
                columns={columns}
                depthNote={depthNote}
                planApplied={applied || priced !== null}
                onOptimise={canOptimise ? optimise : undefined}
                onAssign={
                  plan
                    ? (pumpId, columnId) =>
                        setMoved((current) => ({ ...current, [pumpId]: columnId }))
                    : undefined
                }
                onDispatch={canDispatch ? () => void dispatch() : undefined}
              />

              {movedCount > 0 ? (
                <p className="type-micro text-text-2" role="status">
                  {movedCount} pump{movedCount === 1 ? " has" : "s have"} been moved by hand.{" "}
                  {pricing
                    ? "Pricing the board as arranged through the same model the optimiser uses."
                    : failed
                      ? `${failed} The figures above are still the optimiser's for its own plan.`
                      : "The figures above are the board as arranged, priced by the API."}{" "}
                  Dispatch sends the optimiser&rsquo;s plan: press Optimise to put it back first.
                </p>
              ) : null}

              {priced?.refused.length ? (
                <ul className="type-micro text-text-3 flex flex-col gap-1">
                  {priced.refused.map((r) => (
                    <li key={`${r.pumpId}-${r.targetId}`}>{r.reason}</li>
                  ))}
                </ul>
              ) : null}

              <DispatchOrder order={order} />
            </div>
          )}

          {dispatched ? (
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.8fr)]">
              <Panel
                title="Alert instruction"
                description="What each alert about a dispatched place now says, on /alerts and the phone."
              >
                <ul className="flex flex-col gap-2">
                  {dispatched.instructions.map((line, i) => (
                    <li key={`${line}-${i}`} className="type-body text-text">
                      {line}
                    </li>
                  ))}
                </ul>
                <div className="mt-3 flex flex-col gap-1">
                  {dispatched.notes.map((note) => (
                    <p key={note} className="type-micro text-text-3">
                      {note}
                    </p>
                  ))}
                </div>
              </Panel>
              <Panel
                title="Ward officer's phone"
                description="The WhatsApp card the dispatch produced. On-screen mock."
              >
                <PhoneMock
                  messages={dispatched.messages}
                  freshIds={new Set(dispatched.messages.map((m) => m.id))}
                  popKey={1}
                />
              </Panel>
            </div>
          ) : null}
        </div>
      </div>
    </AppShell>
  );
}

/**
 * Why the screen opened on this cycle rather than the newest: said once, from the cycle list's own
 * counts, so a judge knows the picture is the storm's busiest plan and not a default.
 */
export function describeOpening(plan: PumpPlan, cycles: PumpCycles | null): string {
  const here = cycles?.cycles.find((c) => c.runId === plan.runId);
  const at = cycleClock(here?.cycleTs ?? null);
  const which = at ? `the ${at} cycle` : "this cycle";
  const counts = here
    ? ` (${Math.round(here.minutesSaved)} min, ${here.nAssigned} pump${here.nAssigned === 1 ? "" : "s"})`
    : "";
  return `Opened on ${which}: of the baked cycles, its plan avoids the most minutes above ${plan.thresholdCm} cm${counts}. Pick another cycle to compare.`;
}

/** What the map's recount found, when it did not reproduce the plan, and the road it compared. */
function MapNotes({ map }: { map: PumpMap }) {
  const { summary } = map;
  const roadLine =
    summary.routed > 0 && summary.routedMinutesSaved !== null
      ? `On the roads a truck would take at the cycle time, ${summary.lateOnRoad === 0 ? "every lorry arrives no later than the plan assumes" : `${summary.lateOnRoad} of ${summary.routed} lorries arrive later than the plan assumes`}; priced at those arrivals the plan avoids ${summary.routedMinutesSaved} minutes above ${map.thresholdCm} cm against the plan's ${summary.minutesSaved}. The plan's ETAs are straight lines at ${map.travelSpeedKmh} km/h.`
      : null;
  if (!roadLine && map.notes.length === 0) return null;
  return (
    <ul className="type-micro text-text-3 mt-4 flex max-w-[72ch] flex-col gap-1">
      {roadLine ? <li>{roadLine}</li> : null}
      {map.notes.map((note) => (
        <li key={note}>{note}</li>
      ))}
    </ul>
  );
}

/**
 * What the map says on a cycle whose plan sends no pump, from the plan's own counts, and the
 * cycle it offers instead: the one whose plan avoids the most minutes above 45 cm.
 */
export function describeNothingSent(
  plan: PumpPlan | null,
  cycles: PumpCycles | null,
): { title: string; description: string; offer: { runId: string; clock: string } | null } {
  const here = plan ? cycles?.cycles.find((c) => c.runId === plan.runId) : undefined;
  const at = cycleClock(here?.cycleTs ?? null);
  const which = at ? `the ${at} cycle` : "this cycle";
  const why = !plan
    ? `There is no pump plan for ${which}.`
    : plan.unassigned.length > 0
      ? `${plan.unassigned.length} place${plan.unassigned.length === 1 ? "" : "s"} cross ${plan.thresholdCm} cm on ${which}, and the plan found no pump it could send to any of them.`
      : `Nothing on ${which} crosses ${plan.thresholdCm} cm where a pump would help, so the plan sends none.`;
  const sending = (cycles?.cycles ?? []).filter((c) => c.nAssigned > 0);
  const list = sending
    .map((c) => {
      const clock = cycleClock(c.cycleTs);
      return clock ? `${clock} (${c.nAssigned})` : null;
    })
    .filter((x): x is string => x !== null);
  const busiest = sending.find((c) => c.runId === cycles?.busiestRunId);
  const busiestClock = busiest ? cycleClock(busiest.cycleTs) : null;
  const offer =
    busiest && busiestClock && busiest.runId !== plan?.runId
      ? { runId: busiest.runId, clock: busiestClock }
      : null;
  const elsewhere =
    list.length > 0
      ? ` Cycles that send pumps, with how many: ${list.join(", ")}.`
      : cycles
        ? " No baked cycle of this storm sends a pump."
        : "";
  return { title: "This cycle sends no pump", description: `${why}${elsewhere}`, offer };
}

interface MapActionsProps {
  nPumps: number;
  canOptimise: boolean;
  onOptimise: () => void;
  /** Replays M33-M35; absent while the map has no roads to drive. */
  onReplay?: () => void;
  onDispatch: () => void;
  dispatching: boolean;
  movedCount: number;
  reduced: boolean;
}

/**
 * The map's one decision, in its top-right corner. Before Optimise the fleet waits at its depots
 * and Optimise is the only button; pressing it sends every lorry down its road (M33), drains the
 * gauges (M34) and slides the arrivals onto the timeline (M35). After it: send the plan, or watch
 * it again. It sits over the map rather than inside it, so it is there before the roads load.
 */
function MapActions({
  nPumps,
  canOptimise,
  onOptimise,
  onReplay,
  onDispatch,
  dispatching,
  movedCount,
  reduced,
}: MapActionsProps) {
  const note = canOptimise
    ? movedCount > 0
      ? "The plan board was rearranged by hand. Optimise puts the optimiser's plan back."
      : `${nPumps} pump${nPumps === 1 ? " waits" : "s wait"} at ${nPumps === 1 ? "its depot" : "their depots"}.`
    : reduced
      ? "Reduced motion is on: every pump is shown at its place."
      : null;
  return (
    <div className="pointer-events-none absolute top-3 right-3 flex max-w-[20rem] flex-col items-end gap-2">
      <div className="pointer-events-auto flex flex-wrap justify-end gap-2">
        {canOptimise ? (
          <Button size="lg" onClick={onOptimise}>
            Optimise
          </Button>
        ) : (
          <>
            {onReplay && !reduced ? (
              <Button variant="secondary" size="lg" onClick={onReplay} className="gap-1.5">
                <Play aria-hidden="true" className="size-4" strokeWidth={1.75} />
                Play dispatch
              </Button>
            ) : null}
            <Button size="lg" onClick={onDispatch} disabled={dispatching}>
              Dispatch pumps
            </Button>
          </>
        )}
      </div>
      {note ? (
        <p className="type-micro text-text-2 bg-ink/85 rounded-control px-2 py-1 text-right">
          {note}
        </p>
      ) : null}
    </div>
  );
}

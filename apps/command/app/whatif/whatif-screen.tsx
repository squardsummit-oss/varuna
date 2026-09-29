"use client";

import { motion } from "motion/react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { AppShell } from "@/components/varuna/app-shell";
import { DeltaTable } from "@/components/varuna/delta-table";
import { CityMap } from "@/components/map/city-map";
import { cityBounds } from "@/components/map/basemap";
import { FloodMap } from "@/components/map/flood-map";
import { CyclePicker } from "@/components/varuna/cycle-picker";
import { MapSlot } from "@/components/varuna/map-slot";
import { PageHeader } from "@/components/varuna/page-header";
import { Panel } from "@/components/varuna/panel";
import { PanelErrorBoundary } from "@/components/varuna/panel-error-boundary";
import { PhysicsCheckPanel, TwinRunProgress } from "@/components/varuna/physics-check-result";
import {
  DEFAULT_WHATIF_VALUES,
  WhatIfControls,
  formatRainScale,
  formatTideOffset,
  snapTide,
  type WhatIfValues,
} from "@/components/varuna/whatif-controls";
import { WhatIfDetails } from "@/components/varuna/whatif-details";
import { apiUrl } from "@/lib/api/client";
import { allSegments, type GeoSegment, type RunDepth } from "@/lib/api/run-depth";
import { MAX_CLEANED_SEGMENTS, tideOfferNote, tideStopsFor } from "@/lib/api/whatif";
import { cityFromSearch } from "@/lib/city";
import { formatIst, formatKm, formatTimeWithLead, minutesBetween } from "@/lib/format";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { usePhysicsCheck } from "@/lib/hooks/use-physics-check";
import { useTwinOffer, useWhatIf, type WhatIfEngine } from "@/lib/hooks/use-twin-scenario";
import { DUR, DUR_MS, tween } from "@/lib/motion";
import { denseFrame, pathLengthM, pathPoints, type AffectedFrame } from "@/lib/map/affected-bounds";
import { navItem } from "@/lib/nav";
import { fetchOpeningRunId } from "@/lib/opening-run";

import {
  BEFORE_FLOOR_CM,
  EMULATOR_NO_TIDE,
  ENGINE_LABEL,
  MAX_STREET_ROWS,
  NOTHING_CHANGED_DETAIL,
  emulatorAnswer,
  parseSegments,
  twinAnswer,
  type WhatIfAnswer,
} from "./whatif-answer";

/** Motion M13: the diff layer wipes left to right over 500 ms (SPEC.md 8). */
const WIPE_MS = DUR_MS.diffWipe;

/** City slugs as the API validates a path segment; anything else is not put in a URL. */
const CITY_SLUG = /^[a-z][a-z0-9-]{0,31}$/;

const SCREEN = navItem("whatif");

const DESCRIPTION = "Change the rain, the tide, the pipes or the pumps and see which streets move.";

/**
 * The cycle the lab opens on when the URL pins none: 08:40 IST, the storm's peak in the 2 July
 * replay and the cycle the demo's what-if is asked about (section 15, 4:30). Left to the API, the
 * lab opened on the newest run - 09:10, after the storm - where a rain scenario has almost nothing
 * to move, and the stored tide answers are for 08:40. `/drains` and the dashboard open here too.
 */
export const WHATIF_OPENING_TS = "2019-07-02T08:40:00+05:30";

/**
 * Where the forecast map sits clear of what `MapSlot` floats over it, in px: the depth legend on
 * the right (`w-72` at `right-4`, plus a gutter), the "Reconstructed replay" chip and the
 * attribution line at the bottom. `CityMap` shrinks any side the box cannot afford.
 */
export const FORECAST_FIT_PADDING = { top: 16, right: 320, bottom: 76, left: 16 };

/** What the forecast map needs to say what it shows: the run's cycle and its valid times. */
export interface ForecastShown {
  runId: string;
  cycleTs: string | null;
  stepMin: number;
  validTs: readonly string[];
}

/**
 * One sentence under the forecast map before a what-if has run: which cycle, which step, and what
 * the camera is framed on, every number from the run and `affectedFrame` (rule 6). The step is the
 * frame's own, the run's peak, so the map, the sentence and the "before" of every answer agree.
 */
export function forecastCaption(
  frame: AffectedFrame | null,
  run: ForecastShown | null,
): string | null {
  if (!frame || !run) return null;
  const cycle = run.cycleTs ? `The ${formatIst(run.cycleTs)} IST cycle` : "This cycle";
  const lower = `${cycle.charAt(0).toLowerCase()}${cycle.slice(1)}`;
  if (frame.basis === "aoi") return `${cycle} has no wet street; the map shows the whole city.`;
  if (frame.basis === "hotspots") {
    return `No street reaches 5 cm in ${lower}; the map shows its chronic spots.`;
  }
  const step = frame.step ?? 0;
  const ts = run.validTs[step];
  const lead =
    (run.cycleTs && ts ? minutesBetween(run.cycleTs, ts) : null) ?? (step + 1) * run.stepMin;
  return (
    `Before any change: ${lower} at its peak, ${ts ? formatTimeWithLead(ts, lead) : "its peak step"}. ` +
    `${formatKm(frame.lengthM)} of street at ${frame.thresholdCm} cm or more.`
  );
}

/** The scenario as one line of copy, so the controls and the result panel agree. Every lever the
 * request carries, and only those (SPEC.md rule 6). */
function scenarioLine(values: WhatIfValues): string {
  const parts = [
    `Rain ${formatRainScale(values.rainScale)}`,
    `tide ${formatTideOffset(values.tideOffsetM)}`,
  ];
  const n = values.cleanedSegments.length;
  if (n > 0) parts.push(`${n} segment${n === 1 ? "" : "s"} cleaned`);
  if (values.cleanTop14) parts.push("top 14 pipes cleaned");
  if (values.pumpPlan) parts.push("pump plan on");
  return parts.join(", ");
}

/**
 * Motion M31's second half: the Twin's answer replaces the emulator's with a 300 ms cross-fade.
 * Keyed on the engine, so the fade runs once per swap and never on a re-render; instant under
 * reduced motion.
 */
function AnswerFade({ engine, children }: { engine: WhatIfEngine | null; children: ReactNode }) {
  const reducedMotion = usePrefersReducedMotion();
  return (
    <motion.div
      key={engine ?? "none"}
      initial={reducedMotion || engine !== "twin" ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={reducedMotion ? { duration: 0 } : tween(DUR.crossFade)}
    >
      {children}
    </motion.div>
  );
}

/**
 * What-if lab, Kalpana (SPEC.md section 7.7).
 *
 * "Run what-if" asks the reduced-order emulator (`POST /v1/whatif`), which answers rain, the
 * cleaned segments, "Clean top 14 by blockage" and the pump plan in under a second, levelled on
 * the run's own Twin forecast. The emulator has no sea level, so a question that moves the tide
 * shows the emulator's part at once, labelled "Emulator, tide not included", and runs the whole
 * question on the full-city Twin (`POST /v1/whatif/twin`, about a minute). The bar fills one Twin
 * output step at a time with the time left and a Cancel (M31), and the Twin's answer replaces the
 * emulator's with a cross-fade, including how much sea crossed onto the surface and what moved.
 *
 * The difference layer, "Hotspot deltas" and "Largest changes" read one list per answer, so the
 * map and the tables cannot disagree. "Physics check" re-runs a rain or cleaning scenario on the
 * Twin over a 990 m window and prints the disagreement (P7.8).
 *
 * `?segments=a,b,c&run=<run id>&from=<hotspot>` is the console's "Clean in what-if" deep link
 * (P7.11): the hotspot's own segments arrive as chips and the cycle it was pressed on is the
 * cycle the question is asked about. The streets are drawn from that run's own city.
 */
function WhatIfLab() {
  const searchParams = useSearchParams();
  // Read once. The chips are editable after mount, so re-reading the URL on every render would
  // put back a segment the operator has just removed.
  const [deepLink] = useState(() => ({
    ...parseSegments(searchParams.get("segments")),
    runId: searchParams.get("run") ?? undefined,
    hotspot: searchParams.get("from") ?? undefined,
    city: cityFromSearch(searchParams.toString()),
  }));
  const [values, setValues] = useState<WhatIfValues>({
    ...DEFAULT_WHATIF_VALUES,
    cleanedSegments: deepLink.picked,
  });
  const [wipe, setWipe] = useState(1);
  const reducedMotion = usePrefersReducedMotion();
  // **Which cycle.** A what-if is a question about one forecast. Left unsaid, the handler answers
  // about the newest baked run - 09:10 IST, after the storm, 1,498 wet segments - while the same
  // scenario at 08:40 has 6,474 to move. The operator picks the cycle here as they do on the
  // console, on Alerts and on Pumps - or the deep link brings the cycle it was pressed on.
  const [runId, setRunId] = useState<string | undefined>(deepLink.runId);
  // With no `?run=`, the lab opens on the 08:40 storm cycle (WHATIF_OPENING_TS), and the map holds
  // its load until it knows that is the run to draw.
  const [opened, setOpened] = useState(deepLink.runId !== undefined);
  useEffect(() => {
    if (opened) return;
    const controller = new AbortController();
    void fetchOpeningRunId(deepLink.city, WHATIF_OPENING_TS, apiUrl, controller.signal).then(
      (opening) => {
        if (controller.signal.aborted) return;
        setRunId((current) => current ?? opening);
        setOpened(true);
      },
    );
    return () => controller.abort();
  }, [opened, deepLink.city]);
  // What this API's Twin can answer for the cycle: everything, or only its stored tide answers.
  const offer = useTwinOffer(runId, opened);
  const tideStops = useMemo(() => tideStopsFor(offer), [offer]);
  const flow = useWhatIf(runId, offer);
  const physics = usePhysicsCheck(runId);
  const twin = flow.twin;

  // **The forecast before the question.** Until a what-if has run, the map slot draws the cycle
  // the lab is set to - the console's own `FloodMap`, framed on its main affected area at the
  // run's peak - so the flood is on screen before Run what-if is pressed. Both callbacks are
  // stable: `FloodMap` reloads the run whenever `onLoaded` changes identity.
  const [forecast, setForecast] = useState<ForecastShown | null>(null);
  const [forecastFrame, setForecastFrame] = useState<AffectedFrame | null>(null);
  const onForecastLoaded = useCallback((run: RunDepth) => {
    setForecast({
      runId: run.provenance.runId,
      cycleTs: run.provenance.cycleTs,
      stepMin: run.provenance.stepMin,
      validTs: run.validTs,
    });
  }, []);
  // A picked cycle is a different run: until it has loaded, nothing is said about the last one.
  const forecastShown = forecast && (!runId || forecast.runId === runId) ? forecast : null;
  const forecastLine = forecastCaption(forecastFrame, forecastShown);

  // **Which city's streets.** A run belongs to one city, and a Chennai answer drawn on Mumbai's
  // geometry would join no segment. The run's own `city` decides once it is known; until then the
  // URL's `?city=`, which is the city every other screen is showing.
  const cityRunId = flow.emulator?.runId ?? runId;
  const [runCity, setRunCity] = useState<{ runId: string; city: string } | null>(null);
  useEffect(() => {
    if (!cityRunId || runCity?.runId === cityRunId) return;
    const controller = new AbortController();
    fetch(apiUrl(`/v1/runs/${encodeURIComponent(cityRunId)}`), { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((meta: { city?: unknown } | null) => {
        const city = typeof meta?.city === "string" ? meta.city.toLowerCase() : "";
        if (CITY_SLUG.test(city)) setRunCity({ runId: cityRunId, city });
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [cityRunId, runCity?.runId]);
  const city = runCity && runCity.runId === cityRunId ? runCity.city : deepLink.city;

  // The city's own street geometry, so an answer's per-segment deltas have something to be drawn
  // on. Loaded once per city; a scenario only ever changes the numbers attached to these paths.
  const [streets, setStreets] = useState<{ city: string; rows: GeoSegment[] }>({
    city: "",
    rows: [],
  });
  useEffect(() => {
    const controller = new AbortController();
    fetch(apiUrl(`/v1/city/${encodeURIComponent(city)}/layers/segments`), {
      signal: controller.signal,
    })
      .then((r) => (r.ok ? r.json() : { features: [] }))
      .then((geojson) => setStreets({ city, rows: allSegments(geojson) }))
      .catch(() => undefined);
    return () => controller.abort();
  }, [city]);
  const streetRows = useMemo(() => (streets.city === city ? streets.rows : []), [streets, city]);
  // Every street by the name a list prints: OSM's, else the layer's `display_name`.
  const names = useMemo(
    () =>
      new Map(
        streetRows
          .map((s) => [s.id, s.displayName ?? s.name] as const)
          .filter((row): row is readonly [string, string] => Boolean(row[1])),
      ),
    [streetRows],
  );

  // A different cycle is a different answer, so the last one stops being shown with it.
  const pickCycle = useCallback((next: string) => {
    setRunId(next);
    setOpened(true);
  }, []);

  // The answer on screen: the Twin's once it has finished the question, the emulator's until then.
  // The job's fields are read out first so the memo depends on exactly the values it uses, which
  // is what lets the React Compiler keep this memoization rather than skip the component.
  const twinCacheLabel = twin.job?.cache?.label ?? null;
  const twinScenarioNotes = twin.job?.scenario.notes;
  const answer: WhatIfAnswer | null = useMemo(() => {
    if (flow.engine === "twin" && twin.result) {
      return twinAnswer(twin.result, {
        names,
        cacheLabel: twinCacheLabel,
        asked: flow.asked,
        scenarioNotes: twinScenarioNotes ?? [],
      });
    }
    return flow.emulator ? emulatorAnswer(flow.emulator) : null;
  }, [
    flow.asked,
    flow.engine,
    flow.emulator,
    names,
    twinCacheLabel,
    twinScenarioNotes,
    twin.result,
  ]);
  const tideAsked = (flow.asked?.tideOffsetM ?? 0) !== 0;
  const tideLeftOut = answer?.engine === "emulator" && tideAsked;

  // Every changed segment the answer lists, joined onto the city's geometry, and only those: the
  // rest are drawn as the dry base layer underneath. A changed id the served layer has no geometry
  // for cannot be drawn; `undrawn` counts them so the count and the map still agree.
  const diffSegments = useMemo(() => {
    if (!answer || streetRows.length === 0) return [];
    return streetRows
      .filter((s) => answer.deltaCm.has(s.id))
      .map((s) => ({ ...s, deltaCm: answer.deltaCm.get(s.id) ?? 0 }));
  }, [answer, streetRows]);

  // Where the difference layer opens: on the streets the scenario changed, each weighted by its
  // length times how far its peak moved, framed by the same densest-7-km rule as the console
  // (`lib/map/affected-bounds.ts`) - not on the whole city with the changes a few pixels each.
  // The M13 wipe then sweeps across that frame.
  const diffFrame = useMemo(
    () =>
      denseFrame(
        diffSegments.flatMap((segment) =>
          pathPoints(segment.path, pathLengthM(segment.path) * Math.abs(segment.deltaCm ?? 0)),
        ),
        { within: cityBounds(city) },
      )?.bounds ?? null,
    [diffSegments, city],
  );

  // Motion M13: the diff wipes in left to right over 500 ms when the emulator answers. The Twin's
  // answer swaps in under M31's cross-fade instead, so the map is not wiped twice for one question.
  useEffect(() => {
    if (!flow.emulator) return;
    if (reducedMotion) {
      const settle = requestAnimationFrame(() => setWipe(1));
      return () => cancelAnimationFrame(settle);
    }
    let frame = 0;
    const started = performance.now();
    const tick = () => {
      const t = Math.min((performance.now() - started) / WIPE_MS, 1);
      setWipe(t);
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [flow.emulator, reducedMotion]);

  const undrawn = answer && streetRows.length > 0 ? answer.nChanged - diffSegments.length : 0;
  const emulator = flow.emulator;
  // A cleaned segment's chip reads as its street, not its id.
  const segmentLabel = useCallback((id: string) => names.get(id), [names]);
  const shownValues = { ...values, tideOffsetM: snapTide(values.tideOffsetM, tideStops) };

  return (
    <AppShell>
      <div className="h-full min-h-0 overflow-y-auto">
        <div className="mx-auto flex max-w-[1440px] flex-col gap-6 p-6">
          <PageHeader
            title={SCREEN.label}
            screen={SCREEN}
            description={DESCRIPTION}
            // The engine that answered what is on screen, not the one the page is named after.
            honesty={ENGINE_LABEL[answer?.engine ?? "emulator"]}
          />

          <CyclePicker
            currentRunId={answer?.runId ?? forecastShown?.runId ?? runId}
            onPick={pickCycle}
          />

          <div className="grid min-h-0 gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
            <PanelErrorBoundary title="Scenario">
              <Panel title="Scenario" className="min-w-0">
                <WhatIfControls
                  initial={values}
                  onChange={setValues}
                  onRun={flow.run}
                  onPhysicsCheck={physics.running ? undefined : physics.check}
                  physicsDisabledReason="Checking: the Twin is running the scenario"
                  cleanedSource={deepLink.hotspot}
                  segmentLabel={segmentLabel}
                  tideStops={tideStops}
                  tideNote={tideOfferNote(offer)}
                />
                {deepLink.asked > deepLink.picked.length ? (
                  // The URL is hand-editable, so a longer list is possible; saying nothing would
                  // run a smaller scenario than the address bar describes.
                  <p className="type-micro text-text-3 mt-3">
                    The link asked for {deepLink.asked} segments; the first {MAX_CLEANED_SEGMENTS}{" "}
                    are loaded.
                  </p>
                ) : null}
                {flow.emulatorRunning ? (
                  <p className="type-small text-text-3 mt-3">
                    Running the scenario on the emulator
                  </p>
                ) : null}
                {flow.emulatorError ? (
                  <p className="type-small text-text-2 mt-3">{flow.emulatorError}</p>
                ) : null}
                {flow.twinSkipped ? (
                  <p className="type-small text-text-2 mt-3">{flow.twinSkipped}</p>
                ) : null}
                {tideAsked && !flow.twinSkipped ? (
                  <TwinRunProgress
                    className="border-line mt-4 border-t pt-4"
                    line={twin.line}
                    fraction={twin.fraction}
                    running={twin.running}
                    error={twin.error}
                    cancelled={twin.cancelled}
                    onCancel={twin.cancel}
                  />
                ) : null}
                {emulator && emulator.cleanedUnmatched.length > 0 ? (
                  // The endpoint drops an id this city has no segment for rather than failing,
                  // so the count has to be visible or a partly wrong link reads as a whole answer.
                  <p className="type-micro text-text-2 mt-3">
                    {emulator.cleanedUnmatched.length} of the ids asked for are not road segments in
                    this city and were not cleaned.
                  </p>
                ) : null}
              </Panel>
            </PanelErrorBoundary>

            <div className="flex min-w-0 flex-col gap-4">
              <PanelErrorBoundary title="Difference layer">
                <Panel
                  title="Difference layer"
                  description={
                    answer
                      ? "Change in peak depth per street: improved, worse, unchanged."
                      : "This cycle's forecast until a what-if runs."
                  }
                  className="min-w-0"
                >
                  <div className="rounded-control border-line relative h-[380px] overflow-hidden border">
                    {!answer ? (
                      <>
                        {/* The legend, the replay chip and the credit float over the map, as on
                            the console; the run's own loading, empty and error states cover it. */}
                        <MapSlot emptyState={null} />
                        <FloodMap
                          city={city}
                          runId={runId}
                          deferLoad={!opened}
                          step={forecastFrame?.step ?? 0}
                          onLoaded={onForecastLoaded}
                          frameOn="affected"
                          onFrame={setForecastFrame}
                          fitPadding={FORECAST_FIT_PADDING}
                          showSurcharge={false}
                          showHotspots={false}
                          attribution={false}
                        />
                      </>
                    ) : answer && diffSegments.length > 0 ? (
                      <CityMap
                        frames={[]}
                        rasterBounds={null}
                        baseSegments={streetRows}
                        segments={diffSegments}
                        surcharge={[]}
                        hotspots={[]}
                        diffMode
                        diffProgress={wipe}
                        showRaster={false}
                        showSurcharge={false}
                        showBuildings={false}
                        showHotspots={false}
                        step={0}
                        fitBounds={diffFrame}
                      />
                    ) : (
                      <MapSlot
                        emptyState={
                          answer?.nothingChanged
                            ? {
                                // A scenario that moved nothing draws nothing: no grey streets
                                // standing in for an answer.
                                title: "Nothing changed",
                                description: NOTHING_CHANGED_DETAIL,
                              }
                            : {
                                title: "No scenario run yet",
                                description: "Set a lever, then press Run what-if.",
                              }
                        }
                      />
                    )}
                  </div>
                  <AnswerFade engine={answer?.engine ?? null}>
                    {answer ? (
                      <>
                        <div className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                          <p className="type-body text-text font-medium">{answer.summary}</p>
                          <span className="rounded-chip border-line type-micro text-text-2 border px-2 py-0.5">
                            {tideLeftOut ? EMULATOR_NO_TIDE : ENGINE_LABEL[answer.engine]}
                          </span>
                          {answer.skill ? (
                            <span className="num type-micro text-text-2">{answer.skill}</span>
                          ) : null}
                        </div>
                        {answer.tideOutcome ? (
                          <p className="type-small text-text mt-1">{answer.tideOutcome}</p>
                        ) : null}
                        <WhatIfDetails className="mt-2">
                          <p>
                            Answered in{" "}
                            <span className="num">
                              {Math.round(answer.ms).toLocaleString("en-IN")}
                            </span>{" "}
                            ms
                            {emulator && emulator.cleanedSegments.length > 0
                              ? `; ${emulator.cleanedSegments.length} segment${emulator.cleanedSegments.length === 1 ? "" : "s"} cleaned`
                              : ""}
                            .
                          </p>
                          {!answer.nothingChanged && streetRows.length > 0 ? (
                            <p>
                              Drawn:{" "}
                              <span className="num">
                                {diffSegments.length.toLocaleString("en-IN")}
                              </span>{" "}
                              of{" "}
                              <span className="num">{answer.nChanged.toLocaleString("en-IN")}</span>
                              .
                              {undrawn > 0
                                ? ` ${undrawn.toLocaleString("en-IN")} changed segment${undrawn === 1 ? " has" : "s have"} no geometry in the served street layer and cannot be drawn.`
                                : ""}
                              {answer.nDryBefore > 0
                                ? ` ${answer.nDryBefore.toLocaleString("en-IN")} were below ${BEFORE_FLOOR_CM} cm in the run, which does not store their depth; their change is read from 0 cm.`
                                : ""}
                            </p>
                          ) : null}
                          {answer.lines.map((line) => (
                            <p key={line}>{line}</p>
                          ))}
                        </WhatIfDetails>
                      </>
                    ) : (
                      <>
                        {forecastLine ? (
                          <p className="type-micro text-text-2 mt-3">{forecastLine}</p>
                        ) : null}
                        <p className="type-micro text-text-3 mt-1">
                          Scenario ready to run: {scenarioLine(shownValues)}.
                        </p>
                      </>
                    )}
                  </AnswerFade>
                </Panel>
              </PanelErrorBoundary>

              <PanelErrorBoundary title="Hotspot deltas">
                <Panel
                  title="Hotspot deltas"
                  // One row per junction of the run's own register: its peak and its minutes
                  // above 30 and 45 cm, before and after, from whichever engine answered.
                  description="Peak depth and minutes above 30 and 45 cm, before and after."
                  className="min-w-0"
                >
                  <AnswerFade engine={answer?.engine ?? null}>
                    <DeltaTable
                      rows={answer?.hotspots ?? []}
                      minutesLabel="Minutes above 30 cm"
                      minutes45Label="Minutes above 45 cm"
                      emptyTitle={answer ? "No hotspot to compare" : "No what-if yet"}
                      emptyDescription={
                        answer
                          ? "None of this run's hotspots has a road segment the answer covers."
                          : "Set the controls and run one."
                      }
                    />
                  </AnswerFade>
                </Panel>
              </PanelErrorBoundary>

              <PanelErrorBoundary title="Largest changes">
                <Panel
                  title="Largest changes"
                  description={`The ${MAX_STREET_ROWS} streets the scenario moved most.`}
                  className="min-w-0"
                >
                  <AnswerFade engine={answer?.engine ?? null}>
                    <DeltaTable
                      rows={answer?.streets ?? []}
                      nameLabel="Street"
                      emptyTitle={answer ? "Nothing changed" : "No what-if yet"}
                      emptyDescription={
                        answer ? NOTHING_CHANGED_DETAIL : "Set the controls and run one."
                      }
                    />
                  </AnswerFade>
                </Panel>
              </PanelErrorBoundary>

              <PanelErrorBoundary title="Physics check">
                <Panel
                  title="Physics check"
                  description="The emulator against a Twin run of the same scenario."
                  className="min-w-0"
                >
                  <PhysicsCheckPanel
                    result={physics.result}
                    running={physics.running}
                    error={physics.error}
                    maxRows={8}
                  />
                </Panel>
              </PanelErrorBoundary>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

/**
 * The lab, behind the Suspense boundary `useSearchParams` needs.
 *
 * Without it `next build` refuses the route: a client component reading the query string cannot
 * be prerendered, and Next asks for the boundary rather than silently opting the whole page into
 * client rendering. The fallback is what the lab looks like before its own data loads anyway.
 */
export function WhatIfScreen() {
  return (
    <Suspense
      fallback={
        <AppShell>
          <div className="mx-auto flex max-w-[1440px] flex-col gap-6 p-6">
            <PageHeader
              title={SCREEN.label}
              screen={SCREEN}
              description={DESCRIPTION}
              honesty={ENGINE_LABEL.emulator}
            />
          </div>
        </AppShell>
      }
    >
      <WhatIfLab />
    </Suspense>
  );
}

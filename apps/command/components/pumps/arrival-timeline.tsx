"use client";

import { motion } from "motion/react";

import { formatMinutes } from "@/lib/format";
import { DUR, EASE_UI } from "@/lib/motion";
import { cssVar, depthBand } from "@/lib/ramps";
import type { PumpLeg } from "@/lib/api/pumps";
import { cn } from "@/lib/utils";

import { byFloodStart, clockAt, race } from "./model";
import { rowPointerHandlers, useArrived, type GaugeClock } from "./place-gauges";

export interface ArrivalTimelineProps {
  legs: readonly PumpLeg[];
  cycleTs: string | null;
  stepMin: number;
  nSteps: number;
  thresholdCm: number;
  /** Each pump's place in the dispatch order, which times its arrival (M33); 0 when absent. */
  order?: ReadonlyMap<string, number>;
  /** The dispatch the markers slide in with (M35); null shows them at their times. */
  clock?: GaugeClock | null;
  reduced?: boolean;
  /** The pump whose row was pressed; see `PlaceGauges`. */
  selectedPumpId?: string | null;
  onSelect?: (pumpId: string | null) => void;
  /** The row under the pointer or focus, null when it leaves; never a press. */
  onHover?: (pumpId: string | null) => void;
  className?: string;
}

/** Where `minutes` sits along a timeline of `span` minutes, as a CSS percentage. */
function percentOf(minutes: number, span: number): string {
  return `${Math.min(100, Math.max(0, (minutes / span) * 100))}%`;
}

/** A stretch of consecutive forecast steps above the line in one depth band. */
export interface CellRun {
  fromMin: number;
  toMin: number;
  /** The depth ramp's band key, e.g. "4". */
  band: string;
}

/**
 * The steps above `thresholdCm`, merged into runs of one depth band. One rectangle per run
 * rather than one per 5-minute step: 36 abutting cells drawn at fractional widths overlap by a
 * sub-pixel at every edge, and at 30 % opacity each overlap showed as a dark seam.
 *
 * **A cell begins at its step's valid time.** Step `i` is valid at `(i + 1) * stepMin` after the
 * cycle (`segments_wet.json` `valid_ts`, and the API's `window_before.from_min`), so its cell is
 * `[(i + 1) * stepMin, (i + 2) * stepMin)`. Drawn from `i * stepMin` instead, every cell sat five
 * minutes before the time the row's sentence gives for it, and a pump arriving inside the first
 * pale cell was called "before the water".
 */
export function cellRuns(
  series: readonly number[],
  thresholdCm: number,
  stepMin: number,
): CellRun[] {
  const runs: CellRun[] = [];
  series.forEach((depth, i) => {
    if (!(depth > thresholdCm)) return;
    const band = depthBand(depth).key;
    const from = (i + 1) * stepMin;
    const last = runs[runs.length - 1];
    if (last && last.band === band && last.toMin === from) last.toMin = from + stepMin;
    else runs.push({ fromMin: from, toMin: from + stepMin, band });
  });
  return runs;
}

/**
 * Minutes the timeline spans: the cycle time to the end of the last step's cell, which begins at
 * the last valid time (`nSteps * stepMin`, +180 min) and so ends one step after it.
 */
export function timelineSpan(nSteps: number, stepMin: number): number {
  return Math.max(1, (nSteps + 1) * stepMin);
}

/** One sentence for a row: when the pump arrives against when the water crosses 45 cm. */
export function raceSentence(leg: PumpLeg, cycleTs: string | null, thresholdCm: number): string {
  const r = race(leg);
  const arrives = clockAt(cycleTs, leg.etaMin);
  const at = arrives
    ? `${arrives} (+${formatMinutes(leg.etaMin)})`
    : `+${formatMinutes(leg.etaMin)}`;
  if (r.kind === "dry" || r.kind === "unknown") return `Arrives ${at}.`;
  const crosses = leg.windowBefore ? clockAt(cycleTs, leg.windowBefore.fromMin) : null;
  const when = crosses ? ` at ${crosses}` : "";
  return r.kind === "early"
    ? `Arrives ${at}, ${formatMinutes(r.marginMin)} before the water passes ${thresholdCm} cm${when}.`
    : `Arrives ${at}, ${formatMinutes(r.marginMin)} after the water passes ${thresholdCm} cm${when}.`;
}

/**
 * The race on every street (Jalayantra, motion M35): the forecast minutes above
 * 45 cm with no pump, the minutes that stay above it with the pump, and the pump's arrival, on
 * one clock. "Why this pump goes there, and whether it gets there in time" is this chart.
 *
 * Rows are in the order the water arrives. Each 5-minute step is a cell, because that is the
 * resolution the minutes on the plan are counted at; a smoother bar would claim more. A cell is
 * coloured by the depth ramp at its forecast depth. Before Optimise a row shows only the water
 * with no pump. When the dispatch starts, each pump's arrival marker slides from the cycle time to
 * its arrival in 300 ms (M35), and the cells that stay above 45 cm with the pump replace the
 * no-pump picture as that pump reaches its place on the map; under reduced motion both are simply
 * there once the plan is sent.
 */
export function ArrivalTimeline({
  legs,
  cycleTs,
  stepMin,
  nSteps,
  thresholdCm,
  order,
  clock = null,
  reduced = false,
  selectedPumpId = null,
  onSelect,
  onHover,
  className,
}: ArrivalTimelineProps) {
  const span = timelineSpan(nSteps, stepMin);
  // Ticks run to the last valid time (+180 min), not to the end of its cell.
  const ticks = Array.from({ length: Math.floor((nSteps * stepMin) / 30) + 1 }, (_, i) => i * 30);
  const pct = (minutes: number) => percentOf(minutes, span);
  const rows = byFloodStart(legs);

  return (
    <figure className={cn("flex min-w-0 flex-col gap-3", className)}>
      <figcaption className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="type-small text-text-2">
          Water above {thresholdCm} cm against each pump&rsquo;s arrival, from the{" "}
          {clockAt(cycleTs, 0) ?? "cycle"} forecast
        </span>
        <span className="type-micro text-text-3 flex flex-wrap items-center gap-3">
          <span className="flex items-center gap-1.5">
            <span aria-hidden="true" className="bg-depth-4 h-2.5 w-3 rounded-[2px] opacity-30" />
            Above {thresholdCm} cm with no pump
          </span>
          <span className="flex items-center gap-1.5">
            <span aria-hidden="true" className="bg-depth-4 h-2.5 w-3 rounded-[2px]" />
            Still above with the pump, coloured by depth
          </span>
          <span className="flex items-center gap-1.5">
            <span aria-hidden="true" className="bg-tide h-3 w-0.5" />
            Pump arrives
          </span>
        </span>
      </figcaption>

      <div className="grid grid-cols-[minmax(8rem,13rem)_minmax(0,1fr)_minmax(9rem,12rem)] gap-x-3 gap-y-1">
        {/* Axis */}
        <span aria-hidden="true" />
        <div aria-hidden="true" className="relative h-4">
          {ticks.map((t) => (
            <span
              key={t}
              className="num type-micro text-text-3 absolute -translate-x-1/2 first:translate-x-0"
              style={{ left: pct(t) }}
            >
              {clockAt(cycleTs, t) ?? `+${t}`}
            </span>
          ))}
        </div>
        <span aria-hidden="true" />

        {rows.map((leg) => (
          <TimelineRow
            key={leg.pumpId}
            leg={leg}
            order={order?.get(leg.pumpId) ?? 0}
            cycleTs={cycleTs}
            stepMin={stepMin}
            thresholdCm={thresholdCm}
            span={span}
            clock={clock}
            reduced={reduced}
            selected={leg.pumpId === selectedPumpId}
            onSelect={onSelect}
            onHover={onHover}
          />
        ))}
      </div>
    </figure>
  );
}

interface TimelineRowProps {
  leg: PumpLeg;
  order: number;
  cycleTs: string | null;
  stepMin: number;
  thresholdCm: number;
  span: number;
  clock: GaugeClock | null;
  reduced: boolean;
  selected: boolean;
  onSelect?: (pumpId: string | null) => void;
  onHover?: (pumpId: string | null) => void;
}

function TimelineRow({
  leg,
  order,
  cycleTs,
  stepMin,
  thresholdCm,
  span,
  clock,
  reduced,
  selected,
  onSelect,
  onHover,
}: TimelineRowProps) {
  const pct = (minutes: number) => percentOf(minutes, span);
  const arrived = useArrived(order, clock, reduced);
  // Markers wait for the dispatch to start, then slide in with it; with no dispatch to time
  // from, or under reduced motion, they are drawn where they belong. Before Optimise: none.
  const idle = Boolean(clock?.idle);
  const still = !idle && (reduced || clock === null);
  const moving = !idle && !still && clock !== null && clock.startMs !== null;
  const before = leg.depthBeforeCm ?? [];
  const after = leg.depthAfterCm ?? [];
  const sentence = raceSentence(leg, cycleTs, thresholdCm);
  const r = race(leg);
  const late = r.kind === "late";
  const markerClass = cn("absolute inset-y-0 w-0.5", late ? "bg-text" : "bg-tide");

  return (
    <div className="contents">
      <button
        type="button"
        aria-pressed={selected}
        {...rowPointerHandlers(leg.pumpId, selected, onSelect, onHover)}
        className={cn(
          "rounded-control flex min-w-0 flex-col px-2 py-1 text-left transition-colors duration-150",
          "focus-visible:ring-tide/60 focus-visible:ring-2 focus-visible:outline-none",
          selected ? "bg-well" : "hover:bg-well",
        )}
      >
        <span className="type-small text-text truncate">{leg.target.name}</span>
        <span className="num type-micro text-text-3">
          {leg.pumpId}, {formatMinutes(leg.etaMin)} away
        </span>
      </button>
      <div
        role="img"
        aria-label={`${leg.target.name}: ${formatMinutes(leg.minutesBefore)} above ${thresholdCm} cm with no pump, ${formatMinutes(leg.minutesAfter)} with ${leg.pumpId}. ${sentence}`}
        className={cn(
          "rounded-control border-line bg-ink relative h-9 self-center overflow-hidden border",
          selected && "border-line-strong",
        )}
      >
        {cellRuns(before, thresholdCm, stepMin).map((run) => (
          <span
            key={`b${run.fromMin}`}
            data-testid="cell-before"
            className="absolute inset-y-1 opacity-30"
            style={{
              left: pct(run.fromMin),
              width: pct(run.toMin - run.fromMin),
              backgroundColor: cssVar(`--depth-${run.band}`),
            }}
          />
        ))}
        {arrived
          ? cellRuns(after, thresholdCm, stepMin).map((run) => (
              <span
                key={`a${run.fromMin}`}
                data-testid="cell-after"
                className="absolute inset-y-2.5"
                style={{
                  left: pct(run.fromMin),
                  width: pct(run.toMin - run.fromMin),
                  backgroundColor: cssVar(`--depth-${run.band}`),
                }}
              />
            ))
          : null}
        {still ? (
          <span
            aria-hidden="true"
            data-testid="arrival-marker"
            className={markerClass}
            style={{ left: pct(leg.etaMin) }}
          />
        ) : moving ? (
          <motion.span
            key={clock?.key}
            aria-hidden="true"
            data-testid="arrival-marker"
            className={markerClass}
            initial={{ left: "0%", opacity: 0 }}
            animate={{ left: pct(leg.etaMin), opacity: 1 }}
            transition={{ duration: DUR.crossFade, ease: EASE_UI }}
          />
        ) : null}
      </div>
      <span
        aria-hidden="true"
        className={cn("num type-micro self-center", late ? "text-text" : "text-text-2")}
      >
        {r.kind === "unknown"
          ? "No depth series"
          : r.kind === "dry"
            ? "Never above the line"
            : `${formatMinutes(r.marginMin)} ${late ? "after" : "before"} the water`}
      </span>
    </div>
  );
}

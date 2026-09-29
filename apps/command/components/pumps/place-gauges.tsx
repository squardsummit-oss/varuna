"use client";

import NumberFlow from "@number-flow/react";
import { useEffect, useState } from "react";

import { arriveMs } from "./dispatch-clock";
import { depthBandLabel } from "@/components/varuna/depth-chip";
import { formatMinutes } from "@/lib/format";
import { DUR_MS, EASE_UI_CSS } from "@/lib/motion";
import { cssVar, depthBand } from "@/lib/ramps";
import type { PumpLeg } from "@/lib/api/pumps";
import { cn } from "@/lib/utils";

/** The gauge's full scale: the deepest peak on the plan, never below 60 cm (`--depth-5`). */
export function gaugeScaleCm(legs: readonly PumpLeg[]): number {
  const deepest = Math.max(0, ...legs.map((l) => l.peakBefore?.depthCm ?? 0));
  return Math.max(60, Math.ceil(deepest / 10) * 10);
}

/** 0 to 100: where a depth sits on the gauge. */
export function gaugePercent(depthCm: number, scaleCm: number): number {
  if (!(scaleCm > 0)) return 0;
  return Math.min(100, Math.max(0, (depthCm / scaleCm) * 100));
}

/**
 * How far the solid column shrinks when the pump arrives: the pumped peak as a share of the
 * no-pump peak, which is the `scaleY` M34 drains to. 1 when there is nothing to drain.
 */
export function drainScale(beforeCm: number | null, afterCm: number | null): number {
  if (beforeCm === null || afterCm === null || !(beforeCm > 0)) return 1;
  return Math.min(1, Math.max(0, afterCm / beforeCm));
}

/** The dispatch the gauges are timed from: the same clock as the map's lorries (M33). */
export interface GaugeClock {
  /** Changes for every dispatch, so a replay starts every gauge full again. */
  key: string;
  /** `performance.now()` at which the dispatch began; null until the map starts it. */
  startMs: number | null;
  /** The plan is on screen but not sent (before Optimise): every pump is still at its depot. */
  idle?: boolean;
}

/**
 * Whether this pump has reached its place on the dispatch clock: false from the dispatch's start
 * until `order`'s arrival, then true. One state change per gauge per dispatch, never per frame.
 * Under reduced motion it is true from the start (section 8: the final value, no drain). Before
 * Optimise it is false under every preference: no pump has been sent.
 */
export function useArrived(order: number, clock: GaugeClock | null, reduced: boolean): boolean {
  const [arrivedKey, setArrivedKey] = useState<string | null>(null);
  const key = clock?.key ?? null;
  const startMs = clock?.startMs ?? null;
  const idle = Boolean(clock?.idle);
  useEffect(() => {
    if (idle || reduced || key === null || startMs === null) return;
    const wait = startMs + arriveMs(order) - performance.now();
    const timer = window.setTimeout(() => setArrivedKey(key), Math.max(0, wait));
    return () => window.clearTimeout(timer);
  }, [order, key, startMs, reduced, idle]);
  if (idle) return false;
  // With no dispatch to time from (no clock at all), the plan's answer is what is shown.
  if (reduced || clock === null) return true;
  return arrivedKey === key;
}

export interface PlaceGaugeProps {
  leg: PumpLeg;
  scaleCm: number;
  thresholdCm: number;
  /** Whether the pump is there yet (M34 drains the column when this turns true). */
  arrived: boolean;
  reduced?: boolean;
}

/**
 * One place as a water gauge (Jalayantra, motion M34): the pale column is the peak with no pump,
 * the solid one starts at the same level and, when the pump arrives, drains over 900 ms to the
 * peak with it, its colour moving down the depth ramp as it goes. The tick is the 45 cm a bus
 * stops at. The drain is a CSS `scaleY` transition, so it costs the main thread nothing.
 */
export function PlaceGauge({
  leg,
  scaleCm,
  thresholdCm,
  arrived,
  reduced = false,
}: PlaceGaugeProps) {
  const before = leg.peakBefore?.depthCm ?? null;
  const after = leg.peakAfter?.depthCm ?? null;
  const label =
    before !== null && after !== null
      ? `Peak ${Math.round(before)} cm with no pump, ${Math.round(after)} cm with ${leg.pumpId}. ${depthBandLabel(after)}.`
      : "No depth series for this place.";
  const shown = arrived ? after : before;
  const animate = arrived && !reduced;

  return (
    <div
      role="img"
      aria-label={label}
      className="rounded-control border-line bg-ink relative h-16 w-7 shrink-0 overflow-hidden border"
    >
      {before !== null ? (
        <div
          aria-hidden="true"
          className="absolute inset-x-0 bottom-0 opacity-30"
          style={{
            height: `${gaugePercent(before, scaleCm)}%`,
            backgroundColor: cssVar(`--depth-${depthBand(before).key}`),
          }}
        />
      ) : null}
      {before !== null && shown !== null ? (
        <div
          aria-hidden="true"
          data-testid="gauge-fill"
          className="absolute inset-x-0 bottom-0 origin-bottom"
          style={{
            height: `${gaugePercent(before, scaleCm)}%`,
            transform: `scaleY(${arrived ? drainScale(before, after) : 1})`,
            backgroundColor: cssVar(`--depth-${depthBand(shown).key}`),
            transitionProperty: animate ? "transform, background-color" : "none",
            transitionDuration: `${DUR_MS.gaugeDrain}ms`,
            transitionTimingFunction: EASE_UI_CSS,
          }}
        />
      ) : null}
      <div
        aria-hidden="true"
        className="bg-text absolute inset-x-0 h-px opacity-70"
        style={{ bottom: `${gaugePercent(thresholdCm, scaleCm)}%` }}
      />
    </div>
  );
}

export interface PlaceGaugesProps {
  legs: readonly PumpLeg[];
  thresholdCm: number;
  /** Each pump's place in the dispatch order, which times its arrival (M33, M34). */
  order: ReadonlyMap<string, number>;
  clock?: GaugeClock | null;
  reduced?: boolean;
  /** The pump whose row was pressed: its road stays lit on the map, and the row reads pressed. */
  selectedPumpId?: string | null;
  /** Pressing a row presses it; pressing it again lets go (null). */
  onSelect?: (pumpId: string | null) => void;
  /**
   * The row under the pointer or keyboard focus, null when it leaves. Kept apart from the press:
   * a click always follows a hover and Enter always follows a focus, so a hover that selected
   * made every press a let-go.
   */
  onHover?: (pumpId: string | null) => void;
  className?: string;
}

/** Every place a pump goes, deepest benefit first, each with its gauge and its numbers. */
export function PlaceGauges({
  legs,
  thresholdCm,
  order,
  clock = null,
  reduced = false,
  selectedPumpId = null,
  onSelect,
  onHover,
  className,
}: PlaceGaugesProps) {
  const scale = gaugeScaleCm(legs);
  const ordered = [...legs].sort((a, b) => b.minutesSaved - a.minutesSaved);

  return (
    <ol className={cn("flex flex-col gap-1", className)} aria-label="Places the pumps go">
      {ordered.map((leg) => (
        <GaugeRow
          key={leg.pumpId}
          leg={leg}
          scaleCm={scale}
          thresholdCm={thresholdCm}
          order={order.get(leg.pumpId) ?? 0}
          clock={clock}
          reduced={reduced}
          selected={leg.pumpId === selectedPumpId}
          onSelect={onSelect}
          onHover={onHover}
        />
      ))}
    </ol>
  );
}

const ROLL_TIMING = { duration: DUR_MS.gaugeDrain, easing: EASE_UI_CSS };

/**
 * The handlers a pump row carries, shared by the gauges and the arrival timeline so the two lists
 * behave alike: hover and focus light the pump's road while they last, a press toggles it.
 */
export function rowPointerHandlers(
  pumpId: string,
  selected: boolean,
  onSelect?: (pumpId: string | null) => void,
  onHover?: (pumpId: string | null) => void,
) {
  return {
    onClick: () => onSelect?.(selected ? null : pumpId),
    onMouseEnter: () => onHover?.(pumpId),
    onMouseLeave: () => onHover?.(null),
    onFocus: () => onHover?.(pumpId),
    onBlur: () => onHover?.(null),
  };
}

function GaugeRow({
  leg,
  scaleCm,
  thresholdCm,
  order,
  clock,
  reduced,
  selected,
  onSelect,
  onHover,
}: {
  leg: PumpLeg;
  scaleCm: number;
  thresholdCm: number;
  order: number;
  clock: GaugeClock | null;
  reduced: boolean;
  selected: boolean;
  onSelect?: (pumpId: string | null) => void;
  onHover?: (pumpId: string | null) => void;
}) {
  const arrived = useArrived(order, clock, reduced);
  const minutes = Math.round(arrived ? leg.minutesAfter : leg.minutesBefore);

  return (
    <li>
      <button
        type="button"
        aria-pressed={selected}
        aria-label={`${leg.target.name}: ${Math.round(leg.minutesBefore)} min above ${thresholdCm} cm with no pump, ${Math.round(leg.minutesAfter)} min with ${leg.pumpId} from ${leg.depot.name}, ${formatMinutes(leg.etaMin)} away. Show its road on the map.`}
        {...rowPointerHandlers(leg.pumpId, selected, onSelect, onHover)}
        className={cn(
          "rounded-control flex min-h-11 w-full items-center gap-3 border p-2 text-left transition-colors duration-150",
          "focus-visible:ring-tide/60 focus-visible:ring-2 focus-visible:outline-none",
          selected ? "border-line-strong bg-well" : "hover:bg-well border-transparent",
        )}
      >
        <PlaceGauge
          leg={leg}
          scaleCm={scaleCm}
          thresholdCm={thresholdCm}
          arrived={arrived}
          reduced={reduced}
        />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5" aria-hidden="true">
          <span className="type-small text-text truncate font-medium">{leg.target.name}</span>
          <span className="num type-small text-text-2 flex items-baseline gap-1">
            <span className={cn("font-medium", arrived ? "text-tide" : "text-text")}>
              <NumberFlow
                value={minutes}
                locales="en-IN"
                suffix=" min"
                animated={!reduced && arrived}
                respectMotionPreference={false}
                transformTiming={ROLL_TIMING}
                spinTiming={ROLL_TIMING}
              />
            </span>
            <span>above {thresholdCm} cm</span>
            {arrived && minutes !== Math.round(leg.minutesBefore) ? (
              <span className="text-text-3">(was {Math.round(leg.minutesBefore)})</span>
            ) : null}
          </span>
          <span className="num type-micro text-text-3 truncate">
            {leg.depthBeforeCm === null ? "No depth series. " : null}
            {leg.pumpId}, {formatMinutes(leg.etaMin)} from {leg.depot.name}
          </span>
        </span>
      </button>
    </li>
  );
}

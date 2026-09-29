"use client";

import NumberFlow from "@number-flow/react";
import { useEffect, useMemo, useState } from "react";

import { Skeleton } from "@/components/varuna/skeleton";
import type { PumpMap, PumpPlan } from "@/lib/api/pumps";
import { DUR_MS, EASE_UI_CSS } from "@/lib/motion";
import { cn } from "@/lib/utils";

import { arriveMs } from "./dispatch-clock";
import type { GaugeClock } from "./place-gauges";

export interface PumpHeadlineProps {
  /** The plan the cycle wrote (`GET /v1/pumps`); arrives first and is enough for the figures. */
  plan: PumpPlan | null;
  loading?: boolean;
  /**
   * Each pump's place in the dispatch order (M33). With `clock`, the "with the pumps" figure
   * counts down by each pump's saving as that pump reaches its place on the map.
   */
  order?: ReadonlyMap<string, number>;
  /** The dispatch on screen; idle before Optimise. Null shows the plan's answer. */
  clock?: GaugeClock | null;
  reduced?: boolean;
  className?: string;
}

/**
 * Minutes as minutes. These are sums over places ("985 min above 45 cm at twelve places"), so
 * "16 h 25 min" would read as a duration nobody experiences.
 */
export function plainMinutes(minutes: number): string {
  return `${Math.round(minutes).toLocaleString("en-IN")} min`;
}

/** What the benefit is, in words, by the model that produced it (rule 6). */
export function benefitCaveat(model: string, emulator: PumpMap["emulator"]): string {
  if (model === "emulator" || model === "mixed") {
    const skill = emulator
      ? ` Flash-lite against the Twin: RMSE ${emulator.rmseCm} cm, CSI ${emulator.csi30cm} at 30 cm, ${emulator.nTrainingRuns} training runs.`
      : "";
    return (
      "Emulator estimate, lower bound: each pump is priced against the rain that fell on its own " +
      `streets, not water arriving from the sea or upstream.${skill}`
    );
  }
  return "Bathtub estimate: it ignores the water still arriving, so it overstates a pump at a place that is still filling.";
}

/** The model behind the plan's figures, read from its label until the map lands. */
export function inferModel(plan: PumpPlan): string {
  return plan.benefitLabel.toLowerCase().startsWith("flash-lite") ? "emulator" : "reduced_model";
}

/**
 * How many of `orders` have reached their place on the dispatch clock. One state change per
 * arrival, never per frame; every order under reduced motion or with no clock; none before
 * Optimise.
 */
export function useArrivals(
  orders: readonly number[],
  clock: GaugeClock | null,
  reduced: boolean,
): number {
  const sorted = useMemo(() => [...orders].sort((a, b) => a - b), [orders]);
  const [arrived, setArrived] = useState<{ key: string; count: number } | null>(null);
  const key = clock?.key ?? null;
  const startMs = clock?.startMs ?? null;
  const idle = Boolean(clock?.idle);
  useEffect(() => {
    if (idle || reduced || key === null || startMs === null) return;
    const timers = sorted.map((order, i) =>
      window.setTimeout(
        () => setArrived({ key, count: i + 1 }),
        Math.max(0, startMs + arriveMs(order) - performance.now()),
      ),
    );
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [sorted, key, startMs, reduced, idle]);
  if (idle) return 0;
  if (reduced || clock === null) return sorted.length;
  return arrived?.key === key ? arrived.count : 0;
}

const ROLL_TIMING = { duration: DUR_MS.gaugeDrain, easing: EASE_UI_CSS };

/**
 * What the plan buys, in the rail beside the dispatch map (Jalayantra): the minutes above 45 cm
 * at the places the pumps go with no pump, and with the pumps - a figure that starts at the
 * no-pump total and counts down by each pump's saving as that lorry reaches its place (M34, the
 * gauges' roll, summed). Then how many pumps the plan assigns and the place it helps most.
 * Every figure is the plan's; nothing is summed that the plan did not compute.
 */
export function PumpHeadline({
  plan,
  loading = false,
  order,
  clock = null,
  reduced = false,
  className,
}: PumpHeadlineProps) {
  const assignments = useMemo(() => plan?.assignments ?? [], [plan]);
  // Arrival order: the plan's order where the map gave one, else the plan's own index.
  const orders = useMemo(
    () => assignments.map((a, i) => order?.get(a.pumpId) ?? i),
    [assignments, order],
  );
  const arrivedCount = useArrivals(orders, clock, reduced);

  if (!plan) {
    return loading ? (
      <div className={cn("flex flex-col gap-3", className)} aria-busy="true">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-10 w-1/2" />
        <Skeleton className="h-10 w-1/2" />
        <Skeleton className="h-4 w-full" />
      </div>
    ) : null;
  }

  const before = assignments.reduce((sum, a) => sum + a.minutesBefore, 0);
  const after = assignments.reduce((sum, a) => sum + a.minutesAfter, 0);
  // Savings land in arrival order, so the figure passes through the totals a judge could check.
  const byArrival = assignments
    .map((a, i) => ({ saved: a.minutesBefore - a.minutesAfter, order: orders[i] }))
    .sort((x, y) => x.order - y.order);
  const withPumps =
    arrivedCount >= byArrival.length
      ? after
      : before - byArrival.slice(0, arrivedCount).reduce((sum, x) => sum + x.saved, 0);
  const settled = arrivedCount >= byArrival.length && byArrival.length > 0;
  const sent = !clock?.idle;
  const helped = assignments.reduce<(typeof assignments)[number] | null>(
    (best, a) => (best === null || a.minutesSaved > best.minutesSaved ? a : best),
    null,
  );
  const unserved = plan.unassigned.length;

  return (
    <section aria-label="What the plan buys" className={cn("flex flex-col gap-3", className)}>
      <p className="type-small text-text-2">
        Minutes above {plan.thresholdCm} cm where the pumps go
      </p>
      <dl className="grid grid-cols-[auto_1fr] items-baseline gap-x-4 gap-y-1">
        <dt className="type-small text-text-3">With no pump</dt>
        <dd className="num font-display text-text text-right text-[2rem] leading-none font-semibold">
          {plainMinutes(before)}
        </dd>
        <dt className="type-small text-text-3">With the pumps</dt>
        <dd
          className={cn(
            "num font-display text-right text-[2rem] leading-none font-semibold",
            settled ? "text-tide" : "text-text",
          )}
        >
          {sent ? (
            <NumberFlow
              value={Math.round(withPumps)}
              locales="en-IN"
              suffix=" min"
              animated={!reduced}
              respectMotionPreference={false}
              transformTiming={ROLL_TIMING}
              spinTiming={ROLL_TIMING}
            />
          ) : (
            <span className="type-body text-text-3 font-sans font-normal">Not sent</span>
          )}
        </dd>
      </dl>
      <p className="num type-small text-text-2">
        {assignments.length} of {plan.pumps.length} pumps assigned;{" "}
        {plainMinutes(plan.totalMinutesSaved)} avoided.
        {unserved > 0
          ? ` ${unserved} more place${unserved === 1 ? " crosses" : "s cross"} ${plan.thresholdCm} cm, no pump left.`
          : null}
      </p>
      {helped ? (
        <p className="type-small text-text-2">
          Helped most: <span className="text-text font-medium">{helped.targetName}</span>
          <span className="num">
            , {plainMinutes(helped.minutesBefore)} to {plainMinutes(helped.minutesAfter)} (
            {helped.pumpId}).
          </span>
        </p>
      ) : (
        <p className="type-small text-text-3">No place gains from a pump on this cycle.</p>
      )}
    </section>
  );
}

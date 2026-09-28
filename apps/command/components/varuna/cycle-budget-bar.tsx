"use client";

import { motion } from "motion/react";

import { formatMs } from "@/lib/format";
import { useMotionPref } from "@/lib/motion";
import { cn } from "@/lib/utils";

/** The cycle stages in order (SPEC.md section 11.11); Twin and Flash run in parallel. */
export const STAGE_IDS = ["decode", "sky", "twin", "flash", "pulse", "products"] as const;
export type StageId = (typeof STAGE_IDS)[number];

export const STAGE_LABELS: Record<StageId, string> = {
  decode: "Decode",
  sky: "Sky",
  twin: "Twin",
  flash: "Flash",
  pulse: "Pulse",
  products: "Products",
};

/**
 * Budget per stage in milliseconds. The live cycle is 15 s: Sky 5 s, Twin 8 s and the rest share
 * 2 s (decode, Flash, Pulse and products get 500 ms each here so the bar has a width to draw).
 */
export const STAGE_BUDGET_MS: Record<StageId, number> = {
  decode: 500,
  sky: 5000,
  twin: 8000,
  flash: 500,
  pulse: 500,
  products: 500,
};

export const CYCLE_BUDGET_MS = 15_000;

export interface StageTiming {
  id: StageId;
  /** Milliseconds taken; null while the stage has not reported. */
  ms: number | null;
}

export interface CycleBudgetBarProps {
  /** Timings of the cycle in progress or the last published run; empty or undefined before one. */
  stages?: StageTiming[];
  /** Total shown at the right; defaults to the sum of the reported stages. */
  totalMs?: number | null;
  className?: string;
  /** The stage running now, named in the header while a live cycle is in progress. */
  running?: StageId | null;
  /**
   * One line and the bar, for the time bar's 96 px: the per-stage list folds into the header,
   * which names only the stages that have reported.
   */
  compact?: boolean;
}

/** One meter per stage, width by budget share, filled to the fraction of budget used (M21). */
function BudgetMeters({
  byId,
  preset,
  className,
}: {
  byId: ReadonlyMap<StageId, number | null>;
  preset: ReturnType<typeof useMotionPref>["preset"];
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label="Cycle stage timings"
      className={cn("rounded-chip bg-well flex w-full gap-px overflow-hidden", className)}
    >
      {STAGE_IDS.map((id) => {
        const budget = STAGE_BUDGET_MS[id];
        const ms = byId.get(id) ?? null;
        const fraction = ms === null ? 0 : Math.min(1, ms / budget);
        const over = ms !== null && ms > budget;
        return (
          <div
            key={id}
            role="meter"
            aria-label={STAGE_LABELS[id]}
            aria-valuemin={0}
            aria-valuemax={budget}
            aria-valuenow={ms === null ? 0 : Math.min(budget, ms)}
            aria-valuetext={ms === null ? "Not run" : `${formatMs(ms)} of ${formatMs(budget)}`}
            className="relative h-full min-w-1 overflow-hidden"
            style={{ flexGrow: budget, flexBasis: 0 }}
          >
            <motion.div
              className={cn("h-full", over ? "bg-status-degraded" : "bg-tide")}
              initial={false}
              animate={{ width: `${fraction * 100}%` }}
              transition={preset("M21").transition}
            />
          </div>
        );
      })}
    </div>
  );
}

/**
 * Segmented bar of the five-minute cycle: one segment per stage, width by budget share, filled
 * to the fraction of budget used (M21: 200 ms width tween, instant under reduced motion). Over
 * budget fills in the degraded colour. Without a cycle it reads "No cycle yet".
 */
export function CycleBudgetBar({
  stages,
  totalMs,
  className,
  running = null,
  compact = false,
}: CycleBudgetBarProps) {
  const { preset } = useMotionPref();
  const byId = new Map<StageId, number | null>((stages ?? []).map((s) => [s.id, s.ms]));
  const hasCycle = (stages ?? []).some((s) => s.ms !== null);
  const reportedTotal = (stages ?? []).reduce((sum, s) => sum + (s.ms ?? 0), 0);
  const total = totalMs ?? (hasCycle ? reportedTotal : null);
  const reported = STAGE_IDS.filter((id) => (byId.get(id) ?? null) !== null);

  if (compact) {
    return (
      <div className={cn("space-y-1", className)}>
        <p className="num type-micro text-text-2 truncate" aria-live="polite">
          {running ? `${STAGE_LABELS[running]} running` : null}
          {running && reported.length > 0 ? ". " : null}
          {reported.length > 0
            ? reported.map((id) => `${STAGE_LABELS[id]} ${formatMs(byId.get(id))}`).join(", ")
            : running
              ? null
              : "No cycle yet"}
          {hasCycle ? `. ${formatMs(total)} of ${formatMs(CYCLE_BUDGET_MS)}` : null}
        </p>
        <BudgetMeters byId={byId} preset={preset} className="h-1.5" />
      </div>
    );
  }

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <p className="type-small text-text">Cycle budget</p>
        <p className="num type-small text-text-2">
          {hasCycle ? `${formatMs(total)} of ${formatMs(CYCLE_BUDGET_MS)}` : "No cycle yet"}
        </p>
      </div>
      <BudgetMeters byId={byId} preset={preset} className="h-3" />
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        {STAGE_IDS.map((id) => {
          const ms = byId.get(id) ?? null;
          return (
            <li key={id} className="type-micro flex items-baseline gap-1.5">
              <span className="text-text-2">{STAGE_LABELS[id]}</span>
              <span className="num text-text-3">{ms === null ? "—" : formatMs(ms)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

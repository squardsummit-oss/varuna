"use client";

import { motion } from "motion/react";

import { Button } from "@/components/ui/button";
import { AgreementBar } from "@/components/varuna/agreement-bar";
import { EmptyState } from "@/components/varuna/empty-state";
import { Skeleton } from "@/components/varuna/skeleton";
import { formatCmPrecise, formatCmSigned, formatMassBalance, formatMs } from "@/lib/format";
import type { PhysicsCheckResult } from "@/lib/api/whatif";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { DUR, tween } from "@/lib/motion";
import { cn } from "@/lib/utils";

export interface PhysicsCheckPanelProps {
  result: PhysicsCheckResult | null;
  running: boolean;
  /** The endpoint's own refusal or failure, in its words (SPEC.md 6.8). */
  error: string | null;
  /** Rows to list; the lab shows them all and the console drawer fewer. */
  maxRows?: number;
  className?: string;
}

/**
 * What the check could not compare, in words: the pump plan (the Twin has no pump sink, so both
 * models ran without it) and cleaned pipes the 990 m window does not hold. A check that agrees
 * about a lever it never ran has to say so beside the agreement (rule 6).
 *
 * On the tide path (`runsOnTwin`) the check runs nothing - the full-city Twin job is the answer -
 * so no line may say a model "ran without" a lever; the pump line then speaks of the Twin run.
 */
export function uncheckedLines(result: PhysicsCheckResult): string[] {
  const lines: string[] = [];
  if (result.leversNotChecked.includes("pump_plan")) {
    lines.push(
      result.runsOnTwin
        ? "The pump plan is not in the Twin run: the Twin has no pump sink on the street, so the pumps are priced by the emulator only, as a lower bound."
        : "The pump plan is not in this check: the Twin has no pump sink on the street, so both models ran without it.",
    );
  }
  if (result.leversNotChecked.includes("clean_top")) {
    lines.push("The top 14 pipes by blockage are not in the full-city Twin run.");
  }
  const cleaned = result.cleanedEdges;
  const inside = result.window.cleanedEdgesInside;
  if (!result.runsOnTwin && cleaned > 0 && inside === 0) {
    lines.push(
      `None of the ${cleaned.toLocaleString("en-IN")} cleaned pipes is inside the window, so the cleaning changes nothing the check can see.`,
    );
  } else if (!result.runsOnTwin && cleaned > 0 && inside < cleaned) {
    lines.push(
      `${inside.toLocaleString("en-IN")} of the ${cleaned.toLocaleString("en-IN")} cleaned pipes are inside the window; the rest change nothing the check can see.`,
    );
  }
  return lines;
}

/**
 * The answer to "Physics check" (SPEC.md 7.7, P7.8): section 7.7's sentence as a bar, then the
 * junctions it was measured at, each as the emulator's change beside the Twin's.
 *
 * Changes and not levels, because that is what the endpoint compares: a 990 m crop's absolute
 * depth is not the city's, and printing a level from it would publish a number the whole-AOI run
 * disagrees with. The disagreement is always printed, and so is the check's own cost against its
 * 10 s budget - a check that took longer than the budget says so rather than looking instant.
 */
export function PhysicsCheckPanel({
  result,
  running,
  error,
  maxRows = 6,
  className,
}: PhysicsCheckPanelProps) {
  if (running) {
    return (
      <div className={cn("space-y-2", className)} aria-busy="true">
        <p className="type-small text-text-3">
          Running the Twin twice on a window around the worst junction
        </p>
        <Skeleton className="h-2 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }
  if (error) {
    return <EmptyState title="Physics check refused" description={error} className={className} />;
  }
  if (!result) {
    return <AgreementBar result={null} className={className} />;
  }
  if (result.runsOnTwin) {
    // A tide scenario: the emulator has no answer to check, so the endpoint compares nothing and
    // says where the answer is. Printed as that, not as a disagreement of null against a
    // tolerance, and not as "outside tolerance" (`agrees` is null on this path).
    const expected = result.twinJob?.expectedMs;
    const tail = result.twinJob?.cached
      ? " This scenario is already answered for this run."
      : expected
        ? ` The full-city run took ${formatMs(expected)} when this cycle was baked.`
        : "";
    const unchecked = uncheckedLines(result);
    return (
      <EmptyState
        title="Runs on the Twin"
        description={`${result.summary}${tail}${unchecked.length ? ` ${unchecked.join(" ")}` : ""}`}
        className={className}
      />
    );
  }
  if (result.maxDiffCm === null) {
    return (
      <EmptyState
        title="Nothing to compare"
        description={`${result.summary}. Junctions outside the window: ${result.outside.join(", ") || "none"}.`}
        className={className}
      />
    );
  }

  const rows = result.hotspots.slice(0, maxRows);
  const overBudget = result.ms > result.budgetMs;
  return (
    <div className={cn("space-y-3", className)}>
      <AgreementBar
        result={{
          maxDiffCm: result.maxDiffCm,
          atHotspot: result.maxDiffHotspot ?? undefined,
          physicsMs: result.ms,
        }}
        toleranceCm={result.toleranceCm}
      />
      <table className="type-small w-full">
        <caption className="sr-only">
          Change in peak depth per junction, emulator against Twin
        </caption>
        <thead>
          <tr className="text-text-3 type-micro">
            <th scope="col" className="py-1 text-left font-normal">
              Junction
            </th>
            <th scope="col" className="py-1 text-right font-normal">
              Emulator
            </th>
            <th scope="col" className="py-1 text-right font-normal">
              Twin
            </th>
            <th scope="col" className="py-1 text-right font-normal">
              Difference
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.hotspotId} className="border-line border-t">
              <th scope="row" className="text-text py-1.5 text-left font-normal">
                {row.name}
              </th>
              <td className="num text-text-2 py-1.5 text-right">
                {formatCmSigned(row.emulatorDeltaCm)}
              </td>
              <td className="num text-text-2 py-1.5 text-right">
                {formatCmSigned(row.twinDeltaCm)}
              </td>
              <td className="num text-text py-1.5 text-right">{formatCmPrecise(row.diffCm)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="type-micro text-text-3">
        The Twin ran twice on a <span className="num">{result.window.sizeM}</span> m window around{" "}
        {result.window.centre}, with{" "}
        <span className="num">{result.window.edges.toLocaleString("en-IN")}</span> pipes, in{" "}
        <span className="num">{formatMs(result.ms)}</span>
        {overBudget ? ", over" : ", inside"} the{" "}
        <span className="num">{formatMs(result.budgetMs)}</span> budget. Mass balance{" "}
        <span className="num">{formatMassBalance(result.massBalance.scenario)}</span> on the
        scenario run.
        {result.outside.length > 0
          ? ` Not in the window, so not checked: ${result.outside.join(", ")}.`
          : ""}
      </p>
      {uncheckedLines(result).map((line) => (
        <p key={line} className="type-micro text-text-2">
          {line}
        </p>
      ))}
    </div>
  );
}

export interface TwinRunProgressProps {
  /** "Twin 17 of 36 steps, about 40 s left", from the job's own numbers. */
  line: string | null;
  /** Share of the Twin's output steps done, 0 to 1; null before the Twin starts stepping. */
  fraction: number | null;
  running: boolean;
  /** The job's refusal, failure or cancellation, in the server's words. */
  error: string | null;
  cancelled: boolean;
  onCancel?: () => void;
  className?: string;
}

/**
 * Motion M31: the full-city Twin working on a tide question, as a bar that fills one output step
 * at a time (200 ms width tween; a jump to each step under reduced motion), with its count and
 * the time left, and Cancel while it runs. The answer it produces replaces the emulator's.
 */
export function TwinRunProgress({
  line,
  fraction,
  running,
  error,
  cancelled,
  onCancel,
  className,
}: TwinRunProgressProps) {
  const reducedMotion = usePrefersReducedMotion();
  if (!running && !error && fraction === null) return null;
  const percent = Math.round((fraction ?? 0) * 100);
  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <p className="type-small text-text font-medium">
          {cancelled ? "Twin run cancelled" : error ? "Twin run stopped" : "Tide on the Twin"}
        </p>
        {running && onCancel ? (
          <Button variant="outline" size="sm" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
      </div>
      <div
        role="progressbar"
        aria-label="Full-city Twin run"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={line ?? undefined}
        className="bg-well h-1.5 w-full overflow-hidden rounded-full"
      >
        <motion.div
          className={cn("h-full rounded-full", error ? "bg-text-3" : "bg-tide")}
          initial={false}
          animate={{ width: `${percent}%` }}
          transition={reducedMotion ? { duration: 0 } : tween(DUR.budgetFill)}
        />
      </div>
      {line ? (
        <p className="type-micro text-text-2 num" aria-live="polite">
          {line}
        </p>
      ) : null}
      {error ? <p className="type-micro text-text-3">{error}</p> : null}
    </div>
  );
}

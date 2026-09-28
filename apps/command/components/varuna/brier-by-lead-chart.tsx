"use client";

/**
 * Brier score by lead time for the members' exceedance probability (SPEC.md 7.10, 11.12).
 *
 * The probability is the fraction of Sky members above the threshold, as the run's `P(> 20 mm/h)`
 * product is built. Two references sit beside it: persistence's yes-or-no, and the lead's own
 * observed base rate (`o * (1 - o)`), which is what a forecaster who knew only how often it rained
 * would score. Every value is served by `/v1/verification/rain-skill`; a lead with no member cube
 * has no Brier and is a gap. No animation (section 8 has no row for it).
 */

import { useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartLine } from "lucide-react";

import { EmptyState } from "@/components/varuna/empty-state";
import { LEAD_TICKS_MIN } from "@/components/varuna/skill-by-lead-chart";
import type { RainScope } from "@/lib/api/verification-rain";
import { chartColor, cssVar } from "@/lib/ramps";
import { cn } from "@/lib/utils";

export interface BrierRow {
  leadMin: number;
  brier: number | null;
  persistence: number | null;
  climatology: number | null;
  nCycles: number;
}

/** One scope's Brier by lead at one threshold. Pure; exported for tests. */
export function brierByLeadRows(scope: RainScope, thresholdMmH: number): BrierRow[] {
  const key = String(thresholdMmH);
  return scope.byLead.map((row) => {
    const cell = row.thresholds[key];
    return {
      leadMin: row.leadMin,
      brier: cell?.brier ?? null,
      persistence: cell?.brierPersistence ?? null,
      climatology: cell?.brierClimatology ?? null,
      nCycles: row.nCycles,
    };
  });
}

/** Upper ends the Brier axis may take, so the ticks stay round numbers. */
const BRIER_AXIS_TOPS = [0.05, 0.1, 0.25, 0.5, 0.75, 1] as const;

/**
 * The Brier axis runs from 0 to the smallest round top above every served value, rather than a
 * fixed 0 to 1: on the radar domain every score sits between 0.01 and 0.15, and a 0-to-1 axis
 * would flatten the three lines onto the floor. The top is taken from the served numbers of all
 * three series; it never drops below 0.05 or rises above 1, the score's own range. Pure; exported
 * for tests.
 */
export function brierAxis(rows: readonly BrierRow[]): { top: number; ticks: number[] } {
  let most = 0;
  for (const row of rows) {
    for (const value of [row.brier, row.persistence, row.climatology]) {
      if (value !== null && value > most) most = value;
    }
  }
  const top = BRIER_AXIS_TOPS.find((candidate) => candidate >= most) ?? 1;
  const ticks = [0, 1, 2, 3, 4].map((i) => Number(((top * i) / 4).toFixed(4)));
  return { top, ticks };
}

const three = (value: number | null) => (value === null ? "not scored" : value.toFixed(3));
const axisTick = (value: number) => (value === 0 ? "0" : String(value));

export interface BrierByLeadChartProps {
  rows: readonly BrierRow[];
  thresholdMmH: number;
  height?: number;
  className?: string;
}

export function BrierByLeadChart({
  rows,
  thresholdMmH,
  height = 180,
  className,
}: BrierByLeadChartProps) {
  const color = chartColor(0);
  const naive = cssVar("--naive");
  const reference = cssVar("--text-3");
  const scored = useMemo(() => rows.filter((row) => row.brier !== null), [rows]);
  const axis = useMemo(() => brierAxis(rows), [rows]);

  if (scored.length === 0) {
    return (
      <EmptyState
        size="sm"
        icon={ChartLine}
        title="No Brier score at this threshold"
        description="The runs keep no member cube to take an exceedance fraction from. Re-bake with make bake to score it."
      />
    );
  }

  const first = scored[0]!;
  const summary =
    `Brier score at ${thresholdMmH} mm/h by lead, lower is better: at +${first.leadMin} min ` +
    `${three(first.brier)} against persistence ${three(first.persistence)} and the base rate ` +
    `${three(first.climatology)}; ${scored.length} leads scored.`;

  return (
    <figure className={cn("m-0 flex flex-col gap-2", className)} data-slot="brier-by-lead-chart">
      <div role="img" aria-label={summary} style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={[...rows]} margin={{ top: 8, right: 16, bottom: 18, left: 4 }}>
            <CartesianGrid stroke={cssVar("--line")} strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="leadMin"
              type="number"
              domain={[0, 180]}
              ticks={[...LEAD_TICKS_MIN].filter((tick) => tick % 30 === 0)}
              tick={{ fill: cssVar("--text-3"), fontSize: 12 }}
              tickFormatter={(value: number) => (value > 0 ? `+${value}` : "0")}
              tickLine={false}
              axisLine={{ stroke: cssVar("--line") }}
              label={{
                value: "Lead time (min)",
                position: "insideBottom",
                offset: -12,
                fill: cssVar("--text-3"),
                fontSize: 12,
              }}
            />
            <YAxis
              width={56}
              domain={[0, axis.top]}
              ticks={axis.ticks}
              tick={{ fill: cssVar("--text-3"), fontSize: 12 }}
              tickFormatter={axisTick}
              tickLine={false}
              axisLine={{ stroke: cssVar("--line") }}
              label={{
                value: "Brier score",
                angle: -90,
                position: "insideLeft",
                style: { textAnchor: "middle" },
                fill: cssVar("--text-3"),
                fontSize: 12,
              }}
            />
            <Tooltip
              isAnimationActive={false}
              cursor={{ stroke: cssVar("--line-strong") }}
              contentStyle={{
                background: cssVar("--deep"),
                border: `1px solid ${cssVar("--line")}`,
                borderRadius: 8,
                fontSize: 12,
              }}
              labelFormatter={(value) => `+${String(value)} min`}
              formatter={(value) => (typeof value === "number" ? value.toFixed(3) : "not scored")}
            />
            <Line
              dataKey="climatology"
              name="Base rate"
              type="linear"
              stroke={reference}
              strokeWidth={1.5}
              strokeDasharray="1 4"
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
            <Line
              dataKey="persistence"
              name="Persistence"
              type="linear"
              stroke={naive}
              strokeWidth={2}
              strokeDasharray="6 4"
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
            <Line
              dataKey="brier"
              name="Members' probability"
              type="linear"
              stroke={color}
              strokeWidth={2}
              dot={false}
              connectNulls={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="type-micro text-text-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="rounded-chip h-0.5 w-4 shrink-0"
            style={{ background: color }}
          />
          Members&apos; probability
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-0 w-4 shrink-0 border-t-2 border-dashed"
            style={{ borderColor: naive }}
          />
          Persistence, yes or no
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-0 w-4 shrink-0 border-t-2 border-dotted"
            style={{ borderColor: reference }}
          />
          The lead&apos;s own base rate
        </span>
      </figcaption>
    </figure>
  );
}

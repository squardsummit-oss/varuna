"use client";

/**
 * Detection against the depth threshold (SPEC.md 7.10: "charts have axes, units, and the
 * ground-truth count").
 *
 * The pins record waterlogging, not a depth, so `services/verify` scores the event at 5, 15 and
 * 30 cm rather than pretending one threshold is the answer (ADR-0029). This draws that sweep as
 * it is served by `/v1/verification`: CSI, POD and FAR on a 0-to-1 axis against the threshold in
 * centimetres, with the number of sourced pins the scores were computed on. Nothing here is
 * computed; a threshold whose score has no denominator is a gap in the line, not a zero.
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
import { Skeleton } from "@/components/varuna/skeleton";
import { chartColor, cssVar } from "@/lib/ramps";
import { cn } from "@/lib/utils";

/** One served threshold row, as the chart needs it. */
export interface ThresholdPoint {
  thresholdCm: number;
  csi: number | null;
  pod: number | null;
  far: number | null;
}

export interface VerificationThresholdChartProps {
  points: readonly ThresholdPoint[];
  /** Sourced pins inside the forecast window: the sample every point is scored on. */
  groundTruthCount: number | null;
  loading?: boolean;
  error?: string | null;
  height?: number;
  className?: string;
}

const SERIES = [
  { key: "csi", name: "CSI", color: chartColor(0), note: "higher is better" },
  { key: "pod", name: "POD", color: chartColor(1), note: "higher is better" },
  { key: "far", name: "FAR", color: chartColor(2), note: "lower is better" },
] as const;

const score = (value: number | null) => (value === null ? "no denominator" : value.toFixed(2));

export function VerificationThresholdChart({
  points,
  groundTruthCount,
  loading = false,
  error = null,
  height = 220,
  className,
}: VerificationThresholdChartProps) {
  const rows = useMemo(() => [...points].sort((a, b) => a.thresholdCm - b.thresholdCm), [points]);

  if (loading) return <Skeleton className={cn("h-[220px] w-full", className)} />;
  if (error) {
    return <EmptyState icon={ChartLine} title="Scores did not load" description={error} />;
  }
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={ChartLine}
        title="Not scored yet"
        description="Bake the event and the sweep draws itself from its runs."
      />
    );
  }

  const summary =
    `Detection against the depth threshold, on ${groundTruthCount ?? "an unknown number of"} ` +
    `sourced pins: ` +
    rows
      .map(
        (r) => `${r.thresholdCm} cm CSI ${score(r.csi)}, POD ${score(r.pod)}, FAR ${score(r.far)}`,
      )
      .join("; ") +
    ".";

  return (
    <figure className={cn("m-0 flex flex-col gap-2", className)} data-slot="threshold-chart">
      <div role="img" aria-label={summary} style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 20, left: 4 }}>
            <CartesianGrid stroke={cssVar("--line")} strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="thresholdCm"
              type="number"
              domain={["dataMin", "dataMax"]}
              ticks={rows.map((r) => r.thresholdCm)}
              tick={{ fill: cssVar("--text-3"), fontSize: 12 }}
              tickLine={false}
              axisLine={{ stroke: cssVar("--line") }}
              label={{
                value: "Depth threshold (cm)",
                position: "insideBottom",
                offset: -12,
                fill: cssVar("--text-3"),
                fontSize: 12,
              }}
            />
            <YAxis
              width={56}
              domain={[0, 1]}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tick={{ fill: cssVar("--text-3"), fontSize: 12 }}
              tickLine={false}
              axisLine={{ stroke: cssVar("--line") }}
              label={{
                value: "Score (0 to 1)",
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
              labelFormatter={(value) => `${value} cm`}
              formatter={(value) =>
                typeof value === "number" ? value.toFixed(2) : "no denominator"
              }
            />
            {SERIES.map((series) => (
              <Line
                key={series.key}
                dataKey={series.key}
                name={series.name}
                type="linear"
                stroke={series.color}
                strokeWidth={2}
                dot={{ r: 3, fill: series.color, stroke: cssVar("--ink") }}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="type-micro text-text-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        {SERIES.map((series) => (
          <span key={series.key} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="rounded-chip h-0.5 w-4 shrink-0"
              style={{ background: series.color }}
            />
            {series.name}, {series.note}
          </span>
        ))}
        <span className="num text-text-3">
          {groundTruthCount === null
            ? "Ground-truth count not served"
            : `n = ${groundTruthCount} sourced ${groundTruthCount === 1 ? "pin" : "pins"} in the window`}
        </span>
      </figcaption>
    </figure>
  );
}

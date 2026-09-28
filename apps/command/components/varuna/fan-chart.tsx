"use client";

import { useMemo } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { LineChart } from "lucide-react";

import { EmptyState } from "@/components/varuna/empty-state";
import { Skeleton } from "@/components/varuna/skeleton";
import { formatIst, formatLead } from "@/lib/format";
import { bandFill, chartColor, cssVar } from "@/lib/ramps";
import { cn } from "@/lib/utils";

/** One forecast step: the ensemble's median and its 10th-90th percentile band at that lead. */
export interface FanChartPoint {
  /** When the step is valid (ISO 8601 with the +05:30 offset). */
  validTs: string;
  /** Minutes from the cycle time; negative for the observed half once Phase 6 has one. */
  leadMin: number;
  p10: number;
  p50: number;
  p90: number;
}

/** One ensemble member drawn as a thin line behind the band. */
export interface FanChartMember {
  /** Stable key, e.g. the member index. */
  id: string | number;
  /** One value per entry of `points`, in the same order; a shorter list stops early. */
  values: readonly number[];
}

export interface FanChartProps {
  /** The series, in time order. An empty list renders the empty state, never a flat zero line. */
  points: readonly FanChartPoint[];
  /** What is plotted, e.g. "Rain rate". Names the y axis and opens the accessible summary. */
  quantity: string;
  /** Unit of every value, e.g. "mm/h" or "cm". Printed on the axis and beside every number. */
  unit: string;
  /** Ensemble members drawn behind the band; omit to draw the band alone. */
  members?: readonly FanChartMember[];
  /** Dashed vertical marker, e.g. 90 for the lead where confidence decays. */
  markerLeadMin?: number | null;
  /** Copy beside the marker; kept short, it sits inside the plot. */
  markerLabel?: string;
  /** Line and band colour: a token hex, from `chartColor()` or a ramp. Defaults to chart 1. */
  colorHex?: string;
  /** Plot height in pixels; the width always fills the container. */
  height?: number;
  /** Prints a value with its unit. Defaults to one decimal place plus the unit. */
  formatValue?: (value: number) => string;
  loading?: boolean;
  /** What went wrong and what to do about it, in the API's own words; renders the error state. */
  error?: string | null;
  emptyTitle?: string;
  emptyDescription?: string;
  className?: string;
}

/** Row handed to Recharts: the band is a range, so one Area spans p10 to p90. */
interface ChartRow {
  leadMin: number;
  validTs: string;
  p50: number;
  band: [number, number];
  /** `m0`, `m1`, ... one per member. */
  [member: string]: number | string | [number, number];
}

const DEFAULT_HEIGHT = 200;

/** Ticks every 30 minutes across whatever window the series covers. */
const TICK_MIN = 30;

const oneDecimal = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

/** "+30", "0", "-60": the axis is already labelled "lead time (min)", so ticks stay bare. */
function formatLeadTick(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}

function memberKey(index: number): string {
  return `m${index}`;
}

/**
 * The ensemble fan chart (SPEC.md section 6.6): the p10-p90 band at 20 % opacity of the line
 * colour, the median as the line, and - when the caller has them - every member as a thin line
 * behind both, so the ensemble's own disagreement is visible rather than smoothed away.
 *
 * Colour comes from the chart tokens and the band opacity from `BAND_OPACITY`, so a fan chart of
 * rain and a fan chart of depth read the same way (SPEC.md section 6.2). Nothing animates: the
 * motion catalogue (section 8) has no row for a chart drawing itself in, so Recharts' animation is
 * off for every series rather than switched by preference.
 *
 * Colour is never the only carrier of meaning: the legend under the plot names each series, the
 * tooltip prints the numbers with their unit, and the accessible summary states the peak.
 */
export function FanChart({
  points,
  quantity,
  unit,
  members,
  markerLeadMin = null,
  markerLabel,
  colorHex,
  height = DEFAULT_HEIGHT,
  formatValue,
  loading = false,
  error = null,
  emptyTitle = "No forecast yet",
  emptyDescription = "Press Play on the replay, or Compute live.",
  className,
}: FanChartProps) {
  const color = colorHex ?? chartColor(0);
  const memberColor = cssVar("--chart-2");
  const format = useMemo(
    () => formatValue ?? ((value: number) => `${oneDecimal.format(value)} ${unit}`),
    [formatValue, unit],
  );

  const rows = useMemo<ChartRow[]>(
    () =>
      points.map((point, i) => {
        const row: ChartRow = {
          leadMin: point.leadMin,
          validTs: point.validTs,
          p50: point.p50,
          band: [point.p10, point.p90],
        };
        (members ?? []).forEach((member, m) => {
          const value = member.values[i];
          if (value !== undefined) row[memberKey(m)] = value;
        });
        return row;
      }),
    [members, points],
  );

  const byLead = useMemo(() => new Map(rows.map((row) => [row.leadMin, row])), [rows]);

  const ticks = useMemo(() => {
    if (rows.length === 0) return [];
    const first = rows[0]!.leadMin;
    const last = rows[rows.length - 1]!.leadMin;
    const start = Math.ceil(first / TICK_MIN) * TICK_MIN;
    const out: number[] = [];
    for (let t = start; t <= last; t += TICK_MIN) out.push(t);
    return out;
  }, [rows]);

  /** The peak of the median, so the summary and the aria label quote a real number. */
  const peak = useMemo(() => {
    if (points.length === 0) return null;
    return points.reduce((best, point) => (point.p50 > best.p50 ? point : best), points[0]!);
  }, [points]);

  if (loading) {
    return (
      <div className={cn("flex flex-col gap-2", className)}>
        <div style={{ height }}>
          <Skeleton className="h-full w-full rounded-control" />
        </div>
        <Skeleton className="h-3 w-40" />
      </div>
    );
  }

  if (error) {
    return (
      <ChartShell height={height} className={className}>
        <EmptyState size="sm" icon={LineChart} title="Forecast unavailable" description={error} />
      </ChartShell>
    );
  }

  if (points.length === 0) {
    return (
      <ChartShell height={height} className={className}>
        <EmptyState size="sm" icon={LineChart} title={emptyTitle} description={emptyDescription} />
      </ChartShell>
    );
  }

  const memberCount = members?.length ?? 0;
  const summary =
    peak === null
      ? `${quantity} over ${points.length} steps.`
      : `${quantity}: median and the 10th to 90th percentile band over ${points.length} steps to ${formatLead(
          points[points.length - 1]!.leadMin,
        )}. The median peaks at ${format(peak.p50)} at ${formatIst(peak.validTs)} IST (${formatLead(
          peak.leadMin,
        )}), where the band runs ${format(peak.p10)} to ${format(peak.p90)}.`;

  return (
    <figure className={cn("m-0 flex flex-col gap-2", className)}>
      <div role="img" aria-label={summary} style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 20, left: 4 }}>
            <CartesianGrid stroke={cssVar("--line")} strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="leadMin"
              type="number"
              domain={["dataMin", "dataMax"]}
              ticks={ticks}
              tickFormatter={formatLeadTick}
              tick={{ fill: cssVar("--text-3"), fontSize: 12 }}
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
              tick={{ fill: cssVar("--text-3"), fontSize: 12 }}
              tickLine={false}
              axisLine={{ stroke: cssVar("--line") }}
              label={{
                value: `${quantity} (${unit})`,
                angle: -90,
                position: "insideLeft",
                style: { textAnchor: "middle" },
                fill: cssVar("--text-3"),
                fontSize: 12,
              }}
            />
            <Tooltip
              cursor={{ stroke: cssVar("--line-strong") }}
              isAnimationActive={false}
              content={(tooltip) => {
                const { active, label } = tooltip as { active?: boolean; label?: unknown };
                if (!active || typeof label !== "number") return null;
                const row = byLead.get(label);
                if (!row) return null;
                return (
                  <FanTooltip
                    validTs={row.validTs}
                    leadMin={row.leadMin}
                    p50={row.p50}
                    p10={row.band[0]}
                    p90={row.band[1]}
                    format={format}
                  />
                );
              }}
            />

            {/* Band first, so it sits behind the members and the median. */}
            <Area
              dataKey="band"
              name="p10 to p90"
              stroke="none"
              fill={bandFill(color)}
              isAnimationActive={false}
              activeDot={false}
            />

            {Array.from({ length: memberCount }, (_, m) => (
              <Line
                key={memberKey(m)}
                dataKey={memberKey(m)}
                type="monotone"
                stroke={memberColor}
                strokeWidth={1}
                strokeOpacity={0.35}
                dot={false}
                activeDot={false}
                isAnimationActive={false}
              />
            ))}

            <Line
              dataKey="p50"
              name="Median"
              type="monotone"
              stroke={color}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 3, fill: color, stroke: cssVar("--ink") }}
              isAnimationActive={false}
            />

            {markerLeadMin === null ? null : (
              <ReferenceLine
                x={markerLeadMin}
                stroke={cssVar("--line-strong")}
                strokeDasharray="4 4"
                label={
                  markerLabel
                    ? {
                        value: markerLabel,
                        position: "insideTopRight",
                        fill: cssVar("--text-3"),
                        fontSize: 12,
                      }
                    : undefined
                }
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-1 type-micro text-text-2">
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-0.5 w-4 shrink-0 rounded-chip"
            style={{ background: color }}
          />
          Median (p50)
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-2.5 w-4 shrink-0 rounded-chip"
            style={{ background: bandFill(color) }}
          />
          p10 to p90
        </span>
        {memberCount > 0 ? (
          <span className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="h-0.5 w-4 shrink-0 rounded-chip opacity-40"
              style={{ background: memberColor }}
            />
            <span className="num">{memberCount}</span> members
          </span>
        ) : null}
      </figcaption>
    </figure>
  );
}

interface FanTooltipProps {
  validTs: string;
  leadMin: number;
  p10: number;
  p50: number;
  p90: number;
  format: (value: number) => string;
}

/** The hover card: the step's time and lead, its median, and the band around it. */
function FanTooltip({ validTs, leadMin, p10, p50, p90, format }: FanTooltipProps) {
  return (
    <div className="rounded-control border border-line-strong bg-well px-2.5 py-2">
      <p className="num type-micro text-text-2">
        {formatIst(validTs)} IST ({formatLead(leadMin)})
      </p>
      <p className="num type-small font-medium text-text">{format(p50)}</p>
      <p className="num type-micro text-text-3">
        p10 to p90: {format(p10)} to {format(p90)}
      </p>
    </div>
  );
}

/** The bordered box the loading, empty and error states sit in, at the plot's own height. */
function ChartShell({
  children,
  height,
  className,
}: {
  children: React.ReactNode;
  height: number;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-center rounded-control border border-line bg-ink",
        className,
      )}
      style={{ minHeight: height }}
    >
      {children}
    </div>
  );
}

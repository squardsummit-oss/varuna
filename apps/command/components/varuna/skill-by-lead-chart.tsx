"use client";

/**
 * Rain skill by lead time (SPEC.md 6.6 `SkillByLeadChart`, 7.10, 11.12).
 *
 * Draws what `GET /v1/verification/rain-skill` serves for one scope, threshold and forecast:
 * the pooled score by lead (0 to 180 min, 15-minute ticks), persistence as a dashed `--naive`
 * line, the p10-p90 of the same score taken cycle by cycle as a band where at least three cycles
 * have one, the useful-skill horizon the scorer computed, and the observed event pixels per lead
 * as quiet bars underneath so a reader can see how much weather each point rests on.
 *
 * Nothing here is computed from the rain: the rows are a reshaping of served numbers, and a lead
 * whose score has no denominator is a gap, never a zero (rule 6). Nothing animates - section 8
 * has no row for a chart drawing itself in - so every series has Recharts' animation off.
 */

import { useMemo } from "react";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartLine } from "lucide-react";

import { EmptyState } from "@/components/varuna/empty-state";
import { Skeleton } from "@/components/varuna/skeleton";
import type {
  RainScope,
  SkillHorizon,
  SkillMetric,
  SpreadForecastName,
} from "@/lib/api/verification-rain";
import { bandFill, chartColor, cssVar } from "@/lib/ramps";
import { cn } from "@/lib/utils";

/** 0 to 180 minutes in 15-minute steps (7.10: "confidence decays after 90 minutes" is read here). */
export const LEAD_TICKS_MIN: readonly number[] = Array.from({ length: 13 }, (_, i) => i * 15);

export const METRIC_LABEL: Record<SkillMetric, string> = { csi: "CSI", pod: "POD", far: "FAR" };

export const METRIC_NOTE: Record<SkillMetric, string> = {
  csi: "higher is better",
  pod: "higher is better",
  far: "lower is better",
};

export const FORECAST_LABEL: Record<SpreadForecastName, string> = {
  mean: "ensemble mean",
  p50: "ensemble median",
};

/** One lead as the chart draws it. Every value is a served number or null. */
export interface SkillRow {
  leadMin: number;
  forecast: number | null;
  persistence: number | null;
  /** p10-p90 across cycles; null where fewer than three cycles have a score. */
  band: [number, number] | null;
  /** Cycles the band is taken over (those with a defined score at this lead). */
  bandN: number;
  /** Cycles whose forecast reaches this lead inside the truth window. */
  nCycles: number;
  /** Sky pixels scored at this lead, pooled over cycles. */
  nPixels: number;
  /** Pixels whose reconstructed rain is above the threshold, pooled over cycles. */
  eventPixels: number;
}

/**
 * Reshape one scope's served lead rows into chart rows for one threshold, score and forecast.
 * Pure; exported for tests. A lead with no row for the threshold keeps its place with nulls.
 */
export function skillByLeadRows(
  scope: RainScope,
  thresholdMmH: number,
  metric: SkillMetric,
  forecast: SpreadForecastName,
): SkillRow[] {
  const key = String(thresholdMmH);
  return scope.byLead.map((row) => {
    const cell = row.thresholds[key];
    const spread = cell?.spread[forecast]?.[metric];
    const band: [number, number] | null =
      spread && spread.p10 !== null && spread.p90 !== null ? [spread.p10, spread.p90] : null;
    return {
      leadMin: row.leadMin,
      forecast: cell ? cell.forecasts[forecast][metric] : null,
      persistence: cell ? cell.forecasts.persistence[metric] : null,
      band,
      bandN: spread?.n ?? 0,
      nCycles: row.nCycles,
      nPixels: row.nPixels,
      eventPixels: cell ? cell.eventPixels : 0,
    };
  });
}

const two = (value: number | null) => (value === null ? "no denominator" : value.toFixed(2));

function leadList(leads: readonly number[]): string {
  const labels = leads.map((lead) => `+${lead}`);
  if (labels.length <= 1) return `${labels.join("")} min`;
  return `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]} min`;
}

/**
 * The horizon in words. "Confidence decays after N minutes" is said only when the scorer found
 * a failure after at least one useful lead; every other status says what the data does show.
 */
export function describeHorizon(
  horizon: SkillHorizon | null,
  forecast: SpreadForecastName,
): { headline: string; detail: string } {
  const who = `the ${FORECAST_LABEL[forecast]}`;
  if (!horizon || horizon.status === "no_leads") {
    return {
      headline: "No horizon to state.",
      detail: "No lead was scored at this threshold, so there is nothing to walk.",
    };
  }
  const t = `${horizon.thresholdMmH} mm/h`;
  const failure = horizon.firstFailure;
  const beats =
    horizon.beatsPersistenceLeadsMin.length > 0
      ? ` It beats persistence at ${leadList(horizon.beatsPersistenceLeadsMin)}.`
      : " It never beats persistence.";
  if (horizon.status === "beyond_scored_range") {
    return {
      headline: `Useful skill holds to the last scored lead, +${horizon.leadMin} min.`,
      detail:
        `At ${t} ${who}'s CSI stays at or above persistence and above ${horizon.csiFloor} at ` +
        "every lead the truth window lets us score, so the horizon lies beyond it.",
    };
  }
  if (horizon.status === "undetermined" && failure) {
    const start =
      horizon.leadMin && horizon.leadMin > 0
        ? `Useful to +${horizon.leadMin} min, then undetermined.`
        : "Horizon undetermined.";
    return {
      headline: start,
      detail:
        `At +${failure.leadMin} min nothing above ${t} was forecast or observed in the ` +
        `pooled cycles, so CSI has no denominator and the walk stops there.${beats}`,
    };
  }
  if (horizon.status === "found" && failure) {
    const csi = two(failure.csi);
    const held = two(failure.persistenceCsi);
    const below = failure.reasons.includes("below_persistence");
    const floor = failure.reasons.includes("below_floor");
    const why =
      below && floor
        ? `below persistence's ${held} and below the ${horizon.csiFloor} floor`
        : below
          ? `already below persistence's ${held}`
          : `below the ${horizon.csiFloor} floor`;
    const cycles = `${failure.nCycles} ${failure.nCycles === 1 ? "cycle" : "cycles"}`;
    if (horizon.leadMin && horizon.leadMin > 0) {
      return {
        headline: `Confidence decays after ${horizon.leadMin} minutes.`,
        detail: `At +${failure.leadMin} min ${who}'s CSI at ${t} is ${csi}, ${why}, over ${cycles}.${beats}`,
      };
    }
    return {
      headline: `No useful lead at ${t}.`,
      detail: `At +${failure.leadMin} min, the first lead scored, ${who}'s CSI is ${csi}, ${why}, over ${cycles}.${beats}`,
    };
  }
  return { headline: "No horizon to state.", detail: "The scorer returned no failure lead." };
}

/**
 * Whether the horizon is a lead the chart may mark and quote. A found horizon or one beyond the
 * scored range is; an undetermined one only when useful skill held for at least one lead first,
 * because "Horizon 0 min" beside a headline that reads "Horizon undetermined." would contradict
 * it. Exported for tests.
 */
export function horizonIsDrawn(horizon: SkillHorizon | null): boolean {
  if (!horizon || horizon.leadMin === null) return false;
  if (horizon.status === "found" || horizon.status === "beyond_scored_range") return true;
  return horizon.status === "undetermined" && horizon.leadMin > 0;
}

export interface SkillByLeadChartProps {
  rows: readonly SkillRow[];
  metric: SkillMetric;
  thresholdMmH: number;
  forecast: SpreadForecastName;
  /** The scorer's horizon for this scope, threshold and forecast; drawn on the CSI view only. */
  horizon?: SkillHorizon | null;
  /** The CSI floor the horizon uses, drawn as a reference on the CSI view. */
  csiFloor?: number | null;
  loading?: boolean;
  error?: string | null;
  height?: number;
  className?: string;
}

const AXIS_TICK = { fill: cssVar("--text-3"), fontSize: 12 };
const Y_WIDTH = 56;
const MARGIN = { top: 12, right: 16, bottom: 4, left: 4 };

const compact = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });

function RowTooltip({
  active,
  payload,
  metric,
  forecast,
}: {
  active?: boolean;
  payload?: readonly { payload?: unknown }[];
  metric: SkillMetric;
  forecast: SpreadForecastName;
}) {
  const row = active ? (payload?.[0]?.payload as SkillRow | undefined) : undefined;
  if (!row) return null;
  return (
    <div className="rounded-control border-line bg-deep type-micro text-text-2 border px-2 py-1.5">
      <p className="num text-text font-medium">+{row.leadMin} min</p>
      <p className="num">
        {METRIC_LABEL[metric]}, {FORECAST_LABEL[forecast]}: {two(row.forecast)}
      </p>
      <p className="num">
        {row.band
          ? `p10 to p90 across ${row.bandN} cycles: ${row.band[0].toFixed(2)} to ${row.band[1].toFixed(2)}`
          : `No band: ${row.bandN} ${row.bandN === 1 ? "cycle has" : "cycles have"} a score`}
      </p>
      <p className="num">Persistence: {two(row.persistence)}</p>
      <p className="num text-text-3">
        {row.nCycles} cycles, {row.eventPixels.toLocaleString("en-IN")} event pixels
      </p>
    </div>
  );
}

/**
 * The chart itself: a 0-to-1 score axis over lead time, with the event-pixel bars beneath
 * sharing the lead axis. Colour is never the only carrier: the legend names every series and
 * the accessible summary quotes the numbers.
 */
export function SkillByLeadChart({
  rows,
  metric,
  thresholdMmH,
  forecast,
  horizon = null,
  csiFloor = null,
  loading = false,
  error = null,
  height = 260,
  className,
}: SkillByLeadChartProps) {
  const color = chartColor(0);
  const naive = cssVar("--naive");
  const data = useMemo(() => rows.map((row) => ({ ...row, bandRange: row.band ?? null })), [rows]);

  if (loading) {
    return (
      <div className={cn("flex flex-col gap-2", className)}>
        <Skeleton className="h-4 w-1/2" />
        <div style={{ height }}>
          <Skeleton className="rounded-control h-full w-full" />
        </div>
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }
  if (error) {
    return <EmptyState icon={ChartLine} title="Rain skill did not load" description={error} />;
  }
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={ChartLine}
        title="No leads scored"
        description="No cycle of this event reaches a lead inside the truth window. Bake the event with make bake and reload."
      />
    );
  }

  const label = METRIC_LABEL[metric];
  const showHorizon = metric === "csi" && horizonIsDrawn(horizon);
  const horizonLead = horizon?.leadMin ?? 0;
  const horizonText = horizonLead > 0 ? `+${horizonLead} min` : "0 min, no useful lead";
  const quoted = rows.filter((row) => [5, 30, 60, 90, 120, 180].includes(row.leadMin));
  const summary =
    `${label} at ${thresholdMmH} mm/h by lead time, ${FORECAST_LABEL[forecast]} against ` +
    `persistence: ` +
    quoted
      .map(
        (row) =>
          `+${row.leadMin} min ${two(row.forecast)} against ${two(row.persistence)} ` +
          `over ${row.nCycles} cycles`,
      )
      .join("; ") +
    (showHorizon ? `. Useful-skill horizon ${horizonText}.` : ".");
  const bandCount = rows.filter((row) => row.band !== null).length;

  return (
    <figure className={cn("m-0 flex flex-col gap-1", className)} data-slot="skill-by-lead-chart">
      <div role="img" aria-label={summary} className="flex w-full flex-col">
        <div style={{ height }} className="w-full">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data} margin={MARGIN}>
              <CartesianGrid stroke={cssVar("--line")} strokeDasharray="2 4" vertical={false} />
              <XAxis
                dataKey="leadMin"
                type="number"
                domain={[0, 180]}
                ticks={[...LEAD_TICKS_MIN]}
                tick={AXIS_TICK}
                tickFormatter={(value: number) => (value > 0 ? `+${value}` : "0")}
                tickLine={false}
                axisLine={{ stroke: cssVar("--line") }}
              />
              <YAxis
                width={Y_WIDTH}
                domain={[0, 1]}
                ticks={[0, 0.25, 0.5, 0.75, 1]}
                tick={AXIS_TICK}
                tickLine={false}
                axisLine={{ stroke: cssVar("--line") }}
                label={{
                  value: `${label} (0 to 1)`,
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
                content={(props) => (
                  <RowTooltip
                    active={props.active}
                    payload={props.payload as readonly { payload?: unknown }[] | undefined}
                    metric={metric}
                    forecast={forecast}
                  />
                )}
              />
              {metric === "csi" && csiFloor !== null ? (
                <ReferenceLine
                  y={csiFloor}
                  stroke={cssVar("--line-strong")}
                  strokeDasharray="2 4"
                  ifOverflow="extendDomain"
                  label={{
                    value: `Useful floor ${csiFloor}`,
                    position: "insideTopRight",
                    fill: cssVar("--text-3"),
                    fontSize: 12,
                  }}
                />
              ) : null}
              <Area
                dataKey="bandRange"
                name="p10 to p90 across cycles"
                type="linear"
                stroke="none"
                fill={bandFill(color)}
                connectNulls={false}
                isAnimationActive={false}
                activeDot={false}
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
                dataKey="forecast"
                name={FORECAST_LABEL[forecast]}
                type="linear"
                stroke={color}
                strokeWidth={2}
                dot={{ r: 2, fill: color, stroke: cssVar("--ink") }}
                connectNulls={false}
                isAnimationActive={false}
              />
              {showHorizon ? (
                <ReferenceLine
                  x={horizonLead}
                  stroke={cssVar("--text-2")}
                  strokeDasharray="4 3"
                  label={{
                    // At 0 the line sits on the axis, so the label carries what it means.
                    value:
                      horizonLead > 0
                        ? `Horizon +${horizonLead} min`
                        : "Horizon 0 min, no useful lead",
                    position: "insideTopLeft",
                    fill: cssVar("--text-2"),
                    fontSize: 12,
                  }}
                />
              ) : null}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <div style={{ height: 72 }} className="w-full" data-slot="event-pixel-bars">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ ...MARGIN, top: 4, bottom: 18 }}>
              <XAxis
                dataKey="leadMin"
                type="number"
                domain={[0, 180]}
                ticks={[...LEAD_TICKS_MIN]}
                tick={AXIS_TICK}
                tickFormatter={(value: number) => (value > 0 ? `+${value}` : "0")}
                tickLine={false}
                axisLine={{ stroke: cssVar("--line") }}
                label={{
                  value: "Lead time (min after the cycle)",
                  position: "insideBottom",
                  offset: -14,
                  fill: cssVar("--text-3"),
                  fontSize: 12,
                }}
              />
              <YAxis
                width={Y_WIDTH}
                tick={AXIS_TICK}
                tickCount={2}
                tickFormatter={(value: number) => compact.format(value)}
                tickLine={false}
                axisLine={{ stroke: cssVar("--line") }}
                label={{
                  value: "Pixels",
                  angle: -90,
                  position: "insideLeft",
                  style: { textAnchor: "middle" },
                  fill: cssVar("--text-3"),
                  fontSize: 12,
                }}
              />
              <Bar
                dataKey="eventPixels"
                name="Event pixels"
                fill={cssVar("--line-strong")}
                isAnimationActive={false}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <figcaption className="type-micro text-text-2 flex flex-wrap items-center gap-x-4 gap-y-1 pt-3">
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="rounded-chip h-0.5 w-4 shrink-0"
            style={{ background: color }}
          />
          {label}, {FORECAST_LABEL[forecast]} ({METRIC_NOTE[metric]})
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-0 w-4 shrink-0 border-t-2 border-dashed"
            style={{ borderColor: naive }}
          />
          Persistence, the analysis held for three hours
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-2.5 w-4 shrink-0 rounded-[2px]"
            style={{ background: bandFill(color) }}
          />
          {bandCount > 0
            ? "p10 to p90 of the per-cycle score, where 3 or more cycles have one"
            : "No band: fewer than 3 cycles have a score at every lead"}
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-2.5 w-2 shrink-0 rounded-[2px]"
            style={{ background: cssVar("--line-strong") }}
          />
          Bars: observed event pixels above {thresholdMmH} mm/h, pooled over cycles
        </span>
      </figcaption>
    </figure>
  );
}

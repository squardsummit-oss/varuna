"use client";

/**
 * Reliability diagram for the members' probability of rain above a threshold (SPEC.md 6.6
 * `ReliabilityDiagram`, 7.10, 11.12).
 *
 * Draws what `GET /v1/verification/rain-skill` serves per lead band: ten probability bins, each
 * placed at the mean forecast probability of its pixels (x) against how often the reconstructed
 * rain was actually above the threshold there (y). A perfectly reliable forecast sits on the
 * diagonal; points above it mean the members said too little, points below mean they said too
 * much. The table beside it carries the Murphy decomposition the scorer computed for each band
 * (reliability, resolution, uncertainty) and how many pixel-leads each rests on.
 *
 * Nothing is computed here: a bin with no pixels or no served mean is left out rather than drawn
 * at zero (rule 6). Each band has its own colour, marker shape and name, so colour is never the
 * only carrier (6.10). Nothing animates - section 8 has no row for a chart drawing itself in.
 */

import { useMemo } from "react";
import {
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartScatter } from "lucide-react";

import { EmptyState } from "@/components/varuna/empty-state";
import type { ReliabilityBand } from "@/lib/api/verification-rain";
import { chartColor, cssVar } from "@/lib/ramps";
import { cn } from "@/lib/utils";

/** One populated bin as the chart draws it. Every value is served. */
export interface ReliabilityPoint {
  /** Mean forecast probability of the bin's pixels, 0 to 1. */
  x: number;
  /** Observed frequency of rain above the threshold in those pixels, 0 to 1. */
  y: number;
  n: number;
  pFrom: number;
  pTo: number;
}

export interface ReliabilitySeries {
  label: string;
  leadFromMin: number;
  leadToMin: number;
  n: number;
  baseRate: number | null;
  reliability: number | null;
  resolution: number | null;
  uncertainty: number | null;
  points: ReliabilityPoint[];
}

/** Marker shapes per band, so the bands differ in form as well as colour. */
const SHAPES = ["circle", "square", "triangle"] as const;

/**
 * Reshape the served bands into drawable series. A bin with no pixels, or whose mean probability
 * or observed frequency is null, is dropped; points are ordered by probability. Pure; exported
 * for tests.
 */
export function reliabilitySeries(bands: readonly ReliabilityBand[]): ReliabilitySeries[] {
  return bands.map((band) => ({
    label: `+${band.leadFromMin} to +${band.leadToMin} min`,
    leadFromMin: band.leadFromMin,
    leadToMin: band.leadToMin,
    n: band.n,
    baseRate: band.baseRate,
    reliability: band.reliability,
    resolution: band.resolution,
    uncertainty: band.uncertainty,
    points: band.bins
      .filter((bin) => bin.n > 0 && bin.meanP !== null && bin.observedFrequency !== null)
      .map((bin) => ({
        x: bin.meanP as number,
        y: bin.observedFrequency as number,
        n: bin.n,
        pFrom: bin.pFrom,
        pTo: bin.pTo,
      }))
      .sort((a, b) => a.x - b.x),
  }));
}

const pct = (value: number) => `${Math.round(value * 100)} %`;
const three = (value: number | null) => (value === null ? "not scored" : value.toFixed(3));
const count = (value: number) => value.toLocaleString("en-IN");

/** The diagram in words, quoting each band's lowest and highest populated bin. Exported for tests. */
export function describeReliability(series: readonly ReliabilitySeries[], thresholdMmH: number) {
  const drawn = series.filter((s) => s.points.length > 0);
  const parts = drawn.map((s) => {
    const low = s.points[0]!;
    const high = s.points[s.points.length - 1]!;
    return (
      `${s.label}, ${count(s.n)} pixel-leads: where the members gave ${pct(low.x)} the rain ` +
      `came ${pct(low.y)}; where they gave ${pct(high.x)} it came ${pct(high.y)}; ` +
      `reliability ${three(s.reliability)}, resolution ${three(s.resolution)}`
    );
  });
  return (
    `Reliability of the members' probability of rain above ${thresholdMmH} mm/h, forecast ` +
    `probability against observed frequency, the diagonal is perfect. ` +
    parts.join(". ") +
    "."
  );
}

function PointTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: readonly { payload?: unknown; name?: unknown }[];
}) {
  const item = active ? payload?.[0] : undefined;
  const point = item?.payload as (ReliabilityPoint & { band?: string }) | undefined;
  if (!point) return null;
  return (
    <div className="rounded-control border-line bg-deep type-micro text-text-2 border px-2 py-1.5">
      {point.band ? <p className="text-text font-medium">{point.band}</p> : null}
      <p className="num">
        Bin {pct(point.pFrom)} to {pct(point.pTo)}, {count(point.n)} pixel-leads
      </p>
      <p className="num">Members gave {pct(point.x)} on average</p>
      <p className="num">Rain came above the threshold {pct(point.y)} of the time</p>
    </div>
  );
}

export interface ReliabilityDiagramProps {
  bands: readonly ReliabilityBand[];
  thresholdMmH: number;
  /** Said when no band has a drawable bin, e.g. the scorer's reason the probability is missing. */
  emptyReason?: string | null;
  height?: number;
  className?: string;
}

export function ReliabilityDiagram({
  bands,
  thresholdMmH,
  emptyReason = null,
  height = 260,
  className,
}: ReliabilityDiagramProps) {
  const series = useMemo(() => reliabilitySeries(bands), [bands]);
  const drawn = series.filter((s) => s.points.length > 0);

  if (drawn.length === 0) {
    return (
      <EmptyState
        size="sm"
        icon={ChartScatter}
        title={`No reliability at ${thresholdMmH} mm/h`}
        description={
          emptyReason ??
          "No lead band has a scored probability at this threshold. Re-bake with make bake to keep the member cube it is taken from."
        }
      />
    );
  }

  const axis = { fill: cssVar("--text-3"), fontSize: 12 };
  const ticks = [0, 0.25, 0.5, 0.75, 1];

  return (
    <figure className={cn("m-0 flex flex-col gap-3", className)} data-slot="reliability-diagram">
      <div
        role="img"
        aria-label={describeReliability(series, thresholdMmH)}
        style={{ height }}
        className="w-full"
      >
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 8, right: 16, bottom: 22, left: 4 }}>
            <CartesianGrid stroke={cssVar("--line")} strokeDasharray="2 4" />
            <XAxis
              type="number"
              dataKey="x"
              domain={[0, 1]}
              ticks={ticks}
              tick={axis}
              tickFormatter={(value: number) => pct(value)}
              tickLine={false}
              axisLine={{ stroke: cssVar("--line") }}
              label={{
                value: `Forecast probability of rain above ${thresholdMmH} mm/h`,
                position: "insideBottom",
                offset: -14,
                fill: cssVar("--text-3"),
                fontSize: 12,
              }}
            />
            <YAxis
              type="number"
              dataKey="y"
              width={56}
              domain={[0, 1]}
              ticks={ticks}
              tick={axis}
              tickFormatter={(value: number) => pct(value)}
              tickLine={false}
              axisLine={{ stroke: cssVar("--line") }}
              label={{
                value: "Observed frequency",
                angle: -90,
                position: "insideLeft",
                style: { textAnchor: "middle" },
                fill: cssVar("--text-3"),
                fontSize: 12,
              }}
            />
            <ReferenceLine
              segment={[
                { x: 0, y: 0 },
                { x: 1, y: 1 },
              ]}
              stroke={cssVar("--line-strong")}
              strokeDasharray="4 4"
              ifOverflow="hidden"
            />
            <Tooltip
              isAnimationActive={false}
              cursor={false}
              content={(props) => (
                <PointTooltip
                  active={props.active}
                  payload={props.payload as readonly { payload?: unknown }[] | undefined}
                />
              )}
            />
            {series.map((s, i) =>
              s.points.length === 0 ? null : (
                <Scatter
                  key={s.label}
                  name={s.label}
                  data={s.points.map((point) => ({ ...point, band: s.label }))}
                  fill={chartColor(i)}
                  stroke={chartColor(i)}
                  shape={SHAPES[i % SHAPES.length]}
                  line={{ stroke: chartColor(i), strokeWidth: 1.5 }}
                  lineType="joint"
                  isAnimationActive={false}
                />
              ),
            )}
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="type-micro text-text-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        {series.map((s, i) =>
          s.points.length === 0 ? null : (
            <span key={s.label} className="flex items-center gap-1.5">
              <svg
                aria-hidden="true"
                width="12"
                height="12"
                viewBox="0 0 12 12"
                className="shrink-0"
              >
                {SHAPES[i % SHAPES.length] === "circle" ? (
                  <circle cx="6" cy="6" r="4" fill={chartColor(i)} />
                ) : SHAPES[i % SHAPES.length] === "square" ? (
                  <rect x="2" y="2" width="8" height="8" fill={chartColor(i)} />
                ) : (
                  <path d="M6 1.5 L10.5 10 L1.5 10 Z" fill={chartColor(i)} />
                )}
              </svg>
              Lead {s.label}
            </span>
          ),
        )}
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-0 w-4 shrink-0 border-t-2 border-dashed"
            style={{ borderColor: cssVar("--line-strong") }}
          />
          Perfect reliability
        </span>
      </figcaption>
      <div
        role="region"
        aria-label="Reliability by lead band"
        className="overflow-x-auto"
        tabIndex={0}
      >
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-line border-b text-left">
              {[
                "Lead band",
                "Pixel-leads",
                "Base rate",
                "Reliability (lower is better)",
                "Resolution (higher is better)",
                "Uncertainty",
              ].map((head) => (
                <th
                  key={head}
                  scope="col"
                  className="type-micro text-text-2 px-2 py-1.5 font-medium"
                >
                  {head}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {series.map((s) => (
              <tr key={s.label} className="border-line border-b last:border-b-0">
                <th
                  scope="row"
                  className="num type-small text-text px-2 py-1.5 text-left font-normal"
                >
                  {s.label}
                </th>
                <td className="num type-small text-text-2 px-2 py-1.5">{count(s.n)}</td>
                <td className="num type-small text-text-2 px-2 py-1.5">
                  {s.baseRate === null ? "not scored" : pct(s.baseRate)}
                </td>
                <td className="num type-small text-text px-2 py-1.5">{three(s.reliability)}</td>
                <td className="num type-small text-text px-2 py-1.5">{three(s.resolution)}</td>
                <td className="num type-small text-text-2 px-2 py-1.5">{three(s.uncertainty)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

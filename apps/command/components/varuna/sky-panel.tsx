"use client";

import { useMemo, useState } from "react";
import { CloudRain } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { CycleBudgetBar, type StageTiming } from "@/components/varuna/cycle-budget-bar";
import { EmptyState } from "@/components/varuna/empty-state";
import { FanChart, type FanChartMember, type FanChartPoint } from "@/components/varuna/fan-chart";
import { Panel } from "@/components/varuna/panel";
import { Skeleton } from "@/components/varuna/skeleton";
import { BAKED_RAIN, useRainNowcast, useRainPointSeries, type RainSource } from "@/lib/api/sky";
import {
  NOWCASTER_LABELS,
  type Nowcaster,
  type RainNowcast,
  type RainPointSeries,
  type RainPointStep,
  type RainStep,
} from "@/lib/api/schemas";
import type { ApiError } from "@/lib/api/client";
import {
  formatCount,
  formatDate,
  formatIst,
  formatLead,
  formatMs,
  formatPct,
  formatScore,
} from "@/lib/format";
import { useReplayStore } from "@/lib/stores/replay";
import { cn } from "@/lib/utils";

/** The junction the fan chart is drawn at; the coordinate comes from the city register, not here. */
export const DEFAULT_HOTSPOT = "hindmata";

/**
 * The lead where the spread is quoted against the first half-hour, and the dashed marker on both
 * charts. SPEC.md section 7.10 puts the decay of confidence at about 90 minutes; the numbers
 * beside the marker are computed from the run, so the chart can agree or disagree with that.
 */
export const DIVERGENCE_LEAD_MIN = 90;
const EARLY_LEAD_MIN = 30;

const rainFormat = (value: number): string => `${formatScore(value)} mm/h`;

/** The two lead times the spread sentence compares, once the series is long enough to have them. */
function spreadAt(steps: readonly RainStep[], leadMin: number): number | null {
  const step = steps.find((s) => s.lead_min === leadMin);
  return step ? step.p90_mm_h - step.p10_mm_h : null;
}

function toPoints(steps: readonly RainStep[]): FanChartPoint[] {
  return steps.map((step) => ({
    validTs: step.valid_ts,
    leadMin: step.lead_min,
    p10: step.p10_mm_h,
    p50: step.p50_mm_h,
    p90: step.p90_mm_h,
  }));
}

/** The highest exceedance probability in the series, with the step it happens at. */
function peakExceedance(
  steps: readonly RainPointStep[],
  key: "p_gt_20" | "p_gt_40",
): { p: number; validTs: string } | null {
  if (steps.length === 0) return null;
  const best = steps.reduce((a, b) => (b[key] > a[key] ? b : a), steps[0]!);
  return { p: best[key], validTs: best.valid_ts };
}

function isNowcaster(value: string | null | undefined): value is Nowcaster {
  return value === "pysteps_steps" || value === "fallback_steps";
}

export interface SkyPanelProps {
  /** Register id, slug or part of a name from the city's hotspot register. */
  hotspot?: string;
  /** Plot height; the console rail is short, the design page is not. */
  chartHeight?: number;
  className?: string;
}

/**
 * Temporary Phase 3 panel: the console reading VARUNA-Sky's rain cube (task P3.8).
 *
 * It is scaffolding and says so. Phase 6 builds the console properly - `CityMap`, the real
 * `TimeBar` and the hotspot drawer - and deletes this panel; `FanChart` is the piece that stays,
 * which is why the chart is a component of its own and this file only feeds it.
 *
 * Everything on screen comes from one cycle's `rain/quantiles.zarr`: the fan chart is the 500 m Sky
 * pixel the junction falls in, the second chart is every member's city-mean hyetograph, and the
 * numbers beside them are computed from those same steps. Nothing is placeholder (SPEC.md rule
 * 6): with no baked run the panel shows the API's own message naming the make target to run.
 *
 * The run stamp handles the two things a Sky-only cycle does differently. `run_id` is null when the
 * products were computed on demand, which reads "live, not published as a run" rather than as a
 * missing field; and `notes` are the run's earned honesty labels, printed verbatim.
 */
export function SkyPanel({
  hotspot = DEFAULT_HOTSPOT,
  chartHeight = 190,
  className,
}: SkyPanelProps) {
  const simTime = useReplayStore((s) => s.simTime);
  const bundleId = useReplayStore((s) => s.bundleId);
  const [source, setSource] = useState<RainSource>(BAKED_RAIN);
  /** What the last compute asked for, so the panel can say when the ladder moved it. */
  const [requestedTs, setRequestedTs] = useState<string | null>(null);

  const series = useRainPointSeries(hotspot, source);
  const band = useRainNowcast(source);

  const loading = series.isPending || band.isPending;
  const computing = source.compute && (series.isFetching || band.isFetching);
  const error: ApiError | null = series.error ?? band.error ?? null;

  const compute = () => {
    setRequestedTs(simTime);
    setSource({ compute: true, t: simTime, bundle: bundleId });
    toast.info("Computing one Sky cycle. It takes about eight seconds.");
  };

  const computeButton = (
    <Button size="sm" onClick={compute} disabled={computing}>
      {computing ? "Computing" : "Compute live"}
    </Button>
  );

  return (
    <Panel
      as="section"
      title="Rain nowcast"
      description="Phase 3 scaffolding: the map, the time bar and the hotspot drawer arrive in Phase 6."
      actions={computeButton}
      className={cn("min-w-0", className)}
    >
      {loading ? (
        <SkyPanelSkeleton chartHeight={chartHeight} />
      ) : error ? (
        <EmptyState
          icon={CloudRain}
          title={error.code === "no_rain_runs" ? "No runs yet" : "Rain nowcast unavailable"}
          description={error.message}
          action={
            error.code === "no_rain_runs" ? null : (
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  void series.refetch();
                  void band.refetch();
                }}
              >
                Try again
              </Button>
            )
          }
        />
      ) : series.data && band.data ? (
        <SkyPanelBody
          series={series.data}
          band={band.data}
          chartHeight={chartHeight}
          requestedTs={requestedTs}
        />
      ) : null}
    </Panel>
  );
}

interface SkyPanelBodyProps {
  series: RainPointSeries;
  band: RainNowcast;
  chartHeight: number;
  requestedTs: string | null;
}

/** Everything below the header once both responses are in hand. */
function SkyPanelBody({ series, band, chartHeight, requestedTs }: SkyPanelBodyProps) {
  const point = series.point;
  const live = series.mode === "live";
  const nowcaster = isNowcaster(series.nowcaster) ? series.nowcaster : null;
  const fallbackRan = nowcaster === "fallback_steps";

  const pointPoints = useMemo(() => toPoints(series.steps), [series.steps]);
  const bandPoints = useMemo(() => toPoints(band.steps), [band.steps]);
  const members = useMemo<FanChartMember[]>(
    () => band.members.map((member) => ({ id: member.member, values: member.mm_h })),
    [band.members],
  );

  const early = spreadAt(band.steps, EARLY_LEAD_MIN);
  const late = spreadAt(band.steps, DIVERGENCE_LEAD_MIN);
  const gt20 = peakExceedance(series.steps, "p_gt_20");
  const gt40 = peakExceedance(series.steps, "p_gt_40");
  const [low, high] = series.exceedance_mm_h;

  /** The clamped ladder moved the cycle, so the panel says so instead of showing the wrong time. */
  const moved =
    requestedTs !== null && Date.parse(requestedTs) !== Date.parse(series.valid_ts)
      ? requestedTs
      : null;

  const skyMs = Object.values(series.stage_ms).reduce((sum, ms) => sum + ms, 0);
  const stages: StageTiming[] = [{ id: "sky", ms: skyMs > 0 ? skyMs : null }];

  return (
    <div className="flex flex-col gap-5">
      {/* ------------------------------------------------------------------ the run stamp */}
      <section className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={cn(
              "inline-flex h-6 items-center rounded-chip border px-2.5 type-micro",
              live ? "border-line bg-well text-status-live" : "border-line bg-well text-status-baked",
            )}
          >
            {live ? "Live, not published as a run" : "Baked"}
          </span>
          {series.run_id ? (
            <span className="num truncate type-micro text-text-2" title={series.run_id}>
              run {series.run_id}
            </span>
          ) : null}
          {fallbackRan ? (
            <span className="inline-flex h-6 items-center rounded-chip border border-line bg-well px-2.5 type-micro text-status-degraded">
              Fallback nowcaster
            </span>
          ) : null}
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5">
          <Fact label="Cycle">
            {formatIst(series.valid_ts)} IST, {formatDate(series.valid_ts)}
          </Fact>
          <Fact label="Bundle">{series.bundle ?? "None"}</Fact>
          <Fact label="Ensemble">
            {series.n_members} members, {series.n_steps} steps of {series.step_min} min
          </Fact>
          <Fact label="Seed">{series.seed === null || series.seed === undefined ? "Not recorded" : series.seed}</Fact>
          <Fact label="Nowcaster">{nowcaster ? NOWCASTER_LABELS[nowcaster] : "Not recorded"}</Fact>
          <Fact label="Z-R relation">
            {series.zr
              ? `a = ${formatCount(series.zr.a)}, b = ${formatScore(series.zr.b)} (${
                  series.zr.source === "adaptive" ? "fitted this cycle" : "Marshall-Palmer"
                })`
              : "Not recorded"}
          </Fact>
        </dl>

        {moved ? (
          <p className="type-micro text-text-3">
            Asked for {formatIst(moved)} IST. The cycle ladder and the radar history put this run at{" "}
            {formatIst(series.valid_ts)} IST.
          </p>
        ) : null}
      </section>

      {/* ----------------------------------------------------------- the fan chart at a junction */}
      <section className="flex flex-col gap-2">
        <div className="flex flex-col gap-0.5">
          <h3 className="type-small font-medium text-text">{point.name}</h3>
          <p className="num type-micro text-text-3">
            {point.lat.toFixed(4)}, {point.lon.toFixed(4)} · Sky pixel row {point.row}, column{" "}
            {point.col}, {point.res_m} m
            {point.source_url ? (
              <>
                {" · "}
                <a
                  href={point.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-tide underline underline-offset-2 focus-visible:ring-2 focus-visible:ring-tide focus-visible:outline-none"
                >
                  coordinate source
                </a>
              </>
            ) : null}
          </p>
        </div>

        <FanChart
          points={pointPoints}
          quantity="Rain rate"
          unit="mm/h"
          height={chartHeight}
          markerLeadMin={DIVERGENCE_LEAD_MIN}
          markerLabel={formatLead(DIVERGENCE_LEAD_MIN)}
          formatValue={rainFormat}
          emptyTitle="This cycle has no steps"
          emptyDescription="The run carries no forecast steps. Compute the cycle again, or bake the bundle."
        />

        {gt20 && gt40 && low !== undefined && high !== undefined ? (
          <p className="type-micro text-text-2">
            <span className="num">{formatPct(gt20.p)}</span> is the highest P(R &gt; {low} mm/h) at
            this pixel, at {formatIst(gt20.validTs)} IST; P(R &gt; {high} mm/h) peaks at{" "}
            <span className="num">{formatPct(gt40.p)}</span>.
          </p>
        ) : null}

        <p className="type-micro text-text-3">
          Sampled from the 500 m Sky pixel the junction falls in, not its own square metre. Two
          neighbouring hotspots can share a rain series while their depth series differ.
        </p>
      </section>

      {/* ------------------------------------------------------- every member over the whole city */}
      <section className="flex flex-col gap-2">
        <h3 className="type-small font-medium text-text">Rain over the city, every member</h3>

        <FanChart
          points={bandPoints}
          quantity="Rain rate"
          unit="mm/h"
          members={members}
          height={chartHeight}
          markerLeadMin={DIVERGENCE_LEAD_MIN}
          markerLabel={formatLead(DIVERGENCE_LEAD_MIN)}
          formatValue={rainFormat}
          emptyTitle="This cycle has no steps"
          emptyDescription="The run carries no forecast steps. Compute the cycle again, or bake the bundle."
        />

        {early !== null && late !== null ? (
          <p className="type-micro text-text-2">
            The p10 to p90 band spans <span className="num">{rainFormat(early)}</span> at{" "}
            {formatLead(EARLY_LEAD_MIN)} and <span className="num">{rainFormat(late)}</span> at{" "}
            {formatLead(DIVERGENCE_LEAD_MIN)}.
          </p>
        ) : null}

        <p className="type-micro text-text-3">
          The band is the spread of the city mean, which is narrower than the spread over any one
          street.
        </p>
      </section>

      {/* --------------------------------------------------------------------- what the cycle cost */}
      <section className="flex flex-col gap-2">
        <CycleBudgetBar stages={stages} totalMs={skyMs > 0 ? skyMs : null} />
        <p className="type-micro text-text-3">
          Sky only. Twin, Flash, Pulse and products land in phases 4 and 5, so their segments are
          empty.
        </p>
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {Object.entries(series.stage_ms).map(([stage, ms]) => (
            <li key={stage} className="flex items-baseline gap-1.5 type-micro">
              <span className="text-text-2">{stage}</span>
              <span className="num text-text-3">{formatMs(ms)}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* ----------------------------------------------------------------- the run's own honesty */}
      {series.notes.length > 0 ? (
        <section className="flex flex-col gap-1.5">
          <h3 className="type-small font-medium text-text">What this run says about itself</h3>
          <ul className="flex flex-col gap-1">
            {series.notes.map((note) => (
              <li key={note} className="type-micro text-text-2">
                {note}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

/** One label-and-value pair of the run stamp. */
function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="type-micro text-text-3">{label}</dt>
      <dd className="num type-micro text-text">{children}</dd>
    </div>
  );
}

/** Loading state: shimmer where the stamp, the two charts and the timings will be. */
function SkyPanelSkeleton({ chartHeight }: { chartHeight: number }) {
  return (
    <div className="flex flex-col gap-5">
      <Skeleton lines={3} />
      <div style={{ height: chartHeight }}>
        <Skeleton className="h-full w-full rounded-control" />
      </div>
      <div style={{ height: chartHeight }}>
        <Skeleton className="h-full w-full rounded-control" />
      </div>
      <Skeleton lines={2} />
    </div>
  );
}

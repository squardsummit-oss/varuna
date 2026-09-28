"use client";

import NumberFlow from "@number-flow/react";

import { Skeleton } from "@/components/varuna/skeleton";
import { formatBeta, formatCount } from "@/lib/format";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { DUR_MS, EASE_UI_CSS } from "@/lib/motion";
import { cn } from "@/lib/utils";

import { pipeTitle, type DrainStats } from "@/app/drains/drain-model";

/** Motion M4: numbers roll on the catalogue's panel timing, once per value change. */
const ROLL_TIMING = { duration: DUR_MS.panel, easing: EASE_UI_CSS };

/** A number that rolls to its value (M4), or the plain string under reduced motion. */
function Rolling({
  value,
  decimals = 0,
  suffix = "",
  reduced,
}: {
  value: number;
  decimals?: number;
  suffix?: string;
  reduced: boolean;
}) {
  if (reduced) {
    const text = decimals > 0 ? value.toFixed(decimals) : formatCount(value);
    return (
      <>
        {text}
        {suffix}
      </>
    );
  }
  return (
    <NumberFlow
      value={value}
      locales="en-IN"
      format={{ minimumFractionDigits: decimals, maximumFractionDigits: decimals }}
      suffix={suffix}
      respectMotionPreference={false}
      transformTiming={ROLL_TIMING}
      spinTiming={ROLL_TIMING}
    />
  );
}

function Tile({
  label,
  children,
  sub,
  className,
}: {
  label: string;
  children: React.ReactNode;
  sub: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("rounded-panel border-line bg-deep min-w-0 border px-4 py-2.5", className)}>
      <p className="type-micro text-text-2">{label}</p>
      <p className="num font-display text-h2 tracking-display text-text truncate font-semibold">
        {children}
      </p>
      <p className="num type-micro text-text-3 mt-0.5">{sub}</p>
    </div>
  );
}

/** "+0.09" or "-0.12" percentage points, to two places. */
function signedPoints(points: number): string {
  const rounded = Math.abs(points) < 0.005 ? 0 : points;
  return `${rounded >= 0 ? "+" : "-"}${Math.abs(rounded).toFixed(2)}`;
}

export interface DrainStatsStripProps {
  /** Null while the run is loading. */
  stats: DrainStats | null;
  className?: string;
}

/**
 * What this cycle learned, in four numbers (SPEC.md 7.3): pipes moved, observations
 * assimilated, capacity lost citywide with its split into land use and learning, and the biggest
 * single change. Capacity lost is never shown without the split - 28 % of Mumbai's inferred
 * capacity is the land-use prior, and learning moved it by a tenth of a point.
 */
export function DrainStatsStrip({ stats, className }: DrainStatsStripProps) {
  const reduced = usePrefersReducedMotion();
  if (!stats) {
    return (
      <div
        className={cn("grid gap-3 sm:grid-cols-2 xl:grid-cols-4", className)}
        aria-busy="true"
        aria-label="Loading what this cycle learned"
      >
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="rounded-panel h-[84px]" />
        ))}
      </div>
    );
  }

  const upDown =
    stats.nUp !== null && stats.nDown !== null
      ? `: ${formatCount(stats.nUp)} up, ${formatCount(stats.nDown)} down${stats.partial ? " among the pipes written" : ""}`
      : "";

  return (
    <section aria-label="What this cycle learned" className={cn("space-y-1", className)}>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Tile
          label="Pipes learned this cycle"
          sub={`${formatCount(stats.nMoved)} of ${formatCount(stats.nPipes)} pipes moved this cycle${upDown}`}
        >
          <Rolling value={stats.nMoved} reduced={reduced} />
        </Tile>
        <Tile
          label="Observations assimilated"
          sub={`${formatCount(stats.nTraffic)} traffic, ${formatCount(stats.nReports)} citizen; ${formatCount(stats.nSynthetic)} synthetic, ${formatCount(stats.nReal)} real`}
        >
          <Rolling value={stats.nObs} reduced={reduced} />
        </Tile>
        <Tile
          label="Capacity lost citywide"
          sub={
            stats.capacity
              ? `${stats.capacity.priorPct.toFixed(1)} % assumed from land use, ${signedPoints(stats.capacity.learnedPoints)} points learned${
                  stats.capacity.learnedM3s !== null
                    ? ` (${stats.capacity.learnedM3s >= 0 ? "+" : "-"}${Math.abs(stats.capacity.learnedM3s).toFixed(1)} m³/s)`
                    : ""
                }`
              : "Not in this run: bake it again to split land use from learning."
          }
        >
          {stats.capacity ? (
            <Rolling value={stats.capacity.postPct} decimals={1} suffix=" %" reduced={reduced} />
          ) : (
            <span className="text-text-3">Not split</span>
          )}
        </Tile>
        <Tile
          label="Biggest change"
          sub={
            stats.biggest
              ? [
                  pipeTitle(stats.biggest.edge, stats.biggest.name, stats.biggest.place),
                  stats.biggest.locality,
                ]
                  .filter(Boolean)
                  .join(", ")
              : "No pipe moved this cycle."
          }
        >
          {stats.biggest ? (
            <>
              {formatBeta(stats.biggest.prior)}
              <span className="text-text-3"> → </span>
              {formatBeta(stats.biggest.post)}
            </>
          ) : (
            <span className="text-text-3">None</span>
          )}
        </Tile>
      </div>
      {stats.note ? <p className="type-micro text-text-3">{stats.note}</p> : null}
    </section>
  );
}

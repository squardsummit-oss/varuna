"use client";

import { ArrowDown, ArrowUp, Crosshair } from "lucide-react";

import { formatBeta, formatCount, formatIst } from "@/lib/format";
import { cssVar, drainBand } from "@/lib/ramps";
import { cn } from "@/lib/utils";

import { describeObservation, pipeTitle, type PipeCardData } from "@/app/drains/drain-model";

/** Where on a 0-1 track a blockage sits, in percent. */
function at(beta: number): string {
  return `${Math.min(100, Math.max(0, beta * 100))}%`;
}

export interface PipeCardProps {
  pipe: PipeCardData;
  selected?: boolean;
  onSelect?: (pipe: PipeCardData) => void;
  className?: string;
}

/**
 * One pipe Pulse moved, named and placed (section 6.6's `PipeRow`, drawn as a card on `/drains`).
 *
 * The bar is the blockage track from 0 to 1: a hollow mark at the land-use prior, a filled mark
 * at the posterior with its standard deviation as a whisker, both in the drain ramp. The numbers
 * are printed beside it, so colour is never the only carrier of the change (SPEC.md 6.10).
 */
export function PipeCard({ pipe, selected = false, onSelect, className }: PipeCardProps) {
  const cleared = pipe.direction === "down";
  const postVar = cleared ? "--naive" : `--drain-${drainBand(pipe.post).key}`;
  const priorVar = `--drain-${drainBand(pipe.prior).key}`;
  const lo = Math.max(0, pipe.post - pipe.sd);
  const hi = Math.min(1, pipe.post + pipe.sd);
  // A pipe with no street of its own is named by its id and placed by the nearest street, so the
  // id is in the title and not repeated under it.
  const name = pipeTitle(pipe.id, pipe.name, pipe.place);
  const sub = (name.startsWith(`Pipe ${pipe.id}`) ? [pipe.locality] : [pipe.locality, pipe.id])
    .filter(Boolean)
    .join(", ");
  const Arrow = cleared ? ArrowDown : ArrowUp;

  return (
    <article
      aria-label={`${name}: blockage ${formatBeta(pipe.prior)} to ${formatBeta(pipe.post)}`}
      className={cn(
        "rounded-panel bg-well border p-3",
        selected ? "border-line-strong" : "border-line",
        className,
      )}
    >
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="type-small text-text truncate font-medium">{name}</p>
          {sub ? <p className="type-micro text-text-3 truncate">{sub}</p> : null}
        </div>
        {onSelect ? (
          <button
            type="button"
            onClick={() => onSelect(pipe)}
            aria-label={`Show ${name} on the map`}
            className="rounded-control border-line type-micro text-text-2 hover:text-text focus-visible:outline-tide inline-flex shrink-0 items-center gap-1 border px-2 py-1 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2"
          >
            <Crosshair size={14} strokeWidth={1.75} aria-hidden="true" />
            Show
          </button>
        ) : null}
      </header>

      <div className="mt-3 flex items-center gap-3">
        <div className="relative h-4 flex-1" aria-hidden="true">
          <div className="bg-line absolute inset-x-0 top-1/2 h-px -translate-y-1/2" />
          {/* The spread: one standard deviation either side of the posterior. */}
          <div
            className="rounded-chip absolute top-1/2 h-[3px] -translate-y-1/2 opacity-60"
            style={{
              left: at(lo),
              width: `calc(${at(hi)} - ${at(lo)})`,
              backgroundColor: cssVar(postVar),
            }}
          />
          <div
            className="bg-deep absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2"
            style={{ left: at(pipe.prior), borderColor: cssVar(priorVar) }}
          />
          <div
            className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full"
            style={{ left: at(pipe.post), backgroundColor: cssVar(postVar) }}
          />
        </div>
        <p className="num type-small text-text inline-flex shrink-0 items-center gap-1">
          {formatBeta(pipe.prior)}
          <Arrow size={12} strokeWidth={1.75} aria-hidden="true" className="text-text-3" />
          {formatBeta(pipe.post)}
          <span className="type-micro text-text-3">± {formatBeta(pipe.sd)}</span>
        </p>
      </div>

      <dl className="num type-micro mt-2 grid grid-cols-3 gap-2">
        <div>
          <dt className="text-text-3">Capacity lost</dt>
          <dd className="text-text-2">{pipe.capacityLostPct.toFixed(1)} %</dd>
        </div>
        <div>
          <dt className="text-text-3">Diameter</dt>
          <dd className="text-text-2">{formatCount(pipe.diameterM * 1000)} mm</dd>
        </div>
        <div>
          <dt className="text-text-3">Observations</dt>
          <dd className="text-text-2">{formatCount(pipe.observations)}</dd>
        </div>
      </dl>

      <p className="type-micro text-text-2 mt-2">
        {pipe.movedBy ? (
          <>
            {describeObservation(pipe.movedBy)}, {formatIst(pipe.movedBy.ts)}
            {pipe.movedBy.synthetic ? <span className="text-text-3"> (synthetic)</span> : null}
          </>
        ) : (
          `${cleared ? "Cleared" : "Raised"} via connected pipes`
        )}
      </p>
    </article>
  );
}

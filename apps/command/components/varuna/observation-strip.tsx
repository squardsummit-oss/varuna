"use client";

import { formatIst } from "@/lib/format";
import { cssVar } from "@/lib/ramps";
import { atPlace } from "@/lib/street-label";
import { cn } from "@/lib/utils";
import type { AssimilatedObservation } from "@/lib/api/drains";

import { describeObservation, stripLayout, type StripMark } from "@/app/drains/drain-model";

const KIND_VAR = { traffic: "--obs-traffic", report: "--obs-report" } as const;
const LANE_LABEL = { traffic: "Traffic", report: "Citizen" } as const;
const GROUP_NOUN = { traffic: "traffic anomalies", report: "citizen reports" } as const;

export interface ObservationStripProps {
  observations: readonly AssimilatedObservation[];
  /** The cycle the observations were assimilated at: the strip's right edge. */
  cycleTs: string;
  /** The observations on show beside the map: one, or every member of one group. */
  selectedIds?: readonly string[] | null;
  /** A single observation was picked. */
  onSelect?: (obs: AssimilatedObservation) => void;
  /** A group - every observation of one kind at one minute - was picked. */
  onSelectGroup?: (members: readonly AssimilatedObservation[]) => void;
  className?: string;
}

function sameIds(mark: StripMark, ids: readonly string[] | null | undefined): boolean {
  if (!ids || ids.length !== mark.members.length) return false;
  return mark.members.every((m) => ids.includes(m.id));
}

/** What a mark is, in words: a screen reader hears what a sighted reader sees. */
export function markLabel(mark: StripMark): string {
  if (mark.members.length === 1) {
    const o = mark.members[0];
    const change =
      mark.change === "up" ? "raised" : mark.change === "down" ? "lowered" : "left unchanged";
    return `${describeObservation(o)}${atPlace(o.place)}, ${formatIst(o.ts)}${
      o.synthetic ? ", synthetic" : ""
    }; ${change} its pipe's blockage`;
  }
  const n = mark.members.length;
  const synthetic = mark.members.filter((m) => m.synthetic).length;
  const provenance =
    synthetic === n ? ", all synthetic" : synthetic > 0 ? `, ${synthetic} synthetic` : "";
  const unchanged = n - mark.up - mark.down;
  const moves = [
    mark.up > 0 ? `${mark.up} raised` : null,
    mark.down > 0 ? `${mark.down} lowered` : null,
    unchanged > 0 ? `${unchanged} left unchanged` : null,
  ]
    .filter(Boolean)
    .join(", ");
  return `${n} ${GROUP_NOUN[mark.lane]} at ${formatIst(mark.ts)}${provenance}; ${moves} their pipes' blockage`;
}

/**
 * Every observation the cycle assimilated, on one line of time (SPEC.md 7.3's assimilation
 * timeline, compacted): traffic anomalies on the upper lane, citizen reports on the lower, each a
 * mark in its observation colour at its own time, with a notch for which way it moved its pipe's
 * blockage. Observations of one kind at one minute share a mark that carries their count - a
 * cycle's traffic anomalies all carry the cycle's time - and picking it lists them. Each mark is a
 * button named in words, so the colour is a second cue and never the only one.
 */
export function ObservationStrip({
  observations,
  cycleTs,
  selectedIds = null,
  onSelect,
  onSelectGroup,
  className,
}: ObservationStripProps) {
  const { start, end, marks } = stripLayout(observations, cycleTs);

  if (observations.length === 0) {
    return (
      <p className={cn("type-micro text-text-3", className)}>
        No observations assimilated at this cycle. Press Play on the replay: traffic anomalies and
        citizen reports arrive with the clock.
      </p>
    );
  }

  return (
    <div className={cn("min-w-0", className)}>
      <div className="flex">
        <div className="type-micro text-text-3 flex w-14 shrink-0 flex-col justify-around">
          <span>{LANE_LABEL.traffic}</span>
          <span>{LANE_LABEL.report}</span>
        </div>
        {/* Inset by half a mark either side, so a mark at the cycle is not cut by the edge. */}
        <div className="border-line relative h-12 min-w-0 flex-1 border-b">
          <span aria-hidden="true" className="bg-line absolute inset-x-0 top-1/2 h-px" />
          <ol
            aria-label="Observations assimilated, oldest first"
            className="absolute inset-y-0 right-3 left-3"
          >
            {marks.map((mark) => {
              const group = mark.members.length > 1;
              const selected = sameIds(mark, selectedIds);
              const label = markLabel(mark);
              const colour = cssVar(KIND_VAR[mark.lane]);
              const synthetic = mark.members.every((m) => m.synthetic);
              return (
                <li
                  key={mark.key}
                  className="absolute -translate-x-1/2"
                  style={{
                    left: `${mark.x * 100}%`,
                    top: mark.lane === "traffic" ? "2px" : "25px",
                  }}
                >
                  <button
                    type="button"
                    aria-label={label}
                    aria-pressed={selected}
                    title={label}
                    onClick={() =>
                      group ? onSelectGroup?.(mark.members) : onSelect?.(mark.members[0])
                    }
                    className={cn(
                      "focus-visible:outline-tide relative flex h-5 min-w-5 items-center justify-center rounded-full focus-visible:outline-2 focus-visible:outline-offset-1",
                      selected ? "ring-text ring-2" : "",
                    )}
                  >
                    {group ? (
                      <span
                        aria-hidden="true"
                        className="num text-ink rounded-chip flex h-4 min-w-4 items-center justify-center px-1 text-[11px] leading-none font-semibold"
                        style={{ backgroundColor: colour, opacity: synthetic ? 0.85 : 1 }}
                      >
                        {mark.members.length}
                      </span>
                    ) : (
                      <span
                        aria-hidden="true"
                        className="size-3 rounded-full"
                        style={{ backgroundColor: colour, opacity: synthetic ? 0.75 : 1 }}
                      />
                    )}
                    {mark.up > 0 ? (
                      <span
                        aria-hidden="true"
                        className="border-b-text absolute -top-1 left-1/2 size-0 -translate-x-1/2 border-x-[4px] border-b-[5px] border-x-transparent"
                      />
                    ) : null}
                    {mark.down > 0 ? (
                      <span
                        aria-hidden="true"
                        className="border-t-text absolute -bottom-1 left-1/2 size-0 -translate-x-1/2 border-x-[4px] border-t-[5px] border-x-transparent"
                      />
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ol>
        </div>
      </div>
      <div className="num type-micro text-text-3 flex justify-between pt-1 pr-3 pl-[68px]">
        <time dateTime={new Date(start).toISOString()}>
          {formatIst(new Date(start).toISOString())}
        </time>
        <time dateTime={new Date(end).toISOString()}>{formatIst(new Date(end).toISOString())}</time>
      </div>
    </div>
  );
}

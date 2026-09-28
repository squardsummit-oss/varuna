"use client";

import { useEffect, useState } from "react";

import { apiUrl } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { formatIstDate, formatIstTime } from "@/lib/stores/time";

export interface BakedCycle {
  runId: string;
  cycleTs: string;
  massBalanceErr: number | null;
  /** When the run was written. Decides which run represents a cycle that has been baked twice. */
  createdAt: string;
}

/**
 * One run per cycle time: the most recently written.
 *
 * The registry holds seventeen Mumbai runs across eight cycle times - the 2026-09-13 bake, the
 * 2026-09-23 re-bake and two live runs - and the row was drawing a chip for each, so three chips
 * read "08:10" and two read "09:10" with nothing on screen to tell them apart (found by the P10.2
 * design QA, 2026-09-24). Clicking one of three identical chips is a guess, and the picker exists
 * precisely so a judge does not have to guess which run they are looking at.
 *
 * **This row is the storm's timeline, not the run registry.** Distinguishing the chips instead
 * would mean printing `sky1.0-twin1.0-flash0.1-baked` seventeen times across the top of the map,
 * which is unreadable and is not what a picker of cycles is for. Every run is still listed, with
 * its versions, mode, wall-clock and mass balance, in the replay screen's cycle log. What is lost
 * here is the ability to reach an older bake by clicking; `?run=` still reaches it by name.
 *
 * **`currentRunId` wins its own cycle.** The screen may be showing a run that is not the newest of
 * its cycle - `/pumps` opens on the 09:10 *live* run while the registry's newest at 09:10 is the
 * baked one - and dropping it would leave no chip reading as selected, which is worse than showing
 * a slightly older run: the row would say nothing about where the reader is.
 */
export function newestPerCycle(runs: BakedCycle[], currentRunId?: string | null): BakedCycle[] {
  /** Should `run` take the cycle from `held`? */
  const replaces = (run: BakedCycle, held: BakedCycle | undefined): boolean => {
    if (!held) return true;
    // The run on screen keeps its own cycle, whichever of the two was written later.
    if (held.runId === currentRunId) return false;
    if (run.runId === currentRunId) return true;
    // `localeCompare` on ISO 8601 with a fixed offset orders by instant. A tie falls back to the
    // run id, so the choice is deterministic rather than dependent on the registry's order.
    if (run.createdAt !== held.createdAt) return run.createdAt.localeCompare(held.createdAt) > 0;
    return run.runId.localeCompare(held.runId) > 0;
  };

  const byCycle = new Map<string, BakedCycle>();
  for (const run of runs) {
    if (replaces(run, byCycle.get(run.cycleTs))) byCycle.set(run.cycleTs, run);
  }
  // Oldest cycle first: the row then reads as the storm's timeline, left to right.
  return [...byCycle.values()].sort((a, b) => a.cycleTs.localeCompare(b.cycleTs));
}

export interface CyclePickerProps {
  /** The run currently on screen, so its chip reads as selected. */
  currentRunId?: string | null;
  onPick?: (runId: string) => void;
  className?: string;
}

/**
 * The baked cycles of the replay, as a row of clock times (SPEC.md 7.8, task P6.11).
 *
 * Without this the console shows whichever run is newest, and on a replay that is the calm
 * cycle after the storm has passed - a judge landing on the deployed console would see the city
 * draining rather than flooding. The bundle's cycles are all there; this makes them reachable
 * without knowing to type `?run=` into the address bar.
 *
 * The times are the cycle's own, in IST, because that is what the demo script and the
 * ground-truth pins are quoted in.
 */
export function CyclePicker({ currentRunId, onPick, className }: CyclePickerProps) {
  const [cycles, setCycles] = useState<BakedCycle[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    fetch(apiUrl("/v1/runs"), { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : { runs: [] }))
      .then(
        (body: {
          runs?: {
            run_id: string;
            cycle_ts: string;
            mass_balance_err?: number;
            created_at?: string;
          }[];
        }) => {
          const runs = (body.runs ?? []).map((r) => ({
            runId: r.run_id,
            cycleTs: r.cycle_ts,
            massBalanceErr: r.mass_balance_err ?? null,
            // A registry row without `created_at` sorts before every dated one, so a run that
            // does not say when it was written never displaces one that does.
            createdAt: r.created_at ?? "",
          }));
          setCycles(newestPerCycle(runs, currentRunId));
        },
      )
      .catch(() => setCycles([]));
    return () => controller.abort();
  }, [currentRunId]);

  if (cycles.length === 0) return null;

  return (
    <div
      className={cn(
        "rounded-panel border-line flex items-center gap-1.5 border bg-[var(--ink)]/85 px-2 py-1.5 backdrop-blur-[12px]",
        className,
      )}
    >
      <span className="type-micro text-text-3 shrink-0 pr-1">Cycle</span>
      {cycles.map((cycle) => {
        const selected = cycle.runId === currentRunId;
        return (
          <button
            key={cycle.runId}
            type="button"
            aria-current={selected ? "true" : undefined}
            // The visible label is a time, which tells a screen reader nothing about what the
            // control does. The date comes from the cycle rather than a literal: this row is on
            // the Chennai design storm too, and "2 July 2019" was wrong there (SPEC.md 6.10).
            aria-label={`Forecast from ${formatIstTime(cycle.cycleTs)} IST, ${formatIstDate(cycle.cycleTs)}`}
            onClick={() => onPick?.(cycle.runId)}
            title={`Forecast from ${formatIstTime(cycle.cycleTs)} IST, ${formatIstDate(cycle.cycleTs)}`}
            className={cn(
              "num rounded-chip type-micro border px-2 py-0.5 transition-colors",
              selected
                ? "border-tide bg-tide/15 text-tide"
                : "border-line bg-well text-text-2 hover:text-text",
            )}
          >
            {formatIstTime(cycle.cycleTs)}
          </button>
        );
      })}
    </div>
  );
}

"use client";

import { EmptyState } from "@/components/varuna/empty-state";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatMs, formatMassBalance } from "@/lib/format";
import { formatIstTime } from "@/lib/stores/time";

export interface CycleLogRow {
  /**
   * The run this row is, as `run.json` names it (SPEC.md 10.3). It is the row's identity, not
   * its cycle time: a cycle can be in the registry more than once.
   */
  id: string;
  /** Cycle time, ISO 8601 with +05:30. */
  time: string;
  /** Stages that ran, e.g. "decode, sky, twin, flash, pulse, products". */
  stages: string;
  /** Total stage time in milliseconds. */
  ms: number;
  /** Mass-balance error as a fraction (0.0008 = 0.08 %); null when the cycle was baked without one. */
  massBalance: number | null;
}

export interface CycleLogProps {
  rows: CycleLogRow[];
}

/**
 * The engine versions and mode of a run id - everything from `sky` onward.
 *
 * `run_id` is `<CITY>-<cycle stamp>-sky<v>-twin<v>-flash<v>[-live|-baked]` (SPEC.md 10.3). The
 * city and the stamp are already in the row's own Time cell, so repeating them would spend a
 * column on nothing; what distinguishes two rows of the same cycle is the tail. A run id that does
 * not match the format is shown whole rather than guessed at.
 */
export function runVersionTail(runId: string): string {
  const at = runId.indexOf("-sky");
  return at === -1 ? runId : runId.slice(at + 1);
}

/**
 * The replay panel's cycle log: one row per published run with stage timings and mass balance.
 *
 * **One row per run, not per cycle** (found by the P10.2 design QA, 2026-09-24). The rows were
 * keyed on `time`, and the demo registry holds seventeen Mumbai runs across eight cycle times -
 * the 2026-09-13 and 2026-09-23 bakes plus a live run, three of them sharing 08:10 IST. React
 * warned twenty-nine times on a single load of `/replay` that children shared a key, which under
 * its own documented behaviour means rows may be "duplicated and/or omitted": the log was showing
 * a set of numbers no reader could attribute to a run, and section 14's zero-console-errors gate
 * was being missed on a screen nothing was checking. The key is the run id now, and the Run column
 * makes the duplicate times legible instead of merely non-fatal - 7.8's acceptance criterion asks
 * for "baked and live runs", which is exactly the case that collided.
 *
 * **The stage list is a caption, not a column.** 7.2 asks the log for "time, stages, ms,
 * mass-balance error", and stages were a column - printing the identical string on every row,
 * because the registry summary carries a run's total and not its per-stage split, so there is
 * nothing per-row to say. Five columns did not fit the console's 360 px replay panel: the last
 * one clipped mid-word ("decod"), which is how this was noticed. Said once above the table it is
 * the same claim, legible, and it leaves the Run column the width it needs. The real per-stage
 * timings are at `GET /v1/runs/{run_id}` and in the cycle budget bar.
 */
export function CycleLog({ rows }: CycleLogProps) {
  if (rows.length === 0) {
    return <EmptyState title="No cycles yet" description="Press Play on the replay." />;
  }

  // Every row carries the same list, so it is stated once. A registry that one day reports a run
  // that skipped a stage would break this claim, and the distinct values are listed rather than
  // the first row's, so it would read "decode, sky, twin, pulse, products / decode, sky, twin"
  // instead of quietly speaking for a run it does not describe.
  const stages = [...new Set(rows.map((row) => row.stages).filter(Boolean))];

  return (
    <div className="space-y-1.5">
      {stages.length > 0 ? (
        <p className="type-micro text-text-3">Stages each run: {stages.join(" / ")}.</p>
      ) : null}
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              <TableHead>Run</TableHead>
              <TableHead className="text-right">Time taken</TableHead>
              <TableHead className="text-right">Mass balance</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.id} className="h-8">
                <TableCell className="num">{formatIstTime(row.time)}</TableCell>
                {/* 6.3: mono is for run ids, and this is one. */}
                <TableCell className="type-micro text-text-3 font-mono" title={row.id}>
                  {runVersionTail(row.id)}
                </TableCell>
                <TableCell className="num text-right">{formatMs(row.ms)}</TableCell>
                <TableCell className="num text-right">
                  {formatMassBalance(row.massBalance)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

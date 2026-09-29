"use client";

import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { EmptyState } from "@/components/varuna/empty-state";
import { Skeleton } from "@/components/varuna/skeleton";
import { formatMs, formatMassBalance } from "@/lib/format";
import { formatIstTime } from "@/lib/stores/time";
import { cn } from "@/lib/utils";

export interface CycleLogRow {
  /**
   * The run this row is, as `run.json` names it (SPEC.md 10.3). It is the row's identity, not
   * its cycle time: a cycle can be in the registry more than once.
   */
  id: string;
  /** Cycle time, ISO 8601 with +05:30. */
  time: string;
  /** Stages that ran, as the registry summary names them. Not printed: the budget bar shows them. */
  stages: string;
  /** Total stage time in milliseconds. */
  ms: number;
  /** Mass-balance error as a fraction (0.0008 = 0.08 %); null when the cycle was baked without one. */
  massBalance: number | null;
}

export interface CycleLogProps {
  rows: CycleLogRow[];
  /** What the empty log tells the reader to do. */
  emptyDescription?: string;
  /** The row whose stage timings are shown beside the log; highlighted when set. */
  selectedId?: string | null;
  /** Makes each row's time a button that picks it. Without it the log is read-only. */
  onSelect?: (row: CycleLogRow) => void;
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
 * The cycle log: one row per published run with its total time and mass balance.
 *
 * **One row per run, not per cycle** (P10.2 design QA, 2026-09-24): rows are keyed on the run id,
 * because the registry can hold two bakes and a live run of one cycle, and keying on the time made
 * React drop rows. The Run column is what tells those rows apart.
 *
 * **No stage caption.** It used to print "Stages each run: decode, sky, twin, pulse, products"
 * from a constant, which named a decode stage no baked run reports and left out the Flash stage
 * every one of them does. The per-stage split is `GET /v1/runs/{run_id}`'s, and `/replay` draws it
 * in the cycle budget bar for the row picked here.
 */
export function CycleLog({
  rows,
  emptyDescription = "Run make bake to bake this bundle's cycles.",
  selectedId = null,
  onSelect,
}: CycleLogProps) {
  if (rows.length === 0) {
    // Not "Press Play": the log lists baked runs, and playing the clock bakes nothing.
    return <EmptyState title="No cycles yet" description={emptyDescription} />;
  }

  return (
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
          {rows.map((row) => {
            const selected = row.id === selectedId;
            return (
              <TableRow
                key={row.id}
                className={cn("h-8", selected && "bg-well")}
                aria-current={selected ? "true" : undefined}
              >
                <TableCell className="num">
                  {onSelect ? (
                    <button
                      type="button"
                      aria-pressed={selected}
                      aria-label={`Show the stage timings of the ${formatIstTime(row.time)} run`}
                      onClick={() => onSelect(row)}
                      className={cn(
                        "num rounded-control -mx-1 px-1 underline-offset-2 hover:underline",
                        "focus-visible:ring-tide outline-none focus-visible:ring-2",
                        selected ? "text-tide font-medium" : "text-text",
                      )}
                    >
                      {formatIstTime(row.time)}
                    </button>
                  ) : (
                    formatIstTime(row.time)
                  )}
                </TableCell>
                {/* 6.3: mono is for run ids, and this is one. */}
                <TableCell className="type-micro text-text-3 font-mono" title={row.id}>
                  {runVersionTail(row.id)}
                </TableCell>
                <TableCell className="num text-right">{formatMs(row.ms)}</TableCell>
                <TableCell className="num text-right">
                  {formatMassBalance(row.massBalance)}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

/** The registry read behind a cycle log: `useReplayCycleLog`'s result, typed loosely. */
export interface CycleLogSource {
  rows: CycleLogRow[];
  isPending: boolean;
  isError: boolean;
  error: { message: string } | null;
  refetch: () => unknown;
}

export interface CycleLogStateProps extends Omit<CycleLogProps, "rows" | "emptyDescription"> {
  bundleId: string;
  log: CycleLogSource;
}

/**
 * The cycle log with its loading and error states: a shimmer while the registry is on its way,
 * the API's own words and Try again when it failed, and an empty state only when it is empty.
 */
export function CycleLogState({ bundleId, log, ...props }: CycleLogStateProps) {
  if (log.isPending) {
    return (
      <div className="space-y-1.5" aria-busy="true" aria-label="Loading the cycle log">
        <Skeleton className="h-8" />
        <Skeleton className="h-8" />
        <Skeleton className="h-8" />
      </div>
    );
  }
  if (log.isError) {
    return (
      <EmptyState
        title="The cycle log did not load"
        description={log.error?.message ?? "The run registry did not answer."}
        action={
          <Button variant="outline" onClick={() => void log.refetch()}>
            Try again
          </Button>
        }
      />
    );
  }
  return (
    <CycleLog
      rows={log.rows}
      emptyDescription={`Run make bake BUNDLE=${bundleId} to bake its cycles.`}
      {...props}
    />
  );
}

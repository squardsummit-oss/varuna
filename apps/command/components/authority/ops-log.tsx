"use client";

/**
 * The append-only authority log (UI_SPEC 6, TECH_SPEC 3.6, task D-15).
 *
 * **This is the screen's evidence.** Every act on the desk is one line in `data/ops/<city>.jsonl`
 * and the log is the only record that an edit happened at all - nothing is written into a run, so
 * without this panel an officer would have no way to see what they had done. Newest first, with
 * the user and the time on every row, as UI_SPEC 6 requires.
 *
 * **Nothing is ever removed, and the panel says so.** A closure is lifted by appending a
 * reopening, not by deleting the line that made it, so the two rows sit above each other and the
 * history reads as it happened.
 *
 * **Each row says which half of the screen it came from.** `changesForecast` marks the kinds a
 * route or a feed reads - closure, reopening, pump status - so the split the desk is built around
 * is visible in the record afterwards and not only at the moment of acting.
 *
 * The panel does not fetch: the screen already loads the log to know whether this API accepts
 * writes at all, and a second read would be the same file twice.
 */

import { EmptyState } from "@/components/varuna/empty-state";
import { Panel } from "@/components/varuna/panel";
import { Skeleton } from "@/components/varuna/skeleton";
import { formatIst } from "@/lib/format";
import { changesForecast, describeEntry, type OpsEntry } from "@/lib/api/ops";

export interface OpsLogProps {
  /** Null while the log is loading. */
  entries: OpsEntry[] | null;
  /** Entries in the whole log, which may exceed the page shown. */
  total?: number;
  /** The API's sentence when the log could not be read. */
  error?: string | null;
  className?: string;
}

export function OpsLog({ entries, total = 0, error, className }: OpsLogProps) {
  return (
    <Panel
      className={className}
      title="Ops log"
      description="Every authority edit, newest first. Nothing is ever removed: a closure is lifted by appending a reopening."
      actions={
        total > 0 ? <span className="num type-micro text-text-3">{total} entries</span> : null
      }
    >
      {error ? (
        <p className="type-small text-text-2">{error}</p>
      ) : entries === null ? (
        <Skeleton lines={4} />
      ) : entries.length === 0 ? (
        <EmptyState
          size="sm"
          title="Nothing has been done at this desk"
          description="Close a street or set a pump's status; the row appears here with your name and the time."
        />
      ) : (
        <ul className="max-h-96 space-y-1.5 overflow-y-auto" aria-label="Ops log">
          {entries.map((entry) => (
            <li
              key={entry.id || `${entry.kind}-${entry.ts}`}
              className="rounded-control border-line bg-well/40 border p-2.5"
            >
              <p className="type-small text-text">{describeEntry(entry)}</p>
              <p className="num type-micro text-text-3">
                {entry.user} · {formatIst(entry.ts)} ·{" "}
                {changesForecast(entry.kind) ? "read by the router" : "record only"}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

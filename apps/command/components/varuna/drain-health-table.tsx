"use client";

import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";

import { EmptyState } from "@/components/varuna/empty-state";
import { formatBeta, formatCount, formatIst, formatPct } from "@/lib/format";
import { cssVar, drainBand } from "@/lib/ramps";
import { cn } from "@/lib/utils";

/** One inferred pipe as Pulse leaves it after a cycle (run artifact `drain_health.geojson`). */
export interface DrainHealthRow {
  /** Pipe id in the inferred graph, e.g. "E-01842". */
  id: string;
  /** Street the pipe runs under, e.g. "Dr Ambedkar Road". */
  street: string;
  /** Posterior blockage beta, 0 clear to 1 blocked. */
  betaMean: number;
  /** Posterior standard deviation of beta. */
  betaSd: number;
  /** Capacity lost to the blockage, as a fraction in [0, 1]. */
  capacityReduction: number;
  /** Hotspots this pipe helps explain, e.g. ["Hindmata junction"]. */
  hotspotsExplained: string[];
  /** Observations assimilated into this pipe so far. */
  observations: number;
  /** ISO 8601 with +05:30 of the last assimilation that moved this pipe. */
  lastUpdated: string;
  /**
   * What this cycle's update moved the blockage by, posterior minus prior. Absent where the
   * caller has no prior; the column then prints a dash rather than a zero it does not know.
   */
  betaDelta?: number;
}

/** Columns the operator can sort by; the rest are labels. */
export type DrainSortKey = "beta" | "sd" | "capacity" | "change";
export type SortDirection = "asc" | "desc";

const SORT_LABELS: Record<DrainSortKey, string> = {
  beta: "Blockage",
  sd: "Uncertainty",
  capacity: "Capacity reduction",
  change: "Learned change",
};

function sortValue(row: DrainHealthRow, key: DrainSortKey): number {
  if (key === "beta") return row.betaMean;
  if (key === "sd") return row.betaSd;
  // By size, so a pipe an observation cleared ranks beside one it raised by as much.
  if (key === "change") return Math.abs(row.betaDelta ?? 0);
  return row.capacityReduction;
}

/** Sorts a copy of `rows`; worst-first by default so the desilting list reads top-down. */
export function sortDrainRows(
  rows: readonly DrainHealthRow[],
  key: DrainSortKey,
  direction: SortDirection,
): DrainHealthRow[] {
  const factor = direction === "asc" ? 1 : -1;
  return [...rows].sort(
    (a, b) => (sortValue(a, key) - sortValue(b, key)) * factor || a.id.localeCompare(b.id),
  );
}

/** "+0.34" or "-0.19": a learned change in blockage, signed. */
export function formatBetaDelta(delta: number | undefined): string {
  if (typeof delta !== "number" || !Number.isFinite(delta)) return "—";
  if (Math.abs(delta) < 0.005) return "0.00";
  return `${delta > 0 ? "+" : "-"}${Math.abs(delta).toFixed(2)}`;
}

function SortButton({
  column,
  sortKey,
  direction,
  onToggle,
}: {
  column: DrainSortKey;
  sortKey: DrainSortKey;
  direction: SortDirection;
  onToggle: (key: DrainSortKey) => void;
}) {
  const active = column === sortKey;
  const Icon = direction === "asc" ? ArrowUp : ArrowDown;
  return (
    <button
      type="button"
      onClick={() => onToggle(column)}
      className={cn(
        "rounded-control hover:text-text focus-visible:outline-tide inline-flex items-center gap-1 px-1 py-0.5 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2",
        active ? "text-text" : "text-text-2",
      )}
    >
      {SORT_LABELS[column]}
      {active ? <Icon size={12} strokeWidth={1.75} aria-hidden="true" /> : null}
    </button>
  );
}

export interface DrainHealthTableProps {
  rows: readonly DrainHealthRow[];
  /**
   * The column the table opens sorted by, descending. Blockage by default (the desilting order);
   * `/drains` opens on learned change, because the top of the blockage ranking is the land-use
   * prior rather than anything Pulse learned.
   */
  defaultSort?: DrainSortKey;
  className?: string;
}

/**
 * The desilting priority list (SPEC.md section 7.3): one row per inferred pipe with its
 * posterior blockage and the hotspots it explains. Blockage carries the magenta drain ramp as a
 * dot, but the number is always printed so colour is never the only carrier of meaning.
 */
export function DrainHealthTable({ rows, defaultSort = "beta", className }: DrainHealthTableProps) {
  const [sortKey, setSortKey] = useState<DrainSortKey>(defaultSort);
  const [direction, setDirection] = useState<SortDirection>("desc");

  const sorted = useMemo(() => sortDrainRows(rows, sortKey, direction), [rows, sortKey, direction]);

  if (rows.length === 0) {
    return (
      <EmptyState
        size="sm"
        title="No drain health yet"
        description="Press Play on the replay: Pulse writes each pipe's blockage every cycle."
        className={className}
      />
    );
  }

  const toggle = (key: DrainSortKey) => {
    if (key === sortKey) {
      setDirection((d) => (d === "desc" ? "asc" : "desc"));
      return;
    }
    setSortKey(key);
    setDirection("desc");
  };

  const ariaSort = (key: DrainSortKey): "ascending" | "descending" | "none" =>
    key === sortKey ? (direction === "asc" ? "ascending" : "descending") : "none";

  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">
          Inferred pipes by posterior blockage, sortable by blockage, uncertainty, capacity
          reduction and learned change
        </caption>
        <thead>
          <tr className="border-line type-micro text-text-2 border-b">
            <th scope="col" className="py-2 pr-3 font-medium">
              Pipe
            </th>
            <th scope="col" aria-sort={ariaSort("change")} className="py-2 pr-3 font-medium">
              <SortButton
                column="change"
                sortKey={sortKey}
                direction={direction}
                onToggle={toggle}
              />
            </th>
            <th scope="col" aria-sort={ariaSort("beta")} className="py-2 pr-3 font-medium">
              <SortButton column="beta" sortKey={sortKey} direction={direction} onToggle={toggle} />
            </th>
            <th scope="col" aria-sort={ariaSort("sd")} className="py-2 pr-3 font-medium">
              <SortButton column="sd" sortKey={sortKey} direction={direction} onToggle={toggle} />
            </th>
            <th scope="col" aria-sort={ariaSort("capacity")} className="py-2 pr-3 font-medium">
              <SortButton
                column="capacity"
                sortKey={sortKey}
                direction={direction}
                onToggle={toggle}
              />
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Explains
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Observations
            </th>
            <th scope="col" className="py-2 font-medium">
              Last updated
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const band = drainBand(row.betaMean);
            return (
              <tr key={row.id} className="border-line type-small border-b last:border-b-0">
                <th scope="row" className="py-2 pr-3 text-left font-medium">
                  <span className="text-text block">{row.street}</span>
                  <span className="num type-micro text-text-3 block font-normal">{row.id}</span>
                </th>
                <td className="num text-text py-2 pr-3">{formatBetaDelta(row.betaDelta)}</td>
                <td className="py-2 pr-3">
                  <span className="inline-flex items-center gap-2">
                    <span
                      aria-hidden="true"
                      className="size-2 shrink-0 rounded-full"
                      style={{ backgroundColor: cssVar(`--drain-${band.key}`) }}
                    />
                    <span className="num text-text">{formatBeta(row.betaMean)}</span>
                  </span>
                </td>
                <td className="num text-text-2 py-2 pr-3">{formatBeta(row.betaSd)}</td>
                <td className="num text-text-2 py-2 pr-3">{formatPct(row.capacityReduction)}</td>
                <td className="text-text-2 py-2 pr-3">
                  {row.hotspotsExplained.length > 0 ? row.hotspotsExplained.join(", ") : "—"}
                </td>
                <td className="num text-text-2 py-2 pr-3">{formatCount(row.observations)}</td>
                <td className="num text-text-2 py-2">{formatIst(row.lastUpdated)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

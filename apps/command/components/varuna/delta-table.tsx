"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { DepthChip } from "@/components/varuna/depth-chip";
import { EmptyState } from "@/components/varuna/empty-state";
import { formatMinutes } from "@/lib/format";
import { cn } from "@/lib/utils";

/** One hotspot's (or street's) before and after under a what-if scenario (SPEC.md 7.7). */
export interface DeltaRow {
  id: string;
  /** Hotspot or street name, e.g. "Hindmata junction". */
  hotspot: string;
  /** p50 peak depth in cm before the scenario. */
  beforeCm: number;
  /**
   * The run stores no depth for this street because it stayed below this many centimetres, so
   * `beforeCm` is the 0 the change was read from, not a measurement. The cell says "Below 5 cm"
   * instead of drawing a chip at 0 (rule 6).
   */
  beforeBelowCm?: number;
  /** p50 peak depth in cm after the scenario. */
  afterCm: number;
  /** Minutes the hotspot is impassable for cars (above 30 cm) before the scenario.
   *
   * Optional, and the column only renders when a row carries it. `POST /v1/whatif` returns peak
   * depth per segment and no duration, so the what-if lab passes neither: a "0 min -> 0 min"
   * printed in every row is a number nothing computed, which is what rule 6 forbids. When the
   * emulator gains a per-step exceedance the field comes back and the column returns with it. */
  minutesImpassableBefore?: number;
  /** The same after the scenario. */
  minutesImpassableAfter?: number;
  /**
   * Minutes above 45 cm, where buses stop (SPEC.md 6.2), before the scenario. Drawn only when
   * the table is given `minutes45Label` and a row carries it; both engines return it.
   */
  minutesAbove45Before?: number;
  /** The same after the scenario. */
  minutesAbove45After?: number;
}

export interface DeltaTableProps {
  rows: DeltaRow[];
  /** First column's heading: "Hotspot" in the per-hotspot table, "Street" in the largest changes. */
  nameLabel?: string;
  /** The minutes column's heading, naming its threshold: "Minutes above 30 cm". */
  minutesLabel?: string;
  /**
   * The second minutes column's heading, "Minutes above 45 cm". Absent, the column is not drawn,
   * which is how a narrow panel keeps to one minutes column.
   */
  minutes45Label?: string;
  /** Empty state; the default invites a first run. */
  emptyTitle?: string;
  emptyDescription?: string;
  className?: string;
}

/** Signed centimetre change: "-35 cm", "+4 cm", "0 cm". */
export function formatDeltaCm(beforeCm: number, afterCm: number): string {
  const delta = Math.round(afterCm) - Math.round(beforeCm);
  if (delta === 0) return "0 cm";
  return `${delta < 0 ? "-" : "+"}${Math.abs(delta)} cm`;
}

/**
 * Before and after per hotspot for a what-if result. Depth shows as chips on the fixed ramp so the
 * table agrees with the diff layer; the change column is signed and coloured only as a hint.
 */
export function DeltaTable({
  rows,
  nameLabel = "Hotspot",
  minutesLabel = "Minutes impassable",
  minutes45Label,
  emptyTitle = "No what-if yet",
  emptyDescription = "Set the controls and run one.",
  className,
}: DeltaTableProps) {
  // The column appears only when something actually measured a duration. Rendering it against
  // rows that carry none printed "0 min -> 0 min" on every line, which reads as a computed
  // result rather than an absent one (rule 6).
  const showMinutes = rows.some(
    (row) => row.minutesImpassableBefore !== undefined || row.minutesImpassableAfter !== undefined,
  );
  const showMinutes45 =
    minutes45Label !== undefined &&
    rows.some(
      (row) => row.minutesAbove45Before !== undefined || row.minutesAbove45After !== undefined,
    );

  if (rows.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} className={className} />;
  }

  return (
    <div className={cn("overflow-x-auto", className)}>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{nameLabel}</TableHead>
            <TableHead>Before</TableHead>
            <TableHead>After</TableHead>
            <TableHead className="text-right">Change</TableHead>
            {showMinutes ? <TableHead className="text-right">{minutesLabel}</TableHead> : null}
            {showMinutes45 ? <TableHead className="text-right">{minutes45Label}</TableHead> : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => {
            const improved = row.afterCm < row.beforeCm;
            const worse = row.afterCm > row.beforeCm;
            return (
              <TableRow key={row.id} className="h-10">
                <TableCell className="text-text font-medium">{row.hotspot}</TableCell>
                <TableCell>
                  {row.beforeBelowCm !== undefined ? (
                    <span className="type-micro text-text-3">
                      Below <span className="num">{row.beforeBelowCm}</span> cm
                    </span>
                  ) : (
                    <DepthChip cm={row.beforeCm} size="sm" />
                  )}
                </TableCell>
                <TableCell>
                  <DepthChip cm={row.afterCm} size="sm" />
                </TableCell>
                <TableCell
                  className={cn(
                    "num text-right",
                    improved && "text-tide",
                    worse && "text-text",
                    !improved && !worse && "text-text-3",
                  )}
                >
                  {formatDeltaCm(row.beforeCm, row.afterCm)}
                </TableCell>
                {showMinutes ? (
                  <TableCell className="num text-text-2 text-right">
                    {formatMinutes(row.minutesImpassableBefore ?? 0)} &rarr;{" "}
                    <span className="text-text">
                      {formatMinutes(row.minutesImpassableAfter ?? 0)}
                    </span>
                  </TableCell>
                ) : null}
                {showMinutes45 ? (
                  <TableCell className="num text-text-2 text-right">
                    {formatMinutes(row.minutesAbove45Before ?? 0)} &rarr;{" "}
                    <span className="text-text">{formatMinutes(row.minutesAbove45After ?? 0)}</span>
                  </TableCell>
                ) : null}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

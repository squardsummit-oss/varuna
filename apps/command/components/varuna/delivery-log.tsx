import { Send } from "lucide-react";

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
import { formatIst } from "@/lib/format";
import { cn } from "@/lib/utils";

/** One line of the log, in the API's words (`GET /v1/alerts/delivery`). */
export interface DeliveryLogRow {
  id: string;
  /** "Dashboard", "WhatsApp mock", "SMS mock", "Real send (twilio)". */
  label: string;
  /** The place the alert is about, so rows for different alerts can be told apart. */
  alert?: string | null;
  kind: "mock" | "real";
  /** "Shown on the alert queue", "Rendered, not sent", "Sent", "Failed", "Refused". */
  status: string;
  /** ISO 8601 with +05:30. */
  ts: string;
  /** A real send's recipient, masked by the API; never the whole number. */
  toMasked?: string | null;
  /** Why a real send did not go. */
  error?: string | null;
  user?: string | null;
}

export interface DeliveryLogProps {
  rows: DeliveryLogRow[] | null;
  /** True while the log is being read. */
  loading?: boolean;
  /** What went wrong reading it, in words; replaces the table. */
  error?: string | null;
  /** The API's own lines under the table: what the mocks are, whether a sender exists. */
  notes?: string[];
  className?: string;
}

/**
 * The delivery log (SPEC.md 7.5, task P8.8): what happened to each alert on each channel.
 *
 * **It never claims a send that did not happen.** The prototype's three channels are renders -
 * shown on the queue, shown on the phone mock, an SMS rendered and not sent - and the rows say so
 * in those words. A real send appears only when the API recorded one, with the provider's answer:
 * sent, failed or refused, and the masked number it went to.
 */
export function DeliveryLog({
  rows,
  loading = false,
  error,
  notes = [],
  className,
}: DeliveryLogProps) {
  if (loading) {
    return (
      <div className={cn("flex flex-col gap-2", className)} aria-busy="true">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <EmptyState
        size="sm"
        icon={Send}
        title="Delivery log unavailable"
        description={error}
        className={className}
      />
    );
  }
  if (!rows || rows.length === 0) {
    return (
      <EmptyState
        size="sm"
        icon={Send}
        title="Nothing sent yet"
        description="Sends appear here once this cycle raises an alert."
        className={className}
      />
    );
  }
  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Channel</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Time</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.id} data-kind={row.kind}>
                <TableCell className="whitespace-normal">
                  {row.label}
                  {row.alert ? (
                    <span className="type-micro text-text-3 block">{row.alert}</span>
                  ) : null}
                  {row.kind === "real" && row.toMasked ? (
                    <span className="type-micro text-text-3 block">to {row.toMasked}</span>
                  ) : null}
                </TableCell>
                <TableCell className="whitespace-normal">
                  {row.status}
                  {row.error ? (
                    <span className="type-micro text-text-3 block">{row.error}</span>
                  ) : null}
                </TableCell>
                <TableCell className="num">{row.ts ? formatIst(row.ts) : "-"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {notes.map((note) => (
        <p key={note} className="type-micro text-text-3">
          {note}
        </p>
      ))}
    </div>
  );
}

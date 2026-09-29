"use client";

import { MessageSquareWarning } from "lucide-react";

import { Skeleton } from "@/components/varuna/skeleton";
import type { ReportPin } from "@/lib/api/reports";
import { formatIst } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The chip a reporter picked, as the console prints it. */
const HINT: Record<string, string> = {
  ankle: "Ankle deep",
  knee: "Knee deep",
  waist: "Waist deep",
};

export interface CitizenReportsCardProps {
  /** Newest first. The card prints the first `limit`. */
  reports: readonly ReportPin[];
  /** False until the first answer from `GET /v1/reports`. */
  loaded: boolean;
  /** The API's sentence when the latest read failed; the last list stays on screen. */
  error?: string | null;
  selectedId?: string | null;
  onPick?: (report: ReportPin) => void;
  limit?: number;
  className?: string;
}

/**
 * The newest citizen reports on the console (Drishti), beside the map that pins them.
 *
 * A complaint raised at `/report` is the same object the ward desk and the citizen dashboard read,
 * so it lands here within one poll. Each row says where, how deep the reporter said the water was,
 * when, and what the desk has done about it; pressing a row flies the map to the pin.
 */
export function CitizenReportsCard({
  reports,
  loaded,
  error = null,
  selectedId = null,
  onPick,
  limit = 4,
  className,
}: CitizenReportsCardProps) {
  const shown = reports.slice(0, limit);
  return (
    <section
      aria-labelledby="console-reports-title"
      className={cn(
        "rounded-panel border-line w-[248px] shrink-0 border bg-[var(--ink)]/85 backdrop-blur-[12px]",
        className,
      )}
    >
      <div className="border-line flex h-9 items-center gap-2 border-b px-3">
        <MessageSquareWarning
          size={16}
          strokeWidth={1.75}
          className="text-text-2 shrink-0"
          aria-hidden="true"
        />
        <h2 id="console-reports-title" className="type-small text-text flex-1">
          Citizen reports
        </h2>
        {loaded && reports.length > 0 ? (
          <span className="num type-micro text-text-2">{reports.length}</span>
        ) : null}
      </div>
      {!loaded ? (
        <Skeleton lines={2} className="p-3" />
      ) : shown.length === 0 ? (
        <p className="type-micro text-text-2 p-3">
          {error ?? "No reports yet. A report sent from the public map appears here within 30 s."}
        </p>
      ) : (
        <ul className="p-1">
          {shown.map((report) => {
            const where = report.place ?? report.text ?? "Reported spot";
            const hint = report.depthHint ? (HINT[report.depthHint] ?? report.depthHint) : null;
            const when = report.ts ? formatIst(report.ts) : null;
            return (
              <li key={report.id}>
                <button
                  type="button"
                  onClick={() => onPick?.(report)}
                  aria-pressed={selectedId === report.id}
                  className={cn(
                    "rounded-control hover:bg-well focus-visible:ring-tide flex w-full flex-col gap-0.5 px-2 py-1.5 text-left focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset",
                    selectedId === report.id && "bg-well",
                  )}
                >
                  <span className="type-small text-text truncate">{where}</span>
                  <span className="num type-micro text-text-2 truncate">
                    {[hint, when, report.statusLabel].filter(Boolean).join(", ")}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {loaded && error && shown.length > 0 ? (
        <p className="type-micro text-text-2 border-line border-t px-3 py-2">{error}</p>
      ) : null}
    </section>
  );
}

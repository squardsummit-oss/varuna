"use client";

/**
 * The citizen dashboard's two report sections and the card a map pin opens.
 *
 * - **Complaints near you**: what people have reported, nearest first once the reader's position
 *   or starting place is known, newest first otherwise. Each is the same `ReportCard` the ward
 *   desk reads, so a photo, its credit, a seeded report's "Demo report (synthetic)" and the desk's
 *   status read the same on both screens.
 * - **My reports**: the reports this browser sent, each with the status the ward desk gave it and
 *   the officer's note, polled so the desk's act reaches the reporter.
 *
 * Nothing here composes a reason of its own. When the API says a photo was not kept, or that it
 * has no report by an id, its sentence is what the reader sees.
 */

import Link from "next/link";
import type { Route } from "next";
import { MapPin, Umbrella, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { EmptyState } from "@/components/varuna/empty-state";
import { Skeleton } from "@/components/varuna/skeleton";
import type { PublicReport } from "@/lib/api/reports";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import type { MapPoint } from "./citizen-map";
import { ReportCard } from "./report-card";
import { REPORTS_POLL_MS, type MyReportState } from "./use-report-feeds";

const REPORT_ROUTE = "/report" as Route;

/** Cards shown before "Show all"; a phone's sheet is not a feed. */
export const COMPLAINTS_SHOWN = 4;

const POLL_WORDS = `every ${REPORTS_POLL_MS / 1000} s`;

/** Straight-line metres, flat-earth, which is exact enough across one city. */
export function metresApart(from: MapPoint, to: MapPoint): number {
  const dy = (to.lat - from.lat) * 111_320;
  const dx = (to.lon - from.lon) * 111_320 * Math.cos((from.lat * Math.PI) / 180);
  return Math.hypot(dx, dy);
}

/** "About 800 m away", "About 3.2 km away". The public list rounds positions to about 110 m. */
export function distanceWords(metres: number): string {
  const hundreds = Math.max(100, Math.round(metres / 100) * 100);
  if (hundreds < 1_000) return `About ${hundreds} m away`;
  return `About ${(metres / 1_000).toFixed(1)} km away`;
}

/** Nearest first when there is a centre; the list is already newest first otherwise. */
export function orderComplaints(
  reports: readonly PublicReport[],
  centre: MapPoint | null,
): PublicReport[] {
  if (!centre) return [...reports];
  return [...reports].sort(
    (a, b) =>
      metresApart(centre, { lon: a.lon, lat: a.lat }) -
      metresApart(centre, { lon: b.lon, lat: b.lat }),
  );
}

export function ReportWaterLink({
  variant = "outline",
  className,
}: {
  variant?: "default" | "outline";
  className?: string;
}) {
  return (
    <Link
      href={REPORT_ROUTE}
      className={cn(buttonVariants({ variant, size: "lg" }), "h-11", className)}
    >
      <Umbrella aria-hidden="true" />
      Report water
    </Link>
  );
}

export interface ComplaintsNearYouProps {
  reports: readonly PublicReport[];
  centre: MapPoint | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  loaded: boolean;
  error: string | null;
  /** The API's own notes on the list, printed under it. */
  notes?: readonly string[];
  /** Distinct per mount, since the dashboard mounts the rail in one of two shapes. */
  headingId?: string;
}

export function ComplaintsNearYou({
  reports,
  centre,
  selectedId,
  onSelect,
  loaded,
  error,
  notes = [],
  headingId = "complaints-near-you",
}: ComplaintsNearYouProps) {
  const [showAll, setShowAll] = useState(false);
  const ordered = orderComplaints(reports, centre);
  const shown = showAll ? ordered : ordered.slice(0, COMPLAINTS_SHOWN);
  const title = centre ? "Complaints near you" : "Recent complaints in Mumbai";

  return (
    <section aria-labelledby={headingId} data-slot="complaints" className="flex flex-col gap-3">
      <div>
        <h2 id={headingId} className="font-display text-h3 text-text">
          {title}
        </h2>
        <p className="type-micro text-text-2 mt-0.5">
          What people reported and what the ward desk did. Each is a map pin.
        </p>
      </div>

      {!loaded ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : ordered.length === 0 ? (
        error ? (
          <p className="border-line bg-deep rounded-panel type-small text-text-2 border p-3">
            Complaints did not load: {error} The dashboard asks again {POLL_WORDS}.
          </p>
        ) : (
          <EmptyState
            size="sm"
            title="No complaints yet"
            description="A report of water appears here and on the map."
          />
        )
      ) : (
        <>
          {error ? (
            <p className="type-micro text-status-degraded">
              The latest check failed: {error} Showing the last list; asking again {POLL_WORDS}.
            </p>
          ) : null}
          <ul className="flex flex-col gap-3">
            {shown.map((report) => {
              const selected = report.id === selectedId;
              return (
                <li key={report.id} className="flex flex-col gap-1">
                  <ReportCard report={report} selected={selected} />
                  <div className="flex items-center justify-between gap-3">
                    {centre ? (
                      <span className="num type-micro text-text-3">
                        {distanceWords(metresApart(centre, { lon: report.lon, lat: report.lat }))}
                      </span>
                    ) : (
                      <span />
                    )}
                    <Button
                      variant="ghost"
                      className="h-11"
                      aria-pressed={selected}
                      onClick={() => onSelect(report.id)}
                    >
                      <MapPin aria-hidden="true" />
                      {selected ? "Shown on the map" : "Show on the map"}
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
          {ordered.length > COMPLAINTS_SHOWN ? (
            <Button
              variant="outline"
              className="h-11 self-start"
              aria-expanded={showAll}
              onClick={() => setShowAll((all) => !all)}
            >
              {showAll ? "Show fewer" : `Show all ${ordered.length}`}
            </Button>
          ) : null}
        </>
      )}

      {notes.length > 0 ? (
        <details className="type-micro text-text-3">
          <summary className="text-text-2 hover:text-text focus-visible:outline-tide cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2">
            About these reports
          </summary>
          {notes.map((note) => (
            <p key={note} className="mt-1">
              {note}
            </p>
          ))}
        </details>
      ) : null}
    </section>
  );
}

export interface MyReportsPanelProps {
  items: readonly MyReportState[];
  /** False until this browser's storage has been read. */
  ready: boolean;
  headingId?: string;
}

/** The reader's own reports and what the ward desk has done with each. */
export function MyReportsPanel({ items, ready, headingId = "my-reports" }: MyReportsPanelProps) {
  return (
    <section aria-labelledby={headingId} data-slot="my-reports" className="flex flex-col gap-3">
      <div>
        <h2 id={headingId} className="font-display text-h3 text-text">
          My reports
        </h2>
        <p className="type-micro text-text-2 mt-0.5">
          Sent from this browser, with the ward desk&apos;s status.
        </p>
      </div>

      {!ready ? (
        <Skeleton className="h-16 w-full" />
      ) : items.length === 0 ? (
        <div className="border-line rounded-panel flex flex-col items-start gap-3 border p-4">
          <p className="type-small text-text">You have not sent a report from this browser.</p>
          <p className="type-small text-text-2">
            Press Report water and say how deep it is. Its status appears here.
          </p>
          <ReportWaterLink variant="default" />
        </div>
      ) : (
        <>
          <ul className="flex flex-col gap-3">
            {items.map((item) => (
              <li key={item.entry.id} className="flex flex-col gap-1" data-slot="my-report">
                <p className="num type-micro text-text-3">
                  Sent from this browser {formatDateTime(item.entry.sentAt)}
                </p>
                <MyReportBody item={item} />
              </li>
            ))}
          </ul>
          <ReportWaterLink className="self-start" />
        </>
      )}
    </section>
  );
}

function MyReportBody({ item }: { item: MyReportState }) {
  if (item.kind === "loading") {
    return (
      <div aria-busy="true">
        <span className="sr-only">Checking this report&apos;s status</span>
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }
  if (item.kind === "ready") return <ReportCard report={item.report} />;
  if (item.kind === "missing") {
    return (
      <p className="border-line bg-deep rounded-panel type-small text-text-2 border p-3">
        {item.message}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      {item.report ? <ReportCard report={item.report} /> : null}
      <p className="type-micro text-status-degraded">
        Its status could not be checked: {item.message} Asking again {POLL_WORDS}.
      </p>
    </div>
  );
}

export interface SelectedReportCardProps {
  report: PublicReport;
  onClose: () => void;
  /**
   * Take focus when a report opens. For a card opened from a control that has just gone away -
   * the phone's list, in a sheet that closes to show the map - so focus is not dropped to the page.
   */
  focusOnOpen?: boolean;
  className?: string;
}

/**
 * The card a pin opens, over the map, with a way to put it away. It follows the page's h1 with no
 * section of its own, so its heading is an h2.
 */
export function SelectedReportCard({
  report,
  onClose,
  focusOnOpen = false,
  className,
}: SelectedReportCardProps) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focusOnOpen) box.current?.focus({ preventScroll: true });
  }, [focusOnOpen, report.id]);

  return (
    <div
      ref={box}
      data-slot="selected-report"
      // Focusable by script only, so it can receive focus without joining the tab order.
      tabIndex={-1}
      className={cn("focus-visible:outline-tide relative focus-visible:outline-2", className)}
    >
      <ReportCard report={report} selected headingLevel={2} className="pr-14" />
      <Button
        variant="ghost"
        size="icon"
        aria-label="Close this report"
        onClick={onClose}
        className="absolute top-1 right-1 size-11"
      >
        <X aria-hidden="true" />
      </Button>
    </div>
  );
}

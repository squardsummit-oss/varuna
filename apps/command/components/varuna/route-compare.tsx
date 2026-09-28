"use client";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/varuna/empty-state";
import { Panel } from "@/components/varuna/panel";
import { MISSING, formatCm, formatIst, formatKm, formatMinutes, formatPct } from "@/lib/format";
import { listName } from "@/lib/street-label";
import { cn } from "@/lib/utils";

/** A segment the VARUNA route stepped around, with the probability at the time it was reached. */
export interface AvoidedSegment {
  segmentId: string;
  /**
   * The street as the API names it: OSM's name, else a label that says where it is ("off Dr
   * Ambedkar Road", "Service road near Wadala Depot"; `varuna_api.street_names`).
   */
  name: string;
  /** P(impassable for the profile) at the arrival time, 0 to 1. */
  probability: number;
  /** ISO 8601 with +05:30 of the moment it would have been reached. */
  atTs?: string;
}

/** One alternate route offered beside the chosen one. */
export interface RouteAlternate {
  id: string;
  label: string;
  etaMin?: number;
  maxDepthCm?: number;
}

/** One column of the comparison: naive shortest path, or the VARUNA route. */
export interface RouteSummary {
  /** Travel time in minutes. */
  etaMin?: number;
  /** Route length in metres. */
  distanceM?: number;
  /** Deepest expected water on the route, in cm. */
  maxDepthCm?: number;
  /** ISO 8601 with +05:30 until which the route stays passable for the profile. */
  safeUntil?: string;
  avoided?: AvoidedSegment[];
  alternates?: RouteAlternate[];
}

export interface RouteCompareProps {
  naive?: RouteSummary | null;
  varuna?: RouteSummary | null;
  className?: string;
}

const DASH = <span className="text-text-3">{MISSING}</span>;

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="border-line flex items-baseline justify-between gap-3 border-b py-2 last:border-b-0">
      <span className="type-micro text-text-2">{label}</span>
      <span className="num type-small text-text">{value}</span>
    </div>
  );
}

function Column({
  title,
  note,
  summary,
  accent,
}: {
  title: string;
  note: string;
  summary?: RouteSummary | null;
  accent: "naive" | "varuna";
}) {
  return (
    <section aria-label={title} className="rounded-panel border-line bg-well min-w-0 border p-3">
      <header className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className={cn(
            "h-0.5 w-6 shrink-0 rounded-full",
            accent === "varuna" ? "bg-tide" : "bg-naive",
          )}
        />
        <h3 className="type-small text-text font-medium">{title}</h3>
      </header>
      <p className="type-micro text-text-3 mt-1">{note}</p>
      <div className="mt-2">
        <Metric
          label="ETA"
          value={summary?.etaMin === undefined ? DASH : formatMinutes(summary.etaMin)}
        />
        <Metric
          label="Distance"
          value={summary?.distanceM === undefined ? DASH : formatKm(summary.distanceM)}
        />
        <Metric
          label="Max expected depth"
          value={summary?.maxDepthCm === undefined ? DASH : formatCm(summary.maxDepthCm)}
        />
        <Metric
          label="Safe until"
          value={summary?.safeUntil === undefined ? DASH : `${formatIst(summary.safeUntil)} IST`}
        />
      </div>
    </section>
  );
}

/**
 * Naive versus VARUNA (SPEC.md section 7.4): the two routes side by side, the segments VARUNA
 * stepped around with the probability at the time it would have reached them, and the alternates.
 * Every metric shows an em dash until a route exists, so the layout never jumps when one arrives.
 */
export function RouteCompare({ naive, varuna, className }: RouteCompareProps) {
  const avoided = varuna?.avoided ?? [];
  const alternates = varuna?.alternates ?? [];
  const hasRoute = Boolean(naive || varuna);

  return (
    <Panel
      title="Naive versus VARUNA"
      description="Same trip, same clock: one route ignores the forecast, the other reads it."
      className={className}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Column
          title="Naive (shortest)"
          note="Shortest path on the road graph, water ignored."
          summary={naive}
          accent="naive"
        />
        <Column
          title="VARUNA"
          note="Time-dependent cost with the segment forecast at arrival time."
          summary={varuna}
          accent="varuna"
        />
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <section aria-label="Avoided segments">
          <h3 className="type-small text-text font-medium">Avoided</h3>
          {avoided.length === 0 ? (
            <EmptyState
              size="sm"
              title="Nothing avoided yet"
              description="Find a route: the segments VARUNA steps around are listed here with their probability."
            />
          ) : (
            <ul className="mt-2 space-y-1.5">
              {avoided.map((segment) => (
                <li
                  key={segment.segmentId}
                  className="border-line flex items-baseline justify-between gap-3 border-b py-1.5 last:border-b-0"
                >
                  <span
                    className="type-small text-text min-w-0 truncate"
                    title={listName(segment.name)}
                  >
                    {listName(segment.name)}
                  </span>
                  <span className="num type-micro text-text-2 shrink-0">
                    {formatPct(segment.probability)}
                    {segment.atTs ? ` at ${formatIst(segment.atTs)}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-label="Alternate routes">
          <h3 className="type-small text-text font-medium">Alternates</h3>
          {alternates.length === 0 ? (
            <EmptyState
              size="sm"
              title="No alternates yet"
              description="Find a route: up to two more routes appear once the graph has been searched."
            />
          ) : (
            <ul className="mt-2 space-y-1.5">
              {alternates.map((alternate) => (
                <li
                  key={alternate.id}
                  className="border-line flex items-baseline justify-between gap-3 border-b py-1.5 last:border-b-0"
                >
                  <span className="type-small text-text min-w-0 truncate">{alternate.label}</span>
                  <span className="num type-micro text-text-2 shrink-0">
                    {alternate.etaMin === undefined ? MISSING : formatMinutes(alternate.etaMin)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <div className="border-line mt-4 flex flex-wrap items-center gap-2 border-t pt-4">
        <Button
          variant="outline"
          size="sm"
          disabled={!hasRoute}
          title={hasRoute ? undefined : "Available once a route has been found"}
        >
          Send to dispatch
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!hasRoute}
          title={hasRoute ? undefined : "Available once a route has been found"}
        >
          Copy as GeoJSON
        </Button>
        {hasRoute ? null : (
          <p className="type-micro text-text-3">Available once a route has been found.</p>
        )}
      </div>
    </Panel>
  );
}

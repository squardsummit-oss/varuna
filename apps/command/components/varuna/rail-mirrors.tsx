"use client";

/**
 * The console rail's Alerts and Pumps tabs (SPEC.md 7.2, task P8.11).
 *
 * Both are **mirrors**, not second implementations. `/alerts` and `/pumps` own the full versions -
 * the CAP viewer, the phone mock, the drag board - and these are the operator's glance at the same
 * two products without leaving the map, which is where they spend the demo. Each reads the same
 * endpoint its page does, for the run the console is currently showing, so the rail and the page
 * can never disagree about what this cycle raised.
 */

import { useEffect, useState } from "react";
import Link from "next/link";

import { AlertLevelChip } from "@/components/varuna/alert-level-chip";
import { EmptyState } from "@/components/varuna/empty-state";
import { loadAlerts, type RunAlert } from "@/lib/api/alerts";
import { loadPumpPlan, type PumpPlan } from "@/lib/api/pumps";
import { formatIst, formatMinutes } from "@/lib/format";

/** Alerts listed in the rail. The page shows the queue in full; this is the top of it. */
const RAIL_ALERTS = 12;

/** Assignments listed in the rail, worst first. */
const RAIL_ASSIGNMENTS = 8;

export function RailAlerts({ runId }: { runId?: string | null }) {
  const [alerts, setAlerts] = useState<RunAlert[] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    loadAlerts(runId ?? undefined, controller.signal)
      .then((set) => setAlerts(set?.alerts ?? []))
      .catch(() => setAlerts([]));
    return () => controller.abort();
  }, [runId]);

  if (alerts === null) {
    return <p className="type-small text-text-3">Loading the queue...</p>;
  }
  if (alerts.length === 0) {
    return (
      <EmptyState
        title="No alerts on this cycle"
        description="Alerts raise when a segment stays above its threshold for two cycles."
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="type-micro text-text-3">
        {alerts.length.toLocaleString("en-IN")} raised on this cycle.{" "}
        <Link href="/alerts" className="text-tide underline underline-offset-2">
          Open the alert centre
        </Link>{" "}
        for the CAP documents and the delivery log.
      </p>
      <ol className="flex flex-col gap-2">
        {alerts.slice(0, RAIL_ALERTS).map((alert) => (
          <li
            key={alert.id}
            className="rounded-control border border-line bg-well p-3"
          >
            <div className="flex items-start justify-between gap-2">
              <AlertLevelChip level={alert.level} size="sm" />
              <span className="num type-micro text-text-3">{formatIst(alert.windowFrom)}</span>
            </div>
            <p className="mt-2 type-small text-text">{alert.headline}</p>
            <p className="num type-micro text-text-3">
              {alert.areaDesc} · peak {Math.round(alert.peakCm)} cm
            </p>
          </li>
        ))}
      </ol>
      {alerts.length > RAIL_ALERTS ? (
        <p className="type-micro text-text-3">
          {(alerts.length - RAIL_ALERTS).toLocaleString("en-IN")} more on the alert centre.
        </p>
      ) : null}
    </div>
  );
}

export function RailPumps({ runId }: { runId?: string | null }) {
  const [plan, setPlan] = useState<PumpPlan | null | "loading">("loading");

  useEffect(() => {
    const controller = new AbortController();
    loadPumpPlan(runId ?? undefined, controller.signal)
      .then(setPlan)
      .catch(() => setPlan(null));
    return () => controller.abort();
  }, [runId]);

  if (plan === "loading") {
    return <p className="p-4 type-small text-text-3">Loading the plan...</p>;
  }
  if (!plan || plan.assignments.length === 0) {
    return (
      <div className="p-4">
        <EmptyState
          title="No pump plan on this cycle"
          description="The optimiser assigns pumps where a street is predicted above 45 cm."
        />
      </div>
    );
  }

  const ordered = [...plan.assignments].sort((a, b) => b.minutesSaved - a.minutesSaved);

  return (
    <div className="flex flex-col gap-3 p-4">
      <div>
        <p className="num type-small text-text">
          {plan.assignments.length} of {plan.pumps.length} pumps assigned
        </p>
        <p className="type-micro text-text-3">
          About {formatMinutes(plan.totalMinutesSaved)} above {plan.thresholdCm} cm avoided.{" "}
          {plan.benefitLabel}.
        </p>
      </div>
      <ol className="flex flex-col gap-2">
        {ordered.slice(0, RAIL_ASSIGNMENTS).map((a) => (
          <li key={a.pumpId} className="rounded-control border border-line bg-well p-3">
            <div className="flex items-baseline justify-between gap-2">
              <span className="num type-small text-text">{a.pumpId}</span>
              <span className="num type-micro text-text-2">
                {formatMinutes(a.minutesSaved)} saved
              </span>
            </div>
            <p className="type-micro text-text-2">{a.targetName}</p>
            <p className="num type-micro text-text-3">
              {a.depot} · ETA {Math.round(a.etaMin)} min
            </p>
          </li>
        ))}
      </ol>
      <p className="type-micro text-text-3">
        <Link href="/pumps" className="text-tide underline underline-offset-2">
          Open <span translate="no">Jalayantra</span>, pump dispatch
        </Link>
        , to move a pump or send the order.
      </p>
    </div>
  );
}

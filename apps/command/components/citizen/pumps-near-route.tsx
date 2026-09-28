"use client";

/**
 * What is being done about the water on your route (UI_SPEC 4 item 4, PRD 3.1 item 6).
 *
 * The cycle's pump plan already says which lorry goes to which chronic junction and how many
 * minutes above 45 cm that is expected to remove. This card shows only the assignments near the
 * route the reader is actually taking, and carries the plan's own label for the model that
 * produced the number.
 *
 * **The number is a stated estimate, not a changed forecast.** Nothing writes a pump's effect
 * back into `segment_forecast.parquet`, so a dispatched pump does not move the depth on the map
 * (TECH_SPEC 0). The chip says which model produced the estimate, and when the plan says the
 * bathtub model produced it this card says "Bathtub estimate" rather than laundering it into
 * "Emulator estimate" (SPEC.md rule 6).
 *
 * **Near the route** is measured, not asserted: an assignment counts when its hotspot is within
 * `NEAR_ROUTE_M` of a vertex of the route line. The route's vertices are the junctions the router
 * walked, a few tens of metres apart on Mumbai's OSM graph, so vertex distance is a fair stand-in
 * for distance to the line and never claims more precision than that.
 */

import { useEffect, useState } from "react";

import type { RouteLeg } from "@/lib/api/route";
import { loadPumpPlan, type PumpAssignment, type PumpPlan } from "@/lib/api/pumps";
import { formatCm, formatMinutes } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * How near a hotspot must be to the route to count as "on it", in metres.
 *
 * 400 m is about four Mumbai blocks: near enough that the water the pump is sent to is water the
 * reader would drive through, far enough that a junction one street off the line still counts.
 * It is a choice, and a round one; nothing in the artifacts fixes it.
 */
export const NEAR_ROUTE_M = 400;

const EARTH_RADIUS_M = 6_371_000;

/** Great-circle distance in metres between two [lon, lat] points. */
export function metresBetween(a: [number, number], b: [number, number]): number {
  const toRad = Math.PI / 180;
  const lat1 = a[1] * toRad;
  const lat2 = b[1] * toRad;
  const dLat = lat2 - lat1;
  const dLon = (b[0] - a[0]) * toRad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** The plan's assignments whose hotspot lies within `radiusM` of the route, nearest first. */
export function assignmentsNearRoute(
  plan: PumpPlan | null | undefined,
  route: RouteLeg | null | undefined,
  radiusM: number = NEAR_ROUTE_M,
): PumpAssignment[] {
  const path = route?.path ?? [];
  if (!plan || path.length === 0) return [];
  const near: { assignment: PumpAssignment; metres: number }[] = [];
  for (const assignment of plan.assignments) {
    const { lon, lat } = assignment;
    if (lon === null || lat === null || !Number.isFinite(lon) || !Number.isFinite(lat)) continue;
    let closest = Number.POSITIVE_INFINITY;
    for (const vertex of path) {
      const metres = metresBetween([lon, lat], vertex);
      if (metres < closest) closest = metres;
    }
    if (closest <= radiusM) near.push({ assignment, metres: closest });
  }
  return near.sort((a, b) => a.metres - b.metres).map((entry) => entry.assignment);
}

/** The three labels `services/products/varuna_products/pumps.py` can publish. */
const EMULATOR_LABEL = "Flash-lite emulator re-run with the pump's outflow";
const BATHTUB_LABEL = "Bathtub estimate, not a physics run";
const MIXED_LABEL = "Flash-lite for most assignments, bathtub estimate for the rest";

/**
 * The chip beside each benefit, from the plan's own label, or `null` when it states no model.
 *
 * "Emulator estimate" is one of UI_SPEC 9's three verbatim honesty chips, and it is printed only
 * when the plan says the emulator produced the number.
 */
export function benefitChip(benefitLabel: string | null | undefined): string | null {
  switch ((benefitLabel ?? "").trim()) {
    case EMULATOR_LABEL:
      return "Emulator estimate";
    case BATHTUB_LABEL:
      return "Bathtub estimate";
    case MIXED_LABEL:
      return "Emulator and bathtub estimate";
    default:
      return null;
  }
}

export interface PumpsNearRouteProps {
  /** The route being explained; its vertices decide which assignments are near. */
  route: RouteLeg | null | undefined;
  /** An already-loaded plan. Given this, the card makes no request of its own. */
  plan?: PumpPlan | null;
  /** The run whose plan to fetch when `plan` is not supplied. */
  runId?: string | null;
  className?: string;
}

type PlanState = PumpPlan | null | "loading";

export function PumpsNearRoute({ route, plan, runId, className }: PumpsNearRouteProps) {
  // A plan passed in is the state; only the uncontrolled card keeps one of its own. Deriving it
  // rather than copying it into state is what keeps the effect free of a synchronous setState.
  const controlled = plan !== undefined;
  const [own, setOwn] = useState<PlanState>(controlled ? null : "loading");

  useEffect(() => {
    if (controlled) return;
    const controller = new AbortController();
    loadPumpPlan(runId ?? undefined, controller.signal)
      .then((result) => setOwn(result))
      .catch(() => setOwn(null));
    return () => controller.abort();
  }, [controlled, runId]);

  const fetched: PlanState = controlled ? (plan ?? null) : own;

  const body = () => {
    if (fetched === "loading") {
      return <p className="type-small text-text-3 mt-2">Reading this cycle&rsquo;s pump plan.</p>;
    }
    if (fetched === null) {
      return <p className="type-small text-text-2 mt-2">This cycle has no pump plan.</p>;
    }
    const near = assignmentsNearRoute(fetched, route);
    if (near.length === 0) {
      return (
        <p className="type-small text-text-2 mt-2">
          No pump in this cycle&rsquo;s plan is sent to a junction on your route.
        </p>
      );
    }
    const chip = benefitChip(fetched.benefitLabel);
    const threshold = formatCm(fetched.thresholdCm);
    return (
      <>
        <ul className="mt-2 space-y-2">
          {near.map((assignment) => (
            <li key={assignment.pumpId} className="border-line border-b pb-2 last:border-b-0">
              <p className="num type-small text-text">
                {assignment.pumpId} at {assignment.targetName}
                {assignment.minutesSaved > 0
                  ? ` — expected to remove ${formatMinutes(assignment.minutesSaved)} above ${threshold}`
                  : ` — no change above ${threshold} is expected from this pump`}
              </p>
              {chip ? (
                <span className="rounded-chip border-line bg-well type-micro text-text-2 mt-1 inline-flex items-center border px-2 py-0.5">
                  {chip}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
        {chip === null ? (
          <p className="type-micro text-text-3 mt-2">
            This plan does not say which model produced the number.
          </p>
        ) : null}
        <p className="type-micro text-text-3 mt-2">
          {fetched.inventory === "synthetic" ? "Synthetic pump inventory. " : ""}A dispatched pump
          changes this estimate, not the depth drawn on the map.
        </p>
      </>
    );
  };

  return (
    <section className={cn("border-line border-t pt-4", className)}>
      <h3 className="type-small text-text font-medium">Pumps near your route</h3>
      {body()}
    </section>
  );
}

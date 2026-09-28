"use client";

/**
 * The route answer in words: the two ways compared, then why this one (UI_SPEC 4).
 *
 * The five blocks are in UI_SPEC 4's order and each one disappears rather than degrades when the
 * run cannot support it: an API that sends no reasons and no corridors - which is what the
 * deployed API does today - still renders the two ETAs and says nothing else. That is the shape
 * of every honesty rule on this card. A number that is not in the answer is not on the screen.
 *
 * Sentences come from `lib/explain.ts` and nowhere else, so there is one copy of each and the
 * drop rule is tested in one place.
 */

import { useState } from "react";

import { CorridorPicker, assignedCorridorId } from "@/components/citizen/corridor-picker";
import { PumpsNearRoute } from "@/components/citizen/pumps-near-route";
import type { PumpPlan } from "@/lib/api/pumps";
import type { RouteCorridor, RouteLeg, RoutePlan, RouteReason } from "@/lib/api/route";
import { explainReasons, passableFor, vehicleNoun } from "@/lib/explain";
import { formatIst, formatMinutes } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface RouteAnswerProps {
  plan: RoutePlan;
  /** Structured reasons from the run; an older API sends none and the card stays quiet. */
  reasons?: RouteReason[] | null;
  corridors?: RouteCorridor[] | null;
  selectedCorridorId?: string | null;
  onPickCorridor?: (id: string) => void;
  profile: string;
  /** An already-loaded pump plan. Omit and the pumps block fetches the run's own. */
  pumpPlan?: PumpPlan | null;
  className?: string;
}

/** The corridor the reader is looking at, controlled by the caller or held here. */
function useCorridorChoice(
  corridors: readonly RouteCorridor[],
  selectedCorridorId: string | null | undefined,
  onPickCorridor: ((id: string) => void) | undefined,
): [string | null, (id: string) => void] {
  const [picked, setPicked] = useState<string | null>(null);
  const current = selectedCorridorId ?? picked ?? assignedCorridorId(corridors);
  return [
    current,
    (id: string) => {
      setPicked(id);
      onPickCorridor?.(id);
    },
  ];
}

/** "Shortest way: 21 min · Safe way: 27 min (+6 min)" - never one number without its comparison. */
function comparison(naive: RouteLeg | null, varuna: RouteLeg): string {
  const safe = formatMinutes(varuna.minutes);
  if (!naive) return `Safe way: ${safe}. The shortest way could not be found for this trip.`;
  const difference = Math.round(varuna.minutes - naive.minutes);
  const tail =
    difference > 0
      ? ` (+${difference} min)`
      : difference < 0
        ? ` (${Math.abs(difference)} min sooner)`
        : " (no extra time)";
  return `Shortest way: ${formatMinutes(naive.minutes)} · Safe way: ${safe}${tail}`;
}

export function RouteAnswer({
  plan,
  reasons,
  corridors,
  selectedCorridorId,
  onPickCorridor,
  profile,
  pumpPlan,
  className,
}: RouteAnswerProps) {
  const allCorridors = corridors ?? plan.corridors ?? [];
  const [currentCorridorId, pick] = useCorridorChoice(
    allCorridors,
    selectedCorridorId,
    onPickCorridor,
  );
  const chosen = allCorridors.find((c) => c.id === currentCorridorId) ?? null;
  const shown = chosen?.route ?? plan.varuna;
  const worded = explainReasons(reasons ?? plan.reasons, profile);

  if (!shown) {
    return (
      <section className={cn("rounded-panel border-line bg-deep border p-4", className)}>
        <h2 className="type-h3 text-text">Your way there</h2>
        <p className="type-small text-text-2 mt-2">
          This cycle has no safe way through{" "}
          {vehicleNoun(profile) === "on foot" ? "on foot" : `for ${vehicleNoun(profile)}`}. Try a
          later departure or another vehicle.
        </p>
      </section>
    );
  }

  return (
    <section className={cn("rounded-panel border-line bg-deep border p-4", className)}>
      <h2 className="type-h3 text-text">Your way there</h2>
      <p className="num type-body text-text mt-2">{comparison(plan.naive, shown)}</p>

      {worded.length > 0 ? (
        <section className="border-line mt-4 border-t pt-4">
          <h3 className="type-small text-text font-medium">Why this way</h3>
          <ul className="mt-2 space-y-1.5">
            {worded.map((reason) => (
              <li key={`${reason.kind}-${reason.segmentId}`} className="num type-small text-text-2">
                {reason.text}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <CorridorPicker
        className="mt-4"
        corridors={allCorridors}
        selectedId={currentCorridorId}
        onPick={pick}
      />

      <PumpsNearRoute className="mt-4" route={shown} plan={pumpPlan} runId={plan.runId || null} />

      {shown.safeUntil ? (
        <p className="num border-line type-small text-text mt-4 border-t pt-4">
          Leave before {formatIst(shown.safeUntil)} — after that this cycle no longer shows the
          whole route {passableFor(profile)}.
        </p>
      ) : null}
    </section>
  );
}

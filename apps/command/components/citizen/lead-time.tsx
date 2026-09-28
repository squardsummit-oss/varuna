"use client";

/**
 * How far ahead the citizen map is coloured, and why it does not open at "now".
 *
 * **Measured, 2026-09-27, on the eight baked Mumbai cycles:** at a run's first step (its cycle
 * plus five minutes) no street is 5 cm deep on any of them - the deepest is 2.6 cm, at 08:40.
 * Every cycle starts the Twin's street water from dry, so the first steps say "dry" about streets
 * the previous cycle had under water. A map that opened there would tell a reader in the storm
 * that everything is passable. At +60 min the 08:40 cycle has 5,016 streets at 5 cm or more and
 * 86 at 30 cm or more, and that is a picture the product can stand behind, so the dashboard opens
 * there and says so in words ({@link LEAD_DEFAULT_REASON}).
 *
 * The control offers the six leads a person asks about: now, half-hourly to two hours, and the
 * three-hour edge of the forecast. The step each one lands on is read off the run's own
 * `valid_ts`, never assumed, and the time printed is that step's, so "Now" on the 08:40 cycle
 * reads "08:45 (+5 min)" - the earliest step the run has.
 */

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatTimeWithLead, minutesBetween } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The leads the control offers, in minutes from the run's cycle. */
export const LEAD_MINUTES = [0, 30, 60, 90, 120, 180] as const;
export type LeadMinutes = (typeof LEAD_MINUTES)[number];

/** Where the dashboard opens: the first lead the cold-started runs can defend (see above). */
export const DEFAULT_LEAD_MIN: LeadMinutes = 60;

/** The sentence that says why the map does not open at "now". */
export const LEAD_DEFAULT_REASON =
  "Each forecast starts its street water from dry at its cycle time, so its first steps read " +
  "low: on every baked cycle no street is 5 cm deep at the first step. The map opens at +60 min, " +
  "the first lead it can stand behind.";

export function isLeadMinutes(value: number): value is LeadMinutes {
  return (LEAD_MINUTES as readonly number[]).includes(value);
}

/**
 * The step whose valid time is nearest the cycle plus `leadMin`.
 *
 * Read off `validTs` rather than computed from a step length, so a run written at another cadence
 * still lands on its own nearest step. Without a cycle time it falls back to five-minute steps
 * starting five minutes after the cycle, which is how every baked run is written.
 */
export function stepForLead(
  validTs: readonly string[],
  cycleTs: string | null | undefined,
  leadMin: number,
): number {
  if (validTs.length === 0) return 0;
  const last = validTs.length - 1;
  const cycle = cycleTs ? Date.parse(cycleTs) : Number.NaN;
  if (!Number.isFinite(cycle)) {
    return Math.min(last, Math.max(0, Math.round(leadMin / 5) - 1));
  }
  const target = cycle + leadMin * 60_000;
  let best = 0;
  let bestGap = Number.POSITIVE_INFINITY;
  validTs.forEach((ts, index) => {
    const at = Date.parse(ts);
    if (!Number.isFinite(at)) return;
    const gap = Math.abs(at - target);
    if (gap < bestGap) {
      best = index;
      bestGap = gap;
    }
  });
  return best;
}

/** "09:40 (+60 min)": the chosen step's own time and its lead from the cycle. */
export function stepLabel(
  validTs: readonly string[],
  cycleTs: string | null | undefined,
  step: number,
): string | null {
  const at = validTs[step];
  if (!at) return null;
  const lead = cycleTs ? minutesBetween(cycleTs, at) : null;
  return formatTimeWithLead(at, lead);
}

/** The words on each option; the group's label carries the unit. */
function optionText(lead: LeadMinutes): string {
  return lead === 0 ? "Now" : `+${lead}`;
}

export interface LeadTimeControlProps {
  value: LeadMinutes;
  onValueChange: (lead: LeadMinutes) => void;
  /** The run's steps, so each option's accessible name carries the time it lands on. */
  validTs?: readonly string[];
  cycleTs?: string | null;
  className?: string;
}

/**
 * Six 44 px options in one row, which fits a 390 px phone. The visible text is short ("+60"); the
 * group is labelled in minutes and each option's accessible name is the full "09:40 (+60 min)",
 * so a number never reaches a screen reader without its unit.
 */
export function LeadTimeControl({
  value,
  onValueChange,
  validTs = [],
  cycleTs = null,
  className,
}: LeadTimeControlProps) {
  return (
    <ToggleGroup
      aria-label="Minutes ahead of the forecast cycle"
      variant="outline"
      spacing={0}
      value={[String(value)]}
      onValueChange={(next) => {
        const picked = Number((next as string[])[0]);
        if (isLeadMinutes(picked)) onValueChange(picked);
      }}
      className={cn("w-full", className)}
      data-slot="lead-time-control"
    >
      {LEAD_MINUTES.map((lead) => {
        const label = stepLabel(validTs, cycleTs, stepForLead(validTs, cycleTs, lead));
        return (
          <ToggleGroupItem
            key={lead}
            value={String(lead)}
            aria-label={
              label
                ? `${lead === 0 ? "Now" : `+${lead} min`}, ${label}`
                : lead === 0
                  ? "Now"
                  : `+${lead} min`
            }
            className="num type-small h-11 min-w-0 flex-1 px-1"
          >
            {optionText(lead)}
          </ToggleGroupItem>
        );
      })}
    </ToggleGroup>
  );
}

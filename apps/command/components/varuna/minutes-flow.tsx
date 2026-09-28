"use client";

import NumberFlow from "@number-flow/react";

import { formatMinutes, MISSING } from "@/lib/format";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { DUR_MS, EASE_UI_CSS } from "@/lib/motion";

/**
 * The parts {@link formatMinutes} prints: whole hours, the minutes left over, and whether each
 * part is shown. "45 min" is minutes only, "2 h" is hours only, "1 h 40 min" is both.
 */
export function minuteParts(minutes: number): {
  hours: number;
  rest: number;
  showHours: boolean;
  showMinutes: boolean;
} {
  const rounded = Math.max(0, Math.round(minutes));
  const hours = Math.floor(rounded / 60);
  const rest = rounded % 60;
  return { hours, rest, showHours: hours > 0, showMinutes: hours === 0 || rest > 0 };
}

/** The roll takes the card flight's own duration and curve (motion M17), so they land together. */
const ROLL_TIMING = { duration: DUR_MS.panel, easing: EASE_UI_CSS };

export interface MinutesFlowProps {
  /** Minutes, exactly as the API returned them; rounded the way {@link formatMinutes} rounds. */
  value: number | null | undefined;
}

/**
 * A duration in minutes that rolls to its new value (motion M17's "benefit numbers roll", M4's
 * NumberFlow). It prints the same string as {@link formatMinutes}, digit for digit, so the rolling
 * and the plain renders never disagree. Under reduced motion it is that plain string and nothing
 * moves.
 *
 * The hours and minutes are separate flows in fixed positions, so a change from "1 h 40 min" to
 * "0 min" rolls the minutes rather than remounting them.
 */
export function MinutesFlow({ value }: MinutesFlowProps) {
  const reducedMotion = usePrefersReducedMotion();
  if (typeof value !== "number" || !Number.isFinite(value)) return <>{MISSING}</>;
  if (reducedMotion) return <>{formatMinutes(value)}</>;

  const { hours, rest, showHours, showMinutes } = minuteParts(value);
  // `respectMotionPreference` is off because this component already answered that question
  // above; leaving it on would make two places decide the same thing.
  return (
    <>
      {showHours ? (
        <>
          <NumberFlow
            value={hours}
            respectMotionPreference={false}
            transformTiming={ROLL_TIMING}
            spinTiming={ROLL_TIMING}
          />
          {showMinutes ? " h " : " h"}
        </>
      ) : null}
      {showMinutes ? (
        <>
          <NumberFlow
            value={rest}
            respectMotionPreference={false}
            transformTiming={ROLL_TIMING}
            spinTiming={ROLL_TIMING}
          />
          {" min"}
        </>
      ) : null}
    </>
  );
}

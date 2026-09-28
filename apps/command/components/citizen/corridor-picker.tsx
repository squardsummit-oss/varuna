"use client";

/**
 * Which of the safe roads you are on (UI_SPEC 4 item 3, PRD 3.5).
 *
 * The router returns up to three corridors that all clear the vehicle's depth threshold and
 * assigns this trip to one, deterministically from its `trip_id`. The screen's job is to say so
 * plainly: the assignment is a **policy for spreading traffic**, not a measured demand, and the
 * reader may take any of the three.
 *
 * Accessibility decisions, because this is the one control on the card:
 * - it is a radio group, so the assignment is a value a screen reader announces rather than a
 *   colour (UI_SPEC 10, SPEC.md 6.10: colour is never the only carrier);
 * - every chip carries its letter as text, so the three are told apart without colour;
 * - a live region states the current corridor in words, because the chip's selected state alone
 *   does not say "you are on this one" out loud;
 * - chips are 44 px tall, the citizen floor at 390 x 844.
 *
 * No motion: section 8 has no row for a chip, and a selection that moves would be decoration.
 */

import { Radio } from "@base-ui/react/radio";
import { RadioGroup } from "@base-ui/react/radio-group";

import type { RouteCorridor } from "@/lib/api/route";
import { shareInTen, spreadingDisclosure } from "@/lib/explain";
import { formatMinutes } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface CorridorPickerProps {
  /** The corridors the run offered. Fewer than two and the picker renders nothing. */
  corridors: readonly RouteCorridor[];
  /** The corridor to show as chosen. Omit to follow the run's own assignment. */
  selectedId?: string | null;
  /** Called with the corridor id when the reader picks another one. */
  onPick?: (id: string) => void;
  className?: string;
}

/** The corridor this trip was assigned to, or the first one the run returned. */
export function assignedCorridorId(corridors: readonly RouteCorridor[]): string | null {
  return (corridors.find((c) => c.assigned) ?? corridors[0])?.id ?? null;
}

/** "A · 6 in 10", or "A" when the run gave the corridor no share to print. */
function chipLabel(corridor: RouteCorridor): string {
  const share = shareInTen(corridor.share);
  return share ? `${corridor.label} · ${share}` : corridor.label;
}

/** "Corridor A, 6 in 10 drivers, 27 min" - what a screen reader reads for one chip. */
function chipDescription(corridor: RouteCorridor): string {
  const parts = [`Corridor ${corridor.label}`];
  const share = shareInTen(corridor.share);
  if (share) parts.push(`${share} drivers`);
  if (corridor.route) parts.push(formatMinutes(corridor.route.minutes));
  return parts.join(", ");
}

export function CorridorPicker({ corridors, selectedId, onPick, className }: CorridorPickerProps) {
  if (corridors.length < 2) return null;

  const assigned = assignedCorridorId(corridors);
  const current = selectedId ?? assigned;
  const chosen = corridors.find((c) => c.id === current) ?? null;

  return (
    <section className={cn("border-line border-t pt-4", className)}>
      <h3 className="type-small text-text font-medium">Which safe road you are on</h3>
      <RadioGroup
        aria-label="Which safe road you are on"
        value={current ?? undefined}
        onValueChange={(value) => onPick?.(String(value))}
        className="mt-2 flex flex-wrap gap-2"
      >
        {corridors.map((corridor) => (
          <Radio.Root
            key={corridor.id}
            value={corridor.id}
            aria-label={chipDescription(corridor)}
            className={cn(
              "rounded-chip flex min-h-11 min-w-24 flex-col items-start justify-center border px-3 py-1.5 text-left",
              "border-line bg-well text-text-2",
              "data-checked:border-tide data-checked:bg-tide-soft data-checked:text-text",
            )}
          >
            <span className="num type-small font-medium">{chipLabel(corridor)}</span>
            {corridor.route ? (
              <span className="num type-micro text-text-3">
                {formatMinutes(corridor.route.minutes)}
              </span>
            ) : null}
          </Radio.Root>
        ))}
      </RadioGroup>
      <p role="status" className="type-micro text-text-2 mt-2">
        {chosen === null
          ? "No corridor chosen yet."
          : chosen.id === assigned
            ? `You are on corridor ${chosen.label} of ${corridors.length}.`
            : `You chose corridor ${chosen.label} of ${corridors.length}.`}
      </p>
      <p className="type-micro text-text-3 mt-2">{spreadingDisclosure(corridors.length)}</p>
    </section>
  );
}

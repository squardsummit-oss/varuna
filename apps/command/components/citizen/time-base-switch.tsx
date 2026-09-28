"use client";

/**
 * The dashboard's two clocks, kept apart.
 *
 * Everything the map draws is the reconstructed replay of 2 July 2019. "Today, next 3 h" is a
 * different forecast with a different clock: Open-Meteo's rain for Mumbai now, run through
 * Flash-lite (`GET /v1/outlook`). The two are never mixed. Choosing "Today" opens the outlook card
 * and changes nothing on the map, which keeps saying it is the replay, because recolouring a 2019
 * street map with today's weather would make both of them false.
 */

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

export const TIME_BASES = ["replay", "today"] as const;
export type TimeBase = (typeof TIME_BASES)[number];

export const TIME_BASE_LABEL: Record<TimeBase, string> = {
  replay: "2 July 2019 replay",
  today: "Today, next 3 h",
};

export interface TimeBaseSwitchProps {
  value: TimeBase;
  onValueChange: (value: TimeBase) => void;
  className?: string;
}

export function TimeBaseSwitch({ value, onValueChange, className }: TimeBaseSwitchProps) {
  return (
    <ToggleGroup
      aria-label="Which forecast to read"
      variant="outline"
      spacing={0}
      value={[value]}
      onValueChange={(next) => {
        const picked = (next as string[])[0];
        if (picked === "replay" || picked === "today") onValueChange(picked);
      }}
      className={cn("w-full", className)}
      data-slot="time-base-switch"
    >
      {TIME_BASES.map((base) => (
        <ToggleGroupItem key={base} value={base} className="type-small h-11 min-w-0 flex-1">
          {TIME_BASE_LABEL[base]}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

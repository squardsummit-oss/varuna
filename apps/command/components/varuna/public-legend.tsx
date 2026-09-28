"use client";

import { usePublicT } from "@/lib/i18n";
import { cssVar } from "@/lib/ramps";
import { cn } from "@/lib/utils";

import type { PublicProfile } from "./vehicle-selector";

export interface PublicLegendStop {
  key: "passable" | "caution" | "impassable";
  /** The English label; the legend itself prints the reader's language. */
  label: string;
  colour: string;
}

/**
 * Three colours only on the public map: the console's depth ramp is for operators, a commuter
 * needs go / slow down / do not enter.
 */
export const PUBLIC_LEGEND_STOPS: readonly PublicLegendStop[] = [
  { key: "passable", label: "Passable", colour: cssVar("--depth-1") },
  { key: "caution", label: "Caution", colour: cssVar("--depth-3") },
  { key: "impassable", label: "Impassable", colour: cssVar("--depth-5") },
];

export interface PublicLegendProps {
  /** Named so the legend reads "for a car", not just three swatches. */
  profile?: PublicProfile;
  className?: string;
}

export function PublicLegend({ profile, className }: PublicLegendProps) {
  const t = usePublicT("legend");
  const vehicle = usePublicT("vehicle");
  return (
    <div className={cn("flex flex-wrap items-center gap-x-4 gap-y-1.5", className)}>
      {PUBLIC_LEGEND_STOPS.map((stop) => (
        <span key={stop.key} className="type-micro text-text-2 inline-flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="size-2.5 shrink-0 rounded-full"
            style={{ background: stop.colour }}
          />
          {t(stop.key)}
        </span>
      ))}
      {profile ? (
        <span className="type-micro text-text-3">
          {/* English lowercases the vehicle mid-sentence; Devanagari has no case to change. */}
          {t("for", { vehicle: vehicle(profile).toLowerCase() })}
        </span>
      ) : null}
    </div>
  );
}

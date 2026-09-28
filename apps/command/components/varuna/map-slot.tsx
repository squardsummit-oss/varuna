"use client";

import { CloudRain } from "lucide-react";

import { MAP_ATTRIBUTION } from "@/components/map/satellite";
import { EmptyState } from "@/components/varuna/empty-state";
import { depthLegendStops } from "@/lib/ramps";
import { cn } from "@/lib/utils";

export interface MapSlotProps {
  /** Map layers or overlays rendered above the grid once a map exists. */
  children?: React.ReactNode;
  /**
   * Shift the legend inboard so a 360 px panel floating on the map's right edge (the replay panel)
   * cannot cover it. The legend is always visible (SPEC.md section 6.7), so the panel never wins.
   */
  legendClearsRightPanel?: boolean;
  /**
   * Citizen screens (the public map, the report location picker) carry their own three-colour
   * legend and their own words, so they drop the operator legend and the replay chip
   * (SPEC.md section 7.11). Attribution stays on every audience.
   */
  audience?: "operator" | "public";
  /** Empty-state copy. `null` renders none: a location picker is not a forecast. */
  emptyState?: { title: string; description: string } | null;
  /**
   * Draw the operator's "Reconstructed replay" chip. Off where the slot stands in for a city whose
   * forecast is a design storm (Chennai on the onboarding wizard): the chip would be false there.
   */
  replayChip?: boolean;
}

const OPERATOR_EMPTY_STATE = {
  title: "No runs yet",
  description: "Press Play on the replay, or Compute live.",
};

/**
 * The future map canvas. Until CityMap lands (Phase 6) it is a quiet grid on `--ink` with the honest
 * empty state, the replay chip and the depth legend so the console reads correctly at a glance.
 */
export function MapSlot({
  children,
  legendClearsRightPanel = false,
  audience = "operator",
  emptyState = OPERATOR_EMPTY_STATE,
  replayChip = true,
}: MapSlotProps) {
  const stops = depthLegendStops();
  const isOperator = audience === "operator";

  return (
    <div
      className="bg-ink relative h-full w-full overflow-hidden"
      role="region"
      aria-label="Map canvas"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-35"
        style={{
          backgroundImage:
            "linear-gradient(to right, var(--line) 1px, transparent 1px), linear-gradient(to bottom, var(--line) 1px, transparent 1px)",
          backgroundSize: "48px 48px",
        }}
      />

      {children}

      {emptyState ? (
        // Centred in the canvas above the legend and attribution band, so the empty state stays
        // legible in a short embedded panel (the drain X-ray map) as well as on the console.
        <div className="absolute inset-x-0 top-0 bottom-28 flex items-center justify-center p-6">
          <EmptyState
            icon={CloudRain}
            title={emptyState.title}
            description={emptyState.description}
          />
        </div>
      ) : null}

      {isOperator && replayChip ? (
        <div className="absolute bottom-10 left-4 z-10">
          <span className="border-line bg-deep type-small text-text-2 inline-flex h-7 items-center rounded-full border px-3">
            Reconstructed replay
          </span>
        </div>
      ) : null}

      {isOperator ? (
        <aside
          className={cn(
            // w-72 is the width at which the widest row ("45-60 cm  buses and trucks impassable")
            // sits on one line; anything narrower wraps the threshold away from its colour.
            "border-line bg-deep absolute bottom-10 z-10 w-72 rounded-[var(--radius-panel)] border p-3",
            // 24.5rem clears the replay panel (right-4 + w-360px) and leaves a 16 px gutter.
            legendClearsRightPanel ? "right-[24.5rem]" : "right-4",
          )}
          aria-label="Depth legend"
        >
          <h2 className="type-small text-text font-medium">Depth</h2>
          <ul className="mt-2 space-y-1.5">
            {stops.map((stop) => (
              <li key={stop.key} className="type-micro text-text-2 flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className="size-2.5 shrink-0 rounded-full"
                  style={{ background: stop.cssVar }}
                />
                <span className="num shrink-0 whitespace-nowrap">{stop.label}</span>
                <span className="text-text-3 truncate">{stop.meaning}</span>
              </li>
            ))}
          </ul>
        </aside>
      ) : null}

      {/* One line for both: the imagery on top and the vector data VARUNA derived the city from,
          which is on screen whether or not the imagery loaded. `CityMap` draws the same line on
          the screens that mount it without this slot. */}
      <p className="type-micro text-text-3 absolute inset-x-0 bottom-0 z-10 px-4 py-2">
        {MAP_ATTRIBUTION}
      </p>
    </div>
  );
}

"use client";

import { useEffect, useRef, useState } from "react";
import { Flame, Hospital, TrainFront, Warehouse } from "lucide-react";

import type { FacilityKind, Hotspot } from "@/lib/api/hotspots";
import { Button } from "@/components/ui/button";
import { DepthChip } from "@/components/varuna/depth-chip";
import { EmptyState } from "@/components/varuna/empty-state";
import { Sparkline } from "@/components/varuna/sparkline";
import { formatIstTime } from "@/lib/stores/time";
import { cn } from "@/lib/utils";

export interface HotspotRailProps {
  hotspots: readonly Hotspot[];
  /** Current step on the time bar; the chip shows the depth *now*, not at the peak. */
  step: number;
  selectedId?: string | null;
  onSelect?: (hotspot: Hotspot) => void;
  /** What ordered the list, printed in the header so the ranking is stated, never implied. */
  ranking?: string;
  /** Depth a car stops at, for the "impassable" copy. */
  impassableThresholdCm?: number;
  /** True while the run is still loading; the rail shows nothing rather than a stale order. */
  loading?: boolean;
}

/** How many spots the rail opens on. The register ranks 28, and a list that long scrolls the
 *  worst ones off the top of a 768 px rail; the rest are one button away. */
const HOTSPOT_PREVIEW = 5;

const FACILITY_ICONS: Record<FacilityKind, typeof Hospital> = {
  hospital: Hospital,
  fire_station: Flame,
  station: TrainFront,
  shelter: Warehouse,
};

const FACILITY_LABELS: Record<FacilityKind, string> = {
  hospital: "Hospital within 300 m",
  fire_station: "Fire station within 300 m",
  station: "Railway station within 300 m",
  shelter: "Shelter within 300 m",
};

/** "08:20 (+40 min)" — the SPEC.md 6.8 form for a time with its lead. */
function peakLabel(hotspot: Hotspot): string {
  if (!hotspot.peakTs) return "—";
  return `${formatIstTime(hotspot.peakTs)} (+${hotspot.timeToPeakMin} min)`;
}

/**
 * The console's ranked hotspot rail (SPEC.md section 7.2, task P6.7).
 *
 * Each row is one chronic spot from the sourced register, carrying what an operator scanning the
 * rail actually decides on: how deep it gets, when, and what is next to it.
 *
 * It also carried the "As it happened" ticker of sourced ground-truth pins (task P6.12, motion
 * M18), removed at the team's request because it pushed the ranked list up the rail. The pins
 * still drop on the map as the clock passes them, and `/verify` lists every one with the URL it
 * was read from, so nothing sourced is lost. SPEC.md 7.2 and demo beat 2:40 still name a ticker
 * here. The depth chip follows
 * the time bar so the rail and the map always agree about *now*, while the sparkline keeps the
 * whole three hours in view — the row tells you both where the water is and where it is going.
 *
 * Rows are a single roving-focus list: ↑/↓ move, Enter and Space select, and selecting flies the
 * map to the junction and rings it. Ordinary tab order holds one stop for the whole rail rather
 * than 28, because an operator tabbing to the map should not have to pass through every hotspot.
 */
export function HotspotRail({
  hotspots,
  step,
  selectedId = null,
  onSelect,
  ranking,
  impassableThresholdCm = 30,
  loading = false,
}: HotspotRailProps) {
  const listRef = useRef<HTMLOListElement>(null);
  const [showAll, setShowAll] = useState(false);

  // Ranked worst-first, so the opening five are the five to read first. The selected spot is kept
  // whatever its rank: clicking a ring on the map that ranks below the preview would otherwise
  // mark a row that is not drawn, and the rail would look like it ignored the click. Keeping it
  // rather than expanding on its behalf is what lets "Show fewer" always mean what it says.
  const visible = showAll
    ? hotspots
    : hotspots.filter((h, i) => i < HOTSPOT_PREVIEW || h.id === selectedId);
  const hidden = hotspots.length - visible.length;

  // Keep the selected row in view when the selection comes from the map rather than the rail.
  // Keyed on `showAll` too, so a row revealed by the line above is scrolled to once it is drawn.
  useEffect(() => {
    if (!selectedId) return;
    const row = listRef.current?.querySelector<HTMLElement>(
      `[data-hotspot="${CSS.escape(selectedId)}"]`,
    );
    // Optional call because jsdom has no layout and so no `scrollIntoView`; every browser does.
    row?.scrollIntoView?.({ block: "nearest" });
  }, [selectedId, showAll]);

  // The arrows walk what is drawn. Indexing the full list here would focus a row that is not in
  // the DOM while the preview is collapsed, and the focus ring would simply vanish.
  const move = (from: number, delta: number) => {
    const next = visible[Math.min(Math.max(from + delta, 0), visible.length - 1)];
    if (!next) return;
    onSelect?.(next);
    listRef.current
      ?.querySelector<HTMLElement>(`[data-hotspot="${CSS.escape(next.id)}"]`)
      ?.focus();
  };

  // The tallest peak fixes every row's vertical scale, so a glance down the rail compares like
  // with like instead of 28 curves each drawn to their own ceiling.
  const ceiling = hotspots.reduce((max, h) => Math.max(max, h.peakDepthCm), 0);
  const impassable = hotspots.filter((h) => h.peakDepthCm > impassableThresholdCm).length;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {hotspots.length > 0 ? (
        <div className="shrink-0 border-b border-line px-4 py-2">
          <p className="type-micro text-text-3">
            {hotspots.length} chronic spots, ranked by {ranking ?? "peak depth"}.{" "}
            {impassable > 0
              ? `${impassable} go above ${impassableThresholdCm} cm.`
              : `None go above ${impassableThresholdCm} cm.`}
          </p>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {hotspots.length === 0 ? (
          <div className="p-4">
            <EmptyState
              title={loading ? "Loading hotspots" : "No hotspots yet"}
              description={
                loading
                  ? "The run's ranked chronic spots arrive with its depth frames."
                  : "Press Play on the replay, or Compute live."
              }
            />
          </div>
        ) : (
          <ol ref={listRef} className="divide-y divide-line" aria-label="Ranked hotspots">
            {visible.map((h, i) => {
              const selected = h.id === selectedId;
              const now = h.depthCm[Math.min(step, h.depthCm.length - 1)] ?? 0;
              return (
                <li key={h.id}>
                  <button
                    type="button"
                    data-hotspot={h.id}
                    aria-current={selected ? "true" : undefined}
                    // One tab stop for the rail; the arrows walk it from there.
                    tabIndex={selected || (!selectedId && i === 0) ? 0 : -1}
                    onClick={() => onSelect?.(h)}
                    onKeyDown={(event) => {
                      if (event.key === "ArrowDown") {
                        event.preventDefault();
                        move(i, 1);
                      } else if (event.key === "ArrowUp") {
                        event.preventDefault();
                        move(i, -1);
                      }
                    }}
                    className={cn(
                      "flex w-full flex-col gap-1.5 px-4 py-2.5 text-left transition-colors hover:bg-well focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-tide",
                      selected && "bg-well",
                    )}
                  >
                    <div className="flex items-center gap-2.5">
                      <span className="num w-4 shrink-0 type-small text-text-3">{h.rank}</span>
                      <span className="min-w-0 flex-1 truncate type-small text-text">{h.name}</span>
                      <DepthChip cm={now} size="sm" />
                    </div>

                    <div className="flex items-center gap-2.5 pl-6">
                      <Sparkline
                        values={h.depthCm}
                        markerIndex={step}
                        maxValue={ceiling}
                        label={`${h.name}: peak ${Math.round(h.peakDepthCm)} cm`}
                      />
                      <span className="num min-w-0 flex-1 truncate type-micro text-text-2">
                        peak {Math.round(h.peakDepthCm)} cm at {peakLabel(h)}
                      </span>
                      {h.exposure.facilities.length > 0 ? (
                        <span className="flex shrink-0 items-center gap-1">
                          {h.exposure.facilities.map((kind) => {
                            const Icon = FACILITY_ICONS[kind];
                            return (
                              <Icon
                                key={kind}
                                size={14}
                                strokeWidth={1.75}
                                className="text-text-3"
                                aria-label={FACILITY_LABELS[kind]}
                              />
                            );
                          })}
                        </span>
                      ) : null}
                    </div>
                  </button>
                </li>
              );
            })}
          </ol>
        )}

        {hotspots.length > HOTSPOT_PREVIEW ? (
          <div className="border-t border-line p-3">
            <Button
              variant="outline"
              size="sm"
              aria-expanded={showAll}
              onClick={() => setShowAll((open) => !open)}
            >
              {showAll ? "Show fewer" : `Show all ${hotspots.length} hotspots (${hidden} more)`}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

"use client";

/**
 * What one street is doing, on click (SPEC.md 6.6, 7.2; task P6.9).
 *
 * The hotspot drawer answers "what is happening at the twenty-eight places we already watch". This
 * answers the other question an operator has, which is about the street they just pointed at - and
 * on the 2 July storm most of the wet streets are not on the chronic register at all.
 *
 * Four things, in the order they are asked: how deep it is now, what it does over the next three
 * hours, who can still drive through it, and - when the street belongs to a chronic spot - why
 * (SPEC.md 7.2, "mini fan chart, safe-until per vehicle, 'why' link").
 *
 * The "why" is a link into the hotspot drawer rather than a second explanation rendered here:
 * there is one attribution surface in the console and this points at it.
 */

import { X } from "lucide-react";

import { DepthChip } from "@/components/varuna/depth-chip";
import { Sparkline } from "@/components/varuna/sparkline";
import type { SegmentPick } from "@/components/map/city-map";
import type { Hotspot } from "@/lib/api/hotspots";
import { formatIstTime } from "@/lib/stores/time";
import { LIST_ROAD, isProximityLabel } from "@/lib/street-label";
import { cn } from "@/lib/utils";

/**
 * The depth at which each profile stops, from `varuna_route.profiles` (SPEC.md 11.8).
 *
 * The same numbers the router costs on and the depth ramp colours by, so a street the map draws
 * orange and the popover calls impassable for a car are saying one thing, not two.
 */
const PROFILES: readonly { key: string; label: string; cm: number }[] = [
  { key: "two_wheeler", label: "Two-wheeler", cm: 15 },
  { key: "car", label: "Car", cm: 30 },
  { key: "bus", label: "Bus or truck", cm: 45 },
  { key: "ambulance", label: "Ambulance", cm: 60 },
  { key: "pedestrian", label: "Pedestrian", cm: 30 },
];

export interface SegmentPopoverProps {
  pick: SegmentPick;
  /** Current step, so "now" on the chart is where the time bar is. */
  step: number;
  /** The run's step times, for "safe until 09:25". */
  validTs: readonly string[];
  /**
   * The chronic spot this street is part of, when the rail ranked one that contains it: the
   * console matches `pick.segment.id` against each hotspot's `segment_ids`. Null for the ordinary
   * wet street, which has no junction explanation to open.
   */
  hotspot?: Hotspot | null;
  /**
   * Opens the hotspot drawer on `hotspotId`, at its "Why this junction floods" section. Without
   * it the popover states the hotspot it belongs to and stops; it never draws a link it cannot
   * follow (SPEC.md 17, "never a dead control").
   */
  onWhy?: (hotspotId: string) => void;
  onClose: () => void;
  className?: string;
}

/** The first step at which the street exceeds `cm`, or null if it never does. */
function firstOver(depths: readonly number[], cm: number, from: number): number | null {
  for (let i = from; i < depths.length; i += 1) {
    if (depths[i] > cm) return i;
  }
  return null;
}

export function SegmentPopover({
  pick,
  step,
  validTs,
  hotspot = null,
  onWhy,
  onClose,
  className,
}: SegmentPopoverProps) {
  const { segment, x, y } = pick;
  const depths = segment.depthCm;
  const now = depths[step] ?? 0;
  const peak = depths.length > 0 ? Math.max(...depths) : 0;
  const peakStep = depths.indexOf(peak);

  // The link is drawn only when there is a ranking on the other side of it. No run produces one
  // today (ADR-0042), so what the operator sees instead is the run's own reason, which is the
  // same string the drawer prints - one explanation, stated once.
  const responsiblePipes = hotspot?.attribution.length ?? 0;
  const canOpenWhy = hotspot != null && onWhy != null && responsiblePipes > 0;

  return (
    <aside
      aria-label={`Segment ${segment.displayName || segment.name || segment.id}`}
      // Anchored where the click landed, nudged so the panel never hangs off the left edge or
      // sits under the cursor.
      style={{ left: Math.max(12, x - 130), top: Math.max(12, y + 14) }}
      className={cn(
        "rounded-popover border-line bg-deep/95 pointer-events-auto absolute z-20 w-[17rem] border p-3 backdrop-blur-sm",
        className,
      )}
    >
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="type-small text-text truncate font-medium">
            {segment.displayName || segment.name || LIST_ROAD}
          </h2>
          <p className="num type-micro text-text-3">{segment.id}</p>
          {/* "off Dr Ambedkar Road" says where the lane is, not what it is called; say so. */}
          {isProximityLabel(segment.displayName) ? (
            <p className="type-micro text-text-3">OpenStreetMap does not name this street.</p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded-control text-text-3 hover:bg-well hover:text-text shrink-0 p-1"
        >
          <X size={14} strokeWidth={1.75} aria-hidden="true" />
        </button>
      </header>

      <div className="mt-3 flex items-baseline gap-2">
        <DepthChip cm={now} />
        <span className="type-micro text-text-3">
          now{validTs[step] ? ` · ${formatIstTime(validTs[step])}` : ""}
        </span>
      </div>

      <div className="mt-2">
        <Sparkline values={[...depths]} className="h-8 w-full" />
        <p className="num type-micro text-text-3">
          Peak {peak.toFixed(0)} cm
          {peakStep >= 0 && validTs[peakStep] ? ` at ${formatIstTime(validTs[peakStep])}` : ""}
        </p>
      </div>

      <h3 className="type-micro text-text-2 mt-3 font-medium">Safe until</h3>
      <ul className="mt-1 space-y-0.5">
        {PROFILES.map((profile) => {
          const over = firstOver(depths, profile.cm, step);
          const stopped = (depths[step] ?? 0) > profile.cm;
          return (
            <li key={profile.key} className="type-micro flex items-baseline justify-between gap-2">
              <span className="text-text-2">{profile.label}</span>
              <span className={cn("num", stopped ? "text-depth-4" : "text-text")}>
                {stopped
                  ? "stopped now"
                  : over === null
                    ? "passable all run"
                    : validTs[over]
                      ? formatIstTime(validTs[over])
                      : `step ${over}`}
              </span>
            </li>
          );
        })}
      </ul>

      {/* Method, for the judge who asks; it said "one member" on runs that carry fifty. */}
      <details className="type-micro text-text-3 mt-2">
        <summary className="text-text-2 cursor-pointer">Details</summary>
        <p className="mt-1">
          Depth is the 90th percentile of cells within 15 m of the centreline
          {segment.pGt
            ? ", from the Twin; probability mode reads the ensemble."
            : ". One member, so a forecast and not a distribution."}
        </p>
      </details>

      {hotspot ? (
        <div className="border-line mt-3 border-t pt-2">
          <h3 className="type-micro text-text-2 font-medium">Why it floods</h3>
          <p className="type-micro text-text-3 mt-1 truncate" title={hotspot.name}>
            On the chronic register as {hotspot.name}.
          </p>
          {canOpenWhy ? (
            <button
              type="button"
              onClick={() => onWhy?.(hotspot.id)}
              className="rounded-control type-micro text-tide focus-visible:ring-tide mt-1 underline underline-offset-2 focus-visible:ring-2 focus-visible:outline-none"
            >
              Show the {responsiblePipes} responsible pipes
            </button>
          ) : hotspot.attributionLabel ? (
            <p className="type-micro text-text-3 mt-1">{hotspot.attributionLabel}</p>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}

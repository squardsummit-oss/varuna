/**
 * The hover tooltip (SPEC.md 6.7: "segment tooltip within 80 ms").
 *
 * deck's own tooltip, which is one DOM node it owns rather than a React portal chasing the cursor,
 * so there is no render in its path at all. Only the wet streets are pickable today; PU8 adds the
 * pipes on `/drains` here, dispatching on what was picked.
 */

import type { SegmentPath } from "./types";

export interface TooltipOptions {
  step: number;
  /** Wet streets answer a hover only on a screen that listens for a street pick. */
  streetsPickable: boolean;
}

interface Tooltip {
  text: string;
  style: Record<string, string>;
}

export function mapTooltip({
  step,
  streetsPickable,
}: TooltipOptions): ((info: { object?: SegmentPath }) => Tooltip | null) | undefined {
  if (!streetsPickable) return undefined;
  return ({ object }) => {
    if (!object) return null;
    const depth = object.depthCm[step] ?? 0;
    return {
      text: `${object.displayName || object.name || "Road"}\n${depth.toFixed(1)} cm`,
      style: {
        backgroundColor: "var(--deep)",
        color: "var(--text)",
        border: "1px solid var(--line)",
        borderRadius: "8px",
        fontSize: "12px",
        padding: "6px 8px",
        whiteSpace: "pre-line",
      },
    };
  };
}

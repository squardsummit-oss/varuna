"use client";

import { formatCm, formatMinutes, formatMs, formatPct, formatScore } from "@/lib/format";
import { cn } from "@/lib/utils";

/** One headline score on `/verify` (SPEC.md section 7.10 and 11.12). */
export interface ScoreTile {
  id: string;
  label: string;
  /** Unit or scale as copy, e.g. "0 to 1, higher is better". */
  unit: string;
  /** Null until `services/verify` has scored the event. */
  value: number | null;
  /** Formats a scored value with its unit; defaults to two decimals. */
  format?: (value: number) => string;
  /** One line on what the score measures. */
  note?: string;
}

/** The nine headline tiles, unscored. Values only ever come from `verification.json`. */
export const HEADLINE_SCORE_TILES: ScoreTile[] = [
  {
    id: "csi30",
    label: "CSI at 30 cm",
    unit: "0 to 1, higher is better",
    value: null,
    format: formatScore,
    note: "Critical success index at chronic spots and pins within the window.",
  },
  {
    id: "pod30",
    label: "POD at 30 cm",
    unit: "0 to 1, higher is better",
    value: null,
    format: formatScore,
    note: "Share of observed floods above 30 cm that were forecast.",
  },
  {
    id: "far30",
    label: "FAR at 30 cm",
    unit: "0 to 1, lower is better",
    value: null,
    format: formatScore,
    note: "Share of forecast floods above 30 cm that did not happen.",
  },
  {
    id: "mae",
    label: "Depth MAE at pins",
    unit: "cm",
    value: null,
    format: formatCm,
    note: "Mean absolute depth error where a pin states a depth.",
  },
  {
    id: "timing",
    label: "Timing error",
    unit: "minutes",
    value: null,
    format: formatMinutes,
    note: "Forecast peak versus observed onset at chronic spots.",
  },
  {
    id: "brier",
    label: "Brier score",
    unit: "0 to 1, lower is better",
    value: null,
    format: formatScore,
    note: "Probability skill of P(depth above 30 cm).",
  },
  {
    id: "auc",
    label: "ROC AUC",
    unit: "0.5 to 1, higher is better",
    value: null,
    format: formatScore,
    note: "Ranking skill of the exceedance probability.",
  },
  {
    id: "latency",
    label: "Frame-to-product latency",
    unit: "seconds",
    value: null,
    format: formatMs,
    note: "Radar frame in to segment forecast out, from stage timings.",
  },
  {
    id: "routing",
    label: "Routing value",
    unit: "% of naive trips that crossed an impassable segment",
    value: null,
    format: (value) => formatPct(value),
    note: "100 random hospital-to-hotspot trips, naive versus VARUNA.",
  },
];

export interface VerificationGridProps {
  tiles: ScoreTile[];
  /** How many sourced ground-truth pins the scores rest on; null before verification. */
  groundTruthCount?: number | null;
  className?: string;
}

/**
 * Headline verification scores as a grid of tiles. Every value is computed by `services/verify`
 * from run artifacts; a tile without one says "Not scored yet" rather than showing a placeholder.
 */
export function VerificationGrid({
  tiles,
  groundTruthCount = null,
  className,
}: VerificationGridProps) {
  return (
    <div className={cn("space-y-3", className)}>
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-label="Verification scores">
        {tiles.map((tile) => {
          const scored = tile.value !== null;
          const formatted = scored ? (tile.format ?? formatScore)(tile.value as number) : null;
          return (
            <li
              key={tile.id}
              className="rounded-panel border-line bg-deep flex flex-col gap-1 border p-4"
            >
              <p className="type-small text-text font-medium">{tile.label}</p>
              {/* Not cn(): its merge reads text-h2 and text-text as the same utility and drops the size. */}
              <p
                className={`num font-display text-h2 tracking-display font-semibold ${
                  scored ? "text-text" : "text-text-3"
                }`}
              >
                {scored ? formatted : "Not scored yet"}
              </p>
              <p className="type-micro text-text-2">{tile.unit}</p>
              {tile.note ? <p className="type-micro text-text-3">{tile.note}</p> : null}
            </li>
          );
        })}
      </ul>
      <p className="num type-micro text-text-3">
        {groundTruthCount === null
          ? "Ground-truth pins: none scored yet. Every pin carries a source link."
          : `Ground-truth pins: ${groundTruthCount}, each with a source link.`}
      </p>
    </div>
  );
}

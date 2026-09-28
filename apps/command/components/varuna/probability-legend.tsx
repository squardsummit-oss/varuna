"use client";

/**
 * The probability legend and its threshold selector (SPEC.md 6.2, 7.2; task P6.5).
 *
 * In probability mode the depth ramp still carries the *colour* - a street's p50 depth - and
 * opacity carries `P(depth > threshold)`, floored at 15 % so a street below the threshold fades
 * rather than disappearing. Two channels, two questions: how deep, and how sure.
 *
 * **It says which kind of run it is reading.** On a one-member run the probability is 0 or 1 and
 * the map is binary - that is not an approximation, it is what a deterministic forecast means -
 * and a legend showing a smooth opacity ramp over it would imply a spread the run does not have
 * (rule 6). The line changes when the 50-member products land; nothing else here does.
 */

// The floor comes from `tokens.json` through the generated ramps, so the legend and the map
// cannot drift apart about what "0 %" looks like.
import { MIN_PROBABILITY_OPACITY } from "@/lib/ramps";
import { cn } from "@/lib/utils";

/** The four thresholds of SPEC.md 7.2: the depth at which each class of vehicle stops. */
export const PROBABILITY_THRESHOLDS_CM = [15, 30, 45, 60] as const;

const STOPS: Record<number, string> = {
  15: "Two-wheelers",
  30: "Cars",
  45: "Buses and trucks",
  60: "Rescue vehicles",
};

export interface ProbabilityLegendProps {
  thresholdCm: number;
  onThresholdChange: (cm: number) => void;
  /** True when the run has one member, so P is 0 or 1 and the legend says so. */
  deterministic?: boolean;
  className?: string;
}

export function ProbabilityLegend({
  thresholdCm,
  onThresholdChange,
  deterministic = false,
  className,
}: ProbabilityLegendProps) {
  return (
    <aside
      aria-label="Probability legend"
      className={cn(
        // Laid out in the console's floating column rather than positioned over the map: at a
        // fixed offset from the map's top-left it drew straight across the layer panel's rows
        // (UI_SPEC 8, task D-17).
        "rounded-panel border-line bg-deep/90 pointer-events-auto w-[248px] shrink-0 border p-3 backdrop-blur-sm",
        className,
      )}
    >
      <h2 className="type-small text-text font-medium">Probability of exceeding</h2>

      <div
        role="radiogroup"
        aria-label="Exceedance threshold"
        className="mt-2 flex flex-wrap gap-1"
      >
        {PROBABILITY_THRESHOLDS_CM.map((cm) => (
          <button
            key={cm}
            type="button"
            role="radio"
            aria-checked={cm === thresholdCm}
            title={`${STOPS[cm]} stop at ${cm} cm`}
            onClick={() => onThresholdChange(cm)}
            className={cn(
              "num rounded-chip type-micro border px-2 py-0.5",
              cm === thresholdCm
                ? "border-tide bg-tide-soft text-tide"
                : "border-line bg-well text-text-2 hover:border-line-strong",
            )}
          >
            {cm} cm
          </button>
        ))}
      </div>

      <p className="type-micro text-text-3 mt-2">
        {STOPS[thresholdCm]} stop at <span className="num">{thresholdCm}</span> cm. Colour is the
        p50 depth; opacity is the probability.
      </p>

      {/* The opacity ramp itself, so "faint" has a value attached to it. */}
      <div className="mt-2 flex items-center gap-2">
        <span className="num type-micro text-text-3">0 %</span>
        <div
          aria-hidden="true"
          className="rounded-chip h-2 flex-1"
          style={{
            background: `linear-gradient(to right, color-mix(in srgb, var(--depth-3) ${
              MIN_PROBABILITY_OPACITY * 100
            }%, transparent), var(--depth-3))`,
          }}
        />
        <span className="num type-micro text-text-3">100 %</span>
      </div>

      {deterministic ? (
        <p className="type-micro text-text-2 mt-2">
          This run has one member, so every street is at 0 % or 100 % — a deterministic forecast
          either puts it over the line or it does not. The 50-member ensemble fills the ramp in.
        </p>
      ) : null}
    </aside>
  );
}

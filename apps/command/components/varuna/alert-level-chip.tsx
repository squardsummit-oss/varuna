import { cssVar } from "@/lib/ramps";
import { cn } from "@/lib/utils";

/** Alert levels in queue order (SPEC.md section 11.10): Severe 45 cm, Moderate 30 cm, Watch 15 cm. */
export const ALERT_LEVELS = ["severe", "moderate", "watch"] as const;
export type AlertLevel = (typeof ALERT_LEVELS)[number];

export const ALERT_LEVEL_LABELS: Record<AlertLevel, string> = {
  severe: "Severe",
  moderate: "Moderate",
  watch: "Watch",
};

/** Depth threshold that raises each level, in centimetres. */
export const ALERT_LEVEL_THRESHOLD_CM: Record<AlertLevel, number> = {
  severe: 45,
  moderate: 30,
  watch: 15,
};

/**
 * Each level takes the depth-ramp colour of the band its threshold sits in, so Severe reads as
 * 45-60 cm red, Moderate as 30-45 cm orange and Watch as 15-30 cm amber. Never `--danger`:
 * that token is for destructive UI actions only (SPEC.md section 6.2).
 */
const LEVEL_TOKEN: Record<AlertLevel, string> = {
  severe: "--depth-4",
  moderate: "--depth-3",
  watch: "--depth-2",
};

/** `var(--depth-4)` for a level; for inline styles on markers and borders. */
export function alertLevelCssVar(level: AlertLevel): string {
  return cssVar(LEVEL_TOKEN[level]);
}

export function isAlertLevel(value: unknown): value is AlertLevel {
  return typeof value === "string" && (ALERT_LEVELS as readonly string[]).includes(value);
}

export interface AlertLevelChipProps {
  level: AlertLevel;
  size?: "sm" | "md";
  /** Also print the threshold after the level, e.g. "Severe  45 cm". */
  showThreshold?: boolean;
  className?: string;
}

/** Level as a pill: a dot in the band colour plus the level text, so colour is never alone. */
export function AlertLevelChip({
  level,
  size = "md",
  showThreshold = false,
  className,
}: AlertLevelChipProps) {
  const label = ALERT_LEVEL_LABELS[level];
  const colour = alertLevelCssVar(level);
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-chip border bg-well text-text",
        size === "sm" ? "h-6 gap-1.5 px-2 type-micro" : "h-7 gap-2 px-2.5 type-small",
        className,
      )}
      style={{ borderColor: colour }}
      title={`${label}: depth likely above ${ALERT_LEVEL_THRESHOLD_CM[level]} cm`}
    >
      <span
        aria-hidden="true"
        className="size-2 shrink-0 rounded-full"
        style={{ backgroundColor: colour }}
      />
      <span className="font-medium">{label}</span>
      {showThreshold ? (
        <span className="num text-text-2">{ALERT_LEVEL_THRESHOLD_CM[level]} cm</span>
      ) : null}
    </span>
  );
}

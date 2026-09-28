import { cssVar, depthBand } from "@/lib/ramps";
import { cn } from "@/lib/utils";

export interface DepthChipProps {
  /** Water depth in centimetres; null or undefined renders "no data". */
  cm: number | null | undefined;
  size?: "sm" | "md";
  /** Also print the band label after the number, e.g. "45 cm  45-60 cm". */
  showBand?: boolean;
  className?: string;
}

/** "45-60 cm - buses and trucks impassable": the tooltip and accessible name for a depth. */
export function depthBandLabel(cm: number): string {
  const band = depthBand(cm);
  return `${band.label} - ${band.meaning}`;
}

/**
 * Depth as a pill: a dot in the band colour from the fixed depth ramp plus the number with its
 * unit. Colour is never the only carrier; the number is always shown (SPEC.md section 6.10).
 */
export function DepthChip({ cm, size = "md", showBand = false, className }: DepthChipProps) {
  const base = cn(
    "inline-flex items-center rounded-chip border border-line bg-well text-text",
    size === "sm" ? "h-6 gap-1.5 px-2 text-micro" : "h-7 gap-2 px-2.5 text-small",
    className,
  );

  if (cm === null || cm === undefined || Number.isNaN(cm)) {
    return (
      <span className={cn(base, "text-text-3")} title="No depth forecast for this segment yet">
        <span
          aria-hidden="true"
          className="size-2 shrink-0 rounded-full border border-line-strong"
        />
        no data
      </span>
    );
  }

  const band = depthBand(cm);
  const label = depthBandLabel(cm);
  const rounded = Math.round(cm);
  return (
    <span className={base} title={label} aria-label={`${rounded} cm, ${label}`}>
      <span
        aria-hidden="true"
        className="size-2 shrink-0 rounded-full"
        // The value is a token reference (var(--depth-N)), not a literal colour.
        style={{ backgroundColor: cssVar(`--depth-${band.key}`) }}
      />
      <span className="num font-medium">{rounded} cm</span>
      {showBand ? <span className="text-text-2">{band.label}</span> : null}
    </span>
  );
}

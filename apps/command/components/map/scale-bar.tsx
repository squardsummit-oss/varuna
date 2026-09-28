/**
 * The map's scale bar, drawn in the design system rather than by MapLibre's own control, so it
 * carries the token border, the token type scale and tabular numbers like every other figure on
 * the console (SPEC.md sections 6.2 to 6.4).
 */
import { cn } from "@/lib/utils";

/** Widest the bar is allowed to grow before a shorter round distance is chosen. */
const MAX_BAR_PX = 96;

/** Web Mercator ground resolution at the equator for MapLibre's 512 px tiles, in metres. */
const EQUATOR_METRES = 40_075_016.686;
const TILE_PX = 512;

/** Metres per CSS pixel at a zoom and latitude. */
export function metresPerPixel(zoom: number, latitude: number): number {
  const cos = Math.cos((latitude * Math.PI) / 180);
  return (EQUATOR_METRES * Math.abs(cos)) / (TILE_PX * 2 ** zoom);
}

/** 1, 2, 5 and 10 at every power of ten: the round distances a scale bar may show. */
function roundDistance(metres: number): number {
  const power = 10 ** Math.floor(Math.log10(metres));
  for (const step of [10, 5, 3, 2, 1]) {
    if (step * power <= metres) return step * power;
  }
  return power;
}

export interface ScaleReading {
  /** Bar width in CSS pixels. */
  widthPx: number;
  /** "500 m", "2 km". */
  label: string;
}

/** The widest round distance that fits inside `MAX_BAR_PX` at this zoom and latitude. */
export function scaleReading(zoom: number, latitude: number, maxPx = MAX_BAR_PX): ScaleReading {
  const perPixel = metresPerPixel(zoom, latitude);
  if (!Number.isFinite(perPixel) || perPixel <= 0) return { widthPx: maxPx, label: "" };
  const metres = roundDistance(perPixel * maxPx);
  const widthPx = Math.round(metres / perPixel);
  const label = metres >= 1000 ? `${metres / 1000} km` : `${metres} m`;
  return { widthPx, label };
}

export interface ScaleBarProps {
  zoom: number;
  latitude: number;
  className?: string;
}

/** A scale bar for the current view. Renders nothing until the map reports a usable zoom. */
export function ScaleBar({ zoom, latitude, className }: ScaleBarProps) {
  const reading = scaleReading(zoom, latitude);
  if (!reading.label) return null;
  return (
    <div
      className={cn("flex items-end gap-2", className)}
      role="img"
      aria-label={`Map scale: ${reading.label}`}
    >
      <div
        aria-hidden="true"
        className="h-1.5 border-x border-b border-line-strong"
        style={{ width: `${reading.widthPx}px` }}
      />
      <span aria-hidden="true" className="num type-micro leading-none text-text-3">
        {reading.label}
      </span>
    </div>
  );
}

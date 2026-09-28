import { cssVar, depthBand } from "@/lib/ramps";
import { cn } from "@/lib/utils";

export interface SparklineProps {
  /** The series, in time order. Fewer than two points renders a flat rule, never a fake curve. */
  values: readonly number[];
  /** Index currently selected on the time bar; drawn as a vertical hairline. */
  markerIndex?: number | null;
  /** Fixes the vertical scale across a list so rows are comparable. Defaults to this row's max. */
  maxValue?: number;
  width?: number;
  height?: number;
  /** Colour the line by the depth band of the peak. Off draws it in the accent. */
  colorByDepth?: boolean;
  /** Accessible description; the sparkline is `aria-hidden` without one. */
  label?: string;
  className?: string;
}

/**
 * A 3-hour depth series in the width of a table cell (SPEC.md 6.6, section 7.2's hotspot row).
 *
 * Hand-drawn SVG rather than a chart library: the rail renders one of these per hotspot and they
 * carry no axes, no tooltip and no interaction, so a charting runtime would be paying for
 * machinery none of them use. It is also the only way the line can be coloured by the depth ramp
 * band of its own peak, which is what makes a glance down the rail readable — a red line means a
 * street closes, whatever the row's scale happens to be.
 *
 * There is no motion here; the shape is data and it changes only when the run does.
 */
export function Sparkline({
  values,
  markerIndex = null,
  maxValue,
  width = 72,
  height = 20,
  colorByDepth = true,
  label,
  className,
}: SparklineProps) {
  const peak = values.length > 0 ? Math.max(...values) : 0;
  // A shared ceiling keeps rows comparable; a per-row one would make a 4 cm puddle draw the same
  // mountain as a 60 cm one.
  const ceiling = Math.max(maxValue ?? peak, 1);
  const colour = colorByDepth ? cssVar(`--depth-${depthBand(peak).key}`) : cssVar("--tide");

  if (values.length < 2) {
    return (
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        className={cn("shrink-0 overflow-visible", className)}
        role={label ? "img" : "presentation"}
        aria-label={label}
        aria-hidden={label ? undefined : true}
      >
        <line
          x1={0}
          y1={height - 1}
          x2={width}
          y2={height - 1}
          stroke={cssVar("--line-strong")}
          strokeWidth={1}
        />
      </svg>
    );
  }

  const step = width / (values.length - 1);
  // One pixel of headroom top and bottom so a peak at the ceiling is not clipped by the stroke.
  const y = (v: number) => height - 1 - (Math.max(v, 0) / ceiling) * (height - 2);
  const points = values.map((v, i) => `${(i * step).toFixed(2)},${y(v).toFixed(2)}`);
  const line = `M${points.join("L")}`;
  const area = `${line}L${width},${height}L0,${height}Z`;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("shrink-0", className)}
      role={label ? "img" : "presentation"}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      preserveAspectRatio="none"
    >
      <path d={area} fill={colour} fillOpacity={0.16} />
      <path
        d={line}
        fill="none"
        stroke={colour}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      {markerIndex !== null && markerIndex >= 0 && markerIndex < values.length ? (
        <line
          x1={markerIndex * step}
          y1={0}
          x2={markerIndex * step}
          y2={height}
          stroke={cssVar("--text-3")}
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
    </svg>
  );
}

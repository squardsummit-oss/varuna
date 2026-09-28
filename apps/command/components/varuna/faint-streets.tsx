"use client";

import { useEffect, useState } from "react";

import { apiUrl } from "@/lib/api/client";
import { cn } from "@/lib/utils";

/** [west, south, east, north] in WGS84. */
export type Bbox = readonly [number, number, number, number];

/**
 * Dadar, Parel and King's Circle: the stretch of Mumbai the demo is about, cut landscape so it
 * fills a laptop screen. Inside the MUM-CENTRAL AOI (SPEC.md 3.3).
 */
export const DEFAULT_STREETS_BBOX: Bbox = [72.815, 18.995, 72.885, 19.045];

/** Coordinate units per degree of latitude in the SVG's viewBox; the size the shape is drawn at. */
const UNITS_PER_DEGREE = 10_000;

type Weight = "major" | "mid" | "minor";

const WEIGHT_BY_CLASS: Record<string, Weight> = {
  motorway: "major",
  trunk: "major",
  primary: "major",
  secondary: "mid",
  tertiary: "mid",
};

export interface StreetDrawing {
  width: number;
  height: number;
  /** One SVG path per road weight, every segment of that weight in a single `d`. */
  paths: Record<Weight, string>;
  segmentCount: number;
}

interface LayerFeature {
  properties?: Record<string, unknown> | null;
  geometry?: { type?: string; coordinates?: unknown } | null;
}

/**
 * Project the city's segment layer into an SVG drawing of `bbox`.
 *
 * Equirectangular with the longitude scaled by cos(latitude): at Mumbai's latitude that is within
 * a fraction of a percent of the map the console draws, and it needs no projection library for a
 * picture nobody interacts with.
 */
export function drawStreets(features: readonly LayerFeature[], bbox: Bbox): StreetDrawing {
  const [west, south, east, north] = bbox;
  const kx = Math.cos((((south + north) / 2) * Math.PI) / 180) * UNITS_PER_DEGREE;
  const ky = UNITS_PER_DEGREE;
  const width = (east - west) * kx;
  const height = (north - south) * ky;
  const parts: Record<Weight, string[]> = { major: [], mid: [], minor: [] };
  let segmentCount = 0;

  for (const feature of features) {
    const geometry = feature.geometry;
    const lines: unknown[] =
      geometry?.type === "LineString"
        ? [geometry.coordinates]
        : geometry?.type === "MultiLineString"
          ? ((geometry.coordinates as unknown[]) ?? [])
          : [];
    const weight = WEIGHT_BY_CLASS[String(feature.properties?.class ?? "")] ?? "minor";
    let drawn = false;
    for (const line of lines) {
      if (!Array.isArray(line) || line.length < 2) continue;
      const points: string[] = [];
      for (const point of line) {
        if (!Array.isArray(point)) continue;
        const [lon, lat] = point as [number, number];
        if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
        points.push(`${((lon - west) * kx).toFixed(1)} ${((north - lat) * ky).toFixed(1)}`);
      }
      if (points.length < 2) continue;
      parts[weight].push(`M${points.join("L")}`);
      drawn = true;
    }
    if (drawn) segmentCount += 1;
  }

  return {
    width,
    height,
    paths: { major: parts.major.join(""), mid: parts.mid.join(""), minor: parts.minor.join("") },
    segmentCount,
  };
}

/** A faint street grid drawn from the --line token: the picture when the city cannot be read. */
const gridStyle: React.CSSProperties = {
  backgroundImage:
    "repeating-linear-gradient(0deg, var(--line) 0, var(--line) 1px, transparent 1px, transparent 48px), " +
    "repeating-linear-gradient(90deg, var(--line) 0, var(--line) 1px, transparent 1px, transparent 48px)",
};

/** Fades the picture out towards the edges so the copy over it stays the thing that is read. */
const vignette: React.CSSProperties = {
  maskImage: "radial-gradient(ellipse at 50% 45%, black 0%, transparent 72%)",
  WebkitMaskImage: "radial-gradient(ellipse at 50% 45%, black 0%, transparent 72%)",
};

type State = { kind: "loading" } | { kind: "ready"; drawing: StreetDrawing } | { kind: "fallback" };

export interface FaintStreetsProps {
  city?: string;
  bbox?: Bbox;
  className?: string;
}

/**
 * Mumbai's own streets, drawn faintly behind the 404 copy (SPEC.md 7.13: "the map faintly
 * behind"). Read-only and static: no MapLibre, no WebGL, no motion, nothing to focus. The roads
 * come from the served city segments layer; when the API cannot be reached the page keeps the
 * plain grid instead of a map that is not there.
 */
export function FaintStreets({
  city = "mumbai",
  bbox = DEFAULT_STREETS_BBOX,
  className,
}: FaintStreetsProps) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [west, south, east, north] = bbox;

  useEffect(() => {
    const controller = new AbortController();
    const area = [west, south, east, north] as const;
    (async () => {
      try {
        const response = await fetch(
          apiUrl(`/v1/city/${encodeURIComponent(city)}/layers/segments?bbox=${area.join(",")}`),
          { signal: controller.signal },
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const body = (await response.json()) as { features?: LayerFeature[] };
        const drawing = drawStreets(body.features ?? [], area);
        if (!controller.signal.aborted) {
          setState(drawing.segmentCount > 0 ? { kind: "ready", drawing } : { kind: "fallback" });
        }
      } catch {
        if (!controller.signal.aborted) setState({ kind: "fallback" });
      }
    })();
    return () => controller.abort();
  }, [city, west, south, east, north]);

  return (
    <div
      aria-hidden="true"
      data-slot="faint-streets"
      data-state={state.kind}
      className={cn("pointer-events-none absolute inset-0", className)}
      style={vignette}
    >
      {state.kind === "ready" ? (
        <svg
          data-slot="faint-streets-map"
          className="h-full w-full opacity-70"
          viewBox={`0 0 ${state.drawing.width.toFixed(0)} ${state.drawing.height.toFixed(0)}`}
          preserveAspectRatio="xMidYMid slice"
          focusable="false"
        >
          <g fill="none" strokeLinecap="round" strokeLinejoin="round">
            <path
              d={state.drawing.paths.minor}
              stroke="var(--line)"
              strokeWidth={0.75}
              vectorEffect="non-scaling-stroke"
            />
            <path
              d={state.drawing.paths.mid}
              stroke="var(--line-strong)"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
            <path
              d={state.drawing.paths.major}
              stroke="var(--text-3)"
              strokeOpacity={0.55}
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
          </g>
        </svg>
      ) : (
        <div
          data-slot="faint-streets-grid"
          className="h-full w-full opacity-60"
          style={gridStyle}
        />
      )}
    </div>
  );
}

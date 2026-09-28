"use client";

/**
 * AnimatedBeam, for motion M3 (SPEC.md 8: "Magic UI AnimatedBeam", landing cycle diagram).
 *
 * Source: the Magic UI registry item https://magicui.design/r/animated-beam.json, fetched on
 * 2026-09-15 and written into components/ui by hand rather than through the shadcn CLI, so the
 * CLI could not rewrite components.json or the lockfile. It keeps Magic UI's shape - an absolutely
 * positioned SVG measured against `containerRef`, a faint track and a beam travelling from
 * `fromRef` to `toRef`, re-measured by a ResizeObserver - and changes four things:
 *
 * 1. Colours are tokens (`--line-strong` track, `--tide` beam) instead of the orange-to-violet
 *    defaults, and there is no gradient: section 6.9 bans gradient washes.
 * 2. The beam is a dash travelling along the path (motion's `pathLength`/`pathOffset`), not a
 *    gradient whose x coordinates slide. Magic UI's gradient only moves horizontally, so on a
 *    phone, where the pipeline stacks vertically, its beams never appeared.
 * 3. The path runs edge to edge between the two boxes (`beamSegment`) rather than centre to
 *    centre, so a static arrowhead is not hidden under the node it points at.
 * 4. It travels only while `active` (the diagram passes in view and tab visible), and under
 *    reduced motion it is a static arrow - M3's reduced-motion column.
 *
 * Durations and easing come from lib/motion.ts: M3 states none of its own, so a hop takes the
 * catalogue's draw-on ceiling (1.2 s) on the global UI easing.
 */

import { useEffect, useId, useState, type RefObject } from "react";
import { motion } from "motion/react";

import { DUR, EASE_UI } from "@/lib/motion";
import { cn } from "@/lib/utils";

/** A box relative to the diagram, in CSS pixels. */
export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Segment {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

/**
 * The visible part of the line between two box centres: from where it leaves `from` to where it
 * enters `to`, pulled back `gap` pixels at each end so an arrowhead has room. Null when a box has
 * no size (not laid out yet) or the boxes overlap, where there is no beam to draw.
 */
export function beamSegment(from: Box, to: Box, gap = 6): Segment | null {
  if (from.width <= 0 || from.height <= 0 || to.width <= 0 || to.height <= 0) return null;
  const ax = from.x + from.width / 2;
  const ay = from.y + from.height / 2;
  const bx = to.x + to.width / 2;
  const by = to.y + to.height / 2;
  const dx = bx - ax;
  const dy = by - ay;
  const length = Math.hypot(dx, dy);
  if (length === 0) return null;
  // Fraction of the centre-to-centre line spent inside a box.
  const inside = (box: Box) =>
    Math.min(
      dx === 0 ? Infinity : box.width / 2 / Math.abs(dx),
      dy === 0 ? Infinity : box.height / 2 / Math.abs(dy),
    );
  const start = inside(from) + gap / length;
  const end = 1 - inside(to) - gap / length;
  if (end <= start) return null;
  return { x1: ax + dx * start, y1: ay + dy * start, x2: ax + dx * end, y2: ay + dy * end };
}

/** Share of the path the travelling dash covers. */
export const BEAM_LENGTH = 0.25;

export interface AnimatedBeamProps {
  containerRef: RefObject<HTMLElement | null>;
  fromRef: RefObject<HTMLElement | null>;
  toRef: RefObject<HTMLElement | null>;
  /** The beam travels only while this is true: in view and the tab visible. */
  active: boolean;
  /** A static arrow instead of a beam (M3 under reduced motion). */
  reducedMotion: boolean;
  /** Seconds before the first pass. */
  delay?: number;
  /** Seconds for one pass. */
  duration?: number;
  /** Seconds between passes. */
  repeatDelay?: number;
  /** Pixels between a node's edge and the line. */
  gap?: number;
  className?: string;
}

export function AnimatedBeam({
  containerRef,
  fromRef,
  toRef,
  active,
  reducedMotion,
  delay = 0,
  duration = DUR.drawOnMax,
  repeatDelay = 0,
  gap = 6,
  className,
}: AnimatedBeamProps) {
  const markerId = `beam-arrow-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const [geometry, setGeometry] = useState<{
    width: number;
    height: number;
    segment: Segment | null;
  }>({ width: 0, height: 0, segment: null });

  useEffect(() => {
    const update = () => {
      const container = containerRef.current;
      const from = fromRef.current;
      const to = toRef.current;
      if (!container || !from || !to) return;
      const origin = container.getBoundingClientRect();
      const relative = (rect: DOMRect): Box => ({
        x: rect.left - origin.left,
        y: rect.top - origin.top,
        width: rect.width,
        height: rect.height,
      });
      setGeometry({
        width: origin.width,
        height: origin.height,
        segment: beamSegment(
          relative(from.getBoundingClientRect()),
          relative(to.getBoundingClientRect()),
          gap,
        ),
      });
    };
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    for (const element of [containerRef.current, fromRef.current, toRef.current]) {
      if (element) observer.observe(element);
    }
    return () => observer.disconnect();
  }, [containerRef, fromRef, toRef, gap]);

  const { width, height, segment } = geometry;
  if (!segment) return null;
  const d = `M ${segment.x1},${segment.y1} L ${segment.x2},${segment.y2}`;

  return (
    <svg
      aria-hidden="true"
      fill="none"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("pointer-events-none absolute top-0 left-0", className)}
    >
      {reducedMotion ? (
        <defs>
          <marker
            id={markerId}
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="8"
            markerHeight="8"
            orient="auto"
          >
            <path d="M0,0 L8,4 L0,8 Z" fill="var(--text-3)" />
          </marker>
        </defs>
      ) : null}
      <path
        data-beam="track"
        d={d}
        stroke={reducedMotion ? "var(--text-3)" : "var(--line-strong)"}
        strokeWidth={1.5}
        markerEnd={reducedMotion ? `url(#${markerId})` : undefined}
      />
      {!reducedMotion && active ? (
        <motion.path
          data-beam="travel"
          d={d}
          stroke="var(--tide)"
          strokeWidth={2}
          strokeLinecap="butt"
          // A dash a quarter of the path long, starting just before the path and finishing just
          // past it, so each pass enters and leaves rather than popping in.
          initial={{ pathLength: BEAM_LENGTH, pathOffset: -BEAM_LENGTH }}
          animate={{ pathOffset: 1 }}
          transition={{ duration, delay, ease: EASE_UI, repeat: Infinity, repeatDelay }}
        />
      ) : null}
    </svg>
  );
}

"use client";

/**
 * TracingBeam, for motion M5 (SPEC.md 8: "Aceternity TracingBeam", landing roadmap).
 *
 * Source: the Aceternity registry item https://ui.aceternity.com/registry/tracing-beam.json,
 * fetched on 2026-09-15 and written into components/ui by hand rather than through the shadcn
 * CLI. It keeps Aceternity's idea - a line beside the content whose lit part follows the reader's
 * scroll through it (`useScroll` on the wrapper) - and drops what section 6 does not allow:
 *
 * - the cyan-to-violet gradient stroke (6.9 bans gradient washes; the lit line is `--tide`),
 * - the drop shadow and colour change on the start dot (6.4: no shadows; the roadmap draws its
 *   own dots on the line),
 * - the spring smoothing, which is a motion no catalogue row states. The line is bound to scroll
 *   directly, so it moves exactly as far as the reader does.
 *
 * Reduced motion: the whole line is lit and static (M5's reduced-motion column).
 */

import type { ReactNode } from "react";
import { useRef } from "react";
import { motion, useScroll } from "motion/react";

import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { cn } from "@/lib/utils";

export interface TracingBeamProps {
  children: ReactNode;
  className?: string;
}

/**
 * The scroll window the beam traces over: it starts when the top of the content reaches 80 % of
 * the viewport and is fully lit when the bottom reaches 60 %, so the last stage is lit while it is
 * still comfortably on screen.
 */
export const TRACE_OFFSET = ["start 80%", "end 60%"] as const;

export function TracingBeam({ children, className }: TracingBeamProps) {
  const ref = useRef<HTMLDivElement>(null);
  const reducedMotion = usePrefersReducedMotion();
  const { scrollYProgress } = useScroll({ target: ref, offset: [...TRACE_OFFSET] });

  return (
    <div ref={ref} className={cn("relative", className)}>
      <div
        aria-hidden="true"
        data-beam="track"
        className="bg-line absolute inset-y-0 left-0 w-px"
      />
      {reducedMotion ? (
        <div
          aria-hidden="true"
          data-beam="static"
          className="bg-tide absolute inset-y-0 left-0 w-px"
        />
      ) : (
        <motion.div
          aria-hidden="true"
          data-beam="trace"
          className="bg-tide absolute inset-y-0 left-0 w-px origin-top"
          style={{ scaleY: scrollYProgress }}
        />
      )}
      {children}
    </div>
  );
}

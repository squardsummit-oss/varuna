import { cn } from "@/lib/utils";

export interface SkeletonProps {
  className?: string;
  /** Render this many stacked text lines instead of one block; the last line is shorter. */
  lines?: number;
}

/**
 * Loading placeholder (motion M22): shimmer only, never a spinner. The `skeleton-shimmer`
 * utility in globals.css sweeps a gradient; under prefers-reduced-motion the block is static.
 */
export function Skeleton({ className, lines }: SkeletonProps) {
  if (lines && lines > 1) {
    return (
      <div aria-hidden="true" className={cn("flex flex-col gap-2", className)}>
        {Array.from({ length: lines }, (_, i) => (
          <div
            key={i}
            className={cn(
              "h-3.5 rounded-control skeleton-shimmer motion-reduce:animate-none",
              i === lines - 1 ? "w-2/3" : "w-full",
            )}
          />
        ))}
      </div>
    );
  }
  return (
    <div
      aria-hidden="true"
      className={cn("h-4 w-full rounded-control skeleton-shimmer motion-reduce:animate-none", className)}
    />
  );
}

export interface SkeletonRowsProps {
  /** Number of 40 px list rows to draw. */
  rows?: number;
  className?: string;
}

/** Placeholder for a list or rail: rows of a dot, a label line and a short value line. */
export function SkeletonRows({ rows = 5, className }: SkeletonRowsProps) {
  return (
    <div aria-hidden="true" className={cn("flex flex-col", className)}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex h-10 items-center gap-3 border-b border-line last:border-b-0">
          <div className="size-4 shrink-0 rounded-full skeleton-shimmer motion-reduce:animate-none" />
          <div className="h-3.5 flex-1 rounded-control skeleton-shimmer motion-reduce:animate-none" />
          <div className="h-3.5 w-14 rounded-control skeleton-shimmer motion-reduce:animate-none" />
        </div>
      ))}
    </div>
  );
}

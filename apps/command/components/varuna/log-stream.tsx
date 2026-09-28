"use client";

import { useEffect, useRef } from "react";

import { EmptyState } from "@/components/varuna/empty-state";
import { formatIst } from "@/lib/format";
import { cn } from "@/lib/utils";

export type LogLevel = "info" | "warn" | "error";

export interface LogLine {
  /** ISO 8601 timestamp of the log record. */
  ts: string;
  level?: LogLevel;
  /** The pipeline's own message, never scripted copy. */
  text: string;
}

export interface LogStreamProps {
  lines: LogLine[];
  /** Height of the scroll area; defaults to 16 rem. */
  className?: string;
  /** Empty-state description; the wizard's default says where logs come from. */
  emptyDescription?: string;
}

/**
 * Monospace log area (Geist Mono is allowed for log streams, SPEC.md section 6.3) that follows
 * the newest line. Rendered as a live region so screen readers hear progress.
 */
export function LogStream({
  lines,
  className,
  emptyDescription = "Logs stream here when the wizard runs.",
}: LogStreamProps) {
  const boxRef = useRef<HTMLDivElement>(null);

  // Scroll the log box itself. `scrollIntoView` would also scroll every ancestor scrollport, which
  // drags the whole page down to the log on mount (/design and the onboarding wizard both embed it).
  useEffect(() => {
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [lines.length]);

  if (lines.length === 0) {
    return (
      <div
        className={cn(
          "rounded-control border-line bg-ink flex h-64 items-center justify-center border",
          className,
        )}
      >
        <EmptyState size="sm" title="No logs yet" description={emptyDescription} />
      </div>
    );
  }

  return (
    <div
      ref={boxRef}
      role="log"
      aria-live="polite"
      aria-label="Pipeline log"
      // Focusable because it scrolls: a build writes more lines than the box shows, and a keyboard
      // user has to be able to reach the earlier ones (WCAG 2.1.1, axe scrollable-region-focusable).
      tabIndex={0}
      className={cn(
        "rounded-control border-line bg-ink text-micro focus-visible:ring-tide/50 h-64 overflow-y-auto border p-3 font-mono leading-relaxed focus-visible:ring-3 focus-visible:outline-none",
        className,
      )}
    >
      {lines.map((line, i) => (
        <div
          key={`${line.ts}-${i}`}
          className={cn(
            "flex gap-3 whitespace-pre-wrap",
            line.level === "error" && "text-status-degraded",
            line.level === "warn" && "text-text",
            (line.level ?? "info") === "info" && "text-text-2",
          )}
        >
          <span className="num text-text-3 shrink-0">{formatIst(line.ts, { seconds: true })}</span>
          <span className="min-w-0 break-words">{line.text}</span>
        </div>
      ))}
    </div>
  );
}

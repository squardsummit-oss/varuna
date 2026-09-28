"use client";

import { Suspense, useEffect, useRef, useState, type ReactNode } from "react";

import { PanelErrorBoundary } from "@/components/varuna/panel-error-boundary";
import { Skeleton } from "@/components/varuna/skeleton";
import { cn } from "@/lib/utils";

export interface DeferredStoryProps {
  /** Names the group for the error boundary and the placeholder, e.g. "Jalayantra". */
  title: string;
  /** Height the placeholder reserves, so mounting a group never shifts the page above it. */
  minHeight: number;
  children: ReactNode;
  className?: string;
}

/** How far below the fold a group starts loading, so it is drawn before it is scrolled to. */
const ROOT_MARGIN = "600px 0px";

/**
 * A group of stories that loads when it nears the viewport. The groups below are lazy modules
 * (charts, a dispatch map, API reads), so `/design` pays for them only when someone scrolls to
 * them - the page stays a fast baseline. With no IntersectionObserver (a test runner) the group
 * mounts at once. A group that throws fails in its own frame and never blanks the page.
 */
export function DeferredStory({ title, minHeight, children, className }: DeferredStoryProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node || near) return;
    if (typeof IntersectionObserver === "undefined") {
      setNear(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setNear(true);
          observer.disconnect();
        }
      },
      { rootMargin: ROOT_MARGIN },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [near]);

  const placeholder = (
    <div aria-busy="true" aria-label={`Loading the ${title} stories`} className="flex flex-col gap-3">
      <Skeleton className="h-5 w-56" />
      <div style={{ height: Math.max(0, minHeight - 32) }}>
        <Skeleton className="h-full w-full" />
      </div>
    </div>
  );

  return (
    <div ref={ref} className={cn("min-w-0", className)} style={near ? undefined : { minHeight }}>
      {near ? (
        <PanelErrorBoundary title={title}>
          <Suspense fallback={placeholder}>{children}</Suspense>
        </PanelErrorBoundary>
      ) : (
        placeholder
      )}
    </div>
  );
}

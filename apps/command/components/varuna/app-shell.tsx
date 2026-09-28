"use client";

import { useLatestRun } from "@/lib/hooks/use-latest-run";
import { TopBar } from "@/components/varuna/top-bar";
import { cn } from "@/lib/utils";

export interface AppShellProps {
  children: React.ReactNode;
  /** The 360 px right rail (hotspots, alerts, pumps, reachability). Omit on screens without one. */
  rightRail?: React.ReactNode;
  /** The 96 px time bar. Omit on screens without a scrub. */
  bottomBar?: React.ReactNode;
  /**
   * Makes the top bar, the right rail and the bottom bar inert: nothing in them takes focus,
   * clicks or a screen reader's attention. The console sets it while its full view covers them,
   * so Tab stays in the map region; the chrome stays mounted and comes back exactly as it was.
   */
  chromeInert?: boolean;
  className?: string;
}

/**
 * The console shell (SPEC.md section 6.5): a 52 px top bar, the main canvas, an optional 360 px
 * right rail and an optional 96 px bottom bar. Fills the viewport and never scrolls as a page;
 * each region scrolls on its own. The screen nav used to be a 56 px rail down the left of this
 * row and now rides in the top bar at the team's request, so the canvas starts at the window edge.
 */
export function AppShell({
  children,
  rightRail,
  bottomBar,
  chromeInert = false,
  className,
}: AppShellProps) {
  // Every screen wearing this chrome shows the same run stamp, so every screen loads the run.
  useLatestRun();
  // React 19 writes `inert=""` for true and drops the attribute for undefined.
  const inert = chromeInert || undefined;

  return (
    <div
      data-slot="app-shell"
      className={cn("flex h-dvh min-h-0 flex-col overflow-hidden bg-ink text-text", className)}
    >
      {/* A plain block around the bar so the shell can make it inert without reaching into it;
          it takes the bar's own 52 px, so the layout is unchanged. */}
      <div data-slot="top-bar" className="shrink-0" inert={inert}>
        <TopBar />
      </div>
      <div className="flex min-h-0 flex-1">
        <main className="relative min-h-0 min-w-0 flex-1 overflow-hidden">{children}</main>
        {rightRail ? (
          <aside
            data-slot="right-rail"
            aria-label="Right rail"
            inert={inert}
            className="flex w-right-rail shrink-0 flex-col overflow-y-auto border-l border-line bg-deep"
          >
            {rightRail}
          </aside>
        ) : null}
      </div>
      {bottomBar ? (
        <div
          data-slot="bottom-bar"
          className="h-time-bar shrink-0 border-t border-line"
          inert={inert}
        >
          {bottomBar}
        </div>
      ) : null}
    </div>
  );
}

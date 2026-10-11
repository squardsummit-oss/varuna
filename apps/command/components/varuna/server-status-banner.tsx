"use client";

/**
 * Says what the reader is looking at when the hosted forecast server is not answering (ADR-0097).
 *
 * `lib/api/resilience.ts` answers the replay's requests from the stored 2 July 2019 recording
 * while the server wakes or after it has stopped; this pill is how that is never silent. It
 * appears only on the deployed site, only while the server is not answering or has just come
 * back, and offers the one action that helps: loading the live data once the server answers.
 * It can be hidden, because on some screens it sits over a header; a new state shows it again.
 */

import { RotateCw, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useServerStatus, type ServerState } from "@/lib/api/resilience";
import { cn } from "@/lib/utils";

/** The pill's sentence for each state; `stored` is whether this page has shown stored data. */
export function serverStatusMessage(state: ServerState, stored: boolean): string | null {
  switch (state) {
    case "waking":
      return stored
        ? "Waking the forecast server. Until it answers, this page shows the stored 2 July 2019 replay."
        : "Waking the forecast server. This takes up to a minute.";
    case "offline":
      return stored
        ? "Forecast server offline. This page shows the stored 2 July 2019 replay; live runs, reports and routes need the server."
        : "Forecast server offline. Live runs, reports and routes are unavailable until it answers.";
    case "back":
      return "Forecast server is back.";
    case "ok":
      return null;
  }
}

export interface ServerStatusBannerProps {
  /** Overrides the store (used by /design and tests). */
  state?: ServerState;
  stored?: boolean;
  className?: string;
}

export function ServerStatusBanner({ state: stateProp, stored: storedProp, className }: ServerStatusBannerProps) {
  const storeState = useServerStatus((s) => s.state);
  const storeStored = useServerStatus((s) => s.servedMirror);
  const state = stateProp ?? storeState;
  const stored = storedProp ?? storeStored;
  // Hidden for one state only: a change (offline, then back) is news and shows the pill again.
  const [hiddenFor, setHiddenFor] = useState<ServerState | null>(null);
  const message = serverStatusMessage(state, stored);
  if (!message || hiddenFor === state) return null;

  const back = state === "back";
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "pointer-events-none fixed inset-x-0 top-[60px] z-50 flex justify-center px-4",
        className,
      )}
    >
      <div
        className={cn(
          "pointer-events-auto flex max-w-[min(42rem,100%)] items-center gap-2.5 rounded-full border py-1.5 pr-1.5 pl-3",
          back ? "border-status-live/40 bg-deep" : "border-status-degraded/40 bg-deep",
        )}
      >
        <span
          aria-hidden="true"
          className={cn("size-2 shrink-0 rounded-full", back ? "bg-status-live" : "bg-status-degraded")}
        />
        <p className="type-small text-text">{message}</p>
        {back ? (
          <Button size="sm" variant="outline" className="shrink-0" onClick={() => window.location.reload()}>
            <RotateCw className="size-3.5" aria-hidden="true" />
            Load live data
          </Button>
        ) : null}
        <Button
          size="icon-sm"
          variant="ghost"
          className="shrink-0 rounded-full"
          aria-label="Hide this notice"
          onClick={() => setHiddenFor(state)}
        >
          <X className="size-3.5" aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}

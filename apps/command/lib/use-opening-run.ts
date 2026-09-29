"use client";

import { useEffect, useState } from "react";

import { apiUrl } from "@/lib/api/client";
import { newestLiveRunId, openingRunId, type RunSummary } from "@/lib/opening-run";
import { DEFAULT_SIM_TIME } from "@/lib/stores/replay";

/** How long a screen waits for the run registry before opening on the API's default run. */
export const OPENING_LOOKUP_MS = 4_000;

export interface OpeningRun {
  /** The run to load, or undefined to let the API pick its own newest. */
  runId: string | undefined;
  /** False while the registry is still being read; a map should hold its load until it flips. */
  resolved: boolean;
}

/**
 * The run a screen opens on when the URL pins none: the 06:40 cycle of the demo script.
 *
 * `/console` had this as an inline effect (SPEC.md 15, "the replay is pre-seeked to 06:40");
 * `/map` opened on whatever was newest, which on the replay is 09:10 — the calm cycle after the
 * storm, where a public map about which streets are passable has nothing to show. One rule, one
 * instant, two screens.
 *
 * `pinned` short-circuits the lookup for a screen that already has `?run=`. When the registry is
 * slow, unreachable, or has no run at the opening instant, `runId` stays undefined and the API's
 * default stands: a late map beats a wrong one, and a missing one beats neither.
 */
export function useOpeningRun(
  city?: string,
  pinned?: string,
  options: { preferLive?: boolean } = {},
): OpeningRun {
  const preferLive = options.preferLive ?? false;
  const [runId, setRunId] = useState<string | undefined>(pinned);
  const [resolved, setResolved] = useState(pinned !== undefined);

  useEffect(() => {
    if (pinned !== undefined || resolved) return;
    let cancelled = false;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), OPENING_LOOKUP_MS);
    const query = city ? `?city=${encodeURIComponent(city)}` : "";
    fetch(apiUrl(`/v1/runs${query}`), { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : { runs: [] }))
      .then((body: { runs?: RunSummary[] }) => {
        const runs = body.runs ?? [];
        // A screen for the public opens on today's forecast when the live loop has one; the
        // replay's opening cycle is the fallback, never a forecast presented as today's.
        const opening =
          (preferLive ? newestLiveRunId(runs, Date.now()) : undefined) ??
          openingRunId(runs, DEFAULT_SIM_TIME);
        if (opening && !cancelled) setRunId(opening);
      })
      .catch(() => undefined)
      .finally(() => {
        window.clearTimeout(timer);
        if (!cancelled) setResolved(true);
      });
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [city, pinned, resolved, preferLive]);

  return { runId, resolved };
}

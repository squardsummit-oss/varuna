"use client";

/**
 * The citizen dashboard's way in (motion M27, UI_SPEC 2; task D-14).
 *
 * This is now a name and a session key over {@link GlobeEntry}. Everything that used to be written
 * here - once per session, skippable by any key, the Skip button taking focus at 0.6 s, the globe
 * hidden from assistive technology, reduced motion painting one frame and cutting, and the
 * handover that reveals the map *before* the fade so no frame shows `--ink` - moved with the code
 * into `components/varuna/globe-entry.tsx` when the ward officer's desk was given the same entry
 * on 2026-09-23. Read it there. Nothing about this screen's behaviour changed in the move: the
 * `data-slot`, the session key, the props and the tests are the ones that shipped with D-14.
 *
 * The wrapper is kept rather than deleted because the dashboard's entry is a *decision* about the
 * dashboard - that this screen opens on the planet - and the file that carries the decision should
 * live beside the screen that made it, not in the shared kit.
 */

import { GlobeEntry } from "@/components/varuna/globe-entry";

export { SKIP_AFTER_MS } from "@/components/varuna/globe-entry";

/** Remembers, for this tab only, that the dashboard's entry has already played. */
export const INTRO_SESSION_KEY = "varuna.dashboard-intro.played";

export interface DashboardIntroProps {
  /** Called when the entry sequence has finished, or immediately when there is none. */
  onDone: () => void;
  /**
   * Play even if this tab has seen it. The dashboard leaves it alone; tests and a rehearsal reset
   * use it, because a demo that has to be reopened in a fresh tab is a demo waiting to go wrong.
   */
  force?: boolean;
}

export function DashboardIntro({ onDone, force = false }: DashboardIntroProps) {
  return (
    <GlobeEntry
      sessionKey={INTRO_SESSION_KEY}
      slot="dashboard-intro"
      onDone={onDone}
      force={force}
    />
  );
}

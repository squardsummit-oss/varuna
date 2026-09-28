"use client";

/**
 * Hydrate the run store from the registry, for the chrome on every screen.
 *
 * The console loads a run because it draws one. `/drains`, `/alerts`, `/pumps` and `/whatif` all
 * show the same top bar - mode banner, run stamp, verification chip - and none of them loaded a
 * run, so the chrome read "No runs yet" beside a screen full of that run's own products. The
 * banner is the first thing SPEC.md 15's ten-second judge test asks a stranger to read; it has
 * to be right on the screen they happen to be looking at.
 *
 * The console still sets the store itself, from the run it actually drew. This only fills the gap,
 * so it never overwrites a run that is already loaded.
 */

import { useLayoutEffect } from "react";

import { apiUrl } from "@/lib/api/client";
import { useRunStore, type RunMeta } from "@/lib/stores/run";

/** One row of `GET /v1/runs`, which is `run.json` as written - see the note on the mapping below. */
type RegistryRun = Omit<RunMeta, "mode" | "replay_mode"> & { mode: string; replay_mode: string };

export function useLatestRun(): void {
  // A layout effect, so "loading" is in the store before the first client paint: the banner's
  // hydration pass reads loading, and a passive effect would leave one frame of "No runs yet".
  useLayoutEffect(() => {
    const controller = new AbortController();
    const store = useRunStore.getState;
    // While the registry is being asked, the chrome says so. "No runs yet" before the answer is a
    // claim nobody has checked, and the deployed registry has taken 18 s to answer.
    if (!store().currentRun) store().setStatus("loading");
    /** Settle a non-run answer, unless someone drew a run in the meantime. */
    const settle = (status: "none" | "error", message?: string) => {
      if (store().currentRun || controller.signal.aborted) return;
      store().setStatus(status, message ?? null);
    };
    (async () => {
      try {
        const response = await fetch(apiUrl("/v1/runs?limit=1"), { signal: controller.signal });
        if (!response.ok) {
          settle("error", `The run registry answered HTTP ${response.status}.`);
          return;
        }
        const body = (await response.json()) as { runs?: RegistryRun[] };
        const run = body.runs?.[0];
        if (!run) {
          settle("none");
          return;
        }
        // Whoever drew a run wins: this is a fallback, not a source of truth.
        if (store().currentRun) return;
        store().setRun({
          run_id: run.run_id,
          city: run.city,
          cycle_ts: run.cycle_ts,
          // `run.json` and the store use the same two words for opposite things: the file's
          // `mode` is baked-or-live (how the products were made) and its `replay_mode` is
          // replay-or-live (how the inputs arrived); the store names them the other way round.
          // Copying field to field puts "replay" where the run stamp expects "baked".
          mode: run.replay_mode === "live" ? "live" : "replay",
          replay_mode: run.mode === "live" ? "live" : "baked",
          ensemble_n: run.ensemble_n,
          stage_ms: run.stage_ms,
          mass_balance_err: run.mass_balance_err,
          bundle: run.bundle,
          degraded_feeds: run.degraded_feeds,
          versions: run.versions,
        });
      } catch {
        settle("error", "The API is unreachable, so the run registry could not be read.");
      }
    })();
    return () => {
      controller.abort();
      // Unmounting mid-request must not leave the next screen's banner stuck on "Loading run".
      if (!store().currentRun && store().status === "loading") store().setStatus("none");
    };
  }, []);
}

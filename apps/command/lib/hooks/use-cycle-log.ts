"use client";

/**
 * The cycle log's rows, read from the run registry (SPEC.md 7.2, 7.8).
 *
 * **Why this is a hook rather than two effects.** `/replay` loaded the registry inline and the
 * console's `ReplayPanel` passed a module-level empty array with the comment "Cycle rows arrive
 * from the run registry in Phase 5". Phase 5 arrived; the panel did not. So the console's replay
 * panel showed "No cycles yet - press Play on the replay" over a registry holding seventeen runs,
 * which is the same defect P6.11 fixed on `/replay` and left on the screen that 70 % of the demo
 * happens on. One loader now serves both, and the empty state means empty.
 *
 * **One row per run, not per cycle.** The demo registry holds several runs of the same cycle time
 * - the 2026-09-13 and 2026-09-23 bakes, plus a live run - so rows are identified by `run_id`.
 * Keying them on the cycle time is what made React warn twenty-nine times on one load of
 * `/replay` (P10.2, 2026-09-24).
 *
 * **What it does not do.** It does not follow `runs.published`: a run that is baked while the
 * panel is open does not appear until the bundle changes or the screen is mounted again. The log
 * is a record of what is on disk, and the live cycle already reports itself through the budget
 * bar, so a socket subscription here would buy a row nobody is waiting for.
 */

import { useEffect, useState } from "react";

import { apiUrl } from "@/lib/api/client";
import type { CycleLogRow } from "@/components/varuna/cycle-log";

/**
 * Stages a baked cycle ran. The run summary carries the total, not the per-stage split, so this
 * names what ran rather than claiming a timing the endpoint did not return.
 */
export const BAKED_STAGES = "decode, sky, twin, pulse, products";

/** One row of `GET /v1/runs`, taken loosely because the registry summary has no response model. */
type RegistryRun = Record<string, unknown>;

export interface UseCycleLogOptions {
  /** Show only runs of this bundle. Undefined or empty shows every run the registry returns. */
  bundleId?: string;
}

export function useCycleLog({ bundleId }: UseCycleLogOptions = {}): CycleLogRow[] {
  const [rows, setRows] = useState<CycleLogRow[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    // `city=all`, because the log follows the *bundle* rather than the configured city - the
    // Chennai design storm is selectable here too.
    fetch(apiUrl("/v1/runs?city=all&limit=200"), { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : { runs: [] }))
      .then((body: { runs?: RegistryRun[] }) =>
        setRows(
          (body.runs ?? [])
            .filter((run) => !bundleId || run.bundle === bundleId)
            .map((run) => ({
              id: String(run.run_id ?? ""),
              time: String(run.cycle_ts ?? ""),
              stages: BAKED_STAGES,
              ms: Number(run.total_ms ?? 0),
              massBalance:
                run.mass_balance_err === null || run.mass_balance_err === undefined
                  ? null
                  : Number(run.mass_balance_err),
            }))
            // Oldest first: the log reads as the morning, top to bottom. Runs of the same cycle
            // then order by their own id, so a re-render never shuffles them.
            .sort((a, b) => a.time.localeCompare(b.time) || a.id.localeCompare(b.id)),
        ),
      )
      .catch(() => setRows([]));
    return () => controller.abort();
  }, [bundleId]);

  return rows;
}

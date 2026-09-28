"use client";

import { create } from "zustand";

import { CYCLE_STAGES } from "@/lib/api/schemas";

/** Whether the run was computed live or served from a bake (run.json `mode`). */
export type RunReplayMode = "baked" | "live";

/** System mode shown in the mode banner. */
export type SystemMode = "replay" | "live" | "degraded" | "none";

export type RunStatus = "none" | "loading" | "ready" | "error";

/**
 * The slice of `data/runs/<run_id>/run.json` the chrome reads (SPEC.md section 10.3).
 * Declared locally so the chrome does not depend on the generated API types.
 */
export interface RunMeta {
  run_id: string;
  city: string;
  /** Cycle time, ISO 8601 with +05:30. */
  cycle_ts: string;
  /** "replay" or "live": how the inputs arrived. */
  mode: string;
  /** "baked" or "live": how the products were made. */
  replay_mode: RunReplayMode;
  ensemble_n?: number | null;
  stage_ms?: Record<string, number>;
  mass_balance_err?: number | null;
  bundle?: string | null;
  /** Feeds missing in this cycle, e.g. ["radar"]; non-empty means degraded. */
  degraded_feeds?: string[];
  versions?: { sky?: string; twin?: string; flash?: string };
  /** Minutes between forecast steps; 5 on every run so far. */
  step_min?: number;
  /** p10/p50/p90 of mean street depth per step across members: the time bar's band (7.2). */
  aoi_depth_band?: { p10: number[]; p50: number[]; p90: number[] } | null;
}

export interface RunState {
  currentRun: RunMeta | null;
  status: RunStatus;
  mode: SystemMode;
  /** What went wrong when status is "error"; plain language for the banner. */
  errorMessage: string | null;

  setRun: (run: RunMeta | null) => void;
  setStatus: (status: RunStatus, errorMessage?: string | null) => void;
  setMode: (mode: SystemMode) => void;
  clear: () => void;
}

/** Derives the banner mode from a run's provenance and missing feeds. */
export function deriveSystemMode(run: RunMeta | null): SystemMode {
  if (!run) return "none";
  if (run.degraded_feeds && run.degraded_feeds.length > 0) return "degraded";
  return run.mode === "live" ? "live" : "replay";
}

/**
 * Wall-clock of a cycle in milliseconds, each cycle stage counted once, or null when the run has
 * no stage timings.
 *
 * Only `CYCLE_STAGES` keys count. run.json also carries the Twin's sub-timings
 * (`twin_total_ms`, `twin_surface_ms` and the rest), measured inside the `twin` wall clock, and
 * summing every key showed 189.7 s on the run stamp for a 76.9 s cycle. The API applies the same
 * allowlist (`varuna_schemas.models.stage_total_ms`, ADR-0046), so the stamp and `/v1/runs` agree.
 */
export function totalStageMs(run: RunMeta | null): number | null {
  const stageMs = run?.stage_ms;
  if (!stageMs) return null;
  const values = CYCLE_STAGES.map((stage) => stageMs[stage]).filter(
    (v): v is number => typeof v === "number" && Number.isFinite(v),
  );
  return values.length ? values.reduce((a, b) => a + b, 0) : null;
}

export const useRunStore = create<RunState>()((set) => ({
  currentRun: null,
  status: "none",
  mode: "none",
  errorMessage: null,

  setRun: (run) =>
    set({
      currentRun: run,
      status: run ? "ready" : "none",
      mode: deriveSystemMode(run),
      errorMessage: null,
    }),
  setStatus: (status, errorMessage = null) =>
    set((s) => ({
      status,
      errorMessage: status === "error" ? errorMessage : null,
      mode: status === "error" && !s.currentRun ? "none" : s.mode,
    })),
  setMode: (mode) => set({ mode }),
  clear: () => set({ currentRun: null, status: "none", mode: "none", errorMessage: null }),
}));

export const selectHasRun = (s: RunState): boolean => s.currentRun !== null;

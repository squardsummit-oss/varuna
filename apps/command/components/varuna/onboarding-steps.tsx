"use client";

import {
  Check,
  CircleDashed,
  HardDrive,
  History,
  Loader,
  Minus,
  TriangleAlert,
} from "lucide-react";

import { formatMs, formatSeconds } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The six steps of city-in-a-box (SPEC.md section 7.9), in order. */
export const ONBOARDING_STEP_IDS = [
  "area",
  "fetch",
  "condition",
  "drains",
  "graph",
  "forecast",
] as const;
export type OnboardingStepId = (typeof ONBOARDING_STEP_IDS)[number];

export const ONBOARDING_STEP_LABELS: Record<OnboardingStepId, string> = {
  area: "Choose area",
  fetch: "Fetch open data",
  condition: "Condition terrain",
  drains: "Infer drains",
  graph: "Build graph",
  forecast: "First forecast",
};

/**
 * `cached` is a step no recorded build accounts for: the city was already on disk when the screen
 * opened and the API holds no record of the build that wrote it. It is not `done` - there is no
 * time to report - and it is not `waiting`.
 *
 * `loaded` is a step that ran and read every one of its outputs from the city folder rather than
 * computing them: "Loaded from disk", which is what a rebuild of a built city really does.
 *
 * `skipped` is a step the build did not need, such as a first forecast with no design storm.
 */
export type OnboardingStepStatus =
  "waiting" | "running" | "done" | "loaded" | "cached" | "skipped" | "failed";

export const STEP_STATUS_LABELS: Record<OnboardingStepStatus, string> = {
  waiting: "Waiting",
  running: "Running",
  done: "Done",
  loaded: "Loaded from disk",
  cached: "Already built",
  skipped: "Skipped",
  failed: "Failed",
};

/** One of the first forecast's stages, printed under its row while it runs and after. */
export interface OnboardingStage {
  /** "Sky", "Twin", "Pulse", "Flash", "products". */
  label: string;
  status: "waiting" | "running" | "done" | "failed" | "skipped";
  ms: number | null;
}

export interface OnboardingStepState {
  id: OnboardingStepId;
  /** 0 to 100. */
  progress: number;
  /**
   * Elapsed seconds on this step. Used when `elapsedMs` is absent, and printed only when it is
   * above zero for a finished step: "Done 0 s" would be a time nobody measured.
   */
  elapsedS: number;
  /** The step's own milliseconds, from the pipeline. Preferred over `elapsedS` when present. */
  elapsedMs?: number | null;
  status: OnboardingStepStatus;
  /** One line of detail from the pipeline, e.g. "34,410 roads, 72,573 buildings from OSM". */
  detail?: string | null;
  /** The first forecast's stages, with their milliseconds as they arrive. */
  stages?: OnboardingStage[];
}

/** Every step waiting at zero: the wizard before it starts. */
export const IDLE_ONBOARDING_STEPS: OnboardingStepState[] = ONBOARDING_STEP_IDS.map((id) => ({
  id,
  progress: 0,
  elapsedS: 0,
  status: "waiting",
}));

export interface OnboardingStepsProps {
  steps: OnboardingStepState[];
  className?: string;
  /**
   * Classes for each step's detail line. The wizard hides it on a short screen, where a detail line
   * per row would push the pipeline log below the fold; the line stays the row's tooltip.
   */
  detailClassName?: string;
}

const STATUS_ICONS = {
  waiting: CircleDashed,
  running: Loader,
  done: Check,
  loaded: HardDrive,
  cached: History,
  skipped: Minus,
  failed: TriangleAlert,
} as const satisfies Record<OnboardingStepStatus, unknown>;

/** The icon's colour per status, one class string each rather than overrides. */
const STATUS_TONES: Record<OnboardingStepStatus, string> = {
  waiting: "text-text-3",
  running: "text-text",
  done: "text-tide",
  loaded: "text-text-2",
  cached: "text-text-2",
  skipped: "text-text-3",
  failed: "text-status-degraded",
};

function stepTime(step: OnboardingStepState): string | null {
  if (step.elapsedMs !== undefined && step.elapsedMs !== null) return formatMs(step.elapsedMs);
  if (step.status === "running") return formatSeconds(step.elapsedS);
  return step.elapsedS > 0 ? formatSeconds(step.elapsedS) : null;
}

/**
 * What the right-hand side of a row says: "Done 1.4 s", "Running 12 s", "Loaded from disk",
 * "Waiting". Sentence case, no separators (SPEC.md 6.8), and a time only where one was measured.
 */
export function stepStatusText(step: OnboardingStepState): string {
  const label = STEP_STATUS_LABELS[step.status];
  if (step.status === "waiting" || step.status === "cached" || step.status === "loaded") {
    return label;
  }
  if (step.status === "skipped") return label;
  const time = stepTime(step);
  return time ? `${label} ${time}` : label;
}

/** "Sky 135 ms, Twin running 12.1 s, products 2.5 s". Stages not yet started are left out. */
export function stagesText(stages: readonly OnboardingStage[]): string {
  return stages
    .filter((stage) => stage.status !== "waiting" && stage.status !== "skipped")
    .map((stage) => {
      const time = stage.ms !== null ? formatMs(stage.ms) : null;
      if (stage.status === "running") return `${stage.label} running${time ? ` ${time}` : ""}`;
      if (stage.status === "failed") return `${stage.label} failed`;
      return time ? `${stage.label} ${time}` : stage.label;
    })
    .join(", ");
}

/**
 * The wizard's step list: a 20 px status icon, the step, its status and time, and a 2 px
 * progress underline per row. Driven by props so the wizard feeds it from the job; nothing here
 * is scripted. Compact on purpose: at 1366 x 768 the six rows, the pipeline log and the header
 * share one column that does not scroll.
 */
export function OnboardingSteps({ steps, className, detailClassName }: OnboardingStepsProps) {
  return (
    <ol className={cn("space-y-0.5", className)} aria-label="Onboarding steps">
      {steps.map((step, index) => {
        const Icon = STATUS_ICONS[step.status];
        const label = ONBOARDING_STEP_LABELS[step.id];
        const progress = Math.min(100, Math.max(0, Math.round(step.progress)));
        const stages = step.stages ? stagesText(step.stages) : "";
        const detail = step.detail ?? null;
        return (
          <li
            key={step.id}
            className={cn(
              "rounded-control relative px-2 pt-2 pb-2.5",
              step.status === "running" ? "bg-well" : null,
            )}
            aria-current={step.status === "running" ? "step" : undefined}
            title={detail ?? undefined}
          >
            <div className="flex items-center gap-2.5">
              <Icon
                aria-hidden="true"
                size={20}
                strokeWidth={1.75}
                className={cn("shrink-0", STATUS_TONES[step.status])}
              />
              <p className="type-small text-text min-w-0 flex-1 truncate font-medium">
                <span className="num text-text-3">{index + 1}.</span> {label}
              </p>
              <p className="num type-micro text-text-2 shrink-0">{stepStatusText(step)}</p>
            </div>
            {detail ? (
              <p className={cn("type-micro text-text-3 truncate pl-[30px]", detailClassName)}>
                {detail}
              </p>
            ) : null}
            {stages ? <p className="num type-micro text-text-2 pl-[30px]">{stages}</p> : null}
            {/* The 2 px underline is the step's progress. A native meter would be announced as a
                second control per row; the row's own text already says where it is. */}
            <div
              role="progressbar"
              aria-label={`${label} progress`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress}
              className="bg-line absolute inset-x-2 bottom-0 h-0.5 overflow-hidden rounded-full"
            >
              <div
                className={cn(
                  "h-full rounded-full",
                  step.status === "failed"
                    ? "bg-status-degraded"
                    : step.status === "running"
                      ? "bg-tide"
                      : "bg-tide/35",
                )}
                style={{ width: `${progress}%` }}
              />
            </div>
          </li>
        );
      })}
    </ol>
  );
}

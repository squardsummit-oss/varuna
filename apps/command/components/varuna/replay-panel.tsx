"use client";

import { useState } from "react";
import { Pause, Play, SkipBack, SkipForward, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { CycleLogState } from "@/components/varuna/cycle-log";
import { EmptyState } from "@/components/varuna/empty-state";
import { Panel } from "@/components/varuna/panel";
import { RadarPreview } from "@/components/varuna/radar-preview";
import { Skeleton } from "@/components/varuna/skeleton";
import { useReplayBundles, useReplayControls } from "@/lib/api";
import { useReplayCycleLog } from "@/lib/api/replay";
import { bundleWindowLabel } from "@/lib/format";
import {
  CYCLE_STEP_MIN,
  REPLAY_SPEEDS,
  isReplaySpeed,
  minutesFromStart,
  stepCycle,
  useReplayStore,
  type ReplayMode,
} from "@/lib/stores/replay";
import { addMinutesIso, formatIstDate, formatIstTime } from "@/lib/stores/time";
import { useUiStore } from "@/lib/stores/ui";

export interface ReplayPanelProps {
  /**
   * `rail` is the console's panel: the bundle, the clock, the cycle log and the storm, with a
   * close button. `page` is the clock alone, for `/replay`, which shows the bundle cards, the
   * cycle log and the storm in panels of their own - drawing them here too put the cycle log and
   * the radar loop on that page twice.
   */
  variant?: "rail" | "page";
}

/**
 * Replay control (SPEC.md sections 7.2 and 7.8): the clock, play and pause, cycle-by-cycle
 * seek, a scrub across the whole window, the speed and baked/live mode. Every control drives the
 * API's clock, and the store follows the `replay.clock` events it publishes, so the console, this
 * panel and every other open tab show the same instant.
 */
export function ReplayPanel({ variant = "rail" }: ReplayPanelProps) {
  const bundleId = useReplayStore((s) => s.bundleId);
  const simTime = useReplayStore((s) => s.simTime);
  const playing = useReplayStore((s) => s.playing);
  const speed = useReplayStore((s) => s.speed);
  const mode = useReplayStore((s) => s.mode);
  const note = useReplayStore((s) => s.note);
  const t0 = useReplayStore((s) => s.t0);
  const t1 = useReplayStore((s) => s.t1);
  const setReplayPanelOpen = useUiStore((s) => s.setReplayPanelOpen);

  const controls = useReplayControls();
  const bundles = useReplayBundles();
  const bundle = bundles.data?.find((row) => row.id === bundleId);
  const rail = variant === "rail";

  return (
    <Panel
      title={rail ? "Replay" : "Clock"}
      actions={
        rail ? (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Close the replay panel"
            onClick={() => setReplayPanelOpen(false)}
          >
            <X aria-hidden="true" />
          </Button>
        ) : undefined
      }
    >
      <div className="space-y-4">
        {rail ? (
          <section
            className="border-line bg-well rounded-[var(--radius-control)] border p-3"
            aria-label="Selected bundle"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="num type-small text-text font-medium">{bundleId}</span>
              <span className="border-line bg-deep type-micro text-text-2 inline-flex h-6 items-center rounded-full border px-2.5">
                {bundle?.label ?? "Reconstructed replay"}
              </span>
            </div>
            <p className="type-small text-text-2 mt-1">
              {bundleWindowLabel(bundle?.t0 ?? t0, bundle?.t1 ?? t1)}
            </p>
          </section>
        ) : null}

        {controls.apiReady ? null : controls.loading ? (
          <p role="status" className="type-micro text-text-2">
            Reaching the replay clock.
          </p>
        ) : (
          <div role="status" className="flex items-start justify-between gap-2">
            <p className="type-micro text-text-2">
              {controls.error?.message ?? "The replay clock did not answer."}
            </p>
            <Button variant="outline" size="sm" onClick={controls.retry}>
              Try again
            </Button>
          </div>
        )}

        <ClockControls
          simTime={simTime}
          playing={playing}
          speed={speed}
          t0={t0}
          t1={t1}
          controls={controls}
        />

        {note ? (
          <p role="status" className="type-micro text-text-2">
            {note}
          </p>
        ) : null}

        <section aria-label="Run mode" className="flex items-center justify-between gap-2">
          <span className="type-small text-text-2">Runs</span>
          <ToggleGroup
            aria-label="Baked or live runs"
            variant="outline"
            size="sm"
            spacing={0}
            value={[mode]}
            onValueChange={(value) => {
              const next = (value as string[])[0];
              if (next === "baked" || next === "live") controls.setMode(next as ReplayMode);
            }}
          >
            <ToggleGroupItem value="baked">Baked</ToggleGroupItem>
            <Tooltip>
              <TooltipTrigger render={<span className="inline-flex" />}>
                <ToggleGroupItem value="live" disabled aria-disabled="true">
                  Live
                </ToggleGroupItem>
              </TooltipTrigger>
              {/* Section 17: a control that is not wired says so, with its plan. */}
              <TooltipContent>
                Coming in pilot. One live cycle runs from Compute live.
              </TooltipContent>
            </Tooltip>
          </ToggleGroup>
        </section>

        {rail ? <RailExtras bundleId={bundleId} /> : null}
      </div>
    </Panel>
  );
}

interface ClockControlsProps {
  simTime: string;
  playing: boolean;
  speed: number;
  t0: string;
  t1: string;
  controls: ReturnType<typeof useReplayControls>;
}

/** The clock readout, the transport and the window scrub. */
function ClockControls({ simTime, playing, speed, t0, t1, controls }: ClockControlsProps) {
  const windowMin = Math.max(0, Math.round(minutesFromStart(t1, t0)));
  const atMin = Math.round(minutesFromStart(simTime, t0));
  // The handle follows the pointer while it is held, and the clock is asked once, on release:
  // a POST per pixel of drag would announce dozens of instants to every open tab.
  const [draft, setDraft] = useState<number | null>(null);
  const shown = draft ?? Math.min(windowMin, Math.max(0, atMin));
  const shownIso = draft === null ? simTime : addMinutesIso(t0, draft);

  return (
    <section aria-label="Replay clock" className="space-y-3">
      <div className="flex items-baseline gap-2">
        <span className="num type-h2 font-display text-text">{formatIstTime(shownIso)}</span>
        <span className="num type-small text-text-2">{formatIstDate(shownIso)} IST</span>
      </div>

      {windowMin > 0 ? (
        <div className="space-y-1">
          <Slider
            aria-label="Seek the replay, minutes from the start of the window"
            min={0}
            max={windowMin}
            step={CYCLE_STEP_MIN}
            value={[shown]}
            onValueChange={(value) => {
              const next = Array.isArray(value) ? value[0] : value;
              if (typeof next === "number") setDraft(next);
            }}
            onValueCommitted={(value) => {
              const next = Array.isArray(value) ? value[0] : value;
              setDraft(null);
              if (typeof next === "number") controls.seek(addMinutesIso(t0, next));
            }}
          />
          <div className="num type-micro text-text-3 flex justify-between">
            <span>{formatIstTime(t0)}</span>
            <span>{formatIstTime(t1)}</span>
          </div>
        </div>
      ) : null}

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="icon-sm"
          aria-label="Previous cycle"
          onClick={() => controls.seek(stepCycle(simTime, t0, -1))}
        >
          <SkipBack aria-hidden="true" />
        </Button>
        <Button
          variant={playing ? "secondary" : "default"}
          size="icon-sm"
          aria-label={playing ? "Pause the replay" : "Play the replay"}
          aria-pressed={playing}
          onClick={() => controls.toggle()}
        >
          {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
        </Button>
        <Button
          variant="outline"
          size="icon-sm"
          aria-label="Next cycle"
          onClick={() => controls.seek(stepCycle(simTime, t0, 1))}
        >
          <SkipForward aria-hidden="true" />
        </Button>
        <ToggleGroup
          aria-label="Replay speed"
          variant="outline"
          size="sm"
          spacing={0}
          value={[String(speed)]}
          onValueChange={(value) => {
            const next = Number((value as string[])[0]);
            if (isReplaySpeed(next)) controls.setSpeed(next);
          }}
          className="ml-auto"
        >
          {REPLAY_SPEEDS.map((s) => (
            <ToggleGroupItem key={s} value={String(s)} aria-label={`${s} times speed`}>
              <span className="num">{s}×</span>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
    </section>
  );
}

/** The console rail's cycle log and storm summary; `/replay` gives both panels of their own. */
function RailExtras({ bundleId }: { bundleId: string }) {
  const bundles = useReplayBundles();
  const bundle = bundles.data?.find((row) => row.id === bundleId);
  const cycles = useReplayCycleLog(bundleId);

  return (
    <>
      <section aria-label="Cycle log" className="space-y-2">
        <h3 className="type-small text-text font-medium">Cycle log</h3>
        <CycleLogState bundleId={bundleId} log={cycles} />
      </section>

      <section aria-label="Storm summary" className="space-y-2">
        <h3 className="type-small text-text font-medium">Storm summary</h3>
        {bundle ? (
          <>
            {/* The rail is 360 px wide, so the square cube is capped rather than left to fill it. */}
            <RadarPreview
              bundleId={bundle.id}
              built={bundle.built}
              missingMembers={bundle.missing_members}
              compact
              className="max-w-60"
            />
            <p className="num type-micro text-text-3">
              Seed {bundle.seed}. {bundle.baked_cycles} of {bundle.total_cycles} cycles baked.
            </p>
            {bundle.synthetic_notes.length > 0 ? (
              <details>
                <summary className="type-micro text-text-2 hover:text-text focus-visible:ring-tide rounded-control cursor-pointer focus-visible:ring-2 focus-visible:outline-none">
                  Details
                </summary>
                <ul className="mt-1.5 space-y-1">
                  {bundle.synthetic_notes.map((line) => (
                    <li key={line} className="type-micro text-text-3">
                      {line}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </>
        ) : bundles.isPending ? (
          <Skeleton className="aspect-square max-w-60" />
        ) : (
          <EmptyState
            title={bundles.isError ? "The storm did not load" : "No storm yet"}
            description={
              bundles.isError
                ? bundles.error.message
                : `Run make bundle BUNDLE=${bundleId} to generate it.`
            }
          />
        )}
      </section>
    </>
  );
}

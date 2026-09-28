"use client";

import { Copy } from "lucide-react";
import { toast } from "sonner";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Skeleton } from "@/components/varuna/skeleton";
import { useIsClient } from "@/lib/hooks/use-is-client";
import { cn } from "@/lib/utils";
import { formatMs, shortenRunId } from "@/lib/format";
import { totalStageMs, useRunStore } from "@/lib/stores/run";

export interface RunStampProps {
  className?: string;
}

/**
 * "run MUM-20190702T0640-sky1.0-twin1.0-flash0.3 · baked · 3.9 s" with click-to-copy
 * (SPEC.md section 7.2). Empty state: "No run".
 */
export function RunStamp({ className }: RunStampProps) {
  const run = useRunStore((s) => s.currentRun);
  const storeStatus = useRunStore((s) => s.status);
  // Before hydration the registry has not been asked; the store's initial "none" is no answer.
  const isClient = useIsClient();
  const status = !isClient && storeStatus === "none" ? "loading" : storeStatus;

  if (!run && status === "loading") {
    // A shimmer while the registry answers: "No run" before it has is a guess.
    return (
      <span
        className={cn("inline-flex h-7 w-56 items-center", className)}
        data-slot="run-stamp"
        data-state="loading"
        aria-busy="true"
      >
        <span className="sr-only">Loading the run</span>
        <Skeleton className="h-3 w-full" />
      </span>
    );
  }

  if (!run) {
    return (
      <span className={cn("text-small text-text-3", className)} data-slot="run-stamp">
        No run
      </span>
    );
  }

  const ms = totalStageMs(run);
  const chipClass =
    run.replay_mode === "baked"
      ? "border-status-baked/40 bg-status-baked/10 text-status-baked"
      : "border-status-live/40 bg-status-live/10 text-status-live";

  const copy = async () => {
    try {
      if (!navigator.clipboard) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(run.run_id);
      toast("Run id copied");
    } catch {
      toast("Copy failed. Select the run id in the replay panel and copy it by hand.");
    }
  };

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            type="button"
            aria-label="Copy run id"
            data-slot="run-stamp"
            onClick={() => void copy()}
            className={cn(
              "group rounded-control border-line bg-well/40 inline-flex h-7 min-w-0 items-center gap-2 border px-2.5",
              "text-small text-text-2 hover:bg-well hover:text-text",
              className,
            )}
          />
        }
      >
        <span className="text-text-3">run</span>
        <span className="num text-small text-text truncate font-mono">
          {shortenRunId(run.run_id)}
        </span>
        <span
          className={cn(
            "rounded-chip text-micro inline-flex h-5 items-center border px-1.5 font-medium",
            chipClass,
          )}
        >
          {run.replay_mode}
        </span>
        {ms !== null ? <span className="num text-text-2">{formatMs(ms)}</span> : null}
        <Copy
          aria-hidden="true"
          className="text-text-3 group-hover:text-text-2 size-4"
          strokeWidth={1.75}
        />
      </TooltipTrigger>
      <TooltipContent>
        <span className="font-mono">{run.run_id}</span>
        <span className="text-text-2">Click to copy</span>
      </TooltipContent>
    </Tooltip>
  );
}

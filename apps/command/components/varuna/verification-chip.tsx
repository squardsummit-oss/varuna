"use client";

import { useEffect, useState } from "react";
import { BadgeCheck } from "lucide-react";

import { Skeleton } from "@/components/varuna/skeleton";
import {
  DEFAULT_EVENT,
  INTERIM_AFTER_MS,
  watchVerificationHeadline,
  type VerificationHeadline,
} from "@/lib/api/verification";
import { cn } from "@/lib/utils";
import { formatScore } from "@/lib/format";

export interface VerificationChipProps {
  /** Critical success index for the current event, 0-1; null or undefined when not scored. */
  csi?: number | null;
  /** The depth the CSI was scored at, in cm. Printed because the pins record waterlogging, not a
   * depth, so a CSI without its threshold is not a claim anyone can check. */
  thresholdCm?: number | null;
  /** Replaces "this event", e.g. "2 Jul 2019". */
  eventLabel?: string;
  /** The score came from the copy committed with the build, not from the API just now. */
  fromCommittedCopy?: boolean;
  /** Why the committed copy is showing; the chip's title and screen-reader text. */
  provenanceNote?: string;
  /** Still asking for a score: a shimmer, never "Not scored yet". */
  loading?: boolean;
  /** What went wrong while asking; shown instead of a score. */
  error?: string | null;
  /** Why there is no score, when the scorer answered that there is none. */
  unscoredReason?: string | null;
  className?: string;
}

const CHIP =
  "inline-flex h-7 items-center gap-1.5 rounded-chip border border-line px-2.5 text-small";

/** "CSI 0.24 at 15 cm on this event" in the top bar; "Not scored yet" when the scorer has none. */
export function VerificationChip({
  csi,
  thresholdCm,
  eventLabel,
  fromCommittedCopy = false,
  provenanceNote,
  loading = false,
  error = null,
  unscoredReason = null,
  className,
}: VerificationChipProps) {
  if (loading) {
    return (
      <span
        data-slot="verification-chip"
        data-state="loading"
        aria-busy="true"
        className={cn(CHIP, "bg-well/40 w-44", className)}
      >
        <span className="sr-only">Loading the verification score</span>
        <Skeleton className="h-3 w-full" />
      </span>
    );
  }

  if (error) {
    return (
      <span
        data-slot="verification-chip"
        data-state="error"
        title={error}
        className={cn(CHIP, "bg-well/40 text-text-2", className)}
      >
        <BadgeCheck aria-hidden="true" className="text-text-3 size-4 shrink-0" strokeWidth={1.75} />
        <span>Score unavailable</span>
        <span className="sr-only">. {error}</span>
      </span>
    );
  }

  const scored = typeof csi === "number" && Number.isFinite(csi);
  const where = eventLabel ?? "this event";
  const provenance = fromCommittedCopy
    ? (provenanceNote ?? "Showing the scores committed with this build.")
    : undefined;

  return (
    <span
      data-slot="verification-chip"
      data-state={scored ? "scored" : "unscored"}
      data-source={scored ? (fromCommittedCopy ? "committed" : "api") : undefined}
      title={scored ? provenance : (unscoredReason ?? undefined)}
      className={cn(
        CHIP,
        scored ? "bg-tide-soft/60 text-text" : "bg-well/40 text-text-3",
        className,
      )}
    >
      <BadgeCheck
        aria-hidden="true"
        className={cn("size-4 shrink-0", scored ? "text-tide" : "text-text-3")}
        strokeWidth={1.75}
      />
      {scored ? (
        <span className="whitespace-nowrap">
          CSI <span className="num font-medium">{formatScore(csi)}</span>
          {typeof thresholdCm === "number" && Number.isFinite(thresholdCm) ? (
            <>
              {" "}
              at <span className="num">{thresholdCm.toFixed(0)} cm</span>
            </>
          ) : null}{" "}
          on {where}
          {provenance ? <span className="sr-only">. {provenance}</span> : null}
        </span>
      ) : (
        <span>Not scored yet</span>
      )}
    </span>
  );
}

type HeadlineState = { kind: "loading" } | VerificationHeadline;

/**
 * The headline score for `event`, asked once per tab and shared by every screen's top bar.
 *
 * The committed copy stands in after `interimAfterMs` while the API is still scoring, and the
 * served answer replaces it when it arrives.
 */
export function useVerificationHeadline(
  event: string = DEFAULT_EVENT,
  interimAfterMs: number = INTERIM_AFTER_MS,
): HeadlineState {
  // Keyed by event, so a different event reads as loading until its own answer arrives.
  const [answer, setAnswer] = useState<{ event: string; headline: VerificationHeadline } | null>(
    null,
  );
  useEffect(
    () =>
      watchVerificationHeadline(
        event,
        (headline) => setAnswer({ event, headline }),
        interimAfterMs,
      ),
    [event, interimAfterMs],
  );
  return answer && answer.event === event ? answer.headline : { kind: "loading" };
}

export interface ServedVerificationChipProps {
  event?: string;
  /** How long to shimmer before showing the committed copy while the API scores. */
  interimAfterMs?: number;
  className?: string;
}

/**
 * The top bar's chip, reading `GET /v1/verification` (SPEC.md 7.2) and falling back to the
 * committed `public/verification.json` when the API does not answer. The number is always the
 * scorer's own headline, at the threshold the scorer names.
 */
export function ServedVerificationChip({
  event = DEFAULT_EVENT,
  interimAfterMs,
  className,
}: ServedVerificationChipProps) {
  const state = useVerificationHeadline(event, interimAfterMs);
  switch (state.kind) {
    case "loading":
      return <VerificationChip loading className={className} />;
    case "error":
      return <VerificationChip error={state.message} className={className} />;
    case "unscored":
      return <VerificationChip unscoredReason={state.reason} className={className} />;
    case "scored":
      return (
        <VerificationChip
          csi={state.csi}
          thresholdCm={state.thresholdCm}
          fromCommittedCopy={state.source === "committed"}
          provenanceNote={state.note}
          className={className}
        />
      );
  }
}

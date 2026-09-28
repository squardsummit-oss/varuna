"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { WhatIfValues } from "@/components/varuna/whatif-controls";
import { runPhysicsCheck, type PhysicsCheckResult } from "@/lib/api/whatif";

export interface PhysicsCheckState {
  result: PhysicsCheckResult | null;
  error: string | null;
  running: boolean;
  /** Re-run the scenario on the Twin (`POST /v1/whatif/physics-check`). */
  check: (values: WhatIfValues) => void;
}

/**
 * The physics check for one cycle, shared by the what-if lab and the console's drawer so the two
 * cannot disagree about what the button does.
 *
 * A different cycle is a different question, so the answer is dropped when `runId` changes rather
 * than left on screen beside a forecast it was not computed from. A check still in flight when
 * the cycle changes or the panel unmounts is aborted: it costs two Twin runs on the API.
 */
export function usePhysicsCheck(runId: string | null | undefined): PhysicsCheckState {
  const [result, setResult] = useState<PhysicsCheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const inFlight = useRef<AbortController | null>(null);

  const [answeredFor, setAnsweredFor] = useState(runId);
  if (answeredFor !== runId) {
    setAnsweredFor(runId);
    setResult(null);
    setError(null);
  }

  useEffect(
    () => () => {
      inFlight.current?.abort();
    },
    [runId],
  );

  const check = useCallback(
    (values: WhatIfValues) => {
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;
      setRunning(true);
      setError(null);
      runPhysicsCheck(
        {
          rainScale: values.rainScale,
          tideOffsetM: values.tideOffsetM,
          cleanedSegments: values.cleanedSegments,
          pumpPlan: values.pumpPlan,
          cleanTop: values.cleanTop14,
          runId: runId ?? undefined,
        },
        controller.signal,
      )
        .then((answer) => {
          if (!controller.signal.aborted) setResult(answer);
        })
        .catch((failure: unknown) => {
          if (controller.signal.aborted) return;
          // The API's own words: a refused tide offset explains itself (SPEC.md 6.8).
          setError(failure instanceof Error ? failure.message : String(failure));
          setResult(null);
        })
        .finally(() => {
          if (inFlight.current === controller) {
            inFlight.current = null;
            setRunning(false);
          }
        });
    },
    [runId],
  );

  return { result, error, running, check };
}

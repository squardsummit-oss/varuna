"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { WhatIfValues } from "@/components/varuna/whatif-controls";
import { errorMessage, isApiError } from "@/lib/api/client";
import {
  cancelTwinScenario,
  getTwinOffer,
  getTwinScenario,
  runWhatIf,
  startTwinScenario,
  twinCannotAnswer,
  twinProgressLine,
  type TwinOffer,
  type TwinScenarioJob,
  type TwinScenarioResult,
  type TwinStepMark,
  type WhatIfResult,
} from "@/lib/api/whatif";

/** How often a running job is polled. One output step of the Twin takes 1-2 s on Mumbai. */
export const TWIN_POLL_MS = 750;

/**
 * How long a new question waits for the one it replaced to stop. The server runs one full-city
 * scenario at a time and honours a cancel at the next stage or output step, which during the
 * rain re-make can be several seconds away.
 */
export const TWIN_REPLACE_WAIT_MS = 30_000;

/** The job is already working on exactly this question (the server sorts the segment ids). */
export function sameTwinScenario(job: TwinScenarioJob, values: WhatIfValues): boolean {
  const asked = [...values.cleanedSegments].sort().join(",");
  return (
    Math.abs(job.scenario.rain_scale - values.rainScale) < 1e-9 &&
    Math.abs(job.scenario.tide_offset_m - values.tideOffsetM) < 1e-9 &&
    [...job.scenario.cleaned_segments].sort().join(",") === asked
  );
}

/**
 * Start a scenario, waiting out a `whatif_busy` while the question it replaced stops. Anything
 * else the server refuses with is thrown at once, in its own words.
 */
async function startReplacing(
  values: WhatIfValues,
  runId: string | null | undefined,
  controller: AbortController,
): Promise<TwinScenarioJob> {
  const deadline = Date.now() + TWIN_REPLACE_WAIT_MS;
  for (;;) {
    try {
      return await startTwinScenario(
        {
          rainScale: values.rainScale,
          tideOffsetM: values.tideOffsetM,
          cleanedSegments: values.cleanedSegments,
          // Not run on the Twin; sent so the job names them rather than dropping them unsaid.
          pumpPlan: values.pumpPlan,
          cleanTop: values.cleanTop14,
          runId: runId ?? undefined,
        },
        controller.signal,
      );
    } catch (failure) {
      const busy = isApiError(failure) && failure.code === "whatif_busy";
      if (!busy || Date.now() >= deadline || controller.signal.aborted) throw failure;
      await new Promise((resolve) => window.setTimeout(resolve, TWIN_POLL_MS));
      if (controller.signal.aborted) throw failure;
    }
  }
}

export interface TwinScenarioState {
  /** The job as the API last described it: stage, step `k` of `n`, expected and elapsed ms. */
  job: TwinScenarioJob | null;
  /** The finished answer, or null while running, after a failure, or before any run. */
  result: TwinScenarioResult | null;
  /** The API's own sentence when it refused, the run failed, or it was cancelled (6.8). */
  error: string | null;
  running: boolean;
  /** The job was stopped by Cancel; `error` carries the server's account of where. */
  cancelled: boolean;
  /** Share of the Twin's output steps done, 0 to 1, for the M31 bar; null before the Twin starts. */
  fraction: number | null;
  /** "Twin 17 of 36 steps, about 40 s left": the bar's label, from the job's own numbers. */
  line: string | null;
  /** Run the scenario on the full-city Twin (`POST /v1/whatif/twin`). */
  run: (values: WhatIfValues) => void;
  /** Stop the running job at its next step. Nothing it computed is stored. */
  cancel: () => void;
  /** Stop following any job and forget it, e.g. when the next question has no tide. */
  reset: () => void;
}

interface FollowHandlers {
  onJob: (job: TwinScenarioJob) => void;
  onError: (message: string) => void;
  onTimer: (timer: number) => void;
}

/** Show a job, and while it runs, poll it again after {@link TWIN_POLL_MS}. */
function followJob(
  current: TwinScenarioJob,
  controller: AbortController,
  handlers: FollowHandlers,
): void {
  handlers.onJob(current);
  if (current.state === "failed" || current.state === "cancelled") {
    handlers.onError(current.error?.message ?? "The Twin scenario stopped before it finished.");
  }
  if (current.state !== "running" || controller.signal.aborted) return;
  const timer = window.setTimeout(() => {
    getTwinScenario(current.job_id, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) followJob(next, controller, handlers);
      })
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return;
        handlers.onError(errorMessage(failure, "The what-if job could not be read. Try again."));
      });
  }, TWIN_POLL_MS);
  handlers.onTimer(timer);
}

/**
 * A full-city Twin what-if for one cycle: start, poll to completion, cancel.
 *
 * A different cycle is a different question, so the answer is dropped when `runId` changes. The
 * poll stops when the panel unmounts or the cycle changes, but the job on the server does not:
 * it finishes and lands in the scenario cache, so asking again answers at once. Cancelling is an
 * explicit act (the Cancel button), because the run holds the machine for about a minute.
 */
export function useTwinScenario(runId: string | null | undefined): TwinScenarioState {
  const [job, setJob] = useState<TwinScenarioJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The first poll that saw the Twin stage, so the countdown is this machine's step rate rather
  // than the bake's; kept in state so the line is computed in render from values React knows.
  const [mark, setMark] = useState<TwinStepMark | null>(null);
  // Between "Run what-if" and the server's first answer: what the bar says while it waits.
  const [starting, setStarting] = useState<string | null>(null);
  const poll = useRef<{ controller: AbortController; timer: number | null } | null>(null);

  const stop = useCallback(() => {
    if (poll.current?.timer != null) window.clearTimeout(poll.current.timer);
    poll.current?.controller.abort();
    poll.current = null;
  }, []);

  const [answeredFor, setAnsweredFor] = useState(runId);
  if (answeredFor !== runId) {
    setAnsweredFor(runId);
    setJob(null);
    setError(null);
    setMark(null);
    setStarting(null);
  }

  useEffect(() => stop, [runId, stop]);

  const follow = useCallback((current: TwinScenarioJob, controller: AbortController) => {
    followJob(current, controller, {
      onJob: (next) => {
        setStarting(null);
        setJob(next);
        // The comparison run and the scenario count on one series of steps, so the rate read
        // in either holds for the rest.
        if (next.stage === "twin" || next.stage === "baseline") {
          setMark((held) =>
            held === null || held.step > next.step
              ? { elapsedMs: next.elapsed_ms, step: next.step }
              : held,
          );
        }
      },
      onError: setError,
      onTimer: (timer) => {
        if (poll.current?.controller === controller) poll.current.timer = timer;
      },
    });
  }, []);

  const reset = useCallback(() => {
    stop();
    setJob(null);
    setError(null);
    setMark(null);
    setStarting(null);
  }, [stop]);

  // The job this screen is following, read by `run` without making `run` change on every poll.
  const following = useRef<TwinScenarioJob | null>(null);
  const cancelOnStart = useRef(false);
  useEffect(() => {
    following.current = job;
  }, [job]);

  const run = useCallback(
    (values: WhatIfValues) => {
      // A new tide question replaces the one still running here: this screen asked it, and the
      // server runs one full-city scenario at a time, so leaving it would refuse the new one as
      // busy for a minute. The same question keeps its job - the server hands it back.
      const previous = following.current;
      const replaced =
        previous?.state === "running" && !sameTwinScenario(previous, values) ? previous : null;
      reset();
      cancelOnStart.current = false;
      setStarting(
        replaced ? "Stopping the last tide question, then starting this one" : "Starting the Twin",
      );
      const controller = new AbortController();
      poll.current = { controller, timer: null };
      const stopped = replaced
        ? cancelTwinScenario(replaced.job_id).catch(() => undefined)
        : Promise.resolve(undefined);
      stopped
        .then(() => startReplacing(values, runId, controller))
        .then((started) => {
          if (controller.signal.aborted) return;
          // Cancel pressed before the server had answered: stop the job it just started, and
          // follow it to its own account of where it stopped.
          if (cancelOnStart.current && started.state === "running") {
            cancelTwinScenario(started.job_id).catch(() => undefined);
          }
          follow(started, controller);
        })
        .catch((failure: unknown) => {
          if (controller.signal.aborted) return;
          setStarting(null);
          setError(errorMessage(failure, "The Twin scenario could not start. Try again."));
        });
    },
    [follow, reset, runId],
  );

  const cancel = useCallback(() => {
    if (!job) {
      if (starting !== null) cancelOnStart.current = true;
      return;
    }
    if (job.state !== "running") return;
    cancelTwinScenario(job.job_id).catch((failure: unknown) => {
      setError(errorMessage(failure, "The what-if job could not be cancelled."));
    });
  }, [job, starting]);

  const running = job ? job.state === "running" : starting !== null;
  const fraction =
    job &&
    job.n_steps > 0 &&
    (job.stage === "baseline" || job.stage === "twin" || job.stage === "sampling" || job.step > 0)
      ? Math.min(1, job.step / job.n_steps)
      : job?.state === "done"
        ? 1
        : null;

  return {
    job,
    result: job?.state === "done" ? job.result : null,
    error,
    running,
    cancelled: job?.state === "cancelled",
    fraction,
    line: job ? twinProgressLine(job, mark) : starting,
    run,
    cancel,
    reset,
  };
}

/** Which engine answered the question on screen. */
export type WhatIfEngine = "emulator" | "twin";

/**
 * What this API's full-city Twin can answer for a cycle (`GET /v1/whatif/twin`), or null while it
 * loads and on an API that predates the route. A null offer leaves every lever as it was: the
 * Twin job itself then says what it refuses.
 */
export function useTwinOffer(runId: string | null | undefined, ready = true): TwinOffer | null {
  const [held, setHeld] = useState<{ runId: string | null | undefined; offer: TwinOffer } | null>(
    null,
  );
  useEffect(() => {
    // Not until the screen knows its cycle: an offer for the API's newest run would be dropped.
    if (!ready) return;
    const controller = new AbortController();
    getTwinOffer(runId ?? undefined, controller.signal)
      .then((offer) => {
        if (!controller.signal.aborted) setHeld({ runId, offer });
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [runId, ready]);
  return held && held.runId === runId ? held.offer : null;
}

export interface WhatIfFlow {
  /** The scenario of the last "Run what-if", or null before the first. */
  asked: WhatIfValues | null;
  /**
   * Why the tide in the last question was not sent to the Twin: a cache-only API holds no answer
   * to it. Null when the Twin ran, or the question kept the run's own tide.
   */
  twinSkipped: string | null;
  /** The emulator's answer: rain, cleaning and pumps, never the tide. */
  emulator: WhatIfResult | null;
  emulatorError: string | null;
  emulatorRunning: boolean;
  /** The full-city Twin, started only when the question moves the tide. */
  twin: TwinScenarioState;
  /**
   * The engine whose answer is on screen: the Twin once it has finished the question asked, the
   * emulator until then, null before any answer.
   */
  engine: WhatIfEngine | null;
  run: (values: WhatIfValues) => void;
}

/**
 * The what-if flow the lab and the console drawer share (SPEC.md 7.7, motion M31).
 *
 * "Run what-if" always asks the emulator, which answers in under a second. When the question
 * moves the tide, the emulator's answer leaves the tide out and says so, and the same question
 * starts on the full-city Twin - the only engine with a sea level - whose answer replaces the
 * emulator's when it lands. A question with no tide stops following any Twin job; the job on the
 * server still finishes into the scenario cache.
 */
export function useWhatIf(
  runId: string | null | undefined,
  offer: TwinOffer | null = null,
): WhatIfFlow {
  const twin = useTwinScenario(runId);
  const [asked, setAsked] = useState<WhatIfValues | null>(null);
  const [twinSkipped, setTwinSkipped] = useState<string | null>(null);
  const [emulator, setEmulator] = useState<WhatIfResult | null>(null);
  const [emulatorError, setEmulatorError] = useState<string | null>(null);
  const [emulatorRunning, setEmulatorRunning] = useState(false);
  const inFlight = useRef<AbortController | null>(null);

  const [answeredFor, setAnsweredFor] = useState(runId);
  if (answeredFor !== runId) {
    setAnsweredFor(runId);
    setAsked(null);
    setEmulator(null);
    setEmulatorError(null);
    setTwinSkipped(null);
  }

  useEffect(() => () => inFlight.current?.abort(), [runId]);

  const { run: runTwin, reset: resetTwin } = twin;
  const run = useCallback(
    (values: WhatIfValues) => {
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;
      setAsked(values);
      setEmulatorError(null);
      setEmulatorRunning(true);
      // A cache-only API refuses a tide it holds no answer to, so the question is not sent and
      // the emulator's answer stands, saying the tide is left out and why.
      const skipped = values.tideOffsetM !== 0 ? twinCannotAnswer(offer, values) : null;
      setTwinSkipped(skipped);
      if (values.tideOffsetM !== 0 && skipped === null) runTwin(values);
      else resetTwin();
      runWhatIf(
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
          if (!controller.signal.aborted) setEmulator(answer);
        })
        .catch((failure: unknown) => {
          if (controller.signal.aborted) return;
          // The API's own words (SPEC.md 6.8): a refused scenario explains itself.
          setEmulatorError(errorMessage(failure, "The what-if could not run. Try again."));
          setEmulator(null);
        })
        .finally(() => {
          if (inFlight.current === controller) setEmulatorRunning(false);
        });
    },
    [offer, resetTwin, runId, runTwin],
  );

  const twinAnswers = Boolean(twin.result) && asked !== null && asked.tideOffsetM !== 0;
  const engine: WhatIfEngine | null = twinAnswers ? "twin" : emulator ? "emulator" : null;

  return { asked, twinSkipped, emulator, emulatorError, emulatorRunning, twin, engine, run };
}

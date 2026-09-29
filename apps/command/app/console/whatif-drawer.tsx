"use client";

/**
 * The console's what-if drawer (SPEC.md 7.7: "also a drawer inside the console"; the W key).
 *
 * The same levers, the same endpoints and the same flow as `/whatif`, asked about the cycle the
 * console is showing, with the answer drawn on the console's own map as the difference layer
 * (motion M13). It takes the right rail's slot, as the hotspot drawer does, so the map keeps its
 * whole width.
 *
 * A question that moves the tide shows the emulator's part at once, labelled "Emulator, tide not
 * included", and runs the whole question on the full-city Twin with the M31 bar, time left and
 * Cancel; the Twin's answer replaces the emulator's on the map and in the table when it lands.
 *
 * It accepts the lab's deep link (`?segments=a,b,c&from=<hotspot>` on the console's URL) and
 * remembers the last scenario asked in it for this browser tab, so closing and reopening the
 * drawer, or pressing W twice, does not throw away a question half set up.
 */

import { X } from "lucide-react";
import { motion } from "motion/react";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { DeltaTable } from "@/components/varuna/delta-table";
import { PhysicsCheckPanel, TwinRunProgress } from "@/components/varuna/physics-check-result";
import {
  DEFAULT_WHATIF_VALUES,
  WhatIfControls,
  type WhatIfValues,
} from "@/components/varuna/whatif-controls";
import { WhatIfDetails } from "@/components/varuna/whatif-details";
import { tideOfferNote, tideStopsFor } from "@/lib/api/whatif";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { usePhysicsCheck } from "@/lib/hooks/use-physics-check";
import { useTwinOffer, useWhatIf } from "@/lib/hooks/use-twin-scenario";
import { DUR, DUR_MS, EASE_UI, tween } from "@/lib/motion";
import { navItem } from "@/lib/nav";

import {
  EMULATOR_NO_TIDE,
  ENGINE_LABEL,
  emulatorAnswer,
  parseSegments,
  twinAnswer,
  type WhatIfAnswer,
} from "../whatif/whatif-answer";

/** Motion M13: the difference layer wipes left to right over 500 ms (SPEC.md 8). */
const WIPE_MS = DUR_MS.diffWipe;

const SCREEN = navItem("whatif");

/** Hotspots in the drawer's table; the lab lists them all, with the largest street changes. */
const MAX_ROWS = 8;

/** Where the drawer keeps its last scenario for this tab (sessionStorage; may be unavailable). */
export const WHATIF_DRAWER_KEY = "varuna.whatif.drawer";

export interface WhatIfDiff {
  deltaCm: ReadonlyMap<string, number>;
  progress: number;
}

export interface WhatIfDrawerProps {
  /** The cycle the question is about: the run the console is showing. */
  runId: string | null;
  /** The answer, for the map's difference layer; null clears it. */
  onDiff: (diff: WhatIfDiff | null) => void;
  onClose: () => void;
}

/** The last scenario asked in the drawer, or null. Every read is guarded: storage can throw. */
function readRemembered(): WhatIfValues | null {
  try {
    const raw = window.sessionStorage.getItem(WHATIF_DRAWER_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<WhatIfValues>;
    return {
      rainScale: Number(parsed.rainScale ?? DEFAULT_WHATIF_VALUES.rainScale),
      tideOffsetM: Number(parsed.tideOffsetM ?? DEFAULT_WHATIF_VALUES.tideOffsetM),
      cleanTop14: Boolean(parsed.cleanTop14),
      pumpPlan: Boolean(parsed.pumpPlan),
      cleanedSegments: Array.isArray(parsed.cleanedSegments)
        ? parsed.cleanedSegments.map(String)
        : [],
    };
  } catch {
    return null;
  }
}

function remember(values: WhatIfValues): void {
  try {
    window.sessionStorage.setItem(WHATIF_DRAWER_KEY, JSON.stringify(values));
  } catch {
    // Private windows and blocked storage: the drawer still works, it just forgets.
  }
}

export function WhatIfDrawer({ runId, onDiff, onClose }: WhatIfDrawerProps) {
  const reducedMotion = usePrefersReducedMotion();
  const searchParams = useSearchParams();
  // The starting scenario: a deep link's segments win over the remembered scenario, because the
  // link is the newer, explicit request; otherwise the last question asked here; otherwise none.
  const [start] = useState(() => {
    const link = parseSegments(searchParams?.get("segments") ?? null);
    const remembered = readRemembered() ?? DEFAULT_WHATIF_VALUES;
    return {
      values: link.picked.length > 0 ? { ...remembered, cleanedSegments: link.picked } : remembered,
      from: link.picked.length > 0 ? (searchParams?.get("from") ?? undefined) : undefined,
    };
  });
  // What this API's Twin can answer: any tide, or only its stored answers for this cycle.
  const offer = useTwinOffer(runId);
  const tideStops = useMemo(() => tideStopsFor(offer), [offer]);
  const flow = useWhatIf(runId, offer);
  const physics = usePhysicsCheck(runId);
  const twin = flow.twin;
  const [progress, setProgress] = useState(1);

  // The job's fields are read out first so the memo depends on exactly the values it uses,
  // which is what lets the React Compiler keep this memoization (as the lab does).
  const twinCacheLabel = twin.job?.cache?.label ?? null;
  const twinScenarioNotes = twin.job?.scenario.notes;
  const answer: WhatIfAnswer | null = useMemo(() => {
    if (flow.engine === "twin" && twin.result) {
      return twinAnswer(twin.result, {
        cacheLabel: twinCacheLabel,
        asked: flow.asked,
        scenarioNotes: twinScenarioNotes ?? [],
      });
    }
    return flow.emulator ? emulatorAnswer(flow.emulator) : null;
  }, [flow.asked, flow.engine, flow.emulator, twinCacheLabel, twinScenarioNotes, twin.result]);
  const tideAsked = (flow.asked?.tideOffsetM ?? 0) !== 0;
  const tideLeftOut = answer?.engine === "emulator" && tideAsked;

  // Motion M13 on the console's map: the diff wipes in left to right when the emulator answers.
  // The Twin's answer swaps in whole (M31), so one question is not wiped twice.
  useEffect(() => {
    if (!flow.emulator || reducedMotion) {
      const settle = requestAnimationFrame(() => setProgress(1));
      return () => cancelAnimationFrame(settle);
    }
    let frame = 0;
    const started = performance.now();
    const tick = () => {
      const t = Math.min((performance.now() - started) / WIPE_MS, 1);
      setProgress(t);
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [flow.emulator, reducedMotion]);

  useEffect(() => {
    onDiff(answer ? { deltaCm: answer.deltaCm, progress } : null);
  }, [answer, progress, onDiff]);

  // Closing the drawer takes its answer off the map with it.
  useEffect(() => () => onDiff(null), [onDiff]);

  const run = (values: WhatIfValues) => {
    remember(values);
    flow.run(values);
  };

  return (
    <motion.aside
      aria-label="What-if"
      initial={reducedMotion ? false : { x: 24, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      transition={reducedMotion ? { duration: 0 } : { duration: DUR.drawerSlide, ease: EASE_UI }}
      className="bg-deep flex h-full min-h-0 flex-col overflow-y-auto"
    >
      <header className="border-line flex items-start justify-between gap-3 border-b p-4">
        <div className="min-w-0">
          {/* The screen's name, as on the rail and the lab (ADR-0085), with its English gloss. */}
          <h2 className="type-h3 font-display text-text">{SCREEN.label}</h2>
          <p className="type-micro text-text-2">{SCREEN.gloss}</p>
          <p className="type-micro text-text-3 mt-1">
            {ENGINE_LABEL[answer?.engine ?? "emulator"]}
          </p>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close the what-if">
          <X size={16} strokeWidth={1.75} />
        </Button>
      </header>

      <section className="border-line border-b p-4">
        <WhatIfControls
          initial={start.values}
          onChange={remember}
          onRun={run}
          onPhysicsCheck={physics.running ? undefined : physics.check}
          physicsDisabledReason="Checking: the Twin is running the scenario"
          cleanedSource={start.from}
          tideStops={tideStops}
          tideNote={tideOfferNote(offer)}
        />
        {flow.emulatorRunning ? (
          <p className="type-small text-text-3 mt-3">Running the scenario on the emulator</p>
        ) : null}
        {flow.emulatorError ? (
          <p className="type-small text-text-2 mt-3">{flow.emulatorError}</p>
        ) : null}
        {flow.twinSkipped ? (
          <p className="type-small text-text-2 mt-3">{flow.twinSkipped}</p>
        ) : null}
        {tideAsked && !flow.twinSkipped ? (
          <TwinRunProgress
            className="border-line mt-4 border-t pt-4"
            line={twin.line}
            fraction={twin.fraction}
            running={twin.running}
            error={twin.error}
            cancelled={twin.cancelled}
            onCancel={twin.cancel}
          />
        ) : null}
      </section>

      {answer ? (
        <motion.section
          // M31: the Twin's answer replaces the emulator's with a 300 ms cross-fade.
          key={answer.engine}
          initial={reducedMotion || answer.engine !== "twin" ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={reducedMotion ? { duration: 0 } : tween(DUR.crossFade)}
          className="border-line border-b p-4"
        >
          <h3 className="type-small text-text font-medium">What-if ready</h3>
          <p className="type-small text-text mt-1">{answer.summary}</p>
          <p className="type-micro text-text-2 mt-1">
            {tideLeftOut ? EMULATOR_NO_TIDE : ENGINE_LABEL[answer.engine]}
          </p>
          {answer.skill ? <p className="num type-micro text-text-2">{answer.skill}</p> : null}
          {answer.tideOutcome ? (
            <p className="type-small text-text mt-1">{answer.tideOutcome}</p>
          ) : null}
          <WhatIfDetails className="mt-2">
            <p>
              Answered in{" "}
              <span className="num">{Math.round(answer.ms).toLocaleString("en-IN")}</span> ms.
            </p>
            {answer.lines.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </WhatIfDetails>
          {answer.nothingChanged ? null : (
            <div className="mt-3">
              <DeltaTable
                rows={answer.hotspots.slice(0, MAX_ROWS)}
                minutesLabel="Minutes above 30 cm"
                emptyTitle="No hotspot to compare"
                emptyDescription="None of this run's hotspots has a road segment the answer covers."
              />
            </div>
          )}
        </motion.section>
      ) : null}

      {physics.result || physics.running || physics.error ? (
        <section className="border-line border-b p-4">
          <h3 className="type-small text-text mb-2 font-medium">Physics check</h3>
          <PhysicsCheckPanel
            result={physics.result}
            running={physics.running}
            error={physics.error}
            maxRows={4}
          />
        </section>
      ) : null}
    </motion.aside>
  );
}

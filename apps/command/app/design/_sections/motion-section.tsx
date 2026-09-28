"use client";

import { motion } from "motion/react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  CycleBudgetBar,
  STAGE_IDS,
  type StageTiming,
} from "@/components/varuna/cycle-budget-bar";
import { ModeBanner } from "@/components/varuna/mode-banner";
import { Skeleton, SkeletonRows } from "@/components/varuna/skeleton";
import { M, SPRING, tween, useMotionPref, type MotionId } from "@/lib/motion";
import type { SystemMode } from "@/lib/stores/run";

import { DesignSection, Meta } from "./section";

/** The catalogue rows implemented so far; the rest arrive with their screens. */
const IMPLEMENTED: readonly MotionId[] = ["M6", "M20", "M21", "M22", "M24"];

/** One catalogue row: what it is, what triggers it, and what reduced motion shows instead. */
function MotionRow({
  id,
  children,
  reduced,
}: {
  id: MotionId;
  children: React.ReactNode;
  reduced: boolean;
}) {
  const spec = M[id];
  return (
    <div className="flex flex-col gap-3 rounded-panel border border-line bg-deep p-panel">
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="type-small num font-medium text-text">{spec.id}</span>
          <span className="type-small text-text">{spec.where}</span>
          <Meta>{`trigger: ${spec.trigger}`}</Meta>
        </div>
        <p className="type-small max-w-[72ch] text-text-2">{spec.motion}</p>
        <p className="type-micro text-text-3">
          {reduced
            ? `Reduced motion is on in this browser, so you are seeing: ${spec.reduced}.`
            : `Reduced motion would show: ${spec.reduced}.`}
        </p>
      </div>
      {children}
    </div>
  );
}

/** M6: the scrub handle springs to the new time; the layers restyle without a tween. */
function ScrubDemo({ reduced }: { reduced: boolean }) {
  const [minutes, setMinutes] = useState(0);
  const fraction = (minutes + 60) / 240;
  return (
    <div className="flex flex-col gap-2">
      <div className="relative h-6 rounded-chip border border-line bg-well">
        <motion.span
          aria-hidden="true"
          className="absolute top-1/2 size-4 -translate-y-1/2 rounded-chip bg-tide"
          animate={{ left: `calc(${(fraction * 100).toFixed(1)}% - 8px)` }}
          transition={reduced ? { duration: 0 } : SPRING}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={() => setMinutes(-60)}>
          Scrub to −60 min
        </Button>
        <Button size="sm" variant="outline" onClick={() => setMinutes(40)}>
          Scrub to +40 min
        </Button>
        <Button size="sm" variant="outline" onClick={() => setMinutes(180)}>
          Scrub to +180 min
        </Button>
        <span className="type-micro num text-text-2">{`${minutes >= 0 ? "+" : "−"}${Math.abs(minutes)} min`}</span>
      </div>
    </div>
  );
}

const MODE_CYCLE: readonly SystemMode[] = ["replay", "live", "degraded", "none"];

/** M20: the banner colour cross-fades between modes; degraded pulses once. */
function ModeDemo() {
  const [index, setIndex] = useState(0);
  const mode = MODE_CYCLE[index] ?? "replay";
  return (
    <div className="flex flex-wrap items-center gap-3">
      <ModeBanner mode={mode} label="Replay 30× · 2 Jul 2019 · 06:40 IST" />
      <Button
        size="sm"
        variant="outline"
        onClick={() => setIndex((i) => (i + 1) % MODE_CYCLE.length)}
      >
        Change mode
      </Button>
      <Meta>{`mode="${mode}"`}</Meta>
    </div>
  );
}

/** Stage times of a baked cycle, in the order the orchestrator reports them. */
const DEMO_STAGE_MS: Record<string, number> = {
  decode: 210,
  sky: 4300,
  twin: 6100,
  flash: 280,
  pulse: 1900,
  products: 640,
};

/** M21: stage segments fill as timings arrive over the WebSocket. */
function BudgetDemo() {
  const [filled, setFilled] = useState<number>(STAGE_IDS.length);
  const stages: StageTiming[] = STAGE_IDS.map((id, i) => ({
    id,
    ms: i < filled ? (DEMO_STAGE_MS[id] ?? null) : null,
  }));
  const totalMs = stages.reduce((sum, s) => sum + (s.ms ?? 0), 0);
  return (
    <div className="flex flex-col gap-2">
      <CycleBudgetBar stages={stages} totalMs={filled === 0 ? null : totalMs} />
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => setFilled((f) => (f >= STAGE_IDS.length ? 0 : f + 1))}
        >
          {filled >= STAGE_IDS.length ? "Reset the cycle" : "Report the next stage"}
        </Button>
        <Meta>{`${filled} of ${STAGE_IDS.length} stages reported`}</Meta>
      </div>
    </div>
  );
}

/** M22: skeletons shimmer while a run loads; there are no spinners in this product. */
function SkeletonDemo() {
  const [loading, setLoading] = useState(true);
  return (
    <div className="flex flex-col gap-2">
      {loading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-6 w-48" />
          <SkeletonRows rows={3} />
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          <span className="type-small font-medium text-text">Hindmata junction</span>
          <span className="type-small num text-text-2">55 cm at 08:20 (+40 min)</span>
        </div>
      )}
      <Button size="sm" variant="outline" onClick={() => setLoading((v) => !v)}>
        {loading ? "Show the loaded rail" : "Show the loading rail"}
      </Button>
    </div>
  );
}

/** M24: the public map's bottom sheet snaps between its two heights. */
function SheetDemo({ reduced }: { reduced: boolean }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="flex flex-col gap-2">
      <div className="relative h-56 overflow-hidden rounded-panel border border-line bg-ink">
        <span className="type-micro absolute top-3 left-3 text-text-3">Map behind the sheet</span>
        <motion.div
          className="absolute inset-x-0 bottom-0 rounded-panel border border-line bg-deep p-panel"
          animate={{ height: expanded ? 176 : 88 }}
          transition={reduced ? { duration: 0 } : tween(0.3)}
        >
          <span
            aria-hidden="true"
            className="mx-auto mb-3 block h-1 w-10 rounded-chip bg-line-strong"
          />
          <p className="type-small text-text">Dr Ambedkar Road: passable until 09:25</p>
          {expanded ? (
            <p className="type-small mt-2 text-text-2">
              Hindmata junction: impassable for cars from 08:20. Tilak Bridge stays open.
            </p>
          ) : null}
        </motion.div>
      </div>
      <Button size="sm" variant="outline" onClick={() => setExpanded((v) => !v)}>
        {expanded ? "Collapse the sheet" : "Expand the sheet"}
      </Button>
    </div>
  );
}

export function MotionSection() {
  const { reduced } = useMotionPref();
  const pending = (Object.keys(M) as MotionId[]).filter((id) => !IMPLEMENTED.includes(id));

  return (
    <DesignSection
      id="motion"
      title="Motion"
      description="The catalogue of the build spec, section 8. Motion shows change; nothing here is decoration, and every row has a reduced-motion branch."
    >
      <div className="flex flex-col gap-4">
        <p className="type-small max-w-[72ch] text-text-2">
          {reduced
            ? "This browser asks for reduced motion, so every demo below renders its fallback."
            : "This browser allows motion. Turn on reduce motion in the operating system and reload to check every fallback."}
        </p>

        <MotionRow id="M6" reduced={reduced}>
          <ScrubDemo reduced={reduced} />
        </MotionRow>
        <MotionRow id="M20" reduced={reduced}>
          <ModeDemo />
        </MotionRow>
        <MotionRow id="M21" reduced={reduced}>
          <BudgetDemo />
        </MotionRow>
        <MotionRow id="M22" reduced={reduced}>
          <SkeletonDemo />
        </MotionRow>
        <MotionRow id="M24" reduced={reduced}>
          <SheetDemo reduced={reduced} />
        </MotionRow>

        <div className="flex flex-col gap-2 rounded-panel border border-line bg-deep p-panel">
          <span className="type-small font-medium text-text">Still to come</span>
          <p className="type-small max-w-[72ch] text-text-2">
            These rows land with the screens they belong to, from the map layers in phase 6 onwards.
            Each already carries the fallback a reduced-motion user gets.
          </p>
          <ul className="flex flex-col gap-1">
            {pending.map((id) => (
              <li key={id} className="flex flex-wrap items-baseline gap-2">
                <span className="type-micro num font-medium text-text">{M[id].id}</span>
                <span className="type-micro text-text-2">{M[id].where}</span>
                <Meta>{`reduced motion: ${M[id].reduced}`}</Meta>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </DesignSection>
  );
}

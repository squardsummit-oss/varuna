"use client";

/**
 * The five-minute cycle (SPEC.md 7.1 item 4, motion M3).
 *
 * The pipeline as seven numbered nodes - 7.1 allows numbering because this is a real sequence -
 * with Twin and Flash side by side because they run in parallel. Beams travel from node to node,
 * hop by hop, only while the diagram is on screen and the tab is visible; under reduced motion
 * they are static arrows.
 *
 * Under each node is the stage's measured wall-clock from the newest run, beside its budget, so
 * the section shows the cycle as it is (a Twin at 58 s against 8 s) rather than as designed. The
 * timings are fetched once the section is near the viewport, not on page load, so they never
 * compete with the hero for the landing page's LCP budget (P9.2).
 */

import {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  createRef,
  type RefObject,
} from "react";
import { useInView } from "motion/react";

import {
  CYCLE_BEAMS,
  CYCLE_HOPS,
  CYCLE_NODES,
  cycleTotalMs,
  loadCycleTimings,
  nodeTiming,
  timingLabel,
  type CycleNodeId,
  type CycleTimings,
} from "@/components/landing/cycle-timings";
import { H2, LEAD, SECTION } from "@/components/landing/styles";
import { AnimatedBeam } from "@/components/ui/animated-beam";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate, formatIst, formatMs } from "@/lib/format";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { DUR } from "@/lib/motion";
import { cn } from "@/lib/utils";

/**
 * Where each node sits. Phones stack the pipeline in one column with Twin and Flash side by side;
 * from `lg` it is one row of six columns with Twin over Flash in the third.
 */
const PLACEMENT: Record<CycleNodeId, string> = {
  ingest: "col-span-2 lg:col-span-1 lg:col-start-1 lg:row-span-2",
  sky: "col-span-2 lg:col-span-1 lg:col-start-2 lg:row-span-2",
  twin: "col-span-1 lg:col-start-3 lg:row-start-1",
  flash: "col-span-1 lg:col-start-3 lg:row-start-2",
  pulse: "col-span-2 lg:col-span-1 lg:col-start-4 lg:row-span-2",
  products: "col-span-2 lg:col-span-1 lg:col-start-5 lg:row-span-2",
  outputs: "col-span-2 lg:col-span-1 lg:col-start-6 lg:row-span-2",
};

function subscribeVisibility(onChange: () => void): () => void {
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
}

/** False while the tab is hidden, so the beams stop rather than run for nobody. */
function useDocumentVisible(): boolean {
  return useSyncExternalStore(
    subscribeVisibility,
    () => document.visibilityState !== "hidden",
    () => true,
  );
}

function makeNodeRefs(): Record<CycleNodeId, RefObject<HTMLLIElement | null>> {
  return Object.fromEntries(
    CYCLE_NODES.map((node) => [node.id, createRef<HTMLLIElement>()]),
  ) as Record<CycleNodeId, RefObject<HTMLLIElement | null>>;
}

/**
 * The line under the diagram: the total against its budget, and whether the timings are live or
 * the copy committed with the page. Which run they were measured on is provenance for an expert,
 * so it sits in a collapsed "Details".
 */
function Caption({ timings }: { timings: CycleTimings | null }) {
  if (!timings) {
    return <p>No run has been timed yet. Bake a cycle or compute one live.</p>;
  }
  const total = cycleTotalMs(timings.stageMs);
  const when = timings.cycleTs
    ? `, the ${formatIst(timings.cycleTs)} IST cycle of ${formatDate(timings.cycleTs)}`
    : "";
  return (
    <>
      {timings.source === "committed" ? (
        <p>API offline: these timings were committed with this page.</p>
      ) : null}
      {total !== null ? (
        <p className="num">
          {timings.totalBudgetMs !== null
            ? `Total ${formatMs(total)} against a ${formatMs(timings.totalBudgetMs)} budget.`
            : `Total ${formatMs(total)}.`}
        </p>
      ) : null}
      <details className="mt-1">
        <summary className="text-text-2 cursor-pointer">Details</summary>
        <p className="mt-1">
          Measured on run <span className="font-mono break-all">{timings.runId}</span>
          {when}. A stage the run did not time says so instead of showing a number.
        </p>
      </details>
    </>
  );
}

export function TheCycle() {
  const reducedMotion = usePrefersReducedMotion();
  const visible = useDocumentVisible();
  const sectionRef = useRef<HTMLElement>(null);
  const diagramRef = useRef<HTMLDivElement>(null);
  const [nodeRefs] = useState(makeNodeRefs);

  // Fetch when the reader is within a screen of the section, once.
  const near = useInView(sectionRef, { once: true, margin: "0px 0px 400px 0px" });
  // Beams travel only while the diagram itself is on screen.
  const inView = useInView(diagramRef, { amount: 0.2 });

  const [result, setResult] = useState<{ done: boolean; timings: CycleTimings | null }>({
    done: false,
    timings: null,
  });

  useEffect(() => {
    if (!near) return;
    const controller = new AbortController();
    void loadCycleTimings(controller.signal).then((timings) => {
      if (!controller.signal.aborted) setResult({ done: true, timings });
    });
    return () => controller.abort();
  }, [near]);

  const { done, timings } = result;

  return (
    <section ref={sectionRef} className={SECTION} aria-labelledby="the-cycle-heading">
      <div className="mx-auto max-w-[1200px]">
        <h2 id="the-cycle-heading" className={H2}>
          Every five minutes, the whole city again
        </h2>
        <p className={LEAD}>
          Each stage&apos;s measured time on the newest run, beside its budget.
        </p>

        <div ref={diagramRef} className="relative mt-10">
          {CYCLE_BEAMS.map((beam) => (
            <AnimatedBeam
              key={`${beam.from}-${beam.to}`}
              containerRef={diagramRef}
              fromRef={nodeRefs[beam.from]}
              toRef={nodeRefs[beam.to]}
              active={inView && visible}
              reducedMotion={reducedMotion}
              duration={DUR.drawOnMax}
              delay={beam.hop * DUR.drawOnMax}
              repeatDelay={(CYCLE_HOPS - 1) * DUR.drawOnMax}
            />
          ))}
          <ol className="grid grid-cols-2 gap-x-4 gap-y-10 lg:grid-cols-6 lg:grid-rows-2 lg:gap-x-8 lg:gap-y-6">
            {CYCLE_NODES.map((node) => {
              const timing = nodeTiming(node, timings?.stageMs ?? {}, timings?.budgetMs ?? {});
              return (
                <li
                  key={node.id}
                  ref={nodeRefs[node.id]}
                  data-cycle-node={node.id}
                  className={cn(
                    "rounded-panel border-line bg-deep relative z-10 flex flex-col border p-4 lg:self-center",
                    PLACEMENT[node.id],
                  )}
                >
                  <p className="num text-micro text-text-3">Stage {node.n}</p>
                  <p className="text-h3 text-text mt-1">{node.name}</p>
                  <p className="text-small text-text-2 mt-2">{node.job}</p>
                  <div className="border-line mt-3 border-t pt-3">
                    {done ? (
                      <>
                        <p data-timing={node.id} className="num text-body text-text font-medium">
                          {timingLabel(timing)}
                        </p>
                        {timings && timing.budgetMs !== null ? (
                          <p className="num text-micro text-text-3">
                            Budget {formatMs(timing.budgetMs)}
                          </p>
                        ) : null}
                      </>
                    ) : (
                      <Skeleton aria-hidden="true" className="rounded-control bg-well h-5 w-16" />
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>

        <div className="text-small text-text-3 mt-8 max-w-[72ch]" aria-live="polite">
          {done ? (
            <Caption timings={timings} />
          ) : (
            <p>Reading the newest run&apos;s stage timings.</p>
          )}
        </div>
      </div>
    </section>
  );
}

"use client";

import { Button, buttonVariants } from "@/components/ui/button";
import { formatCm, formatCount, formatMinutes, formatMs } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The design storm, or the rain the run recorded when the storm's manifest was not to hand. */
export interface FinishStorm {
  id: string | null;
  totalMm: number | null;
  durationMin: number | null;
  peakMmH: number | null;
  /** "manifest": the bundle's design storm. "run": the AOI-mean rain the run was forced with. */
  source: "manifest" | "run" | null;
}

/** Every number the card prints, each read from the run the build made (SPEC.md rule 6). */
export interface FinishFacts {
  runId: string;
  wetStreets: number | null;
  streetsTotal: number | null;
  /** The depth a street has to reach at some step to count as wet. */
  wetThresholdCm: number | null;
  medianPeakCm: number | null;
  /**
   * The first forecast's wall time, the same number its step row prints ("First forecast, Done
   * 6 min 06 s"): the stages plus the work between them that no stage times. Null when no build
   * recorded that step, as for a run found on disk rather than made by a build the API watched.
   */
  forecastMs: number | null;
  /** The run's own `stage_ms` added up over `stages`, which is all this sum covers. */
  stagesMs: number | null;
  /** The stages in `stagesMs`, named as the step row names them: "Sky", "Twin", "products". */
  stages: string[];
  storm: FinishStorm | null;
}

export interface OnboardFinishCardProps {
  /** "Chennai". */
  cityName: string;
  /** Null until a first forecast has been read: the card is then the promise, not the result. */
  facts: FinishFacts | null;
  /** Where "Open <city> console" goes; null keeps the button disabled. */
  href: string | null;
  /** Which build these numbers came from, e.g. "Built in 65 s." One line, or none. */
  note?: string | null;
  /** Motion M19: the numbers fade in with the forecast's depth layer (0 to 1). */
  factsOpacity?: number;
  /** The lead-time scrub over the forecast's steps, once there is a forecast to scrub. */
  scrub?: React.ReactNode;
  className?: string;
}

function mm(value: number): string {
  return `${formatCount(value)} mm`;
}

/** "Sky, Twin, Pulse and products": a list in prose, the last two joined by "and". */
export function stageListText(stages: readonly string[]): string {
  if (stages.length <= 1) return stages[0] ?? "";
  return `${stages.slice(0, -1).join(", ")} and ${stages[stages.length - 1]}`;
}

/** "150 mm in 3 h, peak 449 mm/h" (manifest) or "143 mm over 2 h 35 min, peak 449 mm/h" (run). */
export function stormText(storm: FinishStorm): string | null {
  if (storm.totalMm === null) return null;
  const span =
    storm.durationMin !== null
      ? `${storm.source === "run" ? " over" : " in"} ${formatMinutes(storm.durationMin)}`
      : "";
  const peak = storm.peakMmH !== null ? `, peak ${formatCount(storm.peakMmH)} mm/h` : "";
  return `${mm(storm.totalMm)}${span}${peak}`;
}

/**
 * The wizard's finish card (SPEC.md 7.9): what the first forecast produced, said with the run's
 * own numbers, and the one button that takes the operator to that city's console.
 *
 * It floats on the wizard's map rather than sitting at the foot of the step column, because at
 * 1366 x 768 the foot of the column was 450 px below the fold and the demo never scrolls. It is
 * always on screen, holding the sentence and a disabled button until a forecast has been read, so
 * the control lights up where the eye already is rather than appearing.
 */
export function OnboardFinishCard({
  cityName,
  facts,
  href,
  note,
  factsOpacity = 1,
  scrub,
  className,
}: OnboardFinishCardProps) {
  const storm = facts?.storm ? stormText(facts.storm) : null;
  const stages = facts && facts.stages.length > 0 ? stageListText(facts.stages) : null;
  const stormLabel = facts?.storm
    ? facts.storm.source === "run"
      ? `Rain on the run${facts.storm.id ? `, ${facts.storm.id}` : ""}`
      : `Design storm${facts.storm.id ? ` ${facts.storm.id}` : ""}`
    : null;

  return (
    <section
      aria-label="First forecast"
      className={cn(
        "rounded-panel border-line bg-deep w-[360px] max-w-full space-y-3 border p-4",
        className,
      )}
    >
      <div className="space-y-1">
        <h2 className="type-small text-text font-medium">First forecast</h2>
        {facts ? (
          <p
            className="type-micro text-text-3 truncate font-mono"
            title={facts.runId}
            style={{ opacity: factsOpacity }}
          >
            {facts.runId}
          </p>
        ) : null}
      </div>

      {facts ? (
        <dl
          className="grid grid-cols-2 gap-x-4 gap-y-2"
          style={{ opacity: factsOpacity }}
          aria-label="First forecast numbers"
        >
          <div>
            <dt className="type-micro text-text-3">
              Wet streets
              {facts.wetThresholdCm !== null ? `, ${formatCm(facts.wetThresholdCm)} or more` : ""}
            </dt>
            <dd className="num type-small text-text">
              {facts.wetStreets !== null ? formatCount(facts.wetStreets) : "Not recorded"}
              {facts.wetStreets !== null && facts.streetsTotal !== null
                ? ` of ${formatCount(facts.streetsTotal)}`
                : ""}
            </dd>
          </div>
          <div>
            <dt className="type-micro text-text-3">Median street peak</dt>
            <dd className="num type-small text-text">
              {facts.medianPeakCm !== null ? formatCm(facts.medianPeakCm) : "Not recorded"}
            </dd>
          </div>
          {storm && stormLabel ? (
            <div className="col-span-2">
              <dt className="type-micro text-text-3">{stormLabel}</dt>
              <dd className="num type-small text-text">{storm}</dd>
            </div>
          ) : null}
          {/* Two real numbers, each labelled with what it measures. The wall time matches the
              first forecast's row; the stage sum is `run.json`'s own and is smaller, because work
              between stages belongs to no stage - on Chennai's recorded build, rebuilding a stale
              segment index. There they are 6 min 06 s and 5 min 30 s. */}
          <div className="col-span-2">
            <dt className="type-micro text-text-3">
              {facts.forecastMs === null && facts.stagesMs !== null && stages
                ? stages
                : "Forecast computed in"}
            </dt>
            <dd className="num type-small text-text">
              {facts.forecastMs !== null
                ? formatMs(facts.forecastMs)
                : facts.stagesMs !== null
                  ? formatMs(facts.stagesMs)
                  : "Not recorded"}
            </dd>
            {facts.forecastMs !== null && facts.stagesMs !== null && stages ? (
              <dd className="num type-micro text-text-3">
                {stages} took {formatMs(facts.stagesMs)} of it
              </dd>
            ) : null}
          </div>
        </dl>
      ) : null}

      <p className="type-small text-text-2">
        First forecast, uncalibrated. VARUNA learns {cityName}&apos;s drains from the next monsoon.
      </p>
      {note ? <p className="type-micro text-text-3">{note}</p> : null}

      {scrub}

      {href ? (
        // A plain anchor, not `next/link`, and deliberately: `/console` has to read `?run=` from
        // the URL it actually lands on, and a full navigation is the one that guarantees it
        // (measured in a browser, 2026-09-19). It stays a real anchor for middle-click and "open
        // in new tab", styled as the primary button because it is this screen's one next step.
        <a href={href} className={buttonVariants()}>
          Open {cityName} console
        </a>
      ) : (
        <Button disabled aria-disabled="true">
          Open {cityName} console
        </Button>
      )}
    </section>
  );
}

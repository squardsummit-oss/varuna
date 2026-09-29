"use client";

import { Button, buttonVariants } from "@/components/ui/button";
import {
  formatCm,
  formatCount,
  formatDate,
  formatIst,
  formatMinutes,
  formatMs,
  formatTimeWithLead,
} from "@/lib/format";
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

/** Streets whose forecast peak reaches `thresholdCm`, out of every street the run scored. */
export interface FinishFlooded {
  count: number;
  total: number | null;
  thresholdCm: number;
}

/** The deepest street in the forecast and the step it peaks at. */
export interface FinishDeepest {
  cm: number;
  /** The valid time of the peak, ISO 8601; null when the run named no times. */
  at: string | null;
  /** Minutes from the forecast's cycle to `at`. */
  leadMin: number | null;
  /** The street's name, only when the city's streets are the ones the run scored. */
  name: string | null;
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
  /** The headline count, computed from the run's own depths once the map has loaded them. */
  flooded?: FinishFlooded | null;
  /** The deepest street, computed from the same depths. */
  deepest?: FinishDeepest | null;
  /** When the forecast was issued: the run's cycle time. */
  issuedAt?: string | null;
  /** How far ahead the forecast runs, in minutes. */
  horizonMin?: number | null;
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

interface Fact {
  label: string;
  value: string;
  /** Up to two short lines under the number. */
  subs?: string[];
}

/** The three headline facts, each falling back to what an older record carries. */
export function headlineFacts(facts: FinishFacts): [Fact, Fact, Fact] {
  const flooded: Fact = facts.flooded
    ? {
        label: "Flooded streets",
        value: formatCount(facts.flooded.count),
        subs: [
          facts.flooded.total !== null ? `of ${formatCount(facts.flooded.total)}` : null,
          `above ${formatCm(facts.flooded.thresholdCm)}`,
        ].filter((line): line is string => line !== null),
      }
    : {
        label: "Wet streets",
        value: facts.wetStreets !== null ? formatCount(facts.wetStreets) : "Not recorded",
        subs:
          facts.wetStreets !== null
            ? [
                facts.streetsTotal !== null ? `of ${formatCount(facts.streetsTotal)}` : null,
                facts.wetThresholdCm !== null ? `above ${formatCm(facts.wetThresholdCm)}` : null,
              ].filter((line): line is string => line !== null)
            : [],
      };

  const deepestAt = facts.deepest?.at
    ? formatTimeWithLead(facts.deepest.at, facts.deepest.leadMin)
    : null;
  const deepest: Fact = facts.deepest
    ? {
        label: "Deepest street",
        value: formatCm(facts.deepest.cm),
        subs: [deepestAt, facts.deepest.name].filter((line): line is string => Boolean(line)),
      }
    : {
        label: "Median street peak",
        value: facts.medianPeakCm !== null ? formatCm(facts.medianPeakCm) : "Not recorded",
      };

  const issued: Fact = facts.issuedAt
    ? {
        label: "Forecast issued",
        value: formatIst(facts.issuedAt),
        subs: [
          formatDate(facts.issuedAt),
          facts.horizonMin !== null && facts.horizonMin !== undefined
            ? `${formatMinutes(facts.horizonMin)} ahead`
            : null,
        ].filter((line): line is string => line !== null),
      }
    : {
        label: "Forecast computed in",
        value:
          facts.forecastMs !== null
            ? formatMs(facts.forecastMs)
            : facts.stagesMs !== null
              ? formatMs(facts.stagesMs)
              : "Not recorded",
      };

  return [flooded, deepest, issued];
}

/** The lines behind "Details": what an expert may ask, and nothing the headline already says. */
export function detailLines(facts: FinishFacts, cityName: string): string[] {
  const lines: string[] = [];
  const storm = facts.storm ? stormText(facts.storm) : null;
  if (facts.storm && storm) {
    const label =
      facts.storm.source === "run"
        ? `Rain on the run${facts.storm.id ? `, ${facts.storm.id}` : ""}`
        : `Design storm${facts.storm.id ? ` ${facts.storm.id}` : ""}`;
    lines.push(`${label}: ${storm}`);
  }
  if (facts.flooded && facts.wetStreets !== null) {
    const threshold = facts.wetThresholdCm !== null ? formatCm(facts.wetThresholdCm) : "5 cm";
    const total = facts.streetsTotal !== null ? ` of ${formatCount(facts.streetsTotal)}` : "";
    lines.push(`Streets above ${threshold}: ${formatCount(facts.wetStreets)}${total}`);
  }
  if (facts.deepest && facts.medianPeakCm !== null) {
    lines.push(`Median street peak: ${formatCm(facts.medianPeakCm)}`);
  }
  const stages = facts.stages.length > 0 ? stageListText(facts.stages) : null;
  if (facts.issuedAt) {
    if (facts.forecastMs !== null) {
      lines.push(
        `Forecast computed in ${formatMs(facts.forecastMs)}${
          facts.stagesMs !== null && stages
            ? `; ${stages} took ${formatMs(facts.stagesMs)} of it`
            : ""
        }`,
      );
    } else if (facts.stagesMs !== null && stages) {
      lines.push(`${stages}: ${formatMs(facts.stagesMs)}`);
    }
  } else if (facts.forecastMs !== null && facts.stagesMs !== null && stages) {
    lines.push(`${stages} took ${formatMs(facts.stagesMs)} of it`);
  }
  return lines;
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-chip border-line type-micro text-text-2 inline-flex items-center border px-2 py-0.5">
      {children}
    </span>
  );
}

/**
 * The wizard's result (SPEC.md 7.9): the first forecast in three numbers a judge reads in five
 * seconds - how many streets flood, the deepest one and when, and when the forecast was issued -
 * with the honesty chips beside the title, the lead-time scrub for the map, and the one button
 * that opens that city's console. Everything else a scientist might ask sits behind "Details".
 *
 * It is on screen before the forecast too, holding a disabled button, so the control lights up
 * where the eye already is rather than appearing.
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
  const headline = facts ? headlineFacts(facts) : null;
  const details = facts ? detailLines(facts, cityName) : [];

  return (
    <section
      aria-label="First forecast"
      className={cn("rounded-panel border-line bg-deep w-full space-y-3 border p-4", className)}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="type-small text-text mr-1 font-medium">First forecast</h2>
        <Chip>Design storm</Chip>
        <Chip>Uncalibrated</Chip>
      </div>

      {headline ? (
        <dl
          className="grid grid-cols-3 gap-x-3 gap-y-2"
          style={{ opacity: factsOpacity }}
          aria-label="First forecast numbers"
        >
          {headline.map((fact) => (
            <div key={fact.label} className="min-w-0">
              <dt className="type-micro text-text-3">{fact.label}</dt>
              <dd className="num font-display text-h2 tracking-display text-text leading-tight font-semibold">
                {fact.value}
              </dd>
              {(fact.subs ?? []).map((line) => (
                <dd key={line} className="num type-micro text-text-2 line-clamp-2" title={line}>
                  {line}
                </dd>
              ))}
            </div>
          ))}
        </dl>
      ) : (
        <p className="type-small text-text-2">
          Press Start onboarding to generate {cityName}&apos;s flood forecast.
        </p>
      )}

      {!facts && note ? <p className="type-micro text-text-3">{note}</p> : null}

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

      {facts ? (
        <details className="group">
          <summary className="type-micro text-text-2 hover:text-text focus-visible:ring-tide rounded-control w-fit cursor-pointer py-0.5 focus-visible:ring-2 focus-visible:outline-none">
            Details
          </summary>
          <ul className="type-micro text-text-3 mt-1.5 space-y-1">
            {details.map((line) => (
              <li key={line} className="num">
                {line}
              </li>
            ))}
            {note ? <li>{note}</li> : null}
            <li className="truncate font-mono" title={facts.runId}>
              {facts.runId}
            </li>
          </ul>
        </details>
      ) : null}
    </section>
  );
}

"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { CloudRain } from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { BrierByLeadChart, brierByLeadRows } from "@/components/varuna/brier-by-lead-chart";
import { EmptyState } from "@/components/varuna/empty-state";
import { Panel } from "@/components/varuna/panel";
import { ReliabilityDiagram } from "@/components/varuna/reliability-diagram";
import { Skeleton } from "@/components/varuna/skeleton";
import {
  FORECAST_LABEL,
  METRIC_LABEL,
  SkillByLeadChart,
  describeHorizon,
  skillByLeadRows,
  type SkillRow,
} from "@/components/varuna/skill-by-lead-chart";
import { formatIst } from "@/lib/format";
import {
  RainSkillLoadError,
  SKILL_METRICS,
  loadRainSkill,
  type RainSkill,
  type RainSkillScored,
  type SkillMetric,
  type SpreadForecastName,
} from "@/lib/api/verification-rain";
import { cn } from "@/lib/utils";

type ScopeName = "aoi" | "domain";

const SCOPE_OPTIONS: { value: ScopeName; label: string }[] = [
  { value: "aoi", label: "City grid" },
  { value: "domain", label: "Radar domain" },
];

const FORECAST_OPTIONS: { value: SpreadForecastName; label: string }[] = [
  { value: "mean", label: "Ensemble mean" },
  { value: "p50", label: "Ensemble median" },
];

/**
 * A single-choice toggle as a radio group: one tab stop, arrow keys move the choice
 * (WAI-ARIA radio group), 32 px targets for the control room.
 */
function ChoiceGroup<T extends string | number>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly { value: T; label: string }[];
  value: T;
  onChange: (next: T) => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const index = Math.max(
    0,
    options.findIndex((option) => option.value === value),
  );
  const move = (event: KeyboardEvent<HTMLButtonElement>) => {
    const step =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (step === 0) return;
    event.preventDefault();
    const next = (index + step + options.length) % options.length;
    onChange(options[next]!.value);
    refs.current[next]?.focus();
  };
  return (
    <div className="flex flex-col gap-1">
      <span className="type-micro text-text-3" id={`choice-${label.replace(/\W+/g, "-")}`}>
        {label}
      </span>
      <div
        role="radiogroup"
        aria-labelledby={`choice-${label.replace(/\W+/g, "-")}`}
        className="border-line bg-well rounded-control inline-flex w-fit border p-0.5"
      >
        {options.map((option, i) => {
          const checked = i === index;
          return (
            <button
              key={String(option.value)}
              ref={(node) => {
                refs.current[i] = node;
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              tabIndex={checked ? 0 : -1}
              onClick={() => onChange(option.value)}
              onKeyDown={move}
              className={cn(
                "num type-small rounded-control h-8 px-2.5 font-medium",
                checked ? "bg-tide-soft text-tide" : "text-text-2 hover:text-text",
              )}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** The numbers under the chart, for a reader who wants them rather than a line. */
function NumbersTable({
  rows,
  metric,
  forecast,
}: {
  rows: readonly SkillRow[];
  metric: SkillMetric;
  forecast: SpreadForecastName;
}) {
  const two = (value: number | null) => (value === null ? "—" : value.toFixed(2));
  const heads = [
    "Lead",
    "Cycles",
    "Pixels scored",
    "Event pixels",
    `${METRIC_LABEL[metric]}, ${FORECAST_LABEL[forecast]}`,
    "p10 to p90 (cycles)",
    "Persistence",
  ];
  return (
    <div
      role="region"
      className="max-h-80 overflow-auto"
      tabIndex={0}
      aria-label="Scores by lead time"
    >
      <table className="w-full border-collapse">
        <thead className="bg-deep sticky top-0">
          <tr className="border-line border-b text-left">
            {heads.map((head) => (
              <th key={head} scope="col" className="type-micro text-text-2 px-2 py-2 font-medium">
                {head}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.leadMin} className="border-line border-b last:border-b-0">
              <th
                scope="row"
                className="num type-small text-text px-2 py-1.5 text-left font-normal"
              >
                +{row.leadMin} min
              </th>
              <td className="num type-small text-text px-2 py-1.5">{row.nCycles}</td>
              <td className="num type-small text-text-2 px-2 py-1.5">
                {row.nPixels.toLocaleString("en-IN")}
              </td>
              <td className="num type-small text-text-2 px-2 py-1.5">
                {row.eventPixels.toLocaleString("en-IN")}
              </td>
              <td className="num type-small text-text px-2 py-1.5">{two(row.forecast)}</td>
              <td className="num type-small text-text-2 px-2 py-1.5">
                {row.band
                  ? `${row.band[0].toFixed(2)} to ${row.band[1].toFixed(2)} (${row.bandN})`
                  : `— (${row.bandN})`}
              </td>
              <td className="num type-small text-text-2 px-2 py-1.5">{two(row.persistence)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Leads the per-cycle table shows: the first scored step, then the chart's own landmarks. */
export const PER_CYCLE_LEADS_MIN: readonly number[] = [5, 15, 30, 60, 90, 120, 180];

/**
 * CSI at 20 mm/h cycle by cycle, as the scorer serves it (`per_cycle`), so a reader can see which
 * cycles carry the pooled line and which fail at which lead. The scorer serves this curve at
 * 20 mm/h only, whatever threshold the panel is showing, and the caption says so.
 */
function PerCycleTable({ curves }: { curves: RainSkillScored["byScope"]["aoi"]["perCycle"] }) {
  const cell = (values: (number | null)[], leads: number[], lead: number) => {
    const i = leads.indexOf(lead);
    return i < 0 ? null : (values[i] ?? null);
  };
  return (
    <div role="region" aria-label="CSI by cycle" className="overflow-x-auto" tabIndex={0}>
      <table className="w-full border-collapse">
        <caption className="type-micro text-text-2 px-2 py-2 text-left">
          CSI at 20 mm/h for each baked cycle, ensemble mean first and persistence after it. A dash
          is a lead with no score: nothing above 20 mm/h was forecast or seen, or the lead falls
          after the truth window ends.
        </caption>
        <thead>
          <tr className="border-line border-b text-left">
            <th scope="col" className="type-micro text-text-2 px-2 py-1.5 font-medium">
              Cycle (IST)
            </th>
            {PER_CYCLE_LEADS_MIN.map((lead) => (
              <th key={lead} scope="col" className="type-micro text-text-2 px-2 py-1.5 font-medium">
                +{lead} min
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {curves.map((curve) => (
            <tr key={curve.runId} className="border-line border-b last:border-b-0">
              <th
                scope="row"
                className="num type-small text-text px-2 py-1.5 text-left font-normal"
              >
                {formatIst(curve.cycleTs)}
              </th>
              {PER_CYCLE_LEADS_MIN.map((lead) => {
                const mean = cell(curve.csiMean20, curve.leadsMin, lead);
                const held = cell(curve.csiPersistence20, curve.leadsMin, lead);
                return (
                  <td key={lead} className="num type-small text-text px-2 py-1.5">
                    {mean === null ? "—" : mean.toFixed(2)}
                    <span className="text-text-3"> vs {held === null ? "—" : held.toFixed(2)}</span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * A served label that opens a sentence, set to follow "Scope: ". Only the first letter drops to
 * lower case, and not when the label opens on Sky, the engine's name; the rest is left as served.
 */
export function midSentence(label: string): string {
  if (label === "" || /^Sky\b/.test(label)) return label;
  return label.charAt(0).toLowerCase() + label.slice(1);
}

/**
 * Where a served score was computed, when it was not computed by the API answering: a deployment
 * serves the demo laptop's scores for the same runs (`provenance.served_from`, written by
 * `varuna verify`) because its copies of the runs carry no rain cubes. Printed as served.
 */
export function servedFromNote(provenance: Record<string, unknown>): string | null {
  const served = provenance.served_from;
  if (served === null || typeof served !== "object") return null;
  const note = (served as Record<string, unknown>).note;
  return typeof note === "string" && note.trim() !== "" ? note.trim() : null;
}

function MethodNote({
  skill,
  scope,
  thresholdMmH,
}: {
  skill: RainSkillScored;
  scope: ScopeName;
  thresholdMmH: number;
}) {
  const cycles = skill.cycles;
  const byLead = skill.byScope[scope].byLead;
  const most = byLead.reduce((m, row) => Math.max(m, row.nCycles), 0);
  const last = byLead[byLead.length - 1];
  const mismatched = cycles.filter((cycle) => cycle.persistence.matchesCycle === false);
  const items = [
    servedFromNote(skill.provenance),
    `${skill.truth.note} It is labelled "${skill.label || "Reconstructed replay"}" everywhere it is quoted.`,
    `An event is ${skill.definitions.event_pixel ? skill.definitions.event_pixel.replace(/^A /, "a ").replace(/\.$/, "") : "a pixel whose truth rain rate is strictly above the threshold"} (${thresholdMmH} mm/h here), on the 500 m Sky grid at the lead's valid time.`,
    `Scope: ${midSentence(skill.byScope[scope].label)}, ${skill.byScope[scope].nPixels.toLocaleString("en-IN")} pixels per frame.`,
    cycles.length > 0
      ? `Pooled over ${skill.nCycles} baked cycles, ${formatIst(cycles[0]!.cycleTs)} to ${formatIst(cycles[cycles.length - 1]!.cycleTs)} IST. The truth ends at ${formatIst(skill.truth.t1)}, so ${most} cycles reach the short leads and ${last ? `${last.nCycles} reach +${last.leadMin} min` : "fewer reach the long ones"}.`
      : "No cycles were scored.",
    `The band is the 10th to 90th percentile of the score computed cycle by cycle, drawn only where 3 or more cycles have a score. It shows how much the pooled line depends on the cycle; it is not a confidence interval.`,
    `The useful-skill horizon is the last lead before CSI first falls below persistence's or below ${skill.csiFloor}; ${skill.csiFloor} is a design choice stated with it, not a spec number.`,
    skill.definitions.persistence
      ? `Persistence: ${skill.definitions.persistence.charAt(0).toLowerCase()}${skill.definitions.persistence.slice(1)} ${mismatched.length === 0 ? `Its recomputed Z-R matches the run's on all ${cycles.length} cycles.` : `Its recomputed Z-R differs from the run's on ${mismatched.length} cycles, scored anyway.`}`
      : null,
    ...skill.skippedRuns.map((run) => `Skipped ${run.runId}: ${run.reason}`),
  ].filter((item): item is string => Boolean(item));
  return (
    <ul className="flex list-disc flex-col gap-1 pl-5">
      {items.map((item) => (
        <li key={item} className="type-micro text-text-2">
          {item}
        </li>
      ))}
    </ul>
  );
}

function ScoredView({ skill }: { skill: RainSkillScored }) {
  const thresholds = skill.thresholdsMmH.length > 0 ? skill.thresholdsMmH : [20, 40];
  const [metric, setMetric] = useState<SkillMetric>("csi");
  const [threshold, setThreshold] = useState<number>(
    thresholds.includes(skill.headlineThresholdMmH) ? skill.headlineThresholdMmH : thresholds[0]!,
  );
  const [scope, setScope] = useState<ScopeName>("aoi");
  const [forecast, setForecast] = useState<SpreadForecastName>("mean");

  const scoped = skill.byScope[scope];
  const horizon =
    scoped.horizons.find((h) => h.forecast === forecast && h.thresholdMmH === threshold) ?? null;
  const said = describeHorizon(horizon, forecast);
  const rowsByMetric = useMemo(
    () =>
      Object.fromEntries(
        SKILL_METRICS.map((m) => [m, skillByLeadRows(scoped, threshold, m, forecast)]),
      ) as Record<SkillMetric, SkillRow[]>,
    [scoped, threshold, forecast],
  );
  const brier = useMemo(() => brierByLeadRows(scoped, threshold), [scoped, threshold]);
  const firstBrier = brier.find((row) => row.brier !== null) ?? null;

  return (
    <div className="flex flex-col gap-4">
      <div
        className="border-line bg-well rounded-control border-l-line-strong border border-l-2 px-3 py-2"
        data-slot="horizon"
      >
        <p className="type-body text-text font-medium">{said.headline}</p>
        <p className="type-small text-text-2">{said.detail}</p>
        <p className="type-micro text-text-3 mt-1">
          {scope === "aoi" ? "City grid" : "Radar domain"}, {FORECAST_LABEL[forecast]}, {threshold}{" "}
          mm/h
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <ChoiceGroup
          label="Threshold"
          options={thresholds.map((t) => ({ value: t, label: `${t} mm/h` }))}
          value={threshold}
          onChange={setThreshold}
        />
        <ChoiceGroup label="Where" options={SCOPE_OPTIONS} value={scope} onChange={setScope} />
        <ChoiceGroup
          label="Forecast"
          options={FORECAST_OPTIONS}
          value={forecast}
          onChange={setForecast}
        />
      </div>

      <Tabs value={metric} onValueChange={(value) => setMetric(value as SkillMetric)}>
        <TabsList variant="line" aria-label="Score" className="border-line h-10 border-b">
          {SKILL_METRICS.map((m) => (
            <TabsTrigger key={m} value={m} className="type-small px-3">
              {METRIC_LABEL[m]}
            </TabsTrigger>
          ))}
        </TabsList>
        {SKILL_METRICS.map((m) => (
          <TabsContent key={m} value={m} className="pt-3">
            <p className="type-micro text-text-3 mb-2">
              {skill.definitions[m] ?? METRIC_LABEL[m]}{" "}
              {skill.units[m] ? `(${skill.units[m]})` : ""}
            </p>
            <SkillByLeadChart
              rows={rowsByMetric[m]}
              metric={m}
              thresholdMmH={threshold}
              forecast={forecast}
              horizon={horizon}
              csiFloor={skill.csiFloor}
            />
          </TabsContent>
        ))}
      </Tabs>

      <div className="grid gap-4 xl:grid-cols-2">
        <section aria-labelledby="brier-heading" className="flex flex-col gap-2">
          <h3 id="brier-heading" className="type-small text-text font-medium">
            Brier score by lead, {threshold} mm/h
          </h3>
          <p className="type-micro text-text-2">
            {firstBrier
              ? `Lower is better. At +${firstBrier.leadMin} min the members' probability scores ${firstBrier.brier?.toFixed(3)} against persistence's ${firstBrier.persistence?.toFixed(3) ?? "unscored"}.`
              : "Lower is better."}
          </p>
          <BrierByLeadChart rows={brier} thresholdMmH={threshold} height={260} />
        </section>
        <section aria-labelledby="reliability-heading" className="flex flex-col gap-2">
          <h3 id="reliability-heading" className="type-small text-text font-medium">
            Reliability, {threshold} mm/h
          </h3>
          <p className="type-micro text-text-2">
            Above the diagonal the members said too little; below it, too much.
          </p>
          <ReliabilityDiagram
            bands={scoped.reliability[String(threshold)] ?? []}
            thresholdMmH={threshold}
            emptyReason={skill.unavailable[`probability_${threshold}_mm_h`] ?? null}
          />
        </section>
      </div>

      <details className="border-line rounded-control border" data-slot="rain-method">
        <summary className="type-small text-text-2 hover:text-text focus-visible:ring-tide cursor-pointer px-3 py-2 focus-visible:ring-2 focus-visible:outline-none">
          How this was scored
        </summary>
        <div className="border-line border-t px-3 py-2">
          <MethodNote skill={skill} scope={scope} thresholdMmH={threshold} />
        </div>
      </details>

      <details className="border-line rounded-control border">
        <summary className="type-small text-text-2 hover:text-text cursor-pointer px-3 py-2">
          Every number on the {METRIC_LABEL[metric]} chart
        </summary>
        <div className="border-line border-t">
          <NumbersTable rows={rowsByMetric[metric]} metric={metric} forecast={forecast} />
        </div>
      </details>

      {scoped.perCycle.length > 0 ? (
        <details className="border-line rounded-control border">
          <summary className="type-small text-text-2 hover:text-text cursor-pointer px-3 py-2">
            CSI at 20 mm/h, cycle by cycle ({scoped.perCycle.length} cycles,{" "}
            {scope === "aoi" ? "city grid" : "radar domain"})
          </summary>
          <div className="border-line border-t">
            <PerCycleTable curves={scoped.perCycle} />
          </div>
        </details>
      ) : null}
    </div>
  );
}

/**
 * What the not-scored state says: the scorer's reason, then the one command it served for what
 * is missing. The command is served as data, never guessed from the reason's wording, and a
 * scorer that served none gets its reason alone.
 */
export function notScoredText(reason: string, command: string | null): string {
  const said = reason.trim();
  return command ? `${said} Run ${command}, then reload this page.` : said;
}

/**
 * Rain skill by lead time on Pramana (SPEC.md 7.10). Loads `/v1/verification/rain-skill` for
 * the event and renders every state: loading, not scored (with the command that scores it),
 * failed, and scored.
 */
export function RainSkillPanel({ event }: { event: string }) {
  const [answer, setAnswer] = useState<{
    event: string;
    result: RainSkill | null;
    error: string | null;
    /** True only when no API answered, the one case where starting it is the fix. */
    unreachable: boolean;
  } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    loadRainSkill(event, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setAnswer({ event, result, error: null, unreachable: false });
        }
      })
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return;
        setAnswer({
          event,
          result: null,
          error: failure instanceof Error ? failure.message : String(failure),
          unreachable: failure instanceof RainSkillLoadError && failure.unreachable,
        });
      });
    return () => controller.abort();
  }, [event]);

  const current = answer?.event === event ? answer : null;
  const skill = current?.result ?? null;

  return (
    <Panel
      title="Rain skill by lead time"
      description="Rain skill by lead against the reconstructed truth, with persistence as the bar."
    >
      <div id="rain-skill" aria-busy={current === null}>
        {current === null ? (
          <div className="flex flex-col gap-3" data-slot="rain-skill-loading">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="h-[260px] w-full" />
            <p className="type-micro text-text-3">
              Scoring every baked cycle against the truth field.
            </p>
          </div>
        ) : current.error ? (
          <EmptyState
            icon={CloudRain}
            title="Rain skill did not load"
            description={
              current.unreachable
                ? `${current.error} Start the API with make dev, then reload.`
                : current.error
            }
          />
        ) : skill && !skill.available ? (
          <EmptyState
            icon={CloudRain}
            title="Not scored yet"
            description={notScoredText(skill.reason, skill.command)}
          />
        ) : skill && skill.available ? (
          <ScoredView key={skill.event} skill={skill} />
        ) : null}
      </div>
    </Panel>
  );
}

"use client";

import { ChevronDown, Pause, Play } from "lucide-react";
import { motion } from "motion/react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Slider } from "@/components/ui/slider";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  CycleBudgetBar,
  STAGE_IDS,
  type StageId,
  type StageTiming,
} from "@/components/varuna/cycle-budget-bar";
import { useReplayBundles, useReplayControls } from "@/lib/api";
import { apiUrl } from "@/lib/api/client";
import { useLive } from "@/lib/api/live";
import type { LiveEvent } from "@/lib/api/schemas";
import { formatMs } from "@/lib/format";
import { cn } from "@/lib/utils";
import { SPRING, useMotionPref } from "@/lib/motion";
import {
  LEAD_FINE,
  LEAD_MAX,
  LEAD_MIN,
  LEAD_TICK,
  REPLAY_SPEEDS,
  isReplaySpeed,
  selectLeadLabel,
  selectSimDateLabel,
  selectSimTimeLabel,
  selectValidTimeLabel,
  useReplayStore,
} from "@/lib/stores/replay";
import { registerPlayToggle } from "@/lib/shortcuts";
import { useRunStore } from "@/lib/stores/run";

const LEAD_SPAN = LEAD_MAX - LEAD_MIN;
const TICKS = Array.from({ length: LEAD_SPAN / LEAD_TICK + 1 }, (_, i) => LEAD_MIN + i * LEAD_TICK);
const LABELLED_TICKS = new Set([-60, 0, 60, 120, 180]);

function leadToPercent(leadMin: number): number {
  return ((leadMin - LEAD_MIN) / LEAD_SPAN) * 100;
}

/** The band's own vertical room, in the viewBox units the path is drawn in. */
const BAND_HEIGHT = 10;

/**
 * Section 7.2's spread band under the track: p10 to p90 of mean street depth across the run's
 * members, one point per forecast step, placed on the same lead axis as the scrub so a lead
 * reads straight down from the handle. Scaled to the run's own widest p90, because the point is
 * where the uncertainty opens up, not its absolute size - the number is in the label.
 */
function bandPaths(
  band: { p10: number[]; p50: number[]; p90: number[] },
  stepMin: number,
): { area: string; median: string; widestCm: number; atLead: number } | null {
  const n = Math.min(band.p10.length, band.p50.length, band.p90.length);
  if (n < 2) return null;
  const top = Math.max(...band.p90.slice(0, n), 1e-6);
  const x = (i: number) => leadToPercent(Math.min(i * stepMin, LEAD_MAX));
  const y = (cm: number) => BAND_HEIGHT - (cm / top) * BAND_HEIGHT;
  const upper = Array.from({ length: n }, (_, i) => `${x(i)},${y(band.p90[i])}`);
  const lower = Array.from({ length: n }, (_, i) => `${x(n - 1 - i)},${y(band.p10[n - 1 - i])}`);
  let widest = 0;
  let atLead = 0;
  for (let i = 0; i < n; i += 1) {
    const width = band.p90[i] - band.p10[i];
    if (width > widest) {
      widest = width;
      atLead = i * stepMin;
    }
  }
  return {
    area: `M${upper.join(" L")} L${lower.join(" L")} Z`,
    median: `M${Array.from({ length: n }, (_, i) => `${x(i)},${y(band.p50[i])}`).join(" L")}`,
    widestCm: widest,
    atLead,
  };
}

function speedLabel(speed: number): string {
  return `${speed}×`;
}

/**
 * The console time bar (SPEC.md sections 6.5 and 7.2): play, speed, the scrub from -60 to +180 min,
 * the valid time and lead, and the live-compute controls. It is the one glass element in the product.
 * Arrow keys, Shift and Space are handled globally by `useGlobalShortcuts`.
 */
export function TimeBar() {
  const playing = useReplayStore((s) => s.playing);
  const speed = useReplayStore((s) => s.speed);
  const leadMin = useReplayStore((s) => s.leadMin);
  const setLeadMin = useReplayStore((s) => s.setLeadMin);
  const validLabel = useReplayStore(selectValidTimeLabel);
  const leadLabel = useReplayStore(selectLeadLabel);
  const cycleTimeLabel = useReplayStore(selectSimTimeLabel);
  const cycleDateLabel = useReplayStore(selectSimDateLabel);
  // `POST /v1/cycle/compute` runs the shared clock's bundle, which /replay can switch, so the copy
  // names that bundle by its manifest label rather than assuming the 2019 reconstruction.
  const bundleId = useReplayStore((s) => s.bundleId);
  const bundles = useReplayBundles();
  const bundleLabel = bundles.data?.find((b) => b.id === bundleId)?.label ?? null;
  const hasRun = useRunStore((s) => s.currentRun !== null);
  const band = useRunStore((s) => s.currentRun?.aoi_depth_band ?? null);
  const stepMin = useRunStore((s) => s.currentRun?.step_min ?? 5);
  const spread = band ? bandPaths(band, stepMin) : null;
  const controls = useReplayControls();
  const { reduced } = useMotionPref();

  // Space is a global shortcut; while the time bar is on screen it drives the API's clock.
  const toggle = controls.toggle;
  useEffect(() => registerPlayToggle(toggle), [toggle]);

  const handleTransition = reduced ? { duration: 0 } : SPRING;

  const compute = useComputeLive();
  const info = compute.info;
  const canCompute = hasRun && Boolean(info?.enabled) && !compute.active && !info?.busy;
  const copy = computeLiveCopy({
    bundle: { id: bundleId, label: bundleLabel },
    info,
    unreachable: compute.unreachable,
    hasRun,
    active: compute.active,
    elapsedMs: compute.elapsedMs,
    cycleTimeLabel,
    cycleDateLabel,
  });

  return (
    <div
      className="border-line bg-ink/72 flex h-24 w-full items-center gap-6 border-t px-4 backdrop-blur-[12px]"
      role="toolbar"
      aria-label="Replay time bar"
    >
      {/* Left: play and speed */}
      <div className="flex shrink-0 items-center gap-2">
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant={playing ? "secondary" : "default"}
                size="icon"
                aria-label={playing ? "Pause the replay" : "Play the replay"}
                aria-disabled={!hasRun}
                aria-pressed={playing}
                className={cn(!hasRun && "opacity-60")}
                onClick={() => controls.toggle()}
              />
            }
          >
            {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
          </TooltipTrigger>
          <TooltipContent>
            {hasRun
              ? playing
                ? "Pause (Space)"
                : "Play (Space)"
              : "Press Play once a bundle is baked"}
          </TooltipContent>
        </Tooltip>

        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                variant="outline"
                size="sm"
                aria-label={`Replay speed ${speedLabel(speed)}`}
              />
            }
          >
            <span className="num">{speedLabel(speed)}</span>
            <ChevronDown aria-hidden="true" data-icon="inline-end" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuRadioGroup
              value={String(speed)}
              onValueChange={(value) => {
                const next = Number(value);
                if (isReplaySpeed(next)) controls.setSpeed(next);
              }}
            >
              {REPLAY_SPEEDS.map((s) => (
                <DropdownMenuRadioItem key={s} value={String(s)}>
                  <span className="num">{speedLabel(s)}</span>
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Centre: the scrub */}
      <div className="flex min-w-0 flex-1 flex-col justify-center gap-1.5">
        <div className="relative h-8">
          {/* Observed half (-60..0) and forecast half (0..+180) */}
          <div
            aria-hidden="true"
            className="absolute inset-x-0 top-1/2 flex h-1.5 -translate-y-1/2 overflow-hidden rounded-full"
          >
            <div className="bg-text-3/50 h-full" style={{ width: `${leadToPercent(0)}%` }} />
            <div className="bg-tide-soft h-full flex-1" />
          </div>

          {/* 15-minute ticks */}
          <div aria-hidden="true" className="pointer-events-none absolute inset-0">
            {TICKS.map((tick) => (
              <span
                key={tick}
                className={cn(
                  "bg-line-strong absolute top-1/2 w-px -translate-x-1/2",
                  LABELLED_TICKS.has(tick) ? "h-4 -translate-y-1/2" : "h-2 -translate-y-1/2",
                )}
                style={{ left: `${leadToPercent(tick)}%` }}
              />
            ))}
          </div>

          {/* Springing handle (M6); the slider thumb underneath carries the interaction. */}
          <motion.span
            aria-hidden="true"
            className="border-tide bg-ink pointer-events-none absolute top-1/2 z-10 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2"
            initial={false}
            animate={{ left: `${leadToPercent(leadMin)}%` }}
            transition={handleTransition}
          />

          <Slider
            aria-label="Scrub the forecast, minutes from the cycle time"
            min={LEAD_MIN}
            max={LEAD_MAX}
            step={LEAD_FINE}
            value={leadMin}
            onValueChange={(value) => {
              const next = Array.isArray(value) ? value[0] : value;
              if (typeof next === "number") setLeadMin(next);
            }}
            className="[&_[data-slot=slider-thumb]]:border-tide absolute inset-x-0 top-1/2 z-20 -translate-y-1/2 [&_[data-slot=slider-range]]:bg-transparent [&_[data-slot=slider-thumb]]:bg-transparent [&_[data-slot=slider-track]]:bg-transparent"
          />
        </div>

        {/* Tick labels and the ensemble spread band (7.2) */}
        <div className="relative h-6">
          <div aria-hidden="true" className="absolute inset-x-0 top-0">
            {[...LABELLED_TICKS].map((tick) => (
              <span
                key={tick}
                className="num type-micro text-text-3 absolute -translate-x-1/2"
                style={{ left: `${leadToPercent(tick)}%` }}
              >
                {tick > 0 ? `+${tick}` : tick}
              </span>
            ))}
          </div>
          {spread ? (
            <svg
              role="img"
              aria-label={`Ensemble spread of mean street depth, p10 to p90: widest ${spread.widestCm.toFixed(1)} cm, at +${spread.atLead} min`}
              className="absolute inset-x-0 bottom-0 h-3 w-full"
              viewBox={`0 0 100 ${BAND_HEIGHT}`}
              preserveAspectRatio="none"
            >
              <path d={spread.area} style={{ fill: "var(--tide)", fillOpacity: 0.2 }} />
              <path
                d={spread.median}
                vectorEffect="non-scaling-stroke"
                style={{ fill: "none", stroke: "var(--tide)", strokeWidth: 1 }}
              />
            </svg>
          ) : (
            <div className="absolute inset-x-0 bottom-0 flex items-center gap-2">
              <span aria-hidden="true" className="bg-line h-px flex-1" />
              <span className="type-micro text-text-3">
                {hasRun
                  ? "This run has no ensemble spread to draw"
                  : "Ensemble spread appears with the first run"}
              </span>
              <span aria-hidden="true" className="bg-line h-px flex-1" />
            </div>
          )}
        </div>
      </div>

      {/* Right: valid time, compute live, cycle budget */}
      <div className="flex shrink-0 flex-col items-end gap-1">
        <div className="flex items-center gap-3">
          <span className="num type-h3 text-text" aria-live="polite">
            {validLabel}
          </span>
          <span className="sr-only">Lead {leadLabel}</span>
          <Tooltip>
            <TooltipTrigger render={<span className="inline-flex" />}>
              <Button
                variant="outline"
                size="sm"
                disabled={!canCompute}
                aria-disabled={!canCompute}
                aria-describedby="compute-live-note"
                onClick={() => void compute.start()}
              >
                Compute live
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-80">{copy.tooltip}</TooltipContent>
          </Tooltip>
        </div>
        {/* Two short lines rather than one long one: what Compute live runs, then how long it
            takes here. With the budget bar under them the column is 90 px of the bar's 96
            (measured at 1366 x 768 and 1440 x 900), so the gaps stay at 4 and 2 px. */}
        <div className="flex w-72 flex-col gap-0.5">
          <p id="compute-live-note" className="type-micro leading-tight">
            <span className="text-text-2 block truncate">{copy.label}</span>
            {/* Keeps the two lines apart when read as one description. */}{" "}
            <span className="num text-text-3 block truncate">{copy.note}</span>
          </p>
          <CycleBudgetBar
            compact
            stages={compute.stages}
            running={compute.running}
            totalMs={compute.totalMs}
          />
        </div>
      </div>
    </div>
  );
}

/** The bundle the shared replay clock is on: its id and its manifest label once served. */
export interface ComputeLiveBundle {
  id: string;
  /** `label` from `GET /v1/replay/bundles` ("Reconstructed replay", "Design storm"); null until known. */
  label: string | null;
}

/**
 * What the bundle is, in the words the time bar prints, taken from its manifest label. Only the
 * 2019 reconstruction is called "reconstructed" (rule 7); a design storm says so; a bundle whose
 * label is not yet known, or is neither, is named by its id rather than guessed.
 */
function bundleKind(bundle: ComputeLiveBundle): "reconstructed" | "design" | null {
  const label = bundle.label?.trim().toLowerCase() ?? "";
  if (label === "reconstructed replay") return "reconstructed";
  if (label === "design storm") return "design";
  return null;
}

/**
 * The line over Compute live's note. Compute live re-runs the replay clock's current cycle from
 * that clock's bundle with real computation; it is not today's weather, which the console's
 * "Today, next 3 h" card carries on its own clock.
 */
export function computeLiveLabel(bundle: ComputeLiveBundle): string {
  const kind = bundleKind(bundle);
  if (kind === "reconstructed") return "Live compute on the reconstructed replay";
  if (kind === "design") return "Live compute on the design storm";
  return `Live compute on bundle ${bundle.id}`;
}

/** "the reconstructed replay bundle MUM-2019-07-02", "the design storm bundle MUM-IDF-25yr". */
function bundlePhrase(bundle: ComputeLiveBundle): string {
  const kind = bundleKind(bundle);
  if (kind === "reconstructed") return `the reconstructed replay bundle ${bundle.id}`;
  if (kind === "design") return `the design storm bundle ${bundle.id}`;
  return `bundle ${bundle.id}`;
}

/** The note under the label and the button's tooltip, for every state the button can be in. */
export function computeLiveCopy({
  bundle,
  info,
  unreachable = false,
  hasRun,
  active,
  elapsedMs,
  cycleTimeLabel,
  cycleDateLabel,
}: {
  /** The shared clock's bundle, which is what `POST /v1/cycle/compute` runs. */
  bundle: ComputeLiveBundle;
  info: ComputeInfo | null;
  /** `GET /v1/cycle/compute` failed or is not served: the button stays off and says why. */
  unreachable?: boolean;
  hasRun: boolean;
  active: boolean;
  elapsedMs: number | null;
  cycleTimeLabel: string;
  cycleDateLabel: string;
}): { label: string; note: string; tooltip: string } {
  const expected = info?.expected;
  const median = expected?.median_ms ?? null;
  const budget = formatMs(info?.budget_ms ?? 15_000);
  const range =
    expected && median !== null
      ? `${formatMs(expected.min_ms)} to ${formatMs(expected.max_ms)} over ${expected.n_runs} runs`
      : null;
  const cycle = `the ${cycleTimeLabel} cycle of ${cycleDateLabel || "the bundle"}`;

  const note = active
    ? `Running, ${formatMs(elapsedMs)} so far${median !== null ? ` of about ${formatMs(median)}` : ""}`
    : !info
      ? unreachable
        ? "The API did not say whether it can compute"
        : "Asking the server how long a cycle takes"
      : !info.enabled
        ? "Off on this server"
        : median !== null
          ? `About ${formatMs(median)} a cycle here, against ${budget}`
          : "No cycle timed on this server yet";

  const what = `Re-runs ${cycle} from ${bundlePhrase(bundle)} with real computation. It is not today's weather.`;
  const tooltip = !hasRun
    ? "Available once a bundle is loaded"
    : !info && unreachable
      ? "The API did not answer, so Compute live stays off. The baked replay is unaffected."
      : info && !info.enabled
        ? (info.reason ?? "Compute live is off on this server")
        : info?.busy || active
          ? "A live cycle is running; its stages fill the bar below"
          : range && median !== null
            ? `${what} A cycle takes about ${formatMs(median)} here (${range}), against a ${budget} budget.`
            : what;
  return { label: computeLiveLabel(bundle), note, tooltip };
}

/** What `GET /v1/cycle/compute` says about this server. */
export interface ComputeInfo {
  enabled: boolean;
  reason: string | null;
  busy: boolean;
  budget_ms: number;
  expected: {
    median_ms: number | null;
    min_ms: number | null;
    max_ms: number | null;
    n_runs: number;
  };
}

/**
 * "Compute live" (SPEC.md 7.2, 11.11; task P6.11): one real cycle on the API, with the budget
 * bar filling stage by stage from the socket's `cycle.stage` events (motion M21).
 *
 * The button says how long a cycle takes **here** before anyone presses it: the median of the
 * recent runs' own stage totals, which on the demo laptop is a minute or more against section
 * 14's 15 s. And it says so when the server has it off (`VARUNA_COMPUTE_LIVE`), rather than
 * sitting disabled with no reason.
 */
function useComputeLive() {
  const [info, setInfo] = useState<ComputeInfo | null>(null);
  // The server did not answer, or answered without the endpoint: said, never "asking" forever.
  const [unreachable, setUnreachable] = useState(false);
  const [stageMs, setStageMs] = useState<Partial<Record<StageId, number>>>({});
  const [running, setRunning] = useState<StageId | null>(null);
  const [active, setActive] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(0);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    fetch(apiUrl("/v1/cycle/compute"), { signal: controller.signal })
      .then((r) => (r.ok ? (r.json() as Promise<ComputeInfo>) : null))
      .then((body) => {
        if (body) setInfo(body);
        setUnreachable(!body);
      })
      .catch(() => {
        if (!controller.signal.aborted) setUnreachable(true);
      });
    return () => controller.abort();
  }, [refresh]);

  // Elapsed while a cycle runs, once a second: the operator is told how far in they are.
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);

  const onEvent = useCallback((event: LiveEvent) => {
    const payload = (event.payload ?? {}) as Record<string, unknown>;
    if (event.type === "cycle.stage") {
      const stage = String(payload.stage ?? "") as StageId;
      if (!(STAGE_IDS as readonly string[]).includes(stage)) return;
      setActive(true);
      if (payload.status === "started") setRunning(stage);
      if (payload.status === "finished" && typeof payload.ms === "number") {
        const ms = payload.ms;
        setStageMs((current) => ({ ...current, [stage]: ms }));
      }
      if (payload.status === "failed") {
        setActive(false);
        setRunning(null);
        toast.error(`The live cycle failed at ${stage}. The map still shows the last run.`);
      }
      return;
    }
    if (event.type === "runs.published" && payload.mode === "live") {
      const record = (payload.stage_ms ?? {}) as Record<string, number>;
      // The numbers of record from run.json replace the live clocks.
      setStageMs(
        Object.fromEntries(
          STAGE_IDS.filter((id) => typeof record[id] === "number").map((id) => [id, record[id]]),
        ),
      );
      setActive(false);
      setRunning(null);
      setRefresh((n) => n + 1);
      // The console swaps its map to the new run on the same event; this names it.
      toast.success("Live run published", { description: String(payload.run_id ?? "") });
    }
  }, []);
  useLive({ topics: ["cycle.stage", "runs.published"], onEvent });

  const start = useCallback(async () => {
    setStageMs({});
    setRunning("sky");
    setActive(true);
    const t = Date.now();
    setStartedAt(t);
    setNow(t);
    try {
      const response = await fetch(apiUrl("/v1/cycle/compute"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as {
          error?: { message?: string };
        } | null;
        throw new Error(body?.error?.message ?? `Compute live answered ${response.status}.`);
      }
    } catch (error) {
      setActive(false);
      setRunning(null);
      toast.error(error instanceof Error ? error.message : String(error));
      setRefresh((n) => n + 1);
    }
  }, []);

  const stages: StageTiming[] = STAGE_IDS.map((id) => ({ id, ms: stageMs[id] ?? null }));
  const reported = Object.values(stageMs).filter((v): v is number => typeof v === "number");
  const totalMs = reported.length > 0 ? reported.reduce((a, b) => a + b, 0) : null;
  const elapsedMs = active && startedAt !== null ? Math.max(0, now - startedAt) : null;
  return {
    info,
    unreachable,
    stages,
    running: active ? running : null,
    active,
    totalMs,
    elapsedMs,
    start,
  };
}

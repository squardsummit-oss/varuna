"use client";

/**
 * Contingency by threshold, poured (SPEC.md 7.10; motion M36).
 *
 * One measuring cylinder per scored threshold. Each fills from the bottom with that threshold's
 * served contingency: hits, then misses, then false alarms, stacked on one shared count axis, so a
 * band's height is its count and nothing else (rule 6). The water rises 600 ms per band with a
 * 150 ms stagger across thresholds, under a crest whose amplitude decays to flat within 1.2 s; the
 * loop then stops. Picking another threshold drains every cylinder and pours it again while the
 * readout's CSI, POD and FAR roll to the new threshold.
 *
 * Colour is never the only carrier: every band has its word and its number beside the cylinder,
 * the plot's accessible name carries every figure, and the readout is live text. Under reduced
 * motion the cylinders stand full, the crest is flat and the numbers are final at once.
 */

import NumberFlow from "@number-flow/react";
import { useEffect, useId, useMemo, useRef, useState, type RefObject } from "react";

import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { clamp01, DUR_MS, easeUi } from "@/lib/motion";
import { cn } from "@/lib/utils";

/** One served threshold row, as the cylinders need it. */
export interface PourColumn {
  thresholdCm: number;
  hits: number;
  misses: number;
  falseAlarms: number;
  csi: number | null;
  pod: number | null;
  far: number | null;
  medianLeadMin: number | null;
}

export interface ContingencyPourProps {
  columns: readonly PourColumn[];
  /** Sourced pins inside the forecast window: the sample every count is over. */
  groundTruthCount: number | null;
  /** The threshold the headline scores use; selected first when it is among the columns. */
  headlineCm?: number | null;
  className?: string;
}

type BandKey = "hits" | "misses" | "falseAlarms";

interface Band {
  key: BandKey;
  label: string;
  one: string;
  many: string;
  colour: string;
  meaning: string;
}

/** Bottom to top: the order the water pours in. */
export const BANDS: readonly Band[] = [
  {
    key: "hits",
    label: "Hits",
    one: "hit",
    many: "hits",
    colour: "var(--chart-1)",
    meaning: "pin flagged in time",
  },
  {
    key: "misses",
    label: "Misses",
    one: "miss",
    many: "misses",
    colour: "var(--chart-3)",
    meaning: "pin not flagged",
  },
  {
    key: "falseAlarms",
    label: "False alarms",
    one: "false alarm",
    many: "false alarms",
    colour: "var(--chart-2)",
    meaning: "flagged near a pin, no record",
  },
];

const TANK_W = 64;
const PLOT_H = 200;
/** Headroom above the top of the scale, so a crest at full never leaves the cylinder. */
const HEAD = 10;
const SVG_H = PLOT_H + HEAD;
const TANK_R = 10;
const CREST_PX = 7;
const WAVELENGTH = TANK_W * 0.85;
const WAVE_HZ = 1.8;
const SAMPLES = 24;
/** The most one frame may advance the pour, so a long frame pauses it instead of skipping it. */
const MAX_STEP_MS = 48;
/** Number of bubble particles per cylinder during pour. */
const PARTICLE_COUNT = 6;
/** Shimmer stripe count for the water body. */
const SHIMMER_STRIPES = 3;

/** A 0-based axis with at most five ticks, topped at a round number at or above `max`. */
export function niceScale(max: number): { top: number; ticks: number[] } {
  if (!(max > 0)) return { top: 1, ticks: [0, 1] };
  const rough = max / 4;
  const mag = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= rough) ?? 10 * mag;
  const top = Math.ceil(max / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let v = 0; v <= top + 1e-9; v += step) ticks.push(Number(v.toFixed(6)));
  return { top, ticks };
}

const total = (c: PourColumn) => c.hits + c.misses + c.falseAlarms;

interface PlannedBand {
  key: BandKey;
  colour: string;
  from: number;
  to: number;
  /** Milliseconds after the pour begins. */
  start: number;
}

interface Plan {
  bands: PlannedBand[];
  /** When the crest over the last band is flat. */
  end: number;
}

/** A column's pour: only bands with water in them take time, so a zero count adds no pause. */
export function planColumn(column: PourColumn, index: number): Plan {
  let t = index * DUR_MS.staggerThresholds;
  let level = 0;
  const bands: PlannedBand[] = [];
  for (const band of BANDS) {
    const n = column[band.key];
    if (!(n > 0)) continue;
    bands.push({ key: band.key, colour: band.colour, from: level, to: level + n, start: t });
    level += n;
    t += DUR_MS.pourBand;
  }
  const last = bands[bands.length - 1];
  return { bands, end: last ? last.start + DUR_MS.crestSettle : 0 };
}

/** Water level (in counts) and crest amplitude (in px) of a planned pour at `t` ms. */
export function pourAt(plan: Plan, t: number): { level: number; amp: number } {
  let current: PlannedBand | null = null;
  for (const band of plan.bands) if (band.start <= t) current = band;
  if (!current) return { level: 0, amp: 0 };
  const u = clamp01((t - current.start) / DUR_MS.pourBand);
  const decay = 1 - clamp01((t - current.start) / DUR_MS.crestSettle);
  return {
    level: current.from + (current.to - current.from) * easeUi(u),
    amp: CREST_PX * decay * decay,
  };
}

type Phase = "idle" | "drain" | "pour" | "done";

interface Frame {
  phase: Phase;
  levels: number[];
  amps: number[];
  /** Milliseconds since the run began, for the crest's phase. */
  clock: number;
  /** Milliseconds into the pour, for which counts have been revealed. */
  pourT: number;
}

/** Once the element is at least `amount` in view; immediately where there is no observer. */
function useSeenOnce(ref: RefObject<Element | null>, amount = 0.2): boolean {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (seen) return;
    const el = ref.current;
    if (typeof IntersectionObserver === "undefined" || !el) {
      const id = window.setTimeout(() => setSeen(true), 0);
      return () => window.clearTimeout(id);
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setSeen(true);
          io.disconnect();
        }
      },
      { threshold: amount },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [ref, seen, amount]);
  return seen;
}

const two = (value: number | null) => (value === null ? "no denominator" : value.toFixed(2));
const plural = (n: number, band: Band) => (n === 1 ? band.one : band.many);

function describe(columns: readonly PourColumn[], n: number | null): string {
  const over = n === null ? "the sourced pins" : `${n} sourced ${n === 1 ? "pin" : "pins"}`;
  return (
    `Contingency by threshold on ${over}: ` +
    columns
      .map(
        (c) =>
          `${c.thresholdCm} cm ${c.hits} ${plural(c.hits, BANDS[0]!)}, ` +
          `${c.misses} ${plural(c.misses, BANDS[1]!)}, ` +
          `${c.falseAlarms} ${plural(c.falseAlarms, BANDS[2]!)}, ` +
          `CSI ${two(c.csi)}, POD ${two(c.pod)}, FAR ${two(c.far)}`,
      )
      .join("; ") +
    "."
  );
}

export function ContingencyPour({
  columns,
  groundTruthCount,
  headlineCm = null,
  className,
}: ContingencyPourProps) {
  const uid = useId().replace(/:/g, "");
  const rows = useMemo(() => [...columns].sort((a, b) => a.thresholdCm - b.thresholdCm), [columns]);
  const { top, ticks } = useMemo(() => niceScale(Math.max(0, ...rows.map(total))), [rows]);
  const plans = useMemo(() => rows.map(planColumn), [rows]);
  const finalFrame = useMemo<Frame>(
    () => ({
      phase: "done",
      levels: rows.map(total),
      amps: rows.map(() => 0),
      clock: 0,
      pourT: Number.POSITIVE_INFINITY,
    }),
    [rows],
  );

  const reduced = usePrefersReducedMotion();
  const plotRef = useRef<HTMLDivElement | null>(null);
  const seen = useSeenOnce(plotRef);

  const initialCm =
    headlineCm !== null && rows.some((r) => r.thresholdCm === headlineCm)
      ? headlineCm
      : (rows[0]?.thresholdCm ?? null);
  const [picked, setPicked] = useState<number | null>(null);
  const selectedCm =
    picked !== null && rows.some((r) => r.thresholdCm === picked) ? picked : initialCm;
  const selected = rows.find((r) => r.thresholdCm === selectedCm) ?? null;

  const [pour, setPour] = useState({ id: 0, drain: false });
  const [frame, setFrame] = useState<Frame>(() => ({
    phase: "idle",
    levels: rows.map(() => 0),
    amps: rows.map(() => 0),
    clock: 0,
    pourT: 0,
  }));
  const levelsRef = useRef<number[]>(frame.levels);

  useEffect(() => {
    if (!seen || reduced) return;
    const n = plans.length;
    const drainFrom = pour.drain ? levelsRef.current.slice(0, n) : null;
    const drainMs = drainFrom && drainFrom.some((l) => l > 0) ? DUR_MS.pourBand : 0;
    const pourEnd = Math.max(0, ...plans.map((p) => p.end));
    // A clock that advances at most MAX_STEP_MS a frame: a main thread busy drawing the rest of
    // the page pauses the pour rather than skipping it.
    let clock = 0;
    let last: number | null = null;
    let raf = 0;
    const tick = (now: number) => {
      clock += last === null ? 0 : Math.min(Math.max(0, now - last), MAX_STEP_MS);
      last = now;
      const t = clock;
      if (t < drainMs && drainFrom) {
        const s = t / drainMs;
        const levels = drainFrom.map((l) => l * (1 - easeUi(s)));
        const amps = levels.map((l) => (l > 0 ? CREST_PX * 0.6 * (1 - s) : 0));
        levelsRef.current = levels;
        setFrame({ phase: "drain", levels, amps, clock: t, pourT: 0 });
        raf = requestAnimationFrame(tick);
        return;
      }
      const pourT = t - drainMs;
      if (pourT >= pourEnd) {
        levelsRef.current = finalFrame.levels;
        setFrame(finalFrame);
        return;
      }
      const states = plans.map((plan) => pourAt(plan, pourT));
      const levels = states.map((s) => s.level);
      levelsRef.current = levels;
      setFrame({ phase: "pour", levels, amps: states.map((s) => s.amp), clock: t, pourT });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [seen, reduced, pour, plans, finalFrame]);

  const view = reduced ? finalFrame : frame;
  const live = reduced || view.phase !== "idle";

  /** The count a band shows now: its number once the water reaches it, zero before and while draining. */
  const shown = (index: number, key: BandKey): number => {
    const row = rows[index];
    if (!row) return 0;
    if (view.phase === "done") return row[key];
    if (view.phase !== "pour") return 0;
    const band = plans[index]?.bands.find((b) => b.key === key);
    return band && band.start <= view.pourT ? row[key] : 0;
  };

  const choose = (cm: number) => {
    if (cm === selectedCm) return;
    setPicked(cm);
    if (seen && !reduced) setPour((p) => ({ id: p.id + 1, drain: true }));
  };

  const y = (count: number) => HEAD + PLOT_H - (count / top) * PLOT_H;
  const n = groundTruthCount;

  return (
    <figure className={cn("m-0 flex flex-col gap-4", className)} data-slot="contingency-pour">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="radiogroup" aria-label="Threshold" className="flex gap-1">
          {rows.map((r) => {
            const on = r.thresholdCm === selectedCm;
            return (
              <button
                key={r.thresholdCm}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => choose(r.thresholdCm)}
                onKeyDown={(event) => {
                  const at = rows.findIndex((row) => row.thresholdCm === selectedCm);
                  const step =
                    event.key === "ArrowRight" || event.key === "ArrowDown"
                      ? 1
                      : event.key === "ArrowLeft" || event.key === "ArrowUp"
                        ? -1
                        : 0;
                  if (step === 0 || at < 0) return;
                  event.preventDefault();
                  const next = rows[(at + step + rows.length) % rows.length];
                  if (!next) return;
                  choose(next.thresholdCm);
                  const group = event.currentTarget.parentElement;
                  const target = group?.querySelector<HTMLButtonElement>(
                    `[data-cm="${next.thresholdCm}"]`,
                  );
                  target?.focus();
                }}
                tabIndex={on ? 0 : -1}
                data-cm={r.thresholdCm}
                className={cn(
                  "num type-small rounded-control h-8 border px-3 font-medium transition-colors",
                  "focus-visible:ring-tide focus-visible:ring-2 focus-visible:outline-none",
                  on
                    ? "border-line-strong bg-well text-text"
                    : "text-text-2 hover:text-text border-transparent",
                )}
              >
                {r.thresholdCm} cm
              </button>
            );
          })}
        </div>
        <p className="num type-micro text-text-3">
          {n === null
            ? "Ground-truth count not served"
            : `n = ${n} sourced ${n === 1 ? "pin" : "pins"} in the window`}
        </p>
      </div>

      <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
        <div
          ref={plotRef}
          role="img"
          aria-label={describe(rows, n)}
          className="flex min-w-0 flex-1 gap-3"
        >
          <div className="flex shrink-0 flex-col gap-1">
            <div className="relative w-8" style={{ height: SVG_H }}>
              {ticks.map((tick) => (
                <span
                  key={tick}
                  className="num type-micro text-text-3 absolute right-0 -translate-y-1/2"
                  style={{ top: y(tick) }}
                >
                  {tick}
                </span>
              ))}
            </div>
            <span className="type-micro text-text-3 text-right">Count</span>
          </div>

          <div className="relative flex min-w-0 flex-1 flex-wrap justify-around gap-x-8 gap-y-6">
            <span
              aria-hidden="true"
              className="bg-line-strong pointer-events-none absolute inset-x-0 h-px"
              style={{ top: SVG_H - 1 }}
            />
            {rows.map((row, i) => {
              const plan = plans[i]!;
              const level = view.levels[i] ?? 0;
              const amp = view.amps[i] ?? 0;
              const on = row.thresholdCm === selectedCm;
              let k = -1;
              plan.bands.forEach((band, j) => {
                if (level > band.from + 1e-9) k = j;
              });
              const surface = k >= 0 ? plan.bands[k]! : null;
              const yLevel = y(level);
              const wave = Array.from({ length: SAMPLES + 1 }, (_, s) => {
                const x = (s / SAMPLES) * TANK_W;
                const phase =
                  (2 * Math.PI * x) / WAVELENGTH - 2 * Math.PI * WAVE_HZ * (view.clock / 1000);
                return [x, yLevel - amp * Math.sin(phase + i * 1.3)] as const;
              });
              const crest = wave
                .map(([x, yy], s) => `${s === 0 ? "M" : "L"}${x.toFixed(2)} ${yy.toFixed(2)}`)
                .join(" ");
              const water = `M0 ${SVG_H} L${crest.slice(1)} L${TANK_W} ${SVG_H} Z`;
              const tankId = `${uid}-tank-${i}`;
              const waterId = `${uid}-water-${i}`;
              return (
                <div key={row.thresholdCm} className="flex flex-col gap-2">
                  <div className="flex items-end gap-3" style={{ height: SVG_H }}>
                    <svg
                      width={TANK_W}
                      height={SVG_H}
                      viewBox={`0 0 ${TANK_W} ${SVG_H}`}
                      className="shrink-0 overflow-visible"
                      aria-hidden="true"
                    >
                      <defs>
                        <clipPath id={tankId}>
                          <rect x={0} y={0} width={TANK_W} height={SVG_H} rx={TANK_R} />
                        </clipPath>
                        <clipPath id={waterId}>
                          <path d={water} />
                        </clipPath>
                        {/* Glow filter for the water surface crest */}
                        <filter id={`${uid}-glow-${i}`} x="-50%" y="-50%" width="200%" height="200%">
                          <feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur" />
                          <feComposite in="SourceGraphic" in2="blur" operator="over" />
                        </filter>
                        {/* Shimmer gradient that animates across the water body */}
                        <linearGradient
                          id={`${uid}-shimmer-${i}`}
                          gradientUnits="userSpaceOnUse"
                          x1={-TANK_W}
                          x2={TANK_W * 2}
                          y1={0}
                          y2={0}
                        >
                          {Array.from({ length: SHIMMER_STRIPES * 2 + 1 }, (_, s) => {
                            const offset = s / (SHIMMER_STRIPES * 2);
                            const shimmerPhase = ((view.clock / 1200) + i * 0.4) % 1;
                            const adjustedOffset = (offset + shimmerPhase) % 1;
                            return (
                              <stop
                                key={s}
                                offset={adjustedOffset}
                                style={{
                                  stopColor: "var(--truth)",
                                  stopOpacity: s % 2 === 0 ? 0 : 0.12,
                                }}
                              />
                            );
                          })}
                        </linearGradient>
                        {plan.bands.map((band) => (
                          <linearGradient
                            key={band.key}
                            id={`${uid}-g-${i}-${band.key}`}
                            gradientUnits="userSpaceOnUse"
                            x1={0}
                            x2={0}
                            y1={y(band.to)}
                            y2={y(band.from)}
                          >
                            <stop offset="0" style={{ stopColor: band.colour, stopOpacity: 1 }} />
                            <stop offset="0.4" style={{ stopColor: band.colour, stopOpacity: 0.85 }} />
                            <stop offset="1" style={{ stopColor: band.colour, stopOpacity: 0.55 }} />
                          </linearGradient>
                        ))}
                      </defs>
                      {/* Glass tank background with subtle inner glow */}
                      <rect
                        x={0.5}
                        y={0.5}
                        width={TANK_W - 1}
                        height={SVG_H - 1}
                        rx={TANK_R}
                        style={{ fill: "var(--well)", fillOpacity: 0.55 }}
                      />
                      {/* Subtle inner highlight for glass effect */}
                      <rect
                        x={3}
                        y={3}
                        width={TANK_W - 6}
                        height={SVG_H - 6}
                        rx={TANK_R - 2}
                        fill="none"
                        strokeWidth={0.5}
                        style={{ stroke: "var(--text)", strokeOpacity: 0.06 }}
                      />
                      {ticks.slice(1).map((tick) => (
                        <line
                          key={tick}
                          x1={10}
                          x2={TANK_W - 4}
                          y1={y(tick)}
                          y2={y(tick)}
                          strokeDasharray="2 4"
                          style={{ stroke: "var(--line)" }}
                        />
                      ))}
                      <g clipPath={`url(#${tankId})`} opacity={on ? 1 : 0.75}>
                        <g clipPath={`url(#${waterId})`}>
                          {plan.bands.map((band, j) => {
                            if (j > k) return null;
                            const yTop = j === k ? 0 : y(band.to);
                            return (
                              <rect
                                key={band.key}
                                x={0}
                                y={yTop}
                                width={TANK_W}
                                height={Math.max(0, y(band.from) - yTop)}
                                style={{ fill: `url(#${uid}-g-${i}-${band.key})` }}
                              />
                            );
                          })}
                          {/* Shimmer overlay on water body */}
                          {level > 0.1 ? (
                            <rect
                              x={0}
                              y={yLevel}
                              width={TANK_W}
                              height={Math.max(0, SVG_H - yLevel)}
                              style={{
                                fill: `url(#${uid}-shimmer-${i})`,
                                mixBlendMode: "overlay",
                              }}
                            />
                          ) : null}
                          {plan.bands.map((band, j) =>
                            j < k ? (
                              <line
                                key={band.key}
                                x1={0}
                                x2={TANK_W}
                                y1={y(band.to)}
                                y2={y(band.to)}
                                style={{ stroke: "var(--ink)", strokeOpacity: 0.35 }}
                              />
                            ) : null,
                          )}
                        </g>
                        {/* Glowing water surface crest */}
                        {surface ? (
                          <g filter={amp > 0.5 ? `url(#${uid}-glow-${i})` : undefined}>
                            <path
                              d={crest}
                              fill="none"
                              strokeWidth={amp > 1 ? 2.5 : 2}
                              strokeLinecap="round"
                              style={{
                                stroke: surface.colour,
                                strokeOpacity: Math.min(1, 0.7 + amp / CREST_PX),
                              }}
                            />
                          </g>
                        ) : null}
                        {/* Bubble particles rising during pour */}
                        {view.phase === "pour" && level > 0.5
                          ? Array.from({ length: PARTICLE_COUNT }, (_, p) => {
                              const seed = (i * PARTICLE_COUNT + p) * 7919;
                              const px = 6 + ((seed * 13) % (TANK_W - 12));
                              const speed = 0.6 + ((seed * 17) % 100) / 100;
                              const size = 1.2 + ((seed * 23) % 100) / 80;
                              const phase = ((view.pourT / (800 * speed)) + (seed % 100) / 100) % 1;
                              const py = yLevel + (SVG_H - yLevel) * (1 - phase);
                              const bubbleOpacity = phase < 0.15 ? phase / 0.15 : phase > 0.85 ? (1 - phase) / 0.15 : 0.5;
                              if (py > SVG_H || py < yLevel) return null;
                              return (
                                <circle
                                  key={p}
                                  cx={px}
                                  cy={py}
                                  r={size}
                                  style={{
                                    fill: "none",
                                    stroke: surface?.colour ?? "var(--text)",
                                    strokeWidth: 0.5,
                                    strokeOpacity: bubbleOpacity,
                                  }}
                                />
                              );
                            })
                          : null}
                      </g>
                      {ticks.slice(1).map((tick) => (
                        <line
                          key={tick}
                          x1={0}
                          x2={8}
                          y1={y(tick)}
                          y2={y(tick)}
                          style={{ stroke: "var(--line-strong)" }}
                        />
                      ))}
                      {/* Tank border with glow when active */}
                      <rect
                        x={0.5}
                        y={0.5}
                        width={TANK_W - 1}
                        height={SVG_H - 1}
                        rx={TANK_R}
                        fill="none"
                        strokeWidth={on ? 1.5 : 1}
                        style={{ stroke: on ? "var(--tide)" : "var(--line-strong)" }}
                      />
                      {on && level > 0.1 ? (
                        <rect
                          x={0}
                          y={0}
                          width={TANK_W}
                          height={SVG_H}
                          rx={TANK_R}
                          fill="none"
                          strokeWidth={2}
                          style={{
                            stroke: "var(--tide)",
                            strokeOpacity: 0.2 + 0.15 * Math.sin(view.clock / 600),
                          }}
                        />
                      ) : null}
                    </svg>
                    <ul className="flex flex-col gap-1.5 pb-1">
                      {[...BANDS].reverse().map((band) => {
                        const value = shown(i, band.key);
                        return (
                          <li key={band.key} className="flex items-center gap-2">
                            <span
                              className="size-2.5 shrink-0 rounded-[3px]"
                              style={{ background: band.colour }}
                            />
                            <span className="num type-small text-text w-7 text-right font-medium">
                              <NumberFlow value={value} locales="en-IN" />
                            </span>
                            <span className="type-micro text-text-2 whitespace-nowrap">
                              {plural(row[band.key], band)}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                  <div className="flex flex-col" style={{ width: TANK_W }}>
                    <span
                      className={cn(
                        "num type-small text-center",
                        on ? "text-text font-semibold" : "text-text-2",
                      )}
                    >
                      {row.thresholdCm} cm
                    </span>
                    <span className="num type-micro text-text-3 text-center whitespace-nowrap">
                      CSI {row.csi === null ? "n/a" : row.csi.toFixed(2)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {selected ? <Readout row={selected} live={live} /> : null}
      </div>

      <figcaption className="type-micro text-text-2 flex flex-wrap items-center gap-x-5 gap-y-1">
        {BANDS.map((band) => (
          <span key={band.key} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="size-2.5 shrink-0 rounded-[3px]"
              style={{ background: band.colour }}
            />
            <span className="text-text">{band.label}</span>
            <span className="text-text-3">{band.meaning}</span>
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

const SCORES = [
  { key: "csi", label: "CSI", hint: "higher is better" },
  { key: "pod", label: "POD", hint: "higher is better" },
  { key: "far", label: "FAR", hint: "lower is better" },
] as const;

function Readout({ row, live }: { row: PourColumn; live: boolean }) {
  const pins = row.hits + row.misses;
  return (
    <div
      aria-live="polite"
      data-slot="contingency-readout"
      className="rounded-panel border-line bg-well flex shrink-0 flex-col gap-3 border p-4 lg:w-80"
    >
      <p className="type-small text-text-2">
        At <span className="num text-text font-medium">{row.thresholdCm} cm</span>
      </p>
      <dl className="grid grid-cols-3 gap-3">
        {SCORES.map((s) => {
          const value = row[s.key];
          return (
            <div key={s.key} className="flex flex-col gap-0.5">
              <dt className="type-micro text-text-2">{s.label}</dt>
              <dd className="num font-display text-h2 tracking-display text-text font-semibold">
                {value === null ? (
                  <span className="text-text-3" title="No denominator">
                    n/a
                  </span>
                ) : (
                  <NumberFlow
                    value={live ? value : 0}
                    locales="en-IN"
                    format={{ minimumFractionDigits: 2, maximumFractionDigits: 2 }}
                  />
                )}
              </dd>
              <dd className="type-micro text-text-3">{s.hint}</dd>
            </div>
          );
        })}
      </dl>
      <p className="num type-small text-text">
        {row.hits} of {pins} {pins === 1 ? "pin" : "pins"} found, {row.falseAlarms}{" "}
        {row.falseAlarms === 1 ? "false alarm" : "false alarms"}
      </p>
      <p className="num type-micro text-text-2">
        {row.medianLeadMin === null
          ? "No pin flagged before it was logged"
          : `Median lead ${row.medianLeadMin.toFixed(0)} min before the log`}
      </p>
    </div>
  );
}

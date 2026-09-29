"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { AppShell } from "@/components/varuna/app-shell";
import { BundleCard, type BundleKind, type BundleSummary } from "@/components/varuna/bundle-card";
import { CycleBudgetBar, STAGE_IDS, type StageTiming } from "@/components/varuna/cycle-budget-bar";
import { CycleLogState, type CycleLogRow } from "@/components/varuna/cycle-log";
import { EmptyState } from "@/components/varuna/empty-state";
import { PageHeader } from "@/components/varuna/page-header";
import { Panel } from "@/components/varuna/panel";
import { PanelErrorBoundary } from "@/components/varuna/panel-error-boundary";
import { RADAR_FRAMES_MEMBER } from "@/components/varuna/radar-preview";
import { ReplayPanel } from "@/components/varuna/replay-panel";
import { Skeleton } from "@/components/varuna/skeleton";
import {
  StormDesigner,
  type DesignStormHyetograph,
  type StormCell,
} from "@/components/varuna/storm-designer";
import { useReplayBundles, useReplayControls, type ReplayBundle } from "@/lib/api";
import { useRun } from "@/lib/api/queries";
import { useRadarPreview, useReplayCycleLog } from "@/lib/api/replay";
import { bundleWindowLabel, formatIst } from "@/lib/format";
import { navItem } from "@/lib/nav";
import { useReplayStore } from "@/lib/stores/replay";
import { formatIstTime } from "@/lib/stores/time";

/** A finite number, or undefined: the radar index is read as loose JSON, so nothing is assumed. */
function finite(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/**
 * The convective cells of `GET /v1/replay/bundles/{id}/radar`, in the table's own units, in birth
 * order - the manifest lists them in the order they were drawn, which read 08:05, 06:08, 07:54.
 *
 * The API converts them (minutes to IST, the design CRS to lon/lat, components to a speed, and the
 * peak through `intensity_scale`), so nothing is recomputed here. A row that is not complete is
 * dropped rather than shown with a blank cell.
 */
export function toStormCells(value: unknown): StormCell[] {
  if (!Array.isArray(value)) return [];
  const cells: StormCell[] = [];
  for (const row of value) {
    if (typeof row !== "object" || row === null) continue;
    const cell = row as Record<string, unknown>;
    const lifetimeMin = finite(cell.lifetime_min);
    const startLat = finite(cell.start_lat);
    const startLon = finite(cell.start_lon);
    const velocityMs = finite(cell.velocity_ms);
    const sigmaKm = finite(cell.sigma_km);
    const peakMmH = finite(cell.peak_mm_h);
    if (typeof cell.id !== "string" || typeof cell.birth !== "string") continue;
    if (
      lifetimeMin === undefined ||
      startLat === undefined ||
      startLon === undefined ||
      velocityMs === undefined ||
      sigmaKm === undefined ||
      peakMmH === undefined
    ) {
      continue;
    }
    cells.push({
      id: cell.id,
      birth: cell.birth,
      lifetimeMin,
      startLat,
      startLon,
      velocityMs,
      sigmaKm,
      peakMmH,
    });
  }
  return cells.sort(
    (a, b) => Date.parse(a.birth) - Date.parse(b.birth) || a.id.localeCompare(b.id),
  );
}

/** The Chicago hyetograph a design storm carries instead of cells; absent on a replay. */
function toDesignStorm(value: unknown): DesignStormHyetograph | undefined {
  if (typeof value !== "object" || value === null) return undefined;
  const storm = value as Record<string, unknown>;
  const stepMin = finite(storm.step_min);
  const totalDepthMm = finite(storm.total_depth_mm);
  const peakPositionR = finite(storm.peak_position_r);
  const blocksMmH = Array.isArray(storm.blocks_mm_h)
    ? storm.blocks_mm_h.map(finite).filter((block): block is number => block !== undefined)
    : [];
  if (stepMin === undefined || totalDepthMm === undefined || peakPositionR === undefined) {
    return undefined;
  }
  if (blocksMmH.length === 0) return undefined;
  return { stepMin, blocksMmH, totalDepthMm, peakPositionR };
}

/** "mumbai" is how the manifest names the city; the card says it the way a person would. */
function cityLabel(city: string): string {
  return city ? city.charAt(0).toUpperCase() + city.slice(1) : "Unknown city";
}

/**
 * The card's one line. The manifest's own notes run to paragraphs, so they sit behind Details;
 * the card keeps the honesty claim itself - what is synthetic, and what is sourced.
 */
export function bundleNote(bundle: ReplayBundle): string {
  if (!bundle.built) return `Missing ${bundle.missing_members.join(", ")}.`;
  if (bundle.label === "Design storm") return "Synthetic scenario, not a recorded event.";
  const synthetic = "rain, radar, gauges, tide, traffic and reports synthetic.";
  return bundle.ground_truth_n > 0
    ? `${bundle.ground_truth_n} sourced pins; ${synthetic}`
    : `${synthetic.charAt(0).toUpperCase()}${synthetic.slice(1)}`;
}

/** A design storm has no date (its manifest says so), so its card does not print one. */
export function bundleWindow(bundle: Pick<ReplayBundle, "label" | "t0" | "t1">): string {
  if (bundle.label === "Design storm") {
    return `${formatIst(bundle.t0)} to ${formatIst(bundle.t1)} IST, nominal clock`;
  }
  return bundleWindowLabel(bundle.t0, bundle.t1);
}

/**
 * The demo first: the reconstructed replay, then the Mumbai design storm, then the other cities.
 * The API lists bundles by id, which put the Chennai design storm first on the page.
 */
export function bundleOrder(a: ReplayBundle, b: ReplayBundle): number {
  const rank = (bundle: ReplayBundle) =>
    (bundle.label === "Reconstructed replay" ? 0 : 2) + (bundle.city === "mumbai" ? 0 : 1);
  return rank(a) - rank(b) || a.id.localeCompare(b.id);
}

function toCard(bundle: ReplayBundle): BundleSummary {
  return {
    id: bundle.id,
    kind: bundle.label as BundleKind,
    city: cityLabel(bundle.city),
    window: bundleWindow(bundle),
    note: bundleNote(bundle),
    status: bundle.built
      ? `${bundle.baked_cycles} of ${bundle.total_cycles} cycles baked`
      : undefined,
    available: bundle.built,
    t0: bundle.t0,
    t1: bundle.t1,
    simTime: bundle.t0,
  };
}

/** One cited source of a manifest, read loosely because the summary row does not type it. */
interface BundleSource {
  name: string;
  url?: string;
  note?: string;
}

function bundleSources(bundle: ReplayBundle): BundleSource[] {
  const raw = (bundle as { sources?: unknown }).sources;
  if (!Array.isArray(raw)) return [];
  const out: BundleSource[] = [];
  for (const item of raw) {
    if (typeof item !== "object" || item === null) continue;
    const row = item as Record<string, unknown>;
    if (typeof row.name !== "string") continue;
    out.push({
      name: row.name,
      url: typeof row.url === "string" && /^https?:\/\//.test(row.url) ? row.url : undefined,
      note: typeof row.note === "string" ? row.note : undefined,
    });
  }
  return out;
}

/** The manifest's basis text - the calibration of a replay, the construction of a design storm. */
function bundleBasis(bundle: ReplayBundle): string | null {
  const row = bundle as { calibration_basis?: unknown; design_storm_basis?: unknown };
  const text = row.calibration_basis ?? row.design_storm_basis;
  return typeof text === "string" && text.trim() ? text.trim() : null;
}

const SUMMARY_CLASS =
  "type-small text-text-2 hover:text-text focus-visible:ring-tide rounded-control cursor-pointer focus-visible:ring-2 focus-visible:outline-none";

/** Sources, notes and basis of the selected bundle, for the judge who asks where a number came from. */
function BundleDetails({ bundle }: { bundle: ReplayBundle }) {
  const sources = bundleSources(bundle);
  const basis = bundleBasis(bundle);
  if (bundle.synthetic_notes.length === 0 && sources.length === 0 && !basis) return null;
  return (
    <details className="border-line mt-3 border-t pt-3">
      <summary className={SUMMARY_CLASS}>Details: sources and notes for {bundle.id}</summary>
      <div className="mt-3 grid gap-4 lg:grid-cols-2">
        {bundle.synthetic_notes.length > 0 ? (
          <section aria-label="What is synthetic" className="space-y-1.5">
            <h3 className="type-micro text-text font-medium">What is synthetic</h3>
            <ul className="space-y-1.5">
              {bundle.synthetic_notes.map((line) => (
                <li key={line} className="type-micro text-text-3 max-w-[72ch]">
                  {line}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        <div className="space-y-4">
          {sources.length > 0 ? (
            <section aria-label="Sources" className="space-y-1.5">
              <h3 className="type-micro text-text font-medium">Sources</h3>
              <ul className="space-y-1.5">
                {sources.map((source) => (
                  <li key={`${source.name}-${source.url ?? ""}`} className="type-micro">
                    {source.url ? (
                      <a
                        href={source.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-tide focus-visible:ring-tide rounded-control underline underline-offset-2 outline-none focus-visible:ring-2"
                      >
                        {source.name}
                      </a>
                    ) : (
                      <span className="text-text-2">{source.name}</span>
                    )}
                    {source.note ? (
                      <span className="text-text-3 block max-w-[72ch]">{source.note}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {basis ? (
            <details>
              <summary className={SUMMARY_CLASS}>
                {bundle.label === "Design storm" ? "How the storm is built" : "Calibration basis"}
              </summary>
              <p className="type-micro text-text-3 mt-1.5 max-w-[72ch] whitespace-pre-line">
                {basis}
              </p>
            </details>
          ) : null}
        </div>
      </div>
    </details>
  );
}

/** The row the clock is in: the newest run at or before the clock, else the first run. */
export function rowAtClock(rows: readonly CycleLogRow[], simTime: string): CycleLogRow | null {
  const at = Date.parse(simTime);
  let found: CycleLogRow | null = null;
  for (const row of rows) {
    const t = Date.parse(row.time);
    if (!Number.isNaN(t) && !Number.isNaN(at) && t <= at + 1_000) found = row;
  }
  return found ?? rows[0] ?? null;
}

/** `stage_ms` of a run as the budget bar's stages; a stage the run did not report is null. */
export function stageTimings(stageMs: Record<string, number> | undefined): StageTiming[] {
  return STAGE_IDS.map((id) => {
    const ms = stageMs?.[id];
    return { id, ms: typeof ms === "number" && Number.isFinite(ms) ? ms : null };
  });
}

/** The budget bar of one run of the log: its own per-stage split from `GET /v1/runs/{id}`. */
function RunStages({ row }: { row: CycleLogRow | null }) {
  const run = useRun(row?.id ?? null);
  if (!row) return <CycleBudgetBar />;
  if (run.isPending) return <Skeleton className="h-16" />;
  if (run.isError) {
    return (
      <p role="status" className="type-micro text-text-2">
        Stage timings for {formatIstTime(row.time)} did not load: {run.error.message}
      </p>
    );
  }
  const total = (run.data as { total_ms?: unknown }).total_ms;
  return (
    <div className="space-y-1">
      <p className="type-micro text-text-2">
        Stages of the <span className="num">{formatIstTime(row.time)}</span> run
      </p>
      <CycleBudgetBar
        stages={stageTimings(run.data.stage_ms)}
        totalMs={typeof total === "number" && Number.isFinite(total) ? total : undefined}
      />
    </div>
  );
}

/**
 * Smriti, replay control and storm designer (SPEC.md section 7.8): pick a bundle, drive the same
 * clock the console uses, read the cycle log, and look at the storm the bundle was built from. The
 * cards are what `GET /v1/replay/bundles` reports, never a hard-coded list. Editing the storm is a
 * pilot feature; the demo storm is read-only and seeded.
 */
export function ReplayScreen() {
  const bundleId = useReplayStore((s) => s.bundleId);
  const simTime = useReplayStore((s) => s.simTime);

  const bundles = useReplayBundles();
  const controls = useReplayControls();

  const cards = useMemo(
    () => [...(bundles.data ?? [])].sort(bundleOrder).map(toCard),
    [bundles.data],
  );
  const log = useReplayCycleLog(bundleId);
  const cycles = log.rows;

  // The log follows the clock until a row is picked; a pick holds for the bundle it was made in.
  const [picked, setPicked] = useState<{ bundleId: string; runId: string } | null>(null);
  const pickedRow =
    picked && picked.bundleId === bundleId
      ? (cycles.find((row) => row.id === picked.runId) ?? null)
      : null;
  const shownRow = pickedRow ?? rowAtClock(cycles, simTime);

  const selected = cards.find((card) => card.id === bundleId);
  // The designer needs the manifest row itself, not the card: the radar preview decides whether to
  // fetch from the members `make bundle` has actually written.
  const selectedBundle = bundles.data?.find((bundle) => bundle.id === bundleId);

  // The same index the preview animates, so the cell table costs a cache hit and never a second
  // request: it carries the storm the frames were generated from (SPEC.md section 7.8).
  const hasCube =
    (selectedBundle?.built ?? false) &&
    !(selectedBundle?.missing_members ?? []).includes(RADAR_FRAMES_MEMBER);
  const radar = useRadarPreview(selected?.id ?? bundleId, { enabled: hasCube });
  const stormCells = useMemo(() => toStormCells(radar.data?.cells), [radar.data]);
  const designStorm = useMemo(() => toDesignStorm(radar.data?.design_storm), [radar.data]);

  const handleSelect = (bundle: BundleSummary) => {
    controls.selectBundle(bundle.id, {
      t0: bundle.t0,
      t1: bundle.t1,
      simTime: bundle.simTime,
    });
  };

  return (
    <AppShell>
      <div className="h-full min-h-0 overflow-y-auto">
        <div className="mx-auto flex max-w-[1440px] flex-col gap-6 p-6">
          {/* The chip is the selected bundle's own label, so the page's first statement about what
              is replaying is "Reconstructed replay" or "Design storm" (section 6.8, rule 7). */}
          <PageHeader
            title={navItem("replay").label}
            screen={navItem("replay")}
            honesty={selected?.kind}
          />

          <PanelErrorBoundary title="Bundles">
            <Panel title="Bundles">
              {bundles.isPending ? (
                <div className="grid gap-3 lg:grid-cols-3">
                  <Skeleton className="h-32" />
                  <Skeleton className="h-32" />
                  <Skeleton className="h-32" />
                </div>
              ) : cards.length > 0 ? (
                <>
                  <ul aria-label="Bundles" className="grid gap-3 lg:grid-cols-3">
                    {cards.map((card) => (
                      <li key={card.id} className="min-w-0">
                        <BundleCard
                          bundle={card}
                          selected={card.id === bundleId}
                          onSelect={handleSelect}
                        />
                      </li>
                    ))}
                  </ul>
                  {selectedBundle ? <BundleDetails bundle={selectedBundle} /> : null}
                </>
              ) : (
                <EmptyState
                  title={bundles.isError ? "The bundle list is unavailable" : "No bundles yet"}
                  description={
                    bundles.isError
                      ? bundles.error.message
                      : "Run make bundle BUNDLE=MUM-2019-07-02 to generate the reconstructed replay, then reload."
                  }
                  action={
                    bundles.isError ? (
                      <Button variant="outline" onClick={() => void bundles.refetch()}>
                        Try again
                      </Button>
                    ) : undefined
                  }
                />
              )}
            </Panel>
          </PanelErrorBoundary>

          <div className="grid min-h-0 gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
            <PanelErrorBoundary title="Clock">
              <div className="min-w-0">
                <ReplayPanel variant="page" />
              </div>
            </PanelErrorBoundary>

            <PanelErrorBoundary title="Cycle log">
              <Panel title="Cycle log" description="Pick a cycle to see its stage timings.">
                <div className="space-y-4">
                  {log.isPending ? <Skeleton className="h-16" /> : <RunStages row={shownRow} />}
                  <CycleLogState
                    bundleId={bundleId}
                    log={log}
                    selectedId={shownRow?.id ?? null}
                    onSelect={(row) => setPicked({ bundleId, runId: row.id })}
                  />
                </div>
              </Panel>
            </PanelErrorBoundary>
          </div>

          <PanelErrorBoundary title="Storm designer">
            <Panel
              title="Storm designer"
              description="The cells that built this storm, and its radar."
            >
              {bundles.isError ? (
                <EmptyState
                  title="The storm did not load"
                  description="The bundle list is unavailable, so there is no storm to show."
                />
              ) : (
                <StormDesigner
                  cells={stormCells}
                  designStorm={designStorm}
                  bundleId={selected?.id ?? bundleId}
                  built={selectedBundle?.built ?? false}
                  missingMembers={selectedBundle?.missing_members ?? []}
                  loading={bundles.isPending || (hasCube && radar.isPending)}
                  cellsError={radar.isError ? radar.error.message : null}
                />
              )}
            </Panel>
          </PanelErrorBoundary>
        </div>
      </div>
    </AppShell>
  );
}

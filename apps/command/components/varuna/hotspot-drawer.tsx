"use client";

import NumberFlow from "@number-flow/react";
import { ExternalLink, X } from "lucide-react";
import { motion } from "motion/react";
import Link from "next/link";
import { useEffect, useRef } from "react";

import type { Hotspot } from "@/lib/api/hotspots";
import { MAX_CLEANED_SEGMENTS } from "@/lib/api/whatif";
import { Button } from "@/components/ui/button";
import { DepthChip } from "@/components/varuna/depth-chip";
import { EmptyState } from "@/components/varuna/empty-state";
import { FanChart, type FanChartPoint } from "@/components/varuna/fan-chart";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { DUR, EASE_UI } from "@/lib/motion";
import { PROFILE_THRESHOLD_CM, type PassabilityProfile } from "@/lib/ramps";
import { useRunStore } from "@/lib/stores/run";
import { formatIstTime } from "@/lib/stores/time";

export interface HotspotDrawerProps {
  hotspot: Hotspot | null;
  /** Current step on the time bar; the headline number is the depth at this moment. */
  step: number;
  /** Minutes per step, for the lead-time axis. */
  stepMin?: number;
  /** Valid time of each step, so "safe until" is a clock time and not a step number. */
  validTs?: readonly string[];
  onClose?: () => void;
  /**
   * Scroll to "Why this junction floods" when this changes: the segment popover's "why" link
   * (task P6.9) opens the drawer at that section rather than at the top.
   */
  focusWhyKey?: string | number | null;
}

/**
 * The attribution cell for a row that carries neither a pipe id nor a road segment: what is true,
 * in the words `STREET_NOT_RECORDED` uses for a street, and never "unnamed".
 */
export const PIPE_NOT_RECORDED = "Pipe not recorded";

/** The order the safe-until table reads in: lightest vehicle first, rescue last. */
const PROFILES: readonly { key: PassabilityProfile; label: string }[] = [
  { key: "two-wheeler", label: "Two-wheeler" },
  { key: "car", label: "Car" },
  { key: "bus", label: "Bus or truck" },
  { key: "ambulance", label: "Ambulance" },
  { key: "pedestrian", label: "Pedestrian" },
];

/**
 * The first step at which a profile's threshold is exceeded, or -1 if it never is.
 *
 * "Safe until" is the honest phrasing of it: not a promise the street is dry, but the last time
 * before this run expects it to stop carrying that vehicle.
 */
function firstUnsafeStep(depthCm: readonly number[], thresholdCm: number): number {
  return depthCm.findIndex((cm) => cm > thresholdCm);
}

/**
 * The road segments the "Clean in what-if" link preselects (task P7.11).
 *
 * When the run carries an attribution these are the streets the *ranked pipes* run under, in
 * rank order and de-duplicated - several pipes in series often share one street, so fourteen
 * pipes can be four segments. That is the deep link P7.11 asks for: the lab opens on what
 * attribution actually named, not on the junction's whole address book.
 *
 * With no ranking it falls back to the junction's own segments in the register's order, which is
 * what the drawer did before anything could rank pipes. The caption says which of the two
 * happened, because "the streets attribution named" and "every street at this junction" are
 * different claims (rule 6).
 */
function cleanedSegments(hotspot: Hotspot): { ids: string[]; fromAttribution: boolean } {
  const ranked: string[] = [];
  for (const row of hotspot.attribution) {
    if (row.segmentId && !ranked.includes(row.segmentId)) ranked.push(row.segmentId);
  }
  if (ranked.length > 0) {
    return { ids: ranked.slice(0, MAX_CLEANED_SEGMENTS), fromAttribution: true };
  }
  return { ids: hotspot.segmentIds.slice(0, MAX_CLEANED_SEGMENTS), fromAttribution: false };
}

/**
 * The what-if lab, opened on those segments.
 *
 * The ids are road segments, which is the vocabulary `POST /v1/whatif` cleans on; the run travels
 * with them because a what-if is a question about one cycle, and the lab's own default is the
 * newest run rather than the one the operator is looking at.
 */
function cleanInWhatIfHref(hotspot: Hotspot, runId: string | null, ids: string[]) {
  // A `UrlObject` rather than a template string: typed routes reject an interpolated path, and
  // the query is encoded for us, which matters because a hotspot's name carries commas.
  const query: Record<string, string> = {
    segments: ids.join(","),
    from: hotspot.name,
  };
  if (runId) query.run = runId;
  return { pathname: "/whatif", query } as const;
}

export function HotspotDrawer({
  hotspot,
  step,
  stepMin = 5,
  validTs = [],
  onClose,
  focusWhyKey = null,
}: HotspotDrawerProps) {
  const reducedMotion = usePrefersReducedMotion();
  const whyRef = useRef<HTMLElement>(null);
  // A jump, not a smooth scroll: section 8 has no row for one, and this is navigation (M23).
  useEffect(() => {
    if (focusWhyKey == null) return;
    whyRef.current?.scrollIntoView({ block: "start" });
    whyRef.current?.focus({ preventScroll: true });
  }, [focusWhyKey]);
  // The console publishes the run it is showing here (the top bar's run stamp reads the same
  // store), so the deep link can carry the cycle without the drawer being handed it.
  const runId = useRunStore((s) => s.currentRun?.run_id ?? null);
  if (!hotspot) return null;

  const now = hotspot.depthCm[Math.min(step, hotspot.depthCm.length - 1)] ?? 0;
  const cleaned = cleanedSegments(hotspot);
  const cleanedCount = cleaned.ids.length;

  // The junction's band is the streets' (ADR-0076): its own Twin level with the member spread of
  // its registered segments either side. A run baked before the band existed carries none, and
  // then the series is drawn flat - p10 = p50 = p90 - rather than invented.
  const hasBand =
    Array.isArray(hotspot.depthP10Cm) &&
    Array.isArray(hotspot.depthP90Cm) &&
    hotspot.depthP10Cm.length === hotspot.depthCm.length &&
    hotspot.depthP90Cm.length === hotspot.depthCm.length;
  const points: FanChartPoint[] = hotspot.depthCm.map((cm, i) => ({
    validTs: validTs[i] ?? "",
    leadMin: i * stepMin,
    p10: hasBand ? (hotspot.depthP10Cm?.[i] ?? cm) : cm,
    p50: cm,
    p90: hasBand ? (hotspot.depthP90Cm?.[i] ?? cm) : cm,
  }));

  return (
    <motion.aside
      aria-label={`${hotspot.name} forecast`}
      initial={reducedMotion ? false : { x: 24, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      transition={reducedMotion ? { duration: 0 } : { duration: DUR.drawerSlide, ease: EASE_UI }}
      className="bg-deep flex h-full min-h-0 flex-col overflow-y-auto"
    >
      <header className="border-line flex items-start justify-between gap-3 border-b p-4">
        <div className="min-w-0">
          <h2 className="type-h3 font-display text-text">{hotspot.name}</h2>
          <p className="type-micro text-text-3 mt-1">
            Rank {hotspot.rank}
            {hotspot.ward ? ` · ward ${hotspot.ward}` : ""}
            {hotspot.isSink ? " · terrain sink" : ""}
          </p>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
          <X size={16} strokeWidth={1.75} />
        </Button>
      </header>

      <section className="border-line border-b p-4">
        <div className="flex items-baseline gap-2">
          {/* Motion M4: the number rolls as the operator scrubs. */}
          <span className="num font-display text-display text-text leading-none">
            {reducedMotion ? (
              Math.round(now)
            ) : (
              <NumberFlow value={Math.round(now)} respectMotionPreference={false} />
            )}
          </span>
          <span className="type-h3 text-text-2">cm</span>
        </div>
        <p className="type-small text-text-2 mt-2">
          p50 at {validTs[step] ? formatIstTime(validTs[step]) : `+${step * stepMin} min`} · peaks
          at {Math.round(hotspot.peakDepthCm)} cm
          {hotspot.peakTs ? ` at ${formatIstTime(hotspot.peakTs)}` : ""} (+{hotspot.timeToPeakMin}{" "}
          min)
        </p>
        {hotspot.minutesImpassable > 0 ? (
          <p className="type-small text-text-2 mt-1">
            Above 30 cm for {hotspot.minutesImpassable} min
            {hotspot.impassableFromTs ? `, from ${formatIstTime(hotspot.impassableFromTs)}` : ""}.
          </p>
        ) : (
          <p className="type-small text-text-3 mt-1">Stays below the 30 cm car threshold.</p>
        )}
      </section>

      <section className="border-line border-b p-4">
        <h3 className="type-small text-text font-medium">Depth over the forecast</h3>
        <div className="mt-2">
          <FanChart
            points={points}
            quantity="Depth"
            unit="cm"
            height={160}
            markerLeadMin={step * stepMin}
            markerLabel="now"
          />
        </div>
        <details className="type-micro text-text-3 mt-2">
          <summary className="text-text-2 cursor-pointer">Details</summary>
          <p className="mt-1">
            {hasBand
              ? `The line is the Twin's depth here; the band is the ensemble's p10 to p90 over the ${hotspot.bandSegments ?? 0} streets registered to this junction.`
              : hotspot.bandSegments === 0
                ? "One deterministic Twin run for this junction, so the band has no width: none of its streets is in this run's ensemble."
                : "One deterministic Twin run for this junction, so the band has no width: this run was baked before junctions carried the ensemble's band."}
          </p>
        </details>
      </section>

      <section className="border-line border-b p-4">
        <h3 className="type-small text-text font-medium">Safe until</h3>
        <table className="mt-2 w-full">
          <caption className="sr-only">
            The last forecast time before each vehicle can no longer pass {hotspot.name}
          </caption>
          <tbody className="divide-line divide-y">
            {PROFILES.map(({ key, label }) => {
              const threshold = PROFILE_THRESHOLD_CM[key];
              const unsafe = firstUnsafeStep(hotspot.depthCm, threshold);
              return (
                <tr key={key} className="h-8">
                  <th scope="row" className="type-small text-text-2 text-left font-normal">
                    {label}
                  </th>
                  <td className="num type-micro text-text-3 w-16 text-right">{threshold} cm</td>
                  <td className="num type-small text-text w-28 text-right">
                    {unsafe < 0 ? (
                      <span className="text-text-2">passable</span>
                    ) : unsafe === 0 ? (
                      <span className="text-text">already over</span>
                    ) : validTs[unsafe] ? (
                      formatIstTime(validTs[unsafe])
                    ) : (
                      `+${unsafe * stepMin} min`
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      <section className="border-line border-b p-4">
        <h3 className="type-small text-text font-medium">Exposure</h3>
        <dl className="mt-2 space-y-1.5">
          <div className="flex items-baseline justify-between gap-3">
            <dt className="type-small text-text-2">Nearest hospital</dt>
            <dd className="num type-small text-text min-w-0 truncate">
              {hotspot.exposure.nearestHospital
                ? `${hotspot.exposure.nearestHospital} · ${hotspot.exposure.nearestHospitalM} m`
                : "none within 300 m"}
            </dd>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <dt className="type-small text-text-2">Nearest station</dt>
            <dd className="num type-small text-text min-w-0 truncate">
              {hotspot.exposure.nearestStation
                ? `${hotspot.exposure.nearestStation} · ${hotspot.exposure.nearestStationM} m`
                : "none within 300 m"}
            </dd>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <dt className="type-small text-text-2">Road exposure weight</dt>
            <dd className="num type-small text-text">{hotspot.exposure.weight.toFixed(2)}</dd>
          </div>
        </dl>
      </section>

      <section
        ref={whyRef}
        tabIndex={-1}
        aria-labelledby="hotspot-why"
        className="border-line border-b p-4 focus-visible:outline-none"
      >
        <h3 id="hotspot-why" className="type-small text-text font-medium">
          Why this junction floods
        </h3>
        {/* Two states only, never three: a ranking with its combined effect, or an empty list
            with the reason the run recorded. Since P7.7 that reason is a *measured* refusal
            ("51 pipes were re-run; the best explains 0.003 cm") rather than ADR-0042's
            structural one, because `drain1d` is in the loop now and does see across segments. A
            row is only ever drawn for a pipe that cleared the floor. */}
        {hotspot.attribution.length > 0 ? (
          <>
            <table className="mt-2 w-full">
              <caption className="sr-only">
                Inferred pipes ranked by the depth each explains at {hotspot.name}
              </caption>
              <thead>
                <tr className="type-micro text-text-3">
                  <th scope="col" className="w-6 text-left font-normal">
                    #
                  </th>
                  <th scope="col" className="text-left font-normal">
                    Pipe
                  </th>
                  <th scope="col" className="w-14 text-right font-normal">
                    Blockage
                  </th>
                  <th scope="col" className="w-20 text-right font-normal">
                    Explains
                  </th>
                </tr>
              </thead>
              <tbody className="divide-line divide-y">
                {hotspot.attribution.map((row) => (
                  // A row from a run baked before P7.7 has no pipe id, only the road segment it
                  // was keyed on; it is named by that rather than left blank. A row with neither
                  // is kept - its depth is a measurement, and dropping it would leave a gap in
                  // the ranks - and says what is true. `||`, not `??`: the parser writes an
                  // absent pipe id as "".
                  <tr key={row.pipeId || row.segmentId || `rank-${row.rank}`} className="h-8">
                    <td className="num type-micro text-text-3">{row.rank}</td>
                    <td className="type-small text-text-2 min-w-0 truncate">
                      {row.pipeId || row.segmentId || PIPE_NOT_RECORDED}
                      {row.pipeId && row.segmentId ? (
                        <span className="type-micro text-text-3"> · {row.segmentId}</span>
                      ) : null}
                    </td>
                    <td className="num type-small text-text-2 text-right">{row.beta.toFixed(2)}</td>
                    <td className="num type-small text-text text-right">
                      {row.depthExplainedCm.toFixed(2)} cm
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {hotspot.attributionCombined ? (
              <p className="type-small text-text-2 mt-2">
                {/* The sign is printed, not assumed. Cleaning the pipes above a junction can
                    deliver more water to it than the cleaning takes away, and on the 08:40 cycle
                    Sion Circle's seven pipes do exactly that (-0.06 cm). Clamping it at zero
                    would hide a real hydraulic answer. */}
                Cleaning these <span className="num">{hotspot.attributionCombined.nCleaned}</span>{" "}
                pipes together:{" "}
                <span className="num">{hotspot.attributionCombined.depthBeforeCm.toFixed(1)}</span>{" "}
                to{" "}
                <span className="num">{hotspot.attributionCombined.depthAfterCm.toFixed(1)}</span>{" "}
                cm at the peak
                {hotspot.attributionCombined.depthExplainedCm < 0
                  ? ", deeper than before: cleaning upstream delivers more water than it removes"
                  : ""}
                .
              </p>
            ) : null}
            {hotspot.attributionCandidates ? (
              <p className="type-micro text-text-3 mt-2">
                <span className="num">{hotspot.attribution.length}</span> of{" "}
                <span className="num">{hotspot.attributionCandidates}</span> pipes within 5 upstream
                hops explain enough to be named.
              </p>
            ) : null}
            <details className="type-micro text-text-3 mt-2">
              <summary className="text-text-2 cursor-pointer">Details</summary>
              <p className="mt-1">
                {hotspot.attributionMethod ? `Measured on ${hotspot.attributionMethod}: ` : ""}the
                street depth is held at this run&rsquo;s forecast, so each figure is the water the
                drain takes off the junction and an upper bound on what a coupled re-run would
                remove. The drain graph is inferred.
              </p>
            </details>
          </>
        ) : (
          <EmptyState
            size="sm"
            className="mt-1"
            title="No pipe is named for this junction"
            description={
              hotspot.attributionLabel ??
              "This run carries no attribution, so nothing was measured for this junction."
            }
          />
        )}
      </section>

      <footer className="space-y-3 p-4">
        {/* The one action the emulator can actually take on this junction. "Dispatch pumps here"
            and "Show drains" are section 7.2's other two buttons and are not here: the endpoint
            has no pump-plan lever (P7.7) and the drains layer is a console toggle, so a button
            for either would be a control that does nothing. */}
        {cleanedCount > 0 ? (
          <div className="space-y-1">
            <Button
              variant="outline"
              className="w-full"
              render={<Link href={cleanInWhatIfHref(hotspot, runId, cleaned.ids)} />}
              nativeButton={false}
            >
              Clean in what-if
            </Button>
            <p className="type-micro text-text-3">
              {/* Which of the two sets travelled is stated, because "the streets attribution
                  named" and "every street at this junction" are different claims (rule 6). */}
              {cleaned.fromAttribution ? (
                <>
                  Opens the lab on the <span className="num">{cleanedCount}</span> street
                  {cleanedCount === 1 ? "" : "s"} the ranked pipes run under
                </>
              ) : hotspot.segmentIds.length > cleanedCount ? (
                <>
                  Opens the lab with the first <span className="num">{cleanedCount}</span> of this
                  junction&rsquo;s <span className="num">{hotspot.segmentIds.length}</span> road
                  segments
                </>
              ) : (
                <>
                  Opens the lab with this junction&rsquo;s{" "}
                  <span className="num">{cleanedCount}</span> road segment
                  {cleanedCount === 1 ? "" : "s"}
                </>
              )}
              {runId ? ", on this cycle" : ""}.
            </p>
            <details className="type-micro text-text-3">
              <summary className="text-text-2 cursor-pointer">Details</summary>
              <p className="mt-1">
                The lab&rsquo;s own cleaning runs on Flash-lite, which is element-wise per segment,
                so it moves these streets and no others; the ranking above is the hydraulic answer.
              </p>
            </details>
          </div>
        ) : (
          <p className="type-micro text-text-3">
            No road segments are recorded for this junction, so there is nothing to send to{" "}
            <span translate="no">Kalpana</span>, the what-if lab.
          </p>
        )}
        <div className="flex items-center justify-between gap-3">
          <DepthChip cm={hotspot.peakDepthCm} size="sm" showBand />
          {hotspot.sourceUrl ? (
            <a
              href={hotspot.sourceUrl}
              target="_blank"
              rel="noreferrer"
              className="type-micro text-tide focus-visible:ring-tide inline-flex items-center gap-1 underline underline-offset-2 focus-visible:ring-2 focus-visible:outline-none"
            >
              Chronic-spot source
              <ExternalLink size={12} strokeWidth={1.75} />
            </a>
          ) : (
            <span className="type-micro text-text-3">No public source recorded</span>
          )}
        </div>
      </footer>
    </motion.aside>
  );
}

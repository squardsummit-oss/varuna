"use client";

/**
 * The landing hero (SPEC.md 7.1, motions M1 and M2).
 *
 * Two acts. First a globe unrolls into a world map and settles on Mumbai (`GlobeIntro`), which
 * is the product's whole claim as a picture: global forecasting stops at 12 km, and the water
 * arrives at 30 m. Then it cross-fades into the real thing - the same read-only `FloodMap` the
 * console runs, against the same baked run, scrubbing three hours in eight seconds and looping.
 * The first thing a judge sees is the product working rather than a rendering of it.
 *
 * It pauses on hover (M1), holds still under reduced motion, and stops entirely while the tab is
 * hidden - a landing page that keeps a WebGL canvas animating in a background tab is a landing
 * page that drains a laptop before the demo starts.
 */

import Link from "next/link";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";

import { GlobeIntro } from "@/components/landing/globe-intro";
import { HeroFrames, type HeroFramesManifest } from "@/components/landing/hero-frames";
// **Loaded on demand, not in the landing page's first bundle.** `FloodMap` pulls in all of
// deck.gl, and the hero's opening seconds are an SVG globe that needs none of it - so shipping it
// up front cost the landing page its Largest Contentful Paint (3.5 s against a 2.5 s budget) and
// a Lighthouse performance score of 0.41. The import starts when the globe does, so the map is
// usually ready by the time the morph wants to hand over to it, and the handover already waits
// for both halves.
const FloodMap = dynamic(
  () => import("@/components/map/flood-map").then((m) => ({ default: m.FloodMap })),
  {
    ssr: false,
    // No placeholder: the globe is on screen underneath until the handover.
    loading: () => null,
  },
);
import { Button } from "@/components/ui/button";
import { BrandLockup } from "@/components/varuna/wordmark";
import { apiUrl } from "@/lib/api/client";
import type { RunDepth } from "@/lib/api/run-depth";
import { formatIst } from "@/lib/format";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { DUR, DUR_MS, EASE_UI, presetFor } from "@/lib/motion";
import { fetchOpeningRunId } from "@/lib/opening-run";
import { depthLegendStops } from "@/lib/ramps";
import { DEFAULT_SIM_TIME } from "@/lib/stores/replay";

/** Steps in a run: 36 five-minute frames, three hours (SPEC.md 10.3). */
const N_STEPS = 36;

/** One full sweep, in milliseconds. SPEC.md 7.1 asks for eight seconds. */
const LOOP_MS = 8000;

/** The frame reduced motion holds: +120 min, where the storm has arrived. */
const STILL_STEP = 24;

const FRAME_MS = LOOP_MS / N_STEPS;

/** The hero map's fit margin: the copy column on the left (96 px gutter plus a 520 px column) and
 * the readout and legend along the bottom. `CityMap` shrinks a side that would leave the frame
 * under 40 % of the map, so a phone still gets a frame. */
const HERO_FIT_PADDING = { top: 24, right: 24, bottom: 96, left: 640 } as const;

export function useScrubLoop(playing: boolean, still: boolean): number {
  // The loop's own position, advanced only from the animation frame and kept in a ref, so a pause
  // - the pointer over the hero, or the loop not yet handed over - holds the frame on screen and
  // resuming carries on from it. Only reduced motion shows the still +120 min frame (M1's
  // reduced form). Until 2026-09-22 a hover jumped to that still frame and resuming restarted the
  // loop at +0 min, which is neither "pauses on hover" nor a loop.
  const [step, setStep] = useState(0);
  const frame = useRef(0);
  const elapsed = useRef(0);

  useEffect(() => {
    if (!playing || still) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      // A hidden tab throttles rAF to about once a second, which would make the loop lurch when
      // the user comes back. Reading the real delta keeps it in step with the clock instead.
      const delta = Math.min(now - last, 250);
      last = now;
      if (!document.hidden) {
        elapsed.current += delta;
        const next = Math.floor((elapsed.current / FRAME_MS) % N_STEPS);
        if (next !== frame.current) {
          frame.current = next;
          setStep(next);
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, still]);

  return still ? STILL_STEP : step;
}

/**
 * The least opacity the first keyframe paints at.
 *
 * Chrome never records a Largest Contentful Paint for text painted at exactly zero opacity, and a
 * composited opacity animation does not repaint the text as it fades in - so a CSS fade from 0
 * leaves the page with **no LCP at all** (Lighthouse: `NO_LCP`, measured 2026-09-22). One per cent
 * is invisible on `--ink` and lets the headline's first paint count as the paint it is. The
 * catalogue's 0 is still what the preset says and what the parity test reads.
 */
export const M2_FIRST_PAINT_OPACITY = 0.01;

/** A motion preset's `initial` or `animate` target as the CSS a keyframe can carry. */
function m2KeyframeCss(target: Record<string, unknown>): string {
  const opacity = Math.max(Number(target.opacity ?? 1), M2_FIRST_PAINT_OPACITY);
  const filter = String(target.filter ?? "none");
  const y = Number(target.y ?? 0);
  return `opacity:${opacity};filter:${filter};transform:translateY(${y}px)`;
}

/** The keyframes' name; one rule for every line of hero copy. */
export const M2_KEYFRAMES = "varuna-m2-blur-fade";

/**
 * The stylesheet M2 runs on, built from the catalogue's preset rather than written out, so the
 * parity test on section 8 still covers every value. Reduced motion removes the animation
 * outright: the global escape hatch only shortens durations, and a copy line with a 300 ms
 * `animation-delay` would otherwise sit invisible for that long (M2's reduced form is "instant").
 */
export function m2Stylesheet(): string {
  const { initial, animate } = presetFor("M2", false);
  const from = m2KeyframeCss((initial || {}) as Record<string, unknown>);
  const to = m2KeyframeCss(animate as Record<string, unknown>);
  return (
    `@keyframes ${M2_KEYFRAMES}{from{${from}}to{${to}}}` +
    `@media (prefers-reduced-motion: reduce){[data-motion="M2"]{animation:none!important}}`
  );
}

/** The `animation` shorthand for the line at `index`: the preset's duration, easing and stagger. */
export function m2Animation(index: number): string {
  const { transition } = presetFor("M2", false);
  const seconds = Number((transition as { duration?: number }).duration ?? DUR.panel);
  const ease = (transition as { ease?: readonly number[] }).ease ?? EASE_UI;
  return `${M2_KEYFRAMES} ${Math.round(seconds * 1000)}ms cubic-bezier(${ease.join(",")}) ${
    index * DUR_MS.staggerCopy
  }ms both`;
}

/**
 * Motion M2: the hero copy's blur-fade entrance, once, 60 ms apart. The values are the catalogue's
 * (`presetFor("M2")` and `DUR.staggerCopy` in lib/motion.ts), not local literals, so the parity
 * test on section 8 covers them.
 *
 * **It is a CSS animation, not a `motion.div`.** A framer entrance starts from `opacity: 0` in the
 * server HTML and waits for hydration to reveal anything, so the headline - the page's Largest
 * Contentful Paint - painted only once every script on the page had run: 4.7-5.5 s on a 4x
 * throttled phone, 8.96 s in Lighthouse's mobile run. The same preset as keyframes starts with the
 * first paint and needs no JavaScript at all. Under reduced motion the stylesheet removes the
 * animation, so the copy is simply there (M2's "instant").
 */
export function BlurFade({ children, index }: { children: React.ReactNode; index: number }) {
  return (
    <div data-motion="M2" style={{ animation: m2Animation(index) }}>
      {children}
    </div>
  );
}

/** "07:20 IST · +40 min" for the step the loop is on, from the cycle the frames belong to. */
export function heroReadout(cycleTs: string | null, step: number, stepMin = 5): string | null {
  const start = cycleTs ? Date.parse(cycleTs) : NaN;
  if (!Number.isFinite(start)) return null;
  const lead = step * stepMin;
  return `${formatIst(start + lead * 60_000)} IST · +${lead} min`;
}

/** The depth ramp without its dry band: what the water on the hero's streets means. */
function HeroLegend() {
  const stops = depthLegendStops().filter((stop) => stop.key !== "depth-dry");
  return (
    <ul className="flex flex-wrap items-center gap-x-3 gap-y-1" aria-label="Depth on the streets">
      {stops.map((stop) => (
        <li key={stop.key} className="text-micro text-text-2 flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{ backgroundColor: stop.cssVar }}
          />
          <span className="num">{stop.label}</span>
        </li>
      ))}
    </ul>
  );
}

export function Hero() {
  const reducedMotion = usePrefersReducedMotion();
  const [hovered, setHovered] = useState(false);
  // The hand-over needs both halves ready: the globe finished unrolling *and* something to hand
  // over to - the run's 36 frames decoded, or, with no API, the pre-rendered sequence. Firing on
  // the globe alone left a blank hero for the second or two the map still needed.
  const [morphDone, setMorphDone] = useState(reducedMotion);
  const [liveRun, setLiveRun] = useState<RunDepth | null>(null);
  const [mapFailed, setMapFailed] = useState(false);
  const [recorded, setRecorded] = useState<HeroFramesManifest | null>(null);
  const handedOver = morphDone && (liveRun !== null || recorded !== null);
  const onMorphDone = useCallback(() => setMorphDone(true), []);
  const onMapLoaded = useCallback((run: RunDepth) => setLiveRun(run), []);
  const onMapStatus = useCallback(
    (kind: string) => setMapFailed(kind === "error" || kind === "empty"),
    [],
  );
  const step = useScrubLoop(!hovered && handedOver, reducedMotion);

  // The cycle section 7.1's readout names: 06:40 on 2 July, the replay's opening, rather than
  // the newest run (09:10, after the storm). Asked of the run registry the way the console asks
  // it; an unreachable registry answers "undefined" and the API's own default is used.
  const [opening, setOpening] = useState<{ runId?: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetchOpeningRunId("mumbai", DEFAULT_SIM_TIME, apiUrl, controller.signal).then((runId) => {
      if (!controller.signal.aborted) setOpening({ runId });
    });
    return () => controller.abort();
  }, []);

  const readout = liveRun
    ? heroReadout(liveRun.provenance.cycleTs, step, liveRun.provenance.stepMin || 5)
    : recorded
      ? heroReadout(recorded.cycle_ts, step, recorded.step_min)
      : null;

  return (
    <section
      className="bg-ink relative min-h-dvh w-full overflow-hidden"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* M2's keyframes, in the server HTML so the copy enters before any script runs. */}
      <style dangerouslySetInnerHTML={{ __html: m2Stylesheet() }} />
      {/* The city map is mounted from the start and revealed underneath, so the hand-over is a
          fade rather than a load: by the time the globe is gone the run's 36 frames are decoded
          and the scrub is already running. */}
      <div
        aria-hidden="true"
        data-hero-map=""
        data-run-id={liveRun?.provenance.runId ?? recorded?.run_id}
        data-cycle-ts={liveRun?.provenance.cycleTs ?? recorded?.cycle_ts}
        className="absolute inset-0 motion-safe:transition-opacity motion-safe:duration-[900ms]"
        style={{
          opacity: handedOver ? 1 : 0,
          transitionDelay: handedOver ? "0ms" : undefined,
        }}
      >
        {mapFailed ? (
          <HeroFrames step={step} onReady={setRecorded} />
        ) : (
          <FloodMap
            mode="hero"
            step={step}
            runId={opening?.runId}
            deferLoad={opening === null}
            onLoaded={onMapLoaded}
            onStatus={onMapStatus}
            // No surcharge pulse on the hero. M8 is a console motion, and it asks deck to redraw
            // on every animation frame; each redraw re-applies the depth raster's sampler, where
            // luma.gl 9.3.6 builds a debug string of every GL constant's name whether or not it is
            // logging (`getGLKeys` in `_setSamplerParameters`). Profiled on the steady loop, that
            // was 9.8 s of samples in a 10 s window. Without the pulse the map redraws when the
            // loop moves a step - 4.5 times a second rather than 60.
            showSurcharge={false}
            // No footprints on the hero. Section 6.7 draws buildings only from zoom 14 and the
            // hero frames the whole AOI at about 12, so they were never meant to be seen here -
            // and they are 11 MB of JSON and 39,259 polygons to tessellate on the main thread
            // while the loop is trying to start.
            showBuildings={false}
            showHotspots
            // Section 7.1 loops the scrub "over Hindmata/King's Circle/Sion". Framing what is
            // drawn had put the airport and Powai in the middle and the flooded spine under the
            // copy, so the hero opens on the run's main affected area at its peak, in the part of
            // the map the copy column and the readout leave clear. Computed once per run, before
            // the hand-over, and never moved by the loop.
            frameOn="affected"
            fitPadding={HERO_FIT_PADDING}
          />
        )}
      </div>

      {!handedOver ? (
        <div
          aria-hidden="true"
          className="absolute inset-0 motion-safe:transition-opacity motion-safe:duration-[900ms]"
        >
          <GlobeIntro onDone={onMorphDone} still={reducedMotion} />
        </div>
      ) : null}

      {/* The copy needs a readable ground without hiding the map: a one-sided wash from the left,
          which is where the text is, gone by the middle. Stopping it at 60 % rather than letting
          it run to the right edge is what leaves the aerial imagery visible - a gradient that is
          80 % ink across the whole width is just a dark rectangle over a photograph. */}
      <div
        aria-hidden="true"
        data-hero-wash=""
        className="from-ink via-ink/70 absolute inset-0 bg-gradient-to-r from-15% via-40% to-transparent to-60%"
      />

      <div
        data-hero-copy=""
        className="relative flex min-h-dvh items-center px-6 py-16 sm:px-12 lg:px-24"
      >
        <div className="flex max-w-[52ch] flex-col items-start gap-6">
          <BlurFade index={0}>
            {/* The team's logo, emblem over the lettered name. It is drawn smaller than the
                headline is wide and its lettering is darker than the headline's, so the headline
                stays the thing the hero says (section 7.1). */}
            <BrandLockup width={200} className="w-[152px] sm:w-[200px]" />
          </BlurFade>
          <BlurFade index={1}>
            <h1 className="font-display text-display tracking-display sm:text-hero max-w-[14ch] font-semibold">
              Every street. Three hours early.
            </h1>
          </BlurFade>
          <BlurFade index={2}>
            <p className="text-h3 text-text-2 max-w-[54ch]">
              VARUNA turns Doppler radar into street-by-street flood depth for the next three hours,
              learns the city&apos;s hidden drains from every flood, and routes emergency services
              around what is coming.
            </p>
          </BlurFade>
          <BlurFade index={3}>
            <div className="flex flex-wrap items-center gap-3">
              <Button size="lg" render={<Link href="/console" />} nativeButton={false}>
                Open the console
              </Button>
              <Button
                size="lg"
                variant="outline"
                render={<Link href="/console?bundle=MUM-2019-07-02&autoplay=1" />}
                nativeButton={false}
              >
                Watch the 2 July 2019 replay
              </Button>
            </div>
          </BlurFade>
          {/* UI_SPEC 1: the way in for everybody who is not an operator. Underlined as well as
              tinted, because colour alone is not a link (SPEC.md 6.10). */}
          <BlurFade index={4}>
            <p className="text-small text-text-2">
              Are you not an operator?{" "}
              <Link
                href="/dashboard"
                className="text-tide underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--tide)]"
              >
                Open the citizen dashboard
              </Link>
            </p>
          </BlurFade>
          {/* Index 5, not 4: the dashboard link above is a new step in M2's 60 ms stagger, and
              two lines sharing an index would enter together rather than in order. */}
          <BlurFade index={5}>
            <p className="text-small text-text-3">
              SIH 2026 · PS SIH26085 · Ministry of Earth Sciences
            </p>
          </BlurFade>
        </div>
      </div>

      {/* The readout and the depth legend, so the loop is legibly a forecast and its colours
          legibly depths. With no API the panel also says the frames are a recording, and of
          which run. */}
      <div
        hidden={!handedOver}
        data-hero-readout=""
        className="rounded-control border-line bg-ink/70 pointer-events-none absolute right-6 bottom-6 flex max-w-[calc(100%-3rem)] flex-col items-end gap-2 border px-3 py-2 max-sm:hidden"
      >
        {readout ? <p className="num text-small text-text-2">{readout}</p> : null}
        <HeroLegend />
        {recorded && !liveRun ? (
          <p className="text-micro text-text-3 max-w-[44ch] text-right">
            Pre-rendered frames of run {recorded.run_id}. The live map needs the API. Roads:
            OpenStreetMap · Terrain: Copernicus GLO-30
          </p>
        ) : null}
      </div>
    </section>
  );
}

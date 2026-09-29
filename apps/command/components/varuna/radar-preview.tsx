"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CloudRain, Radar } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/varuna/empty-state";
import { Skeleton } from "@/components/varuna/skeleton";
import {
  radarAccumulationUrl,
  radarFrameUrl,
  useRadarPreview,
  useReplayBundles,
} from "@/lib/api/replay";
import type { ReplayBundle, RadarPreview as RadarPreviewIndex } from "@/lib/api/schemas";
import { formatIst, formatMinutes } from "@/lib/format";
import { useMotionPref } from "@/lib/motion";
import { rainLegendStops } from "@/lib/ramps";
import { cn } from "@/lib/utils";

/** The bundle member that holds the radar cube (SPEC.md section 10.2). */
export const RADAR_FRAMES_MEMBER = "radar/frames.zarr";

/** The bundle member the accumulation image is integrated from (SPEC.md section 10.2). */
export const TRUTH_RAIN_MEMBER = "truth/rain.zarr";

/** Motion M25: 4 fps, so a 25-frame cube loops in 6.25 s. */
const FRAME_MS = 250;

/** A rectangle of radar-cube pixels, top-left origin, the same convention as `aoi_px`. */
export interface CubeRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** What the preview shows: the forecast area and the rain around it, or the whole radar domain. */
export type RadarFraming = "area" | "domain";

/**
 * Share of the forecast area's own width and height added on every side of the framed view.
 *
 * Measured on MUM-2019-07-02 on 2026-09-28 against `truth/rain.zarr`: the AOI is 21 x 32 cells of
 * the 120 x 120 cube, 4.7 % of the image, and holds 15.5 % of the window's rain. A quarter of its
 * size on every side, widened to the cube's square, is 48 x 48 cells; that frame holds 41 % of the
 * window's rain, 1,307 of the 1,361 cells (96 %) where the accumulation reaches half its 102.2 mm
 * peak, and at least 40 dBZ in all 18 of the 25 frames that reach 40 dBZ anywhere - the storm is
 * seen arriving from the south-west edge. The AOI then fills 29 % of the image. The other rule on
 * the table, the box around the heaviest accumulation, is the whole domain on both design storms,
 * whose rain is spatially uniform, so it frames nothing there.
 */
export const AREA_MARGIN = 0.25;

/**
 * The cube rectangle the preview draws for `framing`, in whole cells.
 *
 * `domain` is the whole cube. `area` is the AOI grown by `margin` of its own size on every side,
 * then widened on its short side to the cube's own aspect ratio, so switching framing never changes
 * the preview's box on the page; it is centred on the AOI, rounded to whole cells so a source
 * rectangle never samples half a cell, and shifted inside the cube where it would cross an edge.
 * A frame that would be as large as the cube is the cube.
 */
export function radarFrameRect(
  aoi: Pick<RadarPreviewIndex["aoi_px"], "left" | "top" | "width" | "height">,
  cubeWidth: number,
  cubeHeight: number,
  framing: RadarFraming = "area",
  margin: number = AREA_MARGIN,
): CubeRect {
  const whole = { left: 0, top: 0, width: cubeWidth, height: cubeHeight };
  if (framing === "domain") return whole;
  if (!(cubeWidth > 0 && cubeHeight > 0 && aoi.width > 0 && aoi.height > 0)) return whole;

  let width = aoi.width * (1 + 2 * Math.max(0, margin));
  let height = aoi.height * (1 + 2 * Math.max(0, margin));
  const ratio = cubeWidth / cubeHeight;
  if (width / height < ratio) width = height * ratio;
  else height = width / ratio;
  // A hair under a whole number is that number: 31.5 x 1.5 must not become 48.000000001 -> 49.
  width = Math.ceil(width - 1e-9);
  height = Math.ceil(height - 1e-9);
  if (width >= cubeWidth || height >= cubeHeight) return whole;

  const centreX = aoi.left + aoi.width / 2;
  const centreY = aoi.top + aoi.height / 2;
  const clamp = (value: number, max: number) => Math.min(Math.max(value, 0), max);
  return {
    left: clamp(Math.round(centreX - width / 2), cubeWidth - width),
    top: clamp(Math.round(centreY - height / 2), cubeHeight - height),
    width,
    height,
  };
}

/** One bundle's frames, held only once every image of that index has settled. */
interface LoadedFrames {
  preview: RadarPreviewIndex;
  images: HTMLImageElement[];
  /** True when any frame failed to load; the preview then says so instead of animating gaps. */
  failed: boolean;
}

/**
 * Fields of `GET /v1/replay/bundles` the preview reads that the shared row does not type.
 *
 * The two basis strings are the honesty text the summary already carries; the accumulation
 * figure is the manifest number the summary does not carry yet - `calibration.window_achieved_mm`
 * on a reconstruction, `design_storm.total_depth_mm` on a design storm - so the figure appears
 * as soon as `summarize_bundle` publishes it and nothing here ever computes a depth of its own
 * (SPEC.md rule 6).
 */
interface BundleFigures {
  calibration_basis?: string | null;
  design_storm_basis?: string | null;
  window_accumulation_mm?: number | null;
}

/** The bundle's own row widened to the fields above; empty until the listing arrives. */
function bundleFigures(row: ReplayBundle | undefined): BundleFigures {
  return (row ?? {}) as BundleFigures;
}

/**
 * The lead sentence of a manifest basis text, verbatim.
 *
 * A basis runs to thousands of characters and the replay panel renders it in full; under a
 * preview image its first sentence - "Inferred, not measured.", "Design storm - synthetic." -
 * is what keeps the figure beside it from being read as a measurement (SPEC.md rule 6).
 */
function leadSentence(text: string | null | undefined): string | null {
  const trimmed = text?.trim();
  if (!trimmed) return null;
  const stop = trimmed.indexOf(". ");
  return stop === -1 ? trimmed : trimmed.slice(0, stop + 1);
}

/** The API's number with its unit; one decimal, so a depth never reads as a whole number. */
function formatMm(value: number): string {
  return `${value.toFixed(1)} mm`;
}

export interface RadarPreviewProps {
  /** Bundle whose radar cube is animated, e.g. "MUM-2019-07-02". */
  bundleId: string;
  /** False until `make bundle` has written the bundle folder; nothing is fetched. */
  built: boolean;
  /** Bundle members `make bundle` has not written yet, from `GET /v1/replay/bundles`. */
  missingMembers: readonly string[];
  /** Tighter layout for the console's replay panel: no heading, no legend. */
  compact?: boolean;
  className?: string;
}

/**
 * The storm preview of SPEC.md section 7.8: a bundle's radar frames animated in place.
 *
 * Every frame PNG is preloaded before anything moves, so the loop never stalls mid-cycle on a
 * cold cache; the frames are then drawn into a canvas at cube resolution with smoothing off and
 * scaled by CSS, which keeps a 120 x 120 dBZ field readable as pixels rather than as blur. The
 * loop is motion M25 and obeys it: 4 fps, paused on hover, on focus and while the tab is hidden,
 * and under reduced motion it draws the middle frame once and schedules nothing.
 *
 * Beside the player sits the window's accumulation: the API's render of the truth field, the
 * figure the bundle summary reports for it, and the bundle's own basis sentence, so the depth is
 * read as the inference it is (SPEC.md section 7.8 and rule 6).
 *
 * Colours come from the tokens: the frames were rendered with the rain ramp, so the legend is
 * `rainLegendStops()` and never the depth ramp (SPEC.md section 6.2).
 */
export function RadarPreview({
  bundleId,
  built,
  missingMembers,
  compact = false,
  className,
}: RadarPreviewProps) {
  const hasCube = built && !missingMembers.includes(RADAR_FRAMES_MEMBER);
  const query = useRadarPreview(bundleId, { enabled: hasCube });
  const preview = query.data;

  // The same listing the host screen already reads, so this costs a cache hit, not a request:
  // the window the label states and the figure beside the accumulation both come from it.
  const bundles = useReplayBundles();
  const row = bundles.data?.find((bundle) => bundle.id === bundleId);
  const figures = bundleFigures(row);

  const { reduced } = useMotionPref();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [loaded, setLoaded] = useState<LoadedFrames | null>(null);
  const [frame, setFrame] = useState(0);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [tabHidden, setTabHidden] = useState(false);
  // Opens on the forecast area, not on the 60 km domain where the city is a small outline: the
  // "open on the main affected area" request. The whole domain is one press away.
  const [framing, setFraming] = useState<RadarFraming>("area");

  const frames = preview?.frames ?? [];
  const frameCount = frames.length;
  // Identity, not a copy: react-query hands back the same index object for a cached bundle, so a
  // switch to another bundle drops us back to the skeleton until its own frames have settled.
  const settled = preview && loaded?.preview === preview ? loaded : null;
  const ready = Boolean(settled && !settled.failed);
  const imagesFailed = Boolean(settled?.failed);

  // Preload every frame; the loop only starts once all of them have settled.
  useEffect(() => {
    if (!preview || preview.frames.length === 0) return;

    let cancelled = false;
    let done = 0;
    let failed = false;
    const images = preview.frames.map((ref) => {
      const image = new Image();
      image.decoding = "async";
      const onSettled = () => {
        done += 1;
        if (cancelled || done < preview.frames.length) return;
        setLoaded({ preview, images, failed });
      };
      image.onload = onSettled;
      image.onerror = () => {
        failed = true;
        onSettled();
      };
      const url = radarFrameUrl(preview, ref.index);
      if (url) image.src = url;
      else {
        // The index promised a frame it cannot serve; count it so the preview stops waiting.
        failed = true;
        queueMicrotask(onSettled);
      }
      return image;
    });

    return () => {
      cancelled = true;
      for (const image of images) {
        image.onload = null;
        image.onerror = null;
      }
    };
  }, [preview]);

  // The tab going away is a pause, not a dropped loop: a background rAF would jump on return.
  useEffect(() => {
    const read = () => setTabHidden(document.hidden);
    read();
    document.addEventListener("visibilitychange", read);
    return () => document.removeEventListener("visibilitychange", read);
  }, []);

  /**
   * Which frame is on screen. Under reduced motion this is the middle frame of the loop and it
   * never changes, which is the M25 fallback; otherwise it is wherever the rAF timer has reached,
   * wrapped so a bundle with fewer frames than the last one still draws.
   */
  const shown = frameCount === 0 ? 0 : reduced ? Math.floor(frameCount / 2) : frame % frameCount;

  const cubeWidth = preview?.width ?? 1;
  const cubeHeight = preview?.height ?? 1;
  const aoiPx = preview?.aoi_px;
  const rect = useMemo(
    () =>
      aoiPx
        ? radarFrameRect(aoiPx, cubeWidth, cubeHeight, framing)
        : { left: 0, top: 0, width: cubeWidth, height: cubeHeight },
    [aoiPx, cubeWidth, cubeHeight, framing],
  );

  const draw = useCallback(
    (index: number) => {
      const canvas = canvasRef.current;
      const image = settled?.images[index];
      if (!canvas || !image || !image.complete || image.naturalWidth === 0) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      // Nearest neighbour: a 120 x 120 dBZ field must read as pixels, not as blur.
      ctx.imageSmoothingEnabled = false;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      // The framed cells only: the PNG is the whole domain, so the crop is a source rectangle,
      // scaled by the image's own size in case the API ever renders it at more than a pixel a cell.
      const sx = image.naturalWidth / cubeWidth;
      const sy = image.naturalHeight / cubeHeight;
      ctx.drawImage(
        image,
        rect.left * sx,
        rect.top * sy,
        rect.width * sx,
        rect.height * sy,
        0,
        0,
        canvas.width,
        canvas.height,
      );
    },
    [settled, rect, cubeWidth, cubeHeight],
  );

  useEffect(() => {
    if (ready) draw(shown);
  }, [draw, ready, shown]);

  const paused = hovered || focused || tabHidden;
  useEffect(() => {
    if (!ready || reduced || paused || frameCount === 0) return;
    let request = 0;
    let last = performance.now();
    let carried = 0;
    const step = (now: number) => {
      carried += now - last;
      last = now;
      if (carried >= FRAME_MS) {
        const advance = Math.floor(carried / FRAME_MS);
        carried -= advance * FRAME_MS;
        setFrame((current) => (current + advance) % frameCount);
      }
      request = requestAnimationFrame(step);
    };
    request = requestAnimationFrame(step);
    return () => cancelAnimationFrame(request);
  }, [frameCount, paused, ready, reduced]);

  if (!hasCube) {
    return (
      <PreviewShell className={className}>
        <EmptyState
          size="sm"
          icon={Radar}
          title="No radar frames yet"
          description={`Run make bundle BUNDLE=${bundleId} to write the radar cube for this bundle.`}
        />
      </PreviewShell>
    );
  }

  if (query.isError) {
    return (
      <PreviewShell className={className}>
        <EmptyState
          size="sm"
          icon={Radar}
          title="Radar preview unavailable"
          description={query.error.message}
        />
      </PreviewShell>
    );
  }

  if (preview && frameCount === 0) {
    return (
      <PreviewShell className={className}>
        <EmptyState
          size="sm"
          icon={Radar}
          title="This bundle has no radar frames"
          description={`Run make bundle BUNDLE=${bundleId} again to rebuild the radar cube.`}
        />
      </PreviewShell>
    );
  }

  if (imagesFailed && !ready) {
    return (
      <PreviewShell className={className}>
        <EmptyState
          size="sm"
          icon={Radar}
          title="Radar frames did not load"
          description="The frame index arrived but its images did not. Check the API is reachable, then reload."
        />
      </PreviewShell>
    );
  }

  const aoi = preview?.aoi_px;
  const current = frames[shown];
  // The frame's own ratio, which `radarFrameRect` keeps equal to the cube's: switching framing
  // never moves anything else on the page.
  const aspectRatio = `${rect.width} / ${rect.height}`;
  const framedOnArea = rect.width < cubeWidth || rect.height < cubeHeight;

  // ADR-0007 puts the demo window at 05:40-09:40 IST, four hours, so the label is built from the
  // bundle's own t0 and t1 rather than naming a fixed span the bundle may not have.
  const windowLabel = row
    ? `AOI accumulation, ${formatIst(row.t0)}–${formatIst(row.t1)} IST`
    : null;
  const totalMm =
    typeof figures.window_accumulation_mm === "number" &&
    Number.isFinite(figures.window_accumulation_mm)
      ? figures.window_accumulation_mm
      : null;
  const honesty = leadSentence(figures.calibration_basis ?? figures.design_storm_basis);

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {compact || !preview ? null : (
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="type-small text-text font-medium">Radar preview</h3>
          <p className="type-micro text-text-2">
            {preview.label}, frames every {formatMinutes(preview.step_min)}
          </p>
        </div>
      )}

      {preview && aoi ? <FramingToggle framing={framing} onChange={setFraming} /> : null}

      {/* Side by side in the console rail too: a 120 px cube is already near its native size
          there, and stacking the two images would double the panel's height. */}
      <div className={cn("grid gap-3", compact ? "grid-cols-2" : "sm:grid-cols-2")}>
        <div className="flex min-w-0 flex-col gap-2">
          <div
            tabIndex={0}
            role="img"
            aria-label={
              preview
                ? `Radar frames for ${preview.bundle_id}: ${frameCount} frames every ${formatMinutes(preview.step_min)}, ${framedOnArea ? "framed on the forecast area and the rain around it" : "the whole radar domain"}; outlined rectangle is the forecast area`
                : `Radar frames for ${bundleId}, loading`
            }
            onPointerEnter={() => setHovered(true)}
            onPointerLeave={() => setHovered(false)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            className={cn(
              "rounded-control border-line bg-ink relative overflow-hidden border",
              "focus-visible:ring-tide outline-none focus-visible:ring-2",
            )}
            style={{ aspectRatio }}
          >
            {ready && preview ? (
              <canvas
                ref={canvasRef}
                width={rect.width}
                height={rect.height}
                className="block h-full w-full"
                style={{ imageRendering: "pixelated" }}
              />
            ) : (
              <Skeleton className="h-full w-full rounded-none" />
            )}

            {ready && aoi ? <AoiOutline aoi={aoi} frame={rect} /> : null}
          </div>

          <div className="type-micro flex flex-wrap items-baseline justify-between gap-2">
            <span className="num text-text-2">
              {ready && current ? `${formatIst(current.ts)} IST` : "Loading frames"}
            </span>
            <span className="num text-text-3">
              {ready
                ? `Frame ${shown + 1} of ${frameCount}`
                : preview
                  ? `${frameCount} frames`
                  : ""}
            </span>
          </div>
        </div>

        <AccumulationFigure
          bundleId={bundleId}
          preview={preview}
          hasCube={built && !missingMembers.includes(TRUTH_RAIN_MEMBER)}
          aspectRatio={aspectRatio}
          frame={rect}
          totalMm={totalMm}
          windowLabel={windowLabel}
          honesty={honesty}
        />
      </div>

      {compact ? null : (
        <>
          <ul className="flex flex-wrap gap-x-3 gap-y-1">
            {rainLegendStops().map((stop) => (
              <li key={stop.key} className="type-micro text-text-2 flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  className="size-2.5 shrink-0 rounded-full"
                  style={{ background: stop.cssVar }}
                />
                <span className="num whitespace-nowrap">{stop.label}</span>
              </li>
            ))}
          </ul>
          <p className="type-micro text-text-3" title={preview?.accumulation.note}>
            Rain rate in mm/h. The accumulation uses the same band edges in mm.
          </p>
          {reduced ? (
            <p className="type-micro text-text-3">Reduced motion: the middle frame is shown.</p>
          ) : null}
        </>
      )}
    </div>
  );
}

interface AoiOutlineProps {
  /** The area of interest in cube pixels, top-left origin. */
  aoi: RadarPreviewIndex["aoi_px"];
  /** The cube rectangle on screen, so the outline is placed in percentages of what is drawn. */
  frame: CubeRect;
}

/** Where a cube rectangle sits inside the drawn frame, as CSS percentages. */
export function rectInFrame(rect: CubeRect, frame: CubeRect) {
  return {
    left: `${((rect.left - frame.left) / frame.width) * 100}%`,
    top: `${((rect.top - frame.top) / frame.height) * 100}%`,
    width: `${(rect.width / frame.width) * 100}%`,
    height: `${(rect.height / frame.height) * 100}%`,
  };
}

/** The whole cube placed so that `frame` fills the box: the crop an image gets by position. */
export function wholeInFrame(cubeWidth: number, cubeHeight: number, frame: CubeRect) {
  return rectInFrame({ left: 0, top: 0, width: cubeWidth, height: cubeHeight }, frame);
}

/**
 * The forecast area drawn over a cube image: 1 px of `--text-2`, never a fill. It was
 * `--line-strong`, which vanished against the rain ramp's blues on the radar frames.
 */
function AoiOutline({ aoi, frame }: AoiOutlineProps) {
  return (
    <div
      aria-hidden="true"
      className="border-text-2 pointer-events-none absolute border"
      style={rectInFrame(aoi, frame)}
    />
  );
}

interface FramingToggleProps {
  framing: RadarFraming;
  onChange: (framing: RadarFraming) => void;
}

/** The two framings, in the order the toggle lists them. */
const FRAMING_OPTIONS: readonly { value: RadarFraming; label: string }[] = [
  { value: "area", label: "Forecast area" },
  { value: "domain", label: "Whole radar domain" },
];

/**
 * Forecast area or whole radar domain: two buttons carrying `aria-pressed`, in one labelled group,
 * so a keyboard reaches both with Tab and a screen reader hears which is on. The switch is a cut:
 * section 8 has no row for a radar zoom, and a cut is also what reduced motion would ask for.
 */
function FramingToggle({ framing, onChange }: FramingToggleProps) {
  return (
    <div role="group" aria-label="Radar preview extent" className="flex flex-wrap gap-1.5">
      {FRAMING_OPTIONS.map((option) => {
        const pressed = framing === option.value;
        return (
          <Button
            key={option.value}
            type="button"
            size="xs"
            variant={pressed ? "default" : "outline"}
            aria-pressed={pressed}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </Button>
        );
      })}
    </div>
  );
}

interface AccumulationFigureProps {
  bundleId: string;
  /** The frame index, once it has arrived: it carries the accumulation's URL and its label. */
  preview: RadarPreviewIndex | undefined;
  /** False when `truth/rain.zarr` is not on disk; the image is then never requested. */
  hasCube: boolean;
  /** The frame's ratio, so the image holds its place while the index is still loading. */
  aspectRatio: string;
  /** The cube rectangle the frames beside it show, so both images frame the same ground. */
  frame: CubeRect;
  /** Accumulation over the AOI for the whole window, in mm, as the API reports it. */
  totalMm: number | null;
  /** "AOI accumulation, 05:40–09:40 IST", from the bundle's own window. */
  windowLabel: string | null;
  /** The bundle's basis text, so the figure is never read as a measurement. */
  honesty: string | null;
}

/**
 * The accumulated rainfall of the whole replay window beside the frames (SPEC.md section 7.8).
 *
 * The image is the API's own render of `truth/rain.zarr`, outlined with the same AOI rectangle
 * the frames carry, and the figure under it is the manifest's number served by the bundle
 * summary - nothing here integrates a cube or converts a colour back into millimetres.
 */
function AccumulationFigure({
  bundleId,
  preview,
  hasCube,
  aspectRatio,
  frame,
  totalMm,
  windowLabel,
  honesty,
}: AccumulationFigureProps) {
  const [failed, setFailed] = useState(false);

  if (!hasCube) {
    return (
      <PreviewShell>
        <EmptyState
          size="sm"
          icon={CloudRain}
          title="No accumulation yet"
          description={`Run make bundle BUNDLE=${bundleId} to write ${TRUTH_RAIN_MEMBER} for this bundle.`}
        />
      </PreviewShell>
    );
  }

  if (failed) {
    return (
      <PreviewShell>
        <EmptyState
          size="sm"
          icon={CloudRain}
          title="Accumulation image did not load"
          description="The frame index arrived but the accumulation image did not. Check the API is reachable, then reload."
        />
      </PreviewShell>
    );
  }

  return (
    <figure className="flex min-w-0 flex-col gap-2">
      <div
        className="rounded-control border-line bg-ink relative overflow-hidden border"
        style={{ aspectRatio }}
      >
        {preview ? (
          // eslint-disable-next-line @next/next/no-img-element -- an API render, sized by the cube
          <img
            src={radarAccumulationUrl(preview)}
            alt={`${preview.accumulation.label}, ${preview.bundle_id}, with the forecast area outlined`}
            onError={() => setFailed(true)}
            // The whole domain's render, placed so the frame's cells fill the box: the same crop
            // the radar canvas draws, done by position because this is an image, not a canvas.
            className="absolute block max-w-none"
            style={{
              imageRendering: "pixelated",
              ...wholeInFrame(preview.width, preview.height, frame),
            }}
          />
        ) : (
          <Skeleton className="h-full w-full rounded-none" />
        )}
        {preview ? <AoiOutline aoi={preview.aoi_px} frame={frame} /> : null}
      </div>

      <figcaption className="flex flex-col gap-1">
        {totalMm === null ? null : (
          <span className="num type-h3 text-text">{formatMm(totalMm)}</span>
        )}
        <span className="type-micro text-text-2">
          {windowLabel ?? preview?.accumulation.label ?? "Accumulation over the replay window"}
        </span>
        {honesty ? <span className="type-micro text-text-3">{honesty}</span> : null}
      </figcaption>
    </figure>
  );
}

/** The bordered box every state sits in, so an empty preview holds the same place as a playing one. */
function PreviewShell({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "rounded-control border-line bg-ink flex min-h-40 items-center justify-center border",
        className,
      )}
    >
      {children}
    </div>
  );
}

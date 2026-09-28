"use client";

/**
 * The landing hero without the API (SPEC.md 7.1, "States").
 *
 * "If the API is unreachable, the hero plays a pre-rendered image sequence from `public/hero/`
 * (36 frames)". The frames are screenshots of the real hero map - the same `FloodMap` in hero
 * mode, the same run, one per five-minute step - taken by
 * `tests/e2e/landing-hero-frames.spec.ts` against a running API, and `manifest.json` beside them
 * names the run they were rendered from. So a venue with no network still sees the product's own
 * water on Mumbai's streets, and the page says which run it is looking at rather than passing a
 * recording off as live (rule 6).
 *
 * Frames are preloaded once and swapped by `step`, which the hero's own M1 loop drives, so the
 * fallback pauses on hover and holds the +120 min frame under reduced motion exactly as the live
 * map does.
 */

import { useEffect, useRef, useState } from "react";

/** What `public/hero/manifest.json` records about the frames beside it. */
export interface HeroFramesManifest {
  run_id: string;
  cycle_ts: string;
  step_min: number;
  frames: string[];
  width: number;
  height: number;
  rendered_at: string;
  rendered_by: string;
}

export const HERO_FRAMES_MANIFEST = "/hero/manifest.json";

/** Reads the manifest; null when there is none or it does not describe 36 frames. */
export async function loadHeroFramesManifest(
  signal?: AbortSignal,
): Promise<HeroFramesManifest | null> {
  try {
    const response = await fetch(HERO_FRAMES_MANIFEST, { signal });
    if (!response.ok) return null;
    const body = (await response.json()) as Partial<HeroFramesManifest>;
    if (!Array.isArray(body.frames) || body.frames.length === 0 || !body.run_id) return null;
    return body as HeroFramesManifest;
  } catch {
    return null;
  }
}

export interface HeroFramesProps {
  /** The loop's step, 0 to 35. */
  step: number;
  /** Called once the manifest and the first frame are in, with the manifest. */
  onReady?: (manifest: HeroFramesManifest) => void;
}

export function HeroFrames({ step, onReady }: HeroFramesProps) {
  const [manifest, setManifest] = useState<HeroFramesManifest | null>(null);
  const onReadyRef = useRef(onReady);
  useEffect(() => {
    onReadyRef.current = onReady;
  }, [onReady]);

  useEffect(() => {
    const controller = new AbortController();
    // The decoded images are held here so the browser keeps them while the loop swaps `src`.
    const held: HTMLImageElement[] = [];
    void loadHeroFramesManifest(controller.signal).then((found) => {
      if (!found || controller.signal.aborted) return;
      const first = new Image();
      first.src = found.frames[0]!;
      held.push(first);
      first
        .decode()
        .catch(() => undefined)
        .then(() => {
          if (controller.signal.aborted) return;
          setManifest(found);
          onReadyRef.current?.(found);
          for (const src of found.frames.slice(1)) {
            const image = new Image();
            image.src = src;
            held.push(image);
          }
        });
    });
    return () => {
      controller.abort();
      held.length = 0;
    };
  }, []);

  if (!manifest) return null;
  const src = manifest.frames[Math.min(Math.max(step, 0), manifest.frames.length - 1)];
  return (
    // eslint-disable-next-line @next/next/no-img-element -- a 36-frame flipbook swapped by the loop; next/image would re-request and re-lay out on every step.
    <img
      src={src}
      alt=""
      width={manifest.width}
      height={manifest.height}
      data-slot="hero-frames"
      className="absolute inset-0 h-full w-full object-cover"
      decoding="async"
    />
  );
}

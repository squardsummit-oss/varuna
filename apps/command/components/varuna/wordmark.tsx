import Image, { type StaticImageData } from "next/image";

import { cn } from "@/lib/utils";
import mark192 from "@/public/brand/varuna-mark-192.png";
import mark64 from "@/public/brand/varuna-mark-64.png";

/*
 * The brand is the team's own logo, supplied as `Varuna Logo.png` at the repository root: a water
 * "V" framing a rain-lit skyline, with VARUNA lettered below it. Every file under `public/brand/`,
 * the favicon, `icon.svg` and `app/apple-icon.png` are derived from that one file by
 * `tools/brand_assets.py` (cropped and resampled, never redrawn), so the mark in the top bar, the
 * icons and the landing page are the same artwork at different sizes. The script reads the logo
 * from `docs/brand/varuna-logo.png` when the repository keeps it there, else from the root, and
 * `--check` proves the committed bytes only where one of the two is committed beside them.
 *
 * The supplied PNG has a transparent background, so the mark sits on `--deep` or `--ink` with no
 * tile around it.
 */

/** The emblem at 64 px, 3 KB, by its public path: for `/rural`, which is plain HTML. */
export const BRAND_MARK_64_SRC = "/brand/varuna-mark-64.png";
/** The full logo - emblem over the lettered name - trimmed to its artwork. */
export const BRAND_LOCKUP_SRC = "/brand/varuna-lockup.png";
/** The lockup's intrinsic size, so its box is reserved before it loads (no layout shift). */
export const BRAND_LOCKUP_SIZE = { width: 640, height: 497 } as const;

/*
 * The mark never goes through the image optimiser. `/map` and `/report` carry it in their header,
 * and the offline worker (`public/sw.js`) keeps only `/_next/static/*` and a short list of named
 * files: an optimiser URL (`/_next/image?url=...`) is neither, so an installed public map opened
 * with no connection would draw a broken image where its emblem belongs. A static import is
 * emitted under `/_next/static/media/`, which the worker keeps with the page that names it.
 *
 * That is also why the mark has one `src` and no `srcset`: the worker finds a page's files by the
 * quote or bracket in front of each URL, and a srcset's second candidate follows a comma. Each
 * size draws the smallest file that is still exact or larger at 2x, so the 32 px top-bar mark is
 * the 64 px file: pixel for pixel on a 2x screen, a clean halving at 1x.
 */
const MARK_FILES: readonly { side: number; file: StaticImageData | string }[] = [
  { side: 64, file: mark64 },
  { side: 192, file: mark192 },
];

/** A static import is `StaticImageData` under Next and a bare URL under Vitest. */
function fileUrl(file: StaticImageData | string): string {
  return typeof file === "string" ? file : file.src;
}

/** The URL the mark is drawn from at `size` CSS pixels. */
export function markSrc(size: number): string {
  const pick = MARK_FILES.find((f) => f.side >= size * 2) ?? MARK_FILES[MARK_FILES.length - 1]!;
  return fileUrl(pick.file);
}

export interface WordmarkProps {
  /** sm for the top bar, md for panels, lg for large headers. */
  size?: "sm" | "md" | "lg";
  /** Show the emblem next to the word. */
  withMark?: boolean;
  className?: string;
}

const sizes = {
  sm: { text: "text-[17px] leading-none", mark: 32 },
  md: { text: "text-h2 leading-none", mark: 48 },
  lg: { text: "text-display leading-none", mark: 64 },
} as const;

export interface WordmarkMarkProps {
  /** Drawn width and height in CSS pixels. */
  size?: number;
  /** "VARUNA" when the mark stands alone; empty beside the word, which already says it. */
  alt?: string;
  className?: string;
}

/** The VARUNA emblem from the team's logo, drawn square at `size`. */
export function WordmarkMark({ size = 32, alt = "VARUNA", className }: WordmarkMarkProps) {
  return (
    <Image
      src={markSrc(size)}
      alt={alt}
      width={size}
      height={size}
      unoptimized
      // Above the fold on every screen that carries it; there is nothing to gain from deferring
      // a few kilobytes that the first paint shows.
      loading="eager"
      data-slot="brand-mark"
      className={cn("shrink-0 select-none", className)}
      draggable={false}
    />
  );
}

/**
 * The emblem and "VARUNA" in display type. The brand name is the one word set in capitals.
 *
 * The emblem carries the name for assistive technology (alt "VARUNA") and the lettering beside it
 * is hidden from it, so a screen reader says the name once rather than twice.
 */
export function Wordmark({ size = "sm", withMark = true, className }: WordmarkProps) {
  const s = sizes[size];
  return (
    <span className={cn("text-text inline-flex items-center gap-2", className)}>
      {withMark ? <WordmarkMark size={s.mark} /> : null}
      <span
        aria-hidden={withMark ? "true" : undefined}
        className={cn("font-display tracking-display font-semibold", s.text)}
      >
        VARUNA
      </span>
    </span>
  );
}

export interface BrandLockupProps {
  /** Drawn width in CSS pixels; the height follows the artwork's own proportions. */
  width: number;
  className?: string;
}

/**
 * The full logo as the team drew it - the emblem over the lettered name - for the landing page.
 * The lettering is part of the artwork, so the alt text carries the name.
 *
 * This one does go through the optimiser: `/` is outside the offline worker's scope, and the
 * optimiser sends a phone the 1x or 2x width it needs rather than the whole 640 px file.
 */
export function BrandLockup({ width, className }: BrandLockupProps) {
  const height = Math.round((width * BRAND_LOCKUP_SIZE.height) / BRAND_LOCKUP_SIZE.width);
  return (
    <Image
      src={BRAND_LOCKUP_SRC}
      alt="VARUNA"
      width={width}
      height={height}
      loading="eager"
      data-slot="brand-lockup"
      className={cn("h-auto max-w-full select-none", className)}
      draggable={false}
    />
  );
}

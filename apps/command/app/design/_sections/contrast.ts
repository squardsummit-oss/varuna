/**
 * WCAG 2.x relative luminance and contrast ratio, computed at render time for the contrast report
 * on /design (SPEC.md section 6.10: text contrast at least 4.5:1, checked on /design).
 * Pure functions; safe to import from tests and server components.
 */

/** Minimum contrast for body text (WCAG AA). */
export const AA_TEXT_RATIO = 4.5;
/** Minimum contrast for large text (24 px regular or 19 px bold) and UI components. */
export const AA_LARGE_RATIO = 3;

/** "#RRGGBB" or "#RGB" to [r, g, b] in 0-1. Throws on anything else so a bad token fails loudly. */
export function hexToUnitRgb(hex: string): [number, number, number] {
  const raw = hex.trim().replace(/^#/, "");
  const full = raw.length === 3 ? raw.split("").map((c) => c + c).join("") : raw;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) {
    throw new Error(`Not a hex colour: "${hex}"`);
  }
  const n = Number.parseInt(full, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

/** sRGB channel (0-1) to linear light, per WCAG 2.x. */
function linearise(channel: number): number {
  return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
}

/** Relative luminance in 0-1 (0 is black, 1 is white). */
export function relativeLuminance(hex: string): number {
  const [r, g, b] = hexToUnitRgb(hex);
  return 0.2126 * linearise(r) + 0.7152 * linearise(g) + 0.0722 * linearise(b);
}

/** Contrast ratio between two colours, 1-21, order independent. */
export function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const lighter = Math.max(la, lb);
  const darker = Math.min(la, lb);
  return (lighter + 0.05) / (darker + 0.05);
}

/** True when the ratio meets WCAG AA for normal text (or large text when `large`). */
export function passesAa(ratio: number, large = false): boolean {
  return ratio >= (large ? AA_LARGE_RATIO : AA_TEXT_RATIO);
}

/** "4.46:1" with two decimals, as the report prints it. */
export function formatRatio(ratio: number): string {
  return `${ratio.toFixed(2)}:1`;
}

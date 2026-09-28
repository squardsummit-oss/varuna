import { Noto_Sans_Devanagari } from "next/font/google";

/**
 * Noto Sans Devanagari for Hindi and Marathi (SPEC.md 6.3: "loaded only when the locale
 * switches").
 *
 * `preload: false` keeps the font out of the document head, so no page asks for it up front; the
 * `@font-face` rule is declared with a Devanagari-only `unicode-range`, and the family is applied
 * only under a Hindi or Marathi provider. A browser fetches a web font only when text it renders
 * resolves to that face, so an English reader never downloads it, and a Hindi reader's Latin
 * numbers and units still come from Geist.
 */
export const devanagari = Noto_Sans_Devanagari({
  subsets: ["devanagari"],
  weight: ["400", "500", "600"],
  variable: "--font-devanagari",
  display: "swap",
  preload: false,
});

/**
 * The public map's three languages (SPEC.md 3.2, 7.11) and how a choice is remembered.
 *
 * There is no locale routing: `/map` and `/report` keep their URLs, the choice lives in this
 * browser's storage, and the server always renders English. A reader who picked Hindi sees English
 * for the first frame and Hindi from the second, which is the price of not splitting every public
 * URL by language; the map is the same map either way.
 */

export const PUBLIC_LOCALES = ["en", "hi", "mr"] as const;
export type PublicLocale = (typeof PUBLIC_LOCALES)[number];

export const DEFAULT_LOCALE: PublicLocale = "en";

/** Where the choice is kept. One key for both screens, so the report keeps the map's language. */
export const LOCALE_STORAGE_KEY = "varuna.public.locale";

export function isPublicLocale(value: unknown): value is PublicLocale {
  return typeof value === "string" && (PUBLIC_LOCALES as readonly string[]).includes(value);
}

/**
 * The BCP 47 tag handed to `Intl`. Marathi's default numbering system is Devanagari digits, which
 * would print "४५ cm"; the copy rules keep every number and unit Latin ("45 cm", "09:25"), so both
 * Indic locales pin the Latin numbering system explicitly.
 */
export function intlLocale(locale: PublicLocale): string {
  if (locale === "hi") return "hi-IN-u-nu-latn";
  if (locale === "mr") return "mr-IN-u-nu-latn";
  return "en-IN";
}

/** Whether a locale is written in Devanagari and so needs Noto Sans Devanagari (SPEC.md 6.3). */
export function needsDevanagari(locale: PublicLocale): boolean {
  return locale === "hi" || locale === "mr";
}

/** The stored choice, or English. Storage can throw in a private window; English is the answer. */
export function readStoredLocale(): PublicLocale {
  if (typeof window === "undefined") return DEFAULT_LOCALE;
  try {
    const raw = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return isPublicLocale(raw) ? raw : DEFAULT_LOCALE;
  } catch {
    return DEFAULT_LOCALE;
  }
}

export function writeStoredLocale(locale: PublicLocale): void {
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // A refused write only means the choice is not remembered next visit.
  }
}

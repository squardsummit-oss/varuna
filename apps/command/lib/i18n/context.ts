"use client";

import { createContext, useContext, useMemo } from "react";
import { createTranslator } from "next-intl";

import { DEFAULT_LOCALE, intlLocale, type PublicLocale } from "./locales";
import { ENGLISH_MESSAGES, type PublicMessages, type PublicNamespace } from "./messages";

/** The IST zone every time on the public screens is printed in (SPEC.md 6.3). */
export const PUBLIC_TIME_ZONE = "Asia/Kolkata";

export interface PublicI18nValue {
  locale: PublicLocale;
  messages: PublicMessages;
  /** Switch language; resolves once the messages are loaded and the screen has changed. */
  setLocale: (locale: PublicLocale) => Promise<void>;
}

/**
 * Kept apart from the provider so a shared component can translate without importing the
 * provider's font: the dashboard, the design page and the unit tests render these components with
 * no provider at all, and must get English rather than an error.
 */
export const PublicI18nContext = createContext<PublicI18nValue | null>(null);

/** The language switcher's view of the provider, or null when none is mounted. */
export function usePublicLocale(): PublicI18nValue | null {
  return useContext(PublicI18nContext);
}

/**
 * A translator for one namespace of the public messages. Under the provider it follows the chosen
 * language; without one it is English, never a thrown "no provider" error.
 */
export function usePublicT<N extends PublicNamespace>(namespace: N) {
  const context = useContext(PublicI18nContext);
  const locale = context?.locale ?? DEFAULT_LOCALE;
  const messages = context?.messages ?? ENGLISH_MESSAGES;
  return useMemo(
    () =>
      createTranslator<PublicMessages, N>({
        locale: intlLocale(locale),
        messages,
        namespace,
        timeZone: PUBLIC_TIME_ZONE,
      }),
    [locale, messages, namespace],
  );
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { NextIntlClientProvider } from "next-intl";

import { cn } from "@/lib/utils";

import { PUBLIC_TIME_ZONE, PublicI18nContext, type PublicI18nValue } from "./context";
import { devanagari } from "./devanagari";
import {
  DEFAULT_LOCALE,
  intlLocale,
  needsDevanagari,
  readStoredLocale,
  writeStoredLocale,
  type PublicLocale,
} from "./locales";
import { ENGLISH_MESSAGES, loadMessages, type PublicMessages } from "./messages";

import "./devanagari.css";

/**
 * English, Hindi and Marathi for `/map` and `/report` (task P9.9) without locale routing.
 *
 * Mounted by those two layouts only. Every component it translates also renders outside it - the
 * dashboard imports the vehicle selector and the legend - so nothing here is a hard dependency:
 * `usePublicT` answers in English when no provider is mounted, and `usePublicLocale` returns null so
 * a control can tell it has nothing to switch.
 */
export function PublicI18nProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<{ locale: PublicLocale; messages: PublicMessages }>({
    locale: DEFAULT_LOCALE,
    messages: ENGLISH_MESSAGES,
  });
  // The last requested locale, so a slow Marathi load cannot overwrite a later pick of English.
  const requested = useRef<PublicLocale>(DEFAULT_LOCALE);

  const apply = useCallback(async (locale: PublicLocale) => {
    requested.current = locale;
    const messages = await loadMessages(locale);
    if (requested.current !== locale) return;
    setState({ locale, messages });
  }, []);

  const setLocale = useCallback(
    async (locale: PublicLocale) => {
      writeStoredLocale(locale);
      await apply(locale);
    },
    [apply],
  );

  // The server renders English; the stored choice is read after hydration so both agree.
  useEffect(() => {
    const stored = readStoredLocale();
    if (stored !== DEFAULT_LOCALE) void apply(stored);
  }, [apply]);

  // Screen readers pick their voice from the document language, so it follows the choice and is
  // put back when the reader leaves for a screen that is English only.
  useEffect(() => {
    const root = document.documentElement;
    const previous = root.lang;
    root.lang = state.locale;
    return () => {
      root.lang = previous;
    };
  }, [state.locale]);

  const value = useMemo<PublicI18nValue>(
    () => ({ locale: state.locale, messages: state.messages, setLocale }),
    [state, setLocale],
  );
  const deva = needsDevanagari(state.locale);

  return (
    <PublicI18nContext.Provider value={value}>
      <NextIntlClientProvider
        locale={intlLocale(state.locale)}
        messages={state.messages}
        timeZone={PUBLIC_TIME_ZONE}
      >
        <div
          lang={state.locale}
          data-script={deva ? "deva" : undefined}
          className={cn("contents", deva && devanagari.variable)}
        >
          {children}
        </div>
      </NextIntlClientProvider>
    </PublicI18nContext.Provider>
  );
}

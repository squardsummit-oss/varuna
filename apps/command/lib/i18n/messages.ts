import en from "@/messages/en.json";

import type { PublicLocale } from "./locales";

/** The message tree. English is the source; Hindi and Marathi must carry exactly its keys. */
export type PublicMessages = typeof en;
export type PublicNamespace = keyof PublicMessages;

export const ENGLISH_MESSAGES: PublicMessages = en;

type Tree = { [key: string]: string | Tree };

/**
 * `overlay` laid over `base`, key by key. A translation that is missing a key falls back to the
 * English string rather than to next-intl's "namespace.key" placeholder, which would put an
 * identifier in front of a commuter. `messages.test.ts` keeps the key sets equal, so in practice
 * this only matters to a file edited without running the tests.
 */
export function mergeMessages<T extends Tree>(base: T, overlay: Tree): T {
  const out: Tree = { ...base };
  for (const [key, value] of Object.entries(overlay)) {
    const current = out[key];
    if (typeof value === "string") {
      if (typeof current === "string" || current === undefined) out[key] = value;
    } else if (current !== undefined && typeof current !== "string") {
      out[key] = mergeMessages(current, value);
    }
  }
  return out as T;
}

/**
 * The messages for a locale. Hindi and Marathi are separate chunks, fetched only when a reader
 * picks them, so an English reader downloads neither.
 */
export async function loadMessages(locale: PublicLocale): Promise<PublicMessages> {
  if (locale === "hi") {
    const hi = (await import("@/messages/hi.json")).default as Tree;
    return mergeMessages(ENGLISH_MESSAGES as unknown as Tree, hi) as unknown as PublicMessages;
  }
  if (locale === "mr") {
    const mr = (await import("@/messages/mr.json")).default as Tree;
    return mergeMessages(ENGLISH_MESSAGES as unknown as Tree, mr) as unknown as PublicMessages;
  }
  return ENGLISH_MESSAGES;
}

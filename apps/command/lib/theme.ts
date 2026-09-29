/**
 * The theme: dark (the control room at 3 a.m., the default) or light (the same room in daylight).
 *
 * The choice lives in one place - the `data-theme` attribute on `<html>` - because every colour in
 * the app is a token CSS variable and `packages/tokens` writes the light values under
 * `:root[data-theme="light"]`. Switching the attribute therefore restyles the whole page at once,
 * with no React render in its path (motion M37: instant). This module keeps that attribute, the
 * stored preference and React in step:
 *
 *   - `THEME_BOOTSTRAP_SCRIPT` runs in `<head>` before first paint, so a light reader never sees
 *     a dark frame;
 *   - `useTheme()` is the store React components read and write;
 *   - `useThemeColors()` / `themeColor()` hand deck.gl and canvas code the resolved colours, since
 *     neither can read a CSS custom property.
 *
 * Every storage access is wrapped: a private window, blocked site data or a thumbnail capture can
 * throw on `localStorage`, and the page must still render, in the default theme.
 */

import { useSyncExternalStore } from "react";
import {
  DEFAULT_THEME,
  THEME_ATTRIBUTE,
  THEME_STORAGE_KEY,
  THEMES,
  colorsFor,
  hexToRgba,
  isTheme,
  type Rgba,
  type ThemeColorName,
  type ThemeColorTable,
  type ThemeName,
} from "@varuna/tokens";

import { THEME_BOOTSTRAP_SCRIPT } from "./theme-bootstrap";

export type Theme = ThemeName;
export type { ThemeColorName };
export { DEFAULT_THEME, THEME_STORAGE_KEY, THEMES, isTheme };

/** The resolved colours of one theme as deck.gl `[r, g, b, a]` arrays, alpha 255. */
export type ThemeRgbaTable = { readonly [K in ThemeColorName]: Rgba };

/* ------------------------------------------------------------------------------------------------
 * Storage and the <html> attribute
 * ---------------------------------------------------------------------------------------------- */

/** The stored preference, or null when there is none or storage cannot be read. */
export function readStoredTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(value) ? value : null;
  } catch {
    return null;
  }
}

function writeStoredTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Storage blocked: the choice still applies to this page, it just is not remembered.
  }
}

/** The theme the document is showing now, read from `<html data-theme>`. */
function documentTheme(): Theme {
  if (typeof document === "undefined") return DEFAULT_THEME;
  const value = document.documentElement.getAttribute(THEME_ATTRIBUTE);
  return isTheme(value) ? value : DEFAULT_THEME;
}

/**
 * Puts a theme on the document: the attribute every token keys off, `color-scheme` for native
 * controls and scrollbars, and the browser chrome colour so the address bar matches the page.
 */
export function applyThemeToDocument(theme: Theme): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.setAttribute(THEME_ATTRIBUTE, theme);
  root.style.colorScheme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", colorsFor(theme).ink);
}

export { THEME_BOOTSTRAP_SCRIPT };

/* ------------------------------------------------------------------------------------------------
 * The store
 * ---------------------------------------------------------------------------------------------- */

const listeners = new Set<() => void>();
let current: Theme | null = null;

/**
 * The current theme. On the server, and before anything has run, the default.
 *
 * In the browser the stored choice wins over the attribute: the head script put that choice on
 * the document, but a root that React had to re-render (a hydration failure anywhere on the page)
 * rewrites `<html>` with the server's `data-theme="dark"`, and a first read after that must not
 * adopt the server's answer.
 */
export function getTheme(): Theme {
  if (current === null)
    current = (typeof window !== "undefined" && readStoredTheme()) || documentTheme();
  return current;
}

function notify(): void {
  for (const listener of listeners) listener();
}

/** Switches the theme everywhere at once: document, storage and every subscriber. */
export function setTheme(theme: Theme): void {
  if (!isTheme(theme)) return;
  current = theme;
  applyThemeToDocument(theme);
  writeStoredTheme(theme);
  notify();
}

/** Dark to light, light to dark. */
export function toggleTheme(): void {
  setTheme(getTheme() === "dark" ? "light" : "dark");
}

/** Another tab changed the theme: follow it, without writing it back. */
function onStorage(event: StorageEvent): void {
  if (event.key !== THEME_STORAGE_KEY) return;
  const next = isTheme(event.newValue) ? event.newValue : DEFAULT_THEME;
  if (next === getTheme()) return;
  current = next;
  applyThemeToDocument(next);
  notify();
}

/**
 * Subscribes to theme changes; returns the unsubscribe. The first subscriber also re-applies the
 * current theme, which catches the `theme-color` meta tag when Next placed it after the head
 * script and the script could not reach it.
 */
export function subscribeTheme(listener: () => void): () => void {
  listeners.add(listener);
  if (listeners.size === 1 && typeof window !== "undefined") {
    window.addEventListener("storage", onStorage);
    applyThemeToDocument(getTheme());
    guardDocumentTheme();
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && typeof window !== "undefined")
      window.removeEventListener("storage", onStorage);
  };
}

let guard: MutationObserver | null = null;

/**
 * Keeps `<html data-theme>` on the store's theme once anything has subscribed.
 *
 * When hydration fails anywhere on a page, React 19 client-renders the root and resets the
 * `<html>` singleton to the server's attributes, which is `data-theme="dark"`: a light reader
 * was left with light controls on a dark page (seen on /dashboard, 2026-09-29). The observer puts
 * the chosen theme back in the same task. It never fights a real switch, because every switch
 * goes through `setTheme`, which moves the store first. Installed once and kept for the page.
 */
function guardDocumentTheme(): void {
  if (guard !== null || typeof MutationObserver === "undefined") return;
  guard = new MutationObserver(() => {
    if (documentTheme() !== getTheme() || !document.documentElement.hasAttribute(THEME_ATTRIBUTE))
      applyThemeToDocument(getTheme());
  });
  guard.observe(document.documentElement, { attributes: true, attributeFilter: [THEME_ATTRIBUTE] });
}

const serverTheme = (): Theme => DEFAULT_THEME;

export interface UseThemeResult {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggle: () => void;
}

/**
 * The theme for React. The server and the first client render agree on the default, then the
 * client snapshot takes over, so the head script's choice never causes a hydration error.
 */
export function useTheme(): UseThemeResult {
  const theme = useSyncExternalStore(subscribeTheme, getTheme, serverTheme);
  return { theme, setTheme, toggle: toggleTheme };
}

/* ------------------------------------------------------------------------------------------------
 * Colours for deck.gl and canvas
 * ---------------------------------------------------------------------------------------------- */

/** Hex of a token colour in a theme (default: the current theme), e.g. `themeColor("ink")`. */
export function themeColor(name: ThemeColorName, theme: Theme = getTheme()): string {
  return (colorsFor(theme) as ThemeColorTable)[name];
}

/** `[r, g, b, a]` of a token colour in a theme, alpha 0-255 (default 255). */
export function themeRgba(name: ThemeColorName, alpha = 255, theme: Theme = getTheme()): Rgba {
  return hexToRgba(themeColor(name, theme), alpha);
}

const rgbaTables = new Map<Theme, ThemeRgbaTable>();

/** Every colour of a theme as `[r, g, b, a]`, built once per theme and frozen. */
export function themeRgbaTable(theme: Theme): ThemeRgbaTable {
  let table = rgbaTables.get(theme);
  if (!table) {
    const colours = colorsFor(theme) as ThemeColorTable;
    table = Object.freeze(
      Object.fromEntries(
        Object.entries(colours).map(([name, hex]) => [name, Object.freeze(hexToRgba(hex))]),
      ),
    ) as ThemeRgbaTable;
    rgbaTables.set(theme, table);
  }
  return table;
}

/**
 * The current theme's colours as `[r, g, b, a]` arrays for deck.gl and canvas. The table is the
 * same object for as long as the theme is, so it is safe in a `useMemo` dependency list or an
 * `updateTriggers` entry, and a theme switch re-renders the caller with the new table.
 */
export function useThemeColors(): ThemeRgbaTable {
  const { theme } = useTheme();
  return themeRgbaTable(theme);
}

/** Test seam: forget the cached theme so the next read comes from the document again. */
export function __resetThemeForTests(): void {
  current = null;
  listeners.clear();
  guard?.disconnect();
  guard = null;
}

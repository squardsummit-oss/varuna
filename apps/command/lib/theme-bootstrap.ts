/**
 * The inline `<head>` script that puts the stored theme on `<html>` before first paint.
 *
 * Kept apart from `lib/theme.ts` because the root layout is a Server Component and must not pull
 * React hooks into its module graph; this file imports nothing but the token constants.
 */

import {
  DEFAULT_THEME,
  THEME_ATTRIBUTE,
  THEME_STORAGE_KEY,
  THEMES,
  colorsFor,
} from "@varuna/tokens";

/**
 * The inline `<head>` script. It runs synchronously while the HTML parses, before first paint, so
 * the stored theme is on `<html>` before anything is drawn. Built from the token constants so the
 * key, the attribute and the colours cannot drift from what this module reads.
 */
export const THEME_BOOTSTRAP_SCRIPT = [
  "(function(){",
  `var k=${JSON.stringify(THEME_STORAGE_KEY)},a=${JSON.stringify(THEME_ATTRIBUTE)},d=${JSON.stringify(DEFAULT_THEME)};`,
  `var inks=${JSON.stringify(Object.fromEntries(THEMES.map((name) => [name, colorsFor(name).ink])))};`,
  "var t=d;",
  "try{var s=window.localStorage.getItem(k);if(s&&Object.prototype.hasOwnProperty.call(inks,s))t=s;}catch(e){}",
  "var r=document.documentElement;r.setAttribute(a,t);r.style.colorScheme=t;",
  'var m=document.querySelector(\'meta[name="theme-color"]\');if(m)m.setAttribute("content",inks[t]);',
  "})();",
].join("");

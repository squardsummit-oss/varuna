/**
 * The Google Maps browser key, and what the citizen map does when it is not usable.
 *
 * Google is a **basemap only** on this product (TECH_SPEC 0). Every route, every marker and every
 * depth on the citizen screen comes from VARUNA's own artifacts, so a refused key costs the reader
 * a prettier basemap and nothing else.
 *
 * **That is our discipline, not the key's.** This file used to say the key was HTTP-referrer
 * restricted and that Directions, Geocoding, Static Maps and Places answered `REQUEST_DENIED`.
 * That was true of the key this build carried until 2026-09-23 - which turned out not to be this
 * team's key at all, but MCGM's own browser key, read off their public page and left in
 * `.env.local`; naturally its referrer list named none of our origins, which is what ADR-0059
 * measured and what `TASKS.md` D-25 was written about. The key now in use belongs to this team,
 * and measured the same day it answers **200** to Static Maps, Geocoding *and* Directions, and to
 * the Map Tiles tileset from curl with no Referer at all. Nothing server-side stops this app
 * calling a Google routing API; only this rule does.
 *
 * Two consequences worth stating where they will be read. The routing story in section 2.2 is
 * VARUNA's own time-dependent Dijkstra and must stay that way to mean anything. And a
 * `NEXT_PUBLIC_*` value is inlined into the JavaScript every visitor downloads, so an
 * unrestricted key on a billed project is a bill anyone can run up: it must carry an HTTP-referrer
 * restriction and an API allow-list before it is deployed.
 *
 * The reader copies `components/map/basemap.ts`'s rule for a public variable exactly: Next inlines
 * an unset `NEXT_PUBLIC_*` as the literal string "undefined", so that reads as absent, as does
 * "null" and as does whitespace.
 */

/**
 * The key, or `null` when there is none to use.
 *
 * `process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` is written out in full because Next replaces that
 * exact expression at build time; a computed lookup would read an empty object in the browser.
 */
export function googleMapsKey(): string | null {
  const raw = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY;
  if (typeof raw !== "string") return null;
  const value = raw.trim();
  if (!value || value === "undefined" || value === "null") return null;
  return value;
}

/**
 * How long the citizen map waits for Google's bootstrap before drawing VARUNA's own map instead
 * (TECH_SPEC 2.5).
 *
 * Four seconds, not forty: a reader standing in the rain is owed a map, and the fallback carries
 * the same streets, the same depths and the same route. A slow network is therefore a different
 * basemap, never a blank pane.
 */
export const GOOGLE_BOOTSTRAP_TIMEOUT_MS = 4_000;

/** Why the citizen map is drawing VARUNA's own basemap rather than Google's. */
export type GoogleFallbackReason = "no-key" | "timeout" | "error" | "refused";

/**
 * Install Google's authentication-failure callback, returning a function that removes it.
 *
 * This is the only way an application hears about `RefererNotAllowedMapError` and its siblings.
 * The script loads, `google.maps` exists and `<Map>` mounts happily, so neither the bootstrap
 * timeout nor the provider's `onError` fires - and what the reader gets is Google's own grey
 * surface reading "This page didn't load Google Maps correctly", which is somebody else's error
 * message on our screen. Measured on 2026-09-19 at `http://localhost:3000/dashboard`, where the
 * supplied key's referrer list does not include the origin (ADR-0059).
 *
 * Google looks the callback up by name on `window` at the moment it fails, so it is a global and
 * there can be only one; the previous value is restored on teardown rather than deleted.
 */
export function onGoogleAuthFailure(handler: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  const target = window as typeof window & { gm_authFailure?: () => void };
  const previous = target.gm_authFailure;
  target.gm_authFailure = handler;
  return () => {
    if (target.gm_authFailure === handler) target.gm_authFailure = previous;
  };
}

/**
 * The notice shown beside the fallback map, one sentence per cause.
 *
 * Each names what happened and what the reader is looking at instead, per SPEC.md 6.8 - never
 * "something went wrong", and never silence, because a reader who cannot tell which basemap they
 * are on cannot tell whether the water is real either.
 */
export function googleFallbackNotice(reason: GoogleFallbackReason): string {
  if (reason === "no-key") {
    return "No Google Maps key is configured; showing VARUNA's own map.";
  }
  if (reason === "timeout") {
    return "Google Maps did not load in time; showing VARUNA's own map.";
  }
  if (reason === "refused") {
    // **It does not say which refusal it was, because we are not told.** This branch is driven by
    // `gm_authFailure`, and Google fires that callback with no argument for every auth failure -
    // `RefererNotAllowedMapError` and `InvalidKeyMapError` alike. The sentence was written on
    // 2026-09-19 against a referrer refusal (ADR-0059) and said "for this address", which on
    // 2026-09-24 was measurably the wrong cause: with a 39-character key in
    // `apps/command/.env.local`, the console error on `/dashboard` reads `InvalidKeyMapError`,
    // meaning the Maps JavaScript API is not enabled for that key's project or the key is
    // restricted away from it - nothing to do with the address. Naming a cause we cannot observe
    // sends the reader to the wrong page of the Cloud console, so this names both.
    return (
      "Google rejected this key - either the Maps JavaScript API is not enabled for it, or this " +
      "address is not on its referrer list; showing VARUNA's own map."
    );
  }
  return "Google Maps did not load; showing VARUNA's own map.";
}

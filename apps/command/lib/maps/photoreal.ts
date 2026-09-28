/**
 * Google's Photorealistic 3D Tiles: whether they can be drawn at all, and the credits that must
 * be shown when they are.
 *
 * **What this replaces.** 3D mode used to drape a depth frame over a Terrarium heightmap exported
 * from the city's own conditioned DEM (`components/map/layers/terrain.ts`, ADR-0065). That ground
 * is geometrically the one the Twin routed water over, which is honest, but it is bare: no
 * buildings, no bridges, no rail embankment. The photorealistic tileset puts the water on a
 * photographed Mumbai instead, so a judge looking at Hindmata sees the underpass that floods
 * rather than a dip in a grey surface.
 *
 * **What the service answers today.** Measured here with curl on 2026-09-23, against the Cloud
 * project this build now points at - billing linked, Map Tiles API enabled:
 * `GET https://tile.googleapis.com/v1/3dtiles/root.json` answers **HTTP 200** with about 64.5 KB
 * of 3D Tiles 1.0, four ways over - key in an `X-Goog-Api-Key` header, key in a `?key=` query
 * parameter, `Referer: http://localhost:3000/`, and **no `Referer` header at all**. With no key
 * it answers 403 `PERMISSION_DENIED`, "Method doesn't allow unregistered callers". The key
 * carries no HTTP-referrer restriction today, so a command line is as honest a test of it as a
 * browser is. Traversed from the global root down to Hindmata junction (19.012 N, 72.841 E),
 * following the child whose oriented box contains the point with 150 m of height slack for the
 * geoid: 6 nested sub-tilesets, finest geometric error 2.006 m, finest tile 13,900 bytes of
 * glTF 2.0 carrying `asset.copyright: "Google;Airbus"`. Two metres is fifteen times finer than
 * the 30 m DEM the Terrarium ground was built from, which is the whole reason for the swap.
 *
 * **Why a probe exists at all, then.** It is dormant and kept, not dead. The tileset is a
 * separate Google product from the Maps JavaScript API, with its own switch in the Cloud console
 * and its own dependence on the project having billing linked, and both gates were shut earlier
 * on this same day. What follows is the log of getting through them, not a measurement anyone can
 * repeat on this key now: with the Map Tiles API switched off the identical request answered
 * **403**
 * `PERMISSION_DENIED` / `SERVICE_DISABLED` ("Map Tiles API has not been used in project <n>
 * before or it is disabled"), and on two earlier projects with no billing linked every Map Tiles
 * method answered **404 `NOT_FOUND`** while Static Maps answered 403 asking for billing - a
 * diagnosis worth keeping, because a 404 reads as a wrong URL when the gate was money. Any
 * deployment that has not been through those two switches will meet them. Without the probe they
 * reach the reader as an empty 3D view and a pile of red tile requests in the network panel:
 * Google's silence presented as ours. With it, the console says which switch is off and where to
 * throw it.
 *
 * **Nothing in this module logs.** SPEC.md 14 makes a console error during the demo run a
 * failing gate, and a disabled API is a state to render, not a crash to report.
 *
 * **The key is never put in a URL.** It travels in a request header, here and in the layer's
 * `loadOptions`, so it never lands in a browser history entry, a referrer or a server log line.
 * It is read only through `googleMapsKey()`, which reads the one literal expression Next inlines.
 *
 * **Credits are a licence term, not a nicety.** Google's Map Tiles policy requires the data
 * providers of the tiles *currently on screen* to be displayed, which is why `mergeCredits` and
 * the store below exist: the tileset hands its copyright strings to `onTraversalComplete` on
 * every traversal, many times a second, and pushing that through React state would re-render the
 * console at frame rate. The store merges and compares first, so React sees the handful of
 * updates a flight across Mumbai actually produces.
 */

import { useEffect, useState, useSyncExternalStore } from "react";

import { googleMapsKey } from "./google";

/** Google's root tileset for the global photorealistic mesh. */
export const PHOTOREAL_TILESET_URL = "https://tile.googleapis.com/v1/3dtiles/root.json";

/**
 * The header the key travels in.
 *
 * Google documents `X-GOOG-API-KEY` for the Map Tiles API, and the root tileset accepts it:
 * verified above on 2026-09-23, where the header form answered HTTP 200 with the same tileset the
 * `?key=` query form returns - the two bodies differ only in their per-request session and file
 * tokens. HTTP header names are case-insensitive, but one spelling is exported so the probe and
 * the layer cannot drift apart.
 */
export const PHOTOREAL_KEY_HEADER = "X-GOOG-API-KEY";

/** Why there are no photorealistic tiles to draw. */
export type PhotorealReason =
  | "no-key"
  | "api-disabled"
  | "no-billing"
  | "refused"
  | "offline"
  | "error";

export type PhotorealState =
  | { kind: "off" }
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "unavailable"; reason: PhotorealReason; message: string };

/**
 * The one sentence shown where the photorealistic basemap would have been.
 *
 * Each names what happened and what to do about it (SPEC.md 6.8) - never "something went
 * wrong", and never silence, because an operator who cannot tell why the city is grey cannot
 * tell whether the water on it is wrong for the same reason.
 *
 * `detail` is the server's own sentence, quoted rather than paraphrased when Google gave one: a
 * message VARUNA invented for a status it does not recognise would be a guess presented as a
 * diagnosis.
 */
export function photorealNotice(reason: PhotorealReason, detail?: string): string {
  switch (reason) {
    case "no-key":
      return "No Google Maps key is configured, so the photorealistic basemap is off; set NEXT_PUBLIC_GOOGLE_MAPS_API_KEY and restart the app.";
    case "api-disabled":
      return "Google's Map Tiles API is not enabled on this key's Google Cloud project, so there are no photorealistic tiles to draw; enable the Map Tiles API on that project and switch 3D on again.";
    case "no-billing":
      // Measured twice on 2026-09-23, on three different Cloud projects: an unbilled project
      // answers **every** Map Tiles method with a bare 404 "Requested entity was not found.",
      // naming neither billing nor the project, while Static Maps on the same key says "You must
      // enable Billing" outright. The first time, that 404 cost an hour. The second time it was
      // this key, hours after it had been serving 200s, when the billing came off the project
      // again - and the console told the reader only that Google had said something it did not
      // recognise. It recognises it now.
      return "Google answers this key with a 404, which for the Map Tiles API means the Cloud project has no billing account linked, so the photorealistic basemap is off; link a billing account to that project and switch 3D on again.";
    case "refused":
      return "Google refused this key for the Map Tiles API, so the photorealistic basemap is off; check the key's API and referrer restrictions in the Google Cloud console.";
    case "offline":
      return "Google's tile server could not be reached, so the photorealistic basemap is off; the photographed city needs a network, and VARUNA's own map does not.";
    default:
      return detail
        ? `Google's tile server answered something VARUNA does not recognise - "${detail}" - so the photorealistic basemap is off; fix that in the Google Cloud console and switch 3D on again.`
        : "Google's tile server answered something VARUNA does not recognise, so the photorealistic basemap is off; switch 3D on again once the Map Tiles API is answering.";
  }
}

/**
 * The server's own sentence, where it gave one.
 *
 * Google's errors are `{"error": {"code", "message", "status", "details"[]}}` and the `message`
 * is a complete, quotable sentence written for a developer. A body that is not that shape falls
 * back to its first non-empty line, capped, so a proxy's HTML error page cannot paste a page of
 * markup into a map chip.
 */
export function serverSentence(body: string): string | undefined {
  const trimmed = body.trim();
  if (!trimmed) return undefined;
  try {
    const parsed = JSON.parse(trimmed) as { error?: { message?: unknown } };
    const message = parsed.error?.message;
    if (typeof message === "string" && message.trim()) return message.trim();
  } catch {
    // Not JSON: fall through to the first line.
  }
  const line = trimmed.split("\n").find((candidate) => candidate.trim().length > 0);
  if (!line) return undefined;
  const cleaned = line.trim();
  return cleaned.length > 200 ? `${cleaned.slice(0, 199)}…` : cleaned;
}

/**
 * What an answer from the root tileset means.
 *
 * Split out from the fetch so the classification is tested against the real bodies Google sends
 * rather than against a mock of what we imagine it sends. The disabled-service case is checked
 * before the general refusal because both arrive as 403 and only one of them is about whether
 * this key is allowed near the API at all - they are fixed on different pages of the console, so
 * they cannot share a sentence.
 *
 * On today's key only the `ready` branch is reachable: the live service answered 200 on
 * 2026-09-23. Every failure branch below is therefore held by a recorded fixture rather than by
 * the service, and each is kept because it was real on some project on some day and will be
 * again on the next one - the bodies in the tests are the ones Google actually sent.
 */
export function classifyPhotorealProbe(status: number, body: string): PhotorealState {
  if (status >= 200 && status < 300) return { kind: "ready" };

  const disabled =
    body.includes("SERVICE_DISABLED") || body.includes("Map Tiles API has not been used");
  if (status === 403 && disabled) {
    return {
      kind: "unavailable",
      reason: "api-disabled",
      message: photorealNotice("api-disabled"),
    };
  }
  // A 404 from this endpoint is not a wrong URL: the tileset URL is a constant in this module, so
  // the only thing a reader can get wrong about it is the project behind the key. Empirically it
  // is always the billing link. Checked before `denied` because the body carries no code at all -
  // it would otherwise fall through to the "something we do not recognise" sentence, which is
  // what it did on 2026-09-23 while the fix was one line in a Cloud console.
  const notFound =
    status === 404 ||
    body.includes("NOT_FOUND") ||
    body.includes("Requested entity was not found");
  if (notFound) {
    return { kind: "unavailable", reason: "no-billing", message: photorealNotice("no-billing") };
  }
  const denied = /REQUEST_DENIED|PERMISSION_DENIED|referer|referrer|API key/i.test(body);
  if (status === 401 || status === 403 || denied) {
    return { kind: "unavailable", reason: "refused", message: photorealNotice("refused") };
  }
  const detail = serverSentence(body);
  return {
    kind: "unavailable",
    reason: "error",
    message: photorealNotice("error", detail ?? `HTTP ${status}`),
  };
}

/**
 * Ask the root tileset, once, whether it will serve this key.
 *
 * `fetchImpl` is the seam the tests drive; production passes nothing and gets the platform's
 * `fetch`. A thrown fetch is the network being gone - DNS, an offline venue, a blocking proxy -
 * which is `offline` and not an API problem, and the two need different sentences because only
 * one of them is fixed in a Cloud console.
 */
export async function probePhotorealTileset(
  key: string,
  fetchImpl: typeof fetch = fetch,
): Promise<PhotorealState> {
  let response: Response;
  try {
    response = await fetchImpl(PHOTOREAL_TILESET_URL, {
      headers: { [PHOTOREAL_KEY_HEADER]: key },
    });
  } catch {
    return { kind: "unavailable", reason: "offline", message: photorealNotice("offline") };
  }
  let body = "";
  try {
    body = await response.text();
  } catch {
    // A body that cannot be read still leaves a status to classify.
  }
  return classifyPhotorealProbe(response.status, body);
}

/**
 * One verdict per key for the life of the tab, exactly as `terrain.ts` cached one mesh per city:
 * toggling 3D off and on must not cost a round trip to Google.
 *
 * **Only a `ready` verdict is kept.** A refusal is deliberately re-probed on the next toggle,
 * because every one of the failure reasons is something a person can fix while the console is
 * open - enabling the Map Tiles API, linking billing to the project, adding this origin to a key
 * that is referrer-restricted (this one is not), plugging the network back in - and a cached "no"
 * would make them reload the page to see their own fix.
 */
const verdicts = new Map<string, Promise<PhotorealState>>();

/** Forget every cached verdict. For tests; the application has no reason to call it. */
export function resetPhotorealVerdicts(): void {
  verdicts.clear();
}

/**
 * Whether the photorealistic basemap can be drawn, probed the first time 3D is switched on.
 *
 * Returns `off` while 3D is off, so nothing is asked of Google until a reader wants the view that
 * needs it. The Map Tiles API is metered and needs billing linked to the project before it
 * answers at all, so a request made for a screen nobody asked for is somebody's quota spent on
 * nothing.
 */
export function usePhotorealTileset(enabled: boolean): PhotorealState {
  // Read once: Next inlines the key at build time, so it cannot change while the page is open.
  const [key] = useState(() => googleMapsKey());
  const [state, setState] = useState<PhotorealState | null>(null);

  useEffect(() => {
    if (!enabled || !key) return;
    let cancelled = false;
    let pending = verdicts.get(key);
    if (!pending) {
      pending = probePhotorealTileset(key);
      verdicts.set(key, pending);
      void pending.then(
        (verdict) => {
          if (verdict.kind !== "ready") verdicts.delete(key);
        },
        () => verdicts.delete(key),
      );
    }
    void pending.then(
      (verdict) => {
        if (!cancelled) setState(verdict);
      },
      () => {
        // `probePhotorealTileset` resolves rather than rejects; this is the belt to its braces.
        if (!cancelled) {
          setState({ kind: "unavailable", reason: "error", message: photorealNotice("error") });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [enabled, key]);

  if (!enabled) return { kind: "off" };
  if (!key) return { kind: "unavailable", reason: "no-key", message: photorealNotice("no-key") };
  return state ?? { kind: "loading" };
}

/**
 * The one credits line for a set of tiles, per Google's Map Tiles attribution requirement.
 *
 * Each tile's `asset.copyright` is a semicolon-separated list of the providers that contributed
 * to it, and the separator carries no space: read off real tiles on 2026-09-23, the finest tile
 * over Hindmata junction says `"Google;Airbus"` and a coarse one over the sea west of Mumbai says
 * `"Google"`. A city view is hundreds of such tiles whose lists overlap almost entirely.
 * Splitting, trimming, de-duplicating and sorting turns them into the single line the policy asks
 * for - on the console at `http://localhost:3000/console` on 2026-09-23, with the camera over the
 * AOI, that line came out as "Google Maps; Airbus; Data SIO, NOAA, U.S. Navy, NGA, GEBCO; Google;
 * Landsat / Copernicus".
 *
 * Sorted with an explicit "en" collation rather than by code unit: the line is read by a person,
 * and naming the locale keeps it identical in every tab - which code-unit order would also do,
 * but with every accented provider filed after "Z". Empty and whitespace-only fragments are
 * dropped rather than rendered as stray semicolons, because Google's strings do carry trailing
 * ones.
 */
export function mergeCredits(strings: readonly string[]): string {
  const parts = new Set<string>();
  for (const entry of strings) {
    if (typeof entry !== "string") continue;
    for (const piece of entry.split(";")) {
      const trimmed = piece.trim();
      if (trimmed) parts.add(trimmed);
    }
  }
  return [...parts].sort((a, b) => a.localeCompare(b, "en")).join("; ");
}

/**
 * A credits line that deck can write to from inside its render loop.
 *
 * `onTraversalComplete` runs on every tileset traversal - many times a second while the camera
 * moves - and a `setState` there would re-render the whole console just as often. The store
 * merges and compares first and notifies only on a real change, which is the same trick M8 and
 * M9 use to keep the surcharge pulse and the reversed-flow dash off React's critical path
 * (ADR-0053).
 */
export interface CreditStore {
  /** The merged line, for `useSyncExternalStore` and for a direct read. */
  getCredits(): string;
  /** Merge these tiles' copyright strings in; notify only if the line changed. */
  setCredits(parts: readonly string[]): void;
  subscribe(listener: () => void): () => void;
}

export function createCreditStore(initial = ""): CreditStore {
  let credits = initial;
  const listeners = new Set<() => void>();
  return {
    getCredits: () => credits,
    setCredits(parts) {
      const merged = mergeCredits(parts);
      if (merged === credits) return;
      credits = merged;
      // A copy, so a listener that unsubscribes itself cannot skip the next one.
      for (const listener of [...listeners]) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

/** Subscribe a component to a credit store. It renders only when the line changes. */
export function useMapCredits(store: CreditStore | null | undefined): string {
  return useSyncExternalStore(
    (listener) => store?.subscribe(listener) ?? (() => undefined),
    () => store?.getCredits() ?? "",
    // The server has drawn no tiles, so it has no providers to credit.
    () => "",
  );
}

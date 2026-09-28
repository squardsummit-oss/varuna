/**
 * Verification scores (`GET /v1/verification`; SPEC.md 7.10, 12).
 *
 * The response carries what could be scored and, separately, what could not and why. Both are
 * rendered: a dashboard that quietly omits the scores it cannot compute is a dashboard that
 * flatters itself.
 */

import { apiUrl } from "@/lib/api/client";

export interface Contingency {
  hits: number;
  misses: number;
  falseAlarms: number;
}

export interface ThresholdRow {
  thresholdCm: number;
  contingency: Contingency;
  csi: number | null;
  pod: number | null;
  far: number | null;
  medianLeadMin: number | null;
  nHitsEarly: number;
  nHitsAfter: number;
}

export interface MatchedPin {
  pinId: string;
  name: string;
  kind: string;
  pinTs: string;
  forecastTs: string;
  leadMin: number;
  sourceUrl: string;
}

export interface MissedPin {
  pinId: string;
  name: string;
  kind: string;
  pinTs: string;
  deepestNearbyCm: number;
  reason: string;
  sourceUrl: string;
}

export interface Verification {
  event: string;
  runIds: string[];
  window: [string, string] | null;
  headlineThresholdCm: number;
  nPins: number;
  nInWindow: number;
  byThreshold: ThresholdRow[];
  headline: ThresholdRow;
  matched: MatchedPin[];
  missed: MissedPin[];
  unavailable: Record<string, string>;
  notes: string[];
}

function row(raw: Record<string, unknown>): ThresholdRow {
  const c = (raw.contingency ?? {}) as Record<string, number>;
  const s = (raw.scores ?? {}) as Record<string, number | null>;
  return {
    thresholdCm: Number(raw.threshold_cm ?? 0),
    contingency: {
      hits: Number(c.hits ?? 0),
      misses: Number(c.misses ?? 0),
      falseAlarms: Number(c.false_alarms ?? 0),
    },
    csi: s.csi ?? null,
    pod: s.pod ?? null,
    far: s.far ?? null,
    medianLeadMin: s.median_lead_min ?? null,
    nHitsEarly: Number(s.n_hits_early ?? 0),
    nHitsAfter: Number(s.n_hits_after ?? 0),
  };
}

/** The event the replay demo scores itself on (SPEC.md 15, `public/verification.json`). */
export const DEFAULT_EVENT = "MUM-2019-07-02";

/** Where the committed copy lives; the same file the landing page's proof section falls back to. */
export const COMMITTED_VERIFICATION_PATH = "/verification.json";

/**
 * The one score the top bar prints, as the scorer marks it: CSI at `headline_threshold_cm`.
 *
 * `source` says whether the API computed it just now or the page fell back to the copy committed
 * with the build, so the chip can say which instead of passing an old number off as a fresh one.
 */
export type VerificationHeadline =
  | {
      kind: "scored";
      csi: number;
      thresholdCm: number;
      event: string;
      source: "api" | "committed";
      /** Why the committed copy is showing, for the chip's title; absent on a served score. */
      note?: string;
    }
  | { kind: "unscored"; reason: string }
  | { kind: "error"; message: string };

/** The API never answered: the only case in which the committed copy stands in for it. */
export type ServedHeadline = VerificationHeadline | { kind: "unreachable"; why: string };

/** The headline of a raw `/v1/verification` body; "unscored" when it carries no CSI there. */
export function headlineFromBody(
  body: Record<string, unknown>,
  source: "api" | "committed",
): VerificationHeadline {
  const thresholdCm = Number(body.headline_threshold_cm ?? body.threshold_cm);
  const rows = (body.by_threshold ?? {}) as Record<string, Record<string, unknown>>;
  const match = Object.values(rows).find((r) => Number(r.threshold_cm) === thresholdCm);
  // Old bodies without the sweep carry the headline at the top level.
  const scores = ((match ?? body).scores ?? {}) as Record<string, unknown>;
  const csi = scores.csi;
  if (!Number.isFinite(thresholdCm) || typeof csi !== "number" || !Number.isFinite(csi)) {
    return {
      kind: "unscored",
      reason: "The scorer returned no CSI at its headline threshold for this event.",
    };
  }
  return { kind: "scored", csi, thresholdCm, event: String(body.event ?? DEFAULT_EVENT), source };
}

/** Status codes that mean "the API is not answering", not "the API answered no". */
const UNREACHABLE_STATUS = new Set([502, 503, 504]);

/**
 * How long a served score is waited for before the API counts as unreachable.
 *
 * `/v1/verification` scores the event on every request: 33 s on a cold local API, 9-10 s warm, and
 * 67.6 s while the same API was serving a console's run load, measured 2026-09-15 on the seven
 * demo runs. A tight timeout would fall back on nearly every page load and call a busy API
 * unreachable, which is why the chip shows the committed copy in the meantime
 * (`INTERIM_AFTER_MS`) and keeps waiting this long for the real answer.
 */
export const VERIFICATION_TIMEOUT_MS = 180_000;

/** How long the chip shimmers before it shows the committed copy while the API is still scoring. */
export const INTERIM_AFTER_MS = 4_000;

/** Thrown when the ceiling passes: the API may be busy rather than gone, and the copy says which. */
class VerificationTimeout extends Error {}

async function fetchWithTimeout(url: string, timeoutMs: number): Promise<Response> {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    return await fetch(url, { signal: controller.signal });
  } catch (error) {
    throw timedOut ? new VerificationTimeout() : error;
  } finally {
    clearTimeout(timer);
  }
}

/** The committed copy's headline, carrying `why` it is showing instead of a served score. */
export async function committedHeadline(event: string, why: string): Promise<VerificationHeadline> {
  try {
    const response = await fetch(COMMITTED_VERIFICATION_PATH);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const body = (await response.json()) as Record<string, unknown>;
    if (body.event !== undefined && body.event !== event) {
      return {
        kind: "error",
        message: `${why} The committed scores are for ${String(body.event)}, not ${event}.`,
      };
    }
    const headline = headlineFromBody(body, "committed");
    return headline.kind === "scored"
      ? { ...headline, note: `${why} Showing the scores committed with this build.` }
      : headline;
  } catch {
    return {
      kind: "error",
      message: `${why} The committed copy of the scores did not load either.`,
    };
  }
}

/**
 * Load the headline score, falling back to the committed copy when the API is unreachable.
 *
 * Unreachable means the request never got an answer (network error, timeout) or a gateway said
 * the app behind it did not respond. A real answer is believed: a 404 means the event has no
 * ground truth, which is "not scored", and any other error is shown with the API's own message.
 */
export async function loadVerificationHeadline(
  event = DEFAULT_EVENT,
  timeoutMs = VERIFICATION_TIMEOUT_MS,
): Promise<VerificationHeadline> {
  const served = await fetchServedHeadline(event, timeoutMs);
  return served.kind === "unreachable" ? committedHeadline(event, served.why) : served;
}

/** The API's own answer, or `unreachable` when it gave none. Never reads the committed copy. */
export async function fetchServedHeadline(
  event = DEFAULT_EVENT,
  timeoutMs = VERIFICATION_TIMEOUT_MS,
): Promise<ServedHeadline> {
  const url = apiUrl(`/v1/verification?event=${encodeURIComponent(event)}`);
  let response: Response;
  try {
    response = await fetchWithTimeout(url, timeoutMs);
  } catch (error) {
    return {
      kind: "unreachable",
      why:
        error instanceof VerificationTimeout
          ? `The API did not answer within ${Math.round(timeoutMs / 1000)} s.`
          : "The API is unreachable.",
    };
  }
  if (UNREACHABLE_STATUS.has(response.status)) {
    return { kind: "unreachable", why: `The API answered ${response.status}.` };
  }
  const body = (await response.json().catch(() => null)) as Record<string, unknown> | null;
  if (response.status === 404) {
    const envelope = body?.error as { message?: string } | undefined;
    return { kind: "unscored", reason: envelope?.message ?? `No ground truth for ${event}.` };
  }
  if (!response.ok || !body) {
    const envelope = body?.error as { message?: string } | undefined;
    return {
      kind: "error",
      message: envelope?.message ?? `Verification answered HTTP ${response.status}.`,
    };
  }
  return headlineFromBody(body, "api");
}

/** One request per event per tab: every screen wears the chip, and the score does not change
 * while a page is open. */
const servedCache = new Map<string, Promise<ServedHeadline>>();

function cachedServedHeadline(event: string): Promise<ServedHeadline> {
  let pending = servedCache.get(event);
  if (!pending) {
    pending = fetchServedHeadline(event).then((served) => {
      // A failure is not remembered, so the next screen that mounts the chip asks again.
      if (served.kind === "error" || served.kind === "unreachable") servedCache.delete(event);
      return served;
    });
    servedCache.set(event, pending);
  }
  return pending;
}

/**
 * Report the headline for `event` as it becomes known; returns an unsubscribe.
 *
 * The served score always wins. If it has not arrived after `interimAfterMs`, the committed copy is
 * reported in the meantime with a note saying so, and replaced when the API answers. Only when
 * the API gives no answer at all does the committed copy become the final word.
 */
export function watchVerificationHeadline(
  event: string,
  onHeadline: (headline: VerificationHeadline) => void,
  interimAfterMs = INTERIM_AFTER_MS,
): () => void {
  let live = true;
  let served = false;
  const timer = setTimeout(() => {
    void committedHeadline(event, "The API is still scoring this event.").then((interim) => {
      // An interim failure says nothing the served answer will not say better.
      if (live && !served && interim.kind === "scored") onHeadline(interim);
    });
  }, interimAfterMs);

  void cachedServedHeadline(event).then(async (answer) => {
    if (answer.kind !== "unreachable") {
      served = true;
      clearTimeout(timer);
      if (live) onHeadline(answer);
      return;
    }
    const fallback = await committedHeadline(event, answer.why);
    served = true;
    clearTimeout(timer);
    if (live) onHeadline(fallback);
  });

  return () => {
    live = false;
    clearTimeout(timer);
  };
}

/** Tests only: forget cached headlines so each test sees its own fetch. */
export function resetVerificationHeadlineCache(): void {
  servedCache.clear();
}

export async function loadVerification(
  event = "MUM-2019-07-02",
  signal?: AbortSignal,
): Promise<Verification> {
  const response = await fetch(apiUrl(`/v1/verification?event=${encodeURIComponent(event)}`), {
    signal,
  });
  const body = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    const envelope = body.error as { message?: string } | undefined;
    throw new Error(envelope?.message ?? `Verification failed: HTTP ${response.status}`);
  }

  const truth = (body.ground_truth ?? {}) as Record<string, number>;
  const rows = Object.values(
    (body.by_threshold ?? {}) as Record<string, Record<string, unknown>>,
  ).map(row);
  rows.sort((a, b) => a.thresholdCm - b.thresholdCm);
  const headlineCm = Number(body.headline_threshold_cm ?? 15);

  return {
    event: String(body.event ?? event),
    runIds: (body.run_ids as string[]) ?? [],
    window: (body.window as [string, string] | null) ?? null,
    headlineThresholdCm: headlineCm,
    nPins: Number(truth.n_pins ?? 0),
    nInWindow: Number(truth.n_in_window ?? 0),
    byThreshold: rows,
    headline: rows.find((r) => r.thresholdCm === headlineCm) ?? row(body),
    matched: ((body.matched as Record<string, unknown>[]) ?? []).map((m) => ({
      pinId: String(m.pin_id ?? ""),
      name: String(m.name ?? ""),
      kind: String(m.kind ?? ""),
      pinTs: String(m.pin_ts ?? ""),
      forecastTs: String(m.forecast_ts ?? ""),
      leadMin: Number(m.lead_min ?? 0),
      sourceUrl: String(m.source_url ?? ""),
    })),
    missed: ((body.missed as Record<string, unknown>[]) ?? []).map((m) => ({
      pinId: String(m.pin_id ?? ""),
      name: String(m.name ?? ""),
      kind: String(m.kind ?? ""),
      pinTs: String(m.pin_ts ?? ""),
      deepestNearbyCm: Number(m.deepest_nearby_cm ?? 0),
      reason: String(m.reason ?? ""),
      sourceUrl: String(m.source_url ?? ""),
    })),
    unavailable: (body.unavailable as Record<string, string>) ?? {},
    notes: (body.notes as string[]) ?? [],
  };
}

/**
 * Keeps the deployed site readable when its forecast server is asleep or gone (ADR-0097).
 *
 * The hosted API runs on Railway's Free plan. A service there sleeps a few minutes after its last
 * request and has $1 of usage a month, so two things happen that never happen on the laptop:
 *   - the first requests after a quiet spell wait while the container wakes, or fail outright;
 *   - if the month's allowance runs out, the server is gone until the next month starts.
 * Railway answers for a sleeping or stopped service itself, without CORS headers, so in a browser
 * every one of those answers is a plain network error. An answer the API gave - a 404 for a run
 * that does not exist, a 503 for a busy cycle - always carries CORS headers and passes through
 * untouched.
 *
 * Every screen reaches the API through the global `fetch`: about fifty call sites, plus deck.gl's
 * image loads. So the guard wraps `fetch` once, before any screen mounts, rather than each caller.
 * For a request to the API it:
 *   - serves a GET from the stored replay (`/mirror`) when the server is not answering and the
 *     mirror holds that exact request, so a map never sits empty while the server wakes;
 *   - otherwise waits for the server to wake, up to WAKE_BUDGET_MS, and sends the request again;
 *   - publishes what the reader is looking at (`useServerStatus`), which the banner shows.
 *
 * The mirror is what the real API answered for the baked 2 July 2019 cycles, recorded by
 * `scripts/record-mirror.mjs` and keyed by path and sorted query. Nothing that changes with time -
 * a live run, citizen reports, today's weather, a route - is in it, so the mirror can only ever
 * repeat what the server itself would say about the replay.
 *
 * Off on the laptop, where the API is local and a network error means it is not running.
 */
import { create } from "zustand";

import { apiUrl, isRemoteApi } from "./client";

/** Where the stored replay lives on this site, and its index. */
export const MIRROR_BASE = "/mirror";
export const MIRROR_INDEX = `${MIRROR_BASE}/index.json`;

/** How long a request may wait for a sleeping server before it is given up on. */
export const WAKE_BUDGET_MS = 90_000;
/** Gap between wake probes while the server is waking. */
export const PROBE_INTERVAL_MS = 3_000;
/** Each probe's own timeout. */
export const PROBE_TIMEOUT_MS = 10_000;
/** How often a server given up on is asked again, so the page notices it coming back. */
export const OFFLINE_REPROBE_MS = 60_000;
/**
 * How long an API request may go without an answer, while the server's state is unknown, before
 * a GET the mirror holds is answered from it. A healthy API sends headers within a few seconds
 * even for its largest layer; a sleeping one holds the request for as long as waking takes.
 */
export const FIRST_ANSWER_MS = 6_000;
/** A server that answered this recently is treated as awake: its requests are not raced. */
export const KNOWN_UP_MS = 120_000;
/**
 * When a request is slow, how long a `/healthz` probe gets to show the server is awake and merely
 * busy - the verification sweep takes seconds on a cold process - before the mirror answers.
 */
export const ALIVE_GRACE_MS = 4_000;

/**
 * `ok`: the server answers. `waking`: it stopped answering and is being woken. `offline`: it did
 * not wake within the budget. `back`: it answers again after the page was shown stored data.
 */
export type ServerState = "ok" | "waking" | "offline" | "back";

export interface ServerStatus {
  state: ServerState;
  /** True once any response on this page came from the stored replay. */
  servedMirror: boolean;
  setState: (state: ServerState) => void;
  markMirrorServed: () => void;
}

export const useServerStatus = create<ServerStatus>((set) => ({
  state: "ok",
  servedMirror: false,
  setState: (state) => set({ state }),
  markMirrorServed: () => set({ servedMirror: true }),
}));

/**
 * The mirror's key for a request: the path and its query parameters sorted by name, encoded the
 * way `URLSearchParams` encodes them. The recorder uses this same function, so a request matches
 * its recording whatever order its caller built the query in.
 */
export function mirrorKey(url: URL): string {
  const params = new URLSearchParams(url.search);
  params.sort();
  const query = params.toString();
  return query ? `${url.pathname}?${query}` : url.pathname;
}

export interface MirrorEntry {
  /** File name under MIRROR_BASE. */
  file: string;
  /** Content type the API sent. */
  type: string;
  /** "gzip" when the file is stored compressed (everything but images). */
  encoding?: "gzip";
}

/** True when `bytes` starts with the gzip signature. */
function isGzip(bytes: Uint8Array): boolean {
  return bytes.length > 2 && bytes[0] === 0x1f && bytes[1] === 0x8b;
}

/**
 * A stored file's bytes as the API sent them. The check is on the bytes rather than the index: a
 * host that decompresses `.gz` on the way (with Content-Encoding) hands over the plain body, and
 * that must not be decompressed twice.
 */
async function storedBody(response: Response, entry: MirrorEntry): Promise<ArrayBuffer | null> {
  const buffer = await response.arrayBuffer();
  if (entry.encoding !== "gzip" || !isGzip(new Uint8Array(buffer))) return buffer;
  if (typeof DecompressionStream === "undefined") return null;
  const source = new Response(buffer).body;
  if (!source) return null;
  return new Response(source.pipeThrough(new DecompressionStream("gzip"))).arrayBuffer();
}

export interface MirrorIndex {
  version: number;
  generated_at?: string;
  entries: Record<string, MirrorEntry>;
}

type FetchFn = typeof fetch;

export interface ResilientFetchOptions {
  /** The API base, without a trailing slash. */
  apiBase: string;
  /** Same-origin fetch for the mirror and the probes, never wrapped. */
  original: FetchFn;
  /** Test seams. */
  now?: () => number;
  sleep?: (ms: number, signal?: AbortSignal | null) => Promise<void>;
  store?: Pick<ServerStatus, "setState" | "markMirrorServed"> & { getState?: () => ServerStatus };
}

function abortError(): DOMException {
  return new DOMException("The operation was aborted.", "AbortError");
}

function defaultSleep(ms: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** A network failure - refused, reset, CORS-blocked - as opposed to an answer or an abort. */
function isNetworkFailure(error: unknown): boolean {
  if (error instanceof DOMException && (error.name === "AbortError" || error.name === "TimeoutError")) {
    return false;
  }
  return error instanceof TypeError;
}

/** 502 and 504 are the platform's, never the API's; 503 the API uses for a busy cycle. */
function isPlatformStatus(status: number): boolean {
  return status === 502 || status === 504;
}

function requestUrl(input: RequestInfo | URL): string | null {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  if (typeof Request !== "undefined" && input instanceof Request) return input.url;
  return null;
}

function requestMethod(input: RequestInfo | URL, init?: RequestInit): string {
  if (init?.method) return init.method.toUpperCase();
  if (typeof Request !== "undefined" && input instanceof Request) return input.method.toUpperCase();
  return "GET";
}

function requestSignal(input: RequestInfo | URL, init?: RequestInit): AbortSignal | null {
  if (init?.signal) return init.signal;
  if (typeof Request !== "undefined" && input instanceof Request) return input.signal;
  return null;
}

/**
 * Wraps `original` so requests to `apiBase` survive a sleeping or stopped server. Exported for
 * tests; the app installs it once with `installResilientFetch`.
 */
export function createResilientFetch(options: ResilientFetchOptions): FetchFn {
  const { apiBase, original } = options;
  const now = options.now ?? (() => Date.now());
  const sleep = options.sleep ?? defaultSleep;
  const store = options.store ?? useServerStatus.getState();
  const prefix = `${apiBase}/`;

  let state: ServerState = "ok";
  let knownUpAt = 0;
  let servedMirror = false;
  let wake: Promise<boolean> | null = null;
  let reprobe: ReturnType<typeof setTimeout> | null = null;
  let index: Promise<Record<string, MirrorEntry> | null> | null = null;

  const setState = (next: ServerState) => {
    if (state === next) return;
    state = next;
    store.setState(next);
  };

  const markUp = () => {
    knownUpAt = now();
    if (reprobe) {
      clearTimeout(reprobe);
      reprobe = null;
    }
    if (state !== "ok" && state !== "back") setState(servedMirror ? "back" : "ok");
  };

  // The index and the stored files are fetched with the browser's default caching: the host marks
  // them must-revalidate, so a redeployed mirror is never read from a stale copy.
  const loadIndex = (): Promise<Record<string, MirrorEntry> | null> => {
    index ??= original(MIRROR_INDEX)
      .then((response) => (response.ok ? (response.json() as Promise<MirrorIndex>) : null))
      .then((body) => (body && typeof body === "object" && body.entries ? body.entries : null))
      .catch(() => {
        // A failed index load is retried on the next request rather than remembered as "none".
        index = null;
        return null;
      });
    return index;
  };

  const fromMirror = async (url: URL): Promise<Response | null> => {
    const entries = await loadIndex();
    const entry = entries?.[mirrorKey(url)];
    if (!entry) return null;
    try {
      const stored = await original(`${MIRROR_BASE}/${entry.file}`);
      if (!stored.ok) return null;
      const body = await storedBody(stored, entry);
      if (!body) return null;
      servedMirror = true;
      store.markMirrorServed();
      return new Response(body, {
        status: 200,
        headers: { "content-type": entry.type, "x-varuna-mirror": "1" },
      });
    } catch {
      return null;
    }
  };

  /** One probe of `/healthz`; true when the API itself answered. */
  const probe = async (): Promise<boolean> => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
    try {
      const response = await original(`${apiBase}/healthz`, { cache: "no-store", signal: controller.signal });
      return response.ok;
    } catch {
      return false;
    } finally {
      clearTimeout(timer);
    }
  };

  /** Whether the server answers a probe within ALIVE_GRACE_MS; shared by concurrent slow requests. */
  let aliveCheck: Promise<boolean> | null = null;
  const answersSoon = (): Promise<boolean> => {
    aliveCheck ??= Promise.race([probe(), sleep(ALIVE_GRACE_MS).then(() => false)]).finally(() => {
      aliveCheck = null;
    });
    return aliveCheck;
  };

  const scheduleReprobe = () => {
    if (reprobe) return;
    reprobe = setTimeout(() => {
      reprobe = null;
      void probe().then((up) => (up ? markUp() : scheduleReprobe()));
    }, OFFLINE_REPROBE_MS);
  };

  /** Starts (or joins) waking the server; resolves true once it answers, false at the budget. */
  const startWake = (): Promise<boolean> => {
    if (state === "offline") return Promise.resolve(false);
    if (wake) return wake;
    setState("waking");
    const started = now();
    wake = (async () => {
      while (now() - started < WAKE_BUDGET_MS) {
        if (await probe()) {
          markUp();
          return true;
        }
        await sleep(PROBE_INTERVAL_MS);
      }
      setState("offline");
      scheduleReprobe();
      return false;
    })().finally(() => {
      wake = null;
    });
    return wake;
  };

  /** Waits for the wake under way, or rejects when the caller gives up first. */
  const awaitWake = (signal: AbortSignal | null): Promise<boolean> => {
    const pending = startWake();
    if (!signal) return pending;
    if (signal.aborted) return Promise.reject(abortError());
    return new Promise((resolve, reject) => {
      const onAbort = () => reject(abortError());
      signal.addEventListener("abort", onAbort, { once: true });
      pending.then(
        (up) => {
          signal.removeEventListener("abort", onAbort);
          resolve(up);
        },
        (error: unknown) => {
          signal.removeEventListener("abort", onAbort);
          reject(error);
        },
      );
    });
  };

  /** Sends the request; a platform 502/504 is reported as the network failure it stands for. */
  const send = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const response = await original(input, init);
    if (isPlatformStatus(response.status)) throw new TypeError(`The platform answered ${response.status}.`);
    markUp();
    return response;
  };

  return async function resilientFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const raw = requestUrl(input);
    if (!raw || !raw.startsWith(prefix)) return original(input, init);
    const url = new URL(raw);
    const method = requestMethod(input, init);
    const signal = requestSignal(input, init);
    const isGet = method === "GET" || method === "HEAD";
    // A Request carrying a body can be sent once; keep a copy for the retry.
    const spare = typeof Request !== "undefined" && input instanceof Request && !isGet ? input.clone() : null;

    // Known down: a GET the mirror holds is answered at once, and the server is woken meanwhile.
    if (isGet && (state === "waking" || state === "offline")) {
      void startWake();
      const stored = await fromMirror(url);
      if (stored) return stored;
    }

    const knownUp = now() - knownUpAt < KNOWN_UP_MS;
    let first: Promise<Response> = send(input, init);
    // Handled here so a request the page stops waiting for never surfaces as an unhandled
    // rejection; awaiting `first` below still sees its failure.
    first.catch(() => undefined);

    // State unknown (a fresh page, or a quiet spell): race the first answer against the mirror.
    if (isGet && !knownUp && state === "ok") {
      const outcome = await Promise.race([
        first.then(
          (response) => ({ kind: "answer" as const, response }),
          (error: unknown) => ({ kind: "error" as const, error }),
        ),
        sleep(FIRST_ANSWER_MS, signal).then(
          () => ({ kind: "slow" as const }),
          (error: unknown) => ({ kind: "error" as const, error }),
        ),
      ]);
      if (outcome.kind === "answer") return outcome.response;
      if (outcome.kind === "slow") {
        // Slow is not asleep: an awake server busy with this request still answers a probe.
        if (await answersSoon()) {
          markUp();
          return first;
        }
        // Keep the slow request: if it answers, it sets the state back to `ok` or `back`.
        void startWake();
        const stored = await fromMirror(url);
        if (stored) return stored;
        return first;
      }
      if (!isNetworkFailure(outcome.error)) throw outcome.error;
      first = Promise.reject(outcome.error);
      first.catch(() => undefined);
    }

    try {
      return await first;
    } catch (error) {
      if (!isNetworkFailure(error)) throw error;
      if (isGet) {
        void startWake();
        const stored = await fromMirror(url);
        if (stored) return stored;
      }
      // Nothing stored for this request: wait for the server, then ask once more.
      const up = await awaitWake(signal);
      if (!up) throw error;
      return send(spare ?? input, init);
    }
  };
}

let installed = false;

/**
 * Installs the guard on `window.fetch` for a remote API. Runs at module load (below), before any
 * screen's effects, so the first request a page makes is already covered.
 */
export function installResilientFetch(force = false): void {
  if (installed || typeof window === "undefined" || typeof window.fetch !== "function") return;
  if (!force && !isRemoteApi() && process.env.NEXT_PUBLIC_VARUNA_RESILIENCE !== "1") return;
  installed = true;
  const original = window.fetch.bind(window);
  window.fetch = createResilientFetch({ apiBase: apiUrl(), original });
}

installResilientFetch();

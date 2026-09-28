/**
 * Typed fetch for the VARUNA API (SPEC.md section 12).
 *
 * - `apiUrl()` / `wsUrl()` resolve the base from NEXT_PUBLIC_API_URL (default localhost:8000).
 * - `apiFetch()` adds a timeout, JSON bodies, query strings and optional zod validation, and
 *   turns every failure into an `ApiError` whose `message` is shown to users verbatim
 *   (section 6.8: errors say what happened and the fix, never "Something went wrong").
 */
import type { ZodType } from "zod";

import { ApiErrorEnvelope } from "./schemas";

export const DEFAULT_API_URL = "http://localhost:8000";
export const DEFAULT_TIMEOUT_MS = 10_000;

/** Whether the API is somewhere other than this machine.
 *
 * A hosted API on a free tier sleeps, and the first request after it wakes can take five seconds
 * before a byte moves. Timeouts tuned for a local `make dev` - four seconds for the replay clock -
 * fire against it on every cold start, which is what put "check that it is running on :8000" over
 * a deployed console that was talking to Railway perfectly well a second later. */
export function isRemoteApi(): boolean {
  const configured = configuredApiUrl();
  if (!configured) return false;
  return !/^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/i.test(configured);
}

/** Timeout for a call, stretched when the API is not on this machine. */
export function timeoutFor(localMs: number): number {
  return isRemoteApi() ? Math.max(localMs * 4, 20_000) : localMs;
}

/** Error codes the client itself produces; server codes come through unchanged. */
export type ClientErrorCode = "network" | "timeout" | "aborted" | "invalid_response" | "http_error";

export class ApiError extends Error {
  readonly name = "ApiError";
  readonly code: string;
  /** HTTP status; 0 when the request never reached the API. */
  readonly status: number;
  readonly runId: string | null;
  readonly path: string;
  /** The raw body when the API did not use the error envelope (FastAPI `detail`, HTML, text). */
  readonly body: unknown;

  constructor(init: {
    code: string;
    message: string;
    status: number;
    path: string;
    runId?: string | null;
    body?: unknown;
    cause?: unknown;
  }) {
    super(init.message, { cause: init.cause });
    this.code = init.code;
    this.status = init.status;
    this.runId = init.runId ?? null;
    this.path = init.path;
    this.body = init.body;
  }

  /** True when retrying is pointless (the request itself is wrong or unimplemented). */
  get isClientFault(): boolean {
    return this.status >= 400 && this.status < 500;
  }

  /** True when the endpoint exists in the contract but its phase has not landed yet. */
  get isNotImplemented(): boolean {
    return this.status === 501 || this.code === "not_implemented";
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError || (value instanceof Error && value.name === "ApiError");
}

/** User-facing message for any thrown value; ApiError messages pass through verbatim. */
export function errorMessage(error: unknown, fallback = "The request failed. Try again."): string {
  if (isApiError(error)) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

function trimSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

/**
 * NEXT_PUBLIC_API_URL when it carries a usable value. Node coerces `process.env.X = undefined`
 * to the string "undefined" (and Next inlines unset vars the same way), so those read as unset.
 */
function configuredApiUrl(): string | undefined {
  const raw = process.env.NEXT_PUBLIC_API_URL;
  if (typeof raw !== "string") return undefined;
  const value = raw.trim();
  if (!value || value === "undefined" || value === "null") return undefined;
  return value;
}

/** Base URL of the API without a trailing slash, or a full URL when `path` is given. */
export function apiUrl(path = ""): string {
  const base = trimSlash(configuredApiUrl() ?? DEFAULT_API_URL);
  if (!path) return base;
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

/** WebSocket URL for `WS /v1/live` derived from the API base (http -> ws, https -> wss). */
export function wsUrl(path = "/v1/live"): string {
  const http = apiUrl(path);
  if (http.startsWith("https://")) return `wss://${http.slice("https://".length)}`;
  if (http.startsWith("http://")) return `ws://${http.slice("http://".length)}`;
  return http;
}

export type QueryValue = string | number | boolean | null | undefined;

/** Builds `?a=1&b=x` from a record, skipping null and undefined values. */
export function toQueryString(query?: Record<string, QueryValue>): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined) continue;
    params.set(key, String(value));
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

export interface ApiFetchOptions<T = unknown> extends Omit<RequestInit, "body"> {
  /** Serialised as JSON unless it is already a BodyInit (FormData, string, Blob). */
  body?: unknown;
  /** Appended as a query string. */
  query?: Record<string, QueryValue>;
  /** Abort after this many milliseconds; default 10 s. */
  timeoutMs?: number;
  /** Validates the JSON body; a mismatch throws `ApiError("invalid_response")`. */
  schema?: ZodType<T>;
  /** Custom fetch (tests, server components). Defaults to the global fetch. */
  fetchImpl?: typeof fetch;
}

function isBodyInit(body: unknown): body is BodyInit {
  if (typeof body === "string") return true;
  if (typeof FormData !== "undefined" && body instanceof FormData) return true;
  if (typeof Blob !== "undefined" && body instanceof Blob) return true;
  if (typeof URLSearchParams !== "undefined" && body instanceof URLSearchParams) return true;
  if (typeof ArrayBuffer !== "undefined" && body instanceof ArrayBuffer) return true;
  return false;
}

function isAbortError(error: unknown): boolean {
  return (
    error instanceof Error &&
    (error.name === "AbortError" || error.name === "TimeoutError")
  );
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text().catch(() => "");
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

/** Turns a non-2xx response into an ApiError, honouring the error envelope when present. */
export function errorFromResponse(path: string, status: number, statusText: string, body: unknown): ApiError {
  const envelope = ApiErrorEnvelope.safeParse(body);
  if (envelope.success) {
    return new ApiError({
      code: envelope.data.error.code,
      message: envelope.data.error.message,
      status,
      path,
      runId: envelope.data.error.run_id ?? null,
      body,
    });
  }
  // FastAPI's default `{ "detail": ... }` for validation errors and HTTPException.
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail
              .map((item) =>
                item && typeof item === "object" && "msg" in item
                  ? String((item as { msg: unknown }).msg)
                  : JSON.stringify(item),
              )
              .join("; ")
          : JSON.stringify(detail);
    return new ApiError({ code: "http_error", message, status, path, body });
  }
  const what = status === 501 ? "is not implemented yet" : `answered ${status} ${statusText}`.trim();
  return new ApiError({
    code: status === 501 ? "not_implemented" : "http_error",
    message: `The API ${what} for ${path}. Check the API log on :8000.`,
    status,
    path,
    body,
  });
}

/**
 * Fetches `path` from the API and returns its JSON body typed as `T`.
 * Throws `ApiError` for network failures, timeouts, non-2xx responses and schema mismatches.
 */
export async function apiFetch<T = unknown>(path: string, options: ApiFetchOptions<T> = {}): Promise<T> {
  const {
    body,
    query,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    schema,
    fetchImpl,
    headers: headersInit,
    signal: callerSignal,
    ...init
  } = options;

  const url = apiUrl(path) + toQueryString(query);
  const doFetch = fetchImpl ?? (typeof fetch === "function" ? fetch : undefined);
  if (!doFetch) {
    throw new ApiError({
      code: "network",
      message: "This environment has no fetch, so the VARUNA API cannot be reached.",
      status: 0,
      path,
    });
  }

  const headers = new Headers(headersInit);
  if (!headers.has("accept")) headers.set("accept", "application/json");

  let requestBody: BodyInit | undefined;
  if (body !== undefined && body !== null) {
    if (isBodyInit(body)) {
      requestBody = body;
    } else {
      requestBody = JSON.stringify(body);
      if (!headers.has("content-type")) headers.set("content-type", "application/json");
    }
  }

  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onCallerAbort = () => controller.abort();
  if (callerSignal) {
    if (callerSignal.aborted) controller.abort();
    else callerSignal.addEventListener("abort", onCallerAbort, { once: true });
  }

  let response: Response;
  try {
    response = await doFetch(url, {
      ...init,
      method: init.method ?? (requestBody ? "POST" : "GET"),
      headers,
      body: requestBody,
      signal: controller.signal,
    });
  } catch (error) {
    clearTimeout(timer);
    callerSignal?.removeEventListener("abort", onCallerAbort);
    if (timedOut) {
      throw new ApiError({
        code: "timeout",
        message: isRemoteApi()
          ? `The API at ${apiUrl()} did not answer within ${Math.round(timeoutMs / 1000)} s for ${path}. It may be waking up; try again.`
          : `The API did not answer within ${Math.round(timeoutMs / 1000)} s for ${path}. Start it with make dev.`,
        status: 0,
        path,
        cause: error,
      });
    }
    if (isAbortError(error)) {
      throw new ApiError({ code: "aborted", message: "Request cancelled.", status: 0, path, cause: error });
    }
    throw new ApiError({
      code: "network",
      message: `The VARUNA API is unreachable at ${apiUrl()}. Start it with make dev, then retry.`,
      status: 0,
      path,
      cause: error,
    });
  }
  clearTimeout(timer);
  callerSignal?.removeEventListener("abort", onCallerAbort);

  if (!response.ok) {
    const errorBody = await readBody(response);
    throw errorFromResponse(path, response.status, response.statusText, errorBody);
  }

  if (response.status === 204) return undefined as T;

  const data = await readBody(response);
  if (typeof data === "string" && (response.headers.get("content-type") ?? "").includes("json")) {
    throw new ApiError({
      code: "invalid_response",
      message: `The API sent a body the console could not parse for ${path}.`,
      status: response.status,
      path,
      body: data,
    });
  }

  if (schema) {
    const parsed = schema.safeParse(data);
    if (!parsed.success) {
      const issues = parsed.error.issues
        .slice(0, 3)
        .map((issue) => `${issue.path.join(".") || "body"}: ${issue.message}`)
        .join("; ");
      throw new ApiError({
        code: "invalid_response",
        message: `The API answer for ${path} does not match the console contract (${issues}). Run pnpm typegen to refresh the types.`,
        status: response.status,
        path,
        body: data,
      });
    }
    return parsed.data;
  }
  return data as T;
}

/** Convenience wrappers so call sites read as the HTTP verb. */
export const api = {
  get: <T = unknown>(path: string, options: Omit<ApiFetchOptions<T>, "body" | "method"> = {}) =>
    apiFetch<T>(path, { ...options, method: "GET" }),
  post: <T = unknown>(path: string, body?: unknown, options: Omit<ApiFetchOptions<T>, "body" | "method"> = {}) =>
    apiFetch<T>(path, { ...options, method: "POST", body: body ?? {} }),
  put: <T = unknown>(path: string, body?: unknown, options: Omit<ApiFetchOptions<T>, "body" | "method"> = {}) =>
    apiFetch<T>(path, { ...options, method: "PUT", body: body ?? {} }),
  delete: <T = unknown>(path: string, options: Omit<ApiFetchOptions<T>, "body" | "method"> = {}) =>
    apiFetch<T>(path, { ...options, method: "DELETE" }),
};

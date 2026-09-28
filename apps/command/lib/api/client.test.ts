import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import {
  ApiError,
  apiFetch,
  apiUrl,
  errorFromResponse,
  errorMessage,
  isApiError,
  toQueryString,
  wsUrl,
} from "./client";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

const originalEnv = process.env.NEXT_PUBLIC_API_URL;

afterEach(() => {
  process.env.NEXT_PUBLIC_API_URL = originalEnv;
  vi.restoreAllMocks();
});

describe("apiUrl and wsUrl", () => {
  it("defaults to localhost:8000", () => {
    process.env.NEXT_PUBLIC_API_URL = "";
    expect(apiUrl()).toBe("http://localhost:8000");
    expect(apiUrl("/v1/runs")).toBe("http://localhost:8000/v1/runs");
    expect(apiUrl("v1/runs")).toBe("http://localhost:8000/v1/runs");
  });
  it("reads NEXT_PUBLIC_API_URL and strips a trailing slash", () => {
    process.env.NEXT_PUBLIC_API_URL = "https://api.varuna.example/";
    expect(apiUrl("/healthz")).toBe("https://api.varuna.example/healthz");
    expect(wsUrl()).toBe("wss://api.varuna.example/v1/live");
  });
  it("maps http to ws", () => {
    process.env.NEXT_PUBLIC_API_URL = "http://localhost:8000";
    expect(wsUrl()).toBe("ws://localhost:8000/v1/live");
  });
});

describe("toQueryString", () => {
  it("skips null and undefined and encodes the rest", () => {
    expect(toQueryString({ run_id: "MUM-1", t: 40, p: undefined, q: null, live: true })).toBe(
      "?run_id=MUM-1&t=40&live=true",
    );
    expect(toQueryString()).toBe("");
    expect(toQueryString({})).toBe("");
  });
});

describe("apiFetch", () => {
  it("returns parsed JSON and sends the accept header", async () => {
    const fetchImpl = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("accept")).toBe("application/json");
      expect(init?.method).toBe("GET");
      return jsonResponse({ status: "ok" });
    });
    const data = await apiFetch<{ status: string }>("/healthz", { fetchImpl });
    expect(data.status).toBe("ok");
    expect(fetchImpl).toHaveBeenCalledWith("http://localhost:8000/healthz", expect.anything());
  });

  it("serialises object bodies as JSON and defaults to POST", async () => {
    const fetchImpl = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("content-type")).toBe("application/json");
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init?.body))).toEqual({ depth_hint: "knee" });
      return jsonResponse({ accepted: true }, { status: 201 });
    });
    const data = await apiFetch<{ accepted: boolean }>("/v1/reports", {
      body: { depth_hint: "knee" },
      fetchImpl,
    });
    expect(data.accepted).toBe(true);
  });

  it("appends the query string", async () => {
    const fetchImpl = vi.fn(async (url: RequestInfo | URL) => {
      expect(String(url)).toBe("http://localhost:8000/v1/nowcast/hotspots?run_id=MUM-1&limit=10");
      return jsonResponse([]);
    });
    await apiFetch("/v1/nowcast/hotspots", { query: { run_id: "MUM-1", limit: 10 }, fetchImpl });
  });

  it("parses the error envelope into an ApiError shown verbatim", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        {
          error: {
            code: "not_implemented",
            message: "Citizen reports arrive in Phase 7. Until then, reports are queued.",
            run_id: "MUM-20190702T0640-sky1.0-twin1.0-flash0.3-baked",
          },
        },
        { status: 501, statusText: "Not Implemented" },
      ),
    );
    const error = await apiFetch("/v1/reports", { body: {}, fetchImpl }).catch((e: unknown) => e);
    expect(isApiError(error)).toBe(true);
    const apiError = error as ApiError;
    expect(apiError.code).toBe("not_implemented");
    expect(apiError.status).toBe(501);
    expect(apiError.isNotImplemented).toBe(true);
    expect(apiError.isClientFault).toBe(false);
    expect(apiError.runId).toBe("MUM-20190702T0640-sky1.0-twin1.0-flash0.3-baked");
    expect(apiError.message).toBe("Citizen reports arrive in Phase 7. Until then, reports are queued.");
    expect(errorMessage(error)).toBe(apiError.message);
  });

  it("falls back to FastAPI detail strings", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ detail: "Run MUM-9 not found" }, { status: 404, statusText: "Not Found" }),
    );
    const error = (await apiFetch("/v1/runs/MUM-9", { fetchImpl }).catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(404);
    expect(error.isClientFault).toBe(true);
    expect(error.message).toBe("Run MUM-9 not found");
  });

  it("joins FastAPI validation detail arrays", () => {
    const error = errorFromResponse("/v1/route", 422, "Unprocessable Entity", {
      detail: [{ loc: ["body", "profile"], msg: "Input should be a valid profile" }],
    });
    expect(error.message).toBe("Input should be a valid profile");
  });

  it("explains a plain 5xx with the path and a fix", async () => {
    const fetchImpl = vi.fn(
      async () => new Response("<html>gateway</html>", { status: 502, statusText: "Bad Gateway" }),
    );
    const error = (await apiFetch("/v1/runs", { fetchImpl }).catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("http_error");
    expect(error.message).toContain("502");
    expect(error.message).toContain("/v1/runs");
    expect(error.body).toBe("<html>gateway</html>");
  });

  it("turns a network failure into an unreachable message with the base URL", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    const error = (await apiFetch("/healthz", { fetchImpl }).catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("network");
    expect(error.status).toBe(0);
    expect(error.message).toContain("http://localhost:8000");
    expect(error.message).toContain("make dev");
  });

  it("times out with the budget in the message", async () => {
    const fetchImpl = vi.fn(
      (_url: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("The operation was aborted.", "AbortError"));
          });
        }),
    );
    const error = (await apiFetch("/v1/cycle/status", { fetchImpl, timeoutMs: 20 }).catch(
      (e: unknown) => e,
    )) as ApiError;
    expect(error.code).toBe("timeout");
    expect(error.message).toContain("did not answer");
  });

  it("validates with a zod schema and reports the mismatch", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ status: 42 }));
    const schema = z.object({ status: z.string() });
    const error = (await apiFetch("/healthz", { fetchImpl, schema }).catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("invalid_response");
    expect(error.message).toContain("status");
    expect(error.message).toContain("pnpm typegen");
  });

  it("returns the validated value when the schema matches", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ status: "ok", extra: 1 }));
    const schema = z.looseObject({ status: z.string() });
    const data = await apiFetch("/healthz", { fetchImpl, schema });
    expect(data.status).toBe("ok");
  });

  it("returns undefined for 204", async () => {
    const fetchImpl = vi.fn(async () => new Response(null, { status: 204 }));
    const data = await apiFetch("/v1/alerts/1/ack", { fetchImpl, method: "POST" });
    expect(data).toBeUndefined();
  });
});

describe("errorMessage", () => {
  it("passes through plain errors and falls back for unknown values", () => {
    expect(errorMessage(new Error("Traffic feed offline. Pulse is using reports only."))).toBe(
      "Traffic feed offline. Pulse is using reports only.",
    );
    expect(errorMessage("boom")).toBe("The request failed. Try again.");
    expect(errorMessage(null, "No answer.")).toBe("No answer.");
  });
});

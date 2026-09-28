/**
 * Render helpers for the component tests.
 *
 * Screens that read the API need a query client, and Base UI's tooltips need their provider;
 * wrapping by hand in every test file drifts. `renderWithProviders` gives both, with retries
 * off so a failing fetch in jsdom is a single, immediate error instead of a backoff schedule
 * that outlives the test.
 *
 * This file is not a test (vitest collects `*.test.ts(x)` only) and imports nothing from
 * vitest, so it never reaches the app bundle.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions, type RenderResult } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";

import { TooltipProvider } from "@/components/ui/tooltip";

export function makeTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

export interface RenderWithProvidersResult extends RenderResult {
  client: QueryClient;
}

export function renderWithProviders(
  ui: ReactElement,
  options: RenderOptions & { client?: QueryClient } = {},
): RenderWithProvidersResult {
  const { client = makeTestQueryClient(), ...renderOptions } = options;
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
  );
  return { ...render(ui, { wrapper, ...renderOptions }), client };
}

/** One canned API answer: the status and the JSON body a path should reply with. */
export interface StubRoute {
  status?: number;
  body: unknown;
}

/**
 * A `fetch` that answers the routes it is given and rejects anything else, so a test says out
 * loud which endpoints the screen is allowed to call. Match is by path suffix.
 */
export function stubFetch(routes: Record<string, StubRoute | unknown>): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url, "http://localhost:8000").pathname;
    const match = Object.entries(routes).find(([route]) => path.endsWith(route));
    if (!match) {
      return new Response(
        JSON.stringify({ error: { code: "not_found", message: `No stub for ${path}` } }),
        { status: 404, headers: { "content-type": "application/json" } },
      );
    }
    const value = match[1];
    const route: StubRoute =
      value && typeof value === "object" && "body" in value
        ? (value as StubRoute)
        : { body: value };
    return new Response(JSON.stringify(route.body), {
      status: route.status ?? 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
}

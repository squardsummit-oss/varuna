/**
 * Query client provider for the whole app. Mount once in the root layout:
 *
 *   <ApiProvider>{children}</ApiProvider>
 *
 * It also wires the live socket to the query cache so a `runs.published` event refreshes the
 * run registry without any component having to ask.
 */
"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { useLive } from "./live";
import { invalidateOnRunPublished, shouldRetry } from "./queries";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetry,
        refetchOnWindowFocus: false,
        refetchOnReconnect: true,
        staleTime: 15_000,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

let sharedClient: QueryClient | null = null;

/** One client per tab so the cache survives route changes; a fresh one per server render. */
export function getSharedQueryClient(): QueryClient {
  if (typeof window === "undefined") return makeQueryClient();
  if (!sharedClient) sharedClient = makeQueryClient();
  return sharedClient;
}

function LiveInvalidator({ client, enabled }: { client: QueryClient; enabled: boolean }) {
  useLive({
    enabled,
    topics: ["runs.published"],
    onEvent: () => invalidateOnRunPublished(client),
  });
  return null;
}

export interface ApiProviderProps {
  children: ReactNode;
  /** Open the shared socket as soon as the app mounts (default true in the browser). */
  live?: boolean;
  /** Supply a client (tests). */
  client?: QueryClient;
}

export function ApiProvider({ children, live = true, client }: ApiProviderProps) {
  const [queryClient] = useState(() => client ?? getSharedQueryClient());
  return (
    <QueryClientProvider client={queryClient}>
      <LiveInvalidator client={queryClient} enabled={live} />
      {children}
    </QueryClientProvider>
  );
}

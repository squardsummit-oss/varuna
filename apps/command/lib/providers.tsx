"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";

// First, for its side effect: the fetch guard has to be on `window.fetch` before any screen's
// effects run, and a module's top level runs when the bundle loads, ahead of every effect.
import "@/lib/api/resilience";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { CommandPalette } from "@/components/varuna/command-palette";
import { ServerStatusBanner } from "@/components/varuna/server-status-banner";
import { SettingsDrawer } from "@/components/varuna/settings-drawer";
import { ShortcutsOverlay } from "@/components/varuna/shortcuts-overlay";
import { useGlobalShortcuts } from "@/lib/shortcuts";
import { useUiStore } from "@/lib/stores/ui";

/** Mounted once so the keyboard handler lives under every provider. */
function GlobalShortcuts() {
  useGlobalShortcuts();
  return null;
}

/** Rehydrates the persisted slice of the ui store after mount (skipHydration in the store). */
function PersistHydration() {
  useEffect(() => {
    void useUiStore.persist.rehydrate();
  }, []);
  return null;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delay={300}>
        {children}
        <GlobalShortcuts />
        <PersistHydration />
        <CommandPalette />
        <ShortcutsOverlay />
        <SettingsDrawer />
        <ServerStatusBanner />
        <Toaster theme="dark" position="bottom-center" closeButton={false} />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

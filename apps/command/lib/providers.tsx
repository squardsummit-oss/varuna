"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { CommandPalette } from "@/components/varuna/command-palette";
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
        <Toaster theme="dark" position="bottom-center" closeButton={false} />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

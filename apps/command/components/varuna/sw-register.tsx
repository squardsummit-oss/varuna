"use client";

/**
 * Mounts the offline worker for the public map and the report flow (task P9.10), and does nothing
 * on any other page: the console and the operator screens are never registered, never controlled
 * and never cached.
 *
 * Off under `next dev`, where a worker caching hot-reloaded chunks would serve yesterday's code;
 * `NEXT_PUBLIC_VARUNA_SW=1` turns it on there for testing.
 */

import { useEffect } from "react";
import { usePathname } from "next/navigation";

import { currentCity } from "@/lib/city";
import { offlineScopeOf, offlineVersion } from "@/lib/offline/constants";
import { registerOfflineWorker } from "@/lib/offline/register";

function enabled(): boolean {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return false;
  return process.env.NODE_ENV === "production" || process.env.NEXT_PUBLIC_VARUNA_SW === "1";
}

export function SwRegister() {
  const pathname = usePathname();

  useEffect(() => {
    if (!pathname || !offlineScopeOf(pathname) || !enabled()) return;
    const register = () =>
      registerOfflineWorker({
        version: offlineVersion(),
        pathname,
        city: currentCity(),
      }).catch(() => {
        // A browser that refuses the worker (private mode, a policy) keeps the online map; that
        // is the map this page drew before the worker existed.
      });
    // After the page's own load, so registering never competes with the forecast for the network.
    if (document.readyState === "complete") void register();
    else window.addEventListener("load", () => void register(), { once: true });
  }, [pathname]);

  return null;
}

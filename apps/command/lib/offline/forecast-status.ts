"use client";

/**
 * Whether the public map is reading a live forecast or the copy its worker saved, and how old that
 * copy is (task P9.10, SPEC.md 7.11 AC3).
 *
 * Three states, each with its own sentence: online; offline (the browser says so); and
 * unreachable (the browser is online but the forecast service is not answering, so the worker
 * handed back its copy). Only pages the worker controls - `/map` and `/report` - can be anything
 * but online: every other screen reads `controlled: false` and draws what it always drew.
 */

import { useEffect, useState } from "react";

import { apiUrl } from "@/lib/api/client";
import { formatIst } from "@/lib/format";

import { FORECAST_META_PATH, OFFLINE_DB, OFFLINE_STORE, type WorkerMessage } from "./constants";
import { flushQueuedReports } from "./register";

export type ConnectionMode = "online" | "offline" | "unreachable";

/** Provenance of the saved forecast, written by the worker when it keeps a run's bounds. */
export interface SavedForecast {
  runId: string;
  cycleTs: string | null;
  /** When this phone saved it: the age a reader needs, since the run may be hours older. */
  savedAt: string;
}

export interface OfflineForecast {
  controlled: boolean;
  mode: ConnectionMode;
  saved: SavedForecast | null;
  /** Reports posted with no connection and not sent yet. */
  pending: number;
  /** Reports sent from the queue since this page opened. */
  sent: number;
}

const INITIAL: OfflineForecast = {
  controlled: false,
  mode: "online",
  saved: null,
  pending: 0,
  sent: 0,
};

/** "12 min", "3 h 5 min", "2 days": how long ago, in the units a person would say it. */
export function formatAge(fromIso: string, now: Date = new Date()): string {
  const from = new Date(fromIso);
  if (Number.isNaN(from.getTime())) return "an unknown time";
  const minutes = Math.max(0, Math.round((now.getTime() - from.getTime()) / 60_000));
  if (minutes < 1) return "under a minute";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) {
    const rest = minutes - hours * 60;
    return rest ? `${hours} h ${rest} min` : `${hours} h`;
  }
  return `${Math.floor(hours / 24)} days`;
}

/**
 * The sentence the map prints while it draws a saved copy. Null while it is live, because a live
 * map already carries its honesty line.
 */
export function offlineLine(
  mode: ConnectionMode,
  saved: SavedForecast | null,
  now: Date = new Date(),
): string | null {
  if (mode === "online") return null;
  const why = mode === "offline" ? "You are offline." : "The forecast service is not answering.";
  if (!saved) return `${why} This phone has no saved forecast yet.`;
  const run = saved.cycleTs ? `, from the run at ${formatIst(saved.cycleTs)}` : "";
  return `${why} Showing the forecast saved at ${formatIst(saved.savedAt)} (${formatAge(
    saved.savedAt,
    now,
  )} ago)${run}.`;
}

/** The queue's line: what is waiting, or what just went. Null when there is nothing to say. */
export function queueLine(pending: number, sent: number): string | null {
  if (pending > 0) {
    return pending === 1
      ? "1 report is saved on this phone and will be sent when the connection returns."
      : `${pending} reports are saved on this phone and will be sent when the connection returns.`;
  }
  if (sent > 0)
    return sent === 1 ? "Your saved report was sent." : `${sent} saved reports were sent.`;
  return null;
}

export async function readSavedForecast(): Promise<SavedForecast | null> {
  if (typeof caches === "undefined") return null;
  try {
    const response = await caches.match(new URL(FORECAST_META_PATH, location.origin).href);
    if (!response) return null;
    const body = (await response.json()) as Partial<SavedForecast>;
    if (typeof body.runId !== "string" || typeof body.savedAt !== "string") return null;
    return { runId: body.runId, cycleTs: body.cycleTs ?? null, savedAt: body.savedAt };
  } catch {
    return null;
  }
}

export function countQueuedReports(): Promise<number> {
  if (typeof indexedDB === "undefined") return Promise.resolve(0);
  return new Promise((resolve) => {
    const open = indexedDB.open(OFFLINE_DB, 1);
    open.onupgradeneeded = () => {
      if (!open.result.objectStoreNames.contains(OFFLINE_STORE)) {
        open.result.createObjectStore(OFFLINE_STORE, { keyPath: "id", autoIncrement: true });
      }
    };
    open.onerror = () => resolve(0);
    open.onsuccess = () => {
      const db = open.result;
      try {
        const request = db
          .transaction(OFFLINE_STORE, "readonly")
          .objectStore(OFFLINE_STORE)
          .count();
        request.onsuccess = () => {
          resolve(request.result);
          db.close();
        };
        request.onerror = () => {
          resolve(0);
          db.close();
        };
      } catch {
        resolve(0);
        db.close();
      }
    };
  });
}

/** Can the forecast service be reached right now? Not answered by the worker: `/healthz` is
 * not one of the reads it keeps, so a failure here is the network's answer. */
async function apiAnswers(signal: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch(apiUrl("/healthz"), { cache: "no-store", signal });
    return response.ok;
  } catch {
    return false;
  }
}

export function useOfflineForecast(): OfflineForecast {
  const [state, setState] = useState<OfflineForecast>(INITIAL);

  useEffect(() => {
    const container = typeof navigator !== "undefined" ? navigator.serviceWorker : undefined;
    if (!container?.controller) return;
    const controller = new AbortController();
    let alive = true;
    const update = (patch: Partial<OfflineForecast>) => {
      if (alive) setState((s) => ({ ...s, ...patch }));
    };

    const refresh = async (mode?: ConnectionMode) => {
      const [saved, pending] = await Promise.all([readSavedForecast(), countQueuedReports()]);
      update({ saved, pending, ...(mode ? { mode } : {}) });
    };

    const probe = async () => {
      if (!navigator.onLine) return refresh("offline");
      const answers = await apiAnswers(controller.signal);
      return refresh(answers ? "online" : "unreachable");
    };

    const onMessage = (event: MessageEvent<WorkerMessage>) => {
      const data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.type === "varuna:from-cache") {
        void refresh(navigator.onLine ? "unreachable" : "offline");
      } else if (data.type === "varuna:report-queued") {
        update({ pending: data.pending });
      } else if (data.type === "varuna:reports-sent") {
        setState((s) => ({ ...s, pending: data.pending, sent: s.sent + data.sent }));
      }
    };
    const onOnline = () => {
      flushQueuedReports(container);
      void probe();
    };
    const onOffline = () => void refresh("offline");

    update({ controlled: true });
    void probe();
    container.addEventListener("message", onMessage);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      alive = false;
      controller.abort();
      container.removeEventListener("message", onMessage);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  return state;
}

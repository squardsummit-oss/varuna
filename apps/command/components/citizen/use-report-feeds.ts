"use client";

/**
 * The two report feeds the citizen dashboard reads: everybody's complaints near the reader, and
 * the reader's own, each with the status the ward desk gave it.
 *
 * **One report, two screens.** A complaint a citizen raises at `/report` is the same object on the
 * desk's inbox and here: both read `GET /v1/reports`, and the desk's status is merged into it by
 * the API, so when an officer marks it "Crew sent" the citizen sees "Crew sent" on the next poll
 * without either screen knowing about the other.
 *
 * **Polling.** Every {@link REPORTS_POLL_MS} while the tab is visible, and once more when it
 * becomes visible again, which is the moment someone comes back to see what happened. A poll keeps
 * what is on screen while it runs, and an answer that arrives after a newer one is dropped.
 */

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { cityBounds } from "@/components/map/basemap";
import { ApiError } from "@/lib/api/client";
import {
  loadReport,
  loadReports,
  type DismissedReport,
  type PublicReport,
  type ReportList,
} from "@/lib/api/reports";

import { MY_REPORTS_KEY, readMyReports, type MyReport } from "./my-reports";

/** How often an open dashboard asks again (the desk's own cadence, `DESK_REPORTS_POLL_MS`). */
export const REPORTS_POLL_MS = 30_000;

function visible(): boolean {
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}

/** Calls `tick` every `ms` while the tab is visible, and at once when it becomes visible again. */
function useVisiblePoll(tick: () => void, ms: number): void {
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (visible()) tick();
    }, ms);
    const onVisible = () => {
      if (visible()) tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [tick, ms]);
}

export interface PublicReportsFeed {
  /** Reports on the map and in the list, newest first; dismissed ones are never here. */
  reports: PublicReport[];
  /** The API's own notes on the list (which reports are synthetic, how coordinates are rounded). */
  notes: string[];
  /** Null until the first answer. */
  loaded: boolean;
  /** The API's sentence when the latest read failed; the last good list stays on screen. */
  error: string | null;
}

/** When a report was seen, else when it arrived, as epoch ms for sorting; unknown sorts last. */
export function reportTime(report: PublicReport): number {
  const at = Date.parse(report.ts ?? report.received_at ?? "");
  return Number.isFinite(at) ? at : Number.NEGATIVE_INFINITY;
}

/**
 * Whether a report belongs on this city's dashboard.
 *
 * The API files each report under a city, or under none when it is outside every forecast area
 * (`outside_aoi`), and filters by `city` itself. An API from before 2026-09-26 does neither: it
 * ignores `city` and returns every report it holds. The deployed one was measured doing that on
 * 2026-09-27, listing a report at 13.68 N, 79.55 E - nowhere near Mumbai - under a heading that
 * says "in Mumbai". So a report the API did not classify is judged by the city's own box.
 */
export function inCity(report: PublicReport, city: string): boolean {
  if (report.outside_aoi === true || report.city === null) return false;
  if (report.city) return report.city === city;
  const [[west, south], [east, north]] = cityBounds(city);
  return report.lon >= west && report.lon <= east && report.lat >= south && report.lat <= north;
}

/**
 * The public list as the dashboard shows it: dismissed reports out even if an older API lists
 * them, reports from outside the city out when a city is given, newest first.
 */
export function visibleReports(list: ReportList | null, city?: string): PublicReport[] {
  if (!list) return [];
  return list.reports
    .filter((report) => report.status !== "dismissed")
    .filter((report) => city === undefined || inCity(report, city))
    .sort((a, b) => reportTime(b) - reportTime(a));
}

/** `GET /v1/reports` for one city, polled. */
export function usePublicReports(city: string): PublicReportsFeed {
  const [list, setList] = useState<ReportList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const latest = useRef(0);

  const load = useCallback(() => {
    const ticket = ++latest.current;
    const controller = new AbortController();
    loadReports({ city, signal: controller.signal })
      .then((answer) => {
        if (ticket !== latest.current) return;
        setList(answer);
        setError(null);
      })
      .catch((failure: unknown) => {
        if (ticket !== latest.current) return;
        setError(failure instanceof Error ? failure.message : String(failure));
      })
      .finally(() => {
        if (ticket === latest.current) setLoaded(true);
      });
    return controller;
  }, [city]);

  useEffect(() => {
    const controller = load();
    return () => controller.abort();
  }, [load]);

  const poll = useCallback(() => void load(), [load]);
  useVisiblePoll(poll, REPORTS_POLL_MS);

  const reports = useMemo(() => visibleReports(list, city), [list, city]);
  return { reports, notes: list?.notes ?? [], loaded, error };
}

/** One of the reader's own reports, as the dashboard last heard of it. */
export type MyReportState =
  | { kind: "loading"; entry: MyReport }
  | { kind: "ready"; entry: MyReport; report: PublicReport | DismissedReport }
  /** The API has no report by this id: the words are the API's own. */
  | { kind: "missing"; entry: MyReport; message: string }
  /** The read failed; the last answer, if there was one, stays beside the reason. */
  | {
      kind: "error";
      entry: MyReport;
      message: string;
      report: PublicReport | DismissedReport | null;
    };

export interface MyReportsFeed {
  items: MyReportState[];
  /** False until this browser's storage has been read (it cannot be read on the server). */
  ready: boolean;
}

function subscribeToStorage(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

/** The raw stored value, "" when there is none or storage refuses to be read. */
function readStoredReports(): string {
  try {
    return window.localStorage.getItem(MY_REPORTS_KEY) ?? "";
  } catch {
    return "";
  }
}

function noStoredReports(): null {
  return null;
}

/**
 * The reports this browser sent, each polled with `GET /v1/reports/{id}` every 30 s while the
 * tab is visible, so the status the desk sets reaches the reporter without a reload.
 */
export function useMyReports(): MyReportsFeed {
  // The stored string, or null on the server and during hydration, where there is no storage: the
  // first client frame then matches the server's and the list arrives on the next one. A report
  // sent from another tab (the wizard is often opened in one) arrives through the storage event.
  const raw = useSyncExternalStore(subscribeToStorage, readStoredReports, noStoredReports);
  const entries = useMemo<MyReport[]>(() => (raw === null ? [] : readMyReports()), [raw]);
  const ready = raw !== null;
  const [byId, setById] = useState<Record<string, MyReportState>>({});
  const latest = useRef(0);

  const load = useCallback(() => {
    if (entries.length === 0) return;
    const ticket = ++latest.current;
    for (const entry of entries) {
      loadReport(entry.id)
        .then((report) => {
          if (ticket !== latest.current) return;
          setById((prev) => ({ ...prev, [entry.id]: { kind: "ready", entry, report } }));
        })
        .catch((failure: unknown) => {
          if (ticket !== latest.current) return;
          const message = failure instanceof Error ? failure.message : String(failure);
          setById((prev) => {
            if (failure instanceof ApiError && failure.status === 404) {
              return { ...prev, [entry.id]: { kind: "missing", entry, message } };
            }
            const held = prev[entry.id];
            const report =
              held?.kind === "ready" ? held.report : held?.kind === "error" ? held.report : null;
            return { ...prev, [entry.id]: { kind: "error", entry, message, report } };
          });
        });
    }
  }, [entries]);

  useEffect(() => {
    load();
  }, [load]);
  useVisiblePoll(load, REPORTS_POLL_MS);

  const items = useMemo(
    () => entries.map((entry): MyReportState => byId[entry.id] ?? { kind: "loading", entry }),
    [entries, byId],
  );
  return { items, ready };
}

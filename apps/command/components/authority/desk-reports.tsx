"use client";

/**
 * The desk's citizen reports, shared by the inbox and the ward map (user request 2026-09-27).
 *
 * A complaint a citizen raises has to be the same object in both places on the desk: the row in
 * the inbox and the pin on the ward map. So the list, the selected report and the camera target
 * live here, once, and both read them - the inbox through props, the map through context, because
 * the map is handed to the screen by a server component (`app/authority/page.tsx`) and cannot take
 * a callback from it.
 *
 * **Which list.** With the desk open (the API accepted this tab's passphrase) it is
 * `GET /v1/ops/reports`: exact coordinates, officers' names, dismissed reports included. Without
 * it, it is the public `GET /v1/reports` - rounded coordinates, dismissed ones gone - which is
 * what the deployed, read-only API can offer. A gated read the API refuses falls back to the
 * public one rather than blanking the inbox, and a refused passphrase is handed to the screen so
 * the desk closes the way it does after any other refusal.
 *
 * **Polling.** Every 30 s while the tab is visible, and at once after the desk's own write, so a
 * complaint a citizen sends from `/report` appears on the desk without a reload. A poll keeps the
 * list on screen while it runs - the skeleton is for the first load only - and a response that
 * arrives after a newer one is dropped, so a slow poll can never undo a status the desk just set.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { MapFocus } from "@/components/map/city-map";
import { REPORT_FOCUS_ZOOM } from "@/components/map/layers/reports";
import { isGateRefusal, opsRefusal, type OpsRefusal } from "@/lib/api/ops";
import {
  loadDeskReports,
  loadReports,
  type PublicReport,
  type ReportList,
} from "@/lib/api/reports";

/** How often the desk asks for new reports while it is on screen. */
export const DESK_REPORTS_POLL_MS = 30_000;

/** The zoom a report is flown to, shared with the citizen dashboard (`layers/reports.ts`). */
export { REPORT_FOCUS_ZOOM };

export interface DeskReports {
  /** Null until the first answer; the last good list stays while a poll runs or fails. */
  list: ReportList | null;
  /** The API's sentence when the latest read failed; null after a good one. */
  error: string | null;
  /** True when the list is the desk's gated one: exact coordinates and officers' names. */
  exact: boolean;
  selectedId: string | null;
  /** Where the ward map should fly; a new `key` flies again. */
  focus: MapFocus | null;
  /**
   * Choose a report. `fly` moves the ward map to it (motion M10's fly-to); a pin tapped on the
   * map is already in view, so the map selects without flying.
   */
  select: (id: string | null, options?: { fly?: boolean }) => void;
  /** Put the API's answer to a write in place at once, before the next poll confirms it. */
  replace: (report: PublicReport) => void;
}

const DeskReportsContext = createContext<DeskReports | null>(null);

/** The desk's reports, or null outside the desk (the ward map then draws no pins). */
export function useDeskReports(): DeskReports | null {
  return useContext(DeskReportsContext);
}

export function DeskReportsProvider({
  value,
  children,
}: {
  value: DeskReports;
  children: ReactNode;
}) {
  return <DeskReportsContext.Provider value={value}>{children}</DeskReportsContext.Provider>;
}

export interface DeskReportsOptions {
  city: string;
  /** The desk is open: the gated list is readable with the passphrase this tab holds. */
  deskOpen: boolean;
  /** Bumped by the screen after every write; a change reloads at once. */
  refreshKey?: number;
  pollMs?: number;
  /** The gated read refused the passphrase; the screen closes the desk. */
  onGateRefused?: (refusal: OpsRefusal) => void;
}

/** Load, poll and select the desk's reports. The screen owns it and passes it down. */
export function useDeskReportsLoader({
  city,
  deskOpen,
  refreshKey = 0,
  pollMs = DESK_REPORTS_POLL_MS,
  onGateRefused,
}: DeskReportsOptions): DeskReports {
  const [list, setList] = useState<ReportList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exact, setExact] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focus, setFocus] = useState<MapFocus | null>(null);
  // Closing the desk drops the exact list at once - it carries reporters' exact positions and
  // officers' names - rather than leaving it on screen until the public one arrives. Adjusted while
  // rendering, so the exact list is never drawn for the closed desk even for a frame.
  const [seenOpen, setSeenOpen] = useState(deskOpen);
  if (deskOpen !== seenOpen) {
    setSeenOpen(deskOpen);
    if (!deskOpen && exact) {
      setList(null);
      setExact(false);
    }
  }
  // The latest answer wins: every load takes a ticket and only the newest ticket may write.
  const ticket = useRef(0);
  const gateRefused = useRef(onGateRefused);
  useEffect(() => {
    gateRefused.current = onGateRefused;
  }, [onGateRefused]);

  useEffect(() => {
    const controller = new AbortController();

    const load = async () => {
      const mine = ++ticket.current;
      const signal = controller.signal;
      try {
        let answer: ReportList | null = null;
        let gated = false;
        if (deskOpen) {
          try {
            answer = await loadDeskReports({ city, signal });
            gated = true;
          } catch (failure: unknown) {
            if (signal.aborted) return;
            const refusal = opsRefusal(failure);
            if (isGateRefusal(refusal)) gateRefused.current?.(refusal);
            // Anything else the gated read refused still leaves the public list to show.
          }
        }
        if (!answer) answer = await loadReports({ city, signal });
        if (signal.aborted || mine !== ticket.current) return;
        setList(answer);
        setExact(gated);
        setError(null);
      } catch (failure: unknown) {
        if (signal.aborted || mine !== ticket.current) return;
        // The last good list stays on screen; the sentence says why it is not newer.
        setError(opsRefusal(failure).message);
        setList((current) => current ?? { count: 0, reports: [] });
      }
    };

    void load();
    const timer = window.setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      void load();
    }, pollMs);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [city, deskOpen, refreshKey, pollMs]);

  const reportsById = useMemo(
    () => new Map((list?.reports ?? []).map((report) => [report.id, report])),
    [list],
  );

  const select = useCallback(
    (id: string | null, options: { fly?: boolean } = {}) => {
      setSelectedId(id);
      if (!id || !options.fly) return;
      const report = reportsById.get(id);
      if (!report) return;
      setFocus({
        lon: report.lon,
        lat: report.lat,
        key: `report-${id}-${Date.now()}`,
        zoom: REPORT_FOCUS_ZOOM,
      });
    },
    [reportsById],
  );

  const replace = useCallback((report: PublicReport) => {
    // A write answers with the report as the desk sees it; it replaces the row it came from and
    // invalidates any poll already in flight, whose answer predates the write.
    ticket.current += 1;
    setList((current) =>
      current
        ? {
            ...current,
            reports: current.reports.map((row) => (row.id === report.id ? report : row)),
          }
        : current,
    );
  }, []);

  return useMemo(
    () => ({ list, error, exact, selectedId, focus, select, replace }),
    [list, error, exact, selectedId, focus, select, replace],
  );
}

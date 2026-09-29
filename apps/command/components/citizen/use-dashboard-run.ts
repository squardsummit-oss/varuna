"use client";

/**
 * Which forecast cycle the citizen dashboard shows, and the cycles a reader can switch to.
 *
 * Without this the map loads the API's newest run, which on the replay is 09:10 IST: the calm
 * cycle after the storm, where at +60 min 133 streets are 5 cm deep and none reaches a car's
 * 30 cm. The dashboard opens on **08:40** instead - the demo's peak, the cycle the ambulance trip
 * and `/drains` open on - where the same lead has 5,016 and 86. The rule is `/drains`' own
 * (`fetchOpeningRunId` at an instant), so the two screens cannot drift apart; the console opens at
 * 06:40 because it is the start of a replay that plays forward, and this screen does not play.
 *
 * When the registry is slow, unreachable or has no 08:40 run, the API's own newest stands: a late
 * map beats a wrong one.
 */

import { useEffect, useMemo, useState } from "react";

import { splitLive, type BakedCycle } from "@/components/varuna/cycle-picker";
import { apiUrl } from "@/lib/api/client";
import { LIVE_MAX_AGE_MIN, fetchOpeningRunId } from "@/lib/opening-run";

/** The cycle the dashboard opens on: the storm's peak in the 2 July 2019 replay. */
export const DASHBOARD_OPENING_TS = "2019-07-02T08:40:00+05:30";

export interface DashboardRun {
  /** The run to draw; undefined lets the API choose its newest. */
  runId: string | undefined;
  /** False until the registry has answered or given up; the map holds its load until then. */
  resolved: boolean;
  /** One run per cycle time of the replay, oldest first, for this city only. */
  cycles: BakedCycle[];
  /** Today's newest live cycle when one is fresh (`varuna_cycle.live`), else null. */
  live: BakedCycle | null;
  pick: (runId: string) => void;
}

interface RegistryRow {
  run_id: string;
  cycle_ts: string;
  mass_balance_err?: number | null;
  created_at?: string;
  bundle?: string | null;
}

export function useDashboardRun(city: string): DashboardRun {
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const [resolved, setResolved] = useState(false);
  const [rows, setRows] = useState<BakedCycle[]>([]);
  // When the registry was read, so a live cycle's age is judged against that moment and not
  // against a clock read during render.
  const [readAt, setReadAt] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    fetchOpeningRunId(city, DASHBOARD_OPENING_TS, apiUrl, controller.signal, {
      preferLive: true,
    }).then((opening) => {
      if (cancelled) return;
      setRunId((current) => current ?? opening);
      setResolved(true);
    });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [city]);

  // The cycle list for the picker. The shared `CyclePicker` asks for every city's runs and draws
  // chips too small for a thumb, so the dashboard reads the registry for its own city and offers
  // the cycles in a native select, which a phone opens as its own picker.
  useEffect(() => {
    const controller = new AbortController();
    fetch(apiUrl(`/v1/runs?city=${encodeURIComponent(city)}`), { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : { runs: [] }))
      .then((body: { runs?: RegistryRow[] }) => {
        setRows(
          (body.runs ?? []).map((row) => ({
            runId: row.run_id,
            cycleTs: row.cycle_ts,
            massBalanceErr: row.mass_balance_err ?? null,
            createdAt: row.created_at ?? "",
            bundle: row.bundle ?? null,
          })),
        );
        setReadAt(Date.now());
      })
      // Without the list the select is not drawn; the map still shows its run.
      .catch(() => undefined);
    return () => controller.abort();
  }, [city]);

  // The run on screen keeps its own cycle in the list, so the select never shows nothing chosen.
  const split = useMemo(() => splitLive(rows, runId), [rows, runId]);
  const live =
    split.live &&
    readAt !== null &&
    readAt - Date.parse(split.live.cycleTs) <= LIVE_MAX_AGE_MIN * 60_000
      ? split.live
      : null;

  return { runId, resolved, cycles: split.replay, live, pick: setRunId };
}

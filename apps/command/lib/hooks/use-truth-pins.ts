"use client";

/**
 * The sourced ground-truth pins, dropping as the replay clock passes each one (task P6.12, M18).
 *
 * This is the 2:40 moment of the demo script: the clock reaches 08:47, a pin lands on Gandhi
 * Market, and the street underneath it has been red for the last ninety minutes. So the timing has
 * to come from the **clock**, not from a page-load animation - a pin that drops when the console
 * opens says nothing at all. Two rules follow:
 *
 * - **What the clock had already passed when it was first read is simply there.** A pin drops only
 *   when the clock moves past it while someone is watching. That matters most for the six pins
 *   dated 1 July: they are before the replay window opens, so no clock in the replay ever passes
 *   them, and dropping them at 06:40 on 2 July would present the day before as news.
 * - **Scrubbing back un-passes a pin**, so scrubbing forward over it again drops it again. The
 *   ticker is a record of what has happened *by now*, and "now" is wherever the operator put it.
 *
 * **No React render per animation frame.** A newly passed pin is stamped once, on the next frame,
 * with the `performance.now()` its drop begins at; the map layer animates it from that stamp on
 * deck's clock (`components/map/layers/truth-pins.ts`). One timer clears the stamp when the drop is
 * over, so a drop costs this hook three renders - passed, stamped, landed - however long it runs.
 * Under reduced motion nothing is stamped and nothing is scheduled: the pin appears (section 8).
 *
 * **When each pin landed is state, not a ref.** It is read to compute what is rendered, which is
 * the definition of state; a ref there is both a lint error and a real bug waiting - React would
 * be free to skip the render that shows the drop.
 */

import { useEffect, useMemo, useState } from "react";

import { loadGroundTruth, pinsSoFar, type GroundTruthPin } from "@/lib/api/ground-truth";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { DUR_MS, springSettleMs } from "@/lib/motion";
import { formatIstDate, formatIstTime, toIstIso } from "@/lib/stores/time";

/**
 * Motion M18's length: the 600 ms ripple (SPEC.md 8), or the spring's settling time if that were
 * ever longer. The map stops asking deck for frames, and this hook clears the stamp, at this point.
 */
export const DROP_MS = Math.max(DUR_MS.pinRipple, springSettleMs());

/** A pin the clock has passed, with the clock reading it was passed at. */
export interface PassedPin extends GroundTruthPin {
  /** The replay clock (ISO 8601, +05:30) this pin was listed against; the ticker dates a pin from
   * a different day than this one. */
  clockTs: string;
}

export interface DroppedPin extends PassedPin {
  /** See `TruthPin.dropStartMs`: absent once landed, `Infinity` until its first frame. */
  dropStartMs?: number;
}

export interface TruthPinState {
  /** Everything the clock has passed, newest first: what the ticker lists. */
  passed: PassedPin[];
  /** The same pins with their drop state, for the map. Only those inside the AOI. */
  dropping: DroppedPin[];
  /** Every curated pin for the bundle, whether the clock has reached it or not. */
  all: GroundTruthPin[];
}

/** The IST calendar day of an ISO timestamp, "2019-07-02"; null when unreadable. */
function istDay(iso: string): string | null {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? toIstIso(new Date(ms)).slice(0, 10) : null;
}

/**
 * A pin's time for the ticker: "08:47" on the replay clock's own day, "1 Jul 11:52" from any other
 * day. Days are IST days, so a pin at 23:50 the night before still reads as the night before.
 * Without a clock to compare against the date is always shown, because a bare time cannot be read.
 */
export function formatPinTime(pinTs: string, clockTs: string | null | undefined): string {
  const time = formatIstTime(pinTs);
  const pinDay = istDay(pinTs);
  if (!pinDay) return time;
  if (clockTs && istDay(clockTs) === pinDay) return time;
  // "1 Jul 2019" without its year: the replay is one morning, and the year is in the banner.
  return `${formatIstDate(pinTs).replace(/\s+\d{4}$/, "")} ${time}`;
}

interface Loaded {
  bundle: string | undefined;
  pins: GroundTruthPin[];
}

interface Track {
  bundle: string | undefined;
  /** Passed pins already accounted for: there when the clock was first read, dropping, or landed. */
  seen: readonly string[];
  /** When each running drop began; cleared when the drop is over. */
  stamps: Readonly<Record<string, number>>;
}

const EMPTY: GroundTruthPin[] = [];

export function useTruthPins(bundle: string | undefined, simTime: string | null): TruthPinState {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const reducedMotion = usePrefersReducedMotion();
  const [track, setTrack] = useState<Track | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    loadGroundTruth(bundle, controller.signal)
      .then((set) => setLoaded({ bundle, pins: set?.pins ?? [] }))
      .catch(() => {
        if (!controller.signal.aborted) setLoaded({ bundle, pins: [] });
      });
    return () => controller.abort();
  }, [bundle]);

  const all = loaded && loaded.bundle === bundle ? loaded.pins : EMPTY;
  const ready = loaded !== null && loaded.bundle === bundle && simTime !== null;

  const passed = useMemo<PassedPin[]>(
    () => (simTime ? pinsSoFar(all, simTime).map((pin) => ({ ...pin, clockTs: simTime })) : []),
    [all, simTime],
  );
  const passedKey = passed.map((p) => p.id).join("|");

  // Adjusting state while rendering, the React-documented way to follow a prop: the render that
  // sees the clock move already knows which pins are new, so none flashes in at full size first.
  let current = track;
  if (!ready) {
    if (track !== null) {
      current = null;
      setTrack(null);
    }
  } else {
    const ids = passedKey ? passedKey.split("|") : [];
    if (track === null || track.bundle !== bundle) {
      // The clock's first reading: what it had already passed was there before anyone watched.
      current = { bundle, seen: ids, stamps: {} };
      setTrack(current);
    } else {
      const live = new Set(ids);
      const seen = track.seen.filter((id) => live.has(id));
      // Reduced motion: a newly passed pin has landed the moment it is passed.
      if (reducedMotion) for (const id of ids) if (!seen.includes(id)) seen.push(id);
      if (seen.length !== track.seen.length || seen.some((id, i) => id !== track.seen[i])) {
        const stamps: Record<string, number> = {};
        for (const [id, at] of Object.entries(track.stamps)) if (live.has(id)) stamps[id] = at;
        current = { bundle, seen, stamps };
        setTrack(current);
      }
    }
  }

  const seenIds = current?.seen;
  const seenSet = useMemo(() => new Set(seenIds ?? []), [seenIds]);
  const pendingKey =
    current && !reducedMotion
      ? passed
          .filter((p) => !seenSet.has(p.id))
          .map((p) => p.id)
          .join("|")
      : "";

  // Stamp newly passed pins on the next frame: the frame their drop begins. One frame, not a loop.
  useEffect(() => {
    if (!pendingKey) return;
    const ids = pendingKey.split("|");
    const frame = requestAnimationFrame((at) => {
      setTrack((t) => {
        if (!t) return t;
        const seen = [...t.seen];
        const stamps = { ...t.stamps };
        for (const id of ids) {
          if (!seen.includes(id)) seen.push(id);
          stamps[id] = at;
        }
        return { ...t, seen, stamps };
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [pendingKey]);

  // Clear each drop's stamp when it is over, so the pin joins the landed ones. The drops that end
  // first are cleared by one timer; the effect runs again for any still going.
  const stamps = current?.stamps;
  useEffect(() => {
    const starts = Object.values(stamps ?? {});
    if (starts.length === 0) return;
    const due = Math.min(...starts) + DROP_MS;
    const timer = setTimeout(
      () => {
        setTrack((t) => {
          if (!t) return t;
          const kept = Object.entries(t.stamps).filter(([, at]) => at + DROP_MS > due);
          if (kept.length === Object.keys(t.stamps).length) return t;
          return { ...t, stamps: Object.fromEntries(kept) };
        });
      },
      Math.max(0, due - performance.now()),
    );
    return () => clearTimeout(timer);
  }, [stamps]);

  const dropping = useMemo<DroppedPin[]>(
    () =>
      passed
        .filter((pin) => pin.insideAoi)
        .map((pin) => {
          if (reducedMotion || !current) return pin;
          const stamp = current.stamps[pin.id];
          if (stamp !== undefined) return { ...pin, dropStartMs: stamp };
          if (!seenSet.has(pin.id)) return { ...pin, dropStartMs: Number.POSITIVE_INFINITY };
          return pin;
        }),
    [passed, reducedMotion, current, seenSet],
  );

  return { passed, dropping, all };
}

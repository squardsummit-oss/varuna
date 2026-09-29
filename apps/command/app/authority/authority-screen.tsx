"use client";

/**
 * The ward officer's desk (UI_SPEC 6, PRD 3.2, task D-15).
 *
 * **One rule decides the layout.** Does an engine consume it, or is it a note? The left column is
 * everything a route, a feed or the optimiser reads back; the right column is everything that is
 * only ever a record. They are never dressed the same, and every act reports through one
 * component so the two cannot drift into sounding alike.
 *
 * **Nothing here rewrites a product.** Every edit is one appended line in `data/ops/<city>.jsonl`,
 * applied when a route or a feed is read, so a baked run stays byte-identical (SPEC.md rule 8)
 * while the next route avoids the street. The API says this in its own words after every write
 * and the desk prints it rather than paraphrasing.
 *
 * **The desk is read-only unless the API holds a passphrase.** The screen asks the log first: it
 * answers `writes_enabled`, which is false on the deployed API on purpose, and then the desk
 * offers the reads - the citizen inbox and the log - and refuses to take a passphrase it has
 * nowhere to send. That is the honest shape of prototype access, and the gate says so in the
 * words UI_SPEC 6 sets.
 *
 * **Which cycle.** Alerts and pumps only mean something beside the forecast that raised them, and
 * the newest baked run is 09:10 IST, after the storm. The officer picks the cycle here as they do
 * on the console and on the pump board. Closures are not per-cycle - a street is shut or it is
 * not - but the street *list* is, because it is the set this cycle says is in trouble.
 *
 * **The desk opens on the ward** (motion M27, widened to this screen 2026-09-23). The same entry
 * the citizen dashboard uses plays here, for the same reason: an officer who has just been handed
 * this URL needs to be told which city and which ward before they are asked for a passphrase. It
 * plays *before* the gate is decided, so nobody is made to watch a globe after typing a
 * passphrase, and it is a sibling of the page rather than a wrapper, so the ops-log fetch this
 * screen does on mount is already in flight while the globe turns. It is skippable by any key -
 * on a screen used during a flood that matters more than it does on the dashboard - and its
 * session key is its own, so seeing the dashboard's entry does not silence the desk's and a
 * reload mid-incident does not replay it.
 *
 * **The map slot is not the map.** The entry cross-fades into the ward map region at the top of
 * the desk, mounted from the first paint and framed on {@link ENTRY_AOI} - the box the globe's
 * last act ends on. The photorealistic city map that belongs in it is another chunk's work; until
 * it is passed in as {@link AuthorityScreenProps.wardMap} the region says so in its own words
 * rather than drawing something that is not a map. Two honest consequences: the
 * handover lands on an empty frame rather than on a city, and the desk is a scrolling column, so
 * the fade reveals the whole desk with its ward map at the top of the viewport rather than a map
 * that fills the frame the way the dashboard's does.
 *
 * **Citizen reports sit beside the map, and are one object in both.** The inbox and the ward
 * map's pins read one list (`desk-reports.tsx`), loaded here and polled every 30 s and after every
 * write, so a complaint a citizen sends appears on the desk without a reload. Opening a row flies
 * the map to its pin; tapping a pin opens its row. The inbox stands beside the map rather than at
 * the foot of the desk because a fly-to nobody can see is not a link: on a wide screen both are in
 * view, and on a narrow one opening a row brings the map back into view first.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Map as MapIcon } from "lucide-react";
import Link from "next/link";

import { AlertPanel } from "@/components/authority/alert-panel";
import { CitizenInbox } from "@/components/authority/citizen-inbox";
import { DeskReportsProvider, useDeskReportsLoader } from "@/components/authority/desk-reports";
import { ClosurePanel } from "@/components/authority/closure-panel";
import { OpsLog } from "@/components/authority/ops-log";
import {
  DEFAULT_OFFICER,
  PassphraseGate,
  writesDisabledReason,
  type GateStatus,
} from "@/components/authority/passphrase-gate";
import { PumpPanel } from "@/components/authority/pump-panel";
import { SituationNote } from "@/components/authority/situation-note";
import { Button } from "@/components/ui/button";
import { AppShell } from "@/components/varuna/app-shell";
import { CyclePicker } from "@/components/varuna/cycle-picker";
import { EmptyState } from "@/components/varuna/empty-state";
import { ENTRY_AOI, GlobeEntry } from "@/components/varuna/globe-entry";
import { PageHeader } from "@/components/varuna/page-header";
import {
  clearPassphrase,
  isGateRefusal,
  loadOpsLog,
  opsRefusal,
  readPassphrase,
  type OpsEntry,
  type OpsRefusal,
} from "@/lib/api/ops";

/** The city this desk acts for until the switcher threads one through (task D-09). */
const DESK_CITY = "mumbai";

/**
 * Remembers, for this tab only, that the desk's entry has played.
 *
 * Its own key, not the dashboard's. `sessionStorage` survives a reload, so an officer who
 * refreshes the desk during an incident does not watch it again; it is cleared when the tab
 * closes, so the next rehearsal opens on it.
 */
export const DESK_INTRO_SESSION_KEY = "varuna.authority-intro.played";

/** The ward map region's accessible name; the entry hands over to it. */
export const WARD_MAP_LABEL = "Ward map";

/** `ENTRY_AOI` as the one line the placeholder prints, so the frame names the box it is. */
function aoiLine([west, south, east, north]: readonly [number, number, number, number]): string {
  return `${west}–${east} °E, ${south}–${north} °N`;
}

/**
 * What the ward map region shows until a map is passed in.
 *
 * Not "loading": nothing is loading, and SPEC.md rule 6 does not let a screen claim otherwise.
 * It says what is missing, what will fill it, and where the same forecast can be seen now - and it
 * prints the AOI so the frame the entry lands on is visibly the box the globe ended on rather than
 * an empty rectangle.
 */
function WardMapPlaceholder() {
  return (
    <div className="bg-ink relative flex h-full w-full items-center justify-center overflow-hidden">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-35"
        style={{
          backgroundImage:
            "linear-gradient(to right, var(--line) 1px, transparent 1px), linear-gradient(to bottom, var(--line) 1px, transparent 1px)",
          backgroundSize: "48px 48px",
        }}
      />
      <EmptyState
        icon={MapIcon}
        title="No ward map yet"
        description="Open the console to see this cycle's water."
        action={
          <Link href="/console" className="text-tide type-small underline underline-offset-2">
            Open the console
          </Link>
        }
      />
      <p className="num text-text-3 type-micro absolute right-3 bottom-2">{aoiLine(ENTRY_AOI)}</p>
    </div>
  );
}

export interface AuthorityScreenProps {
  /**
   * The map the entry cross-fades into, drawn across the top of the desk.
   *
   * The integrator passes the photorealistic 3D city here from `app/authority/page.tsx`. Whatever
   * it is, it must fill its box (`absolute inset-0` inside this region's `relative` frame) and be
   * framed on {@link ENTRY_AOI} on its *first* paint - not after a fly-to - or the handover lands
   * somewhere the globe was not. When it is absent the region says so rather than pretending.
   */
  wardMap?: React.ReactNode;
}

export function AuthorityScreen({ wardMap }: AuthorityScreenProps = {}) {
  const [status, setStatus] = useState<GateStatus>("checking");
  const [gateReason, setGateReason] = useState<string | null>(null);
  const [officer, setOfficer] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | undefined>(undefined);
  // Bumped after every write so the log and the inbox refetch; the panels keep their own state.
  const [refreshKey, setRefreshKey] = useState(0);
  const [entries, setEntries] = useState<OpsEntry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [logError, setLogError] = useState<string | null>(null);
  // Whether the entry has handed over. Nothing on the desk is hidden behind it - the map region
  // is mounted and framed from the first paint, which is the handover rule - so this only marks
  // the region for a reader of the DOM and for the tests that assert the desk survives the entry.
  const [handedOver, setHandedOver] = useState(false);
  const entryDone = useCallback(() => setHandedOver(true), []);

  // The log is the one read that answers both questions this screen opens with: what has been
  // done, and whether this API accepts writes at all.
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      setLogError(null);
      try {
        const log = await loadOpsLog({ city: DESK_CITY, signal: controller.signal });
        if (controller.signal.aborted) return;
        setEntries(log.entries);
        setTotal(log.nEntries);
        if (!log.writesEnabled) {
          setStatus("disabled");
          // The API's own field; an older API only says it among the notes, so fall back to the
          // note that states the reason (not the last one, which is about officer names).
          setGateReason(log.writesDisabledReason ?? writesDisabledReason(log.notes));
          setOfficer(null);
          clearPassphrase();
          return;
        }
        // A tab that already holds an accepted passphrase stays open across a reload.
        setStatus("locked");
        setGateReason(null);
        if (readPassphrase()) setOfficer((current) => current ?? DEFAULT_OFFICER);
      } catch (failure: unknown) {
        if (controller.signal.aborted) return;
        const refusal = opsRefusal(failure);
        setEntries([]);
        setLogError(refusal.message);
        setStatus(refusal.kind === "unreachable" ? "unreachable" : "disabled");
        setGateReason(refusal.message);
      }
    })();
    return () => controller.abort();
  }, [refreshKey]);

  const wrote = useCallback(() => setRefreshKey((key) => key + 1), []);

  // A rejected passphrase closes the desk rather than leaving a screen of controls that will all
  // fail: the passphrase was changed where the API runs, or this tab is holding a stale one.
  const gateRefused = useCallback((refusal: OpsRefusal) => {
    if (!isGateRefusal(refusal)) return;
    clearPassphrase();
    setOfficer(null);
    setStatus("locked");
  }, []);

  const signOut = useCallback(() => {
    clearPassphrase();
    setOfficer(null);
    setStatus("locked");
  }, []);

  const open = status === "locked" && officer !== null;

  // One list for the inbox and the ward map's pins: the desk's exact one once the desk is open,
  // the public one before that. Reloaded after every write through `refreshKey`.
  const deskReports = useDeskReportsLoader({
    city: DESK_CITY,
    deskOpen: open,
    refreshKey,
    onGateRefused: gateRefused,
  });

  // Opening a row flies the map; on a narrow screen, where the inbox sits under the map, bring the
  // map back into view so the flight is seen. `nearest` moves nothing when it is already visible.
  const mapRegion = useRef<HTMLElement>(null);
  const { focus: reportFocus } = deskReports;
  useEffect(() => {
    const region = mapRegion.current;
    if (!reportFocus || !region || typeof region.scrollIntoView !== "function") return;
    region.scrollIntoView({ block: "nearest" });
  }, [reportFocus]);

  return (
    <AppShell>
      <DeskReportsProvider value={deskReports}>
        <div className="h-full min-h-0 overflow-y-auto">
          <div className="flex flex-col gap-6 p-6">
            <div className="grid shrink-0 gap-4 lg:grid-cols-[3fr_2fr]">
              {/* Mounted before the gate is decided and framed on the AOI, so the entry's cross-fade
                lands on a frame that was already there (UI_SPEC 2's handover rule). */}
              <section
                ref={mapRegion}
                data-slot="ward-map"
                data-handover={handedOver ? "done" : "playing"}
                aria-label={WARD_MAP_LABEL}
                className="rounded-panel border-line relative h-[clamp(320px,62vh,640px)] overflow-hidden border"
              >
                {wardMap ?? <WardMapPlaceholder />}
              </section>

              {/* The same height as the map beside it. 62 % of the viewport, not 50: at 1366 x 768
                  half the screen left the list 118 px for a 141 px card, so the officer saw no
                  whole report beside the map (SPEC.md 6.5 makes 1366 x 768 a size that works). */}
              <CitizenInbox
                className="lg:h-[clamp(320px,62vh,640px)]"
                reports={deskReports}
                access={{ open, gate: status, reason: gateReason, officer }}
                onWrote={wrote}
                onGateRefused={gateRefused}
              />
            </div>

            <PageHeader
              title="Ward officer's desk"
              description="Tell VARUNA what it cannot know: closed streets, broken pumps, seen alerts."
              honesty="Prototype access"
              actions={
                open ? (
                  <Button type="button" variant="ghost" className="h-11 px-4" onClick={signOut}>
                    Sign out
                  </Button>
                ) : null
              }
            />

            {open ? (
              <>
                <p className="type-small text-text-2 max-w-[72ch]">
                  Acting as {officer}. Every edit is appended; no forecast is rewritten.
                </p>

                <CyclePicker currentRunId={runId} onPick={setRunId} />

                <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
                  <section className="flex flex-col gap-4" aria-labelledby="changes-heading">
                    <div>
                      <h2 id="changes-heading" className="type-h3 text-text">
                        Changes the forecast
                      </h2>
                      <p className="type-small text-text-2 max-w-[72ch]">
                        Read back by the router, the pump optimiser and the alert queue.
                      </p>
                    </div>
                    <ClosurePanel
                      runId={runId}
                      city={DESK_CITY}
                      officer={officer}
                      onWrote={wrote}
                      onGateRefused={gateRefused}
                    />
                    <PumpPanel
                      runId={runId}
                      city={DESK_CITY}
                      officer={officer}
                      onWrote={wrote}
                      onGateRefused={gateRefused}
                    />
                    <AlertPanel
                      runId={runId}
                      city={DESK_CITY}
                      officer={officer}
                      onWrote={wrote}
                      onGateRefused={gateRefused}
                    />
                  </section>

                  <section className="flex flex-col gap-4" aria-labelledby="recorded-heading">
                    <div>
                      <h2 id="recorded-heading" className="type-h3 text-text">
                        Recorded only
                      </h2>
                      <p className="type-small text-text-2 max-w-[72ch]">
                        For people only; reaches no forecast, route or alert.
                      </p>
                    </div>
                    <SituationNote officer={officer} />
                  </section>
                </div>
              </>
            ) : (
              <PassphraseGate
                status={status}
                reason={gateReason}
                onOpen={(name) => {
                  setOfficer(name);
                  setStatus("locked");
                }}
              />
            )}

            <OpsLog entries={entries} total={total} error={logError} />
          </div>
        </div>
      </DeskReportsProvider>

      {/* Last in the tree and `fixed` in its own right: a sibling of the desk, not a wrapper
          around it, so nothing on the desk waits for it (M27). */}
      <GlobeEntry sessionKey={DESK_INTRO_SESSION_KEY} slot="authority-intro" onDone={entryDone} />
    </AppShell>
  );
}

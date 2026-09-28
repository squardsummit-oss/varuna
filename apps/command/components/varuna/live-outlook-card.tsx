"use client";

import { ChevronDown } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { DepthChip } from "@/components/varuna/depth-chip";
import { Skeleton } from "@/components/varuna/skeleton";
import {
  DEFAULT_OUTLOOK_CITY,
  formatOutlookAge,
  loadOutlook,
  outlookAgeSeconds,
  outlookSourceLine,
  outlookSpanLabel,
  outlookWindowLabel,
  type Outlook,
  type OutlookState,
} from "@/lib/api/outlook";
import { formatIstTime, parseIso } from "@/lib/stores/time";
import { cn } from "@/lib/utils";

/**
 * How often an open card asks again. The API caches one answer per weather copy and refreshes
 * that copy every 15 minutes, so a repeat inside that window costs it tens of milliseconds.
 */
export const OUTLOOK_REFRESH_MS = 5 * 60_000;

/**
 * Past this age an answer the card is holding has missed a refresh. The API calls a weather copy
 * stale after 15 minutes and the card asks every 5, so with the API answering no copy on screen is
 * older than 20 minutes; one that is has been kept because the API stopped answering.
 */
export const HELD_STALE_AFTER_S = 15 * 60 + OUTLOOK_REFRESH_MS / 1000;

/** Streets named under the sentence; the API sends its five worst. */
const WORST_SHOWN = 3;

export type Freshness = "live" | "stale" | "expired" | "none";

/**
 * How current an answer is, judged against the card's own clock as well as the API's flags: an
 * answer kept open for hours after the API went quiet is past its window even though the API said
 * `expired: false` when it sent it.
 */
export function outlookFreshness(state: OutlookState, nowMs: number): Freshness {
  if (state.kind !== "ready") return "none";
  const { outlook } = state;
  const validTo = parseIso(outlook.valid_to);
  if (outlook.expired || (validTo !== null && nowMs > validTo.getTime())) return "expired";
  if (outlook.source.stale || outlookAgeSeconds(outlook, nowMs) > HELD_STALE_AFTER_S) {
    return "stale";
  }
  return "live";
}

/**
 * The state after an answer arrives. A failed refresh keeps the answer already on screen for the
 * same city, which then ages visibly into "Stale copy" and "Window passed", rather than replacing
 * a forecast with "could not be loaded" over one missed request. A refusal always replaces it.
 */
export function nextOutlookState(
  previous: OutlookState,
  next: OutlookState,
  city: string,
): OutlookState {
  if (next.kind === "unavailable" && previous.kind === "ready" && previous.outlook.city === city) {
    return previous;
  }
  return next;
}

/** The word beside the title, so the dot is never the only carrier of the state (6.10). */
const STATUS_WORD: Record<OutlookState["kind"], string> = {
  loading: "Loading",
  ready: "Live weather",
  refused: "Not for this city",
  unavailable: "Unavailable",
};

/**
 * Collapsed and never asked, nothing is known about the weather copy yet: it may be fresh, stale
 * or missing (the offline venue). The header claims no freshness until an answer says which.
 */
const NOT_LOADED_WORD = "Not loaded";

function statusWord(state: OutlookState, open: boolean, fresh: Freshness): string {
  if (!open && state.kind === "loading") return NOT_LOADED_WORD;
  if (fresh === "expired") return "Window passed";
  if (fresh === "stale") return "Stale copy";
  return STATUS_WORD[state.kind];
}

function sentenceCase(text: string): string {
  return text.length > 0 ? text[0].toUpperCase() + text.slice(1) : text;
}

function formatMm(mm: number): string {
  if (mm <= 0) return "0 mm";
  return `${mm.toFixed(mm >= 10 ? 0 : 1)} mm`;
}

export interface LiveOutlookViewProps {
  state: OutlookState;
  /** The clock the age is counted against; the card ticks it once a minute. */
  nowMs: number;
  onRetry?: () => void;
}

/**
 * The outlook's body for each of its states: loading, ready (fresh, stale or past its window),
 * refused for this city, or unavailable. Every number in it is one the API sent.
 */
export function LiveOutlookView({ state, nowMs, onRetry }: LiveOutlookViewProps) {
  if (state.kind === "loading") {
    return (
      <div className="px-3 py-3" aria-busy="true">
        <p className="sr-only">Loading today&apos;s outlook</p>
        <Skeleton lines={3} />
      </div>
    );
  }

  if (state.kind === "refused") {
    return (
      <div className="flex flex-col gap-1.5 px-3 py-3">
        <p className="type-small text-text">No live outlook for this city.</p>
        <p className="type-micro text-text-2">{state.reason}</p>
      </div>
    );
  }

  if (state.kind === "unavailable") {
    return (
      <div className="flex flex-col items-start gap-2 px-3 py-3">
        <p className="type-small text-text">Today&apos;s outlook could not be loaded.</p>
        <p className="type-micro text-text-2">{state.reason}</p>
        {onRetry ? (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Try again
          </Button>
        ) : null}
      </div>
    );
  }

  return <ReadyBody outlook={state.outlook} nowMs={nowMs} fresh={outlookFreshness(state, nowMs)} />;
}

function ReadyBody({
  outlook,
  nowMs,
  fresh,
}: {
  outlook: Outlook;
  nowMs: number;
  fresh: Freshness;
}) {
  const worst = outlook.summary.worst.slice(0, WORST_SHOWN);
  const age = formatOutlookAge(outlookAgeSeconds(outlook, nowMs));
  // Taken from the steps the API sent, never assumed to be three hours: a short outlook's
  // sentence says "the next 100 min", and the card says the same beside it.
  const span = outlookSpanLabel(outlook);
  return (
    <div className="flex flex-col gap-2.5 px-3 py-3">
      {/* The time base first: everything else on this screen is 2 July 2019. */}
      <p className="num type-micro text-text-3">
        Today, not the replay: {outlookWindowLabel(outlook)}
      </p>

      <p className="type-small text-text">{outlook.summary.sentence}</p>

      {worst.length > 0 ? (
        <ul className="flex flex-col gap-1" aria-label={`Deepest streets in ${span}`}>
          {worst.map((street) => (
            <li key={street.segment_id} className="flex items-center gap-2">
              <span
                className="type-micro text-text-2 min-w-0 flex-1 truncate"
                title={street.display_name || street.name || undefined}
              >
                {street.display_name || street.name || `Segment ${street.segment_id}`}
              </span>
              <DepthChip cm={street.peak_p50_cm} size="sm" />
              <span className="num type-micro text-text-3 shrink-0">
                {formatIstTime(street.peak_ts)}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {/* Worded without a cause: a copy is stale when Open-Meteo did not answer and also under
          VARUNA_OFFLINE=1, where nothing was asked. The API's note under "How this is computed"
          names which. */}
      {fresh === "expired" ? (
        <p className="type-micro text-status-degraded">
          This outlook&apos;s window has passed and no newer weather has arrived; the weather copy
          was fetched {age}.
        </p>
      ) : fresh === "stale" ? (
        <p className="type-micro text-status-degraded">
          This runs on a weather copy fetched {age}; no newer copy has arrived.
        </p>
      ) : null}

      <p className="num type-micro text-text-2">
        {formatMm(outlook.rain_total_mm)} of rain over {span}, for one grid cell{" "}
        {outlook.source.grid_cell_km.toFixed(1)} km from the centre of the mapped area. Source:{" "}
        {outlookSourceLine(outlook, nowMs)}.
      </p>

      <p className="num type-micro text-text-3">
        {sentenceCase(outlook.method)}. Measured skill against VARUNA-Twin on runs it never saw:
        RMSE {outlook.skill.rmse_cm.toFixed(1)} cm, CSI {outlook.skill.csi_30cm.toFixed(3)} at 30
        cm.
      </p>

      {/* The API's own notes, verbatim: the base state it removed, the blockage each member
          drew, what it cannot see. Printed rather than paraphrased. */}
      {outlook.notes.length > 0 ? (
        <details>
          <summary className="type-micro text-text-2 hover:text-text focus-visible:ring-tide rounded-control cursor-pointer focus-visible:ring-2 focus-visible:outline-none">
            How this is computed
          </summary>
          <div className="mt-1.5 flex flex-col gap-1.5">
            {outlook.notes.map((note) => (
              <p key={note} className="num type-micro text-text-3">
                {note}
              </p>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

export interface LiveOutlookCardProps {
  city?: string;
  /** Collapsed behind its header until opened; the outlook loads on first open. */
  collapsible?: boolean;
  defaultOpen?: boolean;
  className?: string;
}

/**
 * "Today, next 3 h" (`GET /v1/outlook`): the one forecast whose clock is today. Open-Meteo's rain
 * for the city through Flash-lite at 50 members, labelled as exactly that, beside screens whose
 * clock is the 2 July 2019 replay. It reads nothing from the replay and writes nothing to it, so
 * opening it never moves the scrub.
 *
 * Shared by the console (collapsed, over the map) and the citizen dashboard (open).
 */
export function LiveOutlookCard({
  city = DEFAULT_OUTLOOK_CITY,
  collapsible = true,
  defaultOpen = false,
  className,
}: LiveOutlookCardProps) {
  const [open, setOpen] = useState(collapsible ? defaultOpen : true);
  const sectionRef = useRef<HTMLElement>(null);
  // Set when the reader opens the card, cleared once its answer has been brought into view.
  const revealRef = useRef(false);
  const [state, setState] = useState<OutlookState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [nowMs, setNowMs] = useState(() => Date.now());
  // A different city starts from loading, never from the previous city's answer (React's pattern
  // for state that resets when a prop changes).
  const [seenCity, setSeenCity] = useState(city);
  if (city !== seenCity) {
    setSeenCity(city);
    setState({ kind: "loading" });
  }

  const retry = useCallback(() => {
    setState({ kind: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  // Loads when first opened, then again every OUTLOOK_REFRESH_MS while open.
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const load = () => {
      void loadOutlook(city, { signal: controller.signal }).then((next) => {
        if (controller.signal.aborted) return;
        setState((previous) => nextOutlookState(previous, next, city));
        setNowMs(Date.now());
      });
    };
    load();
    const timer = window.setInterval(load, OUTLOOK_REFRESH_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [open, city, attempt]);

  // The age reads against a clock that moves once a minute, not the moment of the answer.
  useEffect(() => {
    if (!open) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [open]);

  // Opened by the reader inside a scrolling column (the console's), the answer is brought into
  // view once it arrives, with an instant scroll: no motion outside the catalogue (M23).
  useEffect(() => {
    if (!open || !revealRef.current) return;
    sectionRef.current?.scrollIntoView?.({ block: "nearest" });
    if (state.kind !== "loading") revealRef.current = false;
  }, [open, state.kind]);

  const fresh = outlookFreshness(state, nowMs);
  const bodyId = `live-outlook-${city}`;
  const header = (
    <>
      <span
        aria-hidden="true"
        className={cn(
          "size-2 shrink-0 rounded-full",
          fresh === "live"
            ? "bg-status-live"
            : fresh === "none"
              ? "border-line-strong border"
              : "bg-status-degraded",
        )}
      />
      <span className="type-small text-text flex-1">Today, next 3 h</span>
      <span className="type-micro text-text-3 shrink-0">{statusWord(state, open, fresh)}</span>
    </>
  );

  return (
    <section
      ref={sectionRef}
      data-testid="live-outlook-card"
      aria-label="Today, next 3 h"
      className={cn(
        "rounded-panel border-line w-full border bg-[var(--ink)]/85 backdrop-blur-[12px]",
        className,
      )}
    >
      {collapsible ? (
        <button
          type="button"
          onClick={() => {
            revealRef.current = !open;
            setOpen((o) => !o);
          }}
          aria-expanded={open}
          aria-controls={bodyId}
          className={cn(
            "hover:bg-well focus-visible:ring-tide flex h-9 w-full items-center gap-2 px-3 text-left focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset",
            open ? "rounded-t-panel" : "rounded-panel",
          )}
        >
          {header}
          <ChevronDown
            size={14}
            strokeWidth={1.75}
            aria-hidden="true"
            className={cn("text-text-3 shrink-0", !open && "-rotate-90")}
          />
        </button>
      ) : (
        <div className="flex h-9 items-center gap-2 px-3">{header}</div>
      )}
      {open ? (
        <div id={bodyId} className="border-line border-t">
          <LiveOutlookView state={state} nowMs={nowMs} onRetry={retry} />
        </div>
      ) : null}
    </section>
  );
}

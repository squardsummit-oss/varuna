"use client";

/**
 * Close or reopen a street (UI_SPEC 6 left column, TECH_SPEC 3.6, task D-15).
 *
 * **This is the act that changes the next answer.** A closure beats the forecast: the router
 * treats the segment as impassable whatever the water is doing, the road-conditions feed marks
 * it `cause: "closure"`, and the reason the officer types here comes back on the route as a
 * structured reason the citizen screen words. Nothing is written into a run, so a re-bake is
 * still byte-identical (SPEC.md rule 8) - the overlay is applied when a route is read.
 *
 * **Which streets are offered, and why not all of them.** The picker lists what the chosen cycle
 * says will stop the chosen vehicle, plus what the desk has already shut, because that is an
 * officer's own workset and it arrives with the `segment_id` a closure needs. The city carries
 * 21,296 segments and its map layer is 8 MB; a typeahead over all of them would cost seconds
 * before a word could be typed. A street outside the list is still closable by its id, and the
 * form says so rather than pretending the list is the city.
 *
 * **An expiry is relative, not absolute.** The API refuses a closure whose `until` has already
 * passed, and the clock on this screen is the real one while the map is replaying 2 July 2019 -
 * so "until 10:00" would be refused and the officer would have no idea why. The choices are
 * "until reopened" and a number of hours from now.
 */

import { Search, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Panel } from "@/components/varuna/panel";
import { Skeleton } from "@/components/varuna/skeleton";
import { formatIst } from "@/lib/format";
import {
  loadClosures,
  loadStreetOptions,
  opsRefusal,
  postClosure,
  type Closure,
  type OpsRefusal,
  type StreetOption,
} from "@/lib/api/ops";
import { cn } from "@/lib/utils";

import { ActionResult, type ActionOutcome } from "./action-result";

/** The vehicles the street list can be about; the feed's own profile names. */
const PROFILES = [
  { key: "two_wheeler", label: "two-wheeler" },
  { key: "car", label: "car" },
  { key: "bus", label: "bus" },
  { key: "ambulance", label: "ambulance" },
] as const;

/** How long a closure may be set for, beyond "until reopened". */
const DURATIONS = [
  { hours: 0, label: "Until reopened" },
  { hours: 2, label: "2 hours" },
  { hours: 6, label: "6 hours" },
  { hours: 24, label: "24 hours" },
] as const;

/** How many matches the list shows before asking for a narrower search. */
const MAX_MATCHES = 12;

/**
 * An instant as ISO 8601 in IST, which is the only offset this repository writes (SPEC.md 12).
 *
 * Built by hand rather than with `toISOString`, which would send UTC: the API parses the offset
 * it is given, and a log that reads 04:42 for an act at 10:12 would be wrong in the one column
 * an audit trail exists for.
 */
export function istIso(date: Date): string {
  const shifted = new Date(date.getTime() + (330 + date.getTimezoneOffset()) * 60_000);
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${shifted.getFullYear()}-${pad(shifted.getMonth() + 1)}-${pad(shifted.getDate())}` +
    `T${pad(shifted.getHours())}:${pad(shifted.getMinutes())}:${pad(shifted.getSeconds())}+05:30`
  );
}

/** `until` for a duration in hours from `now`; null for "until reopened". */
export function untilFor(hours: number, now: Date = new Date()): string | null {
  return hours > 0 ? istIso(new Date(now.getTime() + hours * 3_600_000)) : null;
}

/** The streets matching what the officer typed, named streets first, capped. */
export function matchStreets(streets: StreetOption[], query: string): StreetOption[] {
  const needle = query.trim().toLowerCase();
  const matched = needle
    ? streets.filter(
        (street) =>
          (street.name ?? "").toLowerCase().includes(needle) ||
          (street.displayName ?? "").toLowerCase().includes(needle) ||
          street.segmentId.toLowerCase().includes(needle),
      )
    : streets;
  return [...matched]
    .sort((a, b) => {
      if (Boolean(a.name) !== Boolean(b.name)) return a.name ? -1 : 1;
      return b.peakDepthCm - a.peakDepthCm;
    })
    .slice(0, MAX_MATCHES);
}

/**
 * How a street reads in the list and in the result sentence.
 *
 * OSM's name, else the feed's display name for the 52.6 % of Mumbai's segments OSM does not name
 * ("off Dr Ambedkar Road", "Service road near Wadala Depot"), else "Segment <id>". Never
 * "Unnamed road": a segment id typed into the form carries no name because this panel never
 * asked for one - `Dr Babasaheb Ambedkar Marg` closed by its id once read "Unnamed road
 * S618477973-001", a fact the screen invented - and a listed street always has a display name.
 */
export function streetLabel(
  street: Pick<StreetOption, "name" | "segmentId" | "displayName"> & { known?: boolean },
): string {
  if (street.name) return street.name;
  if (street.displayName && street.known !== false)
    return `${street.displayName} (${street.segmentId})`;
  return `Segment ${street.segmentId}`;
}

export interface ClosurePanelProps {
  /** The cycle whose forecast the street list is drawn from. */
  runId?: string;
  city?: string;
  officer: string;
  /** Raised after a successful write so the screen can refresh the log. */
  onWrote?: () => void;
  /** Raised when the API rejects the passphrase, so the screen can close the desk. */
  onGateRefused?: (refusal: OpsRefusal) => void;
  className?: string;
}

interface Result {
  outcome: ActionOutcome;
  message: string;
  notes?: string[];
  at?: string | null;
}

export function ClosurePanel({
  runId,
  city,
  officer,
  onWrote,
  onGateRefused,
  className,
}: ClosurePanelProps) {
  const [profile, setProfile] = useState<string>("car");
  const [streets, setStreets] = useState<StreetOption[] | null>(null);
  const [streetsError, setStreetsError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<StreetOption | null>(null);
  const [segmentId, setSegmentId] = useState("");
  const [reason, setReason] = useState("");
  const [hours, setHours] = useState(0);
  const [closures, setClosures] = useState<Closure[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      setStreets(null);
      setStreetsError(null);
      try {
        const options = await loadStreetOptions({
          profile,
          runId,
          city,
          signal: controller.signal,
        });
        if (!controller.signal.aborted) setStreets(options);
      } catch (error: unknown) {
        if (controller.signal.aborted) return;
        setStreets([]);
        setStreetsError(opsRefusal(error).message);
      }
    })();
    return () => controller.abort();
  }, [city, profile, runId]);

  const refreshClosures = useCallback(
    (signal?: AbortSignal) => {
      loadClosures({ city, signal })
        .then((set) => setClosures(set.closures))
        .catch(() => setClosures([]));
    },
    [city],
  );

  useEffect(() => {
    const controller = new AbortController();
    refreshClosures(controller.signal);
    return () => controller.abort();
  }, [refreshClosures]);

  const matches = useMemo(() => matchStreets(streets ?? [], query), [streets, query]);
  // `known: false` marks a segment this panel was handed rather than looked up, so the result
  // sentence says "Segment <id>" instead of asserting the road has no name.
  const target =
    picked ?? (segmentId.trim() ? { segmentId: segmentId.trim(), name: null, known: false } : null);

  const act = useCallback(
    async (
      kind: "close" | "reopen",
      subject: { segmentId: string; name: string | null; known?: boolean },
    ) => {
      setBusy(true);
      setResult(null);
      try {
        const response = await postClosure({
          segmentId: subject.segmentId,
          reason: kind === "close" ? reason.trim() : "",
          until: kind === "close" ? untilFor(hours) : null,
          user: officer,
          city,
          reopen: kind === "reopen",
        });
        setClosures(response.closures);
        setResult({
          outcome: "changed",
          message:
            kind === "close"
              ? `${streetLabel(subject)} is closed. The next route avoids it, and the reason you gave is what a driver is told.`
              : `${streetLabel(subject)} is open again. The next route may use it if the forecast allows.`,
          notes: response.notes,
          at: response.entry.ts ? formatIst(response.entry.ts) : null,
        });
        if (kind === "close") {
          setReason("");
          setPicked(null);
          setSegmentId("");
          setQuery("");
        }
        onWrote?.();
      } catch (error) {
        const refusal = opsRefusal(error);
        setResult({ outcome: "refused", message: refusal.message });
        onGateRefused?.(refusal);
      } finally {
        setBusy(false);
      }
    },
    [city, hours, officer, onGateRefused, onWrote, reason],
  );

  return (
    <Panel
      className={className}
      title="Close or reopen a street"
      description="A closure beats the forecast: the next route treats the street as impassable whatever the water is doing."
    >
      <div className="space-y-4">
        <fieldset className="space-y-1.5">
          <legend className="type-small text-text">Streets this cycle says will stop a</legend>
          <div className="flex flex-wrap gap-1.5">
            {PROFILES.map((option) => (
              <Button
                key={option.key}
                type="button"
                size="sm"
                variant={profile === option.key ? "secondary" : "ghost"}
                aria-pressed={profile === option.key}
                onClick={() => setProfile(option.key)}
              >
                {option.label}
              </Button>
            ))}
          </div>
        </fieldset>

        <div className="space-y-1.5">
          <Label htmlFor="closure-search" className="type-small text-text">
            Find the street
          </Label>
          <div className="relative">
            <Search
              className="text-text-3 pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2"
              aria-hidden="true"
            />
            <Input
              id="closure-search"
              className="h-11 pl-8"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Dr Ambedkar Road"
              aria-describedby="closure-search-note"
            />
          </div>
          <p id="closure-search-note" className="type-micro text-text-3">
            This list is the streets in trouble on the chosen cycle, plus the ones already closed.
            Any other street can be closed by its segment id below.
          </p>
        </div>

        {streets === null ? (
          <Skeleton lines={3} />
        ) : streetsError ? (
          <p className="type-small text-text-2">{streetsError}</p>
        ) : matches.length === 0 ? (
          <p className="type-small text-text-2">
            No street on this cycle matches that. Widen the vehicle, pick another cycle, or use the
            segment id.
          </p>
        ) : (
          <ul className="max-h-64 space-y-1 overflow-y-auto" aria-label="Streets">
            {matches.map((street) => {
              const selected = picked?.segmentId === street.segmentId;
              return (
                <li key={street.segmentId}>
                  <button
                    type="button"
                    aria-pressed={selected}
                    onClick={() => {
                      setPicked(selected ? null : street);
                      setSegmentId("");
                    }}
                    className={cn(
                      "rounded-control flex min-h-11 w-full items-center justify-between gap-3 border px-3 text-left",
                      selected
                        ? "border-tide bg-tide-soft text-text"
                        : "border-line bg-well/40 text-text-2 hover:bg-well",
                    )}
                  >
                    <span className="type-small min-w-0 truncate">{streetLabel(street)}</span>
                    <span className="num type-micro text-text-3 shrink-0">
                      {street.cause === "closure"
                        ? "already closed"
                        : `${Math.round(street.peakDepthCm)} cm at peak`}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <div className="space-y-1.5">
          <Label htmlFor="closure-segment" className="type-small text-text">
            Or a segment id
          </Label>
          <Input
            id="closure-segment"
            className="h-11"
            value={segmentId}
            onChange={(event) => {
              setSegmentId(event.target.value);
              setPicked(null);
            }}
            placeholder="seg-00421"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="closure-reason" className="type-small text-text">
            Why
          </Label>
          <Input
            id="closure-reason"
            className="h-11"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            maxLength={300}
            placeholder="Water over the kerb at the rail bridge"
            aria-describedby="closure-reason-note"
          />
          <p id="closure-reason-note" className="type-micro text-text-3">
            Required. A driver is shown these words, so the API refuses a closure without them.
          </p>
        </div>

        <fieldset className="space-y-1.5">
          <legend className="type-small text-text">For how long</legend>
          <div className="flex flex-wrap gap-1.5">
            {DURATIONS.map((option) => (
              <Button
                key={option.hours}
                type="button"
                size="sm"
                variant={hours === option.hours ? "secondary" : "ghost"}
                aria-pressed={hours === option.hours}
                onClick={() => setHours(option.hours)}
              >
                {option.label}
              </Button>
            ))}
          </div>
          <p className="type-micro text-text-3">
            Counted from now, on the real clock, not from the replay&rsquo;s clock.
          </p>
        </fieldset>

        <Button
          type="button"
          className="h-11 px-4"
          disabled={busy || !target || !reason.trim()}
          onClick={() => target && act("close", target)}
        >
          {busy ? "Closing" : "Close this street"}
        </Button>

        {result ? <ActionResult {...result} /> : null}

        <div className="border-line space-y-2 border-t pt-3">
          <h3 className="type-small text-text">
            Closed now{closures.length > 0 ? ` (${closures.length})` : ""}
          </h3>
          {closures.length === 0 ? (
            <p className="type-micro text-text-3">
              No street is closed by the desk. Every impassable street on the map is the
              forecast&rsquo;s own.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {closures.map((closure) => (
                <li
                  key={closure.id || closure.segmentId}
                  className="rounded-control border-line bg-well/40 flex items-start justify-between gap-3 border p-2.5"
                >
                  <div className="min-w-0">
                    <p className="type-small text-text truncate">{closure.segmentId}</p>
                    <p className="type-micro text-text-2">{closure.reason}</p>
                    <p className="num type-micro text-text-3">
                      {closure.user} · {formatIst(closure.ts)}
                      {closure.until ? ` · until ${formatIst(closure.until)}` : " · until reopened"}
                    </p>
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-11 shrink-0"
                    disabled={busy}
                    onClick={() =>
                      // The closure set carries no street name - the overlay stores the id the
                      // officer closed and nothing else - so the sentence names the segment.
                      act("reopen", {
                        segmentId: closure.segmentId,
                        name: null,
                        known: false,
                      })
                    }
                  >
                    <X className="size-4" aria-hidden="true" />
                    Reopen
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}

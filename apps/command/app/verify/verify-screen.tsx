"use client";

import { useEffect, useRef, useState, type ReactNode, type Ref } from "react";
import { MapPinOff } from "lucide-react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { AppShell } from "@/components/varuna/app-shell";
import { EmptyState } from "@/components/varuna/empty-state";
import { LimitationsList } from "@/components/varuna/limitations-list";
import { PageHeader } from "@/components/varuna/page-header";
import { Panel } from "@/components/varuna/panel";
import { PanelErrorBoundary } from "@/components/varuna/panel-error-boundary";
import { VerificationGrid, type ScoreTile } from "@/components/varuna/verification-grid";
import { formatIst } from "@/lib/format";
import { loadVerification, type ThresholdRow, type Verification } from "@/lib/api/verification";
import { navItem } from "@/lib/nav";

import { ContingencyPour } from "./contingency-pour";
import { RainSkillPanel } from "./rain-skill-panel";

/** Events that can be scored. Each one is a replay bundle with sourced ground-truth pins. */
const EVENTS = [{ id: "MUM-2019-07-02", label: "MUM-2019-07-02" }] as const;

/**
 * Names for the pin scorer's `unavailable` keys. They are about street depth at the pins; the
 * rain's Brier score and reliability are scored and drawn under Rain skill by lead time, so the
 * label says which probability is missing. An unknown key falls back to its own words.
 */
const UNAVAILABLE_LABEL: Record<string, string> = {
  depth_mae_cm: "Depth error at the pins",
  brier_score: "Brier score of street depth",
  reliability_diagram: "Reliability of street depth",
};

export function unavailableLabel(key: string): string {
  return UNAVAILABLE_LABEL[key] ?? key.replace(/_/g, " ");
}

const asScore = (value: number) => value.toFixed(2);
const asCount = (value: number) => value.toLocaleString("en-IN");
const asMinutes = (value: number) => `${value.toFixed(0)} min`;

/** Two decimals for a 0-to-1 score; null where the score has no denominator. */
function score(value: number | null): string | null {
  return value === null ? null : value.toFixed(2);
}

function tiles(v: Verification | null): ScoreTile[] {
  const h = v?.headline;
  const cm = v?.headlineThresholdCm ?? 15;
  return [
    {
      id: "csi",
      label: `CSI at ${cm} cm`,
      unit: "0 to 1, higher is better",
      value: h?.csi ?? null,
      format: asScore,
      note: "Critical success index against the sourced pins in the window.",
    },
    {
      id: "pod",
      label: `POD at ${cm} cm`,
      unit: "0 to 1, higher is better",
      value: h?.pod ?? null,
      format: asScore,
      note: "Share of sourced pins with water forecast above the threshold.",
    },
    {
      id: "far",
      label: `FAR at ${cm} cm`,
      unit: "0 to 1, lower is better",
      value: h?.far ?? null,
      format: asScore,
      note: "Streets flagged near a pin that no record backs. A lower bound.",
    },
    {
      id: "lead",
      label: "Median lead time",
      unit: "minutes before the report",
      value: h?.medianLeadMin ?? null,
      format: asMinutes,
      note: "Over the pins flagged before the city logged them.",
    },
    {
      id: "pins",
      label: "Ground-truth pins",
      unit: "sourced, in the window",
      value: v?.nInWindow ?? null,
      format: asCount,
      note: "Curated public records, each with a source link.",
    },
  ];
}

function ThresholdTable({ rows }: { rows: ThresholdRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-line border-b text-left">
            {["Threshold", "Hits", "Misses", "False alarms", "CSI", "POD", "FAR", "Lead"].map(
              (head) => (
                <th key={head} className="type-micro text-text-2 px-2 py-2 font-medium">
                  {head}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.thresholdCm} className="border-line border-b last:border-b-0">
              <td className="num type-small text-text px-2 py-2">{r.thresholdCm} cm</td>
              <td className="num type-small text-text px-2 py-2">{r.contingency.hits}</td>
              <td className="num type-small text-text px-2 py-2">{r.contingency.misses}</td>
              <td className="num type-small text-text px-2 py-2">{r.contingency.falseAlarms}</td>
              <td className="num type-small text-text px-2 py-2">{score(r.csi) ?? "—"}</td>
              <td className="num type-small text-text px-2 py-2">{score(r.pod) ?? "—"}</td>
              <td className="num type-small text-text px-2 py-2">{score(r.far) ?? "—"}</td>
              <td className="num type-small text-text px-2 py-2">
                {r.medianLeadMin != null ? `${r.medianLeadMin.toFixed(0)} min` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A collapsed "Details": the method and caveats an expert may want, one click away. */
function Details({
  summary,
  children,
  ref,
}: {
  summary: string;
  children: ReactNode;
  ref?: Ref<HTMLDetailsElement>;
}) {
  // Uncontrolled, and its `open` attribute exempt from hydration checks: the browser opens a
  // disclosure itself when a fragment link (/verify#limitations) lands inside it, before hydration.
  return (
    <details ref={ref} suppressHydrationWarning className="border-line rounded-control border">
      <summary className="type-small text-text-2 hover:text-text focus-visible:ring-tide rounded-control cursor-pointer px-3 py-2 focus-visible:ring-2 focus-visible:outline-none">
        {summary}
      </summary>
      <div className="border-line border-t px-3 py-3">{children}</div>
    </details>
  );
}

/**
 * Verification dashboard (SPEC.md 7.10, task P9.7).
 *
 * Every figure is computed by `services/verify` from run artifacts and the bundle's curated pins.
 * The scores that cannot be computed are rendered too, with the reason: a dashboard that omits
 * what it cannot measure is a dashboard that flatters itself, and a judge asking "where is the
 * depth error?" deserves the answer rather than a blank.
 */
export function VerifyScreen() {
  const [eventId, setEventId] = useState<string>(EVENTS[0].id);
  const [answer, setAnswer] = useState<{
    event: string;
    result: Verification | null;
    error: string | null;
  } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    loadVerification(eventId, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setAnswer({ event: eventId, result, error: null });
      })
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return;
        setAnswer({
          event: eventId,
          result: null,
          error: failure instanceof Error ? failure.message : String(failure),
        });
      });
    return () => controller.abort();
  }, [eventId]);

  // The landing footnote links to /verify#limitations: open the disclosure that holds them.
  const limitsRef = useRef<HTMLDetailsElement | null>(null);
  useEffect(() => {
    const sync = () => {
      if (window.location.hash !== "#limitations" || !limitsRef.current) return;
      limitsRef.current.open = true;
      window.requestAnimationFrame(() =>
        document.getElementById("limitations")?.scrollIntoView({ block: "start" }),
      );
    };
    const id = window.setTimeout(sync, 0);
    window.addEventListener("hashchange", sync);
    return () => {
      window.clearTimeout(id);
      window.removeEventListener("hashchange", sync);
    };
  }, []);

  const current = answer?.event === eventId ? answer : null;
  const v = current?.result ?? null;
  const error = current?.error ?? null;
  const loading = current === null;

  return (
    <AppShell>
      <div className="h-full min-h-0 overflow-y-auto">
        <div className="mx-auto flex max-w-[1400px] flex-col gap-6 p-6">
          <PageHeader
            title={navItem("verify").label}
            screen={navItem("verify")}
            description="Where VARUNA is right, where it is wrong, and how we score ourselves."
            honesty="Reconstructed replay"
            actions={
              <div className="flex flex-col items-end gap-1">
                <Select value={eventId} onValueChange={(value) => setEventId(String(value))}>
                  <SelectTrigger aria-label="Event">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {EVENTS.map((event) => (
                      <SelectItem key={event.id} value={event.id}>
                        {event.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="type-micro text-text-3">
                  {v?.window
                    ? `Forecast window ${formatIst(v.window[0])} to ${formatIst(v.window[1])} IST`
                    : "Scores appear once the event is baked."}
                </p>
              </div>
            }
          />

          {error ? <p className="type-small text-text-2">{error}</p> : null}

          <PanelErrorBoundary title="Headline scores">
            <Panel
              title="Headline scores"
              description="Scored from run artifacts against sourced pins."
            >
              {loading ? (
                <Skeleton className="h-24 w-full" />
              ) : (
                <VerificationGrid tiles={tiles(v)} groundTruthCount={v?.nInWindow ?? null} />
              )}
            </Panel>
          </PanelErrorBoundary>

          <PanelErrorBoundary title="Contingency by threshold">
            <Panel
              title="Contingency by threshold"
              description="Pins record waterlogging, not depth, so we score three thresholds."
            >
              {loading ? (
                <Skeleton className="h-64 w-full" />
              ) : v && v.byThreshold.length > 0 ? (
                <div className="flex flex-col gap-4">
                  <ContingencyPour
                    groundTruthCount={v.nInWindow}
                    headlineCm={v.headlineThresholdCm}
                    columns={v.byThreshold.map((r) => ({
                      thresholdCm: r.thresholdCm,
                      hits: r.contingency.hits,
                      misses: r.contingency.misses,
                      falseAlarms: r.contingency.falseAlarms,
                      csi: r.csi,
                      pod: r.pod,
                      far: r.far,
                      medianLeadMin: r.medianLeadMin,
                    }))}
                  />
                  <Details summary="Every number by threshold">
                    <ThresholdTable rows={v.byThreshold} />
                  </Details>
                </div>
              ) : (
                <EmptyState
                  title="Not scored yet"
                  description="Bake the event and the table fills from its runs."
                />
              )}
            </Panel>
          </PanelErrorBoundary>

          {/* Section 7.10's SkillByLeadChart, served by /v1/verification/rain-skill. */}
          <PanelErrorBoundary title="Rain skill by lead time">
            <RainSkillPanel event={eventId} />
          </PanelErrorBoundary>

          <div className="grid gap-4 xl:grid-cols-2">
            <PanelErrorBoundary title="Where we are wrong">
              <Panel
                title="Where we are wrong"
                description="Pins the model missed, with the deepest water forecast nearby."
              >
                {loading ? (
                  <Skeleton className="h-40 w-full" />
                ) : v && v.missed.length > 0 ? (
                  <ul className="flex flex-col gap-2">
                    {v.missed.slice(0, 12).map((pin) => (
                      <li
                        key={pin.pinId}
                        className="rounded-control border-line bg-well border p-2"
                      >
                        <p className="type-small text-text">{pin.name}</p>
                        <p className="num type-micro text-text-2">
                          Logged {formatIst(pin.pinTs)} IST · deepest nearby{" "}
                          {pin.deepestNearbyCm.toFixed(1)} cm
                        </p>
                        <p className="type-micro text-text-3">{pin.reason}</p>
                        {pin.sourceUrl ? (
                          <a
                            href={pin.sourceUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="type-micro text-tide underline"
                          >
                            Source
                          </a>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState
                    icon={MapPinOff}
                    title="No missed pins"
                    description="Every sourced pin inside the window was flagged before it was logged."
                  />
                )}
              </Panel>
            </PanelErrorBoundary>

            <PanelErrorBoundary title="Where we were early">
              <Panel
                title="Where we were early"
                description="Pins flagged before the city logged them."
              >
                {loading ? (
                  <Skeleton className="h-40 w-full" />
                ) : v && v.matched.length > 0 ? (
                  <ul className="flex flex-col gap-2">
                    {v.matched.slice(0, 12).map((pin) => (
                      <li
                        key={pin.pinId}
                        className="rounded-control border-line bg-well border p-2"
                      >
                        <p className="type-small text-text">{pin.name}</p>
                        <p className="num type-micro text-text-2">
                          Flagged {formatIst(pin.forecastTs)} · logged {formatIst(pin.pinTs)} ·{" "}
                          {pin.leadMin >= 0
                            ? `${pin.leadMin.toFixed(0)} min early`
                            : `${Math.abs(pin.leadMin).toFixed(0)} min late`}
                        </p>
                        {pin.sourceUrl ? (
                          <a
                            href={pin.sourceUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="type-micro text-tide underline"
                          >
                            Source
                          </a>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState
                    title="Nothing flagged yet"
                    description="Matched pins appear once a baked run covers their timestamps."
                  />
                )}
              </Panel>
            </PanelErrorBoundary>
          </div>

          <PanelErrorBoundary title="What we cannot score">
            <Panel title="What we cannot score" description="Shown rather than omitted.">
              {loading ? (
                <Skeleton className="h-24 w-full" />
              ) : (
                <ul className="flex flex-col gap-2">
                  {Object.entries(v?.unavailable ?? {}).map(([key, reason]) => (
                    <li key={key}>
                      <Details summary={unavailableLabel(key)}>
                        <p className="type-small text-text-2 max-w-[72ch]">{reason}</p>
                      </Details>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </PanelErrorBoundary>

          <Panel title="Method and limitations">
            <div className="flex flex-col gap-2">
              {v && v.notes.length > 0 ? (
                <Details summary="How this was scored">
                  <ul className="flex max-w-[72ch] list-disc flex-col gap-1 pl-5">
                    {v.notes.map((note) => (
                      <li key={note} className="type-small text-text-2">
                        {note}
                      </li>
                    ))}
                  </ul>
                </Details>
              ) : null}
              <Details summary="Limitations" ref={limitsRef}>
                <LimitationsList id="limitations" />
              </Details>
            </div>
          </Panel>
        </div>
      </div>
    </AppShell>
  );
}

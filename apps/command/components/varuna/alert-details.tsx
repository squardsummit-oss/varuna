"use client";

import { ArrowUpRight, Check, ChevronDown } from "lucide-react";
import type { Route } from "next";
import Link from "next/link";
import { useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { CapViewer } from "@/components/varuna/cap-viewer";
import { DeliveryLog, type DeliveryLogRow } from "@/components/varuna/delivery-log";
import {
  alertPlace,
  capFilename,
  loadCap,
  nextEscalation,
  type EscalationStep,
  type RunAlert,
} from "@/lib/api/alerts";
import { DEFAULT_CITY } from "@/lib/city";
import { formatIst } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface AlertDetailsProps {
  alert: RunAlert;
  cycleTs: string | null;
  /** The run the queue came from; every link and the CAP request carry it. */
  runId?: string;
  /** The screen's `?city=`; left off links when it is the default. */
  city?: string;
  /** `config/escalation.yaml` as the API serves it; null while loading or when it failed. */
  steps: readonly EscalationStep[] | null;
  stepsError?: string | null;
  /** The WhatsApp card and SMS the API rendered for this alert. */
  messages: { whatsapp: string | null; sms: string | null };
  /** This alert's delivery rows, already filtered. */
  delivery: { rows: DeliveryLogRow[] | null; loading: boolean; error: string | null };
  /** Whether this tab holds the desk passphrase; the acts are refused without it. */
  hasPassphrase: boolean;
  onAcknowledge: (id: string) => void;
  onEscalate: (id: string) => void;
}

/** A link to another screen that keeps this alert's run and city. */
function screenHref(path: string, params: Record<string, string | null | undefined>): Route {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value) search.set(key, value);
  const query = search.toString();
  return (query ? `${path}?${query}` : path) as Route;
}

const AUTHORITY_ROUTE = "/authority" as Route;

const LINK =
  "type-small text-tide inline-flex items-center gap-1 underline underline-offset-2 hover:no-underline focus-visible:ring-tide rounded-control focus-visible:ring-2 focus-visible:outline-none";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h4 className="type-micro text-text-3 font-medium">{title}</h4>
      {children}
    </section>
  );
}

/**
 * What "See more" opens under an alert (SPEC.md 7.5): what to do and where to do it, who has
 * been told and who is next, when each thing happened, the messages as they were rendered, the
 * delivery rows and the CAP 1.2 document. The CAP document is read only when it is opened.
 */
export function AlertDetails({
  alert,
  cycleTs,
  runId,
  city,
  steps,
  stepsError = null,
  messages,
  delivery,
  hasPassphrase,
  onAcknowledge,
  onEscalate,
}: AlertDetailsProps) {
  const place = alertPlace(alert);
  const cityParam = city && city !== DEFAULT_CITY ? city : null;
  // "lon,lat", the order the rest of the API writes a point in. Carried to /route as the
  // destination and to /console as the place to fly to; a screen that does not read it yet
  // opens as it always did.
  const point = alert.lon !== null && alert.lat !== null ? `${alert.lon},${alert.lat}` : null;
  const reached = new Set([...alert.notify, ...(alert.escalatedTo ? [alert.escalatedTo] : [])]);
  const told = (steps ?? []).filter((step) => reached.has(step.id));
  const next = steps ? nextEscalation(alert, steps) : null;
  const acknowledged = Boolean(alert.acknowledgedBy);
  const escalated = alert.state === "escalated";

  const capId = useId();
  const [capOpen, setCapOpen] = useState(false);
  const [xml, setXml] = useState<string | null>(null);
  const [capError, setCapError] = useState<string | null>(null);
  useEffect(() => {
    if (!capOpen) return;
    const controller = new AbortController();
    loadCap(alert.id, runId, controller.signal, cityParam ?? undefined)
      .then(setXml)
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setXml(null);
        setCapError(
          error instanceof Error
            ? `The CAP document could not be read: ${error.message}`
            : "The CAP document could not be read.",
        );
      });
    return () => controller.abort();
  }, [capOpen, alert.id, runId, cityParam]);

  const timeline: { ts: string; text: string }[] = [];
  if (alert.firstSeenTs) {
    timeline.push({ ts: alert.firstSeenTs, text: `First over ${alert.thresholdCm} cm` });
  }
  if (alert.raisedTs) timeline.push({ ts: alert.raisedTs, text: "Raised" });
  if (alert.sentTs) timeline.push({ ts: alert.sentTs, text: "CAP document and messages sent" });
  for (const act of alert.history) {
    const what =
      act.state === "acknowledged"
        ? "Acknowledged"
        : act.state === "escalated"
          ? "Escalated"
          : "Returned to raised";
    timeline.push({
      ts: act.ts,
      text: `${what} by ${act.user}${act.note ? `: ${act.note}` : ""}`,
    });
  }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="flex min-w-0 flex-col gap-4">
        <Section title="What to do">
          {alert.instruction ? (
            <p className="type-small text-text max-w-[72ch]">{alert.instruction}</p>
          ) : (
            <p className="type-small text-text-2">This run carries no instruction for {place}.</p>
          )}
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <Link
              className={LINK}
              href={screenHref("/route", {
                run: runId,
                city: cityParam,
                to: point,
                place,
              })}
            >
              Plan a route around it
            </Link>
            <Link
              className={LINK}
              href={screenHref("/console", { run: runId, city: cityParam, focus: point })}
            >
              Show on the map
            </Link>
          </div>
          {alert.dispatchNote || alert.pumps.length > 0 ? (
            <p className="type-small text-text-2">
              {alert.dispatchNote ?? `Pumps ${alert.pumps.join(", ")} dispatched here.`}{" "}
              <Link className={LINK} href={screenHref("/pumps", { city: cityParam })}>
                Pump dispatch
              </Link>
            </p>
          ) : (
            <p className="type-small text-text-2">
              No pump has been dispatched here.{" "}
              <Link className={LINK} href={screenHref("/pumps", { city: cityParam })}>
                Open pump dispatch
              </Link>
            </p>
          )}
        </Section>

        <Section title="Who has been told">
          {steps === null ? (
            <p className="type-small text-text-2">
              {stepsError ?? "Reading the escalation matrix."}
            </p>
          ) : (
            <>
              <p className="type-small text-text">
                {told.length > 0
                  ? `Told when raised: ${told.map((step) => step.recipient).join(", ")}.`
                  : "No step of the escalation matrix has been told yet."}
              </p>
              <p className="type-small text-text-2">
                {next
                  ? `Next step: escalate to ${next.recipient}, by ${next.channel}.`
                  : "Every step of the escalation matrix has been told."}
              </p>
            </>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {acknowledged ? (
              <span className="type-small text-text-2 inline-flex h-7 items-center gap-1.5">
                <Check size={16} strokeWidth={1.75} aria-hidden="true" className="text-tide" />
                Acknowledged
              </span>
            ) : (
              <Button variant="outline" size="sm" onClick={() => onAcknowledge(alert.id)}>
                Acknowledge
              </Button>
            )}
            {escalated && !next ? (
              <span className="type-small text-text-2 inline-flex h-7 items-center gap-1.5">
                <ArrowUpRight size={16} strokeWidth={1.75} aria-hidden="true" />
                Escalated
              </span>
            ) : (
              <Button variant="ghost" size="sm" onClick={() => onEscalate(alert.id)}>
                Escalate
              </Button>
            )}
          </div>
          {hasPassphrase ? null : (
            <p className="type-micro text-text-3">
              Acknowledge and escalate need the desk passphrase.{" "}
              <Link className={LINK} href={AUTHORITY_ROUTE}>
                Enter it on the authority desk
              </Link>
            </p>
          )}
        </Section>

        <Section title="Timeline">
          {timeline.length === 0 ? (
            <p className="type-small text-text-2">This run records no times for the alert.</p>
          ) : (
            <ol className="flex flex-col gap-1">
              {timeline.map((entry, i) => (
                <li key={`${entry.ts}-${i}`} className="type-small text-text-2 flex gap-3">
                  <span className="num text-text w-12 shrink-0">{formatIst(entry.ts)}</span>
                  <span className="min-w-0">{entry.text}</span>
                </li>
              ))}
            </ol>
          )}
        </Section>

        {alert.membersAbove !== null && alert.membersTotal !== null ? (
          <Section title="Ensemble agreement">
            <p className="type-small text-text">
              <span className="num">
                {alert.membersAbove} of {alert.membersTotal}
              </span>{" "}
              members above {alert.thresholdCm} cm for two steps in a row. Shown, not used to raise.
            </p>
          </Section>
        ) : null}
      </div>

      <div className="flex min-w-0 flex-col gap-4">
        <Section title="Message preview">
          <div className="rounded-control border-tide bg-ink border-l-2 p-3">
            <p className="type-micro text-text-3 mb-1">WhatsApp</p>
            <p className="type-small text-text whitespace-pre-line">
              {messages.whatsapp ?? "This API does not serve the rendered WhatsApp card."}
            </p>
          </div>
          <div className="rounded-control border-line bg-ink border p-3">
            <p className="type-micro text-text-3 mb-1">SMS</p>
            <p className="type-small text-text">
              {messages.sms ?? "This API does not serve the rendered SMS."}
            </p>
          </div>
        </Section>

        <Section title="Delivery">
          <DeliveryLog rows={delivery.rows} loading={delivery.loading} error={delivery.error} />
        </Section>

        <section className="flex flex-col gap-2">
          <button
            type="button"
            aria-expanded={capOpen}
            aria-controls={capId}
            onClick={() => {
              // A failed read is retried by closing and opening again, so it starts clean.
              setCapError(null);
              setCapOpen((open) => !open);
            }}
            className="rounded-control type-small text-text hover:text-tide focus-visible:ring-tide inline-flex items-center gap-1 self-start font-medium focus-visible:ring-2 focus-visible:outline-none"
          >
            CAP 1.2 document
            <ChevronDown
              size={16}
              strokeWidth={1.75}
              aria-hidden="true"
              className={cn(capOpen && "rotate-180")}
            />
          </button>
          <div id={capId} hidden={!capOpen}>
            {capOpen ? (
              capError ? (
                <p className="type-small text-text-2">{capError}</p>
              ) : (
                <CapViewer
                  xml={xml}
                  filename={capFilename(cycleTs, alert)}
                  compact
                  loading={xml === null}
                />
              )
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}

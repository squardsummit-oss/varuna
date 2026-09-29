"use client";

/**
 * Acknowledge or escalate an alert (UI_SPEC 6 left column, PRD 3.2, task D-15).
 *
 * **It reads `/v1/ops/alerts`, not `/v1/alerts`.** The queue is a product: the cycle computed it
 * and no officer may edit it. What an officer changes is the alert's *state*, which lives in the
 * ops log, and only the desk's endpoint folds the two together - so an acknowledgement made here
 * survives a reload, which the alert centre's own read would not show.
 *
 * **An acknowledgement changes no water and the panel says so.** It is in the left column because
 * an engine consumes it: the next read of the desk's queue shows the alert as seen, by whom and
 * when. That is a different claim from "the forecast moved", and the result box carries the API's
 * own sentence rather than a cheerful one of ours.
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { AlertLevelChip } from "@/components/varuna/alert-level-chip";
import { EmptyState } from "@/components/varuna/empty-state";
import { Panel } from "@/components/varuna/panel";
import { Skeleton } from "@/components/varuna/skeleton";
import { formatIst } from "@/lib/format";
import {
  loadDeskAlerts,
  opsRefusal,
  postAlertAction,
  type DeskAlert,
  type OpsRefusal,
} from "@/lib/api/ops";

import { ActionResult, type ActionOutcome } from "./action-result";

/**
 * The steps of the escalation matrix an alert can be sent up (blueprint 6.10). The keys are the
 * tier ids of `config/escalation.yaml`, which `POST /v1/alerts/{id}/escalate` takes as
 * `escalate_to` (varuna_schemas `EscalationTarget`) and `/alerts` reads to name the next step.
 * The desk used to send "police", which that type does not contain, so choosing police and
 * traffic here was refused by the API as an invalid body.
 */
const TARGETS = [
  { key: "control_room", label: "Control room" },
  { key: "police_traffic", label: "Police and traffic" },
  { key: "transit", label: "Transit" },
  { key: "public", label: "Public" },
] as const;

export interface AlertPanelProps {
  runId?: string;
  city?: string;
  officer: string;
  onWrote?: () => void;
  onGateRefused?: (refusal: OpsRefusal) => void;
  className?: string;
}

interface Result {
  outcome: ActionOutcome;
  message: string;
  notes?: string[];
  at?: string | null;
}

export function AlertPanel({
  runId,
  city,
  officer,
  onWrote,
  onGateRefused,
  className,
}: AlertPanelProps) {
  const [alerts, setAlerts] = useState<DeskAlert[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<string>("control_room");
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  const refresh = useCallback(
    (signal?: AbortSignal) => {
      void (async () => {
        setAlerts(null);
        setError(null);
        try {
          const set = await loadDeskAlerts({ runId, city, signal });
          if (!signal?.aborted) setAlerts(set.alerts);
        } catch (failure: unknown) {
          if (signal?.aborted) return;
          setAlerts([]);
          setError(opsRefusal(failure).message);
        }
      })();
    },
    [city, runId],
  );

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const act = useCallback(
    async (alert: DeskAlert, action: "ack" | "escalate") => {
      setBusy(alert.id);
      setResult(null);
      try {
        const response = await postAlertAction({
          alertId: alert.id,
          action,
          user: officer,
          city,
          runId,
          escalateTo: target,
        });
        setAlerts((current) =>
          (current ?? []).map((row) => (row.id === alert.id ? response.alert : row)),
        );
        setResult({
          outcome: "changed",
          message:
            action === "ack"
              ? `Acknowledged by ${officer}. The desk's queue shows it as seen after a reload; the forecast is unchanged.`
              : `Escalated to ${TARGETS.find((t) => t.key === target)?.label ?? target}. The forecast is unchanged.`,
          notes: response.notes,
          at: response.entry.ts ? formatIst(response.entry.ts) : null,
        });
        onWrote?.();
      } catch (failure) {
        const refusal = opsRefusal(failure);
        setResult({ outcome: "refused", message: refusal.message });
        onGateRefused?.(refusal);
      } finally {
        setBusy(null);
      }
    },
    [city, officer, onGateRefused, onWrote, runId, target],
  );

  return (
    <Panel
      className={className}
      title="Alerts"
      description="Acknowledge records that an alert was seen; no forecast changes."
    >
      <div className="space-y-4">
        <fieldset className="space-y-1.5">
          <legend className="type-small text-text">Escalate to</legend>
          <div className="flex flex-wrap gap-1.5">
            {TARGETS.map((option) => (
              <Button
                key={option.key}
                type="button"
                size="sm"
                variant={target === option.key ? "secondary" : "ghost"}
                aria-pressed={target === option.key}
                onClick={() => setTarget(option.key)}
              >
                {option.label}
              </Button>
            ))}
          </div>
        </fieldset>

        {alerts === null ? (
          <Skeleton lines={4} />
        ) : error ? (
          <p className="type-small text-text-2">{error}</p>
        ) : alerts.length === 0 ? (
          <EmptyState
            size="sm"
            title="This cycle raised no alert"
            description="Pick a cycle from the storm; calm cycles raise nothing."
          />
        ) : (
          <ul className="max-h-96 space-y-2 overflow-y-auto" aria-label="Alerts">
            {alerts.map((alert) => (
              <li
                key={alert.id}
                className="rounded-control border-line bg-well/40 space-y-2 border p-2.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <AlertLevelChip level={alert.level} />
                  <span className="num type-micro text-text-3">
                    {Math.round(alert.peakCm)} cm at peak · from {formatIst(alert.windowFrom)}
                  </span>
                </div>
                <p className="type-small text-text">{alert.headline}</p>
                {alert.state ? (
                  <p className="type-micro text-tide">
                    {alert.state === "acknowledged"
                      ? `Acknowledged by ${alert.acknowledgedBy ?? "unknown"}${
                          alert.acknowledgedTs ? ` at ${formatIst(alert.acknowledgedTs)}` : ""
                        }`
                      : `Escalated to ${alert.escalatedTo ?? "the next step"}`}
                  </p>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    className="min-h-11"
                    disabled={busy !== null}
                    onClick={() => act(alert, "ack")}
                  >
                    {busy === alert.id ? "Working" : "Acknowledge"}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="min-h-11"
                    disabled={busy !== null}
                    onClick={() => act(alert, "escalate")}
                  >
                    Escalate
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}

        {result ? <ActionResult {...result} /> : null}
      </div>
    </Panel>
  );
}

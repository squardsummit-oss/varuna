"use client";

/**
 * Pump status, optimise and dispatch (UI_SPEC 6 left column, PRD 3.2, task D-15).
 *
 * **Three acts, and only the first two reach an engine in different ways.** Marking a pump
 * unavailable is read by the next optimise, which is why it sits in the left column: the fleet
 * the solver may assign is smaller from that moment. Optimise itself writes nothing - it
 * re-solves and answers - but it is gated with the writes because it re-prices every candidate
 * through the emulator. Dispatch appends the order and calls no lorry, and the API says so in
 * its own notes, which are printed rather than paraphrased.
 *
 * **The plan is never written back into the run.** `pump_plan.json` stays the plan the cycle
 * computed; what this panel shows after Optimise is a plan computed now, honouring what the desk
 * has withheld. The panel says which, because a judge reading the board and a judge reading this
 * screen must be able to tell why the two differ.
 *
 * **The inventory is synthetic and the benefit is a model.** Both labels come from the API
 * (`synthetic_inventory`, `benefit_label`) and both are printed beside the numbers, because a
 * minutes-saved figure is the most quotable number on this screen and the least real.
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Panel } from "@/components/varuna/panel";
import { Skeleton } from "@/components/varuna/skeleton";
import { formatIst } from "@/lib/format";
import { loadPumpPlan, type PumpUnit } from "@/lib/api/pumps";
import {
  dispatchPumps,
  optimisePumps,
  opsRefusal,
  postPumpStatus,
  type OpsRefusal,
  type PumpPlan as DeskPumpPlan,
  type PumpState,
} from "@/lib/api/ops";
import { cn } from "@/lib/utils";

import { ActionResult, type ActionOutcome } from "./action-result";

const STATES: { key: PumpState; label: string }[] = [
  { key: "available", label: "Available" },
  { key: "unavailable", label: "Unavailable" },
  { key: "moved", label: "Moved" },
];

export interface PumpPanelProps {
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

export function PumpPanel({
  runId,
  city,
  officer,
  onWrote,
  onGateRefused,
  className,
}: PumpPanelProps) {
  const [inventory, setInventory] = useState<PumpUnit[] | null>(null);
  const [overrides, setOverrides] = useState<Record<string, PumpState>>({});
  const [note, setNote] = useState("");
  const [plan, setPlan] = useState<DeskPumpPlan | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    loadPumpPlan(runId, controller.signal)
      .then((loaded) => setInventory(loaded?.pumps ?? []))
      .catch(() => setInventory([]));
    return () => controller.abort();
  }, [runId]);

  const refused = useCallback(
    (error: unknown) => {
      const refusal = opsRefusal(error);
      setResult({ outcome: "refused", message: refusal.message });
      onGateRefused?.(refusal);
    },
    [onGateRefused],
  );

  const setStatus = useCallback(
    async (pumpId: string, status: PumpState) => {
      setBusy(pumpId);
      setResult(null);
      try {
        const response = await postPumpStatus({
          pumpId,
          status,
          user: officer,
          note: note.trim() || null,
          city,
        });
        setOverrides(
          Object.fromEntries(Object.entries(response.pumps).map(([id, pump]) => [id, pump.status])),
        );
        setResult({
          outcome: "changed",
          message:
            status === "unavailable"
              ? `${pumpId} is withheld. The next optimise assigns the rest of the fleet without it.`
              : `${pumpId} is ${status}. The next optimise may assign it.`,
          notes: response.notes,
          at: response.entry.ts ? formatIst(response.entry.ts) : null,
        });
        // The plan on screen was solved against the old fleet, so it is no longer an answer to
        // anything. Clearing it is more honest than leaving a stale board beside a changed fleet.
        setPlan(null);
        setNote("");
        onWrote?.();
      } catch (error) {
        refused(error);
      } finally {
        setBusy(null);
      }
    },
    [city, note, officer, onWrote, refused],
  );

  const optimise = useCallback(async () => {
    setBusy("optimise");
    setResult(null);
    try {
      const solved = await optimisePumps({ runId, city });
      setPlan(solved);
      setResult({
        outcome: "recorded",
        message:
          solved.assignments.length === 0
            ? "The optimiser assigned nothing on this cycle: no candidate crosses the threshold, or every pump is withheld."
            : `${solved.assignments.length} pump${solved.assignments.length === 1 ? "" : "s"} assigned in ${solved.solveMs} ms. Nothing has been dispatched and the run's own plan is untouched.`,
        notes: solved.notes,
      });
    } catch (error) {
      refused(error);
    } finally {
      setBusy(null);
    }
  }, [city, refused, runId]);

  const dispatch = useCallback(async () => {
    setBusy("dispatch");
    setResult(null);
    try {
      const order = await dispatchPumps({ runId, city, user: officer, note: note.trim() || null });
      setResult({
        outcome: "changed",
        message: `${order.orders.length} order${order.orders.length === 1 ? "" : "s"} recorded by ${order.dispatchedBy}.`,
        notes: [...order.orders.map((o) => o.orderText ?? ""), ...order.notes].filter(Boolean),
        at: order.dispatchedTs ? formatIst(order.dispatchedTs) : null,
      });
      setNote("");
      onWrote?.();
    } catch (error) {
      refused(error);
    } finally {
      setBusy(null);
    }
  }, [city, note, officer, onWrote, refused, runId]);

  return (
    <Panel
      className={className}
      title="Pumps"
      description="What the desk withholds, the optimiser cannot assign."
    >
      <div className="space-y-4">
        <p className="type-micro text-text-3">Synthetic pump inventory</p>

        {inventory === null ? (
          <Skeleton lines={4} />
        ) : inventory.length === 0 ? (
          <p className="type-small text-text-2">
            This cycle carries no pump plan, so there is no fleet to set a status on. Pick a cycle
            from the storm, or bake one.
          </p>
        ) : (
          <ul className="max-h-72 space-y-1.5 overflow-y-auto" aria-label="Pumps">
            {inventory.map((pump) => {
              const status = overrides[pump.id] ?? (pump.status as PumpState) ?? "available";
              return (
                <li
                  key={pump.id}
                  className="rounded-control border-line bg-well/40 space-y-1.5 border p-2.5"
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="type-small text-text">{pump.id}</span>
                    <span className="num type-micro text-text-3">
                      {Math.round(pump.capacityM3PerHour)} m³/h · {pump.depot}
                    </span>
                  </div>
                  <div
                    className="flex flex-wrap gap-1.5"
                    role="group"
                    aria-label={`Status of ${pump.id}`}
                  >
                    {STATES.map((state) => (
                      <Button
                        key={state.key}
                        type="button"
                        size="sm"
                        variant={status === state.key ? "secondary" : "ghost"}
                        aria-pressed={status === state.key}
                        disabled={busy !== null}
                        className={cn("min-h-11", status === state.key && "text-text")}
                        onClick={() => setStatus(pump.id, state.key)}
                      >
                        {state.label}
                      </Button>
                    ))}
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        <div className="space-y-1.5">
          <Label htmlFor="pump-note" className="type-small text-text">
            Note for the log
          </Label>
          <Input
            id="pump-note"
            className="h-11"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={300}
            placeholder="Axle broken at the Parel depot"
          />
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            className="h-11 px-4"
            variant="secondary"
            disabled={busy !== null}
            onClick={optimise}
          >
            {busy === "optimise" ? "Optimising" : "Optimise"}
          </Button>
          <Button
            type="button"
            className="h-11 px-4"
            disabled={busy !== null || !plan || plan.assignments.length === 0}
            onClick={dispatch}
          >
            {busy === "dispatch" ? "Dispatching" : "Dispatch the plan"}
          </Button>
        </div>

        {plan ? (
          <div className="rounded-control border-line space-y-2 border p-2.5">
            <p className="type-micro text-text-3">
              Solved now, not read from the run · {plan.benefitLabel || "benefit model unstated"}
            </p>
            {plan.assignments.length === 0 ? (
              <p className="type-small text-text-2">No assignment.</p>
            ) : (
              <ul className="space-y-1">
                {plan.assignments.map((assignment) => (
                  <li key={assignment.pumpId} className="type-small text-text-2">
                    <span className="text-text">{assignment.pumpId}</span> to{" "}
                    {assignment.hotspotName}
                    <span className="num text-text-3">
                      {" "}
                      · ETA {assignment.etaMin} min · saves about {assignment.minutesSaved} min
                      above {Math.round(plan.thresholdCm)} cm
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {plan.withheld.length > 0 ? (
              <p className="type-micro text-text-3">
                Withheld by the desk:{" "}
                {plan.withheld.map((w) => `${w.pumpId} (${w.status})`).join(", ")}.
              </p>
            ) : null}
          </div>
        ) : null}

        {result ? <ActionResult {...result} /> : null}
      </div>
    </Panel>
  );
}

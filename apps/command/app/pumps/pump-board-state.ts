/**
 * What the pump board shows for one plan (SPEC.md 7.6), as a pure function of four things: the
 * plan the API returned, whether the operator has pressed Optimise, where they have dragged pumps
 * by hand, and - once the API has answered - the price of that hand-made board. Kept out of the
 * screen so the rule motion M17 depends on is testable: **a drag moves a card and never a
 * number; a number moves only when the API has priced what the drag made.**
 */

import type { DispatchOrderPlan } from "@/components/varuna/dispatch-order";
import type { PumpColumn } from "@/components/varuna/pump-board";
import type { Pump } from "@/components/varuna/pump-card";
import type { PricedPlan, PumpPlan } from "@/lib/api/pumps";

export interface PumpBoardInputs {
  /** True once Optimise has placed the optimiser's plan on the board. */
  applied: boolean;
  /** Pumps the operator has dragged, over the board as it stood: pump id to column id, or null
   *  for the pool. */
  moved: Readonly<Record<string, string | null>>;
  /**
   * The API's price for the board exactly as it now stands (`POST /v1/pumps/price`), or null
   * while there is none. The screen hands it in only when it priced these same placements, so a
   * stale answer can never be drawn over a board it was not asked about.
   */
  priced?: PricedPlan | null;
}

export interface PumpBoardState {
  available: Pump[];
  columns: PumpColumn[];
  /** The plan in words; null until something has been decided. */
  order: DispatchOrderPlan | null;
}

/** Where every pump sits on the board, as `pumpId -> columnId` (the pool is left out). */
export function boardPlacements(
  plan: PumpPlan | null,
  inputs: Pick<PumpBoardInputs, "applied" | "moved">,
): { pumpId: string; targetId: string }[] {
  const planned = new Map((plan?.assignments ?? []).map((a) => [a.pumpId, a.targetId]));
  const columnIds = new Set(planned.values());
  const out: { pumpId: string; targetId: string }[] = [];
  for (const pump of plan?.pumps ?? []) {
    const target =
      pump.id in inputs.moved
        ? inputs.moved[pump.id]
        : inputs.applied
          ? (planned.get(pump.id) ?? null)
          : null;
    if (target && columnIds.has(target)) out.push({ pumpId: pump.id, targetId: target });
  }
  return out.sort((a, b) => a.pumpId.localeCompare(b.pumpId));
}

/** A stable key for a set of placements, so an answer can be matched to the board it priced. */
export function placementsKey(placements: { pumpId: string; targetId: string }[]): string {
  return placements.map((p) => `${p.pumpId}>${p.targetId}`).join("|");
}

export function buildPumpBoard(plan: PumpPlan | null, inputs: PumpBoardInputs): PumpBoardState {
  const { applied, moved, priced = null } = inputs;
  // One column per place the plan sends a pump, worst first. The columns are the targets this
  // cycle floods rather than a fixed three, and they exist before Optimise so the operator can
  // see where the water is and drag a pump there themselves.
  const base = (plan?.assignments ?? []).slice().sort((a, b) => b.minutesSaved - a.minutesSaved);

  const assignment = new Map(base.map((a) => [a.pumpId, a]));
  const pricedPump = new Map((priced?.placements ?? []).map((p) => [p.pumpId, p]));
  const pricedTarget = new Map((priced?.targets ?? []).map((t) => [t.targetId, t]));

  // Where each pump sits now: the optimiser's target once the plan is applied, the pool before,
  // and the operator's move over either.
  const placed = new Map<string, string | null>();
  for (const pump of plan?.pumps ?? []) {
    placed.set(pump.id, applied ? (assignment.get(pump.id)?.targetId ?? null) : null);
  }
  for (const [pumpId, target] of Object.entries(moved)) placed.set(pumpId, target);

  const cardFor = (pumpId: string): Pump | null => {
    const unit = plan?.pumps.find((p) => p.id === pumpId);
    const planned = assignment.get(pumpId);
    if (!unit && !planned) return null;
    const target = placed.get(pumpId);
    const price = pricedPump.get(pumpId);
    return {
      id: pumpId,
      capacityM3PerHour: unit?.capacityM3PerHour ?? planned?.capacityM3PerHour ?? 0,
      depot: unit?.depot ?? planned?.depot ?? "",
      // The plan is a proposal until Dispatch is pressed, so a placed lorry is still moving.
      status: "moving",
      // The ETA the API computed for where the pump now is: the optimiser's for its own target,
      // the price's for a pump moved by hand, and none while the price has not arrived.
      etaMinutes:
        price && price.targetId === target
          ? price.etaMin
          : planned && target === planned.targetId
            ? planned.etaMin
            : null,
      assignedTo: target ?? undefined,
    };
  };

  const columns: PumpColumn[] = base.map((a) => {
    const price = pricedTarget.get(a.targetId);
    return {
      id: a.targetId,
      title: a.targetName,
      pumps: [...placed.entries()]
        .filter(([, target]) => target === a.targetId)
        .map(([pumpId]) => cardFor(pumpId))
        .filter((p): p is Pump => p !== null),
      // The optimiser's figure for its own plan until the API has priced the board as dragged;
      // then that price, and for a column the operator emptied, no pump and no saving.
      minutesAbove45: priced
        ? price
          ? { before: price.minutesBefore, after: price.minutesAfter }
          : { before: a.minutesBefore, after: a.minutesBefore }
        : { before: a.minutesBefore, after: a.minutesAfter },
    };
  });

  // A pump whose place is not one of this cycle's columns is in the pool, so no card can vanish.
  const columnIds = new Set(columns.map((c) => c.id));
  const available: Pump[] = (plan?.pumps ?? [])
    .filter((p) => !columnIds.has(placed.get(p.id) ?? ""))
    .map((p) => ({
      id: p.id,
      capacityM3PerHour: p.capacityM3PerHour,
      depot: p.depot,
      status: "available" as const,
      etaMinutes: null,
    }));

  const names = new Map(base.map((a) => [a.targetId, a.targetName]));
  let order: DispatchOrderPlan | null = null;
  if (priced && priced.placements.length > 0) {
    order = {
      runId: plan?.runId,
      moves: priced.placements
        .filter((p) => p.etaMin !== null)
        .map((p) => ({
          id: p.pumpId,
          pumpId: p.pumpId,
          from: p.depot,
          to: p.targetName ?? names.get(p.targetId) ?? p.targetId,
          etaMinutes: p.etaMin ?? 0,
          minutesAvoided: p.minutesSaved,
        })),
    };
  } else if (applied && plan?.assignments.length) {
    // The optimiser's order: the plan as placed, or - moved by hand and not yet priced - still
    // the one on record while the screen says the board is being priced.
    order = {
      runId: plan.runId,
      moves: plan.assignments.map((a) => ({
        id: a.pumpId,
        pumpId: a.pumpId,
        from: a.depot,
        to: a.targetName,
        etaMinutes: a.etaMin,
        minutesAvoided: a.minutesSaved,
      })),
    };
  }

  return { available, columns, order };
}

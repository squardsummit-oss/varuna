import { describe, expect, it } from "vitest";

import { buildPumpBoard } from "@/app/pumps/pump-board-state";
import type { PumpPlan } from "@/lib/api/pumps";

/** Shaped like the 08:40 baked plan: one pump per target, one pump left over. */
const PLAN: PumpPlan = {
  runId: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
  thresholdCm: 45,
  benefitLabel: "bathtub estimate, not a physics run",
  inventory: "synthetic",
  pumps: [
    { id: "P-05", capacityM3PerHour: 900, depot: "F/South ward office", status: "available" },
    { id: "P-08", capacityM3PerHour: 600, depot: "G/North ward office", status: "available" },
    { id: "P-11", capacityM3PerHour: 450, depot: "H/West ward office", status: "available" },
  ],
  assignments: [
    {
      pumpId: "P-08",
      capacityM3PerHour: 600,
      depot: "G/North ward office",
      targetId: "street:90 Feet Road",
      targetName: "90 Feet Road",
      lon: null,
      lat: null,
      etaMin: 10,
      minutesBefore: 95,
      minutesAfter: 20,
      minutesSaved: 75,
    },
    {
      pumpId: "P-05",
      capacityM3PerHour: 900,
      depot: "F/South ward office",
      targetId: "street:Jijamata Road",
      targetName: "Jijamata Road",
      lon: null,
      lat: null,
      etaMin: 50,
      minutesBefore: 100,
      minutesAfter: 0,
      minutesSaved: 100,
    },
  ],
  unassigned: [],
  totalMinutesSaved: 175,
};

const ids = (pumps: { id: string }[]) => pumps.map((p) => p.id).sort();

describe("buildPumpBoard", () => {
  it("opens with every pump in the pool, a column per target and no order", () => {
    const board = buildPumpBoard(PLAN, { applied: false, moved: {} });

    expect(ids(board.available)).toEqual(["P-05", "P-08", "P-11"]);
    expect(board.columns.map((c) => c.id)).toEqual(["street:Jijamata Road", "street:90 Feet Road"]);
    expect(board.columns.every((c) => c.pumps.length === 0)).toBe(true);
    expect(board.order).toBeNull();
  });

  it("places the optimiser's plan once applied, with the API's ETA and minutes", () => {
    const board = buildPumpBoard(PLAN, { applied: true, moved: {} });

    expect(ids(board.available)).toEqual(["P-11"]);
    const jijamata = board.columns.find((c) => c.id === "street:Jijamata Road");
    expect(jijamata?.pumps.map((p) => [p.id, p.etaMinutes])).toEqual([["P-05", 50]]);
    expect(jijamata?.minutesAbove45).toEqual({ before: 100, after: 0 });
    expect(board.order?.moves.map((m) => [m.pumpId, m.etaMinutes, m.minutesAvoided])).toEqual([
      ["P-08", 10, 75],
      ["P-05", 50, 100],
    ]);
  });

  it.each([false, true])(
    "a drag moves cards and changes no benefit figure or order line (applied: %s)",
    (applied) => {
      const still = buildPumpBoard(PLAN, { applied, moved: {} });
      const dragged = buildPumpBoard(PLAN, {
        applied,
        moved: { "P-05": "street:90 Feet Road", "P-08": null, "P-11": "street:Jijamata Road" },
      });

      // The cards did move...
      expect(dragged.columns.map((c) => ids(c.pumps))).toEqual([["P-11"], ["P-05"]]);
      expect(ids(dragged.available)).toEqual(["P-08"]);
      // ...and not one displayed number did.
      expect(dragged.columns.map((c) => c.minutesAbove45)).toEqual(
        still.columns.map((c) => c.minutesAbove45),
      );
      expect(dragged.order).toEqual(still.order);
    },
  );

  it("gives a pump dragged off its planned target no ETA, since no route was computed", () => {
    const board = buildPumpBoard(PLAN, {
      applied: true,
      moved: { "P-05": "street:90 Feet Road" },
    });
    const feet = board.columns.find((c) => c.id === "street:90 Feet Road");
    expect(feet?.pumps.map((p) => [p.id, p.etaMinutes])).toEqual([
      ["P-05", null],
      ["P-08", 10],
    ]);
  });

  it("returns a pump placed on a column this cycle lacks to the pool", () => {
    const board = buildPumpBoard(PLAN, { applied: false, moved: { "P-11": "street:Gone" } });
    expect(ids(board.available)).toEqual(["P-05", "P-08", "P-11"]);
  });

  it("is empty without a plan", () => {
    expect(buildPumpBoard(null, { applied: true, moved: {} })).toEqual({
      available: [],
      columns: [],
      order: null,
    });
  });
});

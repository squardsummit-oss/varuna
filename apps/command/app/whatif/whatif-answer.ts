/**
 * One what-if answer, whichever engine gave it, in the shape the lab and the console drawer draw.
 *
 * The emulator (`POST /v1/whatif`) and the full-city Twin (`POST /v1/whatif/twin`) answer in
 * different vocabularies. Both are reduced here to the same rows, so the difference layer, the
 * hotspot table and the street table always read one list and cannot disagree with each other
 * (SPEC.md 7.7: "Diff layer and delta table agree").
 */

import type { DeltaRow } from "@/components/varuna/delta-table";
import type { WhatIfValues } from "@/components/varuna/whatif-controls";
import { formatMassBalance, formatMs } from "@/lib/format";
import {
  MAX_CLEANED_SEGMENTS,
  leverLines,
  twinTideOutcome,
  type TwinScenarioResult,
  type WhatIfResult,
  type WhatIfSegment,
} from "@/lib/api/whatif";
import type { WhatIfEngine } from "@/lib/hooks/use-twin-scenario";

/** The honesty chip for each engine (SPEC.md rule 6 and 6.8: labels are copy, not fine print). */
export const ENGINE_LABEL: Record<WhatIfEngine, string> = {
  emulator: "Reduced-order emulator calibrated to VARUNA-Twin",
  twin: "VARUNA-Twin, full city, prior blockage",
};

/** The emulator's answer to a question that moved the tide, while the Twin works on the whole of it. */
export const EMULATOR_NO_TIDE = "Emulator, tide not included";

/** Streets in the "Largest changes" table; the map draws every changed street. */
export const MAX_STREET_ROWS = 25;

/** The minutes column: 30 cm, where cars stop (SPEC.md 6.2). */
export const MINUTES_CM = "30";

/** The second minutes column: 45 cm, where buses stop (SPEC.md 6.2). */
export const MINUTES_45_CM = "45";

/**
 * The what-if levers the full-city Twin does not run, in the server's own words
 * (`_twin_levers_left_out` in `routers/whatif.py`). A Twin answer to a question that also asked
 * for them leads with these lines, so it is never read as the answer to the whole question
 * (rule 6). Built from what was asked rather than only from the job: the server hands back a job
 * already running for the same rain, tide and streets, whose notes were written for the question
 * that started it.
 */
export const TWIN_LEFT_OUT_LINES = {
  pump_plan:
    "The pump plan is not in this Twin run: the Twin has no pump sink on the street, so the pumps are priced by the emulator only, as a lower bound.",
  clean_top:
    "Clean top pipes by learned blockage is not in this Twin run, which is levelled on the city's prior blockage; clean those streets by name to run them here.",
} as const;

/** The levers a Twin answer to `asked` leaves out, in the order the server names them. */
export function twinLeftOut(
  asked: Pick<WhatIfValues, "pumpPlan" | "cleanTop14"> | null | undefined,
): (keyof typeof TWIN_LEFT_OUT_LINES)[] {
  if (!asked) return [];
  return [
    ...(asked.pumpPlan ? (["pump_plan"] as const) : []),
    ...(asked.cleanTop14 ? (["clean_top"] as const) : []),
  ];
}

const LEFT_OUT_TEXT = new Set<string>(Object.values(TWIN_LEFT_OUT_LINES));

/** The run lists no street below this depth, so a changed street it did not list reads "Below". */
export const BEFORE_FLOOR_CM = 5;

/** A change smaller than this is not counted as a change, by either engine. */
const CHANGED_CM = 0.5;

/** What "Nothing changed" means, said once under the title rather than repeating it. */
export const NOTHING_CHANGED_DETAIL = `No street's peak moved by ${CHANGED_CM} cm or more.`;

export interface WhatIfAnswer {
  engine: WhatIfEngine;
  runId: string;
  /** One sentence: how many streets moved which way, or "Nothing changed." */
  summary: string;
  nothingChanged: boolean;
  /** Streets that moved by 0.5 cm or more (or crossed 5 cm); `deltaCm.size` is always this. */
  nChanged: number;
  /** Of those, how many the run had below 5 cm, so their change is read from 0 cm. */
  nDryBefore: number;
  /** segment id -> change in peak depth, cm: the difference layer's input, and nothing else. */
  deltaCm: ReadonlyMap<string, number>;
  /** One row per hotspot of the run's register, largest change first. */
  hotspots: DeltaRow[];
  /** The largest street changes, named where OSM names the way. */
  streets: DeltaRow[];
  /** The tide's outcome on the Twin, or null when the answer did not move the sea. */
  tideOutcome: string | null;
  /** Lever lines and method notes, each one sentence, in the engine's own words. */
  lines: string[];
  /** Server time for the answer. */
  ms: number;
}

/**
 * "1,687 segments deeper, 12 shallower, each by 0.5 cm or more", worded as `POST /v1/whatif`
 * words its `summary`, so the Twin's answer and the emulator's read alike. A side with nothing on
 * it is left out rather than printed as "0 shallower"; `newlyWet` is added on the deeper side.
 */
export function changeSummary(worse: number, improved: number, newlyWet = 0): string {
  const count = (n: number) => `${n.toLocaleString("en-IN")} segment${n === 1 ? "" : "s"}`;
  const newly = newlyWet > 0 ? ` (${newlyWet.toLocaleString("en-IN")} of them newly wet)` : "";
  const tail = `each by ${CHANGED_CM} cm or more.`;
  if (worse === 0 && improved === 0) return "Nothing changed.";
  if (improved === 0) return `${count(worse)} deeper${newly}, ${tail}`;
  if (worse === 0) return `${count(improved)} shallower, ${tail}`;
  return `${count(worse)} deeper${newly}, ${improved.toLocaleString("en-IN")} shallower, ${tail}`;
}

/**
 * The name a changed street prints: the API's display name or the served layer's ("off Dr
 * Ambedkar Road", "Service road near Wadala Depot"), else OSM's. Only a segment neither knows
 * falls back to its id, said as a segment the way the ward desk does - never "Unnamed road".
 */
function streetName(id: string, name: string | null | undefined): string {
  return name ? name : `Segment ${id}`;
}

function emulatorStreet(row: WhatIfSegment): DeltaRow {
  return {
    id: row.segmentId,
    hotspot: streetName(row.segmentId, row.displayName ?? row.name),
    beforeCm: row.beforeCm,
    afterCm: row.afterCm,
    ...(row.dryBefore ? { beforeBelowCm: BEFORE_FLOOR_CM } : {}),
  };
}

/** The emulator's answer: rain, cleaning and pumps, levelled on the run's own Twin forecast. */
export function emulatorAnswer(result: WhatIfResult): WhatIfAnswer {
  return {
    engine: "emulator",
    runId: result.runId,
    summary: result.summary,
    nothingChanged: result.nothingChanged,
    nChanged: result.nChanged,
    nDryBefore: result.nDryBefore,
    deltaCm: new Map(result.segments.map((row) => [row.segmentId, row.deltaCm])),
    hotspots: result.hotspots.map((row) => ({
      id: row.hotspotId,
      hotspot: row.name,
      beforeCm: row.beforeCm,
      afterCm: row.afterCm,
      minutesImpassableBefore: row.minutesAboveBefore[MINUTES_CM] ?? 0,
      minutesImpassableAfter: row.minutesAboveAfter[MINUTES_CM] ?? 0,
      minutesAbove45Before: row.minutesAboveBefore[MINUTES_45_CM] ?? 0,
      minutesAbove45After: row.minutesAboveAfter[MINUTES_45_CM] ?? 0,
    })),
    streets: result.largestChanges.slice(0, MAX_STREET_ROWS).map(emulatorStreet),
    tideOutcome: null,
    lines: [
      ...leverLines(result),
      // The skill only when the emulator ran. A scenario with no lever never loads it, and the
      // endpoint's own note says why the forecast is the run's.
      ...(result.emulator
        ? [
            `Level from the Twin's own forecast for this run; the emulator supplies only the difference. Emulator skill on held-out storms: RMSE ${result.emulator.rmseCm.toFixed(1)} cm, CSI ${result.emulator.csi30cm.toFixed(2)} at 30 cm.`,
          ]
        : result.notes.slice(0, 1)),
    ],
    ms: result.ms,
  };
}

/**
 * The full-city Twin's answer. Only streets the job counts as changed are drawn and tabled, so
 * the map, the tables and `n_changed` are one list. A street the run had below 5 cm has no stored
 * depth; its change is read from 0 cm and the row says "Below 5 cm".
 *
 * `names` joins OSM street names from the served layer, which the job does not carry. `asked` is
 * the question on screen: a pump plan or "Clean top 14" in it is named first, because the Twin
 * does not run them. `scenarioNotes` are the job's own notes (`scenario.notes`), such as a lever
 * rounded to the cache grid.
 */
export function twinAnswer(
  result: TwinScenarioResult,
  options: {
    names?: ReadonlyMap<string, string>;
    cacheLabel?: string | null;
    asked?: Pick<WhatIfValues, "pumpPlan" | "cleanTop14"> | null;
    scenarioNotes?: readonly string[];
  } = {},
): WhatIfAnswer {
  const counted = result.segments.filter(
    (row) => row.newly_wet || (row.delta_cm !== null && Math.abs(row.delta_cm) >= CHANGED_CM),
  );
  const deltaCm = new Map(counted.map((row) => [row.segment_id, row.delta_cm ?? row.after_cm]));
  // Counted from the list the map draws, as the emulator's are, so the sentence and the drawn
  // count cannot disagree even when a body is trimmed.
  const worse = counted.filter((row) => row.newly_wet || (row.delta_cm ?? 0) > 0).length;
  const improved = counted.length - worse;
  const newlyWet = counted.filter((row) => row.newly_wet).length;
  const nothingChanged = counted.length === 0;
  const summary = changeSummary(worse, improved, newlyWet);
  const lines = [
    ...twinLeftOut(options.asked).map((lever) => TWIN_LEFT_OUT_LINES[lever]),
    // The job's notes, less its own left-out lines: those were written for the question that
    // started the job, and the lines above are for the question on screen.
    ...(options.scenarioNotes ?? []).filter((note) => !LEFT_OUT_TEXT.has(note)),
    `The Twin ran the whole city in ${formatMs(result.twin_ms)}; mass balance ${formatMassBalance(
      result.mass_balance.error_fraction,
    )}${result.mass_balance.within_budget ? ", inside" : ", outside"} the 0.1 % budget.`,
    ...(options.cacheLabel ? [options.cacheLabel] : []),
    // The job's own account of the method. Its tide sentence is printed as the outcome instead.
    ...result.notes.filter((note) => !note.startsWith("Tide offset ")),
  ];
  return {
    engine: "twin",
    runId: result.run_id,
    summary,
    nothingChanged,
    nChanged: counted.length,
    nDryBefore: counted.filter((row) => row.before_cm === null).length,
    deltaCm,
    hotspots: result.hotspots.map((row) => ({
      id: row.hotspot_id ?? row.name ?? "",
      hotspot: row.name ?? row.hotspot_id ?? "Chronic spot",
      beforeCm: row.before_cm ?? 0,
      afterCm: row.after_cm,
      ...(row.before_cm === null ? { beforeBelowCm: BEFORE_FLOOR_CM } : {}),
      minutesImpassableBefore: row.minutes_above_before[MINUTES_CM] ?? 0,
      minutesImpassableAfter: row.minutes_above_after[MINUTES_CM] ?? 0,
      minutesAbove45Before: row.minutes_above_before[MINUTES_45_CM] ?? 0,
      minutesAbove45After: row.minutes_above_after[MINUTES_45_CM] ?? 0,
    })),
    streets: counted.slice(0, MAX_STREET_ROWS).map((row) => ({
      id: row.segment_id,
      hotspot: streetName(row.segment_id, options.names?.get(row.segment_id)),
      beforeCm: row.before_cm ?? 0,
      afterCm: row.after_cm,
      ...(row.before_cm === null ? { beforeBelowCm: BEFORE_FLOOR_CM } : {}),
    })),
    tideOutcome: result.scenario.tide_offset_m !== 0 ? twinTideOutcome(result) : null,
    lines,
    ms: result.ms,
  };
}

/**
 * The segments a deep link asked to clean: `?segments=S100841069-000,S100841079-000`.
 *
 * Deduplicated in the order given and capped, because the URL is a hand-editable surface and a
 * list longer than the lever is written around would be silently truncated by the copy instead.
 * The cap is stated on screen when it bites.
 */
export function parseSegments(raw: string | null): { picked: string[]; asked: number } {
  const ids = (raw ?? "")
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean);
  const unique = [...new Set(ids)];
  return { picked: unique.slice(0, MAX_CLEANED_SEGMENTS), asked: unique.length };
}

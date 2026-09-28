/**
 * The route answer in words (UI_SPEC 4, PRD 3.4): reason records to sentences, and nothing else.
 *
 * The API returns reasons as records - a kind and its numbers - and never as prose
 * (`services/route/varuna_route/reasons.py` says why). This module is the only place those
 * records become English, so there is one copy of every sentence, one place to translate later
 * (`P9.9`), and one place the drop rule lives.
 *
 * **The drop rule.** A reason missing a number its sentence needs is not rendered at all. It is
 * never softened into "this road may flood": a vague sentence reads as knowledge VARUNA does not
 * have, which SPEC.md rule 6 forbids as firmly as a made-up number. Every builder below returns
 * `null` rather than a sentence with a hole in it.
 *
 * **What is deliberately not said.** UI_SPEC 4 writes the avoided sentence as "Avoids Dr Ambedkar
 * Road near Hindmata". No reason record carries a junction, and joining a street to the nearest
 * hotspot here would be a spatial claim invented in the browser, so the sentence names the street
 * and stops.
 *
 * Pure: no React, no fetch, no clock of its own. Times are formatted in IST by `lib/format`.
 */

import type { RouteReason, RouteReasonKind } from "@/lib/api/route";
import { formatIst, toDate } from "@/lib/format";
import { PROFILE_THRESHOLD_CM, type PassabilityProfile } from "@/lib/ramps";
import { A_ROAD, inSentence } from "@/lib/street-label";

/** At most four lines under "Why this way" (UI_SPEC 4). A wall of streets is not an explanation. */
export const MAX_REASONS = 4;

/**
 * What a street is called when the reason carries no name at all: "a road".
 *
 * The API names every segment now - OSM's name, else "off <street>" or "<class> near <place>"
 * (`varuna_api.street_names`) - so this is only an older API's gap. It used to read "an unnamed
 * road", which told the reader nothing they could find and is not said on any screen any more.
 */
export const UNNAMED_ROAD = A_ROAD;

/** One reason, worded. `severity` is exported so the order can be pinned by a test. */
export interface ExplainedReason {
  kind: RouteReasonKind;
  /** The segment the sentence is about; the key a list should render with. */
  segmentId: string;
  /** The sentence, complete with its full stop. */
  text: string;
  /** Higher is worse. See the severity scale below for why a closure outranks water. */
  severity: number;
}

/**
 * The severity scale, documented because the ordering is a judgement and not a measurement.
 *
 * A closure outranks every depth because it is a fact about the world - an officer shut the
 * street - rather than a forecast that might be wrong. Water the route refused outranks water on
 * the route it took, because the first is why the answer differs from the obvious one. The design
 * comparison ranks last: it is context for the street above it, never a reason on its own.
 */
const SEVERITY_BASE: Record<RouteReasonKind, number> = {
  closure: 1000,
  avoided: 100,
  timing: 10,
  design: 1,
};

function severityOf(reason: RouteReason): number {
  const base = SEVERITY_BASE[reason.kind];
  if (reason.kind === "avoided" || reason.kind === "timing") {
    const over = finite(reason.depthCm) - finite(reason.thresholdCm);
    return base + (Number.isFinite(over) ? Math.max(0, over) : 0);
  }
  return base;
}

function finite(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? value : Number.NaN;
}

/** A whole number of centimetres, or null when the value is not one. */
function cm(value: number | null | undefined): number | null {
  const n = finite(value);
  return Number.isFinite(n) ? Math.max(0, Math.round(n)) : null;
}

/** A rate in mm an hour, or null. A rate below a twentieth of a millimetre is not a sentence. */
function mmPerHour(value: number | null | undefined): string | null {
  const n = finite(value);
  if (!Number.isFinite(n) || n < 0.05) return null;
  return n >= 10 ? String(Math.round(n)) : n.toFixed(1);
}

/** "08:20" in IST, or null when the instant is missing or unparseable. */
function at(value: string | null | undefined): string | null {
  return toDate(value) ? formatIst(value) : null;
}

/**
 * The name to print for a street, repaired at read time.
 *
 * 95 Mumbai segments still carry a stringified Python list as their name where a way has several
 * (`services/products/varuna_products/names.py` fixes the products; this is the browser's copy of
 * the same rule, because an older run is still served). The first name wins: "Dr Ambedkar Road"
 * is a street a person can find, "Dr Ambedkar Road / Kalachowki Road" is a string VARUNA made up.
 * A name that merely starts with a bracket ("[Closed] Link Road") is a name and is left alone.
 *
 * The result is worded for the middle of a sentence: the API's proximity labels become "a road
 * off Dr Ambedkar Road" and "a service road near Wadala Depot" (`lib/street-label`), so a reader
 * never takes a description of where a lane is for the lane's own name.
 */
export function streetName(raw: string | null | undefined): string {
  const first = firstOf(raw);
  if (!first) return UNNAMED_ROAD;
  const lower = first.toLowerCase();
  if (/^unnamed (road|way|street)$/.test(lower) || ["none", "nan", "null"].includes(lower)) {
    return UNNAMED_ROAD;
  }
  return inSentence(first);
}

function firstOf(raw: string | null | undefined): string | null {
  const text = (raw ?? "").trim();
  if (!text) return null;
  if (!text.startsWith("[") || !text.endsWith("]")) return text;
  for (const part of text.slice(1, -1).split(",")) {
    const item = part
      .trim()
      .replace(/^['"]|['"]$/g, "")
      .trim();
    if (item && item.toLowerCase() !== "none") return item;
  }
  return null;
}

/** "A road off Dr Ambedkar Road" when a label opens a sentence; a no-op on a real street name. */
function opening(name: string): string {
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/** The vehicles a person names (UI_SPEC 9). Anything else falls back to the profile's own word. */
const VEHICLE_NOUN: Record<PassabilityProfile, string> = {
  "two-wheeler": "a two-wheeler",
  car: "a car",
  bus: "a bus",
  ambulance: "an ambulance",
  "fire-tender": "a fire tender",
  pedestrian: "on foot",
};

/** "a car", "on foot"; the raw profile string when the API names one this build does not know. */
export function vehicleNoun(profile: string): string {
  return profile in PROFILE_THRESHOLD_CM ? VEHICLE_NOUN[profile as PassabilityProfile] : profile;
}

/** "deeper than a car can cross (30 cm)" - a depth always carries the vehicle it stops. */
function tooDeepFor(profile: string, thresholdCm: number): string {
  const noun = vehicleNoun(profile);
  const body = noun === "on foot" ? "is safe on foot" : `${noun} can cross`;
  return `deeper than ${body} (${thresholdCm} cm)`;
}

/** "still under the 30 cm that stops a car" - water that rises without stopping this vehicle. */
function stillUnderFor(profile: string, thresholdCm: number): string {
  const noun = vehicleNoun(profile);
  const body = noun === "on foot" ? "is unsafe on foot" : `stops ${noun}`;
  return `still under the ${thresholdCm} cm that ${body}`;
}

/** "passable for a car", "passable on foot" - the tail of the "leave before" line. */
export function passableFor(profile: string): string {
  const noun = vehicleNoun(profile);
  return noun === "on foot" ? "passable on foot" : `passable for ${noun}`;
}

/**
 * One reason as a sentence, or `null` when a number its sentence needs is missing.
 *
 * `profile` supplies the vehicle the depth is measured against; the threshold itself comes from
 * the reason record, so the sentence quotes the number the router actually routed on rather than
 * a constant this file keeps.
 */
export function explainReason(reason: RouteReason, profile: string): ExplainedReason | null {
  const text = sentence(reason, profile);
  if (text === null) return null;
  return { kind: reason.kind, segmentId: reason.segmentId, text, severity: severityOf(reason) };
}

function sentence(reason: RouteReason, profile: string): string | null {
  const name = streetName(reason.name);
  switch (reason.kind) {
    case "avoided": {
      const depth = cm(reason.depthCm);
      const threshold = cm(reason.thresholdCm);
      const time = at(reason.at);
      if (depth === null || threshold === null || time === null) return null;
      return `Avoids ${name} — ${depth} cm at ${time}, ${tooDeepFor(profile, threshold)}.`;
    }
    case "design": {
      const design = mmPerHour(reason.designIntensityMmH);
      const peak = mmPerHour(reason.forecastPeakMmH);
      if (design === null || peak === null) return null;
      return (
        `The drain under ${name} was sized for ${design} mm of rain an hour; ` +
        `this cycle peaks at ${peak} mm an hour.`
      );
    }
    case "timing": {
      const dryBelow = cm(reason.dryBelowCm);
      const dryUntil = at(reason.dryUntil);
      const depth = cm(reason.depthCm);
      const threshold = cm(reason.thresholdCm);
      const time = at(reason.at);
      if (dryBelow === null || dryUntil === null || depth === null) return null;
      if (threshold === null || time === null) return null;
      const tail =
        depth >= threshold ? tooDeepFor(profile, threshold) : stillUnderFor(profile, threshold);
      return (
        `Your road stays under ${dryBelow} cm until ${dryUntil}, ` +
        `then rises to ${depth} cm by ${time} — ${tail}.`
      );
    }
    case "closure": {
      const time = at(reason.at);
      const why = (reason.reason ?? "").trim();
      if (time === null || !why) return null;
      const by = (reason.user ?? "").trim();
      const until = at(reason.until);
      const head = by ? `closed by ${by} at ${time}` : `closed at ${time}`;
      return `${opening(name)}: ${until ? `${head} until ${until}` : head} — ${trimStop(why)}.`;
    }
    default:
      return null;
  }
}

function trimStop(text: string): string {
  return text.replace(/[.!?]+$/, "");
}

/**
 * Every reason this run can support, worst first, capped at four (UI_SPEC 4).
 *
 * The sort is stable, so reasons of equal severity keep the order the router emitted them in -
 * which for avoided streets is deepest first already.
 */
export function explainReasons(
  reasons: readonly RouteReason[] | null | undefined,
  profile: string,
  options: { max?: number } = {},
): ExplainedReason[] {
  const max = options.max ?? MAX_REASONS;
  const worded: { reason: ExplainedReason; index: number }[] = [];
  for (const reason of reasons ?? []) {
    const explained = explainReason(reason, profile);
    if (explained !== null) worded.push({ reason: explained, index: worded.length });
  }
  return worded
    .sort((a, b) => b.reason.severity - a.reason.severity || a.index - b.index)
    .slice(0, Math.max(0, max))
    .map((entry) => entry.reason);
}

/**
 * A corridor's share as a person counts it: "6 in 10".
 *
 * `null` when there is no share to print, so a chip shows its letter alone rather than a made-up
 * fraction. A share that rounds to nothing is said as "under 1 in 10" rather than "0 in 10",
 * which would read as "nobody", and a share that rounds to everything is said as "over 9 in 10"
 * unless it really is the whole of it.
 */
export function shareInTen(share: number | null | undefined): string | null {
  const n = finite(share);
  if (!Number.isFinite(n) || n <= 0) return null;
  const tenths = Math.round(n * 10);
  if (tenths <= 0) return "under 1 in 10";
  if (tenths >= 10) return n >= 0.999 ? "10 in 10" : "over 9 in 10";
  return `${tenths} in 10`;
}

/**
 * The spreading disclosure (UI_SPEC 4), with the count matching the chips actually on screen.
 *
 * UI_SPEC writes "three safe roads". Printing "three" beside two chips would be a number the
 * screen contradicts, so the count word follows what is shown and the rest is verbatim.
 */
export function spreadingDisclosure(corridorCount: number): string {
  const word = corridorCount === 3 ? "three" : corridorCount === 2 ? "two" : String(corridorCount);
  return (
    `We spread drivers across ${word} safe roads so the safe road does not become the next jam. ` +
    "The split is our policy, not a measured traffic count."
  );
}

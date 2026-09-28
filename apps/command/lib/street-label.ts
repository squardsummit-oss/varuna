/**
 * How a street's display name was made, and how to say it inside a sentence.
 *
 * OSM names none of 52.6 % of Mumbai's road segments, so the API gives each of those a label it
 * builds from true parts (`services/api/varuna_api/street_names.py`): "off Dr Ambedkar Road" for
 * a lane leading off a named street within 200 m, "Service road near Wadala Depot" for the road
 * class near a registered hotspot or station, "Residential street in Mumbai" when nothing is
 * closer. Those labels read as places near a street, never as the street's own name - and a
 * sentence has to keep them that way: "Avoids off Dr Ambedkar Road" is not English, and
 * "Avoids Service road near Wadala Depot" reads as a proper name. `inSentence` turns them into
 * "a road off Dr Ambedkar Road" and "a service road near Wadala Depot".
 *
 * Pure string work: no fetch, no React.
 */

/** Where a label came from, in the API's own words (`street_names.LabelKind`). */
export type StreetLabelKind = "osm" | "off" | "near" | "in";

/**
 * OSM's `highway` class in the words a reader uses: `street_names.CLASS_WORDS`, entry for entry.
 * `street-label.test.ts` reads the Python table and fails if the two differ, and pins
 * `messages/en.json`'s `map.roadClass` to it, so the list, the popover and the public map in
 * English all say what the API says.
 */
export const ROAD_CLASS_WORDS = {
  motorway: "Expressway",
  motorway_link: "Expressway ramp",
  trunk: "Arterial road",
  trunk_link: "Arterial road ramp",
  primary: "Main road",
  primary_link: "Main road link",
  secondary: "Secondary road",
  secondary_link: "Secondary road link",
  tertiary: "Local road",
  tertiary_link: "Local road link",
  residential: "Residential street",
  living_street: "Residential lane",
  unclassified: "Minor road",
  service: "Service road",
  road: "Road",
} as const;

/** One of the classes `ROAD_CLASS_WORDS` has words for. */
export type RoadClass = keyof typeof ROAD_CLASS_WORDS;

/** `road_class` as one this module knows, else "road": the API writes "Road" for any other. */
export function roadClass(value: unknown): RoadClass {
  const key = String(value ?? "")
    .trim()
    .toLowerCase();
  return Object.hasOwn(ROAD_CLASS_WORDS, key) ? (key as RoadClass) : "road";
}

/**
 * The class words, longest first. A "near" or "in" label opens with one of these; anything else
 * is a name.
 */
const CLASS_WORDS = [...new Set(Object.values(ROAD_CLASS_WORDS))].sort(
  (a, b) => b.length - a.length,
);

const CLASS_LABEL = new RegExp(`^(${CLASS_WORDS.join("|")}) (near|in) (\\S.*)$`);

/** What a street is called in a sentence when nothing names it at all. */
export const A_ROAD = "a road";

/**
 * Which kind of label `name` is. "off " in lower case is the API's alone: an OSM name does not
 * open with it. A class-word label is recognised only in the exact form the API writes.
 */
export function streetLabelKind(name: string | null | undefined): StreetLabelKind {
  const text = (name ?? "").trim();
  if (/^off \S/.test(text)) return "off";
  const match = CLASS_LABEL.exec(text);
  if (match) return match[2] === "near" ? "near" : "in";
  return "osm";
}

/**
 * A display name taken apart: how it was made, the proper noun inside it (the street of an "off"
 * label, the place of a "near" label, the city of an "in" label) and the road class. A screen in
 * Hindi or Marathi words its own sentence around `anchor`, so "off" and "Service road" are
 * translated and only the name stays as OSM or the register wrote it.
 */
export interface StreetLabelParts {
  kind: StreetLabelKind;
  /** Null for an OSM name, which is its own anchor. */
  anchor: string | null;
  roadClass: RoadClass;
}

/**
 * The parts of one segment's label, from the fields the segments layer serves: `display_kind` and
 * `display_anchor` when the API sent them, else read back out of `display_name` in the exact form
 * the API writes it (a layer served before the parts were).
 */
export function streetLabelParts(
  displayName: string | null | undefined,
  properties: { display_kind?: unknown; display_anchor?: unknown; class?: unknown } = {},
): StreetLabelParts {
  const cls = roadClass(properties.class);
  const served = properties.display_kind;
  const anchor =
    typeof properties.display_anchor === "string" ? properties.display_anchor.trim() : "";
  if ((served === "off" || served === "near" || served === "in") && anchor) {
    return { kind: served, anchor, roadClass: cls };
  }
  const text = (displayName ?? "").trim();
  const kind = streetLabelKind(text);
  if (kind === "off") return { kind, anchor: text.slice(4).trim(), roadClass: cls };
  if (kind === "near" || kind === "in") {
    const match = CLASS_LABEL.exec(text);
    const words = match?.[1] ?? "";
    // The label's own class words win over the feature's `class` when they disagree.
    const fromWords = (Object.keys(ROAD_CLASS_WORDS) as RoadClass[]).find(
      (key) => ROAD_CLASS_WORDS[key] === words,
    );
    return { kind, anchor: match?.[3] ?? null, roadClass: fromWords ?? cls };
  }
  return { kind: "osm", anchor: null, roadClass: cls };
}

/** True when `name` describes where a street is rather than naming it. */
export function isProximityLabel(name: string | null | undefined): boolean {
  return streetLabelKind(name) !== "osm";
}

/**
 * `name` as it reads mid-sentence: an OSM name unchanged, "a road off Dr Ambedkar Road",
 * "a service road near Wadala Depot", "an expressway ramp near Sion Circle". An empty name reads
 * "a road" - honest about knowing nothing more, and never "an unnamed road".
 */
export function inSentence(name: string | null | undefined): string {
  const text = (name ?? "").trim();
  if (!text) return A_ROAD;
  switch (streetLabelKind(text)) {
    case "off":
      return `${A_ROAD} ${text}`;
    case "near":
    case "in": {
      const lowered = text.charAt(0).toLowerCase() + text.slice(1);
      return `${/^[aeiou]/.test(lowered) ? "an" : "a"} ${lowered}`;
    }
    default:
      return text;
  }
}

/** What a list row prints when nothing names a street: the translated `map.unnamedRoad` says it too. */
export const LIST_ROAD = "Road";

/**
 * `name` as a list row prints it: a name or an API label unchanged, and "Road" for nothing or for
 * the "Unnamed road" an older API still writes (the deployed one does until it is redeployed).
 */
export function listName(name: string | null | undefined): string {
  const text = (name ?? "").trim();
  if (!text || /^unnamed (road|way|street)$/i.test(text)) return LIST_ROAD;
  if (["none", "nan", "null"].includes(text.toLowerCase())) return LIST_ROAD;
  return text;
}

/**
 * The title an observation carries when no street was recorded for it: a citizen report sent
 * without one, or a segment the city build does not carry. It says what is true.
 */
export const STREET_NOT_RECORDED = "Street not recorded";

/**
 * Where something was, as the end of a clause: " at Dr Ambedkar Road", " at a road off Eastern
 * Freeway", " at a service road near Hindmata", and ", street not recorded" when nothing was -
 * "at Street not recorded" is not English.
 */
export function atPlace(place: string | null | undefined): string {
  const text = (place ?? "").trim();
  if (!text || text === STREET_NOT_RECORDED || /^unnamed (road|way|street)$/i.test(text)) {
    return ", street not recorded";
  }
  return ` at ${inSentence(text)}`;
}

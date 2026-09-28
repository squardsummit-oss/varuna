import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import en from "@/messages/en.json";
import hi from "@/messages/hi.json";
import mr from "@/messages/mr.json";

import {
  A_ROAD,
  LIST_ROAD,
  ROAD_CLASS_WORDS,
  STREET_NOT_RECORDED,
  atPlace,
  inSentence,
  isProximityLabel,
  listName,
  roadClass,
  streetLabelKind,
  streetLabelParts,
} from "./street-label";

/** `street_names.CLASS_WORDS`, read out of the Python source so the two cannot drift apart. */
function pythonClassWords(): Record<string, string> {
  const source = readFileSync(
    resolve(__dirname, "../../../services/api/varuna_api/street_names.py"),
    "utf-8",
  );
  const block = /^CLASS_WORDS: dict\[str, str\] = \{([\s\S]*?)^\}/m.exec(source)?.[1] ?? "";
  return Object.fromEntries(
    [...block.matchAll(/"(\w+)": "([^"]+)"/g)].map((m) => [m[1] ?? "", m[2] ?? ""]),
  );
}

describe("street labels", () => {
  it("tells each kind of API label apart from an OSM name", () => {
    expect(streetLabelKind("Dr Ambedkar Road")).toBe("osm");
    expect(streetLabelKind("off Eastern Freeway")).toBe("off");
    expect(streetLabelKind("Service road near Hindmata")).toBe("near");
    expect(streetLabelKind("Residential street in Mumbai")).toBe("in");
    expect(streetLabelKind("Road near Wadala Depot")).toBe("near");
  });

  it("does not mistake a real name for a label", () => {
    // OSM names that happen to hold the words; only the API's exact forms are labels.
    expect(streetLabelKind("Office Lane")).toBe("osm");
    expect(streetLabelKind("Off Carter Road")).toBe("osm");
    expect(streetLabelKind("Main Road near")).toBe("osm");
    expect(streetLabelKind("Link Road in")).toBe("osm");
    expect(isProximityLabel("Senapati Bapat Marg")).toBe(false);
    expect(isProximityLabel("off Senapati Bapat Marg")).toBe(true);
  });

  it("words a label for the middle of a sentence", () => {
    expect(inSentence("off Eastern Freeway")).toBe("a road off Eastern Freeway");
    expect(inSentence("Service road near Hindmata")).toBe("a service road near Hindmata");
    expect(inSentence("Arterial road near Sion Circle")).toBe("an arterial road near Sion Circle");
    expect(inSentence("Minor road in Mumbai")).toBe("a minor road in Mumbai");
    expect(inSentence("Dr Ambedkar Road")).toBe("Dr Ambedkar Road");
  });

  it("says 'a road' when there is nothing to say, never 'unnamed'", () => {
    expect(inSentence("")).toBe(A_ROAD);
    expect(inSentence(null)).toBe(A_ROAD);
    expect(inSentence(undefined)).not.toMatch(/unnamed/i);
  });

  it("prints a list row by its name or label, and 'Road' for what an older API left unnamed", () => {
    expect(listName("off Eastern Freeway")).toBe("off Eastern Freeway");
    expect(listName("Dr Ambedkar Road")).toBe("Dr Ambedkar Road");
    expect(listName("Unnamed road")).toBe(LIST_ROAD);
    expect(listName("unnamed way")).toBe(LIST_ROAD);
    expect(listName("  ")).toBe(LIST_ROAD);
    expect(listName(null)).toBe(LIST_ROAD);
  });

  it("words each road class exactly as the API does, and translates all of them", () => {
    const python = pythonClassWords();
    expect(Object.keys(python)).toHaveLength(15);
    expect(ROAD_CLASS_WORDS).toEqual(python);
    // The public map's English class words are the same table.
    expect(en.map.roadClass).toEqual(python);
    for (const messages of [hi, mr]) {
      expect(Object.keys(messages.map.roadClass).sort()).toEqual(Object.keys(python).sort());
    }
    expect(roadClass("motorway_link")).toBe("motorway_link");
    expect(roadClass("footway")).toBe("road");
    expect(roadClass(undefined)).toBe("road");
  });

  it("takes a label apart from the parts the layer serves, or from its words", () => {
    expect(
      streetLabelParts("off Kokri Agar Road", {
        display_kind: "off",
        display_anchor: "Kokri Agar Road",
        class: "residential",
      }),
    ).toEqual({ kind: "off", anchor: "Kokri Agar Road", roadClass: "residential" });
    // A layer served before the parts: read back out of the words, the class from the label.
    expect(streetLabelParts("Expressway ramp near Sion Circle", { class: "service" })).toEqual({
      kind: "near",
      anchor: "Sion Circle",
      roadClass: "motorway_link",
    });
    expect(streetLabelParts("Service road in Mumbai")).toEqual({
      kind: "in",
      anchor: "Mumbai",
      roadClass: "service",
    });
    expect(streetLabelParts("off Eastern Freeway").anchor).toBe("Eastern Freeway");
    expect(streetLabelParts("Dr Ambedkar Road", { class: "primary" })).toEqual({
      kind: "osm",
      anchor: null,
      roadClass: "primary",
    });
  });

  it("ends a clause with where something was, never 'at Street not recorded'", () => {
    expect(atPlace("Dr Ambedkar Road")).toBe(" at Dr Ambedkar Road");
    expect(atPlace("off Sharada Devi Road")).toBe(" at a road off Sharada Devi Road");
    expect(atPlace("Service road near Saki Naka")).toBe(" at a service road near Saki Naka");
    expect(atPlace(STREET_NOT_RECORDED)).toBe(", street not recorded");
    expect(atPlace("")).toBe(", street not recorded");
    expect(atPlace("Unnamed road")).toBe(", street not recorded");
  });
});

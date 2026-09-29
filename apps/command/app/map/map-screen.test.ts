import { describe, expect, it } from "vitest";

import { createTranslator } from "next-intl";

import en from "@/messages/en.json";
import hi from "@/messages/hi.json";
import mr from "@/messages/mr.json";
import { intlLocale, mergeMessages, type PublicMessages } from "@/lib/i18n";
import { streetLabelParts } from "@/lib/street-label";

import { UNLISTED_ROAD, honestyLine, nearbyStreets, streetRowName } from "./map-screen";

const run = {
  // Two 5-minute steps at 06:45 and 06:50 IST.
  validTs: ["2019-07-02T06:45:00+05:30", "2019-07-02T06:50:00+05:30"],
  depthCm: new Map<string, number[]>([
    ["S-named", [10, 40]],
    ["S-unnamed", [35, 50]],
    ["S-dry", [2, 3]],
  ]),
};

describe("nearbyStreets", () => {
  it("prints the layer's display name, and never 'Unnamed road'", () => {
    const names = new Map([
      ["S-named", "Dr Ambedkar Road"],
      ["S-unnamed", "off Dr Ambedkar Road"],
    ]);
    expect(nearbyStreets(run, 30, names).map((r) => [r.id, r.name])).toEqual([
      ["S-unnamed", "off Dr Ambedkar Road"],
      ["S-named", "Dr Ambedkar Road"],
    ]);
    // A street the layer does not carry reads as a road, not as an unnamed one.
    const partial = nearbyStreets(run, 30, new Map([["S-named", "Dr Ambedkar Road"]]));
    expect(partial.find((r) => r.id === "S-unnamed")?.name).toBe(UNLISTED_ROAD);
    expect(partial.some((r) => /unnamed/i.test(r.name ?? ""))).toBe(false);
  });

  it("claims no name at all while the names are still loading", () => {
    const rows = nearbyStreets(run, 30, null);
    expect(rows.every((r) => r.name === null)).toBe(true);
  });

  it("gives the last passable step, or none when the street is already over the vehicle", () => {
    const rows = nearbyStreets(run, 30, new Map());
    expect(rows.find((r) => r.id === "S-named")?.passableUntil).toBe("06:45");
    expect(rows.find((r) => r.id === "S-unnamed")?.passableUntil).toBeNull();
  });
});

describe("honestyLine", () => {
  it("times the line from the run the map drew", () => {
    expect(honestyLine("2019-07-02T06:40:00+05:30")).toBe(
      "Forecast from the last VARUNA run at 06:40",
    );
  });

  it("does not name a time before a run has loaded", () => {
    expect(honestyLine(null)).not.toMatch(/\d/);
  });

  it("stops claiming to load once the map knows there is nothing to draw", () => {
    expect(honestyLine(null, "empty")).toBe("No VARUNA run yet; the map fills after the first run");
    expect(honestyLine(null, "error")).toBe(
      "The forecast did not load; check the connection and reload",
    );
    expect(honestyLine(null, "error")).not.toMatch(/loading/i);
  });

  it("keeps the run's time when a later reload fails", () => {
    expect(honestyLine("2019-07-02T06:40:00+05:30", "error")).toMatch(/06:40/);
  });
});

describe("streetRowName", () => {
  type Tree = Parameters<typeof mergeMessages>[0];
  const translator = (locale: "hi" | "mr") =>
    createTranslator<PublicMessages, "map">({
      locale: intlLocale(locale),
      messages: mergeMessages(
        en as unknown as Tree,
        (locale === "hi" ? hi : mr) as unknown as Tree,
      ) as unknown as PublicMessages,
      namespace: "map",
    });
  const off = streetLabelParts("off Kokri Agar Road", {
    display_kind: "off",
    display_anchor: "Kokri Agar Road",
    class: "residential",
  });
  const near = streetLabelParts("Residential street near Saki Naka", {
    display_kind: "near",
    display_anchor: "Saki Naka",
    class: "residential",
  });
  const inCity = streetLabelParts("Service road in Mumbai", {
    display_kind: "in",
    display_anchor: "Mumbai",
    class: "service",
  });

  it("prints the API's label unchanged in English", () => {
    expect(streetRowName("off Kokri Agar Road", off)).toBe("off Kokri Agar Road");
    expect(streetRowName("Residential street near Saki Naka", near)).toBe(
      "Residential street near Saki Naka",
    );
    expect(streetRowName(UNLISTED_ROAD, null)).toBe("Road");
  });

  it("words a built label in Hindi and Marathi, keeping only the proper noun in Latin", () => {
    for (const locale of ["hi", "mr"] as const) {
      const t = translator(locale);
      const rows = [
        streetRowName("off Kokri Agar Road", off, locale, t),
        streetRowName("Residential street near Saki Naka", near, locale, t),
        streetRowName("Service road in Mumbai", inCity, locale, t),
      ];
      expect(rows[0]).toContain("Kokri Agar Road");
      expect(rows[1]).toContain("Saki Naka");
      expect(rows[2]).toContain("Mumbai");
      for (const row of rows) {
        // No English connective or class word left in the Devanagari sentence.
        expect(row, `${locale}: ${row}`).not.toMatch(
          /\b(off|near|in|Residential|Service|street|road)\b/,
        );
        expect(row).toMatch(/\p{Script=Devanagari}/u);
      }
      // An OSM name is a proper noun and stays as OSM wrote it.
      expect(streetRowName("Dr Ambedkar Road", null, locale, t)).toBe("Dr Ambedkar Road");
      expect(streetRowName(UNLISTED_ROAD, null, locale, t)).toBe(t("unnamedRoad"));
    }
    expect(streetRowName("off Kokri Agar Road", off, "hi", translator("hi"))).toBe(
      "Kokri Agar Road से लगी सड़क",
    );
    expect(streetRowName("Residential street near Saki Naka", near, "mr", translator("mr"))).toBe(
      "Saki Naka जवळील निवासी रस्ता",
    );
  });
});

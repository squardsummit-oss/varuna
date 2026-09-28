import { createTranslator } from "next-intl";
import { describe, expect, it } from "vitest";

import en from "@/messages/en.json";
import glossary from "@/messages/glossary.json";
import hi from "@/messages/hi.json";
import mr from "@/messages/mr.json";
import { PROFILE_LABELS } from "@/lib/stores/ui";

import { intlLocale } from "./locales";
import { loadMessages, mergeMessages } from "./messages";

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out[path] = value;
    else Object.assign(out, flatten(value, path));
  }
  return out;
}

const EN = flatten(en);
const TRANSLATIONS = { hi: flatten(hi), mr: flatten(mr) } as const;

/** ICU arguments in a message ("{time}", "{count, plural, ...}"), by name. */
function argumentsOf(message: string): string[] {
  return [...message.matchAll(/\{(\w+)/g)].map((m) => m[1] ?? "").sort();
}

describe("public messages", () => {
  for (const [locale, messages] of Object.entries(TRANSLATIONS)) {
    it(`${locale} carries exactly the English keys`, () => {
      expect(Object.keys(messages).sort()).toEqual(Object.keys(EN).sort());
    });

    it(`${locale} keeps every placeholder the English message has`, () => {
      for (const [key, message] of Object.entries(EN)) {
        expect(argumentsOf(messages[key] ?? ""), key).toEqual(argumentsOf(message));
      }
    });

    it(`${locale} prints numbers, units and product names in Latin`, () => {
      for (const [key, message] of Object.entries(messages)) {
        // Devanagari digits are U+0966-U+096F; the copy rules keep "45 cm" and "09:25" Latin.
        expect(message, key).not.toMatch(/[०-९]/);
        for (const word of glossary.keepLatin) {
          if (EN[key]?.includes(word)) expect(message, key).toContain(word);
        }
      }
    });
  }

  it("keeps the honesty lines saying what they say in English", () => {
    // Rule 6: a translation may not soften a claim. The forecast line names the run it came from,
    // the offline confirmation says the report has not changed any forecast.
    for (const locale of ["hi", "mr"] as const) {
      const t = TRANSLATIONS[locale];
      const term = (name: keyof typeof glossary.terms) => glossary.terms[name][locale];
      expect(t["map.honesty"]).toContain("VARUNA");
      expect(t["map.honesty"]).toContain(term("forecast"));
      expect(t["map.honesty"]).toContain("5");
      expect(t["report.honesty"]).toContain("Pulse");
      expect(t["report.honesty"]).toContain(term("cycle"));
      expect(t["report.savedOfflineBody"]).toContain("VARUNA");
      expect(t["report.savedOfflineBody"]).toContain(term("forecast"));
      expect(t["report.queuedFollowUp"]).toContain(term("drain X-ray"));
      expect(t["report.queuedFollowUp"]).toContain(term("observation"));
    }
  });

  it("uses the glossary's term for each vocabulary word", () => {
    const checks: [string, keyof typeof glossary.terms][] = [
      ["legend.passable", "passable"],
      ["legend.impassable", "impassable"],
      ["legend.caution", "caution"],
      ["vehicle.two-wheeler", "two-wheeler"],
      ["vehicle.pedestrian", "pedestrian"],
      ["depth.ankle", "ankle"],
      ["depth.knee", "knee"],
      ["depth.waist", "waist"],
      ["report.stepDepth", "depth"],
      ["report.stepLocation", "location"],
      ["map.impassableNow", "impassable"],
      ["map.passableUntil", "passable"],
      ["map.unnamedRoad", "street"],
    ];
    for (const locale of ["hi", "mr"] as const) {
      for (const [key, term] of checks) {
        expect(TRANSLATIONS[locale][key], `${locale} ${key}`).toContain(
          glossary.terms[term][locale].split(" ")[0],
        );
      }
    }
  });

  it("names each vehicle in English exactly as the rest of the product does", () => {
    for (const profile of ["two-wheeler", "car", "bus", "pedestrian"] as const) {
      expect(EN[`vehicle.${profile}`]).toBe(PROFILE_LABELS[profile]);
    }
  });

  it("formats plural counts with Latin digits in all three languages", () => {
    for (const locale of ["en", "hi", "mr"] as const) {
      const messages = locale === "en" ? en : locale === "hi" ? hi : mr;
      const t = createTranslator({ locale: intlLocale(locale), messages, namespace: "report" });
      expect(t("improved", { count: 3 })).toContain("3");
      expect(t("improved", { count: 3 })).not.toMatch(/[०-९]/);
    }
  });

  it("falls back to English for a key a translation is missing", async () => {
    const merged = mergeMessages(en as unknown as Tree, { map: { reportWater: "X" } }) as Tree;
    expect((merged.map as Tree).reportWater).toBe("X");
    expect((merged.map as Tree).streetsToAvoid).toBe("Streets to avoid");
    expect((await loadMessages("mr")).map.reportWater).toBe(mr.map.reportWater);
    expect(await loadMessages("en")).toBe(en);
  });
});

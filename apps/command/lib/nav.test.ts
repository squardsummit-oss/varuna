import { describe, expect, it } from "vitest";

import { NAV_ITEMS, navAccessibleName, navHref, navItem } from "./nav";
import { navShortcutLabel } from "./shortcuts";

/*
 * ADR-0085 gave six operator screens a Sanskrit name with an English gloss; the console, the alert
 * centre and onboarding followed as Drishti, Sanket and Pravesh, so every screen has one. The URLs,
 * the order and the Alt chords are unchanged, so a bookmark, a test and a rehearsed key still work.
 */
describe("the screen nav", () => {
  it("keeps the order, the URLs and the chords, and names every screen in Sanskrit", () => {
    expect(NAV_ITEMS.map((i) => [i.id, i.href, i.label, i.gloss ?? null, i.hint])).toEqual([
      ["console", "/console", "Drishti", "Command console", "Alt+1"],
      ["drains", "/drains", "Nadi", "Drain health", "Alt+2"],
      ["route", "/route", "Marga", "Route planner", "Alt+3"],
      ["alerts", "/alerts", "Sanket", "Alert centre", "Alt+4"],
      ["pumps", "/pumps", "Jalayantra", "Pump dispatch", "Alt+5"],
      ["whatif", "/whatif", "Kalpana", "What-if lab", "Alt+6"],
      ["replay", "/replay", "Smriti", "Replay", "Alt+7"],
      ["verify", "/verify", "Pramana", "Verification", "Alt+8"],
      ["onboard", "/onboard", "Pravesh", "City onboarding", "Alt+9"],
    ]);
  });

  it("gives every Sanskrit name its Devanagari and a meaning, and plain ASCII on screen", () => {
    expect(NAV_ITEMS.every((i) => i.gloss)).toBe(true);
    for (const item of NAV_ITEMS) {
      // Geist and Bricolage have no IAST dot letters (no ṛ, ṇ or ḍ), so the romanised name is
      // plain ASCII and the Devanagari is the accent that carries the exact word.
      expect(item.label).toMatch(/^[A-Z][a-z]+$/);
      expect(item.deva).toMatch(/^[ऀ-ॿ]+$/);
      expect(item.meaning?.length ?? 0).toBeGreaterThan(0);
    }
  });

  it("gives a Sanskrit name its gloss in the accessible name and the shortcut", () => {
    expect(navAccessibleName(navItem("drains"))).toBe("Nadi, drain health");
    expect(navAccessibleName(navItem("whatif"))).toBe("Kalpana, what-if lab");
    expect(navAccessibleName(navItem("console"))).toBe("Drishti, command console");
    expect(navAccessibleName(navItem("alerts"))).toBe("Sanket, alert centre");
    expect(navAccessibleName(navItem("onboard"))).toBe("Pravesh, city onboarding");
    expect(navShortcutLabel(navItem("verify"))).toBe("Go to Pramana (verification)");
    expect(navShortcutLabel(navItem("alerts"))).toBe("Go to Sanket (alert centre)");
    expect(navShortcutLabel(navItem("console"))).toBe("Go to Drishti (command console)");
  });

  it("carries a city other than the default, and nothing for Mumbai", () => {
    expect(navHref(navItem("pumps"), "chennai")).toBe("/pumps?city=chennai");
    expect(navHref(navItem("pumps"), "mumbai")).toBe("/pumps");
    expect(navHref(navItem("pumps"), null)).toBe("/pumps");
  });

  it("refuses an id the rail does not carry", () => {
    expect(() => navItem("drain")).toThrow('No rail item "drain"');
  });
});

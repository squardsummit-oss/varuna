import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { colors } from "@/lib/ramps";

import { darkMapStyle, resolveToken } from "./google-style";

/** The five tokens TECH_SPEC 2.3 allows the basemap, and their values in `tokens.json`. */
const ALLOWED = [colors.ink, colors.deep, colors.well, colors.line, colors["text-3"]];

describe("darkMapStyle", () => {
  it("paints every colour from a token and never from a literal", () => {
    const used = darkMapStyle()
      .flatMap((rule) => rule.stylers)
      .map((styler) => styler.color)
      .filter((value): value is string => typeof value === "string");

    expect(used.length).toBeGreaterThan(0);
    for (const colour of used) expect(ALLOWED).toContain(colour);
  });

  it("carries no raw colour literal in its own source", () => {
    const source = readFileSync(path.join(__dirname, "google-style.ts"), "utf8");
    // The rule `pnpm lint:design` enforces, asserted here too so a future edit fails a test
    // rather than only a lint the author may not run.
    expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(source).not.toMatch(/\b(rgba?|hsla?|oklch)\(/);
  });

  it("switches off the furniture that carries no flood information", () => {
    const off = darkMapStyle().filter((rule) =>
      rule.stylers.some((styler) => styler.visibility === "off"),
    );
    const features = off.map((rule) => rule.featureType ?? "all");
    expect(features).toContain("poi");
    expect(features).toContain("transit");
  });

  it("gives the sea the deepest tone and the land the panel tone", () => {
    const rules = darkMapStyle();
    const water = rules.find((r) => r.featureType === "water" && r.elementType === "geometry");
    const land = rules.find((r) => r.featureType === undefined && r.elementType === "geometry");
    expect(water?.stylers[0]?.color).toBe(colors.ink);
    expect(land?.stylers[0]?.color).toBe(colors.deep);
  });
});

describe("resolveToken", () => {
  it("falls back to the generated token value when the document has no computed property", () => {
    // jsdom returns "" for an unset custom property, which is the server's situation too.
    expect(resolveToken("--ink")).toBe(colors.ink);
    expect(resolveToken("--text-3")).toBe(colors["text-3"]);
  });

  it("prefers a value the document actually carries, so a theme override reaches the tiles", () => {
    document.documentElement.style.setProperty("--well", "cornflowerblue");
    try {
      expect(resolveToken("--well")).toBe("cornflowerblue");
    } finally {
      document.documentElement.style.removeProperty("--well");
    }
  });
});

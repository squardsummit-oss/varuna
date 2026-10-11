import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  KEYWORDS,
  LONG_DESCRIPTION,
  SITE_DESCRIPTION,
  SITE_TITLE,
  SOURCE_REPOSITORY,
  structuredData,
} from "./seo";

/** Words a description of a prototype must not use about itself. */
const CLAIMS = /\b(best|leading|world-class|first-ever|guaranteed|accurate to)\b/i;

describe("seo", () => {
  it("fits search results: a title under 60 and a description under 160 characters", () => {
    expect(SITE_TITLE.length).toBeLessThanOrEqual(60);
    expect(SITE_DESCRIPTION.length).toBeLessThanOrEqual(160);
    expect(SITE_TITLE).toMatch(/urban flood nowcasting/i);
  });

  it("makes no ranking or accuracy claims", () => {
    for (const text of [SITE_TITLE, SITE_DESCRIPTION, LONG_DESCRIPTION, ...KEYWORDS]) {
      expect(text).not.toMatch(CLAIMS);
    }
    const llms = readFileSync(join(__dirname, "..", "public", "llms.txt"), "utf8");
    expect(llms).not.toMatch(CLAIMS);
    expect(llms).toContain(SOURCE_REPOSITORY);
  });

  it("describes the site, the application and its source as schema.org JSON-LD", () => {
    const data = structuredData("https://example.test");
    const parsed = JSON.parse(JSON.stringify(data)) as { "@graph": { "@type": string }[] };
    expect(parsed["@graph"].map((node) => node["@type"])).toEqual([
      "WebSite",
      "SoftwareApplication",
      "SoftwareSourceCode",
    ]);
    expect(JSON.stringify(data)).toContain("https://example.test/#app");
    expect(JSON.stringify(data)).toContain(SOURCE_REPOSITORY);
  });
});

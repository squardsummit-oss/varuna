/**
 * Self-test for tools/lint-design.mjs. Feeds fixture strings to the exported rule functions.
 *
 * Run: node --test tools/lint-design.test.mjs
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  ACRONYMS,
  GLOBALS_CSS,
  RULES,
  capsViolations,
  checkColorFunctions,
  checkEmoji,
  checkFontFamily,
  checkHex,
  checkJsxText,
  checkTransitionAll,
  copyViolations,
  extractJsxText,
  formatReport,
  isTokenFontStack,
  lintSource,
  shouldScan,
  stripComments,
} from "./lint-design.mjs";

const rules = (findings) => findings.map((f) => f.rule);

describe("hex", () => {
  it("flags six and eight digit hex in classes, styles and CSS", () => {
    assert.equal(checkHex('className="hover:bg-[#383838]"').length, 1);
    assert.equal(checkHex("color: #2DD4BF;", { ext: ".css" }).length, 1);
    assert.equal(checkHex('style={{ background: "#0A1020CC" }}').length, 1);
    const [f] = checkHex('className="hover:bg-[#383838]"');
    assert.equal(f.rule, "hex");
    assert.equal(f.excerpt, "#383838");
    assert.equal(f.col, 22, "1-based column of the # character");
    assert.match(f.message, /var\(--tide\)/);
  });

  it("flags short hex with letters and, in CSS, short hex of digits", () => {
    assert.equal(checkHex('color: "#fff"').length, 1);
    assert.equal(checkHex("color: #abcd;", { ext: ".css" }).length, 1);
    assert.equal(checkHex("border: 1px solid #123;", { ext: ".css" }).length, 1);
  });

  it("does not flag prose numbers, fragments or css identifiers", () => {
    assert.equal(checkHex("Issue #123 in the BMC log").length, 0);
    assert.equal(checkHex('href="/console#alerts"').length, 0);
    assert.equal(checkHex("run #2019").length, 0);
    assert.equal(checkHex("var(--tide)").length, 0);
    assert.equal(checkHex("#hotspot-rail { }", { ext: ".css" }).length, 0);
    assert.equal(checkHex("&#8212;").length, 0);
  });

  it("allows a hex next to a var(--...) mapping only in globals.css", () => {
    const line = "--primary: var(--tide); /* #2DD4BF */";
    assert.equal(checkHex(line, { ext: ".css", isGlobalsCss: true }).length, 0);
    assert.equal(checkHex(line, { ext: ".css", isGlobalsCss: false }).length, 1);
    assert.equal(checkHex("--primary: #2DD4BF;", { ext: ".css", isGlobalsCss: true }).length, 1);
  });
});

describe("color-fn", () => {
  it("flags rgb, hsl and oklch literals", () => {
    assert.equal(checkColorFunctions("--background: oklch(1 0 0);").length, 1);
    assert.equal(checkColorFunctions("--border: oklch(1 0 0 / 10%);").length, 1);
    assert.equal(checkColorFunctions('color: "rgba(0, 0, 0, 0.5)"').length, 1);
    assert.equal(checkColorFunctions("background: hsl(210deg 40% 20%)").length, 1);
    assert.equal(checkColorFunctions("rgb(45 212 191 / 0.45)")[0].rule, "color-fn");
  });

  it("does not flag calls that reference variables or interpolate", () => {
    assert.equal(checkColorFunctions("rgb(var(--tide-rgb) / 0.5)").length, 0);
    assert.equal(checkColorFunctions("`rgb(${r} ${g} ${b})`").length, 0);
    assert.equal(checkColorFunctions("color-mix(in oklch, var(--tide), transparent 50%)").length, 0);
    assert.equal(checkColorFunctions("hexToRgb(hex)").length, 0);
  });
});

describe("font", () => {
  it("accepts the token families, generic families and variables", () => {
    assert.ok(isTokenFontStack('"Bricolage Grotesque", system-ui, sans-serif'));
    assert.ok(isTokenFontStack("var(--font-geist-sans), system-ui, -apple-system, 'Segoe UI', sans-serif"));
    assert.ok(isTokenFontStack("Geist Mono, ui-monospace, SFMono-Regular, Menlo, monospace"));
    assert.ok(isTokenFontStack("inherit"));
    assert.ok(isTokenFontStack("var(--font-display)"));
    assert.equal(checkFontFamily("font-family: var(--font-bricolage), system-ui, sans-serif;").length, 0);
    assert.equal(checkFontFamily('style={{ fontFamily: "var(--font-geist-mono)" }}').length, 0);
    assert.equal(checkFontFamily('className="font-[var(--font-display)]"').length, 0);
    assert.equal(checkFontFamily('import { Bricolage_Grotesque, Geist, Geist_Mono } from "next/font/google";').length, 0);
  });

  it("flags fonts outside the token set everywhere they can be declared", () => {
    assert.equal(checkFontFamily("font-family: Inter, sans-serif;")[0].rule, "font");
    assert.equal(checkFontFamily('style={{ fontFamily: "Roboto" }}').length, 1);
    assert.equal(checkFontFamily('className="font-[Open_Sans]"').length, 1);
    assert.equal(checkFontFamily('className="font-[family-name:Inter]"').length, 1);
    assert.equal(checkFontFamily('import { Inter } from "next/font/google";').length, 1);
    assert.equal(checkFontFamily('import { Geist, Inter as Body } from "next/font/google";').length, 1);
    assert.match(checkFontFamily("font-family: Inter, sans-serif;")[0].message, /font-display, font-sans or font-mono/);
  });
});

describe("transition-all", () => {
  it("flags the class and leaves other transitions alone", () => {
    assert.equal(checkTransitionAll('className="transition-all duration-200"').length, 1);
    assert.equal(checkTransitionAll("@apply transition-all;").length, 1);
    assert.equal(checkTransitionAll('className="transition-colors motion-safe:transition-transform"').length, 0);
    assert.equal(checkTransitionAll("no-transition-all-here").length, 0);
  });
});

describe("emoji", () => {
  it("flags emoji, pictographs and symbol-block characters", () => {
    assert.equal(checkEmoji("Hindmata is flooding \u{1F30A}").length, 1);
    assert.equal(checkEmoji("done ✅").length, 1);
    assert.equal(checkEmoji("\u{1F1EE}\u{1F1F3}").length, 2);
    assert.equal(checkEmoji("sun ☀️").length, 1);
    assert.equal(checkEmoji("warning ⚠").length, 1);
    const [f] = checkEmoji("\u{1F6A8} alert");
    assert.equal(f.rule, "emoji");
    assert.match(f.message, /U\+1F6A8/);
    assert.match(f.message, /Lucide/);
  });

  it("does not flag typography, units or arrows used in copy", () => {
    assert.equal(checkEmoji("18:20 (+40 min) · 45 cm ≥ 30 cm → next").length, 0);
    assert.equal(checkEmoji("© 2026 VARUNA ™ ®").length, 0);
    assert.equal(checkEmoji("− 60 min, 1440 × 900, 25 °C").length, 0);
    assert.equal(checkEmoji("Bricolage Grotesque, Geist").length, 0);
  });
});

describe("caps", () => {
  it("flags all-caps words of four or more letters unless they are acronyms", () => {
    assert.deepEqual(
      capsViolations("OPEN THE CONSOLE").map((v) => v.word),
      ["OPEN", "CONSOLE"],
    );
    assert.equal(capsViolations("Hindmata junction: 55 cm at 18:20").length, 0);
    assert.equal(capsViolations("CSI 0.71 on MUM-2019-07-02 (IST)").length, 0);
    assert.equal(capsViolations("VARUNA · CAP 1.2 · GTFS-RT · WGS84 · EPSG:32643 · P0").length, 0);
    assert.equal(capsViolations("Replay 30× · MUM-CENTRAL · CHN-SOUTH").length, 0);
    assert.equal(capsViolations("The BMC's log").length, 0);
    assert.equal(capsViolations("DISPATCH PUMPS")[0].word, "DISPATCH");
    assert.equal(capsViolations("SEVERE").length, 1);
    assert.equal(capsViolations("ABC-DEFGH").length, 1, "a hyphenated word with an unknown long part is flagged");
    assert.deepEqual(capsViolations("KING'S CIRCLE").map((v) => v.word), ["KING", "CIRCLE"], "possessives are stripped before the check");
    assert.equal(capsViolations("VARUNA'S run").length, 0);
    assert.equal(capsViolations("TODO FIXME").length, 0, "placeholder words belong to the copy rule");
    assert.ok(ACRONYMS.has("EnKF"));
  });
});

describe("copy", () => {
  it("flags lorem ipsum, TODO and FIXME", () => {
    assert.equal(copyViolations("Lorem ipsum dolor sit amet")[0].phrase, "Lorem ipsum");
    assert.equal(copyViolations("LOREM IPSUM").length, 1);
    assert.equal(copyViolations("TODO: wire the fan chart")[0].phrase, "TODO");
    assert.equal(copyViolations("FIXME later").length, 1);
    assert.deepEqual(
      copyViolations("TODO lorem ipsum").map((v) => v.col),
      [0, 5],
      "findings are ordered by column",
    );
    assert.equal(copyViolations("No runs yet - press Play on the replay, or Compute live.").length, 0);
    assert.equal(copyViolations("todos are fine as a word").length, 0);
  });
});

describe("JSX text extraction", () => {
  it("finds text nodes and user-facing attributes, with lines", () => {
    const src = [
      "export function Row() {",
      "  return (",
      '    <button title="Dispatch pumps" aria-label="ACKNOWLEDGE ALERT">',
      "      SEVERE",
      "      <span>{count} streets</span>",
      "    </button>",
      "  );",
      "}",
    ].join("\n");
    const nodes = extractJsxText(src);
    const texts = nodes.map((n) => n.text);
    assert.ok(texts.includes("SEVERE"));
    assert.ok(texts.includes("Dispatch pumps"));
    assert.ok(texts.includes("ACKNOWLEDGE ALERT"));
    assert.ok(texts.includes("streets"));
    assert.equal(nodes.find((n) => n.text === "SEVERE").line, 4);
    assert.equal(nodes.find((n) => n.text === "SEVERE").col, 7);
    assert.equal(nodes.find((n) => n.text === "ACKNOWLEDGE ALERT").line, 3);
  });

  it("splits text around expressions and keeps every copy segment", () => {
    const texts = extractJsxText("<p>Depth {d} cm at {t} on Dr Ambedkar Road</p>").map((n) => n.text);
    assert.deepEqual(texts, ["Depth", "cm at", "on Dr Ambedkar Road"]);
    assert.deepEqual(
      extractJsxText('<li>{items.map((i) => <b key={i.id}>{i.name} FLOODED</b>)}</li>').map((n) => n.text),
      ["FLOODED"],
    );
    assert.deepEqual(extractJsxText("<p>{value}</p>"), []);
  });

  it("ignores generics, expressions and code between angle brackets", () => {
    const src = [
      "const x: Map<string, number> = new Map<string, number>();",
      "function f<T>(a: T): T { return a; }",
      "const y = a > b ? <A /> : <B />;",
      "const cb = () => <div>{value}</div>;",
    ].join("\n");
    assert.deepEqual(extractJsxText(src), []);
  });

  it("checkJsxText reports caps and copy with the rule names", () => {
    const src = "<p>Lorem ipsum</p>\n<h2>WATERLOGGING AT KING'S CIRCLE</h2>\n<i>TODO</i>";
    const found = checkJsxText(src, { file: "x.tsx" });
    assert.deepEqual(rules(found).sort(), ["caps", "caps", "caps", "copy", "copy"]);
    assert.deepEqual(
      found.filter((f) => f.rule === "caps").map((f) => f.line),
      [2, 2, 2],
      "WATERLOGGING, KING and CIRCLE are flagged; AT is short and TODO belongs to the copy rule",
    );
    assert.deepEqual(
      found.filter((f) => f.rule === "copy").map((f) => [f.line, f.excerpt]),
      [
        [1, "Lorem ipsum"],
        [3, "TODO"],
      ],
    );
    assert.equal(checkJsxText(src, { file: "x.ts" }).length, 0, "plain TypeScript has no JSX text");
  });
});

describe("comment stripping", () => {
  it("blanks comments but keeps strings, positions and newlines", () => {
    const src = 'const url = "https://osm.org/#map"; // #2DD4BF\n/* #EF4444 */ const x = 1;';
    const out = stripComments(src, ".tsx");
    assert.equal(out.length, src.length);
    assert.ok(out.includes('"https://osm.org/#map"'));
    assert.ok(!out.includes("#2DD4BF"));
    assert.ok(!out.includes("#EF4444"));
    assert.equal(out.split("\n").length, 2);
  });

  it("treats // inside CSS as content, not a comment", () => {
    const css = "a { background: url(//cdn/x.png); color: #FFF; }";
    assert.ok(stripComments(css, ".css").includes("#FFF"));
  });
});

describe("lintSource", () => {
  it("reports file, line and column and honours the allow markers", () => {
    const src = [
      'const a = "#2DD4BF";',
      'const b = "#2DD4BF"; // lint-design-allow: fixture for the tokens test',
      "// lint-design-allow-next-line",
      'const c = "#2DD4BF";',
      "const d = 'oklch(1 0 0)';",
      '// a comment with #FFFFFF is fine',
      "const e = '\u{1F30A}'; ",
    ].join("\n");
    const found = lintSource(src, "apps/command/lib/x.ts");
    assert.deepEqual(
      found.map((f) => [f.line, f.rule]),
      [
        [1, "hex"],
        [5, "color-fn"],
        [7, "emoji"],
      ],
    );
    assert.equal(found[0].file, "apps/command/lib/x.ts");
    assert.equal(found[0].col, 12);
  });

  it("applies the globals.css bridge allowance only to that file", () => {
    const css = "--primary: var(--tide); /* was #2DD4BF */\n--ring: #2DD4BF;\n--background: oklch(1 0 0);";
    const found = lintSource(css, GLOBALS_CSS);
    assert.deepEqual(
      found.map((f) => [f.line, f.rule]),
      [
        [2, "hex"],
        [3, "color-fn"],
      ],
    );
    assert.equal(lintSource("--x: #2DD4BF; /* var(--tide) */", "apps/command/app/other.css").length, 1);
  });

  it("finds the scaffold problems in a create-next-app page", () => {
    const src = [
      'import { Inter } from "next/font/google";',
      "export default function Home() {",
      "  return (",
      '    <a className="transition-all hover:bg-[#383838]" href="https://vercel.com">',
      "      DEPLOY NOW",
      "    </a>",
      "  );",
      "}",
    ].join("\n");
    const found = lintSource(src, "apps/command/app/page.tsx");
    assert.deepEqual(rules(found).sort(), ["caps", "font", "hex", "transition-all"]);
  });
});

describe("file selection", () => {
  it("scans app sources and skips vendor, generated and test files", () => {
    assert.ok(shouldScan("apps/command/app/page.tsx"));
    assert.ok(shouldScan("apps/command/app/globals.css"));
    assert.ok(shouldScan("apps/command/components/varuna/TimeBar.tsx"));
    assert.ok(shouldScan("apps/command/lib/stores/replay.ts"));
    assert.ok(shouldScan("apps/command/app/design/page.mdx"));
    assert.ok(!shouldScan("apps/command/components/ui/button.tsx"));
    assert.ok(shouldScan("apps/command/lib/ui/format.ts"), "only components/ui is vendor code");
    assert.ok(!shouldScan("apps/command/lib/api/types.ts"));
    assert.ok(!shouldScan("apps/command/lib/ramps.test.ts"));
    assert.ok(!shouldScan("apps/command/lib/__tests__/ramps.ts"));
    assert.ok(!shouldScan("apps/command/next-env.d.ts"));
    assert.ok(!shouldScan("apps/command/app/opengraph-image.png"));
    assert.ok(!shouldScan("apps/command/app/node_modules/x/index.js"));
  });
});

describe("report", () => {
  it("prints one line per finding and a summary with counts", () => {
    const result = {
      files: ["apps/command/app/page.tsx"],
      findings: [
        { file: "apps/command/app/page.tsx", line: 4, col: 33, rule: "hex", message: "raw hex colour", excerpt: "#383838" },
        { file: "apps/command/app/page.tsx", line: 5, col: 7, rule: "caps", message: "all-caps label", excerpt: "DEPLOY" },
      ],
      counts: Object.fromEntries(RULES.map((r) => [r, 0])),
      ms: 3.2,
    };
    result.counts.hex = 1;
    result.counts.caps = 1;
    const text = formatReport(result);
    assert.match(text, /^apps\/command\/app\/page\.tsx:4:33 {2}hex/m);
    assert.match(text, /1 file scanned in 3 ms/);
    assert.match(text, /2 violations in 1 file: hex 1, caps 1/);
    assert.match(text, /lint-design-allow/);
    const clean = formatReport({ files: [], findings: [], counts: result.counts, ms: 1 });
    assert.match(clean, /clean/);
  });
});

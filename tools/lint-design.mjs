#!/usr/bin/env node
/**
 * VARUNA design lint (SPEC.md task P0.9, rule 9 of section 0).
 *
 * The design system in SPEC.md section 6 is law: every colour comes from packages/tokens, every
 * font from the token set, every string follows the copy rules in 6.8, and there are no emoji. This
 * script enforces the mechanical part of that on the app source and fails the build when it slips.
 *
 * Usage:
 *   pnpm lint:design                       scan the default folders (see SCAN_DIRS), exit 1 on violations
 *   node tools/lint-design.mjs <paths...>  scan specific files or folders instead
 *   node tools/lint-design.mjs --json      machine-readable findings on stdout
 *
 * Rules (each is a pure function exported for tools/lint-design.test.mjs):
 *   hex             raw #RGB, #RGBA, #RRGGBB or #RRGGBBAA colours
 *   color-fn        rgb()/rgba()/hsl()/hsla()/oklch()/oklab()/lab()/lch()/hwb() literals
 *   font            font-family, fontFamily, font-[...] or next/font/google naming a non-token font
 *   transition-all  the Tailwind class transition-all (only catalogued motions, section 8)
 *   emoji           any emoji or pictographic code point anywhere in the file (Lucide icons only)
 *   caps            ALL-CAPS words of four or more letters in JSX text that are not known acronyms
 *   copy            "lorem ipsum", "TODO" or "FIXME" in JSX text
 *
 * Escapes: a line containing "lint-design-allow" is exempt from every rule; a line containing
 * "lint-design-allow-next-line" exempts the following line. In apps/command/app/globals.css a line
 * that maps a variable to the tokens with var(--...) may also contain a hex (the shadcn bridge).
 * Comments are ignored by the colour and font rules; emoji are banned even in comments.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(HERE, "..");

/** Folders scanned by default, relative to the repository root. components/ui is vendor code. */
export const SCAN_DIRS = [
  "apps/command/app",
  "apps/command/components/varuna",
  "apps/command/components/v0",
  "apps/command/components/map",
  // Added 2026-09-23. These two were never scanned, so SPEC.md rule 9 had simply never been
  // enforced on the landing page, the globe or the citizen screens - the whole of `/`, and most
  // of `/dashboard`. Both folders were clean when the linter was first pointed at them, so this
  // adds no exceptions; it closes a gate that was open.
  "apps/command/components/landing",
  "apps/command/components/citizen",
  "apps/command/lib",
];

/** Generated or vendor files never scanned, relative to the repository root. */
export const EXCLUDED_FILES = ["apps/command/lib/api/types.ts"];
/** Vendor folders never scanned (shadcn primitives), relative to the repository root. */
export const EXCLUDED_DIRS = ["apps/command/components/ui"];
export const EXCLUDED_DIR_NAMES = new Set(["node_modules", ".next", "__tests__", "dist", "coverage"]);
export const EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx", ".mjs", ".css", ".mdx"]);
const TEST_FILE = /\.(test|spec)\.[cm]?[jt]sx?$/;

/** The only file where a hex may sit next to a var(--...) mapping. */
export const GLOBALS_CSS = "apps/command/app/globals.css";

export const ALLOW_MARKER = "lint-design-allow";
export const ALLOW_NEXT_LINE_MARKER = "lint-design-allow-next-line";

export const RULES = ["hex", "color-fn", "font", "transition-all", "emoji", "caps", "copy"];

/**
 * Words that may appear in ALL CAPS in visible text. The spec list from P0.9 plus the domain
 * acronyms the screens use (city ids, agencies, models). Anything else in caps is a label that
 * should be sentence case (SPEC.md 6.8).
 */
export const ACRONYMS = new Set([
  // spec list
  "VARUNA", "CAP", "CSI", "MAE", "API", "IST", "OSM", "DEM", "IMD", "BMC", "KEM", "LTMG", "WS", "XML",
  "CSV", "GIS", "GPU", "CPU", "EnKF", "ID", "OK", "UI", "PS", "SIH", "MoES", "VIT", "EN", "HI", "MR",
  "P0", "P1", "P2", "3D", "2D", "1D", "JSON", "PNG", "URL", "AOI", "UTM", "LK", "STEPS", "WGS84", "EPSG",
  "IDW", "MFB", "GLO-30", "CARTO", "POD", "FAR", "AUC", "ROC", "LCP", "CLS", "FPS", "HGL", "NWP", "QPE",
  "GTFS-RT", "WhatsApp", "SMS", "OG",
  // domain acronyms that appear on screens
  "MUM-CENTRAL", "CHN-SOUTH", "MUM", "CHN", "IDF", "IFLOWS", "C-FLOWS", "INSAT-3DS", "INSAT", "NCMRWF",
  "INCOIS", "NCCR", "IIT-B", "SWMM", "GNN", "DWR", "AWS", "SVG", "PDF", "HTML", "CSS", "SCS-CN",
  "NASADEM", "SRTM", "UTC", "ISO", "GTFS", "MILP", "ETA", "CCTV", "SAR", "AR", "CFL", "RMSE", "PWA",
  "USB", "COG", "ADR", "ARIA", "WGS", "SWE", "DWL", "MCGM", "MHADA", "MMRDA", "NDMA", "SDMA", "INR",
  "LiDAR", "PMTiles", "MapLibre", "GeoJSON", "GeoParquet", "NetCDF", "Zarr",
]);

/** Font families the design system allows (SPEC.md 6.3), plus generic and system fallbacks. */
export const TOKEN_FONTS = new Set(
  [
    "Bricolage Grotesque", "Geist Sans", "Geist Mono", "Geist", "Noto Sans Devanagari", "Noto Sans Tamil",
    // generic families and the system stacks used as fallbacks in tokens.json
    "system-ui", "sans-serif", "serif", "monospace", "ui-monospace", "ui-sans-serif", "ui-serif", "ui-rounded",
    "-apple-system", "BlinkMacSystemFont", "Segoe UI", "SFMono-Regular", "Menlo", "Consolas", "emoji", "math",
    "fangsong", "cursive", "fantasy",
    // CSS keywords
    "inherit", "initial", "unset", "revert", "revert-layer",
  ].map((f) => f.toLowerCase()),
);

/** next/font/google export names that are token fonts. */
export const TOKEN_FONT_IMPORTS = new Set(["Bricolage_Grotesque", "Geist", "Geist_Mono", "Noto_Sans_Devanagari", "Noto_Sans_Tamil"]);

/* ------------------------------------------------------------------------------------------------
 * Source preparation
 * ---------------------------------------------------------------------------------------------- */

/**
 * Blanks comments (line and block) so colour and font rules ignore documentation, while keeping
 * every character position and newline so line numbers still match. Strings and template literals
 * are respected so "https://" is not mistaken for a comment.
 */
export function stripComments(source, ext = ".tsx") {
  const cssLike = ext === ".css";
  const out = source.split("");
  const blank = (from, to) => {
    for (let i = from; i < to; i += 1) if (out[i] !== "\n") out[i] = " ";
  };
  let i = 0;
  const n = source.length;
  while (i < n) {
    const c = source[i];
    const next = source[i + 1];
    if (c === "/" && next === "*") {
      const end = source.indexOf("*/", i + 2);
      const stop = end === -1 ? n : end + 2;
      blank(i, stop);
      i = stop;
      continue;
    }
    if (!cssLike && c === "/" && next === "/") {
      let end = source.indexOf("\n", i);
      if (end === -1) end = n;
      blank(i, end);
      i = end;
      continue;
    }
    if (!cssLike && (c === '"' || c === "'" || c === "`")) {
      const quote = c;
      let j = i + 1;
      while (j < n) {
        if (source[j] === "\\") {
          j += 2;
          continue;
        }
        if (source[j] === quote) break;
        if (quote !== "`" && source[j] === "\n") break;
        j += 1;
      }
      i = j + 1;
      continue;
    }
    i += 1;
  }
  return out.join("");
}

/** Splits text into lines with 1-based numbers, dropping Windows line endings. */
export function toLines(text) {
  return text.replace(/\r\n/g, "\n").split("\n");
}

/* ------------------------------------------------------------------------------------------------
 * Rules on a single line. Each returns [{ col, rule, message, excerpt }].
 * ---------------------------------------------------------------------------------------------- */

const finding = (rule, col, message, excerpt) => ({ rule, col: col + 1, message, excerpt });

const HEX_RE = /(^|[^\w&/.-])#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])/g;

/** Raw hex colours. Three or four digit forms must contain a letter (so "#123" in prose is prose). */
export function checkHex(line, ctx = {}) {
  const out = [];
  for (const m of line.matchAll(HEX_RE)) {
    const digits = m[2];
    const isCss = ctx.ext === ".css";
    if (digits.length <= 4 && !isCss && !/[a-fA-F]/.test(digits)) continue;
    if (ctx.isGlobalsCss && line.includes("var(--")) continue;
    const col = m.index + m[1].length;
    out.push(finding("hex", col, `raw hex colour "#${digits}"; use a token: var(--tide), bg-depth-3, colors.tide from @varuna/tokens`, `#${digits}`));
  }
  return out;
}

const COLOR_FN_RE = /\b(rgba?|hsla?|oklch|oklab|hwb|lab|lch)\(\s*([0-9.%\s,/deg-]+)\)/gi;

/** rgb()/hsl()/oklch()... literals. Calls that wrap var(--...) or template values are not literals. */
export function checkColorFunctions(line, ctx = {}) {
  const out = [];
  for (const m of line.matchAll(COLOR_FN_RE)) {
    if (ctx.isGlobalsCss && line.includes("var(--")) continue;
    out.push(finding("color-fn", m.index, `${m[1]}() colour literal; colours come from tokens.json (var(--...) or @varuna/tokens helpers)`, m[0]));
  }
  return out;
}

/** Normalises one family name: quotes and underscores removed, lower case. */
function familyName(raw) {
  return raw
    .trim()
    .replace(/^["']|["']$/g, "")
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

/** True when a font-family value only names token families, generic families or var(--...). */
export function isTokenFontStack(value) {
  const parts = [];
  let depth = 0;
  let current = "";
  for (const ch of value) {
    if (ch === "(") depth += 1;
    if (ch === ")") depth -= 1;
    if (ch === "," && depth === 0) {
      parts.push(current);
      current = "";
    } else current += ch;
  }
  parts.push(current);
  return parts.every((part) => {
    const p = part.trim();
    if (!p) return true;
    if (/^var\(--[\w-]+\)$/.test(p) || /^\$\{.*\}$/.test(p) || p.startsWith("--")) return true;
    return TOKEN_FONTS.has(familyName(p));
  });
}

const FONT_FAMILY_CSS_RE = /font-family\s*:\s*([^;}]+)/g;
const FONT_FAMILY_JS_RE = /fontFamily\s*:\s*(["'`])([^"'`]*)\1/g;
const FONT_ARBITRARY_RE = /\bfont-\[(?:family-name:)?([^\]]+)\]/g;
const FONT_IMPORT_RE = /import\s*\{([^}]+)\}\s*from\s*["']next\/font\/google["']/g;

/** Fonts outside the token set, in CSS declarations, style objects, Tailwind arbitrary values and next/font imports. */
export function checkFontFamily(line, ctx = {}) {
  const out = [];
  const flag = (col, value, how) =>
    out.push(finding("font", col, `${how} names a font outside the token set (${value.trim()}); use font-display, font-sans or font-mono`, value.trim()));
  for (const m of line.matchAll(FONT_FAMILY_CSS_RE)) if (!isTokenFontStack(m[1])) flag(m.index, m[1], "font-family");
  for (const m of line.matchAll(FONT_FAMILY_JS_RE)) if (!isTokenFontStack(m[2])) flag(m.index, m[2], "fontFamily");
  for (const m of line.matchAll(FONT_ARBITRARY_RE)) {
    const value = m[1].replace(/_/g, " ");
    if (!isTokenFontStack(value)) flag(m.index, m[0], "font-[...]");
  }
  for (const m of line.matchAll(FONT_IMPORT_RE)) {
    const names = m[1].split(",").map((s) => s.trim().split(/\s+as\s+/)[0].trim()).filter(Boolean);
    for (const name of names) if (!TOKEN_FONT_IMPORTS.has(name)) flag(m.index, name, "next/font/google import");
  }
  void ctx;
  return out;
}

const TRANSITION_ALL_RE = /(?<![\w-])transition-all(?![\w-])/g;

/** The Tailwind class transition-all: transitions must name their property (section 8 motion catalogue). */
export function checkTransitionAll(line) {
  const out = [];
  for (const m of line.matchAll(TRANSITION_ALL_RE)) {
    out.push(finding("transition-all", m.index, "transition-all is banned; transition only the property the motion catalogue names (transition-colors, transition-opacity, transition-transform)", m[0]));
  }
  return out;
}

/**
 * Emoji: Extended_Pictographic code points, emoji presentation, regional indicators and the common
 * symbol blocks. Copyright, registered and trademark signs are typography, not emoji.
 */
const EMOJI_RE = /(?![©®™])(?:\p{Extended_Pictographic}|\p{Emoji_Presentation}|[\u{1F1E6}-\u{1F1FF}]|[\u{1F000}-\u{1FAFF}]|[\u{2600}-\u{27BF}]|[\u{1F900}-\u{1F9FF}])/gu;

export function checkEmoji(line) {
  const out = [];
  for (const m of line.matchAll(EMOJI_RE)) {
    const cp = m[0].codePointAt(0).toString(16).toUpperCase().padStart(4, "0");
    out.push(finding("emoji", m.index, `emoji or pictograph U+${cp} is banned; use a Lucide icon`, m[0]));
  }
  return out;
}

/* ------------------------------------------------------------------------------------------------
 * Rules on JSX text (whole-source, because text nodes can span lines)
 * ---------------------------------------------------------------------------------------------- */

const JSX_TAG_RE = /<\/?[A-Za-z][^<>]*?>/g;
const JSX_ATTR_RE = /\b(title|aria-label|placeholder|alt|label|headline|description|caption|tooltip)=(["'])([^"']*)\2/g;

/**
 * Visible text nodes and user-facing attribute strings in JSX, with the line they start on.
 *
 * A text node is what sits between a tag and the next "<". {expressions} inside it are skipped, so
 * "<span>{count} streets</span>" yields "streets". A run is dropped when it looks like code rather
 * than copy: it contains ";" or "=", hits an unbalanced "}" or a stray ">" (arrow functions,
 * generics, comparisons), or never reaches a closing "<".
 */
export function extractJsxText(source) {
  const out = [];
  const lineAt = (index) => source.slice(0, index).split("\n").length;
  const colAt = (index) => index - source.lastIndexOf("\n", index - 1);
  const node = (start, end) => {
    const text = source.slice(start, end);
    if (!/[A-Za-z]/.test(text) || /[;=]/.test(text)) return null;
    const leading = text.match(/^\s*/)[0].length;
    return { text: text.trim(), line: lineAt(start + leading), col: colAt(start + leading) };
  };
  for (const tag of source.matchAll(JSX_TAG_RE)) {
    const segments = [];
    let i = tag.index + tag[0].length;
    let segmentStart = i;
    let depth = 0;
    let closed = false;
    while (i < source.length) {
      const c = source[i];
      if (depth === 0 && c === "<") {
        segments.push([segmentStart, i]);
        closed = true;
        break;
      }
      if (c === "{") {
        if (depth === 0) segments.push([segmentStart, i]);
        depth += 1;
      } else if (c === "}") {
        if (depth === 0) break;
        depth -= 1;
        if (depth === 0) segmentStart = i + 1;
      } else if (c === ">" && depth === 0) {
        break;
      }
      i += 1;
    }
    if (!closed) continue;
    for (const [start, end] of segments) {
      const found = node(start, end);
      if (found) out.push(found);
    }
  }
  for (const m of source.matchAll(JSX_ATTR_RE)) {
    if (!/[A-Za-z]/.test(m[3])) continue;
    const offset = m.index + m[0].length - m[3].length - 1;
    out.push({ text: m[3].trim(), line: lineAt(offset), col: offset - source.lastIndexOf("\n", offset) });
  }
  return out;
}

const WORD_RE = /[A-Za-z0-9][A-Za-z0-9'’-]*/g;
/** Placeholder words the copy rule reports; the caps rule leaves them alone so each is reported once. */
const COPY_WORDS = new Set(["TODO", "FIXME"]);

/** ALL-CAPS words (four or more letters) that are not known acronyms; the label should be sentence case. */
export function capsViolations(text) {
  const out = [];
  for (const m of text.matchAll(WORD_RE)) {
    const word = m[0].replace(/['’][sS]$/, "");
    if (!/^[A-Z][A-Z0-9-]*$/.test(word)) continue;
    if (word.replace(/[^A-Z]/g, "").length <= 3) continue;
    if (ACRONYMS.has(word) || COPY_WORDS.has(word)) continue;
    if (word.split("-").every((part) => ACRONYMS.has(part) || part.replace(/[^A-Z]/g, "").length <= 3)) continue;
    out.push({ word, col: m.index });
  }
  return out;
}

const LOREM_RE = /\blorem\s+ipsum\b/gi;
const TODO_RE = /\b(TODO|FIXME)\b/g;

/** Placeholder copy that must never reach a screen: lorem ipsum in any case, TODO and FIXME in caps. */
export function copyViolations(text) {
  const out = [];
  for (const re of [LOREM_RE, TODO_RE]) for (const m of text.matchAll(re)) out.push({ phrase: m[0], col: m.index });
  out.sort((a, b) => a.col - b.col);
  return out;
}

export function checkJsxText(source, ctx = {}) {
  if (!/\.(tsx|jsx|mdx)$/.test(ctx.file ?? ".tsx")) return [];
  const out = [];
  for (const node of extractJsxText(source)) {
    for (const v of capsViolations(node.text)) {
      out.push({ ...finding("caps", node.col - 1 + v.col, `"${v.word}" is an all-caps label; use sentence case (or add it to ACRONYMS in tools/lint-design.mjs if it is an acronym)`, node.text.slice(0, 80)), line: node.line });
    }
    for (const v of copyViolations(node.text)) {
      out.push({ ...finding("copy", node.col - 1 + v.col, `"${v.phrase}" in visible text; write the real Mumbai or Chennai copy`, node.text.slice(0, 80)), line: node.line });
    }
  }
  return out;
}

/* ------------------------------------------------------------------------------------------------
 * File-level driver
 * ---------------------------------------------------------------------------------------------- */

/** Lints one source string. `file` is the repository-relative path with forward slashes. */
export function lintSource(source, file) {
  const ext = path.extname(file).toLowerCase();
  const ctx = { file, ext, isGlobalsCss: file === GLOBALS_CSS };
  const rawLines = toLines(source);
  const codeLines = toLines(stripComments(source.replace(/\r\n/g, "\n"), ext));
  const allowed = new Set();
  rawLines.forEach((line, i) => {
    if (line.includes(ALLOW_MARKER)) allowed.add(i + 1);
    if (line.includes(ALLOW_NEXT_LINE_MARKER)) allowed.add(i + 2);
  });
  const findings = [];
  const add = (lineNo, items) => {
    if (allowed.has(lineNo)) return;
    for (const f of items) findings.push({ file, line: lineNo, ...f });
  };
  codeLines.forEach((line, i) => {
    const lineNo = i + 1;
    add(lineNo, checkHex(line, ctx));
    add(lineNo, checkColorFunctions(line, ctx));
    add(lineNo, checkFontFamily(line, ctx));
    add(lineNo, checkTransitionAll(line, ctx));
  });
  rawLines.forEach((line, i) => add(i + 1, checkEmoji(line, ctx)));
  for (const f of checkJsxText(codeLines.join("\n"), ctx)) {
    if (allowed.has(f.line)) continue;
    findings.push({ file, ...f });
  }
  findings.sort((a, b) => a.line - b.line || a.col - b.col || RULES.indexOf(a.rule) - RULES.indexOf(b.rule));
  return findings;
}

const toPosix = (p) => p.split(path.sep).join("/");

/** True when a repository-relative file path should be scanned. */
export function shouldScan(relFile) {
  const rel = toPosix(relFile);
  if (!EXTENSIONS.has(path.extname(rel).toLowerCase())) return false;
  if (EXCLUDED_FILES.includes(rel)) return false;
  if (EXCLUDED_DIRS.some((dir) => rel === dir || rel.startsWith(`${dir}/`))) return false;
  if (TEST_FILE.test(rel)) return false;
  if (rel.endsWith(".d.ts")) return false;
  const parts = rel.split("/");
  for (const part of parts.slice(0, -1)) if (EXCLUDED_DIR_NAMES.has(part)) return false;
  return true;
}

/** All scannable files under the given absolute paths (files or folders), as repository-relative posix paths. */
export function collectFiles(targets, root = REPO_ROOT) {
  const files = [];
  const seen = new Set();
  const consider = (abs) => {
    const rel = toPosix(path.relative(root, abs));
    if (rel.startsWith("..") || !shouldScan(rel) || seen.has(rel)) return;
    seen.add(rel);
    files.push(rel);
  };
  for (const target of targets) {
    const abs = path.resolve(root, target);
    let stat;
    try {
      stat = statSync(abs);
    } catch {
      continue;
    }
    if (stat.isFile()) {
      consider(abs);
      continue;
    }
    for (const entry of readdirSync(abs, { withFileTypes: true, recursive: true })) {
      if (!entry.isFile()) continue;
      const parent = entry.parentPath ?? entry.path;
      consider(path.join(parent, entry.name));
    }
  }
  files.sort();
  return files;
}

/** Lints files under the given targets. Returns { files, findings, counts, ms }. */
export function lintFiles(targets = SCAN_DIRS, root = REPO_ROOT) {
  const started = performance.now();
  const files = collectFiles(targets, root);
  const findings = [];
  for (const rel of files) findings.push(...lintSource(readFileSync(path.join(root, rel), "utf8"), rel));
  const counts = Object.fromEntries(RULES.map((r) => [r, 0]));
  for (const f of findings) counts[f.rule] += 1;
  return { files, findings, counts, ms: performance.now() - started };
}

/** Human-readable report: one line per finding, then a one-line summary. */
export function formatReport({ files, findings, counts, ms }) {
  const lines = [];
  const width = Math.max(...RULES.map((r) => r.length));
  for (const f of findings) {
    lines.push(`${f.file}:${f.line}:${f.col}  ${f.rule.padEnd(width)}  ${f.message}`);
  }
  const ruleSummary = RULES.filter((r) => counts[r] > 0)
    .map((r) => `${r} ${counts[r]}`)
    .join(", ");
  const filesHit = new Set(findings.map((f) => f.file)).size;
  lines.push("");
  lines.push(`lint:design  ${files.length} file${files.length === 1 ? "" : "s"} scanned in ${ms.toFixed(0)} ms`);
  if (findings.length === 0) {
    lines.push("lint:design  clean: no raw colours, off-token fonts, transition-all, emoji or all-caps labels.");
  } else {
    lines.push(`lint:design  ${findings.length} violation${findings.length === 1 ? "" : "s"} in ${filesHit} file${filesHit === 1 ? "" : "s"}: ${ruleSummary}`);
    lines.push(`lint:design  fix them, or mark a deliberate exception with a "${ALLOW_MARKER}" comment on that line.`);
  }
  return lines.join("\n");
}

function main(argv) {
  const json = argv.includes("--json");
  const targets = argv.filter((a) => !a.startsWith("--"));
  const result = lintFiles(targets.length > 0 ? targets : SCAN_DIRS);
  if (json) {
    console.log(JSON.stringify({ files: result.files, findings: result.findings, counts: result.counts, ms: Math.round(result.ms) }, null, 2));
  } else {
    console.log(formatReport(result));
  }
  return result.findings.length === 0 ? 0 : 1;
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  process.exitCode = main(process.argv.slice(2));
}

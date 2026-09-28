/**
 * The rural advisory as a document (UI_SPEC 7, task D-16): one string, no script tag.
 *
 * Why a hand-built document rather than a React page. `/rural` has a transfer budget of 30 KB and
 * a hard rule of no client JavaScript. Every route under `app/layout.tsx` inherits the app shell -
 * `globals.css`, two webfonts and the React runtime that hydrates it - which is the right trade
 * for `/console` and the wrong one for a 2G phone. A route handler renders outside that shell, so
 * this page carries its own few hundred bytes of CSS and nothing else. The cost is that it is not
 * a React screen and gets no component reuse; the gain is measured in `docs/QA.md`.
 *
 * Colours come from `@varuna/tokens` at render time, so the page is on the same palette as the
 * console without a stylesheet to fetch, and no hex is written here (SPEC.md rule 9). The type
 * is the token stack's system fallback: system fonts are what UI_SPEC 7 asks for, and they are
 * the only fonts that cost nothing to send.
 */

import { colors, depthColor, tokens } from "@varuna/tokens";

import type { RuralAdvisory, RuralVehicle } from "@/lib/rural";
// The vehicle's name in a sentence comes from `lib/rural`, which takes it from `lib/explain` -
// the module `/dashboard` words its answers with. This file had its own `a ${word}` copies and
// they printed "a ambulance" while the reason directly below said "an ambulance"; one vocabulary
// per run means one place that decides the article.
import { RURAL_VEHICLES, stopsPhrase, tooDeepPhrase } from "@/lib/rural";

/** `BRAND_MARK_64_SRC` in `components/varuna/wordmark.tsx`, written out so this route handler
 *  does not pull next/image in for one string; `html.test.ts` holds the two equal. */
export const BRAND_MARK = "/brand/varuna-mark-64.png";

/** What the page knows about the run it is quoting; every number on screen belongs to it. */
export interface RuralRunStamp {
  runId: string;
  /** The cycle's own instant, in IST, e.g. "06:40". */
  cycleTime: string;
  /** The date the replay is of, e.g. "2 July 2019". */
  cycleDate: string;
  mode: string;
  /** The honesty label for the source, e.g. "reconstructed replay". */
  label: string;
}

/** The city the advisory covers, named as its config names it. */
export interface RuralCity {
  id: string;
  name: string;
  code: string | null;
}

/** What the reader asked for, kept verbatim so the form and the share link echo it back. */
export interface RuralQuery {
  from: string;
  to: string;
  vehicle: RuralVehicle;
  run: string | null;
  at: string | null;
}

export type RuralBody =
  /** No trip asked for yet, or one this page could not resolve: the form plus the reason. */
  | { kind: "ask"; message: string | null }
  /** A point outside the built area. UI_SPEC 7 requires the "we do not know" answer, not a guess. */
  | { kind: "outside"; field: "from" | "to"; typed: string }
  /** A name the register does not hold, with whatever it nearly matched. */
  | { kind: "unknown"; field: "from" | "to"; typed: string; suggestions: string[] }
  /** The API could not answer; the page says what happened rather than showing an empty road. */
  | { kind: "upstream"; message: string }
  | { kind: "answer"; advisory: RuralAdvisory };

export interface RuralPage {
  city: RuralCity;
  run: RuralRunStamp | null;
  query: RuralQuery;
  body: RuralBody;
  /** The link printed at the foot, already carrying the whole query. */
  shareUrl: string | null;
}

const AMP = /&/g;
const LT = /</g;
const GT = />/g;
const QUOT = /"/g;

/** HTML-escapes text. Every value below is interpolated through this, including street names. */
export function esc(value: string): string {
  return value
    .replace(AMP, "&amp;")
    .replace(LT, "&lt;")
    .replace(GT, "&gt;")
    .replace(QUOT, "&quot;");
}

/**
 * The stylesheet, built from the tokens rather than written in hex.
 *
 * Kept to one declaration per line with no indentation: at this size the whitespace is a
 * measurable share of the page, and the budget is the feature.
 */
export function styles(): string {
  const font = `${tokens.font.sans.fallback}`;
  return [
    `:root{--ink:${colors.ink};--deep:${colors.deep};--line:${colors.line};--text:${colors.text};--text-2:${colors["text-2"]};--text-3:${colors["text-3"]};--tide:${colors.tide}}`,
    "*{box-sizing:border-box}",
    // `font` is `tokens.font.sans.fallback` - the token set's own system stack, "system-ui,
    // -apple-system, Segoe UI, sans-serif". The rule looks for a literal token class and cannot
    // see through the interpolation. Naming font-sans here would be the real violation: it would
    // pull Geist over a 2G connection, and UI_SPEC 7 asks this page to fetch no font at all.
    // lint-design-allow-next-line
    `body{margin:0;background:var(--ink);color:var(--text);font-family:${font};font-size:16px;line-height:1.5}`,
    "main{max-width:36rem;margin:0 auto;padding:16px 16px 40px}",
    "h1{display:flex;align-items:center;gap:8px;font-size:18px;font-weight:600;margin:0}",
    "h2{font-size:15px;font-weight:600;margin:0 0 8px;color:var(--text-2)}",
    "p{margin:0 0 8px}",
    "ul{margin:0 0 8px;padding-left:20px}",
    "li{margin:0 0 4px}",
    "a{color:var(--tide)}",
    "hr{border:0;border-top:1px solid var(--line);margin:20px 0}",
    "section{margin:20px 0 0}",
    ".sub{color:var(--text-2);font-size:14px;margin:4px 0 0}",
    ".muted{color:var(--text-3);font-size:14px}",
    ".lead{font-size:17px;font-weight:600}",
    ".depth{font-weight:600}",
    ".share{display:inline-block;min-height:44px;line-height:28px;padding:8px 0;word-break:break-all}",
    "label{display:block;margin:0 0 12px;color:var(--text-2);font-size:14px}",
    "input,select,button{display:block;width:100%;min-height:44px;margin-top:4px;padding:8px 10px;font:inherit;font-size:16px;color:var(--text);background:var(--deep);border:1px solid var(--line);border-radius:8px}",
    "button{background:var(--tide);color:var(--ink);font-weight:600;border-color:var(--tide);cursor:pointer}",
    ":focus-visible{outline:2px solid var(--tide);outline-offset:2px}",
    // Paper is white and ink is black; neither is a token, because the design system is a dark
    // screen and this rule exists for the officer who prints the page and reads it aloud.
    "@media print{body{background:white;color:black}a{color:black}.muted,.sub,h2{color:black}input,select,button{display:none}label{display:none}}",
  ].join("");
}

/** The whole document. */
export function renderRural(page: RuralPage): string {
  const title = `VARUNA road advisory - ${page.city.name}`;
  return [
    "<!doctype html>",
    '<html lang="en">',
    "<head>",
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width,initial-scale=1">',
    `<title>${esc(title)}</title>`,
    `<meta name="description" content="${esc(description(page))}">`,
    `<style>${styles()}</style>`,
    "</head>",
    "<body>",
    "<main>",
    header(page),
    body(page),
    dontKnow(page),
    share(page),
    form(page),
    "</main>",
    "</body>",
    "</html>",
  ].join("");
}

function description(page: RuralPage): string {
  const where = page.city.name;
  return `Road advisory for ${where}: which road is passable, until when, and the safer way. Text only.`;
}

function header(page: RuralPage): string {
  const stamp = page.run;
  const line = stamp
    ? `${page.city.name} - ${stamp.label} of ${stamp.cycleDate} - ${stamp.cycleTime} IST`
    : `${page.city.name} - no run loaded`;
  const runLine = stamp ? `<p class="muted">Run ${esc(stamp.runId)} - ${esc(stamp.mode)}</p>` : "";
  // The team's emblem, as every other screen's header carries it: the 64 px file (3 KB, drawn at
  // 32 px so a 2x phone gets every pixel) as a plain <img>, because this page has no image
  // optimiser and no script. alt is empty - the heading's own words already say VARUNA.
  return `<h1><img src="${BRAND_MARK}" width="32" height="32" alt="">VARUNA - road advisory</h1><p class="sub">${esc(line)}</p>${runLine}`;
}

function body(page: RuralPage): string {
  switch (page.body.kind) {
    case "answer":
      return answer(page.body.advisory);
    case "outside":
      return outside(page, page.body.field, page.body.typed);
    case "unknown":
      return unknown(page.body.field, page.body.typed, page.body.suggestions);
    case "upstream":
      return `<section><h2>We could not answer this trip</h2><p>${esc(page.body.message)}</p></section>`;
    case "ask":
    default:
      return ask(page.body.message);
  }
}

function ask(message: string | null): string {
  const note = message ? `<p>${esc(message)}</p>` : "";
  return [
    "<section>",
    "<h2>On your road</h2>",
    note,
    "<p>Name where you start and where you are going, pick your vehicle, and this page answers",
    " in words: which road, until when it stays passable, what stops you after that, and the",
    " safer way.</p>",
    "</section>",
  ].join("");
}

function outside(page: RuralPage, field: "from" | "to", typed: string): string {
  const where = field === "from" ? "start from" : "route to";
  return [
    "<section>",
    "<h2>Outside the area VARUNA has built</h2>",
    `<p>We cannot ${where} ${esc(typed)} - the point is outside the ${esc(page.city.name)}`,
    " area VARUNA has built. Pick a point inside it.</p>",
    "</section>",
  ].join("");
}

function unknown(field: "from" | "to", typed: string, suggestions: string[]): string {
  const which = field === "from" ? "start" : "destination";
  const list = suggestions.length
    ? `<p>Did you mean one of these?</p><ul>${suggestions.map((name) => `<li>${esc(name)}</li>`).join("")}</ul>`
    : "";
  return [
    "<section>",
    "<h2>We do not know that place</h2>",
    `<p>VARUNA holds no ${which} called ${esc(typed)}. Use a name from its register - a hospital,`,
    " a fire station or a chronic junction - or two numbers, longitude then latitude.</p>",
    list,
    "</section>",
  ].join("");
}

function answer(advisory: RuralAdvisory): string {
  const parts = [
    "<section>",
    "<h2>On your road</h2>",
    `<p class="lead">${esc(advisory.from.name)} to ${esc(advisory.to.name)}, by ${esc(advisory.vehicle.word)}</p>`,
    roadLine(advisory),
    passableLine(advisory),
    saferLine(advisory),
    etaLine(advisory),
    "</section>",
  ];
  const why = whySection(advisory);
  if (why) parts.push(why);
  const corridors = corridorSection(advisory);
  if (corridors) parts.push(corridors);
  return parts.join("");
}

function roadLine(advisory: RuralAdvisory): string {
  if (!advisory.yourRoad.length) {
    return '<p class="muted">This run does not name the streets on the shortest way.</p>';
  }
  return `<p>Shortest way: ${esc(advisory.yourRoad.join(", "))}.</p>`;
}

function passableLine(advisory: RuralAdvisory): string {
  const stop = advisory.stopper;
  const after = stop
    ? ` After that: <span class="depth" style="color:${depthColor(stop.depthCm)}">${stop.depthCm} cm</span>` +
      ` at ${esc(stop.street)} at ${esc(stop.at)} - ${esc(tooDeepPhrase(advisory.vehicle))}.`
    : ` On this cycle nothing on it rises above the depth that ${esc(stopsPhrase(advisory.vehicle))}.`;
  switch (advisory.passable.kind) {
    case "until":
      // lint-design-allow: UI_SPEC 7 prints this headline in capitals, for a small screen and for
      // an officer reading the page aloud; it is the one line a reader must not miss.
      return `<p class="lead">PASSABLE UNTIL ${esc(advisory.passable.time)}.</p><p>${after}</p>`;
    case "horizon":
      return (
        `<p class="lead">Passable for as long as this run forecasts, to ${esc(advisory.passable.time)}.</p>` +
        `<p>${after}</p>`
      );
    default:
      return `<p class="muted">This run does not say how long that road stays passable.</p><p>${after}</p>`;
  }
}

function saferLine(advisory: RuralAdvisory): string {
  if (advisory.sameRoad) {
    // No detour exists on this cycle, so the only advice left is the clock. Naming a vehicle
    // that could still cross would be a claim about the whole road from one street's depth.
    const leave =
      advisory.passable.kind === "until" ? ` Leave before ${esc(advisory.passable.time)}.` : "";
    return `<p>Safer: the shortest way is already the safe way on this cycle.${leave}</p>`;
  }
  const leave =
    advisory.passable.kind === "until" ? `leave before ${esc(advisory.passable.time)}, or ` : "";
  const via = advisory.detour ? `take ${esc(advisory.detour)}` : "take the safe way";
  const eta = advisory.eta ? ` (${esc(advisory.eta.safe)})` : "";
  return `<p>Safer: ${leave}${via}${eta}.</p>`;
}

function etaLine(advisory: RuralAdvisory): string {
  // Two ETAs are the comparison a reader is weighing (UI_SPEC 4). When both routes are the same
  // road they are the same number twice, and the line above has already said so.
  if (!advisory.eta || advisory.sameRoad) return "";
  return (
    `<p class="muted">Shortest way ${esc(advisory.eta.shortest)} - ` +
    `safe way ${esc(advisory.eta.safe)} (${esc(advisory.eta.difference)}).</p>`
  );
}

function whySection(advisory: RuralAdvisory): string {
  if (!advisory.reasons.length) return "";
  const items = advisory.reasons.map((reason) => `<li>${esc(reason.text)}</li>`).join("");
  return `<section><h2>Why this way</h2><ul>${items}</ul></section>`;
}

function corridorSection(advisory: RuralAdvisory): string {
  if (advisory.corridors.length < 2) return "";
  const items = advisory.corridors
    .map((corridor) => {
      const share = corridor.share ? ` - ${esc(corridor.share)}` : "";
      const minutes = corridor.minutes ? ` - ${esc(corridor.minutes)}` : "";
      const mine = corridor.assigned ? " - yours" : "";
      return `<li>Road ${esc(corridor.label)}${share}${minutes}${mine}</li>`;
    })
    .join("");
  const note = advisory.notes.find((line) => line.includes("policy"));
  const disclosure = note ? `<p class="muted">${esc(note)}</p>` : "";
  return `<section><h2>Other safe roads</h2><ul>${items}</ul>${disclosure}</section>`;
}

/** The block UI_SPEC 7 requires, verbatim in substance, on every state of the page. */
function dontKnow(page: RuralPage): string {
  return [
    "<section>",
    "<h2>What we do not know here</h2>",
    `<p>This advisory covers the built ${esc(page.city.name)} area. Outside it we have no drain map`,
    " and no forecast, and we will say so rather than guess.</p>",
    "</section>",
  ].join("");
}

function share(page: RuralPage): string {
  if (!page.shareUrl) return "";
  const url = esc(page.shareUrl);
  return `<hr><p>Share this: <a class="share" href="${url}">${url}</a></p>`;
}

/**
 * The trip form: a plain GET form, which is the only kind that works without JavaScript.
 *
 * The place fields are text rather than a list of the register's 396 entries, because that list
 * is about 16 KB of options and the budget for the whole page is 30 KB. A name that misses gets
 * the near-matches back, which costs nothing until it is needed.
 */
function form(page: RuralPage): string {
  const options = RURAL_VEHICLES.map((vehicle) => {
    const selected = vehicle.word === page.query.vehicle.word ? " selected" : "";
    return `<option value="${esc(vehicle.word)}"${selected}>${esc(vehicle.word)}</option>`;
  }).join("");
  const hidden = page.query.run
    ? `<input type="hidden" name="run" value="${esc(page.query.run)}">`
    : "";
  return [
    "<hr>",
    "<section><h2>Another trip</h2>",
    '<form method="get" action="/rural">',
    `<label for="from">From<input id="from" name="from" value="${esc(page.query.from)}" autocomplete="off"></label>`,
    `<label for="to">To<input id="to" name="to" value="${esc(page.query.to)}" autocomplete="off"></label>`,
    `<label for="v">Vehicle<select id="v" name="v">${options}</select></label>`,
    hidden,
    '<button type="submit">Show the advisory</button>',
    "</form>",
    '<p class="muted">A name from VARUNA\'s register, or longitude and latitude as two numbers.</p>',
    "</section>",
  ].join("");
}

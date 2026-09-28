// A Markdown summary of a Playwright JSON report for the CI job page (ADR-0043): the counts,
// every test that did not run with the reason it gave, and every failure with its first line.
//
// A skip is only honest if its reason is read, and the reasons otherwise live inside the HTML
// report of an artifact nobody downloads. Usage:
//   node tests/e2e/summary.mjs playwright-results.json >> "$GITHUB_STEP_SUMMARY"

import { existsSync, readFileSync } from "node:fs";

const file = process.argv[2] ?? "playwright-results.json";

if (!existsSync(file)) {
  console.log(
    `### Playwright\n\nNo results were written to \`${file}\`, so the run stopped before its ` +
      "reporters did. The step log above says why.",
  );
  process.exit(0);
}

const report = JSON.parse(readFileSync(file, "utf8"));
const { expected = 0, unexpected = 0, flaky = 0, skipped = 0 } = report.stats ?? {};

// Terminal colour codes in error messages: ESC, then "[", digits and semicolons, then "m".
const ANSI = new RegExp(`${String.fromCharCode(27)}[[][0-9;]*m`, "g");
const cell = (text) =>
  String(text).replace(ANSI, "").replaceAll("|", "\\|").replace(/\s+/g, " ").trim();

const notRun = [];
const failed = [];

function walk(suite, titles) {
  for (const spec of suite.specs ?? []) {
    const name = [...titles, spec.title].join(" › ");
    for (const test of spec.tests ?? []) {
      const annotations = [
        ...(test.annotations ?? []),
        ...(test.results ?? []).flatMap((result) => result.annotations ?? []),
      ];
      if (test.status === "skipped") {
        const reasons = [
          ...new Set(
            annotations
              .filter((a) => a.type === "skip" || a.type === "fixme")
              .map((a) => a.description || `${a.type}: the title names what is missing`),
          ),
        ];
        // A test with no results and no annotation was never started: the run was stopped by the
        // global timeout or by a failure limit, which is not the same thing as a reasoned skip.
        const why =
          reasons.length > 0
            ? reasons.join("; ")
            : "did not start: the run was stopped before reaching it (global timeout)";
        notRun.push(`| ${cell(name)} | ${cell(why)} |`);
      } else if (test.status === "unexpected") {
        const last = (test.results ?? []).at(-1);
        const message = (last?.error?.message ?? last?.status ?? "failed").split("\n")[0];
        failed.push(`| ${cell(name)} | ${cell(message)} |`);
      }
    }
  }
  for (const child of suite.suites ?? []) walk(child, [...titles, child.title]);
}

for (const suite of report.suites ?? []) walk(suite, [suite.title]);

const lines = [
  `### Playwright: ${expected} passed, ${unexpected} failed, ${flaky} flaky, ${skipped} not run`,
  "",
];
if (failed.length > 0) {
  lines.push("#### Failed", "", "| Test | First line of the error |", "|---|---|", ...failed, "");
}
if (notRun.length > 0) {
  lines.push("#### Not run", "", "| Test | Why |", "|---|---|", ...notRun, "");
}
console.log(lines.join("\n"));

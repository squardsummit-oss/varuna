/**
 * Every component ships with a story on /design (SPEC.md 6.6). This test reads the component
 * folders the design system owns and the story files of /design, and fails when a component is
 * exported from one of those folders and drawn nowhere on the page - so a new component cannot
 * land without its story, and a story cannot quietly be deleted.
 *
 * A component counts as covered when a story file renders it as a JSX element (`<Name`). The
 * exclusion list below is the only way out, and every entry says why.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const APP_ROOT = resolve(__dirname, "../../..");

/** The folders whose exported components must each have a story. */
const COMPONENT_FOLDERS = [
  "components/varuna",
  "components/pumps",
  "components/citizen",
  "components/authority",
] as const;

/** Where the stories live: the design page and every section file it renders. */
const STORY_DIRS = ["app/design", "app/design/_sections"] as const;

/**
 * Components exported from those folders that deliberately have no story of their own. Keep this
 * short: only layout frames with nothing to show outside a screen, things with no visible UI, and
 * overlays the page already opens from its own controls.
 */
const EXCLUDED: Record<string, string> = {
  // Layout-only: the frame every screen mounts in (top bar, rail, main). Its parts - TopBar,
  // IconRail, ModeBanner, RunStamp - each have a story; the shell itself only arranges them.
  AppShell: "layout-only frame; its parts are storied individually",
  // No UI: registers the service worker for /map and /report and renders null.
  SwRegister: "renders nothing",
  // Mounted once by lib/providers.tsx for the whole app, so /design already has one of each.
  // The "Overlays" panel opens them with its buttons; a second mount would fight the first for
  // the same store flag and the same keyboard shortcuts.
  CommandPalette: "mounted by lib/providers; opened from the Overlays panel",
  ShortcutsOverlay: "mounted by lib/providers; opened from the Overlays panel",
  SettingsDrawer: "mounted by lib/providers; opened from the Overlays panel",
};

/** PascalCase names a file exports as functions, consts or classes: the components. */
export function exportedComponents(source: string): string[] {
  const names = new Set<string>();
  const re = /^export\s+(?:default\s+)?(?:function|const|class)\s+([A-Z][a-z][A-Za-z0-9]*)/gm;
  for (const match of source.matchAll(re)) names.add(match[1]!);
  return [...names];
}

function listFiles(dir: string, pattern: RegExp): string[] {
  return readdirSync(join(APP_ROOT, dir), { withFileTypes: true })
    .filter((entry) => entry.isFile() && pattern.test(entry.name) && !/\.test\./.test(entry.name))
    .map((entry) => join(dir, entry.name));
}

function componentsByFile(): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const folder of COMPONENT_FOLDERS) {
    for (const file of listFiles(folder, /\.tsx$/)) {
      const names = exportedComponents(readFileSync(join(APP_ROOT, file), "utf8"));
      if (names.length > 0) out.set(file, names);
    }
  }
  return out;
}

function storySource(): string {
  return STORY_DIRS.flatMap((dir) => listFiles(dir, /\.tsx$/))
    .map((file) => readFileSync(join(APP_ROOT, file), "utf8"))
    .join("\n");
}

describe("design page story coverage", () => {
  const components = componentsByFile();
  const stories = storySource();
  const all = [...components.values()].flat();

  it("finds the component folders it guards", () => {
    // A path mistake would make every later assertion vacuous.
    expect(all.length).toBeGreaterThan(100);
    expect(all).toContain("HotspotDrawer");
    expect(all).toContain("DispatchMap");
    expect(all).toContain("CitizenInbox");
  });

  it("draws every exported component on /design, or says why not", () => {
    const missing: string[] = [];
    for (const [file, names] of components) {
      for (const name of names) {
        if (name in EXCLUDED) continue;
        if (!new RegExp(`<${name}[\\s/>]`).test(stories)) missing.push(`${name} (${file})`);
      }
    }
    expect(missing, `components with no story on /design:\n${missing.join("\n")}`).toEqual([]);
  });

  it("excludes only components that still exist and still have no story", () => {
    for (const name of Object.keys(EXCLUDED)) {
      expect(all, `${name} is excluded but no longer exported`).toContain(name);
      expect(
        new RegExp(`<${name}[\\s/>]`).test(stories),
        `${name} has a story now; take it off the exclusion list`,
      ).toBe(false);
    }
  });

  it("reads PascalCase components and skips constants", () => {
    expect(
      exportedComponents(
        [
          "export function DepthChip() {}",
          "export const PUMP_STATUSES = [];",
          "export default function JalayantraStories() {}",
          "export const LiveOutlookView = () => null;",
          "function Local() {}",
        ].join("\n"),
      ),
    ).toEqual(["DepthChip", "JalayantraStories", "LiveOutlookView"]);
  });
});

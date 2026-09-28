import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import manifest from "@/app/manifest";

const APP_ROOT = join(__dirname, "..");

/** Width and height from a PNG's IHDR chunk, which always starts at byte 16. */
function pngSize(publicPath: string): string {
  const bytes = readFileSync(join(APP_ROOT, "public", publicPath));
  expect(bytes.subarray(1, 4).toString("latin1"), `${publicPath} is a PNG`).toBe("PNG");
  return `${bytes.readUInt32BE(16)}x${bytes.readUInt32BE(20)}`;
}

/** The worker's `STATIC_FILES`, read out of the file the browser runs. */
function workerStaticFiles(): string[] {
  const source = readFileSync(join(APP_ROOT, "public", "sw.js"), "utf8");
  const block = /const STATIC_FILES = \[([^\]]*)\]/.exec(source);
  expect(block, "sw.js declares STATIC_FILES").not.toBeNull();
  return [...(block?.[1] ?? "").matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

describe("web app manifest", () => {
  const app = manifest();
  const icons = app.icons ?? [];

  it("lists the 192 and 512 install icons and a maskable 512, and no SVG", () => {
    expect(icons.map((i) => [i.src, i.sizes, i.type, i.purpose])).toEqual([
      ["/brand/varuna-app-icon-192.png", "192x192", "image/png", "any"],
      ["/brand/varuna-app-icon-512.png", "512x512", "image/png", "any"],
      ["/brand/varuna-app-icon-maskable-512.png", "512x512", "image/png", "maskable"],
    ]);
    expect(icons.some((i) => i.src.endsWith(".svg"))).toBe(false);
  });

  it("declares each icon at the size the file on disk really is", () => {
    for (const icon of icons) expect(pngSize(icon.src)).toBe(icon.sizes);
  });

  it("gives the Report shortcut the 192 px icon", () => {
    const report = app.shortcuts?.find((s) => s.url === "/report");
    expect(report?.icons).toEqual([
      { src: "/brand/varuna-app-icon-192.png", sizes: "192x192", type: "image/png" },
    ]);
  });

  it("has the service worker keep every icon it lists, so an offline install keeps its icon", () => {
    const kept = workerStaticFiles();
    for (const icon of icons) expect(kept).toContain(icon.src);
    expect(kept).toContain("/manifest.webmanifest");
  });
});

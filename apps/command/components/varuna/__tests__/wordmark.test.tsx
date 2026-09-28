import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  BRAND_LOCKUP_SIZE,
  BRAND_LOCKUP_SRC,
  BRAND_MARK_64_SRC,
  BrandLockup,
  markSrc,
  Wordmark,
  WordmarkMark,
} from "@/components/varuna/wordmark";

const APP_ROOT = join(__dirname, "..", "..", "..");

/** Width and height from a PNG's IHDR chunk, which always starts at byte 16. */
function pngSize(file: string): { width: number; height: number } {
  const bytes = readFileSync(join(APP_ROOT, file));
  expect(bytes.subarray(1, 4).toString("latin1"), `${file} is a PNG`).toBe("PNG");
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

describe("Wordmark", () => {
  it("draws the team's emblem, not the old three-wave SVG, beside the lettered name", () => {
    const { container } = render(<Wordmark size="sm" withMark />);
    const mark = screen.getByRole("img", { name: "VARUNA" });
    expect(mark.tagName).toBe("IMG");
    expect(mark.getAttribute("src")).toMatch(/varuna-mark-64\.png$/);
    expect(container.querySelector("svg")).toBeNull();
    expect(screen.getByText("VARUNA")).toHaveClass("font-display");
  });

  it("says the name once to a screen reader: the emblem carries it, the lettering is hidden", () => {
    render(<Wordmark size="sm" withMark />);
    expect(screen.getAllByRole("img", { name: "VARUNA" })).toHaveLength(1);
    expect(screen.getByText("VARUNA")).toHaveAttribute("aria-hidden", "true");
  });

  it("keeps the lettering readable to assistive technology when there is no emblem", () => {
    render(<Wordmark size="sm" withMark={false} />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText("VARUNA")).not.toHaveAttribute("aria-hidden");
  });

  it.each([
    ["sm", 32, "varuna-mark-64.png"],
    ["md", 48, "varuna-mark-192.png"],
    ["lg", 64, "varuna-mark-192.png"],
  ] as const)("draws %s at %i px from one file at least twice that size (%s)", (size, px, file) => {
    render(<Wordmark size={size} />);
    const mark = screen.getByRole("img", { name: "VARUNA" });
    expect(mark).toHaveAttribute("width", String(px));
    expect(mark).toHaveAttribute("height", String(px));
    expect(mark.getAttribute("src")?.endsWith(file)).toBe(true);
  });

  /*
   * The offline public map (P9.10) regressed once here: `/map` and `/report` drew the mark through
   * `/_next/image?url=...`, which `public/sw.js` never keeps, so an installed map opened offline
   * showed a broken image in its header. The mark is a static import now, one src and no srcset,
   * because the worker keeps `/_next/static/*` and finds a page's files by the quote in front of
   * each URL - a srcset's second candidate follows a comma and would never be stored.
   */
  it.each([16, 32, 48, 64, 96])(
    "never asks the image optimiser for the %i px mark, and names exactly one file",
    (px) => {
      render(<WordmarkMark size={px} />);
      const mark = screen.getByRole("img", { name: "VARUNA" });
      expect(mark.getAttribute("src")).not.toContain("/_next/image");
      expect(mark.getAttribute("src")).toBe(markSrc(px));
      expect(mark.hasAttribute("srcset")).toBe(false);
    },
  );
});

describe("WordmarkMark", () => {
  it("can stand alone with the name, or be decorative beside words that already say it", () => {
    const { rerender } = render(<WordmarkMark size={32} />);
    expect(screen.getByRole("img", { name: "VARUNA" })).toBeInTheDocument();
    rerender(<WordmarkMark size={32} alt="" />);
    expect(screen.queryByRole("img", { name: "VARUNA" })).toBeNull();
  });
});

describe("BrandLockup", () => {
  it("draws the full logo at its own proportions, named in its alt text", () => {
    render(<BrandLockup width={200} />);
    const logo = screen.getByRole("img", { name: "VARUNA" });
    expect(decodeURIComponent(logo.getAttribute("src") ?? "")).toContain(BRAND_LOCKUP_SRC);
    expect(logo).toHaveAttribute("width", "200");
    expect(logo).toHaveAttribute(
      "height",
      String(Math.round((200 * BRAND_LOCKUP_SIZE.height) / BRAND_LOCKUP_SIZE.width)),
    );
  });
});

describe("brand files", () => {
  /*
   * Every file here is written by `tools/brand_assets.py` from the team's logo; `--check` proves
   * the committed bytes are what the logo gives. Nothing else is kept: the byte copy of the
   * original and a 1200 px export were served from `public/` and loaded by nothing.
   */
  it("are on disk at the sizes the components, the icons and the manifest assume", () => {
    expect(pngSize("public/brand/varuna-mark-64.png")).toEqual({ width: 64, height: 64 });
    expect(pngSize("public/brand/varuna-mark-192.png")).toEqual({ width: 192, height: 192 });
    expect(pngSize(`public${BRAND_LOCKUP_SRC}`)).toEqual(BRAND_LOCKUP_SIZE);
    expect(pngSize("public/brand/varuna-app-icon-192.png")).toEqual({ width: 192, height: 192 });
    expect(pngSize("public/brand/varuna-app-icon-512.png")).toEqual({ width: 512, height: 512 });
    expect(pngSize("public/brand/varuna-app-icon-maskable-512.png")).toEqual({
      width: 512,
      height: 512,
    });
    expect(pngSize("app/apple-icon.png")).toEqual({ width: 180, height: 180 });
  });

  /*
   * A correct file on disk is not enough. Next drops file-based icons whenever a layout sets
   * `metadata.icons` itself, and layout.tsx does, so until it named the PNG as `apple` no page
   * carried <link rel="apple-touch-icon"> and iOS saved a screenshot of the page instead.
   */
  it("is named as the apple-touch-icon by layout.tsx, which sets metadata.icons itself", () => {
    const layout = readFileSync(join(APP_ROOT, "app/layout.tsx"), "utf8");
    expect(layout).toMatch(/icons:\s*\{[^}]*apple:\s*"\/apple-icon\.png"/);
  });

  it("names the 64 px file by the same path /rural writes into its plain HTML", () => {
    expect(BRAND_MARK_64_SRC).toBe("/brand/varuna-mark-64.png");
    expect(markSrc(32)).toMatch(/varuna-mark-64\.png$/);
  });

  it("keeps the 64 px mark light enough for /rural's 30 KB page", () => {
    const bytes = readFileSync(join(APP_ROOT, `public${BRAND_MARK_64_SRC}`)).length;
    expect(bytes).toBeLessThan(4 * 1024);
  });

  it("wraps the 192 px tile in icon.svg, the file layout.tsx and sw.js name", () => {
    const svg = readFileSync(join(APP_ROOT, "public/icon.svg"), "utf8");
    const tile = readFileSync(join(APP_ROOT, "public/brand/varuna-app-icon-192.png"));
    expect(svg).toContain(`data:image/png;base64,${tile.toString("base64")}`);
    expect(svg).toContain('aria-label="VARUNA"');
  });
});

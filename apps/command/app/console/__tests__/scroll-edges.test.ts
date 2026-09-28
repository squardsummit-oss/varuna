/**
 * The console layer column's "there is more below" affordance (chunk INTEGRATE, defect 4).
 *
 * jsdom does no layout, so `scrollHeight` and `clientHeight` are both zero and the hook has
 * nothing real to measure. What can be pinned here is the rule the hook feeds: a fade only on an
 * edge that is actually hiding something, and a mask rather than an overlay, so the clipped row is
 * made translucent instead of covered. The browser half is the screenshot at 1366 x 768.
 */

import { describe, expect, it } from "vitest";

import { edgeFadeStyle } from "@/app/console/use-scroll-edges";

describe("edgeFadeStyle", () => {
  it("leaves a fully visible column crisp", () => {
    expect(edgeFadeStyle({ above: false, below: false })).toBeUndefined();
  });

  it("fades only the bottom when only the bottom is clipped", () => {
    const style = edgeFadeStyle({ above: false, below: true });
    expect(style?.maskImage).toBe(
      "linear-gradient(to bottom, black 0, black calc(100% - 20px), transparent 100%)",
    );
    // Safari still wants the prefix, and a column that loses its mask there loses the affordance.
    expect(style?.WebkitMaskImage).toBe(style?.maskImage);
  });

  it("fades only the top once the column is scrolled to its end", () => {
    const style = edgeFadeStyle({ above: true, below: false });
    expect(style?.maskImage).toBe(
      "linear-gradient(to bottom, transparent 0, black 20px, black 100%)",
    );
  });

  it("fades both edges in the middle of a long column", () => {
    const style = edgeFadeStyle({ above: true, below: true });
    expect(style?.maskImage).toBe(
      "linear-gradient(to bottom, transparent 0, black 20px, black calc(100% - 20px), transparent 100%)",
    );
  });

  it("is a mask, never an overlay, so nothing it does can cover a row", () => {
    const style = edgeFadeStyle({ above: true, below: true }) ?? {};
    // No background, no colour, no element: the only keys are the mask and its prefix.
    expect(Object.keys(style).sort()).toEqual(["WebkitMaskImage", "maskImage"]);
  });
});

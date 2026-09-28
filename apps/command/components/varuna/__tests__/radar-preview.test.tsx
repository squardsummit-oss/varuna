/**
 * The storm preview's framing (motion M25's player, SPEC.md 7.8): it opens on the forecast area
 * and the rain around it, and one press shows the whole radar domain. The crop is plain arithmetic
 * on cube cells, pinned here on the two AOIs the served bundles carry.
 */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AREA_MARGIN,
  RadarPreview,
  radarFrameRect,
  rectInFrame,
  wholeInFrame,
} from "@/components/varuna/radar-preview";
import { renderWithProviders, stubFetch } from "@/lib/test-utils";

/** `aoi_px` as `GET /v1/replay/bundles/MUM-2019-07-02/radar` serves it (2026-09-28). */
const MUMBAI_AOI = { left: 50, top: 44, right: 71, bottom: 76, width: 21, height: 32 };
/** And for CHN-IDF-25yr. */
const CHENNAI_AOI = { left: 51, top: 50, right: 69, bottom: 71, width: 18, height: 21 };

function inside(
  inner: { left: number; top: number; width: number; height: number },
  outer: { left: number; top: number; width: number; height: number },
) {
  return (
    inner.left >= outer.left &&
    inner.top >= outer.top &&
    inner.left + inner.width <= outer.left + outer.width &&
    inner.top + inner.height <= outer.top + outer.height
  );
}

describe("radarFrameRect", () => {
  it("frames Mumbai's AOI with a quarter of its size around it, squared to the cube", () => {
    const rect = radarFrameRect(MUMBAI_AOI, 120, 120);
    // 32 cells tall x 1.5 = 48, and the cube is square, so 48 x 48 centred on (60.5, 60).
    expect(rect).toEqual({ left: 37, top: 36, width: 48, height: 48 });
    expect(inside(MUMBAI_AOI, rect)).toBe(true);
    // At least the margin on the long side: 8 cells above and below a 32-cell AOI.
    expect(MUMBAI_AOI.top - rect.top).toBeGreaterThanOrEqual(AREA_MARGIN * MUMBAI_AOI.height);
    expect(rect.top + rect.height - MUMBAI_AOI.bottom).toBeGreaterThanOrEqual(
      AREA_MARGIN * MUMBAI_AOI.height,
    );
    // The AOI goes from 4.7 % of the image to 29 %.
    expect((MUMBAI_AOI.width * MUMBAI_AOI.height) / (rect.width * rect.height)).toBeCloseTo(
      0.292,
      3,
    );
  });

  it("frames Chennai's AOI by the same rule", () => {
    // 21 x 1.5 = 31.5, a whole cell up to 32.
    expect(radarFrameRect(CHENNAI_AOI, 120, 120)).toEqual({
      left: 44,
      top: 45,
      width: 32,
      height: 32,
    });
  });

  it("returns the whole cube for the domain framing", () => {
    expect(radarFrameRect(MUMBAI_AOI, 120, 120, "domain")).toEqual({
      left: 0,
      top: 0,
      width: 120,
      height: 120,
    });
  });

  it("shifts a frame that would cross the cube's edge back inside it", () => {
    const corner = { left: 0, top: 110, right: 10, bottom: 120, width: 10, height: 10 };
    const rect = radarFrameRect(corner, 120, 120);
    expect(rect).toEqual({ left: 0, top: 105, width: 15, height: 15 });
    expect(inside(corner, rect)).toBe(true);
  });

  it("falls back to the whole cube when the frame would be as large as it", () => {
    const big = { left: 10, top: 10, right: 110, bottom: 110, width: 100, height: 100 };
    expect(radarFrameRect(big, 120, 120)).toEqual({ left: 0, top: 0, width: 120, height: 120 });
    const empty = { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
    expect(radarFrameRect(empty, 120, 120)).toEqual({ left: 0, top: 0, width: 120, height: 120 });
  });

  it("keeps a non-square cube's own ratio, so the page box never changes", () => {
    const rect = radarFrameRect(MUMBAI_AOI, 240, 120);
    expect(rect.width / rect.height).toBeCloseTo(2, 1);
    expect(inside(MUMBAI_AOI, rect)).toBe(true);
  });
});

describe("rectInFrame and wholeInFrame", () => {
  const frame = { left: 37, top: 36, width: 48, height: 48 };

  it("places the AOI outline in percentages of the framed cells", () => {
    const style = rectInFrame(MUMBAI_AOI, frame);
    expect(parseFloat(style.left)).toBeCloseTo((13 / 48) * 100, 6);
    expect(parseFloat(style.top)).toBeCloseTo((8 / 48) * 100, 6);
    expect(parseFloat(style.width)).toBeCloseTo((21 / 48) * 100, 6);
    expect(parseFloat(style.height)).toBeCloseTo((32 / 48) * 100, 6);
  });

  it("places the whole image so the framed cells fill the box", () => {
    const style = wholeInFrame(120, 120, frame);
    expect(parseFloat(style.left)).toBeCloseTo((-37 / 48) * 100, 6);
    expect(parseFloat(style.top)).toBeCloseTo((-36 / 48) * 100, 6);
    expect(parseFloat(style.width)).toBeCloseTo(250, 6);
    expect(parseFloat(style.height)).toBeCloseTo(250, 6);
    expect(wholeInFrame(120, 120, { left: 0, top: 0, width: 120, height: 120 })).toEqual({
      left: "0%",
      top: "0%",
      width: "100%",
      height: "100%",
    });
  });
});

const INDEX = {
  bundle_id: "MUM-2019-07-02",
  label: "Reconstructed replay",
  variable: "dbz",
  n_frames: 2,
  step_min: 10,
  t0: "2019-07-02T05:40:00+05:30",
  width: 120,
  height: 120,
  frames: [
    {
      index: 0,
      ts: "2019-07-02T05:40:00+05:30",
      url: "/v1/replay/bundles/MUM-2019-07-02/radar/0.png",
    },
    {
      index: 1,
      ts: "2019-07-02T05:50:00+05:30",
      url: "/v1/replay/bundles/MUM-2019-07-02/radar/1.png",
    },
  ],
  aoi_px: MUMBAI_AOI,
  ramp: [],
  accumulation: {
    url: "/v1/replay/bundles/MUM-2019-07-02/radar/accumulation.png",
    label: "Accumulation over the replay window, in mm",
    note: "Accumulated depth in mm, coloured by the rain-rate band edges.",
  },
};

describe("RadarPreview framing", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("opens on the forecast area and switches to the whole domain with one press", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch({ "/radar": INDEX, "/v1/replay/bundles": [] })));
    renderWithProviders(<RadarPreview bundleId="MUM-2019-07-02" built missingMembers={[]} />);

    const area = await screen.findByRole("button", { name: "Forecast area" });
    const domain = screen.getByRole("button", { name: "Whole radar domain" });
    expect(screen.getByRole("group", { name: "Radar preview extent" })).toBeInTheDocument();
    expect(area).toHaveAttribute("aria-pressed", "true");
    expect(domain).toHaveAttribute("aria-pressed", "false");

    // The accumulation beside the frames is cropped to the same 48 cells.
    const image = await screen.findByRole("img", { name: /Accumulation over the replay window/ });
    expect(parseFloat(image.style.width)).toBeCloseTo(250, 6);
    expect(parseFloat(image.style.left)).toBeCloseTo((-37 / 48) * 100, 6);

    await act(async () => {
      fireEvent.click(domain);
    });
    await waitFor(() => expect(domain).toHaveAttribute("aria-pressed", "true"));
    expect(area).toHaveAttribute("aria-pressed", "false");
    expect(image.style.width).toBe("100%");
    expect(image.style.left).toBe("0%");
  });
});

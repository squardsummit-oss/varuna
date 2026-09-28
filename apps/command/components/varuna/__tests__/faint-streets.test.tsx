import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import NotFound from "@/app/not-found";
import { DEFAULT_STREETS_BBOX, FaintStreets, drawStreets } from "@/components/varuna/faint-streets";

const layer = {
  type: "FeatureCollection",
  features: [
    {
      properties: { segment_id: "S0-000", class: "primary" },
      geometry: {
        type: "LineString",
        coordinates: [
          [72.84, 19.01],
          [72.845, 19.02],
        ],
      },
    },
    {
      properties: { segment_id: "S0-001", class: "residential" },
      geometry: {
        type: "LineString",
        coordinates: [
          [72.85, 19.03],
          [72.86, 19.031],
        ],
      },
    },
    // Not a line: skipped, and not counted.
    {
      properties: { segment_id: "S0-002" },
      geometry: { type: "Point", coordinates: [72.85, 19.0] },
    },
  ],
};

describe("drawStreets", () => {
  it("projects the box's north-west corner to the origin and weights roads by class", () => {
    const [west, , , north] = DEFAULT_STREETS_BBOX;
    const drawing = drawStreets(
      [
        {
          properties: { class: "trunk" },
          geometry: {
            type: "LineString",
            coordinates: [
              [west, north],
              [west + 0.01, north - 0.01],
            ],
          },
        },
      ],
      DEFAULT_STREETS_BBOX,
    );
    expect(drawing.paths.major.startsWith("M0.0 0.0L")).toBe(true);
    expect(drawing.paths.minor).toBe("");
    expect(drawing.segmentCount).toBe(1);
    // Landscape: 0.07 degrees of longitude at 19 N is wider than 0.05 degrees of latitude.
    expect(drawing.width).toBeGreaterThan(drawing.height);
  });

  it("counts only the features it could draw", () => {
    const drawing = drawStreets(layer.features, DEFAULT_STREETS_BBOX);
    expect(drawing.segmentCount).toBe(2);
    expect(drawing.paths.major).not.toBe("");
    expect(drawing.paths.minor).not.toBe("");
  });
});

describe("FaintStreets", () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the plain grid when the API is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const { container } = render(<FaintStreets />);
    const root = container.querySelector('[data-slot="faint-streets"]');
    await waitFor(() => expect(root).toHaveAttribute("data-state", "fallback"));
    expect(container.querySelector('[data-slot="faint-streets-grid"]')).not.toBeNull();
    expect(container.querySelector("svg")).toBeNull();
  });

  it("keeps the grid when the city is not built (404)", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 404 }));
    const { container } = render(<FaintStreets />);
    await waitFor(() =>
      expect(container.querySelector('[data-slot="faint-streets"]')).toHaveAttribute(
        "data-state",
        "fallback",
      ),
    );
  });

  it("draws the served streets inside the box it asked for", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(layer), { status: 200 }));
    const { container } = render(<FaintStreets />);
    await waitFor(() =>
      expect(container.querySelector('[data-slot="faint-streets-map"]')).not.toBeNull(),
    );
    expect(container.querySelectorAll("path").length).toBe(3);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      `/v1/city/mumbai/layers/segments?bbox=${DEFAULT_STREETS_BBOX.join(",")}`,
    );
    expect(container.querySelector('[data-slot="faint-streets-grid"]')).toBeNull();
  });
});

describe("NotFound", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline"))));
  afterEach(() => vi.unstubAllGlobals());

  it("says the street does not exist, links to the console and falls back to the grid", async () => {
    const { container } = render(<NotFound />);
    expect(
      screen.getByRole("heading", { name: "This street does not exist." }),
    ).toBeInTheDocument();
    // Base UI's Button renders the link with role="button"; the href is what makes it a link.
    expect(screen.getByText("Open the console").closest("a")).toHaveAttribute("href", "/console");
    await waitFor(() =>
      expect(container.querySelector('[data-slot="faint-streets"]')).toHaveAttribute(
        "data-state",
        "fallback",
      ),
    );
    // The picture is decoration: nothing in it is announced or focusable.
    expect(container.querySelector('[data-slot="faint-streets"]')).toHaveAttribute(
      "aria-hidden",
      "true",
    );
  });
});

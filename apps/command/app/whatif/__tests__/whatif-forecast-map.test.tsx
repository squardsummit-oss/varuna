/**
 * Kalpana before a what-if has run: the map slot draws the cycle the lab is set to, framed on its
 * main affected area at the run's peak, and says so in one sentence. Once an answer lands the
 * difference layer takes the slot back, as it always has.
 *
 * `FloodMap` is replaced by a stub that records its props and answers `onLoaded` and `onFrame` the
 * way the real one does once a run has loaded; the real map needs WebGL, which jsdom has not got.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { WhatIfScreen, forecastCaption, type ForecastShown } from "@/app/whatif/whatif-screen";
import type { AffectedFrame } from "@/lib/map/affected-bounds";
import { useRunStore } from "@/lib/stores/run";
import { useUiStore } from "@/lib/stores/ui";

import emulatorFixture from "@/lib/api/__tests__/whatif-emulator.fixture.json";

const nav = vi.hoisted(() => ({ params: new URLSearchParams() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/whatif",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => nav.params,
}));

const RUN_ID = emulatorFixture.emulator.run_id;

/** 36 five-minute steps from 08:45, the 08:40 cycle's valid times. */
const VALID_TS = Array.from({ length: 36 }, (_, i) => {
  const minutes = 8 * 60 + 45 + i * 5;
  const hh = String(Math.floor(minutes / 60)).padStart(2, "0");
  const mm = String(minutes % 60).padStart(2, "0");
  return `2019-07-02T${hh}:${mm}:00+05:30`;
});

/** What `affectedFrame` returns for a deep flood read at step 8, 09:25 IST. */
const FRAME: AffectedFrame = {
  bounds: [
    [72.83, 19.0],
    [72.9, 19.07],
  ],
  basis: "deep",
  thresholdCm: 15,
  step: 8,
  share: 0.7,
  lengthM: 118_500,
};

const recorded = vi.hoisted(() => ({ props: [] as Record<string, unknown>[] }));

vi.mock("@/components/map/flood-map", async () => {
  const React = await import("react");
  function FloodMap(props: Record<string, unknown>) {
    recorded.props.push(props);
    const { onLoaded, onFrame } = props as {
      onLoaded?: (run: unknown) => void;
      onFrame?: (frame: AffectedFrame | null) => void;
    };
    React.useEffect(() => {
      onLoaded?.({
        provenance: {
          runId: (props.runId as string | undefined) ?? RUN_ID,
          cycleTs: "2019-07-02T08:40:00+05:30",
          stepMin: 5,
        },
        validTs: VALID_TS,
      });
      onFrame?.(FRAME);
      // Once per mount, as the real map calls back once per loaded run.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return React.createElement("div", { "data-testid": "forecast-map" });
  }
  return { FloodMap };
});

/** The served street layer, holding the emulator fixture's segments so its answer can draw. */
const LAYER = {
  type: "FeatureCollection",
  features: ["S-A", "S-B", "S-C", "S-D", "S-E"].map((id, i) => ({
    type: "Feature",
    properties: { segment_id: id, name: null },
    geometry: {
      type: "LineString",
      coordinates: [
        [72.84 + i * 0.001, 19.01],
        [72.841 + i * 0.001, 19.011],
      ],
    },
  })),
};

function stubApi() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    if (url.endsWith("/v1/whatif")) return json(emulatorFixture.emulator);
    if (url.includes("/layers/segments")) return json(LAYER);
    if (url.includes("/v1/runs/")) return json({ run_id: RUN_ID, city: "mumbai" });
    return json({ runs: [], features: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderLab() {
  return render(
    <TooltipProvider>
      <WhatIfScreen />
    </TooltipProvider>,
  );
}

const CAPTION =
  "Before any change: the 08:40 IST cycle at its peak, 09:25 (+45 min). 118.5 km of street at " +
  "15 cm or more.";

describe("Kalpana before a what-if", () => {
  beforeEach(() => {
    nav.params = new URLSearchParams({ run: RUN_ID });
    recorded.props = [];
    useRunStore.getState().clear();
    useUiStore.getState().closeOverlays();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("draws the cycle's forecast framed on the affected area, at the frame's peak step", async () => {
    stubApi();
    renderLab();

    expect(screen.getByTestId("forecast-map")).toBeInTheDocument();
    expect(await screen.findByText(CAPTION)).toBeInTheDocument();

    const last = recorded.props.at(-1) ?? {};
    // The run the lab is set to, framed on its affected area, drawn at the step the frame read.
    expect(last).toMatchObject({ runId: RUN_ID, frameOn: "affected", step: 8, city: "mumbai" });
    // No empty state in the slot: the map is the answer to "what does the flood look like now".
    expect(screen.queryByText("No scenario run yet")).not.toBeInTheDocument();
    expect(screen.getByText("This cycle's forecast until a what-if runs.")).toBeInTheDocument();
    // The scenario line is still there, under the map's own sentence.
    expect(screen.getByText(/^Scenario ready to run:/)).toBeInTheDocument();
  });

  it("hands the slot to the difference layer once the emulator answers", async () => {
    stubApi();
    renderLab();
    await screen.findByText(CAPTION);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Run what-if" }));
    });
    await screen.findByText(emulatorFixture.emulator.summary);

    await waitFor(() => expect(screen.queryByTestId("forecast-map")).not.toBeInTheDocument());
    expect(screen.queryByText(CAPTION)).not.toBeInTheDocument();
    expect(
      screen.getByText("Change in peak depth per street: improved, worse, unchanged."),
    ).toBeInTheDocument();
  });
});

describe("forecastCaption", () => {
  const run: ForecastShown = {
    runId: RUN_ID,
    cycleTs: "2019-07-02T08:40:00+05:30",
    stepMin: 5,
    validTs: VALID_TS,
  };

  it("says nothing until both the run and its frame are known", () => {
    expect(forecastCaption(null, run)).toBeNull();
    expect(forecastCaption(FRAME, null)).toBeNull();
  });

  it("says the cycle, its peak and how much street is deep", () => {
    expect(forecastCaption({ ...FRAME, share: 1, lengthM: 850 }, run)).toBe(
      "Before any change: the 08:40 IST cycle at its peak, 09:25 (+45 min). 850 m of street at " +
        "15 cm or more.",
    );
  });

  it("names the fallbacks rather than inventing water", () => {
    const spots: AffectedFrame = { ...FRAME, basis: "hotspots", thresholdCm: null, step: null };
    expect(forecastCaption(spots, run)).toBe(
      "No street reaches 5 cm in the 08:40 IST cycle; the map shows its chronic spots.",
    );
    const aoi: AffectedFrame = { ...spots, basis: "aoi" };
    expect(forecastCaption(aoi, run)).toBe(
      "The 08:40 IST cycle has no wet street; the map shows the whole city.",
    );
  });
});

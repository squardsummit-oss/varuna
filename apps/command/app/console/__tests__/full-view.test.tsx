/**
 * Where the console's map opens, and its full view (the "fit by default" request).
 *
 * The map is mocked, as in `console-url.test.tsx`: jsdom has no GPU, and the assertions are about
 * what the console asks of the map - `frameOn`, `frameWhole`, `frameStep`, `frameHolds`,
 * `fitPadding`, `fitKey`, `keepViewOf` - and what it
 * tells the operator about the frame, not about deck.gl. The frame is
 * `lib/map/affected-bounds.ts`'s and the kept camera `layers/camera.ts`'s, each tested there.
 *
 * jsdom has no Fullscreen API, so by default full view takes the layout fallback; the API's own
 * path is exercised with `requestFullscreen` and `exitFullscreen` stubbed the way a browser answers.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ConsoleScreen,
  FIT_CLEARANCE,
  consoleFitPadding,
  frameCaption,
  leadMin,
} from "@/app/console/console-screen";
import type { SegmentPick } from "@/components/map/city-map";
import type { RunDepth } from "@/lib/api/run-depth";
import type { AffectedFrame } from "@/lib/map/affected-bounds";
import { useGlobalShortcuts } from "@/lib/shortcuts";
import { useReplayStore } from "@/lib/stores/replay";
import { useRunStore } from "@/lib/stores/run";
import { useScrubStore } from "@/lib/stores/scrub";
import { useUiStore } from "@/lib/stores/ui";

vi.mock("next/navigation", () => ({
  usePathname: () => "/console",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(mockQuery.value),
}));

const RUN_ID = "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked";
/** The console's query string; a test that needs another sets it and puts it back. */
const mockQuery = { value: `run=${RUN_ID}` };

/**
 * The 06:40 cycle's steps as the API serves them: 36, five minutes apart, the first at 06:45 -
 * step i is valid at the cycle plus (i + 1) x 5 min (`segments_wet.json` `valid_ts[0]`).
 */
const VALID_TS = Array.from({ length: 36 }, (_, i) => {
  const minutes = 6 * 60 + 45 + i * 5;
  const hh = String(Math.floor(minutes / 60)).padStart(2, "0");
  const mm = String(minutes % 60).padStart(2, "0");
  return `2019-07-02T${hh}:${mm}:00+05:30`;
});

const RUN: RunDepth = {
  provenance: {
    runId: RUN_ID,
    cycleTs: "2019-07-02T06:40:00+05:30",
    mode: "baked",
    bundle: "MUM-2019-07-02",
    nSteps: VALID_TS.length,
    stepMin: 5,
    ensembleN: 20,
    massBalanceErr: 1e-5,
    stageMs: {},
    aoiDepthBand: null,
    notes: [],
  },
  bounds: [
    [72.815, 18.995],
    [72.905, 19.135],
  ],
  frames: [],
  depthCm: new Map(),
  pGt: null,
  validTs: VALID_TS,
  nSegmentsTotal: 21_296,
} as unknown as RunDepth;

/**
 * The 06:40 cycle's own frame, as measured on 2026-09-28 with the 7 km window and the rail's top
 * five spots held: 86 % of the 22.7 km at 15 cm or more at 09:40, the run's last step.
 */
const FRAME: AffectedFrame = {
  bounds: [
    [72.8246, 19.0258],
    [72.905, 19.102],
  ],
  basis: "deep",
  thresholdCm: 15,
  step: 35,
  share: 0.86,
  lengthM: 22_713,
};

/** The same run framed whole (`WHOLE`), as full view asks: every deep street, so the AOI. */
const WHOLE_FRAME: AffectedFrame = {
  ...FRAME,
  bounds: [
    [72.815, 18.995],
    [72.905, 19.135],
  ],
  share: 1,
};

/**
 * Full view entered at +60 min (step 11, 07:40): measured on the 06:40 cycle on 2026-09-28, the
 * streets at 15 cm or more then are 2.1 km, framed whole. At step 0 (06:45) no street is 5 cm deep
 * on that cycle, so `affectedFrameAt` frames the peak, `WHOLE_FRAME`.
 */
const STEP_11_FRAME: AffectedFrame = {
  ...WHOLE_FRAME,
  bounds: [
    [72.815, 19.03],
    [72.905, 19.131],
  ],
  step: 11,
  lengthM: 2_100,
};

/** What the mocked map reports for the frame it was asked for, as `FloodMap` would. */
function frameFor(frameWhole: boolean | undefined, frameStep: number | null | undefined) {
  if (!frameWhole) return FRAME;
  return frameStep === 11 ? STEP_11_FRAME : WHOLE_FRAME;
}

/** A street as the map hands it to the console on a click. */
const PICK: SegmentPick = {
  segment: {
    id: "S1",
    path: [
      [72.842, 19.01],
      [72.843, 19.011],
    ],
    depthCm: VALID_TS.map((_, i) => i),
    width: 3,
    displayName: "Dr Babasaheb Ambedkar Road",
  } as SegmentPick["segment"],
  x: 200,
  y: 200,
};

/** What the map was last asked to open on. */
const map = vi.hoisted(() => ({
  frameOn: "",
  fitKey: "",
  keepViewOf: "",
  frameWhole: "",
  frameStep: "",
  frameHolds: "",
  fitPadding: "",
}));

vi.mock("@/components/map/flood-map", () => ({
  FloodMap: (props: {
    frameOn?: string;
    frameWhole?: boolean;
    frameStep?: number | null;
    frameHolds?: number;
    fitPadding?: unknown;
    fitKey?: string | number | null;
    keepViewOf?: string | number | null;
    onLoaded?: (run: RunDepth) => void;
    onFrame?: (frame: AffectedFrame | null) => void;
    onSegmentPick?: (pick: SegmentPick | null) => void;
  }) => {
    map.frameOn = props.frameOn ?? "";
    map.fitKey = String(props.fitKey ?? "");
    map.keepViewOf = String(props.keepViewOf ?? "");
    map.frameWhole = String(props.frameWhole ?? "");
    map.frameStep = String(props.frameStep ?? "");
    map.frameHolds = String(props.frameHolds ?? "");
    map.fitPadding = JSON.stringify(props.fitPadding ?? null);
    const { onLoaded, onFrame, onSegmentPick, frameWhole, frameStep } = props;
    useEffect(() => {
      onLoaded?.(RUN);
      onFrame?.(frameFor(frameWhole, frameStep));
    }, [onLoaded, onFrame, frameWhole, frameStep]);
    return (
      <div data-testid="flood-map" data-frame-on={props.frameOn}>
        <button type="button" onClick={() => onSegmentPick?.(PICK)}>
          Pick a street
        </button>
      </div>
    );
  },
}));

/** The app's global key handler, which the providers mount on every real page. */
function Keys() {
  useGlobalShortcuts();
  return null;
}

function renderConsole() {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response("{}", { status: 404 }))),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Keys />
      <ConsoleScreen />
    </QueryClientProvider>,
  );
}

const rightRail = () => document.querySelector('[data-slot="right-rail"]');
// The shell wraps its bar so it can make it inert without reaching into it (`chromeInert`).
const topBar = () => document.querySelector('[data-slot="app-shell"] > [data-slot="top-bar"]');
const region = () => screen.getByTestId("console-map-region");
const control = () => screen.getByRole("button", { name: "Full view" });

describe("leadMin", () => {
  it("reads the lead from the cycle, not from the step index", () => {
    // The 06:40 cycle's first step is 06:45: +5 min, and its last, 09:40, is +180 min.
    expect(leadMin(RUN, 0)).toBe(5);
    expect(leadMin(RUN, 11)).toBe(60);
    expect(leadMin(RUN, 35)).toBe(180);
  });

  it("counts from one step before the first valid time when the run has no cycle time", () => {
    const noCycle = { ...RUN, provenance: { ...RUN.provenance, cycleTs: null } };
    expect(leadMin(noCycle, 0)).toBe(5);
    expect(leadMin(noCycle, 35)).toBe(180);
  });
});

describe("frameCaption", () => {
  it("says what share of the water the frame holds, with the time it was read at", () => {
    expect(frameCaption(FRAME, RUN)).toBe(
      "Opened on the densest water and the rail's top 5 spots: 86 % of the 22.7 km of streets at 15 cm or more at 09:40 (+180 min). Full view (F) shows all of it.",
    );
  });

  it("says when the frame holds all of it", () => {
    expect(frameCaption({ ...FRAME, share: 1 }, RUN)).toBe(
      "Opened on all 22.7 km of streets at 15 cm or more at 09:40 (+180 min).",
    );
  });

  it("names the fallbacks instead of printing a share nobody measured", () => {
    expect(frameCaption({ ...FRAME, basis: "hotspots" }, RUN)).toBe(
      "No street reaches 5 cm in this run, so the map opened on its chronic spots.",
    );
    expect(frameCaption({ ...FRAME, basis: "aoi" }, RUN)).toBe(
      "Nothing in this run is wet, so the map shows the whole city.",
    );
    expect(frameCaption(null, RUN)).toBeNull();
  });

  it("in full view, says it holds all of the water and names the way back", () => {
    expect(frameCaption(WHOLE_FRAME, RUN, true)).toBe(
      "Full view: all 22.7 km of streets at 15 cm or more at 09:40 (+180 min). F or Esc goes back.",
    );
    expect(frameCaption({ ...FRAME, basis: "hotspots", step: null }, RUN, true)).toBe(
      "Full view: no street reaches 5 cm, so the chronic spots. F or Esc goes back.",
    );
    expect(frameCaption({ ...FRAME, basis: "aoi", step: null }, RUN, true)).toBe(
      "Full view: nothing is wet, so the whole city. F or Esc goes back.",
    );
  });

  it("in full view, names the step it opened on, or says it had to frame the peak", () => {
    expect(frameCaption(STEP_11_FRAME, RUN, true, 11)).toBe(
      "Full view: all 2.1 km of streets at 15 cm or more at 07:40 (+60 min). F or Esc goes back.",
    );
    expect(frameCaption(WHOLE_FRAME, RUN, true, 0)).toBe(
      "Full view: under 200 m wet at 06:45 (+5 min), so the peak: all 22.7 km of streets at " +
        "15 cm or more at 09:40 (+180 min). F or Esc goes back.",
    );
    // The normal view never reads the step it is asked about: it is always the peak.
    expect(frameCaption(FRAME, RUN, false, 0)).toBe(frameCaption(FRAME, RUN));
  });
});

describe("consoleFitPadding", () => {
  it("clears the layer column and the scrub card, and the replay panel when it is open", () => {
    const { column, scrub, replay, edge } = FIT_CLEARANCE;
    expect(consoleFitPadding(false, false)).toEqual({
      top: edge,
      right: edge,
      bottom: scrub,
      left: column,
    });
    expect(consoleFitPadding(false, true).right).toBe(replay);
  });

  it("in full view clears only the scrub card", () => {
    const { scrub, edge } = FIT_CLEARANCE;
    expect(consoleFitPadding(true, true)).toEqual({
      top: edge,
      right: edge,
      bottom: scrub,
      left: edge,
    });
  });
});

describe("the console's full view", () => {
  beforeEach(() => {
    map.frameOn = "";
    map.fitKey = "";
    map.keepViewOf = "";
    map.frameWhole = "";
    map.frameStep = "";
    map.frameHolds = "";
    map.fitPadding = "";
    useRunStore.getState().clear();
    useUiStore.getState().closeOverlays();
    // The scrub is the replay store's lead, which outlives a render: every test starts at +0.
    useReplayStore.getState().reset();
    useScrubStore.getState().pause();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("opens on the affected area at the run's peak and says so", async () => {
    renderConsole();
    await screen.findByRole("button", { name: "Full view" });
    expect(control()).toHaveAttribute("aria-pressed", "false");
    expect(map.frameOn).toBe("affected");
    expect(map.frameWhole).toBe("false");
    expect(map.frameHolds).toBe("5");
    expect(map.fitPadding).toBe(JSON.stringify(consoleFitPadding(false, false)));
    expect(map.fitKey).toBe("affected");
    expect(map.keepViewOf).toBe("affected");
    expect(region()).toHaveAttribute("data-full-view", "off");
    // The frame is not narrated outside full view: the time bar is the map's only caption there.
    expect(screen.queryByTestId("console-frame")).toBeNull();
    expect(screen.queryByTestId("console-scrub")).toBeNull();
  });

  it("without the Fullscreen API, pins the map over the viewport and comes back", async () => {
    renderConsole();
    await screen.findByRole("button", { name: "Full view" });
    await waitFor(() => expect(useRunStore.getState().currentRun).not.toBeNull());
    // Scrub to +60 min first: full view frames the water at the step on screen.
    act(() => {
      useReplayStore.getState().setLeadMin(60);
    });

    fireEvent.click(control());
    await waitFor(() => expect(region()).toHaveAttribute("data-full-view", "layout"));
    expect(region()).toHaveClass("fixed", "inset-0");
    expect(control()).toHaveAttribute("aria-pressed", "true");
    expect(control()).toHaveFocus();
    expect(map.frameWhole).toBe("true");
    expect(map.frameStep).toBe("11");
    expect(map.fitPadding).toBe(JSON.stringify(consoleFitPadding(true, false)));
    expect(map.fitKey).toBe("full-view");
    await waitFor(() =>
      expect(screen.getByTestId("console-frame")).toHaveTextContent(
        "Full view: all 2.1 km of streets at 15 cm or more at 07:40 (+60 min).",
      ),
    );

    // Scrubbing inside full view never moves the camera: the frame stays at the step it opened on.
    // Two plain arrows are 15 minutes each (SPEC.md 6.10): +60 to +90, step 17.
    act(() => {
      for (let i = 0; i < 2; i += 1) fireEvent.keyDown(window, { key: "ArrowRight" });
    });
    const inMap = within(screen.getByTestId("console-scrub"));
    expect(inMap.getByRole("slider", { name: "Scrub the forecast" })).toHaveValue("17");
    expect(map.frameStep).toBe("11");
    expect(map.fitKey).toBe("full-view");
    // The rail is covered, not unmounted, and neither it nor the top bar can take focus.
    expect(rightRail()).not.toBeNull();
    expect(rightRail()).toHaveAttribute("inert");
    expect(topBar()).toHaveAttribute("inert");
    expect(screen.getByTestId("console-map-column")).toHaveClass("hidden");

    fireEvent.click(control());
    await waitFor(() => expect(region()).toHaveAttribute("data-full-view", "off"));
    expect(region()).not.toHaveClass("fixed");
    expect(control()).toHaveAttribute("aria-pressed", "false");
    expect(control()).toHaveFocus();
    expect(map.fitKey).toBe("affected");
    expect(map.frameWhole).toBe("false");
    expect(map.frameStep).toBe("");
    expect(rightRail()).not.toHaveAttribute("inert");
    expect(topBar()).not.toHaveAttribute("inert");
    expect(screen.getByTestId("console-map-column")).not.toHaveClass("hidden");
  });

  it("toggles on F and leaves on Escape", async () => {
    renderConsole();
    await screen.findByRole("button", { name: "Full view" });
    await waitFor(() => expect(useRunStore.getState().currentRun).not.toBeNull());

    act(() => {
      fireEvent.keyDown(window, { key: "f" });
    });
    await waitFor(() => expect(control()).toHaveAttribute("aria-pressed", "true"));
    // Opened at 06:45, where the 06:40 cycle has no water yet: it says so and frames the peak.
    expect(map.frameStep).toBe("0");
    await waitFor(() =>
      expect(screen.getByTestId("console-frame")).toHaveTextContent(
        "Full view: under 200 m wet at 06:45 (+5 min), so the peak",
      ),
    );

    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    await waitFor(() => expect(control()).toHaveAttribute("aria-pressed", "false"));
    expect(rightRail()).not.toHaveAttribute("inert");

    // F again goes in, and F again comes out.
    act(() => {
      fireEvent.keyDown(window, { key: "f" });
    });
    await waitFor(() => expect(control()).toHaveAttribute("aria-pressed", "true"));
    act(() => {
      fireEvent.keyDown(window, { key: "f" });
    });
    await waitFor(() => expect(control()).toHaveAttribute("aria-pressed", "false"));
  });

  it("F and Escape still work from the scrub, which holds focus after a drag", async () => {
    renderConsole();
    await screen.findByRole("button", { name: "Full view" });
    await waitFor(() => expect(useRunStore.getState().currentRun).not.toBeNull());

    // Outside full view the scrub is the time bar's.
    const barScrub = () =>
      document.querySelector<HTMLInputElement>('[data-slot="slider-thumb"] input') ??
      document.querySelector<HTMLElement>('[data-slot="slider-thumb"]');
    const bar = barScrub();
    expect(bar).not.toBeNull();
    bar!.focus();
    act(() => {
      fireEvent.keyDown(bar!, { key: "f" });
    });
    await waitFor(() => expect(control()).toHaveAttribute("aria-pressed", "true"));

    // In full view the map carries its own; the operator drags it, then leaves.
    const inMap = within(screen.getByTestId("console-scrub")).getByRole("slider", {
      name: "Scrub the forecast",
    });
    inMap.focus();
    act(() => {
      fireEvent.keyDown(inMap, { key: "Escape" });
    });
    await waitFor(() => expect(control()).toHaveAttribute("aria-pressed", "false"));

    act(() => {
      fireEvent.keyDown(window, { key: "f" });
    });
    await waitFor(() => expect(control()).toHaveAttribute("aria-pressed", "true"));
    const again = within(screen.getByTestId("console-scrub")).getByRole("slider", {
      name: "Scrub the forecast",
    });
    again.focus();
    act(() => {
      fireEvent.keyDown(again, { key: "f" });
    });
    await waitFor(() => expect(control()).toHaveAttribute("aria-pressed", "false"));
  });

  it("the time bar and the map's scrub are one clock", async () => {
    renderConsole();
    await screen.findByRole("button", { name: "Full view" });
    await waitFor(() => expect(useRunStore.getState().currentRun).not.toBeNull());
    // The store opens at +0, where no run has a step; the scrub lands on the first one.
    await waitFor(() => expect(useReplayStore.getState().leadMin).toBe(5));

    act(() => {
      useReplayStore.getState().setLeadMin(60);
    });
    fireEvent.click(control());
    await waitFor(() => expect(control()).toHaveAttribute("aria-pressed", "true"));
    const inMap = within(screen.getByTestId("console-scrub")).getByRole("slider", {
      name: "Scrub the forecast",
    });
    expect(inMap).toHaveValue("11");
    fireEvent.change(inMap, { target: { value: "17" } });
    expect(useReplayStore.getState().leadMin).toBe(90);
  });

  it("?autoplay=1 presses Play once the run's steps are known", async () => {
    mockQuery.value = `run=${RUN_ID}&autoplay=1`;
    try {
      renderConsole();
      await waitFor(() => expect(useRunStore.getState().currentRun).not.toBeNull());
      await waitFor(() => expect(useScrubStore.getState().playing).toBe(true));
    } finally {
      mockQuery.value = `run=${RUN_ID}`;
      act(() => useScrubStore.getState().pause());
    }
  });

  it("in full view a clicked street still answers and probability mode keeps its legend", async () => {
    renderConsole();
    await screen.findByRole("button", { name: "Full view" });
    await waitFor(() => expect(useRunStore.getState().currentRun).not.toBeNull());
    fireEvent.click(control());
    await waitFor(() => expect(control()).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByTestId("console-map-column")).toHaveClass("hidden");

    fireEvent.click(screen.getByRole("button", { name: "Pick a street" }));
    act(() => {
      fireEvent.keyDown(window, { key: "p" });
    });
    const overlays = await screen.findByTestId("console-full-view-overlays");
    expect(
      within(overlays).getByRole("complementary", { name: "Segment Dr Babasaheb Ambedkar Road" }),
    ).toBeVisible();
    expect(within(overlays).getByLabelText("Probability legend")).toBeVisible();

    // Back in the layout they return to the column, once each.
    fireEvent.click(control());
    await waitFor(() => expect(control()).toHaveAttribute("aria-pressed", "false"));
    expect(screen.queryByTestId("console-full-view-overlays")).toBeNull();
    const column = screen.getByTestId("console-map-column");
    expect(within(column).getAllByLabelText("Probability legend")).toHaveLength(1);
    expect(
      within(column).getByRole("complementary", { name: "Segment Dr Babasaheb Ambedkar Road" }),
    ).toBeInTheDocument();
  });

  it("an Escape that closes the shortcuts overlay does not also leave full view", async () => {
    renderConsole();
    await screen.findByRole("button", { name: "Full view" });
    fireEvent.click(control());
    await waitFor(() => expect(control()).toHaveAttribute("aria-pressed", "true"));

    act(() => useUiStore.getState().toggleShortcuts());
    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(useUiStore.getState().shortcutsOpen).toBe(false);
    expect(control()).toHaveAttribute("aria-pressed", "true");
  });

  describe("with the Fullscreen API", () => {
    let fullscreenElement: Element | null = null;
    const exitFullscreen = vi.fn(() => {
      fullscreenElement = null;
      document.dispatchEvent(new Event("fullscreenchange"));
      return Promise.resolve();
    });
    // The element asked for is the one the region's ref points at, which is the only element in
    // the console that asks.
    const requestFullscreen = vi.fn((): Promise<void> => {
      fullscreenElement = screen.getByTestId("console-map-region");
      document.dispatchEvent(new Event("fullscreenchange"));
      return Promise.resolve();
    });

    beforeEach(() => {
      fullscreenElement = null;
      exitFullscreen.mockClear();
      requestFullscreen.mockClear();
      Object.defineProperty(document, "fullscreenEnabled", { configurable: true, value: true });
      Object.defineProperty(document, "fullscreenElement", {
        configurable: true,
        get: () => fullscreenElement,
      });
      Object.defineProperty(document, "exitFullscreen", {
        configurable: true,
        value: exitFullscreen,
      });
      Object.defineProperty(HTMLElement.prototype, "requestFullscreen", {
        configurable: true,
        value: requestFullscreen,
      });
    });

    afterEach(() => {
      const doc = document as unknown as Record<string, unknown>;
      delete doc.fullscreenEnabled;
      delete doc.fullscreenElement;
      delete doc.exitFullscreen;
      delete (HTMLElement.prototype as unknown as Record<string, unknown>).requestFullscreen;
    });

    it("asks for the map region itself, and the browser's Escape brings it back", async () => {
      renderConsole();
      await screen.findByRole("button", { name: "Full view" });
      fireEvent.click(control());
      await waitFor(() => expect(region()).toHaveAttribute("data-full-view", "screen"));
      expect(requestFullscreen).toHaveBeenCalledTimes(1);
      expect(requestFullscreen.mock.contexts[0]).toBe(region());
      // In the browser's full screen the region keeps its own classes; the browser sizes it.
      expect(region()).not.toHaveClass("fixed");

      // The browser takes Escape itself and only says that the full screen ended.
      act(() => {
        fullscreenElement = null;
        document.dispatchEvent(new Event("fullscreenchange"));
      });
      await waitFor(() => expect(region()).toHaveAttribute("data-full-view", "off"));
      expect(control()).toHaveAttribute("aria-pressed", "false");
    });

    it("the control leaves the browser's full screen too", async () => {
      renderConsole();
      await screen.findByRole("button", { name: "Full view" });
      fireEvent.click(control());
      await waitFor(() => expect(region()).toHaveAttribute("data-full-view", "screen"));
      fireEvent.click(control());
      await waitFor(() => expect(region()).toHaveAttribute("data-full-view", "off"));
      expect(exitFullscreen).toHaveBeenCalledTimes(1);
    });

    it("falls back to the layout when the browser refuses", async () => {
      requestFullscreen.mockImplementationOnce(() =>
        Promise.reject(new TypeError("Permissions check failed")),
      );
      renderConsole();
      await screen.findByRole("button", { name: "Full view" });
      fireEvent.click(control());
      await waitFor(() => expect(region()).toHaveAttribute("data-full-view", "layout"));
      expect(region()).toHaveClass("fixed", "inset-0");
    });

    it("drops to the layout when an overlay the full screen would hide is opened", async () => {
      renderConsole();
      await screen.findByRole("button", { name: "Full view" });
      fireEvent.click(control());
      await waitFor(() => expect(region()).toHaveAttribute("data-full-view", "screen"));

      act(() => useUiStore.getState().toggleShortcuts());
      await waitFor(() => expect(region()).toHaveAttribute("data-full-view", "layout"));
      expect(exitFullscreen).toHaveBeenCalledTimes(1);
      expect(control()).toHaveAttribute("aria-pressed", "true");
    });
  });
});

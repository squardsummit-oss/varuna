import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReplayScreen } from "@/app/replay/replay-screen";
import { renderWithProviders, stubFetch } from "@/lib/test-utils";
import { formatMs } from "@/lib/format";
import { useReplayStore } from "@/lib/stores/replay";

vi.mock("next/navigation", () => ({
  usePathname: () => "/replay",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

const DEMO = {
  id: "MUM-2019-07-02",
  city: "mumbai",
  label: "Reconstructed replay",
  t0: "2019-07-02T05:40:00+05:30",
  t1: "2019-07-02T09:40:00+05:30",
  seed: 2019,
  built: true,
  missing_members: [],
  baked: false,
  baked_cycles: 0,
  total_cycles: 49,
  sources_n: 3,
  ground_truth_n: 29,
  synthetic_notes: ["Radar frames: storm-designer reconstruction."],
  description: "Reconstructed replay of the 2 July 2019 morning.",
};

const CHENNAI = {
  ...DEMO,
  id: "CHN-IDF-25yr",
  city: "chennai",
  label: "Design storm",
  t0: "2026-11-05T05:40:00+05:30",
  t1: "2026-11-05T08:40:00+05:30",
  built: false,
  missing_members: ["radar/frames.zarr", "truth/rain.zarr"],
  total_cycles: 37,
  ground_truth_n: 0,
  synthetic_notes: ["Design storm: a synthetic scenario, not a recorded event."],
  description: null,
};

const CLOCK = {
  bundle_id: DEMO.id,
  sim_time: DEMO.t0,
  playing: false,
  speed: 30,
  t0: DEMO.t0,
  t1: DEMO.t1,
  cycle_index: 0,
  n_cycles: 49,
  mode: "baked",
  last_run_id: null,
  next_cycle_ts: "2019-07-02T05:45:00+05:30",
  note: null,
  progress: 0,
};

const RUN_0640 = "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked";
const RUN_0710 = "MUM-20190702T0140Z-sky1.0-twin1.0-flash0.1-baked";

/** Two baked runs as `GET /v1/runs` lists them, and one as `GET /v1/runs/{id}` returns it. */
const RUNS = {
  runs: [
    {
      run_id: RUN_0710,
      bundle: DEMO.id,
      cycle_ts: "2019-07-02T07:10:00+05:30",
      total_ms: 55_500,
      mass_balance_err: 1e-6,
    },
    {
      run_id: RUN_0640,
      bundle: DEMO.id,
      cycle_ts: "2019-07-02T06:40:00+05:30",
      total_ms: 70_547,
      mass_balance_err: 2e-6,
    },
  ],
};

const RUN_0640_META = {
  run_id: RUN_0640,
  city: "mumbai",
  cycle_ts: "2019-07-02T06:40:00+05:30",
  mode: "baked",
  bundle: DEMO.id,
  stage_ms: { sky: 3151, twin: 59979, flash: 2370, pulse: 1941, products: 3106, provenance: 14 },
  total_ms: 70_547,
};

function stub(bundles: unknown[] = [DEMO, CHENNAI], extra: Record<string, unknown> = {}) {
  const fetchMock = vi.fn(
    stubFetch({
      // An empty registry answers 200 with no runs; a 404 here would be the log's error state.
      "/v1/runs": { runs: [] },
      ...extra,
      "/v1/replay/bundles": bundles,
      "/v1/replay/clock": CLOCK,
      "/v1/replay/bundle": CLOCK,
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("ReplayScreen", () => {
  beforeEach(() => {
    useReplayStore.getState().reset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    useReplayStore.getState().reset();
  });

  it("lists the bundles the API reports, not a hard-coded set", async () => {
    stub();
    renderWithProviders(<ReplayScreen />);

    expect(screen.getByRole("heading", { level: 1, name: "Smriti" })).toBeInTheDocument();
    // The Sanskrit name is answered on the page: its English gloss sits under the title (ADR-0085).
    expect(screen.getByRole("heading", { level: 1, name: "Smriti" })).toHaveAccessibleDescription(
      /^Replay/,
    );
    expect(await screen.findByRole("button", { name: /MUM-2019-07-02/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /CHN-IDF-25yr/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /MUM-2019-07-02/ })).toHaveTextContent(
      "2 July 2019, 05:40 to 09:40 IST",
    );
  });

  it("labels the header with the selected bundle's kind and names no event of its own", async () => {
    // Section 6.8 and rule 7: the header's first statement about the replay is its honesty label,
    // and the Sanskrit gloss under it neither claims fidelity nor names 2 July 2019, because the
    // same screen plays the design storms.
    stub();
    renderWithProviders(<ReplayScreen />);

    const heading = screen.getByRole("heading", { level: 1, name: "Smriti" });
    expect(heading).not.toHaveAccessibleDescription(/2 July|remembered/);
    const header = heading.parentElement as HTMLElement;
    expect(await within(header).findByText("Reconstructed replay")).toBeInTheDocument();

    // Read straight after the click: the stubbed clock still names the demo bundle, so its next
    // poll points the store back at it.
    fireEvent.click(screen.getByRole("button", { name: /CHN-IDF-25yr/ }));
    expect(within(header).getByText("Design storm")).toBeInTheDocument();
    expect(within(header).queryByText("Reconstructed replay")).not.toBeInTheDocument();
  });

  it("says a bundle is not on disk yet rather than pretending it is", async () => {
    stub();
    renderWithProviders(<ReplayScreen />);

    const chennai = await screen.findByRole("button", { name: /CHN-IDF-25yr/ });
    expect(chennai).toHaveTextContent("Generated by make bundle");
    expect(chennai).toHaveTextContent("Missing radar/frames.zarr, truth/rain.zarr.");
    expect(screen.getByRole("button", { name: /MUM-2019-07-02/ })).not.toHaveTextContent(
      "Generated by make bundle",
    );
  });

  it("points the clock at the bundle the operator picks", async () => {
    const fetchMock = stub();
    renderWithProviders(<ReplayScreen />);

    const chennai = await screen.findByRole("button", { name: /CHN-IDF-25yr/ });
    fireEvent.click(chennai);

    expect(useReplayStore.getState().bundleId).toBe("CHN-IDF-25yr");
    expect(useReplayStore.getState().simTime).toBe(CHENNAI.t0);
    expect(chennai).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(([url]) =>
        String(url).endsWith("/v1/replay/bundle"),
      );
      expect(posted).toBeDefined();
      expect(String(posted?.[1]?.body)).toContain("CHN-IDF-25yr");
    });
  });

  it("says which make target generates a bundle when there is none", async () => {
    stub([]);
    renderWithProviders(<ReplayScreen />);

    expect(await screen.findByText("No bundles yet")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Run make bundle BUNDLE=MUM-2019-07-02 to generate the reconstructed replay, then reload.",
      ),
    ).toBeInTheDocument();
  });

  it("holds the cycle log and the storm designer in their empty states", async () => {
    stub();
    renderWithProviders(<ReplayScreen />);

    expect(await screen.findByText("No cycle yet")).toBeInTheDocument();
    // Once: the page's clock panel no longer repeats the log and the storm (variant "page").
    expect(screen.getAllByText("No cycles yet")).toHaveLength(1);
    expect(
      screen.getByText("Coming in pilot. The demo storm is fixed by its seed."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate bundle" })).toBeDisabled();
  });

  it("keeps a card to one line and puts the bundle's notes and sources behind Details", async () => {
    stub([
      {
        ...DEMO,
        synthetic_notes: [
          "Reconstructed replay: the rain field, the radar frames and the gauges are all generated.",
        ],
        sources: [{ name: "IMD RMC Mumbai", url: "https://mausam.imd.gov.in/", note: "375.2 mm." }],
      },
      CHENNAI,
    ]);
    renderWithProviders(<ReplayScreen />);

    const demo = await screen.findByRole("button", { name: /MUM-2019-07-02/ });
    expect(demo).toHaveTextContent(
      "29 sourced pins; rain, radar, gauges, tide, traffic and reports synthetic.",
    );
    expect(demo).toHaveTextContent("0 of 49 cycles baked");
    expect(demo).not.toHaveTextContent("all generated");

    const summary = screen.getByText("Details: sources and notes for MUM-2019-07-02");
    const details = summary.closest("details") as HTMLElement;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText(/all generated/)).toBeInTheDocument();
    expect(within(details).getByRole("link", { name: "IMD RMC Mumbai" })).toHaveAttribute(
      "href",
      "https://mausam.imd.gov.in/",
    );
  });

  it("does not date a design storm, whose clock is nominal", async () => {
    stub([DEMO, { ...CHENNAI, built: true, missing_members: [] }]);
    renderWithProviders(<ReplayScreen />);

    const chennai = await screen.findByRole("button", { name: /CHN-IDF-25yr/ });
    expect(chennai).toHaveTextContent("05:40 to 08:40 IST, nominal clock");
    expect(chennai).not.toHaveTextContent("2026");
    expect(chennai).toHaveTextContent("Synthetic scenario, not a recorded event.");
  });

  it("shows the stage timings of the run the clock is in, from that run's own record", async () => {
    useReplayStore.setState({ simTime: "2019-07-02T06:45:00+05:30" });
    stub([DEMO], {
      "/v1/runs": RUNS,
      [RUN_0640]: RUN_0640_META,
    });
    renderWithProviders(<ReplayScreen />);

    // 06:45 is between bakes: the 06:40 run is the one the clock is showing.
    expect(await screen.findByText(/Stages of the/)).toHaveTextContent("Stages of the 06:40 run");
    const sky = await screen.findByRole("meter", { name: "Sky" });
    expect(sky).toHaveAttribute("aria-valuetext", `${formatMs(3151)} of ${formatMs(5000)}`);
    expect(screen.getByRole("meter", { name: "Decode" })).toHaveAttribute(
      "aria-valuetext",
      "Not run",
    );
    expect(
      screen.getByRole("button", { name: "Show the stage timings of the 06:40 run" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("gives the clock its own panel without repeating the log or the radar", async () => {
    stub();
    renderWithProviders(<ReplayScreen />);

    await screen.findByRole("button", { name: /MUM-2019-07-02/ });
    expect(
      screen.queryByRole("button", { name: "Close the replay panel" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous cycle" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next cycle" })).toBeInTheDocument();
    // Base UI draws the thumb and its hidden range input; jsdom does not expose the pair as one
    // slider role, so the scrub is found by the label the Slider forwards to its thumb.
    expect(
      document.querySelector(
        '[aria-label="Seek the replay, minutes from the start of the window"]',
      ),
    ).not.toBeNull();
    expect(screen.queryByRole("region", { name: "Storm summary" })).not.toBeInTheDocument();
  });

  it("puts the reconstructed replay first, whatever order the API lists", async () => {
    const mumbaiDesign = { ...CHENNAI, id: "MUM-IDF-25yr", city: "mumbai" };
    stub([CHENNAI, mumbaiDesign, DEMO]);
    renderWithProviders(<ReplayScreen />);

    await screen.findByRole("button", { name: /MUM-2019-07-02/ });
    const list = screen.getByRole("list", { name: "Bundles" });
    const order = within(list)
      .getAllByRole("button")
      .map((card) => card.querySelector("p")?.textContent);
    expect(order).toEqual(["MUM-2019-07-02", "MUM-IDF-25yr", "CHN-IDF-25yr"]);
  });

  it("does not tell the reader to run make bundle while the bundle list is still loading", async () => {
    const answer = stubFetch({ "/v1/runs": { runs: [] }, "/v1/replay/clock": CLOCK });
    // The bundle list never answers: the page must read as loading, not as missing data.
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
        String(input).includes("/v1/replay/bundles")
          ? new Promise<Response>(() => {})
          : answer(input, init),
      ),
    );
    renderWithProviders(<ReplayScreen />);

    expect(await screen.findByLabelText("Loading the storm")).toBeInTheDocument();
    expect(screen.queryByText("No radar frames yet")).not.toBeInTheDocument();
    expect(screen.queryByText("No storm cells yet")).not.toBeInTheDocument();
    expect(screen.queryByText(/Run make bundle/)).not.toBeInTheDocument();
  });

  it("tells an empty log to bake, not to press Play, which bakes nothing", async () => {
    stub();
    renderWithProviders(<ReplayScreen />);

    expect(
      await screen.findByText("Run make bake BUNDLE=MUM-2019-07-02 to bake its cycles."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Press Play on the replay.")).not.toBeInTheDocument();
  });

  it("says it is reaching the clock rather than telling the reader to start the API", async () => {
    const answer = stubFetch({ "/v1/runs": { runs: [] }, "/v1/replay/bundles": [DEMO] });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
        String(input).includes("/v1/replay/clock")
          ? new Promise<Response>(() => {})
          : answer(input, init),
      ),
    );
    renderWithProviders(<ReplayScreen />);

    expect(await screen.findByText("Reaching the replay clock.")).toBeInTheDocument();
    expect(screen.queryByText(/Start it with make dev/)).not.toBeInTheDocument();
  });

  it("gives a clock that is missing its bundle the API's words and a way to ask again", async () => {
    const answer = stubFetch({
      "/v1/runs": { runs: [] },
      "/v1/replay/bundles": [DEMO],
      "/v1/replay/clock": {
        status: 404,
        body: {
          error: { code: "bundle_not_found", message: "Run make bundle BUNDLE=MUM-2019-07-02." },
        },
      },
    });
    const fetchMock = vi.fn(answer);
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ReplayScreen />);

    expect(await screen.findByText("Run make bundle BUNDLE=MUM-2019-07-02.")).toBeInTheDocument();
    const clockCalls = () =>
      fetchMock.mock.calls.filter(([input]) => String(input).includes("/v1/replay/clock")).length;
    const before = clockCalls();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(clockCalls()).toBeGreaterThan(before));
  });
});

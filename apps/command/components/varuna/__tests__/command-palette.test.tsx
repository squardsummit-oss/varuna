import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { defaultFilter } from "cmdk";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CommandPalette, PILOT_PLANS, paletteScore } from "@/components/varuna/command-palette";
import { paletteHref } from "@/lib/api/palette";
import { NAV_ITEMS, PALETTE_ACTIONS } from "@/lib/nav";
import { type RunMeta, useRunStore } from "@/lib/stores/run";
import { useUiStore } from "@/lib/stores/ui";
import { renderWithProviders, stubFetch } from "@/lib/test-utils";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
}));

// jsdom has no scrollIntoView; cmdk calls it on the selected item.
Element.prototype.scrollIntoView = vi.fn();

/*
 * Fixtures are cut from the 08:40 IST baked run (`demo/runs/MUM-20190702T0310Z-...`) and the Mumbai
 * asset layer, so every name, id and number below is one the API actually serves.
 */
const RUN_0840 = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";
const RUN_0910 = "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked";

const RUN: RunMeta = {
  run_id: RUN_0840,
  city: "mumbai",
  cycle_ts: "2019-07-02T08:40:00+05:30",
  mode: "replay",
  replay_mode: "baked",
};

const RUNS = {
  runs: [
    { run_id: RUN_0910, city: "mumbai", cycle_ts: "2019-07-02T09:10:00+05:30", mode: "baked" },
    { run_id: RUN_0840, city: "mumbai", cycle_ts: "2019-07-02T08:40:00+05:30", mode: "baked" },
  ],
};

const HOTSPOTS = {
  run_id: RUN_0840,
  ranking: "peak depth",
  hotspots: [
    {
      rank: 2,
      hotspot_id: "MUM-HS-01",
      name: "Hindmata junction (Hindmata Cinema, Dr B. Ambedkar Marg)",
      peak_depth_cm: 9.7,
    },
    { rank: 7, hotspot_id: "MUM-HS-05", name: "Sion Circle", peak_depth_cm: 7.6 },
    { rank: 8, hotspot_id: "MUM-HS-14", name: "Dadar TT / Khodadad Circle", peak_depth_cm: 7.3 },
  ],
};

const FACILITIES = {
  city: "mumbai",
  count: 2,
  facilities: [
    {
      asset_id: "hospital-001",
      name: "King Edward Memorial (KEM) Hospital, Parel",
      kind: "hospital",
      lon: 72.8414,
      lat: 19.0024,
    },
    {
      asset_id: "fire_station-001",
      name: "Dadar Fire Brigade",
      kind: "fire_station",
      lon: 72.8424,
      lat: 19.0186,
    },
  ],
};

const DRAINS = {
  run_id: RUN_0840,
  n_edges: 49770,
  features: [
    {
      geometry: { coordinates: [] },
      properties: {
        edge_id: "MUM-E018095",
        street: "Khodadad Circle",
        beta_mean: 0.4917,
        beta_sd: 0.1,
      },
    },
    {
      geometry: { coordinates: [] },
      properties: { edge_id: "MUM-E007910", street: null, beta_mean: 0.4088, beta_sd: 0.12 },
    },
  ],
};

const API = {
  "/v1/runs": RUNS,
  "/v1/nowcast/hotspots": HOTSPOTS,
  "/v1/route/facilities": FACILITIES,
  "/v1/drains/health": DRAINS,
};

const HINDMATA = HOTSPOTS.hotspots[0]!.name;

function itemFor(label: string | RegExp): HTMLElement {
  const item = screen.getByText(label).closest("[cmdk-item]");
  if (!(item instanceof HTMLElement)) throw new Error(`No palette item for "${String(label)}"`);
  return item;
}

function input(): HTMLElement {
  return screen.getByPlaceholderText("Jump to a hotspot, facility, pipe, screen or action");
}

function type(value: string) {
  fireEvent.change(input(), { target: { value } });
}

function key(name: string) {
  fireEvent.keyDown(input(), { key: name });
}

async function openWithApi(routes: Record<string, unknown> = API) {
  vi.stubGlobal("fetch", vi.fn(stubFetch(routes)));
  const view = renderWithProviders(<CommandPalette />);
  await screen.findByText(HINDMATA);
  return view;
}

describe("paletteHref", () => {
  it("builds every deep link the palette emits, carrying the cycle it was read from", () => {
    expect(paletteHref.hotspot("MUM-HS-01", RUN_0840)).toBe(
      `/console?run=${RUN_0840}&hotspot=MUM-HS-01`,
    );
    expect(paletteHref.facility("hospital-001", RUN_0840)).toBe(
      `/console?run=${RUN_0840}&tab=reachability&facility=hospital-001`,
    );
    expect(paletteHref.pipe("MUM-E018095", RUN_0840)).toBe(
      `/drains?run=${RUN_0840}&pipe=MUM-E018095`,
    );
    expect(paletteHref.run(RUN_0840)).toBe(`/console?run=${RUN_0840}`);
    expect(paletteHref.dispatch("MUM-HS-01", RUN_0840)).toBe(
      `/pumps?run=${RUN_0840}&hotspot=MUM-HS-01`,
    );
    expect(paletteHref.whatif(RUN_0840)).toBe(`/whatif?run=${RUN_0840}`);
  });

  it("leaves out what it does not know and encodes what it does", () => {
    expect(paletteHref.hotspot("MUM-HS-01")).toBe("/console?hotspot=MUM-HS-01");
    expect(paletteHref.dispatch(undefined)).toBe("/pumps");
    expect(paletteHref.whatif(undefined)).toBe("/whatif");
    expect(paletteHref.facility("a b&c")).toBe("/console?tab=reachability&facility=a%20b%26c");
  });
});

describe("CommandPalette", () => {
  beforeEach(() => {
    push.mockClear();
    useRunStore.getState().clear();
    useUiStore.setState({ commandPaletteOpen: true });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads nothing until it is opened", () => {
    const fetchSpy = vi.fn(stubFetch(API));
    vi.stubGlobal("fetch", fetchSpy);
    useUiStore.setState({ commandPaletteOpen: false });
    renderWithProviders(<CommandPalette />);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("shows shimmer rows while the lists load", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})),
    );
    renderWithProviders(<CommandPalette />);
    for (const label of [
      "Loading hotspots",
      "Loading facilities",
      "Loading pipes",
      "Loading runs",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    // Screens need no data, so they are there from the first frame.
    expect(itemFor("Drishti")).toBeInTheDocument();
  });

  it("lists hotspots, facilities, pipes, screens and runs from the API", async () => {
    await openWithApi();

    expect(itemFor(HINDMATA)).toBeInTheDocument();
    expect(screen.getByText("Peak 10 cm")).toBeInTheDocument();
    expect(itemFor("Sion Circle")).toBeInTheDocument();

    const kem = itemFor("King Edward Memorial (KEM) Hospital, Parel");
    expect(within(kem).getByText("Hospital")).toBeInTheDocument();
    expect(within(itemFor("Dadar Fire Brigade")).getByText("Fire station")).toBeInTheDocument();

    const khodadad = itemFor("Khodadad Circle");
    expect(within(khodadad).getByText("β 0.49")).toBeInTheDocument();
    expect(within(khodadad).getByText("Pipe MUM-E018095")).toBeInTheDocument();
    // A pipe with no street is named by its id rather than an invented street.
    expect(itemFor("Pipe MUM-E007910")).toBeInTheDocument();

    for (const item of NAV_ITEMS) expect(itemFor(item.label)).toBeInTheDocument();

    expect(within(itemFor("2 Jul 2019 09:10 IST")).getByText("baked")).toBeInTheDocument();
    // No run in the store, so the ranking's own run is the one the palette is showing.
    expect(within(itemFor("2 Jul 2019 08:40 IST")).getByText("Showing")).toBeInTheDocument();
  });

  it("reads the lists for the run the operator is looking at", async () => {
    useRunStore.getState().setRun(RUN);
    const fetchSpy = vi.fn(stubFetch(API));
    vi.stubGlobal("fetch", fetchSpy);
    renderWithProviders(<CommandPalette />);
    await screen.findByText(HINDMATA);

    const urls = fetchSpy.mock.calls.map(([url]) => String(url));
    const hotspotsUrl = urls.find((u) => u.includes("/v1/nowcast/hotspots"));
    const drainsUrl = urls.find((u) => u.includes("/v1/drains/health"));
    const facilitiesUrl = urls.find((u) => u.includes("/v1/route/facilities"));
    expect(hotspotsUrl).toContain(`run_id=${RUN_0840}`);
    expect(drainsUrl).toContain(`run_id=${RUN_0840}`);
    expect(drainsUrl).toContain("limit=25");
    expect(facilitiesUrl).toContain("city=mumbai");
  });

  it("filters across every group as the operator types", async () => {
    await openWithApi();

    type("Khodadad");
    await waitFor(() => expect(screen.queryByText("Drishti")).not.toBeInTheDocument());
    expect(itemFor("Dadar TT / Khodadad Circle")).toBeInTheDocument();
    expect(itemFor("Khodadad Circle")).toBeInTheDocument();
    expect(
      screen.queryByText("King Edward Memorial (KEM) Hospital, Parel"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Sion Circle")).not.toBeInTheDocument();

    type("MUM-E007910");
    await waitFor(() => expect(itemFor("Pipe MUM-E007910")).toBeInTheDocument());
    expect(screen.queryByText("Khodadad Circle")).not.toBeInTheDocument();
  });

  it("opens each item on its own deep link", async () => {
    useRunStore.getState().setRun(RUN);
    await openWithApi();

    const cases: [string, string][] = [
      [HINDMATA, `/console?run=${RUN_0840}&hotspot=MUM-HS-01`],
      [
        "King Edward Memorial (KEM) Hospital, Parel",
        `/console?run=${RUN_0840}&tab=reachability&facility=hospital-001`,
      ],
      ["Khodadad Circle", `/drains?run=${RUN_0840}&pipe=MUM-E018095`],
      ["Nadi", "/drains"],
      ["2 Jul 2019 09:10 IST", `/console?run=${RUN_0910}`],
      [`Dispatch pumps at ${HINDMATA}`, `/pumps?run=${RUN_0840}&hotspot=MUM-HS-01`],
      ["Clean pipes in what-if", `/whatif?run=${RUN_0840}`],
    ];
    for (const [label, href] of cases) {
      act(() => useUiStore.setState({ commandPaletteOpen: true }));
      await screen.findByText(label);
      fireEvent.click(itemFor(label));
      expect(push).toHaveBeenLastCalledWith(href);
      expect(useUiStore.getState().commandPaletteOpen).toBe(false);
    }
    // The what-if lab opens on the cycle only: the palette names no pipes and no effect (ADR-0042).
    expect(push.mock.calls.every(([href]) => !String(href).includes("segments="))).toBe(true);
  });

  it("replaces the generic dispatch with one per ranked hotspot", async () => {
    await openWithApi();
    expect(screen.queryByText("Dispatch pumps at a hotspot")).not.toBeInTheDocument();
    for (const h of HOTSPOTS.hotspots) {
      expect(itemFor(`Dispatch pumps at ${h.name}`)).toBeInTheDocument();
    }
  });

  it("says Compute live is coming in pilot and never runs it", async () => {
    await openWithApi();
    const item = itemFor("Compute live");
    expect(item).toHaveAttribute("aria-disabled", "true");
    expect(
      within(item).getByText(`Coming in pilot. ${PILOT_PLANS["compute-live"]}`),
    ).toBeInTheDocument();
    fireEvent.click(item);
    expect(push).not.toHaveBeenCalled();
  });

  it("says what failed and still offers every screen when the API is down", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    renderWithProviders(<CommandPalette />);

    for (const what of ["Hotspots", "Facilities", "Pipes"]) {
      expect(
        await screen.findByText(new RegExp(`^${what} did not load \\(Failed to fetch\\)`)),
      ).toBeInTheDocument();
    }
    expect(await screen.findByText(/^Runs did not load/)).toBeInTheDocument();
    expect(
      screen.getAllByText(/Screens still open; open the palette again to retry\.$/),
    ).toHaveLength(4);

    // The failure stays visible while searching, and screens still work.
    type("pumps");
    expect(screen.getByText(/^Hotspots did not load/)).toBeInTheDocument();
    fireEvent.click(itemFor("Jalayantra"));
    expect(push).toHaveBeenLastCalledWith("/pumps");
  });

  it("names a Sanskrit screen with its gloss and finds it by the English words", async () => {
    // ADR-0085: every screen carries a Sanskrit name. An operator who types what the screen does
    // still finds it, because the id, the gloss and each word of it are keywords.
    vi.stubGlobal("fetch", vi.fn(stubFetch({})));
    renderWithProviders(<CommandPalette />);
    await screen.findByText(/^Facilities did not load/);

    expect(NAV_ITEMS.every((i) => i.gloss)).toBe(true);
    for (const item of NAV_ITEMS) {
      const row = itemFor(item.label);
      expect(within(row).getByText(item.label)).toHaveAttribute("translate", "no");
      expect(within(row).getByText(item.gloss!)).toBeInTheDocument();
    }

    const searches: [string, string][] = [
      ["pumps", "Jalayantra"],
      ["drain", "Nadi"],
      ["what-if", "Kalpana"],
      ["verify", "Pramana"],
      ["route", "Marga"],
      ["replay", "Smriti"],
      // The three renamed last are still found by the words an operator already knows.
      ["console", "Drishti"],
      ["alerts", "Sanket"],
      ["onboard", "Pravesh"],
    ];
    for (const [query, label] of searches) {
      type(query);
      await waitFor(() => expect(itemFor(label)).toBeInTheDocument());
    }
  });

  it("keeps the city in the address bar when it opens a screen, and nothing else", async () => {
    window.history.replaceState(null, "", "/console?city=chennai&run=CHN-SOUTH-20260910T0120Z");
    try {
      vi.stubGlobal("fetch", vi.fn(stubFetch({})));
      renderWithProviders(<CommandPalette />);
      await screen.findByText(/^Facilities did not load/);
      fireEvent.click(itemFor("Nadi"));
      expect(push).toHaveBeenLastCalledWith("/drains?city=chennai");
    } finally {
      window.history.replaceState(null, "", "/");
    }
  });

  it("disables run-only actions with their reason until any run is known", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch({})));
    renderWithProviders(<CommandPalette />);
    await screen.findByText(/^Facilities did not load/);

    for (const action of PALETTE_ACTIONS.filter((a) => a.needsRun && !PILOT_PLANS[a.id])) {
      expect(itemFor(action.label)).toHaveAttribute("aria-disabled", "true");
    }
    expect(screen.getAllByText("Available once a run is loaded").length).toBeGreaterThan(0);
    for (const action of PALETTE_ACTIONS.filter((a) => !a.needsRun)) {
      expect(itemFor(action.label)).not.toHaveAttribute("aria-disabled", "true");
    }
  });

  it("tells the operator to press Play when there are no runs at all", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch({ "/v1/runs": { runs: [] } })));
    renderWithProviders(<CommandPalette />);
    expect(
      await screen.findByText("No hotspots yet — press Play on the replay"),
    ).toBeInTheDocument();
  });

  it("selects the top-ranked hotspot when the ranking lands after the other lists", async () => {
    // Hold the ranking back until facilities have rendered, the order a slow hotspots read gives.
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const answer = stubFetch(API);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).includes("/v1/nowcast/hotspots")) await gate;
        return answer(input);
      }),
    );
    renderWithProviders(<CommandPalette />);

    await screen.findByText("King Edward Memorial (KEM) Hospital, Parel");
    release();
    await screen.findByText(HINDMATA);
    await waitFor(() => expect(itemFor(HINDMATA)).toHaveAttribute("aria-selected", "true"));

    key("Enter");
    expect(push).toHaveBeenLastCalledWith(paletteHref.hotspot("MUM-HS-01", RUN_0840));
  });

  it("jumps to a hotspot by name with Enter", async () => {
    useRunStore.getState().setRun(RUN);
    await openWithApi();

    type("Hindmata");
    await waitFor(() => expect(itemFor(HINDMATA)).toHaveAttribute("aria-selected", "true"));
    key("Enter");
    expect(push).toHaveBeenLastCalledWith(`/console?run=${RUN_0840}&hotspot=MUM-HS-01`);
  });

  it("moves the selection with the arrow keys and opens what is selected", async () => {
    await openWithApi();

    type("Sion");
    const selected = () => document.querySelector<HTMLElement>('[cmdk-item][aria-selected="true"]');
    await waitFor(() => expect(selected()).not.toBeNull());
    const first = selected()!;

    key("ArrowDown");
    await waitFor(() => expect(selected()).not.toBe(first));
    const second = selected()!;
    expect(second.getAttribute("data-href")).toBeTruthy();

    key("Enter");
    expect(push).toHaveBeenLastCalledWith(second.getAttribute("data-href"));
  });
});

/*
 * Real names from the 09:10 IST ranking (`MUM-20190702T0340Z-...`) and the Mumbai asset layer,
 * picked because cmdk's own scattered-letter score matches each of them to a word an operator types
 * for a screen: "console" matched 39 of the 368 facilities, "alerts" 176 and two hotspots,
 * "drishti" 130, "sanket" 21 and four hotspots, "pumps" 6. With the lists in their API order the
 * screens sat under all of them, and Enter opened a hospital.
 */
const PLACES_API = {
  "/v1/runs": RUNS,
  "/v1/nowcast/hotspots": {
    run_id: RUN_0910,
    ranking: "peak depth",
    hotspots: [
      { rank: 1, hotspot_id: "MUM-HS-26", name: "Mahim", peak_depth_cm: 5.1 },
      { rank: 12, hotspot_id: "MUM-HS-04", name: "Gandhi Market (Matunga)", peak_depth_cm: 1.4 },
      {
        rank: 21,
        hotspot_id: "MUM-HS-19",
        name: "Kalanagar / Kherwadi (Bandra East)",
        peak_depth_cm: 1,
      },
      { rank: 26, hotspot_id: "MUM-HS-02", name: "Parel / Bharat Mata Cinema", peak_depth_cm: 0.7 },
    ],
  },
  "/v1/route/facilities": {
    city: "mumbai",
    count: 6,
    facilities: [
      {
        asset_id: "fire_station-006",
        name: "Andheri Fire Station",
        kind: "fire_station",
        lon: 72.840863,
        lat: 19.112162,
      },
      {
        asset_id: "fire_station-002",
        name: "Kurla Agnishaman Kendra",
        kind: "fire_station",
        lon: 72.885935,
        lat: 19.08443,
      },
      {
        asset_id: "osm-251",
        name: "Alphine Life Solutions General Hospital",
        kind: "hospital",
        lon: 72.871065,
        lat: 19.072612,
      },
      {
        asset_id: "osm-263",
        name: "Apex Multi Speciality Hospital",
        kind: "hospital",
        lon: 72.891648,
        lat: 19.099487,
      },
      {
        asset_id: "osm-354",
        name: "K B H B Charitable Ophthalmic and E.N.T Hospital",
        kind: "hospital",
        lon: 72.840409,
        lat: 19.002348,
      },
    ],
  },
  "/v1/drains/health": DRAINS,
};

describe("CommandPalette ranking", () => {
  beforeEach(() => {
    push.mockClear();
    useRunStore.getState().clear();
    useUiStore.setState({ commandPaletteOpen: true });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses fixtures that cmdk's own score mistakes for a screen, and rejects them", () => {
    // If a fixture stops matching under cmdk's score, the tests below prove nothing about it.
    const cases: [string, string, string[]][] = [
      ["console", "facility Alphine Life Solutions General Hospital osm-251", ["reachability"]],
      [
        "console",
        "facility K B H B Charitable Ophthalmic and E.N.T Hospital osm-354",
        ["reachability"],
      ],
      ["alerts", "hotspot Kalanagar / Kherwadi (Bandra East) MUM-HS-19", []],
      ["alerts", "facility Andheri Fire Station fire_station-006", ["reachability"]],
      ["drishti", "facility Andheri Fire Station fire_station-006", ["reachability"]],
      ["sanket", "facility Kurla Agnishaman Kendra fire_station-002", ["reachability"]],
      ["sanket", "hotspot Gandhi Market (Matunga) MUM-HS-04", []],
      ["pumps", "facility Apex Multi Speciality Hospital osm-263", ["reachability"]],
    ];
    for (const [query, value, keywords] of cases) {
      expect(defaultFilter(value, query, keywords), `${query} in ${value}`).toBeGreaterThan(0);
      expect(paletteScore(value, query, keywords), `${query} in ${value}`).toBe(0);
    }
  });

  it("matches named places on whole words, and screens also on scattered letters", () => {
    const kem = "facility King Edward Memorial (KEM) Hospital, Parel hospital-001";
    expect(paletteScore(kem, "kem", ["reachability"])).toBeGreaterThan(30);
    expect(paletteScore(kem, "hosp parel", ["reachability"])).toBeGreaterThan(30);
    expect(paletteScore(`run ${RUN_0840} 2 Jul 2019 08:40 IST`, "0310")).toBeGreaterThan(20);
    // A screen matched on its words outranks a place matched on its words.
    const pumps = NAV_ITEMS.find((item) => item.id === "pumps")!;
    const screenPumps = paletteScore("screen Jalayantra", "pumps", [
      "pumps",
      pumps.gloss!,
      "pump",
      "dispatch",
    ]);
    expect(screenPumps).toBeGreaterThan(
      paletteScore("action dispatch pumps at Mahim MUM-HS-26", "pumps"),
    );
    expect(paletteScore("screen Jalayantra", "jlyntr", ["pumps"])).toBeGreaterThan(10);
    expect(paletteScore("screen Jalayantra", "", ["pumps"])).toBe(1);
  });

  it.each([
    ["console", "Drishti", "/console"],
    ["alerts", "Sanket", "/alerts"],
    ["drishti", "Drishti", "/console"],
    ["sanket", "Sanket", "/alerts"],
    ["pumps", "Jalayantra", "/pumps"],
    ["onboard", "Pravesh", "/onboard"],
    ["jlyntr", "Jalayantra", "/pumps"],
  ])("selects the screen for %s and Enter opens it", async (query, label, href) => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(PLACES_API)));
    renderWithProviders(<CommandPalette />);
    await screen.findByText("K B H B Charitable Ophthalmic and E.N.T Hospital");
    await screen.findByText("Kalanagar / Kherwadi (Bandra East)");

    type(query);
    await waitFor(() => expect(itemFor(label)).toHaveAttribute("aria-selected", "true"));
    // The screen is the first row drawn, not merely the selected one.
    const rows = [...document.querySelectorAll<HTMLElement>("[cmdk-item]")];
    expect(rows[0]).toBe(itemFor(label));
    expect(screen.queryByText("K B H B Charitable Ophthalmic and E.N.T Hospital")).toBeNull();

    key("Enter");
    expect(push).toHaveBeenLastCalledWith(href);
  });

  it("still selects a place when the operator types its name", async () => {
    vi.stubGlobal("fetch", vi.fn(stubFetch(PLACES_API)));
    renderWithProviders(<CommandPalette />);
    await screen.findByText("Kalanagar / Kherwadi (Bandra East)");

    type("kalanagar");
    await waitFor(() =>
      expect(itemFor("Kalanagar / Kherwadi (Bandra East)")).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    key("Enter");
    expect(push).toHaveBeenLastCalledWith(paletteHref.hotspot("MUM-HS-19", RUN_0910));

    act(() => useUiStore.setState({ commandPaletteOpen: true }));
    type("andheri fire");
    await waitFor(() =>
      expect(itemFor("Andheri Fire Station")).toHaveAttribute("aria-selected", "true"),
    );
    key("Enter");
    expect(push).toHaveBeenLastCalledWith(paletteHref.facility("fire_station-006", RUN_0910));
  });
});

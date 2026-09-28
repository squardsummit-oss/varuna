/**
 * `/drains`, Nadi (drain health): what the screen shows in each state a run can be in.
 *
 * - loading: the four tiles are skeletons and nothing claims a number;
 * - a run without drain health: an empty state that says what to do;
 * - a run baked before the product carried its summary: counts of what it wrote, capacity left
 *   unsplit, and a sentence saying so;
 * - a run with a summary: the product's own figures.
 *
 * The map is stubbed: deck.gl has no WebGL in jsdom, and what the map draws is pinned in
 * `components/map/layers/__tests__/drains-learned.test.ts`. What is checked here is the page.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { UNNAMED_PLACE } from "@/lib/api/drains";
import { useRunStore } from "@/lib/stores/run";

const nav = vi.hoisted(() => ({ params: new URLSearchParams(), replace: vi.fn() }));
const mapProps = vi.hoisted(() => ({ last: null as Record<string, unknown> | null }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/drains",
  useRouter: () => ({ push: vi.fn(), replace: nav.replace, prefetch: vi.fn() }),
  useSearchParams: () => nav.params,
}));

vi.mock("@/components/map/city-map", () => ({
  CityMap: (props: Record<string, unknown>) => {
    mapProps.last = props;
    return <div data-testid="city-map" className="absolute inset-0" />;
  },
}));

const { DrainsScreen } = await import("../drains-screen");

const RUN_0840 = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";
const RUN_0910 = "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked";
const CYCLE = "2019-07-02T08:40:00+05:30";

const RUNS = {
  runs: [
    { run_id: RUN_0910, cycle_ts: "2019-07-02T09:10:00+05:30", created_at: "2026-09-26T00:00:00Z" },
    { run_id: RUN_0840, cycle_ts: CYCLE, created_at: "2026-09-26T00:00:00Z" },
  ],
};

function feature(props: Record<string, unknown>) {
  return {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: [
        [72.84, 19.01],
        [72.842, 19.012],
      ],
    },
    properties: {
      beta_sd: 0.1,
      capacity_reduction_pct: 40,
      diameter_m: 0.6,
      observations: 0,
      explains: [],
      confidence: "inferred",
      last_update: CYCLE,
      ...props,
    },
  };
}

/** The shipped 08:40 product before the summary: no `moved`, no names on most pipes. */
const OLD_HEALTH = {
  run_id: RUN_0840,
  operator: "capacity_deficit",
  note: "",
  n_edges: 49770,
  n_updated: 201,
  notes: [],
  features: [
    feature({
      edge_id: "MUM-E037896",
      street: null,
      beta_mean: 0.4926,
      beta_prior: 0.15,
      beta_delta: 0.3426,
      diameter_m: 3,
    }),
    feature({
      edge_id: "MUM-E000001",
      street: "Tulsi Pipe Road",
      beta_mean: 0.35,
      beta_prior: 0.35,
      beta_delta: 0,
    }),
    feature({
      edge_id: "MUM-E003642",
      street: "Dr Babasaheb Ambedkar Marg",
      beta_mean: 0.2197,
      beta_prior: 0.15,
      beta_delta: 0.0697,
    }),
  ],
};

const SUMMARY = {
  source: "product",
  n_pipes: 49770,
  n_moved: 201,
  n_moved_up: 173,
  n_moved_down: 28,
  largest_rise: {
    edge: "MUM-E037896",
    name: "off Eastern Freeway",
    locality: null,
    prior: 0.15,
    post: 0.4926,
  },
  largest_fall: {
    edge: "MUM-E020001",
    name: "Sir Bhalchandra Road",
    locality: "near Dadar TT / Khodadad Circle",
    prior: 0.2,
    post: 0.0047,
  },
  capacity_full_m3s: 63109,
  capacity_lost_prior_pct: 28.408,
  capacity_lost_post_pct: 28.496,
  capacity_learned_m3s: 55.6,
  n_obs: 21,
  n_obs_by_kind: { traffic: 10, report: 11 },
  n_obs_synthetic: 20,
  n_obs_real: 1,
  n_moved_unwritten: null,
  note: null,
};

const NEW_HEALTH = {
  ...OLD_HEALTH,
  summary: SUMMARY,
  features: [
    feature({
      edge_id: "MUM-E037896",
      display_name: "off Eastern Freeway",
      locality: "near Wadala",
      moved: true,
      beta_mean: 0.4926,
      beta_prior: 0.15,
      beta_delta: 0.3426,
      diameter_m: 3,
    }),
    feature({
      edge_id: "MUM-E020001",
      street: "Sir Bhalchandra Road",
      display_name: "Sir Bhalchandra Road",
      locality: "near Dadar TT / Khodadad Circle",
      moved: true,
      beta_mean: 0.0047,
      beta_prior: 0.2,
      beta_delta: -0.1953,
    }),
    feature({
      edge_id: "MUM-E000001",
      display_name: "Tulsi Pipe Road",
      moved: false,
      beta_mean: 0.35,
      beta_prior: 0.35,
      beta_delta: 0,
    }),
  ],
};

const OBSERVATIONS = {
  run_id: RUN_0840,
  cycle_ts: CYCLE,
  n_traffic: 1,
  n_reports: 1,
  n_assimilated: 2,
  n_edges_updated: 201,
  notes: [],
  disagreements: [],
  observations: [
    // A run baked before traffic anomalies were named carries no `place`.
    {
      kind: "traffic",
      segment_id: "S0-342",
      edge_id: "MUM-E003642",
      ts: CYCLE,
      depth_cm: 20,
      depth_sd_cm: 8,
      speed_kmh: 3,
      baseline_kmh: 27.2,
      synthetic: true,
      beta_before: 0.15,
      beta_after: 0.2197,
    },
    {
      kind: "report",
      report_id: "R-real",
      edge_id: "MUM-E020001",
      ts: CYCLE,
      place: "Sir Bhalchandra Road",
      depth_cm: 45,
      depth_sd_cm: 12,
      chip: "knee",
      speed_kmh: null,
      synthetic: false,
      beta_before: 0.2,
      beta_after: 0.0047,
    },
  ],
};

/**
 * Two streets from the city's segments layer: a service road running beside the trunk above
 * (whose middle is 72.841, 19.011), labelled as the API labels a road OSM does not name, and a
 * named main road about 2 km away, which is too far to say where the trunk is.
 */
const SEGMENTS_NEAR_TRUNK = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [
          [72.8405, 19.0112],
          [72.8418, 19.0112],
        ],
      },
      properties: {
        segment_id: "S0-900",
        class: "service",
        display_name: "Service road near Wadala Depot",
      },
    },
    {
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [
          [72.86, 19.02],
          [72.861, 19.021],
        ],
      },
      properties: { segment_id: "S0-901", class: "primary", name: "Dr Ambedkar Road" },
    },
  ],
};

type Route = { status?: number; body?: unknown; pending?: boolean };

function stub(routes: Record<string, Route>) {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    calls.push(url);
    const path = new URL(url, "http://localhost:8000").pathname;
    const match = Object.entries(routes).find(([route]) => path.endsWith(route));
    if (!match) return new Response("{}", { status: 404 });
    const route = match[1];
    if (route.pending) return new Promise<Response>(() => {});
    return new Response(JSON.stringify(route.body ?? {}), {
      status: route.status ?? 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function renderScreen() {
  return render(
    <TooltipProvider>
      <DrainsScreen />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  nav.params = new URLSearchParams();
  nav.replace.mockReset();
  mapProps.last = null;
  useRunStore.getState().clear();
  // Reduced motion, so the numbers render as text rather than as NumberFlow's custom element.
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: query.includes("reduce"),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DrainsScreen", () => {
  it("while loading, names the screen and its honesty label and claims no number", async () => {
    stub({ "/v1/runs": { body: RUNS }, "/v1/drains/health": { pending: true } });
    renderScreen();
    expect(screen.getByRole("heading", { level: 1, name: "Nadi" })).toBeInTheDocument();
    expect(screen.getAllByText("Inferred drain graph").length).toBeGreaterThan(0);
    expect(await screen.findByLabelText("Loading what this cycle learned")).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(screen.queryByText(/pipes moved this cycle/)).not.toBeInTheDocument();
  });

  it("opens on the 08:40 cycle, not the newest, and asks every product for that run", async () => {
    const calls = stub({
      "/v1/runs": { body: RUNS },
      "/v1/drains/health": { body: NEW_HEALTH },
      "/v1/observations": { body: OBSERVATIONS },
    });
    renderScreen();
    await screen.findByText(/pipes moved this cycle/);
    const health = calls.find((u) => u.includes("/v1/drains/health"));
    const observed = calls.find((u) => u.includes("/v1/observations"));
    expect(health).toContain(`run_id=${RUN_0840}`);
    // The learned pipes first: a cleared pipe is never cut for a high land-use prior.
    expect(health).toContain("order=learned");
    expect(observed).toContain(`run_id=${RUN_0840}`);
  });

  it("puts the run it draws in the top bar, over the shell's newest-run fallback", async () => {
    stub({
      "/v1/runs": { body: RUNS },
      [`/v1/runs/${RUN_0840}`]: {
        body: {
          run_id: RUN_0840,
          city: "mumbai",
          cycle_ts: CYCLE,
          mode: "baked",
          replay_mode: "replay",
          ensemble_n: 50,
        },
      },
      "/v1/drains/health": { body: NEW_HEALTH },
      "/v1/observations": { body: OBSERVATIONS },
    });
    renderScreen();
    await screen.findByText(/pipes moved this cycle/);
    // The shell asks the registry for its newest (09:10); the screen draws 08:40 and says so.
    await waitFor(() => expect(useRunStore.getState().currentRun?.run_id).toBe(RUN_0840));
    const run = useRunStore.getState().currentRun;
    expect(run?.cycle_ts).toBe(CYCLE);
    expect(run?.replay_mode).toBe("baked");
    expect(run?.mode).toBe("replay");
  });

  it("uses ?run= when the URL names one", async () => {
    nav.params = new URLSearchParams({ run: RUN_0910 });
    const calls = stub({
      "/v1/runs": { body: RUNS },
      "/v1/drains/health": { body: { ...NEW_HEALTH, run_id: RUN_0910 } },
      "/v1/observations": { body: OBSERVATIONS },
    });
    renderScreen();
    await screen.findByText(/pipes moved this cycle/);
    expect(calls.find((u) => u.includes("/v1/drains/health"))).toContain(`run_id=${RUN_0910}`);
  });

  it("says what to do when the run has no drain health", async () => {
    stub({ "/v1/runs": { body: RUNS }, "/v1/drains/health": { status: 404 } });
    renderScreen();
    expect(await screen.findByText("No drain health for this run yet")).toBeInTheDocument();
    expect(screen.getByText(/Pick a later cycle, or press Play on the replay/)).toBeInTheDocument();
    // And nothing else claims that no pipe moved: the run simply has no product to say.
    expect(screen.queryByText("No pipe moved this cycle")).not.toBeInTheDocument();
    expect(screen.getByText("No pipes to name: this run has no drain health.")).toBeInTheDocument();
  });

  it("on a run baked before the summary, counts what it wrote and leaves capacity unsplit", async () => {
    stub({
      "/v1/runs": { body: RUNS },
      "/v1/drains/health": { body: OLD_HEALTH },
      "/v1/observations": { body: OBSERVATIONS },
      "/layers/segments": { body: SEGMENTS_NEAR_TRUNK },
    });
    renderScreen();
    const strip = await screen.findByRole("region", { name: "What this cycle learned" });
    expect(within(strip).getByText("201")).toBeInTheDocument();
    expect(
      within(strip).getByText(
        /201 of 49,770 pipes moved this cycle: 2 up, 0 down among the pipes written/,
      ),
    ).toBeInTheDocument();
    expect(within(strip).getByText("Not split")).toBeInTheDocument();
    expect(within(strip).getByText(/no summary/)).toBeInTheDocument();
    // The market-prior pipe at 0.35 is not a learned change; the 3 m trunk is. No street names
    // the trunk, so it is titled by its id and placed by the nearest street the segments layer
    // names within 200 m - the service road beside it, not the main road 2 km off.
    const cards = screen
      .getByRole("heading", { name: "Largest learned changes" })
      .closest("section") as HTMLElement;
    await waitFor(() =>
      expect(
        within(cards)
          .getAllByRole("article")
          .map((a) => a.getAttribute("aria-label")),
      ).toEqual([
        "Pipe MUM-E037896 near Wadala Depot: blockage 0.15 to 0.49",
        "Dr Babasaheb Ambedkar Marg: blockage 0.15 to 0.22",
      ]),
    );
    // The biggest-change tile names it the same way.
    expect(within(strip).getByText("Pipe MUM-E037896 near Wadala Depot")).toBeInTheDocument();
    expect(screen.queryByText(/unnamed/i)).not.toBeInTheDocument();
    // A traffic anomaly the bake did not name is not titled by its raw segment id.
    expect(screen.queryByText("S0-342")).not.toBeInTheDocument();
  });

  it("on a run with a summary, leads with the product's own figures", async () => {
    stub({
      "/v1/runs": { body: RUNS },
      "/v1/drains/health": { body: NEW_HEALTH },
      "/v1/observations": { body: OBSERVATIONS },
    });
    renderScreen();
    const strip = await screen.findByRole("region", { name: "What this cycle learned" });
    expect(within(strip).getByText(/173 up, 28 down/)).toBeInTheDocument();
    expect(within(strip).getByText("28.5 %")).toBeInTheDocument();
    expect(
      within(strip).getByText("28.4 % assumed from land use, +0.09 points learned (+55.6 m³/s)"),
    ).toBeInTheDocument();
    // Observation counts follow the list the strip draws.
    expect(
      within(strip).getByText("1 traffic, 1 citizen; 1 synthetic, 1 real"),
    ).toBeInTheDocument();
    expect(within(strip).getByText("off Eastern Freeway")).toBeInTheDocument();

    // The map gets the whole network quiet and only the moved pipes on top.
    await waitFor(() => expect(mapProps.last).not.toBeNull());
    const drains = mapProps.last?.drains as { learnedOverlay?: { learned: { id: string }[] } };
    expect(drains.learnedOverlay?.learned.map((d) => d.id)).toEqual(["MUM-E037896", "MUM-E020001"]);
    expect(mapProps.last?.showSurcharge).toBe(false);
  });

  it("keeps Before and After named exactly, and slides to either", async () => {
    stub({
      "/v1/runs": { body: RUNS },
      "/v1/drains/health": { body: NEW_HEALTH },
      "/v1/observations": { body: OBSERVATIONS },
    });
    renderScreen();
    await screen.findByText(/pipes moved this cycle/);
    // Anchored, because the e2e suites click these by their exact names.
    const before = screen.getByRole("button", { name: /^Before$/ });
    const after = screen.getByRole("button", { name: /^After$/ });
    expect(after).toHaveAttribute("aria-pressed", "true");
    act(() => {
      fireEvent.click(before);
    });
    // No viewport in jsdom, so the split jumps rather than slides: every learned pipe at its prior.
    await waitFor(() => expect(before).toHaveAttribute("aria-pressed", "true"));
    const drains = mapProps.last?.drains as { learnedOverlay?: { splitLon: number } };
    expect(drains.learnedOverlay?.splitLon).toBe(180);
  });

  it("puts every observation and the full table behind disclosures that say they are open", async () => {
    stub({
      "/v1/runs": { body: RUNS },
      "/v1/drains/health": { body: NEW_HEALTH },
      "/v1/observations": { body: OBSERVATIONS },
    });
    renderScreen();
    const seeObs = await screen.findByRole("button", { name: "See all observations (2)" });
    expect(seeObs).toHaveAttribute("aria-expanded", "false");
    const obsPanel = document.getElementById(seeObs.getAttribute("aria-controls") ?? "");
    expect(obsPanel).not.toBeNull();
    fireEvent.click(seeObs);
    expect(screen.getByRole("button", { name: "Hide observations" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    // An anomaly the bake did not place reads as what is true, never as "Unnamed road".
    expect(within(obsPanel!).getByText(UNNAMED_PLACE)).toBeInTheDocument();
    expect(UNNAMED_PLACE).toBe("Street not recorded");
    expect(within(obsPanel!).queryByText(/unnamed/i)).not.toBeInTheDocument();
    expect(within(obsPanel!).getAllByText("Synthetic")).toHaveLength(1);

    const seePipes = screen.getByRole("button", { name: /See all pipes/ });
    fireEvent.click(seePipes);
    const table = screen.getByRole("table");
    expect(within(table).getByRole("button", { name: /Learned change/ })).toBeInTheDocument();
    // It opens on what Pulse learned, not on the land-use prior at the top of the blockage order.
    expect(
      within(table)
        .getAllByRole("columnheader")
        .find((h) => h.textContent?.includes("Learned change")),
    ).toHaveAttribute("aria-sort", "descending");
    expect(within(table).getByText("off Eastern Freeway")).toBeInTheDocument();
    expect(within(table).getByText("+0.34")).toBeInTheDocument();

    // The CSV export stays visible and names the run on screen.
    expect(screen.getByRole("button", { name: /Export desilting priority/ })).toBeVisible();
  });

  it("lists every observation on the strip, each a button named in words", async () => {
    stub({
      "/v1/runs": { body: RUNS },
      "/v1/drains/health": { body: NEW_HEALTH },
      "/v1/observations": { body: OBSERVATIONS },
    });
    renderScreen();
    const strip = await screen.findByRole("list", {
      name: "Observations assimilated, oldest first",
    });
    const dots = within(strip).getAllByRole("button");
    expect(dots).toHaveLength(2);
    expect(dots.map((d) => d.getAttribute("aria-label"))).toEqual(
      expect.arrayContaining([
        expect.stringMatching(
          /^Traffic at 3 km\/h against 27 km\/h usual, street not recorded, 08:40, synthetic; raised/,
        ),
        expect.stringMatching(
          /^Citizen report, knee deep \(45 cm\) at Sir Bhalchandra Road, 08:40; lowered/,
        ),
      ]),
    );
    fireEvent.click(dots[1]);
    expect(screen.getByText("Selected observation")).toBeInTheDocument();
  });

  it("draws a cycle's traffic anomalies as one mark with a count, and lists them when picked", async () => {
    const second = {
      ...OBSERVATIONS.observations[0],
      segment_id: "S0-343",
      edge_id: "MUM-E037896",
      place: "Eastern Freeway",
      beta_before: 0.15,
      beta_after: 0.4926,
    };
    stub({
      "/v1/runs": { body: RUNS },
      "/v1/drains/health": { body: NEW_HEALTH },
      "/v1/observations": {
        body: { ...OBSERVATIONS, observations: [...OBSERVATIONS.observations, second] },
      },
    });
    renderScreen();
    const strip = await screen.findByRole("list", {
      name: "Observations assimilated, oldest first",
    });
    const marks = within(strip).getAllByRole("button");
    // Two traffic anomalies at 08:40 share one mark; the report at 08:40 keeps its own lane.
    expect(marks).toHaveLength(2);
    const group = marks.find((m) =>
      /^2 traffic anomalies at 08:40, all synthetic; 2 raised/.test(
        m.getAttribute("aria-label") ?? "",
      ),
    );
    expect(group).toBeDefined();
    expect(group).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(group!);
    expect(group).toHaveAttribute("aria-pressed", "true");
    const picked = screen.getByRole("region", { name: "Selected on the strip" });
    expect(within(picked).getByText("2 observations at 08:40")).toBeInTheDocument();
    expect(within(picked).getByText("Eastern Freeway")).toBeInTheDocument();
    // The map frames the pipes the group landed on rather than diving into one of them.
    await waitFor(() => expect((mapProps.last?.focus as { zoom?: number } | null)?.zoom).toBe(13));
    fireEvent.click(within(picked).getByRole("button", { name: "Clear" }));
    expect(screen.queryByRole("region", { name: "Selected on the strip" })).not.toBeInTheDocument();
  });
});

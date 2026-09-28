import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { WhatIfScreen } from "@/app/whatif/whatif-screen";
import { useRunStore } from "@/lib/stores/run";
import { useUiStore } from "@/lib/stores/ui";

import fixture from "@/lib/api/__tests__/whatif-emulator.fixture.json";

/** The query string the lab reads at mount (P7.11). Set it before `renderScreen`. */
const nav = vi.hoisted(() => ({ params: new URLSearchParams() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/whatif",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => nav.params,
}));

function renderScreen() {
  return render(
    <TooltipProvider>
      <WhatIfScreen />
    </TooltipProvider>,
  );
}

describe("WhatIfScreen", () => {
  beforeEach(() => {
    nav.params = new URLSearchParams();
    useRunStore.getState().clear();
    useUiStore.getState().closeOverlays();
  });

  it("labels the emulator honestly", () => {
    renderScreen();
    // The screen is Kalpana (ADR-0085), and the page answers the name with its English gloss.
    const heading = screen.getByRole("heading", { level: 1, name: "Kalpana" });
    expect(heading).toHaveAccessibleDescription(/^What-if lab/);
    expect(
      screen.getByText("Reduced-order emulator calibrated to VARUNA-Twin"),
    ).toBeInTheDocument();
  });

  it("offers both the emulator and the physics check", () => {
    renderScreen();

    // Flash-lite landed in Phase 7, so this one runs: about 60 ms against a baked run.
    expect(screen.getByRole("button", { name: "Run what-if" })).toBeEnabled();
    // And so does the check since P7.8: two Twin runs on a 990 m window, 2.4-4.7 s warm.
    const physics = screen.getByRole("button", { name: "Physics check" });
    expect(physics).toBeEnabled();
    expect(physics).not.toHaveAttribute("aria-describedby");
  });

  it("prints the endpoint's disagreement, the Twin's change beside the emulator's", async () => {
    // The shape `POST /v1/whatif/physics-check` returns, trimmed to what the panel reads, with the
    // 2 July 08:40 figures measured at Bandra Talao on rain +30 %.
    const body = {
      run_id: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
      summary: "Emulator vs physics: max difference 5.1 cm at Bandra Talao",
      tolerance_cm: 5,
      agrees: false,
      max_diff_cm: 5.07,
      max_diff_hotspot: "Bandra Talao",
      hotspots: [
        {
          hotspot_id: "MUM-HS-20",
          name: "Bandra Talao",
          emulator_delta_cm: 1.2,
          twin_delta_cm: 6.27,
          diff_cm: 5.07,
        },
      ],
      hotspots_outside_window: ["Hindmata junction"],
      window: { size_m: 990, nodes: 631, edges: 620, centre_hotspot: "Bandra Talao" },
      mass_balance: { baseline: 0, scenario: 0, budget: 0.001 },
      ms: 4320,
      budget_ms: 10000,
      notes: [],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/v1/whatif/physics-check")) {
        return new Response(JSON.stringify(body), { status: 200 });
      }
      return new Response(JSON.stringify({ features: [], runs: [] }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    try {
      renderScreen();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Physics check" }));
      });
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("/v1/whatif/physics-check")),
      ).toBe(true);
      // One decimal beside a 5 cm tolerance: "5 cm" next to "Outside tolerance" would read as
      // a contradiction.
      expect(await screen.findByText("Outside tolerance")).toBeInTheDocument();
      expect(screen.getAllByText("5.1 cm").length).toBeGreaterThan(0);
      expect(screen.getByText("+6.3 cm")).toBeInTheDocument();
      expect(
        screen.getByText(/Not in the window, so not checked: Hindmata junction/),
      ).toBeInTheDocument();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("offers every emulator lever, and names each set lever in the scenario line", () => {
    renderScreen();

    // "Clean top 14 by blockage" and the pump plan are levers of POST /v1/whatif now: each switch
    // is live, and its sub-copy says what the endpoint does with it, including the lower bound.
    const top = screen.getByRole("switch", { name: "Clean top 14 by blockage" });
    const pump = screen.getByRole("switch", { name: "Pump plan" });
    for (const lever of [top, pump]) {
      expect(lever).not.toHaveAttribute("aria-disabled", "true");
    }
    const pumpHelp = document.getElementById(pump.getAttribute("aria-describedby") as string);
    expect(pumpHelp).toHaveTextContent("A lower bound");
    expect(pumpHelp).toHaveTextContent("Synthetic pump inventory");

    expect(screen.getByText(/^Scenario ready to run:/)).toHaveTextContent(
      "Scenario ready to run: Rain 1.0x, tide +0.0 m.",
    );
    act(() => {
      fireEvent.click(top);
      fireEvent.click(pump);
    });
    expect(screen.getByText(/^Scenario ready to run:/)).toHaveTextContent(
      "Rain 1.0x, tide +0.0 m, top 14 pipes cleaned, pump plan on.",
    );
    // The tide note says what happens now, before Run: the tide is not refused, it runs on the
    // full physics, because the emulator has no sea level to move.
    expect(screen.getByText(/^Tide runs the full physics/)).toHaveTextContent(
      "The emulator cannot move the sea.",
    );
    // With the tide at the run's own, there is nothing to reset it to.
    expect(screen.getByRole("button", { name: "Reset the tide offset to +0.0 m" })).toBeDisabled();
  });

  it("draws every changed street, tables each hotspot and names the largest changes", async () => {
    // The body `POST /v1/whatif` produced on the five-street test run (rain 1.5x, tide +0.5 m,
    // the pump plan and "clean top 1"): services/api/tests/test_whatif_emulator.py.
    const layer = {
      type: "FeatureCollection",
      features: ["S-A", "S-B", "S-C", "S-D"].map((id, i) => ({
        type: "Feature",
        properties: { segment_id: id, name: null, road_class: "primary" },
        geometry: {
          type: "LineString",
          coordinates: [
            [72.84 + i * 0.001, 19.01],
            [72.841 + i * 0.001, 19.011],
          ],
        },
      })),
    };
    const fetchMock = vi.fn(async (input: RequestInit | RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/v1/whatif")) {
        return new Response(JSON.stringify(fixture.emulator), { status: 200 });
      }
      if (url.includes("/layers/segments")) {
        return new Response(JSON.stringify(layer), { status: 200 });
      }
      void init;
      return new Response(JSON.stringify({ features: [], runs: [] }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    try {
      renderScreen();
      await act(async () => {
        fireEvent.click(screen.getByRole("switch", { name: "Pump plan" }));
      });
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Run what-if" }));
      });
      const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/v1/whatif"));
      expect(JSON.parse(String((call?.[1] as RequestInit).body))).toMatchObject({
        pump_plan: true,
      });

      // The endpoint's own count, and how much of it the map could draw: five changed, four
      // with geometry in the served layer (S-E has none in this layer).
      expect(await screen.findByText(fixture.emulator.summary)).toBeInTheDocument();
      expect(screen.getByText(/^Drawn:/)).toHaveTextContent(
        "Drawn: 4 of 5. 1 changed segment has no geometry in the served street layer",
      );
      // Tide, clean-top and pump lines, in the endpoint's words.
      expect(screen.getByText(/not in this answer: the emulator has no sea level/)).toBeVisible();
      expect(screen.getByText(/^Top 1 pipe by learned blockage, city-wide: 1 pipe/)).toBeVisible();
      expect(screen.getByText(/^Pump plan: 3 segments drained/)).toHaveTextContent(
        "Synthetic pump inventory",
      );

      // Per hotspot from the register, with its minutes above 30 cm; the street table by name,
      // with the street the run had dry saying so rather than showing 0 cm.
      expect(screen.getByText("Test junction")).toBeInTheDocument();
      expect(screen.getByText("Minutes above 30 cm")).toBeInTheDocument();
      expect(screen.getByText("Segment S-D")).toBeInTheDocument();
      expect(screen.getAllByText(/^Below/).length).toBeGreaterThan(0);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("says a tide-only physics check runs on the Twin instead of printing a disagreement", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/v1/whatif/physics-check")) {
        return new Response(JSON.stringify(fixture.physics_tide), { status: 200 });
      }
      return new Response(JSON.stringify({ features: [], runs: [] }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    try {
      renderScreen();
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Physics check" }));
      });
      expect(await screen.findByText("Runs on the Twin")).toBeInTheDocument();
      expect(screen.queryByText("Outside tolerance")).not.toBeInTheDocument();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("holds the result panels in their empty states until a what-if has run", () => {
    renderScreen();
    // Both result tables, per hotspot and largest street changes, wait for the first run.
    expect(screen.getAllByText("No what-if yet")).toHaveLength(2);
    expect(screen.getAllByText("Set the controls and run one.")).toHaveLength(2);
    // The physics check answers now (P7.8), so its empty state is the ordinary "not run yet"
    // and the button that runs it is live rather than disabled with a reason.
    expect(screen.getByText("Physics check not run")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Physics check" })).toBeEnabled();
  });

  it("carries a hotspot's segments in from the deep link, with the measured ceiling beside them", () => {
    // What "Clean in what-if" on Hindmata puts in the address bar: the junction's own road
    // segments, the junction it was pressed on, and the cycle the console was showing.
    nav.params = new URLSearchParams({
      segments: "S100841069-000,S100841079-000,S102172139-001",
      from: "Hindmata junction",
      run: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
    });
    renderScreen();

    expect(screen.getByText("S100841069-000")).toBeInTheDocument();
    expect(screen.getByText("S102172139-001")).toBeInTheDocument();
    expect(screen.getByText("3 segments")).toBeInTheDocument();
    expect(screen.getByText("From Hindmata junction.")).toBeInTheDocument();
    // The chips are a lever with a ceiling, and the ceiling is measured (ADR-0042). Printing the
    // segments without it would let the operator expect a junction to drain.
    expect(screen.getByText(/Measured ceiling/)).toHaveTextContent(
      "cleaning all 21,296 segments at once moves the deepest street 3.5 cm (ADR-0042)",
    );
    // The scenario line counts what will be sent, so the panel and the request agree.
    expect(screen.getByText(/^Scenario ready to run:/)).toHaveTextContent(
      "Rain 1.0x, tide +0.0 m, 3 segments cleaned.",
    );
  });

  it("says so when the link asks for more segments than the lever carries", () => {
    // The URL is hand-editable, so a longer list is possible; silently running a smaller
    // scenario than the address bar describes is the defect this line exists to prevent.
    const ids = Array.from({ length: 20 }, (_, i) => `S1008410${String(i).padStart(2, "0")}-000`);
    nav.params = new URLSearchParams({ segments: ids.join(",") });
    renderScreen();

    expect(screen.getByText("14 segments")).toBeInTheDocument();
    expect(screen.getByText(/The link asked for/)).toHaveTextContent(
      "The link asked for 20 segments; the first 14 are loaded.",
    );
  });

  it("tells the lab about a change from the handler, never while the controls render", () => {
    // `WhatIfControls` used to call `onChange` inside its state updater. React runs an updater
    // while it renders the component that owns it, and the lab's `onChange` is its own setter,
    // so the lab was updated mid-render: "Cannot update a component while rendering a different
    // component". The first change of a batch escapes because React computes it eagerly; the
    // second is the one that runs in render, so two removals in one act is the reproduction.
    nav.params = new URLSearchParams({
      segments: "S100841069-000,S100841079-000,S102172139-001",
    });
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      renderScreen();
      act(() => {
        fireEvent.click(screen.getByRole("button", { name: "Remove segment S100841069-000" }));
        fireEvent.click(screen.getByRole("button", { name: "Remove segment S100841079-000" }));
      });

      expect(errors.mock.calls.flat().map(String).join("\n")).not.toMatch(
        /while rendering a different component/,
      );
      // Both removals land: the second composes on the first rather than restoring its segment
      // from the render both buttons were drawn in. The lab's scenario line agrees with the chips.
      expect(screen.getByText("1 segment")).toBeInTheDocument();
      expect(screen.queryByText("S100841069-000")).not.toBeInTheDocument();
      expect(screen.getByText(/^Scenario ready to run:/)).toHaveTextContent(
        "Rain 1.0x, tide +0.0 m, 1 segment cleaned.",
      );
    } finally {
      errors.mockRestore();
    }
  });
});

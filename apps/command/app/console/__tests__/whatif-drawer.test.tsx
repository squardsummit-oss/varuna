/**
 * The console's what-if drawer (SPEC.md 7.7, the W key): the lab's deep link, its remembered
 * scenario, and the same emulator-then-Twin flow (motion M31), with the answer handed to the
 * console's map as the difference layer.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import emulatorFixture from "@/lib/api/__tests__/whatif-emulator.fixture.json";
import { TWIN_DONE, TWIN_STARTED } from "@/lib/api/__tests__/whatif-twin.fixture";

import { WHATIF_DRAWER_KEY, WhatIfDrawer, type WhatIfDiff } from "../whatif-drawer";

const nav = vi.hoisted(() => ({ params: new URLSearchParams() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/console",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => nav.params,
}));

const RUN_ID = TWIN_DONE.run_id;

function stubApi() {
  const twin: unknown[] = [TWIN_STARTED, TWIN_DONE];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      });
    if (url.includes("/v1/whatif/twin")) {
      return json(twin.length > 1 ? twin.shift() : twin[0], init?.method === "POST" ? 202 : 200);
    }
    if (url.endsWith("/v1/whatif")) return json(emulatorFixture.emulator);
    return json({ runs: [], features: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderDrawer(onDiff = vi.fn<(diff: WhatIfDiff | null) => void>()) {
  const view = render(
    <TooltipProvider>
      <WhatIfDrawer runId={RUN_ID} onDiff={onDiff} onClose={vi.fn()} />
    </TooltipProvider>,
  );
  return { ...view, onDiff };
}

describe("WhatIfDrawer", () => {
  beforeEach(() => {
    nav.params = new URLSearchParams();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
  });

  it("opens on the scenario last asked in it, and the deep link's segments win over it", () => {
    window.sessionStorage.setItem(
      WHATIF_DRAWER_KEY,
      JSON.stringify({
        rainScale: 1.3,
        tideOffsetM: 0.5,
        cleanTop14: true,
        pumpPlan: false,
        cleanedSegments: ["S-OLD"],
      }),
    );
    nav.params = new URLSearchParams({
      segments: "S100841069-000,S100841079-000",
      from: "Hindmata junction",
    });
    renderDrawer();

    // Named as the rail and the lab name it, and still "What-if" to a screen reader and the e2e.
    expect(screen.getByRole("complementary", { name: "What-if" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Kalpana" })).toBeInTheDocument();
    expect(screen.getByText("1.3x")).toBeInTheDocument();
    expect(screen.getByText("+0.5 m")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Clean top 14 by blockage" })).toBeChecked();
    // The link is the newer request: its segments replace the remembered one.
    expect(screen.getByText("S100841069-000")).toBeInTheDocument();
    expect(screen.queryByText("S-OLD")).toBeNull();
    expect(screen.getByText("From Hindmata junction.")).toBeInTheDocument();
  });

  it("opens on the defaults when storage throws", () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    try {
      renderDrawer();
      expect(screen.getByText("1.0x")).toBeInTheDocument();
      expect(screen.getByText("+0.0 m")).toBeInTheDocument();
    } finally {
      getItem.mockRestore();
    }
  });

  it("runs the lab's flow: the emulator's part first, then the Twin's answer on the map", async () => {
    window.sessionStorage.setItem(
      WHATIF_DRAWER_KEY,
      JSON.stringify({ rainScale: 1.3, tideOffsetM: 1, cleanTop14: false, pumpPlan: false }),
    );
    const fetchMock = stubApi();
    const { onDiff } = renderDrawer();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Run what-if" }));
    });
    // The question is remembered for the next time the drawer opens.
    expect(JSON.parse(window.sessionStorage.getItem(WHATIF_DRAWER_KEY) ?? "{}")).toMatchObject({
      rainScale: 1.3,
      tideOffsetM: 1,
    });
    // Both engines were asked about the console's cycle.
    const posted = fetchMock.mock.calls
      .filter(([, init]) => init?.method === "POST")
      .map(([url, init]) => [String(url), JSON.parse(String(init?.body))] as const);
    expect(posted.find(([url]) => url.endsWith("/v1/whatif"))?.[1]).toMatchObject({
      run_id: RUN_ID,
      tide_offset_m: 1,
    });
    expect(posted.find(([url]) => url.includes("/v1/whatif/twin"))?.[1]).toMatchObject({
      run_id: RUN_ID,
      tide_offset_m: 1,
    });

    // The Twin lands and replaces the emulator's answer; the map gets the Twin's rows.
    await waitFor(
      () =>
        expect(
          screen.getAllByText("VARUNA-Twin, full city, prior blockage").length,
        ).toBeGreaterThan(1),
      { timeout: 3000 },
    );
    expect(screen.getByText("What-if ready")).toBeInTheDocument();
    expect(
      screen.getByText(/more sea crossed onto the land than with the tide as forecast/),
    ).toBeInTheDocument();
    expect(screen.getByText("Test junction")).toBeInTheDocument();
    const last = onDiff.mock.calls.at(-1)?.[0];
    expect(last?.deltaCm.get("S11-000")).toBeCloseTo(114.7);
    expect([...(last?.deltaCm.keys() ?? [])].sort()).toEqual(["S10-000", "S11-000"]);
  });

  it("takes its answer off the map when it closes", () => {
    const { onDiff, unmount } = renderDrawer();
    unmount();
    expect(onDiff).toHaveBeenLastCalledWith(null);
  });
});

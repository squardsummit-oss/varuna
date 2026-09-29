/**
 * The what-if lab's states, one per test (SPEC.md 7.7, motion M31): the emulator alone, the
 * full-city Twin running a tide question, the Twin's answer replacing the emulator's, a Twin run
 * cancelled, and a scenario that moved nothing.
 *
 * The API is a fetch stub routed by path, answering with bodies the real handlers produced
 * (`whatif-emulator.fixture.json`, `whatif-twin.fixture.ts`). The Twin job is polled on real
 * timers, so a state that needs a poll waits for one.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { WhatIfScreen } from "@/app/whatif/whatif-screen";
import { useRunStore } from "@/lib/stores/run";
import { useUiStore } from "@/lib/stores/ui";

import emulatorFixture from "@/lib/api/__tests__/whatif-emulator.fixture.json";
import {
  TWIN_CANCELLED,
  TWIN_DONE,
  TWIN_DONE_WITH_PUMPS,
  TWIN_RUNNING,
  TWIN_STARTED,
} from "@/lib/api/__tests__/whatif-twin.fixture";

const nav = vi.hoisted(() => ({ params: new URLSearchParams() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/whatif",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => nav.params,
}));

const RUN_ID = TWIN_DONE.run_id;

/** The served street layer: the Twin fixture's two streets and the emulator's five. */
const LAYER = {
  type: "FeatureCollection",
  features: ["S10-000", "S11-000", "S-A", "S-B", "S-C", "S-D"].map((id, i) => ({
    type: "Feature",
    properties: { segment_id: id, name: id === "S11-000" ? "Senapati Bapat Marg" : null },
    geometry: {
      type: "LineString",
      coordinates: [
        [72.84 + i * 0.001, 19.01],
        [72.841 + i * 0.001, 19.011],
      ],
    },
  })),
};

/**
 * The endpoint's answer to a question with no lever set, as `POST /v1/whatif` sends it
 * (`services/api/tests/test_whatif_emulator.py` pins the fixture to the handler): nothing moved,
 * the emulator never loaded, and every hotspot of the run tabled unchanged from its own series.
 */
const NOTHING = emulatorFixture.nothing;

/** `GET /v1/whatif/twin` on an API that runs the Twin: every tide, nothing stored. */
const OPEN_OFFER = {
  run_id: RUN_ID,
  enabled: true,
  answers: [],
  tide_offsets_m: [],
  elsewhere: [],
  message: "This server runs the full-city Twin.",
};

/**
 * The same on the deployed API (`VARUNA_WHATIF_TWIN=0`): it runs no Twin and holds the stored
 * answer for +0.5 m at rain 1.0x, as `services/api/tests/test_whatif_twin.py` pins it.
 */
const STORED_OFFER = {
  run_id: RUN_ID,
  enabled: false,
  answers: [{ rain_scale: 1, tide_offset_m: 0.5, cleaned: false, source: "shipped" }],
  tide_offsets_m: [0.5],
  elsewhere: [],
  message:
    "The tide is answered here from stored Twin runs only: +0.5 m at rain 1.0x with no pipes cleaned.",
};

/** The stored +0.5 m answer as `POST /v1/whatif/twin` serves it from the shipped copy. */
const TWIN_STORED = {
  ...TWIN_DONE,
  baseline_steps: 0,
  n_steps: 6,
  step: 6,
  scenario: { ...TWIN_DONE.scenario, rain_scale: 1, tide_offset_m: 0.5 },
  cache: {
    ...TWIN_DONE.cache,
    source: "shipped",
    label: "Answered from the cache shipped with the repository in demo/whatif.",
  },
};

interface Routes {
  offer?: unknown;
  emulator?: unknown;
  /** Answers to `POST /v1/whatif/twin`, then to each poll, in order; the last repeats. */
  twin?: unknown[];
  cancel?: unknown;
}

function stubApi(routes: Routes) {
  const twin = [...(routes.twin ?? [])];
  const next = () => (twin.length > 1 ? twin.shift() : twin[0]);
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      });
    if (url.includes("/cancel")) return json(routes.cancel ?? TWIN_RUNNING);
    if (url.includes("/v1/whatif/twin?") || (url.endsWith("/v1/whatif/twin") && method === "GET")) {
      return json(routes.offer ?? OPEN_OFFER);
    }
    if (url.includes("/v1/whatif/twin")) return json(next(), method === "POST" ? 202 : 200);
    if (url.includes("/v1/whatif/physics-check")) return json(emulatorFixture.physics_tide);
    if (url.endsWith("/v1/whatif")) return json(routes.emulator ?? emulatorFixture.emulator);
    if (url.includes("/layers/segments")) return json(LAYER);
    if (url.includes(`/v1/runs/${encodeURIComponent(RUN_ID)}`)) {
      return json({ run_id: RUN_ID, city: "mumbai" });
    }
    return json({ runs: [], features: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function calls(fetchMock: ReturnType<typeof stubApi>, fragment: string, method = "POST") {
  return fetchMock.mock.calls.filter(
    ([url, init]) => String(url).includes(fragment) && (init?.method ?? "GET") === method,
  );
}

function renderLab() {
  return render(
    <TooltipProvider>
      <WhatIfScreen />
    </TooltipProvider>,
  );
}

/** Set the tide offset slider to its maximum, +1.0 m, the way a keyboard user would. */
function tideToMax() {
  const tide = screen.getByRole("region", { name: "Tide offset" });
  // Base UI draws the thumb's range input hidden until layout, which jsdom never does.
  const input = within(tide).getByRole("slider", { hidden: true });
  act(() => {
    fireEvent.keyDown(input, { key: "End" });
  });
  expect(screen.getByText("+1.0 m")).toBeInTheDocument();
}

async function pressRun() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Run what-if" }));
  });
}

describe("what-if lab states", () => {
  beforeEach(() => {
    nav.params = new URLSearchParams({ run: RUN_ID });
    useRunStore.getState().clear();
    useUiStore.getState().closeOverlays();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("answers from the emulator alone when the tide stays at the run's own", async () => {
    const fetchMock = stubApi({});
    renderLab();
    await pressRun();

    expect(await screen.findByText(emulatorFixture.emulator.summary)).toBeInTheDocument();
    // The engine that answered is the one the header and the answer name.
    expect(
      screen.getAllByText("Reduced-order emulator calibrated to VARUNA-Twin").length,
    ).toBeGreaterThan(1);
    expect(screen.queryByText("Emulator, tide not included")).not.toBeInTheDocument();
    // Its measured skill sits beside the answer, not behind Details (section 15, 4:30).
    expect(screen.getByText("Emulator skill: RMSE 5.7 cm, CSI 0.09 at 30 cm")).toBeVisible();
    // No tide asked, so no Twin job and no M31 bar.
    expect(calls(fetchMock, "/v1/whatif/twin")).toHaveLength(0);
    expect(screen.queryByRole("progressbar", { name: "Full-city Twin run" })).toBeNull();
    // The request named the cycle the deep link brought.
    const [, init] = calls(fetchMock, "/v1/whatif")[0] ?? [];
    expect(JSON.parse(String(init?.body))).toMatchObject({ run_id: RUN_ID, tide_offset_m: 0 });
  });

  it("shows the emulator's part at once and the Twin working on the tide, with Cancel", async () => {
    const fetchMock = stubApi({ twin: [TWIN_STARTED, TWIN_RUNNING] });
    renderLab();
    tideToMax();
    // The Reset button brings the tide back in one press, so it is live once the tide moves.
    expect(screen.getByRole("button", { name: "Reset the tide offset to +0.0 m" })).toBeEnabled();
    await pressRun();

    // The emulator's answer lands first, labelled as leaving the tide out.
    expect(await screen.findByText("Emulator, tide not included")).toBeInTheDocument();
    expect(screen.getByText(emulatorFixture.emulator.summary)).toBeInTheDocument();
    // And the same question went to the Twin, with the tide the operator set.
    const [, init] = calls(fetchMock, "/v1/whatif/twin")[0] ?? [];
    expect(JSON.parse(String(init?.body))).toMatchObject({ tide_offset_m: 1, run_id: RUN_ID });

    // M31: the bar, step k of n from the job's own numbers, and Cancel while it runs.
    const bar = screen.getByRole("progressbar", { name: "Full-city Twin run" });
    await waitFor(() => expect(screen.getByText(/^Twin 17 of 36 steps/)).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(bar).toHaveAttribute("aria-valuenow", "47");
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
    // Until the Twin lands, the header still names the emulator.
    expect(screen.queryByText("VARUNA-Twin, full city, prior blockage")).toBeNull();
  });

  it("replaces the emulator's answer with the Twin's, with the sea it let in", async () => {
    stubApi({ twin: [TWIN_STARTED, TWIN_DONE] });
    renderLab();
    tideToMax();
    await pressRun();

    // The badge and the answer both name the engine that answered.
    await waitFor(
      () =>
        expect(
          screen.getAllByText("VARUNA-Twin, full city, prior blockage").length,
        ).toBeGreaterThan(1),
      { timeout: 3000 },
    );
    expect(screen.queryByText("Emulator, tide not included")).toBeNull();
    // The tide's outcome, every number the job's own.
    // The sea is what crossed onto the land against the same run with the tide as forecast
    // (`sea_to_land_change_m3`), never the clamp's gross tally on the sea cells (`tide_in_m3`).
    expect(
      screen.getByText(
        "1,13,542 m3 more sea crossed onto the land than with the tide as forecast; 12 streets newly impassable for cars (above 30 cm); 1 hotspot moved by 0.5 cm or more.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/1,16,027 m3/)).toBeNull();
    expect(screen.getByText("2 segments deeper, each by 0.5 cm or more.")).toBeInTheDocument();
    expect(screen.getByText(/^Twin finished: 6 of 6 steps/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    // The hotspot table and the street table read the Twin's rows: the register's junction with
    // its minutes above 30 cm, and the street named from the served layer.
    expect(screen.getByText("Test junction")).toBeInTheDocument();
    expect(screen.getByText("Senapati Bapat Marg")).toBeInTheDocument();
    // The count drawn is the count the answer lists, against the street layer's geometry.
    expect(screen.getByText(/^Drawn:/)).toHaveTextContent("Drawn: 2 of 2.");
  });

  it("keeps the emulator's answer and says where the Twin stopped when it is cancelled", async () => {
    const fetchMock = stubApi({
      twin: [TWIN_STARTED, TWIN_RUNNING, TWIN_CANCELLED],
      cancel: TWIN_RUNNING,
    });
    renderLab();
    tideToMax();
    await pressRun();
    const cancel = await screen.findByRole("button", { name: "Cancel" }, { timeout: 3000 });
    await act(async () => {
      fireEvent.click(cancel);
    });
    expect(calls(fetchMock, "/cancel")).toHaveLength(1);

    expect(await screen.findByText("Twin run cancelled", {}, { timeout: 3000 })).toBeVisible();
    expect(
      screen.getByText(
        "Cancelled during the scenario's Twin at step 17 of 36. Nothing was stored.",
      ),
    ).toBeInTheDocument();
    // The emulator's answer stays, still labelled as leaving the tide out.
    expect(screen.getByText("Emulator, tide not included")).toBeInTheDocument();
    expect(screen.getByText(emulatorFixture.emulator.summary)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });

  it("replaces its own running tide question instead of being refused as busy", async () => {
    // The server runs one full-city scenario at a time. A second tide question from the same
    // screen cancels the first, waits out the busy answer while it stops, then starts.
    const second = {
      ...TWIN_STARTED,
      job_id: "b2c4d6e8f0a1",
      scenario: { ...TWIN_STARTED.scenario, tide_offset_m: 0.5 },
    };
    const busy = {
      error: {
        code: "whatif_busy",
        message: "A full-city what-if is already running on this server.",
        run_id: RUN_ID,
      },
    };
    const starts: unknown[] = [TWIN_STARTED, busy, second];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), {
          status,
          headers: { "content-type": "application/json" },
        });
      if (url.includes("/cancel")) return json(TWIN_RUNNING);
      if (url.includes("/v1/whatif/twin") && init?.method === "POST") {
        const body = starts.length > 1 ? starts.shift() : starts[0];
        return json(body, body === busy ? 503 : 202);
      }
      if (url.includes("/v1/whatif/twin/")) {
        return json(url.includes(second.job_id) ? second : TWIN_RUNNING);
      }
      if (url.endsWith("/v1/whatif")) return json(emulatorFixture.emulator);
      if (url.includes("/layers/segments")) return json(LAYER);
      return json({ runs: [], features: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderLab();
    tideToMax();
    await pressRun();
    await waitFor(() => expect(screen.getByText(/^Twin 17 of 36 steps/)).toBeInTheDocument(), {
      timeout: 3000,
    });

    const input = within(screen.getByRole("region", { name: "Tide offset" })).getByRole("slider", {
      hidden: true,
    });
    // One press per render: the slider is controlled, so presses batched into one act all step
    // from the same value.
    for (let i = 0; i < 5; i += 1) {
      act(() => {
        fireEvent.keyDown(input, { key: "ArrowLeft" });
      });
    }
    expect(screen.getByText("+0.5 m")).toBeInTheDocument();
    await pressRun();

    // The first job was cancelled, the busy answer waited out, and the new question started.
    await waitFor(
      () =>
        expect(
          calls(fetchMock, "/v1/whatif/twin").filter(([url]) => !String(url).includes("/cancel")),
        ).toHaveLength(3),
      { timeout: 3000 },
    );
    expect(calls(fetchMock, `/v1/whatif/twin/${TWIN_STARTED.job_id}/cancel`)).toHaveLength(1);
    const last = calls(fetchMock, "/v1/whatif/twin").at(-1);
    expect(JSON.parse(String(last?.[1]?.body))).toMatchObject({ tide_offset_m: 0.5 });
    expect(screen.queryByText(/already running on this server/)).toBeNull();
  });

  it("offers only the stored tides on an API that cannot run the Twin, and says so", async () => {
    const fetchMock = stubApi({ offer: STORED_OFFER, twin: [TWIN_STORED] });
    renderLab();

    // The tide is the values this API can answer, as chips, never a slider reaching the rest.
    const choices = await screen.findByRole("radiogroup", { name: "Tide offset" });
    expect(
      within(choices)
        .getAllByRole("radio")
        .map((radio) => radio.parentElement?.textContent),
    ).toEqual(["+0.0 m", "+0.5 m"]);
    const tide = screen.getByRole("region", { name: "Tide offset" });
    expect(within(tide).queryByRole("slider", { hidden: true })).toBeNull();
    expect(
      screen.getByText("Stored Twin answers on this server, at rain 1.0x."),
    ).toBeInTheDocument();

    act(() => {
      fireEvent.click(within(choices).getByLabelText("+0.5 m"));
    });
    await pressRun();
    const [, init] = calls(fetchMock, "/v1/whatif/twin")[0] ?? [];
    expect(JSON.parse(String(init?.body))).toMatchObject({ tide_offset_m: 0.5, rain_scale: 1 });
    // Served from the store, and said to be: not a run that just finished.
    expect(await screen.findByText("Stored Twin answer: 6 steps, not rerun.")).toBeInTheDocument();

    // Rain moved with the tide: the store holds no such answer, so the Twin is not asked, and the
    // emulator's answer stands with the tide left out and the reason beside it.
    const rain = within(screen.getByRole("region", { name: "Rain scale" })).getByRole("slider", {
      hidden: true,
    });
    act(() => {
      fireEvent.keyDown(rain, { key: "ArrowRight" });
    });
    await pressRun();
    expect(calls(fetchMock, "/v1/whatif/twin")).toHaveLength(1);
    expect(
      await screen.findByText("The tide is answered here only at rain 1.0x with no pipes cleaned."),
    ).toBeInTheDocument();
    expect(await screen.findByText("Emulator, tide not included")).toBeInTheDocument();
  });

  it("says nothing changed rather than drawing grey streets", async () => {
    stubApi({ emulator: NOTHING });
    renderLab();
    await pressRun();

    // The map's empty state and the street table both say it; nothing is drawn as an answer.
    expect(await screen.findAllByText("Nothing changed")).toHaveLength(2);
    expect(screen.getAllByText("Nothing changed.").length).toBeGreaterThan(0);
    expect(screen.getAllByText("No street's peak moved by 0.5 cm or more.")).toHaveLength(2);
    // The emulator did not run, so no skill is printed beside the answer (rule 6); the endpoint's
    // own note says why the forecast is the run's.
    expect(screen.queryByText(/Emulator skill/)).toBeNull();
    expect(screen.getByText(/^No lever is set/)).toBeInTheDocument();
    expect(screen.queryByText(/^Drawn:/)).toBeNull();
    // The hotspots are still tabled, each unchanged, so "nothing" is a measured answer: the
    // handler sends the run's own rows, not an empty table beside "Nothing changed".
    expect(screen.getByText("Test junction")).toBeInTheDocument();
    expect(screen.getByText("Unmodelled junction")).toBeInTheDocument();
    expect(screen.queryByText("No hotspot to compare")).toBeNull();
  });

  it("names the pump plan the Twin did not run when a tide question also asked for it", async () => {
    const fetchMock = stubApi({ twin: [TWIN_STARTED, TWIN_DONE_WITH_PUMPS] });
    renderLab();
    tideToMax();
    act(() => {
      fireEvent.click(screen.getByRole("switch", { name: "Pump plan" }));
    });
    await pressRun();

    // The lever went to the Twin too, so the job can name it in `levers_left_out`.
    const [, init] = calls(fetchMock, "/v1/whatif/twin")[0] ?? [];
    expect(JSON.parse(String(init?.body))).toMatchObject({ tide_offset_m: 1, pump_plan: true });

    await waitFor(
      () =>
        expect(
          screen.getAllByText("VARUNA-Twin, full city, prior blockage").length,
        ).toBeGreaterThan(1),
      { timeout: 3000 },
    );
    // The Twin's answer is not read as the answer to the whole question: the pump plan it left
    // out is said once, beside it, while the switch still reads on.
    expect(
      screen.getAllByText(
        "The pump plan is not in this Twin run: the Twin has no pump sink on the street, so the pumps are priced by the emulator only, as a lower bound.",
      ),
    ).toHaveLength(1);
    expect(screen.getByRole("switch", { name: "Pump plan" })).toBeChecked();
  });
});

import { act, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { OnboardScreen, toSteps } from "@/app/onboard/onboard-screen";
import { ONBOARDING_STEP_IDS } from "@/components/varuna/onboarding-steps";
import type { OnboardJob } from "@/lib/api/onboard";
import { formatDateTime, formatIst } from "@/lib/format";

/** What `useSearchParams` answers; each test may set the query the screen opens on. */
const nav = vi.hoisted(() => ({ search: "city=chennai" }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/onboard",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(nav.search),
}));

/** A run id in the shape SPEC.md 10.3 gives, for a Chennai design-storm cycle. */
const CHENNAI_RUN = "CHN-20260701T0040Z-sky1.0-twin1.0-flash0.0-baked";

/** Chennai with no job in the API's memory. `built` is the only thing the tests vary. */
function job(overrides: Partial<OnboardJob> = {}): OnboardJob {
  return {
    jobId: null,
    city: "chennai",
    status: "none",
    step: "choose_area",
    progress: 0,
    startedAt: null,
    finishedAt: null,
    elapsedS: 0,
    logTail: [],
    log: null,
    logTotal: null,
    steps: null,
    forecast: null,
    firstRunId: null,
    error: null,
    failedStep: null,
    designStorm: null,
    fromRecord: false,
    built: false,
    previous: null,
    lastAttempt: null,
    ...overrides,
  };
}

/** The job as an API too old to send per-step records answers with it. */
function legacyBody(current: OnboardJob): Record<string, unknown> {
  return {
    job_id: current.jobId,
    city: current.city,
    status: current.status,
    step: current.step,
    progress: current.progress,
    started_at: current.startedAt,
    finished_at: current.finishedAt,
    elapsed_s: current.elapsedS,
    log_tail: current.logTail,
    first_run_id: current.firstRunId,
    error: current.error,
    built: current.built,
  };
}

/** Six step records as `varuna_api.onboard` serves them: a cached rebuild of a built Chennai. */
function stepRecords(forecast: Record<string, unknown>): Record<string, unknown> {
  const done = (ms: number, loaded: boolean, detail: string | null = null) => ({
    status: "done",
    ms,
    detail,
    loaded_from_disk: loaded,
    progress: 1,
  });
  return {
    choose_area: done(18.2, false, "CHN-SOUTH, 291 x 334 cells at 30 m, EPSG:32644"),
    fetch_open_data: done(1412.6, true, "34,410 roads, 72,573 buildings from OSM"),
    condition_terrain: done(80.4, true),
    infer_drains: done(2290.1, true),
    build_graph: done(350.2, true),
    first_forecast: forecast,
  };
}

const FORECAST_SUMMARY = {
  run_id: CHENNAI_RUN,
  cycle_ts: "2026-07-01T06:10:00+05:30",
  n_steps: 31,
  step_min: 5,
  streets_total: 18622,
  wet_streets: 15472,
  wet_threshold_cm: 5,
  median_peak_cm: 26,
  max_peak_cm: 240.1,
  stage_ms: { sky: 135, twin: 42859, pulse: 981, flash: 0, products: 2462 },
  forecast_ms: 46437,
  mass_balance_err: 3.4e-7,
  storm: {
    id: "CHN-IDF-25yr",
    total_mm: 150,
    duration_min: 180,
    peak_mm_h: 448.8,
    source: "manifest",
  },
};

const FINISHED_FORECAST = {
  status: "done",
  ms: 46512.3,
  detail: "15,472 of 18,622 streets wet",
  loaded_from_disk: false,
  progress: 1,
  stages: {
    sky: { status: "done", ms: 135 },
    twin: { status: "done", ms: 42859 },
    pulse: { status: "done", ms: 981 },
    flash: { status: "skipped", ms: 0 },
    products: { status: "done", ms: 2462 },
  },
  stage_ms: { sky: 135, twin: 42859, pulse: 981, flash: 0, products: 2462 },
};

const LINES = [
  { ts: "2026-09-26T03:13:43.912+05:30", text: "city.start city=chennai", level: "info" },
  { ts: "2026-09-26T03:13:45.201+05:30", text: "osm.layer name=roads n=34410", level: "info" },
  {
    ts: "2026-09-26T03:14:34.690+05:30",
    text: `First forecast published: ${CHENNAI_RUN}, peak 240.1 cm on 15472 wet segments.`,
    level: "info",
  },
];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/**
 * Answers the onboard job endpoints with `body`, and - when `runId` is given - the two run
 * endpoints `loadRunDepth` reads, with an empty run (no steps, no wet streets) so the map stays on
 * its empty slot and never asks jsdom for WebGL. Everything else, the city layers included, 404s.
 *
 * Stubbing every test, the idle ones too, is deliberate: without it these tests dialled
 * `localhost:8000`, and on a machine where the API was up with Chennai built they were reading
 * that machine's Chennai rather than the case they name.
 */
function stubApi(body: OnboardJob | Record<string, unknown>, runId: string | null = null) {
  const payload = "logTail" in body ? legacyBody(body as OnboardJob) : body;
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, "http://localhost:8000");
    if (url.pathname.startsWith("/v1/onboard/")) return json(payload);
    if (runId && url.pathname === "/v1/nowcast/raster/bounds") {
      return json({
        // A named run is served as asked; `?city=` answers with that city's newest.
        run_id: url.searchParams.get("run_id") ?? runId,
        cycle_ts: "2026-07-01T06:10:00+05:30",
        mode: "baked",
        bundle: "CHN-IDF-25yr",
        n_steps: 0,
        step_min: 5,
        ensemble_n: 1,
        mass_balance_err: null,
        stage_ms: {},
        notes: [],
        bounds: { wgs84: [80.2, 12.96, 80.28, 13.05] },
      });
    }
    if (runId && url.pathname === "/v1/nowcast/segments") {
      return json({ valid_ts: [], n_segments_total: 0, depth_cm: {} });
    }
    return json({ error: { code: "not_found", message: "Not built." } }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/**
 * How long to wait for anything behind the run lookup. The screen asks for the job, then for the
 * run, then parses it, and testing-library's default of one second lost that race in the full
 * suite with pytest running beside it, while the same file alone passed in 15 s (2026-09-26).
 */
const LOADED = { timeout: 5_000 };

/** Let the job request, its JSON parse and the state update after it all land. */
async function settle(fetchMock: ReturnType<typeof stubApi>) {
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/v1/onboard/city/chennai")),
    ).toBe(true),
  );
  for (let i = 0; i < 3; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

function renderOnboard() {
  return render(
    <TooltipProvider>
      <OnboardScreen />
    </TooltipProvider>,
  );
}

function stepRows() {
  return within(screen.getByRole("list", { name: "Onboarding steps" })).getAllByRole("listitem");
}

beforeEach(() => {
  nav.search = "city=chennai";
  stubApi(job());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("OnboardScreen", () => {
  it("lists the six wizard steps, all idle", () => {
    renderOnboard();
    expect(stepRows()).toHaveLength(ONBOARDING_STEP_IDS.length);
    expect(screen.getByText("Choose area")).toBeInTheDocument();
    expect(screen.getAllByText("First forecast").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Waiting")).toHaveLength(ONBOARDING_STEP_IDS.length);
  });

  /**
   * This assertion used to be the opposite: the button was disabled and a note beside it read
   * "The wizard runs from city/cache/chennai in Phase 9." P9.6 landed on 2026-09-10 and made the
   * wizard real, but this test kept asserting the placeholder, so it had been failing ever since -
   * the one test guarding this screen was guarding a screen that no longer existed.
   */
  it("offers a live start button, because the wizard runs for real now", () => {
    renderOnboard();
    const start = screen.getByRole("button", { name: "Start onboarding Chennai" });
    expect(start).toBeEnabled();
    expect(start).toHaveAttribute("aria-busy", "false");
  });

  it("shows the honest empty states until a build has run", () => {
    renderOnboard();
    expect(screen.getByText(/No logs yet/)).toBeInTheDocument();
    // The honesty labels are chips beside the card's title, there before the numbers are.
    const card = within(screen.getByRole("region", { name: "First forecast" }));
    expect(card.getByText("Design storm")).toBeInTheDocument();
    expect(card.getByText("Uncalibrated")).toBeInTheDocument();
    // "Open Chennai console" stays on screen and disabled rather than appearing on success:
    // a control that materialises is harder to find on stage than one that lights up.
    expect(screen.getByRole("button", { name: "Open Chennai console" })).toBeDisabled();
  });

  it("keeps an unbuilt city waiting once the API has answered", async () => {
    const fetchMock = stubApi(job({ built: false }));
    renderOnboard();
    await settle(fetchMock);

    expect(screen.getAllByText("Waiting")).toHaveLength(ONBOARDING_STEP_IDS.length);
    expect(screen.queryByText("Already built")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Chennai console" })).toBeDisabled();
    expect(screen.queryByRole("link", { name: "Open Chennai console" })).not.toBeInTheDocument();
  });

  it("refuses to onboard the replay city and points at Chennai", async () => {
    nav.search = "city=mumbai";
    const fetchMock = stubApi(job({ city: "mumbai", built: true }));
    renderOnboard();

    const start = screen.getByRole("button", { name: "Start onboarding Mumbai" });
    expect(start).toBeDisabled();
    expect(
      screen.getByText(/Mumbai is the replay city and is not onboarded here\./),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Onboard Chennai" })).toHaveAttribute(
      "href",
      "/onboard?city=chennai",
    );
    // Nothing about Mumbai is fetched: no job, no layers, no run to present as a first forecast.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    const asked = fetchMock.mock.calls.map(([input]) => String(input));
    expect(asked.filter((url) => /\/v1\/(onboard|city\/mumbai|nowcast)/.test(url))).toEqual([]);
    expect(screen.queryByRole("button", { name: /Open .* console/ })).not.toBeInTheDocument();
  });

  it("puts the city in the URL when it names none, so the top bar's switcher reads it", async () => {
    nav.search = "";
    window.history.replaceState(null, "", "/onboard");
    renderOnboard();
    await waitFor(() => expect(window.location.search).toBe("?city=chennai"));
  });

  it("does not fetch the buildings until they are switched on", async () => {
    const fetchMock = stubApi(job({ built: true }));
    renderOnboard();
    await settle(fetchMock);
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("/layers/buildings")),
    ).toBe(false);
  });
});

describe("toSteps", () => {
  it("reads no job at all as six idle steps", () => {
    expect(toSteps(null).map((step) => step.status)).toEqual(
      ONBOARDING_STEP_IDS.map(() => "waiting"),
    );
  });

  it("reads an unbuilt city with no job as waiting", () => {
    expect(toSteps(job({ built: false })).map((step) => step.status)).toEqual(
      ONBOARDING_STEP_IDS.map(() => "waiting"),
    );
  });

  it("reads a built city with a run as built, with no time it did not spend", () => {
    const steps = toSteps(job({ built: true }), true);
    expect(steps.map((step) => step.id)).toEqual([...ONBOARDING_STEP_IDS]);
    expect(steps.every((step) => step.status === "cached")).toBe(true);
    expect(steps.every((step) => step.progress === 100 && step.elapsedS === 0)).toBe(true);
  });

  /**
   * `built` is only `city/<city>/segments.parquet` existing (services/api onboard router), which
   * says nothing about a forecast. The deployed API answered `{status: none, built: true}` for
   * Chennai with 404 `no_baked_runs` on every run endpoint, so the forecast row must not claim one.
   */
  it("keeps the first forecast waiting for a built city with no run", () => {
    for (const steps of [toSteps(job({ built: true })), toSteps(job({ built: true }), false)]) {
      expect(steps.map((step) => step.status)).toEqual([
        "cached",
        "cached",
        "cached",
        "cached",
        "cached",
        "waiting",
      ]);
      expect(steps[5]).toEqual({ id: "forecast", progress: 0, elapsedS: 0, status: "waiting" });
    }
  });

  it("follows a rebuild of a built city on an older API rather than calling it cached", () => {
    const steps = toSteps(
      job({
        built: true,
        jobId: "job-1",
        status: "running",
        step: "infer_drains",
        progress: 0.5,
        elapsedS: 21,
      }),
    );
    expect(steps.map((step) => step.status)).toEqual([
      "done",
      "done",
      "done",
      "running",
      "waiting",
      "waiting",
    ]);
    expect(steps[3].elapsedS).toBe(21);
    // The finished rows carry no time: an older API never reported one.
    expect(steps[0].elapsedS).toBe(0);
    expect(steps[0].elapsedMs).toBeUndefined();
  });
});

describe("OnboardScreen while a build runs", () => {
  it("prints each step's own time, the forecast's stages and each line's own time", async () => {
    const fetchMock = stubApi({
      job_id: "onboard-chennai-1a2b3c4d",
      city: "chennai",
      status: "running",
      step: "first_forecast",
      progress: 0.87,
      started_at: "2026-09-26T03:13:43.900+05:30",
      elapsed_s: 24.2,
      log_tail: LINES.slice(0, 2).map((line) => line.text),
      log: LINES.slice(0, 2),
      log_total: 2,
      steps: stepRecords({
        status: "running",
        ms: 19800,
        detail: null,
        loaded_from_disk: false,
        progress: 0.2,
        stages: {
          sky: { status: "done", ms: 135 },
          twin: { status: "running", ms: 12100 },
          pulse: { status: "waiting", ms: null },
          flash: { status: "waiting", ms: null },
          products: { status: "waiting", ms: null },
        },
      }),
      first_run_id: null,
      built: true,
    });
    renderOnboard();
    await settle(fetchMock);

    const rows = stepRows();
    expect(rows[0]).toHaveTextContent("Done 18 ms");
    expect(rows[1]).toHaveTextContent("Loaded from disk");
    expect(rows[5]).toHaveTextContent("Running 19.8 s");
    expect(rows[5]).toHaveAttribute("aria-current", "step");
    // One line a step; what each step reported sits behind Details, word for word.
    expect(rows[1]).not.toHaveTextContent("34,410 roads");
    const details = screen.getByLabelText("Step details");
    expect(details).toHaveTextContent("34,410 roads, 72,573 buildings from OSM");
    expect(details).toHaveTextContent("Sky 135 ms, Twin running 12.1 s");

    const log = screen.getByRole("log", { name: "Pipeline log" });
    expect(log).toHaveTextContent("osm.layer name=roads n=34410");
    // Each line's own capture time, not one time on every line.
    expect(log).toHaveTextContent(formatIst(LINES[0].ts, { seconds: true }));
    expect(log).toHaveTextContent(formatIst(LINES[1].ts, { seconds: true }));

    // Mid-build the console is not offered.
    expect(screen.getByRole("button", { name: "Open Chennai console" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Building Chennai" })).toBeDisabled();
  });
});

describe("OnboardScreen once a build has finished", () => {
  it("lights the finish card with the run's own numbers", async () => {
    stubApi(
      {
        job_id: "onboard-chennai-1a2b3c4d",
        city: "chennai",
        status: "finished",
        step: "first_forecast",
        progress: 1,
        started_at: "2026-09-26T03:13:43.900+05:30",
        finished_at: "2026-09-26T03:14:35.100+05:30",
        elapsed_s: 51.2,
        log_tail: LINES.map((line) => line.text),
        log: LINES,
        log_total: 3,
        steps: stepRecords(FINISHED_FORECAST),
        forecast: FORECAST_SUMMARY,
        first_run_id: CHENNAI_RUN,
        built: true,
      },
      CHENNAI_RUN,
    );
    renderOnboard();

    const link = await screen.findByRole("link", { name: "Open Chennai console" }, LOADED);
    expect(link).toHaveAttribute(
      "href",
      `/console?city=chennai&run=${encodeURIComponent(CHENNAI_RUN)}`,
    );
    const card = screen.getByRole("region", { name: "First forecast" });
    // The headline: the run's own count at 15 cm (this stub run has no wet street), its deepest
    // street from the build's summary, and the cycle it was issued at.
    const numbers = within(
      await within(card).findByLabelText("First forecast numbers", {}, LOADED),
    );
    expect(numbers.getByText("Flooded streets")).toBeInTheDocument();
    expect(numbers.getByText("240 cm")).toBeInTheDocument();
    expect(numbers.getByText("06:10")).toBeInTheDocument();
    // Everything else is behind Details, never deleted: the run id, the 5 cm count, the median,
    // the storm and the times. The card's time is the first forecast row's own wall time, so the
    // two never disagree; the stage sum beside it is `run.json`'s.
    expect(card).toHaveTextContent(CHENNAI_RUN);
    expect(card).toHaveTextContent("Streets above 5 cm: 15,472 of 18,622");
    expect(card).toHaveTextContent("Median street peak: 26 cm");
    expect(card).toHaveTextContent("Design storm CHN-IDF-25yr: 150 mm in 3 h, peak 449 mm/h");
    expect(card).toHaveTextContent(
      "Forecast computed in 46.5 s; Sky, Twin, Pulse and products took 46.4 s of it",
    );
    expect(card).toHaveTextContent("VARUNA learns Chennai's drains from the next monsoon.");
    expect(screen.getByText("Built in 51 s in this session.")).toBeInTheDocument();
    expect(stepRows()[5]).toHaveTextContent("Done 46.5 s");
    expect(screen.getByLabelText("Step details")).toHaveTextContent(
      "Sky 135 ms, Twin 42.9 s, Pulse 981 ms, products 2.5 s",
    );
  });

  it("keeps a finished build's own first run on an older API", async () => {
    stubApi(
      job({
        built: true,
        status: "finished",
        jobId: "job-9",
        firstRunId: CHENNAI_RUN,
        elapsedS: 65.4,
      }),
      CHENNAI_RUN,
    );
    renderOnboard();

    const link = await screen.findByRole("link", { name: "Open Chennai console" }, LOADED);
    expect(link).toHaveAttribute(
      "href",
      `/console?city=chennai&run=${encodeURIComponent(CHENNAI_RUN)}`,
    );
    expect(screen.getByText(/Built in/)).toHaveTextContent("Built in 1 min 05 s in this session.");
    // Built in front of the operator, so the rows are done, not "already built".
    expect(screen.queryByText("Already built")).not.toBeInTheDocument();
    expect(screen.queryByText(/Newest Chennai run/)).not.toBeInTheDocument();
  });
});

describe("OnboardScreen with a recorded previous build", () => {
  function previousBody(extra: Record<string, unknown> = {}): Record<string, unknown> {
    return {
      job_id: null,
      city: "chennai",
      status: "none",
      built: true,
      previous: {
        job_id: "onboard-chennai-1a2b3c4d",
        city: "chennai",
        design_storm: "CHN-IDF-25yr",
        status: "finished",
        started_at: "2026-09-26T03:13:43.900+05:30",
        finished_at: "2026-09-26T03:14:35.100+05:30",
        elapsed_s: 51.2,
        first_run_id: CHENNAI_RUN,
        first_run_exists: true,
        seeded: false,
        steps: stepRecords(FINISHED_FORECAST),
        forecast: FORECAST_SUMMARY,
        log: LINES,
        from_record: true,
        ...extra,
      },
      last_attempt: null,
    };
  }

  it("labels the build, shows its own times and lines, and lights the card with its run", async () => {
    stubApi(previousBody(), CHENNAI_RUN);
    renderOnboard();

    // The date as this machine's Intl writes it ("26 Sep" or "26 Sept"), from the record's time.
    const when = formatDateTime("2026-09-26T03:13:43.900+05:30");
    expect(
      await screen.findByText(`Previous build, ${when}, 51 s.`, {}, LOADED),
    ).toBeInTheDocument();
    const rows = stepRows();
    expect(rows[0]).toHaveTextContent("Done 18 ms");
    expect(rows[2]).toHaveTextContent("Loaded from disk");
    expect(rows[5]).toHaveTextContent("Done 46.5 s");
    expect(screen.queryByText("Already built")).not.toBeInTheDocument();

    const log = screen.getByRole("log", { name: "Pipeline log" });
    expect(log).toHaveTextContent(LINES[2].text);
    expect(log).toHaveTextContent(formatIst(LINES[2].ts, { seconds: true }));

    const link = await screen.findByRole("link", { name: "Open Chennai console" }, LOADED);
    expect(link).toHaveAttribute(
      "href",
      `/console?city=chennai&run=${encodeURIComponent(CHENNAI_RUN)}`,
    );
    const card = screen.getByRole("region", { name: "First forecast" });
    expect(card).toHaveTextContent("15,472 of 18,622");
    expect(card).toHaveTextContent(`From the previous build, ${when}.`);
  });

  it("keeps the card dim when the recorded run is no longer on this API", async () => {
    const fetchMock = stubApi(previousBody({ first_run_exists: false }), CHENNAI_RUN);
    renderOnboard();

    expect(await screen.findByText(/is not on this API/, {}, LOADED)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Chennai console" })).toBeDisabled();
    // Nor does it fall back to whichever run sorts newest: that run is not this build's.
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/v1/nowcast/raster/bounds?city=chennai"),
      ),
    ).toBe(false);
  });
});

describe("OnboardScreen, on a laptop where Chennai is already built (D-21)", () => {
  it("reads every step as already built once a run for it is read, with no elapsed time", async () => {
    stubApi(job({ built: true }), CHENNAI_RUN);
    renderOnboard();

    // The first five rows read "Already built" as soon as the job answers; the sixth only once
    // the run has loaded, so wait for all six rather than the first match.
    await waitFor(
      () => expect(screen.getAllByText("Already built")).toHaveLength(ONBOARDING_STEP_IDS.length),
      LOADED,
    );
    expect(screen.queryByText(/Waiting/)).not.toBeInTheDocument();
    expect(screen.getByText(/Built before this session; no record/)).toBeInTheDocument();
    // The city is on the server when deployed, not the visitor's machine.
    expect(screen.queryByText(/on this machine/)).not.toBeInTheDocument();
  });

  it("names and links the newest Chennai run the map has loaded", async () => {
    stubApi(job({ built: true }), CHENNAI_RUN);
    renderOnboard();

    const link = await screen.findByRole("link", { name: "Open Chennai console" }, LOADED);
    await waitFor(
      () =>
        expect(link).toHaveAttribute(
          "href",
          `/console?city=chennai&run=${encodeURIComponent(CHENNAI_RUN)}`,
        ),
      LOADED,
    );
    const card = screen.getByRole("region", { name: "First forecast" });
    expect(card).toHaveTextContent(CHENNAI_RUN);
    expect(card).toHaveTextContent("Newest Chennai run, built before this session.");
    // No build time is printed for a build this session did not run.
    expect(screen.queryByText(/Built in/)).not.toBeInTheDocument();
  });

  /**
   * The deployed API's state on 2026-09-26: Chennai's layers built, no run served. This test used
   * to assert the opposite - an enabled link to a console with no Chennai run in it, and six rows
   * of "Already built" for a forecast that did not exist.
   */
  it("keeps the forecast waiting and the console dim when a built city has no run", async () => {
    const fetchMock = stubApi(job({ built: true }));
    renderOnboard();

    // Wait for the run lookup to be answered, not just for the job: until the 404 lands the
    // screen does not yet know there is no run.
    expect(await screen.findByText(/No forecast on this API yet/, {}, LOADED)).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("/v1/nowcast/raster/bounds?city=chennai"),
      ),
    ).toBe(true);

    const rows = stepRows();
    expect(rows).toHaveLength(ONBOARDING_STEP_IDS.length);
    for (const row of rows.slice(0, 5)) expect(row).toHaveTextContent("Already built");
    expect(rows[5]).toHaveTextContent("First forecast");
    expect(rows[5]).toHaveTextContent("Waiting");
    expect(rows[5]).not.toHaveTextContent("Already built");

    expect(screen.getByRole("button", { name: "Open Chennai console" })).toBeDisabled();
    expect(screen.queryByRole("link", { name: "Open Chennai console" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Newest Chennai run/)).not.toBeInTheDocument();
    expect(screen.queryByText(/no record of the steps/)).not.toBeInTheDocument();
    expect(screen.queryByText(/on this machine/)).not.toBeInTheDocument();
  });

  it("keeps the console button dim while a built city is being rebuilt", async () => {
    const fetchMock = stubApi(
      job({ built: true, jobId: "job-2", status: "running", step: "condition_terrain" }),
    );
    renderOnboard();
    await settle(fetchMock);

    expect((await screen.findAllByText(/^Running/, {}, LOADED)).length).toBeGreaterThan(0);
    expect(screen.queryByText("Already built")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Chennai console" })).toBeDisabled();
  });
});

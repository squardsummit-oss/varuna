import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SkyPanel } from "@/components/varuna/sky-panel";
import { renderWithProviders, stubFetch } from "@/lib/test-utils";

/**
 * A cut-down copy of what the API answered for the 07:40 IST cycle of MUM-2019-07-02 (seed 2019):
 * three of its 36 steps, two of its 20 members, and its own notes verbatim. Real numbers from a
 * real cycle, so a test can assert the panel prints what the run said (SPEC.md rule 6).
 */
const NOTES = [
  "NWP blend disabled: no NWP field in P0 (SPEC.md 11.1, Appendix A).",
  "Z-R fitted this cycle: a=100, b=1.67 from 20 pairs, clamped to the allowed range.",
  "Gauge merge: bias 1.02 over 10 gauges, inverse-distance residuals, gauges honoured to 0.0 %.",
];

const PRODUCT = {
  run_id: null,
  valid_ts: "2019-07-02T07:40:00+05:30",
  mode: "live",
  bundle: "MUM-2019-07-02",
  city: "mumbai",
  n_members: 20,
  n_steps: 36,
  step_min: 5,
  nowcaster: "pysteps_steps",
  seed: 2019,
  zr: { a: 100, b: 1.6656872272539593, source: "adaptive", n_pairs: 20 },
  stage_ms: { qc: 2, zr: 2, merge: 2, motion: 40, nowcast: 5158, products: 214 },
  notes: NOTES,
};

const POINT_STEPS = [
  {
    valid_ts: "2019-07-02T07:45:00+05:30",
    lead_min: 5,
    p10_mm_h: 3.636,
    p50_mm_h: 4.009,
    p90_mm_h: 4.299,
    p_gt_20: 0,
    p_gt_40: 0,
  },
  {
    valid_ts: "2019-07-02T08:25:00+05:30",
    lead_min: 45,
    p10_mm_h: 1.344,
    p50_mm_h: 3.761,
    p90_mm_h: 4.418,
    p_gt_20: 0.05,
    p_gt_40: 0,
  },
  {
    valid_ts: "2019-07-02T09:10:00+05:30",
    lead_min: 90,
    p10_mm_h: 0,
    p50_mm_h: 1.137,
    p90_mm_h: 4.101,
    p_gt_20: 0,
    p_gt_40: 0,
  },
];

const SERIES = {
  ...PRODUCT,
  point: {
    lon: 72.8421396,
    lat: 19.010099,
    name: "Hindmata junction (Hindmata Cinema, Dr B. Ambedkar Marg)",
    hotspot_id: "MUM-HS-01",
    source_url:
      "https://www.freepressjournal.in/mumbai/mumbai-rains-knee-deep-water-accumulates-in-dadar-hindmata-due-to-heavy-downpour-video-surfaces",
    row: 72,
    col: 56,
    res_m: 500,
  },
  steps: POINT_STEPS,
  exceedance_mm_h: [20, 40],
};

const BAND = {
  ...PRODUCT,
  // The city band has no exceedance fields: they are per-pixel products, not city means.
  steps: POINT_STEPS.map((step) => ({
    valid_ts: step.valid_ts,
    lead_min: step.lead_min,
    p10_mm_h: step.p10_mm_h,
    p50_mm_h: step.p50_mm_h,
    p90_mm_h: step.p90_mm_h,
  })),
  members: [
    { member: 0, mm_h: [3.9, 3.4, 0.4] },
    { member: 1, mm_h: [4.2, 3.9, 2.1] },
  ],
};

const NO_RUNS = {
  status: 404,
  body: {
    error: {
      code: "no_rain_runs",
      message:
        "No run under data/runs carries rain products yet. Run `make bake BUNDLE=MUM-2019-07-02`, press Play on the replay, or add compute=true to compute this cycle from MUM-2019-07-02 now.",
      run_id: null,
    },
  },
};

function stub(routes: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn(stubFetch(routes)));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SkyPanel", () => {
  it("says it is scaffolding, so nobody mistakes it for the finished console", async () => {
    stub({ "/v1/nowcast/rain/series": SERIES, "/v1/nowcast/rain": BAND });
    renderWithProviders(<SkyPanel />);
    expect(
      screen.getByText(
        "Phase 3 scaffolding: the map, the time bar and the hotspot drawer arrive in Phase 6.",
      ),
    ).toBeInTheDocument();
    await screen.findByText(/Hindmata junction/);
  });

  it("reads a null run id as live and unpublished, not as a missing field", async () => {
    stub({ "/v1/nowcast/rain/series": SERIES, "/v1/nowcast/rain": BAND });
    renderWithProviders(<SkyPanel />);
    expect(await screen.findByText("Live, not published as a run")).toBeInTheDocument();
    expect(screen.queryByText(/^run /)).not.toBeInTheDocument();
  });

  it("prints the run's own notes verbatim and names the nowcaster that ran", async () => {
    stub({ "/v1/nowcast/rain/series": SERIES, "/v1/nowcast/rain": BAND });
    renderWithProviders(<SkyPanel />);
    for (const note of NOTES) expect(await screen.findByText(note)).toBeInTheDocument();
    expect(screen.getByText("pySTEPS STEPS")).toBeInTheDocument();
  });

  it("shows the register's own coordinate with the source it was verified against", async () => {
    stub({ "/v1/nowcast/rain/series": SERIES, "/v1/nowcast/rain": BAND });
    renderWithProviders(<SkyPanel />);
    const link = await screen.findByRole("link", { name: "coordinate source" });
    expect(link).toHaveAttribute("href", SERIES.point.source_url);
    expect(screen.getByText(/Sky pixel row 72, column 56, 500 m/)).toBeInTheDocument();
  });

  it("quotes the band width from the two steps, and says nothing when one is missing", async () => {
    // p90 - p10 is 2.09 mm/h at +30 min and 4.10 mm/h at +90 min: the band opening out with lead,
    // computed from the steps rather than asserted.
    const withBothLeads = {
      ...BAND,
      steps: [
        {
          valid_ts: "2019-07-02T08:10:00+05:30",
          lead_min: 30,
          p10_mm_h: 2.146,
          p50_mm_h: 3.742,
          p90_mm_h: 4.238,
        },
        ...BAND.steps,
      ],
    };
    stub({ "/v1/nowcast/rain/series": SERIES, "/v1/nowcast/rain": withBothLeads });
    const { container, unmount } = renderWithProviders(<SkyPanel />);
    await screen.findByText(/Hindmata junction/);
    await waitFor(() => expect(container.textContent).toContain("2.09 mm/h"));
    expect(container.textContent).toContain("4.10 mm/h");
    unmount();

    // Without a +30 step there is no comparison to make, so the sentence is absent rather than
    // filled in with a plausible number (SPEC.md rule 6).
    vi.unstubAllGlobals();
    stub({ "/v1/nowcast/rain/series": SERIES, "/v1/nowcast/rain": BAND });
    const second = renderWithProviders(<SkyPanel />);
    await second.findByText(/Hindmata junction/);
    expect(second.container.textContent).not.toContain("The p10 to p90 band spans");
  });

  it("names the fallback nowcaster on screen when it is what ran", async () => {
    stub({
      "/v1/nowcast/rain/series": { ...SERIES, nowcaster: "fallback_steps" },
      "/v1/nowcast/rain": { ...BAND, nowcaster: "fallback_steps" },
    });
    renderWithProviders(<SkyPanel />);
    expect(await screen.findByText("Fallback nowcaster")).toBeInTheDocument();
  });

  it("shows the API's message and no chart when nothing is baked", async () => {
    stub({ "/v1/nowcast/rain/series": NO_RUNS, "/v1/nowcast/rain": NO_RUNS });
    renderWithProviders(<SkyPanel />);
    expect(await screen.findByText("No runs yet")).toBeInTheDocument();
    expect(screen.getByText(NO_RUNS.body.error.message)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compute live" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("offers a retry when the failure is not the missing bake", async () => {
    const failure = {
      status: 404,
      body: {
        error: {
          code: "city_not_built",
          message: "No hotspot register for mumbai. Run `make city CITY=mumbai`.",
          run_id: null,
        },
      },
    };
    stub({ "/v1/nowcast/rain/series": failure, "/v1/nowcast/rain": failure });
    renderWithProviders(<SkyPanel />);
    expect(await screen.findByText("Rain nowcast unavailable")).toBeInTheDocument();
    expect(
      screen.getByText("No hotspot register for mumbai. Run `make city CITY=mumbai`."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

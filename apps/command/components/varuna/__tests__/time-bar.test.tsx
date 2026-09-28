import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  TimeBar,
  computeLiveCopy,
  computeLiveLabel,
  type ComputeInfo,
} from "@/components/varuna/time-bar";
import { renderWithProviders, stubFetch } from "@/lib/test-utils";
import { useReplayStore } from "@/lib/stores/replay";
import { useRunStore } from "@/lib/stores/run";

const T0 = "2019-07-02T05:40:00+05:30";
const CLOCK = {
  bundle_id: "MUM-2019-07-02",
  sim_time: "2019-07-02T06:40:00+05:30",
  playing: false,
  speed: 30,
  t0: T0,
  t1: "2019-07-02T09:40:00+05:30",
  cycle_index: 12,
  n_cycles: 49,
  mode: "baked",
  last_run_id: null,
  next_cycle_ts: "2019-07-02T06:45:00+05:30",
  note: null,
  progress: 0.25,
};

/** `GET /v1/cycle/compute` on the demo laptop, with the figures measured there (8 runs). */
const COMPUTE_ON: ComputeInfo = {
  enabled: true,
  reason: null,
  busy: false,
  budget_ms: 15_000,
  expected: { median_ms: 104_466, min_ms: 48_228, max_ms: 224_308, n_runs: 8 },
};

/** `GET /v1/replay/bundles` as the three committed manifests label them. */
function bundle(id: string, label: string, t0: string) {
  return { id, city: "mumbai", label, t0, t1: t0, seed: 2019, total_cycles: 49 };
}
const BUNDLES = [
  bundle("MUM-2019-07-02", "Reconstructed replay", T0),
  bundle("MUM-IDF-25yr", "Design storm", "2026-07-01T05:40:00+05:30"),
  bundle("CHN-IDF-25yr", "Design storm", "2026-07-01T05:40:00+05:30"),
];

const RECONSTRUCTED_LABEL = "Live compute on the reconstructed replay";

/** No API in jsdom by default: every replay request answers 404, as it would with no bundle. */
function renderTimeBar() {
  return renderWithProviders(<TimeBar />);
}

/**
 * Base UI renders the thumb with a visually hidden `input[type=range]` (excluded from role queries);
 * read it directly and prefer aria-valuenow, then the input value.
 */
function sliderValue(): string | null {
  const thumb = document.querySelector('[data-slot="slider-thumb"]');
  const input = thumb?.querySelector<HTMLInputElement>("input") ?? null;
  const el = input ?? thumb ?? document.querySelector('[data-slot="slider"] input');
  if (!el) return null;
  return el.getAttribute("aria-valuenow") ?? (el as HTMLInputElement).value ?? null;
}

describe("TimeBar", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(stubFetch({})));
    useReplayStore.getState().reset();
    useRunStore.getState().clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the valid time and lead at the default scrub position", () => {
    renderTimeBar();
    expect(screen.getByText("06:40 (+0 min)")).toBeInTheDocument();
    expect(screen.getByText("Ensemble spread appears with the first run")).toBeInTheDocument();
  });

  it("draws the ensemble's spread under the track once a run carries one (7.2)", () => {
    // Three steps, widest at +10 min: the label names the widest point in the run's own numbers.
    act(() => {
      useRunStore.getState().setRun({
        run_id: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
        city: "mumbai",
        cycle_ts: "2019-07-02T08:40:00+05:30",
        mode: "replay",
        replay_mode: "baked",
        ensemble_n: 50,
        step_min: 5,
        aoi_depth_band: { p10: [1.0, 1.2, 1.5], p50: [1.2, 1.6, 1.8], p90: [1.4, 2.4, 2.1] },
      });
    });
    renderTimeBar();
    expect(
      screen.getByRole("img", {
        name: "Ensemble spread of mean street depth, p10 to p90: widest 1.2 cm, at +5 min",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Ensemble spread appears/)).not.toBeInTheDocument();
  });

  it("says a loaded run has no spread rather than promising one later", () => {
    act(() => {
      useRunStore.getState().setRun({
        run_id: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.0-baked",
        city: "mumbai",
        cycle_ts: "2019-07-02T08:40:00+05:30",
        mode: "replay",
        replay_mode: "baked",
        ensemble_n: 1,
      });
    });
    renderTimeBar();
    expect(screen.getByText("This run has no ensemble spread to draw")).toBeInTheDocument();
  });

  it("reflects leadMin on the slider and the label after setLeadMin(45)", () => {
    renderTimeBar();
    act(() => {
      useReplayStore.getState().setLeadMin(45);
    });
    expect(sliderValue()).toBe("45");
    expect(screen.getByText("07:25 (+45 min)")).toBeInTheDocument();
  });

  it("toggles playing from the play button even without a run", () => {
    renderTimeBar();
    const play = screen.getByRole("button", { name: "Play the replay" });
    expect(play).toHaveAttribute("aria-disabled", "true");
    act(() => {
      play.click();
    });
    expect(useReplayStore.getState().playing).toBe(true);
    expect(screen.getByRole("button", { name: "Pause the replay" })).toBeInTheDocument();
  });

  it("keeps Compute live disabled until a bundle is loaded", () => {
    renderTimeBar();
    expect(screen.getByRole("button", { name: "Compute live" })).toBeDisabled();
  });

  it("says Compute live runs the reconstructed replay, and how long it takes here, before it is pressed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(stubFetch({ "/v1/cycle/compute": COMPUTE_ON, "/v1/replay/bundles": BUNDLES })),
    );
    act(() => {
      useRunStore.getState().setRun({
        run_id: "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked",
        city: "mumbai",
        cycle_ts: "2019-07-02T06:40:00+05:30",
        mode: "replay",
        replay_mode: "baked",
        ensemble_n: 50,
      });
    });
    renderTimeBar();

    const button = screen.getByRole("button", { name: "Compute live" });
    await waitFor(() => expect(button).toBeEnabled());
    // The button is described by the note under it: what it runs, then how long it takes here.
    const note = document.getElementById(button.getAttribute("aria-describedby") ?? "");
    await waitFor(() =>
      expect(note).toHaveTextContent(
        `${RECONSTRUCTED_LABEL} About 1 min 44 s a cycle here, against 15.0 s`,
      ),
    );
  });

  it("names a design storm as one when /replay has switched the shared clock to it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        stubFetch({
          "/v1/cycle/compute": COMPUTE_ON,
          "/v1/replay/bundles": BUNDLES,
          "/v1/replay/clock": {
            ...CLOCK,
            bundle_id: "MUM-IDF-25yr",
            sim_time: "2026-07-01T06:40:00+05:30",
            t0: "2026-07-01T05:40:00+05:30",
            t1: "2026-07-01T09:40:00+05:30",
          },
        }),
      ),
    );
    renderTimeBar();
    await screen.findByText("Live compute on the design storm");
    expect(screen.queryByText(/reconstructed/i)).not.toBeInTheDocument();
  });

  it("says Compute live is off on this server when the API has it off", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(stubFetch({ "/v1/cycle/compute": { ...COMPUTE_ON, enabled: false, reason: "Off." } })),
    );
    renderTimeBar();
    await screen.findByText("Off on this server");
    // Until the bundle list answers, the bundle is named by its id rather than guessed.
    expect(screen.getByText("Live compute on bundle MUM-2019-07-02")).toBeInTheDocument();
  });

  it("asks the API to play once the clock is available", async () => {
    const fetchMock = vi.fn(
      stubFetch({ "/v1/replay/clock": CLOCK, "/v1/replay/play": { ...CLOCK, playing: true } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderTimeBar();

    // The clock the API reports becomes the store's clock.
    await waitFor(() => expect(useReplayStore.getState().cycleIndex).toBe(12));

    act(() => {
      screen.getByRole("button", { name: "Play the replay" }).click();
    });
    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/v1/replay/play"));
      expect(posted).toBeDefined();
      expect(posted?.[1]?.method).toBe("POST");
    });
    expect(useReplayStore.getState().playing).toBe(true);
  });
});

describe("computeLiveCopy", () => {
  const base = {
    bundle: { id: "MUM-2019-07-02", label: "Reconstructed replay" },
    hasRun: true,
    active: false,
    elapsedMs: null,
    cycleTimeLabel: "06:40 IST",
    cycleDateLabel: "2 Jul 2019",
  };

  it("names the replay cycle it re-runs and says it is not today's weather", () => {
    const { label, tooltip } = computeLiveCopy({ ...base, info: COMPUTE_ON });
    expect(label).toBe(RECONSTRUCTED_LABEL);
    expect(tooltip).toBe(
      "Re-runs the 06:40 IST cycle of 2 Jul 2019 from the reconstructed replay bundle " +
        "MUM-2019-07-02 with real computation. It is not today's weather. A cycle takes about " +
        "1 min 44 s here (48.2 s to 3 min 44 s over 8 runs), against a 15.0 s budget.",
    );
  });

  it("calls a design storm a design storm and never the reconstructed replay (rule 7)", () => {
    const { label, tooltip } = computeLiveCopy({
      ...base,
      bundle: { id: "MUM-IDF-25yr", label: "Design storm" },
      cycleDateLabel: "1 Jul 2026",
      info: COMPUTE_ON,
    });
    expect(label).toBe("Live compute on the design storm");
    expect(tooltip).toBe(
      "Re-runs the 06:40 IST cycle of 1 Jul 2026 from the design storm bundle MUM-IDF-25yr with " +
        "real computation. It is not today's weather. A cycle takes about 1 min 44 s here " +
        "(48.2 s to 3 min 44 s over 8 runs), against a 15.0 s budget.",
    );
    expect(`${label} ${tooltip}`).not.toMatch(/reconstructed/i);
  });

  it("names a bundle by its id until its manifest label is known", () => {
    expect(computeLiveLabel({ id: "MUM-2019-07-02", label: null })).toBe(
      "Live compute on bundle MUM-2019-07-02",
    );
    expect(computeLiveLabel({ id: "CHN-IDF-25yr", label: "Design storm" })).toBe(
      "Live compute on the design storm",
    );
    const { tooltip } = computeLiveCopy({
      ...base,
      bundle: { id: "MUM-IDF-25yr", label: null },
      info: COMPUTE_ON,
    });
    expect(tooltip).toMatch(/^Re-runs the 06:40 IST cycle of 2 Jul 2019 from bundle MUM-IDF-25yr /);
  });

  it("counts the elapsed time against the expected one while a cycle runs", () => {
    const { note, tooltip } = computeLiveCopy({
      ...base,
      info: { ...COMPUTE_ON, busy: true },
      active: true,
      elapsedMs: 12_000,
    });
    expect(note).toBe("Running, 12.0 s so far of about 1 min 44 s");
    expect(tooltip).toBe("A live cycle is running; its stages fill the bar below");
  });

  it("carries the server's reason when Compute live is off", () => {
    const { note, tooltip } = computeLiveCopy({
      ...base,
      info: { ...COMPUTE_ON, enabled: false, reason: "Compute live is off on this server." },
    });
    expect(note).toBe("Off on this server");
    expect(tooltip).toBe("Compute live is off on this server.");
  });

  it("says the API did not answer rather than asking forever", () => {
    const { note, tooltip } = computeLiveCopy({ ...base, info: null, unreachable: true });
    expect(note).toBe("The API did not say whether it can compute");
    expect(tooltip).toBe(
      "The API did not answer, so Compute live stays off. The baked replay is unaffected.",
    );
    expect(computeLiveCopy({ ...base, info: null }).note).toBe(
      "Asking the server how long a cycle takes",
    );
  });

  it("says when no cycle has been timed yet rather than inventing a figure", () => {
    const { note } = computeLiveCopy({
      ...base,
      info: {
        ...COMPUTE_ON,
        expected: { median_ms: null, min_ms: null, max_ms: null, n_runs: 0 },
      },
    });
    expect(note).toBe("No cycle timed on this server yet");
  });
});

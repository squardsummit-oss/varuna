import { createRequire } from "node:module";

import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RainSkillPanel, midSentence, notScoredText } from "@/app/verify/rain-skill-panel";
import { RainSkillLoadError, parseRainSkill } from "@/lib/api/verification-rain";

const loadRainSkill = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/verification-rain", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/verification-rain")>()),
  loadRainSkill,
}));

const table = (csi: number | null, pod: number | null, far: number | null) => ({
  hits: 10,
  misses: 5,
  false_alarms: 3,
  correct_negatives: 100,
  csi,
  pod,
  far,
});

const cell = (mean: number, persistence: number, events: number) => ({
  event_pixels: events,
  base_rate: 0.3,
  mean: table(mean, 0.7, 0.2),
  p50: table(mean - 0.02, 0.68, 0.22),
  persistence: table(persistence, 0.8, 0.3),
  brier: 0.13,
  brier_persistence: 0.15,
  brier_climatology: 0.21,
  brier_skill_vs_persistence: 0.13,
  brier_skill_vs_climatology: 0.38,
  spread: {
    mean: {
      csi: { n: 5, p10: 0.35, p90: 0.75 },
      pod: { n: 5, p10: 0.5, p90: 0.9 },
      far: { n: 5, p10: 0.1, p90: 0.3 },
    },
    p50: {
      csi: { n: 2, p10: null, p90: null },
      pod: { n: 2, p10: null, p90: null },
      far: { n: 2, p10: null, p90: null },
    },
  },
});

const failure = (lead: number, csi: number, held: number) => ({
  lead_min: lead,
  reasons: ["below_persistence"],
  csi,
  persistence_csi: held,
  n_cycles: 8,
});

/** The shape the scorer serves, trimmed to two leads and two cycles. */
function scored() {
  const scope = (label: string, horizonLead: number) => ({
    label,
    n_pixels: 589,
    by_lead: [
      {
        lead_min: 5,
        n_cycles: 8,
        n_pixels: 4712,
        mae_mm_h: { mean: 7.25, p50: 7.56, persistence: 9.39 },
        thresholds: { "20": cell(0.5829, 0.6291, 1819), "40": cell(0.2, 0.3, 400) },
      },
      {
        lead_min: 10,
        n_cycles: 8,
        n_pixels: 4712,
        mae_mm_h: { mean: 8.1, p50: 8.3, persistence: 9.9 },
        thresholds: { "20": cell(0.4611, 0.4861, 1655), "40": cell(0.1, 0.2, 380) },
      },
    ],
    reliability: {},
    horizons: [
      {
        threshold_mm_h: 20,
        forecast: "mean",
        csi_floor: 0.5,
        lead_min: horizonLead,
        status: "found",
        first_failure: failure(horizonLead + 5, 0.47, 0.49),
        beats_persistence_leads_min: [15, 20],
      },
      {
        threshold_mm_h: 40,
        forecast: "mean",
        csi_floor: 0.5,
        lead_min: 0,
        status: "found",
        first_failure: { ...failure(5, 0.2, 0.3), reasons: ["below_persistence", "below_floor"] },
        beats_persistence_leads_min: [],
      },
    ],
    per_cycle: [
      {
        run_id: "run-06:00",
        cycle_ts: "2019-07-02T06:00:00+05:30",
        leads_min: [5, 10, 15],
        csi_mean_20: [null, 0.61, 0.3],
        csi_persistence_20: [0.5, 0.48, 0.2],
      },
    ],
  });
  return parseRainSkill(
    {
      event: "MUM-2019-07-02",
      available: true,
      label: "Reconstructed replay",
      truth: {
        source: "truth/rain.zarr",
        note: "Scored against the bundle's reconstructed truth field, not a measurement.",
        t0: "2019-07-02T05:40:00+05:30",
        t1: "2019-07-02T09:40:00+05:30",
        n_frames: 49,
      },
      units: { csi: "0 to 1, higher is better" },
      definitions: {
        event_pixel: "A pixel whose truth rain rate is strictly above the threshold.",
        csi: "Critical success index, hits / (hits + misses + false alarms).",
        persistence: "The analysis the nowcast started from, held unchanged for every lead.",
      },
      thresholds_mm_h: [10, 20, 40],
      headline_threshold_mm_h: 20,
      csi_floor: 0.5,
      lead_bands_min: [[5, 60]],
      cycles: ["06:00", "09:10"].map((hhmm) => ({
        run_id: `run-${hhmm}`,
        cycle_ts: `2019-07-02T${hhmm}:00+05:30`,
        n_members: 20,
        member_cube: true,
        products_source: "rain/quantiles.zarr",
        n_leads: 36,
        n_leads_scored: 36,
        max_lead_scored_min: 180,
        persistence: { matches_cycle: true },
      })),
      n_cycles: 8,
      by_scope: {
        aoi: scope("Sky pixels (500 m) whose centres fall inside the city grid", 0),
        domain: scope("Every pixel the radar covers on the 60 km Sky domain", 10),
      },
      horizon: null,
      runs_without_member_cube: [],
      skipped_runs: [],
      unavailable: {},
      provenance: {},
      notes: [],
    },
    "MUM-2019-07-02",
  );
}

describe("RainSkillPanel (Pramana, 7.10)", () => {
  beforeEach(() => {
    loadRainSkill.mockReset();
  });

  it("shows a skeleton while the event is being scored, with no typed latency", () => {
    loadRainSkill.mockReturnValue(new Promise(() => {}));
    const { container } = render(<RainSkillPanel event="MUM-2019-07-02" />);
    const loading = container.querySelector('[data-slot="rain-skill-loading"]');
    expect(loading).not.toBeNull();
    expect(screen.getByText(/served from the API's cache/)).toBeInTheDocument();
    expect(loading?.textContent).not.toMatch(/\d+\s*s\b/);
  });

  it("says the API is unreachable, as a sentence, and only then says to start it", async () => {
    // What `loadRainSkill` throws when fetch itself rejects with the browser's TypeError.
    loadRainSkill.mockRejectedValue(new RainSkillLoadError("The API is unreachable.", true));
    render(<RainSkillPanel event="MUM-2019-07-02" />);
    expect(await screen.findByText("Rain skill did not load")).toBeInTheDocument();
    expect(
      screen.getByText("The API is unreachable. Start the API with make dev, then reload."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Failed to fetch/)).toBeNull();
  });

  it("shows a running API's own error without telling the reader to start it", async () => {
    const message =
      "No bundle MUM-2019-07-02. Run make bundle BUNDLE=MUM-2019-07-02, then make bake BUNDLE=MUM-2019-07-02.";
    loadRainSkill.mockRejectedValue(new RainSkillLoadError(message, false));
    render(<RainSkillPanel event="MUM-2019-07-02" />);
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByText(/make dev/)).toBeNull();
  });

  it("names make bake, once, when no run keeps rain products", async () => {
    // The exact answer `event_rain_skill` serves for this case (rain_event.py).
    loadRainSkill.mockResolvedValue(
      parseRainSkill(
        {
          event: "MUM-2019-07-02",
          available: false,
          reason:
            "No run of MUM-2019-07-02 keeps rain products (rain/quantiles.zarr or rain/cube.zarr).",
          missing: "runs",
          command: "make bake BUNDLE=MUM-2019-07-02",
        },
        "MUM-2019-07-02",
      ),
    );
    render(<RainSkillPanel event="MUM-2019-07-02" />);
    expect(await screen.findByText("Not scored yet")).toBeInTheDocument();
    const said = screen.getByText(/No run of MUM-2019-07-02 keeps rain products/).textContent ?? "";
    expect(said.match(/make \w+/g)).toEqual(["make bake"]);
    expect(said).not.toContain("`");
    expect(said).toContain("Run make bake BUNDLE=MUM-2019-07-02, then reload this page.");
  });

  it("names make bundle, once, when the bundle has no truth field", () => {
    const answer = parseRainSkill(
      {
        event: "MUM-IDF-25yr",
        available: false,
        reason:
          "MUM-IDF-25yr has no truth/rain.zarr, so there is no rain field to score against. Only a reconstructed or design-storm bundle carries one.",
        missing: "truth",
        command: "make bundle BUNDLE=MUM-IDF-25yr",
      },
      "MUM-IDF-25yr",
    );
    if (answer.available) throw new Error("expected an unavailable answer");
    expect(answer.missing).toBe("truth");
    const said = notScoredText(answer.reason, answer.command);
    expect(said.match(/make \w+/g)).toEqual(["make bundle"]);
    // A scorer that served no command gets its reason alone, never a guessed command.
    expect(notScoredText("Every run of the event failed to load: x.", null)).toBe(
      "Every run of the event failed to load: x.",
    );
  });

  it("keeps Sky capitalised when a scope label follows 'Scope: '", () => {
    expect(midSentence("Sky pixels (500 m) whose centres fall inside the city grid")).toBe(
      "Sky pixels (500 m) whose centres fall inside the city grid",
    );
    expect(midSentence("Every pixel the radar covers on the 60 km Sky domain")).toBe(
      "every pixel the radar covers on the 60 km Sky domain",
    );
  });

  it("states the computed horizon without claiming a decay the data does not show", async () => {
    loadRainSkill.mockResolvedValue(scored());
    render(<RainSkillPanel event="MUM-2019-07-02" />);
    expect(await screen.findByText("No useful lead at 20 mm/h.")).toBeInTheDocument();
    expect(screen.queryByText(/Confidence decays/)).toBeNull();
    // The radar domain has ten useful minutes before persistence wins: now the decay is real.
    fireEvent.click(screen.getByRole("radio", { name: "Radar domain" }));
    expect(screen.getByText("Confidence decays after 10 minutes.")).toBeInTheDocument();
  });

  it("switches score with tabs and threshold with arrow keys", async () => {
    loadRainSkill.mockResolvedValue(scored());
    render(<RainSkillPanel event="MUM-2019-07-02" />);
    await screen.findByText("No useful lead at 20 mm/h.");

    const tabs = screen.getByRole("tablist", { name: "Score" });
    expect(
      within(tabs)
        .getAllByRole("tab")
        .map((tab) => tab.textContent),
    ).toEqual(["CSI", "POD", "FAR"]);
    fireEvent.click(within(tabs).getByRole("tab", { name: "FAR" }));
    expect(screen.getByRole("img", { name: /^FAR at 20 mm\/h/ })).toBeInTheDocument();

    const twenty = screen.getByRole("radio", { name: "20 mm/h" });
    expect(twenty).toHaveAttribute("aria-checked", "true");
    expect(twenty).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("radio", { name: "10 mm/h" })).toHaveAttribute("tabindex", "-1");
    fireEvent.keyDown(twenty, { key: "ArrowRight" });
    expect(screen.getByRole("radio", { name: "40 mm/h" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("No useful lead at 40 mm/h.")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^FAR at 40 mm\/h/ })).toBeInTheDocument();
  });

  it("prints every number and the method beside the chart", async () => {
    loadRainSkill.mockResolvedValue(scored());
    render(<RainSkillPanel event="MUM-2019-07-02" />);
    await screen.findByText("No useful lead at 20 mm/h.");
    const numbers = screen.getByRole("region", { name: "Scores by lead time" });
    expect(within(numbers).getByText("+5 min")).toBeInTheDocument();
    expect(within(numbers).getAllByText("0.35 to 0.75 (5)")).toHaveLength(2);
    expect(within(numbers).getByText("1,819")).toBeInTheDocument();
    expect(screen.getByText(/Pooled over 8 baked cycles/)).toBeInTheDocument();
    expect(screen.getByText(/It is labelled "Reconstructed replay"/)).toBeInTheDocument();
    expect(screen.getByText(/not a confidence interval/)).toBeInTheDocument();
  });

  it("draws the reliability section and the served per-cycle CSI at 20 mm/h", async () => {
    loadRainSkill.mockResolvedValue(scored());
    render(<RainSkillPanel event="MUM-2019-07-02" />);
    await screen.findByText("No useful lead at 20 mm/h.");
    expect(screen.getByRole("heading", { name: "Reliability, 20 mm/h" })).toBeInTheDocument();
    // The fixture serves no reliability bins: the diagram says so rather than drawing zeros.
    expect(screen.getByText("No reliability at 20 mm/h")).toBeInTheDocument();
    const cycles = screen.getByRole("region", { name: "CSI by cycle" });
    const row = within(cycles).getByRole("row", { name: /06:00/ });
    const cells = within(row).getAllByRole("cell");
    // +5 has no ensemble score (a dash, never a zero); +15 is 0.30 against persistence 0.20.
    expect(cells[0]?.textContent).toBe("— vs 0.50");
    expect(cells[1]?.textContent).toBe("0.30 vs 0.20");
    expect(cells[2]?.textContent).toBe("— vs —");
  });

  /**
   * axe over the scored panel, resolved from `@axe-core/playwright` as the citizen tests do.
   * `color-contrast` needs real CSS and layout, which jsdom does not have.
   */
  it("has no axe violations when scored, with the numbers table open", async () => {
    interface AxeCore {
      run(
        context: Element,
        options: { rules: Record<string, { enabled: boolean }> },
      ): Promise<{ violations: { id: string; nodes: unknown[] }[]; passes: unknown[] }>;
    }
    loadRainSkill.mockResolvedValue(scored());
    const { container } = render(<RainSkillPanel event="MUM-2019-07-02" />);
    await screen.findByText("No useful lead at 20 mm/h.");
    container.querySelector("details")?.setAttribute("open", "");
    const here = createRequire(import.meta.url);
    const axe = createRequire(here.resolve("@axe-core/playwright"))("axe-core") as AxeCore;
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.passes.length).toBeGreaterThan(0);
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.length} node(s)`)).toEqual([]);
  });
});

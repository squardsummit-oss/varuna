import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  IDLE_ONBOARDING_STEPS,
  ONBOARDING_STEP_IDS,
  OnboardingStepDetails,
  OnboardingSteps,
  STEP_STATUS_LABELS,
  stagesText,
  stepStatusText,
  type OnboardingStepState,
} from "@/components/varuna/onboarding-steps";

describe("OnboardingSteps", () => {
  it("prints a waiting step without a time nobody measured", () => {
    render(<OnboardingSteps steps={IDLE_ONBOARDING_STEPS} />);
    expect(screen.getAllByText("Waiting")).toHaveLength(ONBOARDING_STEP_IDS.length);
    expect(screen.queryByText(/0 s/)).not.toBeInTheDocument();
  });

  it("prints an already-built step with its own label and no elapsed time", () => {
    const steps: OnboardingStepState[] = ONBOARDING_STEP_IDS.map((id) => ({
      id,
      progress: 100,
      elapsedS: 0,
      status: "cached",
    }));
    render(<OnboardingSteps steps={steps} />);

    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(ONBOARDING_STEP_IDS.length);
    for (const row of rows) {
      const status = within(row).getByText(STEP_STATUS_LABELS.cached);
      // Exactly the label: no time for a build this screen has no record of.
      expect(status.textContent).toBe("Already built");
      // Its own icon, not the waiting circle or the done tick. Lucide's `History` is an alias
      // and renders with its canonical name's class.
      expect(row.querySelector("svg.lucide-rotate-ccw-clock")).not.toBeNull();
      expect(row.querySelector("svg.lucide-circle-dashed")).toBeNull();
      expect(row.querySelector("svg.lucide-check")).toBeNull();
      // Not the running step, so no row claims to be the current one.
      expect(row).not.toHaveAttribute("aria-current");
    }
  });

  it("prints the pipeline's own milliseconds, and loaded-from-disk rows as such", () => {
    render(
      <OnboardingSteps
        steps={[
          { id: "area", progress: 100, elapsedS: 0, elapsedMs: 12.4, status: "done" },
          {
            id: "fetch",
            progress: 100,
            elapsedS: 1.4,
            elapsedMs: 1412,
            status: "loaded",
            detail: "34,410 roads, 72,573 buildings from OSM",
          },
          { id: "condition", progress: 100, elapsedS: 0, elapsedMs: 1400, status: "done" },
          { id: "drains", progress: 40, elapsedS: 12, elapsedMs: 12_300, status: "running" },
          { id: "graph", progress: 0, elapsedS: 0, status: "waiting" },
        ]}
      />,
    );
    expect(screen.getByText("Done 12 ms")).toBeInTheDocument();
    expect(screen.getByText("Loaded from disk")).toBeInTheDocument();
    expect(screen.getByText("34,410 roads, 72,573 buildings from OSM")).toBeInTheDocument();
    expect(screen.getByText("Done 1.4 s")).toBeInTheDocument();
    expect(screen.getByText("Running 12.3 s")).toBeInTheDocument();
    expect(screen.getByText("Waiting")).toBeInTheDocument();
    // The running row is the current step, and only it.
    const rows = screen.getAllByRole("listitem");
    expect(rows.filter((row) => row.getAttribute("aria-current") === "step")).toHaveLength(1);
    // Each row's progress is its own 2 px underline, exposed as a progress bar.
    expect(screen.getByRole("progressbar", { name: "Infer drains progress" })).toHaveAttribute(
      "aria-valuenow",
      "40",
    );
    // Sentence case, no separators (SPEC.md 6.8).
    expect(document.body.textContent).not.toMatch(/ · /);
  });

  it("keeps the old seconds for callers that pass only elapsedS, and omits a zero", () => {
    render(
      <OnboardingSteps
        steps={[
          { id: "area", progress: 100, elapsedS: 0, status: "done" },
          { id: "fetch", progress: 40, elapsedS: 12, status: "running" },
          { id: "condition", progress: 100, elapsedS: 34, status: "done" },
        ]}
      />,
    );
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.getByText("Running 12 s")).toBeInTheDocument();
    expect(screen.getByText("Done 34 s")).toBeInTheDocument();
  });

  it("prints the first forecast's stages under its row, leaving out those not yet started", () => {
    render(
      <OnboardingSteps
        steps={[
          {
            id: "forecast",
            progress: 25,
            elapsedS: 20,
            elapsedMs: 20_000,
            status: "running",
            stages: [
              { label: "Sky", status: "done", ms: 135 },
              { label: "Twin", status: "running", ms: 12_100 },
              { label: "Pulse", status: "waiting", ms: null },
              { label: "Flash", status: "waiting", ms: null },
              { label: "products", status: "waiting", ms: null },
            ],
          },
        ]}
      />,
    );
    expect(screen.getByText("Sky 135 ms, Twin running 12.1 s")).toBeInTheDocument();
  });
});

describe("stepStatusText and stagesText", () => {
  it("says a skipped or failed step plainly", () => {
    expect(stepStatusText({ id: "forecast", progress: 0, elapsedS: 0, status: "skipped" })).toBe(
      "Skipped",
    );
    expect(
      stepStatusText({
        id: "drains",
        progress: 30,
        elapsedS: 0,
        elapsedMs: 2500,
        status: "failed",
      }),
    ).toBe("Failed 2.5 s");
  });

  it("drops a skipped Flash from a finished forecast", () => {
    expect(
      stagesText([
        { label: "Sky", status: "done", ms: 135 },
        { label: "Twin", status: "done", ms: 42_859 },
        { label: "Pulse", status: "done", ms: 981 },
        { label: "Flash", status: "skipped", ms: 0 },
        { label: "products", status: "done", ms: 2462 },
      ]),
    ).toBe("Sky 135 ms, Twin 42.9 s, Pulse 981 ms, products 2.5 s");
  });
});

describe("compact progress list and its details", () => {
  const steps: OnboardingStepState[] = [
    {
      id: "fetch",
      progress: 100,
      elapsedS: 42.4,
      elapsedMs: 42_400,
      status: "done",
      detail: "34,410 roads, 72,573 buildings from OSM",
    },
    {
      id: "forecast",
      progress: 100,
      elapsedS: 109,
      elapsedMs: 109_000,
      status: "done",
      detail: "15,454 of 18,626 streets wet",
      stages: [
        { label: "Sky", status: "done", ms: 15_400 },
        { label: "Twin", status: "done", ms: 51_700 },
      ],
    },
  ];

  it("keeps one line a step: name, status and time, no detail or stages", () => {
    render(<OnboardingSteps steps={steps} compact />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("Done 42.4 s")).toBeInTheDocument();
    expect(screen.queryByText("34,410 roads, 72,573 buildings from OSM")).toBeNull();
    expect(screen.queryByText(/Twin 51\.7 s/)).toBeNull();
  });

  it("puts each step's detail and the forecast's stage times behind the disclosure", () => {
    render(<OnboardingStepDetails steps={steps} />);
    expect(document.querySelector('dl[aria-label="Step details"]')).not.toBeNull();
    expect(screen.getByText("34,410 roads, 72,573 buildings from OSM")).toBeInTheDocument();
    expect(screen.getByText("15,454 of 18,626 streets wet")).toBeInTheDocument();
    expect(screen.getByText("Sky 15.4 s, Twin 51.7 s")).toBeInTheDocument();
  });

  it("renders nothing when no step reported a detail", () => {
    const { container } = render(<OnboardingStepDetails steps={IDLE_ONBOARDING_STEPS} />);
    expect(container).toBeEmptyDOMElement();
  });
});

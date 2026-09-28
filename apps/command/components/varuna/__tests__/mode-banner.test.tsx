import { render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";

import { ModeBanner, degradedLabel } from "@/components/varuna/mode-banner";
import { useReplayStore } from "@/lib/stores/replay";
import { useRunStore, type RunMeta } from "@/lib/stores/run";

const baseRun: RunMeta = {
  run_id: "MUM-20190702T1210Z-sky1.0-twin1.0-flash0.3-baked",
  city: "mumbai",
  cycle_ts: "2019-07-02T06:40:00+05:30",
  mode: "replay",
  replay_mode: "baked",
  stage_ms: { sky: 2100, twin: 1800 },
};

describe("ModeBanner", () => {
  beforeEach(() => {
    useRunStore.getState().clear();
    useReplayStore.getState().reset();
  });

  it("says what to do when there is no run", () => {
    render(<ModeBanner />);
    expect(screen.getByRole("status")).toHaveTextContent("No runs yet");
    expect(screen.getByRole("status")).toHaveAttribute("data-mode", "none");
  });

  it("builds the replay copy from the replay store and shows the baked chip", () => {
    useRunStore.getState().setRun(baseRun);
    useReplayStore.getState().setSimTime("2019-07-02T06:40:00+05:30");
    useReplayStore.getState().setSpeed(30);
    render(<ModeBanner />);
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("data-mode", "replay");
    expect(status).toHaveTextContent("Replay 30×");
    expect(status).toHaveTextContent("2 Jul 2019");
    expect(status).toHaveTextContent("06:40 IST");
    expect(screen.getByText("baked")).toBeInTheDocument();
  });

  it("reads Live for a live run without a baked chip", () => {
    useRunStore.getState().setRun({ ...baseRun, mode: "live", replay_mode: "live" });
    render(<ModeBanner />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Live$/);
    expect(screen.queryByText("baked")).not.toBeInTheDocument();
  });

  it("names the missing feeds when degraded", () => {
    useRunStore.getState().setRun({ ...baseRun, degraded_feeds: ["radar"] });
    render(<ModeBanner />);
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("data-mode", "degraded");
    expect(status).toHaveTextContent("Degraded: radar offline, using gauges and satellite");
    expect(degradedLabel(["radar", "traffic"])).toBe(
      "Degraded: radar and traffic offline, using gauges and satellite",
    );
  });

  it("says the run is loading, not that there are none, while the registry is asked", () => {
    useRunStore.getState().setStatus("loading");
    render(<ModeBanner />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Loading run");
    expect(status).not.toHaveTextContent("No runs yet");
    expect(status).toHaveAttribute("data-status", "loading");
  });

  it("says the registry could not be read, and why, instead of 'No runs yet'", () => {
    useRunStore
      .getState()
      .setStatus("error", "The API is unreachable, so the run registry could not be read.");
    render(<ModeBanner />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Runs unavailable");
    expect(status).not.toHaveTextContent("No runs yet");
    expect(status).toHaveAttribute(
      "title",
      "The API is unreachable, so the run registry could not be read.",
    );
  });

  it("renders 'Loading run' on the server, before the registry has been asked", () => {
    // The store starts at "none"; the server has not asked the registry, so that is no answer.
    const html = renderToString(<ModeBanner />);
    expect(html).toContain("Loading run");
    expect(html).not.toContain("No runs yet");
  });

  it("honours explicit props over the stores", () => {
    useRunStore.getState().setRun(baseRun);
    render(<ModeBanner mode="live" label="Live · Chennai" />);
    expect(screen.getByRole("status")).toHaveTextContent("Live · Chennai");
    expect(screen.queryByText("baked")).not.toBeInTheDocument();
  });
});

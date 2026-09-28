import { render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { RunStamp } from "@/components/varuna/run-stamp";
import { useLatestRun } from "@/lib/hooks/use-latest-run";
import { useRunStore } from "@/lib/stores/run";

function renderStamp() {
  return render(
    <TooltipProvider>
      <RunStamp />
    </TooltipProvider>,
  );
}

describe("RunStamp", () => {
  beforeEach(() => useRunStore.getState().clear());

  it("shimmers while the run is loading instead of saying 'No run'", () => {
    useRunStore.getState().setStatus("loading");
    const { container } = renderStamp();
    expect(container.querySelector('[data-slot="run-stamp"]')).toHaveAttribute(
      "data-state",
      "loading",
    );
    expect(screen.queryByText("No run")).toBeNull();
  });

  it("says 'No run' once the registry has answered with none", () => {
    renderStamp();
    expect(screen.getByText("No run")).toBeInTheDocument();
  });

  it("prints the run once there is one", () => {
    useRunStore.getState().setRun({
      run_id: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
      city: "mumbai",
      cycle_ts: "2019-07-02T08:40:00+05:30",
      mode: "replay",
      replay_mode: "baked",
    });
    renderStamp();
    expect(screen.getByRole("button", { name: "Copy run id" })).toHaveTextContent("baked");
  });
});

describe("useLatestRun", () => {
  const fetchMock = vi.fn<typeof fetch>();
  beforeEach(() => {
    useRunStore.getState().clear();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("marks the store loading until the registry answers, then 'none' for an empty registry", async () => {
    let answer: (response: Response) => void = () => undefined;
    fetchMock.mockReturnValue(new Promise<Response>((resolve) => (answer = resolve)));
    renderHook(() => useLatestRun());
    expect(useRunStore.getState().status).toBe("loading");
    answer(new Response(JSON.stringify({ runs: [] }), { status: 200 }));
    await waitFor(() => expect(useRunStore.getState().status).toBe("none"));
  });

  it("records an error, not an empty registry, when the API cannot be reached", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    renderHook(() => useLatestRun());
    await waitFor(() => expect(useRunStore.getState().status).toBe("error"));
    expect(useRunStore.getState().errorMessage).toMatch(/unreachable/);
  });

  it("never marks a run someone already drew as loading", () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    useRunStore.getState().setRun({
      run_id: "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
      city: "mumbai",
      cycle_ts: "2019-07-02T08:40:00+05:30",
      mode: "replay",
      replay_mode: "baked",
    });
    renderHook(() => useLatestRun());
    expect(useRunStore.getState().status).toBe("ready");
  });
});

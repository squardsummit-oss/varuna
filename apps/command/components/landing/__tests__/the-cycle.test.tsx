import { readFileSync } from "node:fs";
import path from "node:path";

import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  enterViewport,
  installIntersectionObserver,
  mockReducedMotion,
} from "@/components/landing/__tests__/browser-stubs";
import { CYCLE_BEAMS, CYCLE_NODES } from "@/components/landing/cycle-timings";
import { TheCycle } from "@/components/landing/the-cycle";
import { formatMs } from "@/lib/format";

const committed = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../public/cycle-status.json"), "utf8"),
) as { run_id: string; stage_ms: Record<string, number>; budget_ms: Record<string, number> };

/** The live body the API serves: the committed copy without its provenance note. */
const liveBody = { ...committed, run_id: "MUM-live-test-run", fallback: undefined };

/** The desktop layout: one row, Twin above Flash, so every beam has a real segment. */
const POSITION: Record<string, [number, number]> = {
  ingest: [0, 75],
  sky: [200, 75],
  twin: [400, 0],
  flash: [400, 150],
  pulse: [600, 75],
  products: [800, 75],
  outputs: [1000, 75],
};

function layOut() {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    const id = this.dataset.cycleNode;
    let box = { x: 0, y: 0, width: 0, height: 0 };
    if (id) {
      const [x, y] = POSITION[id];
      box = { x, y, width: 150, height: 100 };
    } else if (this.querySelector("[data-cycle-node]")) {
      box = { x: 0, y: 0, width: 1600, height: 300 };
    }
    return {
      ...box,
      left: box.x,
      top: box.y,
      right: box.x + box.width,
      bottom: box.y + box.height,
      toJSON: () => box,
    } as DOMRect;
  });
}

function respond(live: "ok" | "down", committedOk = true) {
  const fetchMock = vi.fn((url: string) => {
    if (url.includes("/v1/cycle/status")) {
      return live === "ok"
        ? Promise.resolve({ ok: true, json: () => Promise.resolve(liveBody) } as Response)
        : Promise.reject(new TypeError("Failed to fetch"));
    }
    return committedOk
      ? Promise.resolve({ ok: true, json: () => Promise.resolve(committed) } as Response)
      : Promise.resolve({ ok: false, json: () => Promise.resolve({}) } as Response);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const timingOf = (container: HTMLElement, id: string) =>
  container.querySelector(`[data-timing="${id}"]`)?.textContent;

beforeEach(() => {
  installIntersectionObserver();
  layOut();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("TheCycle", () => {
  it("does not fetch until the section is near the viewport, and shows skeletons meanwhile", () => {
    mockReducedMotion(false);
    const fetchMock = respond("ok");
    const { container } = render(<TheCycle />);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(CYCLE_NODES.length);
    expect(container.querySelector("[data-timing]")).toBeNull();
  });

  it("prints each node's measured time from the API, and not timed where the run has none", async () => {
    mockReducedMotion(false);
    respond("ok");
    const { container } = render(<TheCycle />);
    act(() => enterViewport());
    await waitFor(() =>
      expect(timingOf(container, "twin")).toBe(formatMs(committed.stage_ms.twin)),
    );
    expect(timingOf(container, "sky")).toBe(formatMs(committed.stage_ms.sky));
    expect(timingOf(container, "flash")).toBe(formatMs(committed.stage_ms.flash));
    expect(timingOf(container, "ingest")).toBe("Not timed");
    expect(timingOf(container, "outputs")).toBe("Not timed");
    expect(screen.getByText("MUM-live-test-run")).toBeInTheDocument();
    expect(screen.getByText(/^Measured on run/)).toBeInTheDocument();
  });

  it("falls back to the committed copy and says so when the API is unreachable", async () => {
    mockReducedMotion(false);
    respond("down");
    const { container } = render(<TheCycle />);
    act(() => enterViewport());
    await waitFor(() => expect(screen.getByText(committed.run_id)).toBeInTheDocument());
    expect(screen.getByText(/committed with this page/)).toBeInTheDocument();
    expect(timingOf(container, "pulse")).toBe(formatMs(committed.stage_ms.pulse));
  });

  it("says no run has been timed when neither source answers, with no numbers", async () => {
    mockReducedMotion(false);
    respond("down", false);
    const { container } = render(<TheCycle />);
    act(() => enterViewport());
    await waitFor(() => expect(screen.getByText(/No run has been timed yet/)).toBeInTheDocument());
    for (const node of CYCLE_NODES) expect(timingOf(container, node.id)).toBe("Not timed");
    expect(container.textContent).not.toMatch(/Budget/);
  });

  it("draws static arrows and no travelling beam under reduced motion", async () => {
    mockReducedMotion(true);
    respond("ok");
    const { container } = render(<TheCycle />);
    act(() => enterViewport());
    await waitFor(() =>
      expect(container.querySelectorAll('path[data-beam="track"]')).toHaveLength(
        CYCLE_BEAMS.length,
      ),
    );
    const tracks = [...container.querySelectorAll('path[data-beam="track"]')];
    expect(tracks.every((path) => path.getAttribute("marker-end")?.startsWith("url(#"))).toBe(true);
    expect(container.querySelector("marker")).not.toBeNull();
    expect(container.querySelector('[data-beam="travel"]')).toBeNull();
  });

  it("sends beams only once the diagram is in view, with no arrowheads", async () => {
    mockReducedMotion(false);
    respond("ok");
    const { container } = render(<TheCycle />);
    await waitFor(() =>
      expect(container.querySelectorAll('path[data-beam="track"]')).toHaveLength(
        CYCLE_BEAMS.length,
      ),
    );
    expect(container.querySelector('[data-beam="travel"]')).toBeNull();
    act(() => enterViewport());
    await waitFor(() =>
      expect(container.querySelectorAll('[data-beam="travel"]')).toHaveLength(CYCLE_BEAMS.length),
    );
    expect(container.querySelector("path[marker-end]")).toBeNull();
  });
});

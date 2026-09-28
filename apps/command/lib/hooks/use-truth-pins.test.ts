import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GroundTruthPin } from "@/lib/api/ground-truth";

const pins: GroundTruthPin[] = [
  // Two of the 2 July bundle's shape: one from the day before the window, one inside it.
  pinAt("P-bhakti", "2019-07-01T11:52:00+05:30", "Bhakti Park"),
  pinAt("P-kranti", "2019-07-02T08:07:00+05:30", "Kranti Nagar"),
  pinAt("P-gandhi", "2019-07-02T08:47:00+05:30", "Gandhi Market"),
];

function pinAt(id: string, ts: string, name: string): GroundTruthPin {
  return {
    id,
    ts,
    tsUncertaintyMin: 10,
    name,
    lon: 72.858,
    lat: 19.032,
    depthCm: null,
    depthPhrase: null,
    kind: "log",
    text: null,
    sourceUrl: "https://example.org/source",
    sourceTitle: null,
    insideAoi: true,
  };
}

vi.mock("@/lib/api/ground-truth", async (original) => {
  const actual = await original<typeof import("@/lib/api/ground-truth")>();
  return {
    ...actual,
    loadGroundTruth: vi.fn(async () => ({ bundle: "MUM-2019-07-02", count: 3, pins, notes: [] })),
  };
});

const { DROP_MS, formatPinTime, useTruthPins } = await import("./use-truth-pins");

let reduced = false;
let frames = new Map<number, FrameRequestCallback>();
let renders = 0;

beforeEach(() => {
  reduced = false;
  renders = 0;
  frames = new Map();
  let nextId = 1;
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: reduced && query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    const id = nextId++;
    frames.set(id, callback);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function flushFrame(at: number) {
  const queued = [...frames.values()];
  frames.clear();
  act(() => {
    for (const callback of queued) callback(at);
  });
}

async function mount(simTime: string) {
  const hook = renderHook(
    ({ time }: { time: string | null }) => {
      renders += 1;
      return useTruthPins("MUM-2019-07-02", time);
    },
    { initialProps: { time: simTime as string | null } },
  );
  await waitFor(() => expect(hook.result.current.all).toHaveLength(3));
  // Only now: `waitFor` polls on real timers, and the drop's end is what the fake ones control.
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  return hook;
}

const dropState = (state: { dropping: { id: string; dropStartMs?: number }[] }) =>
  Object.fromEntries(state.dropping.map((p) => [p.id, p.dropStartMs ?? "landed"]));

describe("useTruthPins (motion M18)", () => {
  it("lists what the clock had passed when first read as landed, with nothing scheduled", async () => {
    const { result } = await mount("2019-07-02T08:45:00+05:30");
    expect(result.current.passed.map((p) => p.id)).toEqual(["P-kranti", "P-bhakti"]);
    expect(dropState(result.current)).toEqual({ "P-kranti": "landed", "P-bhakti": "landed" });
    expect(result.current.passed[0]?.clockTs).toBe("2019-07-02T08:45:00+05:30");
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("drops a pin the clock passes: pending, stamped on one frame, landed when the drop ends", async () => {
    const { result, rerender } = await mount("2019-07-02T08:45:00+05:30");
    rerender({ time: "2019-07-02T08:50:00+05:30" });
    // The render that sees the clock move already holds the new pin back from drawing.
    expect(dropState(result.current)).toMatchObject({ "P-gandhi": Number.POSITIVE_INFINITY });
    expect(frames.size).toBe(1);

    vi.spyOn(performance, "now").mockReturnValue(5000);
    flushFrame(5000);
    expect(dropState(result.current)).toEqual({
      "P-gandhi": 5000,
      "P-kranti": "landed",
      "P-bhakti": "landed",
    });

    // The animation itself is deck's: nothing here queues another frame or renders again.
    expect(frames.size).toBe(0);
    const rendersWhileDropping = renders;
    act(() => {
      vi.advanceTimersByTime(DROP_MS - 1);
    });
    expect(renders).toBe(rendersWhileDropping);
    expect(dropState(result.current)["P-gandhi"]).toBe(5000);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(dropState(result.current)["P-gandhi"]).toBe("landed");
    expect(vi.getTimerCount()).toBe(0);
    expect(frames.size).toBe(0);
  });

  it("drops a pin again when the operator scrubs back before it and forward past it", async () => {
    const { result, rerender } = await mount("2019-07-02T08:50:00+05:30");
    expect(dropState(result.current)["P-gandhi"]).toBe("landed");
    rerender({ time: "2019-07-02T08:30:00+05:30" });
    expect(result.current.passed.map((p) => p.id)).not.toContain("P-gandhi");
    rerender({ time: "2019-07-02T08:55:00+05:30" });
    expect(dropState(result.current)["P-gandhi"]).toBe(Number.POSITIVE_INFINITY);
    flushFrame(9000);
    expect(dropState(result.current)["P-gandhi"]).toBe(9000);
  });

  it("under reduced motion a passed pin appears at once: no pending state, no frame, no timer", async () => {
    reduced = true;
    const { result, rerender } = await mount("2019-07-02T08:45:00+05:30");
    rerender({ time: "2019-07-02T08:50:00+05:30" });
    expect(dropState(result.current)["P-gandhi"]).toBe("landed");
    expect(frames.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("starts again from a first reading when the clock goes away and comes back", async () => {
    const { result, rerender } = await mount("2019-07-02T08:45:00+05:30");
    rerender({ time: null });
    expect(result.current.passed).toEqual([]);
    rerender({ time: "2019-07-02T09:10:00+05:30" });
    expect(dropState(result.current)["P-gandhi"]).toBe("landed");
    expect(frames.size).toBe(0);
  });
});

describe("formatPinTime", () => {
  const clock = "2019-07-02T08:45:00+05:30";

  it("dates a pin from the day before the replay", () => {
    expect(formatPinTime("2019-07-01T11:52:00+05:30", clock)).toBe("1 Jul 11:52");
  });

  it("gives a bare time on the replay clock's own day", () => {
    expect(formatPinTime("2019-07-02T08:07:00+05:30", clock)).toBe("08:07");
  });

  it("compares IST days, not UTC ones", () => {
    // 23:50 IST on 1 July is 18:20 UTC on 1 July; 00:10 IST on 2 July is still 1 July in UTC.
    expect(formatPinTime("2019-07-01T23:50:00+05:30", "2019-07-02T00:10:00+05:30")).toBe(
      "1 Jul 23:50",
    );
    // 04:00 IST on 2 July is 22:30 UTC on 1 July, yet it is the replay's day.
    expect(formatPinTime("2019-07-02T04:00:00+05:30", clock)).toBe("04:00");
  });

  it("dates every pin when there is no clock to compare with", () => {
    expect(formatPinTime("2019-07-02T08:07:00+05:30", undefined)).toBe("2 Jul 08:07");
  });

  it("keeps the unreadable-time marker for an unreadable timestamp", () => {
    expect(formatPinTime("not a time", clock)).toBe("--:--");
  });
});

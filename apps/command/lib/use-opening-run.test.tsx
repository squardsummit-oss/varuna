/**
 * The run a screen opens on when the URL pins none (tasks D-20, and `/console`'s own rule).
 *
 * `/map` used to open on whatever the API called newest, which on the replay is the 09:10 cycle -
 * the calm one after the storm had passed, where a map about which streets are passable has
 * nothing to show. These pin the id it opens on instead.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useOpeningRun } from "@/lib/use-opening-run";

const OPENING = "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked";
const NEWEST = "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked";

const REGISTRY = {
  runs: [
    { run_id: NEWEST, cycle_ts: "2019-07-02T09:10:00+05:30" },
    { run_id: OPENING, cycle_ts: "2019-07-02T06:40:00+05:30" },
    {
      run_id: "MUM-20190702T0040Z-sky1.0-twin1.0-flash0.1-baked",
      cycle_ts: "2019-07-02T06:10:00+05:30",
    },
  ],
};

function stubFetch(body: unknown, ok = true) {
  const fetchMock = vi.fn(async () => ({ ok, json: async () => body }) as unknown as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("useOpeningRun", () => {
  it("opens on the 06:40 storm cycle, not on the newest run", async () => {
    stubFetch(REGISTRY);
    const { result } = renderHook(() => useOpeningRun());
    await waitFor(() => expect(result.current.resolved).toBe(true));
    expect(result.current.runId).toBe(OPENING);
  });

  it("asks the registry for the city it is showing", async () => {
    const fetchMock = stubFetch({ runs: [] });
    renderHook(() => useOpeningRun("chennai"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(String(url)).toContain("city=chennai");
  });

  it("lets a pinned run win without reading the registry at all", async () => {
    const fetchMock = stubFetch(REGISTRY);
    const { result } = renderHook(() => useOpeningRun("mumbai", NEWEST));
    expect(result.current).toEqual({ runId: NEWEST, resolved: true });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("falls back to the API's own default when no run sits at the opening instant", async () => {
    stubFetch({ runs: [REGISTRY.runs[0]] });
    const { result } = renderHook(() => useOpeningRun());
    await waitFor(() => expect(result.current.resolved).toBe(true));
    // Undefined, not the newest id: the map then asks for no run and the API picks, rather than
    // this inventing a nearby cycle and calling it the opening.
    expect(result.current.runId).toBeUndefined();
  });

  it("resolves rather than hanging when the registry is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("offline");
      }),
    );
    const { result } = renderHook(() => useOpeningRun());
    await waitFor(() => expect(result.current.resolved).toBe(true));
    expect(result.current.runId).toBeUndefined();
  });
});

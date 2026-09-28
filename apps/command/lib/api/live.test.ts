import { describe, expect, it, vi } from "vitest";

import { LiveBus, parseLiveEvent } from "./live";

describe("parseLiveEvent", () => {
  it("accepts a typed frame and keeps extra fields", () => {
    const event = parseLiveEvent(
      JSON.stringify({ type: "replay.clock", ts: "2019-07-02T06:40:00+05:30", payload: { sim_time: "x" } }),
    );
    expect(event?.type).toBe("replay.clock");
    expect(event?.payload).toEqual({ sim_time: "x" });
  });
  it("rejects frames without a type, invalid JSON and binary data", () => {
    expect(parseLiveEvent(JSON.stringify({ payload: 1 }))).toBeNull();
    expect(parseLiveEvent("{not json")).toBeNull();
    expect(parseLiveEvent(new ArrayBuffer(2))).toBeNull();
  });
});

describe("LiveBus", () => {
  it("routes events by topic and supports the wildcard", () => {
    const bus = new LiveBus();
    const runs = vi.fn();
    const all = vi.fn();
    const off = bus.subscribe(["runs.published"], runs);
    bus.subscribe("*", all);

    bus.emit({ type: "runs.published", payload: { run_id: "MUM-1" } });
    bus.emit({ type: "ping" });

    expect(runs).toHaveBeenCalledTimes(1);
    expect(all).toHaveBeenCalledTimes(2);

    off();
    bus.emit({ type: "runs.published" });
    expect(runs).toHaveBeenCalledTimes(1);
    expect(bus.size).toBe(1);
  });

  it("keeps delivering when one subscriber throws", () => {
    const bus = new LiveBus();
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const broken = vi.fn(() => {
      throw new Error("panel crashed");
    });
    const healthy = vi.fn();
    bus.subscribe(["alert.raised"], broken);
    bus.subscribe(["alert.raised"], healthy);
    bus.emit({ type: "alert.raised" });
    expect(healthy).toHaveBeenCalledTimes(1);
    expect(error).toHaveBeenCalled();
    error.mockRestore();
  });
});

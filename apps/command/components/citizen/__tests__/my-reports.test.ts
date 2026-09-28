import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MY_REPORTS_KEY,
  MY_REPORTS_MAX,
  readMyReports,
  rememberMyReport,
} from "@/components/citizen/my-reports";

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("my reports", () => {
  it("remembers ids newest first, once each, capped", () => {
    expect(rememberMyReport("rpt-1-aaaaaa", "2026-09-27T08:40:00+05:30")).toBe(true);
    expect(rememberMyReport("rpt-2-bbbbbb", "2026-09-27T08:45:00+05:30")).toBe(true);
    expect(rememberMyReport("rpt-1-aaaaaa", "2026-09-27T08:50:00+05:30")).toBe(true);
    expect(readMyReports()).toEqual([
      { id: "rpt-1-aaaaaa", sentAt: "2026-09-27T08:50:00+05:30" },
      { id: "rpt-2-bbbbbb", sentAt: "2026-09-27T08:45:00+05:30" },
    ]);

    for (let i = 0; i < MY_REPORTS_MAX + 5; i += 1) rememberMyReport(`rpt-${i + 10}-cccccc`);
    expect(readMyReports()).toHaveLength(MY_REPORTS_MAX);
    expect(readMyReports()[0]?.id).toBe(`rpt-${MY_REPORTS_MAX + 14}-cccccc`);
  });

  it("refuses an id the API would not mint", () => {
    expect(rememberMyReport("<script>")).toBe(false);
    expect(rememberMyReport("")).toBe(false);
    expect(window.localStorage.getItem(MY_REPORTS_KEY)).toBeNull();
  });

  it("reads nothing from a value that is not its own", () => {
    window.localStorage.setItem(MY_REPORTS_KEY, "{not json");
    expect(readMyReports()).toEqual([]);
    window.localStorage.setItem(
      MY_REPORTS_KEY,
      JSON.stringify([{ id: "rpt-1-aaaaaa", sentAt: "x" }, { id: 4 }, "rpt-2"]),
    );
    expect(readMyReports()).toEqual([{ id: "rpt-1-aaaaaa", sentAt: "x" }]);
  });

  it("answers false and empty when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    expect(readMyReports()).toEqual([]);
    expect(rememberMyReport("rpt-1-aaaaaa")).toBe(false);
  });
});

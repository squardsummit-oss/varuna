/**
 * The dashboard's report feeds: the public list as it is drawn, and the reader's own reports
 * polled until the ward desk's status reaches them.
 */

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MY_REPORTS_KEY } from "@/components/citizen/my-reports";
import {
  inCity,
  REPORTS_POLL_MS,
  reportTime,
  useMyReports,
  usePublicReports,
  visibleReports,
} from "@/components/citizen/use-report-feeds";
import type { PublicReport, ReportList } from "@/lib/api/reports";

import { apiError, citizenReport, fetchByPath, seedReport } from "./report-fixtures";

function list(reports: PublicReport[]): ReportList {
  return { count: reports.length, reports };
}

describe("visibleReports", () => {
  it("drops dismissed reports and puts the newest first", () => {
    const old = seedReport({ id: "old", ts: "2019-07-02T07:05:00+05:30" });
    const fresh = citizenReport({ id: "fresh" });
    const dismissed = citizenReport({ id: "gone", status: "dismissed" });
    expect(visibleReports(list([old, dismissed, fresh])).map((r) => r.id)).toEqual([
      "fresh",
      "old",
    ]);
    expect(visibleReports(null)).toEqual([]);
  });

  it("keeps a city's reports and drops the rest, judging an unclassified report by the city's box", () => {
    const here = citizenReport({ id: "here" });
    const filedElsewhere = citizenReport({ id: "chennai", city: "chennai" });
    const outside = citizenReport({ id: "outside", city: null, outside_aoi: true });
    // An API from before 2026-09-26 states no city at all; the deployed one listed this report.
    const tirupati = citizenReport({ id: "tirupati", city: undefined, lat: 13.6779, lon: 79.554 });
    const unclassifiedHere = citizenReport({ id: "hindmata", city: undefined });
    expect(inCity(here, "mumbai")).toBe(true);
    expect(inCity(filedElsewhere, "mumbai")).toBe(false);
    expect(inCity(outside, "mumbai")).toBe(false);
    expect(inCity(tirupati, "mumbai")).toBe(false);
    expect(inCity(unclassifiedHere, "mumbai")).toBe(true);
    expect(
      visibleReports(list([here, filedElsewhere, outside, tirupati, unclassifiedHere]), "mumbai")
        .map((r) => r.id)
        .sort(),
    ).toEqual(["here", "hindmata"]);
    // Without a city nothing is filtered by place.
    expect(visibleReports(list([here, tirupati]))).toHaveLength(2);
  });

  it("sorts by when the water was seen, else when the report arrived", () => {
    expect(reportTime(seedReport({ ts: null, received_at: "2026-09-27T10:00:00+05:30" }))).toBe(
      Date.parse("2026-09-27T10:00:00+05:30"),
    );
    expect(reportTime(seedReport({ ts: null, received_at: null }))).toBe(Number.NEGATIVE_INFINITY);
  });
});

function PublicProbe() {
  const feed = usePublicReports("mumbai");
  return (
    <p data-testid="public">
      {feed.loaded ? feed.reports.map((r) => r.id).join(",") || "none" : "loading"}
      {feed.error ? ` | ${feed.error}` : ""}
    </p>
  );
}

function MineProbe() {
  const feed = useMyReports();
  return (
    <ul>
      {feed.items.map((item) => (
        <li key={item.entry.id} data-testid={item.entry.id}>
          {item.kind}
          {item.kind === "ready" ? `:${item.report.status}` : ""}
          {item.kind === "missing" || item.kind === "error" ? `:${item.message}` : ""}
        </li>
      ))}
      <li data-testid="ready">{String(feed.ready)}</li>
    </ul>
  );
}

describe("usePublicReports", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asks for the city's reports and keeps the dismissed ones off the list", async () => {
    const fetch = fetchByPath([
      [
        (path) => path === "/v1/reports",
        () => list([seedReport(), citizenReport({ id: "gone", status: "dismissed" })]),
      ],
    ]);
    vi.stubGlobal("fetch", fetch);
    render(<PublicProbe />);
    expect(await screen.findByText("seed-RPT-MUM-HS-01-ankle")).toBeTruthy();
    const asked = new URL(String(fetch.mock.calls[0]?.[0]), "http://api.test");
    expect(asked.searchParams.get("city")).toBe("mumbai");
  });

  it("says what failed rather than showing an empty list as if there were no complaints", async () => {
    vi.stubGlobal("fetch", fetchByPath([]));
    render(<PublicProbe />);
    expect(await screen.findByText(/none \| /)).toBeTruthy();
  });
});

describe("useMyReports", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("reads nothing and asks for nothing when this browser has sent no report", async () => {
    const fetch = fetchByPath([]);
    vi.stubGlobal("fetch", fetch);
    render(<MineProbe />);
    expect(await screen.findByText("true")).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("follows a report until the ward desk's status reaches the reader", async () => {
    window.localStorage.setItem(
      MY_REPORTS_KEY,
      JSON.stringify([{ id: "rpt-1789225538684", sentAt: "2026-09-27T12:40:02.000Z" }]),
    );
    let status: PublicReport["status"] = "received";
    vi.stubGlobal(
      "fetch",
      fetchByPath([
        [(path) => path === "/v1/reports/rpt-1789225538684", () => citizenReport({ status })],
      ]),
    );
    render(<MineProbe />);
    expect(await screen.findByText("ready:received")).toBeTruthy();

    // The officer marks it at the desk; the dashboard hears it on its next poll.
    status = "crew_sent";
    await act(async () => {
      vi.advanceTimersByTime(REPORTS_POLL_MS);
    });
    expect(await screen.findByText("ready:crew_sent")).toBeTruthy();
  });

  it("keeps the API's words when it has no report by that id", async () => {
    window.localStorage.setItem(
      MY_REPORTS_KEY,
      JSON.stringify([{ id: "rpt-lost", sentAt: "2026-09-27T12:40:02.000Z" }]),
    );
    vi.stubGlobal(
      "fetch",
      fetchByPath([
        [(path) => path === "/v1/reports/rpt-lost", () => apiError(404, "No report rpt-lost.")],
      ]),
    );
    render(<MineProbe />);
    expect(await screen.findByText("missing:No report rpt-lost.")).toBeTruthy();
  });
});

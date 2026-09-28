/**
 * The desk's report list: which list it reads, how it stays fresh, and what selecting does.
 *
 * The inbox and the ward map both draw from this, so a new citizen complaint has to reach it
 * without a reload (a poll, and a reload after the desk's own write), a refused passphrase has to
 * close the desk rather than blank the inbox, and choosing a report from the inbox has to give the
 * map somewhere to fly.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const client = vi.hoisted(() => ({ loadReports: vi.fn(), loadDeskReports: vi.fn() }));

vi.mock("@/lib/api/reports", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/reports")>();
  return {
    ...actual,
    loadReports: client.loadReports,
    loadDeskReports: client.loadDeskReports,
  };
});

import {
  REPORT_FOCUS_ZOOM,
  useDeskReportsLoader,
  type DeskReports,
  type DeskReportsOptions,
} from "@/components/authority/desk-reports";
import { ApiError } from "@/lib/api/client";
import type { PublicReport, ReportList } from "@/lib/api/reports";

function report(id: string, overrides: Partial<PublicReport> = {}): PublicReport {
  return {
    id,
    origin: "citizen",
    synthetic: false,
    ts: "2026-09-27T10:12:00+05:30",
    received_at: "2026-09-27T10:12:04+05:30",
    lat: 19.027,
    lon: 72.857,
    coordinates: "rounded to 3 decimals",
    city: "mumbai",
    outside_aoi: false,
    place: null,
    depth_hint: "knee",
    depth_cm: 45,
    text: null,
    source: "public-map",
    photo_attached: false,
    has_photo: false,
    photo_url: null,
    thumb_url: null,
    photo_note: null,
    credit: null,
    status: "received",
    status_ts: null,
    history: [],
    ...overrides,
  };
}

function listOf(...reports: PublicReport[]): ReportList {
  return { count: reports.length, reports };
}

/** The hook's latest answer, published after each render for the test to act on. */
const handle: { current: DeskReports | null } = { current: null };

function Probe(props: DeskReportsOptions) {
  const desk = useDeskReportsLoader(props);
  useEffect(() => {
    handle.current = desk;
  });
  return (
    <div>
      <p data-testid="ids">
        {desk.list ? desk.list.reports.map((r) => r.id).join(",") : "loading"}
      </p>
      <p data-testid="exact">{String(desk.exact)}</p>
      <p data-testid="status">{desk.list?.reports.map((r) => r.status).join(",")}</p>
      <p data-testid="error">{desk.error ?? ""}</p>
    </div>
  );
}

beforeEach(() => {
  handle.current = null;
  client.loadReports.mockReset();
  client.loadDeskReports.mockReset();
});

describe("useDeskReportsLoader", () => {
  it("reads the public list while the desk is closed", async () => {
    client.loadReports.mockResolvedValue(listOf(report("rpt-1")));
    render(<Probe city="mumbai" deskOpen={false} />);
    await waitFor(() => expect(screen.getByTestId("ids")).toHaveTextContent("rpt-1"));
    expect(client.loadReports).toHaveBeenCalledWith(expect.objectContaining({ city: "mumbai" }));
    expect(client.loadDeskReports).not.toHaveBeenCalled();
    expect(screen.getByTestId("exact")).toHaveTextContent("false");
  });

  it("polls, so a complaint sent after the desk opened appears without a reload", async () => {
    client.loadReports
      .mockResolvedValueOnce(listOf(report("rpt-1")))
      .mockResolvedValue(listOf(report("rpt-2"), report("rpt-1")));
    render(<Probe city="mumbai" deskOpen={false} pollMs={40} />);
    await waitFor(() => expect(screen.getByTestId("ids")).toHaveTextContent(/^rpt-1$/));
    await waitFor(() => expect(screen.getByTestId("ids")).toHaveTextContent("rpt-2,rpt-1"));
  });

  it("reloads at once after the desk's own write", async () => {
    client.loadReports.mockResolvedValue(listOf(report("rpt-1")));
    const { rerender } = render(<Probe city="mumbai" deskOpen={false} refreshKey={0} />);
    await waitFor(() => expect(client.loadReports).toHaveBeenCalledTimes(1));
    rerender(<Probe city="mumbai" deskOpen={false} refreshKey={1} />);
    await waitFor(() => expect(client.loadReports).toHaveBeenCalledTimes(2));
  });

  it("reads the desk's exact list once the desk is open", async () => {
    client.loadDeskReports.mockResolvedValue(listOf(report("rpt-1", { lat: 19.02712 })));
    render(<Probe city="mumbai" deskOpen />);
    await waitFor(() => expect(screen.getByTestId("exact")).toHaveTextContent("true"));
    expect(client.loadReports).not.toHaveBeenCalled();
  });

  it("drops the exact list the moment the desk closes", async () => {
    client.loadDeskReports.mockResolvedValue(listOf(report("rpt-1", { lat: 19.02712 })));
    let answer: (list: ReportList) => void = () => {};
    client.loadReports.mockImplementation(
      () => new Promise<ReportList>((resolve) => (answer = resolve)),
    );
    const { rerender } = render(<Probe city="mumbai" deskOpen />);
    await waitFor(() => expect(screen.getByTestId("exact")).toHaveTextContent("true"));

    rerender(<Probe city="mumbai" deskOpen={false} />);
    // Nothing exact stays on screen while the public list is on its way.
    expect(screen.getByTestId("ids")).toHaveTextContent("loading");
    expect(screen.getByTestId("exact")).toHaveTextContent("false");
    act(() => answer(listOf(report("rpt-1"))));
    await waitFor(() => expect(screen.getByTestId("ids")).toHaveTextContent("rpt-1"));
  });

  it("hands a refused passphrase to the screen and falls back to the public list", async () => {
    const onGateRefused = vi.fn();
    client.loadDeskReports.mockRejectedValue(
      new ApiError({
        code: "ops_passphrase_rejected",
        status: 403,
        path: "/v1/ops/reports",
        message: "The X-Varuna-Ops passphrase does not match this API's VARUNA_OPS_PASSPHRASE.",
      }),
    );
    client.loadReports.mockResolvedValue(listOf(report("rpt-1")));
    render(<Probe city="mumbai" deskOpen onGateRefused={onGateRefused} />);
    await waitFor(() => expect(screen.getByTestId("ids")).toHaveTextContent("rpt-1"));
    expect(onGateRefused).toHaveBeenCalledWith(expect.objectContaining({ kind: "rejected" }));
    expect(screen.getByTestId("exact")).toHaveTextContent("false");
  });

  it("keeps the last list and prints the API's sentence when a poll fails", async () => {
    client.loadReports.mockResolvedValueOnce(listOf(report("rpt-1"))).mockRejectedValue(
      new ApiError({
        code: "unreachable",
        status: 0,
        path: "/v1/reports",
        message: "The VARUNA API did not answer.",
      }),
    );
    render(<Probe city="mumbai" deskOpen={false} pollMs={40} />);
    await waitFor(() =>
      expect(screen.getByTestId("error")).toHaveTextContent("The VARUNA API did not answer."),
    );
    expect(screen.getByTestId("ids")).toHaveTextContent("rpt-1");
  });

  it("flies only when asked, to the report's own coordinates", async () => {
    client.loadReports.mockResolvedValue(listOf(report("rpt-1", { lon: 72.841, lat: 19.012 })));
    render(<Probe city="mumbai" deskOpen={false} />);
    await waitFor(() => expect(screen.getByTestId("ids")).toHaveTextContent("rpt-1"));

    act(() => handle.current?.select("rpt-1"));
    expect(handle.current?.selectedId).toBe("rpt-1");
    expect(handle.current?.focus).toBeNull();

    act(() => handle.current?.select("rpt-1", { fly: true }));
    expect(handle.current?.focus).toMatchObject({
      lon: 72.841,
      lat: 19.012,
      zoom: REPORT_FOCUS_ZOOM,
    });
    const first = handle.current?.focus?.key;
    // A second request flies again: the key is new every time.
    await new Promise((resolve) => setTimeout(resolve, 2));
    act(() => handle.current?.select("rpt-1", { fly: true }));
    expect(handle.current?.focus?.key).not.toBe(first);
  });

  it("puts a write's answer in place before the next poll", async () => {
    client.loadReports.mockResolvedValue(listOf(report("rpt-1"), report("rpt-2")));
    render(<Probe city="mumbai" deskOpen={false} />);
    await waitFor(() =>
      expect(screen.getByTestId("status")).toHaveTextContent("received,received"),
    );
    act(() => handle.current?.replace(report("rpt-2", { status: "crew_sent" })));
    expect(screen.getByTestId("status")).toHaveTextContent("received,crew_sent");
  });
});

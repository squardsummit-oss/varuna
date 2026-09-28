/**
 * The link between the desk's inbox and its ward map (user request 2026-09-27).
 *
 * Opening a row in the inbox flies the ward map to that report's pin and highlights it; tapping a
 * pin opens its row. Driven through the real screen and the real `WardMap`, with `FloodMap`
 * replaced by a double that records what it was handed and can tap a pin, because deck.gl cannot
 * pick in jsdom.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MapFocus } from "@/components/map/city-map";
import type { OpsLog } from "@/lib/api/ops";
import type { PublicReport, ReportList, ReportPin } from "@/lib/api/reports";

vi.mock("@/components/varuna/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/varuna/cycle-picker", () => ({ CyclePicker: () => null }));

interface FloodMapDouble {
  reports?: readonly ReportPin[];
  selectedReportId?: string | null;
  onPickReport?: (id: string) => void;
  focus?: MapFocus | null;
}

vi.mock("@/components/map/flood-map", () => ({
  FloodMap: ({ reports = [], selectedReportId, onPickReport, focus }: FloodMapDouble) => (
    <div data-testid="flood-map">
      <p data-testid="map-selected">{selectedReportId ?? "none"}</p>
      <p data-testid="map-focus">{focus ? `${focus.lon},${focus.lat},${focus.zoom}` : "none"}</p>
      {reports.map((pin) => (
        <button key={pin.id} type="button" onClick={() => onPickReport?.(pin.id)}>
          Pin {pin.id}
        </button>
      ))}
    </div>
  ),
}));

const loadOpsLog = vi.fn<(options?: { city?: string }) => Promise<OpsLog>>();
vi.mock("@/lib/api/ops", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/ops")>()),
  loadOpsLog: (options?: { city?: string }) => loadOpsLog(options),
}));

const loadReports = vi.fn<(query?: { city?: string }) => Promise<ReportList>>();
vi.mock("@/lib/api/reports", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/reports")>()),
  loadReports: (query?: { city?: string }) => loadReports(query),
}));

import { AuthorityScreen } from "@/app/authority/authority-screen";
import { WardMap } from "@/app/authority/ward-map";

const READ_ONLY_LOG: OpsLog = {
  city: "mumbai",
  nEntries: 0,
  entries: [],
  writesEnabled: false,
  passphraseEnv: "VARUNA_OPS_PASSPHRASE",
  // In the order the API sends them (measured 2026-09-27): the read-only reason is not last.
  notes: [
    "An authority edit changes no forecast.",
    "This API is read-only: VARUNA_OPS_PASSPHRASE is not set where it runs.",
    "Report statuses name the officer's role, not the officer. Send the desk passphrase in X-Varuna-Ops to see the name.",
  ],
};

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

const HINDMATA = report("seed-hindmata-1", {
  origin: "seed",
  synthetic: true,
  place: "Hindmata junction",
  lon: 72.841,
  lat: 19.012,
});
const KINGS_CIRCLE = report("rpt-1789225538684-a1b2c3", { lon: 72.857, lat: 19.027 });

function row(id: string): HTMLElement {
  const element = document.querySelector<HTMLElement>(`li[data-report-id='${id}']`);
  if (!element) throw new Error(`no row ${id}`);
  return element;
}

beforeEach(() => {
  window.sessionStorage.clear();
  loadOpsLog.mockReset();
  loadOpsLog.mockResolvedValue(READ_ONLY_LOG);
  loadReports.mockReset();
  loadReports.mockResolvedValue({ count: 2, reports: [HINDMATA, KINGS_CIRCLE] });
  // The globe's topologies fetch on mount; a refused fetch is a state it already prints.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("null", { status: 503 })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function renderDesk() {
  render(<AuthorityScreen wardMap={<WardMap />} />);
  // End the entry, as any key does.
  fireEvent.keyDown(window, { key: "a" });
  await waitFor(() => expect(document.querySelector("li[data-report-id]")).not.toBeNull());
}

describe("the desk's inbox and ward map", () => {
  it("draws every report in the inbox as a pin on the ward map", async () => {
    await renderDesk();
    expect(loadReports).toHaveBeenCalledWith(expect.objectContaining({ city: "mumbai" }));
    const map = within(screen.getByTestId("flood-map"));
    expect(map.getByRole("button", { name: "Pin seed-hindmata-1" })).toBeInTheDocument();
    expect(map.getByRole("button", { name: "Pin rpt-1789225538684-a1b2c3" })).toBeInTheDocument();
  });

  it("flies the map to a row that is opened, and highlights its pin", async () => {
    const user = userEvent.setup();
    await renderDesk();
    await user.click(
      within(row("seed-hindmata-1")).getByRole("button", { name: "Open on the map" }),
    );
    expect(screen.getByTestId("map-selected")).toHaveTextContent("seed-hindmata-1");
    expect(screen.getByTestId("map-focus")).toHaveTextContent("72.841,19.012,15");
    expect(row("seed-hindmata-1")).toHaveAttribute("aria-current", "true");
  });

  it("opens the row of a pin tapped on the map, without a flight", async () => {
    const user = userEvent.setup();
    await renderDesk();
    await user.click(screen.getByRole("button", { name: "Pin rpt-1789225538684-a1b2c3" }));
    const opened = row("rpt-1789225538684-a1b2c3");
    expect(opened).toHaveAttribute("aria-current", "true");
    expect(screen.getByTestId("map-selected")).toHaveTextContent("rpt-1789225538684-a1b2c3");
    expect(screen.getByTestId("map-focus")).toHaveTextContent("none");
    // The read-only API's own sentence stands where the status control would be - the one that
    // says why, not the last note, which tells the reader to send a passphrase nothing can check.
    expect(
      within(opened).getByText(
        "This API is read-only: VARUNA_OPS_PASSPHRASE is not set where it runs.",
      ),
    ).toBeInTheDocument();
    expect(within(opened).queryByText(/Send the desk passphrase/)).not.toBeInTheDocument();
  });
});

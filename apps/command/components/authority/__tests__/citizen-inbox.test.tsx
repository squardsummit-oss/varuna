/**
 * The citizen inbox on the ward desk: a citizen's complaint with its photo, and the desk's answer
 * that the reporter reads (user request 2026-09-27).
 *
 * Guarded here, because each is a claim a refactor could break while the page still rendered:
 *
 * 1. Every state says what it is: loading, empty, a list, a failed read.
 * 2. A photo the API kept is shown with its credit; one it did not keep is never called
 *    "attached" - the API's own sentence stands in for it.
 * 3. A status is set through the desk's gate, and a refusal prints the API's sentence.
 * 4. The open row is the map's selected report, both ways.
 */
import { createRequire } from "node:module";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const client = vi.hoisted(() => ({ postReportStatus: vi.fn() }));

vi.mock("@/lib/api/reports", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/reports")>();
  return { ...actual, postReportStatus: client.postReportStatus };
});

import { CitizenInbox, type InboxWriteAccess } from "@/components/authority/citizen-inbox";
import { ILLUSTRATIVE_PHOTO_LABEL, SYNTHETIC_LABEL } from "@/components/citizen/report-card";
import { ApiError } from "@/lib/api/client";
import { clearPassphrase, WRONG_PASSPHRASE_MESSAGE, writePassphrase } from "@/lib/api/ops";
import type { PublicReport, ReportList, ReportStatusResult } from "@/lib/api/reports";

/** A seeded demo report with its Commons photo, as `GET /v1/reports` returns one. */
function seed(overrides: Partial<PublicReport> = {}): PublicReport {
  return {
    id: "seed-hindmata-1",
    origin: "seed",
    synthetic: true,
    ts: "2019-07-02T08:47:00+05:30",
    received_at: null,
    lat: 19.012,
    lon: 72.841,
    coordinates: "rounded to 3 decimals",
    city: "mumbai",
    outside_aoi: false,
    place: "Hindmata junction",
    depth_hint: "knee",
    depth_cm: 45,
    text: "Water up to the knee under the flyover, buses turning back",
    source: "seed",
    photo_attached: true,
    has_photo: true,
    photo_url:
      "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/Bombay_flooded_street.jpg/960px-Bombay_flooded_street.jpg",
    thumb_url:
      "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/Bombay_flooded_street.jpg/330px-Bombay_flooded_street.jpg",
    photo_note: null,
    credit: {
      title: "File:Bombay flooded street.jpg",
      author: "Hitesh Ashar",
      license: "CC BY 2.0",
      license_url: "https://creativecommons.org/licenses/by/2.0",
      source_url: "https://commons.wikimedia.org/wiki/File:Bombay_flooded_street.jpg",
      caption: "Heavy monsoon in Mumbai, August 2005",
      note: "Illustrative photo from Wikimedia Commons; not taken at this spot or on 2 July 2019.",
    },
    status: "received",
    status_ts: null,
    history: [],
    ...overrides,
  };
}

/** A citizen's own report that reached the deployed API with a photo it does not keep. */
function citizen(overrides: Partial<PublicReport> = {}): PublicReport {
  return seed({
    id: "rpt-1789225538684-a1b2c3",
    origin: "citizen",
    synthetic: false,
    ts: "2026-09-27T10:12:00+05:30",
    received_at: "2026-09-27T10:12:04+05:30",
    lat: 19.0271,
    lon: 72.8572,
    coordinates: "exact",
    place: null,
    text: "Knee deep outside the station, autos stopped",
    source: "public-map",
    credit: null,
    photo_attached: true,
    has_photo: false,
    photo_url: null,
    thumb_url: null,
    photo_note:
      "Photos are stored only on the demo laptop; this API kept your report without its photo.",
    ...overrides,
  });
}

function listOf(...reports: PublicReport[]): ReportList {
  return {
    count: reports.length,
    reports,
    notes: [
      "The 8 seed reports are synthetic: the 2 July 2019 replay bundle's own synthetic reports at registered hotspots.",
    ],
  };
}

const READ_ONLY: InboxWriteAccess = {
  open: false,
  gate: "disabled",
  reason: "This API is read-only: VARUNA_OPS_PASSPHRASE is not set where it runs.",
  officer: null,
};

const OPEN: InboxWriteAccess = {
  open: true,
  gate: "locked",
  reason: null,
  officer: "R. Kulkarni",
};

interface Harness {
  list: ReportList | null;
  error?: string | null;
  exact?: boolean;
  selectedId?: string | null;
  access?: InboxWriteAccess;
}

function renderInbox({
  list,
  error = null,
  exact = false,
  selectedId = null,
  access = READ_ONLY,
}: Harness) {
  const select = vi.fn();
  const replace = vi.fn();
  const onWrote = vi.fn();
  const onGateRefused = vi.fn();
  const view = render(
    <CitizenInbox
      reports={{ list, error, exact, selectedId, select, replace }}
      access={access}
      onWrote={onWrote}
      onGateRefused={onGateRefused}
    />,
  );
  return { ...view, select, replace, onWrote, onGateRefused };
}

function row(id: string): HTMLElement {
  const element = document.querySelector<HTMLElement>(`[data-report-id='${id}']`);
  if (!element) throw new Error(`no row ${id}`);
  return element;
}

beforeEach(() => {
  client.postReportStatus.mockReset();
  clearPassphrase();
  window.sessionStorage.clear();
});

describe("CitizenInbox states", () => {
  it("shows a skeleton and no list before the first answer", () => {
    renderInbox({ list: null });
    expect(screen.queryByRole("list", { name: "Citizen reports" })).toBeNull();
    expect(screen.queryByText("No reports yet")).toBeNull();
    expect(document.querySelector("[aria-hidden='true']")).not.toBeNull();
  });

  it("says what brings a report in when there is none", () => {
    renderInbox({ list: listOf() });
    expect(screen.getByText("No reports yet")).toBeInTheDocument();
    expect(screen.getByText(/Report water button/)).toBeInTheDocument();
  });

  it("prints the API's sentence when the read failed, and keeps the last list", () => {
    renderInbox({
      list: listOf(seed()),
      error: "The VARUNA API did not answer at http://localhost:8000.",
    });
    expect(screen.getByText(/did not answer/)).toBeInTheDocument();
    expect(row("seed-hindmata-1")).toBeInTheDocument();
  });

  it("prints the list's own notes, not a paraphrase of them", () => {
    renderInbox({ list: listOf(seed()) });
    expect(screen.getByText(/The 8 seed reports are synthetic/)).toBeInTheDocument();
  });
});

describe("CitizenInbox photos", () => {
  it("shows a seeded photo with its credit, and labels the report synthetic and the photo illustrative", () => {
    renderInbox({ list: listOf(seed()) });
    const card = within(row("seed-hindmata-1"));
    expect(card.getByRole("img", { name: "Heavy monsoon in Mumbai, August 2005" })).toHaveAttribute(
      "src",
      expect.stringContaining("330px-Bombay_flooded_street.jpg"),
    );
    expect(card.getByText(SYNTHETIC_LABEL)).toBeInTheDocument();
    expect(card.getByText(ILLUSTRATIVE_PHOTO_LABEL)).toBeInTheDocument();
    expect(card.getByText(/Photo: Hitesh Ashar, CC BY 2.0/)).toBeInTheDocument();
  });

  it("never calls a photo the API did not keep 'attached', and prints the API's reason", () => {
    renderInbox({ list: listOf(citizen()) });
    const card = within(row("rpt-1789225538684-a1b2c3"));
    expect(card.queryByRole("img")).toBeNull();
    expect(card.queryByText(/photo attached/i)).toBeNull();
    expect(
      card.getByText(
        "Photos are stored only on the demo laptop; this API kept your report without its photo.",
      ),
    ).toBeInTheDocument();
    // Neither honesty label belongs on a real citizen's report.
    expect(card.queryByText(SYNTHETIC_LABEL)).toBeNull();
  });

  it("prints nothing about a photo when none was sent", () => {
    renderInbox({
      list: listOf(citizen({ photo_attached: false, photo_note: null })),
    });
    const card = within(row("rpt-1789225538684-a1b2c3"));
    expect(card.queryByRole("img")).toBeNull();
    expect(card.queryByText(/photo/i)).toBeNull();
  });
});

describe("CitizenInbox filter", () => {
  it("counts each status and lists only the chosen one", async () => {
    const user = userEvent.setup();
    renderInbox({
      list: listOf(
        seed(),
        citizen(),
        seed({ id: "seed-sion-1", place: "Sion Circle", status: "crew_sent" }),
      ),
    });
    const filters = within(screen.getByRole("group", { name: "Filter by status" }));
    expect(filters.getByRole("button", { name: "All 3" })).toHaveAttribute("aria-pressed", "true");
    expect(filters.getByRole("button", { name: "Received 2" })).toBeInTheDocument();
    // The public list carries no dismissed report, so it offers no filter for one.
    expect(filters.queryByRole("button", { name: /Dismissed/ })).toBeNull();

    await user.click(filters.getByRole("button", { name: "Crew sent 1" }));
    expect(document.querySelector("[data-report-id='seed-sion-1']")).not.toBeNull();
    expect(document.querySelector("[data-report-id='seed-hindmata-1']")).toBeNull();
  });

  it("offers the dismissed filter on the desk's own list, which carries them", () => {
    renderInbox({ list: listOf(seed({ status: "dismissed" })), exact: true, access: OPEN });
    expect(
      screen.getByRole("button", { name: "Dismissed by the ward desk 1" }),
    ).toBeInTheDocument();
  });

  it("names each status in the words the citizen's card prints, and says so when none match", async () => {
    const user = userEvent.setup();
    renderInbox({ list: listOf(seed({ status: "seen" })) });
    const filters = within(screen.getByRole("group", { name: "Filter by status" }));
    // One word for one thing (SPEC.md 6.8): the filter and the card beside it agree.
    expect(filters.getByRole("button", { name: "Seen by the ward desk 1" })).toBeInTheDocument();
    const card = within(document.querySelector<HTMLElement>("[data-slot='report-card']")!);
    expect(card.getByText("Seen by the ward desk")).toBeInTheDocument();

    await user.click(filters.getByRole("button", { name: "Crew sent 0" }));
    expect(screen.getByText("No report has the status Crew sent right now.")).toBeInTheDocument();
  });
});

describe("CitizenInbox status control", () => {
  it("prints the API's own reason instead of a form when the API is read-only", () => {
    renderInbox({ list: listOf(citizen()), selectedId: "rpt-1789225538684-a1b2c3" });
    const open = within(row("rpt-1789225538684-a1b2c3"));
    expect(
      open.getByText("This API is read-only: VARUNA_OPS_PASSPHRASE is not set where it runs."),
    ).toBeInTheDocument();
    expect(open.queryByRole("button", { name: "Set status" })).toBeNull();
    expect(open.queryByRole("radio")).toBeNull();
  });

  it("asks for the passphrase when the API takes writes and the desk is not open", () => {
    renderInbox({
      list: listOf(citizen()),
      selectedId: "rpt-1789225538684-a1b2c3",
      access: { open: false, gate: "locked", reason: null, officer: null },
    });
    expect(screen.getByText("Open the desk with its passphrase to set a status.")).toBeVisible();
  });

  it("sets a status through the gate and reports it in the API's words", async () => {
    const user = userEvent.setup();
    writePassphrase("monsoon-desk");
    const answered = citizen({
      status: "crew_sent",
      history: [
        {
          status: "crew_sent",
          ts: "2026-09-27T10:20:00+05:30",
          role: "ward officer",
          note: "Pump P-12 is on its way",
          user: "R. Kulkarni",
        },
      ],
    });
    const result: ReportStatusResult = {
      entry: { id: "ops-1", ts: "2026-09-27T10:20:00+05:30", status: "crew_sent" },
      city: "mumbai",
      report: answered,
      notes: [
        "The status is appended to the ops log; the report is unchanged and no forecast moved.",
        "Everyone who opens the citizen dashboard sees this status, your role and your note, never your name.",
      ],
    };
    client.postReportStatus.mockResolvedValue(result);
    const { replace, onWrote } = renderInbox({
      list: listOf(citizen()),
      selectedId: "rpt-1789225538684-a1b2c3",
      access: OPEN,
      exact: true,
    });

    const form = within(screen.getByRole("form"));
    expect(form.getByRole("button", { name: "Set status" })).toBeDisabled();
    await user.click(form.getByRole("radio", { name: "Crew sent" }));
    await user.type(
      form.getByLabelText("Note shown with the report on the public dashboard (optional)"),
      "Pump P-12 is on its way",
    );
    await user.click(form.getByRole("button", { name: "Set status" }));

    await waitFor(() => expect(client.postReportStatus).toHaveBeenCalledTimes(1));
    expect(client.postReportStatus).toHaveBeenCalledWith(
      "rpt-1789225538684-a1b2c3",
      "crew_sent",
      "Pump P-12 is on its way",
      "monsoon-desk",
      { user: "R. Kulkarni", role: "ward officer" },
    );
    expect(await screen.findByText("Status set: Crew sent.")).toBeInTheDocument();
    // The note is public, and the form says so rather than calling it a note to one reporter.
    expect(
      screen.getByText(
        /Everyone who opens the citizen dashboard reads the status, the role and the note/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Everyone who opens the citizen dashboard sees this status, your role and your note, never your name."),
    ).toBeInTheDocument();
    // The answer replaces the row at once and the screen reloads the log and the list.
    expect(replace).toHaveBeenCalledWith(answered);
    expect(onWrote).toHaveBeenCalledTimes(1);
  });

  it("prints the API's refusal when a write is refused, and keeps the desk open", async () => {
    const user = userEvent.setup();
    writePassphrase("monsoon-desk");
    client.postReportStatus.mockRejectedValue(
      new ApiError({
        code: "ops_writes_disabled",
        status: 503,
        path: "/v1/ops/reports/rpt-1789225538684-a1b2c3/status",
        message:
          "This API cannot accept authority edits: VARUNA_OPS_PASSPHRASE is not set in its environment, so there is nothing to check a request against. Set it where the API runs and restart it; the deployed API leaves it unset on purpose and is read-only.",
      }),
    );
    const { onGateRefused, onWrote } = renderInbox({
      list: listOf(citizen()),
      selectedId: "rpt-1789225538684-a1b2c3",
      access: OPEN,
    });

    await user.click(screen.getByRole("radio", { name: "Seen by the ward desk" }));
    await user.click(screen.getByRole("button", { name: "Set status" }));

    const refusal = await screen.findByText(/the deployed API leaves it unset on purpose/);
    expect(refusal.closest("[role='status']")).toHaveTextContent("Refused");
    expect(onGateRefused).not.toHaveBeenCalled();
    expect(onWrote).not.toHaveBeenCalled();
  });

  it("gives a refused passphrase the desk's one sentence and closes the desk", async () => {
    const user = userEvent.setup();
    writePassphrase("stale");
    client.postReportStatus.mockRejectedValue(
      new ApiError({
        code: "ops_passphrase_rejected",
        status: 403,
        path: "/v1/ops/reports/x/status",
        message: "The X-Varuna-Ops passphrase does not match this API's VARUNA_OPS_PASSPHRASE.",
      }),
    );
    const { onGateRefused } = renderInbox({
      list: listOf(citizen()),
      selectedId: "rpt-1789225538684-a1b2c3",
      access: OPEN,
    });

    await user.click(screen.getByRole("radio", { name: "Resolved" }));
    await user.click(screen.getByRole("button", { name: "Set status" }));

    expect(await screen.findByText(WRONG_PASSPHRASE_MESSAGE)).toBeInTheDocument();
    expect(onGateRefused).toHaveBeenCalledWith(expect.objectContaining({ kind: "rejected" }));
  });
});

describe("CitizenInbox selection", () => {
  it("opening a row selects it and asks the map to fly", async () => {
    const user = userEvent.setup();
    const { select } = renderInbox({ list: listOf(seed(), citizen()) });
    await user.click(
      within(row("seed-hindmata-1")).getByRole("button", { name: "Open on the map" }),
    );
    expect(select).toHaveBeenCalledWith("seed-hindmata-1", { fly: true });
  });

  it("opens the row the map selected, and closing it clears the selection", async () => {
    const user = userEvent.setup();
    const { select } = renderInbox({
      list: listOf(seed(), citizen()),
      selectedId: "rpt-1789225538684-a1b2c3",
    });
    const open = row("rpt-1789225538684-a1b2c3");
    expect(open).toHaveAttribute("aria-current", "true");
    expect(row("seed-hindmata-1")).not.toHaveAttribute("aria-current");
    const close = within(open).getByRole("button", { name: "Close" });
    expect(close).toHaveAttribute("aria-expanded", "true");
    await user.click(close);
    expect(select).toHaveBeenCalledWith(null, { fly: false });
  });

  it("keeps the selected report listed under a filter that would hide it", async () => {
    const user = userEvent.setup();
    renderInbox({
      list: listOf(seed(), seed({ id: "seed-sion-1", place: "Sion Circle", status: "crew_sent" })),
      selectedId: "seed-hindmata-1",
    });
    await user.click(screen.getByRole("button", { name: "Crew sent 1" }));
    expect(row("seed-hindmata-1")).toHaveAttribute("aria-current", "true");
    expect(row("seed-sion-1")).toBeInTheDocument();
  });
});

describe("CitizenInbox accessibility", () => {
  interface AxeCore {
    run(
      context: Element,
      options: { rules: Record<string, { enabled: boolean }> },
    ): Promise<{ violations: { id: string; nodes: unknown[] }[]; passes: unknown[] }>;
  }

  async function violationsIn(container: HTMLElement) {
    const here = createRequire(import.meta.url);
    const axe = createRequire(here.resolve("@axe-core/playwright"))("axe-core") as AxeCore;
    // jsdom cannot measure colour, so contrast is left to the browser-level axe pass.
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.passes.length).toBeGreaterThan(0);
    return results.violations.map((v) => `${v.id}: ${v.nodes.length} node(s)`);
  }

  it("finds nothing with a row open on the status form, or on the read-only reason", async () => {
    const reports = listOf(seed(), citizen());
    const open = renderInbox({ list: reports, selectedId: "seed-hindmata-1", access: OPEN });
    expect(await violationsIn(open.container)).toEqual([]);
    open.unmount();
    const closed = renderInbox({ list: reports, selectedId: "rpt-1789225538684-a1b2c3" });
    expect(await violationsIn(closed.container)).toEqual([]);
  }, 30_000);
});

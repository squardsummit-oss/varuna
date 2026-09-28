/**
 * The ward officer's desk: the door, the split, and the log (UI_SPEC 6, PRD 3.2, task D-15).
 *
 * These tests guard the three claims the screen is built on, because each of them is a claim a
 * refactor could quietly break while the page still rendered:
 *
 * 1. The door says what it is, and a wrong passphrase gets the same sentence every time.
 * 2. An act that changes the forecast and an act that only records are never worded alike.
 * 3. A situation note is not stored, and the screen says so instead of implying it was.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const client = vi.hoisted(() => ({
  checkPassphrase: vi.fn(),
}));

vi.mock("@/lib/api/ops", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/ops")>();
  return { ...actual, checkPassphrase: client.checkPassphrase };
});

import { ActionResult } from "@/components/authority/action-result";
import { OpsLog } from "@/components/authority/ops-log";
import { PassphraseGate, writesDisabledReason } from "@/components/authority/passphrase-gate";
import { composeNote, SituationNote } from "@/components/authority/situation-note";
import {
  GATE_NOTE,
  OPS_HEADER,
  OPS_PASSPHRASE_ENV,
  OPS_SESSION_KEY,
  clearPassphrase,
  readPassphrase,
  WRONG_PASSPHRASE_MESSAGE,
  type OpsEntry,
} from "@/lib/api/ops";

/**
 * jsdom exposes `navigator.clipboard` through a getter with no setter, so it is defined rather
 * than assigned. A real browser's is no more writable; the panel only ever calls `writeText`.
 */
function stubClipboard(writeText: ReturnType<typeof vi.fn>): void {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
}

beforeEach(() => {
  client.checkPassphrase.mockReset();
  clearPassphrase();
  try {
    window.localStorage.clear();
    window.sessionStorage.clear();
  } catch {
    // A jsdom without storage is not a case this screen has to survive in a test.
  }
});

describe("writesDisabledReason", () => {
  const READ_ONLY = "This API is read-only: VARUNA_OPS_PASSPHRASE is not set where it runs.";
  const NAMES =
    "Report statuses name the officer's role, not the officer. Send the desk passphrase in X-Varuna-Ops to see the name.";

  it("picks the note that says why, wherever it sits", () => {
    expect(writesDisabledReason(["This changed no forecast.", READ_ONLY, NAMES])).toBe(READ_ONLY);
    expect(writesDisabledReason([READ_ONLY, NAMES])).toBe(READ_ONLY);
    expect(writesDisabledReason([NAMES, READ_ONLY])).toBe(READ_ONLY);
    const older = "Writes are disabled: VARUNA_OPS_PASSPHRASE is unset where this API runs.";
    expect(writesDisabledReason([older, NAMES])).toBe(older);
  });

  it("returns nothing rather than another note when none says why", () => {
    expect(writesDisabledReason(["This changed no forecast.", NAMES])).toBeNull();
    expect(writesDisabledReason([])).toBeNull();
    expect(writesDisabledReason(undefined)).toBeNull();
  });
});

describe("PassphraseGate", () => {
  it("prints UI_SPEC 6's sentence verbatim and refuses to call itself a login", () => {
    render(<PassphraseGate status="locked" onOpen={vi.fn()} />);
    expect(screen.getByText(GATE_NOTE)).toBeInTheDocument();
    expect(screen.getByText(GATE_NOTE).textContent).toBe(
      "Prototype access. This is a shared passphrase, not a login.",
    );
  });

  it("names the variable and offers no field when the API holds no passphrase", () => {
    render(<PassphraseGate status="disabled" onOpen={vi.fn()} />);
    expect(screen.getByText(/This API is read-only/i)).toBeInTheDocument();
    expect(screen.getAllByText(new RegExp(OPS_PASSPHRASE_ENV)).length).toBeGreaterThan(0);
    // A field with nowhere to send what is typed is worse than no field.
    expect(screen.queryByLabelText("Passphrase")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open the desk" })).not.toBeInTheDocument();
  });

  it("says nothing about the desk when the API did not answer", () => {
    render(<PassphraseGate status="unreachable" onOpen={vi.fn()} />);
    expect(screen.getByText(/did not answer/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Passphrase")).not.toBeInTheDocument();
  });

  it("opens the desk with the typed name once the API accepts the passphrase", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    client.checkPassphrase.mockResolvedValue(null);
    render(<PassphraseGate status="locked" onOpen={onOpen} />);

    await user.type(screen.getByLabelText("Passphrase"), "monsoon-desk");
    await user.clear(screen.getByLabelText("Your name, for the log"));
    await user.type(screen.getByLabelText("Your name, for the log"), "R. Kulkarni");
    await user.click(screen.getByRole("button", { name: "Open the desk" }));

    await waitFor(() => expect(onOpen).toHaveBeenCalledWith("R. Kulkarni"));
    expect(readPassphrase()).toBe("monsoon-desk");
  });

  it("holds the passphrase for the tab only, never in localStorage and never in a URL", async () => {
    const user = userEvent.setup();
    client.checkPassphrase.mockResolvedValue(null);
    render(<PassphraseGate status="locked" onOpen={vi.fn()} />);

    await user.type(screen.getByLabelText("Passphrase"), "monsoon-desk");
    await user.click(screen.getByRole("button", { name: "Open the desk" }));
    await waitFor(() => expect(readPassphrase()).toBe("monsoon-desk"));

    expect(window.sessionStorage.getItem(OPS_SESSION_KEY)).toBe("monsoon-desk");
    expect(window.localStorage.length).toBe(0);
    expect(window.location.search).not.toContain("monsoon-desk");
    // The field is emptied on success, so the passphrase is not sitting in the DOM either.
    expect(screen.queryByDisplayValue("monsoon-desk")).not.toBeInTheDocument();
  });

  it("gives every wrong passphrase the same sentence, and holds nothing", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    client.checkPassphrase.mockResolvedValue({
      kind: "passphrase_rejected",
      message: WRONG_PASSPHRASE_MESSAGE,
      status: 403,
    });
    render(<PassphraseGate status="locked" onOpen={onOpen} />);

    await user.type(screen.getByLabelText("Passphrase"), "not-the-one");
    await user.click(screen.getByRole("button", { name: "Open the desk" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(WRONG_PASSPHRASE_MESSAGE);
    // The door must not repeat back what was typed, which would confirm half of it.
    expect(alert.textContent).not.toContain("not-the-one");
    expect(onOpen).not.toHaveBeenCalled();
    expect(readPassphrase()).toBeNull();
  });

  it("reports a spent rate limit as itself, with the API's own wait", async () => {
    const user = userEvent.setup();
    client.checkPassphrase.mockResolvedValue({
      kind: "rate_limited",
      message: "30 authority edits a minute is the limit and this process has used them. Wait 18 s",
      status: 429,
    });
    render(<PassphraseGate status="locked" onOpen={vi.fn()} />);

    await user.type(screen.getByLabelText("Passphrase"), "monsoon-desk");
    await user.click(screen.getByRole("button", { name: "Open the desk" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Wait 18 s");
  });

  it("names the header the passphrase travels in, so nobody looks for a cookie", () => {
    render(<PassphraseGate status="locked" onOpen={vi.fn()} />);
    expect(screen.getByText(new RegExp(OPS_HEADER))).toBeInTheDocument();
  });
});

describe("ActionResult", () => {
  it("says an engine reads a change back", () => {
    render(
      <ActionResult
        outcome="changed"
        message="Dr Ambedkar Road is closed. The next route avoids it."
        at="08:14"
      />,
    );
    expect(screen.getByText("Changed the forecast")).toBeInTheDocument();
    expect(screen.getByText(/The next route avoids it/)).toBeInTheDocument();
    expect(screen.getByText("Recorded at 08:14.")).toBeInTheDocument();
  });

  it("says the opposite for a record, in a different label", () => {
    render(<ActionResult outcome="recorded" message="This does not change the forecast." />);
    expect(screen.getByText("Recorded only")).toBeInTheDocument();
    expect(screen.queryByText("Changed the forecast")).not.toBeInTheDocument();
  });

  it("prints the API's notes rather than a paraphrase of them", () => {
    const note =
      "This changed no forecast. Authority edits are an append-only overlay applied when a " +
      "route or a feed is read; every baked product is byte-identical.";
    render(<ActionResult outcome="changed" message="Closed." notes={[note]} />);
    expect(screen.getByText(note)).toBeInTheDocument();
  });

  it("wears the refusal face and keeps the API's sentence, which names the fix", () => {
    render(
      <ActionResult
        outcome="refused"
        message="A closure needs a reason: it is shown to drivers as the street's explanation."
      />,
    );
    expect(screen.getByText("Refused")).toBeInTheDocument();
    expect(screen.getByText(/needs a reason/)).toBeInTheDocument();
  });
});

describe("OpsLog", () => {
  const ROWS: OpsEntry[] = [
    {
      id: "e2",
      kind: "pump_status",
      ts: "2019-07-02T08:20:00+05:30",
      user: "R. Kulkarni",
      detail: { pump_id: "P-12", status: "unavailable" },
    },
    {
      id: "e1",
      kind: "closure",
      ts: "2019-07-02T08:14:00+05:30",
      user: "ward officer",
      detail: { segment_id: "seg-00421", reason: "Water over the kerb at the rail bridge" },
    },
  ];

  it("keeps the order it was given, newest first, with a user and a time on every row", () => {
    render(<OpsLog entries={ROWS} total={2} />);
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Pump P-12 marked unavailable");
    expect(rows[0]).toHaveTextContent("R. Kulkarni");
    expect(rows[1]).toHaveTextContent("seg-00421");
    expect(rows[1]).toHaveTextContent("ward officer");
    rows.forEach((row) => expect(row.textContent).toMatch(/\d{2}:\d{2}/));
  });

  it("marks which half of the screen each row came from", () => {
    render(<OpsLog entries={ROWS} total={2} />);
    expect(screen.getAllByText(/read by the router/)).toHaveLength(2);
  });

  it("tells the officer what to do when nothing has been done yet", () => {
    render(<OpsLog entries={[]} />);
    expect(screen.getByText("Nothing has been done at this desk")).toBeInTheDocument();
    expect(screen.getByText(/Close a street or set a pump/)).toBeInTheDocument();
  });

  it("prints the API's sentence when the log could not be read", () => {
    render(<OpsLog entries={null} error="The VARUNA API did not answer." />);
    expect(screen.getByText("The VARUNA API did not answer.")).toBeInTheDocument();
  });
});

describe("SituationNote", () => {
  it("composes the line an officer copies with its time, place and name", () => {
    const line = composeNote(
      "G/North",
      "Crowd on the east footbridge",
      "R. Kulkarni",
      "2019-07-02T08:14:00+05:30",
    );
    expect(line).toContain("G/North");
    expect(line).toContain("R. Kulkarni");
    expect(line).toContain("Crowd on the east footbridge");
    expect(line).toMatch(/^\d{2}:\d{2}/);
  });

  it("leaves the ward out rather than printing an empty separator", () => {
    expect(
      composeNote("  ", "Water at the market", "ward officer", "2019-07-02T08:14:00+05:30"),
    ).toBe("08:14 · ward officer: Water at the market");
  });

  it("says VARUNA cannot record a note, rather than offering a control that would fail", () => {
    render(<SituationNote officer="ward officer" />);
    expect(screen.getByRole("button", { name: /Record in the log/ })).toBeDisabled();
    expect(screen.getByText(/Recording is not built/)).toBeInTheDocument();
  });

  it("says nothing was recorded after a copy, which is the honest half", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard(writeText);

    render(<SituationNote officer="ward officer" />);
    await user.type(screen.getByLabelText("What is happening"), "Two feeder pumps idle");
    await user.click(screen.getByRole("button", { name: "Copy the note" }));

    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("Two feeder pumps idle"));
    const result = await screen.findByRole("status");
    expect(result).toHaveTextContent("Recorded only");
    expect(result).toHaveTextContent(/VARUNA did not record it either/);
  });

  it("does not claim a copy the browser refused", async () => {
    const user = userEvent.setup();
    stubClipboard(vi.fn().mockRejectedValue(new Error("denied")));

    render(<SituationNote officer="ward officer" />);
    await user.type(screen.getByLabelText("What is happening"), "Crowd on the footbridge");
    await user.click(screen.getByRole("button", { name: "Copy the note" }));

    expect(await screen.findByRole("status")).toHaveTextContent(/the note was not copied/);
  });
});

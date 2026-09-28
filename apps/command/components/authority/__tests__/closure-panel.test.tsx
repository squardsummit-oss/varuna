/**
 * Closing a street: the act that changes the next answer (UI_SPEC 6, TECH_SPEC 3.6, task D-15).
 *
 * The helpers are tested on their own because two of them encode a decision the API will refuse
 * if it is wrong and the screen would have nothing to explain: `istIso` must write +05:30 with
 * the matching wall clock, or the audit trail is wrong in the one column it exists for, and
 * `untilFor` must be relative, because the API refuses an expiry that has already passed while
 * the map is replaying 2 July 2019.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const client = vi.hoisted(() => ({
  loadStreetOptions: vi.fn(),
  loadClosures: vi.fn(),
  postClosure: vi.fn(),
}));

vi.mock("@/lib/api/ops", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/ops")>();
  return {
    ...actual,
    loadStreetOptions: client.loadStreetOptions,
    loadClosures: client.loadClosures,
    postClosure: client.postClosure,
  };
});

import {
  ClosurePanel,
  istIso,
  matchStreets,
  streetLabel,
  untilFor,
} from "@/components/authority/closure-panel";
import type { Closure, StreetOption } from "@/lib/api/ops";

const AMBEDKAR: StreetOption = {
  segmentId: "seg-00421",
  name: "Dr Ambedkar Road",
  peakDepthCm: 62,
  cause: "forecast",
  from: "2019-07-02T08:10:00+05:30",
  to: "2019-07-02T09:40:00+05:30",
};

const UNNAMED: StreetOption = {
  segmentId: "seg-01902",
  name: null,
  displayName: "Service road near Hindmata junction",
  peakDepthCm: 71,
  cause: "forecast",
  from: "2019-07-02T08:10:00+05:30",
  to: "2019-07-02T09:40:00+05:30",
};

const SION: StreetOption = {
  segmentId: "seg-00733",
  name: "Sion Circle approach",
  peakDepthCm: 48,
  cause: "forecast",
  from: "2019-07-02T08:10:00+05:30",
  to: "2019-07-02T09:40:00+05:30",
};

beforeEach(() => {
  client.loadStreetOptions.mockReset().mockResolvedValue([AMBEDKAR, UNNAMED, SION]);
  client.loadClosures.mockReset().mockResolvedValue({ city: "mumbai", closures: [], nEntries: 0 });
  client.postClosure.mockReset();
});

describe("istIso", () => {
  it("writes the offset this repository writes, never UTC", () => {
    expect(istIso(new Date("2019-07-02T08:14:00+05:30"))).toBe("2019-07-02T08:14:00+05:30");
  });

  it("carries the IST wall clock of an instant given in another zone", () => {
    // 02:44 UTC is 08:14 IST; a toISOString() here would have written 02:44 under a +05:30 label.
    expect(istIso(new Date("2019-07-02T02:44:00Z"))).toBe("2019-07-02T08:14:00+05:30");
  });

  it("rolls the date when IST is already tomorrow", () => {
    expect(istIso(new Date("2019-07-02T20:00:00Z"))).toBe("2019-07-03T01:30:00+05:30");
  });
});

describe("untilFor", () => {
  it("is relative to now, because the API refuses an expiry that has passed", () => {
    const now = new Date("2026-09-19T10:00:00+05:30");
    expect(untilFor(2, now)).toBe("2026-09-19T12:00:00+05:30");
    expect(untilFor(24, now)).toBe("2026-09-20T10:00:00+05:30");
  });

  it("is null for until-reopened, which is the absence of an expiry and not a far-off one", () => {
    expect(untilFor(0, new Date("2026-09-19T10:00:00+05:30"))).toBeNull();
  });
});

describe("matchStreets", () => {
  it("matches a name or a segment id, case-insensitively", () => {
    expect(matchStreets([AMBEDKAR, SION], "ambedkar").map((s) => s.segmentId)).toEqual([
      "seg-00421",
    ]);
    expect(matchStreets([AMBEDKAR, SION], "seg-00733").map((s) => s.segmentId)).toEqual([
      "seg-00733",
    ]);
  });

  it("puts named streets first, then the deepest, so a searchable street is reachable", () => {
    // OSM names none of 52.6 % of Mumbai's segments, so an unnamed one at 71 cm would otherwise
    // sit above every street an officer could actually look up.
    expect(matchStreets([UNNAMED, AMBEDKAR, SION], "").map((s) => s.segmentId)).toEqual([
      "seg-00421",
      "seg-00733",
      "seg-01902",
    ]);
  });

  it("caps the list rather than rendering the whole city", () => {
    const many = Array.from({ length: 40 }, (_, i) => ({
      segmentId: `seg-${i}`,
      name: `Road ${i}`,
      peakDepthCm: i,
      cause: "forecast" as const,
      from: "2019-07-02T08:10:00+05:30",
      to: "2019-07-02T09:40:00+05:30",
    }));
    expect(matchStreets(many, "Road")).toHaveLength(12);
  });
});

describe("streetLabel", () => {
  it("prints the OSM name when there is one", () => {
    expect(streetLabel(AMBEDKAR)).toBe("Dr Ambedkar Road");
  });

  it("names a street OSM does not by the feed's display name, never 'Unnamed road'", () => {
    expect(streetLabel(UNNAMED)).toBe("Service road near Hindmata junction (seg-01902)");
    expect(streetLabel({ segmentId: "seg-7", name: null })).toBe("Segment seg-7");
  });

  it("does not call a segment unnamed when it was never looked up", () => {
    // A segment id typed into the form carries no name because nothing asked for one. Dr
    // Babasaheb Ambedkar Marg closed by its id read "Unnamed road S618477973-001" in the browser
    // before this: a fact the screen invented about a street OSM does name.
    expect(streetLabel({ segmentId: "S618477973-001", name: null, known: false })).toBe(
      "Segment S618477973-001",
    );
  });
});

describe("ClosurePanel", () => {
  it("refuses to close without a reason, the way the API does", async () => {
    render(<ClosurePanel officer="ward officer" city="mumbai" />);
    await screen.findByRole("button", { name: /Dr Ambedkar Road/ });

    const close = screen.getByRole("button", { name: "Close this street" });
    expect(close).toBeDisabled();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Dr Ambedkar Road/ }));
    expect(close).toBeDisabled();

    await user.type(screen.getByLabelText("Why"), "Water over the kerb at the rail bridge");
    expect(close).toBeEnabled();
  });

  it("sends the officer's own words and says which engine reads the closure back", async () => {
    const user = userEvent.setup();
    client.postClosure.mockResolvedValue({
      city: "mumbai",
      closures: [],
      nEntries: 1,
      entry: { id: "e1", kind: "closure", ts: "2019-07-02T08:14:00+05:30" },
      notes: ["This changed no forecast. Authority edits are an append-only overlay."],
    });

    render(<ClosurePanel officer="R. Kulkarni" city="mumbai" onWrote={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /Dr Ambedkar Road/ }));
    await user.type(screen.getByLabelText("Why"), "Water over the kerb at the rail bridge");
    await user.click(screen.getByRole("button", { name: "Close this street" }));

    await waitFor(() => expect(client.postClosure).toHaveBeenCalledTimes(1));
    expect(client.postClosure).toHaveBeenCalledWith(
      expect.objectContaining({
        segmentId: "seg-00421",
        reason: "Water over the kerb at the rail bridge",
        user: "R. Kulkarni",
        reopen: false,
        until: null,
      }),
    );

    const result = await screen.findByRole("status");
    expect(result).toHaveTextContent("Changed the forecast");
    expect(result).toHaveTextContent("Dr Ambedkar Road is closed. The next route avoids it");
    expect(result).toHaveTextContent(/append-only overlay/);
  });

  it("closes a street the list does not offer, by its segment id", async () => {
    const user = userEvent.setup();
    client.postClosure.mockResolvedValue({
      city: "mumbai",
      closures: [],
      nEntries: 1,
      entry: { id: "e1", kind: "closure", ts: "2019-07-02T08:14:00+05:30" },
      notes: [],
    });

    render(<ClosurePanel officer="ward officer" city="mumbai" />);
    await screen.findByRole("button", { name: /Dr Ambedkar Road/ });
    await user.type(screen.getByLabelText("Or a segment id"), "seg-09999");
    await user.type(screen.getByLabelText("Why"), "Barricaded by the fire brigade");
    await user.click(screen.getByRole("button", { name: "Close this street" }));

    await waitFor(() =>
      expect(client.postClosure).toHaveBeenCalledWith(
        expect.objectContaining({ segmentId: "seg-09999" }),
      ),
    );
    // Named "Segment seg-09999", not "Unnamed road seg-09999": nothing looked its name up.
    const result = await screen.findByRole("status");
    expect(result).toHaveTextContent("Segment seg-09999 is closed");
    expect(result.textContent).not.toContain("Unnamed road");
  });

  it("reopens by appending, and says the forecast decides the street again", async () => {
    const user = userEvent.setup();
    const closed: Closure = {
      id: "e1",
      segmentId: "seg-00421",
      reason: "Water over the kerb at the rail bridge",
      user: "ward officer",
      ts: "2019-07-02T08:14:00+05:30",
      until: null,
    };
    client.loadClosures.mockResolvedValue({ city: "mumbai", closures: [closed], nEntries: 1 });
    client.postClosure.mockResolvedValue({
      city: "mumbai",
      closures: [],
      nEntries: 2,
      entry: { id: "e2", kind: "reopen", ts: "2019-07-02T08:40:00+05:30" },
      notes: [],
    });

    render(<ClosurePanel officer="ward officer" city="mumbai" />);
    await user.click(await screen.findByRole("button", { name: /Reopen/ }));

    await waitFor(() =>
      expect(client.postClosure).toHaveBeenCalledWith(
        expect.objectContaining({ segmentId: "seg-00421", reopen: true }),
      ),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(/is open again/);
  });

  it("shows the API's refusal rather than a screen-written apology", async () => {
    const user = userEvent.setup();
    client.postClosure.mockRejectedValue(
      Object.assign(new Error("A closure needs a reason: it is shown to drivers."), {
        code: "closure_needs_a_reason",
        status: 422,
      }),
    );

    render(<ClosurePanel officer="ward officer" city="mumbai" />);
    await user.click(await screen.findByRole("button", { name: /Dr Ambedkar Road/ }));
    await user.type(screen.getByLabelText("Why"), "x");
    await user.click(screen.getByRole("button", { name: "Close this street" }));

    const result = await screen.findByRole("status");
    expect(result).toHaveTextContent("Refused");
    expect(result).toHaveTextContent("A closure needs a reason");
  });

  it("says no street is closed by the desk, not that there are no floods", async () => {
    render(<ClosurePanel officer="ward officer" city="mumbai" />);
    expect(await screen.findByText(/No street is closed by the desk/)).toBeInTheDocument();
  });
});

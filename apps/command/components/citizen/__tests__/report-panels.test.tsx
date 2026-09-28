/**
 * "Complaints near you" and "My reports" on the citizen dashboard: every state each can be in,
 * the honesty labels a seeded report and its photo carry, and the ward desk's status and note
 * reaching the person who sent the report.
 */

import { createRequire } from "node:module";

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ILLUSTRATIVE_PHOTO_LABEL, SYNTHETIC_LABEL } from "@/components/citizen/report-card";
import {
  COMPLAINTS_SHOWN,
  ComplaintsNearYou,
  MyReportsPanel,
  SelectedReportCard,
  distanceWords,
  metresApart,
  orderComplaints,
} from "@/components/citizen/report-panels";
import type { MyReportState } from "@/components/citizen/use-report-feeds";

import { citizenReport, seedReport } from "./report-fixtures";

const HINDMATA = { lon: 72.841, lat: 19.012 };
const KINGS_CIRCLE = { lon: 72.857, lat: 19.027 };

interface AxeCore {
  run(
    context: Element,
    options: { rules: Record<string, { enabled: boolean }> },
  ): Promise<{ violations: { id: string; nodes: unknown[] }[]; passes: unknown[] }>;
}

async function violationsIn(container: HTMLElement): Promise<string[]> {
  const here = createRequire(import.meta.url);
  const axe = createRequire(here.resolve("@axe-core/playwright"))("axe-core") as AxeCore;
  // jsdom cannot measure colour; contrast is checked in the browser pass.
  const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
  expect(results.passes.length).toBeGreaterThan(0);
  return results.violations.map((v) => `${v.id}: ${v.nodes.length} node(s)`);
}

describe("distance and order", () => {
  it("words a distance in hundreds of metres, then kilometres", () => {
    expect(distanceWords(30)).toBe("About 100 m away");
    expect(distanceWords(812)).toBe("About 800 m away");
    // 960 m rounds to 1,000 m, which is a kilometre and says so.
    expect(distanceWords(960)).toBe("About 1.0 km away");
    expect(distanceWords(2_250)).toBe("About 2.3 km away");
  });

  it("puts the nearest complaint first once there is a centre, and keeps newest first without", () => {
    const near = citizenReport({ id: "near", lat: 19.013, lon: 72.842 });
    const far = seedReport({ id: "far", lat: 19.119, lon: 72.844 });
    expect(orderComplaints([far, near], HINDMATA).map((r) => r.id)).toEqual(["near", "far"]);
    expect(orderComplaints([far, near], null).map((r) => r.id)).toEqual(["far", "near"]);
    expect(metresApart(HINDMATA, KINGS_CIRCLE)).toBeGreaterThan(2_000);
  });
});

describe("ComplaintsNearYou", () => {
  it("shows a seeded report as synthetic, its photo as illustrative, and credits the photo", () => {
    render(
      <ComplaintsNearYou
        reports={[seedReport()]}
        centre={null}
        selectedId={null}
        onSelect={() => undefined}
        loaded
        error={null}
      />,
    );
    expect(screen.getByRole("heading", { name: "Recent complaints in Mumbai" })).toBeTruthy();
    expect(screen.getByText(SYNTHETIC_LABEL)).toBeTruthy();
    expect(screen.getByText(ILLUSTRATIVE_PHOTO_LABEL)).toBeTruthy();
    expect(screen.getByText("Photo: Hitesh Ashar, CC BY 2.0, via Wikimedia Commons")).toBeTruthy();
  });

  it("says 'near you' and how far once it knows where the reader is", () => {
    render(
      <ComplaintsNearYou
        reports={[citizenReport()]}
        centre={HINDMATA}
        selectedId={null}
        onSelect={() => undefined}
        loaded
        error={null}
      />,
    );
    expect(screen.getByRole("heading", { name: "Complaints near you" })).toBeTruthy();
    expect(screen.getByText(/^About 2\.\d km away$/)).toBeTruthy();
  });

  it("opens a report on the map from the list, and marks the one shown", () => {
    const onSelect = vi.fn();
    const { rerender } = render(
      <ComplaintsNearYou
        reports={[seedReport(), citizenReport()]}
        centre={null}
        selectedId={null}
        onSelect={onSelect}
        loaded
        error={null}
      />,
    );
    fireEvent.click(screen.getAllByRole("button", { name: "Show on the map" })[1]!);
    expect(onSelect).toHaveBeenCalledWith("rpt-1789225538684");

    rerender(
      <ComplaintsNearYou
        reports={[seedReport(), citizenReport()]}
        centre={null}
        selectedId="rpt-1789225538684"
        onSelect={onSelect}
        loaded
        error={null}
      />,
    );
    expect(screen.getByRole("button", { name: "Shown on the map" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("shows a few and offers the rest", () => {
    const many = Array.from({ length: COMPLAINTS_SHOWN + 2 }, (_, i) =>
      citizenReport({ id: `rpt-${i}`, place: `Street ${i}` }),
    );
    render(
      <ComplaintsNearYou
        reports={many}
        centre={null}
        selectedId={null}
        onSelect={() => undefined}
        loaded
        error={null}
      />,
    );
    expect(document.querySelectorAll('[data-slot="report-card"]')).toHaveLength(COMPLAINTS_SHOWN);
    fireEvent.click(screen.getByRole("button", { name: `Show all ${many.length}` }));
    expect(document.querySelectorAll('[data-slot="report-card"]')).toHaveLength(many.length);
  });

  it("has a loading, an empty and an error state, each in words", () => {
    const props = {
      reports: [],
      centre: null,
      selectedId: null,
      onSelect: () => undefined,
    };
    const { rerender } = render(<ComplaintsNearYou {...props} loaded={false} error={null} />);
    expect(document.querySelector('[aria-busy="true"]')).not.toBeNull();

    rerender(<ComplaintsNearYou {...props} loaded error={null} />);
    expect(screen.getByText("No complaints yet")).toBeTruthy();

    rerender(<ComplaintsNearYou {...props} loaded error="The API is not reachable." />);
    expect(screen.getByText(/Complaints did not load: The API is not reachable\./)).toBeTruthy();
  });

  it("keeps the last list when a later check fails, and says so", () => {
    render(
      <ComplaintsNearYou
        reports={[citizenReport()]}
        centre={null}
        selectedId={null}
        onSelect={() => undefined}
        loaded
        error="Request timed out."
      />,
    );
    expect(screen.getByText(/The latest check failed: Request timed out\./)).toBeTruthy();
    expect(document.querySelectorAll('[data-slot="report-card"]')).toHaveLength(1);
  });

  it("prints the API's own notes on the list", () => {
    render(
      <ComplaintsNearYou
        reports={[seedReport()]}
        centre={null}
        selectedId={null}
        onSelect={() => undefined}
        loaded
        error={null}
        notes={["The 8 seed reports are synthetic."]}
      />,
    );
    expect(screen.getByText("The 8 seed reports are synthetic.")).toBeTruthy();
  });
});

const SENT = "2026-09-27T12:40:02.000Z";

function mine(state: Partial<MyReportState> & Pick<MyReportState, "kind">): MyReportState {
  return { entry: { id: "rpt-1789225538684", sentAt: SENT }, ...state } as MyReportState;
}

describe("MyReportsPanel", () => {
  it("tells a reader with no reports how to raise one, and links to the report flow", () => {
    render(<MyReportsPanel items={[]} ready />);
    expect(screen.getByText("You have not sent a report from this browser.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Report water" })).toHaveAttribute("href", "/report");
  });

  it("waits for storage before saying there are none", () => {
    render(<MyReportsPanel items={[]} ready={false} />);
    expect(screen.queryByText("You have not sent a report from this browser.")).toBeNull();
  });

  it("shows the ward desk's status and the officer's note on the reader's own report", () => {
    const report = citizenReport({
      status: "crew_sent",
      status_ts: "2026-09-27T18:25:00+05:30",
      history: [
        { status: "received", ts: "2026-09-27T18:10:00+05:30", role: "citizen" },
        {
          status: "crew_sent",
          ts: "2026-09-27T18:25:00+05:30",
          role: "ward officer",
          note: "Pump P-12 is on its way from Parel depot",
        },
      ],
    });
    render(<MyReportsPanel items={[mine({ kind: "ready", report })]} ready />);
    const item = document.querySelector('[data-slot="my-report"]') as HTMLElement;
    expect(within(item).getByText("Crew sent")).toBeTruthy();
    expect(within(item).getByText(/Pump P-12 is on its way from Parel depot/)).toBeTruthy();
    expect(within(item).getByText(/Ward officer/)).toBeTruthy();
    expect(within(item).getByText(/Sent from this browser 27 Sept? 2026 18:10 IST/)).toBeTruthy();
  });

  it("prints the API's sentence when it has no report by that id", () => {
    render(
      <MyReportsPanel
        items={[
          mine({
            kind: "missing",
            message: "No report rpt-1789225538684. It may have been sent to another VARUNA server.",
          }),
        ]}
        ready
      />,
    );
    expect(
      screen.getByText(
        "No report rpt-1789225538684. It may have been sent to another VARUNA server.",
      ),
    ).toBeTruthy();
  });

  it("keeps the last status on screen when a check fails", () => {
    render(
      <MyReportsPanel
        items={[mine({ kind: "error", message: "Request timed out.", report: citizenReport() })]}
        ready
      />,
    );
    expect(screen.getByText("Received")).toBeTruthy();
    expect(screen.getByText(/Its status could not be checked: Request timed out\./)).toBeTruthy();
  });

  it("shows a skeleton while a report's status is first checked", () => {
    render(<MyReportsPanel items={[mine({ kind: "loading" })]} ready />);
    expect(screen.getByText("Checking this report's status")).toBeTruthy();
  });
});

describe("SelectedReportCard", () => {
  it("opens the pin's card and closes it", () => {
    const onClose = vi.fn();
    render(<SelectedReportCard report={seedReport()} onClose={onClose} />);
    expect(screen.getByRole("article", { name: "Citizen report: Hindmata junction" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close this report" }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe("accessibility", () => {
  it("finds nothing on the complaints, my reports and the opened card", async () => {
    const { container } = render(
      <div>
        <main>
          <ComplaintsNearYou
            reports={[seedReport(), citizenReport()]}
            centre={HINDMATA}
            selectedId="rpt-1789225538684"
            onSelect={() => undefined}
            loaded
            error={null}
            notes={["The 8 seed reports are synthetic."]}
          />
          <MyReportsPanel
            items={[
              mine({ kind: "ready", report: citizenReport({ status: "seen" }) }),
              mine({ kind: "missing", message: "No report by that id." }),
            ]}
            ready
          />
          <SelectedReportCard report={seedReport()} onClose={() => undefined} />
        </main>
      </div>,
    );
    expect(await violationsIn(container)).toEqual([]);
  });

  it("keeps the heading order when a pin's card opens straight after the page's h1", async () => {
    // The dashboard's shape: its one h1, then the card over the map, then the rail's h2 sections.
    const { container } = render(
      <main>
        <h1>Citizen dashboard</h1>
        <SelectedReportCard report={seedReport()} onClose={() => undefined} />
        <ComplaintsNearYou
          reports={[seedReport()]}
          centre={null}
          selectedId="seed-hindmata-1"
          onSelect={() => undefined}
          loaded
          error={null}
        />
      </main>,
    );
    const opened = container.querySelector("[data-slot='selected-report']");
    expect(opened?.querySelector("h2")?.textContent).toBe("Hindmata junction");
    expect(opened?.querySelector("h3")).toBeNull();
    expect(await violationsIn(container)).toEqual([]);
  });

  it("finds nothing on the empty states", async () => {
    const { container } = render(
      <main>
        <ComplaintsNearYou
          reports={[]}
          centre={null}
          selectedId={null}
          onSelect={() => undefined}
          loaded
          error={null}
        />
        <MyReportsPanel items={[]} ready />
      </main>,
    );
    expect(await violationsIn(container)).toEqual([]);
  });
});

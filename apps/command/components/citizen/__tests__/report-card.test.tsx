/**
 * The report card the dashboard and the ward desk both open from a pin: what it says about the
 * report, what it says about the photo, and the honesty labels it may never drop (SPEC.md
 * rule 7).
 */

import { createRequire } from "node:module";

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ILLUSTRATIVE_PHOTO_LABEL,
  ReportCard,
  SYNTHETIC_LABEL,
} from "@/components/citizen/report-card";
import type { DismissedReport, PublicReport } from "@/lib/api/reports";

/** A seeded demo report with its Commons photo, shaped as `GET /v1/reports` returns one. */
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
      "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a8/Bombay_flooded_street.jpg/960px-Bombay_flooded_street.jpg",
    thumb_url:
      "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a8/Bombay_flooded_street.jpg/330px-Bombay_flooded_street.jpg",
    photo_note: null,
    credit: {
      title: "File:Bombay flooded street.jpg",
      author: "Hitesh Ashar",
      license: "CC BY 2.0",
      license_url: "https://creativecommons.org/licenses/by/2.0",
      source_url: "https://commons.wikimedia.org/wiki/File:Bombay_flooded_street.jpg",
      original_source: "https://www.flickr.com/photos/asharism/30478352/",
      taken: "2005-08-01",
      caption: "Heavy monsoon in Mumbai, August 2005",
      note: "Illustrative photo from Wikimedia Commons; not taken at this spot or on 2 July 2019.",
    },
    status: "crew_sent",
    status_ts: "2019-07-02T08:55:00+05:30",
    history: [
      { status: "received", ts: "2019-07-02T08:47:00+05:30", role: "citizen", note: null },
      {
        status: "crew_sent",
        ts: "2019-07-02T08:55:00+05:30",
        role: "ward officer",
        note: "Pump P-12 is on its way from Parel depot",
      },
    ],
    ...overrides,
  };
}

/** A citizen's own report, as the deployed API returns one that arrived with a photo. */
function citizen(overrides: Partial<PublicReport> = {}): PublicReport {
  return seed({
    id: "rpt-1789225538684-a1b2c3",
    origin: "citizen",
    synthetic: false,
    place: "King's Circle",
    source: "public-map",
    credit: null,
    photo_attached: true,
    has_photo: false,
    photo_url: null,
    thumb_url: null,
    photo_note:
      "Photos are stored only on the demo laptop; this API kept your report without its photo.",
    status: "received",
    status_ts: null,
    history: [{ status: "received", ts: "2019-07-02T08:47:00+05:30", role: "citizen", note: null }],
    ...overrides,
  });
}

describe("ReportCard for a seeded demo report", () => {
  it("labels the report synthetic and the photo illustrative, and credits its author", () => {
    render(<ReportCard report={seed()} />);

    expect(screen.getByText(SYNTHETIC_LABEL)).toBeInTheDocument();
    expect(SYNTHETIC_LABEL).toBe("Demo report (synthetic)");
    expect(screen.getByText(ILLUSTRATIVE_PHOTO_LABEL)).toBeInTheDocument();
    expect(ILLUSTRATIVE_PHOTO_LABEL).toBe("Illustrative photo, not taken here or on 2 July 2019");
    expect(screen.getByText(/^Photo: Hitesh Ashar, CC BY 2\.0/)).toHaveTextContent(
      "Wikimedia Commons",
    );
  });

  it("shows the thumbnail with the Commons caption as its alt, and links the full photo", () => {
    render(<ReportCard report={seed()} />);

    const img = screen.getByRole("img", { name: "Heavy monsoon in Mumbai, August 2005" });
    expect(img).toHaveAttribute("src", expect.stringContaining("330px-Bombay_flooded_street.jpg"));
    expect(screen.getByRole("link", { name: "View photo" })).toHaveAttribute(
      "href",
      expect.stringContaining("960px-Bombay_flooded_street.jpg"),
    );
    // The credit links to the photo's page and its licence, never anywhere else.
    expect(screen.getByRole("link", { name: "Photo source" })).toHaveAttribute(
      "href",
      "https://commons.wikimedia.org/wiki/File:Bombay_flooded_street.jpg",
    );
    expect(screen.getByRole("link", { name: "CC BY 2.0" })).toHaveAttribute(
      "href",
      "https://creativecommons.org/licenses/by/2.0",
    );
  });

  it("prints the place, the time, the depth as reported, the status and the desk's note", () => {
    render(<ReportCard report={seed()} />);

    expect(screen.getByRole("heading", { name: "Hindmata junction" })).toBeInTheDocument();
    expect(screen.getByText(/2 Jul 2019/)).toBeInTheDocument();
    // The chip is a water depth, so it is the depth ramp's chip with the number on it.
    expect(screen.getByLabelText(/^45 cm, 45-60 cm/)).toHaveTextContent("45 cm");
    expect(screen.getByText("Knee deep, about 45 cm, as reported")).toBeInTheDocument();
    expect(screen.getByText("Crew sent")).toHaveAttribute("data-status", "crew_sent");
    expect(
      screen.getByText(/Water up to the knee under the flyover, buses turning back/),
    ).toBeInTheDocument();
    const note = document.querySelector("[data-slot='report-desk-note']");
    expect(note).toHaveTextContent("Ward officer");
    expect(note).toHaveTextContent("Pump P-12 is on its way from Parel depot");
  });

  it("says the photo did not load rather than leaving a broken image", () => {
    render(<ReportCard report={seed()} />);
    fireEvent.error(screen.getByRole("img"));

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText(/The photo did not load/)).toBeInTheDocument();
    // The labels stay with the report even when its photo is gone from the card.
    expect(screen.getByText(SYNTHETIC_LABEL)).toBeInTheDocument();
  });
});

describe("ReportCard for a citizen's report", () => {
  it("repeats the API's own sentence about a photo it did not keep, and draws no image", () => {
    render(<ReportCard report={citizen()} />);

    expect(
      screen.getByText(
        "Photos are stored only on the demo laptop; this API kept your report without its photo.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View photo" })).not.toBeInTheDocument();
  });

  it("carries neither the synthetic nor the illustrative label", () => {
    render(<ReportCard report={citizen()} />);

    expect(screen.queryByText(SYNTHETIC_LABEL)).not.toBeInTheDocument();
    expect(screen.queryByText(ILLUSTRATIVE_PHOTO_LABEL)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Photo: /)).not.toBeInTheDocument();
  });

  it("shows a stored photo from the API's own route, with an alt naming the place", () => {
    render(
      <ReportCard
        report={citizen({
          has_photo: true,
          photo_note: null,
          photo_url: "/v1/reports/rpt-1/photo?size=full",
          thumb_url: "/v1/reports/rpt-1/photo?size=thumb",
        })}
      />,
    );

    const img = screen.getByRole("img", { name: "Photo sent with the report at King's Circle" });
    expect(img.getAttribute("src")).toMatch(/\/v1\/reports\/rpt-1\/photo\?size=thumb$/);
    expect(screen.getByRole("link", { name: "View photo" }).getAttribute("href")).toMatch(
      /\/v1\/reports\/rpt-1\/photo\?size=full$/,
    );
    // A citizen's own photo is not illustrative.
    expect(screen.queryByText(ILLUSTRATIVE_PHOTO_LABEL)).not.toBeInTheDocument();
  });

  it("refuses a photo URL that is neither the API's route nor https", () => {
    render(
      <ReportCard
        report={citizen({
          has_photo: true,
          photo_note: null,
          photo_url: "javascript:alert(1)",
          thumb_url: "data:image/png;base64,AAAA",
        })}
      />,
    );

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View photo" })).not.toBeInTheDocument();
  });

  it("says 'No depth given' when the reporter chose none, and draws no depth chip", () => {
    render(<ReportCard report={citizen({ depth_hint: null, depth_cm: null })} />);
    expect(screen.getByText("No depth given")).toBeInTheDocument();
    expect(screen.queryByText(/^\d+ cm$/)).not.toBeInTheDocument();
  });

  it("gives the chip the same centimetres as the words when the API sent only the chip name", () => {
    render(<ReportCard report={citizen({ depth_hint: "waist", depth_cm: null })} />);
    expect(screen.getByText("Waist deep, about 90 cm, as reported")).toBeInTheDocument();
    expect(screen.getByLabelText(/^90 cm/)).toHaveTextContent("90 cm");
  });
});

describe("ReportCard for a dismissed report", () => {
  it("prints the API's sentence and nothing the report said", () => {
    const dismissed: DismissedReport = {
      id: "seed-sion-2",
      origin: "seed",
      status: "dismissed",
      status_ts: "2019-07-02T09:10:00+05:30",
      history: [],
      note: "The ward desk dismissed this report, so it is no longer shown on any map.",
    };
    render(<ReportCard report={dismissed} />);

    expect(screen.getByText("Dismissed by the ward desk")).toBeInTheDocument();
    expect(
      screen.getByText("The ward desk dismissed this report, so it is no longer shown on any map."),
    ).toBeInTheDocument();
    expect(screen.getByText(SYNTHETIC_LABEL)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});

/**
 * axe over the three kinds of card, resolved from `@axe-core/playwright` as `route-answer.test.tsx`
 * does (it is not a dependency of `apps/command`). `color-contrast` needs real CSS and layout,
 * which jsdom does not have; the browser suite answers it. The count of passing checks is asserted
 * so a run that inspected nothing cannot read as clean.
 */
describe("ReportCard axe", () => {
  interface AxeCore {
    run(
      context: Element,
      options: { rules: Record<string, { enabled: boolean }> },
    ): Promise<{ violations: { id: string; nodes: unknown[] }[]; passes: unknown[] }>;
  }

  async function violationsIn(container: HTMLElement) {
    const here = createRequire(import.meta.url);
    const axe = createRequire(here.resolve("@axe-core/playwright"))("axe-core") as AxeCore;
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.passes.length).toBeGreaterThan(0);
    return results.violations.map((v) => `${v.id}: ${v.nodes.length} node(s)`);
  }

  it("finds nothing on a seeded card with its photo, a citizen's card, or a dismissed one", async () => {
    const { container } = render(
      <div>
        <ReportCard report={seed()} selected />
        <ReportCard report={citizen()} />
        <ReportCard
          report={{
            id: "seed-sion-2",
            origin: "seed",
            status: "dismissed",
            status_ts: null,
            history: [],
            note: "The ward desk dismissed this report, so it is no longer shown on any map.",
          }}
        />
      </div>,
    );
    expect(await violationsIn(container)).toEqual([]);
  }, 30_000);
});

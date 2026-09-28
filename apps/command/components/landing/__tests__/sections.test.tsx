import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  Footer,
  Landscape,
  REPOSITORY_URL,
  SixEngines,
  TEAM_ROLES,
} from "@/components/landing/sections";

const SYSTEMS = [
  "IFLOWS-Mumbai",
  "C-FLOWS (Chennai)",
  "IIT-B Mumbai Flood",
  "IMD nowcasts",
  "Google Flood Hub",
  "VARUNA",
];

describe("Landscape", () => {
  it("gives phones a list per system and keeps the table for wider screens", () => {
    const { container } = render(<Landscape />);
    const list = container.querySelector<HTMLElement>('[data-landscape="list"]')!;
    const table = container.querySelector<HTMLElement>('[data-landscape="table"]')!;
    // Below sm the list shows and the table is display:none; from sm up, the other way round.
    expect(list).toHaveClass("sm:hidden");
    expect(table).toHaveClass("hidden", "sm:block", "overflow-x-auto");
    for (const system of SYSTEMS) {
      expect(within(list).getByText(system)).toBeInTheDocument();
      expect(within(table).getByText(system)).toBeInTheDocument();
    }
    expect(within(list).getAllByText("Learns from events")).toHaveLength(SYSTEMS.length);
  });
});

describe("SixEngines", () => {
  it("gives every tile its own micro-diagram and Pulse the largest tile", () => {
    const { container } = render(<SixEngines />);
    const tiles = [...container.querySelectorAll<HTMLElement>("[data-engine]")];
    expect(tiles.map((t) => t.dataset.engine)).toEqual([
      "Pulse",
      "Twin",
      "Flash",
      "Sky",
      "Route",
      "Command",
    ]);
    for (const tile of tiles) {
      expect(tile.querySelector(`svg[data-diagram="${tile.dataset.engine}"]`)).not.toBeNull();
      // Reachable by keyboard, so focus reveals the second sentence as hover does.
      expect(tile).toHaveAttribute("tabindex", "0");
    }
    expect(tiles[0]).toHaveClass("lg:col-span-3", "lg:row-span-2");
    expect(tiles[1]).toHaveClass("lg:col-span-3");
    expect(tiles[5]).toHaveClass("lg:col-span-2");
  });

  it("uses tokens, not literal colours, in the diagrams", () => {
    const { container } = render(<SixEngines />);
    const painted = [...container.querySelectorAll("svg *")].flatMap((el) =>
      ["fill", "stroke"].map((attr) => el.getAttribute(attr)).filter((v) => v && v !== "none"),
    );
    expect(painted.length).toBeGreaterThan(0);
    for (const value of painted) expect(value).toMatch(/^var\(--[a-z0-9-]+\)$/);
  });
});

describe("Footer", () => {
  it("links the console, verification, the API explorer and the repository", () => {
    render(<Footer />);
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    for (const href of ["/console", "/verify", "/api", REPOSITORY_URL]) {
      expect(hrefs).toContain(href);
    }
    expect(REPOSITORY_URL).toBe("https://github.com/shahul-royalss/varuna");
  });

  it("names every operator screen by its Sanskrit name as the console's nav does", () => {
    // ADR-0085: the footer reads the same rail entries, so it can never name a screen the bar
    // does not; the gloss rides with the name so a first-time reader knows where it goes.
    render(<Footer />);
    const cases: [string, string][] = [
      ["Drishti, command console", "/console"],
      ["Nadi, drain health", "/drains"],
      ["Marga, route planner", "/route"],
      ["Sanket, alert centre", "/alerts"],
      ["Jalayantra, pump dispatch", "/pumps"],
      ["Kalpana, what-if lab", "/whatif"],
      ["Smriti, replay", "/replay"],
      ["Pramana, verification", "/verify"],
      ["Pravesh, city onboarding", "/onboard"],
    ];
    for (const [name, href] of cases) {
      const link = screen.getByRole("link", { name });
      expect(link).toHaveAttribute("href", href);
      expect(within(link).getByText(name.split(",")[0]!)).toHaveAttribute("translate", "no");
    }
  });

  it("lists the blueprint's six roles", () => {
    render(<Footer />);
    expect(TEAM_ROLES).toHaveLength(6);
    for (const { role } of TEAM_ROLES) expect(screen.getByText(role)).toBeInTheDocument();
  });
});

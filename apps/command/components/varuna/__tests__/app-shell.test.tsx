import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { AppShell } from "@/components/varuna/app-shell";
import { NAV_ITEMS, navAccessibleName } from "@/lib/nav";
import { useRunStore } from "@/lib/stores/run";
import { useUiStore } from "@/lib/stores/ui";

vi.mock("next/navigation", () => ({
  usePathname: () => "/console",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

function renderShell(extra: { rightRail?: React.ReactNode; bottomBar?: React.ReactNode } = {}) {
  return render(
    <TooltipProvider>
      <AppShell {...extra}>
        <div>Map canvas</div>
      </AppShell>
    </TooltipProvider>,
  );
}

describe("AppShell", () => {
  beforeEach(() => {
    useRunStore.getState().clear();
    useUiStore.getState().closeOverlays();
  });

  it("renders the top bar, the rail and the children", () => {
    renderShell();
    expect(screen.getByText("Map canvas")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Screens" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "VARUNA console" })).toHaveAttribute(
      "href",
      "/console",
    );
    expect(screen.getByRole("button", { name: "Switch city" })).toHaveTextContent("Mumbai");
  });

  it("carries only the wordmark, the city and settings", () => {
    // The mode banner, run stamp, verification chip and the search and shortcuts buttons were
    // removed from the bar at the team's request; Ctrl+K and ? still open their panels.
    renderShell();
    for (const gone of ["Search and commands", "Keyboard shortcuts"]) {
      expect(screen.queryByRole("button", { name: gone })).not.toBeInTheDocument();
    }
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText("No run")).not.toBeInTheDocument();
  });

  it("marks the current screen in the rail", () => {
    renderShell();
    const console = screen.getByRole("link", { name: "Drishti, command console" });
    expect(console).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Nadi, drain health" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(screen.getAllByRole("link")).toHaveLength(10);
  });

  it("names every screen in text, and a Sanskrit name with its gloss for a screen reader", () => {
    // ADR-0085: a name meant to make someone ask what it is has to be seen, so the rail shows it
    // rather than hiding it in a tooltip; the accessible name adds the English gloss.
    renderShell();
    const nav = screen.getByRole("navigation", { name: "Screens" });
    for (const item of NAV_ITEMS) {
      const link = within(nav).getByRole("link", { name: navAccessibleName(item) });
      const label = within(link).getByText(item.label);
      // Sanskrit names are proper nouns a translator must leave alone; English ones are not.
      if (item.gloss) expect(label).toHaveAttribute("translate", "no");
      else expect(label).not.toHaveAttribute("translate");
    }
    expect(within(nav).getByRole("link", { name: "Kalpana, what-if lab" })).toHaveAttribute(
      "href",
      "/whatif",
    );
  });

  it("renders the optional right rail and bottom bar only when given", () => {
    const { unmount } = renderShell();
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    unmount();

    renderShell({ rightRail: <div>Hotspots</div>, bottomBar: <div>Time bar</div> });
    expect(screen.getByRole("complementary", { name: "Right rail" })).toHaveTextContent("Hotspots");
    expect(screen.getByText("Time bar")).toBeInTheDocument();
  });

  describe("on another city", () => {
    afterEach(() => window.history.replaceState(null, "", "/"));

    it("keeps the city on every screen link and drops the rest of the query", () => {
      // A Chennai console that opens Nadi opens Chennai's drains; its run id is Chennai's cycle
      // on the console and means nothing on the next screen.
      window.history.replaceState(null, "", "/console?city=chennai&run=CHN-SOUTH-20260910T0120Z");
      renderShell();
      const nav = screen.getByRole("navigation", { name: "Screens" });
      for (const item of NAV_ITEMS) {
        expect(within(nav).getByRole("link", { name: navAccessibleName(item) })).toHaveAttribute(
          "href",
          `${item.href}?city=chennai`,
        );
      }
    });

    it("sends the wordmark to this city's console, the same one the nav's Drishti opens", () => {
      // Two links to the console that disagree would be a silent switch to Mumbai (D-09).
      window.history.replaceState(null, "", "/console?city=chennai");
      renderShell();
      expect(screen.getByRole("link", { name: "VARUNA console" })).toHaveAttribute(
        "href",
        "/console?city=chennai",
      );
      expect(screen.getByRole("link", { name: "Drishti, command console" })).toHaveAttribute(
        "href",
        "/console?city=chennai",
      );
    });

    it("adds nothing on Mumbai, the default, or on a city that is not a slug", () => {
      window.history.replaceState(null, "", "/console?city=mumbai");
      const { unmount } = renderShell();
      expect(screen.getByRole("link", { name: "Marga, route planner" })).toHaveAttribute(
        "href",
        "/route",
      );
      unmount();

      window.history.replaceState(null, "", "/console?city=..%2Fetc");
      renderShell();
      expect(screen.getByRole("link", { name: "Marga, route planner" })).toHaveAttribute(
        "href",
        "/route",
      );
    });
  });

  it("wires the settings button to the ui store", () => {
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(useUiStore.getState().settingsOpen).toBe(true);
  });
});

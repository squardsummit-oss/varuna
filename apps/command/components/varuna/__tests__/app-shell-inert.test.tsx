import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { AppShell } from "@/components/varuna/app-shell";
import { useRunStore } from "@/lib/stores/run";
import { useUiStore } from "@/lib/stores/ui";

vi.mock("next/navigation", () => ({
  usePathname: () => "/console",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

function renderShell(chromeInert?: boolean) {
  return render(
    <TooltipProvider>
      <AppShell
        chromeInert={chromeInert}
        rightRail={<button type="button">Hindmata junction</button>}
        bottomBar={<button type="button">Play</button>}
      >
        <button type="button">Leave full view</button>
      </AppShell>
    </TooltipProvider>,
  );
}

const chrome = () =>
  ["top-bar", "right-rail", "bottom-bar"].map((slot) =>
    document.querySelector(`[data-slot="app-shell"] [data-slot="${slot}"]`),
  );

describe("AppShell chromeInert", () => {
  beforeEach(() => {
    useRunStore.getState().clear();
    useUiStore.getState().closeOverlays();
  });

  it("leaves the top bar, the rail and the time bar live by default", () => {
    renderShell();
    for (const el of chrome()) {
      expect(el).not.toBeNull();
      expect(el).not.toHaveAttribute("inert");
    }
  });

  it("makes the top bar, the rail and the time bar inert, and never the canvas", () => {
    const { rerender } = renderShell(true);
    for (const el of chrome()) expect(el).toHaveAttribute("inert");
    // The bar is wrapped, not replaced: the header is still inside the inert block.
    expect(document.querySelector('[data-slot="top-bar"] > header')).not.toBeNull();
    expect(screen.getByRole("button", { name: "Leave full view" }).closest("[inert]")).toBeNull();

    // Switching it off removes the attribute rather than writing inert="false".
    rerender(
      <TooltipProvider>
        <AppShell
          chromeInert={false}
          rightRail={<button type="button">Hindmata junction</button>}
          bottomBar={<button type="button">Play</button>}
        >
          <button type="button">Leave full view</button>
        </AppShell>
      </TooltipProvider>,
    );
    for (const el of chrome()) expect(el).not.toHaveAttribute("inert");
  });
});

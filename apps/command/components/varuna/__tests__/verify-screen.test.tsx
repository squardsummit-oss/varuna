import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { VerifyScreen, unavailableLabel } from "@/app/verify/verify-screen";
import { LIMITATIONS } from "@/components/varuna/limitations-list";

vi.mock("next/navigation", () => ({
  usePathname: () => "/verify",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

/**
 * The screen fetches its scores. Before P9.7 it did not - every tile read "Not scored yet" from
 * a literal - and these tests asserted that empty grid rendered synchronously. P9.7 made the
 * scores real and the tests were never moved, so they had been failing since 2026-09-10 against
 * markup that no longer exists. `loadVerification` is mocked here so the states under test are
 * the ones the component actually has: loading, failed, and scored.
 */
const loadVerification = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/verification", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/verification")>()),
  loadVerification,
}));

/** The rain-skill panel loads its own endpoint; it has its own tests (rain-skill-panel.test). */
vi.mock("@/lib/api/verification-rain", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/verification-rain")>()),
  loadRainSkill: () => new Promise(() => {}),
}));

function renderVerify() {
  return render(
    <TooltipProvider>
      <VerifyScreen />
    </TooltipProvider>,
  );
}

describe("VerifyScreen", () => {
  it("names the event and waits, rather than showing zeros it has not computed", () => {
    loadVerification.mockReturnValue(new Promise(() => {}));
    renderVerify();
    expect(screen.getByLabelText("Event")).toHaveTextContent("MUM-2019-07-02");
    // SPEC.md 6.9 bans spinners; the loading state is a skeleton, and SPEC.md 6 forbids
    // standing in a number until one has been computed.
    expect(screen.getByText("Scores appear once the event is baked.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Verification scores" })).toBeNull();
  });

  it("shows why scoring failed instead of a blank panel", async () => {
    loadVerification.mockRejectedValue(new Error("No baked runs for MUM-2019-07-02."));
    renderVerify();
    expect(await screen.findByText("No baked runs for MUM-2019-07-02.")).toBeInTheDocument();
  });

  it("names the missing probability as street depth, since the rain's Brier score is served", () => {
    expect(unavailableLabel("brier_score")).toBe("Brier score of street depth");
    expect(unavailableLabel("reliability_diagram")).toBe("Reliability of street depth");
    expect(unavailableLabel("depth_mae_cm")).toBe("Depth error at the pins");
    expect(unavailableLabel("something_new")).toBe("something new");
  });

  it("anchors the limitations so the landing footnote can deep-link to them", () => {
    loadVerification.mockReturnValue(new Promise(() => {}));
    const { container } = renderVerify();
    const section = container.querySelector("#limitations");
    expect(section).not.toBeNull();
    expect(section).toHaveAccessibleName("Limitations we state before anyone asks");
    for (const limitation of LIMITATIONS) {
      expect(screen.getByText(limitation)).toBeInTheDocument();
    }
  });
});

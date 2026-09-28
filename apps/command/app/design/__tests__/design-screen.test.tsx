import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, stubFetch } from "@/lib/test-utils";
import { useRunStore } from "@/lib/stores/run";

import { DesignScreen } from "../design-screen";
import { DESIGN_SECTIONS } from "../_sections/section";

describe("DesignScreen", () => {
  beforeEach(() => {
    // The page mounts the live components; with no stubbed route every call answers 404,
    // which is what a console with no bundle generated sees.
    vi.stubGlobal("fetch", vi.fn(stubFetch({})));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    useRunStore.getState().clear();
  });

  it("lists every section in the index, linked to its anchor", () => {
    renderWithProviders(<DesignScreen />);
    const index = screen.getByRole("navigation", { name: "Design system sections" });
    for (const section of DESIGN_SECTIONS) {
      const link = within(index).getByRole("link", { name: section.label });
      expect(link).toHaveAttribute("href", `#${section.id}`);
    }
  });

  it("renders a heading for every section the index points at", () => {
    renderWithProviders(<DesignScreen />);
    for (const section of DESIGN_SECTIONS) {
      expect(screen.getByRole("heading", { name: section.label, level: 2 })).toBeInTheDocument();
    }
  });

  it("labels the page as internal rather than part of the demo", () => {
    renderWithProviders(<DesignScreen />);
    expect(screen.getByText(/Internal page/)).toBeInTheDocument();
  });
});

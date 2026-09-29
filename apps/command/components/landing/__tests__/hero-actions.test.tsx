/**
 * The hero's two ways in and its theme switch.
 *
 * The secondary button used to open the 2 July replay, which the console's own replay panel
 * already opens; it now takes a visitor who is not an operator to the citizen dashboard, and the
 * hero carries the theme toggle so the public site can be read in either theme.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockReducedMotion } from "@/components/landing/__tests__/browser-stubs";
import { Hero } from "@/components/landing/hero";

// The globe and the map are WebGL and not under test here.
vi.mock("@/components/landing/globe-intro", () => ({ GlobeIntro: () => null }));
vi.mock("next/dynamic", () => ({ default: () => () => null }));

beforeEach(() => {
  mockReducedMotion(true);
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new TypeError("offline"))),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("hero actions", () => {
  it("opens the console first and the citizen dashboard second, and no longer the replay", () => {
    const { container } = render(<Hero />);
    const copy = container.querySelector<HTMLElement>("[data-hero-copy]")!;
    // Base UI's Button renders the Link as an anchor with its own button role.
    const links = [...copy.querySelectorAll("a")];
    expect(links.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["Open the console", "/console"],
      ["Open the citizen dashboard", "/dashboard"],
    ]);
    expect(screen.queryByText(/Watch the 2 July 2019 replay/)).toBeNull();
    expect(container.querySelector('a[href*="autoplay"]')).toBeNull();
  });

  it("keeps the headline and its one-sentence subline", () => {
    render(<Hero />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Every street. Three hours early." }),
    ).toBeInTheDocument();
    expect(screen.getByText(/turns Doppler radar into street-by-street flood depth/)).toBeVisible();
  });

  it("carries the 44 px theme toggle", () => {
    render(<Hero />);
    const toggle = screen.getByRole("button", { name: /Switch to (light|dark) mode/ });
    expect(toggle).toHaveAttribute("data-theme-toggle");
    expect(toggle).toHaveClass("size-11");
  });
});

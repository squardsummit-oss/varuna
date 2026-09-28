/**
 * The dashboard's entry sequence (M27; task D-14).
 *
 * Every test here is about the same promise: the dashboard is never left waiting behind a globe.
 * Whether the sequence plays, is skipped, is refused by reduced motion or has already been seen
 * this session, `onDone` fires exactly once and the map is revealed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

import {
  DashboardIntro,
  INTRO_SESSION_KEY,
  SKIP_AFTER_MS,
} from "@/components/citizen/dashboard-intro";

/** jsdom has no rAF timing worth speaking of; the globe's loop is exercised elsewhere. */
function reduceMotion(reduced: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: reduced && query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}

beforeEach(() => {
  window.sessionStorage.clear();
  reduceMotion(false);
  // The topologies are fetched on mount; neither test cares what they contain.
  vi.stubGlobal("fetch", async () => new Response("null"));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function overlay(): HTMLElement | null {
  return document.querySelector('[data-slot="dashboard-intro"]');
}

describe("DashboardIntro", () => {
  it("plays on a fresh session and draws the approach", () => {
    render(<DashboardIntro onDone={() => undefined} />);
    expect(overlay()).not.toBeNull();
    expect(document.querySelector('[data-sequence="approach"]')).not.toBeNull();
  });

  it("does not play again in the same session, and hands over at once", async () => {
    window.sessionStorage.setItem(INTRO_SESSION_KEY, "1");
    const onDone = vi.fn();
    render(<DashboardIntro onDone={onDone} />);
    expect(overlay()).toBeNull();
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  });

  it("shows a Skip button from 0.6 s, and it takes focus", () => {
    vi.useFakeTimers();
    try {
      render(<DashboardIntro onDone={() => undefined} />);
      expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
      act(() => {
        vi.advanceTimersByTime(SKIP_AFTER_MS);
      });
      const skip = screen.getByRole("button", { name: "Skip" });
      expect(skip).toBe(document.activeElement);
    } finally {
      vi.useRealTimers();
    }
  });

  it("ends on the Skip button, revealing the map and remembering it played", () => {
    vi.useFakeTimers();
    const onDone = vi.fn();
    try {
      render(<DashboardIntro onDone={onDone} />);
      act(() => {
        vi.advanceTimersByTime(SKIP_AFTER_MS);
      });
      fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    } finally {
      vi.useRealTimers();
    }
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(overlay()).toBeNull();
    expect(window.sessionStorage.getItem(INTRO_SESSION_KEY)).toBe("1");
  });

  it("ends on any key, click, wheel or touch", () => {
    for (const fire of [
      () => fireEvent.keyDown(window, { key: "a" }),
      () => fireEvent.pointerDown(window),
      () => fireEvent.wheel(window),
      () => fireEvent.touchStart(window),
    ]) {
      window.sessionStorage.clear();
      const onDone = vi.fn();
      const view = render(<DashboardIntro onDone={onDone} />);
      expect(overlay()).not.toBeNull();
      fire();
      expect(onDone).toHaveBeenCalledTimes(1);
      expect(overlay()).toBeNull();
      view.unmount();
    }
  });

  it("reveals the map once, however many ways it is ended", () => {
    const onDone = vi.fn();
    render(<DashboardIntro onDone={onDone} />);
    fireEvent.keyDown(window, { key: "a" });
    fireEvent.pointerDown(window);
    fireEvent.keyDown(window, { key: "b" });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("under reduced motion paints the still frame, then cuts", async () => {
    reduceMotion(true);
    const onDone = vi.fn();
    render(<DashboardIntro onDone={onDone} />);
    // The still frame is on screen for the first paint.
    expect(document.querySelector('[data-sequence="approach"]')).not.toBeNull();
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(overlay()).toBeNull());
  });

  it("plays again when forced, because a rehearsal should not need a new tab", () => {
    window.sessionStorage.setItem(INTRO_SESSION_KEY, "1");
    render(<DashboardIntro onDone={() => undefined} force />);
    expect(overlay()).not.toBeNull();
  });
});

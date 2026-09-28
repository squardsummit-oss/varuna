/**
 * The ward officer's desk opens on the globe (motion M27, widened to `/authority` 2026-09-23).
 *
 * The promises under test are the ones an operator screen has to keep, which are stricter than the
 * dashboard's: the entry never holds the desk up, it never holds the *ops-log fetch* up, any key
 * ends it, a reload during an incident does not replay it, and when it is over the desk is all
 * there. The globe's own drawing is exercised by the landing page's tests; here it only has to be
 * the approach and be gone on demand.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OpsLog } from "@/lib/api/ops";

// The shell's top bar loads the run registry on every screen that wears it; none of that is what
// this file is about.
vi.mock("@/components/varuna/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/varuna/cycle-picker", () => ({ CyclePicker: () => null }));

const loadOpsLog = vi.fn<(options?: { city?: string }) => Promise<OpsLog>>();
vi.mock("@/lib/api/ops", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/ops")>()),
  loadOpsLog: (options?: { city?: string }) => loadOpsLog(options),
}));

import {
  AuthorityScreen,
  DESK_INTRO_SESSION_KEY,
  WARD_MAP_LABEL,
} from "@/app/authority/authority-screen";
import { SKIP_AFTER_MS } from "@/components/varuna/globe-entry";

/** The deployed API's answer: it holds no passphrase, so the desk is read-only and says so. */
const READ_ONLY_LOG: OpsLog = {
  city: "mumbai",
  nEntries: 0,
  entries: [],
  writesEnabled: false,
  passphraseEnv: "VARUNA_OPS_PASSPHRASE",
  notes: ["Writes are disabled: VARUNA_OPS_PASSPHRASE is unset where this API runs."],
};

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
  loadOpsLog.mockReset();
  loadOpsLog.mockResolvedValue(READ_ONLY_LOG);
  // The citizen inbox and the globe's topologies both fetch on mount; neither is under test, and
  // a refused fetch is a state both of them already know how to print.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("null", { status: 503 })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function overlay(): HTMLElement | null {
  return document.querySelector('[data-slot="authority-intro"]');
}

function wardMapRegion(): HTMLElement | null {
  return document.querySelector('[data-slot="ward-map"]');
}

describe("the desk's entry", () => {
  it("plays on a fresh session and draws the approach", () => {
    render(<AuthorityScreen />);
    expect(overlay()).not.toBeNull();
    expect(document.querySelector('[data-sequence="approach"]')).not.toBeNull();
  });

  it("does not play again after a reload in the same tab", () => {
    // `sessionStorage` is what survives a reload, so this is that reload: an officer refreshing
    // the desk during an incident must not be shown four seconds of planet.
    window.sessionStorage.setItem(DESK_INTRO_SESSION_KEY, "1");
    render(<AuthorityScreen />);
    expect(overlay()).toBeNull();
  });

  it("keeps its own memory, so the dashboard's entry does not silence it", () => {
    window.sessionStorage.setItem("varuna.dashboard-intro.played", "1");
    render(<AuthorityScreen />);
    expect(overlay()).not.toBeNull();
  });

  it("ends on any key press, and remembers that it played", () => {
    render(<AuthorityScreen />);
    expect(overlay()).not.toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(overlay()).toBeNull();
    expect(window.sessionStorage.getItem(DESK_INTRO_SESSION_KEY)).toBe("1");
  });

  it("offers a Skip button from 0.6 s, and it takes focus while the overlay is up", () => {
    vi.useFakeTimers();
    try {
      render(<AuthorityScreen />);
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

  it("under reduced motion paints the still frame, then cuts", async () => {
    reduceMotion(true);
    render(<AuthorityScreen />);
    expect(document.querySelector('[data-sequence="approach"]')).not.toBeNull();
    await waitFor(() => expect(overlay()).toBeNull());
  });
});

describe("the desk behind the entry", () => {
  it("asks for the ops log while the globe is still on screen", async () => {
    render(<AuthorityScreen />);
    expect(overlay()).not.toBeNull();
    // The entry is a sibling, not a wrapper: the fetch the screen does on mount is already away.
    await waitFor(() => expect(loadOpsLog).toHaveBeenCalledTimes(1));
    expect(loadOpsLog).toHaveBeenCalledWith(expect.objectContaining({ city: "mumbai" }));
    expect(overlay()).not.toBeNull();
  });

  it("mounts the ward map frame before the entry hands over", () => {
    render(<AuthorityScreen />);
    const region = wardMapRegion();
    expect(region).not.toBeNull();
    expect(region).toHaveAttribute("aria-label", WARD_MAP_LABEL);
    // Framed and present while the globe is still playing - UI_SPEC 2's handover rule.
    expect(region?.dataset.handover).toBe("playing");
    expect(overlay()).not.toBeNull();
  });

  it("records the handover when the entry finishes", () => {
    render(<AuthorityScreen />);
    fireEvent.pointerDown(window);
    expect(wardMapRegion()?.dataset.handover).toBe("done");
  });

  it("leaves the desk reachable once the entry is over", async () => {
    render(<AuthorityScreen />);
    fireEvent.keyDown(window, { key: "a" });
    expect(overlay()).toBeNull();

    expect(screen.getByRole("heading", { name: "Ward officer's desk" })).toBeInTheDocument();
    expect(wardMapRegion()).not.toBeNull();
    // The read-only API's own sentence reaches the gate rather than a generic refusal.
    await waitFor(() =>
      expect(screen.getByText(/VARUNA_OPS_PASSPHRASE is unset/)).toBeInTheDocument(),
    );
  });

  it("says the ward map is missing rather than drawing something that is not one", () => {
    render(<AuthorityScreen />);
    expect(screen.getByText("No ward map yet")).toBeInTheDocument();
    // The frame prints the AOI it is framed on, so the handover lands on a named box.
    expect(screen.getByText("72.815–72.905 °E, 18.995–19.135 °N")).toBeInTheDocument();
  });

  it("draws the map it is given instead of the placeholder", () => {
    render(<AuthorityScreen wardMap={<div data-testid="photoreal-city" />} />);
    expect(screen.getByTestId("photoreal-city")).toBeInTheDocument();
    expect(screen.queryByText("No ward map yet")).toBeNull();
  });
});

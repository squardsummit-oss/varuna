import { afterEach, describe, expect, it, vi } from "vitest";

import { googleFallbackNotice, googleMapsKey, onGoogleAuthFailure } from "./google";

const NAME = "NEXT_PUBLIC_GOOGLE_MAPS_API_KEY";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("googleMapsKey", () => {
  it("returns the key when one is configured", () => {
    vi.stubEnv(NAME, "a-browser-key");
    expect(googleMapsKey()).toBe("a-browser-key");
  });

  it("trims surrounding whitespace, which a copied .env line carries", () => {
    vi.stubEnv(NAME, "  a-browser-key\n");
    expect(googleMapsKey()).toBe("a-browser-key");
  });

  it.each(["", "   ", "undefined", "null"])(
    "reads %o as no key at all, the way basemap.ts reads its own",
    (value) => {
      vi.stubEnv(NAME, value);
      expect(googleMapsKey()).toBeNull();
    },
  );

  it("returns null when the variable is not set", () => {
    vi.stubEnv(NAME, undefined);
    expect(googleMapsKey()).toBeNull();
  });
});

describe("googleFallbackNotice", () => {
  it("names the cause and what the reader is looking at instead", () => {
    expect(googleFallbackNotice("no-key")).toBe(
      "No Google Maps key is configured; showing VARUNA's own map.",
    );
    expect(googleFallbackNotice("timeout")).toContain("did not load in time");
    expect(googleFallbackNotice("error")).toContain("VARUNA's own map");
    expect(googleFallbackNotice("refused")).toBe(
      "Google rejected this key - either the Maps JavaScript API is not enabled for it, or this " +
        "address is not on its referrer list; showing VARUNA's own map.",
    );
  });

  it("does not name one cause for a refusal, because gm_authFailure names none", () => {
    // Google calls `gm_authFailure` with no argument for both `RefererNotAllowedMapError` (the
    // 2026-09-19 finding) and `InvalidKeyMapError` (measured on this tree 2026-09-24), so a
    // sentence that picks one sends the reader to the wrong setting.
    const notice = googleFallbackNotice("refused");
    expect(notice).toMatch(/Maps JavaScript API/);
    expect(notice).toMatch(/referrer list/);
  });

  it("never says only that something went wrong (SPEC.md 6.8)", () => {
    for (const reason of ["no-key", "timeout", "error", "refused"] as const) {
      expect(googleFallbackNotice(reason)).not.toMatch(/something went wrong/i);
      expect(googleFallbackNotice(reason)).toMatch(/VARUNA's own map/);
    }
  });
});

describe("onGoogleAuthFailure", () => {
  const target = window as typeof window & { gm_authFailure?: () => void };

  afterEach(() => {
    delete target.gm_authFailure;
  });

  it("installs the global Google looks up by name when a key is refused", () => {
    const handler = vi.fn();
    onGoogleAuthFailure(handler);

    // Exactly how Google calls it: by name, off window, with no arguments.
    target.gm_authFailure?.();

    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("restores whatever was there before, rather than deleting it", () => {
    const first = vi.fn();
    const second = vi.fn();
    target.gm_authFailure = first;

    const remove = onGoogleAuthFailure(second);
    expect(target.gm_authFailure).toBe(second);
    remove();

    expect(target.gm_authFailure).toBe(first);
  });

  it("leaves a newer handler alone when an older one is torn down", () => {
    const older = vi.fn();
    const newer = vi.fn();
    const removeOlder = onGoogleAuthFailure(older);
    onGoogleAuthFailure(newer);

    removeOlder();

    // Two maps mounting and unmounting must not leave the page deaf to an auth failure.
    expect(target.gm_authFailure).toBe(newer);
  });
});

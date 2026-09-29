import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { colors } from "@varuna/tokens";

import {
  THEME_BOOTSTRAP_SCRIPT,
  THEME_STORAGE_KEY,
  __resetThemeForTests,
  getTheme,
  readStoredTheme,
  setTheme,
  themeColor,
  themeRgba,
  themeRgbaTable,
  toggleTheme,
  useTheme,
  useThemeColors,
} from "./theme";

function resetDocument() {
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.style.colorScheme = "";
  document.head.querySelector('meta[name="theme-color"]')?.remove();
}

beforeEach(() => {
  window.localStorage.clear();
  resetDocument();
  __resetThemeForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("theme store", () => {
  it("defaults to dark with nothing stored", () => {
    expect(getTheme()).toBe("dark");
    expect(readStoredTheme()).toBeNull();
  });

  it("sets the attribute, colour scheme, stored value and chrome colour together", () => {
    const meta = document.createElement("meta");
    meta.setAttribute("name", "theme-color");
    meta.setAttribute("content", colors.ink);
    document.head.append(meta);

    setTheme("light");

    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(document.documentElement.style.colorScheme).toBe("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(meta.getAttribute("content")).toBe(themeColor("ink", "light"));
    expect(getTheme()).toBe("light");
  });

  it("toggles both ways", () => {
    toggleTheme();
    expect(getTheme()).toBe("light");
    toggleTheme();
    expect(getTheme()).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("ignores a value that is not a theme", () => {
    setTheme("sepia" as never);
    expect(getTheme()).toBe("dark");
    window.localStorage.setItem(THEME_STORAGE_KEY, "sepia");
    expect(readStoredTheme()).toBeNull();
  });

  it("still switches when storage throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => setTheme("light")).not.toThrow();
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(readStoredTheme()).toBeNull();
  });

  it("reads the theme the head script already put on the document", () => {
    document.documentElement.setAttribute("data-theme", "light");
    __resetThemeForTests();
    expect(getTheme()).toBe("light");
  });

  it("prefers the stored choice when the document was reset to the server default", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    document.documentElement.setAttribute("data-theme", "dark");
    __resetThemeForTests();
    expect(getTheme()).toBe("light");
  });

  it("puts the theme back when React resets <html> to the server attributes", async () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme("light"));
    // What a client re-render of the root does to the <html> singleton after a hydration error.
    document.documentElement.setAttribute("data-theme", "dark");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    document.documentElement.removeAttribute("data-theme");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    // A real switch still goes through.
    act(() => result.current.setTheme("dark"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("follows a change made in another tab", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");
    act(() => {
      window.dispatchEvent(
        new StorageEvent("storage", { key: THEME_STORAGE_KEY, newValue: "light" }),
      );
    });
    expect(result.current.theme).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});

describe("useTheme", () => {
  it("re-renders subscribers on a switch", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");
    act(() => result.current.toggle());
    expect(result.current.theme).toBe("light");
    act(() => result.current.setTheme("dark"));
    expect(result.current.theme).toBe("dark");
  });
});

describe("colours for deck.gl and canvas", () => {
  it("resolves token colours per theme", () => {
    expect(themeColor("ink", "dark")).toBe(colors.ink);
    expect(themeColor("ink", "light")).not.toBe(colors.ink);
    expect(themeColor("depth-3", "light")).toBe(colors["depth-3"]);
    expect(themeRgba("tide", 128, "dark")).toEqual([45, 212, 191, 128]);
  });

  it("builds one frozen table per theme", () => {
    const a = themeRgbaTable("light");
    expect(themeRgbaTable("light")).toBe(a);
    expect(Object.isFrozen(a)).toBe(true);
    expect(a["depth-5"]).toEqual([185, 28, 28, 255]);
  });

  it("hands back a new table when the theme changes", () => {
    const { result } = renderHook(() => useThemeColors());
    const dark = result.current;
    expect(dark.ink).toEqual(themeRgba("ink", 255, "dark"));
    act(() => setTheme("light"));
    expect(result.current).not.toBe(dark);
    expect(result.current.ink).toEqual(themeRgba("ink", 255, "light"));
  });
});

describe("head script", () => {
  function runScript() {
    // The exact string the layout inlines in <head>.
    new Function(THEME_BOOTSTRAP_SCRIPT)();
  }

  it("applies the stored theme before anything renders", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    runScript();
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("falls back to dark for a missing or foreign value", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "constructor");
    runScript();
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("survives storage that throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(runScript).not.toThrow();
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});

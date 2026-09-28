/**
 * The entry must not hand over during hydration (motion M27; found and fixed 2026-09-23).
 *
 * This is a regression test for a defect that shipped and was visible on `/dashboard`: the entry
 * called `onDone` about 1.5 s into the page, before the globe had drawn anything, and the
 * dashboard - which used `onDone` to put the `hidden` attribute on the div wrapping the overlay -
 * hid M27 for the whole of its four seconds. The globe was in the DOM and `display: none`.
 *
 * The cause is not visible from a plain `render()`, which is why it survived a test file of eight
 * cases: jsdom's `render` is a fresh client mount, where `useSyncExternalStore` reads the real
 * store immediately. Only a *hydration* pass serves `getServerSnapshot` - "already played",
 * because the server has no session - and only then does an effect that reads `playing` draw the
 * wrong conclusion. So this file does what the browser does: render to a string, hydrate it, and
 * assert what `onDone` did.
 *
 * Measured in Chrome against the dev build at 1440 x 900 before the fix: `onDone` at about 1.5 s
 * and `hidden` on the wrapper at every sample from then on. After it, the wrapper is gone, the
 * globe paints, and the handover flag stays "playing" until the sequence ends.
 */
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Root } from "react-dom/client";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";

import { GlobeEntry } from "@/components/varuna/globe-entry";

const KEY = "varuna.globe-entry-hydration.played";
const SLOT = "hydration-entry";

let root: Root | null = null;
let container: HTMLElement | null = null;

beforeEach(() => {
  window.sessionStorage.clear();
  // The globe fetches its topologies on mount; this file is about the effect, not the picture.
  vi.stubGlobal("fetch", async () => new Response("null"));
});

afterEach(() => {
  if (root) act(() => root?.unmount());
  container?.remove();
  root = null;
  container = null;
  vi.unstubAllGlobals();
});

/** Server-render, then hydrate, the way a Next page reaches the browser. */
async function hydrate(element: React.ReactElement): Promise<HTMLElement> {
  container = document.createElement("div");
  document.body.appendChild(container);
  container.innerHTML = renderToString(element);
  await act(async () => {
    root = hydrateRoot(container as HTMLElement, element);
  });
  return container;
}

describe("GlobeEntry through hydration", () => {
  it("renders nothing on the server, because the server has no session", () => {
    expect(
      renderToString(<GlobeEntry sessionKey={KEY} slot={SLOT} onDone={() => undefined} />),
    ).toBe("");
  });

  it("does not hand over while the store still reads as the server's", async () => {
    const onDone = vi.fn();
    const host = await hydrate(<GlobeEntry sessionKey={KEY} slot={SLOT} onDone={onDone} />);

    // React re-reads the store after hydration, so the overlay is here...
    expect(host.querySelector(`[data-slot="${SLOT}"]`)).not.toBeNull();
    // ...and the screen has *not* been told to take it away. This is the whole defect.
    expect(onDone).not.toHaveBeenCalled();
  });

  it("still hands over at once when this tab has already seen it", async () => {
    window.sessionStorage.setItem(KEY, "1");
    const onDone = vi.fn();
    const host = await hydrate(<GlobeEntry sessionKey={KEY} slot={SLOT} onDone={onDone} />);

    expect(host.querySelector(`[data-slot="${SLOT}"]`)).toBeNull();
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("still hands over at once when the entry is dismissed", () => {
    // The other half of the same effect: a skip reveals the map even though `hasPlayed` was read
    // before the skip wrote it, because `dismissed` is checked first.
    const onDone = vi.fn();
    render(<GlobeEntry sessionKey={KEY} slot={SLOT} onDone={onDone} force />);
    expect(screen.queryByRole("button", { name: "Skip" })).toBeNull();
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
    });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(document.querySelector(`[data-slot="${SLOT}"]`)).toBeNull();
  });
});

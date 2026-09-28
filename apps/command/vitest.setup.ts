import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// jsdom does not implement matchMedia; motion and the reduced-motion hooks query it.
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

// jsdom has no ResizeObserver; Base UI positioners ask for one.
if (typeof window !== "undefined" && !("ResizeObserver" in window)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  Object.defineProperty(window, "ResizeObserver", {
    writable: true,
    value: ResizeObserverStub,
  });
}

// No test opens a socket to anything.
//
// `ConsoleScreen` subscribes to `WS /v1/live` on mount, and Node 24 gives jsdom a *real*
// `WebSocket` from undici. On a developer's machine the API is usually running on :8000, so that
// socket **connected** - and undici 8.10.1 then constructed its own `Event` and handed it to
// Node's `EventTarget.dispatchEvent`, which rejects it because the two `Event` classes are not
// the same class: `TypeError: The "event" argument must be an instance of Event. Received an
// instance of Event`. Six uncaught exceptions from one file, `vitest run` exiting 1 while every
// test passed, and only on a machine where the API happened to be up - which is why it had not
// been seen before (measured 2026-09-23).
//
// The undici bug is real but incidental. The defect worth fixing is that a unit test about
// parsing a query string was dialling a network service at all: the result depended on whether a
// developer had `make dev` running. This stub never connects, never fires an event and never
// throws, so the socket is inert everywhere. A test that wants to drive `lib/api/live.ts` can
// stub its own over the top.
if (typeof globalThis !== "undefined") {
  class InertWebSocket {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSING = 2;
    static readonly CLOSED = 3;
    readonly CONNECTING = 0;
    readonly OPEN = 1;
    readonly CLOSING = 2;
    readonly CLOSED = 3;
    readonly url: string;
    readonly protocol = "";
    readonly extensions = "";
    binaryType: "blob" | "arraybuffer" = "blob";
    bufferedAmount = 0;
    // Never OPEN: a caller that guards on `readyState` takes its "not connected yet" path, which
    // is the same path it takes against an API that is not running.
    readyState = 0;
    onopen: unknown = null;
    onclose: unknown = null;
    onerror: unknown = null;
    onmessage: unknown = null;
    constructor(url: string | URL) {
      this.url = String(url);
    }
    send() {}
    close() {
      this.readyState = 3;
    }
    addEventListener() {}
    removeEventListener() {}
    dispatchEvent() {
      return false;
    }
  }
  Object.defineProperty(globalThis, "WebSocket", {
    writable: true,
    configurable: true,
    value: InertWebSocket,
  });
}

afterEach(() => {
  cleanup();
});

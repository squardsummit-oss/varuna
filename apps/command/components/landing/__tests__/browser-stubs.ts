/**
 * Browser APIs the landing tests drive by hand: IntersectionObserver (jsdom has none, and motion's
 * `useInView` constructs one) and the reduced-motion media query.
 */
import { vi } from "vitest";

export class IntersectionObserverStub {
  static instances: IntersectionObserverStub[] = [];
  readonly elements = new Set<Element>();
  readonly root = null;
  readonly rootMargin = "";
  readonly thresholds: number[] = [];

  constructor(private readonly callback: IntersectionObserverCallback) {
    IntersectionObserverStub.instances.push(this);
  }

  observe(element: Element) {
    this.elements.add(element);
  }

  unobserve(element: Element) {
    this.elements.delete(element);
  }

  disconnect() {
    this.elements.clear();
  }

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }

  /** Reports every observed element as intersecting. */
  enter() {
    const entries = [...this.elements].map(
      (target) =>
        ({
          target,
          isIntersecting: true,
          intersectionRatio: 1,
        }) as unknown as IntersectionObserverEntry,
    );
    if (entries.length) this.callback(entries, this as unknown as IntersectionObserver);
  }
}

export function installIntersectionObserver() {
  IntersectionObserverStub.instances = [];
  vi.stubGlobal("IntersectionObserver", IntersectionObserverStub);
}

/** Scrolls everything observed so far into view. */
export function enterViewport() {
  for (const observer of [...IntersectionObserverStub.instances]) observer.enter();
}

/** Answers `(prefers-reduced-motion: reduce)` with `reduced`, every other query with false. */
export function mockReducedMotion(reduced: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: reduced && query.includes("prefers-reduced-motion"),
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as MediaQueryList,
  );
}

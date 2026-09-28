import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  enterViewport,
  installIntersectionObserver,
  mockReducedMotion,
} from "@/components/landing/__tests__/browser-stubs";
import {
  BlurFade,
  M2_FIRST_PAINT_OPACITY,
  M2_KEYFRAMES,
  m2Animation,
  m2Stylesheet,
} from "@/components/landing/hero";
import { Figure, LIMITATIONS_HREF, Proof } from "@/components/landing/proof";
import { Roadmap, ROADMAP } from "@/components/landing/roadmap";
import { TracingBeam } from "@/components/ui/tracing-beam";
import { DUR, EASE_UI, presetFor } from "@/lib/motion";

// Records the props a motion.div was given, so the test can compare them with the catalogue.
vi.mock("motion/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("motion/react")>();
  const { createElement, forwardRef } = await import("react");
  const MotionDiv = actual.motion.div as unknown as React.ComponentType<Record<string, unknown>>;
  const Div = forwardRef<HTMLDivElement, Record<string, unknown>>(
    function RecordingDiv(props, ref) {
      return createElement(MotionDiv, {
        ...props,
        ref,
        "data-initial": JSON.stringify(props.initial ?? null),
        "data-transition": JSON.stringify(props.transition ?? null),
      });
    },
  );
  return {
    ...actual,
    motion: new Proxy(actual.motion, {
      get: (target, key) => (key === "div" ? Div : Reflect.get(target, key)),
    }),
  };
});

// NumberFlow draws into a shadow root; the value it is handed is what these tests are about.
vi.mock("@number-flow/react", () => ({
  default: ({ value }: { value: number }) => <span data-number-flow="">{String(value)}</span>,
}));

// The hero's globe and map are not under test here.
vi.mock("@/components/landing/globe-intro", () => ({ GlobeIntro: () => null }));

beforeEach(() => {
  installIntersectionObserver();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("M2 hero copy blur-fade", () => {
  it("runs the catalogue's M2 preset as a CSS animation with a 60 ms stagger", () => {
    render(
      <BlurFade index={3}>
        <p>Every street. Three hours early.</p>
      </BlurFade>,
    );
    const wrapper = screen.getByText("Every street. Three hours early.").parentElement!;
    const m2 = presetFor("M2", false);
    const seconds = (m2.transition as { duration: number }).duration;
    expect(wrapper).toHaveAttribute("data-motion", "M2");
    // Duration and easing are the preset's; the delay is three staggers.
    expect(m2Animation(3)).toBe(
      `${M2_KEYFRAMES} ${Math.round(seconds * 1000)}ms cubic-bezier(${EASE_UI.join(",")}) ${
        3 * DUR.staggerCopy * 1000
      }ms both`,
    );
    expect(wrapper.getAttribute("style")).toContain(M2_KEYFRAMES);
  });

  it("builds its keyframes from the preset's start and end", () => {
    const css = m2Stylesheet();
    const { initial, animate } = presetFor("M2", false) as {
      initial: { filter: string; y: number };
      animate: { filter: string; y: number };
    };
    expect(css).toContain(`filter:${initial.filter}`);
    expect(css).toContain(`transform:translateY(${initial.y}px)`);
    expect(css).toContain(`filter:${animate.filter}`);
    // Zero opacity would leave the page with no Largest Contentful Paint; see the constant.
    expect(css).toContain(`opacity:${M2_FIRST_PAINT_OPACITY}`);
    expect(M2_FIRST_PAINT_OPACITY).toBeLessThanOrEqual(0.01);
  });

  it("is removed outright under reduced motion, so no line waits out its delay invisible", () => {
    expect(m2Stylesheet()).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\{\[data-motion="M2"\]\{animation:none!important\}\}/,
    );
  });
});

describe("M5 roadmap tracing beam", () => {
  it("traces on scroll with full motion", () => {
    mockReducedMotion(false);
    const { container } = render(
      <TracingBeam>
        <p>V1</p>
      </TracingBeam>,
    );
    expect(container.querySelector('[data-beam="trace"]')).not.toBeNull();
    expect(container.querySelector('[data-beam="static"]')).toBeNull();
  });

  it("is a full static line under reduced motion", () => {
    mockReducedMotion(true);
    const { container } = render(<Roadmap />);
    const line = container.querySelector('[data-beam="static"]');
    expect(line).not.toBeNull();
    expect(line).toHaveClass("inset-y-0");
    expect(container.querySelector('[data-beam="trace"]')).toBeNull();
    const titles = [...container.querySelectorAll("li")].map(
      (li) => li.querySelectorAll("p")[1]?.textContent,
    );
    expect(titles).toEqual(ROADMAP.map((stage) => stage.title));
  });
});

describe("M4 proof numbers", () => {
  it("holds at zero until scrolled into view, then rolls to the fetched value", () => {
    mockReducedMotion(false);
    const { container } = render(<Figure value={0.4137} decimals={2} label="POD" note="n" />);
    const flow = () => container.querySelector("[data-number-flow]")?.textContent;
    expect(flow()).toBe("0");
    act(() => enterViewport());
    expect(flow()).toBe("0.41");
  });

  it("shows the value at once under reduced motion", () => {
    mockReducedMotion(true);
    const { container } = render(<Figure value={31} suffix="min" label="Lead" note="n" />);
    expect(container.querySelector("[data-number-flow]")).toBeNull();
    expect(container.querySelector('[data-figure="Lead"]')).toHaveTextContent("31min");
  });

  it("keeps a dash, not a zero, when there is no score", () => {
    mockReducedMotion(false);
    const { container } = render(<Figure value={null} label="Lead" note="n" />);
    act(() => enterViewport());
    expect(container.querySelector('[data-figure="Lead"]')).toHaveTextContent("—");
  });

  it("links its footnote to the limitations on /verify", async () => {
    mockReducedMotion(true);
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("offline"))),
    );
    render(<Proof />);
    const link = screen.getByRole("link", { name: /See how we score ourselves/ });
    expect(link).toHaveAttribute("href", LIMITATIONS_HREF);
    expect(LIMITATIONS_HREF).toBe("/verify#limitations");
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  });
});

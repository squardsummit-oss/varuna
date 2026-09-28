import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const motionMock = vi.hoisted(() => ({ reduced: false, animate: vi.fn() }));
vi.mock("motion/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("motion/react")>();
  const { useRef } = await import("react");
  return {
    ...actual,
    useReducedMotion: () => motionMock.reduced,
    // The shake is a framer animation on the frame; the spy stands in for it so the test can
    // count shakes instead of sampling a transform jsdom never advances.
    useAnimate: () => [useRef(null), motionMock.animate],
  };
});

import { PHONE_SHAKE_X, PhoneMock, type PhoneMessage } from "@/components/varuna/phone-mock";
import { DUR } from "@/lib/motion";

const MESSAGES: PhoneMessage[] = [
  {
    id: "alert-hindmata-severe",
    text: "Hindmata junction: depth likely above 45 cm from 08:40 to 09:40",
    time: "2019-07-02T08:40:00+05:30",
  },
  {
    id: "alert-worlikar-severe",
    text: "V B Worlikar Marg: depth likely above 45 cm from 08:40 to 09:10",
    time: "2019-07-02T08:40:00+05:30",
  },
];

function bubble(text: string): HTMLElement {
  return screen.getByText(text).closest("li") as HTMLElement;
}

beforeEach(() => {
  motionMock.reduced = false;
  motionMock.animate.mockReset();
});

describe("PhoneMock motion M16", () => {
  it("does not shake on the first render", () => {
    render(<PhoneMock messages={MESSAGES} simTime="2019-07-02T08:40:00+05:30" popKey={3} />);
    expect(motionMock.animate).not.toHaveBeenCalled();
  });

  it("shakes the frame once for 300 ms when a batch arrives, and not again on a re-render", () => {
    const { rerender } = render(<PhoneMock messages={MESSAGES} popKey={0} />);
    rerender(<PhoneMock messages={MESSAGES} popKey={1} />);
    expect(motionMock.animate).toHaveBeenCalledTimes(1);
    const [, keyframes, options] = motionMock.animate.mock.calls[0]!;
    expect(keyframes).toEqual({ x: [...PHONE_SHAKE_X] });
    expect(options).toMatchObject({ duration: DUR.phoneShake });
    expect(DUR.phoneShake).toBe(0.3);

    rerender(<PhoneMock messages={MESSAGES} popKey={1} />);
    expect(motionMock.animate).toHaveBeenCalledTimes(1);

    rerender(<PhoneMock messages={MESSAGES} popKey={2} />);
    expect(motionMock.animate).toHaveBeenCalledTimes(2);
  });

  it("never shakes under reduced motion", () => {
    motionMock.reduced = true;
    const { rerender } = render(<PhoneMock messages={MESSAGES} popKey={0} />);
    rerender(<PhoneMock messages={MESSAGES} popKey={1} />);
    expect(motionMock.animate).not.toHaveBeenCalled();
  });

  it("pops a fresh message in at 96 % and leaves the others at rest", () => {
    render(<PhoneMock messages={MESSAGES} freshIds={new Set([MESSAGES[0]!.id])} popKey={1} />);
    const fresh = bubble(MESSAGES[0]!.text);
    expect(fresh).toHaveAttribute("data-fresh", "true");
    expect(fresh.style.opacity).toBe("0");
    expect(fresh.style.transform).toContain("scale(0.96)");

    const old = bubble(MESSAGES[1]!.text);
    expect(old).not.toHaveAttribute("data-fresh");
    expect(old.style.opacity).not.toBe("0");
  });

  it("only fades a fresh message in under reduced motion", () => {
    motionMock.reduced = true;
    render(<PhoneMock messages={MESSAGES} freshIds={new Set([MESSAGES[0]!.id])} popKey={1} />);
    const fresh = bubble(MESSAGES[0]!.text);
    expect(fresh.style.opacity).toBe("0");
    expect(fresh.style.transform).not.toContain("scale");
  });

  it("shows the empty message before any alert is raised", () => {
    render(<PhoneMock messages={[]} />);
    expect(
      screen.getByText("No alerts yet. The ward officer's phone shows the next alert here."),
    ).toBeInTheDocument();
  });
});

describe("PhoneMock brand", () => {
  it("draws the team's emblem in the status bar and the chat avatar, and says the name once", () => {
    const { container } = render(<PhoneMock messages={MESSAGES} />);
    const status = container.querySelector('[data-slot="phone-status-brand"]') as HTMLElement;
    const statusMark = status.querySelector('img[data-slot="brand-mark"]');
    expect(statusMark).toHaveAttribute("src", expect.stringMatching(/varuna-mark-64\.png$/));
    expect(statusMark).toHaveAttribute("width", "16");
    // Decorative: the name beside it is text, so a screen reader does not hear it twice.
    expect(statusMark).toHaveAttribute("alt", "");
    expect(status).toHaveTextContent("VARUNA");

    const avatar = container.querySelector('[data-slot="phone-chat-avatar"]') as HTMLElement;
    expect(avatar).toHaveAttribute("aria-hidden", "true");
    expect(avatar.querySelector('img[data-slot="brand-mark"]')).not.toBeNull();
    // No letter standing in for a logo (SPEC.md 6.9).
    expect(avatar.textContent).toBe("");
    expect(screen.queryAllByRole("img", { name: "VARUNA" })).toHaveLength(0);
  });
});

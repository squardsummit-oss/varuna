import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useUiStore } from "@/lib/stores/ui";

const motionPref = vi.hoisted(() => ({ reduced: false }));
vi.mock("motion/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("motion/react")>();
  return { ...actual, useReducedMotion: () => motionPref.reduced };
});

import {
  CHIME_GAIN,
  CHIME_NOTES_HZ,
  createChime,
  useAlertChime,
  type ChimeAudioContext,
} from "./sound";

/** A fake AudioContext that records every oscillator it was asked to start. */
function fakeContext(state: AudioContextState = "running") {
  const started: number[] = [];
  const peaks: number[] = [];
  const context = {
    state,
    currentTime: 0,
    destination: {} as AudioDestinationNode,
    resume: vi.fn(async () => {
      context.state = "running";
    }),
    createOscillator: vi.fn(() => {
      let hz = 0;
      return {
        type: "sine",
        frequency: { setValueAtTime: (value: number) => (hz = value) },
        connect: vi.fn(),
        start: () => started.push(hz),
        stop: vi.fn(),
      } as unknown as OscillatorNode;
    }),
    createGain: vi.fn(
      () =>
        ({
          gain: {
            setValueAtTime: vi.fn(),
            linearRampToValueAtTime: (value: number) => peaks.push(value),
            exponentialRampToValueAtTime: vi.fn(),
          },
          connect: vi.fn(),
        }) as unknown as GainNode,
    ),
  };
  return { context: context as ChimeAudioContext & { state: AudioContextState }, started, peaks };
}

function pointerdown() {
  act(() => {
    window.dispatchEvent(new Event("pointerdown"));
  });
}

beforeEach(() => {
  motionPref.reduced = false;
  useUiStore.setState({ soundOn: false });
});
afterEach(() => {
  useUiStore.setState({ soundOn: false });
});

describe("createChime", () => {
  it("is silent and creates no context before a gesture unlocks it", () => {
    const factory = vi.fn(() => fakeContext().context);
    const chime = createChime(factory);
    expect(chime.play()).toBe(false);
    expect(factory).not.toHaveBeenCalled();
  });

  it("plays two soft sine notes once unlocked", () => {
    const fake = fakeContext();
    const chime = createChime(() => fake.context);
    chime.unlock();
    expect(chime.play()).toBe(true);
    expect(fake.started).toEqual([...CHIME_NOTES_HZ]);
    expect(fake.peaks).toEqual([CHIME_GAIN, CHIME_GAIN]);
  });

  it("creates one context and resumes it on a later gesture if the browser suspended it", () => {
    const fake = fakeContext("suspended");
    const factory = vi.fn(() => fake.context);
    const chime = createChime(factory);
    chime.unlock();
    expect(chime.play()).toBe(false);
    chime.unlock();
    expect(factory).toHaveBeenCalledTimes(1);
    expect(fake.context.resume).toHaveBeenCalledTimes(1);
  });
});

describe("useAlertChime", () => {
  function setup() {
    const fake = fakeContext();
    const factory = vi.fn(() => fake.context);
    const chime = createChime(factory);
    const hook = renderHook(({ batch }) => useAlertChime(batch, chime), {
      initialProps: { batch: 0 },
    });
    return { fake, factory, hook };
  }

  it("never listens for a gesture or plays while sound is off", () => {
    const { fake, factory, hook } = setup();
    pointerdown();
    hook.rerender({ batch: 1 });
    expect(factory).not.toHaveBeenCalled();
    expect(fake.started).toHaveLength(0);
  });

  it("never unlocks or plays under reduced motion, even with sound on", () => {
    motionPref.reduced = true;
    act(() => useUiStore.setState({ soundOn: true }));
    const { fake, factory, hook } = setup();
    pointerdown();
    hook.rerender({ batch: 1 });
    expect(factory).not.toHaveBeenCalled();
    expect(fake.started).toHaveLength(0);
  });

  it("unlocks on the first pointerdown after sound is switched on", () => {
    const { factory } = setup();
    pointerdown();
    expect(factory).not.toHaveBeenCalled();

    act(() => useUiStore.setState({ soundOn: true }));
    pointerdown();
    expect(factory).toHaveBeenCalledTimes(1);
  });

  it("unlocks on a keydown too, so a keyboard Play is enough", () => {
    act(() => useUiStore.setState({ soundOn: true }));
    const { factory } = setup();
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: " " }));
    });
    expect(factory).toHaveBeenCalledTimes(1);
  });

  it("chimes once per new batch, not on mount and not on a re-render", () => {
    act(() => useUiStore.setState({ soundOn: true }));
    const { fake, hook } = setup();
    pointerdown();
    expect(fake.started).toHaveLength(0);

    hook.rerender({ batch: 1 });
    expect(fake.started).toHaveLength(CHIME_NOTES_HZ.length);

    hook.rerender({ batch: 1 });
    expect(fake.started).toHaveLength(CHIME_NOTES_HZ.length);

    hook.rerender({ batch: 2 });
    expect(fake.started).toHaveLength(CHIME_NOTES_HZ.length * 2);
  });

  it("does not replay a batch that arrived while sound was off once sound comes on", () => {
    const { fake, hook } = setup();
    hook.rerender({ batch: 1 });
    act(() => useUiStore.setState({ soundOn: true }));
    pointerdown();
    hook.rerender({ batch: 1 });
    expect(fake.started).toHaveLength(0);
  });
});

"use client";

/**
 * The phone mock's soft chime (motion M16, SPEC.md sections 7.5, 8 and 15).
 *
 * Synthesised with the Web Audio API: two short sine notes, so there is no audio file to fetch
 * and nothing to ship in the offline package. The rules are the catalogue's:
 *
 * - muted by default; it plays only while `useUiStore.soundOn` is true (the settings drawer's
 *   switch, the one persisted UI value);
 * - never under reduced motion, where M16 is "fade only, no sound";
 * - never before a user gesture. Browsers refuse to start audio a page starts on its own, and a
 *   context created outside a gesture comes up suspended and logs a warning, which section 14's
 *   zero-console-warnings gate would catch. So the context is created (or resumed) on the first
 *   pointerdown or keydown after sound is switched on, and a chime asked for before that is
 *   silently skipped. On the demo path the first gesture is the click that changes the cycle or
 *   presses Play, and the pointerdown of that click arrives before the alerts it causes.
 */

import { useEffect, useRef } from "react";

import { useMotionPref } from "@/lib/motion";
import { useUiStore } from "@/lib/stores/ui";

/** The part of `AudioContext` the chime uses; tests pass a fake. */
export type ChimeAudioContext = Pick<
  AudioContext,
  "state" | "currentTime" | "destination" | "resume" | "createOscillator" | "createGain"
>;

export type ChimeContextFactory = () => ChimeAudioContext | null;

/** The two notes: frequency in Hz, each `NOTE_SECONDS` long, the second starting as the first ends. */
export const CHIME_NOTES_HZ = [660, 880] as const;
export const NOTE_SECONDS = 0.09;
/** Peak gain: soft enough to sit under a presenter's voice. */
export const CHIME_GAIN = 0.06;
const ATTACK_SECONDS = 0.01;
const FLOOR_GAIN = 0.0001;

export interface Chime {
  /** Call from inside a user gesture: creates the context, or resumes a suspended one. */
  unlock(): void;
  /** True once a context exists and is running, so a chime would be heard. */
  isUnlocked(): boolean;
  /** Plays the two notes when unlocked; returns whether it played. */
  play(): boolean;
}

function defaultFactory(): ChimeAudioContext | null {
  if (typeof window === "undefined") return null;
  const Ctor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  return Ctor ? new Ctor() : null;
}

export function createChime(factory: ChimeContextFactory = defaultFactory): Chime {
  let context: ChimeAudioContext | null = null;

  return {
    unlock() {
      if (context === null) {
        context = factory();
        return;
      }
      if (context.state === "suspended") void context.resume().catch(() => {});
    },
    isUnlocked() {
      return context !== null && context.state === "running";
    },
    play() {
      if (context === null || context.state !== "running") return false;
      const start = context.currentTime;
      CHIME_NOTES_HZ.forEach((hz, index) => {
        const at = start + index * NOTE_SECONDS;
        const oscillator = context!.createOscillator();
        const gain = context!.createGain();
        oscillator.type = "sine";
        oscillator.frequency.setValueAtTime(hz, at);
        // A short attack and an exponential release, so neither note clicks.
        gain.gain.setValueAtTime(FLOOR_GAIN, at);
        gain.gain.linearRampToValueAtTime(CHIME_GAIN, at + ATTACK_SECONDS);
        gain.gain.exponentialRampToValueAtTime(FLOOR_GAIN, at + NOTE_SECONDS);
        oscillator.connect(gain);
        gain.connect(context!.destination);
        oscillator.start(at);
        oscillator.stop(at + NOTE_SECONDS + ATTACK_SECONDS);
      });
      return true;
    },
  };
}

/** One chime for the app, so a context unlocked on one visit to a page survives navigation. */
export const appChime: Chime = createChime();

/**
 * Plays the chime once each time `batch` moves on: the alert centre bumps it when a cycle brings
 * alerts the queue did not have. The first value is not a batch (the first queue is not news), a
 * re-render with the same value plays nothing, and sound off or reduced motion plays nothing and
 * listens for no gesture.
 */
export function useAlertChime(batch: number, chime: Chime = appChime): void {
  const soundOn = useUiStore((s) => s.soundOn);
  const { reduced } = useMotionPref();
  const enabled = soundOn && !reduced;

  useEffect(() => {
    if (!enabled || typeof window === "undefined") return;
    const unlock = () => chime.unlock();
    // Capture phase, so the context exists before the click's own handlers change the cycle.
    window.addEventListener("pointerdown", unlock, { capture: true });
    window.addEventListener("keydown", unlock, { capture: true });
    return () => {
      window.removeEventListener("pointerdown", unlock, { capture: true });
      window.removeEventListener("keydown", unlock, { capture: true });
    };
  }, [enabled, chime]);

  const played = useRef(batch);
  useEffect(() => {
    if (batch === played.current) return;
    played.current = batch;
    if (enabled) chime.play();
  }, [batch, enabled, chime]);
}

"use client";

/**
 * The way in to a screen that opens on a map (motion M27, UI_SPEC 2).
 *
 * Two screens use it: the citizen dashboard (task D-14) and, from 2026-09-23, the ward officer's
 * desk. It was written for the first and extracted for the second without changing a line of its
 * behaviour, so `DashboardIntro` is now a four-line wrapper and every test that was written
 * against it still passes unchanged.
 *
 * **What it is for.** A reader arriving at `/dashboard` has no idea what they are looking at. Four
 * seconds of vector Earth answer the only question that matters before any number does: this is
 * the whole planet, and then it is your city, at the scale of your street. The sequence is the
 * scale claim, not an animation over it.
 *
 * **Once per session, per screen.** `sessionStorage` remembers that it has played, because a judge
 * who opens the dashboard, goes to the console and comes back should not watch it again. It is
 * cleared when the tab closes, so the next demo run opens on it. The key is a prop rather than a
 * constant so the two screens are remembered *independently*: seeing the dashboard's entry is not
 * a reason to deny the desk its own, and a ward officer who reloads `/authority` mid-incident is
 * in the same tab and does not watch it a second time.
 *
 * **Always skippable, and the skip is real.** Any key, click, wheel or touch ends it, and from
 * 0.6 s a "Skip" button is on screen, taking focus - the globe itself is hidden from assistive
 * technology, because the sequence has nothing in it for a screen reader and the control does.
 * That matters more on the desk than on the dashboard: the desk is used during a flood, and the
 * first keystroke of someone who came here to close a street has to end the animation, not be
 * swallowed by it.
 *
 * **The handover rule (UI_SPEC 2).** `onDone` is what reveals the map. The screen mounts that map
 * underneath and frames it on the AOI while the globe is still playing, so the cross-fade ends on
 * a map that was already there - no frame ever shows an unframed world.
 *
 * **Reduced motion.** No turn and no cross-fade: the finished Mumbai frame paints once, then the
 * screen cuts to the map. The still frame is the same geometry the sequence ends on, so the cut
 * is between two pictures of the same place.
 *
 * **What this component does not do.** It does not block anything the screen does on mount. The
 * overlay is a sibling of the page, not a wrapper around it, so the desk's ops-log fetch and the
 * dashboard's run load are already in flight while the globe turns; by the time the fade ends the
 * screen underneath has usually finished loading rather than starting to.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { GlobeIntro } from "@/components/landing/globe-intro";
import { usePrefersReducedMotion } from "@/lib/hooks/use-media-query";
import { DUR_MS } from "@/lib/motion";

/** How long the sequence runs before the "Skip" button appears (UI_SPEC 2). */
export const SKIP_AFTER_MS = 600;

/**
 * The Mumbai AOI the sequence's last act ends on (SPEC.md 3.3), as west/south/east/north.
 *
 * It is here rather than imported because `globe-intro.tsx` keeps its own copy private to the
 * drawing code. A screen that mounts this entry must frame its map on exactly this box, or the
 * cross-fade lands somewhere the globe was not; the two literals are the same four numbers and
 * changing one without the other is the defect to watch for.
 */
export const ENTRY_AOI: readonly [number, number, number, number] = [
  72.815, 18.995, 72.905, 19.135,
];

export interface GlobeEntryProps {
  /**
   * `sessionStorage` key under which this screen remembers that its entry has played. One key per
   * screen, so the dashboard and the desk are independent.
   */
  sessionKey: string;
  /**
   * `data-slot` for the overlay; the skip button carries `${slot}-skip`. Tests and the design page
   * find the sequence by it, and the two screens must not answer to the same name.
   */
  slot: string;
  /** Called when the entry sequence has finished, or immediately when there is none. */
  onDone: () => void;
  /**
   * Play even if this tab has seen it. Neither screen passes it; tests and a rehearsal reset use
   * it, because a demo that has to be reopened in a fresh tab is a demo waiting to go wrong.
   */
  force?: boolean;
}

/** Whether this tab has already played the entry. A blocked or absent store means "no". */
function hasPlayed(sessionKey: string): boolean {
  try {
    return window.sessionStorage.getItem(sessionKey) === "1";
  } catch {
    return false;
  }
}

function markPlayed(sessionKey: string): void {
  try {
    window.sessionStorage.setItem(sessionKey, "1");
  } catch {
    // A private window with storage blocked plays it once per load instead of once per session.
    // That is the harmless end of this failure, so it is swallowed rather than surfaced.
  }
}

/**
 * `sessionStorage` read as an external store, the way `useMediaQuery` reads `matchMedia`.
 *
 * Nothing subscribes, because nothing else in this tab writes the key. The point of the shape is
 * the server snapshot: the server has no session, so it renders as though the entry had already
 * played - nothing - and the client's first render after hydration decides for real. Deriving it
 * in an effect instead would be a setState in an effect body, which is the cascading render
 * React's own lint rule refuses.
 */
const NO_SUBSCRIPTION = () => () => {};
const PLAYED_ON_SERVER = () => true;

export function GlobeEntry({ sessionKey, slot, onDone, force = false }: GlobeEntryProps) {
  const reducedMotion = usePrefersReducedMotion();
  const read = useCallback(() => hasPlayed(sessionKey), [sessionKey]);
  const seen = useSyncExternalStore(NO_SUBSCRIPTION, read, PLAYED_ON_SERVER);
  const [dismissed, setDismissed] = useState(false);
  const playing = !dismissed && (force || !seen);
  const [skippable, setSkippable] = useState(false);
  const [fading, setFading] = useState(false);
  const revealed = useRef(false);
  const skipRef = useRef<HTMLButtonElement | null>(null);

  /** Tell the screen to show its map. Exactly once, however many ways we get here. */
  const reveal = useCallback(() => {
    if (revealed.current) return;
    revealed.current = true;
    onDone();
  }, [onDone]);

  /** Reveal the map and take the globe away in the same frame: the skip, and the cut. */
  const finish = useCallback(() => {
    markPlayed(sessionKey);
    reveal();
    setDismissed(true);
  }, [reveal, sessionKey]);

  /**
   * The globe has reached its last frame. The screen's map is revealed now and the globe is held,
   * fading, for exactly `heroHandover` - so the reader never sees `--ink` between the two, and the
   * map they end on was framed on the AOI before the fade began.
   */
  const handOver = useCallback(() => {
    markPlayed(sessionKey);
    reveal();
    setFading(true);
    window.setTimeout(() => setDismissed(true), DUR_MS.heroHandover);
  }, [reveal, sessionKey]);

  /**
   * Already seen this session, or dismissed: hand over on the frame after mount, so the screen is
   * never left waiting for a sequence that is not going to play.
   *
   * **It asks the store, not `playing`, and that is the whole point of this effect.** The first
   * client render *is* the hydration render, and React serves `useSyncExternalStore` its
   * `getServerSnapshot` there - which says "already played", because the server has no session.
   * An effect that read `playing` therefore fired `onDone` during hydration, before the store had
   * been read on the client at all. It is a real defect and it shipped: measured on `/dashboard`
   * at 1440 x 900 on this laptop (dev build, Next 16.3.4), `onDone` fired at about 1.5 s, at which
   * point the dashboard set `introDone` and put the `hidden` attribute on the div wrapping the
   * overlay - so M27 was in the DOM and `display: none` for its whole four seconds and **the
   * citizen dashboard's globe was never visible**. Reading `hasPlayed()` here instead is correct
   * because an effect only ever runs on the client, after hydration, where the store is readable.
   */
  useEffect(() => {
    if (dismissed || (!force && hasPlayed(sessionKey))) reveal();
  }, [dismissed, force, reveal, sessionKey]);

  /**
   * Reduced motion: the still frame paints, then the screen cuts. It is deliberately not held for
   * a beat - a reader who has asked for less motion has not asked to be made to wait - so the
   * frame is on screen for one paint and the map takes over.
   */
  useEffect(() => {
    if (!playing || !reducedMotion) return;
    const raf = requestAnimationFrame(() => finish());
    return () => cancelAnimationFrame(raf);
  }, [finish, playing, reducedMotion]);

  useEffect(() => {
    if (!playing || reducedMotion) return;
    const timer = window.setTimeout(() => setSkippable(true), SKIP_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [playing, reducedMotion]);

  // UI_SPEC 10 asks for the skip to be first in the tab order. The overlay covers the page but
  // does not remove it from the tab order, so the button takes focus when it appears: that is
  // what "first" has to mean for a control sitting on top of everything else.
  useEffect(() => {
    if (skippable) skipRef.current?.focus();
  }, [skippable]);

  // Any key, click, wheel or touch ends it. A reader who has started doing something has told us
  // they are finished watching, and making them find the button would be the rude reading.
  useEffect(() => {
    if (!playing) return;
    const skip = () => finish();
    window.addEventListener("keydown", skip);
    window.addEventListener("pointerdown", skip);
    window.addEventListener("wheel", skip, { passive: true });
    window.addEventListener("touchstart", skip, { passive: true });
    return () => {
      window.removeEventListener("keydown", skip);
      window.removeEventListener("pointerdown", skip);
      window.removeEventListener("wheel", skip);
      window.removeEventListener("touchstart", skip);
    };
  }, [finish, playing]);

  if (!playing) return null;

  return (
    <div
      data-slot={slot}
      data-fading={fading ? "true" : "false"}
      className="bg-ink fixed inset-0 z-50 motion-safe:transition-opacity motion-safe:duration-[900ms]"
      style={{ opacity: fading ? 0 : 1 }}
    >
      <GlobeIntro sequence="approach" still={reducedMotion} onDone={handOver} />
      {skippable ? (
        <button
          ref={skipRef}
          type="button"
          data-slot={`${slot}-skip`}
          onClick={finish}
          className="border-line bg-deep text-small text-text-2 hover:text-text focus-visible:outline-tide rounded-control absolute right-6 bottom-6 inline-flex h-11 items-center border px-4 focus-visible:outline-2 focus-visible:outline-offset-2"
        >
          Skip
        </button>
      ) : null}
    </div>
  );
}

"use client";

import { motion, useAnimate } from "motion/react";
import { useEffect, useRef, type ReactNode } from "react";

import { WordmarkMark } from "@/components/varuna/wordmark";
import { formatIst } from "@/lib/format";
import { DUR, EASE_UI, presetFor, tween, useMotionPref, type MotionPreset } from "@/lib/motion";
import { useReplayStore } from "@/lib/stores/replay";
import { cn } from "@/lib/utils";

/** What the phone shows before any alert has been raised. */
export const DEFAULT_PHONE_MESSAGE =
  "No alerts yet. The ward officer's phone shows the next alert here.";

/** The phone frame's horizontal offsets in px over the 300 ms shake of motion M16. */
export const PHONE_SHAKE_X = [0, -4, 4, -3, 2, 0] as const;

/** A new message pops: it grows from just under full size as it fades in (M16, full motion). */
export const MESSAGE_POP: MotionPreset = {
  initial: { opacity: 0, scale: 0.96 },
  animate: { opacity: 1, scale: 1 },
  transition: tween(DUR.alertSlide),
};

export interface PhoneMessage {
  id: string;
  /** Message body as the ward officer reads it. */
  text: string;
  /** Sent time, ISO 8601 with +05:30. */
  time?: string;
  /**
   * The map snapshot above the text: a node renders it, `null` draws the empty slot (the alert
   * has a snapshot that has not arrived), `undefined` draws no slot.
   */
  snapshot?: ReactNode | null;
}

export interface PhoneMockProps {
  messages: PhoneMessage[];
  /** Status-bar clock, ISO 8601; defaults to the replay clock. */
  simTime?: string;
  /** Ids of the messages that just arrived; they pop in (fade only under reduced motion). */
  freshIds?: ReadonlySet<string>;
  /**
   * Moves on each time a batch of new alerts reaches the phone, which shakes the frame once. The
   * value the phone mounts with is not a batch, so nothing shakes on the first render.
   */
  popKey?: number;
  className?: string;
}

const NO_FRESH: ReadonlySet<string> = new Set();

/**
 * The ward officer's phone (SPEC.md section 7.5): a 40 px-radius frame on `--ink`, a status
 * bar with the wordmark and the sim time, and a dark chat in which VARUNA's messages appear as
 * `--deep` bubbles with a tide accent.
 *
 * Motion M16: when a batch of new alerts arrives the new messages pop in and the frame shakes
 * once for 300 ms. Under reduced motion the messages fade in and the frame stays still; the chime
 * belongs to the alert centre (`useAlertChime`), which is where the batch is decided.
 */
export function PhoneMock({
  messages,
  simTime,
  freshIds = NO_FRESH,
  popKey = 0,
  className,
}: PhoneMockProps) {
  const storeSimTime = useReplayStore((s) => s.simTime);
  const clock = formatIst(simTime ?? storeSimTime);
  const { reduced } = useMotionPref();
  const [frame, animate] = useAnimate<HTMLDivElement>();

  const shaken = useRef(popKey);
  useEffect(() => {
    if (popKey === shaken.current) return;
    shaken.current = popKey;
    if (reduced || !frame.current) return;
    void animate(
      frame.current,
      { x: [...PHONE_SHAKE_X] },
      { duration: DUR.phoneShake, ease: EASE_UI },
    );
  }, [popKey, reduced, animate, frame]);

  const pop = reduced ? presetFor("M16", true) : MESSAGE_POP;

  return (
    <div
      ref={frame}
      role="figure"
      aria-label="Ward officer's phone"
      data-pop-key={popKey}
      className={cn(
        "rounded-phone border-line bg-ink mx-auto w-full max-w-[320px] border p-2",
        className,
      )}
    >
      <div className="border-line bg-ink flex h-[560px] flex-col overflow-hidden rounded-[32px] border">
        <div className="type-micro text-text-2 flex h-8 shrink-0 items-center justify-between px-5">
          {/* The emblem is decoration beside the name, which is still said as text. */}
          <span
            data-slot="phone-status-brand"
            className="text-text inline-flex items-center gap-1.5 font-medium"
          >
            <WordmarkMark size={16} alt="" />
            VARUNA
          </span>
          <span className="num">{clock}</span>
        </div>

        <div className="border-line bg-deep flex shrink-0 items-center gap-3 border-y px-4 py-2">
          {/* The chat's avatar is the team's emblem, never a letter standing in for a logo. */}
          <span
            aria-hidden="true"
            data-slot="phone-chat-avatar"
            className="bg-tide-soft flex size-8 shrink-0 items-center justify-center rounded-full"
          >
            <WordmarkMark size={20} alt="" />
          </span>
          <div className="min-w-0">
            <p className="type-small text-text truncate font-medium">VARUNA alerts</p>
            <p className="type-micro text-text-3">Ward officer</p>
          </div>
        </div>

        {/* Focusable: the message list scrolls and the cards in it are not controls, so a
            keyboard user could otherwise see the first message and no others (WCAG 2.1.1). */}
        <ol
          tabIndex={0}
          className="focus-visible:ring-tide/50 flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3 focus-visible:ring-3 focus-visible:outline-none"
          aria-label="Messages"
        >
          {messages.length === 0 ? (
            <li className="rounded-control bg-well type-micro text-text-2 self-center px-3 py-2 text-center">
              {DEFAULT_PHONE_MESSAGE}
            </li>
          ) : (
            messages.map((message) => {
              const fresh = freshIds.has(message.id);
              return (
                <motion.li
                  key={message.id}
                  data-fresh={fresh ? "true" : undefined}
                  initial={fresh ? pop.initial : false}
                  animate={pop.animate}
                  transition={pop.transition}
                  className="rounded-control border-tide bg-deep max-w-[88%] origin-top-left self-start border-l-2 p-3"
                >
                  {message.snapshot === undefined ? null : message.snapshot === null ? (
                    <div className="rounded-control border-line bg-well type-micro text-text-3 mb-2 flex aspect-[4/3] items-center justify-center border border-dashed">
                      Map snapshot arrives with the alert
                    </div>
                  ) : (
                    <div className="rounded-control mb-2 overflow-hidden">{message.snapshot}</div>
                  )}
                  <p className="type-small text-text whitespace-pre-line">{message.text}</p>
                  {message.time ? (
                    <p className="num type-micro text-text-3 mt-1 text-right">
                      {formatIst(message.time)}
                    </p>
                  ) : null}
                </motion.li>
              );
            })
          )}
        </ol>

        <div className="border-line bg-deep flex shrink-0 items-center gap-2 border-t px-3 py-2">
          <span className="rounded-chip border-line bg-well type-micro text-text-3 flex-1 border px-3 py-1.5">
            Message
          </span>
        </div>
      </div>
    </div>
  );
}

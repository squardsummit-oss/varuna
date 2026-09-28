"use client";

import { ChevronDown } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import type { ReactNode } from "react";

import { AlertLevelChip, ALERT_LEVEL_LABELS } from "@/components/varuna/alert-level-chip";
import type { AlertLevel, AlertStatusKind, RunAlert } from "@/lib/api/alerts";
import { alertPlace, alertWindowLine } from "@/lib/api/alerts";
import { formatCm } from "@/lib/format";
import { presetFor, useMotionPref } from "@/lib/motion";
import { cn } from "@/lib/utils";

export interface AlertRowProps {
  alert: Pick<
    RunAlert,
    | "id"
    | "level"
    | "name"
    | "areaDesc"
    | "locality"
    | "peakCm"
    | "windowFrom"
    | "windowTo"
    | "windowOpenEnded"
  >;
  /** The cycle that raised it, for the lead time and the end of the forecast. */
  cycleTs: string | null;
  /** "New", "Held 3 cycles", "Acknowledged 08:52", "Escalated to control room". */
  status: { kind: AlertStatusKind; label: string };
  /** The level this place raises at next cycle if it holds, when that is a level up. */
  upgrade?: AlertLevel | null;
  /** Whether the details under the row are open. */
  open: boolean;
  onToggle: (id: string) => void;
  /** The details element's id, for `aria-controls`. */
  detailsId: string;
  /** Slides in from 12 px above over 180 ms (M16), fades under reduced motion. */
  entering?: boolean;
  /** The details, rendered only while open (M29). */
  children?: ReactNode;
  className?: string;
}

/** The pill's tone: the desk's state reads calmer than an alert nobody has seen. */
const STATUS_TONE: Record<AlertStatusKind, string> = {
  new: "border-tide text-tide",
  held: "border-line text-text-2",
  acknowledged: "border-line text-text-2",
  escalated: "border-line-strong text-text",
};

/**
 * One alert in the alert centre's queue (SPEC.md 7.5), two lines at about 56 px: the level
 * chip, the place, its peak and where the desk has got with it; then the window with its lead
 * time and, when a pending level sits above this one, that it steps up next cycle if it holds.
 * Everything else - what to do, who has been told, the timeline, the messages and the CAP
 * document - is behind "See more", which opens under the row (M29).
 *
 * The row stays a `motion.article` with `layout="position"` so M16 still holds: a new alert
 * slides into the queue and the rows already there move down with it.
 */
export function AlertRow({
  alert,
  cycleTs,
  status,
  upgrade = null,
  open,
  onToggle,
  detailsId,
  entering = false,
  children,
  className,
}: AlertRowProps) {
  const { reduced } = useMotionPref();
  const slide = presetFor("M16", reduced);
  const expand = presetFor("M29", reduced);
  const place = alertPlace(alert);

  return (
    <motion.article
      layout={reduced ? false : "position"}
      initial={entering ? slide.initial : false}
      animate={slide.animate}
      transition={slide.transition}
      data-entering={entering ? "true" : undefined}
      data-alert-id={alert.id}
      aria-label={`${ALERT_LEVEL_LABELS[alert.level]}: ${place}`}
      className={cn(
        "rounded-control border-line bg-deep border",
        open && "border-line-strong bg-well",
        className,
      )}
    >
      <div className="flex min-h-14 items-center gap-3 px-3 py-2">
        <AlertLevelChip level={alert.level} size="sm" className="shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="flex min-w-0 flex-wrap items-baseline gap-x-2">
            <span className="type-small text-text min-w-0 truncate font-medium">{place}</span>
            {alert.locality ? (
              <span className="type-micro text-text-3 truncate">{alert.locality}</span>
            ) : null}
          </p>
          <p className="type-micro text-text-2 flex flex-wrap gap-x-3">
            <span className="num">{alertWindowLine(alert, cycleTs)}</span>
            {upgrade ? (
              <span className="text-text">
                {ALERT_LEVEL_LABELS[upgrade]} next cycle if it holds
              </span>
            ) : null}
          </p>
        </div>
        <span className="num type-small text-text shrink-0 font-medium">
          {formatCm(alert.peakCm)}
        </span>
        <span
          className={cn(
            "rounded-chip type-micro inline-flex shrink-0 border px-2 py-0.5",
            STATUS_TONE[status.kind],
          )}
        >
          {status.label}
        </span>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={detailsId}
          onClick={() => onToggle(alert.id)}
          className="rounded-control type-micro text-text-2 hover:text-text focus-visible:ring-tide inline-flex h-7 shrink-0 items-center gap-1 px-2 focus-visible:ring-2 focus-visible:outline-none"
        >
          {open ? "See less" : "See more"}
          <ChevronDown
            size={16}
            strokeWidth={1.75}
            aria-hidden="true"
            className={cn(open && "rotate-180")}
          />
        </button>
      </div>
      {/* The target of `aria-controls` stays mounted while closed, so the reference always
          resolves; what it holds is mounted only while open. */}
      <div id={detailsId}>
        <AnimatePresence initial={false}>
          {open ? (
            <motion.div
              key="details"
              initial={expand.initial}
              animate={expand.animate}
              exit={expand.exit}
              transition={expand.transition}
              className="overflow-hidden"
            >
              <div className="border-line border-t px-3 py-3">{children}</div>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    </motion.article>
  );
}

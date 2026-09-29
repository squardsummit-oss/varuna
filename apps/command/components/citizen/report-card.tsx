"use client";

/**
 * One citizen report, as the citizen dashboard and the ward desk both show it.
 *
 * The card is what a pin on the map opens, and it carries the three things a reader must not
 * have to guess:
 *
 * - **what the reporter said**: where, when, how deep (the chip is the reporter's depth in the
 *   depth ramp, because it is a water depth; the words say it was their estimate), and their text;
 * - **what the ward desk has done with it**: the status in words, outlined the way the pin is
 *   outlined on the map, and the desk's latest note to the reporter;
 * - **what is real**: a seeded demo report says "Demo report (synthetic)", and its photo - which
 *   is somebody else's Wikimedia Commons photo of an older flood - says it is illustrative and
 *   credits its author and licence (SPEC.md rule 7). A photo the API did not keep is not
 *   pretended into existence: the card prints the API's own sentence about it and nothing else.
 *
 * The thumbnail is a plain `<img>`: the URL is either the API's own photo route or a Commons
 * thumbnail, both already sized, and `reportPhotoUrl` refuses anything else.
 */

import { useState } from "react";

import { reportDepthCm, reportDepthWords } from "@/components/map/layers/reports";
import { DepthChip } from "@/components/varuna/depth-chip";
import {
  isDismissed,
  OFFICER_ROLES,
  reportToPin,
  statusLabel,
  type DismissedReport,
  type PublicReport,
  type ReportHistoryEntry,
  type ReportStatus,
} from "@/lib/api/reports";
import { formatDate, formatDateTime, formatIst } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The label every seeded report carries (SPEC.md rule 7). */
export const SYNTHETIC_LABEL = "Demo report (synthetic)";

/** The label under every seeded photo: it is a real photo, of somewhere else, on another day. */
export const ILLUSTRATIVE_PHOTO_LABEL = "Illustrative photo, not taken here or on 2 July 2019";

/**
 * The status chip's outline, matching the pin's outline on the map (`REPORT_STATUS_STYLE`), so
 * the card and the pin read as one thing. Never a depth-ramp colour: a status is not a depth.
 */
const STATUS_CHIP: Record<ReportStatus, string> = {
  received: "border border-line-strong text-text-2",
  seen: "border border-text text-text",
  crew_sent: "border-2 border-tide text-text",
  resolved: "border border-naive text-text-2",
  dismissed: "border border-naive text-text-3",
};

export interface ReportStatusChipProps {
  status: ReportStatus;
  /** A seeded demo status rather than the desk's act: the words say "(demo status)" (rule 7). */
  seeded?: boolean;
  className?: string;
}

/** A report's status in words, in a pill outlined as its pin is. */
export function ReportStatusChip({ status, seeded = false, className }: ReportStatusChipProps) {
  return (
    <span
      data-slot="report-status"
      data-status={status}
      data-seeded={seeded || undefined}
      className={cn(
        // Grows with its words: "Seen by the ward desk (demo status)" wraps in a narrow card.
        "rounded-chip bg-well type-micro inline-flex min-h-6 items-center px-2.5 py-0.5 font-medium",
        STATUS_CHIP[status],
        className,
      )}
    >
      {statusLabel(status, { seeded })}
    </span>
  );
}

/** "Ward officer" from "ward officer". */
function sentenceCase(text: string): string {
  return text ? `${text.charAt(0).toUpperCase()}${text.slice(1)}` : text;
}

/**
 * The act that set the status on screen: the last entry in the report's history, which the API
 * orders so that a real act at the desk is always the latest. Null while nobody has acted, and for
 * an entry whose role is not one the desk uses (an old row the API never re-labelled).
 */
function latestAct(history: readonly ReportHistoryEntry[]): ReportHistoryEntry | null {
  const entry = history.at(-1) ?? null;
  if (!entry) return null;
  if (entry.seeded) return entry;
  return (OFFICER_ROLES as readonly string[]).includes(entry.role) ? entry : null;
}

/** "08:55", or "27 Sep 2026 14:02 IST" when the act was on another day than the report. */
function actTime(actTs: string, reportTs: string | null): string {
  return reportTs && formatDate(actTs) === formatDate(reportTs)
    ? formatIst(actTs)
    : formatDateTime(actTs);
}

/** What the photo shows, for its `alt`: the Commons caption for a seed, the place for a citizen. */
function photoAlt(report: PublicReport): string {
  const caption = report.credit?.caption?.trim() || report.credit?.title?.trim();
  if (report.origin === "seed" && caption) return caption;
  const place = report.place?.trim();
  return place ? `Photo sent with the report at ${place}` : "Photo sent with the report";
}

/** A credit link from the API, only when it is plain https; anything else is not rendered. */
function httpsOnly(url: string | null | undefined): string | null {
  return typeof url === "string" && url.startsWith("https://") ? url : null;
}

const LINK =
  "rounded-control text-tide type-small inline-flex h-11 items-center px-1 underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-tide";

export interface ReportCardProps {
  report: PublicReport | DismissedReport;
  /** The card whose pin is selected on the map. */
  selected?: boolean;
  /**
   * The level of the place's heading. 3 inside a list under its own h2, which is where the card
   * usually sits; 2 where it stands alone after the page's h1 - the card a pin opens over the
   * dashboard's map, which an h3 there made a skipped level (axe `heading-order`).
   */
  headingLevel?: 2 | 3;
  className?: string;
}

export function ReportCard({
  report,
  selected = false,
  headingLevel = 3,
  className,
}: ReportCardProps) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  const [photoFailed, setPhotoFailed] = useState(false);

  if (isDismissed(report)) {
    return (
      <article
        data-slot="report-card"
        data-report-id={report.id}
        aria-label="Citizen report, dismissed by the ward desk"
        className={cn(
          "rounded-panel border-line bg-deep border p-4",
          selected && "border-line-strong",
          className,
        )}
      >
        <ReportStatusChip status="dismissed" />
        {/* The API's own sentence: a dismissed report keeps its status and loses its content. */}
        <p className="type-small text-text-2 mt-2">{report.note}</p>
        {report.origin === "seed" ? (
          <p className="type-micro text-text-3 mt-2">{SYNTHETIC_LABEL}</p>
        ) : null}
      </article>
    );
  }

  // The pin's own reading of the report, so the card and the pin on the map cannot disagree about
  // the photo, its credit, the status words or whether it is synthetic.
  const pin = reportToPin(report);
  const seed = pin.synthetic;
  const place = pin.place?.trim() || "Citizen report";
  const when = pin.ts ?? report.received_at ?? null;
  const depthWords = reportDepthWords(pin);
  const depthCm = reportDepthCm(pin);
  const thumb = pin.thumbUrl;
  const full = pin.photoUrl ?? thumb;
  // Credited only under a photo that is shown; `pin.credit` is null otherwise.
  const creditLine = pin.credit;
  const credit = creditLine ? (report.credit ?? null) : null;
  const sourceHref = credit ? httpsOnly(credit.source_url) : null;
  const licenceHref = credit ? httpsOnly(credit.license_url) : null;
  const act = latestAct(report.history);
  const actNote = act?.note?.trim() || null;
  // A photo was sent and is not here. The API says why (the deployed API keeps no photos); the
  // card repeats it and never guesses at a reason of its own.
  const photoMissingNote =
    report.photo_attached && !report.has_photo ? report.photo_note?.trim() || null : null;
  const showPhoto = Boolean(thumb) && !photoFailed;

  return (
    <article
      data-slot="report-card"
      data-report-id={report.id}
      aria-label={`Citizen report: ${place}`}
      className={cn(
        "rounded-panel border-line bg-deep border p-4",
        selected && "border-line-strong",
        className,
      )}
    >
      <div className="flex gap-3">
        {showPhoto ? (
          // eslint-disable-next-line @next/next/no-img-element -- an already-sized API or Commons thumbnail; next/image would proxy somebody else's photo
          <img
            src={thumb ?? undefined}
            alt={photoAlt(report)}
            width={96}
            height={72}
            loading="lazy"
            decoding="async"
            referrerPolicy="no-referrer"
            onError={() => setPhotoFailed(true)}
            className="rounded-control bg-well h-[72px] w-24 shrink-0 object-cover"
          />
        ) : null}
        <div className="min-w-0 flex-1">
          <Heading className="type-body text-text truncate font-medium">{place}</Heading>
          <p className="type-small text-text-2 num">
            {when ? <time dateTime={when}>{formatDateTime(when)}</time> : "Time not given"}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {depthCm !== null ? <DepthChip cm={depthCm} size="sm" /> : null}
            <span className="type-small text-text-2">
              {depthWords ? `${depthWords}, as reported` : "No depth given"}
            </span>
            <ReportStatusChip status={pin.status} seeded={pin.statusSeeded} />
          </div>
        </div>
      </div>

      {report.text?.trim() ? (
        <p className="type-body text-text mt-3 break-words">{report.text.trim()}</p>
      ) : null}

      {act ? (
        // Who set the status and when, by role: the reporter never sees a name, and the desk's
        // exact list carries one (`user`), which is printed beside the role only there. A seeded
        // status says no officer set it (rule 7), so it can never read as the desk's act.
        <p
          data-slot="report-desk-note"
          className="type-small text-text-2 mt-2"
          // A seeded status's note only restates the label, so it moves to the title.
          title={act.seeded ? (actNote ?? undefined) : undefined}
        >
          {act.seeded ? (
            <span className="text-text font-medium">Demo status, not set by the ward desk</span>
          ) : (
            <>
              <span className="text-text font-medium">
                {sentenceCase(act.role)}
                {act.user?.trim() ? ` (${act.user.trim()})` : ""}
              </span>
              {act.ts ? (
                <span className="num">
                  {" "}
                  at {actTime(act.ts, pin.ts ?? report.received_at ?? null)}
                </span>
              ) : null}
            </>
          )}
          {actNote && !act.seeded ? `: ${actNote}` : null}
        </p>
      ) : null}

      {photoMissingNote ? (
        <p data-slot="report-photo-note" className="type-small text-text-2 mt-2">
          {photoMissingNote}
        </p>
      ) : null}
      {thumb && photoFailed ? (
        <p className="type-small text-text-2 mt-2">
          The photo did not load. Open it on its own page instead.
        </p>
      ) : null}

      {seed || creditLine ? (
        <div data-slot="report-honesty" className="mt-3 flex flex-col gap-1">
          {seed ? <p className="type-micro text-text-2">{SYNTHETIC_LABEL}</p> : null}
          {creditLine ? (
            <>
              <p className="type-micro text-text-2">{ILLUSTRATIVE_PHOTO_LABEL}</p>
              <p className="type-micro text-text-3">{creditLine}</p>
            </>
          ) : null}
        </div>
      ) : null}

      {full || sourceHref || licenceHref ? (
        <div className="mt-1 flex flex-wrap gap-x-4">
          {full ? (
            <a href={full} target="_blank" rel="noopener noreferrer" className={LINK}>
              View photo
            </a>
          ) : null}
          {sourceHref ? (
            <a href={sourceHref} target="_blank" rel="noopener noreferrer" className={LINK}>
              Photo source
            </a>
          ) : null}
          {licenceHref && credit ? (
            <a href={licenceHref} target="_blank" rel="noopener noreferrer" className={LINK}>
              {credit.license}
            </a>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

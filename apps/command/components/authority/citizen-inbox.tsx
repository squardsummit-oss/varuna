"use client";

/**
 * The citizen inbox (UI_SPEC 6, PRD 3.2, task D-15; photos and linked status, 2026-09-27).
 *
 * A ward officer is the one person for whom "someone is standing in knee-deep water on this
 * street, ten minutes ago" is actionable, so this is where a citizen's complaint lands - with the
 * photo they took, the depth they chose, where and when - and where the desk answers it. The
 * answer is a status (received, seen, crew sent, resolved, dismissed) with the officer's role and
 * an optional note, and it is the same answer the reporter reads on the dashboard: the status is
 * appended to the ops log and `GET /v1/reports` folds it onto the report, so there is one record
 * and two readers.
 *
 * **Each row is the card the citizen dashboard shows** (`ReportCard`), so the two screens cannot
 * describe one report differently: the same photo and credit, the same "Demo report (synthetic)"
 * and "Illustrative photo" labels on the seeded demo reports, the same status words. A photo the
 * API did not keep is never labelled "attached": the card prints the API's own sentence about it.
 *
 * **The status control sits behind the desk's passphrase gate**, like every other write. When the
 * API holds no passphrase - the deployed one, on purpose - the row prints the API's own reason
 * instead of a form, and a write the API refuses prints the API's own sentence (SPEC.md 6.8:
 * what happened and the fix). A refused passphrase closes the desk, as it does from any panel.
 *
 * **One report is open at a time, and it is the one the ward map has selected.** Opening a row
 * flies the map to it; tapping a pin opens its row. The list is filtered by status here only - the
 * map keeps every pin - and the open report is listed whatever the filter, so a pin tapped under a
 * filter that would hide its row still opens it, and a status just set does not make its row vanish.
 *
 * **A report does not change the forecast by being read or answered here.** It changes it when
 * the next cycle's Pulse assimilates it, which is what the panel says.
 */

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { ReportCard } from "@/components/citizen/report-card";
import { ActionResult } from "@/components/authority/action-result";
import type { DeskReports } from "@/components/authority/desk-reports";
import type { GateStatus } from "@/components/authority/passphrase-gate";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState } from "@/components/varuna/empty-state";
import { Panel } from "@/components/varuna/panel";
import { Skeleton } from "@/components/varuna/skeleton";
import {
  describeRefusal,
  isGateRefusal,
  opsRefusal,
  readPassphrase,
  type OpsRefusal,
} from "@/lib/api/ops";
import {
  OFFICER_ROLES,
  postReportStatus,
  REPORT_STATUS_LABEL,
  REPORT_STATUS_NOTE_MAX,
  REPORT_STATUSES,
  type OfficerRole,
  type PublicReport,
  type ReportStatus,
  type ReportStatusResult,
} from "@/lib/api/reports";
import { formatIst } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * What the desk calls each status: the words the citizen's card prints, and no others. The desk
 * once had its own set, and a row read "Status set: Seen by ward officer" beside a card saying
 * "Seen by the ward desk" (SPEC.md 6.8: one word for one thing).
 */
export const DESK_STATUS_LABEL: Record<ReportStatus, string> = REPORT_STATUS_LABEL;

type Filter = "all" | ReportStatus;

/** Whether this desk can write, and why not when it cannot - the screen's gate, passed down. */
export interface InboxWriteAccess {
  /** This tab holds a passphrase the API accepted. */
  open: boolean;
  gate: GateStatus;
  /** The API's own sentence for why writes are off or the API is out of reach. */
  reason: string | null;
  /** The name the log carries for this officer; never shown to a reporter. */
  officer: string | null;
}

export interface CitizenInboxProps {
  reports: Pick<DeskReports, "list" | "error" | "exact" | "selectedId" | "select" | "replace">;
  access: InboxWriteAccess;
  /** Called after a status is set, so the screen reloads the log and the list. */
  onWrote?: () => void;
  /** The API refused this tab's passphrase; the screen closes the desk. */
  onGateRefused?: (refusal: OpsRefusal) => void;
  className?: string;
}

function rowDomId(id: string): string {
  return `inbox-report-${id.replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

/** "19.0123, 72.8412", to the precision the API sent: four places exact, three when rounded. */
function coordinateLine(report: PublicReport, exact: boolean): string {
  const places = exact ? 4 : 3;
  return `${report.lat.toFixed(places)}, ${report.lon.toFixed(places)}`;
}

export function CitizenInbox({
  reports,
  access,
  onWrote,
  onGateRefused,
  className,
}: CitizenInboxProps) {
  const { list, error, exact, selectedId, select, replace } = reports;
  const [filter, setFilter] = useState<Filter>("all");
  const rows = list?.reports ?? [];

  // Bring the open row into view inside the list. `nearest` moves nothing when it is already
  // visible, and it is instant: section 8 has no row for a scroll.
  const listRef = useRef<HTMLUListElement>(null);
  useEffect(() => {
    if (!selectedId) return;
    const row = listRef.current?.querySelector<HTMLElement>(`#${rowDomId(selectedId)}`);
    if (row && typeof row.scrollIntoView === "function") row.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  const counts = new Map<Filter, number>([["all", rows.length]]);
  for (const row of rows) counts.set(row.status, (counts.get(row.status) ?? 0) + 1);
  // The open report is always listed, whatever the filter: a pin tapped on the map opens its row,
  // and a status the desk has just set does not make the row it was set on disappear.
  const visible =
    filter === "all" ? rows : rows.filter((row) => row.status === filter || row.id === selectedId);
  // The public list leaves dismissed reports out, so the desk offers that filter only when its
  // own list carries them.
  const filters: Filter[] = ["all", ...REPORT_STATUSES.filter((s) => exact || s !== "dismissed")];

  const done = useCallback(
    (result: ReportStatusResult) => {
      replace(result.report);
      onWrote?.();
    },
    [replace, onWrote],
  );

  return (
    <Panel
      className={className}
      title="Citizen inbox"
      description="What people reported, newest first, with their photos. A status you set here is shown with the report on the citizen dashboard. Pulse assimilates reports on the next cycle."
    >
      {list === null ? (
        <Skeleton lines={4} />
      ) : (
        <div className="flex h-full min-h-0 flex-col gap-3">
          {error ? (
            <p role="status" className="type-small text-text-2">
              {error}
            </p>
          ) : null}

          {rows.length === 0 ? (
            error ? null : (
              <EmptyState
                size="sm"
                title="No reports yet"
                description="A report arrives from the public map's Report water button, or from /report. This inbox checks for new ones every 30 seconds."
              />
            )
          ) : (
            <>
              <div role="group" aria-label="Filter by status" className="flex flex-wrap gap-1.5">
                {filters.map((option) => {
                  const pressed = filter === option;
                  return (
                    <button
                      key={option}
                      type="button"
                      aria-pressed={pressed}
                      onClick={() => setFilter(option)}
                      className={cn(
                        "rounded-chip border-line type-micro num inline-flex h-9 items-center gap-1.5 border px-3 transition-colors",
                        pressed
                          ? "bg-well text-text border-line-strong"
                          : "text-text-2 hover:bg-well",
                      )}
                    >
                      {option === "all" ? "All" : DESK_STATUS_LABEL[option]}{" "}
                      <span className="text-text-3">{counts.get(option) ?? 0}</span>
                    </button>
                  );
                })}
              </div>

              {visible.length === 0 ? (
                <p className="type-small text-text-2">
                  No report has the status {DESK_STATUS_LABEL[filter as ReportStatus]} right now.
                </p>
              ) : (
                <ul
                  ref={listRef}
                  aria-label="Citizen reports"
                  className="max-h-[36rem] min-h-0 flex-1 space-y-2 overflow-y-auto lg:max-h-none"
                  // Scrollable, so reachable by keyboard (axe `scrollable-region-focusable`).
                  tabIndex={0}
                >
                  {visible.map((report) => {
                    const open = report.id === selectedId;
                    const panelId = `${rowDomId(report.id)}-status`;
                    return (
                      <li
                        key={report.id}
                        id={rowDomId(report.id)}
                        data-report-id={report.id}
                        data-open={open || undefined}
                        aria-current={open ? "true" : undefined}
                        className="space-y-2"
                      >
                        <ReportCard report={report} selected={open} />
                        <div className="flex flex-wrap items-center justify-between gap-2 px-1">
                          <p className="num type-micro text-text-3">
                            {coordinateLine(report, exact)} · {report.coordinates} · {report.source}
                          </p>
                          <Button
                            type="button"
                            variant={open ? "ghost" : "secondary"}
                            className="h-9 px-3"
                            aria-expanded={open}
                            aria-controls={open ? panelId : undefined}
                            onClick={() => select(open ? null : report.id, { fly: !open })}
                          >
                            {open ? "Close" : "Open on the map"}
                          </Button>
                        </div>
                        {open ? (
                          <div id={panelId}>
                            <ReportStatusForm
                              key={report.id}
                              report={report}
                              access={access}
                              onDone={done}
                              onGateRefused={onGateRefused}
                            />
                          </div>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </>
          )}

          {/* The API's notes on the list, folded: at 1366 x 768 they took the height the first
              report needed, and the officer saw a sliver of a card beside the map. */}
          {list.notes?.length ? (
            <details data-slot="inbox-notes" className="shrink-0">
              <summary className="type-micro text-text-2 hover:text-text focus-visible:outline-tide cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2">
                About this list
              </summary>
              <ul className="mt-1 space-y-1" aria-label="About this list">
                {list.notes.map((note) => (
                  <li key={note} className="type-micro text-text-3">
                    {note}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------------------
// The status control
// ---------------------------------------------------------------------------------------

interface ReportStatusFormProps {
  report: PublicReport;
  access: InboxWriteAccess;
  onDone: (result: ReportStatusResult) => void;
  onGateRefused?: (refusal: OpsRefusal) => void;
}

type Outcome =
  | { kind: "set"; status: ReportStatus; at: string; notes: string[] }
  | { kind: "refused"; message: string };

/** Why this desk cannot set a status now, in the API's words wherever the API gave some. */
function closedReason(access: InboxWriteAccess): string {
  if (access.gate === "checking") return "Checking whether this API accepts status changes.";
  if (access.gate === "locked") return "Open the desk with its passphrase to set a status.";
  if (access.reason) return access.reason;
  // `writes_enabled` was false and the API sent no sentence with it.
  return access.gate === "unreachable"
    ? "The VARUNA API did not answer, so no status can be set."
    : "This API is read-only, so no status can be set from here.";
}

function ReportStatusForm({ report, access, onDone, onGateRefused }: ReportStatusFormProps) {
  const [status, setStatus] = useState<ReportStatus>(report.status);
  const [role, setRole] = useState<OfficerRole>("ward officer");
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const idBase = rowDomId(report.id);

  const submit = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      if (pending) return;
      setPending(true);
      setOutcome(null);
      try {
        const result = await postReportStatus(report.id, status, note, readPassphrase(), {
          user: access.officer ?? undefined,
          role,
        });
        setOutcome({
          kind: "set",
          status: result.entry.status,
          at: formatIst(result.entry.ts),
          notes: result.notes,
        });
        setNote("");
        onDone(result);
      } catch (failure: unknown) {
        const refusal = opsRefusal(failure);
        setOutcome({ kind: "refused", message: describeRefusal(refusal) });
        if (isGateRefusal(refusal)) onGateRefused?.(refusal);
      } finally {
        setPending(false);
      }
    },
    [pending, report.id, status, note, access.officer, role, onDone, onGateRefused],
  );

  if (!access.open) {
    return (
      <p
        data-slot="report-status-closed"
        className="rounded-control border-line bg-well/40 type-small text-text-2 border p-3"
      >
        {closedReason(access)}
      </p>
    );
  }

  // Setting the status it already has is still an act when it carries a note.
  const unchanged = status === report.status && !note.trim();

  return (
    <form
      onSubmit={submit}
      aria-label={`Set the status of the report at ${report.place?.trim() || coordinateLine(report, true)}`}
      className="rounded-control border-line bg-well/40 space-y-3 border p-3"
    >
      <fieldset className="space-y-1.5">
        <legend className="type-small text-text font-medium">Status</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {REPORT_STATUSES.map((option) => (
            <label
              key={option}
              className="type-small text-text inline-flex h-9 cursor-pointer items-center gap-2"
            >
              <input
                type="radio"
                name={`${idBase}-status`}
                value={option}
                checked={status === option}
                onChange={() => setStatus(option)}
                className="accent-tide size-4"
              />
              {DESK_STATUS_LABEL[option]}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid gap-3 sm:grid-cols-[auto_1fr]">
        <div className="space-y-1.5">
          <Label htmlFor={`${idBase}-role`} className="type-small text-text">
            Your role, shown with the report
          </Label>
          <select
            id={`${idBase}-role`}
            value={role}
            onChange={(event) => setRole(event.target.value as OfficerRole)}
            className="rounded-control border-line bg-well type-small text-text h-9 border px-2"
          >
            {OFFICER_ROLES.map((option) => (
              <option key={option} value={option}>
                {option.charAt(0).toUpperCase() + option.slice(1)}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idBase}-note`} className="type-small text-text">
            Note shown with the report on the public dashboard (optional)
          </Label>
          <Textarea
            id={`${idBase}-note`}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={REPORT_STATUS_NOTE_MAX}
            rows={2}
            placeholder="Pump P-12 is on its way from Parel depot."
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" className="h-9 px-4" disabled={pending || unchanged}>
          {pending ? "Setting status" : "Set status"}
        </Button>
        <p className="type-micro text-text-3">
          Everyone who opens the citizen dashboard reads the status, the role and the note. Your
          name stays on the desk.
        </p>
      </div>

      {outcome?.kind === "set" ? (
        <ActionResult
          outcome="recorded"
          at={outcome.at}
          message={`Status set: ${DESK_STATUS_LABEL[outcome.status]}.`}
          notes={outcome.notes}
        />
      ) : outcome?.kind === "refused" ? (
        <ActionResult outcome="refused" message={outcome.message} />
      ) : null}
    </form>
  );
}

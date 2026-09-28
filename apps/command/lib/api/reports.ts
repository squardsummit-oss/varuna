/**
 * Citizen reports with photos and the desk's status (`/v1/reports`, `/v1/ops/reports`).
 *
 * The API side is `services/api/varuna_api/routers/reports.py`; the schemas here are hand-written
 * against it until the lead regenerates `types.ts`, and loose so an added field does not break a
 * screen.
 *
 * **Three things a screen must not get wrong.**
 *
 * - A photo URL is either relative to the API (a citizen's photo, served by
 *   `GET /v1/reports/{id}/photo`) or an absolute Wikimedia Commons link (a seed report's
 *   illustrative photo). {@link resolvePhotoUrl} turns both into something an `<img>` can load
 *   and refuses anything else, so a `data:` or `javascript:` value can never reach the page.
 * - A seed report is synthetic and its photo is somebody else's. {@link photoCredit} is the line
 *   that has to sit under it; the API's own `note` says it is illustrative.
 * - The public list rounds coordinates to three decimals (about 110 m) and names an officer by
 *   role. The exact view is {@link loadDeskReports}, behind the desk passphrase.
 *
 * **Seed reports.** `GET /v1/reports` merges eight synthetic demo reports into what people sent
 * (`origin: "seed"`, `synthetic: true`), and two of them carry demo statuses no officer set
 * (`status_seeded: true`, each history entry `seeded: true`). {@link statusLabel} with
 * `{ seeded: true }` and {@link reportToPin}'s `statusLabel` say so in the words a screen prints;
 * a screen must never show a seeded status as if the desk had acted.
 */
import { z } from "zod";

import { ApiError, apiFetch, apiUrl, type QueryValue } from "@/lib/api/client";
import { OPS_HEADER, readPassphrase } from "@/lib/api/ops";
import { ReportInput } from "@/lib/api/schemas";

/** A report's life at the desk, in the order it usually moves (`ops_overlay.REPORT_STATES`). */
export const REPORT_STATUSES = ["received", "seen", "crew_sent", "resolved", "dismissed"] as const;
export type ReportStatus = (typeof REPORT_STATUSES)[number];

/** What the reporter reads for each status. Sentence case, no jargon (SPEC.md 6.8). */
export const REPORT_STATUS_LABEL: Record<ReportStatus, string> = {
  received: "Received",
  seen: "Seen by the ward desk",
  crew_sent: "Crew sent",
  resolved: "Resolved",
  dismissed: "Dismissed by the ward desk",
};

/** Who set a status, as the public sees it (`reports.OFFICER_ROLES`). */
export const OFFICER_ROLES = ["ward officer", "control room", "field crew"] as const;
export type OfficerRole = (typeof OFFICER_ROLES)[number];

/** `reports.PHOTO_DATA_URL_MAX`: the longest `photo_data_url` the API accepts, in characters. */
export const REPORT_PHOTO_MAX_CHARS = 700_000;

/** `reports.REPORT_BODY_LIMIT`: a whole report body, photo included, in bytes. */
export const REPORT_BODY_MAX_BYTES = 1_000_000;

/** `reports.REPORTS_PER_MINUTE`: reports one phone may send in a rolling minute. */
export const REPORTS_PER_MINUTE = 6;

const StatusSchema = z.enum(REPORT_STATUSES);

export const ReportHistoryEntrySchema = z.looseObject({
  status: StatusSchema,
  ts: z.string().nullish(),
  role: z.string(),
  note: z.string().nullish(),
  /** A demo status from the seed that no officer set. */
  seeded: z.boolean().optional(),
  /** Desk view only. */
  user: z.string().nullish(),
});
export type ReportHistoryEntry = z.infer<typeof ReportHistoryEntrySchema>;

export const PhotoCreditSchema = z.looseObject({
  title: z.string(),
  author: z.string(),
  license: z.string(),
  license_url: z.string(),
  source_url: z.string(),
  original_source: z.string().nullish(),
  taken: z.string().nullish(),
  caption: z.string().nullish(),
  note: z.string(),
});
export type PhotoCredit = z.infer<typeof PhotoCreditSchema>;

/**
 * What `report.coordinates` reads when the API did not say how precise the position is. An API
 * from before 2026-09-26 sent the stored point as it was, and sent no precision label at all.
 */
export const COORDINATES_NOT_STATED = "precision not stated";

/**
 * One report as `GET /v1/reports` or the desk serves it.
 *
 * The deployed API can lag this build (Railway is redeployed by hand, Vercel on every push), and
 * an API from before 2026-09-26 sends only `id, ts, received_at, lat, lon, depth_hint, depth_cm,
 * text, has_photo, source, synthetic` per report. The fields it lacks default to what is true of
 * such an API rather than failing the whole list: every report it held was a citizen's, none had
 * a status or a history beyond "received", and it stated no city, area or coordinate precision,
 * so `city` and `outside_aoi` stay absent rather than claiming "inside Mumbai". Its `has_photo`
 * meant "a photo was attached" and it served no photo, so that becomes `photo_attached` and
 * `has_photo` is true only when there is a URL to load.
 */
export const PublicReportSchema = z
  .looseObject({
    id: z.string(),
    origin: z.enum(["citizen", "seed"]).default("citizen"),
    synthetic: z.boolean(),
    ts: z.string().nullish(),
    received_at: z.string().nullish(),
    lat: z.number(),
    lon: z.number(),
    /** "exact" on the desk, "rounded to 3 decimals" in public, {@link COORDINATES_NOT_STATED} otherwise. */
    coordinates: z.string().default(COORDINATES_NOT_STATED),
    /** Null when the report is outside every forecast area; absent when the API did not say. */
    city: z.string().nullish(),
    /** Absent when the API did not classify the report. */
    outside_aoi: z.boolean().optional(),
    place: z.string().nullish(),
    depth_hint: z.string().nullish(),
    depth_cm: z.number().nullish(),
    text: z.string().nullish(),
    source: z.string(),
    photo_attached: z.boolean().optional(),
    has_photo: z.boolean(),
    photo_url: z.string().nullish(),
    thumb_url: z.string().nullish(),
    photo_note: z.string().nullish(),
    credit: PhotoCreditSchema.nullish(),
    status: StatusSchema.default("received"),
    status_ts: z.string().nullish(),
    /** The status on screen is a seeded demo status, not the desk's act. */
    status_seeded: z.boolean().optional(),
    history: z.array(ReportHistoryEntrySchema).default([]),
    /** Seed reports only: the register hotspot and the bundle row the seed restates. */
    hotspot_id: z.string().nullish(),
    bundle_report_id: z.string().nullish(),
  })
  .transform((report) => {
    const predatesPhotos = report.photo_attached === undefined;
    return {
      ...report,
      photo_attached: report.photo_attached ?? report.has_photo,
      has_photo: predatesPhotos
        ? report.has_photo && Boolean(report.photo_url || report.thumb_url)
        : report.has_photo,
    };
  });
export type PublicReport = z.infer<typeof PublicReportSchema>;

/** What `GET /v1/reports/{id}` says of a dismissed report: that it was, and nothing it said. */
export const DismissedReportSchema = z.looseObject({
  id: z.string(),
  origin: z.enum(["citizen", "seed"]),
  status: z.literal("dismissed"),
  status_ts: z.string().nullish(),
  history: z.array(ReportHistoryEntrySchema),
  note: z.string(),
});
export type DismissedReport = z.infer<typeof DismissedReportSchema>;

/**
 * `GET /v1/reports` and `GET /v1/ops/reports`. The hidden counts and notes are optional so a
 * list from an API that predates them still parses, reports and all ({@link PublicReportSchema});
 * a count it did not send stays absent rather than reading 0. Such an API hid nothing: it listed
 * every report it held, with the point as stored.
 */
export const ReportListSchema = z.looseObject({
  count: z.number(),
  n_returned: z.number().optional(),
  n_dismissed_hidden: z.number().optional(),
  n_outside_hidden: z.number().optional(),
  reports: z.array(PublicReportSchema),
  notes: z.array(z.string()).optional(),
  writes_enabled: z.boolean().optional(),
});
export type ReportList = z.infer<typeof ReportListSchema>;

/**
 * `POST /v1/reports` response (202). `feedback_streets` stays null until a cycle has run.
 *
 * Only `id` is required. The report is already stored when this arrives, so an API that answers
 * with fewer fields - the deployed one can lag this build - must not turn a stored report into an
 * error on the reporter's phone.
 */
export const ReportAckSchema = z.looseObject({
  id: z.string(),
  accepted: z.boolean().optional(),
  status: StatusSchema.optional(),
  city: z.string().nullish(),
  outside_aoi: z.boolean().optional(),
  run_id: z.string().nullish(),
  streets_nearby: z.number().optional(),
  feedback_streets: z.number().int().nullish(),
  photo_attached: z.boolean().optional(),
  photo_stored: z.boolean().optional(),
  photo_note: z.string().nullish(),
  photo_url: z.string().nullish(),
  thumb_url: z.string().nullish(),
  message: z.string().nullish(),
});
export type ReportAck = z.infer<typeof ReportAckSchema>;

export const ReportStatusResultSchema = z.looseObject({
  entry: z.looseObject({ id: z.string(), ts: z.string(), status: StatusSchema }),
  city: z.string(),
  report: PublicReportSchema,
  notes: z.array(z.string()),
});
export type ReportStatusResult = z.infer<typeof ReportStatusResultSchema>;

// ---------------------------------------------------------------------------------------
// Helpers a screen prints through
// ---------------------------------------------------------------------------------------

/**
 * An `<img src>` for a report photo, or null.
 *
 * `/v1/...` is the API's own photo route and is prefixed with the API base; an `https://` link
 * is a seed's Commons image and is used as it is. Anything else - `data:`, `javascript:`, a
 * protocol-relative `//host` - is refused rather than rendered.
 */
export function resolvePhotoUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  if (url.startsWith("/v1/")) return apiUrl(url);
  if (url.startsWith("https://")) return url;
  return null;
}

/**
 * The status as the reporter reads it. A seeded demo status says it is one (rule 7): "Crew sent
 * (demo status)" is a thing a seed claims, "Crew sent" is a thing the desk did.
 */
export function statusLabel(status: ReportStatus, options: { seeded?: boolean } = {}): string {
  const label = REPORT_STATUS_LABEL[status];
  return options.seeded ? `${label} (demo status)` : label;
}

/**
 * A report's photo as an `<img src>`: `thumb` is 330 px, `full` is 1280 px for a citizen's photo
 * and 960 px for a seed's Commons link. Null when there is no photo to show, or when the URL is
 * not one {@link resolvePhotoUrl} will load. A full-size URL falls back to the thumbnail and back.
 */
export function reportPhotoUrl(
  report: Pick<PublicReport, "has_photo" | "photo_url" | "thumb_url">,
  size: "thumb" | "full" = "thumb",
): string | null {
  if (!report.has_photo) return null;
  const [first, second] =
    size === "thumb" ? [report.thumb_url, report.photo_url] : [report.photo_url, report.thumb_url];
  return resolvePhotoUrl(first) ?? resolvePhotoUrl(second);
}

/** "Photo: Hitesh Ashar, CC BY 2.0, via Wikimedia Commons" - the line under a seed's photo. */
export function photoCredit(credit: PhotoCredit | null | undefined): string | null {
  if (!credit) return null;
  const author = credit.author.trim() || "Unknown author";
  return `Photo: ${author}, ${credit.license}, via Wikimedia Commons`;
}

/** True when a data URL is too long for the API; the report flow must shrink it or drop it. */
export function photoTooLarge(dataUrl: string | null | undefined): boolean {
  return typeof dataUrl === "string" && dataUrl.length > REPORT_PHOTO_MAX_CHARS;
}

/** True for a report whose content the desk has withdrawn from the public. */
export function isDismissed(report: PublicReport | DismissedReport): report is DismissedReport {
  return report.status === "dismissed" && !("lat" in report);
}

/**
 * One report as a screen draws it: a pin, a list row or a card. Everything a screen prints is
 * already resolved - the status in words, photo URLs an `<img>` may load, the photo's credit -
 * so no screen reads the raw API body to decide what is safe to show.
 */
export interface ReportPin {
  id: string;
  lon: number;
  lat: number;
  /** "ankle", "knee" or "waist", as the reporter chose it; null when the API sent none. */
  depthHint: string | null;
  /** The centimetres Pulse assumes for that chip (SPEC.md 11.6), as the API sent them. */
  depthCm: number | null;
  status: ReportStatus;
  /** {@link statusLabel}, with "(demo status)" when the status was seeded. */
  statusLabel: string;
  /** The status is a seeded demo status, not the desk's act. */
  statusSeeded: boolean;
  /** When the water was seen, ISO 8601 with its offset. */
  ts: string | null;
  text: string | null;
  /** Where it was, in words, when the API has a name for it (seed reports carry the hotspot's). */
  place: string | null;
  /** 330 px; null without a photo. */
  thumbUrl: string | null;
  /** 1280 px for a citizen's photo, 960 px for a seed's; null without a photo. */
  photoUrl: string | null;
  /** The credit line a seed's photo must carry ({@link photoCredit}); null otherwise. */
  credit: string | null;
  /** A seeded demo report rather than one a person sent (rule 7). */
  synthetic: boolean;
  origin: "citizen" | "seed";
}

/** A report from `GET /v1/reports` (or the desk's list) as a {@link ReportPin}. */
export function reportToPin(report: PublicReport): ReportPin {
  const seeded = report.status_seeded === true;
  const thumbUrl = reportPhotoUrl(report, "thumb");
  return {
    id: report.id,
    lon: report.lon,
    lat: report.lat,
    depthHint: report.depth_hint ?? null,
    depthCm: report.depth_cm ?? null,
    status: report.status,
    statusLabel: statusLabel(report.status, { seeded }),
    statusSeeded: seeded,
    ts: report.ts ?? null,
    text: report.text ?? null,
    place: report.place ?? null,
    thumbUrl,
    photoUrl: reportPhotoUrl(report, "full"),
    // A credit belongs under a photo; with no photo shown there is nothing to credit.
    credit: thumbUrl && report.origin === "seed" ? photoCredit(report.credit) : null,
    synthetic: report.synthetic || report.origin === "seed",
    origin: report.origin,
  };
}

// ---------------------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------------------

export interface ReportQuery {
  city?: string;
  /** `[minLon, minLat, maxLon, maxLat]`. */
  bbox?: readonly [number, number, number, number];
  status?: ReportStatus;
  /** ISO 8601; only reports received at or after it. */
  since?: string;
  origin?: "citizen" | "seed";
  limit?: number;
  signal?: AbortSignal;
}

function toQuery(query: ReportQuery): Record<string, QueryValue> {
  return {
    city: query.city,
    bbox: query.bbox ? query.bbox.join(",") : undefined,
    status: query.status,
    since: query.since,
    origin: query.origin,
    limit: query.limit,
  };
}

/**
 * `GET /v1/reports`: the public list, newest first, coordinates rounded, dismissed ones gone.
 * Citizen reports and the demo seed together; `origin` picks one.
 */
export async function loadReports(query: ReportQuery = {}): Promise<ReportList> {
  return apiFetch("/v1/reports", {
    method: "GET",
    query: toQuery(query),
    signal: query.signal,
    schema: ReportListSchema,
  });
}

/** The same call as {@link loadReports}, by the name part 1 gave it. */
export const loadPublicReports = loadReports;

/** `GET /v1/reports/{id}`: what a reporter polls after pressing Send. */
export async function loadReport(
  id: string,
  options: { signal?: AbortSignal } = {},
): Promise<PublicReport | DismissedReport> {
  return apiFetch(`/v1/reports/${encodeURIComponent(id)}`, {
    method: "GET",
    signal: options.signal,
    schema: z.union([PublicReportSchema, DismissedReportSchema]),
  });
}

function deskHeaders(): Record<string, string> {
  const passphrase = readPassphrase();
  return passphrase ? { [OPS_HEADER]: passphrase } : {};
}

/**
 * `GET /v1/ops/reports`: every report with exact coordinates and officers' names, dismissed and
 * out-of-area ones included. Gated by the desk passphrase; not counted against the write window.
 * A refusal throws an `ApiError` that `opsRefusal` in `lib/api/ops` classifies.
 */
export async function loadDeskReports(query: ReportQuery = {}): Promise<ReportList> {
  return apiFetch("/v1/ops/reports", {
    method: "GET",
    query: toQuery(query),
    signal: query.signal,
    headers: deskHeaders(),
    schema: ReportListSchema,
  });
}

// ---------------------------------------------------------------------------------------
// The desk's act
// ---------------------------------------------------------------------------------------

export interface ReportStatusInput {
  reportId: string;
  status: ReportStatus;
  /** Shown to the reporter beside the status. At most 300 characters. */
  note?: string;
  /** Kept on the desk only. */
  user?: string;
  role?: OfficerRole;
}

/** `reports.ReportStatusRequest.note`'s limit. */
export const REPORT_STATUS_NOTE_MAX = 300;

function statusBody(input: Omit<ReportStatusInput, "reportId">): Record<string, string> {
  const note = input.note?.trim();
  return {
    status: input.status,
    ...(note ? { note: note.slice(0, REPORT_STATUS_NOTE_MAX) } : {}),
    ...(input.user?.trim() ? { user: input.user.trim().slice(0, 80) } : {}),
    ...(input.role ? { role: input.role } : {}),
  };
}

/**
 * `POST /v1/ops/reports/{id}/status` with the passphrase this tab holds: an append to the ops
 * log; the report is unchanged.
 */
export async function setReportStatus(input: ReportStatusInput): Promise<ReportStatusResult> {
  return apiFetch(`/v1/ops/reports/${encodeURIComponent(input.reportId)}/status`, {
    method: "POST",
    body: statusBody(input),
    headers: deskHeaders(),
    timeoutMs: 15_000,
    schema: ReportStatusResultSchema,
  });
}

/**
 * The same act with the passphrase passed in, for a screen that holds it itself. The passphrase
 * goes in the `X-Varuna-Ops` header and nowhere else - never the query string, never the body.
 * An empty one sends no header, and the API answers 401 with the words to show.
 */
export async function postReportStatus(
  id: string,
  status: ReportStatus,
  note: string | null | undefined,
  passphrase: string | null | undefined,
  options: { user?: string; role?: OfficerRole; signal?: AbortSignal } = {},
): Promise<ReportStatusResult> {
  const secret = passphrase?.trim();
  return apiFetch(`/v1/ops/reports/${encodeURIComponent(id)}/status`, {
    method: "POST",
    body: statusBody({ status, note: note ?? undefined, user: options.user, role: options.role }),
    headers: secret ? { [OPS_HEADER]: secret } : {},
    signal: options.signal,
    timeoutMs: 15_000,
    schema: ReportStatusResultSchema,
  });
}

// ---------------------------------------------------------------------------------------
// Sending a report
// ---------------------------------------------------------------------------------------

/**
 * `POST /v1/reports`: JSON with the photo as a data URL, the same body the offline service
 * worker queues, so a report sent now and one replayed later are the same request.
 *
 * A photo too long for the API is refused here, before a megabyte goes over a phone's
 * connection to be refused there; the thrown `ApiError` says what to do. The answer's `id` is
 * what {@link loadReport} polls, and `message` is written for the reporter.
 */
export async function submitReport(
  input: ReportInput,
  options: { signal?: AbortSignal } = {},
): Promise<ReportAck> {
  const body = ReportInput.parse(input);
  if (photoTooLarge(body.photo_data_url)) {
    throw new ApiError({
      code: "report_photo_too_large",
      status: 0,
      path: "/v1/reports",
      message:
        "This photo is too large to send. Nothing was sent. Send the report without it, or " +
        "with a smaller photo.",
    });
  }
  return apiFetch("/v1/reports", {
    method: "POST",
    body,
    signal: options.signal,
    timeoutMs: 20_000,
    schema: ReportAckSchema,
  });
}

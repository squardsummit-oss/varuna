/**
 * The reports this browser sent, so the dashboard can follow what the ward desk does with them.
 *
 * Stored under `varuna.my-reports` as a JSON array of `{ id, sentAt }`, newest first, at most
 * {@link MY_REPORTS_MAX} entries. Only an id the API answered with is kept: a report the offline
 * service worker queued has no id yet, and nothing here may pretend it reached VARUNA.
 *
 * Every read and write is wrapped, because storage can be missing or refuse (a private window,
 * blocked site data, a full quota). A failure reads as "no reports remembered", never as an error
 * on the reporter's screen - the report itself is already at the API.
 */

export const MY_REPORTS_KEY = "varuna.my-reports";
export const MY_REPORTS_MAX = 20;

export interface MyReport {
  /** The id `POST /v1/reports` answered with, as `GET /v1/reports/{id}` takes it. */
  id: string;
  /** When this browser sent it, ISO 8601. */
  sentAt: string;
}

/** Report ids the API mints are short and plain; anything else in storage is not ours. */
const ID_PATTERN = /^[A-Za-z0-9_-]{1,80}$/;

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function isMyReport(value: unknown): value is MyReport {
  if (!value || typeof value !== "object") return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry.id === "string" &&
    ID_PATTERN.test(entry.id) &&
    typeof entry.sentAt === "string" &&
    entry.sentAt.length <= 40
  );
}

/** The remembered reports, newest first; empty when there are none or storage is unavailable. */
export function readMyReports(): MyReport[] {
  const store = storage();
  if (!store) return [];
  try {
    const raw = store.getItem(MY_REPORTS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isMyReport).slice(0, MY_REPORTS_MAX) : [];
  } catch {
    return [];
  }
}

/**
 * Remember one sent report. Returns false when the id is not one the API mints or storage
 * refused the write; the caller has nothing to show for either.
 */
export function rememberMyReport(id: string, sentAt: string = new Date().toISOString()): boolean {
  if (!ID_PATTERN.test(id)) return false;
  const store = storage();
  if (!store) return false;
  const next = [{ id, sentAt }, ...readMyReports().filter((entry) => entry.id !== id)].slice(
    0,
    MY_REPORTS_MAX,
  );
  try {
    store.setItem(MY_REPORTS_KEY, JSON.stringify(next));
    return true;
  } catch {
    return false;
  }
}

/**
 * Small IST time helpers shared by the replay store and the chrome. Every time on screen is IST,
 * 24-hour (SPEC.md section 6.3). Kept next to the stores so they stay dependency-free.
 */

export const IST_OFFSET = "+05:30";
export const IST_TIME_ZONE = "Asia/Kolkata";

const hhmm = new Intl.DateTimeFormat("en-GB", {
  timeZone: IST_TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const dayMonthYear = new Intl.DateTimeFormat("en-GB", {
  timeZone: IST_TIME_ZONE,
  day: "numeric",
  month: "short",
  year: "numeric",
});

/** Parses an ISO 8601 string; returns null for anything Date cannot read. */
export function parseIso(iso: string): Date | null {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Adds minutes to an ISO timestamp and returns an ISO string with the IST offset. */
export function addMinutesIso(iso: string, minutes: number): string {
  const d = parseIso(iso);
  if (!d) return iso;
  return toIstIso(new Date(d.getTime() + minutes * 60_000));
}

/** Formats a Date as ISO 8601 with the +05:30 offset (what the API and run.json use). */
export function toIstIso(date: Date): string {
  const shifted = new Date(date.getTime() + 330 * 60_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}` +
    `T${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}:${pad(shifted.getUTCSeconds())}${IST_OFFSET}`
  );
}

/** "17:40" in IST for an ISO timestamp; "--:--" when unreadable. */
export function formatIstTime(iso: string): string {
  const d = parseIso(iso);
  return d ? hhmm.format(d) : "--:--";
}

/** "2 Jul 2019" in IST for an ISO timestamp. */
export function formatIstDate(iso: string): string {
  const d = parseIso(iso);
  return d ? dayMonthYear.format(d) : "";
}

/** "+40 min", "-15 min" or "+0 min": a lead relative to the cycle time. */
export function formatLead(leadMin: number): string {
  const sign = leadMin < 0 ? "-" : "+";
  return `${sign}${Math.abs(Math.round(leadMin))} min`;
}

/** "18:20 (+40 min)": the valid time and its lead, as the copy rules require. */
export function formatValidTime(cycleIso: string, leadMin: number): string {
  return `${formatIstTime(addMinutesIso(cycleIso, leadMin))} (${formatLead(leadMin)})`;
}

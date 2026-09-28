/**
 * Number, time and unit formatting for every string the console shows (SPEC.md section 6.3
 * and 6.8): times are IST 24-hour, depth is "cm", lead is "+40 min", probability is "82 %",
 * and every number carries its unit. All helpers are pure and safe to call on the server.
 */

/** Every time on screen is Indian Standard Time. */
export const IST_TIME_ZONE = "Asia/Kolkata";

/** Rendered when a value is missing or unparseable; never an empty string so layouts hold. */
export const MISSING = "—";

const timeFormatter = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: IST_TIME_ZONE,
});

const timeWithSecondsFormatter = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
  timeZone: IST_TIME_ZONE,
});

const dateFormatter = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: IST_TIME_ZONE,
});

const longDateFormatter = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "long",
  year: "numeric",
  timeZone: IST_TIME_ZONE,
});

const integerFormatter = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const oneDecimalFormatter = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const twoDecimalFormatter = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export type DateInput = string | number | Date | null | undefined;

/** Parses an ISO string, epoch or Date; returns null for anything invalid. */
export function toDate(input: DateInput): Date | null {
  if (input === null || input === undefined || input === "") return null;
  const date = input instanceof Date ? input : new Date(input);
  return Number.isNaN(date.getTime()) ? null : date;
}

function isFinite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * A bundle window as the replay cards and the panel say it: "2 July 2019, 05:40 to 09:40 IST".
 * A design storm has a nominal day, so the date is still shown - it is what the clock walks.
 */
export function bundleWindowLabel(t0: DateInput, t1: DateInput): string {
  const from = toDate(t0);
  const to = toDate(t1);
  if (!from || !to) return MISSING;
  return `${longDateFormatter.format(from)}, ${timeFormatter.format(from)} to ${timeFormatter.format(to)} IST`;
}

/** "18:20" in IST. `seconds` adds ":05" for log streams. */
export function formatIst(input: DateInput, options: { seconds?: boolean } = {}): string {
  const date = toDate(input);
  if (!date) return MISSING;
  return (options.seconds ? timeWithSecondsFormatter : timeFormatter).format(date);
}

/** "2 Jul 2019" in IST. */
export function formatDate(input: DateInput): string {
  const date = toDate(input);
  if (!date) return MISSING;
  return dateFormatter.format(date);
}

/** "2 Jul 2019 17:40 IST". */
export function formatDateTime(input: DateInput): string {
  const date = toDate(input);
  if (!date) return MISSING;
  return `${dateFormatter.format(date)} ${timeFormatter.format(date)} IST`;
}

/** "+40 min", "-15 min", "+0 min". Always signed so a lead never reads as a clock time. */
export function formatLead(minutes: number | null | undefined): string {
  if (!isFinite(minutes)) return MISSING;
  const rounded = Math.round(minutes);
  const sign = rounded < 0 ? "-" : "+";
  return `${sign}${Math.abs(rounded)} min`;
}

/** "18:20 (+40 min)". */
export function formatTimeWithLead(
  input: DateInput,
  leadMinutes: number | null | undefined,
): string {
  const time = formatIst(input);
  if (time === MISSING) return MISSING;
  return `${time} (${formatLead(leadMinutes)})`;
}

/** "45 cm". Rounds to whole centimetres; negative depth is clamped to 0. */
export function formatCm(cm: number | null | undefined): string {
  if (!isFinite(cm)) return MISSING;
  return `${integerFormatter.format(Math.max(0, Math.round(cm)))} cm`;
}

/**
 * "5.1 cm": one decimal, for a comparison against a stated tolerance. Whole centimetres are right
 * for a depth on a street, and wrong beside a 5 cm tolerance: 5.07 cm rounded to "5 cm" next to
 * "Outside tolerance" reads as a contradiction.
 */
export function formatCmPrecise(cm: number | null | undefined): string {
  if (!isFinite(cm)) return MISSING;
  return `${oneDecimalFormatter.format(Math.max(0, cm))} cm`;
}

/** "+3.4 cm" / "−1.1 cm": a signed change in depth, where the direction is the point. */
export function formatCmSigned(cm: number | null | undefined): string {
  if (!isFinite(cm)) return MISSING;
  const rounded = Math.round(cm * 10) / 10;
  if (rounded === 0) return "0.0 cm";
  const sign = rounded > 0 ? "+" : "−";
  return `${sign}${oneDecimalFormatter.format(Math.abs(rounded))} cm`;
}

/** "55 → 20 cm" for before/after depth pairs (drain X-ray, what-if). */
export function formatCmDelta(beforeCm: number, afterCm: number): string {
  if (!isFinite(beforeCm) || !isFinite(afterCm)) return MISSING;
  return `${Math.max(0, Math.round(beforeCm))} → ${Math.max(0, Math.round(afterCm))} cm`;
}

/**
 * "82 %". Takes a fraction in [0, 1] by default (P(> 30 cm) = 0.82); pass `{ fraction: false }`
 * for values already in percent. Clamps to 0-100 and rounds to a whole number.
 */
export function formatPct(
  value: number | null | undefined,
  options: { fraction?: boolean } = {},
): string {
  if (!isFinite(value)) return MISSING;
  const percent = options.fraction === false ? value : value * 100;
  const clamped = Math.min(100, Math.max(0, percent));
  return `${integerFormatter.format(Math.round(clamped))} %`;
}

/** The Twin's mass-balance budget: the coupled run must conserve to within 0.1 % (SPEC.md 11.3). */
export const MASS_BALANCE_BUDGET = 0.001;

/**
 * A mass-balance error, to the precision the budget is judged at.
 *
 * `formatPct` rounds to whole percent, which is right for "82 % likely" and useless here: a run
 * conserving to 3.7e-04 rendered as "0 %", so the cycle log said nothing at all about the number
 * it exists to report. Three significant figures below a tenth of a percent, so 0.037 % reads as
 * comfortably inside a 0.1 % budget rather than as zero.
 */
export function formatMassBalance(value: number | null | undefined): string {
  if (value === null || value === undefined || !isFinite(value)) return MISSING;
  const percent = Math.abs(value) * 100;
  if (percent === 0) return "0 %";
  if (percent < 0.001) return "< 0.001 %";
  if (percent < 1) return `${percent.toPrecision(2)} %`;
  return `${percent.toFixed(1)} %`;
}

/** "820 ms" below a second, "3.9 s" above, "1 min 05 s" above a minute (cycle budget bar). */
export function formatMs(ms: number | null | undefined): string {
  if (!isFinite(ms)) return MISSING;
  const rounded = Math.max(0, ms);
  if (rounded < 1000) return `${integerFormatter.format(Math.round(rounded))} ms`;
  if (rounded < 60_000) return `${oneDecimalFormatter.format(rounded / 1000)} s`;
  const minutes = Math.floor(rounded / 60_000);
  const seconds = Math.round((rounded % 60_000) / 1000);
  return `${minutes} min ${String(seconds).padStart(2, "0")} s`;
}

/** "850 m" below a kilometre, "2.4 km" above. Input is metres. */
export function formatKm(metres: number | null | undefined): string {
  if (!isFinite(metres)) return MISSING;
  const rounded = Math.max(0, metres);
  if (rounded < 1000) return `${integerFormatter.format(Math.round(rounded))} m`;
  return `${oneDecimalFormatter.format(rounded / 1000)} km`;
}

/** "25 min", "3 h", "1 h 20 min". Used for ETAs and lead-time gains. */
export function formatMinutes(minutes: number | null | undefined): string {
  if (!isFinite(minutes)) return MISSING;
  const rounded = Math.max(0, Math.round(minutes));
  if (rounded < 60) return `${rounded} min`;
  const hours = Math.floor(rounded / 60);
  const rest = rounded % 60;
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

/** "0 s", "42 s", "1 min 05 s", "3 min" for onboarding elapsed time. Input is seconds. */
export function formatSeconds(seconds: number | null | undefined): string {
  if (!isFinite(seconds)) return MISSING;
  const rounded = Math.max(0, Math.round(seconds));
  if (rounded < 60) return `${rounded} s`;
  const minutes = Math.floor(rounded / 60);
  const rest = rounded % 60;
  return rest === 0 ? `${minutes} min` : `${minutes} min ${String(rest).padStart(2, "0")} s`;
}

/** "0.42" for a posterior blockage beta or clogging kappa. */
export function formatBeta(beta: number | null | undefined): string {
  if (!isFinite(beta)) return MISSING;
  return twoDecimalFormatter.format(Math.min(1, Math.max(0, beta)));
}

/** "0.42 ± 0.08" for a posterior mean with its standard deviation. */
export function formatBetaWithSd(mean: number, sd: number): string {
  if (!isFinite(mean) || !isFinite(sd)) return MISSING;
  return `${formatBeta(mean)} ± ${twoDecimalFormatter.format(Math.max(0, sd))}`;
}

/** "30×" for replay speed. */
export function formatSpeed(multiplier: number | null | undefined): string {
  if (!isFinite(multiplier)) return MISSING;
  return `${integerFormatter.format(Math.round(multiplier))}×`;
}

/** "1,20,000" in Indian grouping for counts (pumps m³/h, cells, segments). */
export function formatCount(value: number | null | undefined): string {
  if (!isFinite(value)) return MISSING;
  return integerFormatter.format(Math.round(value));
}

/** "0.71" for skill scores (CSI, POD, FAR, Brier, AUC). */
export function formatScore(value: number | null | undefined): string {
  if (!isFinite(value)) return MISSING;
  return twoDecimalFormatter.format(value);
}

/** "MUM-20190702T0640-sky1.0-twin1.0-flash0.3-baked" shortened for chips: keeps head and tail. */
export function shortenRunId(runId: string | null | undefined, max = 28): string {
  if (!runId) return MISSING;
  if (runId.length <= max) return runId;
  const head = Math.ceil((max - 1) / 2);
  const tail = Math.floor((max - 1) / 2);
  return `${runId.slice(0, head)}…${runId.slice(runId.length - tail)}`;
}

/** Minutes between two instants, positive when `to` is later. */
export function minutesBetween(from: DateInput, to: DateInput): number | null {
  const a = toDate(from);
  const b = toDate(to);
  if (!a || !b) return null;
  return Math.round((b.getTime() - a.getTime()) / 60_000);
}

/** Adds minutes to an instant and returns the ISO string with the +05:30 offset. */
export function addMinutesIso(input: DateInput, minutes: number): string | null {
  const date = toDate(input);
  if (!date || !isFinite(minutes)) return null;
  return toIstIso(new Date(date.getTime() + minutes * 60_000));
}

/** ISO 8601 with the +05:30 offset, e.g. "2019-07-02T06:40:00+05:30" (SPEC.md section 12). */
export function toIstIso(input: DateInput): string | null {
  const date = toDate(input);
  if (!date) return null;
  const offsetMs = 5.5 * 60 * 60 * 1000;
  const shifted = new Date(date.getTime() + offsetMs);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}` +
    `T${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}:${pad(shifted.getUTCSeconds())}+05:30`
  );
}

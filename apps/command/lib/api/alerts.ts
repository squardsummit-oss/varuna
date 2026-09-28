/**
 * The alert queue a run raised (`GET /v1/alerts`, `GET /v1/alerts/{id}.cap`; SPEC.md 11.10).
 *
 * Alerts are computed when the cycle runs, not when the page opens, so the queue, the map and
 * the hotspot rail are always describing the same forecast.
 */

import { api, apiUrl } from "@/lib/api/client";
import { formatIst, formatTimeWithLead, minutesBetween, toIstIso } from "@/lib/format";

export type AlertLevel = "severe" | "moderate" | "watch";

/**
 * Where an alert has got to with the people who have to act on it.
 *
 * `raised` is the cycle's own answer and is what the product carries. The other two come from
 * the ops log, folded onto the queue at read time, so they survive a reload *and* the change of
 * cycle that renames every alert (see `lib/alert-identity.ts`).
 */
export type AlertState = "raised" | "acknowledged" | "escalated";

/** One thing the desk did to an alert, oldest first. */
export interface AlertAction {
  ts: string;
  state: AlertState;
  user: string;
  note: string | null;
}

export interface RunAlert {
  id: string;
  runId: string;
  level: AlertLevel;
  thresholdCm: number;
  headline: string;
  instruction: string | null;
  areaDesc: string;
  /**
   * The place without the sentence around it ("Pipeline Road"), for a row that lays itself out.
   * Null on a run baked before the product carried it; `areaDesc` is then the place.
   */
  name: string | null;
  /**
   * "near Hindmata junction": the closest registered hotspot within 1.5 km of a street the
   * register does not name, since no Mumbai segment carries a ward. Null for a register alert,
   * for a street with no hotspot that close, and on older runs.
   */
  locality: string | null;
  /** "hotspot" for a chronic spot from the register, "segment" for any other street. */
  scope: string;
  hotspotId: string | null;
  lon: number | null;
  lat: number | null;
  peakCm: number;
  windowFrom: string;
  /** The last step over the threshold, which is the forecast horizon when `windowOpenEnded`. */
  windowTo: string;
  /**
   * True when the place is still over its threshold at the forecast's last step, so `windowTo`
   * is where the forecast stops, not where the water goes. Null on a run baked before the product
   * said so.
   */
  windowOpenEnded: boolean | null;
  /**
   * Of `membersTotal` ensemble members, how many also keep the street over this level's
   * threshold for two steps in a row. Reported beside the raise, never used for it: every alert
   * is raised on the Twin's own run. Null when the run carried no member count.
   */
  membersAbove: number | null;
  membersTotal: number | null;
  raisedTs: string;
  /**
   * Consecutive cycles the level has held (SPEC.md 11.10): the Twin's street depth above the
   * threshold for two 5-minute steps in a row, cycle after cycle. A run baked before the
   * cross-cycle rule counts forecast steps instead, and `persistsUnit` says which.
   */
  persistsCycles: number;
  persistsUnit: string;
  /** The cycle the level first crossed its threshold (one cycle before it raised); null on old runs. */
  firstSeenTs: string | null;
  /** When this cycle's document went out; the raise time is kept in `raisedTs`. */
  sentTs: string | null;
  /** Escalation tiers this level reaches when raised, from config/escalation.yaml. */
  notify: string[];
  /** Pumps the desk dispatched to this place, and the sentence the phone carries for them. */
  pumps: string[];
  dispatchNote: string | null;
  /**
   * The product's `trigger_p`. 1.0 on every alert: the raise is the Twin's deterministic run, so
   * this is not a probability and no screen prints it. `membersAbove` is the ensemble's word.
   */
  triggerP: number;
  /** "Exercise" on every replay alert — the document says on its face that it is a drill. */
  capStatus: string;
  sourceUrl: string | null;
  /** The desk's state, overlaid on the product by the API. `raised` until someone acts. */
  state: AlertState;
  /** Who acknowledged it; kept even after an escalation, so the trail is not lost. */
  acknowledgedBy: string | null;
  acknowledgedTs: string | null;
  /** The step of the escalation matrix it was sent to, when it was escalated. */
  escalatedTo: string | null;
  /** Every action taken on it, oldest first; empty when nobody has touched it. */
  history: AlertAction[];
}

/** A level that crossed its threshold this cycle for the first time and raises next cycle if it holds. */
export interface PendingAlert {
  id: string;
  level: AlertLevel;
  headline: string;
  areaDesc: string;
  name: string | null;
  locality: string | null;
  scope: string;
  peakCm: number;
  membersAbove: number | null;
  membersTotal: number | null;
  sinceTs: string;
  /**
   * The level the place is already raised at, or null when this would be its first. Only
   * meaningful when `raisedLevelKnown`: an API that predates the field cannot say, and a place
   * with no listed row may then be raised below the queue's cap.
   */
  raisedLevel: AlertLevel | null;
  raisedLevelKnown: boolean;
}

/** A level that was raised and fell to P <= 0.3 this cycle. */
export interface ClearedAlert {
  situation: string;
  level: AlertLevel;
  name: string | null;
  areaDesc: string;
  raisedTs: string | null;
  clearedTs: string;
  persistsCycles: number;
}

/** The product lists at most this many alerts, and this many pending places, worst first. */
export const MAX_LISTED = 60;

export interface AlertSet {
  runId: string;
  cycleTs: string | null;
  alerts: RunAlert[];
  /**
   * Every alert the cycle raised, uncapped: at 08:40 on 2 July, 213 against the 60 listed. Null
   * when the API does not say, and `capped` then means only that the list is full.
   */
  nRaised: number | null;
  /** `nRaised` by each alert's worst raised level: Severe 13, Moderate 35, Watch 165 at 08:40. */
  nRaisedByLevel: Record<AlertLevel, number> | null;
  /** True when the list may be missing raised alerts: more raised than listed, or a full list. */
  capped: boolean;
  /**
   * The earliest window over every raised alert. Null when the API does not carry it, which a
   * capped list cannot stand in for: the cap keeps the worst levels, not the earliest.
   */
  firstOnset: { ts: string; place: string; level: AlertLevel } | null;
  pending: PendingAlert[];
  nPending: number;
  /** Of `nPending`, places raised at nothing yet, and places going up from a raised level. */
  nPendingNew: number | null;
  nPendingStepUp: number | null;
  cleared: ClearedAlert[];
  nCleared: number;
  /**
   * True when the run was baked under the cross-cycle rule. False on a run written before it,
   * whose queue decided on one cycle and counts persistence in forecast steps.
   */
  crossCycle: boolean;
  previousRunId: string | null;
  notes: string[];
}

interface RawAlert {
  id?: string;
  run_id?: string;
  level?: string;
  threshold_cm?: number;
  headline?: string;
  instruction?: string | null;
  area_desc?: string;
  name?: string | null;
  locality?: string | null;
  scope?: string;
  hotspot_id?: string | null;
  lon?: number | null;
  lat?: number | null;
  peak_cm?: number;
  window_from?: string;
  window_to?: string;
  window_open_ended?: boolean | null;
  members_above?: number | null;
  members_total?: number | null;
  raised_ts?: string;
  persists_cycles?: number;
  persists_unit?: string;
  first_seen_ts?: string | null;
  sent_ts?: string | null;
  notify?: string[];
  pumps?: string[];
  dispatch_note?: string | null;
  trigger_p?: number;
  cap_status?: string;
  source_url?: string | null;
  state?: string;
  acknowledged_by?: string | null;
  acknowledged_ts?: string | null;
  escalated_to?: string | null;
  history?: {
    ts?: string;
    state?: string;
    user?: string;
    note?: string | null;
  }[];
}

const LEVELS: readonly string[] = ["severe", "moderate", "watch"];
const STATES: readonly string[] = ["raised", "acknowledged", "escalated"];

interface RawPending {
  id?: string;
  level?: string;
  headline?: string;
  area_desc?: string;
  name?: string | null;
  locality?: string | null;
  scope?: string;
  peak_cm?: number;
  members_above?: number | null;
  members_total?: number | null;
  since_ts?: string;
  raised_level?: string | null;
}

interface RawCleared {
  situation?: string;
  level?: string;
  name?: string | null;
  area_desc?: string;
  raised_ts?: string | null;
  cleared_ts?: string;
  persists_cycles?: number;
}

function toLevel(raw: string | undefined): AlertLevel {
  return (LEVELS.includes(raw ?? "") ? raw : "watch") as AlertLevel;
}

/** An unknown state is read as `raised`: never claim an alert has been seen when it has not. */
function toState(raw: string | undefined): AlertState {
  return (STATES.includes(raw ?? "") ? raw : "raised") as AlertState;
}

/**
 * `?run_id=&city=` for the alert routes. A run id names its own city, so `city` only decides
 * which run is newest when no run is named; it is sent anyway so a city the API does not know is
 * refused rather than answered with Mumbai's queue.
 */
function alertQuery(runId?: string, city?: string): string {
  const params = new URLSearchParams();
  if (runId) params.set("run_id", runId);
  if (city) params.set("city", city);
  const query = params.toString();
  return query ? `?${query}` : "";
}

/**
 * Fetch a run's alerts. Returns null when the run predates the alert product.
 *
 * `city` is the screen's `?city=`; omitted, the API's own city stands, which is Mumbai.
 */
export async function loadAlerts(
  runId?: string,
  signal?: AbortSignal,
  city?: string,
): Promise<AlertSet | null> {
  const response = await fetch(apiUrl(`/v1/alerts${alertQuery(runId, city)}`), { signal });
  if (!response.ok) {
    if (response.status === 404) return null;
    throw new Error(`Alerts failed: HTTP ${response.status}`);
  }
  const body = (await response.json()) as {
    run_id?: string;
    cycle_ts?: string | null;
    alerts?: RawAlert[];
    n_raised?: number | null;
    n_raised_by_level?: Partial<Record<string, number>> | null;
    first_onset?: { ts?: string; name?: string | null; level?: string } | null;
    pending?: RawPending[];
    n_pending?: number;
    n_pending_new?: number | null;
    n_pending_step_up?: number | null;
    cleared?: RawCleared[];
    n_cleared?: number;
    hysteresis?: { previous_run_id?: string | null } | null;
    notes?: string[];
  };

  const pending = (body.pending ?? []).map((p, i) => ({
    id: p.id ?? `pending-${i}`,
    level: toLevel(p.level),
    headline: p.headline ?? "",
    areaDesc: p.area_desc ?? "",
    name: p.name ?? null,
    locality: p.locality ?? null,
    scope: p.scope ?? "segment",
    peakCm: p.peak_cm ?? 0,
    membersAbove: p.members_above ?? null,
    membersTotal: p.members_total ?? null,
    sinceTs: p.since_ts ?? "",
    raisedLevel: p.raised_level ? toLevel(p.raised_level) : null,
    raisedLevelKnown: p.raised_level !== undefined,
  }));
  const cleared = (body.cleared ?? []).map((c, i) => ({
    situation: c.situation ?? `cleared-${i}`,
    level: toLevel(c.level),
    name: c.name ?? null,
    areaDesc: c.area_desc ?? "",
    raisedTs: c.raised_ts ?? null,
    clearedTs: c.cleared_ts ?? "",
    persistsCycles: c.persists_cycles ?? 0,
  }));

  const listed = body.alerts?.length ?? 0;
  const nRaised = typeof body.n_raised === "number" ? body.n_raised : null;
  const byLevel = body.n_raised_by_level;
  const onset = body.first_onset;

  return {
    runId: body.run_id ?? "",
    cycleTs: body.cycle_ts ?? null,
    nRaised,
    nRaisedByLevel: byLevel
      ? { severe: byLevel.severe ?? 0, moderate: byLevel.moderate ?? 0, watch: byLevel.watch ?? 0 }
      : null,
    capped: nRaised !== null ? nRaised > listed : listed >= MAX_LISTED,
    firstOnset: onset?.ts
      ? { ts: onset.ts, place: onset.name ?? "", level: toLevel(onset.level) }
      : null,
    pending,
    nPending: body.n_pending ?? pending.length,
    nPendingNew: typeof body.n_pending_new === "number" ? body.n_pending_new : null,
    nPendingStepUp: typeof body.n_pending_step_up === "number" ? body.n_pending_step_up : null,
    cleared,
    nCleared: body.n_cleared ?? cleared.length,
    crossCycle: Boolean(body.hysteresis),
    previousRunId: body.hysteresis?.previous_run_id ?? null,
    notes: body.notes ?? [],
    alerts: (body.alerts ?? []).map((a, i) => ({
      id: a.id ?? `alert-${i}`,
      runId: a.run_id ?? "",
      level: toLevel(a.level),
      thresholdCm: a.threshold_cm ?? 15,
      headline: a.headline ?? "",
      instruction: a.instruction ?? null,
      areaDesc: a.area_desc ?? "",
      name: a.name ?? null,
      locality: a.locality ?? null,
      scope: a.scope ?? (a.hotspot_id ? "hotspot" : "segment"),
      hotspotId: a.hotspot_id ?? null,
      lon: a.lon ?? null,
      lat: a.lat ?? null,
      peakCm: a.peak_cm ?? 0,
      windowFrom: a.window_from ?? "",
      windowTo: a.window_to ?? "",
      windowOpenEnded: a.window_open_ended ?? null,
      membersAbove: a.members_above ?? null,
      membersTotal: a.members_total ?? null,
      raisedTs: a.raised_ts ?? "",
      persistsCycles: a.persists_cycles ?? 1,
      persistsUnit: a.persists_unit ?? "forecast steps of 5 minutes",
      firstSeenTs: a.first_seen_ts ?? null,
      sentTs: a.sent_ts ?? null,
      notify: a.notify ?? [],
      pumps: a.pumps ?? [],
      dispatchNote: a.dispatch_note ?? null,
      triggerP: a.trigger_p ?? 1,
      capStatus: a.cap_status ?? "Exercise",
      sourceUrl: a.source_url ?? null,
      state: toState(a.state),
      acknowledgedBy: a.acknowledged_by ?? null,
      acknowledgedTs: a.acknowledged_ts ?? null,
      escalatedTo: a.escalated_to ?? null,
      history: (a.history ?? []).map((h) => ({
        ts: h.ts ?? "",
        state: toState(h.state),
        user: h.user ?? "unknown",
        note: h.note ?? null,
      })),
    })),
  };
}

/** The CAP 1.2 document for one alert, as XML text. */
export async function loadCap(
  alertId: string,
  runId?: string,
  signal?: AbortSignal,
  city?: string,
): Promise<string> {
  const query = alertQuery(runId, city);
  const response = await fetch(apiUrl(`/v1/alerts/${encodeURIComponent(alertId)}.cap${query}`), {
    signal,
  });
  if (!response.ok) throw new Error(`CAP failed: HTTP ${response.status}`);
  return response.text();
}

/**
 * The unit a card counts persistence in, singular, from what the API said it counted.
 *
 * "cycles" on a run baked under SPEC.md 11.10's cross-cycle rule; "forecast steps of 5
 * minutes" on one baked before it. The card pluralises it, so it is handed the singular.
 */
export function persistenceUnit(persistsUnit: string): string {
  return persistsUnit.startsWith("cycle") ? "cycle" : "forecast step";
}

// ---------------------------------------------------------------------------------------------
// Escalation matrix, sender and delivery log (SPEC.md 7.5)
// ---------------------------------------------------------------------------------------------

/** One step of `config/escalation.yaml`, ward officer to public. */
export interface EscalationStep {
  id: string;
  recipient: string;
  trigger: string;
  channel: string;
  /** Alert levels that reach this step when raised; empty when only an escalation does. */
  levels: AlertLevel[];
}

/** The matrix as the API serves it from `config/escalation.yaml`. */
export async function loadEscalation(signal?: AbortSignal): Promise<EscalationStep[]> {
  const body = await api.get<{
    tiers?: {
      id?: string;
      recipient?: string;
      trigger?: string;
      channel?: string;
      levels?: string[];
    }[];
  }>("/v1/alerts/escalation", { signal });
  return (body.tiers ?? []).map((t, i) => ({
    id: t.id ?? `tier-${i}`,
    recipient: t.recipient ?? "",
    trigger: t.trigger ?? "",
    channel: t.channel ?? "",
    levels: (t.levels ?? []).map((l) => toLevel(l)),
  }));
}

/**
 * The next step up the matrix for an alert: past every step its level already reached and any
 * step it was escalated to. Null at the top, where there is nobody left to tell.
 */
export function nextEscalation(
  alert: Pick<RunAlert, "notify" | "escalatedTo">,
  steps: readonly EscalationStep[],
): EscalationStep | null {
  const reached = new Set([...alert.notify, ...(alert.escalatedTo ? [alert.escalatedTo] : [])]);
  let top = -1;
  steps.forEach((step, i) => {
    if (reached.has(step.id)) top = i;
  });
  return steps[top + 1] ?? null;
}

/** Whether a real phone sender is configured where the API runs. Never a key, never a number. */
export interface SenderStatus {
  configured: boolean;
  provider: string | null;
  channel: string | null;
  toMasked: string | null;
}

export async function loadSender(signal?: AbortSignal): Promise<SenderStatus> {
  const body = await api.get<{
    configured?: boolean;
    provider?: string | null;
    channel?: string | null;
    to_masked?: string | null;
  }>("/v1/alerts/sender", { signal });
  return {
    configured: body.configured === true,
    provider: body.provider ?? null,
    channel: body.channel ?? null,
    toMasked: body.to_masked ?? null,
  };
}

/** One line of the delivery log: a mock render or a real attempt, in the API's own words. */
export interface DeliveryRow {
  id: string;
  alertId: string;
  /** "Dashboard", "WhatsApp mock", "SMS mock", "Real send (twilio)". */
  label: string;
  kind: "mock" | "real";
  /** "Shown on the alert queue", "Rendered, not sent", "Sent", "Failed", "Refused". */
  status: string;
  ts: string;
  toMasked: string | null;
  error: string | null;
  user: string | null;
  /**
   * What the channel carried, rendered by the API from the alert (`notify.whatsapp_text`,
   * `notify.sms_text`): the WhatsApp card on the WhatsApp row, the SMS on the SMS row, null on the
   * dashboard row. Null on an API that predates it.
   */
  text: string | null;
}

export interface DeliveryLog {
  runId: string;
  rows: DeliveryRow[];
  nReal: number;
  notes: string[];
}

/**
 * The delivery log for a run's queue. `limit` counts alerts, in queue order, not rows: each alert
 * brings three mock renders plus any real attempt. The alert centre asks for the whole queue (the
 * product caps it at 60) and filters per alert, because the endpoint has no `alert_id` filter.
 */
export async function loadDelivery(
  runId?: string,
  signal?: AbortSignal,
  limit = 20,
  city?: string,
): Promise<DeliveryLog> {
  const body = await api.get<{
    run_id?: string;
    n_real?: number;
    notes?: string[];
    rows?: {
      id?: string;
      alert_id?: string;
      label?: string;
      kind?: string;
      status?: string;
      ts?: string | null;
      to_masked?: string | null;
      error?: string | null;
      user?: string | null;
      text?: string | null;
    }[];
  }>("/v1/alerts/delivery", { query: { run_id: runId, limit, city }, signal });
  return {
    runId: body.run_id ?? "",
    nReal: body.n_real ?? 0,
    notes: body.notes ?? [],
    rows: (body.rows ?? []).map((r, i) => ({
      id: r.id ?? `row-${i}`,
      alertId: r.alert_id ?? "",
      label: r.label ?? "",
      kind: r.kind === "real" ? "real" : "mock",
      status: r.status ?? "",
      ts: r.ts ?? "",
      toMasked: r.to_masked ?? null,
      error: r.error ?? null,
      user: r.user ?? null,
      text: r.text ?? null,
    })),
  };
}

// ---------------------------------------------------------------------------------------------
// How the alert centre reads a queue (SPEC.md 7.5). Pure, so the rows and their tests agree.
// ---------------------------------------------------------------------------------------------

/** Minutes from the cycle to the forecast's last step: 36 steps of 5 minutes (SPEC.md 11.1). */
export const FORECAST_HORIZON_MIN = 180;

const LEVEL_RANK: Record<AlertLevel, number> = { severe: 0, moderate: 1, watch: 2 };

/** The place an alert is about, without the sentence around it. */
export function alertPlace(alert: Pick<RunAlert, "name" | "areaDesc">): string {
  return alert.name ?? alert.areaDesc;
}

/**
 * True when the place is still over its threshold where the forecast stops, so the window's end
 * is the horizon rather than the water going. The product says so on runs baked since the copy
 * change; an older run is read by comparing `windowTo` with the cycle's horizon.
 */
export function isOpenEnded(
  alert: Pick<RunAlert, "windowOpenEnded" | "windowTo">,
  cycleTs: string | null,
): boolean {
  if (alert.windowOpenEnded !== null) return alert.windowOpenEnded;
  if (!cycleTs) return false;
  const lead = minutesBetween(cycleTs, alert.windowTo);
  return lead !== null && lead >= FORECAST_HORIZON_MIN;
}

/**
 * The row's window: "09:20 (+40 min) until the end of the forecast", or "09:20 (+40 min) to
 * 10:10" when the forecast has the water going below the threshold before it stops.
 */
export function alertWindowLine(
  alert: Pick<RunAlert, "windowFrom" | "windowTo" | "windowOpenEnded">,
  cycleTs: string | null,
): string {
  const lead = cycleTs ? minutesBetween(cycleTs, alert.windowFrom) : null;
  const from =
    lead === null ? formatIst(alert.windowFrom) : formatTimeWithLead(alert.windowFrom, lead);
  return isOpenEnded(alert, cycleTs)
    ? `${from} until the end of the forecast`
    : `${from} to ${formatIst(alert.windowTo)}`;
}

export type AlertStatusKind = "new" | "held" | "acknowledged" | "escalated";

/** A step's recipient in running text: "control room", not "Control room". */
export function recipientInText(recipient: string): string {
  return recipient ? recipient.charAt(0).toLowerCase() + recipient.slice(1) : recipient;
}

/**
 * The row's status pill, from the desk's state first and the hysteresis second: "Escalated to
 * control room", "Acknowledged 08:52", "New" (raised on this cycle), "Held 3 cycles".
 */
export function alertStatus(
  alert: Pick<
    RunAlert,
    | "state"
    | "escalatedTo"
    | "acknowledgedBy"
    | "acknowledgedTs"
    | "raisedTs"
    | "persistsCycles"
    | "persistsUnit"
  >,
  cycleTs: string | null,
  steps: readonly EscalationStep[] | null,
): { kind: AlertStatusKind; label: string } {
  if (alert.state === "escalated") {
    const step = steps?.find((s) => s.id === alert.escalatedTo);
    const to = step
      ? recipientInText(step.recipient)
      : (alert.escalatedTo ?? "the next step").replace(/_/g, " ");
    return { kind: "escalated", label: `Escalated to ${to}` };
  }
  if (alert.acknowledgedBy || alert.state === "acknowledged") {
    return {
      kind: "acknowledged",
      label: alert.acknowledgedTs
        ? `Acknowledged ${formatIst(alert.acknowledgedTs)}`
        : "Acknowledged",
    };
  }
  if (cycleTs && alert.raisedTs && minutesBetween(cycleTs, alert.raisedTs) === 0) {
    return { kind: "new", label: "New" };
  }
  const unit = persistenceUnit(alert.persistsUnit);
  const n = alert.persistsCycles;
  return { kind: "held", label: `Held ${n} ${n === 1 ? unit : `${unit}s`}` };
}

/** The cross-cycle situation a raised or pending entry belongs to: scope and place. */
function situationOf(entry: { scope: string; areaDesc: string }): string {
  return `${entry.scope}|${entry.areaDesc}`;
}

/**
 * Where each pending entry belongs on the screen:
 *
 * - `upgrades`: a listed alert's next level up ("Severe next cycle if it holds"), keyed by alert
 *   id. Measured on the 08:40 cycle, 28 of the 60 listed pending places were already in the queue
 *   one level lower; listing them again as separate headlines read as 28 more places.
 * - `stepUps`: places already raised at a lower level that the capped list does not show. At
 *   08:40 the queue lists 60 of 213 raised, and 24 of the 32 pending places with no listed row are
 *   these; calling them "not raised yet" was untrue.
 * - `pendingOnly`: places raised at nothing yet.
 * - `unplaced`: no listed row, a capped queue and an API that does not say the raised level, so
 *   the screen cannot tell a step-up from a first raise and says so.
 *
 * `capped` is `AlertSet.capped`. Uncapped, a place with no listed row is not raised.
 */
export function splitPending(
  alerts: readonly RunAlert[],
  pending: readonly PendingAlert[],
  capped = false,
): {
  upgrades: Map<string, PendingAlert>;
  stepUps: PendingAlert[];
  pendingOnly: PendingAlert[];
  unplaced: PendingAlert[];
} {
  const raisedBySituation = new Map(alerts.map((a) => [situationOf(a), a]));
  const upgrades = new Map<string, PendingAlert>();
  const stepUps: PendingAlert[] = [];
  const pendingOnly: PendingAlert[] = [];
  const unplaced: PendingAlert[] = [];
  for (const entry of pending) {
    const raised = raisedBySituation.get(situationOf(entry));
    if (raised) {
      if (LEVEL_RANK[entry.level] < LEVEL_RANK[raised.level]) upgrades.set(raised.id, entry);
    } else if (entry.raisedLevel !== null) stepUps.push(entry);
    else if (entry.raisedLevelKnown || !capped) pendingOnly.push(entry);
    else unplaced.push(entry);
  }
  return { upgrades, stepUps, pendingOnly, unplaced };
}

/**
 * What floods first, then what floods deepest: the order an officer reads a level's alerts in.
 * The API sorts by peak; onset is the question a ward officer asks first.
 */
export function sortByOnset<T extends Pick<RunAlert, "windowFrom" | "peakCm" | "id">>(
  alerts: readonly T[],
): T[] {
  return [...alerts].sort(
    (a, b) =>
      a.windowFrom.localeCompare(b.windowFrom) || b.peakCm - a.peakCm || a.id.localeCompare(b.id),
  );
}

export interface QueueSummary {
  counts: Record<AlertLevel, number>;
  unacknowledged: number;
  /** The deepest peak in the queue. */
  worst: RunAlert | null;
  /** The earliest window start, and its lead from the cycle in minutes. */
  firstOnset: { ts: string; leadMin: number | null } | null;
}

/** Counts per level, what nobody has acknowledged, the deepest place and the first onset. */
export function summariseQueue(alerts: readonly RunAlert[], cycleTs: string | null): QueueSummary {
  const counts: Record<AlertLevel, number> = { severe: 0, moderate: 0, watch: 0 };
  let worst: RunAlert | null = null;
  let first: string | null = null;
  let unacknowledged = 0;
  for (const alert of alerts) {
    counts[alert.level] += 1;
    if (isUnacknowledged(alert)) unacknowledged += 1;
    if (!worst || alert.peakCm > worst.peakCm) worst = alert;
    if (alert.windowFrom && (first === null || alert.windowFrom.localeCompare(first) < 0)) {
      first = alert.windowFrom;
    }
  }
  return {
    counts,
    unacknowledged,
    worst,
    firstOnset:
      first === null
        ? null
        : { ts: first, leadMin: cycleTs ? minutesBetween(cycleTs, first) : null },
  };
}

/** Nobody at the desk has acknowledged or escalated it yet. */
export function isUnacknowledged(alert: Pick<RunAlert, "acknowledgedBy" | "state">): boolean {
  return !alert.acknowledgedBy && alert.state === "raised";
}

/** A filename-safe slug of a place: "Sant Shitolebaba Maharaj Marg" to "sant-shitolebaba-maharaj-marg". */
export function placeSlug(place: string): string {
  const slug = place
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60)
    .replace(/-+$/g, "");
  return slug || "alert";
}

/**
 * "20190702-0840-sant-shitolebaba-maharaj-marg-severe.cap.xml": the cycle in IST, the place and
 * the level. The alert id carries the full run id and made an 80-character filename.
 */
export function capFilename(
  cycleTs: string | null,
  alert: Pick<RunAlert, "name" | "areaDesc" | "level">,
): string {
  const iso = cycleTs ? toIstIso(cycleTs) : null;
  const cycle = iso
    ? `${iso.slice(0, 10).replace(/-/g, "")}-${iso.slice(11, 16).replace(":", "")}-`
    : "";
  return `${cycle}${placeSlug(alertPlace(alert))}-${alert.level}.cap.xml`;
}

/** The WhatsApp card and the SMS the API rendered for one alert, from its delivery rows. */
export function alertMessages(
  rows: readonly DeliveryRow[] | null | undefined,
  alertId: string,
): { whatsapp: string | null; sms: string | null } {
  let whatsapp: string | null = null;
  let sms: string | null = null;
  for (const row of rows ?? []) {
    if (row.alertId !== alertId || !row.text) continue;
    if (row.label === "WhatsApp mock") whatsapp = row.text;
    else if (row.label === "SMS mock") sms = row.text;
  }
  return { whatsapp, sms };
}

/**
 * How many alerts a run raised, and how many of them are severe, for the alert centre's "open
 * the cycle with the most alerts" when the cycle on screen raises nothing. Asks for the severe
 * level only, so the payload is the severe queue.
 *
 * `n_raised` is every alert raised; `n_total` is how many the product listed, which is capped at
 * 60, so 08:10 and 08:40 both read 60 on it while 08:40 raised 213. The listed count is the
 * fallback on an API that does not serve `n_raised`, and `listedOnly` says so.
 */
export async function loadAlertCount(
  runId: string,
  signal?: AbortSignal,
  city?: string,
): Promise<{ total: number; severe: number; listedOnly: boolean } | null> {
  const params = new URLSearchParams({ run_id: runId, level: "severe" });
  if (city) params.set("city", city);
  const response = await fetch(apiUrl(`/v1/alerts?${params.toString()}`), { signal });
  if (!response.ok) return null;
  const body = (await response.json()) as {
    n_total?: number;
    n_raised?: number | null;
    n_raised_by_level?: Partial<Record<string, number>> | null;
    alerts?: { level?: string }[];
  };
  const alerts = body.alerts ?? [];
  const listedSevere = alerts.filter((a) => a.level === "severe").length;
  if (typeof body.n_raised === "number") {
    return {
      total: body.n_raised,
      severe: body.n_raised_by_level?.severe ?? listedSevere,
      listedOnly: false,
    };
  }
  return { total: body.n_total ?? alerts.length, severe: listedSevere, listedOnly: true };
}

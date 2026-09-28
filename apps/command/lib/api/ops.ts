/**
 * The authority write path, from the browser (`/v1/ops/*`, the gated alert and pump acts;
 * TECH_SPEC 3.6, PRD 3.2, task D-15).
 *
 * Everything else this console fetches is a forecast being read. This file is the one place a
 * person tells VARUNA something it could not know - that a street is shut, that a lorry is broken,
 * that an alert has been seen - so three properties matter more here than anywhere else.
 *
 * **The passphrase never leaves the session.** It is held in a module variable and mirrored into
 * `sessionStorage`, which dies with the tab, and it is sent only in the `X-Varuna-Ops` header.
 * Never `localStorage` (TECH_SPEC 3.6: a desk that leaves the passphrase in the browser is a desk
 * anybody who borrows the laptop can act as), never a query string, never a log line, never a
 * toast. {@link describeRefusal} is the only thing that ever renders after a rejection and it
 * composes its own sentence rather than echoing anything the caller typed.
 *
 * **A refusal is a state, not an exception.** The desk has four ways to be refused - the API holds
 * no passphrase at all, this browser sent none, this browser sent the wrong one, the minute's
 * thirty writes are spent - and the screen must tell them apart, because the fix is different for
 * each. {@link opsRefusal} maps the API's own error codes onto {@link OpsRefusal} and keeps the
 * API's sentence, which already names the fix (SPEC.md 6.8).
 *
 * **Nothing here is composed.** Every result carries the API's `notes`, including the sentence
 * that says an authority edit changed no forecast, and the screen prints them rather than writing
 * its own version of what just happened.
 */

import { api, apiFetch, ApiError, errorMessage, isApiError } from "@/lib/api/client";

/** Header the passphrase travels in; matches `varuna_api.routers.ops.OPS_HEADER`. */
export const OPS_HEADER = "X-Varuna-Ops";

/** Where the passphrase is mirrored so a reload inside one tab does not re-prompt. */
export const OPS_SESSION_KEY = "varuna.ops.passphrase";

/** Environment variable the API checks against; shown when the API holds none. */
export const OPS_PASSPHRASE_ENV = "VARUNA_OPS_PASSPHRASE";

/** Writes a process accepts per rolling minute (`varuna_api.routers.ops.WRITES_PER_MINUTE`). */
export const OPS_WRITES_PER_MINUTE = 30;

/**
 * One sentence for every wrong passphrase, whatever was wrong with it (UI_SPEC 6).
 *
 * The API distinguishes "you sent none" (401) from "you sent the wrong one" (403). The screen
 * does not: a door that answers differently for a blank than for a near miss is a door that
 * answers questions.
 */
export const WRONG_PASSPHRASE_MESSAGE =
  "That passphrase does not match this API. Nothing was sent and nothing was written.";

/** The honest line above the field, verbatim from UI_SPEC 6. */
export const GATE_NOTE = "Prototype access. This is a shared passphrase, not a login.";

// ---------------------------------------------------------------------------------------
// The passphrase, for this tab only
// ---------------------------------------------------------------------------------------

/** Held here as well as in `sessionStorage` so a private window with storage blocked still works. */
let held: string | null = null;

function session(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    // A browser with site data blocked throws on the accessor itself.
    return null;
  }
}

/** The passphrase this tab is holding, or null. */
export function readPassphrase(): string | null {
  if (held !== null) return held;
  try {
    const stored = session()?.getItem(OPS_SESSION_KEY) ?? null;
    held = stored && stored.length > 0 ? stored : null;
  } catch {
    held = null;
  }
  return held;
}

/** Hold a passphrase for this tab. An empty value clears it. */
export function writePassphrase(value: string): void {
  const trimmed = value.trim();
  if (!trimmed) {
    clearPassphrase();
    return;
  }
  held = trimmed;
  try {
    session()?.setItem(OPS_SESSION_KEY, trimmed);
  } catch {
    // Storage refused; the module variable still carries it for this page's lifetime.
  }
}

/** Forget it, here and in the tab's storage. Called by "Sign out" and by a rejection. */
export function clearPassphrase(): void {
  held = null;
  try {
    session()?.removeItem(OPS_SESSION_KEY);
  } catch {
    // Nothing to do: there was nothing to remove.
  }
}

function authHeaders(): Record<string, string> {
  const passphrase = readPassphrase();
  return passphrase ? { [OPS_HEADER]: passphrase } : {};
}

// ---------------------------------------------------------------------------------------
// Refusals
// ---------------------------------------------------------------------------------------

/**
 * Why an authority act did not happen.
 *
 * - `disabled` - the API holds no passphrase, so there is nothing to check against. Read-only.
 * - `passphrase_required` - this browser sent none.
 * - `rejected` - this browser sent the wrong one.
 * - `rate_limited` - thirty writes this minute; wait.
 * - `unreachable` - the request never arrived.
 * - `refused` - the API understood it and said no (no run, no reason, nothing to dispatch).
 */
export type OpsRefusalKind =
  "disabled" | "passphrase_required" | "rejected" | "rate_limited" | "unreachable" | "refused";

export interface OpsRefusal {
  kind: OpsRefusalKind;
  /** What the screen prints: the API's own sentence, except for a wrong passphrase. */
  message: string;
  /** The API's error code where there was one, for tests and the log. */
  code: string | null;
  status: number;
}

/** True when the gate itself refused, so the screen must ask for the passphrase again. */
export function isGateRefusal(refusal: OpsRefusal): boolean {
  return refusal.kind === "passphrase_required" || refusal.kind === "rejected";
}

/** Map any thrown value onto an {@link OpsRefusal}. Never throws, never returns null. */
export function opsRefusal(error: unknown): OpsRefusal {
  if (isApiError(error)) {
    const { code, status, message } = error as ApiError;
    if (code === "ops_writes_disabled" || status === 503)
      return { kind: "disabled", message, code, status };
    if (code === "ops_passphrase_required" || status === 401)
      return {
        kind: "passphrase_required",
        message: WRONG_PASSPHRASE_MESSAGE,
        code: code ?? null,
        status,
      };
    if (code === "ops_passphrase_rejected" || status === 403)
      return { kind: "rejected", message: WRONG_PASSPHRASE_MESSAGE, code: code ?? null, status };
    if (code === "rate_limited" || status === 429)
      return { kind: "rate_limited", message, code: code ?? null, status };
    if (status === 0) return { kind: "unreachable", message, code: code ?? null, status };
    return { kind: "refused", message, code: code ?? null, status };
  }
  return {
    kind: "unreachable",
    message: errorMessage(error, "The authority desk could not reach the VARUNA API."),
    code: null,
    status: 0,
  };
}

/** The sentence a screen shows for a refusal, with the fix named. */
export function describeRefusal(refusal: OpsRefusal): string {
  if (refusal.kind === "rate_limited") {
    return refusal.message;
  }
  if (isGateRefusal(refusal)) return WRONG_PASSPHRASE_MESSAGE;
  return refusal.message;
}

// ---------------------------------------------------------------------------------------
// Reads (ungated: a closure is a public fact)
// ---------------------------------------------------------------------------------------

/** One line of the append-only log, as the API stores it. */
export interface OpsEntry {
  id: string;
  kind: string;
  ts: string;
  user: string;
  /** Everything the kind adds: `segment_id`, `pump_id`, `status`, `order_text`, `note`... */
  detail: Record<string, unknown>;
}

export interface OpsLog {
  city: string;
  nEntries: number;
  entries: OpsEntry[];
  /** False when the API holds no passphrase: the desk is read-only there and says so. */
  writesEnabled: boolean;
  /** The API's own sentence for why writes are off; null when they are on or an older API omits it. */
  writesDisabledReason?: string | null;
  passphraseEnv: string;
  notes: string[];
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function entry(raw: unknown): OpsEntry {
  const row = record(raw);
  const { id, kind, ts, user, ...detail } = row;
  return {
    id: text(id),
    kind: text(kind, "unknown"),
    ts: text(ts),
    user: text(user, "unknown"),
    detail,
  };
}

function notes(raw: unknown): string[] {
  return Array.isArray(raw) ? raw.map((n) => String(n)) : [];
}

/** The whole log for a city, newest first, plus whether this API accepts writes at all. */
export async function loadOpsLog(
  options: { city?: string; limit?: number; kind?: string; signal?: AbortSignal } = {},
): Promise<OpsLog> {
  const body = await api.get<Record<string, unknown>>("/v1/ops/log", {
    query: { city: options.city, limit: options.limit ?? 100, kind: options.kind },
    signal: options.signal,
  });
  return {
    city: text(body.city),
    nEntries: Number(body.n_entries ?? 0),
    entries: Array.isArray(body.entries) ? body.entries.map(entry) : [],
    writesEnabled: body.writes_enabled === true,
    writesDisabledReason: text(body.writes_disabled_reason) || null,
    passphraseEnv: text(body.passphrase_env, OPS_PASSPHRASE_ENV),
    notes: notes(body.notes),
  };
}

/** A street an authority has shut, as the overlay currently folds it. */
export interface Closure {
  id: string;
  segmentId: string;
  /** The officer's own words; the route's explanation quotes them. */
  reason: string;
  user: string;
  ts: string;
  until: string | null;
}

export interface ClosureSet {
  city: string;
  /** The instant expiries were evaluated at. */
  at: string;
  closures: Closure[];
  nEntries: number;
  writesEnabled: boolean;
}

function closure(raw: unknown): Closure {
  const row = record(raw);
  return {
    id: text(row.id),
    segmentId: text(row.segment_id),
    reason: text(row.reason),
    user: text(row.user, "unknown"),
    ts: text(row.ts),
    until: typeof row.until === "string" ? row.until : null,
  };
}

function closureSet(body: Record<string, unknown>): ClosureSet {
  return {
    city: text(body.city),
    at: text(body.at),
    closures: Array.isArray(body.closures) ? body.closures.map(closure) : [],
    nEntries: Number(body.n_entries ?? 0),
    writesEnabled: body.writes_enabled === true,
  };
}

/** Every closure in force for a city. */
export async function loadClosures(
  options: { city?: string; at?: string; signal?: AbortSignal } = {},
): Promise<ClosureSet> {
  const body = await api.get<Record<string, unknown>>("/v1/ops/closures", {
    query: { city: options.city, at: options.at },
    signal: options.signal,
  });
  return closureSet(body);
}

/**
 * An alert as the desk sees it: the run's own alert with the log's state folded in.
 *
 * `GET /v1/alerts` serves the product untouched, which is right for the alert centre and wrong
 * here - an acknowledgement has to survive a reload, and that state lives in the ops log.
 */
export interface DeskAlert {
  id: string;
  level: "severe" | "moderate" | "watch";
  headline: string;
  areaDesc: string;
  thresholdCm: number;
  peakCm: number;
  windowFrom: string;
  windowTo: string;
  /** "acknowledged" or "escalated" once an officer has acted; null while untouched. */
  state: "acknowledged" | "escalated" | null;
  acknowledgedBy: string | null;
  acknowledgedTs: string | null;
  escalatedTo: string | null;
}

export interface DeskAlertSet {
  runId: string;
  city: string;
  alerts: DeskAlert[];
  writesEnabled: boolean;
  notes: string[];
}

const LEVELS = new Set(["severe", "moderate", "watch"]);

function deskAlert(raw: unknown): DeskAlert {
  const row = record(raw);
  const level = text(row.level, "watch");
  const state = text(row.state);
  return {
    id: text(row.id),
    level: (LEVELS.has(level) ? level : "watch") as DeskAlert["level"],
    headline: text(row.headline),
    areaDesc: text(row.area_desc),
    thresholdCm: Number(row.threshold_cm ?? 0),
    peakCm: Number(row.peak_cm ?? 0),
    windowFrom: text(row.window_from),
    windowTo: text(row.window_to),
    state: state === "acknowledged" || state === "escalated" ? state : null,
    acknowledgedBy: typeof row.acknowledged_by === "string" ? row.acknowledged_by : null,
    acknowledgedTs: typeof row.acknowledged_ts === "string" ? row.acknowledged_ts : null,
    escalatedTo: typeof row.escalated_to === "string" ? row.escalated_to : null,
  };
}

/** The alert queue with the desk's state applied (`GET /v1/ops/alerts`). */
export async function loadDeskAlerts(
  options: { runId?: string; city?: string; signal?: AbortSignal } = {},
): Promise<DeskAlertSet> {
  const body = await api.get<Record<string, unknown>>("/v1/ops/alerts", {
    query: { run_id: options.runId, city: options.city },
    signal: options.signal,
  });
  return {
    runId: text(body.run_id),
    city: text(body.city),
    alerts: Array.isArray(body.alerts) ? body.alerts.map(deskAlert) : [],
    writesEnabled: body.writes_enabled === true,
    notes: notes(body.notes),
  };
}

/** One street the officer can act on: what the cycle says, or what the desk already shut. */
export interface StreetOption {
  segmentId: string;
  /** OSM's name, or null where OSM names none - never invented. */
  name: string | null;
  /** The feed's `display_name` for a street OSM does not name ("off Dr Ambedkar Road",
   * "Service road near Wadala Depot"); absent from a feed that predates it. */
  displayName?: string | null;
  peakDepthCm: number;
  /** "closure" when an authority shut it, "forecast" when the water did. */
  cause: "closure" | "forecast";
  /** The window it is impassable for, from the feed. */
  from: string;
  to: string;
}

/**
 * Streets to choose from, read from the road-conditions feed (`GET /v1/feeds/road-conditions`).
 *
 * **Why this list and not every street in the city.** The city carries 21,296 segments and its
 * map layer is 8 MB; a desk does not need a typeahead over all of them, and shipping one would
 * cost a reader seconds before they could type. The feed is the set that matters to an officer -
 * every street this cycle says will stop the chosen vehicle, plus every street the desk has
 * already shut - and it arrives with the segment id the closure needs. A street outside the list
 * is still closable: the form takes a segment id directly, and says so.
 */
export async function loadStreetOptions(
  options: { profile?: string; runId?: string; city?: string; signal?: AbortSignal } = {},
): Promise<StreetOption[]> {
  const body = await api.get<Record<string, unknown>>("/v1/feeds/road-conditions", {
    query: { profile: options.profile ?? "car", run_id: options.runId, city: options.city },
    signal: options.signal,
    timeoutMs: 20_000,
  });
  const features = Array.isArray(body.features) ? body.features : [];
  return features.map((feature) => {
    const properties = record(record(feature).properties);
    return {
      segmentId: text(properties.segment_id),
      name: typeof properties.name === "string" && properties.name ? properties.name : null,
      ...(typeof properties.display_name === "string" && properties.display_name
        ? { displayName: properties.display_name }
        : {}),
      peakDepthCm: Number(properties.peak_depth_cm ?? 0),
      cause: properties.cause === "closure" ? "closure" : "forecast",
      from: text(properties.from),
      to: text(properties.to),
    };
  });
}

/** One citizen report, as `GET /v1/reports` stores it. */
export interface CitizenReport {
  id: string;
  ts: string;
  receivedAt: string;
  lat: number;
  lon: number;
  /** ankle, knee or waist - the body landmark the reporter picked. */
  depthHint: string;
  depthCm: number;
  text: string | null;
  hasPhoto: boolean;
  source: string;
  synthetic: boolean;
}

/** The citizen inbox, newest first. */
export async function loadReports(
  options: { limit?: number; signal?: AbortSignal } = {},
): Promise<CitizenReport[]> {
  const body = await api.get<Record<string, unknown>>("/v1/reports", {
    query: { limit: options.limit ?? 30 },
    signal: options.signal,
  });
  const rows = Array.isArray(body.reports) ? body.reports : [];
  return rows.map((raw) => {
    const row = record(raw);
    return {
      id: text(row.id),
      ts: text(row.ts),
      receivedAt: text(row.received_at),
      lat: Number(row.lat ?? 0),
      lon: Number(row.lon ?? 0),
      depthHint: text(row.depth_hint),
      depthCm: Number(row.depth_cm ?? 0),
      text: typeof row.text === "string" && row.text ? row.text : null,
      hasPhoto: row.has_photo === true,
      source: text(row.source, "unknown"),
      synthetic: row.synthetic === true,
    };
  });
}

// ---------------------------------------------------------------------------------------
// Writes (gated)
// ---------------------------------------------------------------------------------------

async function post<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    body: body ?? {},
    headers: authHeaders(),
    timeoutMs: 30_000,
  });
}

/**
 * Check a passphrase without writing anything.
 *
 * **There is no "verify this passphrase" endpoint, and there should not be one.** So the gate
 * asks `POST /v1/pumps/optimise` for the one solver the API refuses by name. The passphrase gate
 * is a FastAPI dependency, so it runs before the handler: a wrong passphrase answers 401 or 403
 * and never reaches the body, and a right one reaches a handler whose first line raises 501 "the
 * MILP solver is P1". Nothing is written, no optimiser runs, and the only cost is one of the
 * minute's thirty write slots.
 *
 * Anything that is not the gate's own refusal counts as accepted, so this stays true if the MILP
 * solver is ever implemented: then the probe would be answered rather than refused, which is
 * still proof that the gate let it through.
 */
export async function checkPassphrase(passphrase: string): Promise<OpsRefusal | null> {
  const trimmed = passphrase.trim();
  if (!trimmed) {
    return {
      kind: "passphrase_required",
      message: WRONG_PASSPHRASE_MESSAGE,
      code: null,
      status: 0,
    };
  }
  try {
    await apiFetch("/v1/pumps/optimise", {
      method: "POST",
      body: { solver: "milp" },
      headers: { [OPS_HEADER]: trimmed },
      timeoutMs: 20_000,
    });
    return null;
  } catch (error) {
    const refusal = opsRefusal(error);
    if (isGateRefusal(refusal) || refusal.kind === "disabled" || refusal.kind === "unreachable") {
      return refusal;
    }
    // 501, 429, 404, anything else: the gate passed and the handler answered.
    return null;
  }
}

/** What a closure changed, in the API's own words plus the state it left behind. */
export interface ClosureResult extends ClosureSet {
  entry: OpsEntry;
  notes: string[];
}

export interface ClosureInput {
  segmentId: string;
  reason: string;
  /** ISO 8601 with an offset, or null for until-reopened. */
  until?: string | null;
  user: string;
  city?: string;
  reopen?: boolean;
}

/** Close a street, or reopen one. Both are appends; nothing is ever deleted. */
export async function postClosure(input: ClosureInput): Promise<ClosureResult> {
  const body = await post<Record<string, unknown>>("/v1/ops/closures", {
    segment_id: input.segmentId,
    reason: input.reason,
    until: input.until ?? null,
    user: input.user,
    city: input.city ?? null,
    reopen: input.reopen ?? false,
  });
  return { ...closureSet(body), entry: entry(body.entry), notes: notes(body.notes) };
}

export type PumpState = "available" | "unavailable" | "moved";

export interface PumpStatusResult {
  entry: OpsEntry;
  city: string;
  /** Every pump the desk has set a status on, keyed by pump id. */
  pumps: Record<string, { status: PumpState; user: string; ts: string }>;
  notes: string[];
}

/** Mark a pump available, unavailable, or moved to a new depot. */
export async function postPumpStatus(input: {
  pumpId: string;
  status: PumpState;
  user: string;
  note?: string | null;
  city?: string;
  lon?: number | null;
  lat?: number | null;
}): Promise<PumpStatusResult> {
  const body = await post<Record<string, unknown>>(
    `/v1/ops/pumps/${encodeURIComponent(input.pumpId)}/status`,
    {
      status: input.status,
      user: input.user,
      note: input.note ?? null,
      city: input.city ?? null,
      lon: input.lon ?? null,
      lat: input.lat ?? null,
    },
  );
  const pumps: PumpStatusResult["pumps"] = {};
  for (const [id, raw] of Object.entries(record(body.pumps))) {
    const row = record(raw);
    const status = text(row.status, "available");
    pumps[id] = {
      status: (status === "unavailable" || status === "moved" ? status : "available") as PumpState,
      user: text(row.user, "unknown"),
      ts: text(row.ts),
    };
  }
  return { entry: entry(body.entry), city: text(body.city), pumps, notes: notes(body.notes) };
}

export interface AlertActionResult {
  runId: string;
  entry: OpsEntry;
  alert: DeskAlert;
  notes: string[];
}

/** Acknowledge an alert, or escalate it up the matrix. Both are recorded, neither changes water. */
export async function postAlertAction(input: {
  alertId: string;
  action: "ack" | "escalate";
  user: string;
  note?: string | null;
  city?: string;
  runId?: string;
  escalateTo?: string;
}): Promise<AlertActionResult> {
  const suffix = input.action === "ack" ? "ack" : "escalate";
  const query = input.runId ? `?run_id=${encodeURIComponent(input.runId)}` : "";
  const body = await post<Record<string, unknown>>(
    `/v1/alerts/${encodeURIComponent(input.alertId)}/${suffix}${query}`,
    {
      user: input.user,
      note: input.note ?? null,
      city: input.city ?? null,
      escalate_to: input.escalateTo ?? "control_room",
    },
  );
  return {
    runId: text(body.run_id),
    entry: entry(body.entry),
    alert: deskAlert(body.alert),
    notes: notes(body.notes),
  };
}

/** One pump the optimiser assigned, with the benefit it priced and the model that priced it. */
export interface PumpAssignment {
  pumpId: string;
  depot: string;
  hotspotId: string;
  hotspotName: string;
  etaMin: number;
  minutesSaved: number;
  benefitModel: string;
  /** Present on a dispatch: the plain-language order the API composed. */
  orderText?: string;
}

export interface PumpPlan {
  runId: string;
  city: string;
  thresholdCm: number;
  assignments: PumpAssignment[];
  /** Pumps the desk has withheld, with the status that withheld them. */
  withheld: { pumpId: string; status: string }[];
  totalMinutesSaved: number;
  benefitModel: string;
  benefitLabel: string;
  solveMs: number;
  notes: string[];
}

function assignment(raw: unknown): PumpAssignment {
  const row = record(raw);
  return {
    pumpId: text(row.pump_id),
    depot: text(row.depot),
    hotspotId: text(row.hotspot_id),
    hotspotName: text(row.hotspot_name),
    etaMin: Number(row.eta_min ?? 0),
    minutesSaved: Number(row.minutes_saved ?? 0),
    benefitModel: text(row.benefit_model, "unknown"),
    ...(typeof row.order_text === "string" ? { orderText: row.order_text } : {}),
  };
}

function plan(body: Record<string, unknown>): PumpPlan {
  const assignments = Array.isArray(body.assignments) ? body.assignments.map(assignment) : [];
  return {
    runId: text(body.run_id),
    city: text(body.city),
    thresholdCm: Number(body.threshold_cm ?? 45),
    assignments,
    withheld: (Array.isArray(body.withheld) ? body.withheld : []).map((raw) => {
      const row = record(raw);
      return { pumpId: text(row.pump_id), status: text(row.status, "unavailable") };
    }),
    totalMinutesSaved: Number(
      body.total_minutes_saved ?? assignments.reduce((sum, a) => sum + a.minutesSaved, 0),
    ),
    benefitModel: text(body.benefit_model, "unknown"),
    benefitLabel: text(body.benefit_label),
    solveMs: Number(body.solve_ms ?? 0),
    notes: notes(body.notes),
  };
}

/** Re-run the greedy optimiser now, honouring what the desk has withheld. Writes nothing. */
export async function optimisePumps(
  input: { runId?: string; city?: string } = {},
): Promise<PumpPlan> {
  const body = await post<Record<string, unknown>>("/v1/pumps/optimise", {
    run_id: input.runId ?? null,
    city: input.city ?? null,
    solver: "greedy",
  });
  return plan(body);
}

export interface DispatchResult {
  runId: string;
  city: string;
  dispatchedBy: string;
  dispatchedTs: string;
  orders: PumpAssignment[];
  benefitLabel: string;
  syntheticInventory: boolean;
  notes: string[];
  /** One sentence per dispatched place, as the alert about it now carries it (7.6 AC4). */
  alertInstructions: string[];
  /** The phone-mock message per place: the alert's own WhatsApp text, or the order itself. */
  phoneMessages: { hotspotId: string; alertId: string | null; instruction: string; text: string }[];
}

/** Record the dispatch order for the current plan. No lorry moves; the response says so. */
export async function dispatchPumps(input: {
  runId?: string;
  city?: string;
  pumpIds?: string[];
  user: string;
  note?: string | null;
}): Promise<DispatchResult> {
  const body = await post<Record<string, unknown>>("/v1/pumps/dispatch", {
    run_id: input.runId ?? null,
    city: input.city ?? null,
    pump_ids: input.pumpIds ?? [],
    user: input.user,
    note: input.note ?? null,
  });
  return {
    runId: text(body.run_id),
    city: text(body.city),
    dispatchedBy: text(body.dispatched_by, "unknown"),
    dispatchedTs: text(body.dispatched_ts),
    orders: Array.isArray(body.orders) ? body.orders.map(assignment) : [],
    benefitLabel: text(body.benefit_label),
    syntheticInventory: body.synthetic_inventory === true,
    notes: notes(body.notes),
    alertInstructions: Array.isArray(body.alert_instructions)
      ? body.alert_instructions.map((n) => String(n))
      : [],
    phoneMessages: (Array.isArray(body.phone_messages) ? body.phone_messages : []).map((raw) => {
      const row = record(raw);
      return {
        hotspotId: text(row.hotspot_id),
        alertId: typeof row.alert_id === "string" ? row.alert_id : null,
        instruction: text(row.instruction),
        text: text(row.text),
      };
    }),
  };
}

/** What a real send did: the delivery log row the API recorded, and the text it sent. */
export interface AlertSendResult {
  runId: string;
  status: string;
  toMasked: string | null;
  providerId: string | null;
  text: string;
}

/**
 * Send one alert to the phone configured where the API runs (task P8.8). Gated like every desk
 * act. The recipient is the API's configuration, never this call's: there is no number to pass.
 */
export async function sendAlertToPhone(input: {
  alertId: string;
  user: string;
  city?: string;
  runId?: string;
}): Promise<AlertSendResult> {
  const query = input.runId ? `?run_id=${encodeURIComponent(input.runId)}` : "";
  const body = await post<Record<string, unknown>>(
    `/v1/alerts/${encodeURIComponent(input.alertId)}/send${query}`,
    { user: input.user, city: input.city ?? null },
  );
  const delivery = record(body.delivery);
  return {
    runId: text(body.run_id),
    status: text(delivery.status, "unknown"),
    toMasked: typeof delivery.to_masked === "string" ? delivery.to_masked : null,
    providerId: typeof delivery.provider_id === "string" ? delivery.provider_id : null,
    text: text(body.text),
  };
}

// ---------------------------------------------------------------------------------------
// Wording the log
// ---------------------------------------------------------------------------------------

/**
 * One line of plain language for a log entry, built from its own fields.
 *
 * The API stores facts, not sentences - `{kind: "closure", segment_id, reason}` - and the log is
 * the audit trail the desk is judged on, so the row says exactly what the entry holds and
 * invents nothing. An entry of a kind this function does not know prints its kind rather than
 * being hidden, because a log that silently drops rows is not a log.
 */
export function describeEntry(row: OpsEntry): string {
  const detail = row.detail;
  const segment = text(detail.segment_id);
  const pump = text(detail.pump_id);
  switch (row.kind) {
    case "closure":
      return `Closed ${segment}${text(detail.reason) ? ` — ${text(detail.reason)}` : ""}`;
    case "reopen":
      return `Reopened ${segment}`;
    case "pump_status":
      return `Pump ${pump} marked ${text(detail.status, "unknown")}`;
    case "alert_ack":
      return `Acknowledged alert ${text(detail.alert_id)}`;
    case "alert_escalate":
      return `Escalated alert ${text(detail.alert_id)} to ${text(detail.to, "the next step")}`;
    case "dispatch":
      return text(detail.order_text, `Dispatched pump ${pump}`);
    default:
      return `Recorded an entry of kind ${row.kind}`;
  }
}

/** Whether an entry's kind is one a route or a feed reads, or one that is only ever a record. */
export function changesForecast(kind: string): boolean {
  return kind === "closure" || kind === "reopen" || kind === "pump_status";
}

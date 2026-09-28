/**
 * The API explorer's model (SPEC.md 7.12, task P9.8): what the committed OpenAPI snapshot says,
 * how a request is built from it, and how an answer is read back.
 *
 * Pure functions only, so the page can reduce the 214 kB snapshot on the server and hand the
 * client a list of operations, and so every rule below is tested without a browser.
 *
 * **Two rules the explorer never bends.**
 *
 * 1. It never sends a passphrase. The desk's writes are gated by the `x-varuna-ops` header
 *    (`services/api/varuna_api/routers/ops.py`); that header is stripped from every form, and
 *    `buildRequest` refuses a request that carries it, whoever put it there.
 * 2. It only sends what cannot change the API's state: every GET, plus the two POSTs that compute
 *    an answer and store nothing (`/v1/route`, `/v1/whatif`). Everything else - the replay clock,
 *    a live cycle, an onboarding job, a report, a desk action - is shown and copied, not sent,
 *    because an explorer on a shared deployment would otherwise move the clock every judge is
 *    watching.
 */

/** Header the desk passphrase travels in (`varuna_api.routers.ops.OPS_HEADER`). */
export const PASSPHRASE_HEADER = "x-varuna-ops";

/** The run every preset is pinned to: 2 July 2019, 08:40 IST, the ambulance beat of section 15.
 * Run ids carry the cycle time in compact UTC, so 08:40 IST is `T0310Z`. */
export const DEMO_RUN_ID = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";

/** The demo instant, with its offset (SPEC.md 12: all times ISO 8601 with +05:30). */
export const DEMO_TIME = "2019-07-02T08:40:00+05:30";

/** POSTs that compute an answer and write nothing, so the explorer may send them. */
export const SAFE_POSTS: ReadonlySet<string> = new Set(["/v1/route", "/v1/whatif"]);

/** Characters of a response body drawn on screen; the rest is counted, not dropped silently. */
export const MAX_SHOWN_CHARS = 60_000;

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface ApiParam {
  name: string;
  in: "path" | "query";
  required: boolean;
  description: string;
  /** JSON-schema type, flattened: "string", "integer", "number", "boolean", or "string | null". */
  type: string;
  enumValues: string[];
  defaultValue: string | null;
}

export interface ApiField {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

export type ApiBody = { kind: "schema"; schema: string; fields: ApiField[] } | { kind: "free" };

export interface ApiOperation {
  /** Stable key, `"POST /v1/route"`. */
  key: string;
  method: HttpMethod;
  path: string;
  tag: string;
  summary: string;
  description: string;
  params: ApiParam[];
  body: ApiBody | null;
  responses: string[];
  /** True when the operation takes the desk passphrase header. */
  needsPassphrase: boolean;
  /** Whether the explorer sends it, and when not, why. */
  runnable: boolean;
  notRunnableReason: string | null;
}

// ---------------------------------------------------------------------------------------------
// Reading the snapshot
// ---------------------------------------------------------------------------------------------

interface SchemaLike {
  $ref?: string;
  type?: string | string[];
  anyOf?: SchemaLike[];
  oneOf?: SchemaLike[];
  items?: SchemaLike;
  enum?: unknown[];
  default?: unknown;
  description?: string;
  title?: string;
  properties?: Record<string, SchemaLike>;
  required?: string[];
  additionalProperties?: unknown;
}

interface ParameterLike {
  name: string;
  in: string;
  required?: boolean;
  description?: string;
  schema?: SchemaLike;
}

interface OperationLike {
  summary?: string;
  description?: string;
  tags?: string[];
  parameters?: ParameterLike[];
  requestBody?: { content?: Record<string, { schema?: SchemaLike }> };
  responses?: Record<string, unknown>;
}

export interface OpenApiLike {
  paths: Record<string, Record<string, OperationLike>>;
  components?: { schemas?: Record<string, SchemaLike> };
}

const METHODS: readonly HttpMethod[] = ["GET", "POST", "PUT", "PATCH", "DELETE"];

function refName(ref: string): string {
  return ref.split("/").pop() ?? ref;
}

/** A schema's type as one short string: `integer`, `string | null`, `array of number`, `Point`. */
export function typeLabel(schema: SchemaLike | undefined): string {
  if (!schema) return "any";
  if (schema.$ref) return refName(schema.$ref);
  const variants = schema.anyOf ?? schema.oneOf;
  if (variants) return variants.map(typeLabel).join(" | ");
  if (Array.isArray(schema.type)) return schema.type.join(" | ");
  if (schema.type === "array") return `array of ${typeLabel(schema.items)}`;
  return schema.type ?? "object";
}

function stringOrNull(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  return typeof value === "string" ? value : JSON.stringify(value);
}

function paramFrom(raw: ParameterLike): ApiParam | null {
  if (raw.in !== "path" && raw.in !== "query") return null;
  const schema = raw.schema;
  const enumSource = schema?.enum ?? (schema?.anyOf ?? []).flatMap((v) => v.enum ?? []);
  return {
    name: raw.name,
    in: raw.in,
    required: Boolean(raw.required),
    description: raw.description ?? schema?.description ?? "",
    type: typeLabel(schema),
    enumValues: enumSource.map((v) => String(v)),
    defaultValue: stringOrNull(schema?.default),
  };
}

function bodyFrom(op: OperationLike, schemas: Record<string, SchemaLike>): ApiBody | null {
  const content = op.requestBody?.content;
  if (!content) return null;
  const schema = content["application/json"]?.schema ?? Object.values(content)[0]?.schema;
  if (!schema?.$ref) return { kind: "free" };
  const name = refName(schema.$ref);
  const resolved = schemas[name];
  const required = new Set(resolved?.required ?? []);
  const fields = Object.entries(resolved?.properties ?? {}).map(([field, spec]) => ({
    name: field,
    type: typeLabel(spec),
    required: required.has(field),
    description: spec.description ?? "",
  }));
  return { kind: "schema", schema: name, fields };
}

/** Why the explorer shows an operation without sending it, or null when it sends it. */
export function notRunnableReason(
  method: HttpMethod,
  path: string,
  needsPassphrase: boolean,
): string | null {
  if (needsPassphrase) {
    return "An authority write: it needs the desk passphrase in the x-varuna-ops header, and this explorer never sends or stores one. Copy it as curl and send it from the desk.";
  }
  if (method === "GET") return null;
  if (method === "POST" && SAFE_POSTS.has(path)) return null;
  if (path.startsWith("/v1/replay/")) {
    return "This moves the replay clock every console on this API is watching. Copy it as curl and send it yourself.";
  }
  if (path === "/v1/whatif/physics-check") {
    return "This re-runs the Twin, which took 47 to 114 s per cycle on the demo laptop. Copy it as curl and send it yourself.";
  }
  return "This changes what the API holds (a live cycle, a job or a report). Copy it as curl and send it yourself.";
}

/** Every operation in the snapshot, in document order, with the passphrase header removed. */
export function operationsFromOpenApi(doc: OpenApiLike): ApiOperation[] {
  const schemas = doc.components?.schemas ?? {};
  const out: ApiOperation[] = [];
  for (const [path, item] of Object.entries(doc.paths)) {
    for (const method of METHODS) {
      const op = item[method.toLowerCase()];
      if (!op) continue;
      const rawParams = op.parameters ?? [];
      const needsPassphrase = rawParams.some(
        (p) => p.in === "header" && p.name.toLowerCase() === PASSPHRASE_HEADER,
      );
      const params = rawParams.map(paramFrom).filter((p): p is ApiParam => p !== null);
      const reason = notRunnableReason(method, path, needsPassphrase);
      out.push({
        key: `${method} ${path}`,
        method,
        path,
        tag: op.tags?.[op.tags.length - 1] ?? "other",
        summary: op.summary ?? "",
        description: op.description ?? "",
        params,
        body: bodyFrom(op, schemas),
        responses: Object.keys(op.responses ?? {}),
        needsPassphrase,
        runnable: reason === null,
        notRunnableReason: reason,
      });
    }
  }
  return out;
}

/** Operations grouped by their last tag, groups in the order they first appear. */
export function groupByTag(
  operations: readonly ApiOperation[],
): Array<{ tag: string; operations: ApiOperation[] }> {
  const groups = new Map<string, ApiOperation[]>();
  for (const op of operations) {
    const list = groups.get(op.tag) ?? [];
    list.push(op);
    groups.set(op.tag, list);
  }
  return [...groups].map(([tag, list]) => ({ tag, operations: list }));
}

/** Case-insensitive filter over method, path, summary and tag. */
export function filterOperations(
  operations: readonly ApiOperation[],
  query: string,
): ApiOperation[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...operations];
  return operations.filter((op) =>
    `${op.method} ${op.path} ${op.summary} ${op.tag}`.toLowerCase().includes(needle),
  );
}

/** A form's starting values: the parameter defaults, overlaid with preset values. */
export function initialValues(
  op: ApiOperation,
  preset: Record<string, string> = {},
): Record<string, string> {
  const values: Record<string, string> = {};
  for (const param of op.params) {
    values[param.name] = preset[param.name] ?? (param.name === "run_id" ? DEMO_RUN_ID : "");
  }
  return values;
}

// ---------------------------------------------------------------------------------------------
// Building a request
// ---------------------------------------------------------------------------------------------

export interface BuiltRequest {
  method: HttpMethod;
  url: string;
  headers: Record<string, string>;
  body: string | null;
}

export type BuildResult = { ok: true; request: BuiltRequest } | { ok: false; message: string };

function trimSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

/** Throws when a header set carries the desk passphrase. The explorer's last line of defence. */
export function assertNoPassphrase(headers: Record<string, string>): void {
  for (const name of Object.keys(headers)) {
    if (name.toLowerCase() === PASSPHRASE_HEADER) {
      throw new Error("The API explorer never sends the desk passphrase.");
    }
  }
}

/**
 * The request an operation's form describes, or the reason there is none.
 *
 * Empty optional parameters are left out rather than sent empty, so the API applies its own
 * default; a missing required one is refused here with its name, before anything is sent.
 */
export function buildRequest(
  op: ApiOperation,
  values: Record<string, string>,
  bodyText: string,
  base: string,
): BuildResult {
  let path = op.path;
  const query = new URLSearchParams();
  for (const param of op.params) {
    const value = (values[param.name] ?? "").trim();
    if (!value) {
      if (param.required) return { ok: false, message: `${param.name} is required.` };
      continue;
    }
    if (param.in === "path") {
      path = path.replace(`{${param.name}}`, encodeURIComponent(value));
    } else {
      query.set(param.name, value);
    }
  }
  const qs = query.toString();
  const headers: Record<string, string> = { accept: "application/json" };
  let body: string | null = null;
  if (op.body) {
    const text = bodyText.trim() || "{}";
    try {
      body = JSON.stringify(JSON.parse(text));
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      return { ok: false, message: `The request body is not JSON: ${detail}` };
    }
    headers["content-type"] = "application/json";
  }
  assertNoPassphrase(headers);
  return {
    ok: true,
    request: {
      method: op.method,
      url: `${trimSlash(base)}${path}${qs ? `?${qs}` : ""}`,
      headers,
      body,
    },
  };
}

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

/** The request as a curl command. A desk write shows where the passphrase goes, never its value. */
export function toCurl(request: BuiltRequest, needsPassphrase = false): string {
  const lines = [`curl -X ${request.method} ${shellQuote(request.url)}`];
  for (const [name, value] of Object.entries(request.headers)) {
    if (name === "accept") continue;
    lines.push(`  -H ${shellQuote(`${name}: ${value}`)}`);
  }
  if (needsPassphrase) lines.push(`  -H "${PASSPHRASE_HEADER}: $VARUNA_OPS_PASSPHRASE"`);
  if (request.body !== null) lines.push(`  -d ${shellQuote(request.body)}`);
  return lines.join(" \\\n");
}

// ---------------------------------------------------------------------------------------------
// Reading the answer
// ---------------------------------------------------------------------------------------------

export interface ErrorEnvelope {
  code: string;
  message: string;
  runId: string | null;
}

export interface ExplorerResponse {
  /** HTTP status; 0 when the request never reached the API. */
  status: number;
  statusText: string;
  /** Round trip measured in the browser, request start to last byte. */
  ms: number;
  contentType: string;
  bytes: number;
  kind: "json" | "text" | "image" | "empty" | "unreachable";
  /** What is drawn: pretty JSON or text, cut at MAX_SHOWN_CHARS. */
  shown: string;
  /** Characters in the whole body when `shown` was cut, else null. */
  truncatedFrom: number | null;
  /** The API's error envelope when it sent one. */
  envelope: ErrorEnvelope | null;
  /** Image bytes, for the PNG endpoints. */
  blob: Blob | null;
}

/** The `{error: {code, message, run_id}}` envelope (SPEC.md 12), or null. */
export function readEnvelope(body: unknown): ErrorEnvelope | null {
  if (!body || typeof body !== "object" || !("error" in body)) return null;
  const error = (body as { error: unknown }).error;
  if (!error || typeof error !== "object") return null;
  const { code, message, run_id: runId } = error as Record<string, unknown>;
  if (typeof code !== "string" || typeof message !== "string") return null;
  return { code, message, runId: typeof runId === "string" ? runId : null };
}

function cut(text: string): { shown: string; truncatedFrom: number | null } {
  if (text.length <= MAX_SHOWN_CHARS) return { shown: text, truncatedFrom: null };
  return { shown: text.slice(0, MAX_SHOWN_CHARS), truncatedFrom: text.length };
}

/** Sends a built request and reads the answer. Never throws: an unreachable API is a result too. */
export async function sendRequest(
  request: BuiltRequest,
  options: { fetchImpl?: typeof fetch; now?: () => number; signal?: AbortSignal } = {},
): Promise<ExplorerResponse> {
  assertNoPassphrase(request.headers);
  const doFetch = options.fetchImpl ?? fetch;
  const now = options.now ?? (() => performance.now());
  const started = now();
  let response: Response;
  try {
    response = await doFetch(request.url, {
      method: request.method,
      headers: request.headers,
      body: request.body ?? undefined,
      signal: options.signal,
    });
  } catch (error) {
    const detail = error instanceof Error && error.message ? ` (${error.message})` : "";
    return {
      status: 0,
      statusText: "",
      ms: now() - started,
      contentType: "",
      bytes: 0,
      kind: "unreachable",
      shown: `The request never reached ${new URL(request.url).origin}${detail}. Start the API with make dev, or check NEXT_PUBLIC_API_URL.`,
      truncatedFrom: null,
      envelope: null,
      blob: null,
    };
  }
  const contentType = response.headers.get("content-type") ?? "";
  const base = { status: response.status, statusText: response.statusText, contentType };
  if (contentType.startsWith("image/")) {
    const blob = await response.blob();
    return {
      ...base,
      ms: now() - started,
      bytes: blob.size,
      kind: "image",
      shown: "",
      truncatedFrom: null,
      envelope: null,
      blob,
    };
  }
  const text = await response.text();
  const ms = now() - started;
  const bytes = new TextEncoder().encode(text).length;
  if (!text) {
    return {
      ...base,
      ms,
      bytes,
      kind: "empty",
      shown: "",
      truncatedFrom: null,
      envelope: null,
      blob: null,
    };
  }
  try {
    const parsed: unknown = JSON.parse(text);
    return {
      ...base,
      ms,
      bytes,
      kind: "json",
      ...cut(JSON.stringify(parsed, null, 2)),
      envelope: readEnvelope(parsed),
      blob: null,
    };
  } catch {
    return { ...base, ms, bytes, kind: "text", ...cut(text), envelope: null, blob: null };
  }
}

/** "18.2 kB", "940 B", "1.4 MB". */
export function formatBytes(bytes: number): string {
  if (bytes < 1000) return `${bytes} B`;
  if (bytes < 1_000_000) return `${(bytes / 1000).toFixed(1)} kB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

// ---------------------------------------------------------------------------------------------
// Presets
// ---------------------------------------------------------------------------------------------

export interface PresetPlaces {
  origin: [number, number];
  destination: [number, number];
}

export interface Preset {
  id: "segments" | "route" | "whatif";
  label: string;
  description: string;
  operationKey: string;
  values: Record<string, string>;
  /** The body, given the demo trip's two hospitals when the preset needs them. */
  body: (places: PresetPlaces | null) => string;
  /** Whether the body needs the hospitals looked up before it can be sent. */
  needsPlaces: boolean;
  /** Where the served endpoint departs from what the snapshot documents, said beside the preset. */
  caveat: string | null;
}

/** Dadar East around Hindmata junction: small enough that the answer reads on one screen. */
export const DEMO_BBOX = "72.835,19.005,72.850,19.020";

export const PRESETS: readonly Preset[] = [
  {
    id: "segments",
    label: "Segments in a bounding box",
    description:
      "Street depth quantiles, exceedance probabilities and safe-until around Hindmata junction at 08:40 IST, for a car.",
    operationKey: "GET /v1/nowcast/segments",
    values: { run_id: DEMO_RUN_ID, bbox: DEMO_BBOX, t: DEMO_TIME, profile: "car" },
    body: () => "",
    needsPlaces: false,
    caveat:
      "The snapshot documents bbox, t and profile, but the handler that serves this path reads only run_id, city and min_depth_cm, so the answer is every street of the run that gets wetter than 5 cm (about 3.7 MB). A true bounding box, without depths, is GET /v1/city/mumbai/layers/segments?bbox=.",
  },
  {
    id: "route",
    label: "Ambulance, KEM to Sion",
    description:
      "KEM Hospital to Sion Hospital at 08:40 IST, ambulance profile, risk tolerance 0.2, with the safe corridors and the reasons. Both hospitals are read from the city register.",
    operationKey: "POST /v1/route",
    values: {},
    body: (places) =>
      JSON.stringify(
        {
          origin: places?.origin ?? null,
          destination: places?.destination ?? null,
          depart_at: DEMO_TIME,
          profile: "ambulance",
          risk_tolerance: 0.2,
          run_id: DEMO_RUN_ID,
          spread: true,
          trip_id: "api-explorer-demo",
          explain: true,
        },
        null,
        2,
      ),
    needsPlaces: true,
    caveat: null,
  },
  {
    id: "whatif",
    label: "What-if at 1.3x rain",
    description:
      "The same cycle with 30 % more rain. The reduced-order emulator moves the Twin's own depths and prints its measured skill beside the answer.",
    operationKey: "POST /v1/whatif",
    values: {},
    body: () => JSON.stringify({ run_id: DEMO_RUN_ID, rain_scale: 1.3, tide_offset_m: 0 }, null, 2),
    needsPlaces: false,
    caveat: null,
  },
];

/** KEM and Sion from `GET /v1/route/facilities`, matched the way the route planner matches them. */
export function demoPlacesFrom(body: unknown): PresetPlaces | null {
  const list = (body as { facilities?: unknown } | null)?.facilities;
  if (!Array.isArray(list)) return null;
  const find = (needle: string) =>
    list.find(
      (f): f is { lon: number; lat: number } =>
        !!f &&
        typeof f === "object" &&
        String((f as { name?: unknown }).name ?? "").includes(needle) &&
        Number.isFinite(Number((f as { lon?: unknown }).lon)) &&
        Number.isFinite(Number((f as { lat?: unknown }).lat)),
    );
  const kem = find("(KEM)");
  const sion = find("(LTMG)");
  if (!kem || !sion) return null;
  return {
    origin: [Number(kem.lon), Number(kem.lat)],
    destination: [Number(sion.lon), Number(sion.lat)],
  };
}

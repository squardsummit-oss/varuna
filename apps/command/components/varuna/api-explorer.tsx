"use client";

import { Copy, Lock, Play } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/components/varuna/panel";
import { Skeleton } from "@/components/varuna/skeleton";
import { useCopyToClipboard } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import {
  DEMO_RUN_ID,
  PRESETS,
  buildRequest,
  demoPlacesFrom,
  filterOperations,
  formatBytes,
  groupByTag,
  initialValues,
  sendRequest,
  toCurl,
  type ApiOperation,
  type ExplorerResponse,
  type Preset,
  type PresetPlaces,
} from "./api-explorer-model";

export interface ApiExplorerProps {
  /** Every operation in the committed snapshot, reduced on the server. */
  operations: ApiOperation[];
  /** The API the explorer talks to, without a trailing slash. */
  base: string;
  /** Test seam; the browser's fetch otherwise. */
  fetchImpl?: typeof fetch;
}

/** The page opens on the ambulance trip: the request section 15's 5:20 beat is about. */
const OPENING_PRESET: Preset = PRESETS.find((p) => p.id === "route") ?? PRESETS[0];

type PlacesState =
  | { status: "loading" }
  | { status: "ready"; places: PresetPlaces }
  | { status: "failed"; message: string };

/**
 * The API explorer (SPEC.md 7.12, task P9.8), built in the design system rather than framed
 * from Swagger: an index of every operation in `openapi.json`, a form per operation, and three
 * presets that run against the real API and show status, round-trip time and the answer - or the
 * API's own error envelope when it refuses.
 *
 * Why not Swagger UI or Scalar: either brings its own fonts, colours and light theme (sections 6.2
 * and 6.3), and both offer an Authorize box and a "Try it out" on every write, which is exactly the
 * passphrase field and the replay-clock button this page must not have. The contract is small
 * enough - 52 paths - that owning the renderer costs less than overriding someone else's.
 */
export function ApiExplorer({ operations, base, fetchImpl }: ApiExplorerProps) {
  const byKey = useMemo(() => new Map(operations.map((op) => [op.key, op])), [operations]);
  const [filter, setFilter] = useState("");
  const [presetId, setPresetId] = useState<Preset["id"] | null>(OPENING_PRESET.id);
  const [selectedKey, setSelectedKey] = useState<string>(OPENING_PRESET.operationKey);
  const selected = byKey.get(selectedKey) ?? operations[0];
  const [values, setValues] = useState<Record<string, string>>(() =>
    selected ? initialValues(selected, OPENING_PRESET.values) : {},
  );
  /** What the reader typed into the body, or null while it is still the preset's own body. */
  const [bodyEdit, setBodyEdit] = useState<string | null>(null);
  const [places, setPlaces] = useState<PlacesState>({ status: "loading" });
  const [response, setResponse] = useState<ExplorerResponse | null>(null);
  const [sending, setSending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const { copy } = useCopyToClipboard();

  const preset = PRESETS.find((p) => p.id === presetId) ?? null;
  // A preset's body is derived, so the ambulance trip fills in when the hospitals arrive.
  const bodyText =
    bodyEdit ?? (preset ? preset.body(places.status === "ready" ? places.places : null) : "");

  // The ambulance preset needs KEM and Sion from the city register, not typed-in coordinates.
  useEffect(() => {
    const controller = new AbortController();
    const doFetch = fetchImpl ?? fetch;
    doFetch(`${base}/v1/route/facilities?city=mumbai`, { signal: controller.signal })
      .then(async (r) => {
        const body: unknown = await r.json().catch(() => null);
        const found = r.ok ? demoPlacesFrom(body) : null;
        if (found) setPlaces({ status: "ready", places: found });
        else
          setPlaces({
            status: "failed",
            message: r.ok
              ? "The city register has no KEM or LTMG Sion hospital, so the route preset has no trip. Type origin and destination as [lon, lat]."
              : `GET /v1/route/facilities answered ${r.status}, so KEM and Sion could not be looked up. Type origin and destination as [lon, lat].`,
          });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setPlaces({
          status: "failed",
          message: `The API at ${base} did not answer (${error instanceof Error ? error.message : "no response"}), so KEM and Sion could not be looked up. Type origin and destination as [lon, lat].`,
        });
      });
    return () => controller.abort();
  }, [base, fetchImpl]);

  const selectOperation = useCallback((op: ApiOperation, from: Preset | null = null) => {
    abortRef.current?.abort();
    setSelectedKey(op.key);
    setPresetId(from?.id ?? null);
    setValues(initialValues(op, from?.values));
    setBodyEdit(from ? null : op.body ? "{}" : "");
    setResponse(null);
    setFormError(null);
    setSending(false);
  }, []);

  const built = useMemo(
    () => (selected ? buildRequest(selected, values, bodyText, base) : null),
    [selected, values, bodyText, base],
  );

  const waitingForPlaces =
    preset?.needsPlaces === true && places.status !== "ready" && places.status !== "failed";

  const send = useCallback(async () => {
    if (!selected || !built) return;
    if (!built.ok) {
      setFormError(built.message);
      return;
    }
    if (!selected.runnable) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setFormError(null);
    setSending(true);
    const result = await sendRequest(built.request, { fetchImpl, signal: controller.signal });
    if (controller.signal.aborted) return;
    setResponse(result);
    setSending(false);
  }, [built, fetchImpl, selected]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const visible = useMemo(() => filterOperations(operations, filter), [operations, filter]);
  const groups = useMemo(() => groupByTag(visible), [visible]);

  if (!selected) {
    return (
      <p className="text-body text-text-2">
        The committed OpenAPI snapshot lists no operations. Regenerate it with uv run varuna
        openapi.
      </p>
    );
  }

  const curl = built?.ok ? toCurl(built.request, selected.needsPassphrase) : null;

  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="api-presets" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="api-presets" className="text-h3 text-text font-medium">
            Try it
          </h2>
          <p className="text-small text-text-2">
            Every preset is pinned to run <span className="text-text font-mono">{DEMO_RUN_ID}</span>
            , the 08:40 IST cycle of the reconstructed replay.
          </p>
        </div>
        <div role="group" aria-label="Ready-made requests" className="flex flex-wrap gap-2">
          {PRESETS.map((p) => {
            const active = presetId === p.id;
            return (
              <button
                key={p.id}
                type="button"
                aria-pressed={active}
                onClick={() => {
                  const op = byKey.get(p.operationKey);
                  if (op) selectOperation(op, p);
                }}
                className={cn(
                  "rounded-control text-small min-h-11 border px-4 font-medium transition-colors duration-150",
                  "focus-visible:outline-tide focus-visible:outline-2 focus-visible:outline-offset-2",
                  active
                    ? "border-tide bg-tide-soft text-text"
                    : "border-line bg-deep text-text-2 hover:bg-well hover:text-text",
                )}
              >
                {p.label}
              </button>
            );
          })}
        </div>
        {preset ? (
          <div className="max-w-[72ch] space-y-1">
            <p className="text-body text-text-2">{preset.description}</p>
            {preset.caveat ? (
              <p className="text-small text-status-degraded">{preset.caveat}</p>
            ) : null}
          </div>
        ) : null}
      </section>

      <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Panel
          dense
          title="Operations"
          description={`${operations.length} in the committed snapshot`}
          as="aside"
        >
          <label className="sr-only" htmlFor="api-filter">
            Filter operations
          </label>
          <Input
            id="api-filter"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Filter by path or summary"
            className="mb-3"
          />
          <nav
            aria-label="Operations"
            tabIndex={0}
            className="focus-visible:outline-tide max-h-[60vh] overflow-y-auto pr-1 focus-visible:outline-2 lg:max-h-[640px]"
          >
            {groups.length === 0 ? (
              <p className="text-small text-text-2 px-1 py-4">No operation matches that filter.</p>
            ) : (
              groups.map((group) => (
                <div key={group.tag} className="mb-3">
                  <h3 className="text-micro text-text-3 px-1 pb-1 font-medium">{group.tag}</h3>
                  <ul>
                    {group.operations.map((op) => {
                      const current = op.key === selected.key;
                      return (
                        <li key={op.key}>
                          <button
                            type="button"
                            aria-current={current ? "true" : undefined}
                            onClick={() => selectOperation(op)}
                            className={cn(
                              "rounded-control flex min-h-8 w-full items-center gap-2 px-1.5 py-1 text-left transition-colors duration-150",
                              "focus-visible:outline-tide focus-visible:outline-2",
                              current
                                ? "bg-well text-text"
                                : "text-text-2 hover:bg-well hover:text-text",
                            )}
                          >
                            <MethodTag method={op.method} />
                            <span className="text-micro min-w-0 flex-1 truncate font-mono">
                              {op.path}
                            </span>
                            {!op.runnable ? (
                              <Lock
                                size={12}
                                strokeWidth={1.75}
                                aria-label="Copied, not sent"
                                className="text-text-3 shrink-0"
                              />
                            ) : null}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))
            )}
          </nav>
        </Panel>

        <div className="flex min-w-0 flex-col gap-4">
          <Panel
            title={
              <span className="flex min-w-0 items-center gap-2">
                <MethodTag method={selected.method} />
                <span className="truncate font-mono">{selected.path}</span>
              </span>
            }
            description={selected.summary}
          >
            <div className="flex flex-col gap-4">
              {selected.description ? (
                <details className="group">
                  <summary className="text-small text-text-2 hover:text-text cursor-pointer">
                    What the handler says about itself
                  </summary>
                  <p className="text-small text-text-2 mt-2 max-w-[72ch] whitespace-pre-line">
                    {selected.description}
                  </p>
                </details>
              ) : null}

              {selected.notRunnableReason ? (
                <p className="rounded-control border-line bg-well text-small text-text-2 flex max-w-[72ch] items-start gap-2 border px-3 py-2">
                  <Lock
                    size={16}
                    strokeWidth={1.75}
                    aria-hidden="true"
                    className="text-text-3 mt-0.5 shrink-0"
                  />
                  {selected.notRunnableReason}
                </p>
              ) : null}

              {selected.params.length > 0 ? (
                <fieldset className="grid gap-3 sm:grid-cols-2">
                  <legend className="text-small text-text mb-2 font-medium">Parameters</legend>
                  {selected.params.map((param) => {
                    const id = `param-${param.in}-${param.name}`;
                    const listId = param.enumValues.length ? `${id}-values` : undefined;
                    return (
                      <div
                        key={`${param.in}-${param.name}`}
                        className="flex min-w-0 flex-col gap-1"
                      >
                        <label
                          htmlFor={id}
                          className="text-small text-text flex flex-wrap items-baseline gap-x-2"
                        >
                          <span className="font-mono">{param.name}</span>
                          <span className="text-micro text-text-3">
                            {param.in}, {param.type}
                            {param.required ? ", required" : ""}
                          </span>
                        </label>
                        <Input
                          id={id}
                          list={listId}
                          value={values[param.name] ?? ""}
                          onChange={(event) =>
                            setValues((current) => ({
                              ...current,
                              [param.name]: event.target.value,
                            }))
                          }
                          placeholder={
                            param.defaultValue ? `Default ${param.defaultValue}` : undefined
                          }
                          className="font-mono"
                          aria-required={param.required || undefined}
                        />
                        {listId ? (
                          <datalist id={listId}>
                            {param.enumValues.map((v) => (
                              <option key={v} value={v} />
                            ))}
                          </datalist>
                        ) : null}
                      </div>
                    );
                  })}
                </fieldset>
              ) : null}

              {selected.body ? (
                <div className="flex flex-col gap-2">
                  <label htmlFor="api-body" className="text-small text-text font-medium">
                    Request body
                    <span className="text-micro text-text-3 ml-2 font-normal">
                      {selected.body.kind === "schema"
                        ? `JSON, ${selected.body.schema}`
                        : "JSON; the fields are in the handler's description"}
                    </span>
                  </label>
                  {selected.body.kind === "schema" && selected.body.fields.length > 0 ? (
                    <ul className="text-micro text-text-2 flex flex-wrap gap-x-4 gap-y-1">
                      {selected.body.fields.map((f) => (
                        <li key={f.name}>
                          <span className="text-text font-mono">{f.name}</span> {f.type}
                          {f.required ? ", required" : ""}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  <textarea
                    id="api-body"
                    value={bodyText}
                    onChange={(event) => setBodyEdit(event.target.value)}
                    spellCheck={false}
                    rows={Math.min(16, Math.max(4, bodyText.split("\n").length + 1))}
                    className="rounded-control border-line bg-ink text-micro text-text focus-visible:border-line-strong focus-visible:outline-tide w-full border px-3 py-2 font-mono focus-visible:outline-2"
                  />
                  {waitingForPlaces ? (
                    <p className="text-small text-text-2" role="status">
                      Looking up KEM Hospital and Sion Hospital in the city register.
                    </p>
                  ) : null}
                  {presetId === "route" && places.status === "failed" ? (
                    <p className="text-small text-status-degraded">{places.message}</p>
                  ) : null}
                </div>
              ) : null}

              {formError ? (
                <p role="alert" className="text-small text-status-degraded">
                  {formError}
                </p>
              ) : null}

              <div className="flex flex-wrap items-center gap-2">
                {selected.runnable ? (
                  <Button
                    onClick={() => void send()}
                    disabled={sending || waitingForPlaces}
                    className="bg-tide text-ink hover:bg-tide/90 min-h-11 px-4"
                  >
                    <Play aria-hidden="true" />
                    {sending ? "Sending" : "Send request"}
                  </Button>
                ) : null}
                <Button
                  variant="outline"
                  className="min-h-11 px-4"
                  disabled={!curl}
                  onClick={() => curl && void copy(curl, "Request copied")}
                >
                  <Copy aria-hidden="true" />
                  Copy as curl
                </Button>
              </div>

              {curl ? (
                <pre
                  tabIndex={0}
                  aria-label="The request as curl"
                  className="rounded-control border-line bg-ink text-micro text-text-2 focus-visible:outline-tide overflow-x-auto border p-3 font-mono leading-relaxed focus-visible:outline-2"
                >
                  <code>{curl}</code>
                </pre>
              ) : null}
            </div>
          </Panel>

          {selected.runnable ? (
            <ResponsePanel
              response={response}
              sending={sending}
              onCopy={(text) => void copy(text, "Response copied")}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function MethodTag({ method }: { method: string }) {
  return (
    <span
      className={cn(
        "rounded-control text-micro inline-flex w-11 shrink-0 justify-center border px-1 py-0.5 font-mono",
        method === "GET" ? "border-line text-text-2" : "border-line-strong bg-well text-text",
      )}
    >
      {method}
    </span>
  );
}

interface ResponsePanelProps {
  response: ExplorerResponse | null;
  sending: boolean;
  onCopy: (text: string) => void;
}

function ResponsePanel({ response, sending, onCopy }: ResponsePanelProps) {
  const blob = response?.blob ?? null;
  const imageUrl = useMemo(() => (blob ? URL.createObjectURL(blob) : null), [blob]);
  useEffect(
    () => () => {
      if (imageUrl) URL.revokeObjectURL(imageUrl);
    },
    [imageUrl],
  );

  if (sending) {
    return (
      <Panel title="Response" description="Waiting for the API">
        <div role="status" aria-label="Waiting for the API">
          <Skeleton lines={6} />
        </div>
      </Panel>
    );
  }
  if (!response) {
    return (
      <Panel title="Response">
        <p className="text-small text-text-2">
          Send the request to see the API&apos;s answer here.
        </p>
      </Panel>
    );
  }

  const ok = response.status >= 200 && response.status < 300;
  const statusLabel =
    response.status === 0
      ? "Not reached"
      : `${response.status}${response.statusText ? ` ${response.statusText}` : ""}`;

  return (
    <Panel
      title="Response"
      actions={
        response.shown && response.truncatedFrom === null ? (
          <Button variant="ghost" size="sm" onClick={() => onCopy(response.shown)}>
            <Copy aria-hidden="true" />
            Copy response
          </Button>
        ) : null
      }
    >
      <div className="flex flex-col gap-3" aria-live="polite">
        <dl className="num text-small flex flex-wrap gap-x-6 gap-y-1">
          <div className="flex gap-2">
            <dt className="text-text-3">Status</dt>
            <dd className={ok ? "text-tide" : "text-status-degraded"} data-testid="api-status">
              {statusLabel}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="text-text-3">Round trip</dt>
            <dd className="text-text" data-testid="api-time">
              {Math.round(response.ms)} ms
            </dd>
          </div>
          {response.contentType ? (
            <div className="flex gap-2">
              <dt className="text-text-3">Type</dt>
              <dd className="text-text-2">{response.contentType.split(";")[0]}</dd>
            </div>
          ) : null}
          {response.status !== 0 ? (
            <div className="flex gap-2">
              <dt className="text-text-3">Size</dt>
              <dd className="text-text-2">{formatBytes(response.bytes)}</dd>
            </div>
          ) : null}
        </dl>

        {response.envelope ? (
          <div
            className="rounded-control border-status-degraded border px-3 py-2"
            data-testid="api-envelope"
          >
            <p className="text-small text-text">
              Refused with <span className="font-mono">{response.envelope.code}</span>
            </p>
            <p className="text-small text-text-2">{response.envelope.message}</p>
          </div>
        ) : null}

        {response.kind === "image" && imageUrl ? (
          // eslint-disable-next-line @next/next/no-img-element -- a blob URL the API just sent; next/image cannot optimise it
          <img
            src={imageUrl}
            alt="The image the API returned"
            className="rounded-control border-line max-h-96 w-auto self-start border"
          />
        ) : null}

        {response.kind === "empty" ? (
          <p className="text-small text-text-2">The API sent an empty body.</p>
        ) : null}

        {response.shown ? (
          <pre
            tabIndex={0}
            aria-label="Response body"
            className="rounded-control border-line bg-ink text-micro text-text-2 focus-visible:outline-tide max-h-[480px] overflow-auto border p-3 font-mono leading-relaxed focus-visible:outline-2"
          >
            <code>{response.shown}</code>
          </pre>
        ) : null}

        {response.truncatedFrom !== null ? (
          <p className="num text-micro text-text-3">
            Showing the first {response.shown.length.toLocaleString("en-IN")} of{" "}
            {response.truncatedFrom.toLocaleString("en-IN")} characters. Copy as curl to read the
            whole answer.
          </p>
        ) : null}
      </div>
    </Panel>
  );
}

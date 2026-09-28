"use client";

import { ApiExplorer } from "@/components/varuna/api-explorer";
import { operationsFromOpenApi, type OpenApiLike } from "@/components/varuna/api-explorer-model";
import { apiUrl } from "@/lib/api/client";
import snapshot from "@/openapi.json";

/** The three paths the story keeps: a liveness read, the run registry and the ambulance route. */
const KEEP = new Set(["GET /healthz", "GET /v1/runs", "POST /v1/route"]);

/**
 * The explorer over three paths of the committed OpenAPI snapshot. The snapshot is 214 kB, so this
 * module is loaded only when the story is opened. Reads are sent to the local API when pressed;
 * the route is compute-only, and nothing that changes state is ever sent.
 */
const OPERATIONS = operationsFromOpenApi(snapshot as unknown as OpenApiLike).filter((op) =>
  KEEP.has(`${op.method} ${op.path}`),
);

export default function ApiExplorerStory() {
  return <ApiExplorer operations={OPERATIONS} base={apiUrl()} />;
}

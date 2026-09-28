import type { Metadata } from "next";

import { operationsFromOpenApi, type OpenApiLike } from "@/components/varuna/api-explorer-model";
import snapshot from "@/openapi.json";

import { ApiScreen } from "./api-screen";

export const metadata: Metadata = {
  title: "API explorer",
  description:
    "Every path of the VARUNA API from its committed OpenAPI snapshot, with three requests that run against the real API: segments in a bounding box, an ambulance route from KEM Hospital to Sion Hospital, and a what-if at 1.3x rain.",
};

/** The 214 kB snapshot is reduced here, on the server, so the page ships the operations and not
 * the document. */
const OPERATIONS = operationsFromOpenApi(snapshot as unknown as OpenApiLike);

export default function ApiPage() {
  return <ApiScreen operations={OPERATIONS} />;
}

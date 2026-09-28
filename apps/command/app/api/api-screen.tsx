"use client";

import { Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiExplorer } from "@/components/varuna/api-explorer";
import type { ApiOperation } from "@/components/varuna/api-explorer-model";
import { AppShell } from "@/components/varuna/app-shell";
import { PageHeader } from "@/components/varuna/page-header";
import { apiUrl } from "@/lib/api";
import { useCopyToClipboard } from "@/lib/hooks";

export interface ApiScreenProps {
  operations: ApiOperation[];
}

/** API explorer (SPEC.md section 7.12, task P9.8). */
export function ApiScreen({ operations }: ApiScreenProps) {
  const base = apiUrl();
  const { copy } = useCopyToClipboard();

  return (
    <AppShell>
      <div className="flex flex-col gap-6 p-6">
        <PageHeader
          title="API explorer"
          description={`Every path in the committed OpenAPI snapshot, read against the API at ${base}. Reads and the two compute-only requests are sent; anything that changes state is shown and copied, never sent.`}
          honesty="Never sends the desk passphrase"
          actions={
            <Button variant="outline" onClick={() => void copy(`${base}/docs`, "Docs URL copied")}>
              <Copy aria-hidden="true" />
              Copy the docs URL
            </Button>
          }
        />
        <ApiExplorer operations={operations} base={base} />
      </div>
    </AppShell>
  );
}

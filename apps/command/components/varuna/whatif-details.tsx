import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface WhatIfDetailsProps {
  /** The disclosure's label; "Details" unless the content wants its own name. */
  summary?: string;
  children: ReactNode;
  className?: string;
}

/**
 * Kalpana's collapsed "Details": the method, the caveats and the provenance an expert judge may
 * want, kept one click away so the screen itself reads in a few seconds. A native disclosure, so
 * it opens with Enter or Space and needs no script.
 */
export function WhatIfDetails({ summary = "Details", children, className }: WhatIfDetailsProps) {
  return (
    <details className={cn("group", className)}>
      <summary className="type-micro text-text-2 hover:text-text focus-visible:ring-tide rounded-control w-fit cursor-pointer py-0.5 focus-visible:ring-2 focus-visible:outline-none">
        {summary}
      </summary>
      <div className="type-micro text-text-3 mt-1.5 flex flex-col gap-1.5">{children}</div>
    </details>
  );
}

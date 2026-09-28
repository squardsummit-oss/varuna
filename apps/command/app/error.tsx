"use client";

import { useEffect } from "react";
import { RotateCcw } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Wordmark } from "@/components/varuna/wordmark";
import { errorMessage } from "@/lib/api/client";

export interface ErrorPageProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/** Route-segment error page: says what happened and offers the retry (SPEC.md section 6.8). */
export default function ErrorPage({ error, reset }: ErrorPageProps) {
  useEffect(() => {
    console.error("Screen failed to render.", error);
  }, [error]);

  return (
    <main className="flex min-h-dvh flex-1 items-center bg-ink px-6 py-16 text-text sm:px-12">
      <div role="alert" className="flex max-w-[60ch] flex-col items-start gap-6">
        <Wordmark size="sm" />
        <div className="space-y-3">
          <h1 className="font-display text-display font-semibold tracking-display">
            This screen failed to render.
          </h1>
          <p className="max-w-[52ch] text-body text-text-2">
            {errorMessage(error, "The last run is still in memory.")} Try again; if it fails twice,
            open the console and check the API log on :8000.
          </p>
          {error.digest ? (
            <p className="font-mono text-micro text-text-3">error {error.digest}</p>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={reset}>
            <RotateCcw data-icon="inline-start" aria-hidden="true" />
            Try again
          </Button>
          <Button variant="outline" render={<Link href="/console" />} nativeButton={false}>
            Open the console
          </Button>
        </div>
      </div>
    </main>
  );
}

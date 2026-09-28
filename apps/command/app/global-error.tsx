"use client";

import { useEffect } from "react";

import "./globals.css";

export interface GlobalErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Last-resort boundary: replaces the root layout when it fails, so it renders its own html and
 * body and cannot rely on the app shell, fonts or providers (Next 16 app router).
 */
export default function GlobalError({ error, reset }: GlobalErrorProps) {
  useEffect(() => {
    console.error("The app shell failed to render.", error);
  }, [error]);

  return (
    <html lang="en" className="dark h-full">
      <body className="flex min-h-full flex-col bg-ink font-sans text-text antialiased">
        <main className="flex min-h-dvh flex-1 items-center px-6 py-16 sm:px-12">
          <div role="alert" className="flex max-w-[60ch] flex-col items-start gap-6">
            <p className="text-small font-semibold tracking-display text-text">VARUNA</p>
            <div className="space-y-3">
              <h1 className="text-display font-semibold tracking-display">
                This screen failed to render.
              </h1>
              <p className="max-w-[52ch] text-body text-text-2">
                {error.message || "The app shell threw before it could draw."} Try again; if it
                fails twice, reload the page and check the API log on :8000.
              </p>
              {error.digest ? (
                <p className="font-mono text-micro text-text-3">error {error.digest}</p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={reset}
              className="inline-flex h-8 items-center rounded-control bg-tide px-3 text-small font-medium text-ink"
            >
              Try again
            </button>
          </div>
        </main>
      </body>
    </html>
  );
}

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { FaintStreets } from "@/components/varuna/faint-streets";
import { Wordmark } from "@/components/varuna/wordmark";

/** SPEC.md 7.13: the map faintly behind, "This street does not exist. Open the console." */
export default function NotFound() {
  return (
    <main className="bg-ink text-text relative flex min-h-dvh flex-1 items-center overflow-hidden px-6 py-16 sm:px-12">
      <FaintStreets />
      <div className="relative z-10 flex max-w-[60ch] flex-col items-start gap-6">
        <Wordmark size="sm" />
        <div className="space-y-3">
          <h1 className="font-display text-display tracking-display font-semibold">
            This street does not exist.
          </h1>
          <p className="text-body text-text-2 max-w-[44ch]">
            The address you typed is not on the map.
          </p>
        </div>
        <Button render={<Link href="/console" />} nativeButton={false}>
          Open the console
        </Button>
      </div>
    </main>
  );
}

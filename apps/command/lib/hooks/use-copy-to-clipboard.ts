"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

export interface CopyToClipboard {
  /** Copies `text`; toasts "Copied" (or `label`) on success and the failure reason otherwise. */
  copy: (text: string, label?: string) => Promise<boolean>;
  /** True for `resetMs` after a successful copy, for a "Copied" button state. */
  copied: boolean;
  /** Plain-language failure, or null. */
  error: string | null;
}

/** Legacy path for insecure contexts (http on a venue LAN) where the async clipboard is missing. */
function copyWithExecCommand(text: string): boolean {
  if (typeof document === "undefined") return false;
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

/**
 * Copy to clipboard with the copy rules baked in: the toast reads "Copied" (SPEC.md 6.8) and a
 * failure says what happened and the fix.
 */
export function useCopyToClipboard(resetMs = 1500): CopyToClipboard {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const copy = useCallback(
    async (text: string, label = "Copied") => {
      let ok = false;
      try {
        if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(text);
          ok = true;
        } else {
          ok = copyWithExecCommand(text);
        }
      } catch {
        ok = copyWithExecCommand(text);
      }
      if (ok) {
        setCopied(true);
        setError(null);
        toast(label);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setCopied(false), resetMs);
      } else {
        const message = "The browser blocked the clipboard. Select the text and copy it by hand.";
        setError(message);
        toast.error(message);
      }
      return ok;
    },
    [resetMs],
  );

  return { copy, copied, error };
}

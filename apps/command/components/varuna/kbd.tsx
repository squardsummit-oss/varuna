import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface KbdProps {
  /** The key as the overlay prints it: "Ctrl K", "Space", "←", "?". */
  children: ReactNode;
  className?: string;
}

/** A key cap for shortcut hints in the palette, the shortcuts overlay and tooltips. */
export function Kbd({ children, className }: KbdProps) {
  return (
    <kbd
      className={cn(
        "inline-flex h-5 items-center rounded border border-line bg-well px-1.5 font-sans type-micro text-text-2",
        className,
      )}
    >
      {children}
    </kbd>
  );
}

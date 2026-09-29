"use client";

import Link from "next/link";
import { Settings2 } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { CitySwitcher } from "@/components/varuna/city-switcher";
import { DARK_TOOLTIP_CLASS, IconRail, useAddressCity } from "@/components/varuna/icon-rail";
import { ThemeToggle } from "@/components/varuna/theme-toggle";
import { Wordmark } from "@/components/varuna/wordmark";
import { navHref, navItem } from "@/lib/nav";
import { cn } from "@/lib/utils";
import { useUiStore } from "@/lib/stores/ui";

const iconButtonClass = cn(
  "inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-control px-1.5 text-text-2",
  "hover:bg-well hover:text-text aria-expanded:bg-well",
);

export interface TopBarProps {
  className?: string;
}

/** The 52 px top bar: wordmark, city and settings. */
export function TopBar({ className }: TopBarProps) {
  const toggleSettings = useUiStore((s) => s.toggleSettings);
  // The wordmark opens the console the nav's Drishti opens: this city's, never a silent Mumbai.
  const consoleHref = navHref(navItem("console"), useAddressCity());

  return (
    <header
      className={cn(
        "h-top-bar border-line bg-deep flex shrink-0 items-center gap-3 border-b px-3",
        className,
      )}
    >
      <Link
        href={consoleHref}
        aria-label="VARUNA console"
        className="rounded-control flex shrink-0 items-center px-1"
      >
        <Wordmark size="sm" withMark />
      </Link>

      <CitySwitcher />

      {/* The bar carries the wordmark, the city, the screen nav and settings. The mode banner, the
          run stamp, the verification chip and the search and shortcuts buttons were removed at the
          team's request. Ctrl+K and ? still open the palette and the shortcuts overlay - the keys
          are unchanged, only the buttons are gone. The nav moved here from the left rail, also at
          the team's request, and names every screen in text from 1280 px up (ADR-0085). It
          scrolls inside its own strip rather than pushing settings off a narrower bar. SPEC.md
          6.5 and 7.2 still describe the older shell. */}
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <IconRail className="min-w-0 overflow-x-auto" />
      </div>

      <div className="flex shrink-0 items-center gap-1">
        {/* Dark is the control room at night, light the same room in daylight (UI_UX.md). */}
        <ThemeToggle size="sm" />
        <Tooltip>
          <TooltipTrigger
            render={
              <button
                type="button"
                aria-label="Settings"
                onClick={toggleSettings}
                className={cn(iconButtonClass, "w-8")}
              />
            }
          >
            <Settings2 aria-hidden="true" className="size-5" strokeWidth={1.75} />
          </TooltipTrigger>
          <TooltipContent className={DARK_TOOLTIP_CLASS}>Settings</TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}

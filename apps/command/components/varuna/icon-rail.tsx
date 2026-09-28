"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSyncExternalStore } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { DEFAULT_CITY, currentCity } from "@/lib/city";
import { cn } from "@/lib/utils";
import { NAV_ITEMS, isNavActive, navAccessibleName, navHref, type NavItem } from "@/lib/nav";

export interface IconRailProps {
  className?: string;
}

/**
 * The dark skin for a tooltip that carries more than one line. The shadcn default is a light
 * card with ink text, where section 6.2's `--text-2` and `--text-3` would sit at under 2:1; on
 * `--well` both clear 6.10's 4.5:1 (ADR-0036). The last child of the popup is its arrow, which
 * takes the same fill.
 */
export const DARK_TOOLTIP_CLASS = cn(
  "border border-line-strong bg-well text-text",
  "[&>:last-child]:bg-well [&>:last-child]:fill-well",
);

/**
 * Re-reads the address bar whenever it changes: back and forward, and every `pushState` the router
 * makes where the browser has the Navigation API.
 *
 * During a client navigation Next renders the new screen before it writes the URL (its history
 * update is an insertion effect), so the city read while rendering is the page being left; the
 * `currententrychange` that follows the write corrects it. `useSearchParams` is the router's own
 * value, but it would need a Suspense boundary around the bar on every prerendered screen and a
 * mock in every test that renders the shell.
 */
function subscribeToAddress(onChange: () => void): () => void {
  const navigation = Reflect.get(window, "navigation") as EventTarget | undefined;
  window.addEventListener("popstate", onChange);
  navigation?.addEventListener?.("currententrychange", onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    navigation?.removeEventListener?.("currententrychange", onChange);
  };
}

/**
 * The city in the address bar; Mumbai on the server and through hydration, like the switcher.
 * Every link in the top bar that opens a screen reads it here - the nav and the wordmark - so the
 * two can never disagree about which city's console they open.
 */
export function useAddressCity(): string {
  return useSyncExternalStore(subscribeToAddress, currentCity, () => DEFAULT_CITY);
}

/** What the tooltip says about a screen: its name, what it does, the word, and the key. */
function NavTooltip({ item }: { item: NavItem }) {
  return (
    <span className="flex flex-col items-start gap-0.5 text-left">
      <span className="type-small text-text font-medium" translate={item.gloss ? "no" : undefined}>
        {item.label}
      </span>
      {item.gloss ? <span className="type-small text-text-2">{item.gloss}</span> : null}
      {item.deva ? (
        <span lang="sa" aria-hidden="true" className="type-small text-text-2">
          {item.deva}
        </span>
      ) : null}
      {item.meaning ? (
        <span className="type-micro text-text-3 max-w-[32ch]">{item.meaning}</span>
      ) : null}
      <kbd className="border-line bg-deep type-micro text-text-2 mt-1 rounded-[4px] border px-1 font-sans">
        {item.hint}
      </kbd>
    </span>
  );
}

/**
 * The screen nav: every console screen, each with its icon and its name.
 *
 * It ran down the left of the shell as a 56 px vertical rail (SPEC.md sections 6.5 and 7.2).
 * At the team's request it now sits in the top bar and runs across, so the map gets those 56 px
 * back and the bar - which lost its mode banner, run stamp and chips - carries the navigation
 * instead of empty space. The active screen is marked by an underline rather than the old left
 * edge, because an edge marker reads as a border in a horizontal strip. SPEC.md 6.5 still draws
 * the older shell.
 *
 * Every screen carries a Sanskrit name (ADR-0085 named six; Drishti, Sanket and Pravesh followed),
 * and a name meant to make someone ask what it is has to be seen, so the names are text beside the
 * icons from 1280 px up (measured: all nine fit at 1280, 1366 and 1440 without scrolling). Below
 * that the bar keeps the icons and the tooltip, which gives the name, the English gloss, the
 * Devanagari and what the word means. Every link's accessible name is the name and the gloss
 * ("Nadi, drain health"), so a screen reader never announces only a word it cannot translate.
 *
 * The links carry the address bar's `?city=` and nothing else, so a Chennai console that opens
 * Nadi opens Chennai's drains; a run id or a selection belongs to the screen it was made on.
 */
export function IconRail({ className }: IconRailProps) {
  const pathname = usePathname();
  const city = useAddressCity();

  return (
    <nav aria-label="Screens" className={cn("flex items-center gap-0.5", className)}>
      {NAV_ITEMS.map((item) => {
        const active = isNavActive(pathname, item.href);
        const Icon = item.icon;
        return (
          <Tooltip key={item.id}>
            <TooltipTrigger
              render={
                <Link
                  href={navHref(item, city)}
                  aria-label={navAccessibleName(item)}
                  aria-current={active ? "page" : undefined}
                  data-active={active ? "" : undefined}
                  className={cn(
                    "rounded-control relative flex h-9 shrink-0 items-center justify-center gap-1.5",
                    "type-small text-text-2 px-2 whitespace-nowrap",
                    "hover:bg-well hover:text-text",
                    active && "text-tide hover:text-tide",
                  )}
                />
              }
            >
              {active ? (
                <span
                  aria-hidden="true"
                  className="rounded-chip bg-tide absolute right-1.5 bottom-0 left-1.5 h-0.5"
                />
              ) : null}
              <Icon aria-hidden="true" className="size-5 shrink-0" strokeWidth={1.75} />
              <span
                data-slot="nav-label"
                translate={item.gloss ? "no" : undefined}
                className="hidden font-medium xl:inline"
              >
                {item.label}
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom" className={cn(DARK_TOOLTIP_CLASS, "px-3 py-2")}>
              <NavTooltip item={item} />
            </TooltipContent>
          </Tooltip>
        );
      })}
    </nav>
  );
}

"use client";

import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { apiUrl } from "@/lib/api/client";
import { DEFAULT_CITY, currentCity, parseCities, withCity, type CityRow } from "@/lib/city";
import { useIsClient } from "@/lib/hooks";
import { cn } from "@/lib/utils";

export interface CitySwitcherProps {
  className?: string;
}

/**
 * City switcher in the top bar (task D-09).
 *
 * The rows come from `GET /v1/cities`, which tests `city/<id>/map/segments.geojson` for each
 * city VARUNA has a config for. That matters on stage: the switcher used to carry a hard-coded
 * disabled Chennai, so after the wizard had built Chennai the row still read "Onboard first" -
 * the one moment the screen is supposed to prove the second city is real.
 *
 * A city with nothing baked is offered anyway and says so, because its console is reachable and
 * honest (an empty map naming the command that fills it) rather than broken.
 */
export function CitySwitcher({ className }: CitySwitcherProps) {
  const [cities, setCities] = useState<CityRow[] | null>(null);
  // The server has no URL to read, so the first render must be the default on both sides or
  // the trigger hydrates with a different name than it rendered; `useIsClient` is that gate.
  const city = useIsClient() ? currentCity() : DEFAULT_CITY;

  useEffect(() => {
    const controller = new AbortController();
    fetch(apiUrl("/v1/cities"), { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : { cities: [] }))
      .then((body) => setCities(parseCities(body)))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  // Until the list arrives, the switcher names the city it is on and offers nothing else. That is
  // the truth at that moment, and it is steadier than a list that grows under the pointer.
  const rows: CityRow[] = cities ?? [
    {
      id: city,
      name: city === DEFAULT_CITY ? "Mumbai" : city,
      code: null,
      built: true,
      latestRunId: null,
    },
  ];
  const current = rows.find((row) => row.id === city);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="Switch city"
        className={cn(
          "rounded-control text-body text-text inline-flex h-7 items-center gap-1 px-2 font-medium",
          "hover:bg-well aria-expanded:bg-well",
          className,
        )}
      >
        {current?.name ?? (city === DEFAULT_CITY ? "Mumbai" : city)}
        <ChevronDown aria-hidden="true" className="text-text-3 size-4" strokeWidth={1.75} />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-56">
        {rows.map((row) => (
          <DropdownMenuCheckboxItem
            key={row.id}
            checked={row.id === city}
            disabled={!row.built}
            onClick={() => {
              if (!row.built || row.id === city) return;
              window.location.assign(
                withCity(window.location.pathname, window.location.search, row.id),
              );
            }}
          >
            <span className="flex min-w-0 flex-col">
              <span>{row.name}</span>
              {row.built && row.latestRunId ? null : (
                <span className="text-micro text-text-3">
                  {row.built ? "Built, nothing baked yet" : "Onboard first"}
                </span>
              )}
            </span>
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

"use client";

import { ChevronDown, Layers } from "lucide-react";
import { useState } from "react";

import { Kbd } from "@/components/varuna/kbd";
import { cn } from "@/lib/utils";

/** The layers the console can actually draw today. */
export interface LayerToggles {
  satellite: boolean;
  /** Probability mode: opacity carries P(> threshold) rather than the depth alone. */
  probability: boolean;
  raster: boolean;
  segments: boolean;
  surcharge: boolean;
  drains: boolean;
  buildings: boolean;
  hotspots: boolean;
  /** The reachability bands of the facility picked in the Reachability tab. */
  isochrones: boolean;
  /** The demo ambulance trip, naive against VARUNA, at the scrub time. */
  routes: boolean;
  /** Google's photorealistic city, with every VARUNA layer draped on it (task P6.15). */
  threeD: boolean;
  /** The inferred drains drawn at their invert depth beneath the streets (motion M28). */
  xray: boolean;
}

export type LayerKey = keyof LayerToggles;

export interface LayerPanelProps {
  value: LayerToggles;
  onChange: (key: LayerKey, next: boolean) => void;
  /** Counts from the current run, so a row says what it would draw before you turn it on. */
  counts?: Partial<Record<LayerKey, number>>;
  /** A sentence under a row, shown whether or not the layer is on: what the run holds that the row
   * draws only in part (the surcharge row quotes the run's reversed-pipe total, which is a fact
   * about the run and does not depend on the toggle). */
  details?: Partial<Record<LayerKey, string>>;
}

const ROWS: readonly {
  key: LayerKey;
  label: string;
  hint: string;
  shortcut?: string;
}[] = [
  {
    key: "satellite",
    label: "Satellite",
    hint: "Esri aerial imagery, dimmed so the water reads",
    shortcut: "V",
  },
  {
    key: "probability",
    label: "Probability",
    hint: "Opacity carries P(above the threshold)",
    shortcut: "P",
  },
  { key: "raster", label: "Depth raster", hint: "30 m surface depth from the Twin" },
  { key: "segments", label: "Streets (depth)", hint: "Road segments coloured by depth" },
  { key: "surcharge", label: "Surcharge", hint: "Manholes pushing water up", shortcut: "S" },
  { key: "drains", label: "Drains", hint: "Inferred graph, coloured by blockage", shortcut: "D" },
  {
    key: "isochrones",
    label: "Isochrones",
    hint: "5, 10 and 15 minute reach of the facility picked under Reachability",
    shortcut: "I",
  },
  {
    key: "routes",
    label: "Routes",
    hint: "KEM Hospital to Sion Hospital by ambulance, naive against VARUNA",
    shortcut: "R",
  },
  { key: "buildings", label: "Buildings", hint: "Footprints from OpenStreetMap" },
  { key: "hotspots", label: "Ground truth", hint: "The chronic register", shortcut: "G" },
  {
    key: "threeD",
    label: "Photorealistic city",
    hint: "Google's photographed 3D Mumbai, with the water and the routes draped on it",
    shortcut: "3",
  },
  {
    key: "xray",
    label: "Drain X-ray",
    hint: "The inferred pipes at their invert depth under the streets; best in 3D",
    shortcut: "X",
  },
];

/**
 * The console's floating layer panel (SPEC.md sections 6.5, 7.2, task P6.10).
 *
 * Only layers that exist are listed. SPEC.md 17 forbids a dead control, and a switch that does
 * nothing is worse than no switch: it makes the operator wonder whether the data is missing or
 * the map is broken. Where a layer needs something first - a facility for the isochrones,
 * Google's Map Tiles API for the photorealistic city, a re-exported drain layer carrying invert
 * elevations for the X-ray - the row's detail line says what, rather than the switch doing
 * nothing silently.
 *
 * The photorealistic row's detail line reads, today, as the one that says what was drawn.
 * Measured on 2026-09-23 once the key's Cloud project had billing linked and the Map Tiles API
 * enabled: the root tileset answers HTTP 200 from curl and from the page alike - with no
 * `Referer` header, with `http://localhost:3000/`, and in both the `X-Goog-Api-Key` and the
 * `?key=` form - and driving `http://localhost:3000/console` that day, the row read "Google's
 * photorealistic Mumbai, with the water, the routes and the markers draped on it." with the
 * merged Google credits under the map. The key carries no HTTP-referrer restriction, so a
 * command line tests it as honestly as a browser does.
 *
 * The row's other sentences are dormant rather than dead, and each was real on this key's
 * predecessors earlier the same day: 403 `SERVICE_DISABLED` before the Map Tiles API was switched
 * on, and 404 `NOT_FOUND` on two projects with no billing linked. They keep their tests because
 * the next deployment starts where those did.
 *
 * Whatever the answer, the row leaves the map exactly as it was rather than emptying it. The same
 * rule carries the X-ray: a drain layer exported before the invert elevations landed carries
 * none, and the row then names `make city CITY=mumbai` instead of drawing a sewer at sea level.
 */
export function LayerPanel({ value, onChange, counts = {}, details = {} }: LayerPanelProps) {
  const [open, setOpen] = useState(true);

  // No `overflow-hidden` on the root: it clipped the rows when the column ran out of room
  // instead of letting the column scroll them into reach (UI_SPEC 8, task D-17). The rounded
  // corners it was there for are kept by the header and the list, which are what touch them.
  return (
    <div className="rounded-panel border-line w-[248px] shrink-0 border bg-[var(--ink)]/85 backdrop-blur-[12px]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="rounded-t-panel hover:bg-well focus-visible:ring-tide flex h-9 w-full items-center gap-2 px-3 text-left focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset"
      >
        <Layers size={16} strokeWidth={1.75} className="text-text-2 shrink-0" />
        <span className="type-small text-text flex-1">Layers</span>
        <ChevronDown
          size={14}
          strokeWidth={1.75}
          className={cn("text-text-3 shrink-0 transition-transform", !open && "-rotate-90")}
        />
      </button>

      {open ? (
        <ul className="rounded-b-panel border-line border-t p-1">
          {ROWS.map((row) => {
            const on = value[row.key];
            const count = counts[row.key];
            const detail = details[row.key];
            return (
              <li key={row.key}>
                <button
                  type="button"
                  role="switch"
                  aria-checked={on}
                  onClick={() => onChange(row.key, !on)}
                  title={row.hint}
                  className="rounded-control hover:bg-well focus-visible:ring-tide flex w-full items-center gap-2.5 px-2 py-1.5 text-left focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset"
                >
                  <span
                    aria-hidden="true"
                    className={cn(
                      "rounded-chip relative h-3.5 w-6 shrink-0 border transition-colors",
                      on ? "border-tide bg-tide/30" : "border-line-strong bg-well",
                    )}
                  >
                    <span
                      className={cn(
                        "absolute top-1/2 size-2 -translate-y-1/2 rounded-full transition-[left]",
                        on ? "bg-tide left-[13px]" : "left-[3px] bg-[var(--text-3)]",
                      )}
                    />
                  </span>
                  <span className="type-small text-text min-w-0 flex-1 truncate">{row.label}</span>
                  {count !== undefined ? (
                    <span className="num type-micro text-text-3 shrink-0">
                      {count.toLocaleString("en-IN")}
                    </span>
                  ) : null}
                  {row.shortcut ? <Kbd>{row.shortcut}</Kbd> : null}
                </button>
                {detail ? (
                  <p className="num type-micro text-text-3 px-2 pb-1.5 pl-[42px]">{detail}</p>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

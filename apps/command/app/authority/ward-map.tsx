"use client";

/**
 * The map across the top of the ward officer's desk (`PRD.md` 3.2, UI_SPEC 2).
 *
 * The desk's entry animation (motion M27) ends on the Mumbai AOI and cross-fades into whatever is
 * behind it, so this map has two hard requirements and one soft one:
 *
 * 1. **It fills its box.** The region it sits in is `relative` with a clamped height; `FloodMap`
 *    and `CityMap` are both `absolute inset-0`, so they do.
 * 2. **It is framed on {@link ENTRY_AOI} on its first paint**, not after a fly-to. `bounds` is
 *    passed straight through to `CityMap`'s camera, which fits it before the first frame - no
 *    flight, so the handover cannot land somewhere the globe was not.
 * 3. It draws this cycle's water. It is the same `FloodMap` the console draws, loading the same
 *    newest baked run, so the desk and the console cannot disagree about which streets are wet.
 *
 * **It is the flat map, deliberately.** The console's photorealistic 3D city is one toggle away
 * from here, and it was not put on the desk: 3D is a camera for exploring, and a 220-380 px strip
 * at the top of a working screen is a glance. A correct flat map beats a half-wired 3D one, and
 * an officer who wants to fly the city has `/console`. The buildings, the depth raster and the
 * drain network are all off for the same reason - 11 MB of footprints and 18 MB of pipes on a
 * screen whose job is a closure form is somebody else's load.
 *
 * The desk's own closures are **not** drawn on it yet. They are stored as ops-log entries against
 * segment ids and the map would have to resolve each to a geometry before it could draw one; that
 * is a real join, not a prop, and claiming it here by drawing nothing would be worse than saying
 * so. The closures list below the map is where they are read today.
 *
 * **Citizen reports are drawn on it** (2026-09-27): the same list the inbox beside it shows, read
 * from the desk's context (`components/authority/desk-reports.tsx`) because this map is handed to
 * the screen by a server component and cannot take a callback from it. A report with a photo
 * carries a ring; its status is the pin's outline (`layers/reports.ts`). Tapping a pin opens its
 * row in the inbox; opening a row flies the map here, on motion M10's fly-to. Outside the desk -
 * no provider - the map draws no pins and nothing is pickable.
 */

import { useMemo } from "react";

import { useDeskReports } from "@/components/authority/desk-reports";
import { FloodMap } from "@/components/map/flood-map";
import { ENTRY_AOI } from "@/components/varuna/globe-entry";
import type { Bbox } from "@/components/map/basemap";
import { reportToPin, type ReportPin } from "@/lib/api/reports";

/** Stable empty list, so a desk with no reports hands the map the same array every render. */
const NO_PINS: readonly ReportPin[] = [];

/** The step the desk shows: now. The officer acts on the present, not on a lead time. */
const NOW_STEP = 0;

/**
 * {@link ENTRY_AOI} in `CityMap`'s corner form.
 *
 * `ENTRY_AOI` is flat (west, south, east, north — SPEC.md 3.3) and `Bbox` is two corners, so
 * the conversion is written once here rather than at the call site. It is checked against
 * `cityBounds("mumbai")` by the test beside this file: the entry's box and the city's AOI are the
 * same four numbers in two places, and the day they diverge the desk's handover breaks silently.
 */
export function entryBounds(): Bbox {
  const [west, south, east, north] = ENTRY_AOI;
  return [
    [west, south],
    [east, north],
  ];
}

export function WardMap() {
  const desk = useDeskReports();
  const list = desk?.list ?? null;
  const pins = useMemo(() => (list ? list.reports.map(reportToPin) : NO_PINS), [list]);
  const select = desk?.select;
  // A pin is already in view, so tapping one selects without a flight.
  const pick = useMemo(() => (select ? (id: string) => select(id) : undefined), [select]);

  return (
    <FloodMap
      city="mumbai"
      step={NOW_STEP}
      bounds={entryBounds()}
      showBuildings={false}
      showRaster={false}
      showDrains={false}
      showSurcharge
      showHotspots
      reports={pins}
      selectedReportId={desk?.selectedId ?? null}
      onPickReport={pick}
      focus={desk?.focus ?? null}
      // `MapSlot` is not mounted behind this map, so this map draws its own credit line.
      attribution
    />
  );
}

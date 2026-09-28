/**
 * The ward map's frame.
 *
 * Motion M27 ends the desk's entry on the Mumbai AOI and cross-fades into whatever is behind it,
 * so the one thing that can silently break the handover is the map opening on a different box.
 * `ENTRY_AOI` is a second copy of the four numbers `globe-intro.tsx` draws with and
 * `CITY_BOUNDS.mumbai` frames with (`.wf/entry-requests.md` section 2 asked for the duplication
 * to be watched), so this asserts they are still the same box - in the corner form `CityMap`'s
 * camera takes, which is where a west/south transposition would hide.
 */

import { describe, expect, it } from "vitest";

import { cityBounds } from "@/components/map/basemap";
import { ENTRY_AOI } from "@/components/varuna/globe-entry";
import { entryBounds } from "../ward-map";

describe("entryBounds", () => {
  it("is ENTRY_AOI as two corners, south-west then north-east", () => {
    const [west, south, east, north] = ENTRY_AOI;
    expect(entryBounds()).toEqual([
      [west, south],
      [east, north],
    ]);
  });

  it("is the same box CityMap would have framed for Mumbai anyway", () => {
    // If these ever diverge the desk's entry lands off its own map, and nothing else would say so.
    expect(entryBounds()).toEqual(cityBounds("mumbai"));
  });

  it("is SPEC.md 3.3's MUM-CENTRAL box", () => {
    expect(ENTRY_AOI).toEqual([72.815, 18.995, 72.905, 19.135]);
  });
});

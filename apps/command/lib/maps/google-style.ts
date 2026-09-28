/**
 * The citizen map's Google basemap style, built at runtime from the design tokens (TECH_SPEC 2.3).
 *
 * Two constraints decide the shape of this file.
 *
 * 1. **A raster map, no Map ID.** Google refuses an inline `styles` array on a vector map and on
 *    any map carrying a Map ID, and cloud styling needs Cloud Console access this build does not
 *    have. So the citizen map is a raster map styled inline. The upgrade path - a Map ID with the
 *    same palette published in the cloud - belongs in `docs/SIMPLIFICATIONS.md`.
 * 2. **No raw hex in `apps/command`** (SPEC.md rule 9, enforced by `pnpm lint:design`). Google
 *    needs a real colour string, not `var(--ink)`, so the values are *resolved* here: the
 *    document's own computed custom properties first, then the generated `@varuna/tokens` values
 *    as the fallback for the server and for jsdom. Either way the number comes from `tokens.json`,
 *    never from this file.
 *
 * The palette follows the app's own surface hierarchy so the basemap reads as the same product as
 * the panels around it: land is `--deep`, the sea and the creeks are `--ink` (the deepest tone),
 * roads lift to `--well` with arterials at `--line`, and every label is `--text-3` haloed on
 * `--ink`. Points of interest and transit are switched off entirely - SPEC.md 6.1 spends the
 * boldness on the water, and a basemap full of restaurant pins spends it on lunch.
 */

import { colors } from "@/lib/ramps";

/** The five tokens the basemap is allowed to use. */
export type StyleToken = "--ink" | "--deep" | "--well" | "--line" | "--text-3";

/**
 * Values from the generated token module, used when there is no document to read - the server
 * render, and vitest's jsdom, which reports no computed value for a custom property.
 */
const TOKEN_FALLBACK: Record<StyleToken, string> = {
  "--ink": colors.ink,
  "--deep": colors.deep,
  "--well": colors.well,
  "--line": colors.line,
  "--text-3": colors["text-3"],
};

/**
 * One token's current value as a colour string.
 *
 * The computed property is preferred over the compiled constant so that a theme override on
 * `:root` - the mechanism SPEC.md 6.2 already uses for dark mode - reaches the basemap too.
 */
export function resolveToken(token: StyleToken): string {
  if (typeof window !== "undefined" && typeof document?.documentElement !== "undefined") {
    try {
      const value = window
        .getComputedStyle(document.documentElement)
        .getPropertyValue(token)
        .trim();
      if (value) return value;
    } catch {
      // A detached document or a browser that refuses computed styles; the token value stands.
    }
  }
  return TOKEN_FALLBACK[token];
}

/**
 * A Google Maps style rule.
 *
 * Declared structurally rather than as `google.maps.MapTypeStyle`: `@types/google.maps` is a
 * transitive dependency and its ambient `google` namespace is not in this app's type program, so
 * naming it would fail `pnpm typecheck`. The shape is identical and assigns to Google's own type
 * structurally, which is what the `<Map styles={...}>` prop needs.
 */
export interface MapTypeStyle {
  featureType?: string;
  elementType?: string;
  stylers: Record<string, string | number>[];
}

/** One rule, written so the token name stays visible beside the thing it paints. */
function paint(
  featureType: string | undefined,
  elementType: string | undefined,
  stylers: Record<string, string | number>[],
): MapTypeStyle {
  const rule: MapTypeStyle = { stylers };
  if (featureType) rule.featureType = featureType;
  if (elementType) rule.elementType = elementType;
  return rule;
}

/**
 * The dark basemap style, resolved from tokens at call time.
 *
 * Call it inside the component that mounts the map rather than at module scope: at module scope
 * there is no document, so every value would be the compiled fallback and a theme override would
 * never reach the tiles.
 */
export function darkMapStyle(): MapTypeStyle[] {
  const ink = resolveToken("--ink");
  const deep = resolveToken("--deep");
  const well = resolveToken("--well");
  const line = resolveToken("--line");
  const muted = resolveToken("--text-3");

  return [
    // Land, and the default for anything a later rule does not name.
    paint(undefined, "geometry", [{ color: deep }]),
    // Labels: quiet, haloed on the background so a name never competes with a flooded street
    // (SPEC.md 6.7).
    paint(undefined, "labels.text.fill", [{ color: muted }]),
    paint(undefined, "labels.text.stroke", [{ color: ink }, { weight: 2 }]),
    paint(undefined, "labels.icon", [{ visibility: "off" }]),

    // Ward and city boundaries, present but never a line the eye lands on.
    paint("administrative", "geometry", [{ color: line }]),
    paint("administrative.land_parcel", undefined, [{ visibility: "off" }]),
    paint("administrative.neighborhood", "labels", [{ visibility: "off" }]),

    // Shops, schools and parks carry no flood information; the register's own places are drawn
    // by VARUNA's label layer instead.
    paint("poi", undefined, [{ visibility: "off" }]),
    paint("poi.park", "geometry", [{ color: deep }, { visibility: "on" }]),
    paint("poi.park", "labels", [{ visibility: "off" }]),

    // Roads lift off the land, because the road is the thing this product is about.
    paint("road", "geometry", [{ color: well }]),
    paint("road", "geometry.stroke", [{ color: ink }]),
    paint("road.highway", "geometry", [{ color: line }]),
    paint("road.highway", "geometry.stroke", [{ color: ink }]),
    paint("road.local", "labels", [{ visibility: "off" }]),

    // Rail lines exist on the ground but add nothing under water; their stations come from OSM.
    paint("transit", undefined, [{ visibility: "off" }]),

    // The Arabian Sea, Mahim Creek and the Mithi: the deepest tone on the map, so the coast reads
    // as an edge and the tide-locked outfalls sit on something.
    paint("water", "geometry", [{ color: ink }]),
    paint("water", "labels.text.fill", [{ color: muted }]),
  ];
}

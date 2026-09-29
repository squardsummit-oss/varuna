/**
 * Basemap configuration for `CityMap` (SPEC.md section 6.7).
 *
 * The basemap is CARTO "dark matter, no labels" — free with attribution, dark enough that water is
 * the only bright thing on the screen. The style ships without any label layer, so the quiet label
 * layer of section 6.7 is added on top of the style's own vector source: road names from z 15,
 * locality names from z 12, both in `--text-3` so a name never competes with a flooded street.
 */
import type { Map as MapLibreMap, StyleSpecification } from "maplibre-gl";

import { getTheme, themeColor, type Theme } from "@/lib/theme";

/** CARTO dark matter, no labels. Free for any use with the attribution below. */
export const DEFAULT_BASEMAP_STYLE =
  "https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json";

/** Attribution the basemap licence requires; rendered on every map, in every mode. */
export const BASEMAP_ATTRIBUTION = "Basemap: CARTO, OpenStreetMap contributors";

/**
 * The style URL, from `NEXT_PUBLIC_BASEMAP_STYLE` when it carries a usable value.
 * Next inlines an unset public variable as the string "undefined", so that reads as unset —
 * the same rule `lib/site.ts` applies to `NEXT_PUBLIC_SITE_URL`.
 */
export function basemapStyleUrl(): string {
  const raw = process.env.NEXT_PUBLIC_BASEMAP_STYLE;
  if (typeof raw !== "string") return DEFAULT_BASEMAP_STYLE;
  const value = raw.trim();
  if (!value || value === "undefined" || value === "null") return DEFAULT_BASEMAP_STYLE;
  return value;
}

/** South-west then north-east corner, in degrees: `[[west, south], [east, north]]`. */
export type Bbox = readonly [readonly [number, number], readonly [number, number]];

/**
 * City areas of interest (SPEC.md section 3.3). There is no city-config endpoint yet, so these
 * two boxes are the one place the AOI is written down on the client; `CityMap` also takes an
 * explicit `bounds` prop for a nest or an onboarding area drawn by the operator.
 */
export const CITY_BOUNDS = {
  /** MUM-CENTRAL: lon 72.815 to 72.905, lat 18.995 to 19.135 (about 9.5 km by 15.5 km). */
  mumbai: [
    [72.815, 18.995],
    [72.905, 19.135],
  ],
  /** CHN-SOUTH: lon 80.20 to 80.28, lat 12.96 to 13.05 (Velachery, Adyar, T. Nagar). */
  chennai: [
    [80.2, 12.96],
    [80.28, 13.05],
  ],
} as const satisfies Record<string, Bbox>;

export type CityId = keyof typeof CITY_BOUNDS;

/** The AOI of a city, or Mumbai when the city is not one VARUNA has onboarded. */
export function cityBounds(city: string | undefined): Bbox {
  if (city && city in CITY_BOUNDS) return CITY_BOUNDS[city as CityId];
  return CITY_BOUNDS.mumbai;
}

/** Centre of a box, for the hero's static frame and for tests. */
export function boundsCentre(bounds: Bbox): { longitude: number; latitude: number } {
  return {
    longitude: (bounds[0][0] + bounds[1][0]) / 2,
    latitude: (bounds[0][1] + bounds[1][1]) / 2,
  };
}

/**
 * The zoom range the map is designed for: below 9 the AOI is a dot, and 19 is the deepest level
 * the satellite layer still draws real imagery at (`satellite.tsx`, task D-19). The upper bound
 * read 18 while the imagery stopped at 17; both were one level off what Esri serves here.
 *
 * These are documentation, not enforcement: nothing passes them to the deck `MapView`, which
 * clamps at its own default of 20. Wiring them to the controller belongs with whoever next owns
 * `city-map.tsx`; raising the imagery cap is what makes the close view sharp either way.
 */
export const MIN_ZOOM = 9;
export const MAX_ZOOM = 19;

/** Zoom at which road names appear, then locality names (SPEC.md section 6.7). */
export const ROAD_LABEL_MIN_ZOOM = 15;
export const LOCALITY_LABEL_MIN_ZOOM = 12;

/** Layer ids the quiet label layer adds, so a caller can place deck layers beneath them. */
export const ROAD_LABEL_LAYER_ID = "varuna-road-labels";
export const LOCALITY_LABEL_LAYER_ID = "varuna-locality-labels";

/**
 * OpenMapTiles source layers, the schema CARTO's free vector tiles use. If a different style is
 * configured and does not carry them, `addQuietLabels` adds nothing rather than throwing.
 */
const ROAD_NAME_SOURCE_LAYER = "transportation_name";
const PLACE_SOURCE_LAYER = "place";

/** The first vector source in a style, which is the tile set the labels are drawn from. */
function firstVectorSource(style: StyleSpecification | undefined): string | null {
  const sources = style?.sources ?? {};
  for (const [id, source] of Object.entries(sources)) {
    if (source && source.type === "vector") return id;
  }
  return null;
}

/**
 * Adds the quiet label layer to a loaded map.
 *
 * No `text-font` is set: MapLibre labels are signed-distance-field glyphs served by the style's
 * own glyph endpoint, and the token faces (Bricolage Grotesque, Geist) are not published as SDF
 * glyphs. Leaving the property out uses the basemap's own stack rather than naming a font outside
 * the token set; self-hosted token glyphs arrive with the PMTiles basemap (P1).
 *
 * Safe to call twice: existing layers are skipped, so a style reload after a context restore does
 * not duplicate them.
 *
 * The colours are the theme's: `--text-3` on an `--ink` halo is grey on night in the dark theme
 * and grey on paper in the light one, so the labels stay quiet on either ground.
 */
export function addQuietLabels(map: MapLibreMap, theme: Theme = getTheme()): void {
  let style: StyleSpecification | undefined;
  try {
    style = map.getStyle();
  } catch {
    return;
  }
  const source = firstVectorSource(style);
  if (!source) return;

  const paint = {
    "text-color": themeColor("text-3", theme),
    "text-halo-color": themeColor("ink", theme),
    "text-halo-width": 1,
  } as const;

  try {
    if (!map.getLayer(LOCALITY_LABEL_LAYER_ID)) {
      map.addLayer({
        id: LOCALITY_LABEL_LAYER_ID,
        type: "symbol",
        source,
        "source-layer": PLACE_SOURCE_LAYER,
        minzoom: LOCALITY_LABEL_MIN_ZOOM,
        layout: {
          "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
          "text-size": 12,
          "text-letter-spacing": 0.04,
          "text-max-width": 8,
        },
        paint,
      });
    }
    if (!map.getLayer(ROAD_LABEL_LAYER_ID)) {
      map.addLayer({
        id: ROAD_LABEL_LAYER_ID,
        type: "symbol",
        source,
        "source-layer": ROAD_NAME_SOURCE_LAYER,
        minzoom: ROAD_LABEL_MIN_ZOOM,
        layout: {
          "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
          "text-size": 11,
          "symbol-placement": "line",
          "text-max-angle": 30,
        },
        paint,
      });
    }
  } catch {
    // A style without these source layers is a configuration choice, not an error the operator
    // can act on; the map still reads correctly without names.
  }
}

/**
 * True when this browser can create a WebGL context.
 *
 * `CityMap` asks before importing MapLibre at all, which keeps the WebGL bundle out of jsdom
 * (vitest) and out of any browser that would only show a blank canvas. The `WebGL*Context`
 * check comes first so jsdom is answered without calling `getContext`, which jsdom reports as
 * unimplemented.
 */
export function hasWebGl(): boolean {
  if (typeof window === "undefined" || typeof document === "undefined") return false;
  const w = window as Window & {
    WebGL2RenderingContext?: unknown;
    WebGLRenderingContext?: unknown;
  };
  if (w.WebGL2RenderingContext === undefined && w.WebGLRenderingContext === undefined) return false;
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") ?? canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

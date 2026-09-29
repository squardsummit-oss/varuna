/**
 * The public map's offline basemap (task P9.10): VARUNA's own PMTiles archive, drawn by deck.gl.
 *
 * Built by `python -m varuna_city.basemap_tiles` from the city's OpenStreetMap extract (roads,
 * buildings, waterways) and ESA WorldCover (open water), served by
 * `GET /v1/city/{city}/basemap.pmtiles`, and kept by the service worker. It is fetched whole -
 * 4.2 MB for Mumbai - rather than by byte range, so the worker can keep it as one ordinary
 * response and the map can read it from a `Blob` with no network at all.
 *
 * Drawn only while the map is reading a saved forecast. Online, the treated Esri imagery is the
 * ground; offline it cannot be (its licence forbids a cached copy), and this is what replaces it.
 *
 * Colours are tokens, as deck.gl RGBA arrays like the rest of `layers/palette.ts`: open water in
 * `--well`, waterways and streets in `--line` and `--line-strong`, buildings in `--deep`. None of
 * them is the depth ramp, which means water depth and nothing else (SPEC.md 6.2).
 *
 * **Two themes.** The constants below are the dark theme's and are live bindings: when the page
 * switches to light they are reassigned to the daylight mapping (sea in `--line`, streets in
 * `--line-strong`, buildings in `--well` on the paper ground), so a layer built after the switch
 * draws the new ground. Paper sea on paper land would vanish, which is why light does not reuse
 * the dark theme's token for each role.
 */

import { TileLayer } from "@deck.gl/geo-layers";
import { GeoJsonLayer } from "@deck.gl/layers";
import { MVTLoader } from "@loaders.gl/mvt";
import { PMTilesSource, type PMTilesTileSource } from "@loaders.gl/pmtiles";

import { apiUrl } from "@/lib/api/client";
import {
  getTheme,
  subscribeTheme,
  themeRgbaTable,
  type Theme,
  type ThemeColorName,
} from "@/lib/theme";

type Rgba = [number, number, number, number];

/** `--well` #17233B: open water, a shade lighter than the `--ink` land around it. */
export let WATER_FILL: Rgba = [23, 35, 59, 255];
/** `--line-strong` #33436A: creeks, nallahs and canals as lines. */
export let WATERWAY_LINE: Rgba = [51, 67, 106, 220];
/** `--line` #24314F: every street, under the city's own dry-street layer. */
export let ROAD_LINE: Rgba = [36, 49, 79, 255];
/** `--deep` #111A2E with a `--line` hairline, as the online map draws buildings. */
export let BUILDING_FILL: Rgba = [17, 26, 46, 235];
export let BUILDING_LINE: Rgba = [36, 49, 79, 170];

/** The offline ground's colours in a theme. `offlineBasemapPalette("dark")` equals the constants
 * above exactly. */
export function offlineBasemapPalette(theme: Theme): {
  WATER_FILL: Rgba;
  WATERWAY_LINE: Rgba;
  ROAD_LINE: Rgba;
  BUILDING_FILL: Rgba;
  BUILDING_LINE: Rgba;
} {
  const table = themeRgbaTable(theme);
  const at = (name: ThemeColorName, alpha: number): Rgba => {
    const [r, g, b] = table[name];
    return [r, g, b, alpha];
  };
  if (theme === "light") {
    return {
      WATER_FILL: at("line", 255),
      WATERWAY_LINE: at("line-strong", 220),
      ROAD_LINE: at("line-strong", 170),
      BUILDING_FILL: at("well", 235),
      BUILDING_LINE: at("line", 200),
    };
  }
  return {
    WATER_FILL: at("well", 255),
    WATERWAY_LINE: at("line-strong", 220),
    ROAD_LINE: at("line", 255),
    BUILDING_FILL: at("deep", 235),
    BUILDING_LINE: at("line", 170),
  };
}

let appliedTheme: Theme = "dark";

function applyOfflinePalette(theme: Theme): void {
  if (theme === appliedTheme) return;
  appliedTheme = theme;
  const p = offlineBasemapPalette(theme);
  WATER_FILL = p.WATER_FILL;
  WATERWAY_LINE = p.WATERWAY_LINE;
  ROAD_LINE = p.ROAD_LINE;
  BUILDING_FILL = p.BUILDING_FILL;
  BUILDING_LINE = p.BUILDING_LINE;
}

applyOfflinePalette(getTheme());
subscribeTheme(() => applyOfflinePalette(getTheme()));

/** Credit the archive must carry (ODbL and CC BY 4.0), used if its metadata cannot be read. */
export const OFFLINE_BASEMAP_ATTRIBUTION =
  "© OpenStreetMap contributors (ODbL); Water: ESA WorldCover 2021 (CC BY 4.0)";

/** Zooms the archive holds; deck over-zooms the last one rather than asking for tiles past it. */
const MIN_ZOOM = 11;
const MAX_ZOOM = 16;

export interface OfflineBasemap {
  source: PMTilesTileSource;
  attribution: string;
  bytes: number;
}

type BasemapFeature = {
  properties?: { layerName?: string; class?: string } | null;
};

export function basemapPath(city: string): string {
  return `/v1/city/${encodeURIComponent(city)}/basemap.pmtiles`;
}

/** Fetch the whole archive (from the worker's copy when offline) and open it. */
export async function loadOfflineBasemap(
  city: string,
  signal?: AbortSignal,
): Promise<OfflineBasemap> {
  const response = await fetch(apiUrl(basemapPath(city)), { signal });
  if (!response.ok) throw new Error(`Offline basemap: HTTP ${response.status}`);
  const blob = await response.blob();
  const source = PMTilesSource.createDataSource(blob, {});
  let attribution = OFFLINE_BASEMAP_ATTRIBUTION;
  try {
    const metadata = (await source.pmtiles.getMetadata()) as { attribution?: unknown } | null;
    if (typeof metadata?.attribution === "string" && metadata.attribution) {
      attribution = metadata.attribution;
    }
  } catch {
    // The credit is known either way; an unreadable metadata block only loses nothing.
  }
  return { source, attribution, bytes: blob.size };
}

export function basemapFillColor(feature: BasemapFeature): Rgba {
  return feature.properties?.layerName === "water" ? WATER_FILL : BUILDING_FILL;
}

export function basemapLineColor(feature: BasemapFeature): Rgba {
  switch (feature.properties?.layerName) {
    case "waterways":
      return WATERWAY_LINE;
    case "buildings":
      return BUILDING_LINE;
    case "water":
      return [0, 0, 0, 0];
    default:
      return ROAD_LINE;
  }
}

/** Line width in pixels: arterials read as arterials, lanes as hairlines. */
export function basemapLineWidth(feature: BasemapFeature): number {
  const layer = feature.properties?.layerName;
  if (layer === "buildings") return 0.4;
  if (layer === "waterways") return 1.5;
  switch (feature.properties?.class) {
    case "motorway":
    case "trunk":
    case "primary":
      return 2.5;
    case "secondary":
    case "tertiary":
      return 1.5;
    default:
      return 0.8;
  }
}

/** The deck.gl layers for one opened archive; none when there is nothing to draw. */
export function offlineBasemapLayers(basemap: OfflineBasemap | null): unknown[] {
  if (!basemap) return [];
  const { source } = basemap;
  return [
    new TileLayer({
      id: "offline-basemap",
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
      tileSize: 512,
      refinementStrategy: "best-available",
      pickable: false,
      getTileData: async ({ index }: { index: { x: number; y: number; z: number } }) => {
        const buffer = await source.getTile(index);
        if (!buffer) return [];
        // Parsed here on the main thread: the bundler cannot load loaders.gl's worker (the same
        // reason MapLibre's vector tiles never worked on this map), and a tile is a few KB.
        return MVTLoader.parseSync?.(buffer, {
          mvt: {
            shape: "geojson",
            coordinates: "wgs84",
            tileIndex: index,
            layerProperty: "layerName",
          },
        } as never) as unknown;
      },
      renderSubLayers: (props: { id: string; data: unknown }) =>
        new GeoJsonLayer({
          id: `${props.id}-geojson`,
          data: (props.data ?? []) as never,
          filled: true,
          stroked: true,
          getFillColor: basemapFillColor as never,
          getLineColor: basemapLineColor as never,
          getLineWidth: basemapLineWidth as never,
          // The accessors read the theme's live colours, so a layer built after a switch
          // re-evaluates them rather than keeping the other theme's ground.
          updateTriggers: { getFillColor: appliedTheme, getLineColor: appliedTheme },
          lineWidthUnits: "pixels",
          lineWidthMinPixels: 0.4,
          pickable: false,
        }),
    } as never),
  ];
}

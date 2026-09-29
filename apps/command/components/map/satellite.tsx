/**
 * The satellite basemap (SPEC.md 6.7's basemap slot, filled at last).
 *
 * **Why this works where MapLibre did not.** The note at the top of `CityMap` records why this map
 * has run without a basemap: MapLibre decodes vector tiles in a web worker, that worker does not
 * load under this bundler, and a permanently blank basemap is worse than none. Raster imagery has
 * no worker and no style pipeline - deck.gl's own `TileLayer` fetches an image per tile and draws
 * it through a `BitmapLayer`, which is the same path the depth raster already takes. So the thing
 * that broke does not exist here.
 *
 * **The imagery is treated, not shown raw.** SPEC.md 6.1 is explicit that water is the one
 * memorable thing on this screen and everything else stays quiet, and raw satellite imagery is the
 * opposite of quiet: bright greens and browns at full saturation, competing with the depth ramp's
 * blue-to-red for exactly the attention the depth ramp needs. So the tiles are drawn dim and a
 * `--ink` scrim is laid over them. What survives is the *texture* a judge reads as a real city -
 * the coastline, the creeks, the airport, the density gradient from Colaba to Andheri - under
 * water that is still the brightest thing on the map.
 *
 * **Offline (SPEC.md 17).** Tiles come from a network service, and the finale may have none. A
 * failed tile is drawn as nothing rather than as an error, so the map falls back to exactly what
 * it renders today: the city's own building footprints and street network. Nothing is lost that
 * was not there before; the imagery is a gain when there is a network and silent when there is not.
 */

import { BitmapLayer } from "@deck.gl/layers";
import { TileLayer } from "@deck.gl/geo-layers";

import { getTheme, type Theme } from "@/lib/theme";

/**
 * Esri World Imagery, the standard free-with-attribution aerial basemap.
 *
 * Note the `{z}/{y}/{x}` order - Esri's REST tile endpoint puts the row before the column, which
 * is the opposite of the `{z}/{x}/{y}` almost every other service uses. Getting it the usual way
 * round returns tiles from somewhere else entirely, or a 404, and the map looks broken in a way
 * that does not point at the cause.
 */
export const SATELLITE_URL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

/**
 * Esri's reference overlay: localities, neighbourhoods and major road names on transparent tiles.
 *
 * This is the second half of a hybrid satellite view. Imagery alone shows a judge *texture* -
 * that is plainly a real city - but it cannot answer "which junction is that?", and the whole
 * product is about named streets. The reference layer is drawn over the water rather than under
 * it, because a label the depth ramp paints over is a label nobody can read.
 */
export const LABELS_URL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}";

/**
 * The same reference labels drawn for a light ground: Esri's light grey canvas reference, dark
 * grey type with no halo. The dark-theme tiles are white type in a black halo, which reads over
 * any photograph but looks like a night map pasted onto a day one. Same service, same order of
 * `{z}/{y}/{x}`, same coverage over Mumbai (checked to z 17 on 2026-09-29).
 */
export const LABELS_URL_LIGHT =
  "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}";

/** Attribution the map must carry while the imagery is drawn. */
export const SATELLITE_ATTRIBUTION =
  "Imagery: Esri, Maxar, Earthstar Geographics and the GIS User Community";

/** The whole map's credit, in one line.
 *
 * Both halves are always true: the imagery is Esri's when it loads, and every line and polygon
 * over it - the streets, the buildings, the drain graph, the terrain the depths were solved on -
 * is VARUNA's own derivation from open data. Splitting them across two lines put one behind the
 * "Reconstructed replay" chip; one line is also easier to read at a glance from across a room. */
export const MAP_ATTRIBUTION =
  "Imagery: Esri, Maxar · Roads: OpenStreetMap · Terrain: Copernicus GLO-30";

/**
 * Tile pyramid limits. Below 8 the AOI is a speck.
 *
 * The upper limit was 17 on the belief that Esri had no imagery above it here; it has. Esri's
 * World Imagery publishes to level 23 over Mumbai, and at 17 the last real tile was being
 * stretched across every closer view, so a judge zooming to a junction got a blur where the
 * product's whole claim is the street. 19 is the measured stopping point rather than the
 * service's: see `docs/QA.md` for the tile count and cache size of a 12 to 19 zoom over
 * Hindmata, which is what decides how far it is worth going.
 */
const MIN_ZOOM = 8;
const MAX_ZOOM = 19;
const TILE_SIZE = 256;

/**
 * How much of the imagery survives the treatment, per theme.
 *
 * **Dark is 1, because 1 is what has always been drawn.** This constant read 0.78 (and 0.78 x 0.7
 * under a raster) from P6.1 to 2026-09-29, but `renderSubLayers` built each tile's `BitmapLayer`
 * without the parent's props, and deck.gl forwards a composite layer's `opacity` only through
 * those props - so every tile was drawn opaque and the only treatment the dark map ever had was
 * the `--ink` scrim in `city-map.tsx`. That look is the one the team approved and recorded the demo
 * on, so dark keeps it exactly; the opacity now reaches the GPU, which is what the light theme
 * needs.
 *
 * **Light washes the photograph toward paper.** The map's ground and its scrim are `--ink`, paper
 * in this theme. Measured on /console at 1440 x 900 (2026-09-29, `scratchpad/ux/theme/ground.mjs`):
 * drawn opaque, the aerial left a mid-grey ground (median rgb 141 145 139) on which the 5-15 cm
 * blue read 1.15:1 and the pale dry streets were the brightest lines on screen. At 0.3 the
 * photograph keeps about a fifth of its weight after the console's 30 % scrim: the coast, the
 * creeks and the street grain still read as Mumbai, the dry streets recede, and the water is again
 * the only saturated thing (UI_UX.md 4).
 */
const IMAGERY_OPACITY = 1;
const IMAGERY_OPACITY_LIGHT = 0.3;

/** Under a depth raster the light imagery drops to this share of its strength. Dark stays at 1
 * for the reason above. */
const RASTER_DIMMING_LIGHT = 0.7;

/** The imagery's layer opacity for a theme, with or without a depth raster over it. */
export function imageryOpacity(theme: Theme, dimmed = false): number {
  if (theme !== "light") return IMAGERY_OPACITY;
  return dimmed ? IMAGERY_OPACITY_LIGHT * RASTER_DIMMING_LIGHT : IMAGERY_OPACITY_LIGHT;
}

/** Tiles kept in GPU memory. Enough for a scrub across the AOI without refetching. */
const MAX_CACHE_TILES = 220;

export interface SatelliteOptions {
  /** Drawn only when true; the layer is not built at all otherwise. */
  enabled: boolean;
  /** Dimmer still under a depth raster, which is itself a translucent sheet over the city. */
  dimmed?: boolean;
  /** The theme to treat the imagery for. Defaults to the one on the page now; a host that
   * memoises its layers passes `useTheme().theme` and lists it among the dependencies. */
  theme?: Theme;
}

/** How strongly the reference labels read: full strength in both themes. The dark value read
 * 0.85 until 2026-09-29 but never reached the GPU (see `IMAGERY_OPACITY`), so 1 is what the dark
 * map has always shown; the light tiles carry no halo and need full strength to read on a pale
 * ground. */
const LABEL_OPACITY = 1;
const LABEL_OPACITY_LIGHT = 1;

/** Below this the labels are country and state names, which say nothing about a city. */
const LABEL_MIN_ZOOM = 10;

/** Esri's place labels, drawn *over* the run so water never paints over a street name.
 *
 * Each theme has its own tile set and its own layer id, so a switch starts a fresh tile cache
 * instead of drawing night labels and day labels side by side while the new tiles arrive. */
export function labelLayers({
  enabled,
  theme = getTheme(),
}: {
  enabled: boolean;
  theme?: Theme;
}): unknown[] {
  if (!enabled) return [];
  const light = theme === "light";
  return [
    new TileLayer({
      id: light ? "place-labels-light" : "place-labels",
      data: light ? LABELS_URL_LIGHT : LABELS_URL,
      minZoom: LABEL_MIN_ZOOM,
      maxZoom: MAX_ZOOM,
      tileSize: TILE_SIZE,
      maxCacheSize: MAX_CACHE_TILES,
      refinementStrategy: "best-available",
      maxRequests: 12,
      onTileError: () => undefined,
      pickable: false,
      opacity: light ? LABEL_OPACITY_LIGHT : LABEL_OPACITY,
      renderSubLayers: (props) => {
        const box = (props.tile as { boundingBox: number[][] }).boundingBox;
        const [west, south] = box[0] as [number, number];
        const [east, north] = box[1] as [number, number];
        return new BitmapLayer({
          id: props.id,
          image: props.data as never,
          bounds: [west, south, east, north],
          opacity: props.opacity,
          pickable: false,
        });
      },
    }),
  ];
}

/** The basemap layers, bottom of the stack. Empty when the basemap is off. */
export function satelliteLayers({
  enabled,
  dimmed = false,
  theme = getTheme(),
}: SatelliteOptions): unknown[] {
  if (!enabled) return [];

  return [
    new TileLayer({
      id: "satellite",
      data: SATELLITE_URL,
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
      tileSize: TILE_SIZE,
      maxCacheSize: MAX_CACHE_TILES,
      // Show whatever is already decoded while the right zoom loads. Without this the map is
      // empty until every tile at the target level has arrived, which over a venue's network is
      // the first twenty seconds of the demo; with it a single coarse tile fills the AOI almost
      // at once and sharpens underneath the water.
      refinementStrategy: "best-available",
      // Esri answers quickly and the AOI is about thirty tiles at the fitted zoom; letting them
      // go out together costs nothing and removes the staircase of a serialised fetch.
      maxRequests: 16,
      // A tile that fails - no network, a rate limit, a gap in coverage - is simply not drawn.
      // deck logs it once and carries on, and the city's own GIS shows through underneath.
      onTileError: () => undefined,
      pickable: false,
      opacity: imageryOpacity(theme, dimmed),
      // deck types `tile.boundingBox` as `number[][]`, so the corners are read positionally.
      renderSubLayers: (props) => {
        const box = (props.tile as { boundingBox: number[][] }).boundingBox;
        const [west, south] = box[0] as [number, number];
        const [east, north] = box[1] as [number, number];
        return new BitmapLayer({
          id: props.id,
          image: props.data as never,
          bounds: [west, south, east, north],
          // deck.gl forwards the tile layer's opacity only through these props; without it every
          // tile was drawn opaque whatever `imageryOpacity` said (see the note above).
          opacity: props.opacity,
          // The imagery is a ground, never a target: picking it would put a tile under every
          // hover instead of the street the operator is pointing at.
          pickable: false,
        });
      },
    }),
  ];
}

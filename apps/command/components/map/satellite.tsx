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
 * How much of the imagery survives the treatment.
 *
 * Low on purpose. At full strength the imagery reads as the subject and the water reads as an
 * overlay on it; at this strength the city is a ground the water sits on, which is the order
 * SPEC.md 6.1 asks for. It is still plainly a photograph of Mumbai.
 */
const IMAGERY_OPACITY = 0.78;

/** Tiles kept in GPU memory. Enough for a scrub across the AOI without refetching. */
const MAX_CACHE_TILES = 220;

export interface SatelliteOptions {
  /** Drawn only when true; the layer is not built at all otherwise. */
  enabled: boolean;
  /** Dimmer still under a depth raster, which is itself a translucent sheet over the city. */
  dimmed?: boolean;
}

/** How strongly the reference labels read. Bright enough to be legible over dark imagery,
 * dim enough that they are furniture rather than content. */
const LABEL_OPACITY = 0.85;

/** Below this the labels are country and state names, which say nothing about a city. */
const LABEL_MIN_ZOOM = 10;

/** Esri's place labels, drawn *over* the run so water never paints over a street name. */
export function labelLayers({ enabled }: { enabled: boolean }): unknown[] {
  if (!enabled) return [];
  return [
    new TileLayer({
      id: "place-labels",
      data: LABELS_URL,
      minZoom: LABEL_MIN_ZOOM,
      maxZoom: MAX_ZOOM,
      tileSize: TILE_SIZE,
      maxCacheSize: MAX_CACHE_TILES,
      refinementStrategy: "best-available",
      maxRequests: 12,
      onTileError: () => undefined,
      pickable: false,
      opacity: LABEL_OPACITY,
      renderSubLayers: (props) => {
        const box = (props.tile as { boundingBox: number[][] }).boundingBox;
        const [west, south] = box[0] as [number, number];
        const [east, north] = box[1] as [number, number];
        return new BitmapLayer({
          id: props.id,
          image: props.data as never,
          bounds: [west, south, east, north],
          pickable: false,
        });
      },
    }),
  ];
}

/** The basemap layers, bottom of the stack. Empty when the basemap is off. */
export function satelliteLayers({ enabled, dimmed = false }: SatelliteOptions): unknown[] {
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
      opacity: dimmed ? IMAGERY_OPACITY * 0.7 : IMAGERY_OPACITY,
      // deck types `tile.boundingBox` as `number[][]`, so the corners are read positionally.
      renderSubLayers: (props) => {
        const box = (props.tile as { boundingBox: number[][] }).boundingBox;
        const [west, south] = box[0] as [number, number];
        const [east, north] = box[1] as [number, number];
        return new BitmapLayer({
          id: props.id,
          image: props.data as never,
          bounds: [west, south, east, north],
          // The imagery is a ground, never a target: picking it would put a tile under every
          // hover instead of the street the operator is pointing at.
          pickable: false,
        });
      },
    }),
  ];
}

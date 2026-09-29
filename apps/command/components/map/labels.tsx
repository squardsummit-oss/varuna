/**
 * VARUNA's own map labels: street names, facilities and the places the register names.
 *
 * Esri's reference tiles (`satellite.tsx`) label localities and arterials. They do not label
 * *this* product's subjects - the street the forecast is about, the hospital the route runs to,
 * the junction the register calls chronic - because those come from the city VARUNA built, not
 * from a basemap. So they are drawn here, from the same `segments`, `assets` and `hotspots`
 * layers everything else on the map reads.
 *
 * **Everything is zoom-gated**, and that is the whole design. Twenty-one thousand street names
 * at once is a grey smear; the same names three at a time under the cursor are a map. So:
 * facilities appear when the operator is looking at a district, street names when they are
 * looking at a street, and the cap is on *what is in view* rather than on the whole city.
 */

import { IconLayer, TextLayer } from "@deck.gl/layers";

import { getTheme, subscribeTheme, themeRgbaTable, type Theme } from "@/lib/theme";

import type { SegmentPath } from "./city-map";

/** One named thing on the map. */
export interface MapLabel {
  id: string;
  text: string;
  lon: number;
  lat: number;
  kind: "street" | "hospital" | "fire_station" | "station" | "hotspot";
}

/** Zoom at which each kind of label starts to draw (SPEC.md 6.7: labels at z >= 12 and z >= 15). */
const MIN_ZOOM: Record<MapLabel["kind"], number> = {
  hotspot: 11.5,
  hospital: 12.5,
  station: 13.5,
  fire_station: 13.5,
  street: 15,
};

/** Labels drawn at once. Past this the map is text, not a map. */
const MAX_LABELS = 260;

type Rgba = [number, number, number, number];

/** `--text` over an `--ink` halo, and `--tide` for the register's chronic junctions, which are the
 * map's own subject. In dark that is light type on a dark halo; in light it inverts to dark type
 * on a paper halo, which is how a daylight map is read. Either way legible over any imagery. */
function labelColours(theme: Theme): { fill: Rgba; halo: Rgba; hotspot: Rgba; theme: Theme } {
  const table = themeRgbaTable(theme);
  const at = (name: "text" | "ink" | "tide", alpha: number): Rgba => {
    const [r, g, b] = table[name];
    return [r, g, b, alpha];
  };
  return { fill: at("text", 255), halo: at("ink", 235), hotspot: at("tide", 255), theme };
}

let colours = labelColours(getTheme());
subscribeTheme(() => {
  if (getTheme() !== colours.theme) colours = labelColours(getTheme());
});

/** A colour update trigger that also changes with the theme. In dark it is the bare count, so a
 * dark layer hands deck exactly the props the equivalence fixtures recorded. */
function colourTrigger(count: number): number | string {
  return colours.theme === "dark" ? count : `${count}-${colours.theme}`;
}

const SIZE: Record<MapLabel["kind"], number> = {
  hotspot: 13,
  hospital: 12,
  station: 11,
  fire_station: 11,
  street: 10,
};

/** The midpoint of a path, as the anchor for its name. */
function midpoint(path: [number, number][]): [number, number] | null {
  if (path.length === 0) return null;
  const middle = path[Math.floor(path.length / 2)];
  return middle ? [middle[0], middle[1]] : null;
}

/**
 * Street-name labels from the run's own segments, one per distinct name.
 *
 * One per *name*, not one per segment: OSM splits Dr Babasaheb Ambedkar Marg into eighty
 * segments, and eighty copies of that name stacked along one road is the thing that makes a
 * labelled map unreadable.
 */
export function streetLabels(segments: readonly SegmentPath[]): MapLabel[] {
  const seen = new Set<string>();
  const out: MapLabel[] = [];
  for (const segment of segments) {
    const name = segment.name?.trim();
    if (!name || seen.has(name)) continue;
    const point = midpoint(segment.path);
    if (!point) continue;
    seen.add(name);
    out.push({ id: segment.id, text: name, lon: point[0], lat: point[1], kind: "street" });
  }
  return out;
}

export interface LabelOptions {
  labels: readonly MapLabel[];
  zoom: number;
  /** Framed viewport, so only what is on screen counts against the cap. */
  bounds?: readonly (readonly [number, number])[] | null;
}

/**
 * Which labels to draw at this zoom, nearest the centre of the view first.
 *
 * The cap is applied *after* the viewport filter, so zooming in reveals more of the street
 * network rather than the same 260 labels the whole city started with.
 */
export function visibleLabels({ labels, zoom, bounds }: LabelOptions): MapLabel[] {
  const inView = labels.filter((label) => {
    if (zoom < MIN_ZOOM[label.kind]) return false;
    if (!bounds || bounds.length < 2) return true;
    const [west, south] = bounds[0];
    const [east, north] = bounds[1];
    return label.lon >= west && label.lon <= east && label.lat >= south && label.lat <= north;
  });
  if (inView.length <= MAX_LABELS) return inView;

  // Keep the ones nearest the middle of the view: that is where the operator is looking.
  const [west, south] = bounds?.[0] ?? [0, 0];
  const [east, north] = bounds?.[1] ?? [0, 0];
  const cx = (west + east) / 2;
  const cy = (south + north) / 2;
  return [...inView]
    .sort((a, b) => {
      const da = (a.lon - cx) ** 2 + (a.lat - cy) ** 2;
      const db = (b.lon - cx) ** 2 + (b.lat - cy) ** 2;
      // Facilities and chronic junctions outrank street names at the same distance: they are
      // what an operator is scanning for.
      const rank = MIN_ZOOM[a.kind] - MIN_ZOOM[b.kind];
      return rank !== 0 ? rank : da - db;
    })
    .slice(0, MAX_LABELS);
}

/** The text and marker layers for a set of labels. Empty when nothing qualifies at this zoom. */
export function labelTextLayers(labels: MapLabel[]): unknown[] {
  if (labels.length === 0) return [];
  return [
    new TextLayer<MapLabel>({
      id: "map-labels",
      data: labels,
      getPosition: (d) => [d.lon, d.lat],
      getText: (d) => d.text,
      getSize: (d) => SIZE[d.kind],
      sizeUnits: "pixels",
      getColor: (d) => (d.kind === "hotspot" ? colours.hotspot : colours.fill),
      // The halo is what makes a label readable over bright imagery and over the depth ramp
      // alike, without a panel behind it.
      outlineWidth: 3,
      outlineColor: colours.halo,
      fontSettings: { sdf: true, fontSize: 64, buffer: 8 },
      fontFamily: "var(--font-sans), system-ui, sans-serif",
      fontWeight: 600,
      getTextAnchor: "middle",
      getAlignmentBaseline: "bottom",
      getPixelOffset: (d) => (d.kind === "street" ? [0, 0] : [0, -12]),
      characterSet: "auto",
      pickable: false,
      updateTriggers: {
        getText: labels.length,
        getColor: colourTrigger(labels.length),
        getSize: labels.length,
      },
    }),
  ];
}

/** A small dot under each facility label, so the name has something to point at. */
export function labelMarkerLayers(labels: MapLabel[]): unknown[] {
  const facilities = labels.filter((l) => l.kind !== "street");
  if (facilities.length === 0) return [];
  return [
    new IconLayer<MapLabel>({
      id: "label-markers",
      data: facilities,
      // A generated 1 px sprite tinted per feature: no atlas to ship and no network fetch, which
      // is what SPEC.md 17's offline requirement needs from an icon layer.
      iconAtlas: DOT_SPRITE,
      iconMapping: { dot: { x: 0, y: 0, width: 16, height: 16, mask: true, anchorY: 8 } },
      getIcon: () => "dot",
      getPosition: (d) => [d.lon, d.lat],
      getSize: (d) => (d.kind === "hotspot" ? 9 : 7),
      sizeUnits: "pixels",
      getColor: (d) => (d.kind === "hotspot" ? colours.hotspot : colours.fill),
      pickable: false,
      updateTriggers: { getColor: colourTrigger(facilities.length), getSize: facilities.length },
    }),
  ];
}

/** A 16 px disc as a data URI: the **mask** an `IconLayer` tints per feature.
 *
 * The fill is not a colour. `mask: true` makes deck read only this sprite's alpha and paint it
 * with `getColor`, so white here means "fully opaque" and the token colours in `getColor` above
 * are what actually reach the screen. A design token would be misleading, not safer. */
const DOT_SPRITE =
  "data:image/svg+xml;charset=utf-8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">' +
      // lint-design-allow-next-line: alpha mask, not a colour; see the note above.
      '<circle cx="8" cy="8" r="6" fill="#fff"/></svg>',
  );

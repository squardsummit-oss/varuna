/**
 * A small Mumbai for the MO1 equivalence tests: two dry streets, three wet ones, two surcharging
 * manholes near Hindmata, the KEM-to-Sion trip and a sourced pin at Gandhi Market. Every value is
 * test input, not a product; the tests compare layer props, not what the numbers mean.
 */

// Imported through `city-map`, which re-exports them, so the same fixture ran against the monolith.
import type {
  BuildingPolygon,
  CityMapProps,
  DrainPath,
  HotspotRing,
  Isochrone,
  RouteLine,
  SegmentPath,
  SurchargeNode,
  TruthPin,
} from "../../city-map";
import type { MapLabel } from "../../labels";
import { fakeBitmap } from "./serialize";

export const baseSegments: SegmentPath[] = [
  {
    id: "S-dry-1",
    path: [
      [72.84, 19.01],
      [72.842, 19.012],
    ],
    depthCm: [],
    width: 3,
    name: "Dr Babasaheb Ambedkar Road",
  },
  {
    id: "S-dry-2",
    path: [
      [72.856, 19.026],
      [72.858, 19.028],
      [72.859, 19.03],
    ],
    depthCm: [],
    width: 1,
  },
];

export const segments: SegmentPath[] = [
  {
    id: "S-wet-1",
    path: [
      [72.841, 19.012],
      [72.843, 19.013],
    ],
    depthCm: [2, 18, 47, 70],
    pGt: { "30": [0, 0.1, 0.8, 1] },
    width: 4,
    name: "Hindmata junction",
    deltaCm: -12,
  },
  {
    id: "S-wet-2",
    path: [
      [72.857, 19.027],
      [72.858, 19.0275],
    ],
    depthCm: [0, 9, 33, 29],
    width: 2,
    name: "King's Circle",
    deltaCm: 0.3,
  },
  {
    id: "S-wet-3",
    path: [
      [72.862, 19.039],
      [72.863, 19.04],
      [72.864, 19.041],
    ],
    depthCm: [5, 61, 12],
    width: 6,
    deltaCm: 25,
  },
];

export const surcharge: SurchargeNode[] = [
  { id: "N-1", lon: 72.8412, lat: 19.0121, q: 0.4 },
  { id: "N-2", lon: 72.8601, lat: 19.035 },
];

export const hotspots: HotspotRing[] = [
  { id: "H-hindmata", name: "Hindmata junction", lon: 72.841, lat: 19.012 },
  { id: "H-sion", name: "Sion Circle", lon: 72.862, lat: 19.039 },
];

export const buildings: BuildingPolygon[] = [
  [
    [72.84, 19.0],
    [72.8405, 19.0],
    [72.8405, 19.0005],
    [72.84, 19.0],
  ],
];

export const drains: DrainPath[] = [
  { path: [[72.841, 19.012], [72.8412, 19.0121]], beta: 0.1, diameter: 0.45 },
  { path: [[72.85, 19.02], [72.851, 19.021]], beta: 0.3, diameter: 0.9 },
  { path: [[72.86, 19.03], [72.861, 19.031]], beta: 0.6, diameter: 1.5 },
  { path: [[72.87, 19.04], [72.871, 19.041]], beta: 0.8, diameter: 3 },
];

export const routes: RouteLine[] = [
  {
    id: "naive",
    kind: "naive",
    path: [
      [72.841, 19.003],
      [72.842, 19.02],
      [72.862, 19.041],
    ],
  },
  {
    id: "varuna",
    kind: "varuna",
    path: [
      [72.841, 19.003],
      [72.835, 19.015],
      [72.85, 19.03],
      [72.862, 19.041],
    ],
  },
  {
    id: "alt-1",
    kind: "alternate",
    path: [
      [72.841, 19.003],
      [72.862, 19.041],
    ],
  },
  {
    id: "avoided-1",
    kind: "avoided",
    path: [
      [72.841, 19.012],
      [72.843, 19.013],
    ],
  },
];

export const isochrones: Isochrone[] = [
  {
    minutes: 5,
    rings: [
      [
        [72.84, 19.0],
        [72.845, 19.0],
        [72.845, 19.005],
        [72.84, 19.0],
      ],
    ],
  },
  {
    minutes: 15,
    rings: [
      [
        [72.83, 18.99],
        [72.86, 18.99],
        [72.86, 19.02],
        [72.83, 18.99],
      ],
    ],
  },
  { minutes: 10, rings: [] },
  { minutes: 20, rings: [[[72.8, 18.9], [72.9, 18.9], [72.9, 19.1], [72.8, 18.9]]] },
];

export const truthPins: TruthPin[] = [
  // Gandhi Market began dropping at 700 ms on the tests' mocked clock; Milan subway has landed.
  { id: "P-1", lon: 72.858, lat: 19.032, name: "Gandhi Market", dropStartMs: 700 },
  { id: "P-2", lon: 72.84, lat: 19.079, name: "Milan subway" },
];

export const labels: MapLabel[] = [
  { id: "L-kem", text: "KEM Hospital", lon: 72.841, lat: 19.003, kind: "hospital" },
  { id: "L-hindmata", text: "Hindmata junction", lon: 72.841, lat: 19.012, kind: "hotspot" },
];

export const frames = [fakeBitmap(0), fakeBitmap(1), null, fakeBitmap(3)];

export const rasterBounds: [number, number, number, number] = [72.815, 18.995, 72.905, 19.135];

/** Everything on, as the console draws it. */
export function consoleProps(overrides: Partial<CityMapProps> = {}): CityMapProps {
  return {
    frames,
    rasterBounds,
    baseSegments,
    segments,
    surcharge,
    hotspots,
    buildings,
    drains,
    routes,
    isochrones,
    truthPins,
    labels,
    step: 1,
    showDrains: true,
    // Asked for explicitly since `CityMap`'s default turned buildings off: "everything on" is what
    // the monolith fixture captured, and saying so here keeps that picture byte-identical.
    showBuildings: true,
    selectedHotspotId: "H-hindmata",
    onSegmentPick: () => {},
    ...overrides,
  };
}

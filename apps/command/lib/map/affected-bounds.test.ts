import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Bbox } from "@/components/map/basemap";
import {
  affectedFrame,
  affectedFrameAt,
  boundsOverlap,
  clampInside,
  denseFrame,
  extentFrame,
  pathLengthM,
  pathsFrame,
  peakStep,
  useSettledBounds,
  WHOLE,
  type DepthStreet,
  type WeightedPoint,
} from "./affected-bounds";

/** The Mumbai AOI (SPEC.md 3.3), the box every frame here is kept inside. */
const AOI: Bbox = [
  [72.815, 18.995],
  [72.905, 19.135],
];

const M_PER_DEG_LAT = 110_540;
const kx = (lat: number) => 111_320 * Math.cos((lat * Math.PI) / 180);

/** A point `dx`, `dy` metres from (lon, lat). */
function offset(lon: number, lat: number, dx: number, dy: number): [number, number] {
  return [lon + dx / kx(lat), lat + dy / M_PER_DEG_LAT];
}

/** Width and height of a box in metres. */
function spanM(box: Bbox): { w: number; h: number } {
  const lat = (box[0][1] + box[1][1]) / 2;
  return { w: (box[1][0] - box[0][0]) * kx(lat), h: (box[1][1] - box[0][1]) * M_PER_DEG_LAT };
}

function contains(box: Bbox, [lon, lat]: readonly [number, number]): boolean {
  return lon >= box[0][0] && lon <= box[1][0] && lat >= box[0][1] && lat <= box[1][1];
}

/** `n` points on a regular lattice `stepM` apart, centred on (lon, lat). */
function lattice(lon: number, lat: number, n: number, stepM: number, weight = 1): WeightedPoint[] {
  const side = Math.ceil(Math.sqrt(n));
  const out: WeightedPoint[] = [];
  for (let i = 0; i < n; i += 1) {
    const [x, y] = offset(
      lon,
      lat,
      ((i % side) - side / 2) * stepM,
      (Math.floor(i / side) - side / 2) * stepM,
    );
    out.push({ lon: x, lat: y, weight });
  }
  return out;
}

// Hindmata and Kurla LBS Marg, from the register's approximate positions (SPEC.md 3.3): 8 km apart.
const HINDMATA: [number, number] = [72.841, 19.012];
const KURLA: [number, number] = [72.879, 19.066];

describe("denseFrame", () => {
  it("returns null when nothing carries weight", () => {
    expect(denseFrame([])).toBeNull();
    expect(denseFrame([{ lon: 72.84, lat: 19.01, weight: 0 }])).toBeNull();
    expect(denseFrame([{ lon: Number.NaN, lat: 19.01, weight: 5 }])).toBeNull();
  });

  it("frames a compact flood whole", () => {
    const points = lattice(...HINDMATA, 64, 200);
    const frame = denseFrame(points, { within: AOI })!;
    expect(frame.share).toBe(1);
    for (const p of points) expect(contains(frame.bounds, [p.lon, p.lat])).toBe(true);
  });

  it("is not stretched by one far street", () => {
    const points = [...lattice(...HINDMATA, 100, 150), { lon: KURLA[0], lat: KURLA[1], weight: 1 }];
    const frame = denseFrame(points, { within: AOI })!;
    expect(contains(frame.bounds, KURLA)).toBe(false);
    expect(contains(frame.bounds, HINDMATA)).toBe(true);
    // 100 of the 101 units of weight.
    expect(frame.share).toBeCloseTo(100 / 101, 6);
    expect(spanM(frame.bounds).h).toBeLessThan(3_000);
  });

  it("picks the heavier of two clusters too far apart to share a frame", () => {
    const points = [...lattice(...HINDMATA, 30, 150), ...lattice(...KURLA, 60, 150)];
    // 6 km apart north to south: a 5 km window cannot hold both (the default 7 km one can).
    const frame = denseFrame(points, { within: AOI, maxSpanM: 5_000 })!;
    expect(contains(frame.bounds, KURLA)).toBe(true);
    expect(contains(frame.bounds, HINDMATA)).toBe(false);
    expect(frame.share).toBeCloseTo(60 / 90, 6);
  });

  it("keeps two clusters that fit one window together", () => {
    const near = offset(...HINDMATA, 2_000, 2_000);
    const points = [...lattice(...HINDMATA, 30, 100), ...lattice(near[0], near[1], 30, 100)];
    const frame = denseFrame(points, { within: AOI })!;
    expect(frame.share).toBe(1);
  });

  it("never frames narrower than the minimum span, so one street is not a close-up", () => {
    const street = [HINDMATA, offset(...HINDMATA, 40, 0)].map(([lon, lat]) => ({
      lon,
      lat,
      weight: 20,
    }));
    const { w, h } = spanM(denseFrame(street, { within: AOI, minSpanM: 1_200 })!.bounds);
    expect(w).toBeGreaterThanOrEqual(1_199);
    expect(h).toBeGreaterThanOrEqual(1_199);
  });

  it("never frames wider than the window plus its padding", () => {
    // Water everywhere: a lattice over the whole AOI.
    const everywhere = lattice(72.86, 19.065, 900, 350);
    const frame = denseFrame(everywhere, { within: AOI, maxSpanM: 6_000 })!;
    const { w, h } = spanM(frame.bounds);
    expect(Math.max(w, h)).toBeLessThan(6_000 * 1.3);
    expect(frame.share).toBeGreaterThan(0.1);
    // A 10.5 km lattice seen through a padded 6 km window: about (7.4 / 10.5)^2 of it.
    expect(frame.share).toBeLessThan(0.75);
  });

  it("stays inside the AOI", () => {
    // Water on the AOI's south-west corner: the padding would reach into the sea off Colaba.
    const corner = lattice(72.8155, 18.9955, 20, 50);
    const frame = denseFrame(corner, { within: AOI })!;
    expect(frame.bounds[0][0]).toBeGreaterThanOrEqual(AOI[0][0]);
    expect(frame.bounds[0][1]).toBeGreaterThanOrEqual(AOI[0][1]);
    expect(frame.bounds[1][0]).toBeLessThanOrEqual(AOI[1][0]);
    expect(frame.bounds[1][1]).toBeLessThanOrEqual(AOI[1][1]);
  });

  it("gives the same frame for the same water in any order", () => {
    const points = [...lattice(...HINDMATA, 40, 180, 2), ...lattice(...KURLA, 40, 180, 2)];
    const a = denseFrame(points, { within: AOI })!;
    const b = denseFrame([...points].reverse(), { within: AOI })!;
    expect(b.bounds).toEqual(a.bounds);
  });
});

describe("clampInside", () => {
  it("shifts an overflowing box in and cuts one larger than the AOI", () => {
    const shifted = clampInside(
      [
        [72.8, 19.0],
        [72.83, 19.02],
      ],
      AOI,
    );
    expect(shifted[0][0]).toBeCloseTo(72.815, 9);
    expect(shifted[1][0] - shifted[0][0]).toBeCloseTo(0.03, 9);
    expect(
      clampInside(
        [
          [72.7, 18.9],
          [73.0, 19.2],
        ],
        AOI,
      ),
    ).toEqual(AOI);
  });
});

describe("pathsFrame and extentFrame", () => {
  it("frames every vertex of a route, with air around it", () => {
    const route = [HINDMATA, offset(...HINDMATA, 1_500, 3_000), KURLA];
    const box = pathsFrame([route], { within: AOI })!;
    for (const vertex of route) expect(contains(box, vertex)).toBe(true);
    expect(box[0][1]).toBeLessThan(HINDMATA[1]);
    expect(box[1][1]).toBeGreaterThan(KURLA[1]);
  });

  it("frames two places as a corridor no narrower than the minimum span", () => {
    // KEM Hospital to Sion Hospital, about 4.3 km apart and almost due north.
    const kem = { lon: 72.841, lat: 19.003 };
    const sion = { lon: 72.862, lat: 19.041 };
    const box = extentFrame([kem, sion], { within: AOI, minSpanM: 1_200 })!;
    expect(contains(box, [kem.lon, kem.lat])).toBe(true);
    expect(contains(box, [sion.lon, sion.lat])).toBe(true);
    expect(spanM(box).w).toBeGreaterThanOrEqual(1_199);
  });

  it("returns null with nothing to frame", () => {
    expect(pathsFrame([])).toBeNull();
    expect(extentFrame([])).toBeNull();
  });
});

/** A street 100 m long running east from `at`, with a depth series. */
function street(at: [number, number], depthCm: number[], lengthM = 100): DepthStreet {
  return { path: [at, offset(at[0], at[1], lengthM, 0)], depthCm };
}

describe("peakStep", () => {
  it("is the step with the most street length at or above the threshold", () => {
    const streets = [
      street(HINDMATA, [0, 20, 20, 0]),
      street(KURLA, [0, 0, 20, 20]),
      street(offset(...KURLA, 0, 200), [0, 0, 20, 0], 400),
    ];
    expect(peakStep(streets, 15)?.step).toBe(2);
    expect(peakStep(streets, 15)?.lengthM).toBeCloseTo(600, 0);
    expect(peakStep(streets, 50)).toBeNull();
  });

  it("measures length in metres", () => {
    expect(pathLengthM([HINDMATA, offset(...HINDMATA, 300, 400)])).toBeCloseTo(500, 0);
  });
});

describe("affectedFrame", () => {
  it("frames the deep streets at the run's peak step", () => {
    const deep = Array.from({ length: 12 }, (_, i) =>
      street(offset(...KURLA, (i % 4) * 150, Math.floor(i / 4) * 150), [0, 5, 30]),
    );
    const shallow = Array.from({ length: 12 }, (_, i) =>
      street(offset(...HINDMATA, (i % 4) * 150, Math.floor(i / 4) * 150), [0, 6, 8]),
    );
    const frame = affectedFrame({ streets: [...deep, ...shallow], fallback: AOI });
    expect(frame.basis).toBe("deep");
    expect(frame.thresholdCm).toBe(15);
    expect(frame.step).toBe(2);
    expect(contains(frame.bounds, KURLA)).toBe(true);
    expect(contains(frame.bounds, HINDMATA)).toBe(false);
    expect(frame.lengthM).toBeCloseTo(1_200, 0);
  });

  it("reads a given step instead of the peak", () => {
    const streets = [
      ...Array.from({ length: 6 }, (_, i) => street(offset(...KURLA, i * 120, 0), [0, 30, 0])),
      ...Array.from({ length: 6 }, (_, i) => street(offset(...HINDMATA, i * 120, 0), [0, 0, 30])),
    ];
    const frame = affectedFrame({ streets, fallback: AOI, step: 1 });
    expect(frame.step).toBe(1);
    expect(contains(frame.bounds, KURLA)).toBe(true);
    expect(contains(frame.bounds, HINDMATA)).toBe(false);
  });

  it("falls back to 5 cm, then to the chronic spots, then to the AOI", () => {
    const wet = Array.from({ length: 4 }, (_, i) =>
      street(offset(...KURLA, i * 120, 0), [0, 8, 9]),
    );
    expect(affectedFrame({ streets: wet, fallback: AOI }).basis).toBe("wet");

    const dry = [street(KURLA, [0, 1, 2])];
    const spots = affectedFrame({
      streets: dry,
      fallback: AOI,
      hotspots: [
        { lon: HINDMATA[0], lat: HINDMATA[1] },
        { lon: KURLA[0], lat: KURLA[1] },
      ],
    });
    expect(spots.basis).toBe("hotspots");
    expect(contains(spots.bounds, HINDMATA)).toBe(true);
    expect(contains(spots.bounds, KURLA)).toBe(true);

    const empty = affectedFrame({ streets: [], fallback: AOI });
    expect(empty.basis).toBe("aoi");
    expect(empty.bounds).toBe(AOI);
    expect(empty.share).toBe(1);
  });

  it("holds the points it is told to, beyond the densest window", () => {
    // The water is at Kurla; the rail's top spot is Hindmata, 8 km away and dry.
    const deep = Array.from({ length: 12 }, (_, i) =>
      street(offset(...KURLA, (i % 4) * 150, Math.floor(i / 4) * 150), [0, 30]),
    );
    const plain = affectedFrame({ streets: deep, fallback: AOI });
    expect(contains(plain.bounds, HINDMATA)).toBe(false);
    const held = affectedFrame({
      streets: deep,
      fallback: AOI,
      include: [{ lon: HINDMATA[0], lat: HINDMATA[1] }],
    });
    expect(contains(held.bounds, HINDMATA)).toBe(true);
    expect(contains(held.bounds, KURLA)).toBe(true);
    expect(held.basis).toBe("deep");
    expect(held.share).toBe(1);
  });

  it("frames every qualifying street with WHOLE, where the window frames the densest part", () => {
    // Water at both ends of the AOI, the heavier end at Kurla: the window takes Kurla alone.
    const streets = [
      ...Array.from({ length: 20 }, (_, i) => street(offset(...KURLA, i * 60, 0), [0, 30])),
      ...Array.from({ length: 8 }, (_, i) => street(offset(...HINDMATA, i * 60, 0), [0, 30])),
    ];
    const dense = affectedFrame({ streets, fallback: AOI, maxSpanM: 4_000 });
    expect(contains(dense.bounds, HINDMATA)).toBe(false);
    expect(dense.share).toBeLessThan(1);
    const whole = affectedFrame({ streets, fallback: AOI, ...WHOLE });
    expect(contains(whole.bounds, HINDMATA)).toBe(true);
    expect(contains(whole.bounds, KURLA)).toBe(true);
    expect(whole.share).toBe(1);
  });

  it("uses a 7 km window by default", () => {
    // Two clusters 6.5 km apart east-west: one 7 km window holds both, a 6 km one does not.
    const east = offset(...HINDMATA, 6_500, 0);
    const points = [...lattice(...HINDMATA, 25, 60), ...lattice(east[0], east[1], 30, 60)];
    expect(denseFrame(points, { within: AOI })!.share).toBe(1);
    expect(denseFrame(points, { within: AOI, maxSpanM: 6_000 })!.share).toBeLessThan(1);
  });

  it("does not call a single wet street an area", () => {
    // 100 m at 30 cm is under the 200 m floor: the 5 cm tier is tried, then the AOI.
    const frame = affectedFrame({ streets: [street(KURLA, [30])], fallback: AOI });
    expect(frame.basis).toBe("aoi");
  });
});

describe("affectedFrameAt", () => {
  // Water at Kurla at step 1, then at Hindmata at step 3, and none at step 0 or 2.
  const streets = [
    ...Array.from({ length: 6 }, (_, i) => street(offset(...KURLA, i * 120, 0), [0, 30, 0, 0])),
    ...Array.from({ length: 12 }, (_, i) => street(offset(...HINDMATA, i * 120, 0), [0, 0, 0, 30])),
  ];
  const spots = [{ lon: KURLA[0], lat: KURLA[1] }];

  it("frames the water at the step it is given", () => {
    const frame = affectedFrameAt({ streets, fallback: AOI, hotspots: spots, ...WHOLE }, 1);
    expect(frame.step).toBe(1);
    expect(frame.basis).toBe("deep");
    expect(contains(frame.bounds, KURLA)).toBe(true);
    expect(contains(frame.bounds, HINDMATA)).toBe(false);
  });

  it("frames the peak when the step has no water, rather than the chronic spots", () => {
    // `affectedFrame` at step 0 would frame the one chronic spot, which is dry until step 1.
    expect(affectedFrame({ streets, fallback: AOI, hotspots: spots, step: 0 }).basis).toBe(
      "hotspots",
    );
    const frame = affectedFrameAt({ streets, fallback: AOI, hotspots: spots, ...WHOLE }, 0);
    expect(frame.step).toBe(3);
    expect(frame.basis).toBe("deep");
    expect(contains(frame.bounds, HINDMATA)).toBe(true);
  });

  it("is the peak with no step", () => {
    expect(affectedFrameAt({ streets, fallback: AOI }, null).step).toBe(3);
    expect(affectedFrameAt({ streets, fallback: AOI }, undefined).step).toBe(3);
  });

  it("keeps the fallbacks when the whole run is dry", () => {
    const dry = [street(KURLA, [0, 1, 2])];
    expect(affectedFrameAt({ streets: dry, fallback: AOI, hotspots: spots }, 1).basis).toBe(
      "hotspots",
    );
    expect(affectedFrameAt({ streets: dry, fallback: AOI }, 1).basis).toBe("aoi");
  });
});

describe("boundsOverlap and useSettledBounds", () => {
  const a: Bbox = [
    [72.84, 19.0],
    [72.86, 19.02],
  ];
  const nudged: Bbox = [
    [72.841, 19.001],
    [72.861, 19.021],
  ];
  const elsewhere: Bbox = [
    [72.87, 19.06],
    [72.89, 19.08],
  ];

  it("measures overlap as intersection over union", () => {
    expect(boundsOverlap(a, a)).toBeCloseTo(1, 9);
    expect(boundsOverlap(a, elsewhere)).toBe(0);
    expect(boundsOverlap(a, null)).toBe(0);
    expect(boundsOverlap(a, nudged)).toBeGreaterThan(0.8);
  });

  it("holds a frame through small moves and follows a real one", () => {
    const { result, rerender } = renderHook(({ next }) => useSettledBounds(next), {
      initialProps: { next: a as Bbox | null },
    });
    expect(result.current).toBe(a);
    act(() => rerender({ next: nudged }));
    expect(result.current).toBe(a);
    act(() => rerender({ next: elsewhere }));
    expect(result.current).toBe(elsewhere);
    act(() => rerender({ next: null }));
    expect(result.current).toBeNull();
  });
});

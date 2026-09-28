/**
 * What the drain map reads from `GET /v1/drains/health` and `/v1/observations`.
 *
 * The product now carries a `summary`, a `moved` flag and words for where each pipe is; runs
 * baked before it carry none of them. These pin both: the new fields reach the screen, and an
 * older run still reads sensibly - a moved pipe found from its `beta_delta`, a pipe named by its
 * street, and a traffic anomaly titled in words rather than by a raw segment id.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  loadDrainHealth,
  loadObservations,
  parseDrainSummary,
  UNNAMED_PLACE,
} from "@/lib/api/drains";

const RUN_ID = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";

function respond(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })),
  );
}

function feature(props: Record<string, unknown>) {
  return {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates: [
        [72.84, 19.01],
        [72.841, 19.01],
      ],
    },
    properties: { beta_mean: 0.2, beta_sd: 0.1, beta_prior: 0.2, beta_delta: 0, ...props },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseDrainSummary", () => {
  it("reads the product's summary, with capacity left null where the bake had none", () => {
    const summary = parseDrainSummary({
      source: "product",
      n_pipes: 49770,
      n_moved: 201,
      n_moved_up: 173,
      n_moved_down: 28,
      largest_rise: {
        edge: "MUM-E037896",
        name: "off Eastern Freeway",
        locality: null,
        prior: 0.15,
        post: 0.4926,
      },
      largest_fall: null,
      capacity_full_m3s: 63109,
      capacity_lost_prior_pct: 28.408,
      capacity_lost_post_pct: 28.496,
      capacity_learned_m3s: null,
      n_obs: 21,
      n_obs_by_kind: { report: 11, traffic: 10 },
      n_obs_synthetic: 20,
      n_obs_real: 1,
    });
    expect(summary).not.toBeNull();
    expect(summary?.nMovedUp).toBe(173);
    expect(summary?.largestRise?.name).toBe("off Eastern Freeway");
    expect(summary?.largestFall).toBeNull();
    expect(summary?.capacityLostPriorPct).toBe(28.408);
    expect(summary?.capacityLearnedM3s).toBeNull();
    expect(summary?.nObsByKind).toEqual({ report: 11, traffic: 10 });
    expect(summary?.nMovedUnwritten).toBeNull();
  });

  it("is null when there is no summary to read", () => {
    expect(parseDrainSummary(undefined)).toBeNull();
    expect(parseDrainSummary("nothing")).toBeNull();
  });
});

describe("loadDrainHealth", () => {
  it("reads the moved flag and the display name the bake wrote", async () => {
    respond({
      run_id: RUN_ID,
      n_edges: 49770,
      n_updated: 1,
      summary: { source: "product", n_moved: 1 },
      features: [
        feature({
          edge_id: "MUM-E037896",
          street: null,
          display_name: "off Eastern Freeway",
          locality: "near Wadala",
          beta_delta: 0.0001,
          moved: false,
        }),
      ],
    });
    const health = await loadDrainHealth(RUN_ID);
    const edge = health?.edges[0];
    expect(edge?.displayName).toBe("off Eastern Freeway");
    expect(edge?.locality).toBe("near Wadala");
    // The flag wins over the rounded delta: 0.0001 can be a pipe that moved by 0.6e-4.
    expect(edge?.moved).toBe(false);
    expect(health?.summary?.nMoved).toBe(1);
  });

  it("falls back to the street and to beta_delta on a run baked before either existed", async () => {
    respond({
      run_id: RUN_ID,
      features: [
        feature({ edge_id: "A", street: "Road No 28A", beta_delta: 0.3 }),
        feature({ edge_id: "B", street: null, beta_delta: 0 }),
      ],
    });
    const health = await loadDrainHealth(RUN_ID);
    expect(health?.edges.map((e) => e.displayName)).toEqual(["Road No 28A", null]);
    expect(health?.edges.map((e) => e.moved)).toEqual([true, false]);
    expect(health?.summary).toBeNull();
  });

  it("sends the order only when a caller opts into one, so the default stays worst first", async () => {
    const fetchSpy = vi.fn(
      async () => new Response(JSON.stringify({ run_id: RUN_ID, features: [] }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    await loadDrainHealth(RUN_ID, undefined, 25);
    await loadDrainHealth(RUN_ID, undefined, 6000, "mumbai", "learned");
    const [plain, learned] = fetchSpy.mock.calls.map((call) => String((call as unknown[])[0]));
    expect(plain).toContain("limit=25");
    expect(plain).not.toContain("order=");
    expect(learned).toContain("order=learned");
    expect(learned).toContain("city=mumbai");
  });
});

describe("loadObservations", () => {
  it("titles an unnamed observation in words, never by segment id", async () => {
    respond({
      run_id: RUN_ID,
      observations: [
        { kind: "traffic", segment_id: "S102177717-000", edge_id: "MUM-E1", ts: "x" },
        {
          kind: "traffic",
          segment_id: "S289412584-010",
          place: "off Eastern Freeway",
          locality: "near Wadala",
          ts: "x",
        },
      ],
    });
    const set = await loadObservations(RUN_ID);
    expect(set?.observations[0].place).toBe(UNNAMED_PLACE);
    expect(set?.observations[0].locality).toBeNull();
    expect(set?.observations[1].place).toBe("off Eastern Freeway");
    expect(set?.observations[1].locality).toBe("near Wadala");
  });
});

/**
 * The run preloader's frame ordering, which concurrency put at risk (task P10.4).
 *
 * `decodeFrames` used to fetch the run's 36 depth rasters one after another, so a frame could
 * only ever land in the slot it was asked for. That cost 2,907 ms of serialised round trips on
 * this laptop at 12 python processes - the largest single term in a console first render of
 * 6,100 ms against section 14's 2,000 ms - and it is now six in flight.
 *
 * Six in flight means the responses arrive out of order, and `frames[step]` is what the scrub
 * indexes to decide which depth raster to draw at which minute. A frame in the wrong slot would
 * be the map showing 07:15's water at 08:40 - wrong, plausible, and invisible to anything that
 * only checks that 36 frames came back. So these tests resolve the fetches deliberately out of
 * order and assert the mapping, not the count.
 *
 * `createImageBitmap` does not exist in jsdom, so it is stubbed with the step number it was
 * handed: the assertion "frame 7 is the bitmap made from step 7's body" is then something a test
 * can state rather than something a mock can only approximate.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { loadRunDepth } from "@/lib/api/run-depth";

const RUN_ID = "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked";
const N_STEPS = 36;

/** `GET /v1/nowcast/raster/bounds` as the API answers it, trimmed to what the loader reads. */
function boundsBody() {
  return {
    run_id: RUN_ID,
    city: "mumbai",
    cycle_ts: "2019-07-02T08:40:00+05:30",
    mode: "baked",
    bundle: "MUM-2019-07-02",
    n_steps: N_STEPS,
    step_min: 5,
    ensemble_n: 20,
    mass_balance_err: 0.000381,
    stage_ms: { sky: 6320, twin: 74000 },
    notes: ["Reconstructed replay"],
    bounds: { wgs84: [72.815, 18.995, 72.905, 19.135] },
  };
}

/** `GET /v1/nowcast/segments` with one wet segment, which is all the ordering test needs. */
function segmentsBody() {
  return {
    run_id: RUN_ID,
    n_segments_total: 21_296,
    valid_ts: Array.from({ length: N_STEPS }, (_, i) => `step-${i}`),
    depth_cm: { "MUM-S000001": Array.from({ length: N_STEPS }, (_, i) => i) },
    p_gt: null,
  };
}

/** The step number a raster request asked for, or null when it is not a raster request. */
function stepOf(url: string): number | null {
  const match = /[?&]step=(\d+)/.exec(url);
  return match ? Number(match[1]) : null;
}

interface Pending {
  step: number;
  release: () => void;
}

/**
 * Install a fetch that answers bounds and segments immediately and holds every raster request
 * open until the test releases it, so the test decides the arrival order.
 */
function installFetch(): { pending: Pending[]; urls: string[] } {
  const pending: Pending[] = [];
  const urls: string[] = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      urls.push(url);
      if (url.includes("/v1/nowcast/raster/bounds")) {
        return new Response(JSON.stringify(boundsBody()), { status: 200 });
      }
      if (url.includes("/v1/nowcast/segments")) {
        return new Response(JSON.stringify(segmentsBody()), { status: 200 });
      }
      const step = stepOf(url);
      if (step === null) return new Response("{}", { status: 404 });
      return new Promise<Response>((resolve) => {
        pending.push({
          step,
          // The body is the step number; the stubbed `createImageBitmap` reads it back, so the
          // bitmap in `frames[step]` can be traced to the request that produced it.
          release: () => resolve(new Response(String(step), { status: 200 })),
        });
      });
    }),
  );

  return { pending, urls };
}

beforeEach(() => {
  // jsdom has neither of these. The bitmap stub carries the step it was decoded from.
  vi.stubGlobal("createImageBitmap", async (blob: Blob) => {
    const text = await blob.text();
    return { step: Number(text) } as unknown as ImageBitmap;
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/**
 * Let the loader's workers get as far as their next request.
 *
 * A macrotask turn, not a microtask drain: reading a `Response` body goes through a stream, and
 * `await Promise.resolve()` returns long before a `blob()` has resolved - which is how the first
 * version of this file timed out four times over with every worker still waiting on step 0.
 */
async function settle(turns = 4): Promise<void> {
  for (let i = 0; i < turns; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

/**
 * Answer every raster request the loader makes until it makes no more.
 *
 * `reverse` releases whatever is in flight newest-first, which - with six workers open at a time
 * - is the most out-of-order arrival the pool can actually produce. It settles *before* looking
 * at the queue, because on the first turn the workers have not issued anything yet and a loop
 * that checks first exits immediately and then waits forever on a load nobody answered.
 */
async function drain(pending: Pending[], reverse: boolean): Promise<void> {
  for (let guard = 0; guard < N_STEPS * 4; guard++) {
    await settle();
    if (!pending.length) {
      // One more turn in case a worker is between its decode and its next fetch.
      await settle();
      if (!pending.length) return;
    }
    const open = pending.splice(0, pending.length);
    for (const request of reverse ? open.reverse() : open) request.release();
  }
}

describe("loadRunDepth frame ordering", () => {
  it("puts every frame in its own step's slot when the responses arrive backwards", async () => {
    const { pending } = installFetch();
    const loading = loadRunDepth(RUN_ID);

    await drain(pending, true);

    const run = await loading;
    expect(run.frames).toHaveLength(N_STEPS);
    run.frames.forEach((frame, step) => {
      expect(frame, `step ${step} decoded nothing`).not.toBeNull();
      expect(
        (frame as unknown as { step: number }).step,
        `slot ${step} holds the frame fetched for a different step`,
      ).toBe(step);
    });
  });

  it("holds six requests open at once rather than one or thirty-six", async () => {
    const { pending } = installFetch();
    const loading = loadRunDepth(RUN_ID);
    await settle(20);

    // Six is the pool. One would be the serial loader this replaced; 36 would be the memory
    // spike the serial loader's comment was right to avoid.
    expect(pending.length).toBe(6);

    await drain(pending, false);
    await loading;
  });

  it("reports progress monotonically up to the step count", async () => {
    const { pending } = installFetch();
    const seen: number[] = [];
    const loading = loadRunDepth(RUN_ID, undefined, (done, total) => {
      expect(total).toBe(N_STEPS);
      seen.push(done);
    });

    await drain(pending, true);
    await loading;

    expect(seen).toHaveLength(N_STEPS);
    expect(seen).toEqual([...seen].sort((a, b) => a - b));
    expect(seen.at(-1)).toBe(N_STEPS);
  });

  it("gives a failed frame a null slot and keeps the rest", async () => {
    const pending: Pending[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/v1/nowcast/raster/bounds")) {
          return new Response(JSON.stringify(boundsBody()), { status: 200 });
        }
        if (url.includes("/v1/nowcast/segments")) {
          return new Response(JSON.stringify(segmentsBody()), { status: 200 });
        }
        const step = stepOf(url);
        if (step === null) return new Response("{}", { status: 404 });
        // Step 13 is missing from the run directory, which is how a half-written bake looks.
        if (step === 13) return new Response("not found", { status: 404 });
        return new Promise<Response>((resolve) => {
          pending.push({
            step,
            release: () => resolve(new Response(String(step), { status: 200 })),
          });
        });
      }),
    );

    const loading = loadRunDepth(RUN_ID);
    await drain(pending, false);
    const run = await loading;

    expect(run.frames[13]).toBeNull();
    expect(run.frames.filter((f) => f === null)).toHaveLength(1);
    expect((run.frames[14] as unknown as { step: number }).step).toBe(14);
  });
});

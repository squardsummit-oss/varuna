/**
 * Contract tests for the full-city Twin what-if client (`POST /v1/whatif/twin`).
 *
 * The fixtures are bodies `services/api/varuna_api/routers/whatif.py` produced on the synthetic
 * 12 x 12 city of `services/api/tests/test_whatif_twin.py` (rain 1.3x, tide +1.0 m), trimmed, so
 * a rename on the API side fails here rather than on the console.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { getTwinScenario, startTwinScenario, TwinScenarioJobSchema } from "@/lib/api/whatif";

import { TWIN_DONE as DONE, TWIN_STARTED as STARTED } from "./whatif-twin.fixture";

function respond(status: number, body: unknown) {
  return vi.fn<(url: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
    async () => new Response(JSON.stringify(body), { status }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("full-city Twin what-if client", () => {
  it("parses a started job and a finished one as the API writes them", () => {
    expect(TwinScenarioJobSchema.parse(STARTED).state).toBe("running");
    const done = TwinScenarioJobSchema.parse(DONE);
    expect(done.state).toBe("done");
    expect(done.result?.method).toBe("twin_full_aoi");
    expect(done.result?.blockage).toBe("prior");
    expect(done.result?.sea.tide_in_m3).toBeGreaterThan(0);
    expect(done.cache?.source).toBe("computed");
  });

  it("posts the levers under the API's names and returns the job", async () => {
    const fetchMock = respond(202, STARTED);
    vi.stubGlobal("fetch", fetchMock);
    const job = await startTwinScenario({ rainScale: 1.3, tideOffsetM: 1, runId: "RUN" });
    expect(job.job_id).toBe(STARTED.job_id);
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(JSON.parse(String(init?.body))).toEqual({
      run_id: "RUN",
      rain_scale: 1.3,
      tide_offset_m: 1,
      cleaned_segments: [],
    });
  });

  it("reads a finished job back from its poll", async () => {
    vi.stubGlobal("fetch", respond(200, DONE));
    const job = await getTwinScenario(DONE.job_id);
    expect(job.result?.hotspots[0]?.name).toBe("Test junction");
  });

  it("throws the API's own sentence when it refuses", async () => {
    vi.stubGlobal(
      "fetch",
      respond(503, {
        error: {
          code: "whatif_busy",
          message: "A live cycle or a computed rain nowcast is using the machine.",
          run_id: null,
        },
      }),
    );
    await expect(startTwinScenario({ rainScale: 1, tideOffsetM: 0.5 })).rejects.toThrow(
      "A live cycle or a computed rain nowcast is using the machine.",
    );
  });
});

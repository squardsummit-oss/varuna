/**
 * Contract tests for the weather client (task D-13).
 *
 * The fixture is the shape `services/api/varuna_api/routers/weather.py` serialises, field for
 * field, so a rename on the API side fails here rather than on a judge's screen.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ageLabel,
  GRID_NOTICE_KM,
  isGridDistant,
  loadWeather,
  sourceLine,
  temperatureLabel,
  weatherFromBody,
} from "@/lib/api/weather";

/** A live response as `routers/weather.py` writes it. */
function body(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    city: "mumbai",
    point: { lon: 72.86, lat: 19.065 },
    grid_point: { lon: 72.875, lat: 19.0 },
    grid_offset_km: 7.29,
    elevation_m: 8,
    current: {
      ts: "2026-09-19T14:15:00+05:30",
      temperature_c: 28.4,
      humidity_pct: 79,
      precipitation_mm: 0.3,
      wind_kmh: 17.6,
      weather_code: 63,
      weather: "Moderate rain",
    },
    hourly: [
      {
        ts: "2026-09-19T15:00:00+05:30",
        precipitation_mm: 1.2,
        precipitation_probability_pct: 62,
      },
      {
        ts: "2026-09-19T16:00:00+05:30",
        precipitation_mm: 0,
        precipitation_probability_pct: 18,
      },
    ],
    fetched_at: "2026-09-19T14:13:00+05:30",
    age_s: 124.0,
    stale: false,
    ttl_s: 900,
    source: "open-meteo",
    source_url: "https://open-meteo.com/",
    licence: "CC BY 4.0",
    licence_url: "https://creativecommons.org/licenses/by/4.0/",
    attribution: "Weather data by Open-Meteo.com (CC BY 4.0)",
    notes: [],
    ...overrides,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("weatherFromBody", () => {
  it("reads every field the API sends", () => {
    const weather = weatherFromBody(body());
    expect(weather.city).toBe("mumbai");
    expect(weather.current.temperatureC).toBe(28.4);
    expect(weather.current.weather).toBe("Moderate rain");
    expect(weather.hourly).toHaveLength(2);
    expect(weather.hourly[0]?.precipitationProbabilityPct).toBe(62);
    expect(weather.gridOffsetKm).toBe(7.29);
    expect(weather.attribution).toBe("Weather data by Open-Meteo.com (CC BY 4.0)");
    expect(weather.stale).toBe(false);
  });

  it("keeps a missing number null rather than defaulting it to zero", () => {
    const weather = weatherFromBody(
      body({
        current: { ts: "2026-09-19T14:15:00+05:30", temperature_c: null, weather_code: null },
      }),
    );
    expect(weather.current.temperatureC).toBeNull();
    expect(weather.current.windKmh).toBeNull();
    expect(temperatureLabel(weather)).toBeNull();
  });

  it("carries the API's own notes so the dialog can print why a copy is old", () => {
    const weather = weatherFromBody(
      body({ stale: true, age_s: 2460, notes: ["Open-Meteo could not be reached (timeout)."] }),
    );
    expect(weather.stale).toBe(true);
    expect(weather.notes).toEqual(["Open-Meteo could not be reached (timeout)."]);
    expect(sourceLine(weather)).toBe("Open-Meteo · CC BY 4.0 · fetched 41 min ago");
  });
});

describe("loadWeather", () => {
  it("returns a reading and asks for the city it was given", async () => {
    const asked: string[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      asked.push(String(input));
      return jsonResponse(body());
    });

    const state = await loadWeather("mumbai");

    expect(state.kind).toBe("ready");
    if (state.kind !== "ready") throw new Error("expected a reading");
    expect(state.weather.current.temperatureC).toBe(28.4);
    expect(asked[0]).toContain("/v1/weather?city=mumbai");
  });

  it("turns a 404 into unavailable carrying the API's sentence, and no temperature", async () => {
    vi.stubGlobal("fetch", async () =>
      jsonResponse(
        {
          error: {
            code: "city_not_configured",
            message: "No city config at services/city/configs/atlantis.yaml.",
          },
        },
        404,
      ),
    );

    const state = await loadWeather("atlantis");

    expect(state).toEqual({
      kind: "unavailable",
      reason: "No city config at services/city/configs/atlantis.yaml.",
    });
  });

  it("turns a dead network into unavailable rather than throwing", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("fetch failed");
    });

    const state = await loadWeather("mumbai");

    expect(state.kind).toBe("unavailable");
    if (state.kind !== "unavailable") throw new Error("expected unavailable");
    expect(state.reason).toContain("unreachable");
  });

  it("reports a 503 with the reason the API gave for having nothing cached", async () => {
    vi.stubGlobal("fetch", async () =>
      jsonResponse(
        {
          error: {
            code: "weather_unavailable",
            message:
              "Open-Meteo could not be reached and nothing has been cached for mumbai. The flood forecast on this page does not depend on it.",
          },
        },
        503,
      ),
    );

    const state = await loadWeather("mumbai");

    expect(state.kind).toBe("unavailable");
    if (state.kind !== "unavailable") throw new Error("expected unavailable");
    expect(state.reason).toContain("does not depend on it");
  });
});

describe("ageLabel", () => {
  it("rounds down so a copy is never described as younger than it is", () => {
    expect(ageLabel(0)).toBe("just now");
    expect(ageLabel(59.9)).toBe("just now");
    expect(ageLabel(119)).toBe("1 min ago");
    expect(ageLabel(2460)).toBe("41 min ago");
    expect(ageLabel(3600 * 3 + 59)).toBe("3 h ago");
    expect(ageLabel(3600 * 30)).toBe("1 d ago");
  });

  it("says so rather than inventing a time when the age is not a number", () => {
    expect(ageLabel(Number.NaN)).toBe("age unknown");
    expect(ageLabel(-1)).toBe("age unknown");
  });
});

describe("isGridDistant", () => {
  it("flags a cell centre further than the notice distance", () => {
    expect(isGridDistant(weatherFromBody(body({ grid_offset_km: GRID_NOTICE_KM + 0.1 })))).toBe(
      true,
    );
    expect(isGridDistant(weatherFromBody(body({ grid_offset_km: 0.4 })))).toBe(false);
  });
});

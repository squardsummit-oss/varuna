import { describe, expect, it } from "vitest";

import { DEFAULT_CITY, cityFromSearch, parseCities, withCity } from "@/lib/city";

describe("cityFromSearch", () => {
  it("reads the slug a screen was opened with", () => {
    expect(cityFromSearch("?city=chennai")).toBe("chennai");
    expect(cityFromSearch("?run=MUM-1&city=Chennai")).toBe("chennai");
  });

  it("falls back to Mumbai when nothing names a city", () => {
    expect(cityFromSearch("")).toBe(DEFAULT_CITY);
    expect(cityFromSearch("?run=MUM-1")).toBe(DEFAULT_CITY);
  });

  it("refuses anything that is not a plain slug, because the API makes it a path segment", () => {
    for (const bad of ["../mumbai", "mum bai", "/etc", "MUM/../..", "1city", ""]) {
      expect(cityFromSearch(`?city=${encodeURIComponent(bad)}`)).toBe(DEFAULT_CITY);
    }
  });
});

describe("withCity", () => {
  it("keeps every other parameter so a switch does not discard the demo's bundle", () => {
    expect(withCity("/console", "?bundle=MUM-2019-07-02&autoplay=1", "chennai")).toBe(
      "/console?bundle=MUM-2019-07-02&autoplay=1&city=chennai",
    );
  });

  it("drops the pinned run, which belongs to the city being left", () => {
    expect(withCity("/console", "?run=MUM-20190702T0110Z&city=mumbai", "chennai")).toBe(
      "/console?city=chennai",
    );
  });

  it("leaves the default city out of the URL rather than spelling it", () => {
    expect(withCity("/console", "?city=chennai", DEFAULT_CITY)).toBe("/console");
  });

  it("spells Mumbai on /onboard, whose own default is Chennai", () => {
    expect(withCity("/onboard", "?city=chennai", DEFAULT_CITY)).toBe("/onboard?city=mumbai");
  });
});

describe("parseCities", () => {
  it("reads the wire's snake_case and says plainly what is built", () => {
    expect(
      parseCities({
        cities: [
          { id: "mumbai", name: "Mumbai", code: "MUM", built: true, latest_run_id: "MUM-1" },
          { id: "chennai", name: "Chennai", code: "CHN", built: false, latest_run_id: null },
        ],
      }),
    ).toEqual([
      { id: "mumbai", name: "Mumbai", code: "MUM", built: true, latestRunId: "MUM-1" },
      { id: "chennai", name: "Chennai", code: "CHN", built: false, latestRunId: null },
    ]);
  });

  it("drops a row with no id instead of rendering a blank switcher entry", () => {
    expect(parseCities({ cities: [{ name: "Nowhere" }] })).toEqual([]);
  });

  it("treats a missing `built` as not built, never as ready", () => {
    expect(parseCities({ cities: [{ id: "chennai" }] })[0]).toMatchObject({
      name: "chennai",
      built: false,
    });
  });
});

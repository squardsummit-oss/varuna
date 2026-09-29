import { describe, expect, it } from "vitest";

import type { Place } from "@/lib/api/route";

import { arrivalFromSearch, runCycleIst } from "../route-screen";

const PLACES: Place[] = [
  { id: "h1", name: "KEM Hospital (KEM)", kind: "hospital", lon: 72.8414, lat: 19.0032 },
  { id: "j1", name: "Hindmata junction", kind: "hotspot", lon: 72.8412, lat: 19.012 },
];

describe("arrivalFromSearch", () => {
  it("reads nothing when the link names no destination", () => {
    expect(arrivalFromSearch("?run=MUM-1", PLACES)).toBeNull();
  });

  it("picks the nearest registered place within 300 m and says how far it is", () => {
    const arrival = arrivalFromSearch("?to=72.8414,19.0125&place=Dr+Ambedkar+Road", PLACES);
    expect(arrival?.place?.id).toBe("j1");
    expect(arrival?.note).toMatch(
      /^Destination set to Hindmata junction, the registered place \d+ m from Dr Ambedkar Road\.$/,
    );
  });

  it("keeps the demo trip and asks for a destination when nothing registered is near", () => {
    const arrival = arrivalFromSearch("?to=72.88,19.10&place=Some+road", PLACES);
    expect(arrival?.place).toBeNull();
    expect(arrival?.note).toContain("Some road is not near a registered place");
    expect(arrival?.note).toContain("pick the destination");
  });

  it("ignores a coordinate that does not parse", () => {
    expect(arrivalFromSearch("?to=abc,def", PLACES)).toBeNull();
  });
});

describe("runCycleIst", () => {
  it("names a run by its cycle in IST rather than printing the id", () => {
    // 03:10 UTC is 08:40 IST, the demo's storm cycle.
    expect(runCycleIst("MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked")).toBe("08:40 IST");
    expect(runCycleIst("MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked")).toBe("06:40 IST");
  });

  it("wraps past midnight", () => {
    expect(runCycleIst("MUM-20190702T2000Z-sky1.0-twin1.0-flash0.1-live")).toBe("01:30 IST");
  });

  it("falls back to the id when it carries no cycle stamp", () => {
    expect(runCycleIst("MUM-1")).toBe("MUM-1");
  });
});

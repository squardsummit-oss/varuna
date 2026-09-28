import { describe, expect, it } from "vitest";

import type { Place } from "@/lib/api/route";

import { arrivalFromSearch } from "../route-screen";

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

  it("keeps the demo trip and says so when nothing registered is near the street", () => {
    const arrival = arrivalFromSearch("?to=72.88,19.10&place=Some+road", PLACES);
    expect(arrival?.place).toBeNull();
    expect(arrival?.note).toContain("Some road is not near a registered place");
  });

  it("ignores a coordinate that does not parse", () => {
    expect(arrivalFromSearch("?to=abc,def", PLACES)).toBeNull();
  });
});

import { describe, expect, it } from "vitest";

import { alertIdentities, alertIdentity, freshAlertIds } from "./alert-identity";
import type { RunAlert } from "./api/alerts";

type Input = Pick<RunAlert, "id" | "scope" | "hotspotId" | "areaDesc" | "level">;

/** Two baked cycles as the API returns them: ids carry the run, so they never repeat. */
const AT_0640: Input[] = [
  {
    id: "VARUNA-MUM-20190702T0110Z-SKY1.0-TWIN1.0-FLASH0.1-BAKED-STREET-1105-SEVERE",
    scope: "segment",
    hotspotId: null,
    areaDesc: "V B Worlikar Marg",
    level: "severe",
  },
  {
    id: "VARUNA-MUM-20190702T0110Z-SKY1.0-TWIN1.0-FLASH0.1-BAKED-STREET-0926-MODERATE",
    scope: "segment",
    hotspotId: null,
    areaDesc: "Sant Shitolebaba Maharaj Marg",
    level: "moderate",
  },
];

const AT_0840: Input[] = [
  {
    id: "VARUNA-MUM-20190702T0310Z-SKY1.0-TWIN1.0-FLASH0.1-BAKED-STREET-1105-SEVERE",
    scope: "segment",
    hotspotId: null,
    areaDesc: "V B Worlikar Marg",
    level: "severe",
  },
  {
    id: "VARUNA-MUM-20190702T0310Z-SKY1.0-TWIN1.0-FLASH0.1-BAKED-STREET-0926-SEVERE",
    scope: "segment",
    hotspotId: null,
    areaDesc: "Sant Shitolebaba Maharaj Marg",
    level: "severe",
  },
  {
    id: "VARUNA-MUM-20190702T0310Z-SKY1.0-TWIN1.0-FLASH0.1-BAKED-STREET-0560-SEVERE",
    scope: "segment",
    hotspotId: null,
    areaDesc: "Mahatma Gandhi Road",
    level: "severe",
  },
];

describe("alertIdentity", () => {
  it("ignores the run id, so one street at one level is one identity across cycles", () => {
    expect(alertIdentity(AT_0640[0]!)).toBe(alertIdentity(AT_0840[0]!));
  });

  it("separates levels, scopes and places", () => {
    const base = AT_0640[0]!;
    const ids = new Set([
      alertIdentity(base),
      alertIdentity({ ...base, level: "moderate" }),
      alertIdentity({ ...base, scope: "hotspot" }),
      alertIdentity({ ...base, areaDesc: "Dr Ambedkar Road" }),
    ]);
    expect(ids.size).toBe(4);
  });

  it("names a registered hotspot by its id rather than its area text", () => {
    const hindmata = {
      scope: "hotspot",
      hotspotId: "hindmata",
      areaDesc: "Hindmata junction",
      level: "severe" as const,
    };
    expect(alertIdentity({ ...hindmata, areaDesc: "Hindmata, Dadar East" })).toBe(
      alertIdentity(hindmata),
    );
  });
});

describe("freshAlertIds", () => {
  it("calls nothing new on the first queue a screen shows", () => {
    expect(freshAlertIds(null, AT_0840).size).toBe(0);
  });

  it("does not call the same street at the same level new across two runs", () => {
    const fresh = freshAlertIds(alertIdentities(AT_0640), AT_0840);
    expect(fresh.has(AT_0840[0]!.id)).toBe(false);
  });

  it("calls a street that stepped up a level new, and a street not warned about before", () => {
    const fresh = freshAlertIds(alertIdentities(AT_0640), AT_0840);
    expect([...fresh].sort()).toEqual([AT_0840[1]!.id, AT_0840[2]!.id].sort());
  });

  it("calls nothing new when the queue is shown again unchanged", () => {
    expect(freshAlertIds(alertIdentities(AT_0840), AT_0840).size).toBe(0);
  });

  it("calls everything new after an empty queue was shown", () => {
    expect(freshAlertIds(new Set(), AT_0640).size).toBe(AT_0640.length);
  });
});

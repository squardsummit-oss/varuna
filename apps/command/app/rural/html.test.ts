import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { BRAND_MARK_64_SRC } from "@/components/varuna/wordmark";
import type { RoutePlan } from "@/lib/api/route";
import { RURAL_VEHICLES, buildAdvisory, stopper, type RuralVehicle } from "@/lib/rural";

import { BRAND_MARK, esc, renderRural, type RuralBody, type RuralPage } from "./html";

/** UI_SPEC 7: "under 30 KB". The document alone, before any transport compression. */
const BUDGET_BYTES = 30 * 1024;

const RUN = {
  runId: "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked",
  cycleTime: "06:40",
  cycleDate: "2 Jul 2019",
  mode: "baked",
  label: "Reconstructed replay",
};

function page(body: RuralBody, vehicle = RURAL_VEHICLES[0]): RuralPage {
  return {
    city: { id: "mumbai", name: "Mumbai", code: "MUM" },
    run: RUN,
    query: {
      from: "Sion Circle",
      to: "Kurla, LBS Marg",
      vehicle,
      run: null,
      at: null,
    },
    body,
    shareUrl: body.kind === "answer" ? "http://localhost:3000/rural?from=a&to=b&v=car" : null,
  };
}

function answerBody(
  overrides: Partial<RoutePlan> = {},
  vehicle: RuralVehicle = RURAL_VEHICLES[0],
): RuralBody {
  const leg = {
    minutes: 21,
    distanceM: 6800,
    maxDepthCm: 4,
    depart: "2019-07-02T06:40:00+05:30",
    arrive: "2019-07-02T07:01:00+05:30",
    safeUntil: "2019-07-02T07:55:00+05:30",
    path: [] as [number, number][],
    streets: ["Sion Circle", "Sion Panvel Highway"],
  };
  const plan: RoutePlan = {
    runId: RUN.runId,
    profile: "two_wheeler",
    departAt: "2019-07-02T06:40:00+05:30",
    naive: leg,
    varuna: { ...leg, minutes: 27, streets: ["Sion Circle", "LBS Marg"] },
    alternates: [],
    avoided: [
      {
        segmentId: "S1",
        name: "Dr Ambedkar Road",
        depthCm: 47,
        probability: 0.9,
        at: "2019-07-02T08:20:00+05:30",
        path: [],
      },
    ],
    corridors: [],
    reasons: [
      {
        kind: "avoided",
        segmentId: "S1",
        name: "Dr Ambedkar Road",
        depthCm: 47,
        thresholdCm: 15,
        at: "2019-07-02T08:20:00+05:30",
      },
    ],
    tripId: null,
    notes: [],
    ms: 1,
    ...overrides,
  };
  const point = (name: string) => ({ name, lon: 72.86, lat: 19.04, fromRegister: true });
  return {
    kind: "answer",
    advisory: buildAdvisory(
      plan,
      vehicle,
      point("Sion Circle"),
      point("Kurla, LBS Marg"),
      "2019-07-02T09:40:00+05:30",
    ),
  };
}

const BODIES: RuralBody[] = [
  { kind: "ask", message: null },
  { kind: "outside", field: "from", typed: "79.088,21.146" },
  { kind: "unknown", field: "to", typed: "LBS Marg", suggestions: ["Kurla, LBS Marg"] },
  { kind: "upstream", message: "The VARUNA API did not answer." },
  answerBody(),
];

describe("the rural document", () => {
  it("carries no client JavaScript at all, in any state", () => {
    // The whole point of the screen (PRD 3.3). A script tag, an inline handler or a javascript:
    // URL would each break it on the phone this page exists for.
    for (const body of BODIES) {
      const html = renderRural(page(body));
      expect(html).not.toMatch(/<script/i);
      expect(html).not.toMatch(/\son[a-z]+\s*=/i);
      expect(html).not.toMatch(/javascript:/i);
    }
  });

  it("fetches no font and no stylesheet", () => {
    for (const body of BODIES) {
      const html = renderRural(page(body));
      expect(html).not.toMatch(/<link/i);
      expect(html).not.toMatch(/@import/i);
      expect(html).not.toMatch(/url\(/i);
    }
  });

  it("carries the team's 64 px emblem as a plain image, and counts it against the budget", () => {
    const markBytes = readFileSync(join(__dirname, "..", "..", "public", BRAND_MARK)).length;
    expect(BRAND_MARK).toBe(BRAND_MARK_64_SRC);
    for (const body of BODIES) {
      const html = renderRural(page(body));
      expect(html).toContain(`<h1><img src="${BRAND_MARK}" width="32" height="32" alt="">`);
      // The page and the one image it asks for, together, still inside UI_SPEC 7's 30 KB.
      const bytes = Buffer.byteLength(html, "utf8") + markBytes;
      expect(bytes, `${body.kind} with its emblem is ${bytes} bytes`).toBeLessThan(BUDGET_BYTES);
    }
  });

  it("stays inside the 30 KB budget in every state", () => {
    for (const body of BODIES) {
      const bytes = Buffer.byteLength(renderRural(page(body)), "utf8");
      expect(bytes, `${body.kind} is ${bytes} bytes`).toBeLessThan(BUDGET_BYTES);
    }
  });

  it("says what it does not know, whatever it was asked", () => {
    for (const body of BODIES) {
      const html = renderRural(page(body));
      expect(html).toContain("What we do not know here");
      expect(html).toContain("we have no drain map");
    }
  });

  it("refuses a point outside the built area instead of estimating", () => {
    const html = renderRural(page({ kind: "outside", field: "from", typed: "79.088,21.146" }));
    expect(html).toContain("outside the Mumbai");
    expect(html).not.toContain("PASSABLE UNTIL");
  });

  it("prints the run it is quoting, so no number floats free", () => {
    const html = renderRural(page(answerBody()));
    expect(html).toContain(RUN.runId);
    expect(html).toContain("Reconstructed replay of 2 Jul 2019");
    expect(html).toContain("06:40 IST");
  });

  it("answers with the road, the hour and what stops the vehicle", () => {
    const html = renderRural(page(answerBody()));
    expect(html).toContain("PASSABLE UNTIL 07:55");
    expect(html).toContain("47 cm");
    expect(html).toContain("Dr Ambedkar Road");
    expect(html).toContain("too deep for a two-wheeler");
    expect(html).toContain("Safer: leave before 07:55, or take LBS Marg (27 min)");
  });

  it("names the vehicle the way the rest of the product names it", () => {
    // This page used to compose "a " + the picker's word and print "a ambulance" one line above
    // an explanation, from the shared module, that said "an ambulance".
    const ambulance = RURAL_VEHICLES.find((v) => v.word === "ambulance");
    if (!ambulance) throw new Error("the picker lost the ambulance");
    const html = renderRural(page(answerBody({ avoided: [], reasons: [] }, ambulance), ambulance));
    expect(html).not.toContain("a ambulance");
    expect(html).toContain("an ambulance");
  });

  it("does not say water 'stops on foot', which is not English", () => {
    const foot = RURAL_VEHICLES.find((v) => v.word === "on foot");
    if (!foot) throw new Error("the picker lost the pedestrian");
    const html = renderRural(page(answerBody({ avoided: [], reasons: [] }, foot), foot));
    expect(html).not.toContain("stops on foot");
    expect(html).toContain("unsafe on foot");
  });

  it("carries the whole query in the share link", () => {
    expect(renderRural(page(answerBody()))).toContain("/rural?from=a&amp;to=b&amp;v=car");
  });

  it("has a print stylesheet that drops the form and puts ink on paper", () => {
    const html = renderRural(page(answerBody()));
    expect(html).toContain("@media print");
    expect(html).toMatch(/@media print\{[^}]*background:white/);
  });

  it("escapes what a reader typed", () => {
    const html = renderRural(
      page({ kind: "unknown", field: "from", typed: "<b>Sion</b>", suggestions: [] }),
    );
    expect(html).not.toContain("<b>Sion</b>");
    expect(html).toContain("&lt;b&gt;Sion&lt;/b&gt;");
  });

  it("escapes the five characters that break a document", () => {
    expect(esc(`a&b<c>d"e`)).toBe("a&amp;b&lt;c&gt;d&quot;e");
  });
});

describe("the stopper", () => {
  it("is the water on the road when the route went around nothing", () => {
    // The demo trip on the 06:40 cycle: no detour exists, and the road still floods.
    const found = stopper({
      avoided: [],
      reasons: [
        {
          kind: "timing",
          segmentId: "S2",
          name: "Sion Panvel Highway",
          dryBelowCm: 5,
          dryUntil: "2019-07-02T07:10:00+05:30",
          depthCm: 21,
          thresholdCm: 15,
          at: "2019-07-02T09:40:00+05:30",
        },
      ],
    });
    expect(found).toEqual({ depthCm: 21, street: "Sion Panvel Highway", at: "09:40" });
  });

  it("is nothing when the water never reaches the depth that stops the vehicle", () => {
    expect(
      stopper({
        avoided: [],
        reasons: [
          {
            kind: "timing",
            segmentId: "S2",
            name: "Sion Panvel Highway",
            depthCm: 9,
            thresholdCm: 15,
            at: "2019-07-02T09:40:00+05:30",
          },
        ],
      }),
    ).toBeNull();
  });
});

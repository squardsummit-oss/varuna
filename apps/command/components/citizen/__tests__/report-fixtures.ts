/**
 * Reports shaped as `GET /v1/reports` returns them, for the dashboard's report tests. The seed
 * carries a Commons photo with its credit, as the API's demo seed does; the citizen report carries
 * no stored photo.
 */

import { vi } from "vitest";

import type { PublicReport } from "@/lib/api/reports";

export function seedReport(overrides: Partial<PublicReport> = {}): PublicReport {
  return {
    id: "seed-RPT-MUM-HS-01-ankle",
    origin: "seed",
    synthetic: true,
    ts: "2019-07-02T08:31:00+05:30",
    received_at: null,
    lat: 19.012,
    lon: 72.841,
    coordinates: "rounded to 3 decimals",
    city: "mumbai",
    outside_aoi: false,
    place: "Hindmata junction",
    depth_hint: "ankle",
    depth_cm: 10,
    text: "Water over the kerb at Hindmata junction.",
    source: "seed",
    photo_attached: true,
    has_photo: true,
    photo_url:
      "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a8/Bombay_flooded_street.jpg/960px-Bombay_flooded_street.jpg",
    thumb_url:
      "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a8/Bombay_flooded_street.jpg/330px-Bombay_flooded_street.jpg",
    photo_note: null,
    credit: {
      title: "File:Bombay flooded street.jpg",
      author: "Hitesh Ashar",
      license: "CC BY 2.0",
      license_url: "https://creativecommons.org/licenses/by/2.0",
      source_url: "https://commons.wikimedia.org/wiki/File:Bombay_flooded_street.jpg",
      original_source: null,
      taken: "2005-08-01",
      caption: "Heavy monsoon in Mumbai, August 2005",
      note: "Illustrative photo from Wikimedia Commons; not taken at this spot or on 2 July 2019.",
    },
    status: "received",
    status_ts: null,
    history: [{ status: "received", ts: "2019-07-02T08:31:00+05:30", role: "citizen" }],
    ...overrides,
  };
}

export function citizenReport(overrides: Partial<PublicReport> = {}): PublicReport {
  return seedReport({
    id: "rpt-1789225538684",
    origin: "citizen",
    synthetic: false,
    ts: "2026-09-27T18:10:00+05:30",
    received_at: "2026-09-27T18:10:02+05:30",
    lat: 19.027,
    lon: 72.857,
    place: "King's Circle",
    depth_hint: "knee",
    depth_cm: 45,
    text: "Knee deep outside the station",
    source: "public-map",
    photo_attached: false,
    has_photo: false,
    photo_url: null,
    thumb_url: null,
    credit: null,
    ...overrides,
  });
}

/** A `fetch` stub that answers by path; anything unmatched is a 503 with the API's envelope. */
export function fetchByPath(
  handlers: [(path: string, url: URL) => boolean, (url: URL) => unknown | Response][],
) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, "http://api.test");
    for (const [matches, answer] of handlers) {
      if (!matches(url.pathname, url)) continue;
      const body = answer(url);
      return body instanceof Response
        ? body
        : new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
    }
    return new Response(
      JSON.stringify({ error: { code: "unavailable", message: "Not stubbed in this test." } }),
      { status: 503, headers: { "content-type": "application/json" } },
    );
  });
}

export function apiError(status: number, message: string): Response {
  return new Response(JSON.stringify({ error: { code: "not_found", message } }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

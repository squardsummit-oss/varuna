/**
 * Contract tests for the citizen report client.
 *
 * The fixtures are bodies `services/api/varuna_api/routers/reports.py` produced on 2026-09-26
 * (a citizen report with a stored photo and one "seen" status, a seed report with its Commons
 * photo and two seeded statuses, the same citizen report after the desk dismissed it), so a
 * rename on the API side fails here rather than on the dashboard or the desk. The seed's photo
 * credits are read from the committed `seed_reports.json` itself, which the API passes through
 * as it is.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearPassphrase, OPS_HEADER, writePassphrase } from "@/lib/api/ops";
import { isApiError } from "@/lib/api/client";
import {
  COORDINATES_NOT_STATED,
  DismissedReportSchema,
  isDismissed,
  loadDeskReports,
  loadPublicReports,
  loadReport,
  loadReports,
  photoCredit,
  PhotoCreditSchema,
  photoTooLarge,
  postReportStatus,
  PublicReportSchema,
  REPORT_PHOTO_MAX_CHARS,
  REPORT_STATUS_LABEL,
  REPORT_STATUSES,
  ReportAckSchema,
  ReportListSchema,
  reportPhotoUrl,
  reportToPin,
  resolvePhotoUrl,
  setReportStatus,
  statusLabel,
  submitReport,
} from "@/lib/api/reports";

const CITIZEN = {
  id: "rpt-1790442310511-b626e7",
  origin: "citizen",
  synthetic: false,
  ts: "2019-07-02T08:40:00+05:30",
  received_at: "2026-09-26T22:35:10+05:30",
  lat: 19.009,
  lon: 72.842,
  coordinates: "rounded to 3 decimals",
  city: "mumbai",
  outside_aoi: false,
  place: null,
  depth_hint: "knee",
  depth_cm: 45.0,
  text: "Knee deep outside the cinema.",
  source: "public-map",
  photo_attached: true,
  has_photo: true,
  photo_url: "/v1/reports/rpt-1790442310511-b626e7/photo?size=full",
  thumb_url: "/v1/reports/rpt-1790442310511-b626e7/photo?size=thumb",
  photo_note: null,
  credit: null,
  status: "seen",
  status_ts: "2026-09-26T22:35:10+05:30",
  history: [{ status: "seen", ts: "2026-09-26T22:35:10+05:30", role: "ward officer", note: null }],
};

const SEED = {
  id: "seed-RPT-MUM-HS-04-ankle",
  origin: "seed",
  synthetic: true,
  ts: "2019-07-02T07:05:00+05:30",
  received_at: null,
  lat: 19.033,
  lon: 72.858,
  coordinates: "rounded to 3 decimals",
  city: "mumbai",
  outside_aoi: false,
  place: "Gandhi Market (Matunga)",
  depth_hint: "ankle",
  depth_cm: 10.0,
  text: "Water over the kerb at Gandhi Market (Matunga).",
  source: "seed",
  photo_attached: true,
  has_photo: true,
  photo_url:
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/6/67/Bombay_flooded_street2.jpg/960px-Bombay_flooded_street2.jpg",
  thumb_url:
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/6/67/Bombay_flooded_street2.jpg/330px-Bombay_flooded_street2.jpg",
  photo_note: null,
  credit: {
    title: "Bombay flooded street2.jpg",
    author: "Hitesh Ashar",
    license: "CC BY 2.0",
    license_url: "https://creativecommons.org/licenses/by/2.0",
    source_url: "https://commons.wikimedia.org/wiki/File:Bombay_flooded_street2.jpg",
    original_source: "https://www.flickr.com/photos/asharism/30477406/",
    taken: "2005-08-01",
    caption: "Heavy monsoon in Mumbai, August 2005",
    note: "Illustrative photo from Wikimedia Commons; not taken at this spot or on 2 July 2019.",
  },
  status: "crew_sent",
  status_ts: "2019-07-02T07:46:00+05:30",
  status_seeded: true,
  history: [
    {
      status: "seen",
      ts: "2019-07-02T07:18:00+05:30",
      role: "ward officer",
      note: "Seeded demo status; nobody at the ward desk set it.",
      seeded: true,
    },
    {
      status: "crew_sent",
      ts: "2019-07-02T07:46:00+05:30",
      role: "control room",
      note: "Seeded demo status; nobody at the ward desk set it.",
      seeded: true,
    },
  ],
  bundle_report_id: "RPT-MUM-HS-04-ankle",
  hotspot_id: "MUM-HS-04",
};

/** A seed report with no photo and no status, as `GET /v1/reports` serves it. */
const SEED_BARE = {
  ...SEED,
  id: "seed-RPT-MUM-HS-06-ankle",
  place: "Chunabhatti railway station",
  photo_attached: false,
  has_photo: false,
  photo_url: null,
  thumb_url: null,
  credit: null,
  status: "received",
  status_ts: null,
  status_seeded: false,
  history: [],
  bundle_report_id: "RPT-MUM-HS-06-ankle",
  hotspot_id: "MUM-HS-06",
};

const DISMISSED = {
  id: "rpt-1790442310511-b626e7",
  origin: "citizen",
  status: "dismissed",
  status_ts: "2026-09-26T22:35:10+05:30",
  history: [
    { status: "seen", ts: "2026-09-26T22:35:10+05:30", role: "ward officer", note: null },
    { status: "dismissed", ts: "2026-09-26T22:35:10+05:30", role: "ward officer", note: null },
  ],
  note: "The ward desk dismissed this report, so it is no longer shown on any map.",
};

const LIST = {
  count: 7,
  n_returned: 2,
  n_dismissed_hidden: 0,
  n_outside_hidden: 0,
  reports: [CITIZEN, SEED],
  notes: ["Coordinates are rounded to 3 decimals (about 110 m)."],
};

const ACK = {
  id: "rpt-1790442310511-b626e7",
  accepted: true,
  status: "received",
  city: "mumbai",
  outside_aoi: false,
  run_id: null,
  streets_nearby: 0,
  feedback_streets: null,
  photo_attached: true,
  photo_stored: true,
  photo_note: null,
  photo_url: "/v1/reports/rpt-1790442310511-b626e7/photo?size=full",
  thumb_url: "/v1/reports/rpt-1790442310511-b626e7/photo?size=thumb",
  message: "Thanks. Your report is queued; the next cycle assimilates it.",
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

interface Sent {
  url: string;
  init: RequestInit | undefined;
}

function capture(payload: unknown, status = 200): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    sent.push({ url: String(input), init });
    return jsonResponse(payload, status);
  });
  return sent;
}

beforeEach(() => {
  clearPassphrase();
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  clearPassphrase();
});

describe("schemas", () => {
  it("accept the bodies the API writes", () => {
    expect(PublicReportSchema.safeParse(CITIZEN).success).toBe(true);
    expect(PublicReportSchema.safeParse(SEED).success).toBe(true);
    expect(DismissedReportSchema.safeParse(DISMISSED).success).toBe(true);
    expect(ReportAckSchema.safeParse(ACK).success).toBe(true);
  });

  it("refuse a status the desk cannot set", () => {
    expect(PublicReportSchema.safeParse({ ...CITIZEN, status: "fixed" }).success).toBe(false);
  });

  it("label every status in sentence case", () => {
    for (const status of REPORT_STATUSES) {
      const label = REPORT_STATUS_LABEL[status];
      expect(label[0]).toBe(label[0]?.toUpperCase());
      expect(label.slice(1)).toBe(label.slice(1).toLowerCase());
    }
  });
});

describe("resolvePhotoUrl", () => {
  it("prefixes the API's own photo route with the API base", () => {
    expect(resolvePhotoUrl(CITIZEN.thumb_url)).toMatch(
      /^https?:\/\/[^/]+\/v1\/reports\/rpt-1790442310511-b626e7\/photo\?size=thumb$/,
    );
  });

  it("uses a seed's https Commons link as it is", () => {
    expect(resolvePhotoUrl(SEED.thumb_url)).toBe(SEED.thumb_url);
  });

  it("refuses anything an img must never load", () => {
    for (const bad of [
      "data:image/svg+xml;base64,PHN2Zz4=",
      "javascript:alert(1)",
      "//evil.example/x.jpg",
      "http://example.com/x.jpg",
      "",
      null,
      undefined,
    ]) {
      expect(resolvePhotoUrl(bad)).toBeNull();
    }
  });
});

describe("helpers", () => {
  it("credit a seed photo to its author and licence", () => {
    expect(photoCredit(PublicReportSchema.parse(SEED).credit)).toBe(
      "Photo: Hitesh Ashar, CC BY 2.0, via Wikimedia Commons",
    );
    expect(photoCredit(null)).toBeNull();
  });

  it("know when a photo is too long for the API", () => {
    expect(photoTooLarge("x".repeat(REPORT_PHOTO_MAX_CHARS))).toBe(false);
    expect(photoTooLarge("x".repeat(REPORT_PHOTO_MAX_CHARS + 1))).toBe(true);
    expect(photoTooLarge(undefined)).toBe(false);
  });

  it("tell a dismissed report from a public one", () => {
    expect(isDismissed(DismissedReportSchema.parse(DISMISSED))).toBe(true);
    expect(isDismissed(PublicReportSchema.parse(CITIZEN))).toBe(false);
  });
});

describe("reads", () => {
  it("ask the public list with every filter and parse it", async () => {
    const sent = capture(LIST);

    const list = await loadPublicReports({
      city: "mumbai",
      bbox: [72.83, 19.0, 72.86, 19.03],
      status: "seen",
      since: "2026-09-26T18:00:00+05:30",
      origin: "citizen",
      limit: 20,
    });

    expect(list.reports.map((r) => r.id)).toEqual([CITIZEN.id, SEED.id]);
    const url = new URL(sent[0]!.url);
    expect(url.pathname).toBe("/v1/reports");
    expect(url.searchParams.get("bbox")).toBe("72.83,19,72.86,19.03");
    expect(url.searchParams.get("status")).toBe("seen");
    expect(url.searchParams.get("city")).toBe("mumbai");
    expect(new Headers(sent[0]!.init?.headers).get(OPS_HEADER)).toBeNull();
  });

  it("read one report, dismissed or not", async () => {
    capture(DISMISSED);
    const report = await loadReport(DISMISSED.id);
    expect(isDismissed(report)).toBe(true);
  });

  it("send the desk passphrase for the exact list and nowhere else", async () => {
    writePassphrase("monsoon-desk");
    const sent = capture({ ...LIST, writes_enabled: true });

    await loadDeskReports({ status: "dismissed" });

    expect(new URL(sent[0]!.url).pathname).toBe("/v1/ops/reports");
    expect(new Headers(sent[0]!.init?.headers).get(OPS_HEADER)).toBe("monsoon-desk");
  });
});

describe("setReportStatus", () => {
  it("posts the status with the passphrase and trims the note to 300 characters", async () => {
    writePassphrase("monsoon-desk");
    const sent = capture({
      entry: { id: "037be64690d0", ts: "2026-09-26T22:35:10+05:30", status: "crew_sent" },
      city: "mumbai",
      report: { ...CITIZEN, status: "crew_sent" },
      notes: ["The status is appended to the ops log; the report is unchanged."],
    });

    const result = await setReportStatus({
      reportId: CITIZEN.id,
      status: "crew_sent",
      note: `  ${"n".repeat(400)}  `,
      role: "control room",
    });

    expect(result.report.status).toBe("crew_sent");
    expect(new URL(sent[0]!.url).pathname).toBe(`/v1/ops/reports/${CITIZEN.id}/status`);
    expect(new Headers(sent[0]!.init?.headers).get(OPS_HEADER)).toBe("monsoon-desk");
    const body = JSON.parse(String(sent[0]!.init?.body)) as Record<string, string>;
    expect(body.status).toBe("crew_sent");
    expect(body.role).toBe("control room");
    expect(body.note).toHaveLength(300);
    expect(body).not.toHaveProperty("user");
  });
});

describe("seed reports", () => {
  it("parse with their seeded statuses and register hotspot", () => {
    const seed = PublicReportSchema.parse(SEED);
    expect(seed.status_seeded).toBe(true);
    expect(seed.history.every((entry) => entry.seeded === true)).toBe(true);
    expect(seed.hotspot_id).toBe("MUM-HS-04");
    expect(PublicReportSchema.parse(SEED_BARE).status_seeded).toBe(false);
  });

  it("say a seeded status is a demo status and leave a real one alone", () => {
    expect(statusLabel("crew_sent", { seeded: true })).toBe("Crew sent (demo status)");
    expect(statusLabel("crew_sent")).toBe("Crew sent");
    expect(statusLabel("seen", { seeded: false })).toBe("Seen by the ward desk");
  });

  it("carry credits the client parses and photo links an img may load, in the committed file", () => {
    // `import.meta.url` is not a file URL under vitest; `__dirname` is the stable anchor.
    const file = path.resolve(__dirname, "../../../../services/api/varuna_api/seed_reports.json");
    const seed = JSON.parse(readFileSync(file, "utf8")) as {
      reports: { id: string; photo: { thumb_url: string; url: string; credit: unknown } | null }[];
    };
    const photos = seed.reports.flatMap((row) => (row.photo ? [row.photo] : []));

    expect(seed.reports).toHaveLength(8);
    expect(photos).toHaveLength(5);
    for (const photo of photos) {
      const credit = PhotoCreditSchema.parse(photo.credit);
      expect(credit.note).toContain("not taken at this spot or on 2 July 2019");
      expect(photoCredit(credit)).toBe(
        `Photo: ${credit.author}, ${credit.license}, via Wikimedia Commons`,
      );
      expect(resolvePhotoUrl(photo.thumb_url)).toBe(photo.thumb_url);
      expect(resolvePhotoUrl(photo.url)).toBe(photo.url);
    }
  });
});

describe("reportPhotoUrl", () => {
  it("gives the thumbnail or the full size, and falls back to the other", () => {
    const seed = PublicReportSchema.parse(SEED);
    expect(reportPhotoUrl(seed, "thumb")).toBe(SEED.thumb_url);
    expect(reportPhotoUrl(seed, "full")).toBe(SEED.photo_url);
    expect(reportPhotoUrl({ ...seed, photo_url: null }, "full")).toBe(SEED.thumb_url);
    expect(reportPhotoUrl(PublicReportSchema.parse(CITIZEN), "full")).toMatch(
      /\/v1\/reports\/rpt-1790442310511-b626e7\/photo\?size=full$/,
    );
  });

  it("gives nothing without a photo, or for a URL an img must not load", () => {
    expect(reportPhotoUrl(PublicReportSchema.parse(SEED_BARE))).toBeNull();
    const seed = PublicReportSchema.parse(SEED);
    expect(reportPhotoUrl({ ...seed, has_photo: false })).toBeNull();
    expect(
      reportPhotoUrl({ ...seed, thumb_url: "javascript:alert(1)", photo_url: "data:x" }),
    ).toBeNull();
  });
});

describe("reportToPin", () => {
  it("resolves everything a screen prints for a seed with a photo", () => {
    expect(reportToPin(PublicReportSchema.parse(SEED))).toEqual({
      id: SEED.id,
      lon: 72.858,
      lat: 19.033,
      depthHint: "ankle",
      depthCm: 10,
      status: "crew_sent",
      statusLabel: "Crew sent (demo status)",
      statusSeeded: true,
      ts: "2019-07-02T07:05:00+05:30",
      text: "Water over the kerb at Gandhi Market (Matunga).",
      place: "Gandhi Market (Matunga)",
      thumbUrl: SEED.thumb_url,
      photoUrl: SEED.photo_url,
      credit: "Photo: Hitesh Ashar, CC BY 2.0, via Wikimedia Commons",
      synthetic: true,
      origin: "seed",
    });
  });

  it("credits nothing when no photo is shown, and keeps a citizen report real", () => {
    expect(reportToPin(PublicReportSchema.parse(SEED_BARE)).credit).toBeNull();
    const pin = reportToPin(PublicReportSchema.parse(CITIZEN));
    expect(pin.synthetic).toBe(false);
    expect(pin.statusLabel).toBe("Seen by the ward desk");
    expect(pin.credit).toBeNull();
    expect(pin.thumbUrl).toMatch(/size=thumb$/);
  });
});

describe("loadReports", () => {
  it("asks the public list by city, status and time, and parses citizen and seed rows", async () => {
    const sent = capture({ ...LIST, reports: [CITIZEN, SEED, SEED_BARE] });
    const controller = new AbortController();

    const list = await loadReports({
      city: "mumbai",
      status: "crew_sent",
      since: "2019-07-02T06:40:00+05:30",
      signal: controller.signal,
    });

    expect(list.reports.map((r) => r.origin)).toEqual(["citizen", "seed", "seed"]);
    const url = new URL(sent[0]!.url);
    expect(url.pathname).toBe("/v1/reports");
    expect(url.searchParams.get("city")).toBe("mumbai");
    expect(url.searchParams.get("status")).toBe("crew_sent");
    expect(url.searchParams.get("since")).toBe("2019-07-02T06:40:00+05:30");
    expect(url.searchParams.has("bbox")).toBe(false);
    expect(loadPublicReports).toBe(loadReports);
  });

  it("parses a list that carries no hidden counts without inventing them", () => {
    const list = ReportListSchema.parse({ count: 1, reports: [CITIZEN] });
    expect(list.n_dismissed_hidden).toBeUndefined();
    expect(list.notes).toBeUndefined();
  });

  /**
   * The list the deployed API served before 2026-09-26, row for row the shape its inbox stored.
   * Vercel ships on every push and Railway by hand, so the console meets this body in production.
   */
  const OLD_LIST = {
    count: 2,
    reports: [
      {
        id: "rpt-1757912345678-a1b2c3",
        ts: "2026-09-15T10:12:00+05:30",
        received_at: "2026-09-15T10:12:03+05:30",
        lat: 19.00912,
        lon: 72.84187,
        depth_hint: "knee",
        depth_cm: 45.0,
        text: null,
        has_photo: true,
        source: "public-map",
        synthetic: false,
      },
      {
        id: "rpt-1757912000000-d4e5f6",
        ts: "2026-09-15T10:06:00+05:30",
        received_at: "2026-09-15T10:06:01+05:30",
        lat: 19.0301,
        lon: 72.8561,
        depth_hint: "ankle",
        depth_cm: 10.0,
        text: "Water over the kerb.",
        has_photo: false,
        source: "public-map",
        synthetic: false,
      },
    ],
  };

  it("parses the list an older API serves, asserting nothing it did not say", async () => {
    capture(OLD_LIST);

    const list = await loadReports();

    const [withPhoto, bare] = list.reports;
    expect(withPhoto).toMatchObject({
      origin: "citizen",
      status: "received",
      history: [],
      coordinates: COORDINATES_NOT_STATED,
      photo_attached: true,
      // It was attached and never stored: there is nothing an <img> could load.
      has_photo: false,
    });
    expect(withPhoto!.city).toBeUndefined();
    expect(withPhoto!.outside_aoi).toBeUndefined();
    expect(bare!.photo_attached).toBe(false);
    expect(list.n_dismissed_hidden).toBeUndefined();

    const pin = reportToPin(withPhoto!);
    expect(pin.statusLabel).toBe("Received");
    expect(pin.thumbUrl).toBeNull();
    expect(pin.photoUrl).toBeNull();
    expect(pin.synthetic).toBe(false);
  });

  it("keeps a current API's photo flags as they are", () => {
    const report = PublicReportSchema.parse({ ...CITIZEN, has_photo: false, photo_url: null });
    expect(report.photo_attached).toBe(true);
    expect(report.has_photo).toBe(false);
    expect(PublicReportSchema.parse(CITIZEN).has_photo).toBe(true);
  });
});

describe("submitReport", () => {
  const INPUT = {
    ts: "2019-07-02T08:40:00+05:30",
    lat: 19.0091,
    lon: 72.8419,
    depth_hint: "knee" as const,
    text: "Knee deep outside the cinema.",
    photo_data_url: "data:image/jpeg;base64,/9j/4AAQ",
    source: "public-map",
  };

  it("posts the JSON body the offline queue replays, photo included, and returns the id", async () => {
    const sent = capture(ACK, 202);

    const ack = await submitReport(INPUT);

    expect(ack.id).toBe(ACK.id);
    expect(ack.message).toBe(ACK.message);
    expect(sent[0]!.init?.method).toBe("POST");
    expect(new URL(sent[0]!.url).pathname).toBe("/v1/reports");
    expect(JSON.parse(String(sent[0]!.init?.body))).toEqual(INPUT);
    expect(new Headers(sent[0]!.init?.headers).get(OPS_HEADER)).toBeNull();
  });

  it("accepts an older API's answer: an id and a message", async () => {
    capture({ id: "rpt-1790442310511-b626e7", accepted: true, message: "Thanks." }, 202);
    const ack = await submitReport(INPUT);
    expect(ack.id).toBe("rpt-1790442310511-b626e7");
    expect(ack.streets_nearby).toBeUndefined();
  });

  it("refuses a photo too long for the API before sending anything", async () => {
    const sent = capture(ACK, 202);
    const photo = `data:image/jpeg;base64,${"A".repeat(REPORT_PHOTO_MAX_CHARS)}`;

    const error = await submitReport({ ...INPUT, photo_data_url: photo }).catch((e: unknown) => e);

    expect(isApiError(error)).toBe(true);
    expect((error as Error).message).toMatch(/too large to send\. Nothing was sent\./);
    expect(sent).toHaveLength(0);
  });

  it("refuses a body the API would refuse", async () => {
    const sent = capture(ACK, 202);
    await expect(submitReport({ ...INPUT, lat: 120 })).rejects.toThrow();
    expect(sent).toHaveLength(0);
  });
});

describe("postReportStatus", () => {
  const RESULT = {
    entry: { id: "037be64690d0", ts: "2026-09-26T22:35:10+05:30", status: "seen" },
    city: "mumbai",
    report: { ...SEED, status: "seen", status_seeded: false },
    notes: ["The status is appended to the ops log; the report is unchanged."],
  };

  it("sends the passphrase it is given in the header and nowhere else", async () => {
    const sent = capture(RESULT);

    const result = await postReportStatus(SEED.id, "seen", "  Crew on the way  ", "monsoon-desk");

    expect(result.report.status).toBe("seen");
    const url = new URL(sent[0]!.url);
    expect(url.pathname).toBe(`/v1/ops/reports/${SEED.id}/status`);
    expect(url.search).toBe("");
    expect(new Headers(sent[0]!.init?.headers).get(OPS_HEADER)).toBe("monsoon-desk");
    const body = JSON.parse(String(sent[0]!.init?.body)) as Record<string, string>;
    expect(body).toEqual({ status: "seen", note: "Crew on the way" });
    expect(JSON.stringify(body)).not.toContain("monsoon-desk");
  });

  it("sends no header for an empty passphrase, and no note for an empty one", async () => {
    const sent = capture(RESULT);

    await postReportStatus(SEED.id, "resolved", "   ", "  ", { role: "field crew" });

    expect(new Headers(sent[0]!.init?.headers).get(OPS_HEADER)).toBeNull();
    const body = JSON.parse(String(sent[0]!.init?.body)) as Record<string, string>;
    expect(body).toEqual({ status: "resolved", role: "field crew" });
  });
});

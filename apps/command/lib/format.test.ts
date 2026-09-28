import { describe, expect, it } from "vitest";

import {
  MISSING,
  addMinutesIso,
  formatBeta,
  formatBetaWithSd,
  formatCm,
  formatCmDelta,
  formatCount,
  formatDate,
  formatDateTime,
  formatIst,
  formatKm,
  formatLead,
  formatMinutes,
  formatMs,
  formatPct,
  formatScore,
  formatSeconds,
  formatSpeed,
  formatTimeWithLead,
  minutesBetween,
  shortenRunId,
  toDate,
  toIstIso,
} from "./format";

// 2 July 2019, 06:40 IST is 01:10 UTC on the replay day (ADR-0007).
const REPLAY_UTC = "2019-07-02T01:10:00Z";
const REPLAY_IST = "2019-07-02T06:40:00+05:30";

describe("formatIst", () => {
  it("renders IST 24-hour time from a UTC instant", () => {
    expect(formatIst(REPLAY_UTC)).toBe("06:40");
  });
  it("renders the same instant from an offset ISO string", () => {
    expect(formatIst(REPLAY_IST)).toBe("06:40");
  });
  it("keeps 24-hour after midnight (00:05, never 24:05 or 12:05 am)", () => {
    expect(formatIst("2019-07-02T18:35:00Z")).toBe("00:05");
  });
  it("adds seconds on request", () => {
    expect(formatIst("2019-07-02T01:10:05Z", { seconds: true })).toBe("06:40:05");
  });
  it("returns the missing marker for invalid input", () => {
    expect(formatIst("not a date")).toBe(MISSING);
    expect(formatIst(null)).toBe(MISSING);
    expect(formatIst(undefined)).toBe(MISSING);
  });
});

describe("formatDate and formatDateTime", () => {
  it("renders day, short month, year in IST", () => {
    expect(formatDate(REPLAY_UTC)).toBe("2 Jul 2019");
  });
  it("rolls the date forward across IST midnight", () => {
    expect(formatDate("2019-07-02T19:00:00Z")).toBe("3 Jul 2019");
  });
  it("renders date, time and the IST suffix", () => {
    expect(formatDateTime(REPLAY_UTC)).toBe("2 Jul 2019 06:40 IST");
  });
});

describe("formatLead and formatTimeWithLead", () => {
  it("signs positive leads", () => {
    expect(formatLead(40)).toBe("+40 min");
  });
  it("signs negative leads", () => {
    expect(formatLead(-15)).toBe("-15 min");
  });
  it("signs zero as plus", () => {
    expect(formatLead(0)).toBe("+0 min");
  });
  it("rounds fractional minutes", () => {
    expect(formatLead(39.6)).toBe("+40 min");
  });
  it("combines time and lead", () => {
    expect(formatTimeWithLead("2019-07-02T01:50:00Z", 40)).toBe("07:20 (+40 min)");
  });
  it("does not append a lead to a missing time", () => {
    expect(formatTimeWithLead(null, 40)).toBe(MISSING);
  });
});

describe("depth", () => {
  it("formats whole centimetres with a unit", () => {
    expect(formatCm(45)).toBe("45 cm");
    expect(formatCm(44.6)).toBe("45 cm");
  });
  it("clamps negative depth to zero", () => {
    expect(formatCm(-3)).toBe("0 cm");
  });
  it("formats a before-after pair", () => {
    expect(formatCmDelta(55, 20)).toBe("55 → 20 cm");
  });
  it("handles NaN", () => {
    expect(formatCm(Number.NaN)).toBe(MISSING);
  });
});

describe("formatPct", () => {
  it("takes fractions by default", () => {
    expect(formatPct(0.82)).toBe("82 %");
  });
  it("takes percents when told", () => {
    expect(formatPct(82, { fraction: false })).toBe("82 %");
  });
  it("clamps to the unit interval", () => {
    expect(formatPct(1.4)).toBe("100 %");
    expect(formatPct(-0.2)).toBe("0 %");
  });
});

describe("durations and distances", () => {
  it("formats milliseconds under a second", () => {
    expect(formatMs(820)).toBe("820 ms");
  });
  it("formats seconds with one decimal", () => {
    expect(formatMs(3900)).toBe("3.9 s");
  });
  it("formats minutes with padded seconds", () => {
    expect(formatMs(65_000)).toBe("1 min 05 s");
  });
  it("formats metres and kilometres", () => {
    expect(formatKm(850)).toBe("850 m");
    expect(formatKm(2400)).toBe("2.4 km");
  });
  it("formats minutes, hours and mixed", () => {
    expect(formatMinutes(25)).toBe("25 min");
    expect(formatMinutes(180)).toBe("3 h");
    expect(formatMinutes(80)).toBe("1 h 20 min");
  });
  it("formats elapsed seconds", () => {
    expect(formatSeconds(0)).toBe("0 s");
    expect(formatSeconds(42)).toBe("42 s");
    expect(formatSeconds(65)).toBe("1 min 05 s");
    expect(formatSeconds(180)).toBe("3 min");
  });
});

describe("scores and ids", () => {
  it("formats beta with two decimals and clamps", () => {
    expect(formatBeta(0.4234)).toBe("0.42");
    expect(formatBeta(1.2)).toBe("1.00");
    expect(formatBetaWithSd(0.42, 0.08)).toBe("0.42 ± 0.08");
  });
  it("formats speed with a multiplication sign", () => {
    expect(formatSpeed(30)).toBe("30×");
  });
  it("formats counts with Indian grouping", () => {
    expect(formatCount(120000)).toBe("1,20,000");
  });
  it("formats skill scores", () => {
    expect(formatScore(0.712)).toBe("0.71");
  });
  it("shortens long run ids from the middle", () => {
    const id = "MUM-20190702T0640-sky1.0-twin1.0-flash0.3-baked";
    const short = shortenRunId(id, 20);
    expect(short.length).toBe(20);
    expect(short.startsWith("MUM-2019")).toBe(true);
    expect(short.endsWith("baked")).toBe(true);
    expect(shortenRunId("short")).toBe("short");
    expect(shortenRunId(null)).toBe(MISSING);
  });
});

describe("instants", () => {
  it("parses and rejects", () => {
    expect(toDate(REPLAY_UTC)?.toISOString()).toBe("2019-07-02T01:10:00.000Z");
    expect(toDate("garbage")).toBeNull();
    expect(toDate("")).toBeNull();
  });
  it("measures minutes between instants", () => {
    expect(minutesBetween(REPLAY_UTC, "2019-07-02T01:50:00Z")).toBe(40);
    expect(minutesBetween(REPLAY_UTC, "bad")).toBeNull();
  });
  it("writes ISO with the +05:30 offset", () => {
    expect(toIstIso(REPLAY_UTC)).toBe("2019-07-02T06:40:00+05:30");
    expect(addMinutesIso(REPLAY_UTC, 40)).toBe("2019-07-02T07:20:00+05:30");
    expect(addMinutesIso("bad", 40)).toBeNull();
  });
});

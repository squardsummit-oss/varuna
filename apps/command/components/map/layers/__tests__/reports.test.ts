/**
 * The citizen report pins (`layers/reports.ts`, motion M32): what each report draws, what it
 * never draws, and that the drop is M18's mechanism rather than a copy of it.
 */

import { describe, expect, it, vi } from "vitest";

import { reportToPin, type PublicReport } from "@/lib/api/reports";
import { depthColor, hexToRgb, obsColor } from "@/lib/ramps";
import {
  REPORT_FILL,
  REPORT_LAYER_PREFIX,
  REPORT_STATUS_STYLE,
  isReportLayer,
  reportDepthWords,
  reportPinLayers,
  reportTooltipText,
  type DroppingReportPin,
} from "../reports";
import { TruthDropExtension } from "../truth-pins";

interface LayerLike {
  id: string;
  props: Record<string, unknown> & {
    data: DroppingReportPin[];
    pickable?: boolean;
    onClick?: (info: { object?: DroppingReportPin }) => boolean;
    extensions?: unknown[];
    dropStartMs?: number;
    dropPart?: string;
    getLineColor?: unknown;
    getLineWidth?: unknown;
    getFillColor?: unknown;
  };
}

const THUMB =
  "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a8/Bombay_flooded_street.jpg/330px-Bombay_flooded_street.jpg";

/** A pin in `lib/api/reports`' shape, as `reportToPin` builds one. */
function pin(overrides: Partial<DroppingReportPin> = {}): DroppingReportPin {
  return {
    id: "rpt-1",
    lon: 72.841,
    lat: 19.012,
    depthHint: "knee",
    depthCm: 45,
    status: "received",
    statusLabel: "Received",
    statusSeeded: false,
    ts: "2019-07-02T08:47:00+05:30",
    text: null,
    place: "Hindmata junction",
    thumbUrl: null,
    photoUrl: null,
    credit: null,
    synthetic: false,
    origin: "citizen",
    ...overrides,
  };
}

function layers(options: Parameters<typeof reportPinLayers>[0]): LayerLike[] {
  return reportPinLayers(options) as LayerLike[];
}

function byId(list: LayerLike[], id: string): LayerLike {
  const found = list.find((l) => l.id === id);
  if (!found) throw new Error(`no layer ${id} in ${list.map((l) => l.id).join(", ")}`);
  return found;
}

describe("reportPinLayers", () => {
  it("draws nothing for no reports", () => {
    expect(reportPinLayers({ reports: [] })).toEqual([]);
  });

  it("fills every pin in --obs-report, straight from the token", () => {
    const [r, g, b] = hexToRgb(obsColor("report"));
    expect(REPORT_FILL.slice(0, 3)).toEqual([r, g, b]);
  });

  it("names every layer with the report prefix, so a tooltip can tell a pin from a street", () => {
    const built = layers({
      reports: [pin(), pin({ id: "rpt-2", thumbUrl: THUMB })],
      selectedReportId: "rpt-2",
    });
    expect(built.length).toBeGreaterThan(0);
    for (const layer of built) {
      expect(layer.id.startsWith(REPORT_LAYER_PREFIX)).toBe(true);
      expect(isReportLayer(layer)).toBe(true);
    }
    expect(isReportLayer({ id: "streets-wet" })).toBe(false);
  });

  it("rings only the reports that carry a photo", () => {
    const built = layers({
      reports: [pin(), pin({ id: "rpt-2", thumbUrl: THUMB })],
    });
    expect(byId(built, "report-photo-rings").props.data.map((d) => d.id)).toEqual(["rpt-2"]);
    expect(byId(built, "report-pins").props.data.map((d) => d.id)).toEqual(["rpt-1", "rpt-2"]);
  });

  it("draws no photo ring when no report has a photo", () => {
    const built = layers({ reports: [pin()] });
    expect(built.map((l) => l.id)).toEqual(["report-pins"]);
  });

  it("carries the status in the outline's colour and width, never in the depth ramp", () => {
    // One depth from each band of the ramp: dry, 5-15, 15-30, 30-45, 45-60, above 60 cm.
    const depthRamp = [0, 10, 20, 35, 50, 70].map((cm) => hexToRgb(depthColor(cm)).join(","));
    const widths = new Set<number>();
    for (const style of Object.values(REPORT_STATUS_STYLE)) {
      expect(depthRamp).not.toContain(style.line.slice(0, 3).join(","));
      widths.add(style.width);
    }
    // Width varies with status, so the state is not carried by colour alone (6.10).
    expect(widths.size).toBeGreaterThan(2);

    const built = layers({
      reports: [pin({ status: "received" }), pin({ id: "rpt-2", status: "crew_sent" })],
    });
    const pins = byId(built, "report-pins");
    const width = pins.props.getLineWidth as (d: DroppingReportPin) => number;
    const line = pins.props.getLineColor as (d: DroppingReportPin) => number[];
    const [received, crew] = pins.props.data;
    expect(width(received!)).toBe(REPORT_STATUS_STYLE.received.width);
    expect(width(crew!)).toBe(REPORT_STATUS_STYLE.crew_sent.width);
    expect(line(crew!)).toEqual(REPORT_STATUS_STYLE.crew_sent.line);
  });

  it("fades a resolved report's fill rather than removing it", () => {
    const built = layers({ reports: [pin({ status: "resolved" })] });
    const fill = byId(built, "report-pins").props.getFillColor as (
      d: DroppingReportPin,
    ) => number[];
    expect(fill(pin({ status: "resolved" }))[3]).toBe(REPORT_STATUS_STYLE.resolved.fillAlpha);
    expect(REPORT_STATUS_STYLE.resolved.fillAlpha).toBeLessThan(255);
  });

  it("is unpickable without a handler and hands the tapped id to one", () => {
    expect(byId(layers({ reports: [pin()] }), "report-pins").props.pickable).toBe(false);

    const onPick = vi.fn();
    const pins = byId(layers({ reports: [pin()], onPick }), "report-pins");
    expect(pins.props.pickable).toBe(true);
    // Returning true keeps the tap from also reaching the street underneath.
    expect(pins.props.onClick?.({ object: pin() })).toBe(true);
    expect(onPick).toHaveBeenCalledWith("rpt-1");
    expect(pins.props.onClick?.({})).toBe(false);
  });

  it("rings the selected report last, so no neighbour covers it", () => {
    const built = layers({
      reports: [pin(), pin({ id: "rpt-2" })],
      selectedReportId: "rpt-2",
    });
    const selected = built.at(-1);
    expect(selected?.id).toBe("report-selected");
    expect(selected?.props.data.map((d) => d.id)).toEqual(["rpt-2"]);
    expect(selected?.props.pickable).toBe(false);
  });

  it("drops a new report on M18's extension, with a ripple under it (motion M32)", () => {
    const built = layers({
      reports: [pin(), pin({ id: "rpt-new", thumbUrl: THUMB, dropStartMs: 5000 })],
    });
    const ids = built.map((l) => l.id);
    // Ripples first, then the landed pins, then the ones still dropping.
    expect(ids).toEqual([
      "report-ripple-rpt-new",
      "report-pins",
      "report-drop-photo-rpt-new",
      "report-drop-rpt-new",
    ]);
    const ripple = byId(built, "report-ripple-rpt-new");
    expect(ripple.props.dropPart).toBe("ripple");
    expect(ripple.props.dropStartMs).toBe(5000);
    expect(ripple.props.pickable).toBe(false);
    expect(ripple.props.extensions?.[0]).toBeInstanceOf(TruthDropExtension);
    const drop = byId(built, "report-drop-rpt-new");
    expect(drop.props.dropPart).toBe("pin");
    expect(drop.props.extensions?.[0]).toBeInstanceOf(TruthDropExtension);
  });

  it("under reduced motion lands every pin at once, with no ripple", () => {
    const built = layers({
      reports: [pin(), pin({ id: "rpt-new", dropStartMs: 5000 })],
      reducedMotion: true,
    });
    expect(built.map((l) => l.id)).toEqual(["report-pins"]);
    expect(built[0]?.props.extensions ?? []).toEqual([]);
  });
});

describe("report words", () => {
  it("says the depth as the reporter chose it, with the centimetres Pulse assumes", () => {
    expect(reportDepthWords({ depthHint: "knee", depthCm: 45 })).toBe("Knee deep, about 45 cm");
    expect(reportDepthWords({ depthHint: null, depthCm: 10 })).toBe("Water about 10 cm");
    expect(reportDepthWords({ depthHint: null, depthCm: null })).toBeNull();
    // No centimetres from the API: the ones Pulse assumes for the chip (SPEC.md 11.6).
    expect(reportDepthWords({ depthHint: "waist" })).toBe("Waist deep, about 90 cm");
    expect(reportDepthWords({ depthHint: "ankle" })).toBe("Ankle deep, about 10 cm");
  });

  it("names a seeded report as synthetic in its tooltip", () => {
    const text = reportTooltipText(
      pin({
        synthetic: true,
        origin: "seed",
        thumbUrl: THUMB,
        status: "crew_sent",
        statusLabel: "Crew sent (demo status)",
        statusSeeded: true,
      }),
    );
    expect(text.split("\n")).toEqual([
      "Hindmata junction",
      "Knee deep, about 45 cm",
      "Crew sent (demo status)",
      "Photo attached",
      "Demo report (synthetic)",
    ]);
  });
});

describe("a pin built by reportToPin", () => {
  const base: PublicReport = {
    id: "seed-3",
    origin: "seed",
    synthetic: false,
    ts: "2019-07-02T08:47:00+05:30",
    received_at: null,
    lat: 19.032,
    lon: 72.858,
    coordinates: "rounded to 3 decimals",
    city: "mumbai",
    outside_aoi: false,
    place: "Gandhi Market",
    depth_hint: "waist",
    depth_cm: 90,
    text: null,
    source: "seed",
    photo_attached: true,
    has_photo: true,
    photo_url: "https://upload.wikimedia.org/x.jpg",
    thumb_url: "https://upload.wikimedia.org/x-330.jpg",
    photo_note: null,
    credit: null,
    status: "seen",
    status_ts: null,
    status_seeded: true,
    history: [],
  };

  it("is drawn as synthetic, with a photo ring, and names its seeded status", () => {
    const made = reportToPin(base);
    expect(made.synthetic).toBe(true);
    const built = layers({ reports: [made] });
    expect(byId(built, "report-photo-rings").props.data.map((d) => d.id)).toEqual(["seed-3"]);
    expect(reportTooltipText(made).split("\n")).toEqual([
      "Gandhi Market",
      "Waist deep, about 90 cm",
      "Seen by the ward desk (demo status)",
      "Photo attached",
      "Demo report (synthetic)",
    ]);
  });

  it("rings a citizen's photo only when the API actually kept it", () => {
    const sent = reportToPin({
      ...base,
      origin: "citizen",
      has_photo: false,
      photo_attached: true,
      photo_url: null,
      thumb_url: null,
      status_seeded: false,
    });
    expect(sent.synthetic).toBe(false);
    expect(layers({ reports: [sent] }).map((l) => l.id)).toEqual(["report-pins"]);
  });
});

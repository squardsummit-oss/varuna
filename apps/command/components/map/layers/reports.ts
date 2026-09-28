/**
 * Citizen report pins: what people on the street said the water was doing, on the map the ward
 * officer and the citizen both read (dashboard and ward desk; motion M32).
 *
 * **A report is an observation, not a forecast, and it is drawn as one.** The pin is filled in
 * `--obs-report`, the colour SPEC.md 6.2 gives citizen reports in Pulse, and never in the depth
 * ramp: the depth ramp means modelled water depth on a street and nothing else. What the reporter
 * said about depth ("knee") is on the card and in the tooltip as words and centimetres.
 *
 * Three things are read off a pin without opening it:
 *
 * - **its status**, from the outline: a thin `--ink` casing while it waits, `--text` once the ward
 *   desk has seen it, a thick `--tide` ring once a crew is on the way, and `--naive` grey with a
 *   faded fill when it is resolved or dismissed. The outline also changes width, so the state is
 *   not carried by colour alone (6.10), and the card always prints the status in words;
 * - **whether it carries a photo**, from a second, open ring in the report colour around it;
 * - **which one is selected**, from a `--text` ring outside both.
 *
 * **M32 is M18's mechanism, reused rather than copied.** A report that arrives after the map first
 * drew its reports is stamped with the `performance.now()` its drop begins at
 * ({@link useReportDrops}), and its layers carry `TruthDropExtension` from `truth-pins.ts`: the pin
 * scales 0 to 1 on the catalogue spring and a ripple expands to the same 4.6 times the pin's size
 * and fades over 600 ms, all on deck's clock with no React render per frame. Reports that were
 * already there when the map first drew them are simply there - dropping forty pins on page load
 * would announce nothing. Under reduced motion nothing is stamped and no ripple is built: the pin
 * appears (section 8's fallback).
 *
 * Pins are markers, sized in pixels, so a report never grows into a disc over the junction it is
 * about.
 */

import { ScatterplotLayer } from "@deck.gl/layers";
import { useEffect, useMemo, useState } from "react";

import { REPORT_STATUS_LABEL, type ReportPin, type ReportStatus } from "@/lib/api/reports";
import { DEPTH_HINT_CM, type DepthHint } from "@/lib/api/schemas";
import { DROP_MS } from "@/lib/hooks/use-truth-pins";
import { colors, hexToRgba, obsColor } from "@/lib/ramps";
import type { Rgba } from "./palette";
import { RIPPLE_ALPHA, TruthDropExtension } from "./truth-pins";

/**
 * One citizen report as the map draws it: `lib/api/reports.ts`'s pin, built by `reportToPin`, so
 * the map, the dashboard's list and the desk's inbox all read the same resolved fields.
 */
export type { ReportPin } from "@/lib/api/reports";

/**
 * A pin with its drop state (motion M32). `dropStartMs` is the `performance.now()` at which the
 * pin began to drop: absent for a pin that has landed or was there when the map first drew,
 * `Infinity` until its first frame, which draws nothing. Set by {@link useReportDrops}, never by a
 * screen.
 */
export type DroppingReportPin = ReportPin & { dropStartMs?: number };

/** A photo is stored and can be shown: the pin draws the open ring around itself. */
export function reportHasPhoto(pin: Pick<ReportPin, "thumbUrl" | "photoUrl">): boolean {
  return Boolean(pin.thumbUrl ?? pin.photoUrl);
}

// ---- Colours and sizes ----------------------------------------------------------------------

function rgba(hex: string, alpha: number): Rgba {
  const [r, g, b] = hexToRgba(hex, alpha);
  return [r, g, b, alpha];
}

/** `--obs-report` #FDE68A: the pin's fill, straight from the token rather than typed out again. */
export const REPORT_FILL: Rgba = rgba(obsColor("report"), 255);

/** `--obs-report` at 86 %: the open ring that says a photo is attached. */
export const REPORT_PHOTO_RING: Rgba = rgba(obsColor("report"), 220);

/** `--text`: the ring around the report whose card is open. */
export const REPORT_SELECTED_RING: Rgba = rgba(colors.text, 255);

/** The pin's radius in pixels, and the rings drawn outside it. */
export const REPORT_PIN_PX = 6;
export const REPORT_PHOTO_RING_PX = 10;
export const REPORT_SELECTED_PX = 14;

/** How a status is drawn: an outline colour and width, and the fill's alpha. */
export interface ReportStatusStyle {
  line: Rgba;
  /** Outline width in pixels; wider means further along, so width carries the state too. */
  width: number;
  fillAlpha: number;
}

/**
 * The outline each status draws with. No depth-ramp colour anywhere (6.2): the ramp means water
 * depth, and a status is not a depth.
 */
export const REPORT_STATUS_STYLE: Record<ReportStatus, ReportStatusStyle> = {
  // Waiting: the pin itself, with only the `--ink` casing that lifts it off the imagery.
  received: { line: rgba(colors.ink, 235), width: 1.5, fillAlpha: 255 },
  // Somebody at the ward desk has read it.
  seen: { line: rgba(colors.text, 255), width: 2, fillAlpha: 255 },
  // A crew is on the way: the brand accent, and the widest ring.
  crew_sent: { line: rgba(colors.tide, 255), width: 3, fillAlpha: 255 },
  // Finished with: grey and quieter, still there so the history of the storm is readable.
  resolved: { line: rgba(colors.naive, 255), width: 2, fillAlpha: 140 },
  // Only the desk's view carries dismissed reports; the public list withholds them.
  dismissed: { line: rgba(colors.naive, 200), width: 1, fillAlpha: 70 },
};

function styleOf(pin: DroppingReportPin): ReportStatusStyle {
  return REPORT_STATUS_STYLE[pin.status] ?? REPORT_STATUS_STYLE.received;
}

// ---- Layers ---------------------------------------------------------------------------------

/** Every report layer's id starts with this, so a picking handler can tell a pin from a street. */
export const REPORT_LAYER_PREFIX = "report-";

/**
 * The zoom a report is brought into view at, on the desk and on the dashboard: close enough to see
 * the street, as a hotspot fly-to settles.
 */
export const REPORT_FOCUS_ZOOM = 15;

/** True for a layer this module built. */
export function isReportLayer(layer: unknown): boolean {
  const id = (layer as { id?: unknown } | null | undefined)?.id;
  return typeof id === "string" && id.startsWith(REPORT_LAYER_PREFIX);
}

/** One instance for every report layer, so a rebuilt layer keeps its compiled program. */
const DROP = new TruthDropExtension();

export interface ReportPinLayerOptions {
  reports: readonly DroppingReportPin[];
  selectedReportId?: string | null;
  /** A pin was tapped. Absent leaves the pins unpickable. */
  onPick?: (id: string) => void;
  /** Every pin drawn landed, with no ripple (section 8's fallback for M32). */
  reducedMotion?: boolean;
}

type DropLayerProps = ConstructorParameters<typeof ScatterplotLayer<DroppingReportPin>>[0] & {
  dropStartMs?: number;
  dropPart?: "pin" | "ripple";
};

const position = (d: DroppingReportPin) => [d.lon, d.lat] as [number, number];

/**
 * A layer-level click handler rather than deck's own `onClick`, so it works the same under
 * `CityMap`'s DeckGL and under the Google overlay, and returning true keeps the tap from also
 * reaching the street underneath.
 */
function clickProps(onPick: ((id: string) => void) | undefined) {
  if (!onPick) return { pickable: false };
  return {
    pickable: true,
    onClick: ({ object }: { object?: DroppingReportPin }) => {
      if (!object) return false;
      onPick(object.id);
      return true;
    },
  };
}

function pinProps(onPick: ((id: string) => void) | undefined) {
  return {
    getPosition: position,
    getRadius: REPORT_PIN_PX,
    radiusUnits: "pixels" as const,
    stroked: true,
    filled: true,
    getFillColor: (d: DroppingReportPin): Rgba => [
      REPORT_FILL[0],
      REPORT_FILL[1],
      REPORT_FILL[2],
      styleOf(d).fillAlpha,
    ],
    getLineColor: (d: DroppingReportPin) => styleOf(d).line,
    getLineWidth: (d: DroppingReportPin) => styleOf(d).width,
    lineWidthUnits: "pixels" as const,
    ...clickProps(onPick),
  };
}

function photoRingProps(onPick: ((id: string) => void) | undefined) {
  return {
    getPosition: position,
    getRadius: REPORT_PHOTO_RING_PX,
    radiusUnits: "pixels" as const,
    stroked: true,
    filled: false,
    getLineColor: REPORT_PHOTO_RING,
    getLineWidth: 1.5,
    lineWidthUnits: "pixels" as const,
    ...clickProps(onPick),
  };
}

const rippleProps = {
  getPosition: position,
  getRadius: REPORT_PIN_PX,
  radiusUnits: "pixels" as const,
  stroked: true,
  filled: false,
  getLineColor: [REPORT_FILL[0], REPORT_FILL[1], REPORT_FILL[2], RIPPLE_ALPHA] as Rgba,
  getLineWidth: 2,
  lineWidthUnits: "pixels" as const,
  pickable: false,
};

/**
 * Ripples first, then the landed pins with their photo rings, then the pins still dropping, then
 * the selection ring: a dropping pin is drawn over the ones already there, and the selection is
 * never hidden under a neighbour.
 */
export function reportPinLayers({
  reports,
  selectedReportId = null,
  onPick,
  reducedMotion = false,
}: ReportPinLayerOptions): unknown[] {
  if (reports.length === 0) return [];

  const landed: DroppingReportPin[] = [];
  const drops = new Map<number, DroppingReportPin[]>();
  for (const pin of reports) {
    const start = pin.dropStartMs;
    if (reducedMotion || start === undefined) {
      landed.push(pin);
      continue;
    }
    const group = drops.get(start);
    if (group) group.push(pin);
    else drops.set(start, [pin]);
  }

  const ripples: unknown[] = [];
  const dropping: unknown[] = [];
  for (const [start, pins] of drops) {
    // Named for the group's first report, so the layer keeps its identity from pending to started.
    const key = pins[0]?.id ?? String(start);
    ripples.push(
      new ScatterplotLayer<DroppingReportPin>({
        id: `${REPORT_LAYER_PREFIX}ripple-${key}`,
        data: pins,
        ...rippleProps,
        extensions: [DROP],
        dropStartMs: start,
        dropPart: "ripple",
      } as DropLayerProps),
    );
    const withPhoto = pins.filter(reportHasPhoto);
    if (withPhoto.length > 0) {
      dropping.push(
        new ScatterplotLayer<DroppingReportPin>({
          id: `${REPORT_LAYER_PREFIX}drop-photo-${key}`,
          data: withPhoto,
          ...photoRingProps(onPick),
          extensions: [DROP],
          dropStartMs: start,
          dropPart: "pin",
        } as DropLayerProps),
      );
    }
    dropping.push(
      new ScatterplotLayer<DroppingReportPin>({
        id: `${REPORT_LAYER_PREFIX}drop-${key}`,
        data: pins,
        ...pinProps(onPick),
        extensions: [DROP],
        dropStartMs: start,
        dropPart: "pin",
      } as DropLayerProps),
    );
  }

  const built: unknown[] = [...ripples];
  if (landed.length > 0) {
    const withPhoto = landed.filter(reportHasPhoto);
    if (withPhoto.length > 0) {
      built.push(
        new ScatterplotLayer<DroppingReportPin>({
          id: `${REPORT_LAYER_PREFIX}photo-rings`,
          data: withPhoto,
          ...photoRingProps(onPick),
        }),
      );
    }
    built.push(
      new ScatterplotLayer<DroppingReportPin>({
        id: `${REPORT_LAYER_PREFIX}pins`,
        data: landed,
        ...pinProps(onPick),
      }),
    );
  }
  built.push(...dropping);

  const selected = selectedReportId ? reports.find((r) => r.id === selectedReportId) : undefined;
  if (selected) {
    built.push(
      new ScatterplotLayer<DroppingReportPin>({
        id: `${REPORT_LAYER_PREFIX}selected`,
        data: [selected],
        getPosition: position,
        getRadius: REPORT_SELECTED_PX,
        radiusUnits: "pixels",
        stroked: true,
        filled: false,
        getLineColor: REPORT_SELECTED_RING,
        getLineWidth: 2,
        lineWidthUnits: "pixels",
        pickable: false,
      }),
    );
  }
  return built;
}

// ---- Tooltip --------------------------------------------------------------------------------

function isDepthHint(value: string): value is DepthHint {
  return Object.hasOwn(DEPTH_HINT_CM, value);
}

interface ReportDepth {
  depthHint?: string | null;
  depthCm?: number | null;
}

/**
 * The centimetres a report stands for: the API's own when it sent them, else the ones Pulse
 * assumes for the reporter's chip (SPEC.md 11.6, `DEPTH_HINT_CM`), else null. One function, so
 * the card's depth chip and the words beside it can never name two different depths.
 */
export function reportDepthCm(pin: ReportDepth): number | null {
  if (typeof pin.depthCm === "number" && Number.isFinite(pin.depthCm)) return pin.depthCm;
  const hint = pin.depthHint?.trim().toLowerCase() ?? "";
  return isDepthHint(hint) ? DEPTH_HINT_CM[hint].cm : null;
}

/** "Knee deep, about 45 cm", or null when the reporter gave no depth ({@link reportDepthCm}). */
export function reportDepthWords(pin: ReportDepth): string | null {
  const hint = pin.depthHint?.trim().toLowerCase() ?? "";
  const cm = reportDepthCm(pin);
  const about = cm === null ? null : `about ${Math.round(cm)} cm`;
  if (hint) {
    const word = `${hint.charAt(0).toUpperCase()}${hint.slice(1)} deep`;
    return about ? `${word}, ${about}` : word;
  }
  return about ? `Water ${about}` : null;
}

/** The hover text for a pin: where, how deep, what the desk has done, and what kind of report. */
export function reportTooltipText(pin: ReportPin): string {
  const lines = [pin.place?.trim() || "Citizen report"];
  lines.push(reportDepthWords(pin) ?? "No depth given");
  // `statusLabel` already says "(demo status)" when a seed set it (rule 7).
  lines.push(pin.statusLabel || REPORT_STATUS_LABEL[pin.status] || REPORT_STATUS_LABEL.received);
  if (reportHasPhoto(pin)) lines.push("Photo attached");
  if (pin.synthetic) lines.push("Demo report (synthetic)");
  return lines.join("\n");
}

// ---- The drop (motion M32) ------------------------------------------------------------------

interface DropTrack {
  /** Reports already accounted for: there on the first draw, dropping, or landed. */
  seen: readonly string[];
  /** When each running drop began; cleared when the drop is over. */
  stamps: Readonly<Record<string, number>>;
}

/**
 * The reports with their drop state for {@link reportPinLayers}.
 *
 * The first non-empty list is the baseline and nothing in it drops. Every report that appears
 * after it is stamped on the next animation frame with the time its drop begins, and the stamp is
 * cleared when the drop is over, so a new report costs three renders of the caller - arrived,
 * stamped, landed - however long the animation runs. Under reduced motion nothing is stamped.
 *
 * Returns `reports` itself whenever nothing is dropping, so a caller's layer memo keeps its
 * identity.
 */
export function useReportDrops(
  reports: readonly ReportPin[],
  reducedMotion: boolean,
): readonly DroppingReportPin[] {
  const [track, setTrack] = useState<DropTrack | null>(null);
  const idsKey = reports.map((r) => r.id).join("|");

  // Adjusting state while rendering, the React-documented way to follow a prop, as
  // `useTruthPins` does: the render that sees a new report already knows it is new, so it never
  // flashes in at full size before its drop begins.
  let current = track;
  if (idsKey && track === null) {
    current = { seen: idsKey.split("|"), stamps: {} };
    setTrack(current);
  } else if (idsKey && track && reducedMotion) {
    const fresh = idsKey.split("|").filter((id) => !track.seen.includes(id));
    if (fresh.length > 0) {
      current = { seen: [...track.seen, ...fresh], stamps: track.stamps };
      setTrack(current);
    }
  }

  const seenIds = current?.seen;
  const seen = useMemo(() => new Set(seenIds ?? []), [seenIds]);
  const pendingKey =
    current && !reducedMotion && idsKey
      ? idsKey
          .split("|")
          .filter((id) => !seen.has(id))
          .join("|")
      : "";

  // Stamp new reports on the next frame: the frame their drop begins. One frame, not a loop.
  useEffect(() => {
    if (!pendingKey) return;
    const ids = pendingKey.split("|");
    const frame = requestAnimationFrame((at) => {
      setTrack((t) => {
        if (!t) return t;
        const nextSeen = [...t.seen];
        const stamps = { ...t.stamps };
        for (const id of ids) {
          if (!nextSeen.includes(id)) nextSeen.push(id);
          stamps[id] = at;
        }
        return { seen: nextSeen, stamps };
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [pendingKey]);

  // Clear each stamp when its drop is over, so the pin joins the landed layer.
  const stamps = current?.stamps;
  useEffect(() => {
    const starts = Object.values(stamps ?? {});
    if (starts.length === 0) return;
    const due = Math.min(...starts) + DROP_MS;
    const timer = setTimeout(
      () => {
        setTrack((t) => {
          if (!t) return t;
          const kept = Object.entries(t.stamps).filter(([, at]) => at + DROP_MS > due);
          if (kept.length === Object.keys(t.stamps).length) return t;
          return { ...t, stamps: Object.fromEntries(kept) };
        });
      },
      Math.max(0, due - performance.now()),
    );
    return () => clearTimeout(timer);
  }, [stamps]);

  const tracking = current !== null;
  return useMemo((): readonly DroppingReportPin[] => {
    if (!tracking || reducedMotion) return reports;
    const stampsNow = stamps ?? {};
    const anyDrop =
      Object.keys(stampsNow).length > 0 || reports.some((report) => !seen.has(report.id));
    if (!anyDrop) return reports;
    return reports.map((report) => {
      const stamp = stampsNow[report.id];
      if (stamp !== undefined) return { ...report, dropStartMs: stamp };
      if (!seen.has(report.id)) return { ...report, dropStartMs: Number.POSITIVE_INFINITY };
      return report;
    });
  }, [reports, tracking, reducedMotion, seen, stamps]);
}

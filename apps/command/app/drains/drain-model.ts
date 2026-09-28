/**
 * What `/drains` reads out of a run's drain-health product and its observations, as plain data.
 *
 * Every number the screen leads with comes from here, and every one comes from the run (rule 6):
 * the product's own `summary` when the bake wrote one, else what can honestly be counted from the
 * pipes the run wrote, with the gap said out loud rather than papered over.
 */

import type { LearnedDrain } from "@/components/map/layers/drains";
import type {
  AssimilatedObservation,
  DrainEdge,
  DrainHealth,
  ObservationSet,
} from "@/lib/api/drains";
import { STREET_NOT_RECORDED, streetLabelParts } from "@/lib/street-label";

// ---------------------------------------------------------------------------------------------
// What to call a pipe
// ---------------------------------------------------------------------------------------------

/**
 * How far the nearest street may be from a pipe's middle and still say where the pipe is, in
 * metres: the radius the API itself uses for "off <street>" (`street_names`).
 */
export const PIPE_PLACE_RADIUS_M = 200;

/** Where a point is, as the nearest street's display name, or null when none is close. */
export type PlaceAt = (lonLat: readonly [number, number]) => string | null;

/** A street the finder reads: the segments layer's path and its name or display name. */
export interface NamedStreet {
  path: readonly (readonly [number, number])[];
  name?: string;
  displayName?: string;
}

function usableLabel(text: string | null | undefined): string | null {
  const trimmed = (text ?? "").trim();
  if (!trimmed || trimmed === STREET_NOT_RECORDED) return null;
  if (/^unnamed\b/i.test(trimmed) || ["none", "nan", "null"].includes(trimmed.toLowerCase())) {
    return null;
  }
  return trimmed;
}

/**
 * What a pipe is called on `/drains`. Its own name when the drain product gave it one (its street,
 * or "off <street>" within 200 m). Otherwise it is named by its id and placed by the nearest
 * street the segments layer names: "Pipe MUM-E037896 off Eastern Freeway", "Pipe MUM-E037896 near
 * Wadala Depot", "Pipe MUM-E037896 near Dr Ambedkar Road". A street labelled only by its city
 * ("Residential street in Mumbai") places nothing, so that pipe is "Pipe MUM-E037896". Never
 * "Unnamed pipe": the id is a true name for an inferred pipe.
 */
export function pipeTitle(
  id: string,
  name: string | null | undefined,
  nearby?: string | null,
): string {
  const own = usableLabel(name);
  if (own) return own;
  const pipe = `Pipe ${id}`;
  const label = usableLabel(nearby);
  if (!label) return pipe;
  const parts = streetLabelParts(label);
  switch (parts.kind) {
    case "off":
      return parts.anchor ? `${pipe} off ${parts.anchor}` : pipe;
    case "near":
      return parts.anchor ? `${pipe} near ${parts.anchor}` : pipe;
    case "in":
      return pipe;
    default:
      return `${pipe} near ${label}`;
  }
}

/** Metres per degree of latitude; longitude is scaled by the streets' mean latitude. */
const M_PER_DEG = 111_320;

/**
 * A lookup from a point to the nearest named street within `radiusM`, over the segments layer the
 * screen already draws. The index - every street piece in a grid of `radiusM` cells - is built on
 * the first question, not when the layer loads, so a run whose pipes all carry names never pays
 * for it. Ties go to the street listed first, so the answer never depends on the grid. Pure.
 */
export function nearbyStreetFinder(
  streets: readonly NamedStreet[],
  radiusM = PIPE_PLACE_RADIUS_M,
): PlaceAt {
  type Index = {
    cells: Map<number, number[]>;
    /** Piece i runs from (ax, ay) to (bx, by) in local metres; its street's label is label[i]. */
    ax: Float64Array;
    ay: Float64Array;
    bx: Float64Array;
    by: Float64Array;
    label: string[];
    kx: number;
  };
  let index: Index | null = null;
  const key = (cx: number, cy: number) => cx * 1_000_003 + cy;

  function build(): Index {
    const named: { path: NamedStreet["path"]; text: string }[] = [];
    let latSum = 0;
    for (const s of streets) {
      const text = usableLabel(s.displayName) ?? usableLabel(s.name);
      if (text === null || s.path.length === 0) continue;
      named.push({ path: s.path, text });
      latSum += s.path[0]![1];
    }
    const lat0 = named.length > 0 ? latSum / named.length : 0;
    const kx = M_PER_DEG * Math.cos((lat0 * Math.PI) / 180);
    const total = named.reduce((sum, s) => sum + Math.max(1, s.path.length - 1), 0);
    const ax = new Float64Array(total);
    const ay = new Float64Array(total);
    const bx = new Float64Array(total);
    const by = new Float64Array(total);
    const label = new Array<string>(total);
    const cells = new Map<number, number[]>();
    let i = 0;
    for (const { path, text } of named) {
      const last = path.length - 1;
      for (let j = 0; j < Math.max(1, last); j += 1) {
        const a = path[j]!;
        const b = path[Math.min(j + 1, last)]!;
        ax[i] = a[0] * kx;
        ay[i] = a[1] * M_PER_DEG;
        bx[i] = b[0] * kx;
        by[i] = b[1] * M_PER_DEG;
        label[i] = text;
        const x0 = Math.floor(Math.min(ax[i]!, bx[i]!) / radiusM);
        const x1 = Math.floor(Math.max(ax[i]!, bx[i]!) / radiusM);
        const y0 = Math.floor(Math.min(ay[i]!, by[i]!) / radiusM);
        const y1 = Math.floor(Math.max(ay[i]!, by[i]!) / radiusM);
        for (let cx = x0; cx <= x1; cx += 1) {
          for (let cy = y0; cy <= y1; cy += 1) {
            const k = key(cx, cy);
            const held = cells.get(k);
            if (held) held.push(i);
            else cells.set(k, [i]);
          }
        }
        i += 1;
      }
    }
    return { cells, ax, ay, bx, by, label, kx };
  }

  return (lonLat) => {
    if (streets.length === 0) return null;
    index ??= build();
    const { cells, ax, ay, bx, by, label, kx } = index;
    const px = lonLat[0] * kx;
    const py = lonLat[1] * M_PER_DEG;
    const cx = Math.floor(px / radiusM);
    const cy = Math.floor(py / radiusM);
    let best = radiusM * radiusM;
    let bestPiece = -1;
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dy = -1; dy <= 1; dy += 1) {
        for (const p of cells.get(key(cx + dx, cy + dy)) ?? []) {
          const vx = bx[p]! - ax[p]!;
          const vy = by[p]! - ay[p]!;
          const len2 = vx * vx + vy * vy;
          const t =
            len2 > 0
              ? Math.min(1, Math.max(0, ((px - ax[p]!) * vx + (py - ay[p]!) * vy) / len2))
              : 0;
          const ex = ax[p]! + t * vx - px;
          const ey = ay[p]! + t * vy - py;
          const d2 = ex * ex + ey * ey;
          if (d2 < best || (d2 === best && (bestPiece < 0 || p < bestPiece))) {
            best = d2;
            bestPiece = p;
          }
        }
      }
    }
    return bestPiece < 0 ? null : label[bestPiece]!;
  };
}

/** What the four tiles above the map say. */
export interface DrainStats {
  nPipes: number;
  nMoved: number;
  /** Null when neither the summary nor the written pipes can say. */
  nUp: number | null;
  nDown: number | null;
  /** True when the counts come from the written pipes and some moved pipes were not written. */
  partial: boolean;
  nObs: number;
  nTraffic: number;
  nReports: number;
  nSynthetic: number;
  nReal: number;
  /** Capacity lost citywide, weighted by full-flow capacity; null on a run baked without it. */
  capacity: {
    postPct: number;
    priorPct: number;
    /** Post minus prior, in percentage points. */
    learnedPoints: number;
    learnedM3s: number | null;
    fullM3s: number | null;
  } | null;
  biggest: {
    edge: string;
    name: string | null;
    locality: string | null;
    /** The nearest named street, looked up only when the pipe has no name of its own. */
    place: string | null;
    prior: number;
    post: number;
  } | null;
  /** Said under the strip when the summary was rebuilt or is missing. */
  note: string | null;
}

/** The API rebuilds a summary for a run baked before the bake wrote one; say which it is. */
const REBUILT_NOTE =
  "This run was baked before the product carried its own summary, so these figures are rebuilt from the pipes it wrote.";
const MISSING_NOTE =
  "This run carries no summary: counts are of the pipes it wrote, and capacity lost needs the run baked again.";

function observationCounts(observed: ObservationSet | null) {
  const list = observed?.observations ?? [];
  return {
    nObs: list.length,
    nTraffic: list.filter((o) => o.kind === "traffic").length,
    nReports: list.filter((o) => o.kind === "report").length,
    nSynthetic: list.filter((o) => o.synthetic).length,
    nReal: list.filter((o) => !o.synthetic).length,
  };
}

/** The nearest named street to an edge's middle, or null; `placeAt` absent means no lookup. */
function edgePlace(
  health: DrainHealth,
  edgeId: string,
  name: string | null,
  placeAt: PlaceAt | undefined,
): string | null {
  if (!placeAt || usableLabel(name)) return null;
  const path = health.edges.find((e) => e.id === edgeId)?.path ?? [];
  const at = midpoint(path);
  return at ? placeAt(at) : null;
}

export function deriveDrainStats(
  health: DrainHealth,
  observed: ObservationSet | null,
  placeAt?: PlaceAt,
): DrainStats {
  const s = health.summary;
  const counted = observationCounts(observed);
  if (s) {
    const rise = s.largestRise;
    const fall = s.largestFall;
    const pick =
      rise && fall
        ? Math.abs(rise.post - rise.prior) >= Math.abs(fall.post - fall.prior)
          ? rise
          : fall
        : (rise ?? fall);
    const capacity =
      s.capacityLostPostPct !== null && s.capacityLostPriorPct !== null
        ? {
            postPct: s.capacityLostPostPct,
            priorPct: s.capacityLostPriorPct,
            learnedPoints: s.capacityLostPostPct - s.capacityLostPriorPct,
            learnedM3s: s.capacityLearnedM3s,
            fullM3s: s.capacityFullM3s,
          }
        : null;
    // The observation list is the one the timeline draws; the summary's counts are the bake's.
    // They are the same set, and the list wins when it has loaded, so the tile and the strip
    // beneath it can never disagree.
    const obs =
      observed !== null
        ? counted
        : {
            nObs: s.nObs,
            nTraffic: s.nObsByKind.traffic ?? 0,
            nReports: s.nObsByKind.report ?? 0,
            nSynthetic: s.nObsSynthetic,
            nReal: s.nObsReal,
          };
    // A rebuilt summary counts the moved pipes the run wrote, and says how many it did not; the
    // tile leads with every pipe Pulse moved, and the split beneath it says it covers the written.
    const rebuilt = s.source === "written_features";
    const note = rebuilt ? (s.note ?? REBUILT_NOTE) : null;
    return {
      // A rebuilt summary may have counted only the pipes the run wrote; the network's size is
      // the product's `n_edges`, so the tile never reads "of 6,000 pipes" for 49,770.
      nPipes: rebuilt ? health.nEdges || s.nPipes : s.nPipes || health.nEdges,
      nMoved: s.nMoved + (s.nMovedUnwritten ?? 0),
      nUp: s.nMovedUp,
      nDown: s.nMovedDown,
      partial: (s.nMovedUnwritten ?? 0) > 0,
      ...obs,
      capacity,
      biggest: pick
        ? {
            edge: pick.edge,
            name: pick.name,
            locality: pick.locality,
            place: edgePlace(health, pick.edge, pick.name, placeAt),
            prior: pick.prior,
            post: pick.post,
          }
        : null,
      note,
    };
  }

  // No summary: count what the run wrote, and say it is a count of what it wrote.
  const moved = health.edges.filter((e) => e.moved);
  const up = moved.filter((e) => e.betaDelta > 0).length;
  const down = moved.length - up;
  const top = [...moved].sort((a, b) => Math.abs(b.betaDelta) - Math.abs(a.betaDelta))[0];
  return {
    nPipes: health.nEdges,
    nMoved: health.nUpdated,
    nUp: up,
    nDown: down,
    partial: moved.length < health.nUpdated,
    ...counted,
    capacity: null,
    biggest: top
      ? {
          edge: top.id,
          name: top.displayName,
          locality: top.locality,
          place: edgePlace(health, top.id, top.displayName, placeAt),
          prior: top.betaPrior,
          post: top.betaMean,
        }
      : null,
    note: MISSING_NOTE,
  };
}

/** The pipes Pulse moved this cycle, as the map's learned overlay draws them. */
export function learnedDrains(health: DrainHealth | null): LearnedDrain[] {
  if (!health) return [];
  return health.edges
    .filter((e) => e.moved && e.path.length >= 2)
    .map((e) => ({
      id: e.id,
      path: e.path,
      beta: e.betaMean,
      diameter: e.diameterM,
      prior: e.betaPrior,
      post: e.betaMean,
      sd: e.betaSd,
      direction: e.betaDelta < 0 ? "down" : "up",
      name: e.displayName,
      locality: e.locality,
      capacityLostPct: e.capacityReductionPct,
      lastUpdate: e.lastUpdate,
    }));
}

/** One of the handful of pipes the screen names: the largest learned changes. */
export interface PipeCardData {
  id: string;
  name: string | null;
  locality: string | null;
  /** The nearest named street, looked up only when the pipe has no name of its own. */
  place: string | null;
  prior: number;
  post: number;
  sd: number;
  direction: "up" | "down";
  capacityLostPct: number;
  diameterM: number;
  observations: number;
  /** An observation that landed on this pipe itself, if any did. */
  movedBy: AssimilatedObservation | null;
  /** Where to fly: the middle of the pipe. */
  lon: number;
  lat: number;
}

export function midpoint(path: readonly [number, number][]): [number, number] | null {
  if (path.length === 0) return null;
  if (path.length === 1) return path[0];
  const a = path[Math.floor((path.length - 1) / 2)];
  const b = path[Math.ceil((path.length - 1) / 2)];
  if (path.length % 2 === 1) return a;
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
}

/**
 * The pipes to name, ranked by **learned change**, not by blockage: the top of the blockage
 * ranking is the 0.35 market prior, which Pulse did not learn. Ties break on the id so the order
 * never depends on the file's.
 */
export function rankPipeCards(
  health: DrainHealth | null,
  observed: ObservationSet | null,
  n = 6,
  placeAt?: PlaceAt,
): PipeCardData[] {
  if (!health) return [];
  const byEdge = new Map<string, AssimilatedObservation>();
  for (const o of observed?.observations ?? []) {
    if (!o.edgeId) continue;
    const held = byEdge.get(o.edgeId);
    // The one that moved it most, where the bake recorded the pair.
    const size = (x: AssimilatedObservation) =>
      x.betaBefore !== null && x.betaAfter !== null ? Math.abs(x.betaAfter - x.betaBefore) : 0;
    if (!held || size(o) > size(held)) byEdge.set(o.edgeId, o);
  }
  return health.edges
    .filter((e: DrainEdge) => e.moved && e.path.length >= 1)
    .sort((a, b) => Math.abs(b.betaDelta) - Math.abs(a.betaDelta) || a.id.localeCompare(b.id))
    .slice(0, n)
    .map((e) => {
      const [lon, lat] = midpoint(e.path) ?? [0, 0];
      return {
        id: e.id,
        name: e.displayName,
        locality: e.locality,
        place:
          placeAt && !usableLabel(e.displayName) && e.path.length > 0 ? placeAt([lon, lat]) : null,
        prior: e.betaPrior,
        post: e.betaMean,
        sd: e.betaSd,
        direction: e.betaDelta < 0 ? "down" : "up",
        capacityLostPct: e.capacityReductionPct,
        diameterM: e.diameterM,
        observations: e.observations,
        movedBy: byEdge.get(e.id) ?? null,
        lon,
        lat,
      };
    });
}

/** "Traffic 3 km/h against 27 km/h usual" or "Citizen report, knee deep (45 cm)". */
export function describeObservation(o: AssimilatedObservation): string {
  if (o.kind === "traffic") {
    const speed = o.speedKmh !== null ? `${Math.round(o.speedKmh)} km/h` : null;
    const usual = o.baselineKmh !== null ? `${Math.round(o.baselineKmh)} km/h usual` : null;
    if (speed && usual) return `Traffic at ${speed} against ${usual}`;
    return "Traffic anomaly";
  }
  const chip = o.chip ? `${o.chip} deep` : null;
  return chip
    ? `Citizen report, ${chip} (${Math.round(o.depthCm)} cm)`
    : `Citizen report, ${Math.round(o.depthCm)} cm`;
}

/** Minutes in a strip: from two hours before the cycle, or the earliest observation. */
export const STRIP_SPAN_MIN = 120;

/** Which way an observation moved its pipe's blockage, or null when the run recorded no change. */
export function observationChange(o: AssimilatedObservation): "up" | "down" | null {
  if (o.betaBefore === null || o.betaAfter === null || o.betaAfter === o.betaBefore) return null;
  return o.betaAfter > o.betaBefore ? "up" : "down";
}

/**
 * One mark on the strip: every observation of one kind stamped with the same minute.
 *
 * A cycle's traffic anomalies all carry the cycle's own time (ten of them at 08:40 on 2 July), so
 * one dot per observation would either stack ten on one pixel or, pushed apart, draw them across
 * the half hour before the cycle - a time none of them has. They are one mark with a count, at
 * the time they share.
 */
export interface StripMark {
  /** Lane and minute: stable across renders. */
  key: string;
  lane: "traffic" | "report";
  /** 0 to 1 along the strip, at the members' own time. */
  x: number;
  /** The members' shared time, ISO 8601. */
  ts: string;
  /** Oldest id first, so the order never depends on the file's. */
  members: AssimilatedObservation[];
  up: number;
  down: number;
  /** A single member's change; null for a group, whose members say theirs. */
  change: "up" | "down" | null;
}

/** Place each observation on the strip, grouped by kind and minute, every mark at its own time. */
export function stripLayout(
  observations: readonly AssimilatedObservation[],
  cycleTs: string,
): { start: number; end: number; marks: StripMark[] } {
  const times = observations.map((o) => Date.parse(o.ts)).filter(Number.isFinite);
  // A run that did not stamp its cycle ends the strip at its latest observation.
  const stamped = Date.parse(cycleTs);
  const end = Number.isFinite(stamped) ? stamped : times.length > 0 ? Math.max(...times) : 0;
  const earliest = times.length > 0 ? Math.min(...times) : end;
  const start = Math.min(end - STRIP_SPAN_MIN * 60_000, earliest);
  const span = Math.max(1, end - start);

  const groups = new Map<
    string,
    { lane: "traffic" | "report"; t: number; list: AssimilatedObservation[] }
  >();
  for (const o of observations) {
    const parsed = Date.parse(o.ts);
    // An observation with no readable time sits at the cycle, which is when it was assimilated.
    const t = Number.isFinite(parsed) ? Math.floor(parsed / 60_000) * 60_000 : end;
    const key = `${o.kind}|${t}`;
    const held = groups.get(key);
    if (held) held.list.push(o);
    else groups.set(key, { lane: o.kind, t, list: [o] });
  }

  const marks: StripMark[] = [...groups.entries()].map(([key, g]) => {
    const members = [...g.list].sort((a, b) => a.id.localeCompare(b.id));
    const changes = members.map(observationChange);
    return {
      key,
      lane: g.lane,
      x: Math.min(1, Math.max(0, (g.t - start) / span)),
      ts: members[0].ts,
      members,
      up: changes.filter((c) => c === "up").length,
      down: changes.filter((c) => c === "down").length,
      change: members.length === 1 ? changes[0] : null,
    };
  });
  marks.sort((a, b) => a.x - b.x || a.lane.localeCompare(b.lane));
  return { start, end, marks };
}

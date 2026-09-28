/**
 * The run's ranked hotspots, for the console rail (SPEC.md 7.2, 11.8; `GET /v1/nowcast/hotspots`).
 *
 * Loaded once per run alongside the depth frames, not per scrub step: every hotspot carries its
 * whole 3-hour depth series, so moving the time bar reads an array index rather than the network
 * (SPEC.md 7.2 AC, "no network during scrub").
 */

import { apiUrl } from "@/lib/api/client";

/** Asset kinds the exposure row draws an icon for, matching `FACILITY_KINDS` in the products service. */
export type FacilityKind = "hospital" | "fire_station" | "station" | "shelter";

export interface HotspotExposure {
  /** The city pipeline's own exposure weight for the busiest road through the junction (P1.6). */
  weight: number;
  facilities: FacilityKind[];
  nearestHospital?: string;
  nearestHospitalM?: number;
  nearestStation?: string;
  nearestStationM?: number;
}

/**
 * One pipe named as responsible for a junction's peak.
 *
 * Written by `varuna_flash.whatif.attribute_pipes`: `drain1d` re-run on the junction's own
 * catchment with the street depth frozen at what the Twin produced, once per candidate pipe
 * within five upstream hops, ranked by how much more water the drain then takes off the
 * junction. It replaced the Flash-lite finite difference of ADR-0042, which was element-wise
 * per segment and so could only ever score the target's own street.
 *
 * A run can still carry no rows: at a junction whose inlets rather than whose pipes are the
 * constraint, nothing clears the floor, and `attributionLabel` carries the measured reason.
 */
export interface HotspotAttribution {
  rank: number;
  /**
   * The inferred pipe, `MUM-E…`; what the drain X-ray and the desilting CSV are keyed on.
   *
   * Optional because it is what P7.7 added: a row from a run baked before the hydraulic
   * operator existed is keyed on a road segment and has no pipe. The drawer falls back to the
   * segment rather than printing an empty cell.
   */
  pipeId?: string;
  /** The road segment the pipe runs under, when the drain graph records one. */
  segmentId: string | null;
  /** Blockage on that pipe in the run this was measured on. */
  beta: number;
  /**
   * Centimetres of the junction's peak this pipe alone explains.
   *
   * Positive means cleaning it makes the junction shallower; **negative means deeper**, which
   * is a real answer at a junction that is already surcharging - a clear pipe above it delivers
   * more water than the cleaning takes away. The ranking is by magnitude.
   */
  depthExplainedCm: number;
  /** The peak it is explaining part of, so a row can read "1.6 of 9.2 cm". Added with P7.7. */
  depthBeforeCm?: number;
}

/**
 * Cleaning the whole ranking at once — SPEC.md 7.2's "cleaning these 14 pipes: 55 → 20 cm".
 *
 * A separate run rather than the sum of the rows, because pipes in series share the water they
 * carry. It can come back *negative*: cleaning the pipes above a junction can deliver more water
 * to it than the cleaning takes away, and the drawer prints that rather than clamping it.
 */
export interface HotspotAttributionCombined {
  nCleaned: number;
  depthBeforeCm: number;
  depthAfterCm: number;
  depthExplainedCm: number;
}

export interface Hotspot {
  rank: number;
  id: string;
  name: string;
  slug: string | null;
  lon: number;
  lat: number;
  ward: string | null;
  /** True where the register recorded the spot as a terrain sink (a subway or underpass). */
  isSink: boolean;
  /** The report this chronic spot was verified against (SPEC.md rule 7). */
  sourceUrl: string | null;
  sourced: boolean;
  peakDepthCm: number;
  peakTs: string | null;
  timeToPeakMin: number;
  /** Depth in cm at every step of the run, so the row can follow the scrub without a fetch. */
  depthCm: number[];
  /** 0 or 1 on a deterministic run; continuous once Flash brings the ensemble (Phase 7). */
  pImpassableAtPeak: number;
  impassableFromTs: string | null;
  minutesImpassable: number;
  expectedImpact: number;
  exposure: HotspotExposure;
  /** The road segments the junction is made of; what a what-if deep link carries. */
  segmentIds: string[];
  /** Responsible pipes, deepest first. Empty exactly when `attributionLabel` is set. */
  attribution: HotspotAttribution[];
  /** Why the ranking is empty, when it is: measured refusal, or not attempted on this run. */
  attributionLabel: string | null;
  /**
   * Cleaning every listed pipe together; null or absent when there is no ranking.
   *
   * These three fields are optional because they arrived with P7.7: a run baked before it, and
   * a fixture written against the older shape, simply does not carry them.
   */
  attributionCombined?: HotspotAttributionCombined | null;
  /** How the ranking was measured, printed beside it so the operator is never guessing. */
  attributionMethod?: string | null;
  /** How many pipes were evaluated, so a short list is not read as a short candidate set. */
  attributionCandidates?: number | null;
  /**
   * Which state an empty list is in: `refused` means pipes were re-run and none cleared the
   * floor, `not_attempted` means this junction was under the wet floor or past the budget, so an
   * empty list is never read as "no pipe is responsible" when nobody looked.
   */
  attributionStatus?: "ranked" | "refused" | "not_attempted" | "unavailable" | "off" | null;
  /**
   * The ensemble's p10 and p90 at this junction, per step: its own Twin level plus the member
   * spread of its registered streets (ADR-0076). Absent on a run baked before the band existed,
   * or when none of its streets is in the forecast; the drawer then draws the series flat.
   */
  depthP10Cm?: number[] | null;
  depthP90Cm?: number[] | null;
  /** How many of the junction's streets the band was averaged over; 0 means no band. */
  bandSegments?: number | null;
}

export interface HotspotSet {
  runId: string;
  /** Which score ordered the list; printed in the rail so the ranking is never implied. */
  ranking: string;
  impassableThresholdCm: number;
  hotspots: Hotspot[];
}

interface RawExposure {
  weight?: number;
  facilities?: string[];
  nearest_hospital?: string;
  nearest_hospital_m?: number;
  nearest_station?: string;
  nearest_station_m?: number;
}

interface RawHotspot {
  rank?: number;
  hotspot_id?: string;
  name?: string;
  slug?: string | null;
  lon?: number;
  lat?: number;
  ward?: string | null;
  is_sink?: boolean;
  source_url?: string | null;
  sourced?: boolean;
  peak_depth_cm?: number;
  peak_ts?: string | null;
  time_to_peak_min?: number;
  depth_cm?: number[];
  p_impassable_at_peak?: number;
  impassable_from_ts?: string | null;
  minutes_impassable?: number;
  expected_impact?: number;
  exposure?: RawExposure;
  segment_ids?: string[];
  attribution?: RawAttribution[];
  attribution_label?: string | null;
  attribution_combined?: RawCombined;
  attribution_method?: string | null;
  attribution_candidates?: number | null;
  attribution_status?: "ranked" | "refused" | "not_attempted" | "unavailable" | "off" | null;
  depth_p10_cm?: number[] | null;
  depth_p90_cm?: number[] | null;
  band_segments?: number | null;
}

interface RawAttribution {
  rank?: number;
  pipe_id?: string;
  segment_id?: string | null;
  beta?: number;
  depth_explained_cm?: number;
  depth_before_cm?: number;
}

interface RawCombined {
  n_cleaned?: number;
  depth_before_cm?: number;
  depth_after_cm?: number;
  depth_explained_cm?: number;
}

const FACILITY_KINDS: readonly string[] = ["hospital", "fire_station", "station", "shelter"];

/** Fetch the ranked hotspots for a run. Returns null when the run predates hotspot ranking. */
export async function loadHotspots(
  runId: string | undefined,
  signal?: AbortSignal,
): Promise<HotspotSet | null> {
  const query = new URLSearchParams({ limit: "100" });
  if (runId) query.set("run_id", runId);

  const response = await fetch(apiUrl(`/v1/nowcast/hotspots?${query}`), { signal });
  if (!response.ok) {
    // A run baked before P5.4 has no `hotspots.json`. That is an empty rail, not a broken
    // console: the map, the scrub and the run stamp all still work.
    if (response.status === 404) return null;
    throw new Error(`Hotspots failed: HTTP ${response.status}`);
  }

  const body = (await response.json()) as {
    run_id?: string;
    ranking?: string;
    impassable_threshold_cm?: number;
    hotspots?: RawHotspot[];
  };

  return {
    runId: body.run_id ?? "",
    ranking: body.ranking ?? "peak depth",
    impassableThresholdCm: body.impassable_threshold_cm ?? 30,
    hotspots: (body.hotspots ?? []).map((h, i) => ({
      rank: h.rank ?? i + 1,
      id: h.hotspot_id ?? h.slug ?? `hotspot-${i}`,
      name: h.name ?? "Chronic spot",
      slug: h.slug ?? null,
      lon: h.lon ?? 0,
      lat: h.lat ?? 0,
      ward: h.ward ?? null,
      isSink: Boolean(h.is_sink),
      sourceUrl: h.source_url ?? null,
      sourced: Boolean(h.sourced),
      peakDepthCm: h.peak_depth_cm ?? 0,
      peakTs: h.peak_ts ?? null,
      timeToPeakMin: h.time_to_peak_min ?? 0,
      depthCm: h.depth_cm ?? [],
      pImpassableAtPeak: h.p_impassable_at_peak ?? 0,
      impassableFromTs: h.impassable_from_ts ?? null,
      minutesImpassable: h.minutes_impassable ?? 0,
      expectedImpact: h.expected_impact ?? 0,
      exposure: {
        weight: h.exposure?.weight ?? 0,
        facilities: (h.exposure?.facilities ?? []).filter((k): k is FacilityKind =>
          FACILITY_KINDS.includes(k),
        ),
        nearestHospital: h.exposure?.nearest_hospital,
        nearestHospitalM: h.exposure?.nearest_hospital_m,
        nearestStation: h.exposure?.nearest_station,
        nearestStationM: h.exposure?.nearest_station_m,
      },
      segmentIds: h.segment_ids ?? [],
      attribution: (h.attribution ?? []).map((row, j) => ({
        rank: row.rank ?? j + 1,
        pipeId: row.pipe_id ?? "",
        segmentId: row.segment_id ?? null,
        beta: row.beta ?? 0,
        depthExplainedCm: row.depth_explained_cm ?? 0,
        depthBeforeCm: row.depth_before_cm ?? h.peak_depth_cm ?? 0,
      })),
      attributionLabel: h.attribution_label ?? null,
      attributionCombined: h.attribution_combined
        ? {
            nCleaned: h.attribution_combined.n_cleaned ?? 0,
            depthBeforeCm: h.attribution_combined.depth_before_cm ?? 0,
            depthAfterCm: h.attribution_combined.depth_after_cm ?? 0,
            depthExplainedCm: h.attribution_combined.depth_explained_cm ?? 0,
          }
        : null,
      attributionMethod: h.attribution_method ?? null,
      attributionCandidates: h.attribution_candidates ?? null,
      attributionStatus: h.attribution_status ?? null,
      depthP10Cm: h.depth_p10_cm ?? null,
      depthP90Cm: h.depth_p90_cm ?? null,
      bandSegments: h.band_segments ?? null,
    })),
  };
}

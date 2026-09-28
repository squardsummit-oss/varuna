/**
 * A body `services/api/varuna_api/routers/outlook.py` produced on the shipped emulator (12 mm/h
 * then 3 mm/h from 14:10 IST, trimmed to two streets). Shared by the client's contract tests and
 * the card's, so a rename on the API side fails both rather than neither.
 */
const STEPS = 36;

function series(peak: number): number[] {
  return Array.from({ length: STEPS }, (_, i) => Math.round(peak * Math.min(1, i / 23) * 10) / 10);
}

export function outlookBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    city: "mumbai",
    mode: "outlook",
    run_id: null,
    issued_at: "2026-09-26T14:12:03+05:30",
    valid_from: "2026-09-26T14:10:00+05:30",
    valid_to: "2026-09-26T17:10:00+05:30",
    expired: false,
    steps_min: 5,
    n_steps: STEPS,
    valid_ts: Array.from({ length: STEPS }, (_, i) => `step-${i}`),
    rain_mm_h: Array.from({ length: STEPS }, (_, i) => (i < 10 ? 12 : i < 22 ? 3 : 0)),
    rain_total_mm: 13.0,
    members: 50,
    source: {
      name: "Open-Meteo",
      url: "https://open-meteo.com/",
      licence: "CC BY 4.0",
      licence_url: "https://creativecommons.org/licenses/by/4.0/",
      attribution: "Weather data by Open-Meteo.com (CC BY 4.0)",
      fetched_at: "2026-09-26T14:12:00+05:30",
      age_s: 240.0,
      stale: false,
      grid_cell_km: 2.45,
      grid_point: { lon: 72.85, lat: 19.08 },
      series: "hourly",
      series_note:
        "Rain is Open-Meteo's hourly forecast, each value held for the hour before its timestamp.",
    },
    segments: [
      {
        segment_id: "S1267122772-000",
        name: "Mathuradas Vasanji Road (Andheri Kurla Road)",
        p50_cm: series(32.3),
        p90_cm: series(32.7),
        p_gt_15: series(1),
        p_gt_30: series(0.6),
        p_gt_45: series(0),
        peak_p50_cm: 32.3,
        peak_p90_cm: 32.7,
        peak_ts: "2026-09-26T16:05:00+05:30",
      },
      {
        segment_id: "S1509385069-002",
        name: null,
        p50_cm: series(23.1),
        p90_cm: series(32.5),
        p_gt_15: series(0.8),
        p_gt_30: series(0.2),
        p_gt_45: series(0),
        peak_p50_cm: 23.1,
        peak_p90_cm: 32.5,
        peak_ts: "2026-09-26T16:05:00+05:30",
      },
    ],
    n_segments_over_1cm: 6958,
    truncated: true,
    summary: {
      max_cm: 32.3,
      max_p90_cm: 32.7,
      n_ge_5: 393,
      n_ge_15: 10,
      n_ge_30: 1,
      worst: [
        {
          segment_id: "S1267122772-000",
          name: "Mathuradas Vasanji Road (Andheri Kurla Road)",
          peak_p50_cm: 32.3,
          peak_p90_cm: 32.7,
          peak_ts: "2026-09-26T16:05:00+05:30",
        },
      ],
      sentence:
        "393 streets expected above 5 cm in the next 3 h, 10 above 15 cm; deepest 32 cm on Mathuradas Vasanji Road (Andheri Kurla Road) around 16:05.",
    },
    baseline_removed: { n_ge_5: 1083, n_ge_15: 63, n_ge_30: 11, max_cm: 71.5 },
    blockage: {
      kind: "pulse_posterior",
      run_id: "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked",
      from_posterior: 2512,
      from_prior: 13241,
      flat: 5543,
    },
    skill: {
      rmse_cm: 5.704,
      csi_30cm: 0.0854,
      n_training_runs: 6,
      fitted_segments: 13702,
      n_segments: 21296,
    },
    no_rain_response: 7701,
    unmatched_streets: 1247,
    notes: ["Today, not the replay: rain for 14:10 to 17:10 IST on 26 Sep 2026."],
    method:
      "reduced-order emulator (Flash-lite) on Open-Meteo NWP rain, AOI-uniform; not a radar nowcast",
    compute_ms: 2299,
    cached: false,
    ...overrides,
  };
}

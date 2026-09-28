/**
 * Bodies `services/api/varuna_api/routers/whatif.py` produced for a full-city Twin what-if on the
 * synthetic 12 x 12 city of `services/api/tests/test_whatif_twin.py` (rain 1.3x, tide +1.0 m),
 * trimmed, on 2026-09-27: the job runs the Twin with nothing changed first (6 steps), then the
 * scenario (6 more), and compares the two. Shared by the client's contract test and the lab's
 * and drawer's state tests, so a rename on the API side fails in every place that reads it.
 */

export const TWIN_STARTED = {
  job_id: "3f473a24d3a1",
  run_id: "TST-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
  state: "running",
  stage: "queued",
  step: 0,
  n_steps: 12,
  baseline_steps: 6,
  elapsed_ms: 3,
  expected_ms: 8000,
  expected_from:
    "Run TST-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked: twin 3,400 ms twice (the scenario, and the same run with nothing changed to compare it with) + sky 1,200 ms, plus the city load on a server's first scenario.",
  scenario: {
    rain_scale: 1.3,
    tide_offset_m: 1.0,
    cleaned_segments: [],
    levers_left_out: [],
    notes: [],
  },
  started_at: "2026-09-27T20:40:30.948983+05:30",
  finished_at: null,
  error: null,
  cache: null,
  result: null,
} as const;

export const TWIN_DONE = {
  job_id: "3f473a24d3a1",
  run_id: "TST-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
  state: "done",
  stage: "done",
  step: 12,
  n_steps: 12,
  baseline_steps: 6,
  elapsed_ms: 9951,
  expected_ms: 8000,
  expected_from:
    "Run TST-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked: twin 3,400 ms twice (the scenario, and the same run with nothing changed to compare it with) + sky 1,200 ms, plus the city load on a server's first scenario.",
  scenario: {
    rain_scale: 1.3,
    tide_offset_m: 1.0,
    cleaned_segments: [],
    levers_left_out: [],
    notes: [],
  },
  started_at: "2026-09-27T20:40:30.948983+05:30",
  finished_at: "2026-09-27T20:40:40.903051+05:30",
  error: null,
  cache: {
    source: "computed",
    computed_at: "2026-09-27T20:40:40+05:30",
    computed_on: {
      system: "Windows",
      machine: "AMD64",
      cpus: 8,
    },
    label:
      "Computed now on this server. Computed 2026-09-27T20:40:40+05:30 on Windows AMD64 with 8 logical CPUs.",
  },
  result: {
    run_id: "TST-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
    method: "twin_full_aoi",
    blockage: "prior",
    scenario: {
      rain_scale: 1.3,
      tide_offset_m: 1.0,
      cleaned_segments: [],
      cleaned_no_pipe: [],
      cleaned_unmatched: [],
      cleaned_edges: 0,
    },
    segments: [
      {
        segment_id: "S11-000",
        before_cm: 24.7,
        after_cm: 139.4,
        delta_cm: 114.7,
        newly_wet: false,
        minutes_above_before: {
          "15": 5,
          "30": 0,
          "45": 0,
        },
        minutes_above_after: {
          "15": 30,
          "30": 30,
          "45": 30,
        },
      },
      {
        segment_id: "S10-000",
        before_cm: 21.0,
        after_cm: 133.5,
        delta_cm: 112.5,
        newly_wet: false,
        minutes_above_before: {
          "15": 5,
          "30": 0,
          "45": 0,
        },
        minutes_above_after: {
          "15": 30,
          "30": 30,
          "45": 30,
        },
      },
    ],
    n_segments: 12,
    n_segments_compared: 12,
    n_changed: 12,
    n_worse: 12,
    n_improved: 0,
    n_newly_wet: 0,
    newly_above: {
      "15": 9,
      "30": 12,
      "45": 12,
    },
    max_abs_delta_cm: 114.7,
    hotspots: [
      {
        hotspot_id: "TST-HS-1",
        name: "Test junction",
        lon: 72.84,
        lat: 19.01,
        before_cm: 6.4,
        after_cm: 79.2,
        delta_cm: 72.8,
        minutes_above_before: {
          "15": 0,
          "30": 0,
          "45": 0,
        },
        minutes_above_after: {
          "15": 15,
          "30": 15,
          "45": 15,
        },
        segments: 2,
        segments_missing: 1,
        run_peak_cm: 10.0,
      },
    ],
    hotspots_moved: 1,
    hotspots_not_in_index: [],
    baseline: {
      method: "twin_nothing_changed",
      source: "computed",
      computed_at: "2026-09-27T20:40:36+05:30",
      computed_on: {
        system: "Windows",
        machine: "AMD64",
        cpus: 8,
      },
      twin_ms: 5445,
      drift: {
        n_segments_compared: 12,
        n_changed: 0,
        n_worse: 0,
        n_improved: 0,
        max_abs_delta_cm: 0.0,
        hotspots_moved: 0,
        n_unsampled: 0,
        bake_code_matches: null,
        bake_city_matches: null,
      },
    },
    sea: {
      offset_m: 1.0,
      source: "synthetic test tide (illustrative)",
      sea_to_land_m3: 115218.9,
      tide_in_m3: 116026.6,
      tide_out_m3: 0.0,
      sea_stored_start_m3: 342.0,
      sea_stored_end_m3: 1149.7,
      outfall_m3: 42.9,
      sea_to_land_change_m3: 113542.3,
      volume_in_m3: 126164.3,
      volume_in_change_m3: 116199.8,
    },
    mass_balance: {
      error_fraction: 8.073871249388948e-16,
      budget: 0.001,
      within_budget: true,
      ledger: {
        volume_in_m3: 126164.269,
      },
      run_error_fraction: 9.127361318802944e-16,
    },
    rain_reproduces_run: true,
    rain_max_diff_mm_h: 0.0,
    timings: {
      twin_ms: 4433,
      baseline_ms: 5445,
    },
    twin_ms: 4433,
    ms: 9922,
    notes: [
      "VARUNA-Twin, full city, at the city's inferred prior blockage: the same terrain, drains, rain and tide the cycle's own Twin ran on, with the scenario applied to one of them.",
      "Blockage is the city's prior, not Pulse's posterior, because the run's depth forecast was computed at the prior; the emulator what-if and the physics check run at the posterior.",
      "Before is a Twin run with nothing changed, on this server's code and city; it reproduces the run's own forecast on every street, so the change is the scenario's.",
      "Across the 12 streets wet at 5 cm or more with nothing changed, the largest change is 114.7 cm.",
      "Tide series is synthetic test tide (illustrative), not a published tide table (rule 7).",
      "Tide offset +1.0 m on every step of the bundle's series: 0.12 Mm3 of sea crossed onto the land, net (+0.11 Mm3 against the tide as forecast). 12 streets newly above 30 cm, 1 hotspot moved by 0.5 cm or more.",
    ],
    fingerprint: "42fc1d2ffeae8db8-twin1.0-code59dae8a42091-cityda39a3ee5e6b",
    computed_at: "2026-09-27T20:40:40+05:30",
    computed_on: {
      system: "Windows",
      machine: "AMD64",
      cpus: 8,
    },
  },
} as const;

/**
 * The same answer to a question that also asked for the pump plan: the job names the lever it
 * does not run, in `levers_left_out` and its notes. The run itself is the same scenario.
 */
export const TWIN_DONE_WITH_PUMPS = {
  ...TWIN_DONE,
  scenario: {
    ...TWIN_DONE.scenario,
    levers_left_out: ["pump_plan"],
    notes: [
      "The pump plan is not in this Twin run: the Twin has no pump sink on the street, so the pumps are priced by the emulator only, as a lower bound.",
    ],
  },
} as const;

/** A job whose comparison run was cached: the scenario's own Twin, step 17 of 36, 20.4 s in. */
export const TWIN_RUNNING = {
  ...TWIN_STARTED,
  stage: "twin",
  step: 17,
  n_steps: 36,
  baseline_steps: 0,
  elapsed_ms: 20400,
  expected_ms: 41637,
} as const;

/** A job running the Twin with nothing changed first: step 9 of its 36, of 72 in all. */
export const TWIN_BASELINE_RUNNING = {
  ...TWIN_STARTED,
  stage: "baseline",
  step: 9,
  n_steps: 72,
  baseline_steps: 36,
  elapsed_ms: 12000,
  expected_ms: 84474,
} as const;

/** The same job stopped by Cancel, in the server's own words. */
export const TWIN_CANCELLED = {
  ...TWIN_RUNNING,
  state: "cancelled",
  finished_at: "2026-09-27T20:41:06.101000+05:30",
  error: {
    code: "cancelled",
    message: "Cancelled during the scenario's Twin at step 17 of 36. Nothing was stored.",
  },
} as const;

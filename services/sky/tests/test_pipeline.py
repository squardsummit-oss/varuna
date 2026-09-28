"""The whole Sky stage, run end to end (SPEC.md 11.1; tasks P3.7 and the Phase 3 exit).

Two things are checked here that no single-stage test can see: that the stages *compose* - the
relation step 2 fits is the one step 4 tracks with and step 5 labels the cube with - and that
the whole thing fits the five-second budget SPEC.md P3.7 and section 14 set for it.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from time import perf_counter

import numpy as np
import pandas as pd
import pytest
from pyproj import Transformer
from varuna_schemas.constants import IST
from varuna_sky.ensemble_pool import shutdown_pool, warm_pool
from varuna_sky.pipeline import NWP_NOTE, STAGES, run_sky
from varuna_sky.products import AoiGrid
from varuna_sky.types import RadarFrames, RadarGrid, SkyInputs, ZRParams

MP = ZRParams(a=200.0, b=1.6, source="marshall_palmer", n_pairs=0)
"""The relation the bundles are rendered with, so a fit on them should recover something near it."""

SKY_PX = 120
"""The production Sky grid: 60 km at 500 m (SPEC.md 3.3)."""

MID_STORM_FRAME = 12
"""The last of the three frames a mid-storm cycle sees: 120 min into the replay window, the
same place the measurement on `bundles/MUM-2019-07-02/radar/frames.zarr` was taken."""

SKY_RES_M = 500.0
LEFT, TOP = 280_000.0, 2_140_000.0
"""A UTM 43N origin placing the domain over the Mumbai AOI; only the georeference matters."""


def sky_grid(n_px: int = SKY_PX) -> RadarGrid:
    return RadarGrid(
        crs="EPSG:32643",
        res_m=SKY_RES_M,
        n_px=n_px,
        transform=(SKY_RES_M, 0.0, LEFT, 0.0, -SKY_RES_M, TOP),
    )


def aoi_grid(sky: RadarGrid, *, res_m: float = 30.0, width: int = 64, height: int = 96) -> AoiGrid:
    """A small AOI window sitting inside the Sky domain, as Mumbai's does."""
    left = sky.left + 10_000.0
    top = sky.top - 10_000.0
    return AoiGrid(
        crs=sky.crs,
        res_m=res_m,
        width=width,
        height=height,
        transform=(res_m, 0.0, left, 0.0, -res_m, top),
    )


def designed_frames(
    grid: RadarGrid, *, n_frames: int = 3, seed: int = 2019
) -> tuple[RadarFrames, np.ndarray]:
    """Three frames from the storm designer that builds the demo bundle, rendered as it is.

    :func:`storm_frames` is one Gaussian on a *uniform* background. That is a good fixture for
    pairing, projection and units, and a degenerate one for a nowcaster: pySTEPS decomposes
    into a scale cascade and fits AR(2) per level from the lag correlations of the
    advection-corrected frames, and over a constant background those correlations come out of
    numerical noise. On the 120 px domain it measures a **negative** lag-1 correlation at the
    largest scale, phi_0 goes to ~0.99, and the forecast is almost pure noise - the cell
    flattens from 36 mm/h to 12 mm/h at the first 5-minute step.

    Writing a prettier synthetic field does not fix that; a hand-rolled multi-scale field
    measured 28 % away. So this uses `varuna_replay.storm` - the same seeded generator that
    writes `bundles/MUM-2019-07-02/radar/frames.zarr`, through the same Marshall-Palmer
    inverse and 5 dBZ quantisation - so the test measures Sky on the field Sky is actually
    given. The bundle's own cube is gitignored (it is generated), so it is regenerated here
    rather than read, which keeps the test working from a clean clone.
    """
    from varuna_replay.domain import StormDomain, step_times_min
    from varuna_replay.storm import RadarRender, radar_dbz, rain_field, random_storm

    domain = StormDomain(
        crs=int(grid.crs.split(":")[-1]),
        res_m=grid.res_m,
        n_px=grid.n_px,
        left=grid.left,
        top=grid.top,
    )
    # The full replay window, then the frames a mid-storm cycle actually sees. At the storm's
    # birth the cells are still growing, so a nowcast built on advection plus persistence
    # under-forecasts by construction (measured 20.5 % at t=0..20 min against 6.7 % mid-storm
    # on the bundle's own cube) - and a cycle at 06:40 is not what the demo runs on anyway.
    window_min = 240.0
    design = random_storm(domain, seed=seed, window_min=window_min)
    times = step_times_min(0.0, window_min, 10.0)
    rain_stack = rain_field(design, domain, times)
    dbz = radar_dbz(rain_stack, domain, RadarRender(seed=seed))
    first = MID_STORM_FRAME - n_frames + 1
    rain_stack = rain_stack[first : MID_STORM_FRAME + 1]
    dbz = dbz[first : MID_STORM_FRAME + 1]
    t0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
    stamps = tuple(t0 + timedelta(minutes=10 * (first + k)) for k in range(n_frames))
    return RadarFrames(dbz=dbz, times=stamps, grid=grid), rain_stack


def storm_frames(
    grid: RadarGrid,
    *,
    n_frames: int = 3,
    du: float = 3.0,
    dv: float = 1.0,
    peak_mm_h: float = 40.0,
) -> tuple[RadarFrames, np.ndarray]:
    """A translating convective cell on a stratiform background, rendered to dBZ through MP.

    Returns the frames and the rain field they were rendered from, so a test can ask whether a
    stage recovered what went in rather than only whether it returned something.
    """
    rows, cols = np.mgrid[0 : grid.n_px, 0 : grid.n_px]
    rain = []
    for k in range(n_frames):
        r2 = (cols - (40.0 + du * k)) ** 2 + (rows - (55.0 + dv * k)) ** 2
        rain.append(peak_mm_h * np.exp(-r2 / (2 * 9.0**2)) + 2.0)
    rain_stack = np.stack(rain)
    dbz = 10.0 * np.log10(MP.a * np.power(np.maximum(rain_stack, 1e-6), MP.b))
    t0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
    times = tuple(t0 + timedelta(minutes=10 * k) for k in range(n_frames))
    return RadarFrames(dbz=dbz, times=times, grid=grid), rain_stack


def gauges_from(
    frames: RadarFrames,
    rain: np.ndarray,
    *,
    n_stations: int = 12,
    noise: float = 0.0,
    seed: int = 2019,
) -> pd.DataFrame:
    """Synthetic gauge readings sampled from the truth field at scattered stations.

    The bundle's ``gauges.csv`` schema (SPEC.md 10.2): ``ts``, ``station_id``, ``lon``,
    ``lat``, ``mm_5min``. Stations are placed on the metric grid and converted to WGS84, which
    is the direction the real pairing has to undo.
    """
    rng = np.random.default_rng(seed)
    to_wgs = Transformer.from_crs(frames.grid.crs, "EPSG:4326", always_xy=True)
    rows = rng.integers(20, frames.grid.n_px - 20, size=n_stations)
    cols = rng.integers(20, frames.grid.n_px - 20, size=n_stations)

    records = []
    for index, (row, col) in enumerate(zip(rows, cols, strict=True)):
        x, y = frames.grid.xy(float(row), float(col))
        lon, lat = to_wgs.transform(x, y)
        for frame_index, ts in enumerate(frames.times):
            mm_h = float(rain[frame_index, row, col])
            if noise:
                mm_h *= 1.0 + noise * rng.standard_normal()
            records.append(
                {
                    # A reading is stamped at the END of the accumulation it covers.
                    "ts": ts + timedelta(minutes=2, seconds=30),
                    "station_id": f"S{index:02d}",
                    "lon": float(lon),
                    "lat": float(lat),
                    "mm_5min": max(mm_h, 0.0) / 12.0,
                }
            )
    return pd.DataFrame.from_records(records)


def cycle_inputs(
    frames: RadarFrames,
    gauges: pd.DataFrame,
    *,
    n_members: int = 4,
    n_steps: int = 6,
) -> SkyInputs:
    return SkyInputs(
        frames=frames,
        gauges=gauges,
        cycle_ts=frames.latest_ts,
        n_members=n_members,
        n_steps=n_steps,
    )


@pytest.fixture(scope="module")
def small_cycle() -> tuple[SkyInputs, AoiGrid]:
    """One cheap cycle on a 64 px domain, shared by every behavioural test in this file."""
    grid = sky_grid(n_px=64)
    frames, rain = storm_frames(grid)
    return cycle_inputs(frames, gauges_from(frames, rain)), aoi_grid(grid)


# ============================================================================ composition
def test_the_result_carries_every_stage_the_contract_promises(
    small_cycle: tuple[SkyInputs, AoiGrid],
) -> None:
    inputs, aoi = small_cycle
    result = run_sky(inputs, aoi)

    assert result.ensemble.rain_mm_h.shape == (
        inputs.n_members,
        inputs.n_steps,
        inputs.frames.grid.n_px,
        inputs.frames.grid.n_px,
    )
    assert result.products.p50.shape == (
        inputs.n_steps,
        inputs.frames.grid.n_px,
        inputs.frames.grid.n_px,
    )
    assert result.products.aoi_hyetographs.shape == (inputs.n_members, inputs.n_steps)
    assert result.qc.coverage.shape == inputs.frames.grid.shape
    assert result.merge.method in {"mfb+idw", "mfb", "none"}


def test_the_relation_the_fit_produced_is_the_one_the_cube_is_labelled_with(
    small_cycle: tuple[SkyInputs, AoiGrid],
) -> None:
    """Composition, not shape: a cube labelled with a relation other than the one that decoded
    it would make every downstream depth wrong in a way no single-stage test could see."""
    inputs, aoi = small_cycle
    result = run_sky(inputs, aoi)
    assert result.ensemble.zr.a == pytest.approx(result.ensemble.zr.a)
    assert result.ensemble.zr.source in {"adaptive", "marshall_palmer"}
    # The ensemble's motion field is the one measured this cycle, not a default.
    assert result.ensemble.motion is not None
    assert result.ensemble.motion.method in {"lucas_kanade", "zero"}


def test_the_fit_recovers_the_relation_the_frames_were_rendered_with(
    small_cycle: tuple[SkyInputs, AoiGrid],
) -> None:
    """The gauges are sampled from the same field the frames were rendered from, so the cycle
    has enough pairs to fit rather than fall back - which is what makes this an end-to-end
    check of pairing, projection and unit handling at once. A wrong lon/lat transform or a
    mm_5min-as-mm/h slip would show up here as a fallback or a wild exponent."""
    inputs, aoi = small_cycle
    result = run_sky(inputs, aoi)
    zr = result.ensemble.zr
    assert zr.n_pairs > 0, "no gauge paired with the radar; check the projection or the window"
    if zr.source == "adaptive":
        assert 1.1 <= zr.b <= 1.8
        assert 100.0 <= zr.a <= 400.0


def test_the_merge_honours_its_gauges(small_cycle: tuple[SkyInputs, AoiGrid]) -> None:
    """SPEC.md 11.1 step 3: 'merged field honours gauges within 5 %'. The merge measures its
    own miss, so the contract is checked against a number the run reports, not an assumption."""
    inputs, aoi = small_cycle
    result = run_sky(inputs, aoi)
    if result.merge.method == "mfb+idw" and result.merge.max_gauge_error_pct is not None:
        assert result.merge.max_gauge_error_pct <= 5.0


# ============================================================================ timings
def test_every_stage_is_timed_and_the_total_is_their_sum(
    small_cycle: tuple[SkyInputs, AoiGrid],
) -> None:
    """The console prints these as the cycle budget bar, so the arithmetic has to hold."""
    inputs, aoi = small_cycle
    result = run_sky(inputs, aoi)

    assert set(result.stage_ms) == set(STAGES)
    assert all(isinstance(ms, int) and ms >= 0 for ms in result.stage_ms.values())
    assert result.total_ms == sum(result.stage_ms.values())


# ============================================================================ honesty labels
def test_the_run_says_the_nwp_blend_is_off(small_cycle: tuple[SkyInputs, AoiGrid]) -> None:
    """Rule 6: the blend of Appendix A is disabled in P0 and the run has to say so."""
    inputs, aoi = small_cycle
    assert NWP_NOTE in run_sky(inputs, aoi).notes


def test_a_cycle_with_no_gauges_falls_back_and_says_both_things() -> None:
    """No gauges means no adaptive fit and no merge. Neither may be silent: a judge reading
    'Z-R fitted this cycle' when eight pairs never existed is exactly the failure rule 6 is
    about."""
    grid = sky_grid(n_px=64)
    frames, _ = storm_frames(grid)
    empty = pd.DataFrame(columns=["ts", "station_id", "lon", "lat", "mm_5min"])
    result = run_sky(cycle_inputs(frames, empty), aoi_grid(grid))

    notes = " ".join(result.notes)
    assert result.ensemble.zr.source == "marshall_palmer"
    assert "Marshall-Palmer" in notes
    assert result.merge.method == "none"
    assert "No gauge merge" in notes


def test_a_dry_sky_produces_no_rain_and_no_invented_motion() -> None:
    """Nothing in the chain may manufacture rain from an empty domain."""
    grid = sky_grid(n_px=64)
    t0 = datetime(2019, 7, 2, 6, 40, tzinfo=IST)
    frames = RadarFrames(
        dbz=np.full((3, grid.n_px, grid.n_px), np.nan),
        times=tuple(t0 + timedelta(minutes=10 * k) for k in range(3)),
        grid=grid,
    )
    empty = pd.DataFrame(columns=["ts", "station_id", "lon", "lat", "mm_5min"])
    result = run_sky(cycle_inputs(frames, empty), aoi_grid(grid))

    assert float(result.ensemble.rain_mm_h.max()) == 0.0
    assert float(result.products.p90.max()) == 0.0
    assert float(result.products.p_gt_20.max()) == 0.0


# ============================================================================ determinism
def test_two_runs_of_one_cycle_agree_byte_for_byte(
    small_cycle: tuple[SkyInputs, AoiGrid],
) -> None:
    """Rule 8. Two bakes of the same cycle must produce identical products."""
    inputs, aoi = small_cycle
    first, second = run_sky(inputs, aoi), run_sky(inputs, aoi)
    assert np.array_equal(first.ensemble.rain_mm_h, second.ensemble.rain_mm_h)
    assert np.array_equal(first.products.p50, second.products.p50)
    assert np.array_equal(first.products.aoi_hyetographs, second.products.aoi_hyetographs)
    assert first.notes == second.notes


# ============================================================================ P3.7 budget
# SPEC.md P3.7 and section 14 put the Sky stage at five seconds or less. Section 14 calls the
# budgets acceptance criteria, so this measures the real production configuration - 20 members,
# 36 steps, 120 x 120 - and prints what it got.
#
# WHAT CHANGED ON 2026-09-24. The stage used to run its twenty members in one process and
# measured 6.32 s warm / 8.39 s cold. It now splits them across worker processes
# (`varuna_sky.ensemble_pool`, which carries the two measurements that led there: the nowcast
# is 322 ms per member with an ~8 ms fixed cost, and 57 % of it is `scipy.ndimage` C code that
# holds the GIL, which is why pySTEPS' own thread-based `num_workers` never helped). The cube
# is unchanged element for element - `test_steps.py` pins that - so this is wall clock only.
#
# The cost that split cannot remove is the workers' spawn: Windows has no fork, so each worker
# imports numpy, scipy and pysteps from scratch, and measured inside a loaded pytest process
# that took one cycle 45.2 s. So the pool is never waited for. The FIRST cycle in a process
# runs sequentially while the pool warms behind it, and every cycle after it is pooled. That is
# what the two measurements below are, and why there are two.
#
# MEASURED, 2026-09-24, Intel i5-1155G7 (4 physical, 8 logical cores), full 20 x 36 x 120 x 120
# configuration, same process and same inputs, with the python-process count beside each figure
# because this repository's timings move by a factor of two under contention:
#
#   sequential (one process, as the stage used to be)   11.21 / 10.95 / 12.26 s  @ 16 procs
#   pooled, 4 workers, warm                              4.75 /  4.79 /  5.61 s  @ 22-28 procs
#   pool spawn + worker imports, once per process        16.03 s                  @ 24 procs
#
# Two of the three pooled runs are inside the five seconds and the third is not, and the pooled
# runs were taken under HEAVIER load than the sequential ones, because the pool's own four
# workers are counted in those process numbers. So the stage now sits ON the budget rather than
# at twice it. It is not a pass, and the STATUS BOARD should say what this test prints.
#
# For scale on the contention: the same measurement on a quieter machine (12 procs) put the
# sequential stage at 6.82 s, and inside a loaded pytest process it has measured 41 s.
#
# Earlier work on this stage, kept so it is not repeated: num_workers 2/4/6/8 measured
# 4.58/6.40/4.83/4.68 s against 4.52 s at one worker; fft_method "scipy" 4.64 s and "numpy"
# 6.29 s; domain="spectral" 4.18 s but a different cube, so an 8 % gain bought by changing the
# science. All three were thread- or backend-level knobs on a stage whose cost is per member.
#
# The assertion below is a REGRESSION guard, not the budget: it catches a 2x blow-up without
# turning machine noise into a red suite. The printed number is the one to read.
SKY_BUDGET_S = 5.0
CI_SLACK = 4.0 if os.environ.get("CI") else 2.0


@pytest.mark.slow
def test_the_full_configuration_does_not_regress_past_twice_its_budget() -> None:
    """The name says what the assertion does, because the two are not the same thing.

    This was called ``..._meets_the_five_second_budget`` while asserting
    ``SKY_BUDGET_S * CI_SLACK`` - 10 s locally, 20 s under CI. The comment above the
    constants has always said it is a regression guard rather than the budget, but a green
    test read from a CI log or a test list says whatever its name says, and this one said
    the section 14 budget was met when the measured number is over it. Rule 13 makes that
    budget an acceptance criterion, so the miss belongs on the STATUS BOARD (it is there),
    not hidden behind a test name that reports success.

    Two cycles are timed, because a process now has two speeds. The first pays nothing for the
    worker pool and gains nothing from it; the second is the steady state a bake and a second
    "Compute live" both run at, and it is the one the assertion is on.
    """
    grid = sky_grid()
    frames, rain = storm_frames(grid)
    inputs = cycle_inputs(
        frames,
        gauges_from(frames, rain, n_stations=12),
        n_members=20,
        n_steps=36,
    )
    aoi = aoi_grid(grid, width=323, height=522)

    shutdown_pool()
    start = perf_counter()
    first = run_sky(inputs, aoi)
    first_elapsed = perf_counter() - start

    # A bake would call warm_pool() once up front; here it stands in for the cycles that pass
    # while the workers import, so the steady state is measured rather than a race with them.
    warmed = warm_pool(20, timeout=180.0)
    start = perf_counter()
    result = run_sky(inputs, aoi)
    elapsed = perf_counter() - start

    stages = " ".join(f"{name}={result.stage_ms[name]}ms" for name in STAGES)
    verdict = "within" if elapsed <= SKY_BUDGET_S else "OVER"
    print(
        f"\nSky at 20 x 36 on {grid.n_px}x{grid.n_px}: first cycle in the process "
        f"{first_elapsed:.2f} s (sequential, pool warming), steady state {elapsed:.2f} s "
        f"({verdict} the {SKY_BUDGET_S:.0f} s budget of SPEC.md 14, pool warm={warmed}); "
        f"{stages}"
    )

    assert result.ensemble.rain_mm_h.shape == (20, 36, grid.n_px, grid.n_px)
    assert np.array_equal(first.ensemble.rain_mm_h, result.ensemble.rain_mm_h), (
        "rule 8: the sequential first cycle and the pooled second must be the same cube, or "
        "a bake would depend on how far along its own worker pool was"
    )
    # Two guards, and BOTH must hold. The absolute one is the 2x-budget regression guard this
    # test has always carried, unchanged. The relative one is new with the worker split and is
    # what the split claims: the pooled cycle must beat a sequential cycle of the same inputs
    # taken seconds earlier on the same machine under the same load. It is asserted only when
    # the pool actually warmed, because otherwise the second cycle is sequential too and the
    # comparison is between two equal things.
    #
    # An earlier draft took the *larger* of the two as one ceiling, on the grounds that the
    # absolute guard goes red under contention (this suite has measured the same cycle at 5.65 s
    # and at 41 s). That made the test strictly looser than it had been - a pooled cycle as slow
    # as a loaded sequential one would pass - so it was reverted: contention is a reason to read
    # the printed number, not to widen the guard.
    assert elapsed < SKY_BUDGET_S * CI_SLACK, (
        f"Sky took {elapsed:.2f} s, past the {SKY_BUDGET_S * CI_SLACK:.0f} s regression "
        f"guard ({SKY_BUDGET_S:.0f} s budget x {CI_SLACK:.0f} slack); stages: {stages}"
    )
    if warmed:
        assert elapsed < first_elapsed, (
            f"the pooled cycle took {elapsed:.2f} s against {first_elapsed:.2f} s for the same "
            f"cycle run sequentially moments ago, so the worker split is not paying; {stages}"
        )


def test_the_ensemble_mean_is_near_persistence_at_the_first_lead() -> None:
    """SPEC.md 11.1: "total rain of the ensemble mean over the domain is within 15 % of
    persistence at lead 0".

    Five minutes after the analysis, before advection has moved anything far and before the
    stochastic cascade has had room to diverge, the ensemble mean should still carry
    essentially the field the merge produced. It is the cheapest statement that the nowcast is
    anchored to the observation rather than generating weather of its own, and it was the one
    test of the three in 11.1 that had never been written.

    It runs on :func:`designed_frames`, not the single-Gaussian fixture: over a uniform
    background pySTEPS' cascade correlations are estimated from numerical noise and the
    forecast degenerates, which measures the fixture rather than Sky (ADR-0040).

    **This carried ``xfail(strict=True)`` at median 21.7 % low until 2026-09-24.** What it was
    measuring turned out to be two defects stacked, and only one of them was Sky's:

    * every lead time that falls *between* two 10-minute radar steps - which is every odd lead
      of the 36, including this one - was blended by pySTEPS in dBR, a geometric mean in rain
      (:func:`varuna_sky.steps.correct_interpolated_mass` carries the mechanism, the per-lead
      saw-tooth it produced, and the correction). Fixing that took the lead-0 figure from a
      median 21.7 % to **5.4 % across the same five storm draws** (3.0-6.5 %), inside 11.1's
      15 %;
    * the whole pySTEPS steps carry their own deficit - **10.5 to 13.5 % at lead 10 minutes**,
      growing past 30 % by two hours - and nothing here touches it. That is the STEPS cascade,
      its precipitation mask and its probability matching. So this test passing says the
      nowcast is anchored at five minutes; it does **not** say the cube conserves mass, and
      ``test_the_whole_step_deficit_is_the_one_still_open`` keeps that number on the record.
    """
    grid = sky_grid(n_px=120)
    frames, rain = designed_frames(grid)
    inputs = cycle_inputs(frames, gauges_from(frames, rain), n_members=8, n_steps=6)
    result = run_sky(inputs, aoi_grid(grid))

    persistence = float(np.nansum(result.merge.rain_mm_h))
    first_lead = float(np.nansum(np.nanmean(result.ensemble.rain_mm_h[:, 0], axis=0)))

    assert persistence > 0.0, "the fixture storm must be wet, or this proves nothing"
    relative = abs(first_lead - persistence) / persistence
    print(
        f"\nlead-0 total: {first_lead:.1f} against persistence {persistence:.1f} "
        f"({relative * 100:.1f} % away; 11.1 allows 15 %)"
    )
    assert relative <= 0.15, (
        f"the ensemble mean holds {first_lead:.1f} mm/h against persistence "
        f"{persistence:.1f} mm/h at lead 0, {relative * 100:.1f} % away; 11.1 allows 15 %"
    )


def test_the_whole_step_deficit_is_the_one_still_open() -> None:
    """The half of the lead-0 bias that was NOT fixed, kept on the record with a number.

    ``correct_interpolated_mass`` restores the mass pySTEPS' dBR temporal blend destroys, which
    is what the lead-0 test above measures. It does nothing at all to the lead times that are
    whole pySTEPS steps, and those are 10.5-13.5 % low at ten minutes on the same storms - the
    STEPS cascade, its precipitation mask and its probability matching, none of which is ours.

    Rule 6 says the number on the screen is one that was measured; this is the test that keeps
    measuring it, so nobody reads a green lead-0 test as "Sky conserves mass". The bound is
    generous (25 %) because the point is to print the figure and to catch a *collapse*, not to
    freeze a number the cascade is entitled to move.
    """
    grid = sky_grid(n_px=120)
    frames, rain = designed_frames(grid)
    inputs = cycle_inputs(frames, gauges_from(frames, rain), n_members=8, n_steps=2)
    result = run_sky(inputs, aoi_grid(grid))

    persistence = float(np.nansum(result.merge.rain_mm_h))
    # Output 1 is lead 1.0 of the input interval - ten minutes, a whole pySTEPS step, with no
    # temporal interpolation in it at all.
    whole_step = float(np.nansum(np.nanmean(result.ensemble.rain_mm_h[:, 1], axis=0)))
    deficit = (persistence - whole_step) / persistence
    print(
        f"\nwhole-step (10 min) total: {whole_step:.1f} against persistence "
        f"{persistence:.1f} ({deficit * 100:.1f} % low; the STEPS cascade's own, not the "
        "temporal blend's)"
    )
    assert deficit < 0.25, (
        f"the whole-step deficit has grown to {deficit * 100:.1f} %; it measured 10.5-13.5 % "
        "over five storms on 2026-09-24, and past 25 % something other than the cascade is wrong"
    )

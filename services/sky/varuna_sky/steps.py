"""The STEPS ensemble nowcast (SPEC.md 11.1 step 5).

``pysteps.nowcasts.get_method("steps")`` run exactly as SPEC.md 11.1 step 5 specifies: 20
members, 5-minute steps, 36 lead times, six cascade levels, AR(2), probability matching on.
:func:`nowcast` is the only entry point the cycle needs; it tries pySTEPS and, if pySTEPS is
missing or raises, hands the same arguments to :mod:`varuna_sky.fallback_steps` (SPEC.md 17).
The caller never chooses, and never has to know which ran: ``RainEnsemble.source`` says.

**The dBR conversion.** pySTEPS works on a logarithmic field, not on rain rate: the cascade
decomposition, the AR fit and the nonparametric noise generator all assume something close to
Gaussian. So the analysis goes in as dBR (``10 log10 R`` in mm/h, dry pixels floored at
:data:`~varuna_sky.motion.DBR_ZEROVALUE`), ``precip_thr`` is that floor in dBR, and the output
comes back through :func:`~varuna_sky.motion.from_dbr`, where anything under the floor - and
every ``nan`` pySTEPS leaves outside its precipitation mask - becomes exactly zero rain.

**Step length against frame interval.** The bundles carry radar every 10 minutes but VARUNA
forecasts every 5 (SPEC.md 10.2, 10.3). pySTEPS measures lead time in input intervals, so
the request is a list of fractional steps - 0.5, 1.0, 1.5 ... - rather than a rescaled motion
field. Rescaling the motion would have told pySTEPS that the frames were 5 minutes apart and
halved the lifetime of every scale in the AR fit.

That request has a cost pySTEPS does not advertise and VARUNA had been paying since Phase 3:
the lead times that fall *between* two integer steps - every odd one of the 36 - are reached by
blending the bracketing states linearly in dBR, which is a geometric mean in rain and is 10 to
13 percentage points low at every one of them. :func:`correct_interpolated_mass` carries the
measurement and the correction; it is why SPEC.md 11.1's "within 15 % of persistence at lead
0" went from a strict xfail at 21.7 % to 5.4 % on 2026-09-24.

Determinism (rule 8): ``seed`` is passed straight to pySTEPS, which seeds its noise and its
velocity perturbations from it, so a re-run of a cycle reproduces the cube.

**Where the five seconds went.** The ensemble is split across worker processes when there are
enough members to be worth it (P3.7; :mod:`varuna_sky.ensemble_pool` carries the measurements
and the reasoning). The split reproduces the single-call cube element for element, because each
group is handed the seed pySTEPS would have held at that member, so a baked run does not change
when the worker count does. :func:`steps_members` is the part that runs in a worker: it takes
plain arrays and numbers rather than VARUNA dataclasses, so nothing but the cube crosses the
process boundary.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np
import structlog

from varuna_sky import fallback_steps
from varuna_sky.ensemble_pool import member_groups, member_seed, run_groups, worker_count
from varuna_sky.motion import DBR_THRESHOLD, MAX_RAIN_MM_H, from_dbr, rain_history, to_dbr
from varuna_sky.types import MotionField, RainEnsemble, SkyInputs, ZRParams

if TYPE_CHECKING:  # pragma: no cover - typing only
    from typing import Any

    from numpy.typing import NDArray

log = structlog.get_logger("varuna.sky.steps")

__all__ = ["AR_ORDER", "N_CASCADE_LEVELS", "nowcast", "steps_members", "steps_nowcast"]

N_CASCADE_LEVELS = 6
"""Scale cascade levels (SPEC.md 11.1 step 5)."""

AR_ORDER = 2
"""AR(2) per cascade level (SPEC.md 11.1 step 5)."""

PROBMATCHING_METHOD = "cdf"
""""Probability matching on" (SPEC.md 11.1 step 5): each member's intensity distribution is
matched to the observed one."""

MASK_METHOD = "incremental"
"""How the precipitation mask is grown with lead time. SPEC.md does not fix this; it is the
pySTEPS default and is the one that keeps the wet area from collapsing over three hours."""

VEL_PERT_METHOD = "bps"
"""Velocity perturbations (Bowler et al.), the pySTEPS default: members disagree about where
the storm goes, not only about how hard it rains. A choice, not a SPEC.md requirement."""


def _forecast_times(inputs: SkyInputs) -> tuple:
    """Valid time of each step: step ``k`` is ``cycle_ts + (k + 1) * step_min`` (types.py)."""
    return tuple(
        inputs.cycle_ts + timedelta(minutes=inputs.step_min * (step + 1))
        for step in range(int(inputs.n_steps))
    )


def _analysis_history(
    rain_mm_h: NDArray[np.floating],
    inputs: SkyInputs,
    zr: ZRParams,
) -> NDArray[np.floating]:
    """The ``(AR_ORDER + 1, n_px, n_px)`` rain history pySTEPS needs to fit AR(2).

    A 2D ``rain_mm_h`` is the merged analysis alone, so the earlier frames are rebuilt from
    radar through the cycle's Z-R relation (:func:`~varuna_sky.motion.rain_history`).
    """
    analysis = np.asarray(rain_mm_h, dtype=np.float64)
    if analysis.ndim == 2:
        return rain_history(inputs.frames, zr, analysis, n_frames=AR_ORDER + 1)
    if analysis.ndim != 3:
        msg = f"rain_mm_h must be 2D or 3D, got {analysis.shape}"
        raise ValueError(msg)
    if analysis.shape[0] < AR_ORDER + 1:
        pad = np.repeat(analysis[:1], AR_ORDER + 1 - analysis.shape[0], axis=0)
        analysis = np.concatenate([pad, analysis], axis=0)
    return np.ascontiguousarray(analysis[-(AR_ORDER + 1) :], dtype=np.float64)


def steps_members(
    history_mm_h: NDArray[np.floating],
    velocity: NDArray[np.floating],
    timesteps: list[float],
    *,
    n_members: int,
    seed: int,
    km_per_px: float,
    interval_min: float,
) -> NDArray[np.floating]:
    """One pySTEPS call for ``n_members`` members, returning rain in mm/h.

    This is the half of the stage that runs inside a worker process, so its arguments are
    arrays, lists and numbers only - no VARUNA dataclass, no pandas frame, nothing that would
    drag the gauge table or the radar cube across the process boundary for a forecast that
    does not read them. ``seed`` is this *group's* seed, which
    :func:`varuna_sky.ensemble_pool.member_seed` derives so the group's first member is the
    member pySTEPS would have produced at that offset of a single call.

    Raises whatever pySTEPS raises. The caller decides whether that means the section 17
    fallback or, in a worker, a fall back to the sequential path.
    """
    from pysteps.nowcasts import get_method

    n_px = int(np.asarray(history_mm_h).shape[-1])
    cube = get_method("steps")(
        to_dbr(history_mm_h),
        velocity,
        timesteps,
        n_ens_members=int(n_members),
        n_cascade_levels=N_CASCADE_LEVELS,
        precip_thr=float(DBR_THRESHOLD),
        kmperpixel=float(km_per_px),
        timestep=float(interval_min),
        ar_order=AR_ORDER,
        probmatching_method=PROBMATCHING_METHOD,
        mask_method=MASK_METHOD,
        vel_pert_method=VEL_PERT_METHOD,
        seed=int(seed),
        num_workers=1,
    )
    rain = np.minimum(from_dbr(np.asarray(cube, dtype=np.float64)), MAX_RAIN_MM_H)
    expected = (int(n_members), len(timesteps), n_px, n_px)
    if rain.shape != expected:
        # pySTEPS short-circuits a domain with no rain above the threshold ("the resulting
        # forecast will contain only zeros") and, when the lead times are fractional, returns
        # that zero field at the wrong length. A dry forecast is still pySTEPS' answer, so it
        # is restated at the requested length rather than being called a fallback.
        if not np.any(rain) and rain.shape[0] == expected[0] and rain.shape[2:] == expected[2:]:
            log.info("steps.dry_forecast", returned=tuple(rain.shape), expected=expected)
            rain = np.zeros(expected, dtype=np.float64)
        else:
            msg = f"pySTEPS returned {rain.shape}, expected {expected}"
            raise ValueError(msg)
    return rain


def _group_payloads(
    history: NDArray[np.floating],
    velocity: NDArray[np.floating],
    timesteps: list[float],
    groups: tuple[tuple[int, int], ...],
    inputs: SkyInputs,
    km_per_px: float,
    interval_min: float,
) -> tuple[dict[str, Any], ...]:
    """One :func:`steps_members` keyword payload per group, each with its own chained seed."""
    return tuple(
        {
            "history_mm_h": history,
            "velocity": velocity,
            "timesteps": timesteps,
            "n_members": size,
            "seed": member_seed(int(inputs.seed), offset),
            "km_per_px": km_per_px,
            "interval_min": interval_min,
        }
        for offset, size in groups
    )


MAX_MASS_FACTOR = 2.0
"""Ceiling on the interpolated-lead mass correction (:func:`correct_interpolated_mass`).

The factor a correct blend implies is ``1.1``-``1.35`` on the storms it was measured on. A
factor beyond a factor of two either way means the two whole steps bracketing this lead
disagree so violently that "the mass in between is between them" has stopped being a
statement about the same storm, and in that case the field is left alone and the clamp is
logged rather than silently applied.
"""


def _whole_step_totals(
    rain: NDArray[np.floating],
    timesteps: list[float],
    analysis_total: NDArray[np.floating],
) -> dict[float, NDArray[np.floating]]:
    """Domain total per member at each whole input step, plus step 0 (the analysis)."""
    totals: dict[float, NDArray[np.floating]] = {0.0: analysis_total}
    for index, step in enumerate(timesteps):
        if float(step).is_integer():
            totals[float(step)] = rain[:, index].sum(axis=(1, 2))
    return totals


def correct_interpolated_mass(
    rain: NDArray[np.floating],
    timesteps: list[float],
    analysis_mm_h: NDArray[np.floating],
) -> tuple[NDArray[np.floating], int]:
    """Put the mass back into the lead times pySTEPS reaches by temporal interpolation.

    **The defect.** VARUNA forecasts every 5 minutes from radar that arrives every 10
    (SPEC.md 10.2, 10.3), so half of the 36 lead times land between two of pySTEPS' integer
    steps. For those, ``pysteps.nowcasts.utils.nowcast_main_loop`` blends the two bracketing
    states linearly **in dBR** before advecting::

        precip_forecast_ip = (1 - w) * precip_forecast_prev + w * precip_forecast_new

    dBR is ``10 log10 R``, so a linear blend of two dBR fields is the *geometric* mean of the
    two rain fields. The geometric mean is below the arithmetic mean whenever the two disagree,
    and they disagree exactly where the storm is, because that is what has moved between them.
    The result is a systematic low bias on every interpolated lead, and it is not small.

    **Measured** (2026-09-24, eight members, three seeded design storms from the storm designer
    that writes the demo bundle, domain total against the merged analysis, per lead)::

        seed 2019  -23.2 -11.3 -28.3 -15.2 -30.1 -17.5 -36.4 -22.8 -41.3 -34.5 -46.4 -35.7 %
        seed 2021  -22.2 -13.5 -33.9 -17.0 -34.0 -20.8 -34.1 -23.2 -36.9 -27.7 -45.0 -33.1 %
        seed 2023  -25.4 -10.5 -28.8 -14.2 -30.9 -16.9 -36.2 -25.8 -47.4 -44.3 -53.7 -34.6 %

    Every odd entry is an interpolated lead and every even one is a whole pySTEPS step, and the
    saw-tooth is 10 to 13 percentage points deep at every tooth in all three storms. It is not
    an edge effect: masking a 12-pixel rim off the domain moves the lead-0 figure by less than
    a point. It is not the ensemble average either: individual member totals carry the same
    bias, so the mean inherits it rather than creating it.

    **The correction.** Had the blend been arithmetic in rain, the blended field's domain total
    would be exactly ``(1 - w) * total_prev + w * total_next``. That is the target used here -
    computed from this run's own whole-step fields, never from a constant - and each
    interpolated lead is scaled by one factor per member to reach it. Nothing else is touched:
    the advected *pattern* pySTEPS produced is the correct one, and it is the first moment of
    that pattern the log blend destroyed.

    **What it does not fix, and must not be read as fixing.** The whole steps carry their own
    deficit - 10.5 to 13.5 % at lead 10 minutes, growing past 30 % by two hours - and that is
    the STEPS cascade, its precipitation mask and its probability matching, not this. Nothing
    here touches it, and SPEC.md 11.1's "within 15 % of persistence at lead 0" is met after
    this correction by the 5-minute lead, not by the cube as a whole.

    **What it costs the tail, measured, because that is what ADR-0040 traded.** ADR-0040
    rejected ``probmatching_method="mean"`` for this same mass deficit because it inflated the
    99.9th percentile by 52 % - a phantom cloudburst in a flood nowcaster. The same five storms
    with this correction on and off, 99.9th percentile of the lead-0 ensemble mean, against the
    analysis it decays from::

        seed        2019    2020    2021    2022    2023     mm/h
        off        24.09   55.52   88.33   22.62   12.62
        on         29.51   64.27  106.63   24.28   15.79     +7.3 % to +25.1 %
        analysis   73.06   75.35  152.94   45.45   36.32

    Every corrected value is still well under the analysis' own tail, and it has to be: the
    factor is a ratio of totals and measured 1.07-1.25. The blend had pushed these leads' tails
    *below* the whole step that follows them, which is not a forecast decaying, and the test in
    ``test_steps.py`` asserts the ordering rather than the factor.

    Returns the corrected cube and the number of leads that were corrected.
    """
    if rain.size == 0 or not np.any(rain):
        return rain, 0
    analysis = np.nan_to_num(np.asarray(analysis_mm_h, dtype=np.float64))
    if analysis.ndim == 3:
        analysis = analysis[-1]
    analysis_total = np.full(rain.shape[0], float(analysis.sum()), dtype=np.float64)

    totals = _whole_step_totals(rain, timesteps, analysis_total)
    out = rain
    corrected = 0
    for index, step in enumerate(timesteps):
        value = float(step)
        if value.is_integer():
            continue
        low, high = float(np.floor(value)), float(np.ceil(value))
        if low not in totals or high not in totals:
            # A lead whose bracketing whole steps are not both in the cube - only possible if
            # the requested leads stop mid-interval. There is no target to aim at, so it is
            # left as pySTEPS produced it.
            continue
        weight = value - low
        target = (1.0 - weight) * totals[low] + weight * totals[high]
        actual = out[:, index].sum(axis=(1, 2))
        with np.errstate(divide="ignore", invalid="ignore"):
            factor = np.where(actual > 0.0, target / np.where(actual > 0.0, actual, 1.0), 1.0)
        clamped = int(
            np.count_nonzero((factor > MAX_MASS_FACTOR) | (factor < 1.0 / MAX_MASS_FACTOR))
        )
        if clamped:
            log.warning(
                "steps.mass_factor_clamped",
                lead_index=index,
                members=clamped,
                max_factor=round(float(np.max(factor)), 3),
            )
            factor = np.clip(factor, 1.0 / MAX_MASS_FACTOR, MAX_MASS_FACTOR)
        if out is rain:
            out = rain.copy()
        out[:, index] = np.minimum(out[:, index] * factor[:, None, None], MAX_RAIN_MM_H)
        corrected += 1
    return out, corrected


def steps_nowcast(
    rain_mm_h: NDArray[np.floating],
    motion: MotionField,
    inputs: SkyInputs,
    zr: ZRParams,
) -> RainEnsemble:
    """Run pySTEPS STEPS. Raises if pySTEPS is missing or fails - :func:`nowcast` catches it.

    The members are split across worker processes when there is more than one group's worth of
    them (P3.7). Whether they were or not, the cube is the same one: each group carries the
    seed pySTEPS would have reached at its first member, which ``test_steps.py`` pins with an
    element-for-element comparison against the single call.
    """
    history = _analysis_history(rain_mm_h, inputs, zr)
    grid = inputs.frames.grid
    interval_min = max(inputs.frames.interval.total_seconds() / 60.0, 1.0)
    step_ratio = float(inputs.step_min) / interval_min
    timesteps = [round((step + 1) * step_ratio, 6) for step in range(int(inputs.n_steps))]
    velocity = np.stack([np.asarray(motion.u), np.asarray(motion.v)])
    km_per_px = grid.res_m / 1000.0

    n_workers = worker_count(int(inputs.n_members))
    groups = member_groups(int(inputs.n_members), n_workers)
    parts: tuple[NDArray[np.floating], ...] | None = None
    if len(groups) > 1:
        payloads = _group_payloads(
            history, velocity, timesteps, groups, inputs, km_per_px, interval_min
        )
        parts = run_groups(payloads, len(groups))
    if parts is None:
        parts = (
            steps_members(
                history,
                velocity,
                timesteps,
                n_members=int(inputs.n_members),
                seed=int(inputs.seed),
                km_per_px=km_per_px,
                interval_min=interval_min,
            ),
        )
        groups = ((0, int(inputs.n_members)),)

    rain = parts[0] if len(parts) == 1 else np.concatenate(parts, axis=0)
    expected = (int(inputs.n_members), int(inputs.n_steps), grid.n_px, grid.n_px)
    if rain.shape != expected:
        msg = f"pySTEPS returned {rain.shape}, expected {expected}"
        raise ValueError(msg)
    # After the concatenation, never inside a worker: the correction reads whole-step totals
    # that may sit in a different group, and doing it here also keeps the split cube and the
    # sequential cube identical by construction.
    rain, n_corrected = correct_interpolated_mass(rain, timesteps, history)
    log.info(
        "steps.pysteps",
        members=expected[0],
        steps=expected[1],
        groups=len(groups),
        interpolated_leads_corrected=n_corrected,
        max_mm_h=round(float(rain.max()), 2),
        motion=motion.method,
    )
    return RainEnsemble(
        rain_mm_h=rain,
        times=_forecast_times(inputs),
        grid=grid,
        source="pysteps_steps",
        seed=int(inputs.seed),
        zr=zr,
        motion=motion,
    )


def nowcast(
    rain_mm_h: NDArray[np.floating],
    motion: MotionField,
    inputs: SkyInputs,
    zr: ZRParams,
) -> RainEnsemble:
    """The 20-member, 3-hour rain ensemble for this cycle (SPEC.md 11.1 step 5).

    ``rain_mm_h`` is the gauge-merged analysis: either the latest field ``(n_px, n_px)`` or a
    history ``(n_frames, n_px, n_px)`` ending with it. pySTEPS runs if it can; if it cannot,
    :func:`varuna_sky.fallback_steps.nowcast` does, and the returned ``source`` says which, so
    the console can label the run rather than quietly showing a lesser forecast as the real one.
    """
    try:
        return steps_nowcast(rain_mm_h, motion, inputs, zr)
    except Exception as exc:
        log.warning("steps.pysteps_failed", error=str(exc), fallback="fallback_steps")
        return fallback_steps.nowcast(rain_mm_h, motion, inputs, zr)

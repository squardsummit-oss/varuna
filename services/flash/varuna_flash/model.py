"""Flash-lite: a reduced-order emulator of VARUNA-Twin (SPEC.md 11.7, tasks P7.5 to P7.8).

The Twin is a coupled 1D-2D solver and a three-hour Mumbai run takes about three minutes. That is
the right cost for the forecast of record and the wrong cost for the two things an operator does
interactively: **what-if** ("clean these fourteen pipes - what changes?") and **attribution**
("which pipes are making Hindmata flood?"). Attribution alone needs one run per candidate pipe.
So the physics is emulated by something that runs the same three hours in milliseconds.

**The model.** Per road segment, a two-reservoir Nash cascade driven by effective rain over the
segment's own catchment, drained at a rate the pipe below it can carry::

    dS1/dt = R_eff * A  -  S1 / k
    dS2/dt = S1 / k     -  S2 / k
    depth  = S2 / A_pond  -  (1 - beta) * q_capacity * dt / A_pond   (floored at zero)

Two reservoirs because one gives an instantaneous peak and three is a shape the data here cannot
distinguish from two. ``k`` is the storage coefficient - the time the catchment takes to deliver
its water - and it is the parameter fitted per segment against Twin runs.

**Where it departs from SPEC.md 11.7.** The spec fits ``k_u`` per *surface unit* and couples
through `drain1d`. This fits per *road segment* and folds the drain into a per-segment capacity
term. The reason is that every product downstream - the map, the rail, alerts, routing - is keyed
on segments, so a unit-keyed emulator would need a unit-to-segment reduction on every call and
would be fitted against a target it never predicts. The structure is the same two-reservoir
cascade; what changed is the element it is solved on. ADR-0025.

**It emulates a deviation, not a depth field.** A local rain cascade cannot produce what the
Twin produces at the coast: segment ``S215609077-002`` takes 10 mm of rain and ponds 60 cm,
because that water is the tide and the runoff of half a catchment upstream, not the rain that
fell on it. Fitting the whole depth to local rain gave a peak correlation of 0.98 and a third of
the amplitude - the right map at the wrong scale, and a CSI at 30 cm of 0.02.

So the model carries a **base state**: the depth series every training storm produced regardless
of intensity, taken as the element-wise minimum over the fitting runs. The cascade is fitted to
the deviation from it, and :func:`simulate` adds it back. That is a perturbation model around a
reference, which is a standard reduced-order construction and has a standard limitation - the
base state belongs to the tide series and the three-hour window it was measured in.

**What follows from that, and is enforced:** what-if may scale the rain, clean pipes and run
pumps, because those move the deviation. It may **not** move the tide, because that moves the
base state and this model has no representation of it. `/v1/whatif` rejects a tide offset and
says why rather than returning a number it cannot support.

**What it is fitted to, honestly.** Eight Twin runs: design storms at 25, 50, 75 and 110 mm/h
peak, each at the city's blockage prior and at 1.8x that prior. SPEC.md 11.7 asks for 200. Eight
runs at three minutes each is the budget that existed; the held-out skill below is measured and
published rather than asserted, so the reader can see exactly how much to trust it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from numpy.typing import NDArray

log = structlog.get_logger("varuna.flash.model")

__all__ = [
    "K_BOUNDS",
    "K_LOG_SD",
    "FlashModel",
    "draw_members",
    "fit",
    "load",
    "simulate",
]

K_BOUNDS = (0.5, 12.0)
"""Storage coefficient bounds, in units of the 5-minute step.

Half a step is a catchment that responds instantly - a paved junction with its inlet in the
gutter. Twelve steps is an hour, which is about as slow as a 30 m cell's contributing area can
plausibly be. Bounds rather than a free fit because the objective is nearly flat for a segment
that never gets wet, and an unbounded least squares there returns nonsense with confidence."""

DEFAULT_K = 3.0
"""What a segment gets when the training runs never wet it enough to fit a curve.

Fifteen minutes. Stated as a constant rather than hidden in a fallback branch, because a good
fraction of the network is never wet in eight design storms and a reader deserves to know that
those segments are carrying a prior rather than a measurement."""

K_FIT_GRID = 16
"""How many storage coefficients :func:`_fit_one` scores. See :data:`K_LOG_SD`."""

K_LOG_SD = 0.25
"""Multiplicative spread on ``k`` between ensemble members, as a log-space standard deviation.

SPEC.md 11.7 asks for "MC noise on ``k_u`` for structural spread": the members should disagree
about the emulator's own parameters, not only about the weather, or the ensemble understates how
little the model knows. The size of the noise is derived rather than chosen. :func:`_fit_one`
searches ``k`` on a grid of :data:`K_FIT_GRID` points across :data:`K_BOUNDS`, so two values
closer than one grid step - ``11.5 / 15 = 0.77``, which is 26 % of the typical fitted
:data:`DEFAULT_K` - are values the fit could not tell apart in the first place. A quarter in log
space is that resolution limit, and is therefore the least the members may honestly disagree by.

It is a resolution limit and **not** a fitted posterior: the eight-run fit (ADR-0025) publishes no
per-segment uncertainty on ``k``, so this is the same width on every street, including the ones
that were never wet in training and are carrying :data:`DEFAULT_K` rather than a measurement."""


@dataclass(frozen=True, slots=True)
class FlashModel:
    """A fitted emulator: one storage coefficient and one drainage rate per segment."""

    segment_ids: tuple[str, ...]
    k_steps: NDArray[np.floating]
    """Nash storage coefficient per segment, in 5-minute steps."""

    gain: NDArray[np.floating]
    """cm of depth per mm/h of sustained rain, per segment, at the fitted blockage.

    Absorbs the catchment area, the ponding area and the runoff coefficient into one number,
    because the emulator is fitted to depth and only their product is identifiable from it."""

    drain_cm_per_step: NDArray[np.floating]
    """Depth the pipe below removes per step at beta = 0, per segment."""

    beta_ref: NDArray[np.floating]
    """The blockage the fit was made at, so what-if can scale the drainage term from it."""

    baseline_cm: NDArray[np.floating]
    """``(n_steps, n_segments)`` depth present in every fitting storm: tide, and runoff arriving
    from upstream. The cascade predicts the deviation from this and :func:`simulate` adds it
    back. Measured from the fitting runs only, never from the holdout."""

    n_training_runs: int
    rmse_cm: float
    """Held-out root-mean-square error against the Twin, in cm."""

    csi_30cm: float
    """Held-out critical success index at the 30 cm car threshold."""

    fitted_segments: int
    """How many segments had enough wet steps to fit; the rest carry :data:`DEFAULT_K`."""

    @property
    def n_segments(self) -> int:
        return len(self.segment_ids)


def simulate(
    model: FlashModel,
    rain_mm_h: NDArray[np.floating],
    *,
    beta: NDArray[np.floating] | None = None,
    k_steps: NDArray[np.floating] | None = None,
    pump_cm_per_step: NDArray[np.floating] | None = None,
) -> NDArray[np.floating]:
    """Three hours of street depth for every segment, in cm. ``(n_steps, n_segments)``.

    Args:
        model: the fitted emulator.
        rain_mm_h: ``(n_steps,)`` AOI rain, or ``(n_steps, n_segments)`` per segment.
        beta: blockage per segment to run at; the fitted reference when absent. This is the
            what-if handle - cleaning a pipe is lowering its beta.
        k_steps: storage coefficient per segment to run at; the fitted value when absent. This
            is the handle :func:`draw_members` needs - a member that cannot be given its own
            ``k`` carries no structural spread at all, only the weather's.
        pump_cm_per_step: extra drawdown per segment, for the pump plan.

    The whole point is the cost: this is two vectorised recursions over 36 steps, so the entire
    21,296-segment city runs in single-digit milliseconds and a what-if is interactive.
    """
    rain = np.asarray(rain_mm_h, dtype=np.float64)
    if rain.ndim == 1:
        rain = rain[:, None] * np.ones(model.n_segments)[None, :]
    n_steps = rain.shape[0]

    k = np.clip(model.k_steps if k_steps is None else np.asarray(k_steps), *K_BOUNDS)
    outflow_fraction = 1.0 / k

    # The drainage a pipe still provides at the blockage being asked about. Manning's capacity
    # falls as (1 - beta)^(5/3), the same exponent `varuna_pulse.health` reports.
    beta_now = model.beta_ref if beta is None else np.clip(np.asarray(beta), 0.0, 1.0)
    capacity = model.drain_cm_per_step * (1.0 - beta_now) ** (5.0 / 3.0)
    if pump_cm_per_step is not None:
        capacity = capacity + np.asarray(pump_cm_per_step)

    s1 = np.zeros(model.n_segments)
    s2 = np.zeros(model.n_segments)
    depth = np.zeros((n_steps, model.n_segments))
    ponded = np.zeros(model.n_segments)

    for step in range(n_steps):
        inflow = rain[step] * model.gain
        # Two linear reservoirs in series: the classic Nash cascade, explicit at the 5-minute
        # step the products are on.
        out1 = s1 * outflow_fraction
        s1 = s1 + inflow - out1
        out2 = s2 * outflow_fraction
        s2 = s2 + out1 - out2
        # What reaches the street ponds there, less what the drain takes away.
        ponded = np.maximum(ponded + out2 - capacity, 0.0)
        depth[step] = ponded

    # Add the base state back. Cleaning a pipe drains what the rain put on the street; it does
    # not hold back the sea, so the drainage term above acts on the deviation alone.
    base = model.baseline_cm
    if base.size:
        take = min(n_steps, base.shape[0])
        depth[:take] += base[:take]
    return depth


def draw_members(
    model: FlashModel,
    beta_mean: NDArray[np.floating],
    beta_sd: NDArray[np.floating],
    n_members: int = 50,
    n_sky: int = 20,
    seed: int = 2019,
) -> tuple[NDArray[np.int64], NDArray[np.float64], NDArray[np.float64]]:
    """Draw the parameters of a 50-member street ensemble. ``(sky_index, beta, k)``.

    SPEC.md 11.7: "50 members = 20 Sky members x parameter draws from the Pulse posterior
    (with replacement); MC noise on ``k_u`` for structural spread". Each member is therefore one
    Sky rain member run through one draw of the emulator's parameters, and the count of members
    is not the count of rain members - fifty streets-level futures over twenty weather futures,
    because the weather is not the only thing the forecast is uncertain about.

    Args:
        model: the fitted emulator; ``model.k_steps`` is the centre of the ``k`` draw.
        beta_mean, beta_sd: the Pulse posterior per **road segment**, in
            ``model.segment_ids`` order. Pulse publishes blockage per *drain edge*
            (:class:`varuna_pulse.enkf.EnkfResult`); ``varuna_pulse.join.segment_beta`` is what
            carries it across to this index, and a length mismatch here almost always means the
            edge-keyed arrays were passed straight in.
        n_members: how many members to draw.
        n_sky: how many Sky rain members there are to draw from.
        seed: rule 8 - two bakes on the same inputs must be byte-identical, so every draw in
            this function comes from one ``np.random.default_rng(seed)`` and nothing else.

    Returns:
        ``sky_index`` ``(n_members,)`` - which Sky rain member each ensemble member uses, drawn
        with replacement; ``beta`` and ``k`` ``(n_members, n_segments)`` - the blockage and
        storage coefficient to run that member at. Feed a member straight to :func:`simulate`::

            sky_index, beta, k = draw_members(model, mean, sd)
            depth = simulate(model, hyetographs[sky_index[m]], beta=beta[m], k_steps=k[m])

    **Unresolved segments stay unresolved.** ``segment_beta`` returns NaN for the 4,313 Mumbai
    segments with no inlet link rather than handing them the prior, and this function propagates
    that NaN into ``beta`` and on into a NaN depth. Substituting the city prior here would launder
    "we do not know" into "we measured" (rule 6); the caller that does substitute is expected to
    count the segments it substituted and say so on screen.
    """
    if n_sky < 1:
        msg = f"need at least one Sky member to draw from; got n_sky={n_sky}"
        raise ValueError(msg)
    if n_members < 1:
        msg = f"need at least one ensemble member; got n_members={n_members}"
        raise ValueError(msg)

    mean = np.asarray(beta_mean, dtype=np.float64)
    sd = np.asarray(beta_sd, dtype=np.float64)
    if mean.shape != (model.n_segments,) or sd.shape != (model.n_segments,):
        msg = (
            f"blockage must be one value per segment, {model.n_segments} of them; got "
            f"mean {mean.shape} and sd {sd.shape}. Pulse publishes blockage per drain edge - "
            "join it to segments with varuna_pulse.join.segment_beta first."
        )
        raise ValueError(msg)

    rng = np.random.default_rng(seed)
    shape = (n_members, model.n_segments)

    # Which weather each member gets. With replacement, per 11.7: fifty members over twenty rain
    # members cannot be a permutation, and sampling makes the rain members equally likely rather
    # than giving the first ten an extra draw apiece the way tiling would.
    sky_index = rng.integers(n_sky, size=n_members, dtype=np.int64)

    # Blockage, drawn in beta space and clipped to a valid blockage. Note that the clip biases
    # the draw for a pipe whose posterior sits within a standard deviation of 0 or 1 - the
    # spread there comes back narrower than `beta_sd` says. The EnKF avoids this by working in
    # logit space; here the spec asks for the published mean and sd, and the honest cost of
    # taking them at face value is a slightly tight ensemble on a pipe that is nearly certain.
    beta = np.clip(mean[None, :] + rng.normal(size=shape) * sd[None, :], 0.0, 1.0)

    # Structural spread: the emulator's own parameter, not the weather's. Lognormal because k is
    # a timescale - it cannot go negative, and "20 % slower" and "20 % faster" should be equally
    # likely, which is a statement about ratios. The median member keeps the fitted value.
    k = np.clip(
        model.k_steps[None, :] * np.exp(rng.normal(size=shape) * K_LOG_SD),
        *K_BOUNDS,
    )

    unresolved = int(np.count_nonzero(~np.isfinite(mean)))
    if unresolved:
        log.info(
            "flash.members.unresolved_blockage",
            segments=unresolved,
            of=model.n_segments,
            note="no posterior for these segments; their members carry NaN, not the prior",
        )
    log.info(
        "flash.members.drawn",
        members=n_members,
        sky_members=n_sky,
        segments=model.n_segments,
        seed=seed,
        k_log_sd=K_LOG_SD,
    )
    return sky_index, beta, k


DEPTH_WEIGHT_CM = 5.0
"""Depth at which a step counts double in the fit.

A street is dry at almost every step of almost every storm, so an unweighted least squares is
minimised by predicting nearly zero everywhere: it scores well on the thousands of dry steps and
gives up the handful of deep ones, which are the only steps anybody looks at. Measured, that fit
reached a respectable 6.8 cm RMSE and a CSI at 30 cm of **0.02** - it had the pattern (peak
correlation 0.98) and a third of the amplitude.

Weighting each step by ``1 + depth/5`` makes a 50 cm step count eleven dry ones. SPEC.md 11.7
anticipates exactly this for the GNN, whose loss carries an "exceedance-weighted term"."""


def _ponding(response: NDArray[np.floating], gain: float, drain: float) -> NDArray[np.floating]:
    """The depth series the emulator would actually produce, floor and all.

    The same recursion :func:`simulate` runs. The fit used to score a closed form instead -
    ``max(gain * cumsum(response) - drain * t, 0)`` - which is a different model: flooring a
    cumulative sum carries a drainage debt forward, while flooring each step forgets it the
    moment the street runs dry. Fitting one model and running another is its own bug, and it
    biased every parameter it touched.
    """
    out = np.empty(response.size)
    ponded = 0.0
    for step in range(response.size):
        ponded = max(ponded + gain * response[step] - drain, 0.0)
        out[step] = ponded
    return out


def _fit_one(
    target_cm: NDArray[np.floating], rain_mm_h: NDArray[np.floating]
) -> tuple[float, float, float]:
    """Fit ``(k, gain, drain)`` for one segment by grid search over the storage coefficient.

    Grid rather than a gradient method: the objective is not convex in ``k``, the parameter is
    one-dimensional and bounded, and a few hundred evaluations of a 36-step recursion is
    microseconds. At each ``k`` the two linear parameters come from a weighted least squares on
    the unfloored model - which is where they can be solved in closed form - and the candidate is
    then *scored* with the real recursion, so the parameters that win are the ones that make the
    emulator right rather than the ones that make the algebra tidy.
    """
    weights = 1.0 + np.maximum(target_cm, 0.0) / DEPTH_WEIGHT_CM
    best = (DEFAULT_K, 0.0, 0.0, np.inf)

    for k in np.linspace(K_BOUNDS[0], K_BOUNDS[1], K_FIT_GRID):
        # Run the cascade with unit gain, so the response is a basis the linear fit scales.
        s1 = s2 = 0.0
        response = np.empty(rain_mm_h.size)
        for step in range(rain_mm_h.size):
            out1 = s1 / k
            s1 = s1 + rain_mm_h[step] - out1
            out2 = s2 / k
            s2 = s2 + out1 - out2
            response[step] = out2

        cumulative = np.cumsum(response)
        steps = np.arange(1.0, rain_mm_h.size + 1.0)
        design = np.column_stack([cumulative, -steps]) * weights[:, None]
        try:
            coefficients, *_ = np.linalg.lstsq(design, target_cm * weights, rcond=None)
        except np.linalg.LinAlgError:  # pragma: no cover - a degenerate segment
            continue

        gain0 = max(float(coefficients[0]), 0.0)
        drain0 = max(float(coefficients[1]), 0.0)
        if gain0 <= 0.0:
            continue

        # A short refinement around the closed-form answer, scored on the true recursion. The
        # closed form is a good starting point and a poor endpoint, because it never sees the
        # floor.
        # The upper end is wide on purpose. The closed form is fitted without the floor, and a
        # street that drains dry between bursts needs a larger gain than the unfloored algebra
        # asks for; capping the search at about twice the closed form left the emulator with the
        # right map and a third of the amplitude.
        for gain_scale in (0.6, 1.0, 1.5, 2.2, 3.2, 4.5, 6.0):
            for drain_scale in (0.0, 0.5, 1.0, 1.5):
                gain = gain0 * gain_scale
                drain = drain0 * drain_scale
                predicted = _ponding(response, gain, drain)
                error = float(np.mean(weights * (predicted - target_cm) ** 2))
                if error < best[3]:
                    best = (float(k), gain, drain, error)

    return best[0], best[1], best[2]


def fit(
    training: list[tuple[NDArray[np.floating], NDArray[np.floating], NDArray[np.floating]]],
    segment_ids: tuple[str, ...],
    *,
    beta_ref: NDArray[np.floating],
    holdout: int = 2,
    min_wet_cm: float = 2.0,
) -> FlashModel:
    """Fit the emulator to Twin runs and measure it on runs it never saw.

    Args:
        training: ``(depth_cm, rain_mm_h, beta)`` per run. ``depth_cm`` is
            ``(steps, segments)``; ``rain_mm_h`` is per segment too, or ``(steps,)`` for a
            spatially uniform storm.
        segment_ids: the segment order every array is in.
        beta_ref: the blockage the fitted drainage term refers to.
        holdout: runs kept back for the skill numbers. They are the *last* runs in the list,
            which by construction are the heaviest storms - the honest direction to hold out in,
            because a model that only saw drizzle should be judged on a downpour.
        min_wet_cm: a segment needs a step this deep in training to be fitted at all.
    """
    if len(training) <= holdout:
        msg = f"need more than {holdout} runs to fit and hold out; got {len(training)}"
        raise ValueError(msg)

    fit_runs = training[:-holdout]
    test_runs = training[-holdout:]
    n_segments = len(segment_ids)

    # The base state: what every fitting storm produced whatever its intensity. Taken from the
    # fitting runs only - measuring it over the holdout too would be leakage, and the holdout
    # numbers below are the ones anybody should be reading.
    baseline = np.min([d for d, _, _ in fit_runs], axis=0)

    k = np.full(n_segments, DEFAULT_K)
    gain = np.zeros(n_segments)
    drain = np.zeros(n_segments)

    # The deepest training run decides which segments are worth fitting: a segment that never
    # gets wet has no curve to fit and would otherwise pull a meaningless k out of noise.
    # Fit on the deviation, which is the only part a rain cascade can explain.
    deviations = [np.maximum(d - baseline[: d.shape[0]], 0.0) for d, _, _ in fit_runs]
    peak_over_runs = np.max([d.max(axis=0) for d in deviations], axis=0)
    fittable = np.flatnonzero(peak_over_runs >= min_wet_cm)

    for index in fittable:
        # Concatenate the runs so one k has to explain every storm, which is what stops the fit
        # memorising a single hyetograph. Each run contributes this segment's own rain, so the
        # gain being fitted is a property of the catchment rather than of where the storm sat.
        target = np.concatenate([d[:, index] for d in deviations])
        rain = np.concatenate([(r[:, index] if r.ndim == 2 else r) for _, r, _ in fit_runs])
        k[index], gain[index], drain[index] = _fit_one(target, rain)

    model = FlashModel(
        segment_ids=segment_ids,
        k_steps=k,
        gain=gain,
        drain_cm_per_step=drain,
        beta_ref=np.asarray(beta_ref, dtype=np.float64),
        baseline_cm=baseline,
        n_training_runs=len(fit_runs),
        rmse_cm=0.0,
        csi_30cm=0.0,
        fitted_segments=int(fittable.size),
    )

    rmse, csi = score(model, test_runs)
    scored = FlashModel(
        segment_ids=model.segment_ids,
        k_steps=model.k_steps,
        gain=model.gain,
        drain_cm_per_step=model.drain_cm_per_step,
        beta_ref=model.beta_ref,
        baseline_cm=model.baseline_cm,
        n_training_runs=model.n_training_runs,
        rmse_cm=rmse,
        csi_30cm=csi,
        fitted_segments=model.fitted_segments,
    )
    log.info(
        "flash.fitted",
        segments=n_segments,
        fitted=int(fittable.size),
        train_runs=len(fit_runs),
        holdout_runs=len(test_runs),
        rmse_cm=round(rmse, 2),
        csi_30cm=round(csi, 3),
    )
    return scored


def score(
    model: FlashModel,
    runs: list[tuple[NDArray[np.floating], NDArray[np.floating], NDArray[np.floating]]],
) -> tuple[float, float]:
    """RMSE in cm and CSI at 30 cm against the Twin, over held-out runs.

    CSI is scored on segments that are wet in *either* the Twin or the emulator, which is the
    right denominator: the 21,296-segment city is mostly dry in every storm, and counting those
    agreements would make any model look excellent.
    """
    errors: list[NDArray[np.floating]] = []
    hits = misses = false_alarms = 0

    for depth, rain, beta in runs:
        predicted = simulate(model, rain, beta=beta)
        steps = min(predicted.shape[0], depth.shape[0])
        errors.append((predicted[:steps] - depth[:steps]).ravel())

        truth_wet = depth[:steps] > 30.0
        model_wet = predicted[:steps] > 30.0
        hits += int(np.sum(truth_wet & model_wet))
        misses += int(np.sum(truth_wet & ~model_wet))
        false_alarms += int(np.sum(~truth_wet & model_wet))

    residual = np.concatenate(errors) if errors else np.zeros(1)
    rmse = float(np.sqrt(np.mean(residual**2)))
    denominator = hits + misses + false_alarms
    csi = float(hits / denominator) if denominator else 0.0
    return rmse, csi


def save(model: FlashModel, path: Path) -> None:
    """Persist the fit, plus the skill it was measured at."""
    np.savez_compressed(
        path,
        segment_ids=np.array(model.segment_ids),
        k_steps=model.k_steps,
        gain=model.gain,
        drain_cm_per_step=model.drain_cm_per_step,
        beta_ref=model.beta_ref,
        baseline_cm=model.baseline_cm.astype(np.float32),
        meta=np.array(
            json.dumps(
                {
                    "n_training_runs": model.n_training_runs,
                    "rmse_cm": model.rmse_cm,
                    "csi_30cm": model.csi_30cm,
                    "fitted_segments": model.fitted_segments,
                }
            )
        ),
    )


def load(path: Path) -> FlashModel:
    """Read a fitted emulator."""
    blob = np.load(path, allow_pickle=False)
    meta = json.loads(str(blob["meta"]))
    return FlashModel(
        segment_ids=tuple(str(s) for s in blob["segment_ids"]),
        k_steps=blob["k_steps"],
        gain=blob["gain"],
        drain_cm_per_step=blob["drain_cm_per_step"],
        beta_ref=blob["beta_ref"],
        baseline_cm=blob["baseline_cm"].astype(np.float64),
        n_training_runs=int(meta["n_training_runs"]),
        rmse_cm=float(meta["rmse_cm"]),
        csi_30cm=float(meta["csi_30cm"]),
        fitted_segments=int(meta["fitted_segments"]),
    )

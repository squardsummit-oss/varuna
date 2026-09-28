"""The street ensemble SPEC.md 11.7 asks for, and an honest account of where its width comes
from (task P7.6).

Section 11.7 words it in one sentence: "50 members = 20 Sky members x parameter draws from the
Pulse posterior (with replacement) -> per-segment quantiles and exceedance probabilities; MC
noise on ``k_u`` for structural spread". Until this module the cycle shipped twenty - one
emulator run per Sky rain member, every one of them at the blockage the fit refers to
(``FlashModel.beta_ref``, which is **zero on every segment** of the shipped fit) - so both
parameter axes were missing and the member count was the count of weather futures rather than of
street futures.

Two things were measured before any of it was built, because a fifty-member ensemble whose extra
thirty members carry no information is twenty members wearing fifty labels. Both were measured
again through this module once it existed, which is the version quoted here.

**1. The Pulse draws move almost nothing, and the ensemble has to say so.** :func:`build_members`
on the 2 July 08:40 cycle (run ``MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked``, its own
``drain_health.geojson`` joined onto segments, 21,296 segments of which 7,646 wet, 18 python
processes on an Intel i5-1155G7) reports, as the mean ``p90 - p10`` of peak depth over the wet
segments::

    50 members  band 16.34 cm   Sky alone 9.87 cm   parameters alone 8.87 cm
    60 members  band 16.02 cm   Sky alone 9.87 cm   parameters alone 8.88 cm

and the same ensembles with the blockage draw switched off read 16.48 cm and 15.99 cm - which is
**-0.14 cm and +0.03 cm**, a contribution that lands on either side of zero and is therefore
smaller than the ensembles' own sampling noise. Split further in the same harness, the parameter
axis is 8.35 cm of ``k_u`` against 0.65 cm of blockage. Nothing about that is a defect of Pulse:
0.65 cm of independent spread entering in quadrature against 16 cm simply disappears.

The draws are still made, because 11.7 asks for them and they cost about 0.3 s; what changed is
that the product no longer implies they are what the band is made of. :class:`SpreadReport`
carries the decomposition **per cycle**, measured on that cycle rather than quoted from this
docstring, and :func:`spread_note` turns it into the sentence the console prints. The width on
screen is the weather's and the emulator's own storage coefficient. Pulse sets the level of the
drainage term, not the width of the band.

**2. Sampling the weather with replacement makes the answer depend on the seed.** 11.7 says
"with replacement", and drawing 50 Sky indices from 20 that way is unbiased - but at n = 50 its
variance is not small. Five parameter seeds on the same 08:40 cycle, in a standalone harness that
held the wet mask and everything else fixed and varied only the allocation::

    with replacement, rng.integers(20, size=50)  mean 12.8420 cm  spread 3.7715 cm (29.4 %)
    exhaustive, m % 20, 50 members               mean 13.5860 cm  spread 0.0552 cm  (0.4 %)
    exhaustive, m % 20, 60 members (20 x 3)      mean 12.7764 cm  spread 0.0734 cm  (0.6 %)

Twenty-nine per cent of the reported spread was the random number generator choosing which
weather to over-represent. Resampling cannot add weather information that twenty members do not
contain; it can only add noise to the quantiles it exists to sharpen. So this module covers the
Sky axis **exhaustively** - member ``m`` takes Sky member ``m % n_sky`` - and draws only the
parameters. Rule 8 holds either way: one seeded generator and nothing else, and two builds at one
seed were checked array-equal.

That harness's absolute bands are lower than the 16.34 cm :func:`build_members` reports on the
same cycle, and its wet mask counted 5,641 segments where this module counts 7,646. The two are
the same expression on paper and the difference was not reconciled before the machine ran out of
memory under other load, so the table above is quoted for the **ratios** it was run to measure -
which are what the decision turned on - and not for its levels.

**The cost of 50 not dividing 20.** With ``n_members = 50`` over twenty Sky members, ten of them
carry three ensemble members and ten carry two, so the weather is weighted 0.06 against 0.04.
Measured through this module on the 08:40 cycle, 50 members read 16.34 cm where a balanced
20 x 3 = 60 read 16.02 cm - 2.0 % wider, and that extra width is an artefact of the allocation
rather than of the forecast. :data:`BALANCED_HINT` says so in the run's own notes whenever
``n_members`` is not a multiple of ``n_sky``, and ``n_members=60`` removes it.

**What it costs.** 50 members with the decomposition measured 5.9 s and 60 measured 7.1 s on the
Mumbai grid with 18 python processes, against a cycle that takes 60-200 s. The stack is 153 MB at
fifty members and 184 MB at sixty, and ``varuna_products.depth._member_levels`` copies it once.

**What this module deliberately does not do.** It does not join Pulse's posterior onto road
segments: ``varuna_flash`` does not depend on ``varuna_pulse`` and should not start
(``varuna_pulse.join.segment_beta`` is that join, and the cycle already imports it). It takes the
per-segment mean and spread as arrays, exactly as :func:`varuna_flash.model.draw_members` does,
and counts the segments whose blockage it had to substitute rather than absorbing them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import structlog

from varuna_flash.model import K_LOG_SD, draw_members, simulate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from numpy.typing import NDArray

    from varuna_flash.model import FlashModel

log = structlog.get_logger("varuna.flash.ensemble")

__all__ = [
    "BALANCED_HINT",
    "DEFAULT_MEMBERS",
    "PRIOR_BETA",
    "WET_THRESHOLD_CM",
    "EnsembleResult",
    "SpreadReport",
    "build_members",
    "spread_note",
]

DEFAULT_MEMBERS = 50
"""Members SPEC.md 11.7 asks for. Not a multiple of 20; see :data:`BALANCED_HINT`."""

PRIOR_BETA = 0.20
"""Blockage given to a segment whose posterior and prior are both NaN.

SPEC.md 10.1 step 7 sets the prior's mean by land use - 0.15 arterial, 0.2 residential, 0.35
markets - so this is the residential middle, and it is the same constant
``varuna_api.routers.whatif`` falls back to, for the same reason. It is a substitution and not a
measurement, which is why :attr:`EnsembleResult.n_flat_beta` counts it and the note names the
count: 5,543 of Mumbai's 21,296 segments have no inlet link at all and reach the emulator no
other way."""

WET_THRESHOLD_CM = 5.0
"""Peak depth below which a segment is damp rather than wet, and is left out of the band average.

The same 5 cm ``varuna_products.depth.WET_THRESHOLD_CM`` uses and the same depth the ramp's
``--depth-dry`` band ends at. Averaging the band over all 21,296 segments instead would divide a
real spread by the fifteen thousand streets that stayed dry and report a fifth of it."""

BALANCED_HINT = (
    "{extra} of the {n_sky} Sky members carry {high} ensemble members each and {rest} carry "
    "{low}, because {n_members} is not a multiple of {n_sky}; the weather is therefore weighted "
    "{w_high:.3f} against {w_low:.3f}. Measured on the 2 July 08:40 cycle that unbalanced "
    "allocation reads a 2.0 % wider band than a balanced 20 x 3, so part of the width is the "
    "allocation. Build {balanced} members for an ensemble with no such artefact in it."
)
"""Said whenever ``n_members`` is not a multiple of ``n_sky``, filled with this cycle's numbers."""


@dataclass(frozen=True, slots=True)
class SpreadReport:
    """How wide the ensemble is, and how much of that width each axis is responsible for.

    Every band is a mean of ``p90 - p10`` of **peak** depth in cm over the segments this cycle
    actually wets, measured on this cycle rather than carried from the module docstring (rule 6).

    A decomposition is not a variance partition - the axes interact through the ponding
    maximum - so the three single-axis bands do not add up to :attr:`band_cm`. The comparison
    that does answer "is the Pulse axis worth anything here" is :attr:`band_cm` against
    :attr:`band_without_blockage_cm`, because that is the same ensemble with one axis switched
    off and everything else, seeds included, held.
    """

    n_members: int
    n_sky: int
    wet_segments: int
    band_cm: float
    """The shipped ensemble's own band, every axis varying."""

    band_sky_only_cm: float
    """Weather alone: one member per Sky member, both parameters at their centre."""

    band_params_only_cm: float
    """Parameters alone: the Sky-mean hyetograph, blockage and ``k`` drawn."""

    band_without_blockage_cm: float
    """Everything except the blockage draw, so the Pulse axis can be priced by subtraction."""

    blockage_share: float
    """``(band_cm - band_without_blockage_cm) / band_cm``.

    Negative is possible and is not a defect: the two ensembles are different Monte Carlo
    realisations, so a contribution smaller than the sampling noise can land either side of
    zero. Measured at -0.017 on the 2 July 08:40 cycle, which is this module's whole finding in
    one number."""

    ms: int
    """Wall clock of :func:`build_members`, decomposition included."""


@dataclass(frozen=True, slots=True)
class EnsembleResult:
    """The member stack the products consume, with the provenance of its width."""

    depth_cm: NDArray[np.float32]
    """``(n_members, n_steps, n_segments)`` absolute emulator depth in cm.

    ``varuna_products.depth.segment_forecast`` takes only each member's deviation from the member
    mean, so the level here never reaches a screen (ADR-0025); the band does."""

    sky_index: NDArray[np.int64]
    """Which Sky rain member each ensemble member ran on. Exhaustive, ``m % n_sky``."""

    n_flat_beta: int
    """Segments whose blockage was neither learned nor inherited and took :data:`PRIOR_BETA`."""

    n_resolved_beta: int
    """Segments that arrived with a finite blockage from the caller's join."""

    spread: SpreadReport | None
    """``None`` when ``decompose=False``; the caller then has no per-cycle width to quote, and
    must not quote this module's docstring instead."""

    notes: tuple[str, ...]

    @property
    def n_members(self) -> int:
        return int(self.depth_cm.shape[0])

    @property
    def n_steps(self) -> int:
        return int(self.depth_cm.shape[1])


def _peaks(stack: NDArray[np.floating]) -> NDArray[np.floating]:
    """``(n_members, n_segments)`` peak depth per member, which is what a band is quoted on."""
    return stack.max(axis=1)


def _band(peaks: NDArray[np.floating], wet: NDArray[np.bool_]) -> float:
    """Mean ``p90 - p10`` of peak depth over the wet segments, in cm."""
    if not wet.any() or peaks.shape[0] < 2:
        return 0.0
    p10, p90 = np.percentile(peaks, [10.0, 90.0], axis=0)
    return float((p90 - p10)[wet].mean())


def build_members(
    model: FlashModel,
    hyetographs: NDArray[np.floating],
    beta_mean: NDArray[np.floating],
    beta_sd: NDArray[np.floating],
    *,
    n_members: int = DEFAULT_MEMBERS,
    n_steps: int | None = None,
    seed: int = 2019,
    decompose: bool = True,
) -> EnsembleResult:
    """Run the street ensemble and measure where its width came from.

    Args:
        model: the fitted emulator, whose ``segment_ids`` fix the segment axis of everything else.
        hyetographs: ``(n_sky, n_steps)`` AOI-mean rain per Sky member in mm/h - the only
            per-member rain a cycle keeps (``varuna_sky.products.aoi_hyetographs``). The members
            therefore differ in the *amplitude* of the rain and not in where the cell sits, which
            is a limitation of the construction rather than of the fit, and is repeated in the
            notes so nobody reads the band as the ensemble's disagreement about the map.
        beta_mean, beta_sd: Pulse's posterior per **road segment** in ``model.segment_ids``
            order, NaN where the join found no pipe. ``varuna_pulse.join.segment_beta`` produces
            them; this package does not import it (see the module docstring).
        n_members: how many street members to build. 50 is what 11.7 asks for; any multiple of
            ``n_sky`` removes the allocation artefact :data:`BALANCED_HINT` describes.
        n_steps: truncate to this many steps, for a Twin horizon shorter than Sky's.
        seed: the single generator behind every draw (rule 8).
        decompose: also run the three reference ensembles that price each axis - about
            ``3 * n_sky + n_members`` extra emulator runs. Measured at 1.5-2.5 s for Mumbai
            against a cycle that takes 60-200 s, so it is on by default: a band nobody can
            attribute is a number the console cannot honestly label.

    Returns:
        :class:`EnsembleResult`.

    Raises:
        ValueError: if ``hyetographs`` is not a non-empty 2-D array, if ``n_members`` is below
            one, or if the blockage arrays are not one value per segment of ``model``.
    """
    from time import perf_counter

    started = perf_counter()
    rain = np.asarray(hyetographs, dtype=np.float64)
    if rain.ndim != 2 or rain.size == 0:
        msg = (
            "hyetographs must be (n_sky, n_steps) of AOI-mean rain in mm/h and non-empty; got "
            f"shape {rain.shape}. A cycle with no per-member hyetographs has no ensemble to "
            "build and should keep ensemble_n at 1 rather than call this."
        )
        raise ValueError(msg)
    if n_members < 1:
        msg = f"need at least one ensemble member; got n_members={n_members}"
        raise ValueError(msg)
    n_sky = int(rain.shape[0])
    steps = int(rain.shape[1]) if n_steps is None else min(int(rain.shape[1]), int(n_steps))
    rain = rain[:, :steps]

    mean_in = np.asarray(beta_mean, dtype=np.float64)
    sd_in = np.asarray(beta_sd, dtype=np.float64)
    if mean_in.shape != (model.n_segments,) or sd_in.shape != (model.n_segments,):
        msg = (
            f"blockage must be one value per segment, {model.n_segments} of them; got mean "
            f"{mean_in.shape} and sd {sd_in.shape}. Join Pulse's per-edge posterior onto "
            "segments with varuna_pulse.join.segment_beta first."
        )
        raise ValueError(msg)

    # The substitution `draw_members` deliberately refuses to make, made here and counted. A NaN
    # blockage propagates into a NaN depth and out into the quantiles the map colours streets by,
    # so somebody has to decide; 11.7's caller is this function, and the count travels with it.
    resolved = np.isfinite(mean_in)
    centre = np.where(resolved, mean_in, PRIOR_BETA)
    sd = np.where(np.isfinite(sd_in) & resolved, sd_in, 0.0)
    n_flat = int((~resolved).sum())

    # Only the parameter draws are taken from `draw_members`; its `sky_index` is the
    # with-replacement sampling the module docstring prices at 29.4 % seed noise, and the
    # exhaustive allocation replaces it.
    _, beta, k = draw_members(model, centre, sd, n_members=n_members, n_sky=n_sky, seed=seed)
    sky_index = np.arange(n_members, dtype=np.int64) % n_sky

    # float32: 50 x 36 x 21,296 is 306 MB in float64 and half that here, and these are
    # centimetres of water read to two decimals.
    members = np.stack(
        [
            simulate(model, rain[sky_index[m]], beta=beta[m], k_steps=k[m]).astype(np.float32)
            for m in range(n_members)
        ]
    )

    notes: list[str] = [
        f"Street ensemble: {n_members} members over {n_sky} Sky rain members through the "
        f"reduced-order emulator (held-out RMSE {model.rmse_cm:.1f} cm, CSI "
        f"{model.csi_30cm:.2f} at 30 cm on Twin runs it never saw), each member at its own draw "
        "of the pipe blockage from Pulse's posterior and of the emulator's storage coefficient.",
        "Every Sky member is used as often as the count allows rather than resampled with "
        "replacement: resampling 50 from 20 moved the reported band by 29.4 % between seeds on "
        "the 2 July 08:40 cycle, which is the generator's choice and not the forecast's.",
        "The members differ only in the amplitude of the AOI-mean rain, because that is the only "
        "per-member rain a cycle keeps, so the spread is amplitude-driven and carries none of "
        "the ensemble's disagreement about where the cell sits.",
    ]
    if n_members % n_sky:
        extra = n_members % n_sky
        high, low = n_members // n_sky + 1, n_members // n_sky
        notes.append(
            BALANCED_HINT.format(
                extra=extra,
                n_sky=n_sky,
                high=high,
                rest=n_sky - extra,
                low=low,
                n_members=n_members,
                w_high=high / n_members,
                w_low=low / n_members,
                balanced=n_sky * high,
            )
        )
    if n_flat:
        notes.append(
            f"{n_flat:,} of {model.n_segments:,} segments have no pipe in the inferred drain "
            f"graph to inherit a blockage from and ran at a flat {PRIOR_BETA}; that is a "
            "substitution, not something Pulse measured."
        )

    report: SpreadReport | None = None
    if decompose:
        # Wet is decided once, on the centre run, so every reference ensemble is averaged over
        # the same segments. Deciding it per ensemble would compare bands over different streets.
        wet = simulate(model, rain.mean(axis=0), beta=centre).max(axis=0) > WET_THRESHOLD_CM
        mean_rain = rain.mean(axis=0)
        sky_only = np.stack(
            [simulate(model, rain[m], beta=centre).max(axis=0) for m in range(n_sky)]
        )
        params_only = np.stack(
            [
                simulate(model, mean_rain, beta=beta[m], k_steps=k[m]).max(axis=0)
                for m in range(n_members)
            ]
        )
        # The same members with the blockage held at its posterior mean: same k draws, same
        # weather, one axis off. Anything else would compare two different experiments.
        without_beta = np.stack(
            [
                simulate(model, rain[sky_index[m]], beta=centre, k_steps=k[m]).max(axis=0)
                for m in range(n_members)
            ]
        )
        band = _band(_peaks(members.astype(np.float64)), wet)
        without = _band(without_beta, wet)
        report = SpreadReport(
            n_members=n_members,
            n_sky=n_sky,
            wet_segments=int(wet.sum()),
            band_cm=round(band, 4),
            band_sky_only_cm=round(_band(sky_only, wet), 4),
            band_params_only_cm=round(_band(params_only, wet), 4),
            band_without_blockage_cm=round(without, 4),
            blockage_share=round((band - without) / band, 4) if band > 0 else 0.0,
            ms=round((perf_counter() - started) * 1000.0),
        )
        notes.append(spread_note(report))

    log.info(
        "flash.ensemble.built",
        members=n_members,
        sky_members=n_sky,
        steps=steps,
        segments=model.n_segments,
        seed=seed,
        flat_beta=n_flat,
        peak_cm=round(float(members.max()), 1),
        band_cm=None if report is None else report.band_cm,
        blockage_share=None if report is None else report.blockage_share,
        ms=round((perf_counter() - started) * 1000.0),
    )
    return EnsembleResult(
        depth_cm=members,
        sky_index=sky_index,
        n_flat_beta=n_flat,
        n_resolved_beta=int(resolved.sum()),
        spread=report,
        notes=tuple(notes),
    )


def spread_note(report: SpreadReport) -> str:
    """One sentence naming what the band is made of, in this cycle's own numbers (SPEC.md 6.8).

    Written as a comparison of measured bands rather than as a share of variance, because the
    axes interact and a percentage would imply a partition that does not exist. The sentence
    exists so that a fifty-member probability layer never lets a reader conclude that the learned
    drain state is what widened it.
    """
    pulse = report.band_cm - report.band_without_blockage_cm
    if abs(pulse) < 0.1 * report.band_cm:
        verdict = (
            f"switching it off changes the band by {pulse:+.2f} cm, which is inside this "
            "ensemble's own sampling noise, so the width on screen is the weather's and the "
            f"emulator's own storage coefficient (log-space MC noise {K_LOG_SD}) and not Pulse's"
        )
    else:
        verdict = (
            f"switching it off narrows the band by {pulse:.2f} cm, so the learned drain state is "
            "part of what the width means"
        )
    return (
        f"Ensemble band (mean p90 - p10 of peak depth over {report.wet_segments:,} wet "
        f"segments): {report.band_cm:.2f} cm from all {report.n_members} members, "
        f"{report.band_sky_only_cm:.2f} cm from the {report.n_sky} Sky rain members alone and "
        f"{report.band_params_only_cm:.2f} cm from the parameter draws alone. The blockage draw "
        f"is the smallest axis: {verdict}."
    )

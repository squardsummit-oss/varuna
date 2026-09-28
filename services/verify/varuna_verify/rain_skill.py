"""Rain skill against the bundle's truth field (SPEC.md 11.12, task P3.6).

SPEC.md 11.12 asks for "rain CSI at 20/40 mm/h by lead versus truth/gauges", and 7.10 draws
it as the skill-versus-lead-time chart on ``/verify`` - the chart where "confidence decays
after 90 minutes" has to be visible rather than asserted. This module computes exactly that,
and the two companions a probabilistic forecast owes the same chart:

* per lead time and per threshold, the contingency table of the **ensemble median** against
  the truth field, and from it CSI, POD and FAR;
* per lead time and per threshold, the **Brier score** of the ensemble exceedance probability
  against the binary truth;
* per lead time, the **mean absolute error** of the ensemble median in mm/h.

Definitions, fixed here so no caller has to guess. Write ``f`` for the ensemble median rain
rate at a pixel, ``p`` for the fraction of members exceeding the threshold, ``o`` for the
truth, and ``T`` for the threshold. A pixel is a *hit* when ``f > T`` and ``o > T``, a *miss*
when ``f <= T < o``, a *false alarm* when ``o <= T < f``, a *correct negative* when neither
exceeds. The comparison is strictly greater than, matching the ``P(> 20 mm/h)`` products of
SPEC.md 11.1 step 6. Then

* ``CSI = H / (H + M + F)`` - critical success index, 1 for a perfect forecast, 0 when
  nothing that happened was forecast;
* ``POD = H / (H + M)`` - probability of detection;
* ``FAR = F / (H + F)`` - false-alarm ratio, so 0 is the good end;
* ``Brier = mean((p - 1[o > T])^2)`` over the scored pixels, 0 for a perfect deterministic
  forecast;
* ``MAE = mean(|f - o|)`` in mm/h.

A pixel is scored only where both fields are finite, so the radar's coverage mask
(SPEC.md 11.1 step 1 leaves what the radar cannot see as ``nan``, never a sentinel) narrows
the sample rather than counting as a dry forecast. Each lead reports how many pixels it
actually scored in :attr:`LeadSkill.n`.

Each score is ``None`` rather than a number when its denominator is empty, the same
convention ``varuna_schemas.models.verification.ContingencyTable`` uses: a lead time with no
event forecast and none observed has no false-alarm ratio, and inventing one would be a
fabricated number (rule 6).

**Alignment.** The truth cube and the forecast share the Sky grid (SPEC.md 10.2: both cubes
a bundle carries live on the storm domain), so alignment is by valid time only, and the grids
are checked rather than assumed. A forecast whose steps reach past the end of the truth cube
is scored on the steps that do overlap and reports how many it could not score, in
:attr:`RainSkillReport.n_leads_unmatched` and in its notes - never silently. No overlap at all
raises :class:`TimeAxisMismatchError`, and so does a grid that is not the truth's grid.

**Honesty (rules 6 and 7).** ``truth/rain.zarr`` exists only for synthetic bundles: it is the
storm designer's own field, a reconstruction, not a measurement. Every report carries that
label in :attr:`RainSkillReport.notes` and names the artifact it scored against, so the
``/verify`` chart can say what "truth" meant.

Determinism (rule 8): nothing here is random. The same cubes give the same integers.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog
from varuna_schemas.constants import IST, STEP_MIN
from varuna_schemas.models.verification import SkillByLead
from varuna_sky.types import RadarGrid, RainEnsemble, SkyProducts

if TYPE_CHECKING:  # pragma: no cover - numpy stays out of the runtime type surface
    from numpy.typing import NDArray

log = structlog.get_logger("varuna.verify.rain_skill")

__all__ = [
    "GridMismatchError",
    "LeadSkill",
    "RainSkillReport",
    "ThresholdSkill",
    "TimeAxisMismatchError",
    "TruthCube",
    "load_truth_cube",
    "rain_skill",
    "rain_skill_from_products",
]

# ----------------------------------------------------------------------- thresholds
SKILL_THRESHOLDS_MM_H: tuple[float, float] = (20.0, 40.0)
"""Rain rates the skill-versus-lead chart is scored at (SPEC.md 11.12)."""

SCHEMA_THRESHOLDS_MM_H: frozenset[int] = frozenset({20, 40})
"""The thresholds ``SkillByLead`` accepts; the chart on ``/verify`` draws these two only."""

# ----------------------------------------------------------------------- cube layout
TRUTH_VARIABLE = "rain"
"""Array name inside ``truth/rain.zarr`` (the bundle writer, SPEC.md 10.2)."""

TRUTH_SOURCE = "truth/rain.zarr"
"""What a report names as the field it scored against."""

TRUTH_NOTE = (
    "Scored against the bundle's reconstructed truth field, not a measurement: "
    "truth/rain.zarr is the storm designer's own rain field."
)
"""Honesty label carried on every report (rules 6 and 7; SPEC.md 10.2)."""

# ----------------------------------------------------------------------- tolerances
TIME_TOLERANCE_S = 1.0
"""How close two valid times must be to count as the same instant. Not a spec number: the
cube's time axis is minutes-since-t0 in float64, so a second of slack absorbs the round trip
without ever matching two different 5-minute steps."""

GRID_TOLERANCE_M = 1e-6
"""Metric slack when comparing two affine transforms. A design choice, not a spec number."""


# ============================================================================ errors
class TimeAxisMismatchError(ValueError):
    """The forecast and the truth cube share no valid time, so nothing can be scored."""


class GridMismatchError(ValueError):
    """The forecast and the truth cube are not on the same grid."""


# ============================================================================ inputs
@dataclass(frozen=True, slots=True)
class TruthCube:
    """The bundle's truth rain field: ``mm/h[t, y, x]`` every 5 minutes (SPEC.md 10.2).

    ``times`` holds one timezone-aware IST instant per frame, in the cube's own order, and
    ``grid`` is the storm domain the frames live on - the same grid Sky forecasts on.
    """

    rain_mm_h: NDArray[np.floating]
    times: tuple[datetime, ...]
    grid: RadarGrid
    source: str = TRUTH_SOURCE

    @property
    def n_frames(self) -> int:
        return int(self.rain_mm_h.shape[0])


# ============================================================================ scores
@dataclass(frozen=True, slots=True)
class ThresholdSkill:
    """One lead time's contingency table and Brier score at one rain-rate threshold."""

    threshold_mm_h: float
    hits: int
    misses: int
    false_alarms: int
    correct_negatives: int
    brier: float | None = None
    """Brier score of the ensemble exceedance probability; ``None`` when nothing was scored."""

    @property
    def n(self) -> int:
        """Pixels scored at this lead and threshold."""
        return self.hits + self.misses + self.false_alarms + self.correct_negatives

    @property
    def csi(self) -> float | None:
        """Critical success index = hits / (hits + misses + false alarms)."""
        denom = self.hits + self.misses + self.false_alarms
        return None if denom == 0 else self.hits / denom

    @property
    def pod(self) -> float | None:
        """Probability of detection = hits / (hits + misses)."""
        denom = self.hits + self.misses
        return None if denom == 0 else self.hits / denom

    @property
    def far(self) -> float | None:
        """False-alarm ratio = false alarms / (hits + false alarms)."""
        denom = self.hits + self.false_alarms
        return None if denom == 0 else self.false_alarms / denom

    def as_skill_by_lead(self, lead_min: int) -> SkillByLead:
        """The contract model the ``/verify`` chart reads (``SkillByLead``).

        Raises:
            ValueError: when the threshold is not one of the two the contract allows, so a
                score can never reach the chart mislabelled as 20 or 40 mm/h.
        """
        threshold = round(self.threshold_mm_h)
        if abs(self.threshold_mm_h - threshold) > 1e-9 or threshold not in SCHEMA_THRESHOLDS_MM_H:
            msg = (
                f"SkillByLead carries {sorted(SCHEMA_THRESHOLDS_MM_H)} mm/h only, "
                f"not {self.threshold_mm_h}"
            )
            raise ValueError(msg)
        return SkillByLead(
            lead_min=lead_min,
            threshold_mm_h=threshold,  # type: ignore[arg-type]
            csi=self.csi,
            pod=self.pod,
            far=self.far,
            n=self.n,
        )

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly form for ``verification.json``."""
        return {
            "threshold_mm_h": self.threshold_mm_h,
            "hits": self.hits,
            "misses": self.misses,
            "false_alarms": self.false_alarms,
            "correct_negatives": self.correct_negatives,
            "n": self.n,
            "csi": self.csi,
            "pod": self.pod,
            "far": self.far,
            "brier": self.brier,
        }


@dataclass(frozen=True, slots=True)
class LeadSkill:
    """Every score at one lead time: the median's error, and one table per threshold."""

    lead_min: int
    valid_ts: datetime
    n: int
    """Pixels scored: finite in both the forecast and the truth."""

    mae_mm_h: float | None
    thresholds: tuple[ThresholdSkill, ...]

    def at(self, threshold_mm_h: float) -> ThresholdSkill:
        """The table at one threshold.

        Raises:
            KeyError: when this lead was not scored at that threshold.
        """
        for score in self.thresholds:
            if abs(score.threshold_mm_h - threshold_mm_h) < 1e-9:
                return score
        msg = f"lead {self.lead_min} min was not scored at {threshold_mm_h} mm/h"
        raise KeyError(msg)

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly form for ``verification.json``."""
        return {
            "lead_min": self.lead_min,
            "valid_ts": self.valid_ts.isoformat(),
            "n": self.n,
            "mae_mm_h": self.mae_mm_h,
            "thresholds": [score.to_dict() for score in self.thresholds],
        }


@dataclass(frozen=True, slots=True)
class RainSkillReport:
    """One cycle's rain skill by lead time, ready to be written into a JSON product."""

    cycle_ts: datetime
    leads: tuple[LeadSkill, ...]
    thresholds_mm_h: tuple[float, ...]
    n_members: int
    truth_source: str
    n_leads_unmatched: int = 0
    """Forecast steps with no truth frame - reported, never silently dropped."""

    notes: tuple[str, ...] = ()

    @property
    def lead_minutes(self) -> tuple[int, ...]:
        """The lead-time axis of the chart, in minutes."""
        return tuple(lead.lead_min for lead in self.leads)

    @property
    def skill_by_lead(self) -> list[SkillByLead]:
        """The rows ``VerificationSummary.skill_by_lead`` expects, lead-major."""
        rows: list[SkillByLead] = []
        for lead in self.leads:
            rows.extend(score.as_skill_by_lead(lead.lead_min) for score in lead.thresholds)
        return rows

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly form for ``verification.json`` (SPEC.md 10.3)."""
        return {
            "cycle_ts": self.cycle_ts.isoformat(),
            "thresholds_mm_h": list(self.thresholds_mm_h),
            "n_members": self.n_members,
            "truth_source": self.truth_source,
            "n_leads_unmatched": self.n_leads_unmatched,
            "notes": list(self.notes),
            "leads": [lead.to_dict() for lead in self.leads],
        }


# ============================================================================ internals
@dataclass(frozen=True, slots=True)
class _Forecast:
    """What scoring needs from a forecast, whichever shape it arrived in."""

    median: NDArray[np.floating]
    """``(n_steps, n_px, n_px)`` ensemble median rain rate in mm/h."""

    probs: tuple[NDArray[np.floating], ...]
    """One ``(n_steps, n_px, n_px)`` exceedance probability per threshold, in the same order."""

    times: tuple[datetime, ...]
    grid: RadarGrid
    n_members: int


def _instant(ts: datetime) -> float:
    """POSIX seconds of a timezone-aware instant.

    Raises:
        ValueError: on a naive datetime, which would compare two clocks as if they were one.
    """
    if ts.tzinfo is None:
        msg = f"valid times must be timezone-aware (IST on the contract surface), got {ts!r}"
        raise ValueError(msg)
    return ts.timestamp()


def _check_grid(forecast: RadarGrid, truth: RadarGrid) -> None:
    """Insist the two grids are the same one before comparing pixel to pixel."""
    if forecast.shape != truth.shape:
        msg = f"forecast grid {forecast.shape} is not the truth grid {truth.shape}"
        raise GridMismatchError(msg)
    if abs(forecast.res_m - truth.res_m) > GRID_TOLERANCE_M:
        msg = f"forecast pixel {forecast.res_m} m is not the truth pixel {truth.res_m} m"
        raise GridMismatchError(msg)
    if forecast.crs != truth.crs:
        msg = f"forecast CRS {forecast.crs} is not the truth CRS {truth.crs}"
        raise GridMismatchError(msg)
    if not np.allclose(forecast.transform, truth.transform, atol=GRID_TOLERANCE_M):
        msg = (
            f"forecast transform {forecast.transform} is not the truth transform {truth.transform}"
        )
        raise GridMismatchError(msg)


def _match_times(
    forecast_times: Sequence[datetime], truth_times: Sequence[datetime]
) -> list[tuple[int, int]]:
    """Pair each forecast step with the truth frame at the same instant.

    Returns the ``(step, truth_frame)`` pairs in step order.

    Raises:
        TimeAxisMismatchError: when not one forecast step has a truth frame.
    """
    truth_instants = np.asarray([_instant(ts) for ts in truth_times], dtype=np.float64)
    order = np.argsort(truth_instants)
    sorted_instants = truth_instants[order]
    pairs: list[tuple[int, int]] = []
    for step, ts in enumerate(forecast_times):
        target = _instant(ts)
        position = int(np.searchsorted(sorted_instants, target))
        for candidate in (position - 1, position):
            if 0 <= candidate < sorted_instants.size:
                if abs(sorted_instants[candidate] - target) <= TIME_TOLERANCE_S:
                    pairs.append((step, int(order[candidate])))
                    break
    if not pairs:
        msg = (
            "the forecast and the truth cube share no valid time: forecast "
            f"{forecast_times[0].isoformat()}..{forecast_times[-1].isoformat()}, truth "
            f"{min(truth_times).isoformat()}..{max(truth_times).isoformat()}"
        )
        raise TimeAxisMismatchError(msg)
    return pairs


def _score_step(
    median: NDArray[np.floating],
    probs: Sequence[NDArray[np.floating]],
    truth: NDArray[np.floating],
    thresholds: Sequence[float],
    *,
    lead_min: int,
    valid_ts: datetime,
) -> LeadSkill:
    """Score one lead time: MAE of the median, then a table and a Brier score per threshold."""
    valid = np.isfinite(median) & np.isfinite(truth)
    n = int(valid.sum())
    if n == 0:
        empty = tuple(
            ThresholdSkill(float(t), hits=0, misses=0, false_alarms=0, correct_negatives=0)
            for t in thresholds
        )
        return LeadSkill(lead_min=lead_min, valid_ts=valid_ts, n=0, mae_mm_h=None, thresholds=empty)

    f = median[valid]
    o = truth[valid]
    mae = float(np.abs(f - o).mean())

    scores: list[ThresholdSkill] = []
    for threshold, prob in zip(thresholds, probs, strict=True):
        forecast_yes = f > threshold
        observed_yes = o > threshold
        hits = int(np.count_nonzero(forecast_yes & observed_yes))
        misses = int(np.count_nonzero(~forecast_yes & observed_yes))
        false_alarms = int(np.count_nonzero(forecast_yes & ~observed_yes))
        correct_negatives = int(np.count_nonzero(~forecast_yes & ~observed_yes))
        p = np.clip(prob[valid], 0.0, 1.0)
        brier = float(np.square(p - observed_yes.astype(np.float64)).mean())
        scores.append(
            ThresholdSkill(
                threshold_mm_h=float(threshold),
                hits=hits,
                misses=misses,
                false_alarms=false_alarms,
                correct_negatives=correct_negatives,
                brier=brier,
            )
        )
    return LeadSkill(
        lead_min=lead_min,
        valid_ts=valid_ts,
        n=n,
        mae_mm_h=mae,
        thresholds=tuple(scores),
    )


def _lead_minutes(valid_ts: datetime, cycle_ts: datetime) -> int:
    """Whole minutes from the cycle to a valid time.

    Raises:
        ValueError: on a negative lead, which would mean the cycle time is wrong: a forecast
            step is always after the cycle that produced it (SPEC.md 11.1 step 5).
    """
    minutes = (valid_ts - cycle_ts).total_seconds() / 60.0
    lead = round(minutes)
    if lead < 0:
        msg = (
            f"step valid at {valid_ts.isoformat()} precedes the cycle at "
            f"{cycle_ts.isoformat()}; a forecast step is never before its cycle"
        )
        raise ValueError(msg)
    return lead


def _infer_cycle_ts(times: Sequence[datetime]) -> datetime:
    """The cycle time implied by a step axis: one step before the first forecast instant."""
    if len(times) >= 2:
        step = times[1] - times[0]
    else:
        step = timedelta(minutes=STEP_MIN)
    return times[0] - step


def _score(
    forecast: _Forecast,
    truth: TruthCube,
    thresholds: Sequence[float],
    cycle_ts: datetime | None,
) -> RainSkillReport:
    """Align the two cubes by valid time and score every step that has a truth frame."""
    _check_grid(forecast.grid, truth.grid)
    if forecast.median.shape[0] != len(forecast.times):
        msg = f"{forecast.median.shape[0]} forecast steps but {len(forecast.times)} valid times"
        raise ValueError(msg)
    if truth.rain_mm_h.shape[0] != len(truth.times):
        msg = f"{truth.rain_mm_h.shape[0]} truth frames but {len(truth.times)} valid times"
        raise ValueError(msg)

    pairs = _match_times(forecast.times, truth.times)
    origin = cycle_ts if cycle_ts is not None else _infer_cycle_ts(forecast.times)

    leads = tuple(
        _score_step(
            np.asarray(forecast.median[step], dtype=np.float64),
            [np.asarray(prob[step], dtype=np.float64) for prob in forecast.probs],
            np.asarray(truth.rain_mm_h[frame], dtype=np.float64),
            thresholds,
            lead_min=_lead_minutes(forecast.times[step], origin),
            valid_ts=forecast.times[step],
        )
        for step, frame in pairs
    )

    unmatched = len(forecast.times) - len(pairs)
    notes = [TRUTH_NOTE]
    if unmatched:
        notes.append(
            f"{unmatched} of {len(forecast.times)} forecast steps reach past the truth cube "
            "and are not scored."
        )
    report = RainSkillReport(
        cycle_ts=origin,
        leads=leads,
        thresholds_mm_h=tuple(float(t) for t in thresholds),
        n_members=forecast.n_members,
        truth_source=truth.source,
        n_leads_unmatched=unmatched,
        notes=tuple(notes),
    )
    log.info(
        "verify.rain_skill",
        cycle_ts=origin.isoformat(),
        n_leads=len(leads),
        n_unmatched=unmatched,
        thresholds_mm_h=list(report.thresholds_mm_h),
    )
    return report


# ============================================================================ entry points
def rain_skill(
    ensemble: RainEnsemble,
    truth: TruthCube,
    *,
    thresholds_mm_h: Sequence[float] = SKILL_THRESHOLDS_MM_H,
    cycle_ts: datetime | None = None,
) -> RainSkillReport:
    """Score a Sky ensemble against the bundle's truth field (SPEC.md 11.12, P3.6).

    The ensemble median is the deterministic forecast the contingency tables score, and the
    fraction of members above each threshold is the probability the Brier score scores - the
    same two products SPEC.md 11.1 step 6 publishes.

    Args:
        ensemble: the 20-member, 3-hour forecast, ``(member, step, row, col)``.
        truth: the bundle's ``truth/rain.zarr``, on the same grid.
        thresholds_mm_h: rain rates to score at; 20 and 40 mm/h by default.
        cycle_ts: the cycle these steps forecast from, used for the lead-time axis. Inferred
            from the step spacing when omitted.

    Raises:
        TimeAxisMismatchError: when no forecast step has a truth frame.
        GridMismatchError: when the forecast is not on the truth's grid.
    """
    if not thresholds_mm_h:
        msg = "at least one threshold is needed to score rain skill"
        raise ValueError(msg)
    rain = np.asarray(ensemble.rain_mm_h, dtype=np.float64)
    if rain.ndim != 4:
        msg = f"an ensemble must be (member, step, row, col), got shape {rain.shape}"
        raise ValueError(msg)
    finite = np.isfinite(rain)
    n_finite = finite.sum(axis=0)
    covered = n_finite > 0
    with warnings.catch_warnings():
        # A pixel no member covers is the radar's coverage mask, not a fault: it stays nan and
        # is dropped from the sample below, so numpy's all-NaN notice is noise on 36 leads.
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(rain, axis=0)
    probs = tuple(
        np.divide(
            np.count_nonzero(finite & (rain > threshold), axis=0),
            n_finite,
            out=np.full(median.shape, np.nan, dtype=np.float64),
            where=covered,
        )
        for threshold in thresholds_mm_h
    )
    forecast = _Forecast(
        median=median,
        probs=probs,
        times=tuple(ensemble.times),
        grid=ensemble.grid,
        n_members=ensemble.n_members,
    )
    return _score(forecast, truth, thresholds_mm_h, cycle_ts)


def rain_skill_from_products(
    products: SkyProducts,
    truth: TruthCube,
    *,
    n_members: int = 0,
    cycle_ts: datetime | None = None,
) -> RainSkillReport:
    """Score the published Sky products instead of the raw ensemble.

    ``SkyProducts`` carries ``p50`` and the two exceedance fields ``p_gt_20`` and ``p_gt_40``,
    so this path scores exactly the numbers the console drew - at 20 and 40 mm/h only, which
    is what the ``/verify`` chart shows.
    """
    forecast = _Forecast(
        median=np.asarray(products.p50, dtype=np.float64),
        probs=(
            np.asarray(products.p_gt_20, dtype=np.float64),
            np.asarray(products.p_gt_40, dtype=np.float64),
        ),
        times=tuple(products.times),
        grid=products.grid,
        n_members=n_members,
    )
    return _score(forecast, truth, SKILL_THRESHOLDS_MM_H, cycle_ts)


# ============================================================================ loading
def load_truth_cube(path: Path, *, variable: str = TRUTH_VARIABLE) -> TruthCube:
    """Read ``truth/rain.zarr`` into memory with its grid and time axis.

    The layout is the bundle writer's (SPEC.md 10.2): a Zarr group holding the ``rain``
    array, a ``time_min`` axis of minutes since ``t0``, and the grid in the group attributes.

    Raises:
        FileNotFoundError: when there is no cube at ``path``.
        KeyError: when the group holds no such array.
    """
    import zarr

    if not path.exists():
        msg = f"No truth cube at {path}"
        raise FileNotFoundError(msg)
    group = zarr.open_group(str(path), mode="r")
    if variable not in list(group.array_keys()):
        msg = f"{path} has no array {variable!r}; found {sorted(group.array_keys())}"
        raise KeyError(msg)
    attrs: dict[str, Any] = dict(group.attrs)
    t0 = datetime.fromisoformat(str(attrs["t0"])).astimezone(IST)
    times_min = np.asarray(group["time_min"][:], dtype=np.float64)
    transform = tuple(float(v) for v in attrs["transform"])
    if len(transform) != 6:
        msg = f"{path} carries a {len(transform)}-coefficient transform, not an affine six"
        raise ValueError(msg)
    grid = RadarGrid(
        crs=str(attrs["crs"]),
        res_m=float(attrs["res_m"]),
        n_px=int(attrs["n_px"]),
        transform=transform,  # type: ignore[arg-type]
    )
    rain = np.asarray(group[variable][:], dtype=np.float32)
    times = tuple(t0 + timedelta(minutes=float(m)) for m in times_min.tolist())
    log.info("verify.truth_loaded", path=str(path), shape=list(rain.shape), n_times=len(times))
    return TruthCube(rain_mm_h=rain, times=times, grid=grid, source=TRUTH_SOURCE)

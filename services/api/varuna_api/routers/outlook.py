"""`GET /v1/outlook`: today's rain through the street emulator, for the next three hours.

Everything else the console draws is the reconstructed replay of 2 July 2019, or a cycle recomputed
from it. This is the one street forecast on the screen whose time base is *today*: Open-Meteo's rain
for the city's grid cell, held hour by hour over the next three hours, run through Flash-lite at 50
members, reported as depth per street. It exists so a screen that is otherwise a replay can say,
truthfully, what the next three hours hold - and on a dry afternoon that answer is "nothing".

**It is not a radar nowcast, and the response says so in `method`.** The rain is a numerical weather
prediction for one model cell, applied evenly to every street; it places no storm cell, and the
emulator it drives has a held-out CSI of 0.085 at 30 cm against the Twin (`docs/verification/
flash_lite.json`). The skill travels in `skill` so no screen has to quote it from memory.

**The emulator's base state is removed, and that is the whole point of this module.** Flash-lite
predicts a deviation from a stored base state - the depth every one of its training storms produced
regardless of intensity: tide, and runoff arriving from upstream (`varuna_flash.model`). That base
state belongs to the Twin runs it was fitted on, not to this afternoon, and with no rain at all it
would paint about a thousand Mumbai streets above 5 cm. Adding it here would draw a training
window's sea onto today's city. So the outlook runs the cascade with the base state emptied and
reports only the water today's rain puts on the street; `baseline_removed` says what was taken out
and the notes say that no live tide is connected.

**Where each number comes from.**

- Rain: :func:`varuna_api.routers.weather.get_weather`, the same copy and 15-minute cache the
  dashboard's weather chip reads. Hourly values are held for their hour (each is the sum over the
  hour *before* its timestamp). The 15-minute series is compared and not used: for India it is
  interpolated from the hourly models, and the note gives both totals so that is checkable.
- Blockage: the newest run's Pulse posterior per road segment where one exists, the city's inferred
  prior elsewhere, and a flat 0.20 for the streets with no pipe at all - each counted.
- Members: 50 draws of blockage and of the emulator's storage coefficient
  (`varuna_flash.model.draw_members`), all on the one rain series. The band is therefore the
  emulator's uncertainty and not the weather's, and the notes say so.

**Stale weather is labelled, never refreshed by invention.** A copy served offline or after an
upstream failure keeps its own window: the outlook covers the three hours after that copy was
fetched, `expired` turns true once they have passed, and nothing is shifted onto the present.

**Kept out of the run registry** (rule 8). Nothing is written under `data/runs`; the answer lives in
this process's memory for as long as the weather copy it was computed from, so a baked replay and
its byte-identical re-bake are untouched.

Mumbai only for now: the emulator is positional over one city's streets, so any other city is
refused with 422 rather than handed Mumbai's response curves, and a Mumbai build the emulator was
not fitted to is refused with 503 and the command that fixes it.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Annotated, Any, Literal

import numpy as np
import structlog
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from varuna_schemas.constants import IST
from varuna_schemas.paths import city_dir, repo_root

from varuna_api import street_names
from varuna_api.routers import weather as weather_router
from varuna_api.runs_util import latest_run_for
from varuna_api.state import api_error

if TYPE_CHECKING:  # pragma: no cover - typing only; varuna_flash is imported lazily
    from numpy.typing import NDArray
    from varuna_flash.model import FlashModel

log = structlog.get_logger("varuna.api.outlook")

router = APIRouter(prefix="/v1", tags=["weather"])

MODEL_PATHS = ("data/train/flash_lite.npz", "demo/flash_lite.npz")
"""Where the fitted emulator is looked for, in order - the same two `/v1/whatif` reads."""

FITTED_CITY = "mumbai"
"""The city `make train` fits the emulator to (`varuna_flash.train`'s default).

The emulator file carries no city, so it is named here. Comparing ids would refuse Chennai today
only by accident of the id scheme: ids are ``S<osm way>-<part>``, and both cities number the
segments whose way id was lost from ``S0-000`` upward (1,247 in Mumbai, 1,001 in Chennai), so the
two tables share ids that are different streets. The street-table comparison below is for the
other failure, an emulator fitted to an older build of this city."""

MAX_UNMATCHED_SHARE = 0.10
"""Refuse when more than this share of the emulator's streets are missing from the city's street
table. Measured 2026-09-26: 1,247 of 21,296 (5.9 %) are, because `city/mumbai/segments.parquet`
was rebuilt on 12 Sep with those segments renamed ``S0-*`` while the segment index the emulator
and every baked run were built from (`segment_cells.npz`, 10 Sep) kept their way ids. Those
streets are still forecast; the response counts them and says they carry no name."""

DRAIN_HEALTH = "drain_health.geojson"
"""Pulse's posterior per pipe, written into every run directory by the cycle."""

STEP_MIN = 5
N_STEPS = 36
"""Three hours at five minutes: the same horizon and step as every VARUNA run."""

MEMBERS = 50
"""SPEC.md 11.7's ensemble size."""

SEED = 2019
"""One generator behind every draw, so the same weather copy gives the same answer twice."""

THRESHOLDS_CM = (15.0, 30.0, 45.0)
"""Exceedance probabilities reported per step: two-wheelers, cars, buses (SPEC.md 6.2)."""

LISTED_MIN_CM = 1.0
"""A street is listed when its p90 exceeds this at any step. Below a centimetre is a damp road."""

MAX_LISTED = 3000
"""Streets returned, wettest first; `n_segments_over_1cm` and `truncated` say when there were more."""

WORST_STREETS = 5
CHUNK = 2048
"""Streets simulated per pass. Fifty members of 2,048 streets is one 102k-column emulator run and
about 30 MB of depth; the whole city at once would be 300 MB, which the deployed host cannot spare."""

METHOD = (
    "reduced-order emulator (Flash-lite) on Open-Meteo NWP rain, AOI-uniform; not a radar nowcast"
)


# ============================================================================ response models
class OutlookSource(BaseModel):
    """Where the rain came from, and how old the copy is at this request."""

    name: str = "Open-Meteo"
    url: str = weather_router.SOURCE_URL
    licence: str = weather_router.LICENCE
    licence_url: str = weather_router.LICENCE_URL
    attribution: str = weather_router.ATTRIBUTION
    fetched_at: datetime
    age_s: float = Field(description="Seconds since `fetched_at`, computed per request.")
    stale: bool = Field(description="True once the weather copy is past its 15-minute window.")
    grid_cell_km: float = Field(
        description="Distance from the AOI centre to the centre of the Open-Meteo grid cell that "
        "answered, km. The rain is that cell's, applied to every street."
    )
    grid_point: weather_router.GeoPoint
    series: Literal["hourly", "minutely_15"] = Field(
        default="hourly",
        description="Which Open-Meteo series the rain was built from. Always `hourly` for India, "
        "where the 15-minute series is interpolated from the hourly models and adds no timing; "
        "each hourly value is held for the hour before its timestamp.",
    )
    series_note: str


class OutlookSegment(BaseModel):
    """One street's three hours, per step, above the emulator's base state."""

    segment_id: str
    name: str | None = Field(description="OSM street name, or null when OSM names none.")
    display_name: str = Field(
        default="",
        description=(
            "The name to print: OSM's, else 'off <street>', else '<class> near <place>', else "
            "'<class> in <city>'. Never 'Unnamed road'."
        ),
    )
    p50_cm: list[float]
    p90_cm: list[float]
    p_gt_15: list[float]
    p_gt_30: list[float]
    p_gt_45: list[float]
    peak_p50_cm: float
    peak_p90_cm: float
    peak_ts: datetime = Field(description="Valid time of the p50 peak.")


class OutlookStreet(BaseModel):
    """A street named in the summary."""

    segment_id: str
    name: str | None
    display_name: str = Field(default="", description="The name to print; never 'Unnamed road'.")
    peak_p50_cm: float
    peak_p90_cm: float
    peak_ts: datetime


class OutlookSummary(BaseModel):
    """The answer in counts and one sentence. Counts are by each street's peak p50."""

    max_cm: float = Field(description="Deepest p50 on any street at any step, cm.")
    max_p90_cm: float
    n_ge_5: int
    n_ge_15: int
    n_ge_30: int
    worst: list[OutlookStreet]
    sentence: str


class OutlookBaseline(BaseModel):
    """The base state taken out: what the emulator would have drawn with no rain at all."""

    n_ge_5: int
    n_ge_15: int
    n_ge_30: int
    max_cm: float


class OutlookBlockage(BaseModel):
    """Which blockage the members were drawn around, street by street."""

    kind: Literal["pulse_posterior", "city_prior"]
    run_id: str | None = Field(description="The run whose Pulse posterior was read, if any.")
    from_posterior: int
    from_prior: int
    flat: int = Field(description="Streets with no pipe at all, run at a flat 0.20.")


class OutlookSkill(BaseModel):
    """The emulator's measured skill against the Twin, from the fit that ran."""

    rmse_cm: float
    csi_30cm: float
    n_training_runs: int
    fitted_segments: int
    n_segments: int


class Outlook(BaseModel):
    """Today's next three hours of street depth from live rain, labelled as what it is."""

    city: str
    mode: Literal["outlook"] = "outlook"
    run_id: str | None = Field(
        default=None, description="Always null: an outlook is not a run and is not registered."
    )
    issued_at: datetime
    valid_from: datetime
    valid_to: datetime
    expired: bool = Field(description="True once `valid_to` has passed (a stale weather copy).")
    steps_min: int = STEP_MIN
    n_steps: int
    valid_ts: list[datetime] = Field(description="End of each 5-minute step.")
    rain_mm_h: list[float] = Field(description="AOI-uniform rain rate per step, mm/h.")
    rain_total_mm: float
    members: int
    source: OutlookSource
    segments: list[OutlookSegment]
    n_segments_over_1cm: int
    truncated: bool
    summary: OutlookSummary
    baseline_removed: OutlookBaseline
    blockage: OutlookBlockage
    skill: OutlookSkill
    no_rain_response: int = Field(
        description="Streets the emulator has no rain response for (never wet in training); "
        "they read 0 cm here whatever the rain."
    )
    unmatched_streets: int = Field(
        description="The emulator's streets whose id the city's current street table does not "
        "carry: forecast, but with no name and no geometry in today's street layer."
    )
    notes: list[str]
    method: str = METHOD
    compute_ms: int
    cached: bool = Field(description="True when this answer was computed for an earlier request.")


# ============================================================================ inputs
@dataclass(frozen=True, slots=True)
class _Streets:
    ids: frozenset[str]
    count: int
    names: dict[str, str]
    display: dict[str, str] = field(default_factory=dict)
    """Every segment's display name (``varuna_api.street_names``); OSM's name where it has one."""
    city_label: str = ""


@dataclass(frozen=True, slots=True)
class _Blockage:
    mean: NDArray[np.float64]
    sd: NDArray[np.float64]
    info: OutlookBlockage
    key: tuple[Any, ...]


_models: dict[str, tuple[int, FlashModel]] = {}
_streets: dict[str, tuple[int, _Streets]] = {}
_blockages: dict[tuple[Any, ...], _Blockage] = {}
_kept: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
_guard = threading.Lock()
"""One outlook computed at a time: two readers arriving together should cost one run, not two."""


def clear_cache() -> None:
    """Forget every kept model, street table, blockage and answer (tests)."""
    with _guard:
        _models.clear()
        _streets.clear()
        _blockages.clear()
        _kept.clear()


def _load_model() -> tuple[str, FlashModel]:
    """The fitted emulator and a key that changes when its file does."""
    from varuna_flash.model import load

    for candidate in MODEL_PATHS:
        path = repo_root() / candidate
        if not path.is_file():
            continue
        mtime = path.stat().st_mtime_ns
        kept = _models.get(str(path))
        if kept is None or kept[0] != mtime:
            kept = (mtime, load(path))
            _models[str(path)] = kept
        return f"{candidate}@{mtime}", kept[1]
    raise api_error(
        503,
        "no_emulator",
        "Flash-lite has not been fitted, so there is nothing to run today's rain through. Run "
        "`make train` to fit it from Twin runs.",
    )


def _city_streets(city: str) -> _Streets:
    """The city's road segments and their OSM names, from `city/<city>/segments.parquet`."""
    path = city_dir(city) / "segments.parquet"
    if not path.is_file():
        raise api_error(
            503,
            "city_not_built",
            f"No street table at city/{city}/segments.parquet, so there are no streets to "
            f"forecast. Run `make city CITY={city}`.",
        )
    mtime = path.stat().st_mtime_ns
    kept = _streets.get(city)
    if kept is not None and kept[0] == mtime:
        return kept[1]
    import pandas as pd

    frame = pd.read_parquet(path, columns=["segment_id", "name"])
    ids = [str(s) for s in frame["segment_id"]]
    names = {
        str(sid): str(name).strip()
        for sid, name in zip(frame["segment_id"], frame["name"], strict=True)
        if isinstance(name, str) and name.strip()
    }
    shown = street_names.street_names(city)
    streets = _Streets(
        ids=frozenset(ids),
        count=len(ids),
        names=names,
        display=dict(shown.names) if shown is not None else {},
        city_label=shown.city_label if shown is not None else street_names.city_label(city),
    )
    _streets[city] = (mtime, streets)
    return streets


def _posterior_edges(run: Path) -> Any:
    """The run's learned blockage per pipe as a frame, or None when it carries none."""
    import pandas as pd

    path = run / DRAIN_HEALTH
    try:
        features = json.loads(path.read_text(encoding="utf-8")).get("features") or []
    except (OSError, ValueError):
        return None
    if not features:
        return None
    return pd.DataFrame([feature.get("properties", {}) for feature in features])


def _blockage(city: str, model: FlashModel, model_key: str) -> _Blockage:
    """Blockage mean and spread per street: the newest posterior, else the prior; NaN for none.

    NaN is left for streets with no pipe in either table; the ensemble substitutes the flat
    prior for them and counts it, exactly as the cycle's builder does.
    """
    from varuna_pulse.join import SegmentBetaError, segment_beta

    run = latest_run_for(city, lambda p: (p / DRAIN_HEALTH).is_file())
    mtime = (run / DRAIN_HEALTH).stat().st_mtime_ns if run is not None else 0
    key = (city, run.name if run is not None else None, mtime, model_key)
    kept = _blockages.get(key)
    if kept is not None:
        return kept

    ids = model.segment_ids
    n = model.n_segments
    try:
        prior_mean, prior_sd = segment_beta(city, None, ids)
    except SegmentBetaError as error:
        log.warning("outlook.no_prior", city=city, error=str(error))
        prior_mean, prior_sd = np.full(n, np.nan), np.full(n, np.nan)

    learned_mean, learned_sd = np.full(n, np.nan), np.full(n, np.nan)
    posterior = _posterior_edges(run) if run is not None else None
    if posterior is not None:
        try:
            learned_mean, learned_sd = segment_beta(city, posterior, ids)
        except SegmentBetaError as error:
            log.warning("outlook.no_posterior_join", city=city, error=str(error))
            posterior = None

    from_posterior = np.isfinite(learned_mean)
    from_prior = ~from_posterior & np.isfinite(prior_mean)
    mean = np.where(from_posterior, learned_mean, prior_mean)
    sd = np.where(from_posterior, learned_sd, prior_sd)
    info = OutlookBlockage(
        kind="pulse_posterior" if posterior is not None else "city_prior",
        run_id=run.name if posterior is not None and run is not None else None,
        from_posterior=int(from_posterior.sum()),
        from_prior=int(from_prior.sum()),
        flat=int(n - from_posterior.sum() - from_prior.sum()),
    )
    blockage = _Blockage(mean=mean, sd=sd, info=info, key=key)
    if len(_blockages) >= 4:
        _blockages.clear()
    _blockages[key] = blockage
    return blockage


# ============================================================================ rain
def _floor_5min(ts: datetime) -> datetime:
    local = ts.astimezone(IST)
    return local.replace(minute=local.minute - local.minute % STEP_MIN, second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class _Rain:
    valid_from: datetime
    rates: list[float]
    """mm/h per 5-minute step, as long as the hourly series covers; never padded."""

    minutely_total_mm: float | None
    """The 15-minute series' total over the same window, for the note; None when absent."""


def _rain(weather: weather_router.Weather) -> _Rain:
    """Hold each hourly value over the hour before its timestamp, five minutes at a time.

    The series stops at the first step no hourly value covers. It is never padded with zeros:
    "no data" is not "no rain", and a zero would be a forecast nobody made.
    """
    valid_from = _floor_5min(weather.fetched_at)
    hours = [
        (step.ts.astimezone(IST), step.precipitation_mm)
        for step in weather.hourly
        if step.precipitation_mm is not None
    ]
    rates: list[float] = []
    for index in range(N_STEPS):
        start = valid_from + timedelta(minutes=index * STEP_MIN)
        rate = next(
            (mm for end, mm in hours if end - timedelta(hours=1) <= start < end),
            None,
        )
        if rate is None:
            break
        rates.append(max(float(rate), 0.0))

    minutely: float | None = None
    if weather.minutely_15 and rates:
        window_end = valid_from + timedelta(minutes=len(rates) * STEP_MIN)
        total = 0.0
        covered = 0.0
        for step in weather.minutely_15:
            if step.precipitation_mm is None:
                continue
            end = step.ts.astimezone(IST)
            start = end - timedelta(minutes=15)
            overlap = (min(end, window_end) - max(start, valid_from)).total_seconds()
            if overlap <= 0:
                continue
            total += step.precipitation_mm * overlap / 900.0
            covered += overlap
        # Only quoted when it covers the whole window; a partial total compared with a whole one
        # would be a disagreement the data does not contain.
        if covered >= (window_end - valid_from).total_seconds() - 1:
            minutely = total
    return _Rain(valid_from=valid_from, rates=rates, minutely_total_mm=minutely)


# ============================================================================ the ensemble
@dataclass(frozen=True, slots=True)
class _Members:
    active: NDArray[np.int64]
    """Model columns with a rain response; every other street is exactly 0 cm above base."""

    p50: NDArray[np.float32]
    p90: NDArray[np.float32]
    exceed: dict[float, NDArray[np.float32]]
    n_flat: int


def _members(model: FlashModel, rain: NDArray[np.float64], blockage: _Blockage) -> _Members:
    """Fifty emulator members on one rain series with the base state emptied.

    The cascade is per street with no coupling between streets, so fifty members of ``n`` streets
    are one emulator run of ``50 n`` independent columns (member-major); running them that way,
    a chunk of streets at a time, is the same arithmetic as fifty calls and a tenth of the memory.
    A street whose fitted gain is zero receives no inflow and so stays at exactly 0 cm above its
    base state in every member; it is left out of the run rather than simulated to zero.
    """
    from varuna_flash.ensemble import PRIOR_BETA
    from varuna_flash.model import draw_members, simulate

    resolved = np.isfinite(blockage.mean)
    centre = np.where(resolved, blockage.mean, PRIOR_BETA)
    spread = np.where(resolved & np.isfinite(blockage.sd), blockage.sd, 0.0)
    _, beta, k = draw_members(model, centre, spread, n_members=MEMBERS, n_sky=1, seed=SEED)

    active = np.flatnonzero(np.asarray(model.gain) > 0).astype(np.int64)
    n_steps = int(rain.size)
    p50 = np.zeros((n_steps, active.size), dtype=np.float32)
    p90 = np.zeros((n_steps, active.size), dtype=np.float32)
    exceed = {t: np.zeros((n_steps, active.size), dtype=np.float32) for t in THRESHOLDS_CM}

    # No rain means no inflow, and a cascade with no inflow ponds exactly nothing in every
    # member (`simulate` clamps at zero), so the zeros above are already the answer. Skipping the
    # run saves about a second on a dry afternoon and changes no number.
    chunks = range(0, active.size, CHUNK) if np.any(rain > 0) else range(0)
    for start in chunks:
        idx = active[start : start + CHUNK]
        n = idx.size
        columns = MEMBERS * n
        wide = replace(
            model,
            segment_ids=("",) * columns,
            k_steps=np.tile(model.k_steps[idx], MEMBERS),
            gain=np.tile(model.gain[idx], MEMBERS),
            drain_cm_per_step=np.tile(model.drain_cm_per_step[idx], MEMBERS),
            beta_ref=np.tile(model.beta_ref[idx], MEMBERS),
            # Emptied, so `simulate` adds nothing back: the answer is the rain's own water.
            baseline_cm=np.empty((0, columns)),
        )
        depth = simulate(
            wide, rain, beta=beta[:, idx].reshape(-1), k_steps=k[:, idx].reshape(-1)
        ).reshape(n_steps, MEMBERS, n)
        quantiles = np.percentile(depth, [50.0, 90.0], axis=1)
        p50[:, start : start + n] = quantiles[0]
        p90[:, start : start + n] = quantiles[1]
        for threshold in THRESHOLDS_CM:
            exceed[threshold][:, start : start + n] = (depth > threshold).mean(axis=1)

    return _Members(active=active, p50=p50, p90=p90, exceed=exceed, n_flat=int((~resolved).sum()))


# ============================================================================ assembly
def _clock(ts: datetime) -> str:
    return ts.astimezone(IST).strftime("%H:%M")


def _age_words(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def _sentence(
    n_ge_5: int,
    n_ge_15: int,
    worst: list[OutlookStreet],
    horizon_min: int,
    rain_total_mm: float,
) -> str:
    """The one sentence a reader takes away, in the median member's numbers (SPEC.md 6.8)."""
    span = "the next 3 h" if horizon_min == N_STEPS * STEP_MIN else f"the next {horizon_min} min"
    if n_ge_5 == 0:
        if rain_total_mm <= 0:
            return f"No rain forecast and no street above 5 cm expected in {span}."
        return f"No street above 5 cm expected in {span}."
    head = f"{n_ge_5:,} street{'' if n_ge_5 == 1 else 's'} expected above 5 cm in {span}"
    if n_ge_15:
        head += f", {n_ge_15:,} above 15 cm"
    deepest = worst[0]
    where = deepest.display_name or deepest.name or "a street in the city"
    return (
        f"{head}; deepest {deepest.peak_p50_cm:.0f} cm on {where} around {_clock(deepest.peak_ts)}."
    )


def _compute(
    city: str,
    weather: weather_router.Weather,
    model: FlashModel,
    streets: _Streets,
    blockage: _Blockage,
) -> dict[str, Any]:
    started = perf_counter()
    rain = _rain(weather)
    if not rain.rates:
        raise api_error(
            503,
            "no_rain_forecast",
            "Open-Meteo's copy carries no hourly rain for the three hours after it was fetched, so "
            "there is no series to run. Nothing was substituted for it; try again once the weather "
            "source answers.",
        )
    series = np.asarray(rain.rates, dtype=np.float64)
    n_steps = int(series.size)
    valid_ts = [rain.valid_from + timedelta(minutes=STEP_MIN * (i + 1)) for i in range(n_steps)]
    valid_to = valid_ts[-1]
    rain_total = float(series.sum() * STEP_MIN / 60.0)

    members = _members(model, series, blockage)
    ids = model.segment_ids
    peak_p50 = members.p50.max(axis=0) if members.active.size else np.zeros(0, np.float32)
    peak_p90 = members.p90.max(axis=0) if members.active.size else np.zeros(0, np.float32)
    peak_step = members.p50.argmax(axis=0) if members.active.size else np.zeros(0, np.int64)

    listed = np.flatnonzero(peak_p90 > LISTED_MIN_CM)
    listed = listed[np.argsort(-peak_p90[listed], kind="stable")]
    truncated = listed.size > MAX_LISTED

    def street(column: int) -> OutlookStreet:
        segment = ids[int(members.active[column])]
        return OutlookStreet(
            segment_id=segment,
            name=streets.names.get(segment),
            display_name=streets.display.get(segment)
            or streets.names.get(segment)
            or f"Road in {streets.city_label or 'the city'}",
            peak_p50_cm=round(float(peak_p50[column]), 1),
            peak_p90_cm=round(float(peak_p90[column]), 1),
            peak_ts=valid_ts[int(peak_step[column])],
        )

    # Rounded once as arrays and handed over as rows: rounding 3,000 streets x 5 series x 36 steps
    # one Python float at a time was half a second of a three-second answer.
    shown = listed[:MAX_LISTED]
    rows = {
        "p50_cm": np.round(members.p50[:, shown].astype(np.float64), 1).T.tolist(),
        "p90_cm": np.round(members.p90[:, shown].astype(np.float64), 1).T.tolist(),
        **{
            f"p_gt_{int(t)}": np.round(members.exceed[t][:, shown].astype(np.float64), 2).T.tolist()
            for t in THRESHOLDS_CM
        },
    }
    segments = []
    for row, column in enumerate(shown):
        head = street(int(column))
        segments.append(
            OutlookSegment(
                segment_id=head.segment_id,
                name=head.name,
                display_name=head.display_name,
                p50_cm=rows["p50_cm"][row],
                p90_cm=rows["p90_cm"][row],
                p_gt_15=rows["p_gt_15"][row],
                p_gt_30=rows["p_gt_30"][row],
                p_gt_45=rows["p_gt_45"][row],
                peak_p50_cm=head.peak_p50_cm,
                peak_p90_cm=head.peak_p90_cm,
                peak_ts=head.peak_ts,
            )
        )

    by_p50 = np.argsort(-peak_p50, kind="stable")
    worst = [street(int(c)) for c in by_p50[:WORST_STREETS] if peak_p50[c] >= LISTED_MIN_CM]
    n_ge = {t: int(np.count_nonzero(peak_p50 >= t)) for t in (5.0, 15.0, 30.0)}
    horizon = n_steps * STEP_MIN
    summary = OutlookSummary(
        max_cm=round(float(peak_p50.max()) if peak_p50.size else 0.0, 1),
        max_p90_cm=round(float(peak_p90.max()) if peak_p90.size else 0.0, 1),
        n_ge_5=n_ge[5.0],
        n_ge_15=n_ge[15.0],
        n_ge_30=n_ge[30.0],
        worst=worst,
        sentence=_sentence(n_ge[5.0], n_ge[15.0], worst, horizon, rain_total),
    )

    base = np.asarray(model.baseline_cm, dtype=np.float64)
    base_peak = base[:n_steps].max(axis=0) if base.size else np.zeros(model.n_segments)
    baseline = OutlookBaseline(
        n_ge_5=int(np.count_nonzero(base_peak >= 5.0)),
        n_ge_15=int(np.count_nonzero(base_peak >= 15.0)),
        n_ge_30=int(np.count_nonzero(base_peak >= 30.0)),
        max_cm=round(float(base_peak.max()) if base_peak.size else 0.0, 1),
    )
    no_response = int(model.n_segments - members.active.size)
    unmatched = sum(1 for segment in ids if segment not in streets.ids)

    hourly_note = (
        "Rain is Open-Meteo's hourly forecast, each value held for the hour before its timestamp."
    )
    if rain.minutely_total_mm is None:
        series_note = f"{hourly_note} This copy carries no 15-minute series covering the window."
    else:
        series_note = (
            f"{hourly_note} Its 15-minute series sums to {rain.minutely_total_mm:.1f} mm over the "
            f"same {horizon} min against {rain_total:.1f} mm hourly; outside Europe and North "
            "America Open-Meteo interpolates the 15-minute series from hourly models, so it adds "
            "no timing and is not used."
        )

    info = blockage.info
    if info.kind == "pulse_posterior":
        blockage_words = (
            f"Pulse's posterior from run {info.run_id} on {info.from_posterior:,} streets, the "
            f"city's inferred prior on {info.from_prior:,}"
        )
    else:
        blockage_words = f"the city's inferred prior on {info.from_prior:,} streets"
    if members.n_flat:
        blockage_words += f" and a flat 0.20 on {members.n_flat:,} with no pipe to inherit one"

    notes = [
        f"Today, not the replay: rain for {_clock(rain.valid_from)} to {_clock(valid_to)} IST on "
        f"{rain.valid_from.astimezone(IST).strftime('%d %b %Y').lstrip('0')}, from one Open-Meteo "
        f"grid cell {weather.grid_offset_km:.1f} km from the AOI centre, applied evenly to every "
        "street. It is a weather-model forecast, not a radar nowcast, so it places no storm cell.",
        series_note,
        f"Depth is the water today's rain adds. The emulator's stored base state - tide and "
        f"upstream runoff from the Twin runs it was fitted on - is removed: with no rain at all it "
        f"would put {baseline.n_ge_5:,} streets above 5 cm (deepest {baseline.max_cm:.0f} cm), "
        "which is that training window's water, not today's. No live tide source is connected, so "
        "tidal backing is not in this outlook.",
        f"{MEMBERS} members, each at its own draw of pipe blockage ({blockage_words}) and of the "
        "emulator's storage coefficient. They share one rain series, so the spread between members "
        "is the emulator's uncertainty, not the weather's. 'Expected' means the median member.",
        f"{no_response:,} of {model.n_segments:,} streets were never wet enough in the emulator's "
        "training runs to fit a rain response, so they read 0 cm here whatever the rain.",
        f"Emulator skill against the Twin on runs it never saw: RMSE {model.rmse_cm:.1f} cm, CSI "
        f"{model.csi_30cm:.3f} at 30 cm, from {model.n_training_runs} training runs. Read it as a "
        "pattern, not a street's depth.",
        "Kept out of the run registry: nothing is written to data/runs and no replay product "
        "changes.",
    ]
    if unmatched:
        notes.append(
            f"{unmatched:,} of the emulator's streets carry ids the city's current street table "
            "does not: the table was rebuilt after the segment index the emulator and the baked "
            "runs share was cached. They are forecast here but carry no name and have no line in "
            "today's street layer."
        )
    if truncated:
        notes.append(
            f"{listed.size:,} streets exceed 1 cm at p90; the wettest {MAX_LISTED:,} are listed."
        )

    body = Outlook(
        city=city,
        issued_at=datetime.now(IST),
        valid_from=rain.valid_from,
        valid_to=valid_to,
        expired=False,
        n_steps=n_steps,
        valid_ts=valid_ts,
        rain_mm_h=[round(float(v), 2) for v in series],
        rain_total_mm=round(rain_total, 2),
        members=MEMBERS,
        source=OutlookSource(
            fetched_at=weather.fetched_at,
            age_s=weather.age_s,
            stale=weather.stale,
            grid_cell_km=weather.grid_offset_km,
            grid_point=weather.grid_point,
            series_note=series_note,
        ),
        segments=segments,
        n_segments_over_1cm=int(listed.size),
        truncated=truncated,
        summary=summary,
        baseline_removed=baseline,
        blockage=info,
        skill=OutlookSkill(
            rmse_cm=round(float(model.rmse_cm), 3),
            csi_30cm=round(float(model.csi_30cm), 4),
            n_training_runs=int(model.n_training_runs),
            fitted_segments=int(model.fitted_segments),
            n_segments=int(model.n_segments),
        ),
        no_rain_response=no_response,
        unmatched_streets=unmatched,
        notes=notes,
        compute_ms=round((perf_counter() - started) * 1000),
        cached=False,
    )
    log.info(
        "outlook.computed",
        city=city,
        steps=n_steps,
        rain_total_mm=round(rain_total, 2),
        n_ge_5=summary.n_ge_5,
        listed=int(listed.size),
        ms=body.compute_ms,
    )
    return body.model_dump(mode="json")


def _stamp(body: dict[str, Any], weather: weather_router.Weather, cached: bool) -> Outlook:
    """Re-age a kept answer at serve time: the weather's age and whether its window has passed."""
    outlook = Outlook.model_validate(body)
    notes = list(outlook.notes)
    if weather.stale:
        why = " ".join(weather.notes) or "Open-Meteo has not been reached since."
        notes.insert(
            0,
            f"The rain is from a weather copy {_age_words(weather.age_s)} old. {why} This "
            f"outlook covers {_clock(outlook.valid_from)} to {_clock(outlook.valid_to)} IST and is "
            "not today's latest.",
        )
    expired = datetime.now(IST) > outlook.valid_to
    if expired:
        notes.insert(0, "That window has passed; nothing has replaced it with invented rain.")
    return outlook.model_copy(
        update={
            "source": outlook.source.model_copy(
                update={"age_s": weather.age_s, "stale": weather.stale}
            ),
            "expired": expired,
            "notes": notes,
            "cached": cached,
        }
    )


def _weather_for_outlook(city: str) -> weather_router.Weather:
    """The dashboard's weather copy; a refusal is re-worded, because here the answer depends on it.

    Built from the refusal's ``reason`` (the cause and its fix), never from the weather route's
    message, which ends with the dashboard's "the flood forecast on this page does not depend on
    it" - true there, and false of an outlook made of this rain.
    """
    try:
        return weather_router.get_weather(city)
    except weather_router.WeatherRefused as error:
        raise api_error(
            503,
            error.code,
            "The live outlook runs on Open-Meteo's rain and there is none to run: "
            f"{error.reason} Nothing was invented in its place.",
        ) from error


@router.get(
    "/outlook",
    response_model=Outlook,
    summary="Today's next 3 h of street depth from live Open-Meteo rain, through the emulator",
    responses={
        422: {"description": "No emulator is fitted to this city's streets (Chennai)"},
        503: {
            "description": "No weather copy, no fitted emulator, the city is not built, or the "
            "emulator was fitted to an older build of it"
        },
    },
)
def outlook(
    city: Annotated[str, Query(description="City slug; Mumbai is the only one fitted.")] = "mumbai",
) -> Outlook:
    """A labelled live outlook: Open-Meteo NWP rain, AOI-uniform, through Flash-lite at 50 members.

    Depth is reported **above** the emulator's stored base state, never with it: that base state
    is a training window's tide and upstream runoff and would draw a flood on a dry day. Cached
    with the weather copy it came from (15 minutes); a stale copy is served with its age and its
    own window, never shifted onto the present.
    """
    slug = weather_router._city(city)
    weather_router._config(slug)  # 404 for a city with no config, before anything is loaded

    if slug != FITTED_CITY:
        name = slug.title()
        raise api_error(
            422,
            "no_emulator_for_city",
            f"There is no live outlook for {name}: Flash-lite, the emulator it runs on, is fitted "
            f"to {FITTED_CITY.title()}'s streets only, and its response curves would belong to "
            f"the wrong roads. {name}'s first forecast is the design storm on the onboarding "
            "screen; it gets an outlook once an emulator is fitted to its own Twin runs.",
        )

    model_key, model = _load_model()
    streets = _city_streets(slug)
    missing = sum(1 for segment in model.segment_ids if segment not in streets.ids)
    if missing > MAX_UNMATCHED_SHARE * model.n_segments:
        raise api_error(
            503,
            "emulator_mismatch",
            f"{missing:,} of Flash-lite's {model.n_segments:,} streets are not in "
            f"city/{slug}/segments.parquet, so it was fitted to another build of the city and its "
            "response curves would land on the wrong roads. Run `make train` to fit it to this "
            "build.",
        )

    weather = _weather_for_outlook(slug)
    with _guard:
        blockage = _blockage(slug, model, model_key)
        key = (slug, weather.fetched_at.isoformat(), model_key, blockage.key)
        kept = _kept.get(slug)
        if kept is not None and kept[0] == key:
            return _stamp(kept[1], weather, cached=True)
        body = _compute(slug, weather, model, streets, blockage)
        _kept[slug] = (key, body)
    return _stamp(body, weather, cached=False)


__all__ = ["METHOD", "Outlook", "clear_cache", "router"]

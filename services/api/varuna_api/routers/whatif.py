"""What-if on a baked run (SPEC.md 7.7, 12; task P7.8).

**The level comes from the Twin, the sensitivity from the emulator.** A baked run already holds
the coupled solver's answer for this storm, per segment, per step. Flash-lite is not accurate
enough to replace that - its CSI at the 30 cm threshold is 0.085 on held-out storms
(`varuna_flash.model`) - but it is fast enough to answer *how the answer moves* when the scenario
changes. So a what-if is the run's own depths plus the difference between two emulator runs:

    scenario = twin_depth + (emulator(scenario) - emulator(baseline))

Which is a delta-correction, and the standard way to use a cheap surrogate beside an expensive
model: the surrogate's systematic error largely cancels in the difference, and the level anybody
reads off the screen is still the physics'.

The response says all of this in `method` and `notes`, and carries the emulator's measured skill,
because a number whose provenance is two models deserves to say so (rule 6).

A scenario the emulator cannot represent at all - a different sea level - runs on one full-city
coupled Twin instead, as a job: ``POST /v1/whatif/twin`` (see the last section of this module).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Annotated, Any

import numpy as np
import pandas as pd
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse
from varuna_pulse.join import SegmentBetaError, segment_beta
from varuna_schemas.constants import IST
from varuna_schemas.constants import PHYSICS_CHECK_TOLERANCE_CM as PHYSICS_TOLERANCE_CM
from varuna_schemas.paths import bundle_dir, city_dir, data_dir, repo_root, run_dir

from varuna_api import street_names
from varuna_api.runs_util import latest_run_for, no_run_hint, resolve_city
from varuna_api.state import AppState, api_error, get_state

# **Imported at module scope, unlike `varuna_flash` below, because it costs 0.8 s.** That is the
# process's first `import pandas`, and deferring it to the first request spent the whole of this
# endpoint's 1 s budget on it - 1.1 s cold against 0.1 s once warm. The API is started before the
# judges arrive and the what-if is pressed in front of them, so the second of the two is the one
# that must be fast; a server paying for its dependencies at boot is the ordinary arrangement.

if TYPE_CHECKING:  # pragma: no cover - typing only; varuna_flash is imported lazily below
    from numpy.typing import NDArray
    from varuna_flash.model import FlashModel

log = structlog.get_logger("varuna.api.whatif")

router = APIRouter(prefix="/v1", tags=["whatif"])

MODEL_PATHS = ("data/train/flash_lite.npz", "demo/flash_lite.npz")
"""Where the fitted emulator is looked for, in order."""

UNMATCHED_NAMED = 5
"""Unmatched ids quoted back in the refusal, before it says how many more there were."""

DRAIN_HEALTH = "drain_health.geojson"
"""Pulse's posterior for the cycle, written per run by `varuna_pulse.health.write_drain_health`."""

PRIOR_BETA = 0.20
"""The flat blockage used where the city has no pipe under a segment to inherit one from.

SPEC.md 10.1 step 7 sets the prior's mean by land use - 0.15 arterial, 0.2 residential, 0.35
markets - so 0.2 is the residential middle, and it is what this endpoint used to run *every*
segment at. It is now the last of three sources rather than the only one, and the response counts
the segments it reached instead of absorbing them."""

_BETA_CACHE: dict[tuple[str, str, int, int], tuple[NDArray[np.float64], dict[str, Any]]] = {}
"""One joined beta vector per run, keyed on the posterior's mtime so a re-bake invalidates it.

The join is two merges over Mumbai's 67k segment-edge pairs, about 320 ms measured - affordable
once against the endpoint's 1 s budget, and not per drag of a what-if slider. The vector is a
pure function of the run's posterior and the city's graph, so caching it changes no number."""

MAX_CACHED_RUNS = 8
"""Joined vectors held before the cache is emptied, so scrubbing a replay cannot grow it forever."""


def _resolve_cleaned(requested: set[str], model: FlashModel) -> tuple[set[str], list[str]]:
    """Split the ids asked for into the ones this emulator can clean and the rest.

    **The endpoint used to echo the request back as the answer.** `run_scenario` drops an id it
    does not recognise, so posting drain edge ids returned 200, said "2 pipes cleaned to beta =
    0.05" and reported zero change - a response that named pipes nothing had touched (rule 6).
    Drain edge ids (`MUM-E035757`) and road-segment ids (`S1001383363-000`) are disjoint
    vocabularies: 6,000 edges and 21,296 segments with no overlap, so the mistake is easy to
    make and was invisible.
    """
    known = set(model.segment_ids)
    matched = requested & known
    unmatched = sorted(requested - known)
    if requested and not matched:
        named = ", ".join(unmatched[:UNMATCHED_NAMED])
        extra = (
            f" and {len(unmatched) - UNMATCHED_NAMED} more"
            if len(unmatched) > UNMATCHED_NAMED
            else ""
        )
        raise api_error(
            422,
            "unknown_segments",
            f"None of the ids to clean are road segments in this city: {named}{extra}. "
            "Cleaning is applied to the pipe under a road segment, so this endpoint takes "
            "road-segment ids (S...), not drain edge ids (MUM-E...); the desilting CSV at "
            "/v1/drains/health.csv lists edge ids, which are a different vocabulary. Read "
            "segment ids from /v1/nowcast/segments or the hotspot drawer.",
        )
    return matched, unmatched


def _model():
    from varuna_flash.model import load

    for candidate in MODEL_PATHS:
        path = repo_root() / candidate
        if path.is_file():
            return load(path)
    raise api_error(
        503,
        "no_emulator",
        "Flash-lite has not been fitted. Run `make train` to fit it from Twin runs.",
    )


def _latest_run(city: str | None = None) -> Path:
    """The newest run for a city that carries a segment forecast.

    City-filtered: see `varuna_api.runs_util`, where a second city's runs shadowing the first is
    written up. The city goes through `resolve_city` like every other route's, so one VARUNA has
    no run code for is refused rather than handed another city's run, and the no-run message
    names the command that bakes that city's own bundle.
    """
    name = resolve_city(city)
    found = latest_run_for(name, lambda p: (p / "segments_wet.json").is_file())
    if found is None:
        raise api_error(404, "no_runs", no_run_hint(name, "a segment forecast"))
    return found


def _posterior_edges(path: Path) -> pd.DataFrame | None:
    """The run's learned blockage per pipe, or None when the run carries no posterior.

    `drain_health.geojson` is capped at the worst `varuna_pulse.health.MAX_WRITTEN_EDGES` pipes,
    so this frame is 6,000 of the graph's 49,770 edges, and the segments it leaves unresolved
    fall through to the city's prior and are counted there.

    **The posterior wins over the prior even when the prior is higher.** A segment whose worst
    *learned* pipe sits at 0.20 can have an unwritten neighbour whose *prior* is 0.35, and taking
    the higher of the two would refuse to learn downward - assimilation moved 366 pipes this
    cycle and some of them fell. Preferring what was learned understates 2 of the 2,517 resolved
    segments on the 09:10 run, by at most 0.025 of blockage; taking the maximum instead would
    overrule Pulse on every pipe it cleared.
    """
    health_path = path / DRAIN_HEALTH
    if not health_path.is_file():
        return None
    health = json.loads(health_path.read_text(encoding="utf-8"))
    features = health.get("features") or []
    if not features:
        return None
    # `capacity_reduction_pct` rides along: the join prefers the caller's column to recomputing
    # it, so the endpoint and the drain X-ray never disagree about which pipe is worst.
    return pd.DataFrame([feature.get("properties", {}) for feature in features])


def _beta_vector(
    path: Path, city: str | None, model: FlashModel
) -> tuple[NDArray[np.float64], dict[str, Any]]:
    """Blockage per road segment for the emulator, and where every value in it came from.

    **This endpoint used to run at a flat 0.20** while the same run directory carried 6,000 pipes
    between 0.20 and 0.68 on the 09:10 cycle - the learned state Pulse exists to produce, sitting
    unread beside the model that needed it (SPEC.md 11.7, rule 6). The substitution was silent,
    which is the part that mattered: a what-if answered at a uniform blockage looks exactly like
    one answered at the posterior, and cleaning a pipe that was never blocked reports a benefit
    the city would not get.

    Three sources, in order, each counted in the returned `beta_source`:

    1. the run's own posterior (`drain_health.geojson`), which is what Pulse learned this cycle;
    2. the city's per-pipe prior, for segments whose worst pipe is not in the written cap;
    3. :data:`PRIOR_BETA`, for segments with no inlet link at all - service roads, footways,
       slivers between intersections - which have no pipe to inherit from in either table.

    A city with no drain graph cannot be joined at all; that is the flat-prior fallback, and it
    says so rather than looking like a measurement.
    """
    if not city:
        # Without a city there is no graph to join against, and guessing one would attach another
        # city's learned drains to this run's streets.
        return np.full(model.n_segments, PRIOR_BETA), {
            "kind": "prior_uniform",
            "value": PRIOR_BETA,
            "reason": f"Run {path.name} names no city in run.json, so its drain graph cannot be "
            "identified. Re-bake the cycle.",
        }

    health_path = path / DRAIN_HEALTH
    mtime = health_path.stat().st_mtime_ns if health_path.is_file() else 0
    key = (path.name, city, mtime, model.n_segments)
    cached = _BETA_CACHE.get(key)
    if cached is not None:
        beta, source = cached
        return beta.copy(), dict(source)

    posterior = _posterior_edges(path)
    try:
        prior, _ = segment_beta(city, None, model.segment_ids)
        learned = (
            segment_beta(city, posterior, model.segment_ids)[0]
            if posterior is not None
            else np.full(model.n_segments, np.nan)
        )
    except SegmentBetaError as error:
        # No graph, no join. The flat prior is then the only honest thing left, and the reason
        # travels with it so nobody reads 0.20 as something Pulse measured.
        return np.full(model.n_segments, PRIOR_BETA), {
            "kind": "prior_uniform",
            "value": PRIOR_BETA,
            "reason": str(error),
        }

    from_posterior = np.isfinite(learned)
    from_prior = ~from_posterior & np.isfinite(prior)
    beta = np.where(from_posterior, learned, np.where(from_prior, prior, PRIOR_BETA))
    flat = int(model.n_segments - from_posterior.sum() - from_prior.sum())

    if flat == model.n_segments:
        # Nothing joined at all: the vector is literally uniform, so calling it a posterior would
        # be a label on an empty join. This is what a Chennai run looks like against the
        # Mumbai-fitted emulator - the two segment vocabularies do not meet.
        return beta, {
            "kind": "prior_uniform",
            "value": PRIOR_BETA,
            "reason": f"None of the emulator's {model.n_segments:,} segments has a pipe in "
            f"{city}'s drain graph, so neither the posterior nor the prior could be joined. The "
            "fitted emulator and this run are not the same city.",
        }

    source: dict[str, Any] = {
        "kind": "pulse_posterior" if posterior is not None else "city_prior",
        # The run whose posterior was read. It is the run the what-if is levelled on, so the two
        # cannot drift apart; naming it anyway means a response never leaves that assumed.
        "run_id": path.name,
        "resolved": int(from_posterior.sum()),
        # Everything the posterior did not reach: the city's own prior where a pipe exists,
        # PRIOR_BETA where none does. The second is counted separately because it is weaker.
        "filled_with_prior": int(from_prior.sum()) + flat,
        "filled_with_flat_prior": flat,
        "min": round(float(beta.min()), 4),
        "max": round(float(beta.max()), 4),
    }
    if posterior is not None:
        source["posterior_edges"] = len(posterior)
    else:
        source["reason"] = (
            f"Run {path.name} carries no {DRAIN_HEALTH}, so nothing was learned to join; "
            "blockage is the city's inferred prior per pipe."
        )
    if len(_BETA_CACHE) >= MAX_CACHED_RUNS:
        _BETA_CACHE.clear()
    _BETA_CACHE[key] = (beta, source)
    return beta.copy(), dict(source)


def _beta_note(source: dict[str, Any]) -> str:
    """One sentence naming which blockage the answer was computed at (SPEC.md 6.8)."""
    if source["kind"] == "prior_uniform":
        return (
            f"Blockage is a flat {source['value']} on every street: {source['reason']} "
            "Pulse's learned drain state was not used."
        )
    tail = (
        f"{source['filled_with_flat_prior']:,} of them at a flat {PRIOR_BETA} because no pipe "
        f"drains the street. Blockage spans {source['min']} to {source['max']}."
    )
    if source["kind"] == "city_prior":
        return f"{source['reason']} All {source['filled_with_prior']:,} segments took it, {tail}"
    return (
        f"Blockage is Pulse's posterior from run {source['run_id']}: "
        f"{source['resolved']:,} segments from the learned state and "
        f"{source['filled_with_prior']:,} from the city's inferred prior, {tail}"
    )


CHANGE_CM = 0.5
"""A street counts as changed - and is returned, drawn and counted - when its peak moves this much.

Every changed street is returned, not the largest few thousand: the counts on screen are the
length of the list the map draws, so a count and a map can no longer disagree. At rain 2.0x on
the 08:40 cycle that is tens of thousands of rows of about 80 bytes, which gzip carries."""

LARGEST_CHANGES = 25
"""Rows in ``largest_changes``, the named list beside the per-hotspot table."""

CLEAN_TOP_N = 14
"""Section 7.7's "Clean top 14 by blockage": the pipes the lever desilts, city-wide."""

CLEAN_TOP_MAX = 50
"""Most pipes the lever accepts when a caller asks for a number other than fourteen."""

EMULATOR_MINUTES_CM = (30, 45)
"""Thresholds the per-hotspot minutes are counted at: cars (30 cm) and buses (45 cm)."""

TWIN_ENDPOINT = "/v1/whatif/twin"
"""Where a scenario the emulator cannot represent - a different sea level - is answered."""

PUMP_LOWER_BOUND_LABEL = (
    "Lower bound: the emulator prices a pump against the rain that fell locally, not against the "
    "sea or water arriving from upstream, so a pump at a tide-locked street does more than this."
)

CLEAN_TOP_LABEL = "Top {n} pipe{s} by learned blockage, city-wide"
"""The lever's name on screen, section 7.7's "Clean top 14 by blockage" said as what it does."""

_NAMES_CACHE: dict[tuple[str, int], dict[str, str]] = {}
"""Street names per city, keyed on the segments table's mtime; read once, not per slider drag."""

_TOP_CLEAN_CACHE: dict[tuple[str, int, int, int], tuple[NDArray[np.float64], dict[str, Any]]] = {}
"""The re-joined blockage for "clean top N", per run and posterior mtime: a pure function of both."""


def _street_names(city: str | None) -> dict[str, str]:
    """Segment id to its OSM street name, for the rows a reader has to find on a map."""
    if not city:
        return {}
    table = city_dir(city) / "segments.parquet"
    if not table.is_file():
        return {}
    key = (city, table.stat().st_mtime_ns)
    held = _NAMES_CACHE.get(key)
    if held is None:
        from varuna_products.depth import segment_names

        held = segment_names(city_dir(city))
        _NAMES_CACHE.clear()
        _NAMES_CACHE[key] = held
    return held


def _lever_number(body: dict[str, Any], name: str, default: float) -> float:
    try:
        return float(body.get(name, default))
    except (TypeError, ValueError) as error:
        raise api_error(422, "bad_scenario", f"{name} is a number: {error}.") from error


def _clean_top_count(value: Any) -> int:
    """How many pipes "clean top N" desilts: ``true`` is fourteen, a number is that many."""
    if value is None or value is False:
        return 0
    if value is True:
        return CLEAN_TOP_N
    try:
        count = int(value)
    except (TypeError, ValueError) as error:
        raise api_error(
            422, "bad_scenario", f"clean_top is true or a number of pipes: {error}."
        ) from error
    if not 0 <= count <= CLEAN_TOP_MAX:
        raise api_error(
            422, "out_of_range", f"clean_top cleans 1 to {CLEAN_TOP_MAX} pipes; got {count}."
        )
    return count


def _tide_block(tide_offset_m: float) -> dict[str, Any]:
    """What the emulator did with the tide lever: nothing, and where the answer is instead."""
    if tide_offset_m == 0.0:
        return {"requested_m": 0.0, "applied_m": 0.0, "needs_twin": False, "message": None}
    return {
        "requested_m": tide_offset_m,
        "applied_m": 0.0,
        "needs_twin": True,
        "twin_endpoint": TWIN_ENDPOINT,
        "message": (
            f"Tide {tide_offset_m:+.1f} m is not in this answer: the emulator has no sea level. "
            "Rain, cleaning and pumps are answered here; the tide needs a full-city Twin run of "
            "about a minute."
        ),
    }


def _clean_top(
    path: Path, city: str | None, model: FlashModel, beta: NDArray[np.float64], count: int
) -> tuple[NDArray[np.float64] | None, dict[str, Any]]:
    """The blockage with the ``count`` worst pipes in the run's posterior desilted, re-joined.

    Ranked by ``beta_mean`` over the run's ``drain_health.geojson`` (Pulse's posterior for this
    cycle), ties broken by edge id so the choice never depends on file order. The posterior is
    then re-joined onto the emulator's segments with those pipes at :data:`CLEANED_BETA`, so a
    street whose worst pipe was cleaned drops to its **next-worst** pipe rather than to the
    cleaned value - the join the what-if already runs at, not a second rule for the same thing.
    """
    from varuna_flash.whatif import CLEANED_BETA
    from varuna_pulse.health import capacity_reduction_pct

    label = CLEAN_TOP_LABEL.format(n=count, s="" if count == 1 else "s")
    posterior = _posterior_edges(path)
    if not city or posterior is None or "beta_mean" not in posterior.columns:
        return None, {
            "applied": False,
            "label": label,
            "reason": f"Run {path.name} carries no learned blockage ({DRAIN_HEALTH}), so there "
            "is no ranking of pipes to clean. Re-bake the cycle.",
        }
    health = path / DRAIN_HEALTH
    key = (path.name, count, health.stat().st_mtime_ns, model.n_segments)
    held = _TOP_CLEAN_CACHE.get(key)
    if held is not None:
        return held[0].copy(), dict(held[1])

    frame = posterior.copy()
    frame["edge_id"] = frame["edge_id"].astype(str)
    frame["beta_mean"] = pd.to_numeric(frame["beta_mean"], errors="coerce")
    ranked = frame.dropna(subset=["beta_mean"]).sort_values(
        ["beta_mean", "edge_id"], ascending=[False, True], kind="stable"
    )
    top = ranked.head(count)
    chosen = set(top["edge_id"])
    hit = frame["edge_id"].isin(chosen)
    frame.loc[hit, "beta_mean"] = CLEANED_BETA
    if "capacity_reduction_pct" in frame.columns:
        frame.loc[hit, "capacity_reduction_pct"] = capacity_reduction_pct(
            np.full(int(hit.sum()), CLEANED_BETA)
        )
    try:
        learned = segment_beta(city, frame, model.segment_ids)[0]
    except SegmentBetaError as error:
        return None, {"applied": False, "label": label, "reason": str(error)}
    scenario_beta = np.where(np.isfinite(learned), learned, beta)
    moved = np.flatnonzero(np.abs(scenario_beta - beta) > 1e-12)

    try:
        pairs = _segment_edge_pairs(city)
        under = pairs[pairs["edge_id"].astype(str).isin(chosen)]
        streets = set(under["segment_id"].astype(str))
    except SegmentBetaError:
        streets = set()
    known = set(model.segment_ids)
    info = {
        "applied": True,
        "label": label,
        "pipes": [
            {
                "edge_id": str(row["edge_id"]),
                "beta_before": round(float(row["beta_mean"]), 3),
                "street": row.get("street") if isinstance(row.get("street"), str) else None,
            }
            for _, row in top.iterrows()
        ],
        "beta_after": CLEANED_BETA,
        "ranked_from": len(ranked),
        "streets_under": len(streets),
        "streets_not_in_emulator": len(streets - known),
        "segments_changed": int(moved.size),
    }
    if len(_TOP_CLEAN_CACHE) >= MAX_CACHED_RUNS:
        _TOP_CLEAN_CACHE.clear()
    _TOP_CLEAN_CACHE[key] = (scenario_beta, info)
    return scenario_beta.copy(), dict(info)


def _pump_lever(
    path: Path,
    city: str | None,
    model: FlashModel,
    n_steps: int,
    step_min: int,
) -> tuple[NDArray[np.float64] | None, NDArray[np.int64] | None, dict[str, Any]]:
    """The run's own pump plan as drawdown per segment, from each pump's arrival.

    Built from ``pump_plan.json`` with the pump board's own helpers - the pump's rated capacity
    over the 45 m disc as cm per step (:func:`varuna_products.pumps._rate_cm_per_step`), the
    travel time at the board's speed (``_travel_min``), arrival as whole steps - and applied to
    every road segment of the place it was sent: the register's listed segments for a hotspot,
    every segment of the street for a street. Where two pumps reach one segment the larger rate
    and the earlier arrival hold.
    """
    from varuna_products.pumps import _rate_cm_per_step, _travel_min

    plan_path = path / "pump_plan.json"
    if not plan_path.is_file():
        return (
            None,
            None,
            {
                "applied": False,
                "label": PUMP_LOWER_BOUND_LABEL,
                "reason": f"Run {path.name} carries no pump_plan.json, so there is no plan to "
                "price. Re-bake the cycle.",
            },
        )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assignments = plan.get("assignments") or []
    base = {
        "inventory": plan.get("inventory", "synthetic"),
        "label": PUMP_LOWER_BOUND_LABEL,
        "board_benefit_model": plan.get("benefit_model"),
    }
    if not assignments:
        return (
            None,
            None,
            {
                **base,
                "applied": False,
                "reason": "This cycle's pump plan assigns no pump: nothing crosses 45 cm where a "
                "pump could reach it inside the window.",
            },
        )

    pumps = {str(p.get("pump_id")): p for p in plan.get("pumps") or []}
    hotspots_path = path / "hotspots.json"
    register = (
        {str(h.get("hotspot_id")): h for h in json.loads(hotspots_path.read_text(encoding="utf-8"))}
        if hotspots_path.is_file()
        else {}
    )
    by_street: dict[str, list[str]] = {}
    for segment_id, name in _street_names(city).items():
        by_street.setdefault(name, []).append(segment_id)
    index = {sid: i for i, sid in enumerate(model.segment_ids)}

    rate = np.zeros(model.n_segments, dtype=np.float64)
    arrival = np.full(model.n_segments, n_steps, dtype=np.int64)
    placed: list[dict[str, Any]] = []
    unpriced: list[str] = []
    too_late: list[str] = []
    for assignment in assignments:
        target = str(assignment.get("hotspot_id"))
        name = str(assignment.get("hotspot_name") or target)
        if target.startswith("street:"):
            ids = by_street.get(target.removeprefix("street:"), [])
        else:
            ids = [str(s) for s in (register.get(target) or {}).get("segment_ids") or []]
        columns = [index[s] for s in ids if s in index]
        pump = pumps.get(str(assignment.get("pump_id")))
        if pump is None or not columns:
            unpriced.append(name)
            continue
        travel = _travel_min(pump, {"lon": assignment["lon"], "lat": assignment["lat"]})
        step = int(travel // step_min)
        if step >= n_steps:
            too_late.append(name)
            continue
        cm = _rate_cm_per_step(float(assignment.get("capacity_m3_per_h") or 0.0), step_min)
        rate[columns] = np.maximum(rate[columns], cm)
        arrival[columns] = np.minimum(arrival[columns], step)
        placed.append(
            {
                "pump_id": assignment.get("pump_id"),
                "hotspot_id": target,
                "name": name,
                "eta_min": assignment.get("eta_min"),
                "arrival_step": step,
                "segments": len(columns),
                "cm_per_step": round(cm, 3),
            }
        )
    info = {
        **base,
        "applied": bool(placed),
        "assignments": placed,
        "segments_drained": int(np.count_nonzero(rate > 0.0)),
        "not_priced": unpriced,
        "arrives_after_window": too_late,
    }
    if not placed:
        info["reason"] = (
            "None of the plan's pumps could be placed on a road segment the emulator knows, so "
            "the plan changes nothing here."
        )
        return None, None, info
    return rate, arrival, info


def _emulator_rows(
    model: FlashModel,
    depth_cm: NDArray[np.floating],
    baseline_cm: NDArray[np.floating],
    before_by_id: dict[str, list[float]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every street the scenario moved by :data:`CHANGE_CM` or more, dry-at-baseline included.

    The delta-correction is applied **per step**, the construction the pump board prices with:
    ``after[t] = max(before[t] + emulator_scenario[t] - emulator_baseline[t], 0)``. So a street's
    peak, its minutes above a threshold and its row on the map all come from one series.

    ``before`` is the run's own forecast, which lists no street below its wet threshold. A street
    it does not list is compared from 0 cm and its row carries ``before_cm: null`` - the truth is
    somewhere under 5 cm, and the change is read from the emulator either way.
    """
    index = {sid: i for i, sid in enumerate(model.segment_ids)}
    n_steps = int(depth_cm.shape[0])
    step_delta = np.asarray(depth_cm, dtype=np.float64) - np.asarray(baseline_cm, dtype=np.float64)
    before = np.zeros_like(step_delta)
    listed = np.zeros(model.n_segments, dtype=bool)
    not_modelled = 0
    for segment_id, series in before_by_id.items():
        k = index.get(segment_id)
        if k is None:
            not_modelled += 1
            continue
        values = np.asarray(series[:n_steps], dtype=np.float64)
        before[: values.size, k] = values
        listed[k] = True
    after = np.round(np.maximum(before + step_delta, 0.0), 1)
    empty = np.zeros(model.n_segments)
    before_peak = before.max(axis=0) if n_steps else empty
    after_peak = after.max(axis=0) if n_steps else empty
    delta = np.round(after_peak - before_peak, 1)
    changed = np.flatnonzero(np.abs(delta) >= CHANGE_CM)
    changed = changed[np.argsort(-np.abs(delta[changed]), kind="stable")]

    rows = [
        {
            "segment_id": model.segment_ids[k],
            "before_cm": round(float(before_peak[k]), 1) if listed[k] else None,
            "after_cm": round(float(after_peak[k]), 1),
            "delta_cm": round(float(delta[k]), 1),
            "newly_wet": bool(not listed[k] and after_peak[k] >= WET_CM),
        }
        for k in changed.tolist()
    ]
    counts = {
        "n_changed": len(rows),
        "n_worse": sum(1 for r in rows if r["delta_cm"] > 0),
        "n_improved": sum(1 for r in rows if r["delta_cm"] < 0),
        "n_newly_wet": sum(1 for r in rows if r["newly_wet"]),
        "n_dry_before": sum(1 for r in rows if r["before_cm"] is None),
        "n_segments_compared": int(listed.sum()),
        "n_not_modelled": not_modelled,
    }
    return rows, counts


def _emulator_hotspots(
    path: Path,
    model: FlashModel,
    depth_cm: NDArray[np.floating],
    baseline_cm: NDArray[np.floating],
    step_min: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Each ranked hotspot, before and after, from the run's own ``hotspots.json`` series.

    Before is the hotspot's own depth series (the one the console's rail and the pump board
    read). The change added to it, step by step, is the emulator's per-step difference **averaged
    over the listed segments it knows** - the aggregation the physics check compares against the
    Twin (its ``emulator_delta_cm``), so the table and the check talk about the same number.

    Not the emulator's deepest segment, which is what the pump board reads a drawdown at. That was
    tried and measured on the 08:40 cycle at rain 1.3x: Dadar TT read 7.0 to 29.6 cm, because the
    emulator's deepest street there ponds far deeper than the Twin's junction and a delta taken
    from it is amplified by the emulator's level error (CSI 0.085 at 30 cm). A pump drains every
    listed segment at one rate, so for the pump lever the two readings nearly coincide.

    Returns the rows and the names of hotspots with no segment the emulator knows.
    """
    hotspots_path = path / "hotspots.json"
    if not hotspots_path.is_file():
        return [], []
    index = {sid: i for i, sid in enumerate(model.segment_ids)}
    step_delta = np.asarray(depth_cm, dtype=np.float64) - np.asarray(baseline_cm, dtype=np.float64)

    def minutes(series: list[float]) -> dict[str, int]:
        return {str(t): step_min * sum(1 for v in series if v > t) for t in EMULATOR_MINUTES_CM}

    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for hotspot in json.loads(hotspots_path.read_text(encoding="utf-8")):
        listed = [str(s) for s in hotspot.get("segment_ids") or []]
        present = [s for s in listed if s in index]
        before = [float(v) for v in hotspot.get("depth_cm") or []]
        name = str(hotspot.get("name") or hotspot.get("hotspot_id"))
        if not present or not before:
            missing.append(name)
            continue
        columns = np.asarray([index[s] for s in present], dtype=np.int64)
        mean_delta = step_delta[:, columns].mean(axis=1)
        steps = min(len(before), step_delta.shape[0])
        after = [round(max(before[t] + float(mean_delta[t]), 0.0), 1) for t in range(steps)]
        after += before[steps:]
        before_peak = round(max(before), 1)
        after_peak = round(max(after), 1)
        rows.append(
            {
                "hotspot_id": hotspot.get("hotspot_id"),
                "name": hotspot.get("name"),
                "lon": hotspot.get("lon"),
                "lat": hotspot.get("lat"),
                "before_cm": before_peak,
                "after_cm": after_peak,
                "delta_cm": round(after_peak - before_peak, 1),
                "minutes_above_before": minutes(before),
                "minutes_above_after": minutes(after),
                "segments": len(present),
                "segments_missing": len(listed) - len(present),
            }
        )
    rows.sort(key=lambda r: (-abs(r["delta_cm"]), -r["before_cm"]))
    return rows, missing


def _unchanged_hotspots(path: Path, step_min: int) -> list[dict[str, Any]]:
    """Each ranked hotspot with its own series as both before and after: the answer to a scenario
    with no lever set, read from ``hotspots.json`` without running anything. Same shape as
    :func:`_emulator_hotspots`, deepest first."""
    hotspots_path = path / "hotspots.json"
    if not hotspots_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for hotspot in json.loads(hotspots_path.read_text(encoding="utf-8")):
        series = [float(v) for v in hotspot.get("depth_cm") or []]
        if not series:
            continue
        peak = round(max(series), 1)
        minutes = {str(t): step_min * sum(1 for v in series if v > t) for t in EMULATOR_MINUTES_CM}
        rows.append(
            {
                "hotspot_id": hotspot.get("hotspot_id"),
                "name": hotspot.get("name"),
                "lon": hotspot.get("lon"),
                "lat": hotspot.get("lat"),
                "before_cm": peak,
                "after_cm": peak,
                "delta_cm": 0.0,
                "minutes_above_before": minutes,
                "minutes_above_after": dict(minutes),
                "segments": len(hotspot.get("segment_ids") or []),
                "segments_missing": 0,
            }
        )
    rows.sort(key=lambda r: -r["before_cm"])
    return rows


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _clean_top_note(info: dict[str, Any]) -> str:
    if not info["applied"]:
        return f"{info['label']}: not applied. {info['reason']}"
    note = (
        f"{info['label']}: {_plural(len(info['pipes']), 'pipe')} cleaned to beta = "
        f"{info['beta_after']}, which changed the blockage of "
        f"{_plural(info['segments_changed'], 'street')} in the emulator. A street whose worst "
        "pipe was cleaned runs at its next-worst pipe."
    )
    if info["streets_not_in_emulator"]:
        note += (
            f" {_plural(info['streets_not_in_emulator'], 'street')} under those pipes "
            "carry an id the fitted emulator does not know and are not in the answer."
        )
    return note


def _pump_note(info: dict[str, Any]) -> str:
    if not info["applied"]:
        return f"Pump plan not applied: {info['reason']}"
    note = (
        f"The cycle's pump plan: {_plural(len(info['assignments']), 'pump')} draining "
        f"{_plural(info['segments_drained'], 'street')} from each pump's arrival. "
        f"{info['label']} Synthetic pump inventory."
    )
    if info["arrives_after_window"]:
        note += (
            f" {_plural(len(info['arrives_after_window']), 'pump')} would arrive after the "
            "forecast window and drain nothing in it."
        )
    if info["not_priced"]:
        note += (
            f" {_plural(len(info['not_priced']), 'assignment')} could not be placed on a road "
            "segment the emulator knows: " + ", ".join(info["not_priced"][:UNMATCHED_NAMED]) + "."
        )
    return note


def _twin_levers_left_out(body: dict[str, Any], *, in_check: bool) -> tuple[list[str], list[str]]:
    """The emulator-only levers a Twin run was asked for and does not apply, and why.

    The Twin has no pump sink on the street, so the pump plan is never in a Twin answer. "Clean
    top N" is applied by the physics check's crop (``in_check``), which runs at the posterior it
    ranks by, and left out of the full-city job, which is levelled on the prior. Named in the response rather than dropped, so a Twin answer is never
    read as the answer to a scenario it did not run.
    """
    left: list[str] = []
    notes: list[str] = []
    if bool(body.get("pump_plan")):
        left.append("pump_plan")
        notes.append(
            (
                "The pump plan is not in this check: the Twin has no pump sink on the street, so "
                "both models ran without it, and the pumps' effect in the what-if is the "
                "emulator's alone, a lower bound."
            )
            if in_check
            else "The pump plan is not in this Twin run: the Twin has no pump sink on the "
            "street, so the pumps are priced by the emulator only, as a lower bound."
        )
    if not in_check and _clean_top_count(body.get("clean_top")):
        left.append("clean_top")
        notes.append(
            "Clean top pipes by learned blockage is not in this Twin run, which is levelled on "
            "the city's prior blockage; clean those streets by name to run them here."
        )
    return left, notes


def _summary(counts: dict[str, Any], tide: dict[str, Any]) -> str:
    """The one sentence on top of the answer (SPEC.md 6.8).

    Worded in section 6.8's vocabulary - a road *segment*, not a street - and led by the count
    deeper, which is the sentence the demo reads out at 4:30 ("1,687 segments deeper"). A side
    with nothing on it is left out rather than printed as a zero: "3 segments shallower", not
    "0 segments deeper, 3 shallower", which is what "Clean top 14" read on the 08:40 cycle.
    """
    worse, improved = counts["n_worse"], counts["n_improved"]
    if not counts["n_changed"]:
        head = "Nothing changed"
    elif worse and improved:
        head = (
            f"{_plural(worse, 'segment')} deeper, {improved:,} shallower, "
            f"each by {CHANGE_CM} cm or more"
        )
    elif worse:
        head = f"{_plural(worse, 'segment')} deeper, each by {CHANGE_CM} cm or more"
    else:
        head = f"{_plural(improved, 'segment')} shallower, each by {CHANGE_CM} cm or more"
    if tide["needs_twin"]:
        return f"{head}. Tide not included: it runs on the Twin."
    return f"{head}."


@router.post("/whatif", summary="What-if via the emulator, levelled on the run's own physics")
def whatif(body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """Scale the rain, clean pipes or run the pump plan, and report every street that changes.

    Body: ``{run_id?, rain_scale?, cleaned_segments?, clean_top?, pump_plan?, tide_offset_m?}``.

    * ``cleaned_segments`` are road-segment ids. Ids this city has no segment for are reported in
      ``cleaned_unmatched`` and never counted as cleaned; a request where *none* of them match is
      refused with 422 ``unknown_segments`` rather than answered for a scenario nobody asked for.
    * ``clean_top`` (``true`` or a number) desilts the worst pipes in the run's learned blockage,
      city-wide - section 7.7's "Clean top 14".
    * ``pump_plan: true`` runs the cycle's own ``pump_plan.json`` from each pump's arrival, and
      the answer is labelled a lower bound.
    * ``tide_offset_m`` is **neither refused nor applied**: the emulator has no sea level, so the
      answer is the other levers with the tide left out, and ``tide`` says so and names
      ``POST /v1/whatif/twin``, which answers the tide on the full-city Twin.

    Every street whose peak moves by :data:`CHANGE_CM` or more is in ``segments``, including
    streets the run had dry, and every count is counted from that list. A scenario with no lever
    set answers "Nothing changed" without running the emulator.
    """
    from varuna_flash.whatif import run_scenario

    started = perf_counter()
    run_id = body.get("run_id")
    path = run_dir(str(run_id)) if run_id else _latest_run()
    if not (path / "segments_wet.json").is_file():
        raise api_error(404, "run_not_found", f"Run {path.name} has no segment forecast.")

    meta = json.loads((path / "run.json").read_text(encoding="utf-8"))
    rain = meta.get("rain_aoi_mm_h")
    if not rain:
        raise api_error(
            409,
            "no_rain_series",
            f"Run {path.name} predates the stored rain series, so there is no storm to scale. "
            "Re-bake the cycle.",
        )

    rain_scale = _lever_number(body, "rain_scale", 1.0)
    lo, hi = RAIN_SCALE_RANGE
    if not lo <= rain_scale <= hi:
        raise api_error(
            422, "out_of_range", f"Rain scale runs from {lo}x to {hi}x; got {rain_scale}x."
        )
    tide = _tide_block(_lever_number(body, "tide_offset_m", 0.0) + 0.0)
    requested = {str(s) for s in body.get("cleaned_segments") or []}
    top_count = _clean_top_count(body.get("clean_top"))
    wants_pumps = bool(body.get("pump_plan"))
    city = meta.get("city")
    step_min = int(meta.get("step_min") or 5)

    base_response: dict[str, Any] = {
        "run_id": meta.get("run_id", path.name),
        "method": "twin_level_emulator_delta",
        "engine": "emulator",
        "rain_scale": rain_scale,
        "tide_offset_m": tide["requested_m"],
        "tide": tide,
        "change_threshold_cm": CHANGE_CM,
        "before_floor_cm": WET_CM,
    }
    if abs(rain_scale - 1.0) < 1e-9 and not requested and not top_count and not wants_pumps:
        # Nothing to run: the emulator's two runs would be the same run. Answered without it, so
        # a slider left at its default costs nothing and the screen draws no grey streets.
        counts = {"n_changed": 0, "n_worse": 0, "n_improved": 0, "n_newly_wet": 0}
        ms = (perf_counter() - started) * 1000.0
        return {
            **base_response,
            "nothing_changed": True,
            "summary": _summary(counts, tide),
            **counts,
            "n_dry_before": 0,
            "cleaned_segments": [],
            "cleaned_unmatched": [],
            "levers": {"pump_plan": None, "clean_top": None},
            "segments": [],
            # The run's own hotspots, each unchanged: a table of "no hotspot to compare" beside
            # "Nothing changed" would say the run has none.
            "hotspots": _unchanged_hotspots(path, step_min),
            "hotspots_not_modelled": [],
            "largest_changes": [],
            "worst_after": [],
            "ms": round(ms, 1),
            "notes": [
                "No lever is set: rain 1.0x, nothing cleaned, no pumps. The forecast is the "
                "run's own.",
                *([tide["message"]] if tide["needs_twin"] else []),
            ],
        }

    model = _model()
    cleaned, unmatched = _resolve_cleaned(requested, model)
    beta, beta_source = _beta_vector(path, city, model)
    n_steps = len(rain)

    scenario_beta = None
    clean_top_info: dict[str, Any] | None = None
    if top_count:
        scenario_beta, clean_top_info = _clean_top(path, city, model, beta, top_count)
    pump_cm = pump_from = None
    pump_info: dict[str, Any] | None = None
    if wants_pumps:
        pump_cm, pump_from, pump_info = _pump_lever(path, city, model, n_steps, step_min)

    try:
        scenario = run_scenario(
            model,
            np.asarray(rain, dtype=np.float64),
            beta=beta,
            rain_scale=rain_scale,
            cleaned_segments=cleaned,
            pump_cm_per_step=pump_cm,
            pump_from_step=pump_from,
            scenario_beta=scenario_beta,
        )
    except ValueError as error:
        raise api_error(422, "unsupported_scenario", str(error)) from error

    wet = json.loads((path / "segments_wet.json").read_text(encoding="utf-8"))
    rows, counts = _emulator_rows(
        model, scenario.depth_cm, scenario.baseline_cm, wet.get("depth_cm", {})
    )
    hotspots, hotspots_missing = _emulator_hotspots(
        path, model, scenario.depth_cm, scenario.baseline_cm, step_min
    )
    names = _street_names(city)
    # `display_name` names the 52.6 % of segments OSM does not ("off Dr Ambedkar Road"), so the
    # list never reads "Unnamed road"; `name` stays OSM's (varuna_api.street_names). It is always
    # a string: a segment the city build does not carry reads by its class, in the city.
    largest = [
        {
            **row,
            "name": names.get(row["segment_id"]),
            "display_name": street_names.display_name_for(
                city, row["segment_id"], names.get(row["segment_id"])
            ),
        }
        for row in rows[:LARGEST_CHANGES]
    ]

    lever_notes: list[str] = []
    if tide["needs_twin"]:
        lever_notes.append(tide["message"])
    if clean_top_info is not None:
        lever_notes.append(_clean_top_note(clean_top_info))
    if pump_info is not None:
        lever_notes.append(_pump_note(pump_info))
    if counts["n_dry_before"]:
        lever_notes.append(
            f"{counts['n_dry_before']:,} of the changed streets were below {WET_CM:.0f} cm in the "
            "run, which does not store their depth; their change is measured from 0 cm."
        )
    if hotspots_missing:
        lever_notes.append(
            f"{len(hotspots_missing)} hotspots have no road segment in the fitted emulator and "
            "are not in the table: " + ", ".join(hotspots_missing[:UNMATCHED_NAMED]) + "."
        )

    ms = (perf_counter() - started) * 1000.0
    log.info(
        "api.whatif",
        run_id=path.name,
        ms=round(ms, 1),
        rain_scale=rain_scale,
        tide_ignored=tide["needs_twin"],
        cleaned=len(cleaned),
        unmatched=len(unmatched),
        clean_top=top_count,
        pumps=bool(pump_info and pump_info["applied"]),
        beta_kind=beta_source["kind"],
        changed=counts["n_changed"],
        worse=counts["n_worse"],
        improved=counts["n_improved"],
    )
    return {
        **base_response,
        "emulator": {
            "rmse_cm": round(model.rmse_cm, 2),
            "csi_30cm": round(model.csi_30cm, 3),
            "n_training_runs": model.n_training_runs,
        },
        # Which blockage the emulator ran at, and how much of it Pulse actually learned. The
        # scenario is a difference between two runs at this vector, so it is as much a part of
        # the answer's provenance as the emulator's own skill.
        "beta_source": beta_source,
        "nothing_changed": counts["n_changed"] == 0,
        "summary": _summary(counts, tide),
        **counts,
        # Only what was actually cleaned. `cleaned_unmatched` carries the ids this city has no
        # segment for, so a partly wrong request is visible rather than absorbed.
        "cleaned_segments": sorted(cleaned),
        "cleaned_unmatched": unmatched,
        "levers": {"pump_plan": pump_info, "clean_top": clean_top_info},
        # Every changed street, so the counts above are the length of what the map draws.
        "segments": rows,
        "hotspots": hotspots,
        "hotspots_not_modelled": hotspots_missing,
        "largest_changes": largest,
        "worst_after": sorted(rows, key=lambda r: -r["after_cm"])[:20],
        "ms": round(ms, 1),
        "notes": [
            *scenario.notes,
            _beta_note(beta_source),
            *lever_notes,
            *(
                [
                    f"{len(unmatched)} of the ids asked for are not road segments in this city "
                    f"and were not cleaned; they are listed in cleaned_unmatched."
                ]
                if unmatched
                else []
            ),
            "The level is the Twin's own forecast for this run; the emulator supplies only the "
            "difference the scenario makes, step by step. Run the physics check for a scenario "
            "the Twin has solved end to end.",
        ],
    }


# =========================================================== the physics check (task P7.8)
#
# SPEC.md 7.7 asks for one sentence on screen - "Emulator vs physics: max difference 4 cm at
# Sion Circle" - and section 14 gives it 10 s. A full-AOI Mumbai Twin run is 58-114 s in six of
# the seven baked cycles, so the check has to run on a crop. What a crop costs, and what it costs
# in accuracy, was measured before this was built (Intel i5-1155G7, 8 logical cores, one 36-step
# coupled run each, warm, 10-12 python processes):
#
#     window            grid     nodes  edges   wall    peak depth   mass balance
#     +-4 cells  270 m  9 x 9       48     43   2.56 s     10.22 cm       5.0e-15
#     +-16      990 m  33 x 33     631    620   1.58 s     87.44 cm       1.7e-02
#     +-40    2,430 m  81 x 81   3,112  3,056   4.87 s    123.33 cm       5.1e-03
#
# Three things follow, and all three are in the response rather than only here.
#
# **The crop is fast enough and the whole city is not.** Two runs at +-16 cells are about 3 s
# against the 10 s budget. The same three runs measured earlier the same evening under another
# job's load took 33-37 s each for identical output, so every timing this endpoint reports is
# its own measurement of its own call, never this table.
#
# **A crop's absolute depth is not the city's.** The +-16 window peaks at 87 cm and the +-40
# window at 123 cm on the same storm, because a smaller window cuts off more of the contributing
# area, and the Twin's own hotspot product reads 12.0 cm at the junction where the +-16 crop's
# centre cell reads 15.4. So this endpoint compares **differences**, not levels: the Twin runs
# the crop twice, baseline and scenario, and its delta is compared with the emulator's delta.
# Whatever the crop's boundary does wrong, it does wrong in both runs and largely cancels - which
# is the same delta-correction argument the module docstring makes for `/v1/whatif` itself.
#
# **The crop's mass balance depends on the blockage it is run at, so it is reported per call.**
# At the city's *prior* blockage the +-16 window closed at 1.7e-02, seventeen times the Twin's
# own 1e-03 budget; at the run's *posterior*, which is what this endpoint runs at, the same
# window closed at exactly 0.0 on every call measured on 2026-09-24, with the solver's own
# ledger reporting `clamp_created_m3` zero. One number is not a rule either way, which is why
# both runs' audits are in the response rather than a sentence in this comment.
#
# **Cost, measured on 2026-09-24 with twelve python processes on an Intel i5-1155G7.** The first
# call in a fresh process is 19.4-22.6 s: it pays for the city load (terrain 1.4 s, drain graph
# 2.0-5.2 s, tide 4.9 s cold) and for Numba's compile. Every call after that is **2.4-4.7 s**,
# two coupled Twin runs included, against section 14's 10 s. So the budget is met warm and
# missed on the first call of a process - the same shape as the pandas import above, and the
# same answer: the API is started before the judges arrive. `_CITY_CACHE` is what makes the
# second call the normal one; the response reports its own `ms` either way and never this
# comment's.

TWIN_BUDGET_MS = 10_000
"""Section 14's budget for the physics check, in milliseconds. Reported against, not asserted."""

CROP_PAD_CELLS = 16
"""Half-width of the window, in 30 m cells: a 33 x 33 crop, 990 m on a side.

Chosen from the sweep above rather than by taste. Four cells is too small to wet anything
(10.22 cm peak where the +-40 window reaches 123.33 cm); forty costs 4.87 s a run, so two runs
plus the city load would sit on the 10 s budget. Sixteen runs in about 1.6 s and still carries
620 pipes and 631 manholes around the junction."""

CROP_HOTSPOTS = 6
"""Most hotspots compared. Only those inside the one window are checked; the rest are named."""

MIN_WINDOW_CELLS = 5
"""A window smaller than this is refused rather than run: there is no junction inside it."""

_CITY_CACHE: dict[tuple[str, str], tuple[Any, Any, Any]] = {}
"""Terrain, drain network and tide per (city, bundle), because loading them is the slow part.

Measured cold on this machine: terrain 1.41 s, network 2.00-5.23 s, tide 4.85 s - between 8 and
11 s before a single step is taken, which is the whole budget spent on I/O. They are pure
functions of files on disk that a bake does not rewrite mid-session, so one copy per process is
the same data every call would have read."""


def _city_inputs(city: str, bundle: str):
    """Terrain grid, drain network and tide series for a city, loaded once per process."""
    key = (city, bundle)
    cached = _CITY_CACHE.get(key)
    if cached is not None:
        return cached
    from varuna_twin.city import load_network, load_terrain, load_tide

    try:
        loaded = (load_terrain(city), load_network(city), load_tide(bundle, city=city))
    except FileNotFoundError as error:
        raise api_error(
            503,
            "no_city_grid",
            f"The physics check needs {city}'s conditioned terrain and drain graph, and they "
            f"are not built: {error}. Run `make city CITY={city}`.",
        ) from error
    _CITY_CACHE[key] = loaded
    return loaded


def _cell_of(terrain, lon: float, lat: float) -> tuple[int, int]:
    """Grid row and column of a WGS84 point, from the terrain's own affine transform."""
    from pyproj import Transformer

    res, _, left, _, _, top = terrain.transform
    x, y = Transformer.from_crs("EPSG:4326", terrain.crs, always_xy=True).transform(lon, lat)
    return int((top - y) // res), int((x - left) // res)


def _crop(terrain, network, row: int, col: int, pad: int):
    """A window of the city and the drain graph inside it, as a Twin can run them.

    The surface crop is a plain slice with the transform shifted to the window's own top-left
    corner, so the cropped grid is georeferenced correctly and nothing downstream has to know it
    is a crop.

    The drain crop is the part with a decision in it. An edge with one end inside and one outside
    is kept, and its outside node becomes a **free outfall** with no 2D cell. Dropping such an
    edge instead would dam the window: every pipe that carries water out of the junction would
    end in a closed manhole, and the check would report a flood the city does not have. A free
    outfall is the opposite approximation - water leaves as fast as the pipe can carry it, with
    nothing downstream to back it up - and it is the one that errs toward the crop draining too
    well rather than too badly, which is the safer direction for a tool whose answer is a
    *difference* between two runs that share the boundary.

    **The sea goes with the window.** The crop is the terrain type
    :func:`varuna_twin.city.load_terrain` returns, carrying the city's own sea raster cut to the
    same window, because the Twin reads the sea off the terrain and the rule it applies depends
    on whether there is a raster at all. With one, the tide is imposed on the sea cells and
    nothing else, rain on them is not booked, and a node standing on one exchanges nothing.
    Without one it falls back to the old rule - the tidal outfalls' cells are the sea - so a crop
    that dropped the raster ran a different coast from the city it was cut from: a tidal outfall
    on the land behind the wall became a Dirichlet sea cell, and the real sea ponded rain as if it
    were land. A window with no sea cell carries an empty mask, not ``None``: the city has a
    coastline, and its absence from this window must not bring the old rule back. A city built
    before the sea step carries ``None`` and runs as it always did.

    Returns the cropped terrain, the cropped network and the window ``(row0, row1, col0, col1)``.
    """
    import numpy as np
    from varuna_twin.city import CityTerrain
    from varuna_twin.types import DrainNetwork

    res, _, left, _, _, top = terrain.transform
    r0, r1 = max(row - pad, 0), min(row + pad + 1, terrain.n_rows)
    c0, c1 = max(col - pad, 0), min(col + pad + 1, terrain.n_cols)
    if (r1 - r0) < MIN_WINDOW_CELLS or (c1 - c0) < MIN_WINDOW_CELLS:
        raise api_error(
            422,
            "hotspot_outside_grid",
            f"The window around the chosen hotspot is {r1 - r0} x {c1 - c0} cells, which is not "
            "a junction. The hotspot sits on the edge of the city grid; pick another.",
        )
    window = (slice(r0, r1), slice(c0, c1))
    sea = getattr(terrain, "sea", None)
    crop_terrain = CityTerrain(
        z=terrain.z[window].copy(),
        manning_n=terrain.manning_n[window].copy(),
        blocked=terrain.blocked[window].copy(),
        imperviousness=terrain.imperviousness[window].copy(),
        cn=terrain.cn[window].copy(),
        res_m=terrain.res_m,
        crs=terrain.crs,
        transform=(res, 0.0, left + c0 * res, 0.0, -res, top - r0 * res),
        sea=None if sea is None else np.asarray(sea, dtype=np.bool_)[window].copy(),
    )

    inside = (
        (network.cell_row >= r0)
        & (network.cell_row < r1)
        & (network.cell_col >= c0)
        & (network.cell_col < c1)
    )
    keep_edge = inside[network.from_node] | inside[network.to_node]
    keep_node = inside.copy()
    keep_node[network.from_node[keep_edge]] = True
    keep_node[network.to_node[keep_edge]] = True
    nodes = np.flatnonzero(keep_node)
    edges = np.flatnonzero(keep_edge)
    renumber = np.full(network.n_nodes, -1, dtype=np.int64)
    renumber[nodes] = np.arange(nodes.size)

    outer = ~inside[nodes]
    boundary = network.boundary[nodes].copy()
    boundary[outer] = 2  # free outfall; see the docstring
    crop_network = DrainNetwork(
        node_ids=tuple(network.node_ids[i] for i in nodes),
        z_ground=network.z_ground[nodes].copy(),
        z_invert=network.z_invert[nodes].copy(),
        storage_area=network.storage_area[nodes].copy(),
        inlet_length=network.inlet_length[nodes].copy(),
        inlet_area=network.inlet_area[nodes].copy(),
        kappa=network.kappa[nodes].copy(),
        boundary=boundary.astype(np.int8),
        flap_gate=network.flap_gate[nodes].copy(),
        # -1 on a halo node: it exchanges nothing with the street, because its street is not in
        # the window. Leaving the city's own row and column here would index past the crop.
        cell_row=np.where(outer, -1, network.cell_row[nodes] - r0).astype(np.int32),
        cell_col=np.where(outer, -1, network.cell_col[nodes] - c0).astype(np.int32),
        edge_ids=tuple(network.edge_ids[i] for i in edges),
        from_node=renumber[network.from_node[edges]].astype(np.int32),
        to_node=renumber[network.to_node[edges]].astype(np.int32),
        length=network.length[edges].copy(),
        area=network.area[edges].copy(),
        hydraulic_radius=network.hydraulic_radius[edges].copy(),
        diameter=network.diameter[edges].copy(),
        edge_manning_n=network.edge_manning_n[edges].copy(),
        q_full=network.q_full[edges].copy(),
        beta=network.beta[edges].copy(),
    )
    return crop_terrain, crop_network, (r0, r1, c0, c1)


def _crop_sea_notes(crop_terrain, crop_network) -> list[str]:
    """What the physics check's window holds of the city's sea: one note, or none.

    A window with no sea cell - every window away from the coast, and every window cut from a
    city built before the sea step - gets no note, so its response is the one it always was.
    """
    import numpy as np

    sea = getattr(crop_terrain, "sea", None)
    if sea is None or not bool(np.asarray(sea).any()):
        return []
    mask = np.asarray(sea, dtype=np.bool_)
    row = np.asarray(crop_network.cell_row, dtype=np.int64)
    col = np.asarray(crop_network.cell_col, dtype=np.int64)
    inside = (row >= 0) & (col >= 0)  # a halo node carries -1: its street is not in the window
    on_sea = int(mask[row[inside], col[inside]].sum())
    manholes = (
        "no manhole stands on one"
        if on_sea == 0
        else f"the {_plural(on_sea, 'manhole')} standing on them exchange no water with the "
        "street, though their pipes still carry water through them"
    )
    cells = int(mask.sum())
    return [
        f"{_plural(cells, 'cell')} of the window {'is' if cells == 1 else 'are'} the city's sea: "
        f"both runs hold them at the tide's level and book no rain on them, and {manholes}."
    ]


def _crop_beta(network, posterior: pd.DataFrame | None, cleaned_edges: set[str]) -> NDArray:
    """Blockage per cropped edge: Pulse's posterior where it learned one, then the cleaning.

    The city's ``drain_edges.parquet`` carries the *prior*, and that is what ``load_network``
    puts in ``network.beta``. Running the physics check at the prior while the emulator ran at
    the posterior would make the two disagree about the drains before they disagreed about
    anything else, and the disagreement on screen would be that substitution rather than the
    emulator's error.
    """
    import numpy as np

    beta = np.asarray(network.beta, dtype=np.float64).copy()
    if posterior is not None and "edge_id" in posterior.columns:
        learned = dict(
            zip(
                posterior["edge_id"].astype(str),
                pd.to_numeric(posterior["beta_mean"], errors="coerce"),
                strict=False,
            )
        )
        for index, edge_id in enumerate(network.edge_ids):
            value = learned.get(str(edge_id))
            if value is not None and np.isfinite(value):
                beta[index] = float(value)
    if cleaned_edges:
        from varuna_flash.whatif import CLEANED_BETA

        for index, edge_id in enumerate(network.edge_ids):
            if str(edge_id) in cleaned_edges:
                beta[index] = CLEANED_BETA
    return beta


def _edges_under(city: str, segments: set[str]) -> set[str]:
    """Every pipe the inferred graph puts under the given road segments.

    The same incidence ``varuna_pulse.join.segment_beta`` reads, so cleaning a segment in the
    emulator and cleaning it in the Twin mean the same pipes.
    """
    if not segments:
        return set()
    from varuna_pulse.join import segment_edges

    pairs = segment_edges(city)
    hit = pairs[pairs["segment_id"].astype(str).isin(segments)]
    return set(hit["edge_id"].astype(str))


def _run_crop(terrain, network, beta, rain_mm_h, t0, tide):
    """One coupled Twin run on the crop, at a given blockage and rain series."""
    from dataclasses import replace

    import numpy as np
    from varuna_twin.runner import run_twin
    from varuna_twin.types import TwinInputs

    rain = np.asarray(rain_mm_h, dtype=np.float64)
    cube = np.broadcast_to(rain[:, None, None], (rain.size, terrain.n_rows, terrain.n_cols)).copy()
    return run_twin(
        TwinInputs(
            terrain=terrain,
            network=replace(network, beta=beta),
            rain_mm_h=cube,
            t0=t0,
            step_min=5,
            tide=tide,
        )
    )


def _physics_on_twin(
    path: Path, meta: dict[str, Any], body: dict[str, Any], tide_offset: float, started: float
) -> dict[str, Any]:
    """The physics check's answer to a tide scenario: it runs on the Twin, whose answer is physics.

    Nothing is run here. The full-city Twin is a job of about a minute that holds the host, and
    a check button is not where that should start unasked; the response carries the body to post
    to ``POST /v1/whatif/twin`` and whether this exact scenario is already answered in the cache,
    so the screen can offer it at once. ``agrees`` is null rather than false: there is no second
    model to disagree with.
    """
    rain_scale = _lever_number(body, "rain_scale", 1.0)
    requested = sorted({str(s) for s in body.get("cleaned_segments") or []})
    job_body: dict[str, Any] = {
        "run_id": path.name,
        "rain_scale": rain_scale,
        "tide_offset_m": tide_offset,
        "cleaned_segments": requested,
        # Carried so the job names them in `levers_left_out` rather than answering as if they
        # had never been asked for.
        **({"pump_plan": True} if body.get("pump_plan") else {}),
        **({"clean_top": body["clean_top"]} if _clean_top_count(body.get("clean_top")) else {}),
    }
    cached: str | None = None
    expected: int | None = None
    expected_from = "The run records no stage timings to estimate from."
    try:
        city = str(meta["city"])
        edges, *_ = _twin_cleaning(city, set(requested), _known_segments(city))
        key = scenario_key(_quantise(rain_scale), _quantise(tide_offset), edges)
        fingerprint = run_fingerprint(path)
        hit = _cached(path.name, key, fingerprint)
        cached = None if hit is None else str(hit[1]["source"])
        expected, expected_from = _expected_ms(
            meta,
            _rain_key(meta) in _RAIN_CACHE,
            1 if baseline_cached(path.name, fingerprint) else 2,
        )
    except (HTTPException, SegmentBetaError, KeyError, OSError, ValueError) as error:
        # Only the offer is lost: the job endpoint refuses the same scenario with its own reason.
        log.info("api.physics_check_twin_offer", run_id=path.name, error=str(error))
    enabled = twin_scenario_enabled()
    left_out, left_out_notes = _twin_levers_left_out(body, in_check=False)
    ms = (perf_counter() - started) * 1000.0
    return {
        "run_id": meta.get("run_id", path.name),
        "method": TWIN_SCENARIO_METHOD,
        "runs_on_twin": True,
        "summary": (
            f"This scenario runs on the Twin: with the tide at {tide_offset:+.1f} m its answer is "
            "the physics, so there is no emulator answer to check."
        ),
        "tolerance_cm": PHYSICS_TOLERANCE_CM,
        "agrees": None,
        "max_diff_cm": None,
        "max_diff_hotspot": None,
        "hotspots": [],
        "hotspots_outside_window": [],
        "rain_scale": rain_scale,
        "tide_offset_m": tide_offset,
        "cleaned_segments": requested,
        "twin_job": {
            "endpoint": TWIN_ENDPOINT,
            "body": job_body,
            "enabled": enabled,
            "cached": cached,
            "expected_ms": expected,
            "expected_from": expected_from,
        },
        "levers_not_checked": left_out,
        "twin_ms": 0.0,
        "ms": round(ms, 1),
        "budget_ms": TWIN_BUDGET_MS,
        "within_budget": True,
        "notes": [
            "Flash-lite has no sea level, so a tide scenario has no emulator answer and nothing "
            "for the physics to check. It runs on one full-city coupled Twin at the cycle's own "
            "inputs, and that run is the answer.",
            (
                "This exact scenario is already answered for this run; posting it returns the "
                "stored answer at once."
                if cached
                else "The full-city run takes about a minute and holds the server while it runs."
                if enabled
                else f"The full-city Twin what-if is off on this server ({TWIN_JOB_ENV}=0)."
            ),
            *left_out_notes,
        ],
    }


@router.post(
    "/whatif/physics-check",
    summary="Re-run the Twin on a what-if scenario and report the disagreement",
)
def physics_check(body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """Run the coupled Twin twice on a window around the hotspots and print the disagreement.

    Body: the same ``{run_id?, rain_scale?, cleaned_segments?, clean_top?, pump_plan?,
    tide_offset_m?}`` ``/v1/whatif`` takes, plus ``hotspot_ids?`` and ``pad_cells?``, so the
    console can post the scenario object it already has. ``clean_top`` desilts the same pipes in
    both models; ``pump_plan`` is run by neither, because the Twin has no pump sink, and is named
    in ``levers_not_checked`` rather than dropped.

    **What is compared.** The what-if's answer at a hotspot is the Twin's own level plus the
    emulator's delta. The check's answer is the Twin's delta between two crop runs of the same
    scenario. So the number reported per hotspot is ``|emulator delta - Twin delta|``, and the
    headline is section 7.7's sentence over the hotspots inside the window. Comparing *levels*
    would be comparing a 990 m crop against the whole AOI, which the crop sweep in this module
    measured at 87 cm against 123 cm on the same storm - a disagreement about the window, not
    about the emulator.

    **Two samplings of one junction.** The Twin's delta is read at the hotspot's own 30 m cell;
    the emulator's is the mean over the road segments the hotspot register lists, each of which
    is the 90th percentile of the cells within 15 m of it (SPEC.md 11.8). They are two
    different ways to say "the depth at this junction", and the response says so rather than
    letting the difference between them be read as emulator error.

    **A tide offset is answered, not refused, and not checked.** The emulator has no sea level,
    so there is no emulator answer to compare; the scenario runs on the full-city Twin
    (``POST /v1/whatif/twin``), and that answer *is* the physics. The response says so with
    ``runs_on_twin: true``, ``agrees: null`` and the job body to start, rather than a 422 that
    sent the user back to the what-if that had sent them here.
    """
    from datetime import datetime
    from time import perf_counter

    import numpy as np
    from varuna_flash.whatif import run_scenario

    started = perf_counter()
    run_id = body.get("run_id")
    path = run_dir(str(run_id)) if run_id else _latest_run()
    if not (path / "segments_wet.json").is_file():
        raise api_error(404, "run_not_found", f"Run {path.name} has no segment forecast.")
    meta = json.loads((path / "run.json").read_text(encoding="utf-8"))
    rain = meta.get("rain_aoi_mm_h")
    if not rain:
        raise api_error(
            409,
            "no_rain_series",
            f"Run {path.name} predates the stored rain series, so there is no storm to re-run. "
            "Re-bake the cycle.",
        )
    city = meta.get("city")
    if not city:
        raise api_error(
            409,
            "no_city",
            f"Run {path.name} names no city in run.json, so its terrain and drain graph cannot "
            "be identified. Re-bake the cycle.",
        )
    tide_offset = _lever_number(body, "tide_offset_m", 0.0) + 0.0
    if tide_offset != 0.0:
        return _physics_on_twin(path, meta, body, tide_offset, started)

    hotspots_path = path / "hotspots.json"
    if not hotspots_path.is_file():
        raise api_error(
            409,
            "no_hotspots",
            f"Run {path.name} carries no hotspots.json, so there is no junction to check at.",
        )
    ranked = sorted(
        json.loads(hotspots_path.read_text(encoding="utf-8")),
        key=lambda h: -float(h.get("peak_depth_cm") or 0.0),
    )
    wanted = set(body.get("hotspot_ids") or [])
    if wanted:
        ranked = [h for h in ranked if h.get("hotspot_id") in wanted] or ranked
    if not ranked:
        raise api_error(409, "no_hotspots", f"Run {path.name} ranks no hotspots.")

    # ---- the emulator's answer, exactly as /v1/whatif computes it -------------------------
    # Every lever the what-if takes, except the pump plan: the crop's Twin has no pump sink, so
    # both sides run without it and the response names it in `levers_not_checked`.
    model = _model()
    rain_scale = _lever_number(body, "rain_scale", 1.0)
    lo, hi = RAIN_SCALE_RANGE
    if not lo <= rain_scale <= hi:
        raise api_error(
            422, "out_of_range", f"Rain scale runs from {lo}x to {hi}x; got {rain_scale}x."
        )
    cleaned, unmatched = _resolve_cleaned(set(body.get("cleaned_segments") or []), model)
    beta_segments, beta_source = _beta_vector(path, city, model)
    top_count = _clean_top_count(body.get("clean_top"))
    top_beta: NDArray[np.float64] | None = None
    clean_top_info: dict[str, Any] | None = None
    if top_count:
        top_beta, clean_top_info = _clean_top(path, city, model, beta_segments, top_count)
    not_checked, not_checked_notes = _twin_levers_left_out(body, in_check=True)
    try:
        scenario = run_scenario(
            model,
            np.asarray(rain, dtype=np.float64),
            beta=beta_segments,
            rain_scale=rain_scale,
            cleaned_segments=cleaned,
            scenario_beta=top_beta,
        )
    except ValueError as error:
        raise api_error(422, "unsupported_scenario", str(error)) from error
    position = {sid: i for i, sid in enumerate(model.segment_ids)}

    # ---- the crop the Twin will run -------------------------------------------------------
    terrain, network, tide = _city_inputs(city, str(meta.get("bundle") or ""))
    pad = int(body.get("pad_cells") or CROP_PAD_CELLS)
    centre = ranked[0]
    row, col = _cell_of(terrain, float(centre["lon"]), float(centre["lat"]))
    crop_terrain, crop_network, (r0, r1, c0, c1) = _crop(terrain, network, row, col, pad)

    checked: list[dict[str, Any]] = []
    outside: list[str] = []
    for hotspot in ranked[: max(CROP_HOTSPOTS, 1)]:
        hr, hc = _cell_of(terrain, float(hotspot["lon"]), float(hotspot["lat"]))
        if r0 <= hr < r1 and c0 <= hc < c1:
            checked.append({"hotspot": hotspot, "cell": (hr - r0, hc - c0)})
        else:
            outside.append(str(hotspot.get("name") or hotspot.get("hotspot_id")))

    posterior = _posterior_edges(path)
    cleaned_edges = _edges_under(city, cleaned)
    if clean_top_info is not None and clean_top_info["applied"]:
        # The same pipes the emulator's re-join cleaned, desilted in the crop's own graph.
        cleaned_edges |= {str(pipe["edge_id"]) for pipe in clean_top_info["pipes"]}
    # How many of the cleaned pipes the window holds: a pipe outside it changes nothing here,
    # and a check that agrees because nothing it can see was cleaned has to say so.
    cleaned_in_window = sum(1 for e in crop_network.edge_ids if str(e) in cleaned_edges)
    base_beta = _crop_beta(crop_network, posterior, set())
    scenario_beta = _crop_beta(crop_network, posterior, cleaned_edges)

    t0 = datetime.fromisoformat(str(meta["cycle_ts"]))
    series = np.asarray(rain, dtype=np.float64)
    twin_started = perf_counter()
    baseline_run = _run_crop(crop_terrain, crop_network, base_beta, series, t0, tide)
    scenario_run = _run_crop(
        crop_terrain, crop_network, scenario_beta, series * rain_scale, t0, tide
    )
    twin_ms = (perf_counter() - twin_started) * 1000.0

    rows: list[dict[str, Any]] = []
    for entry in checked:
        hotspot = entry["hotspot"]
        cr, cc = entry["cell"]
        twin_before = float(baseline_run.depth_m[:, cr, cc].max()) * 100.0
        twin_after = float(scenario_run.depth_m[:, cr, cc].max()) * 100.0
        ids = [s for s in hotspot.get("segment_ids") or [] if s in position]
        deltas = [float(scenario.delta_cm[position[s]]) for s in ids]
        emulator_delta = float(np.mean(deltas)) if deltas else 0.0
        rows.append(
            {
                "hotspot_id": hotspot.get("hotspot_id"),
                "name": hotspot.get("name"),
                "emulator_delta_cm": round(emulator_delta, 2),
                "twin_delta_cm": round(twin_after - twin_before, 2),
                "diff_cm": round(abs(emulator_delta - (twin_after - twin_before)), 2),
                # The two levels the deltas were taken from, so a reader can see how far the
                # crop's own baseline sits from the run's product at the same junction.
                "twin_crop_before_cm": round(twin_before, 2),
                "run_peak_cm": hotspot.get("peak_depth_cm"),
                "emulator_segments": len(ids),
            }
        )
    rows.sort(key=lambda r: -r["diff_cm"])
    worst = rows[0] if rows else None
    total_ms = (perf_counter() - started) * 1000.0

    log.info(
        "api.physics_check",
        run_id=path.name,
        ms=round(total_ms, 1),
        twin_ms=round(twin_ms, 1),
        grid=crop_terrain.shape,
        nodes=crop_network.n_nodes,
        edges=crop_network.n_edges,
        hotspots=len(rows),
        max_diff_cm=None if worst is None else worst["diff_cm"],
        mass_balance=round(scenario_run.mass_balance.error_fraction, 6),
    )
    return {
        "run_id": meta.get("run_id", path.name),
        "method": "twin_crop_delta",
        "summary": (
            f"Emulator vs physics: max difference {worst['diff_cm']:.1f} cm at {worst['name']}"
            if worst
            else "Emulator vs physics: no hotspot inside the window to compare"
        ),
        "tolerance_cm": PHYSICS_TOLERANCE_CM,
        "agrees": bool(worst is not None and worst["diff_cm"] <= PHYSICS_TOLERANCE_CM),
        "max_diff_cm": None if worst is None else worst["diff_cm"],
        "max_diff_hotspot": None if worst is None else worst["name"],
        "hotspots": rows,
        "hotspots_outside_window": outside,
        "rain_scale": rain_scale,
        "cleaned_segments": sorted(cleaned),
        "cleaned_unmatched": unmatched,
        "cleaned_edges": sorted(cleaned_edges),
        "clean_top": clean_top_info,
        # Levers of the what-if this check could not run on the Twin; each is named in `notes`.
        "levers_not_checked": not_checked,
        "beta_source": beta_source,
        "window": {
            "rows": [r0, r1],
            "cols": [c0, c1],
            "cells": [crop_terrain.n_rows, crop_terrain.n_cols],
            "size_m": round(crop_terrain.n_rows * crop_terrain.res_m),
            "nodes": crop_network.n_nodes,
            "edges": crop_network.n_edges,
            "centre_hotspot": centre.get("name"),
            "cleaned_edges_inside": cleaned_in_window,
        },
        "mass_balance": {
            "baseline": round(baseline_run.mass_balance.error_fraction, 6),
            "scenario": round(scenario_run.mass_balance.error_fraction, 6),
            "budget": 1e-3,
        },
        "twin_ms": round(twin_ms, 1),
        "ms": round(total_ms, 1),
        "budget_ms": TWIN_BUDGET_MS,
        "within_budget": total_ms <= TWIN_BUDGET_MS,
        "notes": [
            f"The Twin ran twice on a {crop_terrain.n_rows} x {crop_terrain.n_cols} cell window "
            f"({round(crop_terrain.n_rows * crop_terrain.res_m)} m) around "
            f"{centre.get('name')}, with {crop_network.n_edges:,} pipes and "
            f"{crop_network.n_nodes:,} manholes, because a full-AOI Mumbai run measures 58-114 s "
            f"in six of the seven baked cycles against this endpoint's {TWIN_BUDGET_MS / 1000:.0f} s budget.",
            "Deltas are compared, not levels: a crop's absolute depth is not the city's, and "
            "whatever its boundary does wrong it does in both runs and largely cancels.",
            "The Twin's delta is read at each hotspot's own 30 m cell; the emulator's is the "
            "mean over the road segments the register lists for it, each the 90th percentile of "
            "the cells within 15 m. Part of every difference below is those two samplings.",
            "Pipes that leave the window become free outfalls, so the crop drains a little too "
            "well at its edge, and no level from it is published for that reason. Its two runs' "
            f"mass balance closed at {baseline_run.mass_balance.error_fraction:.2e} and "
            f"{scenario_run.mass_balance.error_fraction:.2e} against the Twin's 1e-3 budget.",
            _beta_note(beta_source),
            *_crop_sea_notes(crop_terrain, crop_network),
            *([_clean_top_note(clean_top_info)] if clean_top_info is not None else []),
            *not_checked_notes,
            *(
                [
                    f"None of the {len(cleaned_edges):,} cleaned pipes is inside the window, "
                    "so the cleaning changes nothing the check can see; only the rain scale "
                    "is compared here."
                ]
                if cleaned_edges and not cleaned_in_window
                else [
                    f"{cleaned_in_window:,} of the {len(cleaned_edges):,} cleaned pipes are "
                    "inside the window; the rest change nothing the check can see."
                ]
                if cleaned_edges and cleaned_in_window < len(cleaned_edges)
                else []
            ),
            *(
                [
                    f"{len(outside)} of the run's top hotspots fall outside the one window and "
                    "were not checked: " + ", ".join(outside) + "."
                ]
                if outside
                else []
            ),
            *(
                [
                    f"{len(unmatched)} of the ids asked for are not road segments in this city "
                    "and were not cleaned in either model."
                ]
                if unmatched
                else []
            ),
        ],
    }


# ============================================ the full-city Twin scenario (the tide lever)
#
# **Why a job, and why the whole city.** Flash-lite has no sea level (ADR-0025), and the physics
# check's 990 m crop is centred on a hotspot, turns every pipe that leaves it into a free outfall
# and runs only the cycle's own tide - it carries whatever of the city's sea its window holds,
# but a sea level moving the city is not a question a window can answer. The survey of 2026-09-26
# measured the only honest answer: one full-AOI coupled Twin with exactly the cycle's inputs - the
# cycle's own Sky rain on the 30 m grid, the city's inferred prior blockage (the blockage the
# cycle's Twin ran at, before Pulse), and the bundle's tide - with the lever applied to one of
# them. At a scenario of nothing it reproduced the bake to under 0.05 cm on 6,902 of 6,904 wet
# streets; at +1.0 m of tide its shoreline exchange read 1.26 Mm3 inland across the Mahim cell -
# `sea_to_land_m3`, the net face exchange between sea cells and land, not the sea that entered the
# city (see `_sea_block`), and measured when the sea was the tidal outfalls' cells, before the city
# had a sea raster, when that quantity also counted the rain on those cells - and moved no hotspot.
# That run costs 45-75 s
# here (Sky 14.3 s, Twin 58.8 s under contention; the bake recorded 38.8 s), which is a job with a
# progress bar and not a request, and it holds the host, so it takes the same one-heavy-run lock
# the live cycle and the rain nowcast take.
#
# **Cached by content.** A scenario is a pure function of the run and the lever values, so its
# answer is kept on disk (`data/whatif/<run_id>/<key>.json.gz`) and in a small memory LRU, keyed
# on the lever values quantised to 0.1 and the cleaned pipes' hash, and stamped with a fingerprint
# of the run - the sha1 of its `run.json` and the Twin version - that a re-bake changes. The
# fingerprint is the file's content rather than its mtime because the shipped read-only cache
# (`demo/whatif/`) crosses machines, where a checkout sets every mtime to the clone's.

TWIN_JOB_ENV = "VARUNA_WHATIF_TWIN"
"""Set to 0/false/no/off to refuse the full-city Twin what-if on a host too small to hold it."""

PROGRESS_TOPIC = "whatif.progress"
"""Bus topic a running scenario publishes each output step on; the WebSocket relays it."""

TWIN_SCENARIO_METHOD = "twin_full_aoi"
BLOCKAGE_SOURCE = "prior"
"""The blockage a Twin scenario runs at, and the one the run's own Twin level was computed at."""

SCENARIO_QUANTUM = 0.1
"""Lever resolution: the rain scale and the tide offset are rounded to this before running, so a
cached answer is exactly the scenario that was asked for rather than one near it."""

RAIN_SCALE_RANGE = (0.5, 2.0)
TIDE_OFFSET_RANGE_M = (-0.5, 1.0)
"""The lever ranges SPEC.md 7.7 gives the what-if lab."""

MINUTES_THRESHOLDS_CM = (15, 30, 45)
"""Minutes above these depths are reported before and after: two-wheeler, car, bus (6.2)."""

WET_CM = 5.0
"""The run's `segments_wet.json` omits every street below this (`depth.WET_THRESHOLD_CM`)."""

MEMORY_CACHE_SIZE = 16
DISK_CACHE_PER_RUN = 48
"""Answers held per run on disk, oldest dropped first."""

MAX_JOBS_KEPT = 32
"""Finished jobs remembered for their GET, so a poll after completion still finds the answer."""

_MEMORY: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
_MEMORY_GUARD = threading.Lock()
_RAIN_CACHE: dict[tuple[str, str, str, int], NDArray[np.float64]] = {}
"""The cycle's rain cube on the city grid, one entry: 36 x 522 x 323 float64 is 49 MB, and a
second scenario on the same cycle - the next tide value - then skips Sky entirely."""

_JOBS: OrderedDict[str, TwinScenarioJob] = OrderedDict()
_JOBS_GUARD = threading.Lock()
_OWN_LOCK = threading.Lock()


class _Cancelled(Exception):
    """Raised from the progress hook to stop a run the operator cancelled."""


def twin_scenario_enabled() -> bool:
    """On unless the host says otherwise."""
    value = os.environ.get(TWIN_JOB_ENV, "").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _heavy_lock() -> threading.Lock:
    """The one-heavy-run-at-a-time lock the live cycle holds, or this module's own.

    `routers/cycle.py` and the rain nowcast share `varuna_api.rain._LIVE_CYCLE`; taking the same
    lock here means a what-if Twin, a Compute live and a computed rain cycle never overlap, which
    is what took the deployed API down on 2026-09-19 when two Sky cycles did.
    """
    try:
        from varuna_api.routers.cycle import _LIVE_CYCLE
    except ImportError:  # pragma: no cover - the cycle router is part of this package
        return _OWN_LOCK
    return _LIVE_CYCLE


def _quantise(value: float) -> float:
    """``value`` on the lever's 0.1 grid, with -0.0 folded into 0.0 so the key has one spelling."""
    return round(round(float(value) / SCENARIO_QUANTUM) * SCENARIO_QUANTUM, 1) + 0.0


def scenario_key(rain_scale: float, tide_offset_m: float, cleaned_edges: set[str]) -> str:
    """A filename-safe key for one scenario on one run."""
    edges = (
        hashlib.sha1("\n".join(sorted(cleaned_edges)).encode("utf-8")).hexdigest()[:12]
        if cleaned_edges
        else "none"
    )
    return (
        f"rain{_quantise(rain_scale):.1f}_tide{_quantise(tide_offset_m):+.1f}"
        f"_clean-{edges}_{BLOCKAGE_SOURCE}"
    )


TWIN_CODE_MODULES = (
    "varuna_twin",
    "varuna_products.segment_table",
    "varuna_products.depth",
    "varuna_cycle.twin_cycle",
)
"""The code a Twin scenario's numbers come from: the solver package, the street sampler and the
cycle's Twin wiring (the city load, the tide, the rain on the grid)."""

CITY_BUILD_SUFFIXES = (".tif", ".npz", ".parquet")
"""The city files a Twin scenario reads are rasters, the segment index and the graph tables."""

_FILE_DIGESTS: dict[tuple[str, int, int], str] = {}
_CODE_DIGEST: list[str] = []


def _file_digest(path: Path) -> str:
    """sha1 of a file's bytes, remembered while its size and mtime stay put."""
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    held = _FILE_DIGESTS.get(key)
    if held is None:
        held = hashlib.sha1(path.read_bytes()).hexdigest()
        _FILE_DIGESTS[key] = held
    return held


def twin_code_fingerprint() -> str:
    """sha1 over the source of :data:`TWIN_CODE_MODULES`, line endings folded, once per process.

    The version string alone was not enough: ``TWIN_VERSION`` stayed "1.0" through the capture
    front-loading and the sea fill, so an answer cached on older code, or a bake made on it, was
    served as this code's. A fresh process is how new code is loaded, so once per process is exact.
    """
    if _CODE_DIGEST:
        return _CODE_DIGEST[0]
    import importlib

    digest = hashlib.sha1()
    for name in TWIN_CODE_MODULES:
        module = importlib.import_module(name)
        origin = Path(str(module.__file__))
        files = sorted(origin.parent.rglob("*.py")) if origin.name == "__init__.py" else [origin]
        for source in files:
            digest.update(source.relative_to(origin.parent).as_posix().encode("utf-8"))
            digest.update(source.read_bytes().replace(b"\r\n", b"\n"))
    _CODE_DIGEST.append(digest.hexdigest())
    return _CODE_DIGEST[0]


def city_build_fingerprint(city: str) -> str:
    """sha1 over the city's rasters, segment index and graph tables, by name and content."""
    root = city_dir(city)
    digest = hashlib.sha1()
    files = [p for p in (*root.glob("*"), *root.glob("graph/*")) if p.suffix in CITY_BUILD_SUFFIXES]
    for source in sorted(files):
        if source.is_file():
            digest.update(source.relative_to(root).as_posix().encode("utf-8"))
            digest.update(_file_digest(source).encode("ascii"))
    return digest.hexdigest()


def run_fingerprint(path: Path) -> str:
    """What a cached answer must match: the run's `run.json` bytes, the Twin's version and code,
    and the city build it runs on. A change to any of them is a different answer (rule 6)."""
    from varuna_cycle.twin_cycle import TWIN_VERSION

    raw = (path / "run.json").read_bytes()
    city = str(json.loads(raw.decode("utf-8")).get("city") or "")
    digest = hashlib.sha1(raw).hexdigest()[:16]
    city_part = city_build_fingerprint(city)[:12] if city else "none"
    return f"{digest}-twin{TWIN_VERSION}-code{twin_code_fingerprint()[:12]}-city{city_part}"


def _cache_root() -> Path:
    return data_dir() / "whatif"


def _shipped_cache_root() -> Path:
    return repo_root() / "demo" / "whatif"


def _remember(run_id: str, key: str, payload: dict[str, Any]) -> None:
    with _MEMORY_GUARD:
        _MEMORY[(run_id, key)] = payload
        _MEMORY.move_to_end((run_id, key))
        while len(_MEMORY) > MEMORY_CACHE_SIZE:
            _MEMORY.popitem(last=False)


def _cached(
    run_id: str, key: str, fingerprint: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """A stored answer for this scenario on this exact run, and where it came from."""
    with _MEMORY_GUARD:
        held = _MEMORY.get((run_id, key))
        if held is not None and held.get("fingerprint") == fingerprint:
            _MEMORY.move_to_end((run_id, key))
            return held, {"source": "memory"}
        if held is not None:
            del _MEMORY[(run_id, key)]
    for source, root in (("disk", _cache_root()), ("shipped", _shipped_cache_root())):
        stored = root / run_id / f"{key}.json.gz"
        if not stored.is_file():
            continue
        try:
            payload = json.loads(gzip.decompress(stored.read_bytes()).decode("utf-8"))
        except (OSError, ValueError, EOFError):
            payload = None
        if not isinstance(payload, dict) or payload.get("fingerprint") != fingerprint:
            # Another bake of this run, or another Twin: the answer is not this run's. The local
            # cache is ours to clean; the shipped one is read-only and is simply not used.
            if source == "disk":
                stored.unlink(missing_ok=True)
            continue
        _remember(run_id, key, payload)
        return payload, {"source": source, "path": f"{root.name}/{run_id}/{stored.name}"}
    return None


def _store(run_id: str, key: str, payload: dict[str, Any], *, root: Path | None = None) -> Path:
    """Write one answer atomically, then drop the run's oldest past :data:`DISK_CACHE_PER_RUN`."""
    folder = (root or _cache_root()) / run_id
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{key}.json.gz"
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    temporary = folder / f".{key}.{uuid.uuid4().hex}.tmp"
    temporary.write_bytes(gzip.compress(body, compresslevel=6, mtime=0))
    temporary.replace(target)
    if root is None:
        kept = sorted(folder.glob("*.json.gz"), key=lambda p: p.stat().st_mtime_ns)
        for stale in kept[:-DISK_CACHE_PER_RUN]:
            stale.unlink(missing_ok=True)
    _remember(run_id, key, payload)
    return target


def _cache_label(where: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Where an answer came from, in words a reader can check (rule 6)."""
    on = payload.get("computed_on") or {}
    machine = f"{on.get('system', 'an unknown system')} {on.get('machine', '')}".strip()
    if on.get("cpus"):
        machine += f" with {on['cpus']} logical CPUs"
    label = {
        "memory": "Answered from this server's memory; computed earlier in this process.",
        "disk": "Answered from this server's cache in data/whatif; computed earlier here.",
        "shipped": "Answered from the cache shipped with the repository in demo/whatif; "
        "not recomputed on this machine.",
        "computed": "Computed now on this server.",
    }[where["source"]]
    return {
        **where,
        "computed_at": payload.get("computed_at"),
        "computed_on": on,
        "label": f"{label} Computed {payload.get('computed_at')} on {machine}.",
    }


# ------------------------------------------------------------------------------- inputs
@dataclass(frozen=True, slots=True)
class _ScenarioInputs:
    """Everything the cycle's Twin ran on, loaded once per scenario."""

    terrain: Any
    network: Any
    tide: Any
    rain_mm_h: NDArray[np.float64]
    index: tuple[Any, Any, Any]
    rain_ms: int
    rain_cached: bool


def _rain_key(meta: dict[str, Any]) -> tuple[str, str, str, int]:
    cycle_ts = datetime.fromisoformat(str(meta["cycle_ts"]))
    return (str(meta["bundle"]), cycle_ts.isoformat(), str(meta["city"]), _n_steps(meta))


def _n_steps(meta: dict[str, Any]) -> int:
    return int(meta.get("n_steps") or 36)


def _cycle_rain(meta: dict[str, Any]) -> tuple[NDArray[np.float64], int, bool]:
    """The cycle's own rain cube on the city grid: the call `run_cycle` makes, kept once."""
    key = _rain_key(meta)
    held = _RAIN_CACHE.get(key)
    if held is not None:
        return held, 0, True
    from varuna_cycle.twin_cycle import _sky_rain_on_city

    bundle, _, city, n_steps = key
    started = perf_counter()
    cube, _sky = _sky_rain_on_city(
        bundle, datetime.fromisoformat(str(meta["cycle_ts"])), city, n_steps
    )
    cube = np.asarray(cube, dtype=np.float64)
    _RAIN_CACHE.clear()
    _RAIN_CACHE[key] = cube
    return cube, round((perf_counter() - started) * 1000.0), False


def _load_scenario_inputs(meta: dict[str, Any]) -> _ScenarioInputs:
    """Terrain, prior network, tide, the cycle's rain and the segment index, as the bake had them."""
    from varuna_products.depth import segment_cell_index

    city, bundle = str(meta["city"]), str(meta["bundle"])
    terrain, network, tide = _city_inputs(city, bundle)
    rain, rain_ms, rain_cached = _cycle_rain(meta)
    index = segment_cell_index(city_dir(city), terrain.transform, terrain.shape, terrain.crs)
    return _ScenarioInputs(terrain, network, tide, rain, index, rain_ms, rain_cached)


def _missing_sources(city: str, bundle: str) -> str | None:
    """Why this server cannot run the city's Twin, or None when it can."""
    root = city_dir(city)
    needed = [root / "dem_conditioned.tif", root / "graph" / "nodes.parquet"]
    missing = [p for p in needed if not p.is_file()]
    if missing:
        return (
            f"The full-city Twin needs {city}'s conditioned terrain and drain graph, and "
            f"{', '.join(p.name for p in missing)} is not built on this server. Run "
            f"`make city CITY={city}`."
        )
    if not bundle_dir(bundle).is_dir():
        return (
            f"The run was made from bundle {bundle}, which is not on this server, so the cycle's "
            f"rain cannot be re-made. Run `make bundle BUNDLE={bundle}`."
        )
    return None


def _segment_edge_pairs(city: str) -> pd.DataFrame:
    """The segment-to-pipe incidence `_edges_under` reads, as a frame."""
    from varuna_pulse.join import segment_edges

    return segment_edges(city)


_KNOWN_SEGMENTS: dict[tuple[str, int, int], frozenset[str]] = {}


def _known_segments(city: str) -> set[str]:
    """Every road-segment id of the city's street table, the table the segment index is built
    from; read once per version of the file. A street with no pipe under it is still a street,
    and asking to clean it is answered "changed nothing" rather than refused as unknown."""
    table = city_dir(city) / "segments.parquet"
    if not table.is_file():
        return set()
    stat = table.stat()
    key = (str(table), stat.st_size, stat.st_mtime_ns)
    held = _KNOWN_SEGMENTS.get(key)
    if held is None:
        ids = pd.read_parquet(table, columns=["segment_id"])["segment_id"].astype(str)
        held = frozenset(ids)
        _KNOWN_SEGMENTS.clear()
        _KNOWN_SEGMENTS[key] = held
    return set(held)


def _twin_cleaning(
    city: str, requested: set[str], known_segments: set[str]
) -> tuple[set[str], list[str], list[str], list[str]]:
    """Pipes to clean, and how each requested street resolved: cleaned, no pipe, unknown."""
    if not requested:
        return set(), [], [], []
    try:
        pairs = _segment_edge_pairs(city)
    except SegmentBetaError as error:
        raise api_error(503, "no_city_grid", str(error)) from error
    hit = pairs[pairs["segment_id"].astype(str).isin(requested)]
    with_pipe = set(hit["segment_id"].astype(str))
    edges = set(hit["edge_id"].astype(str))
    known = known_segments | set(pairs["segment_id"].astype(str))
    unknown = sorted(requested - known)
    no_pipe = sorted((requested & known) - with_pipe)
    if not with_pipe and not no_pipe:
        named = ", ".join(unknown[:UNMATCHED_NAMED])
        raise api_error(
            422,
            "unknown_segments",
            f"None of the ids to clean are road segments in this city: {named}. Cleaning is "
            "applied to the pipes under a road segment, so this takes road-segment ids (S...), "
            "not drain edge ids (MUM-E...).",
        )
    return edges, sorted(with_pipe), no_pipe, unknown


# ------------------------------------------------------------------------------- compute
#
# **Before is a Twin run on this code, not the bake.** A scenario used to be compared with the
# run's own `segments_wet.json`, and nothing checked that the Twin and the city it re-runs on were
# still the ones the bake used. On 2026-09-27 they were not: a scenario of *nothing* on the 08:40
# cycle came back with 952 streets changed and 83.6 cm at most - the capture front-loading, the
# sea fill and a rebuilt segment index since the bake - so every tide answer credited the tide
# with about 950 street changes it did not make. Each scenario is now compared with a scenario of
# nothing run on the same code, city and inputs: computed once per run, cached under the same
# fingerprint as the answers, and its own distance from the bake reported as `baseline.drift`.

BASELINE_FILE = f"baseline_{BLOCKAGE_SOURCE}.npz"
"""The scenario of nothing for one run, per street per step, beside that run's cached answers."""


@dataclass(frozen=True, slots=True)
class _Baseline:
    """The Twin at the cycle's inputs with nothing changed: what every scenario is compared with."""

    depth_cm: NDArray[np.float64]
    """(n_steps, n_segments), cm, rounded to one decimal as the run's own layer is."""
    segment_ids: tuple[str, ...]
    meta: dict[str, Any]
    """fingerprint, computed_at, computed_on, twin_ms, sea (the runner's ledger), volume_in_m3,
    mass_balance_error, ledger, notes."""


_BASELINES: OrderedDict[tuple[str, str], _Baseline] = OrderedDict()
_BASELINES_KEPT = 2


def _round_cm(depth_cm: NDArray[np.floating]) -> NDArray[np.float64]:
    """Street depths to one decimal, the same way for the baseline and every scenario."""
    return np.round(np.asarray(depth_cm, dtype=np.float64), 1) + 0.0


def _baseline_file(root: Path, run_id: str) -> Path:
    return root / run_id / BASELINE_FILE


def _load_baseline(run_id: str, fingerprint: str) -> tuple[_Baseline, str] | None:
    """This run's scenario of nothing on this code and city, and where it came from."""
    held = _BASELINES.get((run_id, fingerprint))
    if held is not None:
        _BASELINES.move_to_end((run_id, fingerprint))
        return held, "memory"
    for source, root in (("disk", _cache_root()), ("shipped", _shipped_cache_root())):
        stored = _baseline_file(root, run_id)
        if not stored.is_file():
            continue
        try:
            with np.load(stored, allow_pickle=False) as data:
                meta = json.loads(str(data["meta"]))
                depth = np.asarray(data["depth_cm"], dtype=np.float64)
                ids = tuple(str(s) for s in data["segment_ids"].tolist())
        except (OSError, ValueError, KeyError):
            continue
        if meta.get("fingerprint") != fingerprint:
            if source == "disk":
                stored.unlink(missing_ok=True)
            continue
        baseline = _Baseline(depth, ids, meta)
        _remember_baseline(run_id, baseline)
        return baseline, source
    return None


def _remember_baseline(run_id: str, baseline: _Baseline) -> None:
    _BASELINES[(run_id, str(baseline.meta["fingerprint"]))] = baseline
    while len(_BASELINES) > _BASELINES_KEPT:
        _BASELINES.popitem(last=False)


def _store_baseline(run_id: str, baseline: _Baseline, *, root: Path | None = None) -> Path:
    """Write the baseline atomically; ``root`` is the shipped cache when prewarming for it."""
    folder = (root or _cache_root()) / run_id
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / BASELINE_FILE
    temporary = folder / f".baseline.{uuid.uuid4().hex}.npz"
    with temporary.open("wb") as handle:
        np.savez_compressed(
            handle,
            depth_cm=baseline.depth_cm,
            segment_ids=np.array(baseline.segment_ids, dtype=str),
            meta=np.array(json.dumps(baseline.meta, sort_keys=True)),
        )
    temporary.replace(target)
    _remember_baseline(run_id, baseline)
    return target


def baseline_cached(run_id: str, fingerprint: str) -> bool:
    """Whether a scenario on this run can skip its comparison run."""
    return _load_baseline(run_id, fingerprint) is not None


@dataclass(frozen=True, slots=True)
class _TwinRun:
    depth_cm: NDArray[np.float64]
    sea: dict[str, float]
    balance: Any
    notes: tuple[str, ...]
    stage_ms: dict[str, Any]
    ms: int


def _run_scenario_twin(
    inputs: _ScenarioInputs,
    network: Any,
    rain: NDArray[np.float64],
    tide: Any,
    meta: dict[str, Any],
    on_step: Callable[[int, int], None] | None,
) -> _TwinRun:
    """One full-city coupled Twin, sampled onto the streets."""
    from varuna_products import segment_table
    from varuna_twin.runner import run_twin
    from varuna_twin.types import TwinInputs

    sea: dict[str, float] = {}
    started = perf_counter()
    twin = run_twin(
        TwinInputs(
            terrain=inputs.terrain,
            network=network,
            rain_mm_h=rain,
            t0=datetime.fromisoformat(str(meta["cycle_ts"])),
            step_min=int(meta.get("step_min") or 5),
            tide=tide,
        ),
        on_step=on_step,
        sea_ledger=sea,
    )
    ms = round((perf_counter() - started) * 1000.0)
    depth = _round_cm(segment_table.sample_segments(twin.depth_m, inputs.index))
    return _TwinRun(depth, sea, twin.mass_balance, tuple(twin.notes), dict(twin.stage_ms), ms)


def _sea_block(sea: dict[str, float]) -> dict[str, float]:
    """The runner's sea ledger, rounded.

    ``sea_to_land_m3`` is the net face exchange between the sea cells and the land: water that
    crossed the shoreline, positive inland. Rain on the sea is not in it (the runner never books
    it) and nor is the drain (a node on a sea cell neither captures nor surcharges). It is **not**
    the sea that entered the city, because the city meets the sea by a second path: the pipes.
    That one is ``outfall_m3``, the net discharge at every outfall, positive out to sea, so
    seawater pushed up a tide-locked pipe lowers it and shows as a negative ``outfall_m3`` once it
    outweighs what the drains let out. On a city built before the sea step the sea is the tidal
    outfalls' cells, and ``sea_to_land_m3`` also carries the rain that fell on them.
    """
    keys = (
        "sea_to_land_m3",
        "tide_in_m3",
        "tide_out_m3",
        "sea_stored_start_m3",
        "sea_stored_end_m3",
        "outfall_m3",
    )
    return {k: round(float(sea.get(k, 0.0)), 1) for k in keys}


def _computed_on() -> dict[str, Any]:
    return {"system": platform.system(), "machine": platform.machine(), "cpus": os.cpu_count()}


def _minutes_above(depth_cm: NDArray[np.float64], step_min: int) -> dict[str, NDArray[np.int64]]:
    """Minutes above each threshold, per street, from (n_steps, n_streets) cm."""
    return {str(t): step_min * (depth_cm > t).sum(axis=0) for t in MINUTES_THRESHOLDS_CM}


def _segment_rows(
    after: NDArray[np.float64],
    before: NDArray[np.float64],
    segment_ids: tuple[str, ...],
    step_min: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per-street before and after, for every street wet (>= 5 cm) in either that the scenario
    moved, plus the counts. Both sides are Twin runs on the same code, so every street has a
    measured before depth, and one that crosses 5 cm is marked newly wet with that depth."""
    if not after.size:
        empty = {str(t): 0 for t in MINUTES_THRESHOLDS_CM}
        return [], {
            "n_segments": len(segment_ids),
            "n_segments_compared": 0,
            "n_changed": 0,
            "n_worse": 0,
            "n_improved": 0,
            "n_newly_wet": 0,
            "newly_above": empty,
            "max_abs_delta_cm": 0.0,
        }
    before_peak = before.max(axis=0)
    after_peak = after.max(axis=0)
    wet = (before_peak >= WET_CM) | (after_peak >= WET_CM)
    delta = np.round(after_peak - before_peak, 1) + 0.0
    minutes_before = _minutes_above(before, step_min)
    minutes_after = _minutes_above(after, step_min)
    minutes_moved = np.zeros(after.shape[1], dtype=bool)
    for t in minutes_before:
        minutes_moved |= minutes_before[t] != minutes_after[t]
    newly_wet = (before_peak < WET_CM) & (after_peak >= WET_CM)
    listed = np.flatnonzero(wet & ((np.abs(delta) >= 0.1) | minutes_moved))

    rows = [
        {
            "segment_id": segment_ids[k],
            "before_cm": float(before_peak[k]),
            "after_cm": float(after_peak[k]),
            "delta_cm": float(delta[k]),
            "newly_wet": bool(newly_wet[k]),
            "minutes_above_before": {t: int(v[k]) for t, v in minutes_before.items()},
            "minutes_above_after": {t: int(v[k]) for t, v in minutes_after.items()},
        }
        for k in listed.tolist()
    ]
    rows.sort(key=lambda r: -abs(r["delta_cm"]))
    worse = wet & (newly_wet | (delta >= CHANGE_CM))
    improved = wet & (delta <= -CHANGE_CM)
    counts = {
        "n_segments": len(segment_ids),
        "n_segments_compared": int((before_peak >= WET_CM).sum()),
        "n_changed": int((worse | improved).sum()),
        "n_worse": int(worse.sum()),
        "n_improved": int(improved.sum()),
        "n_newly_wet": int(newly_wet.sum()),
        "newly_above": {
            t: int(((minutes_before[t] == 0) & (minutes_after[t] > 0)).sum())
            for t in minutes_before
        },
        "max_abs_delta_cm": round(float(np.abs(delta[wet]).max()) if wet.any() else 0.0, 1),
    }
    return rows, counts


def _hotspot_rows(
    path: Path,
    after: NDArray[np.float64],
    before: NDArray[np.float64],
    segment_ids: tuple[str, ...],
    step_min: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Each ranked hotspot as the deepest of the streets the register lists for it, before and
    after, and the names of hotspots none of whose streets is in the segment index."""
    hotspots_path = path / "hotspots.json"
    if not hotspots_path.is_file():
        return [], []
    position = {sid: k for k, sid in enumerate(segment_ids)}
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for hotspot in json.loads(hotspots_path.read_text(encoding="utf-8")):
        listed = [str(s) for s in hotspot.get("segment_ids") or []]
        columns = [position[s] for s in listed if s in position]
        if not columns:
            missing.append(str(hotspot.get("name") or hotspot.get("hotspot_id")))
            continue
        worst_before = before[:, columns].max(axis=1)
        worst_after = after[:, columns].max(axis=1)
        before_peak = round(float(worst_before.max()), 1)
        after_peak = round(float(worst_after.max()), 1)
        rows.append(
            {
                "hotspot_id": hotspot.get("hotspot_id"),
                "name": hotspot.get("name"),
                "lon": hotspot.get("lon"),
                "lat": hotspot.get("lat"),
                "before_cm": before_peak,
                "after_cm": after_peak,
                "delta_cm": round(after_peak - before_peak, 1) + 0.0,
                "minutes_above_before": {
                    t: int(v[0]) for t, v in _minutes_above(worst_before[:, None], step_min).items()
                },
                "minutes_above_after": {
                    t: int(v[0]) for t, v in _minutes_above(worst_after[:, None], step_min).items()
                },
                "segments": len(columns),
                "segments_missing": len(listed) - len(columns),
                "run_peak_cm": hotspot.get("peak_depth_cm"),
            }
        )
    rows.sort(key=lambda r: (-abs(r["delta_cm"]), -float(r["after_cm"] or 0.0)))
    return rows, missing


def _bake_depths(
    path: Path, segment_ids: tuple[str, ...], n_steps: int
) -> tuple[NDArray[np.float64], int]:
    """The bake's own street forecast on the segment index, and how many of its streets the index
    does not have. Streets the bake left out were below 5 cm; they read 0 here."""
    wet = json.loads((path / "segments_wet.json").read_text(encoding="utf-8"))
    position = {sid: k for k, sid in enumerate(segment_ids)}
    depth = np.zeros((n_steps, len(segment_ids)), dtype=np.float64)
    unsampled = 0
    for sid, series in (wet.get("depth_cm") or {}).items():
        k = position.get(str(sid))
        if k is None:
            unsampled += 1
            continue
        values = np.asarray(series[:n_steps], dtype=np.float64)
        depth[: values.size, k] = values
    return depth, unsampled


def _drift(
    path: Path,
    meta: dict[str, Any],
    baseline: _Baseline,
    step_min: int,
) -> dict[str, Any]:
    """How far the comparison run is from the bake: what comparing with the bake would have
    credited to any scenario. Zero on a bake made on this code and city."""
    bake, unsampled = _bake_depths(path, baseline.segment_ids, baseline.depth_cm.shape[0])
    _, counts = _segment_rows(baseline.depth_cm, bake, baseline.segment_ids, step_min)
    hotspots, _ = _hotspot_rows(path, baseline.depth_cm, bake, baseline.segment_ids, step_min)
    recorded = {
        "code": meta.get("twin_code_fingerprint"),
        "city": meta.get("city_build_fingerprint"),
    }
    now = {"code": twin_code_fingerprint(), "city": city_build_fingerprint(str(meta["city"]))}
    return {
        "n_segments_compared": counts["n_segments_compared"],
        "n_changed": counts["n_changed"],
        "n_worse": counts["n_worse"],
        "n_improved": counts["n_improved"],
        "max_abs_delta_cm": counts["max_abs_delta_cm"],
        "hotspots_moved": sum(1 for r in hotspots if abs(r["delta_cm"]) >= CHANGE_CM),
        "n_unsampled": unsampled,
        # Null when the bake predates recording them, so the match is unknown rather than assumed.
        "bake_code_matches": None if not recorded["code"] else recorded["code"] == now["code"],
        "bake_city_matches": None if not recorded["city"] else recorded["city"] == now["city"],
    }


def _drift_note(drift: dict[str, Any]) -> str:
    moved = drift["n_changed"]
    if moved == 0 and not drift["n_unsampled"]:
        return (
            "Before is a Twin run with nothing changed, on this server's code and city; it "
            "reproduces the run's own forecast on every street, so the change is the scenario's."
        )
    why = {
        (True, True): "on the same code and city, which should not happen; report it",
        (False, True): "because the Twin's code has changed since the bake",
        (True, False): "because the city was rebuilt since the bake",
        (False, False): "because the Twin's code and the city have both changed since the bake",
    }.get(
        (drift["bake_code_matches"], drift["bake_city_matches"]),
        "because the Twin's code or the city has changed since the bake (the run does not record "
        "which it was made on)",
    )
    unsampled = (
        f", and {drift['n_unsampled']:,} of the run's streets are not in today's segment index"
        if drift["n_unsampled"]
        else ""
    )
    return (
        f"Before is a Twin run with nothing changed, on this server's code and city, not the "
        f"run's own forecast: the two differ on {_plural(moved, 'street')} by up to "
        f"{drift['max_abs_delta_cm']:.1f} cm and move {_plural(drift['hotspots_moved'], 'hotspot')}"
        f" {why}{unsampled}. Comparing with the run would have credited the scenario with that "
        "difference; re-bake the run to bring its map back in line."
    )


def _tide_note(tide_offset_m: float, sea: dict[str, float], base_sea: dict[str, Any]) -> str:
    """What the tide offset did at the coast, in the runner's own terms (rule 6).

    Both paths by which the sea and the city exchange water, each named for what it is (see
    :func:`_sea_block`): the shoreline, ``sea_to_land_m3``, and the pipes, ``outfall_m3``.
    Neither alone is the sea that entered the city, so neither is quoted as if it were.
    """
    to_land = float(sea.get("sea_to_land_m3", 0.0))
    change = to_land - float(base_sea.get("sea_to_land_m3", 0.0))
    outfall = float(sea.get("outfall_m3", 0.0))
    outfall_change = outfall - float(base_sea.get("outfall_m3", 0.0))
    shoreline = (
        f"{to_land / 1e6:.2f} Mm3 of sea crossed the shoreline onto the land, net"
        if to_land > 0
        else (
            "no sea crossed the shoreline onto the land, net; the land passed "
            f"{-to_land / 1e6:.2f} Mm3 to the sea across it"
        )
    )
    pipes = (
        f"the drains let {outfall / 1e6:.2f} Mm3 out at their outfalls, net"
        if outfall >= 0
        else f"the sea pushed {-outfall / 1e6:.2f} Mm3 up the drains at their outfalls, net"
    )
    return (
        f"Tide offset {tide_offset_m:+.1f} m on every step of the bundle's series: {shoreline} "
        f"({change / 1e6:+.2f} Mm3 against the tide as forecast), and {pipes} "
        f"({outfall_change / 1e6:+.2f} Mm3)."
    )


def _scenario_notes(
    *,
    tide_offset_m: float,
    sea: dict[str, float],
    base_sea: dict[str, Any],
    counts: dict[str, Any],
    hotspots_moved: int,
    hotspots_missing: list[str],
    cleaning: tuple[set[str], list[str], list[str], list[str]],
    rain_diff: float | None,
    tide_labels: list[str],
    twin_notes: tuple[str, ...],
    drift: dict[str, Any],
) -> list[str]:
    from varuna_flash.whatif import CLEANED_BETA

    edges, with_pipe, no_pipe, unknown = cleaning
    notes = [
        "VARUNA-Twin, full city, at the city's inferred prior blockage: the same terrain, drains, "
        "rain and tide the cycle's own Twin ran on, with the scenario applied to one of them.",
        "Blockage is the city's prior, not Pulse's posterior, because the run's depth forecast "
        "was computed at the prior; the emulator what-if and the physics check run at the "
        "posterior.",
        _drift_note(drift),
        f"Across the {counts['n_segments_compared']:,} streets wet at 5 cm or more with nothing "
        f"changed, the largest change is {counts['max_abs_delta_cm']:.1f} cm.",
        *tide_labels,
        *twin_notes,
    ]
    if tide_offset_m != 0.0:
        above = counts["newly_above"]["30"]
        notes.append(
            f"{_tide_note(tide_offset_m, sea, base_sea)} {_plural(above, 'street')} newly above "
            f"30 cm, {_plural(hotspots_moved, 'hotspot')} moved by {CHANGE_CM} cm or more."
        )
    if edges:
        notes.append(
            f"{len(edges):,} pipes under {len(with_pipe):,} streets cleaned to "
            f"beta = {CLEANED_BETA}."
        )
    if no_pipe:
        notes.append(
            f"{len(no_pipe)} of the streets asked for have no pipe under them in the inferred "
            "graph, so cleaning them changed nothing."
        )
    if unknown:
        notes.append(f"{len(unknown)} of the ids asked for are not road segments in this city.")
    if hotspots_missing:
        notes.append(
            f"{_plural(len(hotspots_missing), 'hotspot')} list no street in today's segment index "
            "and are not in the table: " + ", ".join(hotspots_missing[:UNMATCHED_NAMED]) + "."
        )
    if rain_diff is not None and rain_diff > 0.001:
        notes.append(
            f"The rain re-made for this scenario differs from the run's stored series by up to "
            f"{rain_diff:.3f} mm/h, so the run is not reproduced exactly."
        )
    return notes


def compute_twin_scenario(
    path: Path,
    *,
    rain_scale: float = 1.0,
    tide_offset_m: float = 0.0,
    cleaned_segments: set[str] | None = None,
    on_step: Callable[[int, int], None] | None = None,
    on_stage: Callable[[str], None] | None = None,
    baseline_root: Path | None = None,
) -> dict[str, Any]:
    """One full-city coupled Twin at the cycle's inputs with the scenario applied, compared with
    the same Twin with nothing changed. Synchronous; the job and the prewarm CLI both call this.

    The comparison run is taken from the cache when this run, code and city already have one;
    otherwise it runs first (stage ``baseline``) and is stored, in ``baseline_root`` when given
    (the shipped cache, for a prewarm). ``on_step`` then counts both runs' output steps as one
    series. The lever values are used as given; callers quantise them first (:func:`_quantise`).
    """
    from dataclasses import replace

    from varuna_cycle.twin_cycle import mass_balance_ledger, tide_notes
    from varuna_flash.whatif import CLEANED_BETA

    started = perf_counter()
    meta = json.loads((path / "run.json").read_text(encoding="utf-8"))
    run_id = str(meta.get("run_id", path.name))
    step_min = int(meta.get("step_min") or 5)
    n_steps = _n_steps(meta)
    fingerprint = run_fingerprint(path)

    if on_stage is not None:
        on_stage("sky")
    inputs = _load_scenario_inputs(meta)
    segment_ids = tuple(str(s) for s in inputs.index[0])

    city = str(meta["city"])
    cleaning = _twin_cleaning(
        city, set(cleaned_segments or ()), set(segment_ids) | _known_segments(city)
    )
    network = inputs.network
    if cleaning[0]:
        beta = np.asarray(network.beta, dtype=np.float64).copy()
        beta[[i for i, e in enumerate(network.edge_ids) if str(e) in cleaning[0]]] = CLEANED_BETA
        network = replace(network, beta=beta)

    tide = inputs.tide
    if tide_offset_m != 0.0:
        if tide is None:
            raise api_error(
                422,
                "no_tide",
                f"Bundle {meta.get('bundle')} carries no tide series, so there is no sea level to "
                "raise or lower. Set the tide offset to 0.",
                run_id=run_id,
            )
        tide = replace(tide, stage_m=np.asarray(tide.stage_m, dtype=np.float64) + tide_offset_m)
    # Unscaled rain is the cube itself, not a product with 1.0, so a scenario of nothing is the
    # bake's input bit for bit.
    rain = inputs.rain_mm_h if rain_scale == 1.0 else inputs.rain_mm_h * rain_scale
    nothing = rain_scale == 1.0 and tide_offset_m == 0.0 and not cleaning[0]

    held = _load_baseline(run_id, fingerprint)
    if held is not None and held[0].segment_ids != segment_ids:
        held = None
    runs = 1 if held is not None or nothing else 2

    def stepper(offset: int) -> Callable[[int, int], None] | None:
        if on_step is None:
            return None
        return lambda k, n: on_step(offset + k, runs * n)

    baseline_source = held[1] if held is not None else "computed"
    if held is not None:
        baseline = held[0]
    else:
        if not nothing and on_stage is not None:
            on_stage("baseline")
        elif on_stage is not None:
            on_stage("twin")
        base_run = _run_scenario_twin(
            inputs, inputs.network, inputs.rain_mm_h, inputs.tide, meta, stepper(0)
        )
        baseline = _Baseline(
            base_run.depth_cm,
            segment_ids,
            {
                "fingerprint": fingerprint,
                "computed_at": datetime.now(IST).isoformat(timespec="seconds"),
                "computed_on": _computed_on(),
                "twin_ms": base_run.ms,
                "sea": _sea_block(base_run.sea),
                "volume_in_m3": float(base_run.balance.volume_in_m3),
                "mass_balance_error": float(base_run.balance.error_fraction),
                "ledger": mass_balance_ledger(base_run.balance),
                "notes": list(base_run.notes),
            },
        )
        _store_baseline(run_id, baseline, root=baseline_root)

    if nothing:
        # The comparison run is this scenario: the Twin is deterministic on the same inputs, so
        # running it again would reproduce it bit for bit.
        after, sea = baseline.depth_cm, dict(baseline.meta.get("sea") or {})
        twin_ms = 0 if held is not None else int(baseline.meta["twin_ms"])
        volume_in = float(baseline.meta["volume_in_m3"])
        error_fraction = float(baseline.meta["mass_balance_error"])
        ledger = baseline.meta.get("ledger")
        twin_notes: tuple[str, ...] = tuple(baseline.meta.get("notes") or ())
        twin_stage_ms: dict[str, Any] = {}
    else:
        if on_stage is not None:
            on_stage("twin")
        scenario_run = _run_scenario_twin(
            inputs, network, rain, tide, meta, stepper(n_steps if runs == 2 else 0)
        )
        after, sea = scenario_run.depth_cm, _sea_block(scenario_run.sea)
        twin_ms = scenario_run.ms
        volume_in = float(scenario_run.balance.volume_in_m3)
        error_fraction = float(scenario_run.balance.error_fraction)
        ledger = mass_balance_ledger(scenario_run.balance)
        twin_notes = scenario_run.notes
        twin_stage_ms = scenario_run.stage_ms

    if on_stage is not None:
        on_stage("sampling")
    sampling_started = perf_counter()
    rows, counts = _segment_rows(after, baseline.depth_cm, segment_ids, step_min)
    hotspots, hotspots_missing = _hotspot_rows(
        path, after, baseline.depth_cm, segment_ids, step_min
    )
    drift = _drift(path, meta, baseline, step_min)
    sampling_ms = round((perf_counter() - sampling_started) * 1000.0)

    hotspots_moved = sum(1 for r in hotspots if abs(r["delta_cm"]) >= CHANGE_CM)
    base_sea = dict(baseline.meta.get("sea") or {})
    run_rain = meta.get("rain_aoi_mm_h") or []
    hyetograph = [round(float(v), 3) for v in inputs.rain_mm_h.mean(axis=(1, 2))]
    rain_diff = (
        max(abs(a - b) for a, b in zip(hyetograph, run_rain, strict=True))
        if run_rain and len(run_rain) == len(hyetograph)
        else None
    )

    return {
        "run_id": run_id,
        "method": TWIN_SCENARIO_METHOD,
        "blockage": BLOCKAGE_SOURCE,
        "scenario": {
            "rain_scale": rain_scale,
            "tide_offset_m": tide_offset_m,
            "cleaned_segments": cleaning[1],
            "cleaned_no_pipe": cleaning[2],
            "cleaned_unmatched": cleaning[3],
            "cleaned_edges": len(cleaning[0]),
        },
        "segments": rows,
        **counts,
        "hotspots": hotspots,
        "hotspots_moved": hotspots_moved,
        "hotspots_not_in_index": hotspots_missing,
        "baseline": {
            "method": "twin_nothing_changed",
            "source": baseline_source,
            "computed_at": baseline.meta.get("computed_at"),
            "computed_on": baseline.meta.get("computed_on"),
            "twin_ms": baseline.meta.get("twin_ms"),
            "drift": drift,
        },
        "sea": {
            "offset_m": tide_offset_m,
            "source": getattr(tide, "source", None),
            **_sea_block(sea),
            "sea_to_land_change_m3": round(
                float(sea.get("sea_to_land_m3", 0.0)) - float(base_sea.get("sea_to_land_m3", 0.0)),
                1,
            ),
            "volume_in_m3": round(volume_in, 1),
            "volume_in_change_m3": round(volume_in - float(baseline.meta["volume_in_m3"]), 1),
        },
        "mass_balance": {
            "error_fraction": error_fraction,
            "budget": 1e-3,
            "within_budget": error_fraction <= 1e-3,
            "ledger": ledger,
            "run_error_fraction": meta.get("mass_balance_err"),
        },
        "rain_reproduces_run": rain_diff is not None and rain_diff <= 0.001,
        "rain_max_diff_mm_h": rain_diff,
        "timings": {
            "rain_ms": inputs.rain_ms,
            "rain_cached": inputs.rain_cached,
            "twin_ms": twin_ms,
            "baseline_ms": None if held is not None or nothing else baseline.meta["twin_ms"],
            "twin_stage_ms": twin_stage_ms,
            "sampling_ms": sampling_ms,
        },
        "twin_ms": twin_ms,
        "ms": round((perf_counter() - started) * 1000.0),
        "notes": _scenario_notes(
            tide_offset_m=tide_offset_m,
            sea=sea,
            base_sea=base_sea,
            counts=counts,
            hotspots_moved=hotspots_moved,
            hotspots_missing=hotspots_missing,
            cleaning=cleaning,
            rain_diff=rain_diff,
            tide_labels=tide_notes(tide),
            twin_notes=twin_notes,
            drift=drift,
        ),
    }


# ------------------------------------------------------------------------------- jobs
@dataclass
class TwinScenarioJob:
    """One full-city Twin what-if, readable from any request while it runs and after."""

    job_id: str
    run_id: str
    key: str
    scenario: dict[str, Any]
    expected_ms: int | None
    expected_from: str
    started_at: datetime
    state: str = "running"
    stage: str = "queued"
    step: int = 0
    n_steps: int = 0
    baseline_steps: int = 0
    """Of ``n_steps``, how many belong to the comparison run with nothing changed (0 when it is
    cached, or when the scenario is that run)."""
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    cache: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    cancel: bool = False
    clock: float = field(default_factory=perf_counter)
    ended: float | None = None

    @property
    def running(self) -> bool:
        return self.state == "running"

    def progress(self) -> dict[str, Any]:
        """The small payload the WebSocket carries per step."""
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "state": self.state,
            "stage": self.stage,
            "step": self.step,
            "n_steps": self.n_steps,
            "baseline_steps": self.baseline_steps,
            "elapsed_ms": round(((self.ended or perf_counter()) - self.clock) * 1000.0),
            "expected_ms": self.expected_ms,
        }

    def payload(self) -> dict[str, Any]:
        return {
            **self.progress(),
            "expected_from": self.expected_from,
            "scenario": self.scenario,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
            "cache": self.cache,
            "result": self.result,
        }


def _keep(job: TwinScenarioJob) -> None:
    with _JOBS_GUARD:
        _JOBS[job.job_id] = job
        finished = [k for k, j in _JOBS.items() if not j.running]
        for stale in finished[: max(0, len(_JOBS) - MAX_JOBS_KEPT)]:
            del _JOBS[stale]


def _publish_progress(state: AppState | None, job: TwinScenarioJob) -> None:
    """Relay a job's progress when the bus knows the topic; polling the GET works regardless."""
    if state is None:
        return
    try:
        from varuna_cycle.bus import TOPICS

        if PROGRESS_TOPIC not in TOPICS:
            return
        state.bus.publish_threadsafe(PROGRESS_TOPIC, job.progress(), run_id=job.run_id)
    except (RuntimeError, ValueError):
        log.debug("whatif.twin_progress_not_published", job_id=job.job_id)


def _computed_payload(result: dict[str, Any], fingerprint: str) -> dict[str, Any]:
    return {
        **result,
        "fingerprint": fingerprint,
        "computed_at": datetime.now(IST).isoformat(timespec="seconds"),
        "computed_on": _computed_on(),
    }


def _cancelled_message(job: TwinScenarioJob) -> str:
    """Where a cancelled job stopped, counted in the run it was in rather than both together."""
    base = job.baseline_steps
    if job.stage == "baseline":
        where = f"the Twin with nothing changed at step {job.step} of {base or 'the run'}"
    elif job.stage == "twin":
        own = job.n_steps - base
        where = f"the scenario's Twin at step {max(0, job.step - base)} of {own or 'the run'}"
    else:
        where = {
            "queued": "the start",
            "sky": "the re-make of the cycle's rain",
            "sampling": "the street comparison",
        }.get(job.stage, job.stage)
    return f"Cancelled during {where}. Nothing was stored."


def _run_job(
    job: TwinScenarioJob,
    path: Path,
    params: dict[str, Any],
    fingerprint: str,
    state: AppState | None,
    lock: threading.Lock,
) -> None:
    saw_baseline = False

    def on_step(k: int, n: int) -> None:
        # Both runs are counted as one series of `n`; the comparison run is its first half.
        job.step, job.n_steps = k, n
        job.baseline_steps = n // 2 if saw_baseline else 0
        _publish_progress(state, job)
        if job.cancel:
            raise _Cancelled

    def on_stage(name: str) -> None:
        nonlocal saw_baseline
        if name == "baseline":
            saw_baseline = True
        elif name == "twin" and not saw_baseline and job.baseline_steps:
            # The comparison run was cached after all (another job stored it while this one
            # waited), so only the scenario's own steps are left.
            job.n_steps -= job.baseline_steps
            job.baseline_steps = 0
        job.stage = name
        _publish_progress(state, job)
        if job.cancel:
            raise _Cancelled

    try:
        result = compute_twin_scenario(path, on_step=on_step, on_stage=on_stage, **params)
        payload = _computed_payload(result, fingerprint)
        _store(job.run_id, job.key, payload)
        job.result = payload
        job.cache = _cache_label({"source": "computed"}, payload)
        job.state, job.stage = "done", "done"
    except _Cancelled:
        job.state = "cancelled"
        job.error = {"code": "cancelled", "message": _cancelled_message(job)}
    except HTTPException as error:
        job.state = "failed"
        job.error = (
            dict(error.detail)
            if isinstance(error.detail, dict)
            else {"code": "twin_failed", "message": str(error.detail)}
        )
    except Exception as error:  # every failure becomes the job's own sentence
        job.state = "failed"
        job.error = {
            "code": "twin_failed",
            "message": f"The full-city Twin scenario failed during {job.stage}: {error}",
        }
        log.warning("whatif.twin_failed", job_id=job.job_id, stage=job.stage, error=str(error))
    finally:
        job.finished_at = datetime.now(IST)
        job.ended = perf_counter()
        lock.release()
        _publish_progress(state, job)
        log.info(
            "whatif.twin_finished",
            job_id=job.job_id,
            run_id=job.run_id,
            key=job.key,
            state=job.state,
            ms=round((perf_counter() - job.clock) * 1000.0),
        )


def _expected_ms(
    meta: dict[str, Any], rain_cached: bool, twin_runs: int = 1
) -> tuple[int | None, str]:
    """What the run's own Sky and Twin stages took, which is what this job repeats: the Twin once
    for the scenario, and once more for the comparison run when this run has none cached."""
    stage_ms = meta.get("stage_ms") or {}
    twin = stage_ms.get("twin")
    sky = 0 if rain_cached else stage_ms.get("sky")
    if twin is None or sky is None:
        return None, "The run records no stage timings to estimate from."
    twin_part = (
        f"twin {int(twin):,} ms"
        if twin_runs == 1
        else f"twin {int(twin):,} ms twice (the scenario, and the same run with nothing changed "
        "to compare it with)"
    )
    source = (
        f"Run {meta.get('run_id')}: {twin_part}"
        + ("; the cycle's rain is already in memory" if rain_cached else f" + sky {int(sky):,} ms")
        + ", plus the city load on a server's first scenario."
    )
    return int(twin) * twin_runs + int(sky), source


def _scenario_params(body: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The lever values on the 0.1 grid, refused outside SPEC.md 7.7's ranges."""
    notes: list[str] = []
    try:
        rain = float(body.get("rain_scale", 1.0))
        tide = float(body.get("tide_offset_m", 0.0))
    except (TypeError, ValueError) as error:
        raise api_error(
            422, "bad_scenario", f"Rain scale and tide offset are numbers: {error}."
        ) from error
    lo, hi = RAIN_SCALE_RANGE
    if not lo <= rain <= hi:
        raise api_error(422, "out_of_range", f"Rain scale runs from {lo}x to {hi}x; got {rain}x.")
    lo, hi = TIDE_OFFSET_RANGE_M
    if not lo <= tide <= hi:
        raise api_error(
            422, "out_of_range", f"Tide offset runs from {lo} to +{hi} m; got {tide} m."
        )
    rain_q, tide_q = _quantise(rain), _quantise(tide)
    if abs(rain_q - rain) > 1e-9 or abs(tide_q - tide) > 1e-9:
        notes.append(
            f"Levers rounded to the {SCENARIO_QUANTUM} grid the cache is keyed on: rain "
            f"{rain_q:.1f}x, tide {tide_q:+.1f} m."
        )
    cleaned = {str(s) for s in body.get("cleaned_segments") or []}
    return {"rain_scale": rain_q, "tide_offset_m": tide_q, "cleaned_segments": cleaned}, notes


def _resolve_twin_run(body: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    run_id = body.get("run_id")
    path = run_dir(str(run_id)) if run_id else _latest_run()
    if not (path / "run.json").is_file():
        raise api_error(404, "run_not_found", f"Run {path.name} is not in data/runs.")
    meta = json.loads((path / "run.json").read_text(encoding="utf-8"))
    if not meta.get("city") or not meta.get("bundle") or not meta.get("cycle_ts"):
        raise api_error(
            409,
            "no_city",
            f"Run {path.name} does not name its city, bundle and cycle in run.json, so its Twin "
            "cannot be re-run. Re-bake the cycle.",
            run_id=path.name,
        )
    if not (path / "segments_wet.json").is_file():
        raise api_error(
            409,
            "no_baseline",
            f"Run {path.name} has no segments_wet.json to compare a scenario with. Re-bake it.",
            run_id=path.name,
        )
    return path, meta


def _refuse_missing(path: Path, meta: dict[str, Any], tide_offset_m: float) -> None:
    city, bundle = str(meta["city"]), str(meta["bundle"])
    reason = _missing_sources(city, bundle)
    if reason:
        raise api_error(503, "no_city_grid", reason, run_id=path.name)
    if tide_offset_m != 0.0 and not (bundle_dir(bundle) / "tide.csv").is_file():
        raise api_error(
            422,
            "no_tide",
            f"Bundle {bundle} carries no tide series, so there is no sea level to raise or lower. "
            "Set the tide offset to 0.",
            run_id=path.name,
        )


def prewarm_scenario(
    run_id: str,
    *,
    rain_scale: float = 1.0,
    tide_offset_m: float = 0.0,
    cleaned_segments: set[str] | None = None,
    shipped: bool = False,
    force: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compute one scenario synchronously and store it; `varuna whatif prewarm` calls this.

    ``shipped`` writes to the read-only cache that ships with the repository (`demo/whatif/`)
    instead of this machine's `data/whatif/`. Returns the answer and where it came from.
    """
    path, meta = _resolve_twin_run({"run_id": run_id})
    params, _ = _scenario_params(
        {
            "rain_scale": rain_scale,
            "tide_offset_m": tide_offset_m,
            "cleaned_segments": sorted(cleaned_segments or ()),
        }
    )
    _refuse_missing(path, meta, params["tide_offset_m"])
    fingerprint = run_fingerprint(path)
    edges, *_ = _twin_cleaning(
        str(meta["city"]), params["cleaned_segments"], _known_segments(str(meta["city"]))
    )
    key = scenario_key(params["rain_scale"], params["tide_offset_m"], edges)
    if not force:
        hit = _cached(path.name, key, fingerprint)
        if hit is not None and (not shipped or hit[1]["source"] == "shipped"):
            return hit[0], _cache_label(hit[1], hit[0])
    root = _shipped_cache_root() if shipped else None
    payload = _computed_payload(
        compute_twin_scenario(path, baseline_root=root, **params), fingerprint
    )
    stored = _store(path.name, key, payload, root=root)
    if shipped and payload["baseline"]["source"] != "shipped":
        # The shipped answers are compared with a comparison run that must travel with them.
        held = _load_baseline(path.name, fingerprint)
        if held is not None:
            _store_baseline(path.name, held[0], root=root)
    where = {"source": "computed", "path": f"{stored.parent.parent.name}/{path.name}/{stored.name}"}
    return payload, _cache_label(where, payload)


@router.post(
    "/whatif/twin",
    summary="Run one scenario on the full-city Twin (the tide lever), as a job",
    status_code=202,
)
def whatif_twin(
    body: Annotated[dict[str, Any], Body()],
    state: Annotated[AppState, Depends(get_state)],
) -> JSONResponse:
    """Start a full-city coupled Twin at the cycle's inputs with the scenario applied.

    Body: ``{run_id?, rain_scale?, tide_offset_m?, cleaned_segments?}``; a ``pump_plan`` or
    ``clean_top`` from the what-if is not run here and is named in ``scenario.levers_left_out``.
    Answers **202** with the
    job (``job_id``, ``expected_ms`` from the run's own stage timings); poll
    ``GET /v1/whatif/twin/{job_id}`` or listen for ``whatif.progress`` on ``WS /v1/live``. A
    scenario already computed for this exact run answers **200** with ``state: done`` and the
    result, saying which cache it came from.
    """
    if not twin_scenario_enabled():
        raise api_error(
            403,
            "whatif_twin_disabled",
            f"The full-city Twin what-if is off on this server ({TWIN_JOB_ENV}=0): one scenario "
            "holds the host for about a minute. The emulator what-if and the physics check still "
            "answer rain and cleaning.",
        )
    path, meta = _resolve_twin_run(body)
    params, param_notes = _scenario_params(body)
    _refuse_missing(path, meta, params["tide_offset_m"])
    edges, *_ = _twin_cleaning(
        str(meta["city"]), params["cleaned_segments"], _known_segments(str(meta["city"]))
    )
    key = scenario_key(params["rain_scale"], params["tide_offset_m"], edges)
    fingerprint = run_fingerprint(path)
    left_out, left_out_notes = _twin_levers_left_out(body, in_check=False)
    scenario = {
        "rain_scale": params["rain_scale"],
        "tide_offset_m": params["tide_offset_m"],
        "cleaned_segments": sorted(params["cleaned_segments"]),
        # Emulator-only levers the caller also set: not in this run, and said so.
        "levers_left_out": left_out,
        "notes": [*param_notes, *left_out_notes],
    }
    now = datetime.now(IST)

    hit = _cached(path.name, key, fingerprint)
    if hit is not None:
        payload, where = hit
        done = TwinScenarioJob(
            job_id=uuid.uuid4().hex[:12],
            run_id=path.name,
            key=key,
            scenario=scenario,
            expected_ms=0,
            expected_from="Already computed for this run.",
            started_at=now,
            state="done",
            stage="done",
            n_steps=_n_steps(meta),
            step=_n_steps(meta),
            finished_at=now,
            ended=perf_counter(),
            result=payload,
            cache=_cache_label(where, payload),
        )
        _keep(done)
        return JSONResponse(status_code=200, content=done.payload())

    with _JOBS_GUARD:
        running = [j for j in _JOBS.values() if j.running]
    same = next((j for j in running if j.run_id == path.name and j.key == key), None)
    if same is not None:
        return JSONResponse(status_code=202, content=same.payload())

    lock = _heavy_lock()
    if not lock.acquire(blocking=False):
        other = running[0] if running else None
        message = (
            f"A full-city what-if is already running on this server (job {other.job_id}, rain "
            f"{other.scenario['rain_scale']:.1f}x, tide {other.scenario['tide_offset_m']:+.1f} m, "
            f"step {other.step} of {other.n_steps or 'the run'}). One runs at a time: ask this "
            "one when it finishes, or cancel it where it was started."
            if other is not None
            else "A live cycle or a computed rain nowcast is using the machine. Run the what-if "
            "when it has published."
        )
        raise api_error(503, "whatif_busy", message, run_id=path.name)

    nothing = params["rain_scale"] == 1.0 and params["tide_offset_m"] == 0.0 and not edges
    baseline_steps = 0 if nothing or baseline_cached(path.name, fingerprint) else _n_steps(meta)
    expected, expected_from = _expected_ms(
        meta, _rain_key(meta) in _RAIN_CACHE, 2 if baseline_steps else 1
    )
    job = TwinScenarioJob(
        job_id=uuid.uuid4().hex[:12],
        run_id=path.name,
        key=key,
        scenario=scenario,
        expected_ms=expected,
        expected_from=expected_from,
        started_at=now,
        n_steps=_n_steps(meta) + baseline_steps,
        baseline_steps=baseline_steps,
    )
    _keep(job)
    try:
        threading.Thread(
            target=_run_job,
            args=(job, path, params, fingerprint, state, lock),
            name=f"whatif-twin-{job.job_id}",
            daemon=True,
        ).start()
    except Exception:
        job.state = "failed"
        lock.release()
        raise
    log.info("whatif.twin_started", job_id=job.job_id, run_id=path.name, key=key)
    return JSONResponse(status_code=202, content=job.payload())


def _job(job_id: str) -> TwinScenarioJob:
    with _JOBS_GUARD:
        job = _JOBS.get(job_id)
    if job is None:
        raise api_error(
            404,
            "unknown_job",
            f"No what-if job {job_id} on this server. Jobs are held in memory and a restart "
            "forgets them; post the scenario again, and a finished one answers from the cache.",
        )
    return job


@router.get("/whatif/twin/{job_id}", summary="A full-city Twin what-if job: progress and result")
def whatif_twin_job(job_id: str) -> dict[str, Any]:
    """State (``running``, ``done``, ``failed``, ``cancelled``), step ``k`` of ``n``, and the
    result once done."""
    return _job(job_id).payload()


@router.post("/whatif/twin/{job_id}/cancel", summary="Cancel a running full-city Twin what-if")
def whatif_twin_cancel(job_id: str) -> dict[str, Any]:
    """Stop the job at its next output step. Nothing it computed is stored."""
    job = _job(job_id)
    if job.running:
        job.cancel = True
    return job.payload()

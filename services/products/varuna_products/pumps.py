"""Where to send the pumps, and what it buys (SPEC.md 11.10, 7.6; tasks P8.9, P8.10).

**The inventory is synthetic and says so.** `city/<city>/assets.geojson` carries twelve mobile
pumps with `synthetic: true` and a note saying their capacity and status are invented; only the
depot coordinates are real OSM ward offices. Every product here carries that label forward, and
the board prints it in its header (SPEC.md rule 6).

**The benefit model, stated in full.** SPEC.md 11.10 wants benefit as "minutes above 45 cm
avoided", measured by re-running the emulator with the extra outflow. Flash-lite exists (task
P7.5), so that is what happens when it is on disk and the storm that drove the run is known:

    The pump's rated capacity is turned into cm per step over the 45 m disc the depth series
    describes, and `varuna_flash.simulate` is re-run for the candidate's own road segments with
    that drawdown in `pump_cm_per_step`. The difference between the two emulator runs is the
    water the pump actually finds to remove, and it is subtracted from the *Twin's* depth series
    before the minutes above the threshold are recounted - the delta-correction
    `varuna_api.routers.whatif` uses, so the level anybody reads stays the physics'.

    That difference saturates, which is the whole point of using the emulator: a pump can only
    remove water that is there, and once the locally ponded rain is gone it removes nothing more.

**What that model cannot see.** Flash-lite is a perturbation around a base state - the depth
every training storm produced regardless of intensity, which at the coast is tide and runoff
arriving from upstream (`varuna_flash.model`). The base state cancels in the difference, so the
benefit counted here is the pump against the *rain that fell locally* and not against the sea. On
a tide-locked street that understates a real pump. It is a lower bound, and it is labelled.

**The fallback, when no fitted emulator is on disk or the caller did not hand over the storm**
(a fresh clone before `make train`; a caller with no hyetograph to give), is the explicit reduced
model this file has always carried::

    A pump at the hotspot removes `capacity_m3_per_h` from the water ponded over its
    neighbourhood - the same 45 m disc the depth is sampled over - which lowers the depth series
    at a constant rate of `capacity / area` metres per hour from the moment it arrives. The
    minutes above the threshold are then recounted on the lowered series.

That is a bathtub: it ignores the inflow that keeps arriving, the drain that is already pulling
water out, and the fact that a junction is not a cylinder. It will overstate the benefit of a
pump at a spot that is still filling. Which model produced a number is on the plan in
``benefit_model`` and on every assignment, so the board and the screen cannot drift apart.

**The optimiser** is the greedy of SPEC.md 11.10 (P0; MILP is P1): pumps in descending
capacity, each to the hotspot with the largest weighted remaining excess, one hotspot per pump.
It skips a pump the authority desk has marked unavailable (:data:`ASSIGNABLE_STATES`, task
D-07); the pump stays on the plan, with its status, under `withheld`, because a board that
silently drops a lorry is a board that has stopped telling an officer where his fleet is.
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only; varuna_flash is imported lazily below
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from varuna_flash.model import FlashModel

log = structlog.get_logger("varuna.products.pumps")

__all__ = [
    "ASSIGNABLE_STATES",
    "BENEFIT_LABEL",
    "BENEFIT_LABELS",
    "EMULATOR_LABEL",
    "MAX_PLACEMENTS",
    "MODEL_PATHS",
    "PUMP_THRESHOLD_CM",
    "TRAVEL_SPEED_KMH",
    "build_pump_plan",
    "price_placements",
    "rain_for_run",
    "write_pump_plan",
]

PUMP_THRESHOLD_CM = 45.0
"""The depth pumping is judged against: `--depth-4`, where buses and trucks stop (SPEC.md 6.2).

SPEC.md 11.10 measures a pump's benefit in "minutes above 45 cm avoided", so this is the
spec's number rather than a choice."""

HOTSPOT_RADIUS_M = 45.0
"""The disc the depth series describes, and therefore the area a pump is drawing down.

The same radius `varuna_products.hotspots` samples over, so the depth being lowered and the area
it is spread across are the same patch of ground. Both benefit models use it, so the two answer
the same question with different dynamics."""

TRAVEL_SPEED_KMH = 18.0
"""Assumed speed for a pump lorry crossing a flooding city.

A choice, and a conservative one: Mumbai traffic on a dry weekday averages more, and this is a
monsoon morning with streets closing. It is used only for the ETA on the dispatch order, never
for the benefit - the arrival time shifts when the drawdown starts, which is why it appears at
all. Route-aware travel time arrives with the routing service in P8.1."""

MODEL_PATHS = ("data/train/flash_lite.npz", "demo/flash_lite.npz")
"""Where the fitted emulator is looked for, in order - the same two paths `/v1/whatif` uses."""

BENEFIT_LABEL = "Bathtub estimate, not a physics run"
"""What the board prints beside a benefit the fallback model produced (rule 6)."""

EMULATOR_LABEL = "Flash-lite emulator re-run with the pump's outflow"
"""What the board prints beside a benefit the emulator produced (rule 6)."""

MIXED_LABEL = "Flash-lite for most assignments, bathtub estimate for the rest"
"""When some candidate had no road segment the emulator knows, so both models are in the plan."""

BENEFIT_LABELS = {
    "emulator": EMULATOR_LABEL,
    "reduced_model": BENEFIT_LABEL,
    "mixed": MIXED_LABEL,
}
"""``benefit_model`` to the sentence printed beside the number, so the two cannot disagree."""

ASSIGNABLE_STATES = frozenset({"available", "moved"})
"""Pump states the optimiser may still send somewhere (task D-07).

``varuna_route.ops_overlay.PUMP_STATES`` has three values and only one of them withholds a
pump. ``unavailable`` is a lorry that cannot go - broken, or already committed elsewhere - and
the greedy skips it. ``moved`` is the same lorry at a different depot, and the desk's entry
carries that depot's point precisely so it can be dispatched from there; withholding it would
make the coordinate pointless. This is narrower than
:meth:`~varuna_route.ops_overlay.OpsOverlay.unavailable_pump_ids`, which answers "not at its
depot" rather than "cannot be sent", so the caller passes the statuses and this module decides.

Until this list existed the status was read off the asset, carried onto the plan and then
ignored: every pump in the inventory was assignable whatever it said."""


def _minutes_above(series: Sequence[float], threshold: float, step_min: int) -> int:
    return sum(step_min for value in series if value > threshold)


def _drawn_down(series: list[float], rate_cm_per_step: float, from_step: int) -> list[float]:
    """The depth series with a pump running from ``from_step``, under the fallback model.

    The drawdown accumulates - a pump that has been running for an hour has removed an hour of
    water - and depth is floored at zero, because a pump cannot make a street concave.
    """
    out = list(series)
    removed = 0.0
    for i in range(from_step, len(out)):
        removed += rate_cm_per_step
        out[i] = max(out[i] - removed, 0.0)
    return out


def _after_delta(series: list[float], delta: Sequence[float], from_step: int) -> list[float]:
    """The depth series with the emulator's drawdown applied from ``from_step``.

    ``delta`` is the emulator's answer for a pump that started at step 0, so a pump that arrives
    at step ``a`` gets that same profile shifted by ``a``: it has been running for ``t - a``
    steps when the forecast reaches step ``t``. Past the end of the profile the drawdown holds at
    its last value, which is where it has saturated.
    """
    out = list(series)
    if not delta:
        return out
    for i in range(from_step, len(out)):
        removed = delta[min(i - from_step, len(delta) - 1)]
        out[i] = max(out[i] - removed, 0.0)
    return out


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _load_model() -> FlashModel | None:
    """The fitted emulator, or ``None`` when nobody has run ``make train`` in this clone.

    Guarded rather than raised: a missing emulator is not a broken pump plan, it is a plan whose
    benefit comes from the fallback model and says so.
    """
    from varuna_schemas.paths import repo_root

    try:
        from varuna_flash.model import load
    except ImportError:  # pragma: no cover - varuna-flash is a declared dependency
        return None
    for candidate in MODEL_PATHS:
        path = repo_root() / candidate
        if path.is_file():
            try:
                return load(path)
            except (OSError, ValueError, KeyError) as error:
                log.warning("products.pump_model_unreadable", path=str(path), error=str(error))
                return None
    return None


def rain_for_run(run_id: str) -> list[float] | None:
    """The storm a **published** run was driven by, read back from its own ``run.json``.

    The emulator has to be driven by the same rain the Twin saw, and the cycle stores it for
    exactly this reason (`rain_aoi_mm_h`, SPEC.md 10.3). This is how a re-optimise prices a
    plan for a run that already exists.

    **It is the caller's explicit act, never a fallback inside** :func:`build_pump_plan`. Reading
    the run directory from in there would make the plan depend on whether that directory happened
    to exist: a first bake would find nothing and use the fallback model, a re-bake over the top
    of it would find the rain it had just written and use the emulator, and the same cycle would
    produce two different `pump_plan.json` files. Rule 8 asks for byte-identical bakes, and
    `services/cycle/tests/test_idempotence.py` checks it.
    """
    from varuna_schemas.paths import run_dir

    try:
        record = run_dir(run_id) / "run.json"
        if not record.is_file():
            return None
        rain = json.loads(record.read_text(encoding="utf-8")).get("rain_aoi_mm_h")
    except (OSError, ValueError) as error:
        log.warning("products.pump_rain_unreadable", run_id=run_id, error=str(error))
        return None
    if not rain:
        return None
    return [float(v) for v in rain]


class _EmulatorBenefit:
    """Benefit as SPEC.md 11.10 asks for it: the emulator re-run with the pump's outflow.

    One instance per plan. Flash-lite solves each road segment independently, so a pump at one
    junction can be priced on a model sliced down to that junction's own segments - half a
    millisecond per call instead of fourteen for the whole city - and the answers are identical
    to slicing the full run afterwards.
    """

    def __init__(self, model: FlashModel, rain: Sequence[float], city_root: Path) -> None:
        import numpy as np

        self._model = model
        self._rain = np.asarray(rain, dtype=np.float64)
        self._city_root = city_root
        self._index = {sid: i for i, sid in enumerate(model.segment_ids)}
        self._streets: dict[str, list[str]] | None = None
        # candidate id -> (sub-model, index of the segment the series is read at), or None when
        # the emulator has no segment for that candidate.
        self._sub: dict[str, tuple[FlashModel, int] | None] = {}
        self._delta: dict[tuple[str, float], tuple[float, ...]] = {}

    @property
    def provenance(self) -> dict[str, Any]:
        """The emulator's measured skill, carried beside every number it produced (rule 6)."""
        return {
            "rmse_cm": round(self._model.rmse_cm, 2),
            "csi_30cm": round(self._model.csi_30cm, 3),
            "n_training_runs": self._model.n_training_runs,
        }

    def _street_segments(self) -> dict[str, list[str]]:
        """Street name to its segment ids, for the candidates that are streets rather than
        register entries. Read once per plan; the register's own entries carry their ids."""
        if self._streets is None:
            from varuna_products.depth import segment_names

            grouped: dict[str, list[str]] = {}
            for segment_id, name in segment_names(self._city_root).items():
                grouped.setdefault(name, []).append(segment_id)
            self._streets = grouped
        return self._streets

    def _sub_model(
        self, candidate_id: str, hotspot: dict[str, Any]
    ) -> tuple[FlashModel, int] | None:
        """The emulator sliced to this candidate, and which of its segments the benefit is read at.

        The depth series a candidate is judged on is the deepest water in its neighbourhood - a
        90th percentile over the hotspot's disc, or the worst segment on a street - so the
        drawdown is read at the segment the emulator itself makes deepest. The pump is applied to
        all of the candidate's segments; because they are independent, that choice changes only
        which one is reported.
        """
        if candidate_id in self._sub:
            return self._sub[candidate_id]

        import dataclasses

        import numpy as np
        from varuna_flash.model import simulate

        ids = list(hotspot.get("segment_ids") or [])
        if not ids:
            ids = self._street_segments().get(str(hotspot.get("name")), [])
        known = [sid for sid in ids if sid in self._index]
        if not known:
            self._sub[candidate_id] = None
            return None

        select = np.array([self._index[sid] for sid in known])
        model = self._model
        sub = dataclasses.replace(
            model,
            segment_ids=tuple(known),
            k_steps=model.k_steps[select],
            gain=model.gain[select],
            drain_cm_per_step=model.drain_cm_per_step[select],
            beta_ref=model.beta_ref[select],
            baseline_cm=model.baseline_cm[:, select],
        )
        deepest = int(np.argmax(simulate(sub, self._rain).max(axis=0)))
        self._sub[candidate_id] = (sub, deepest)
        return self._sub[candidate_id]

    def drawdown(
        self, candidate_id: str, hotspot: dict[str, Any], rate_cm_per_step: float
    ) -> tuple[float, ...] | None:
        """cm the pump removes at each step after it arrives, or ``None`` if it cannot be priced.

        Two emulator runs at the candidate's segments, with and without the pump's outflow; the
        difference is what the pump found to remove. Cached per (candidate, rate) because the
        greedy prices every pump against every candidate, and a fleet carries far fewer distinct
        capacities than pumps - Mumbai's twelve are three.
        """
        key = (candidate_id, round(rate_cm_per_step, 6))
        if key in self._delta:
            return self._delta[key]
        sliced = self._sub_model(candidate_id, hotspot)
        if sliced is None:
            return None

        import numpy as np
        from varuna_flash.model import simulate

        sub, deepest = sliced
        pump = np.full(sub.n_segments, rate_cm_per_step)
        before = simulate(sub, self._rain)[:, deepest]
        after = simulate(sub, self._rain, pump_cm_per_step=pump)[:, deepest]
        # Non-negative by construction - extra outflow cannot raise a depth - but clipped rather
        # than trusted, because a negative "benefit" would silently become a saving below.
        delta = tuple(float(v) for v in np.maximum(before - after, 0.0))
        self._delta[key] = delta
        return delta


def _emulator(
    city_root: Path,
    run_id: str,
    model: FlashModel | None,
    rain_mm_h: Sequence[float] | None,
) -> _EmulatorBenefit | None:
    """The emulator-backed benefit model, or ``None`` when this plan has to use the fallback."""
    fitted = model if model is not None else _load_model()
    if fitted is None:
        log.info("products.pump_benefit_fallback", run_id=run_id, reason="no_fitted_emulator")
        return None
    if not rain_mm_h:
        log.info("products.pump_benefit_fallback", run_id=run_id, reason="no_rain_series")
        return None
    return _EmulatorBenefit(fitted, list(rain_mm_h), city_root)


def _fleet(
    city_root: Path,
    pump_status: Mapping[str, str] | None,
    pump_depots: Mapping[str, tuple[float, float]] | None,
) -> list[dict[str, Any]] | None:
    """The synthetic fleet with the desk's statuses and depots over it, largest first.

    None when the city carries no inventory at all, which is a different answer from an empty
    fleet: the caller says "no inventory" rather than "no pump could help".
    """
    assets_path = city_root / "assets.geojson"
    if not assets_path.is_file():
        return None

    features = json.loads(assets_path.read_text(encoding="utf-8")).get("features", [])
    pumps = []
    for feature in features:
        props = feature.get("properties", {})
        if props.get("kind") != "mobile_pump":
            continue
        lon, lat = feature["geometry"]["coordinates"][:2]
        pump_id = str(props.get("asset_id"))
        # The desk's word beats the inventory's: the status on the asset is synthetic and fixed
        # at build time, and an officer who marked a lorry unavailable this morning is the only
        # one of the two who has looked at it (TECH_SPEC 3.6).
        status = str((pump_status or {}).get(pump_id) or props.get("status") or "available")
        moved_to = (pump_depots or {}).get(pump_id)
        pumps.append(
            {
                "pump_id": pump_id,
                "capacity_m3_per_h": float(props.get("capacity_m3_per_h") or 0.0),
                "depot": props.get("depot"),
                "status": status,
                "assignable": status in ASSIGNABLE_STATES,
                "lon": float(moved_to[0]) if moved_to else float(lon),
                "lat": float(moved_to[1]) if moved_to else float(lat),
                "moved": bool(moved_to),
                "synthetic": bool(props.get("synthetic", True)),
            }
        )
    pumps.sort(key=lambda p: -p["capacity_m3_per_h"])
    return pumps


def _candidates(
    hotspots: list[dict[str, Any]],
    streets: dict[str, list[float]] | None,
    street_points: dict[str, tuple[float, float]] | None,
    step_min: int,
) -> list[dict[str, Any]]:
    """Every place that crosses the threshold: the register, then named streets, worst first."""
    candidates: list[dict[str, Any]] = []
    for hotspot in hotspots:
        series = [float(v) for v in hotspot.get("depth_cm", [])]
        before = _minutes_above(series, PUMP_THRESHOLD_CM, step_min)
        if before <= 0:
            continue
        candidates.append({"hotspot": hotspot, "series": series, "minutes_before": before})

    for street, series in (streets or {}).items():
        point = (street_points or {}).get(street)
        if point is None:
            continue  # no coordinate, nowhere to send a lorry
        values = [float(v) for v in series]
        before = _minutes_above(values, PUMP_THRESHOLD_CM, step_min)
        if before <= 0:
            continue
        candidates.append(
            {
                # Shaped like a hotspot so the loop below does not care which it is; the id is
                # prefixed so a street can never collide with a register entry.
                "hotspot": {
                    "hotspot_id": f"street:{street}",
                    "name": street,
                    "lon": point[0],
                    "lat": point[1],
                    "exposure": {"weight": 0.5},
                },
                "series": values,
                "minutes_before": before,
            }
        )
    candidates.sort(key=lambda c: -c["minutes_before"])
    return candidates


def _travel_min(pump: Mapping[str, Any], hotspot: Mapping[str, Any]) -> float:
    return (
        _haversine_km(pump["lon"], pump["lat"], hotspot["lon"], hotspot["lat"])
        / TRAVEL_SPEED_KMH
        * 60.0
    )


def _rate_cm_per_step(capacity_m3_per_h: float, step_min: int) -> float:
    """m3/h over the hotspot's disc, as cm of depth per forecast step."""
    area_m2 = math.pi * HOTSPOT_RADIUS_M**2
    return capacity_m3_per_h / area_m2 * 100.0 * (step_min / 60.0)


def build_pump_plan(
    hotspots: list[dict[str, Any]],
    city_root: Path,
    run_id: str,
    step_min: int = 5,
    streets: dict[str, list[float]] | None = None,
    street_points: dict[str, tuple[float, float]] | None = None,
    *,
    model: FlashModel | None = None,
    rain_mm_h: Sequence[float] | None = None,
    pump_status: Mapping[str, str] | None = None,
    pump_depots: Mapping[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Assign the synthetic pump fleet to the places that flood, greedily by benefit.

    Only places that actually cross the threshold are candidates: dispatching a lorry to a
    junction the forecast keeps below 45 cm is the kind of thing an operator would notice, and a
    board that suggests it stops being trusted.

    Candidates are the chronic register **and** the named streets, for the same reason the alert
    queue carries both - on a cycle where the register stays below 45 cm and 226 streets do not,
    a board scoped to hotspots would send twelve pumps nowhere.

    Args:
        model: the fitted Flash-lite emulator. Loaded from :data:`MODEL_PATHS` when absent.
        rain_mm_h: the storm the run was driven by, one value per step - the cycle's own AOI-mean
            hyetograph while it is computing, or :func:`rain_for_run` for a run already on disk.
            Never read from the run directory here; see :func:`rain_for_run` for why.
        pump_status: pump id to the status the authority desk last set, overriding the synthetic
            inventory's own field. Anything outside :data:`ASSIGNABLE_STATES` is listed on the
            plan under ``withheld`` and is never assigned.
        pump_depots: pump id to the lon/lat the desk moved it to, used for the travel time.

    Without both of those the benefit falls back to the bathtub model, and ``benefit_model`` on
    the plan and on every assignment says which one produced the number.
    """
    pumps = _fleet(city_root, pump_status, pump_depots)
    if pumps is None:
        return {"run_id": run_id, "pumps": [], "assignments": [], "unassigned": []}

    candidates = _candidates(hotspots, streets, street_points, step_min)
    emulator = _emulator(city_root, run_id, model, rain_mm_h) if candidates else None

    assignments: list[dict[str, Any]] = []
    taken: set[str] = set()
    for pump in pumps:
        if not pump["assignable"]:
            continue  # withheld by the desk; it is on the plan, it is not in the plan
        best: dict[str, Any] | None = None
        for candidate in candidates:
            hotspot = candidate["hotspot"]
            key = str(hotspot.get("hotspot_id"))
            if key in taken:
                continue

            travel_min = _travel_min(pump, hotspot)
            arrive_step = int(travel_min // step_min)
            if arrive_step >= len(candidate["series"]):
                continue  # arrives after the forecast ends; it cannot help in this window

            rate_cm_per_step = _rate_cm_per_step(pump["capacity_m3_per_h"], step_min)
            delta = (
                emulator.drawdown(key, hotspot, rate_cm_per_step) if emulator is not None else None
            )
            if delta is not None:
                lowered = _after_delta(candidate["series"], delta, arrive_step)
                benefit_model = "emulator"
            else:
                lowered = _drawn_down(candidate["series"], rate_cm_per_step, arrive_step)
                benefit_model = "reduced_model"
            after = _minutes_above(lowered, PUMP_THRESHOLD_CM, step_min)
            saved = candidate["minutes_before"] - after
            # Weighted by exposure: two junctions saving the same minutes are not equal if one
            # of them is beside a hospital.
            weight = float(hotspot.get("exposure", {}).get("weight") or 0.5)
            score = saved * (0.5 + weight)
            if saved > 0 and (best is None or score > best["score"]):
                best = {
                    "score": score,
                    "hotspot_id": key,
                    "hotspot_name": hotspot.get("name"),
                    "lon": hotspot.get("lon"),
                    "lat": hotspot.get("lat"),
                    "pump_id": pump["pump_id"],
                    "capacity_m3_per_h": pump["capacity_m3_per_h"],
                    "depot": pump["depot"],
                    "eta_min": round(travel_min),
                    "minutes_before": candidate["minutes_before"],
                    "minutes_after": after,
                    "minutes_saved": saved,
                    "benefit_model": benefit_model,
                }

        if best is not None:
            taken.add(best["hotspot_id"])
            assignments.append(best)

    if emulator is None:
        benefit_model = "reduced_model"
    elif any(a["benefit_model"] != "emulator" for a in assignments):
        # Some candidate had no road segment this emulator knows, so both models are in the plan
        # and the label has to say so rather than claim the better one for all of it.
        benefit_model = "mixed"
    else:
        benefit_model = "emulator"

    plan = {
        "run_id": run_id,
        "threshold_cm": PUMP_THRESHOLD_CM,
        "benefit_model": benefit_model,
        "benefit_label": BENEFIT_LABELS[benefit_model],
        "inventory": "synthetic",
        "travel_speed_kmh": TRAVEL_SPEED_KMH,
        "n_pumps": len(pumps),
        "n_assignable": sum(1 for p in pumps if p["assignable"]),
        "withheld": [
            {"pump_id": p["pump_id"], "status": p["status"]} for p in pumps if not p["assignable"]
        ],
        "pumps": pumps,
        "assignments": assignments,
        "unassigned": [
            {
                "hotspot_id": c["hotspot"].get("hotspot_id"),
                "name": c["hotspot"].get("name"),
                "minutes_above": c["minutes_before"],
            }
            for c in candidates
            if str(c["hotspot"].get("hotspot_id")) not in taken
        ],
        "total_minutes_saved": sum(a["minutes_saved"] for a in assignments),
    }
    if emulator is not None:
        plan["emulator"] = emulator.provenance
    log.info(
        "products.pump_plan",
        run_id=run_id,
        pumps=len(pumps),
        assignable=plan["n_assignable"],
        assigned=len(assignments),
        candidates=len(candidates),
        benefit_model=benefit_model,
        minutes_saved=plan["total_minutes_saved"],
    )
    return plan


MAX_PLACEMENTS = 64
"""A board holds at most one card per pump; this bounds a request that claims otherwise."""


def price_placements(
    hotspots: list[dict[str, Any]],
    city_root: Path,
    run_id: str,
    placements: Sequence[tuple[str, str]],
    step_min: int = 5,
    streets: dict[str, list[float]] | None = None,
    street_points: dict[str, tuple[float, float]] | None = None,
    *,
    model: FlashModel | None = None,
    rain_mm_h: Sequence[float] | None = None,
    pump_status: Mapping[str, str] | None = None,
    pump_depots: Mapping[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Price a plan the operator made by hand: the benefit of *these* pumps at *these* places.

    SPEC.md 7.6 AC2: a drag re-prices through the emulator rather than leaving the optimiser's
    figure beside a plan the optimiser did not make. ``placements`` is ``(pump_id, target_id)``,
    the target being a candidate id exactly as the plan names it (``MUM-HS-..`` or
    ``street:<name>``). Each is priced with the same model and the same arithmetic the greedy
    uses, so an unmoved plan prices to the optimiser's own numbers.

    **Two pumps on one place** are priced in arrival order, each by its *marginal* drawdown: the
    emulator run at the combined rate minus the run at the rate already there, applied from when
    that pump arrives. The emulator saturates, so the second lorry at a junction the first has
    drained buys less - which is the point of pricing it. On the bathtub fallback rates add.

    A pump on a place that does not cross the threshold saves 0 minutes and says why; a pump the
    desk has withheld, or one not in the fleet, is refused per placement rather than priced.
    """
    if len(placements) > MAX_PLACEMENTS:
        msg = f"{len(placements)} placements; a board holds at most {MAX_PLACEMENTS}"
        raise ValueError(msg)
    pumps = _fleet(city_root, pump_status, pump_depots)
    if pumps is None:
        return {
            "run_id": run_id,
            "placements": [],
            "targets": [],
            "total_minutes_saved": 0,
            "benefit_model": "reduced_model",
            "benefit_label": BENEFIT_LABELS["reduced_model"],
            "inventory": "synthetic",
            "refused": [
                {"pump_id": p, "target_id": t, "reason": "This city has no pump inventory."}
                for p, t in placements
            ],
        }
    by_pump = {p["pump_id"]: p for p in pumps}
    candidates = {
        str(c["hotspot"].get("hotspot_id")): c
        for c in _candidates(hotspots, streets, street_points, step_min)
    }
    emulator = _emulator(city_root, run_id, model, rain_mm_h) if candidates else None

    refused: list[dict[str, Any]] = []
    grouped: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    seen: set[str] = set()
    for pump_id, target_id in placements:
        pump = by_pump.get(pump_id)
        reason = None
        if pump is None:
            reason = f"{pump_id} is not in this city's fleet."
        elif not pump["assignable"]:
            reason = f"{pump_id} is marked {pump['status']} by the desk and cannot be sent."
        elif pump_id in seen:
            reason = f"{pump_id} is placed twice; a lorry is in one place."
        if reason is not None:
            refused.append({"pump_id": pump_id, "target_id": target_id, "reason": reason})
            continue
        seen.add(pump_id)
        candidate = candidates.get(target_id)
        travel = _travel_min(pump, candidate["hotspot"]) if candidate else 0.0
        grouped.setdefault(target_id, []).append((travel, pump))

    priced: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    models_used: set[str] = set()
    for target_id in sorted(grouped):
        candidate = candidates.get(target_id)
        arrivals = sorted(grouped[target_id], key=lambda item: (item[0], item[1]["pump_id"]))
        if candidate is None:
            for _travel, pump in arrivals:
                priced.append(
                    {
                        "pump_id": pump["pump_id"],
                        "hotspot_id": target_id,
                        "hotspot_name": None,
                        "depot": pump["depot"],
                        "capacity_m3_per_h": pump["capacity_m3_per_h"],
                        "eta_min": None,
                        "minutes_saved": 0,
                        "benefit_model": None,
                        "note": (
                            "This place does not cross 45 cm in this cycle's forecast, so a pump "
                            "there avoids nothing the forecast can count."
                        ),
                    }
                )
            targets.append(
                {
                    "hotspot_id": target_id,
                    "hotspot_name": None,
                    "minutes_before": 0,
                    "minutes_after": 0,
                    "minutes_saved": 0,
                }
            )
            continue

        hotspot = candidate["hotspot"]
        series = list(candidate["series"])
        before = candidate["minutes_before"]
        current = before
        rate_so_far = 0.0
        delta_so_far: tuple[float, ...] | None = None
        for travel, pump in arrivals:
            arrive_step = int(travel // step_min)
            rate = _rate_cm_per_step(pump["capacity_m3_per_h"], step_min)
            model_name = "reduced_model"
            if arrive_step >= len(series):
                saved = 0
                note = "Arrives after the three-hour forecast ends; it cannot help in this window."
            else:
                combined = (
                    emulator.drawdown(target_id, hotspot, rate_so_far + rate)
                    if emulator is not None
                    else None
                )
                if combined is not None:
                    already = delta_so_far or tuple(0.0 for _ in combined)
                    marginal = tuple(
                        max(c - a, 0.0) for c, a in zip(combined, already, strict=True)
                    )
                    series = _after_delta(series, marginal, arrive_step)
                    delta_so_far = combined
                    model_name = "emulator"
                else:
                    series = _drawn_down(series, rate, arrive_step)
                after = _minutes_above(series, PUMP_THRESHOLD_CM, step_min)
                saved = current - after
                current = after
                note = None
            rate_so_far += rate
            models_used.add(model_name)
            priced.append(
                {
                    "pump_id": pump["pump_id"],
                    "hotspot_id": target_id,
                    "hotspot_name": hotspot.get("name"),
                    "depot": pump["depot"],
                    "capacity_m3_per_h": pump["capacity_m3_per_h"],
                    "eta_min": round(travel),
                    "minutes_saved": saved,
                    "benefit_model": model_name,
                    "note": note,
                }
            )
        targets.append(
            {
                "hotspot_id": target_id,
                "hotspot_name": hotspot.get("name"),
                "minutes_before": before,
                "minutes_after": current,
                "minutes_saved": before - current,
            }
        )

    if not models_used or models_used == {"reduced_model"}:
        benefit_model = "reduced_model"
    elif models_used == {"emulator"}:
        benefit_model = "emulator"
    else:
        benefit_model = "mixed"
    result: dict[str, Any] = {
        "run_id": run_id,
        "threshold_cm": PUMP_THRESHOLD_CM,
        "placements": priced,
        "targets": targets,
        "refused": refused,
        "total_minutes_saved": sum(t["minutes_saved"] for t in targets),
        "benefit_model": benefit_model,
        "benefit_label": BENEFIT_LABELS[benefit_model],
        "inventory": "synthetic",
    }
    if emulator is not None:
        result["emulator"] = emulator.provenance
    return result


def write_pump_plan(run_dir: Path, plan: dict[str, Any]) -> None:
    """Write ``pump_plan.json`` into a run directory (SPEC.md 10.3)."""
    (run_dir / "pump_plan.json").write_text(
        json.dumps(plan, separators=(",", ":")), encoding="utf-8"
    )

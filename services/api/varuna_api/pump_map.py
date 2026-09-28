"""The dispatch map behind Jalayantra: where each pump goes, by which road, and what it buys.

``GET /v1/pumps`` returns the plan the cycle wrote - who goes where, the ETA and the minutes above
45 cm before and after. That is enough for a table and not enough for the screen a judge has to
read in ten seconds, which needs three more things per assignment:

1. **The depth series at the place, with and without its pump.** The gauge on the map is that
   series, and the arrival timeline is its window above 45 cm against the pump's arrival. They are
   recomputed here with the *same* functions the optimiser used (``varuna_products.pumps``), for
   the plan's own placements, and every leg says whether the recount reproduces the minutes the
   plan wrote (``agrees``). A series that disagrees is served with the flag down, never adjusted
   to fit (rule 6).
2. **The depot and the place on the city**, both of which the plan already carries.
3. **The road the lorry would take.** The plan's ETA is a straight line at 18 km/h
   (``TRAVEL_SPEED_KMH``); this asks the router for a truck (45 cm) leaving at the cycle time and
   prices the pump again at the routed arrival, so a lorry that the roads make late is shown late
   and its smaller benefit is shown beside the plan's. The plan itself is not changed.

Nothing is written. The run directory is what the cycle produced (rule 8).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

log = structlog.get_logger("varuna.api.pump_map")

__all__ = ["ROUTE_PROFILE", "cached_dispatch_map", "clear_cache", "dispatch_map", "pump_cycles"]

_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_CACHE_SIZE = 16


def clear_cache() -> None:
    """Forget every answer; for tests, which swap the data directory under one process."""
    _CACHE.clear()


def _overlay_key(city: str) -> tuple[Any, ...]:
    """What the desk has changed that a route or a plan would read: closures and pump states."""
    try:
        from varuna_route import ops_overlay as ops

        overlay = ops.active(city)
    except Exception:  # an unreadable log is the router's to report, not the cache's
        return ()
    return (
        tuple(sorted(overlay.closures)),
        tuple(sorted((pid, p.status) for pid, p in overlay.pumps.items())),
    )


def cached_dispatch_map(run_id: str | None, city: str, *, routes: bool = True) -> dict[str, Any]:
    """:func:`dispatch_map`, remembered per run, city and desk state.

    The first answer for a run costs the city's segment table (about 7 s cold on the demo laptop)
    and twelve truck routes (3-5 s); the answer depends on nothing else, so it is computed once.
    A closure or a pump status set on the desk changes the key, because either can move a route.
    ``cached`` on the response says which kind of answer it is.
    """
    from varuna_api.routers.ops import _run_path

    path = _run_path(run_id, city, "pump_plan.json")
    record = path / "pump_plan.json"
    key = (path.name, city, routes, record.stat().st_mtime_ns, _overlay_key(city))
    hit = _CACHE.get(key)
    if hit is not None:
        return {**hit, "cached": True}
    body = dispatch_map(path.name, city, routes=routes)
    if len(_CACHE) >= _CACHE_SIZE:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = body
    return {**body, "cached": False}


ROUTE_PROFILE = "truck"
"""A pump lorry is a truck: stopped at 45 cm, the depth the benefit is counted against."""

UNASSIGNED_ON_MAP = 20
"""How many untreated places the map marks; the rest are counted, not drawn."""


def _window(series: Sequence[float], threshold: float, step_min: int) -> dict[str, int] | None:
    """First and last forecast minute above the threshold; ``None`` when it never crosses.

    Step ``i`` is valid at ``cycle + (i + 1) * step`` (``segments_wet.json`` ``valid_ts``), so the
    minutes are counted from the cycle time. The window may have gaps; the series has them.
    """
    above = [i for i, v in enumerate(series) if v > threshold]
    if not above:
        return None
    return {"from_min": (above[0] + 1) * step_min, "to_min": (above[-1] + 1) * step_min}


def _peak(series: Sequence[float], step_min: int) -> dict[str, float | int] | None:
    if not series:
        return None
    i = max(range(len(series)), key=lambda k: series[k])
    return {"depth_cm": round(float(series[i]), 1), "at_min": (i + 1) * step_min}


def _rounded(series: Sequence[float]) -> list[float]:
    return [round(float(v), 1) for v in series]


def _route(
    origin: tuple[float, float],
    destination: tuple[float, float],
    depart_at: datetime | None,
    run_id: str,
    city: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """The truck's road route from depot to place, or why there is none."""
    try:
        from varuna_route.router import as_dict, plan

        result = as_dict(
            plan(
                origin,
                destination,
                depart_at=depart_at,
                vehicle=ROUTE_PROFILE,
                city=city,
                run_id=run_id,
                spread=False,
                explain=False,
            )
        )
    except Exception as error:  # the map still draws without a road; it says why
        return None, f"No road route: {error}"
    chosen = result.get("varuna")
    if chosen is None:
        return None, "The router found no road a truck can use at the cycle time."
    return chosen, None


def dispatch_map(run_id: str | None, city: str, *, routes: bool = True) -> dict[str, Any]:
    """Every assignment of the run's pump plan, with its depot, place, road and depth series."""
    from varuna_products.pumps import (
        PUMP_THRESHOLD_CM,
        TRAVEL_SPEED_KMH,
        _after_delta,
        _candidates,
        _drawn_down,
        _emulator,
        _haversine_km,
        _minutes_above,
        _rate_cm_per_step,
    )
    from varuna_schemas.constants import STEP_MIN

    from varuna_api.routers.ops import _plan_inputs
    from varuna_api.state import api_error

    started = time.perf_counter()
    inputs = _plan_inputs(run_id, city)
    path = inputs["path"]
    record = path / "pump_plan.json"
    if not record.is_file():
        raise api_error(
            404,
            "no_pump_plan",
            f"Run {path.name} has no pump plan. Bake it with `make bake`.",
            run_id=path.name,
        )
    plan = json.loads(record.read_text(encoding="utf-8"))
    meta = json.loads((path / "run.json").read_text(encoding="utf-8"))
    cycle_ts = meta.get("cycle_ts")
    depart_at = datetime.fromisoformat(cycle_ts) if cycle_ts else None
    step = STEP_MIN
    threshold = float(plan.get("threshold_cm") or PUMP_THRESHOLD_CM)
    speed = float(plan.get("travel_speed_kmh") or TRAVEL_SPEED_KMH)

    candidates = {
        str(c["hotspot"].get("hotspot_id")): c
        for c in _candidates(inputs["hotspots"], inputs["streets"], inputs["points"], step)
    }
    emulator = (
        _emulator(inputs["root"], path.name, None, inputs["rain"]) if inputs["rain"] else None
    )
    fleet = {str(p.get("pump_id")): p for p in plan.get("pumps", [])}
    notes: list[str] = []

    legs: list[dict[str, Any]] = []
    route_ms = 0.0
    for a in plan.get("assignments", []):
        pump_id = str(a.get("pump_id"))
        target_id = str(a.get("hotspot_id"))
        pump = fleet.get(pump_id, {})
        candidate = candidates.get(target_id)
        depot = {
            "name": a.get("depot") or pump.get("depot"),
            "lon": pump.get("lon"),
            "lat": pump.get("lat"),
        }
        target = {
            "id": target_id,
            "name": a.get("hotspot_name"),
            "kind": "street" if target_id.startswith("street:") else "register",
            "lon": a.get("lon"),
            "lat": a.get("lat"),
        }
        leg: dict[str, Any] = {
            "pump_id": pump_id,
            "capacity_m3_per_h": a.get("capacity_m3_per_h"),
            "depot": depot,
            "target": target,
            "eta_min": a.get("eta_min"),
            "minutes_before": a.get("minutes_before"),
            "minutes_after": a.get("minutes_after"),
            "minutes_saved": a.get("minutes_saved"),
            "benefit_model": a.get("benefit_model", plan.get("benefit_model")),
            "depth_before_cm": None,
            "depth_after_cm": None,
            "window_before": None,
            "window_after": None,
            "peak_before": None,
            "peak_after": None,
            "effective_from_min": None,
            "agrees": False,
            "route": None,
            "route_note": None,
        }

        complete = None not in (depot["lon"], depot["lat"], target["lon"], target["lat"])
        if candidate is None or not complete:
            leg["series_note"] = (
                "This place no longer crosses 45 cm in the run's own forecast, so there is no "
                "series to draw."
                if complete
                else "The plan carries no coordinate for this depot or place."
            )
            legs.append(leg)
            continue

        series: list[float] = list(candidate["series"])
        travel_min = (
            _haversine_km(depot["lon"], depot["lat"], target["lon"], target["lat"]) / speed * 60.0
        )
        rate = _rate_cm_per_step(float(a.get("capacity_m3_per_h") or 0.0), step)
        wanted = leg["benefit_model"]
        delta = (
            emulator.drawdown(target_id, candidate["hotspot"], rate)
            if emulator is not None and wanted == "emulator"
            else None
        )

        def lowered_at(arrive_step: int, delta=delta, series=series, rate=rate) -> list[float]:
            if delta is not None:
                return _after_delta(series, delta, arrive_step)
            return _drawn_down(series, rate, arrive_step)

        arrive_step = int(travel_min // step)
        after = lowered_at(arrive_step)
        before_min = _minutes_above(series, threshold, step)
        after_min = _minutes_above(after, threshold, step)
        used = "emulator" if delta is not None else "reduced_model"
        leg.update(
            {
                "depth_before_cm": _rounded(series),
                "depth_after_cm": _rounded(after),
                "window_before": _window(series, threshold, step),
                "window_after": _window(after, threshold, step),
                "peak_before": _peak(series, step),
                "peak_after": _peak(after, step),
                "effective_from_min": (arrive_step + 1) * step,
                "series_model": used,
                "agrees": (
                    before_min == a.get("minutes_before")
                    and after_min == a.get("minutes_after")
                    and used == wanted
                ),
            }
        )
        if not leg["agrees"]:
            notes.append(
                f"{pump_id} at {target['name']}: recounted {before_min} to {after_min} minutes "
                f"above {threshold:g} cm against the plan's {a.get('minutes_before')} to "
                f"{a.get('minutes_after')} ({used} now, {wanted} in the plan). The series is "
                "served as recomputed."
            )

        if routes:
            t0 = time.perf_counter()
            chosen, why = _route(
                (float(depot["lon"]), float(depot["lat"])),
                (float(target["lon"]), float(target["lat"])),
                depart_at,
                path.name,
                city,
            )
            route_ms += (time.perf_counter() - t0) * 1000.0
            if chosen is not None:
                minutes = float(chosen["minutes"])
                routed_after = lowered_at(int(minutes // step))
                routed_after_min = _minutes_above(routed_after, threshold, step)
                leg["route"] = {
                    "profile": ROUTE_PROFILE,
                    "minutes": round(minutes, 1),
                    "distance_m": chosen.get("distance_m"),
                    "max_depth_cm": chosen.get("max_depth_cm"),
                    "path": chosen.get("path", []),
                    "streets": chosen.get("streets", []),
                    "minutes_after": routed_after_min,
                    "minutes_saved": before_min - routed_after_min,
                }
            else:
                leg["route_note"] = why
        legs.append(leg)

    assigned = [leg for leg in legs if leg["minutes_saved"] is not None]
    before_total = sum(int(leg["minutes_before"] or 0) for leg in assigned)
    after_total = sum(int(leg["minutes_after"] or 0) for leg in assigned)
    helped = max(assigned, key=lambda leg: int(leg["minutes_saved"] or 0), default=None)
    routed = [leg for leg in legs if leg["route"] is not None]
    summary: dict[str, Any] = {
        "pumps_dispatched": len(assigned),
        "pumps_in_fleet": int(plan.get("n_pumps") or len(fleet)),
        "minutes_before": before_total,
        "minutes_after": after_total,
        "minutes_saved": int(plan.get("total_minutes_saved") or before_total - after_total),
        "helped_most": (
            {
                "pump_id": helped["pump_id"],
                "name": helped["target"]["name"],
                "minutes_before": helped["minutes_before"],
                "minutes_after": helped["minutes_after"],
                "minutes_saved": helped["minutes_saved"],
            }
            if helped is not None
            else None
        ),
        "routed": len(routed),
        "routed_minutes_saved": (
            sum(int(leg["route"]["minutes_saved"]) for leg in routed) if routed else None
        ),
        "late_on_road": sum(
            1 for leg in routed if leg["route"]["minutes"] > float(leg["eta_min"] or 0) + 0.5
        ),
    }
    if routes and routed and len(routed) < len(legs):
        notes.append(f"{len(legs) - len(routed)} of {len(legs)} lorries have no road route.")

    unassigned: list[dict[str, Any]] = []
    for u in plan.get("unassigned", [])[:UNASSIGNED_ON_MAP]:
        c = candidates.get(str(u.get("hotspot_id")))
        hotspot = c["hotspot"] if c else {}
        unassigned.append(
            {
                "id": u.get("hotspot_id"),
                "name": u.get("name"),
                "minutes_above": u.get("minutes_above"),
                "lon": hotspot.get("lon"),
                "lat": hotspot.get("lat"),
            }
        )

    depots: dict[str, dict[str, Any]] = {}
    for p in plan.get("pumps", []):
        name = str(p.get("depot"))
        entry = depots.setdefault(
            name, {"name": name, "lon": p.get("lon"), "lat": p.get("lat"), "pump_ids": []}
        )
        entry["pump_ids"].append(p.get("pump_id"))

    valid_from = (
        (depart_at + timedelta(minutes=step)).isoformat() if depart_at is not None else None
    )
    body = {
        "run_id": path.name,
        "city": city,
        "cycle_ts": cycle_ts,
        "valid_from": valid_from,
        "step_min": step,
        "n_steps": max((len(leg["depth_before_cm"] or []) for leg in legs), default=0),
        "threshold_cm": threshold,
        "travel_speed_kmh": speed,
        "benefit_model": plan.get("benefit_model"),
        "benefit_label": plan.get("benefit_label"),
        "inventory": plan.get("inventory", "synthetic"),
        "emulator": plan.get("emulator"),
        "route_profile": ROUTE_PROFILE if routes else None,
        "summary": summary,
        "legs": legs,
        "depots": list(depots.values()),
        "unassigned": unassigned,
        "n_unassigned": len(plan.get("unassigned", [])),
        "withheld": plan.get("withheld", []),
        "notes": [*notes, *inputs["notes"][1:]],
        "ms": round((time.perf_counter() - started) * 1000.0),
        "route_ms": round(route_ms),
    }
    log.info(
        "api.pumps_dispatch_map",
        run_id=path.name,
        legs=len(legs),
        agree=sum(1 for leg in legs if leg["agrees"]),
        routed=len(routed),
        ms=body["ms"],
    )
    return body


def pump_cycles(city: str) -> dict[str, Any]:
    """Every run of ``city`` with a pump plan, and what its plan sends where.

    Jalayantra opens on the cycle the console is showing, and many cycles send no pump at all -
    06:00 and 07:10 on 2 July among them, because nothing crosses 45 cm there or the water has
    gone. A screen that only said "no pumps" would leave the operator guessing where the plan is,
    so this names each cycle that has one, oldest first, with the counts its own ``pump_plan.json``
    carries. Read-only: nothing is recomputed, only counted.
    """
    from varuna_schemas.paths import runs_dir

    from varuna_api.runs_util import resolve_city, run_prefix

    name = resolve_city(city)
    prefix = run_prefix(name)
    root = runs_dir()
    cycles: list[dict[str, Any]] = []
    if prefix and root.is_dir():
        for path in sorted(root.iterdir()):
            if not path.is_dir() or not path.name.startswith(prefix):
                continue
            record = path / "pump_plan.json"
            if not record.is_file():
                continue
            try:
                plan = json.loads(record.read_text(encoding="utf-8"))
                meta_path = path / "run.json"
                meta = (
                    json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
                )
            except (OSError, ValueError):
                continue  # a half-written run is the registry's to report, not this list's
            assignments = plan.get("assignments", []) or []
            cycles.append(
                {
                    "run_id": path.name,
                    "cycle_ts": meta.get("cycle_ts"),
                    "n_assigned": len(assignments),
                    "n_unassigned": len(plan.get("unassigned", []) or []),
                    "minutes_saved": int(plan.get("total_minutes_saved") or 0),
                }
            )
    busiest = max(cycles, key=lambda c: (c["minutes_saved"], c["n_assigned"]), default=None)
    return {
        "city": name,
        "cycles": cycles,
        "busiest_run_id": busiest["run_id"] if busiest and busiest["n_assigned"] > 0 else None,
    }

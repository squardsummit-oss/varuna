"""Why a route went the way it did, as structured data (task D-08, TECH_SPEC 3.2).

**No English sentences live in this file.** A reason is a record - a kind and its numbers - and
the frontend words it (`apps/command/lib/explain.ts`, UI_SPEC 4). Two reasons for keeping the
prose out of Python: the same reason has to be said differently on a phone, on the operator's
console and in Hindi or Marathi later (`P9.9`), and a sentence assembled here would be a number
the screen could not check.

**A reason without its number is not emitted.** UI_SPEC 4 requires that a reason whose number is
missing is dropped rather than softened into "this road may flood". The drop happens here too, so
the screen is never handed a reason it will have to throw away: every builder below returns
``None`` when the number it exists to carry is absent.

The four kinds, matching UI_SPEC 4:

``avoided``
    A street the naive way uses and this route refused: its depth, the profile's threshold, the
    clock time the vehicle would have reached it, and the probability the run gives it.
``design``
    What the drain under that street was sized for, against what this cycle's rain peaks at.
    The design intensity is per drain edge, from the city build; the peak is the run's
    **AOI mean**, which the response's notes say out loud.
``timing``
    When the road the route does take stops being dry, and how deep it gets.
``closure``
    A street an authority closed, with the officer's own words and the time they entered it.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import structlog
from varuna_schemas.paths import city_dir

from varuna_route.forecast import DRY_CM

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

    from varuna_route.forecast import SegmentDepths
    from varuna_route.ops_overlay import OpsOverlay
    from varuna_route.profiles import Profile
    from varuna_route.router import Avoided, Route

log = structlog.get_logger("varuna.route.reasons")

__all__ = [
    "MAX_AVOIDED_REASONS",
    "avoided_reason",
    "build_reasons",
    "closure_reason",
    "design_intensity_by_segment",
    "design_reason",
    "timing_reason",
]

MAX_AVOIDED_REASONS = 3
"""Avoided streets worth naming. The screen shows at most four reasons in total (UI_SPEC 4), and
a list of eleven flooded streets is a wall, not an explanation."""


@lru_cache(maxsize=2)
def design_intensity_by_segment(city: str) -> dict[str, float]:
    """``segment_id -> design rainfall intensity in mm/h`` for the drain under that street.

    Built from the city's own drain graph: each inlet node carries the ``segment_id`` it sits on
    and each edge carries the ``design_intensity_mm_h`` the rational method sized it with
    (SPEC.md 10.1 step 7). Where a street has several inlets the **smallest** intensity wins,
    because a corridor drains no better than its weakest pipe.

    On Mumbai this covers 16,983 of 21,296 segments at 25 or 50 mm/h - the legacy and
    BRIMSTOWAD-upgraded figures in `services/city/configs/mumbai.yaml`. A segment it does not
    cover simply gets no ``design`` reason; nothing is guessed.

    Returns an empty mapping when the city is not built, so a reason is skipped rather than a
    route failing.
    """
    import pyarrow.parquet as pq

    nodes_path = city_dir(city) / "drain_nodes.parquet"
    edges_path = city_dir(city) / "drain_edges.parquet"
    if not nodes_path.is_file() or not edges_path.is_file():
        log.info("route.no_drain_graph", city=city)
        return {}

    nodes = pq.read_table(nodes_path, columns=["node_id", "segment_id"]).to_pydict()
    edges = pq.read_table(edges_path, columns=["from_node", "design_intensity_mm_h"]).to_pydict()

    by_node: dict[Any, float] = {}
    for node, intensity in zip(edges["from_node"], edges["design_intensity_mm_h"], strict=True):
        if intensity is None or not math.isfinite(float(intensity)):
            continue
        by_node.setdefault(node, float(intensity))

    out: dict[str, float] = {}
    for node, segment_id in zip(nodes["node_id"], nodes["segment_id"], strict=True):
        if not segment_id:
            continue
        intensity = by_node.get(node)
        if intensity is None:
            continue
        key = str(segment_id)
        current = out.get(key)
        if current is None or intensity < current:
            out[key] = intensity
    log.info("route.design_intensity_loaded", city=city, segments=len(out))
    return out


def avoided_reason(entry: Avoided, threshold_cm: float) -> dict[str, Any]:
    """A street the route refused, with the water on it when the vehicle would have arrived."""
    return {
        "kind": "avoided",
        "segment_id": entry.segment_id,
        "name": entry.name,
        "depth_cm": round(float(entry.depth_cm), 1),
        "threshold_cm": float(threshold_cm),
        "at": entry.at.isoformat(),
        "probability": round(float(entry.probability), 3),
    }


def design_reason(
    segment_id: str,
    name: str,
    city: str,
    depths: SegmentDepths,
) -> dict[str, Any] | None:
    """What the drain under a street was sized for, against this cycle's peak rain.

    ``None`` when either number is missing - the street has no inlet in the inferred graph, or
    the run kept no AOI hyetograph - because half of this comparison says nothing.
    """
    intensity = design_intensity_by_segment(city).get(segment_id)
    if intensity is None:
        return None
    if not depths.rain_aoi_mm_h:
        return None
    peak = max(depths.rain_aoi_mm_h)
    if not math.isfinite(peak) or peak <= 0.0:
        return None
    return {
        "kind": "design",
        "segment_id": segment_id,
        "name": name,
        "design_intensity_mm_h": round(float(intensity), 1),
        "forecast_peak_mm_h": round(float(peak), 1),
    }


def timing_reason(route: Route, depths: SegmentDepths, vehicle: Profile) -> dict[str, Any] | None:
    """When the road this route does take stops being dry, and how deep it gets.

    Taken on the wettest segment of the route, at the times the run gives it - not at the time
    the vehicle passes, because the question the sentence answers ("how long is this good for")
    is about the street and not about this one trip.

    ``None`` when the route never gets wet, or is wet from the first step, since there is then no
    "until" to name.
    """
    worst_id = ""
    worst_name = ""
    worst_peak = 0.0
    for leg in route.legs:
        peak = depths.peak(leg.segment_id)
        if peak > worst_peak:
            worst_peak, worst_id, worst_name = peak, leg.segment_id, leg.name
    if worst_peak <= DRY_CM:
        return None

    series = depths.depth_cm.get(worst_id) or []
    dry_until: str | None = None
    peak_step = 0
    for step, value in enumerate(series):
        if value <= DRY_CM:
            dry_until = depths.time_of(step).isoformat()
        if value >= worst_peak:
            peak_step = step
            break
    if dry_until is None:
        return None
    return {
        "kind": "timing",
        "segment_id": worst_id,
        "name": worst_name or "Unnamed road",
        "dry_until": dry_until,
        "dry_below_cm": DRY_CM,
        "depth_cm": round(float(worst_peak), 1),
        "threshold_cm": float(vehicle.depth_cm),
        "at": depths.time_of(peak_step).isoformat(),
    }


def closure_reason(segment_id: str, name: str, overlay: OpsOverlay) -> dict[str, Any] | None:
    """A street an authority closed. ``None`` when it is open, or closed without a stated reason.

    A closure with no reason text is dropped rather than rendered as "closed": the screen would
    have nothing to say after the comma, and UI_SPEC 4 forbids softening it.
    """
    closure = overlay.closures.get(segment_id)
    if closure is None or not closure.reason:
        return None
    return {
        "kind": "closure",
        "segment_id": segment_id,
        "name": name,
        "reason": closure.reason,
        "user": closure.user,
        "at": closure.ts.isoformat(),
        "until": closure.until.isoformat() if closure.until else None,
    }


def build_reasons(
    *,
    avoided: Sequence[Avoided],
    route: Route | None,
    depths: SegmentDepths,
    vehicle: Profile,
    city: str,
    overlay: OpsOverlay | None = None,
    closed_on_naive: Sequence[tuple[str, str]] = (),
) -> list[dict[str, Any]]:
    """Every reason this route can support, in the order UI_SPEC 4 reads them.

    Args:
        avoided: the streets the naive way uses and this route refused, deepest first.
        route: the route taken, for the timing reason; ``None`` when nothing was routable.
        depths: the run being routed against.
        vehicle: the profile whose threshold the reasons quote.
        city: which city's drain graph the design intensity comes from.
        overlay: the authority overlay, for closure reasons.
        closed_on_naive: ``(segment_id, name)`` of closed streets the naive way uses.
    """
    out: list[dict[str, Any]] = []

    for entry in list(avoided)[:MAX_AVOIDED_REASONS]:
        out.append(avoided_reason(entry, vehicle.depth_cm))

    if avoided:
        worst = avoided[0]
        design = design_reason(worst.segment_id, worst.name, city, depths)
        if design is not None:
            out.append(design)

    if route is not None:
        timing = timing_reason(route, depths, vehicle)
        if timing is not None:
            out.append(timing)

    if overlay is not None:
        for segment_id, name in closed_on_naive:
            closure = closure_reason(segment_id, name, overlay)
            if closure is not None:
                out.append(closure)

    return out

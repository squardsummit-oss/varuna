"""Serving one cycle's rain: where it comes from, where it is sampled, what it looks like.

The router in :mod:`varuna_api.routers.nowcast` is thin; this is the part with decisions in it.

**Where the rain comes from.** A baked run under ``data/runs`` is the normal source, and
``?compute=true`` is the escape hatch until ``make bake`` lands in Phase 5: it runs one Sky
cycle from the replay bundle and returns it without publishing a run. Every response says
which of the two happened (``mode`` is ``baked`` or ``live``), because that is exactly what
the run stamp shows the operator (SPEC.md 7.2, readiness item R1). With nothing on disk and
no ``compute``, the answer is a 404 naming ``make bake`` - never an empty series, which on a
chart is indistinguishable from a forecast of no rain (rule 6).

**Where it is sampled.** A fan chart is drawn at a junction, and the junction's coordinate
comes from the city's own hotspot register (``city/<city>/hotspots.geojson``), which carries
the verified position and the ``source_url`` it was verified against (SPEC.md 10.1 step 9).
Nothing here holds a coordinate of its own.

**What the numbers are.** Rain rates are published to 0.001 mm/h and probabilities to four
decimals - far finer than the 0.1 mm/h that means anything and than the 5 % an ensemble of
twenty can resolve, so nothing meaningful is lost and the payload stays readable.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog
from pyproj import Transformer
from varuna_cycle.sky_cycle import (
    RainCycle,
    has_rain_products,
    read_run_rain,
    run_bundle_cycle,
)
from varuna_replay.bundle import BundleNotFoundError
from varuna_schemas.models.run import RunMeta
from varuna_schemas.models.sky import (
    RainMemberSeries,
    RainNowcast,
    RainPoint,
    RainPointSeries,
    RainPointStep,
    RainStep,
    ZRRelation,
)
from varuna_schemas.paths import city_dir

from varuna_api.replay import bundle_hint
from varuna_api.state import AppState, api_error, run_not_found

if TYPE_CHECKING:  # pragma: no cover - typing only
    from varuna_sky.types import RadarGrid

log = structlog.get_logger("varuna.api.rain")

__all__ = [
    "HOTSPOT_REGISTER",
    "RAIN_DECIMALS",
    "hotspot_register",
    "rain_nowcast",
    "rain_point_series",
    "resolve_point",
    "resolve_rain",
]

_LIVE_CYCLE = threading.Lock()
"""One live Sky cycle at a time in this process.

A computed cycle is a 20-member pySTEPS ensemble over the 120 x 120 radar domain, and its peak
memory is most of what a small host has. On 2026-09-19 the deployed API logged two of them
starting in the same second - `sky.merge` twice at 15:58:14.98 UTC - and never logged anything
again: the container was killed and did not come back, while the platform went on reporting the
deployment as healthy. A second request that arrives while one is running is refused at once,
with the reason and what to do instead, rather than doubling the memory and taking the API down
for every other screen."""

HOTSPOT_REGISTER = "hotspots.geojson"
"""The chronic waterlogging register the city pipeline writes (SPEC.md 10.1 step 9)."""

RAIN_DECIMALS = 3
"""Decimal places a published rain rate keeps (0.001 mm/h)."""

PROBABILITY_DECIMALS = 4
"""Decimal places a published probability keeps; 20 members resolve steps of 0.05."""

WGS84 = "EPSG:4326"

RUN_SCAN_LIMIT = 500
"""Runs looked at when picking the newest one that carries rain products."""


def _mm(value: Any) -> float:
    return round(float(value), RAIN_DECIMALS)


def _p(value: Any) -> float:
    return round(float(value), PROBABILITY_DECIMALS)


# ============================================================================ where it comes from
def _no_rain_runs(state: AppState) -> Exception:
    """The empty-state error: what the operator should do, in the copy of SPEC.md 6.8."""
    bundle = state.settings.varuna_bundle
    return api_error(
        404,
        "no_rain_runs",
        f"No run under data/runs carries rain products yet. Run "
        f"`make bake BUNDLE={bundle}`, press Play on the replay, or add compute=true to "
        f"compute this cycle from {bundle} now.",
    )


def _from_run(state: AppState, meta: RunMeta) -> RainCycle:
    try:
        return read_run_rain(state.registry.runs_dir / meta.run_id, meta)
    except FileNotFoundError as exc:
        raise api_error(404, "rain_not_in_run", str(exc), run_id=meta.run_id) from exc


def _latest_with_rain(state: AppState) -> RunMeta | None:
    """Newest run of the configured city whose rain products are actually on disk."""
    runs = state.registry.list(city=state.settings.varuna_city, limit=RUN_SCAN_LIMIT)
    for meta in runs:
        if has_rain_products(state.registry.runs_dir / meta.run_id):
            return meta
    return None


def _computed(state: AppState, bundle: str | None, when: datetime | None) -> RainCycle:
    """One Sky cycle computed from a bundle, with every failure named in UI copy."""
    clock = state.replay.clock
    wanted = bundle or (clock.bundle_id if clock else state.settings.varuna_bundle)
    if when is None and clock is not None and clock.bundle_id == wanted:
        when = clock.snapshot().sim_time
    if not _LIVE_CYCLE.acquire(blocking=False):
        raise api_error(
            503,
            "cycle_busy",
            "A live rain cycle is already running on this server, and running two at once is "
            "what exhausts its memory. Try again in a minute, or leave compute off to read the "
            "newest baked run.",
        )
    try:
        rain = run_bundle_cycle(wanted, when)
    except BundleNotFoundError as exc:
        raise api_error(
            404,
            "bundle_not_found",
            f"Cannot compute a cycle from {wanted}: {exc}. {bundle_hint(wanted)}",
        ) from exc
    except FileNotFoundError as exc:  # the city has not been built; the message names make city
        raise api_error(404, "city_not_built", str(exc)) from exc
    except ValueError as exc:
        raise api_error(
            422, "cycle_not_computable", f"Cannot compute a cycle from {wanted}: {exc}"
        ) from exc
    finally:
        _LIVE_CYCLE.release()
    log.info(
        "rain.computed",
        bundle=rain.bundle,
        cycle_ts=rain.cycle_ts.isoformat(),
        nowcaster=rain.nowcaster,
        total_ms=sum(rain.stage_ms.values()),
    )
    return rain


def resolve_rain(
    state: AppState,
    run_id: str | None = None,
    *,
    compute: bool = False,
    bundle: str | None = None,
    t: datetime | None = None,
) -> RainCycle:
    """The rain products this request is about: a baked run, or a cycle computed now.

    An explicit ``run_id`` always wins - asking for a named run and getting a freshly computed
    one instead would make the run stamp a lie. Otherwise ``compute`` decides, and with neither
    a run nor ``compute`` the caller gets the empty-state 404.
    """
    if run_id:
        meta = state.registry.get(run_id)
        if meta is None:
            raise run_not_found(run_id)
        return _from_run(state, meta)
    if compute:
        return _computed(state, bundle, t)
    meta = _latest_with_rain(state)
    if meta is None:
        raise _no_rain_runs(state)
    return _from_run(state, meta)


# ============================================================================ where it is sampled
def hotspot_register(city: str) -> list[dict[str, Any]]:
    """The city's chronic waterlogging register as GeoJSON features.

    Raises:
        HTTPException: 404 when the city has not been built, naming ``make city``.
    """
    try:
        path = city_dir(city) / HOTSPOT_REGISTER
    except ValueError:
        raise _city_not_built(city) from None
    if not path.is_file():
        raise _city_not_built(city)
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("features", [])
    return [feature for feature in features if isinstance(feature, dict)]


def _city_not_built(city: str) -> Exception:
    return api_error(
        404,
        "city_not_built",
        f"No hotspot register for {city}. Run `make city CITY={city}` to build "
        f"city/{city}/{HOTSPOT_REGISTER}, which is where verified junction coordinates live.",
    )


def _hotspot_name(feature: dict[str, Any]) -> str:
    return str(feature.get("properties", {}).get("name", "")).strip()


def _public_url(value: Any) -> str | None:
    """A register's ``source_url`` when it is a public http(s) link, else nothing.

    A pin whose provenance is a cached file rather than a URL is served without one instead of
    with a link that does not resolve (SPEC.md 0.7).
    """
    url = str(value).strip() if value else ""
    return url if url.lower().startswith(("http://", "https://")) else None


def _find_hotspot(city: str, query: str) -> dict[str, Any]:
    """One register entry by id, slug or name; part of a name is enough when it is unique."""
    key = query.strip().casefold()
    features = hotspot_register(city)
    exact = [
        feature
        for feature in features
        if key
        in {
            str(feature.get("properties", {}).get(field, "")).strip().casefold()
            for field in ("hotspot_id", "slug", "name")
        }
    ]
    matches = exact or [
        feature
        for feature in features
        if key in _hotspot_name(feature).casefold()
        or key in str(feature.get("properties", {}).get("slug", "")).casefold()
    ]
    if not matches:
        raise api_error(
            404,
            "hotspot_not_found",
            f"No hotspot matching {query!r} in the {city} register. Use a register id "
            f"(MUM-HS-01), a slug, or part of a name; GET /v1/city/{city}/layers/hotspots "
            "lists them all.",
        )
    if len(matches) > 1:
        names = ", ".join(sorted(_hotspot_name(feature) for feature in matches)[:5])
        raise api_error(
            422,
            "hotspot_ambiguous",
            f"{query!r} matches {len(matches)} hotspots in the {city} register ({names}). "
            "Give a register id or a longer part of the name.",
        )
    return matches[0]


def _register_coordinate(city: str, feature: dict[str, Any]) -> tuple[float, float]:
    """The verified position of a register entry: its ``lon``/``lat``, else its geometry.

    The city pipeline writes both, and they agree. Reading the properties first keeps the
    number the register was verified with rather than one a simplification pass may have moved.
    """
    properties = feature.get("properties", {})
    candidates: list[Any] = [properties.get("lon"), properties.get("lat")]
    if candidates[0] is None or candidates[1] is None:
        candidates = list((feature.get("geometry") or {}).get("coordinates") or [None, None])[:2]
    try:
        return (float(candidates[0]), float(candidates[1]))
    except (TypeError, ValueError, IndexError) as exc:
        raise api_error(
            422,
            "hotspot_has_no_coordinate",
            f"{_hotspot_name(feature) or 'That hotspot'} carries no usable coordinate in the "
            f"{city} register. Run `make city CITY={city}` to rebuild it, or give lon= and "
            "lat= instead.",
        ) from exc


@lru_cache(maxsize=4)
def _to_metric(crs: str) -> Transformer:
    return Transformer.from_crs(WGS84, crs, always_xy=True)


@lru_cache(maxsize=4)
def _to_wgs84(crs: str) -> Transformer:
    return Transformer.from_crs(crs, WGS84, always_xy=True)


def _domain_bounds(grid: RadarGrid) -> tuple[float, float, float, float]:
    """The Sky domain in WGS84, for an error message a human can act on."""
    left, bottom, right, top = grid.bounds
    lons, lats = _to_wgs84(grid.crs).transform(
        [left, right, left, right], [bottom, bottom, top, top]
    )
    return (min(lons), min(lats), max(lons), max(lats))


def resolve_point(
    city: str,
    grid: RadarGrid,
    *,
    lon: float | None = None,
    lat: float | None = None,
    hotspot: str | None = None,
) -> RainPoint:
    """The point a fan chart is drawn at, and the Sky pixel it falls in.

    Either a ``hotspot`` (an entry in the city register, which carries the verified coordinate
    and its source) or an explicit ``lon``/``lat``. A point outside the radar domain is refused
    rather than clamped to the edge pixel, which would answer a question about one place with
    the rain over another.
    """
    if hotspot:
        feature = _find_hotspot(city, hotspot)
        properties = feature.get("properties", {})
        lon, lat = _register_coordinate(city, feature)
        name = str(properties.get("name") or hotspot)
        hotspot_id = str(properties.get("hotspot_id")) if properties.get("hotspot_id") else None
        source_url = _public_url(properties.get("source_url"))
    elif lon is None or lat is None:
        raise api_error(
            422,
            "point_missing",
            "Say where to sample the rain: hotspot=MUM-HS-01 (or part of its name), or "
            "lon= and lat= in WGS84.",
        )
    else:
        name = f"{lat:.4f}, {lon:.4f}"
        hotspot_id = None
        source_url = None

    x, y = _to_metric(grid.crs).transform(lon, lat)
    row, col = grid.rowcol(float(x), float(y))
    if not grid.contains(row, col):
        min_lon, min_lat, max_lon, max_lat = _domain_bounds(grid)
        raise api_error(
            422,
            "point_outside_domain",
            f"{name} ({lon:.4f}, {lat:.4f}) is outside the radar domain, which covers "
            f"{min_lon:.3f} to {max_lon:.3f} east and {min_lat:.3f} to {max_lat:.3f} north. "
            "Pick a point inside it, or build a city whose domain covers this one.",
        )
    return RainPoint(
        lon=lon,
        lat=lat,
        name=name,
        hotspot_id=hotspot_id,
        source_url=source_url,
        row=row,
        col=col,
        res_m=float(grid.res_m),
    )


# ============================================================================ what it looks like
def _lead_min(rain: RainCycle, valid_ts: datetime) -> int:
    return round((valid_ts - rain.cycle_ts).total_seconds() / 60.0)


def _provenance(rain: RainCycle) -> dict[str, Any]:
    """The fields every rain response shares, straight off the cycle."""
    zr = None
    if rain.zr_a is not None and rain.zr_b is not None and rain.zr_source is not None:
        zr = ZRRelation(a=rain.zr_a, b=rain.zr_b, source=rain.zr_source, n_pairs=rain.zr_pairs)
    return {
        "run_id": rain.run_id,
        "valid_ts": rain.cycle_ts,
        "mode": rain.mode,
        "bundle": rain.bundle,
        "city": rain.city,
        "n_members": rain.n_members,
        "n_steps": rain.n_steps,
        "step_min": rain.step_min,
        "nowcaster": rain.nowcaster,
        "seed": rain.seed,
        "zr": zr,
        "stage_ms": dict(rain.stage_ms),
        "notes": list(rain.notes),
    }


def rain_nowcast(rain: RainCycle) -> RainNowcast:
    """The AOI-mean hyetographs and their spread band (``GET /v1/nowcast/rain``)."""
    hyetographs = np.asarray(rain.products.aoi_hyetographs, dtype=np.float64)
    p10, p50, p90 = rain.aoi_band()
    steps = [
        RainStep(
            valid_ts=ts,
            lead_min=_lead_min(rain, ts),
            p10_mm_h=_mm(p10[k]),
            p50_mm_h=_mm(p50[k]),
            p90_mm_h=_mm(p90[k]),
        )
        for k, ts in enumerate(rain.products.times)
    ]
    members = [
        RainMemberSeries(member=index, mm_h=[_mm(value) for value in row])
        for index, row in enumerate(hyetographs)
    ]
    return RainNowcast(**_provenance(rain), steps=steps, members=members)


def rain_point_series(rain: RainCycle, point: RainPoint) -> RainPointSeries:
    """The fan chart at one Sky pixel (``GET /v1/nowcast/rain/series``)."""
    products = rain.products
    row, col = point.row, point.col
    steps = [
        RainPointStep(
            valid_ts=ts,
            lead_min=_lead_min(rain, ts),
            p10_mm_h=_mm(products.p10[k, row, col]),
            p50_mm_h=_mm(products.p50[k, row, col]),
            p90_mm_h=_mm(products.p90[k, row, col]),
            p_gt_20=_p(products.p_gt_20[k, row, col]),
            p_gt_40=_p(products.p_gt_40[k, row, col]),
        )
        for k, ts in enumerate(products.times)
    ]
    return RainPointSeries(
        **_provenance(rain),
        point=point,
        steps=steps,
        exceedance_mm_h=list(rain.exceedance_mm_h),
    )

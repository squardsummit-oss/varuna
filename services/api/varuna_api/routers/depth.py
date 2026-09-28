"""Serving a baked run's depth products to the console (SPEC.md 12, P5.7).

Three things the map needs and nothing else:

* ``GET /v1/nowcast/raster`` - the RGBA PNG for one step, straight off disk. The console
  preloads all 36 of a run on ``runs.published`` and swaps them during a scrub, so this must be
  a plain file read: no decoding, no re-ramping, no per-request work (SPEC.md 7.2 AC, "no
  network during scrub" - the network happens once, up front).
* ``GET /v1/nowcast/raster/bounds`` - where to put it, in lon/lat.
* ``GET /v1/nowcast/segments`` - the per-segment depth series the streets are coloured by.

Every response carries ``run_id`` and the run's honesty notes, because the console prints them
under the run stamp and a depth map with no provenance is exactly what SPEC.md rule 6 exists
to prevent.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Query, Response
from varuna_schemas.paths import city_dir, run_dir
from varuna_schemas.settings import get_settings

from varuna_api.runs_util import (
    bake_hint,
    city_of_run,
    latest_run_for,
    no_run_hint,
    resolve_city,
)
from varuna_api.state import api_error

log = structlog.get_logger("varuna.api.depth")

router = APIRouter(prefix="/v1", tags=["nowcast"])


def _bake_hint_for(run_id: str, city: str | None = None) -> str:
    """The command that bakes ``run_id``'s own city again, for a run missing a product.

    This used to be one constant naming ``MUM-2019-07-02`` whatever run or city was asked about,
    so a Chennai run with no hotspot ranking told its reader to bake Mumbai. The run id says
    which city it is; ``city`` and then the configured city stand in only for an id that does not
    parse.
    """
    return bake_hint(city_of_run(run_id) or city or get_settings().varuna_city)


CityQuery = Annotated[
    str | None,
    Query(
        description="City id, e.g. mumbai. Picks whose newest run answers when run_id is omitted."
    ),
]
"""Which city an endpoint means when the caller named no run.

Runs from every city share ``data/runs/`` and their ids sort chronologically, so ``CHN-`` sorts
after ``MUM-`` for the same instant: without this, a Chennai console asking for "the newest run"
and a Mumbai console asking for "the newest run" got the same answer, and one of them was wrong
(:func:`varuna_api.runs_util.latest_run_for`). Omitted, the settings' city stands, which is what
every Mumbai screen relies on today. A city the run-id scheme has no code for is refused with 404
``unknown_city`` (:func:`varuna_api.runs_util.resolve_city`), because there is no prefix to filter
its runs by and the only thing left to serve it would be another city's.
"""


NO_ATTRIBUTION_LABEL = (
    "Not computed on this run: it was baked before attribution moved onto drain1d "
    "(ADR-0071). Re-bake it to rank the pipes."
)
"""Why a hotspot's ``attribution`` is empty on a run that carries no reason of its own.

Since ADR-0071 the cycle ranks pipes on ``drain1d`` and writes a label beside every empty list,
measured at that junction ("51 pipes re-run, the best moves it 0.003 cm"). Runs baked before
that carry an empty list and nothing else; ADR-0042's reason for them - Flash-lite is
element-wise per segment - was true of the operator they were baked with, so this says what
changed and what to do rather than repeating a sentence that is no longer the whole story. It is
not imported from ``varuna_flash`` because the hotspot rail must not pull the emulator in to
answer a file read."""


def _latest_run_with_depth(city: str | None = None) -> Path | None:
    """The newest run directory for a city that actually has depth rasters in it.

    Newest by run id, which sorts chronologically because the id embeds a UTC stamp
    (SPEC.md 10.3). A run without a ``depth/`` folder is skipped rather than returned and then
    404'd one request later: a bake in progress leaves earlier complete runs perfectly usable.

    **Filtered by city**, and that is not optional once a second city exists. Onboarding Chennai
    put `CHN-` runs in the same directory, they sort after `MUM-` for the same date, and every
    endpoint that means "the current run" started answering a Mumbai console with Chennai water.
    """
    return latest_run_for(city, lambda p: (p / "depth" / "bounds.json").is_file())


def _resolve(run_id: str | None, city: str | None = None) -> Path:
    """The run directory to serve, or an error that names the command that makes one.

    ``city`` only decides which run is newest; a ``run_id`` names its own city and is served as
    asked, because a run directory already knows which city it belongs to and a second opinion
    from the query string could only disagree with it. A ``city`` VARUNA has no code for is still
    refused beside a ``run_id``: it is a request for a city that does not exist, and answering it
    would say otherwise.
    """
    if run_id:
        asked = resolve_city(city) if city else None
        path = run_dir(run_id)
        if not (path / "depth" / "bounds.json").is_file():
            raise api_error(
                404,
                "run_not_found",
                f"Run {run_id} has no depth products. {_bake_hint_for(run_id, asked)}",
                run_id=run_id,
            )
        return path
    name = resolve_city(city)
    latest = _latest_run_with_depth(name)
    if latest is None:
        raise api_error(404, "no_baked_runs", no_run_hint(name, "depth products"))
    return latest


def _meta(path: Path) -> dict[str, Any]:
    record = path / "run.json"
    return json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}


@router.get("/nowcast/raster/bounds", summary="Where a run's depth rasters sit, and what they are")
def raster_bounds(
    run_id: Annotated[str | None, Query()] = None, city: CityQuery = None
) -> dict[str, Any]:
    """The lon/lat corners for the BitmapLayer, the step count, and the run's provenance."""
    path = _resolve(run_id, city)
    meta = _meta(path)
    bounds = json.loads((path / "depth" / "bounds.json").read_text(encoding="utf-8"))
    steps = sorted(p.name for p in (path / "depth").glob("p50_*.png"))
    return {
        "run_id": meta.get("run_id", path.name),
        "cycle_ts": meta.get("cycle_ts"),
        "mode": meta.get("mode"),
        "bundle": meta.get("bundle"),
        "bounds": bounds,
        "n_steps": len(steps),
        "step_min": meta.get("step_min", 5),
        "ensemble_n": meta.get("ensemble_n", 1),
        "mass_balance_err": meta.get("mass_balance_err"),
        "stage_ms": meta.get("stage_ms", {}),
        # The time bar's spread band (SPEC.md 7.2): p10/p50/p90 of mean street depth per step,
        # absent on a run with fewer than two members or baked before the band existed.
        "aoi_depth_band": meta.get("aoi_depth_band"),
        "notes": meta.get("notes", []),
        "frames": [
            f"/v1/nowcast/raster?run_id={meta.get('run_id', path.name)}&step={i}"
            for i in range(len(steps))
        ],
    }


@router.get(
    "/nowcast/raster",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}, "description": "Depth PNG"}},
    summary="One step's depth raster as RGBA PNG",
)
def raster(
    step: Annotated[int, Query(ge=0)] = 0,
    run_id: Annotated[str | None, Query()] = None,
    city: CityQuery = None,
    stat: Annotated[Literal["p50", "p90"], Query()] = "p50",
) -> Response:
    """The PNG for one 5-minute step, cached hard because a baked run never changes.

    ``immutable`` is honest here in a way it usually is not: the run id contains the cycle time
    and the engine versions, so a given URL's bytes cannot change. That is what lets the console
    preload 36 frames and scrub without touching the network again.
    """
    path = _resolve(run_id, city)
    png = path / "depth" / f"{stat}_{step:02d}.png"
    if not png.is_file():
        available = len(list((path / "depth").glob(f"{stat}_*.png")))
        raise api_error(
            404,
            "step_not_found",
            f"Step {step} has no {stat} raster in this run; it has {available} steps (0-{available - 1}).",
            run_id=path.name,
        )
    return Response(
        content=png.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    """``minlon,minlat,maxlon,maxlat`` in WGS84, or a 400 that says which part is wrong."""
    if not bbox:
        return None
    parts = bbox.replace(" ", "").split(",")
    try:
        if len(parts) != 4:
            raise ValueError
        minlon, minlat, maxlon, maxlat = (float(p) for p in parts)
    except ValueError:
        raise api_error(
            400,
            "bad_bbox",
            f"bbox {bbox!r} is not four numbers: give minlon,minlat,maxlon,maxlat in WGS84.",
        ) from None
    if minlon > maxlon or minlat > maxlat:
        raise api_error(
            400,
            "bad_bbox",
            "bbox corners are the wrong way round: give minlon,minlat,maxlon,maxlat.",
        )
    return (minlon, minlat, maxlon, maxlat)


def _ids_within(box: tuple[float, float, float, float], city: str) -> set[str]:
    """The city's segments whose midpoint falls inside ``box``."""
    from varuna_products.depth import segment_points
    from varuna_schemas.paths import city_dir

    minlon, minlat, maxlon, maxlat = box
    return {
        sid
        for sid, (lon, lat) in segment_points(city_dir(city)).items()
        if minlon <= lon <= maxlon and minlat <= lat <= maxlat
    }


def _within_bbox(
    product: dict[str, Any], box: tuple[float, float, float, float], city: str
) -> dict[str, Any]:
    """``segments_wet.json`` cut to the segments inside ``box``; every per-segment map is cut."""
    inside = _ids_within(box, city)
    depth = {sid: v for sid, v in (product.get("depth_cm") or {}).items() if sid in inside}
    p_gt = {
        threshold: {sid: v for sid, v in series.items() if sid in inside}
        for threshold, series in (product.get("p_gt") or {}).items()
    }
    return {**product, "depth_cm": depth, "p_gt": p_gt, "n_segments_wet": len(depth)}


@router.get("/nowcast/segments", summary="Per-segment depth series for the street layer")
def segments(
    run_id: Annotated[str | None, Query()] = None,
    city: CityQuery = None,
    min_depth_cm: Annotated[float, Query(ge=0)] = 5.0,
    bbox: Annotated[
        str | None,
        Query(
            description="minlon,minlat,maxlon,maxlat (WGS84): only segments whose midpoint is inside."
        ),
    ] = None,
) -> dict[str, Any]:
    """Every segment that gets wet in this run, with its depth at each step.

    Only segments reaching ``min_depth_cm`` at some point are returned, and the default is the
    5 cm the depth ramp calls dry (SPEC.md 6.2). Mumbai has 21,296 segments and a storm cycle
    wets several thousand of them, so this is the difference between a response the console can
    hold and a 20 MB one it cannot. The dry remainder is drawn from the city layer, in the dry
    colour, and needs no per-step data at all.

    ``bbox`` is section 12's "segments in bbox": a navigation app asking about the streets on its
    screen gets those and not the whole AOI. It filters on each segment's midpoint - the same
    point the alerts and the pump board put a pin on - so a street is either in or out, never
    split. The console omits it, because it preloads the whole run for a scrub that must make no
    requests (P6.3).
    """
    path = _resolve(run_id, city)
    box = parse_bbox(bbox)

    # The fast path, and the only one a baked run ever takes: the cycle already wrote exactly
    # this shape at bake time. Reading the 19 MB parquet, filtering it and re-serialising it
    # per request was most of the console's time-to-first-map.
    compact = path / "segments_wet.json"
    if compact.is_file():
        product = json.loads(compact.read_text(encoding="utf-8"))
        meta = _meta(path)
        if box is not None:
            product = _within_bbox(
                product, box, str(meta.get("city") or city_of_run(path.name) or resolve_city(city))
            )
        log.info(
            "api.segments",
            run_id=path.name,
            wet=product.get("n_segments_wet"),
            cached=True,
            bbox=bbox,
        )
        return {
            **product,
            "step_min": meta.get("step_min", 5),
            "ensemble_n": meta.get("ensemble_n", 1),
            "safe_until": {},
            "notes": meta.get("notes", []),
        }

    parquet = path / "segment_forecast.parquet"
    if not parquet.is_file():
        raise api_error(
            404,
            "no_segment_forecast",
            f"Run {path.name} has no segment forecast. {_bake_hint_for(path.name)}",
            run_id=path.name,
        )

    import pandas as pd

    frame = pd.read_parquet(parquet)
    meta = _meta(path)
    wet_ids = frame.loc[frame["depth_p50_cm"] >= min_depth_cm, "segment_id"].unique()
    if box is not None:
        inside = _ids_within(
            box, str(meta.get("city") or city_of_run(path.name) or resolve_city(city))
        )
        wet_ids = [sid for sid in wet_ids if str(sid) in inside]
    wet = frame[frame["segment_id"].isin(wet_ids)].sort_values(["segment_id", "valid_ts"])

    times = [str(t) for t in sorted(frame["valid_ts"].unique())]
    series: dict[str, list[float]] = {}
    safe_until: dict[str, Any] = {}
    for seg_id, group in wet.groupby("segment_id", sort=True):
        series[str(seg_id)] = [round(float(v), 1) for v in group["depth_p50_cm"]]
        first = group.iloc[0]
        if isinstance(first.get("safe_until"), str):
            safe_until[str(seg_id)] = json.loads(first["safe_until"])

    log.info(
        "api.segments", run_id=path.name, wet=len(series), of=int(frame["segment_id"].nunique())
    )
    return {
        "run_id": meta.get("run_id", path.name),
        "valid_ts": times,
        "step_min": meta.get("step_min", 5),
        "ensemble_n": meta.get("ensemble_n", 1),
        "min_depth_cm": min_depth_cm,
        "n_segments_total": int(frame["segment_id"].nunique()),
        "n_segments_wet": len(series),
        "depth_cm": series,
        "safe_until": safe_until,
        "notes": meta.get("notes", []),
    }


def _with_attribution(row: dict[str, Any]) -> dict[str, Any]:
    """One hotspot with the attribution pair section 10.3 promises, ranked or labelled.

    Nothing is synthesised: the list is whatever the artifact carries. The cycle's own label wins
    whenever there is one - including beside an empty list, where it is a measured refusal - and
    :data:`NO_ATTRIBUTION_LABEL` fills in only for runs baked before the cycle wrote any.
    """
    rows = row.get("attribution") or []
    label = row.get("attribution_label") or (None if rows else NO_ATTRIBUTION_LABEL)
    return {**row, "attribution": rows, "attribution_label": label}


@router.get("/nowcast/hotspots", summary="Ranked hotspots for the rail")
def hotspots(
    run_id: Annotated[str | None, Query()] = None,
    city: CityQuery = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> dict[str, Any]:
    """The run's ranked chronic spots, deepest first (SPEC.md 11.8, P5.4).

    Read straight off the run directory: ``hotspots.json`` was computed once when the cycle ran,
    and re-deriving it per request would let the rail and the map disagree about the same run.

    Every entry carries the register's ``source_url``, so the claim "this junction floods" stays
    traceable to the report it was verified against (rule 7). ``ranking`` says out loud which
    score ordered the list, because on a deterministic run it is **not** the spec's
    ``P x exposure_weight`` - that product is reported per hotspot and is 0 or 1 until Flash
    brings a real ensemble in Phase 7.

    ``attribution`` and ``attribution_label`` are in the contract section 10.3 asks for: the pipes
    ranked on ``drain1d`` (ADR-0071), or an empty list with the measured reason it is empty. The
    field is present rather than absent so the drawer reads a refusal it can print instead of a
    missing key it has to guess at.
    """
    path = _resolve(run_id, city)
    record = path / "hotspots.json"
    if not record.is_file():
        raise api_error(
            404,
            "no_hotspots",
            f"Run {path.name} predates hotspot ranking. {_bake_hint_for(path.name)}",
            run_id=path.name,
        )

    ranked = [_with_attribution(row) for row in json.loads(record.read_text(encoding="utf-8"))]
    meta = _meta(path)
    log.info("api.hotspots", run_id=path.name, n=len(ranked), limit=limit)
    return {
        "run_id": meta.get("run_id", path.name),
        "cycle_ts": meta.get("cycle_ts"),
        "step_min": meta.get("step_min", 5),
        "ensemble_n": meta.get("ensemble_n", 1),
        "ranking": "peak depth",
        "impassable_threshold_cm": 30,
        "n_total": len(ranked),
        "hotspots": ranked[:limit],
        "notes": meta.get("notes", []),
    }


@router.get("/nowcast/surcharge", summary="Manholes surcharging and pipes running backwards")
def surcharge(
    run_id: Annotated[str | None, Query()] = None, city: CityQuery = None
) -> dict[str, Any]:
    """The run's surcharging manholes and reversed edges (SPEC.md 11.4, 11.5; P6.6).

    This is the demo's 1:40 moment made drawable: red markers where the drain is pushing water
    back up into the street, and the edges where the sea is holding a trunk shut. Only the nodes
    that actually surcharge are stored, so this stays a small file over a 49,897-node graph.
    """
    path = _resolve(run_id, city)
    record = path / "node_surcharge.json"
    if not record.is_file():
        raise api_error(
            404,
            "no_surcharge_product",
            f"Run {path.name} predates the surcharge product. {_bake_hint_for(path.name)}",
            run_id=path.name,
        )
    product = json.loads(record.read_text(encoding="utf-8"))
    meta = _meta(path)
    log.info(
        "api.surcharge",
        run_id=path.name,
        surcharging=product.get("n_surcharging"),
        reversed_edges=product.get("n_reversed_edges"),
    )
    # The product's own notes (reversed edges the city export gave no line to) sit after the
    # run's; replacing them with the run's would hide an edge the map silently cannot draw.
    return {**product, "notes": [*meta.get("notes", []), *product.get("notes", [])]}


@router.get("/alerts", tags=["alerts"], summary="Alerts raised by a run")
def alerts(
    run_id: Annotated[str | None, Query()] = None,
    city: CityQuery = None,
    level: Annotated[Literal["severe", "moderate", "watch"] | None, Query()] = None,
) -> dict[str, Any]:
    """The alert queue for a run, worst level first (SPEC.md 11.10, P8.7).

    Computed once when the cycle ran, so the queue, the map and the hotspot rail are all reading
    the same forecast. Every alert on a replay carries CAP ``status=Exercise``.

    **The desk's state is folded in at read time.** The queue itself is the cycle's product and
    is never edited, but whether an officer has *seen* an alert is not a forecast - it lives in
    the ops log, and a console showing "raised" beside a desk showing "acknowledged" is one alert
    described two ways. ``apply_alert_state`` is the same call ``GET /v1/ops/alerts`` makes, with
    the city resolved the same way, so the two cannot disagree. With no ops log it returns the
    product untouched, which is every run on a fresh clone.
    """
    from varuna_products.alerts import served_queue

    from varuna_api.routers.ops import apply_alert_state

    path = _resolve(run_id, city)
    record = path / "alerts.json"
    if not record.is_file():
        raise api_error(
            404,
            "no_alerts",
            f"Run {path.name} has no alert product. {_bake_hint_for(path.name)}",
            run_id=path.name,
        )

    body = json.loads(record.read_text(encoding="utf-8"))
    # The uncapped counts and the annotated pending list: the product lists at most 60 alerts,
    # and a screen that counts the list would present the cap as the cycle.
    served = served_queue(body)
    queue = apply_alert_state(body.get("alerts", []), city)
    if level:
        queue = [a for a in queue if a.get("level") == level]
    meta = _meta(path)
    record = body.get("hysteresis")
    notes = list(meta.get("notes", []))
    if not isinstance(record, dict):
        # Written before the cross-cycle rule (SPEC.md 11.10): the queue decided on one cycle's
        # forecast and `persists_cycles` counts forecast steps. Said, not hidden, until re-baked.
        notes.append(
            "This run's queue was written before alerts needed two consecutive cycles: it raised "
            "on this cycle alone, and its persistence is counted in forecast steps. Re-bake it "
            "to apply the cross-cycle rule."
        )
    log.info("api.alerts", run_id=path.name, n=len(queue), level=level)
    return {
        "run_id": meta.get("run_id", path.name),
        "cycle_ts": meta.get("cycle_ts"),
        "n_total": len(body.get("alerts", [])),
        "alerts": queue,
        # The cross-cycle state beside the queue: what raises next cycle if it holds, and what
        # this cycle cleared. The per-situation record itself stays in the file - it is the next
        # cycle's input, not the screen's.
        "pending": served["pending"],
        "n_pending": body.get("n_pending", len(body.get("pending", []))),
        "n_raised": served["n_raised"],
        "n_raised_by_level": served["n_raised_by_level"],
        "n_pending_new": served["n_pending_new"],
        "n_pending_step_up": served["n_pending_step_up"],
        "first_onset": served["first_onset"],
        "counts_source": served["counts_source"],
        "cleared": body.get("cleared", []),
        "n_cleared": body.get("n_cleared", len(body.get("cleared", []))),
        "hysteresis": (
            {
                "rule": record.get("rule"),
                "previous_run_id": record.get("previous_run_id"),
                "previous_legacy": record.get("previous_legacy", False),
            }
            if isinstance(record, dict)
            else None
        ),
        "notes": notes,
    }


@router.get(
    "/alerts/{alert_id}.cap",
    tags=["alerts"],
    response_class=Response,
    responses={200: {"content": {"application/xml": {}}, "description": "CAP 1.2"}},
    summary="CAP 1.2 XML document for one alert",
)
def alert_cap(
    alert_id: str, run_id: Annotated[str | None, Query()] = None, city: CityQuery = None
) -> Response:
    """One alert as a CAP 1.2 document, exactly as it was written into the run directory."""
    path = _resolve(run_id, city)
    document = path / "alerts" / f"{alert_id}.cap.xml"
    if not document.is_file():
        raise api_error(
            404,
            "alert_not_found",
            f"No CAP document {alert_id} in run {path.name}.",
            run_id=path.name,
        )
    return Response(content=document.read_text(encoding="utf-8"), media_type="application/xml")


@router.get("/pumps", tags=["pumps"], summary="The run's pump inventory and dispatch plan")
def pumps(run_id: Annotated[str | None, Query()] = None, city: CityQuery = None) -> dict[str, Any]:
    """The greedy assignment of the synthetic pump fleet to the hotspots that flood.

    The inventory is synthetic and the response says so in `inventory`; the benefit is a
    documented reduced model, labelled in `benefit_label` and printed beside every number the
    board shows (SPEC.md rule 6, 11.10).
    """
    path = _resolve(run_id, city)
    record = path / "pump_plan.json"
    if not record.is_file():
        raise api_error(
            404,
            "no_pump_plan",
            f"Run {path.name} has no pump plan. {_bake_hint_for(path.name)}",
            run_id=path.name,
        )
    plan = json.loads(record.read_text(encoding="utf-8"))
    meta = _meta(path)
    log.info("api.pumps", run_id=path.name, assigned=len(plan.get("assignments", [])))
    return {**plan, "cycle_ts": meta.get("cycle_ts"), "notes": meta.get("notes", [])}


@router.get("/drains/health", tags=["drains"], summary="The drain map Pulse learned")
def drains_health(
    run_id: Annotated[str | None, Query()] = None,
    city: CityQuery = None,
    min_beta: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
    limit: Annotated[int, Query(ge=1, le=50_000)] = 4_000,
    order: Annotated[Literal["blockage", "learned"], Query()] = "blockage",
) -> dict[str, Any]:
    """Every pipe with its posterior blockage, its spread and what moved it (SPEC.md 11.6).

    Mumbai's inferred graph has 49,770 edges and the drain X-ray draws the ones that matter, so
    the response is capped and ordered worst-first. `n_edges` is the true total; the cap is what
    was sent. By default (`order=blockage`) the cap keeps the worst pipes by posterior blockage,
    so `limit=25` is the 25 worst pipes of the written product. `order=learned` fills the cap
    with every pipe Pulse moved this cycle (`moved`, up or down) before any pipe that merely sits
    at a high land-use prior - the rule the product is written with - and still sends them worst
    first; a limit below the number moved keeps the worst of the moved.

    Each feature carries `confidence: "inferred"`, which is why the map draws them dashed - the
    geometry is a synthesis from roads and terrain, not a municipal record - and `display_name`
    ("off Eastern Freeway") and `locality` ("near Wadala") where the bake could place it.

    `summary` is what this cycle learned: pipes moved up and down, the largest rise, capacity
    lost at the land-use prior and after learning (weighted by full-flow capacity over the whole
    network), and the observations by kind, synthetic and real. A run baked before the product
    carried it gets one rebuilt from its written pipes, with `source: "written_features"` and a
    `note` saying what that leaves out.
    """
    path = _resolve(run_id, city)
    record = path / "drain_health.geojson"
    if not record.is_file():
        raise api_error(
            404,
            "no_drain_health",
            f"Run {path.name} has no drain-health product. {_bake_hint_for(path.name)}",
            run_id=path.name,
        )

    from varuna_pulse.health import written_features

    health = _read_json_cached(record)
    features = health.get("features", [])
    if min_beta > 0.0:
        features = [f for f in features if float(f["properties"].get("beta_mean", 0)) >= min_beta]
    if order == "learned":
        # Every pipe Pulse moved first, then the worst blockage, then sent worst-first: the rule
        # the product was written with, so a limit below the written count still sends the pipes
        # an observation cleared rather than cutting them for a 0.35 land-use prior.
        features = written_features(features, cap=limit)[:limit]
    else:
        # The worst pipes, full stop: what "the 25 worst pipes" means to the command palette.
        # The sort is stable, so ties keep the file's own order and two requests agree (rule 8).
        features = sorted(
            features, key=lambda f: -float(f["properties"].get("beta_mean", 0.0) or 0.0)
        )[:limit]

    summary = health.get("summary") or _summary_from_features(path, health)
    features = _name_unnamed_pipes(path, features)
    log.info("api.drain_health", run_id=path.name, sent=len(features), of=health.get("n_edges"))
    return {**health, "summary": summary, "features": features, "n_sent": len(features)}


def _name_unnamed_pipes(path: Path, features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every pipe a ``display_name``, so the drain X-ray never titles one "Unnamed pipe".

    The bake names a pipe by its own street, else "off <street>" within 200 m, and a run baked
    before it did carries neither on 4,963-4,980 of the 6,000 pipes it wrote. A pipe missing both
    takes the display name of the road above it (``varuna_api.street_names.pipe_names``): OSM's
    name, "off Dr Ambedkar Road" for a lane OSM does not name, else "<class> near <place>".
    ``street`` stays what the bake wrote, so it still says whether OSM names the pipe's road.
    The cached features are never mutated: a filled pipe is a new dict.
    """

    def unnamed(props: dict[str, Any]) -> bool:
        return not props.get("display_name") and not props.get("street")

    if not any(unnamed(f.get("properties") or {}) for f in features):
        return features
    city = city_of_run(path.name)
    if not city:
        return features
    from varuna_api.street_names import pipe_names

    try:
        names = pipe_names(city)
    except Exception as error:  # a name is a courtesy; the product is served without it
        log.warning("api.drain_health.pipe_names_failed", run_id=path.name, error=str(error))
        return features
    if not names:
        return features
    out: list[dict[str, Any]] = []
    for feature in features:
        props = feature.get("properties") or {}
        found = names.get(str(props.get("edge_id"))) if unnamed(props) else None
        out.append(
            feature
            if found is None
            else {**feature, "properties": {**props, "display_name": found}}
        )
    return out


def _file_key(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _read_json_cached(path: Path) -> dict[str, Any]:
    """A run file parsed once per version on disk, not once per request.

    ``drain_health.geojson`` is 2.3 MB and the drain X-ray asks for it on every visit; keyed on
    the file's size and modification time, so a re-bake is read afresh. Callers must not mutate
    what comes back - :func:`drains_health` builds a new dict and new lists from it.
    """
    return _parse_json(*_file_key(path))


@lru_cache(maxsize=8)
def _parse_json(path: str, _mtime_ns: int, _size: int) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def _city_pipes(
    path: str, _mtime_ns: int, _size: int
) -> tuple[tuple[str, ...], dict[str, int], Any, Any]:
    """Every inferred pipe's id, prior blockage and full-flow capacity, from the city build."""
    import numpy as np
    import pandas as pd

    frame = pd.read_parquet(path, columns=["edge_id", "beta_mean", "q_full_m3s"])
    ids = tuple(str(e) for e in frame["edge_id"])
    return (
        ids,
        {eid: i for i, eid in enumerate(ids)},
        frame["beta_mean"].to_numpy(dtype=np.float64),
        frame["q_full_m3s"].to_numpy(dtype=np.float64),
    )


def _summary_from_features(path: Path, health: dict[str, Any]) -> dict[str, Any]:
    """The product's ``summary`` rebuilt from what an older run wrote, and labelled as such.

    Runs baked before the product carried a summary still get the drain X-ray's headline numbers
    - pipes moved up and down, the largest rise, capacity lost at the prior and after learning,
    observations by kind - computed by the same function the bake uses
    (:func:`varuna_pulse.health.drain_summary`). The network outside the written pipes is taken
    at its prior from ``city/<city>/drain_edges.parquet``, which is what those pipes were in the
    old product's own terms, and the full-flow capacity comes from there too.

    **It is not the exact figure**, and the ``note`` says why: those runs wrote the worst 6,000
    pipes by blockage and dropped the ones an observation cleared (52 of 201 at 08:40 on 2 July
    2019), so the split and the capacity learned cover the written pipes only.
    ``n_moved_unwritten`` is how many moved pipes are missing from it.
    """
    import numpy as np
    from varuna_pulse.health import drain_summary

    props = [f.get("properties", {}) for f in health.get("features", [])]
    observations: list[dict[str, Any]] = []
    obs_path = path / "observations.json"
    if obs_path.is_file():
        observations = list(_read_json_cached(obs_path).get("observations", []))

    city = city_of_run(path.name)
    table_path = city_dir(city) / "drain_edges.parquet" if city else None
    if table_path is not None and table_path.is_file():
        ids, index, prior_all, q_full = _city_pipes(*_file_key(table_path))
        prior = prior_all.copy()
        post = prior_all.copy()
        names: list[str | None] = [None] * len(ids)
        for p in props:
            i = index.get(str(p.get("edge_id")))
            if i is None:
                continue
            post[i] = float(p.get("beta_mean", prior[i]) or 0.0)
            prior[i] = float(p.get("beta_prior", prior[i]) or 0.0)
            names[i] = p.get("display_name") or p.get("street")
        summary = drain_summary(
            post, prior, ids, q_full_m3s=q_full, display_name=names, observations=observations
        )
    else:
        # No city build to read the rest of the network from: the written pipes alone, and no
        # capacity figures rather than ones over a sixth of the network.
        summary = drain_summary(
            np.array([float(p.get("beta_mean", 0.0) or 0.0) for p in props]),
            np.array([float(p.get("beta_prior", 0.0) or 0.0) for p in props]),
            [str(p.get("edge_id")) for p in props],
            display_name=[p.get("display_name") or p.get("street") for p in props],
            observations=observations,
        )
        # The counts above are over the written pipes, but ``n_pipes`` is the network's size
        # ("12 of 49,770 pipes moved"), and the product carries that even when the city is absent.
        summary["n_pipes"] = int(health.get("n_edges") or len(props))

    n_updated = health.get("n_updated")
    unwritten = max(int(n_updated) - summary["n_moved"], 0) if isinstance(n_updated, int) else None
    summary["source"] = "written_features"
    summary["n_moved_unwritten"] = unwritten
    # One line on the drain X-ray at 1366 px: the screen leads with it, so it says the gap and the
    # fix and nothing else.
    summary["note"] = (
        f"Rebuilt from the {len(props):,} pipes this run wrote"
        + (
            f"; {unwritten:,} of the {n_updated:,} pipes Pulse moved were not written, so the "
            "up and down split and the capacity learned cover the written pipes only"
            if unwritten
            else ", because it was baked before the product carried its own summary"
        )
        + ". Re-bake the run for the exact figures."
    )
    return summary


@router.get(
    "/drains/health.csv",
    tags=["drains"],
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Desilting priority"}},
    summary="Desilting priority list as CSV",
)
def drains_health_csv(
    run_id: Annotated[str | None, Query()] = None, city: CityQuery = None
) -> Response:
    """The ranked desilting list a ward engineer can hand to a jetting crew (SPEC.md 7.3)."""
    path = _resolve(run_id, city)
    csv_path = path / "desilting.csv"
    if not csv_path.is_file():
        raise api_error(
            404,
            "no_desilting_csv",
            f"Run {path.name} has no desilting list. {_bake_hint_for(path.name)}",
            run_id=path.name,
        )
    return Response(
        content=csv_path.read_text(encoding="utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="desilting-{path.name}.csv"'},
    )


@router.get("/observations", tags=["observations"], summary="What Pulse assimilated this cycle")
def observations(
    run_id: Annotated[str | None, Query()] = None, city: CityQuery = None
) -> dict[str, Any]:
    """The traffic anomalies and citizen reports that moved the drain map (SPEC.md 7.3).

    This is the assimilation timeline on the drain X-ray: each observation with its time, place,
    the depth it implied and the pipe it was about. Synthetic observations are flagged, because
    the replay's traffic and report streams are synthetic and the screen must say so (rule 7).
    """
    path = _resolve(run_id, city)
    record = path / "observations.json"
    if not record.is_file():
        raise api_error(
            404,
            "no_observations",
            f"Run {path.name} assimilated nothing. {_bake_hint_for(path.name)}",
            run_id=path.name,
        )
    body = json.loads(record.read_text(encoding="utf-8"))
    body["observations"] = _name_unnamed_traffic(path, body.get("observations", []))
    meta = _meta(path)
    log.info("api.observations", run_id=path.name, n=len(body.get("observations", [])))
    return {**body, "cycle_ts": meta.get("cycle_ts")}


def _name_unnamed_traffic(path: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Name the traffic anomalies a run baked before the cycle named them.

    Those runs wrote a traffic anomaly with only its segment id, and the drain X-ray titled 10 of
    the 21 cards on the 08:40 cycle "Unnamed road". The city build knows each segment's street,
    and :func:`varuna_pulse.cycle.segment_place_names` applies the same rules a cycle applies now
    ("off Eastern Freeway", "near Wadala"). Only a missing ``place`` or ``locality`` is filled;
    what the bake wrote always stands, and without a city build nothing changes.
    """

    def unnamed(r: dict[str, Any]) -> bool:
        return (
            r.get("kind") == "traffic"
            and bool(r.get("segment_id"))
            and str(r.get("place") or "").strip().lower() in {"", "unnamed road", "none"}
        )

    city = city_of_run(path.name)
    root = city_dir(city) if city else None
    if not any(unnamed(r) for r in records) or root is None:
        return records
    if not (root / "segments.parquet").is_file():
        return records
    try:
        from varuna_pulse.cycle import segment_place_names

        index = segment_place_names(root, [str(r["segment_id"]) for r in records if unnamed(r)])
    except Exception:  # a name is a courtesy, never a reason to fail the list
        log.warning("api.observations.naming_failed", run_id=path.name, exc_info=True)
        return records
    from varuna_api import street_names

    names = street_names.street_names(city) if city else None
    named: list[dict[str, Any]] = []
    for r in records:
        if unnamed(r):
            place, locality = index.get(str(r["segment_id"]), (None, None))
            # Past the pulse rule's 200 m, the street layer's display name ("Service road near
            # Wadala Depot") rather than nothing, which the screen printed as "Unnamed road".
            if not place and names is not None:
                place = names.for_segment(r["segment_id"])
            r = {**r, "place": place, "locality": r.get("locality") or locality}
        named.append(r)
    return named

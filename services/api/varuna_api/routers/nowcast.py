"""Rain nowcast products: ``GET /v1/nowcast/rain`` and ``/v1/nowcast/rain/series``
(SPEC.md 11.1 step 6, 12; task P3.8).

The console reads two things out of a cycle's rain: the spread band under the time bar, which
is every member's area-of-interest-mean hyetograph, and the fan chart at a junction, which is
one 500 m Sky pixel's quantiles and exceedance probabilities. Both live in
``data/runs/<run_id>/rain/quantiles.zarr``, which a browser cannot open; these two routes are
that store in JSON.

Both take the same source arguments: a ``run_id``, or nothing (the newest baked run that has
rain), or ``compute=true`` to run one Sky cycle from the replay bundle right now. Every answer
carries ``run_id`` and ``valid_ts`` and says whether it was ``baked`` or ``live``, which is
what the run stamp shows. The decisions live in :mod:`varuna_api.rain`; this module is the
contract.

The handlers are synchronous on purpose: reading a Zarr store blocks, and computing a cycle
blocks for seconds, so FastAPI runs them on the threadpool rather than on the event loop that
the replay clock and the WebSocket share.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from varuna_schemas.models import ErrorEnvelope
from varuna_schemas.models.common import Timestamp
from varuna_schemas.models.sky import RainNowcast, RainPointSeries

from varuna_api.rain import rain_nowcast, rain_point_series, resolve_point, resolve_rain
from varuna_api.state import AppState, get_state

router = APIRouter(
    prefix="/v1/nowcast",
    tags=["nowcast"],
    responses={
        404: {"model": ErrorEnvelope, "description": "No run, bundle or city to answer from"},
        422: {
            "model": ErrorEnvelope,
            "description": "The request names a point or cycle that cannot be served",
        },
    },
)

State = Annotated[AppState, Depends(get_state)]

RunIdQ = Annotated[
    str | None,
    Query(description="Run id; default = the newest run under data/runs that carries rain."),
]
ComputeQ = Annotated[
    bool,
    Query(
        description=(
            "Compute this cycle from the replay bundle instead of reading a baked run. The "
            "result is not published as a run, and the response reads mode=live."
        )
    ),
]
BundleQ = Annotated[
    str | None,
    Query(description="Bundle to compute from; default = the open replay, else VARUNA_BUNDLE."),
]
TimeQ = Annotated[
    Timestamp | None,
    Query(
        description=(
            "Cycle time to compute (ISO 8601 with offset, e.g. 2019-07-02T06:40:00+05:30). "
            "Floored onto the bundle's five-minute cycle ladder; default = the replay clock."
        )
    ),
]
HotspotQ = Annotated[
    str | None,
    Query(
        description=(
            "Register id, slug or part of a name from the city's hotspots, e.g. Hindmata. "
            "The coordinate comes from the register, with the source it was verified against."
        )
    ),
]
LonQ = Annotated[float | None, Query(ge=-180, le=180, description="WGS84 longitude.")]
LatQ = Annotated[float | None, Query(ge=-90, le=90, description="WGS84 latitude.")]


@router.get(
    "/rain",
    response_model=RainNowcast,
    summary="Rain over the city: every member's hyetograph and the spread band",
    response_description="AOI-mean rain per member in mm/h, with the p10/p50/p90 band",
)
def nowcast_rain(
    state: State,
    run_id: RunIdQ = None,
    compute: ComputeQ = False,
    bundle: BundleQ = None,
    t: TimeQ = None,
) -> RainNowcast:
    """The time bar's spread band (SPEC.md 7.2): the ensemble's own disagreement about how
    much rain the city is about to get, one line per member over the three-hour horizon."""
    return rain_nowcast(resolve_rain(state, run_id, compute=compute, bundle=bundle, t=t))


@router.get(
    "/rain/series",
    response_model=RainPointSeries,
    summary="Rain fan chart at one junction: quantiles and exceedance per step",
    response_description="p10/p50/p90 in mm/h and P(R > 20/40 mm/h) at the point's Sky pixel",
)
def nowcast_rain_series(
    state: State,
    hotspot: HotspotQ = None,
    lon: LonQ = None,
    lat: LatQ = None,
    run_id: RunIdQ = None,
    compute: ComputeQ = False,
    bundle: BundleQ = None,
    t: TimeQ = None,
) -> RainPointSeries:
    """The fan chart at Hindmata (task P3.8), or at any point inside the radar domain."""
    rain = resolve_rain(state, run_id, compute=compute, bundle=bundle, t=t)
    point = resolve_point(rain.city, rain.products.grid, lon=lon, lat=lat, hotspot=hotspot)
    return rain_point_series(rain, point)


__all__ = ["router"]

"""``GET /v1/runs`` and ``GET /v1/runs/{run_id}``: the run registry and provenance."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from varuna_schemas.models import ErrorEnvelope, RunList, RunMeta

from varuna_api.runs_util import onboarded_run_for
from varuna_api.state import AppState, get_state, run_not_found

router = APIRouter(prefix="/v1/runs", tags=["runs"])


@router.get("", response_model=RunList, summary="List runs, newest first")
def list_runs(
    state: Annotated[AppState, Depends(get_state)],
    city: Annotated[
        str | None,
        Query(
            description="City slug, e.g. mumbai. Defaults to the configured city; 'all' for every city."
        ),
    ] = None,
    bundle: Annotated[str | None, Query(description="Replay bundle id")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> RunList:
    """Runs for one city, newest first.

    **Defaults to the configured city rather than to every city.** Onboarding Chennai put its runs
    in the same directory, and since ids sort chronologically `CHN-` came out above `MUM-` - so the
    console's run stamp, which reads `latest_run_id` from here, began naming a Chennai run over a
    map of Mumbai. `city=all` is the way to ask for the whole registry.

    An onboarded city (Chennai) reports its onboarding build's first forecast as `latest_run_id`
    when that run is on disk, the same run the wizard and its finish card open on
    (`runs_util.onboarded_run_for`). Name order alone had let a superseded run win.
    """
    scope = None if city == "all" else (city or state.settings.varuna_city)
    listing = state.registry.run_list(city=scope, bundle=bundle, limit=limit)
    if scope is not None and bundle is None:
        onboarded = onboarded_run_for(scope)
        if onboarded is not None:
            listing = listing.model_copy(update={"latest_run_id": onboarded.name})
    return listing


@router.get(
    "/{run_id}",
    response_model=RunMeta,
    responses={404: {"model": ErrorEnvelope, "description": "Run not baked or computed"}},
    summary="Run provenance (versions, stage timings, mass balance)",
)
def get_run(run_id: str, state: Annotated[AppState, Depends(get_state)]) -> RunMeta:
    meta = state.registry.get(run_id)
    if meta is None:
        raise run_not_found(run_id)
    return meta


__all__ = ["router"]

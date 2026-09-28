"""City-in-a-box on stage (SPEC.md 7.9, 12; tasks P9.5, P9.6).

`POST /v1/onboard` starts a build in a background thread and answers immediately with a job; the
wizard polls `GET /v1/onboard/{job_id}` and also receives the pipeline's own `onboard.progress`
events over the WebSocket. Both carry the same state, so a dropped socket degrades to polling
rather than to a frozen progress bar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter
from varuna_schemas.paths import city_dir

from varuna_api.onboard import (
    get_job,
    latest_job,
    previous_build,
    record_for_job,
    served_job,
    start_job,
)
from varuna_api.routers.stubs import OnboardRequest
from varuna_api.runs_util import known_cities
from varuna_api.state import api_error

log = structlog.get_logger("varuna.api.onboard")

router = APIRouter(prefix="/v1", tags=["onboard"])


@router.post("/onboard", status_code=202, summary="Start a city-in-a-box job")
def onboard(body: OnboardRequest) -> dict[str, Any]:
    """Build a city and run its first forecast.

    Validated through `OnboardRequest`, the documented shape (SPEC.md 12), so a malformed body
    gets the 422 envelope rather than a message about the city not existing.

    Answers 202 with the job straight away: the build is minutes of work and holding the request
    open for it would time out behind any proxy, and the wizard wants to render its first step
    immediately anyway.

    A request for a city that is already building returns that job rather than starting a second
    one - on stage, a double-click on "Start" must not raise a dialog.

    A request for the configured replay city (``VARUNA_CITY``, Mumbai) answers 409
    ``city_is_replay_city`` and starts nothing.
    """
    from varuna_city.config import load_city_config
    from varuna_schemas.settings import get_settings

    # Refused before anything is validated or started. On 13 September 2026 a Chennai build was
    # started against the public Railway API: it downloaded tiles and OSM responses onto the
    # 500 MB volume beside two built cities, failed at `export` with ENOSPC, and left the volume
    # at 495 MB, after which the API crashed on its next restart and stayed down. The wizard is a
    # demo-laptop feature (SPEC.md 7.9 "on the demo laptop"), so the deployment says so.
    if not get_settings().varuna_onboard_enabled:
        raise api_error(
            403,
            "onboard_disabled",
            "City-in-a-box is switched off on this public deployment. A Chennai build needs about "
            "320 MB of terrain tiles, OSM responses and outputs, and the volume here is 500 MB "
            "shared with the Mumbai demo. Run the wizard against a local API (make demo), or "
            "build from the command line with make city CITY=chennai.",
        )

    city = (body.city or "chennai").strip().lower()
    # The replay city is never onboarded here. The wizard rebuilds every step after `fetch` in
    # place, so a click on it would rewrite `city/mumbai/` under the demo, and its design-storm
    # run would then be the configured city's newest and displace the 2 July 2019 bake as the run
    # every screen defaults to (`runs_util.latest_run_for`). SPEC.md rule 5.
    replay_city = get_settings().varuna_city.strip().lower()
    if city == replay_city:
        raise api_error(
            409,
            "city_is_replay_city",
            f"{city.capitalize()} is this API's replay city and is not onboarded through the "
            f"wizard: a build here would rewrite city/{city}/ under the demo. Onboard another "
            f"city (POST /v1/onboard with city=chennai), or rebuild {city.capitalize()} from the "
            f"command line with make city CITY={city} before the demo starts.",
        )
    try:
        load_city_config(city)
    except (FileNotFoundError, KeyError, ValueError) as error:
        raise api_error(
            404,
            "unknown_city",
            f"No city config for {city!r}. Add services/city/configs/{city}.yaml.",
        ) from error

    state = start_job(
        city=city,
        design_storm=body.design_storm,
        from_cache_only=body.from_cache_only,
    )
    return state.to_dict()


def _built(city: str) -> bool:
    """Whether ``city/<city>/segments.parquet`` exists, so a built city reads as built."""
    try:
        return (Path(city_dir(city)) / "segments.parquet").is_file()
    except ValueError:
        return False


@router.get("/onboard/{job_id}", summary="Job status, with the pipeline's own log tail")
def onboard_job(job_id: str) -> dict[str, Any]:
    """Poll one job.

    A job this process no longer holds - the API restarted since it ran - is answered from the
    city's persisted record when that record names the same job (``from_record: true``), so a
    reopened wizard still shows the build's own log and timings rather than a 404.
    """
    state = get_job(job_id)
    if state is not None:
        return state.to_dict()
    record = record_for_job(job_id)
    if record is not None:
        return served_job(record, built=_built(str(record.get("city") or "")))
    raise api_error(
        404, "no_job", f"No onboarding job {job_id!r}. Start one with POST /v1/onboard."
    )


@router.get("/onboard/city/{city}", summary="The most recent job for a city")
def onboard_latest(city: str) -> dict[str, Any]:
    """What the wizard asks on load, so a reopened tab rejoins a build already running.

    With no job in this process the answer is ``status: "none"`` plus ``previous``: the city's
    last good build as `city/<city>/onboard_last.json` recorded it (its log lines, each with the
    instant it was captured, its per-step milliseconds and its first run), or null when no build
    was ever recorded. ``last_attempt`` is set when a later build did not finish, so the wizard
    can say that it failed without losing the forecast the good one made.
    """
    try:
        city_dir(city)
    except ValueError as error:
        raise api_error(
            404,
            "unknown_city",
            f"{city!r} is not a city name. Ask for one of {', '.join(known_cities())}.",
        ) from error
    state = latest_job(city)
    if state is None:
        return {
            "job_id": None,
            "city": city,
            "status": "none",
            "built": _built(city),
            **previous_build(city),
        }
    payload = state.to_dict()
    payload["built"] = _built(city)
    return payload

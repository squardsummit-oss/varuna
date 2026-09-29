"""``GET /v1/runs`` and ``GET /v1/runs/{run_id}``: the run registry and provenance."""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import tarfile
import uuid
from pathlib import PurePosixPath
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, Query, Request
from varuna_schemas.models import ErrorEnvelope, RunList, RunMeta

from varuna_api.runs_util import onboarded_run_for
from varuna_api.state import AppState, api_error, get_state, run_not_found

log = structlog.get_logger("varuna.api.runs")

UPLOAD_TOKEN_ENV = "VARUNA_RUN_UPLOAD_TOKEN"
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_UNPACKED_BYTES = 160 * 1024 * 1024
LIVE_RUN_RE = re.compile(r"^[A-Z]{3}-\d{8}T\d{4}Z-sky[0-9.]+-twin[0-9.]+-flash[0-9.]+-live$")
KEEP_LIVE_RUNS = 2

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


def _members_are_safe(tar: tarfile.TarFile, run_id: str) -> int:
    """Refuse anything but plain files and folders under ``<run_id>/``; returns unpacked bytes."""
    total = 0
    for member in tar.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != run_id:
            raise api_error(400, "bad_archive", f"Refused {member.name!r}: outside {run_id}/.")
        if not (member.isfile() or member.isdir()):
            raise api_error(400, "bad_archive", f"Refused {member.name!r}: not a file or folder.")
        total += max(member.size, 0)
    return total


@router.post(
    "/upload",
    status_code=201,
    responses={
        401: {"model": ErrorEnvelope, "description": "Missing or wrong upload token"},
        404: {"model": ErrorEnvelope, "description": "Uploads are not enabled on this API"},
        413: {"model": ErrorEnvelope, "description": "Archive too large"},
    },
    summary="Publish a live run computed elsewhere (token-protected)",
)
async def upload_run(
    request: Request,
    state: Annotated[AppState, Depends(get_state)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Store one **live** run directory, sent as a gzipped tar, and keep the newest two.

    A live cycle needs about 1.5 GB, more than the hosted API's container has beside the API
    itself (ADR-0095), so the cycle runs on a scheduled GitHub Actions job and publishes its run
    here. Only a run whose id ends in ``-live`` and whose ``run.json`` says it was forced from a
    ``-LIVE`` folder is accepted; every member must sit under ``<run_id>/`` as a plain file or
    folder; the write is atomic, so a reader never sees a half-unpacked run. Without
    ``VARUNA_RUN_UPLOAD_TOKEN`` set the endpoint answers 404, and a wrong token 401.
    """
    token = os.environ.get(UPLOAD_TOKEN_ENV, "")
    if not token:
        raise api_error(404, "uploads_disabled", "Run uploads are not enabled on this API.")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(given.encode(), token.encode()):
        raise api_error(401, "bad_token", "The upload token is missing or wrong.")

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_UPLOAD_BYTES:
            raise api_error(413, "too_large", f"A run archive is at most {MAX_UPLOAD_BYTES} bytes.")
    try:
        tar = tarfile.open(fileobj=io.BytesIO(bytes(body)), mode="r:gz")
    except tarfile.TarError as error:
        raise api_error(400, "bad_archive", f"Not a gzipped tar: {error}.") from error

    with tar:
        names = [PurePosixPath(m.name).parts[0] for m in tar.getmembers() if m.name.strip("/")]
        run_ids = sorted(set(names))
        if len(run_ids) != 1 or not LIVE_RUN_RE.match(run_ids[0]):
            raise api_error(
                400, "bad_archive", "The archive must hold exactly one live run folder."
            )
        run_id = run_ids[0]
        if _members_are_safe(tar, run_id) > MAX_UNPACKED_BYTES:
            raise api_error(413, "too_large", "The run unpacks to more than this API keeps.")
        root = state.registry.runs_dir
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f".upload-{uuid.uuid4().hex[:10]}"
        staging.mkdir()
        try:
            tar.extractall(staging, filter="data")
            unpacked = staging / run_id
            meta = json.loads((unpacked / "run.json").read_text(encoding="utf-8"))
            if meta.get("run_id") != run_id or not str(meta.get("bundle") or "").endswith("-LIVE"):
                raise api_error(400, "bad_archive", "run.json does not describe a live run.")
            target = root / run_id
            if target.exists():
                shutil.rmtree(target)
            unpacked.replace(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    code = run_id.split("-", 1)[0]
    live = sorted(p.name for p in root.glob(f"{code}-*-live") if p.is_dir())
    for old in live[:-KEEP_LIVE_RUNS]:
        shutil.rmtree(root / old, ignore_errors=True)
    log.info("api.run_uploaded", run_id=run_id, bytes=len(body), kept=live[-KEEP_LIVE_RUNS:])
    return {"run_id": run_id, "stored": True, "kept": live[-KEEP_LIVE_RUNS:]}


__all__ = ["router"]

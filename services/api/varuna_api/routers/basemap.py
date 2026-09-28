"""The offline vector basemap (task P9.10): ``GET /v1/city/{city}/basemap.pmtiles``.

One PMTiles archive per city, written by ``python -m varuna_city.basemap_tiles`` from the city's
own OpenStreetMap extract and ESA WorldCover water. The public map fetches it whole, once, and
its service worker keeps that copy, which is what lets ``/map`` draw streets and water with no
network at all. Esri's imagery is never cached for offline use because its licence does not
allow it; this file exists so there is something that may be.

Served as a file with an ETag, so a browser that has it answers 304, and with byte ranges
(Starlette's ``FileResponse`` honours ``Range``), so a PMTiles reader that asks for pieces gets
pieces. The attribution travels in the archive's metadata and in an ``X-Attribution`` header.

A city with no basemap answers 404 with the command that builds it.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from varuna_city.basemap_tiles import ATTRIBUTION_ASCII, basemap_path
from varuna_schemas.models import ErrorEnvelope

from varuna_api.state import api_error

router = APIRouter(prefix="/v1", tags=["basemap"])

PMTILES_MEDIA_TYPE = "application/vnd.pmtiles"

CACHE_CONTROL = "public, max-age=3600"
"""The archive changes only when it is rebuilt; an hour is the city layers' own policy."""


@router.get(
    "/city/{city}/basemap.pmtiles",
    responses={
        200: {"content": {PMTILES_MEDIA_TYPE: {}}, "description": "PMTiles v3 archive"},
        404: {"model": ErrorEnvelope, "description": "Basemap not built for this city"},
    },
    summary="Offline vector basemap (OSM roads, buildings, waterways; WorldCover water)",
    response_class=Response,
)
def city_basemap(request: Request, city: str) -> Response:
    """The city's PMTiles basemap, whole or by byte range."""
    try:
        path = basemap_path(city)
    except ValueError:
        path = None
    if path is None or not path.is_file():
        raise api_error(
            404,
            "basemap_not_built",
            f"No offline basemap for {city}. Run `uv run python -m varuna_city.basemap_tiles "
            f"--city {city}` to build city/{city}/map/basemap.pmtiles.",
        )
    stat = path.stat()
    etag = f'"basemap-{int(stat.st_mtime)}-{stat.st_size}"'
    headers = {
        "ETag": etag,
        "Cache-Control": CACHE_CONTROL,
        "X-Attribution": ATTRIBUTION_ASCII,
    }
    if request.headers.get("if-none-match") == etag and "range" not in request.headers:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type=PMTILES_MEDIA_TYPE, headers=headers)
